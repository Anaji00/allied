"""Round 26 (adversary), part 2: the damage check on temporary SQLite files, the cache, and the "database newer than the app" mode. Temporary files only: nothing real is opened.
xfail(strict=False, reason="ADV26-Tn ...") marks holes."""
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import database
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]


@pytest.fixture()
def clients(auth_enforced):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"dc-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("OPERATOR", "MANAGER", "VIEWER")}
    return c, heads


def _make_db(path: Path, rows=3000, wal=False):
    con = sqlite3.connect(path)
    if wal:
        con.execute("pragma journal_mode=wal")
    con.execute("create table customers (id integer primary key, name text, notes text)")
    con.execute("create table orders (id integer primary key, customer_id integer, total real)")
    con.execute("create index ix_orders_c on orders(customer_id)")
    con.executemany("insert into customers (name, notes) values (?, ?)", [(f"c{i}", "x" * 200) for i in range(rows)])
    con.executemany("insert into orders (customer_id, total) values (?, ?)", [(i % 100, i * 1.5) for i in range(rows)])
    con.commit()
    con.close()
    return path


def _zero_a_page(p: Path, page_no=3):
    raw = bytearray(p.read_bytes())
    raw[4096 * page_no: 4096 * (page_no + 1)] = b"\x00" * 4096
    p.write_bytes(bytes(raw))


def test_a_sound_database_is_reported_sound_including_in_wal_mode_and_with_a_reader_open(tmp_path):
    p = _make_db(tmp_path / "sound.db")
    assert database.database_problems(p) == []
    w = _make_db(tmp_path / "wal.db", wal=True)
    hold = sqlite3.connect(w)
    hold.execute("begin")
    hold.execute("select * from customers limit 1").fetchall()
    assert database.database_problems(w) == []
    hold.close()


@pytest.mark.parametrize("damage", ["truncate-half", "zero-a-page", "garbage-header", "delete-last-page", "text-file"])
def test_a_damaged_file_is_reported_damaged_never_sound_and_never_a_crash(tmp_path, damage):
    p = _make_db(tmp_path / f"{damage}.db")
    raw = bytearray(p.read_bytes())
    page = 4096
    if damage == "truncate-half":
        raw = raw[: len(raw) // 2]
    elif damage == "zero-a-page":
        raw[page * 3: page * 4] = b"\x00" * page
    elif damage == "flip-bytes-in-data":
        for i in range(page * 2 + 100, page * 2 + 160):
            raw[i] ^= 0xFF
    elif damage == "garbage-header":
        raw[:16] = b"NOT A SQLITE FILE"
    elif damage == "delete-last-page":
        raw[len(raw) - page: len(raw)] = b"\x00" * page
    elif damage == "text-file":
        raw = bytearray(b"hello world, this is not a database" * 100)
    p.write_bytes(bytes(raw))
    got = database.database_problems(p)
    assert isinstance(got, list) and got, f"{damage} was reported sound"


def test_t9_flipped_data_bytes_are_not_sound(tmp_path):
    p = _make_db(tmp_path / "flip.db")
    raw = bytearray(p.read_bytes())
    for i in range(4096 * 5 + 500, 4096 * 5 + 560):       # bytes inside a page that holds rows (the gap in an interior page holds nothing)
        raw[i] ^= 0xFF
    p.write_bytes(bytes(raw))
    assert database.database_problems(p)


def test_t3_a_damaged_tail_is_not_sound(tmp_path):
    p = _make_db(tmp_path / "tail.db")
    p.write_bytes(p.read_bytes() + b"\xde\xad" * 3000)
    assert database.database_problems(p)


def test_t4_an_empty_file_is_not_a_sound_database(tmp_path):
    p = tmp_path / "empty.db"
    p.write_bytes(b"")
    assert database.database_problems(p)


def test_t5_a_database_missing_the_apps_tables_is_not_sound(tmp_path):
    p = tmp_path / "blank.db"
    con = sqlite3.connect(p)
    con.execute("create table unrelated (a)")
    con.commit()
    con.close()
    assert database.database_problems(p)


@pytest.mark.parametrize("folder", ["100%", "a#b", "p%41ss"], ids=["percent", "hash", "encoded"])
def test_t6_a_sound_database_in_an_odd_folder_name_is_sound(tmp_path, folder):
    d = tmp_path / folder
    d.mkdir()
    p = _make_db(d / "x.db", rows=50)
    assert database.database_problems(p) == []


@pytest.mark.parametrize("weird", [None, "", 0, b"bytes", "x" * 5000, "C:/does/not/exist/x.db", "nul"])
def test_odd_paths_give_a_plain_list_and_never_raise(weird):
    assert isinstance(database.database_problems(weird), list)


def test_t7_a_path_with_a_nul_is_reported_not_raised():
    assert isinstance(database.database_problems("a" + chr(0) + "b.db"), list)


def test_a_directory_and_a_missing_file_are_reported(tmp_path):
    assert database.database_problems(tmp_path)
    assert database.database_problems(tmp_path / "missing.db")


def test_t10_the_check_is_read_only_and_leaves_no_side_files_in_wal_mode(tmp_path):
    p = _make_db(tmp_path / "ro.db", wal=True)
    before = {x.name: x.stat().st_size for x in tmp_path.iterdir() if x.suffix == ".db"}
    names_before = sorted(x.name for x in tmp_path.iterdir())
    database.database_problems(p)
    assert {x.name: x.stat().st_size for x in tmp_path.iterdir() if x.suffix == ".db"} == before
    assert sorted(x.name for x in tmp_path.iterdir()) == names_before


def test_t8_a_cached_sound_answer_does_not_hide_fresh_damage(tmp_path, monkeypatch):
    p = _make_db(tmp_path / "live.db")
    monkeypatch.setattr(database, "_sqlite_target", lambda url: (str(p), None))
    database._check_cache.update(at=0.0, result=None)
    assert database.check_live_database()["ok"] is True
    _zero_a_page(p)
    try:
        assert database.check_live_database()["ok"] is False
    finally:
        database._check_cache.update(at=0.0, result=None)


def test_a_forced_recheck_sees_the_damage_and_the_text_tells_the_person_not_to_restart(tmp_path, monkeypatch):
    p = _make_db(tmp_path / "live2.db")
    monkeypatch.setattr(database, "_sqlite_target", lambda url: (str(p), None))
    database._check_cache.update(at=0.0, result=None)
    _zero_a_page(p)
    got = database.check_live_database(force=True)
    database._check_cache.update(at=0.0, result=None)
    assert got["ok"] is False and "Do not restart" in got["text"]


@pytest.fixture()
def newer():
    saved = dict(database.schema_state)
    database.schema_state.update(mode="newer", message="The database is newer than this version of the app.")
    yield
    database.schema_state.clear()
    database.schema_state.update(saved)


def test_in_newer_mode_the_probe_and_health_answer_and_everything_else_is_a_plain_503(clients, newer):
    c, heads = clients
    assert c.get("/api/v1/health/live").status_code == 200                       # the Docker probe must still pass: the container is not killed
    h = c.get("/api/v1/health", headers=heads["OPERATOR"])
    assert h.status_code == 200 and h.json()["status"] == "database_newer_than_app"
    for method, path in (("GET", "/api/v1/rfqs"), ("POST", "/api/v1/rfqs"), ("GET", "/api/v1/auth/profile"), ("POST", "/api/v1/auth/login"), ("GET", "/api/v1/jobs"), ("GET", "/api/v1/alerts")):
        r = c.request(method, path, headers=heads["OPERATOR"], json={} if method == "POST" else None)
        assert r.status_code == 503 and r.json().get("mode") == "database_newer_than_app", (method, path, r.status_code)
    assert c.get("/openapi.json").status_code == 200


def test_in_newer_mode_no_route_under_the_health_prefix_can_write(clients, newer):
    c, heads = clients
    spec = c.get("/openapi.json").json()["paths"]
    writers = [(p, m) for p, ops in spec.items() if p.startswith("/api/v1/health") for m in ops if m in ("post", "put", "patch", "delete")]
    assert set(writers) <= {("/api/v1/health/backups/run", "post")}, writers            # the one allowed writer: starting a backup
    for bad in ("/api/v1/health/../rfqs", "/api/v1/health/%2e%2e/rfqs", "/api/v1/health%2F..%2Frfqs", "/api/v1//rfqs", "/api/v1/healthz-x"):
        r = c.get(bad, headers=heads["OPERATOR"])
        assert r.status_code in (404, 503, 307, 308), (bad, r.status_code, r.text[:80])


def test_a_damaged_file_shows_in_health_and_a_sound_one_does_not_and_the_probe_still_passes(clients, monkeypatch, tmp_path):
    c, heads = clients
    p = _make_db(tmp_path / "h.db")
    monkeypatch.setattr(database, "_sqlite_target", lambda url: (str(p), None))
    database._check_cache.update(at=0.0, result=None)
    assert c.get("/api/v1/health", headers=heads["OPERATOR"]).json()["database"]["file_check"]["ok"] is True
    _zero_a_page(p)
    database._check_cache.update(at=0.0, result=None)
    body = c.get("/api/v1/health", headers=heads["OPERATOR"]).json()
    database._check_cache.update(at=0.0, result=None)
    assert body["status"] == "damaged" and body["database"]["file_check"]["ok"] is False
    assert c.get("/api/v1/health/live").status_code == 200                       # a damaged file must not fail the probe (that would restart the container onto a bad file)
