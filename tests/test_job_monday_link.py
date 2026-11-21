"""The job-to-monday link: the migration (rehearsed on a scratch copy only), linking and unlinking through the records layer, the stored link first, suggestions never saved, the link made when a job is
made from or pushed to a monday item. Temporary database; nothing is sent to monday."""
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import settings
from app.services import create_anything, jobs_board, monday_push, records
from app.services.coordination import boards
from tests.conftest import staff_token
from tests.test_board_edit import _Sender

ROOT = Path(__file__).resolve().parent.parent
TAG = uuid.uuid4().hex[:5]
CUSTOMER = "Owens Corning Starr Composites"
OP = {"email": f"jl-operator-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"}


# ------------------------------------------------------------------ the migration, on a scratch database only
@pytest.fixture
def scratch(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'link.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    engine = sa.create_engine(url)
    yield cfg, engine
    engine.dispose()


def test_the_migration_adds_two_empty_columns_and_a_unique_index_and_changes_nothing_else(scratch):
    cfg, engine = scratch
    command.upgrade(cfg, "n7d9f1a3b5c7")
    cols = {c["name"] for c in sa.inspect(engine).get_columns("jobs")}
    assert "monday_item_id" not in cols
    with engine.begin() as conn:
        conn.execute(sa.text("insert into jobs (ref, title, customer) values ('JOB-OLD-1', 'Old title', 'Old Co'), ('JOB-OLD-2', 'Old title', 'Old Co')"))
        before = conn.execute(sa.text("select id, ref, title, customer, sales_order_number from jobs order by id")).fetchall()
    command.upgrade(cfg, "o8e0a2c4d6f8")                                                # this migration, whatever is added after it
    insp = sa.inspect(engine)
    assert {"monday_board_id", "monday_item_id"} <= {c["name"] for c in insp.get_columns("jobs")}
    idx = {i["name"]: i for i in insp.get_indexes("jobs")}
    assert idx["ux_jobs_monday_item"]["unique"] and idx["ux_jobs_monday_item"]["column_names"] == ["monday_board_id", "monday_item_id"]
    with engine.begin() as conn:
        after = conn.execute(sa.text("select id, ref, title, customer, sales_order_number from jobs order by id")).fetchall()
        links = conn.execute(sa.text("select monday_board_id, monday_item_id from jobs")).fetchall()
        version = conn.execute(sa.text("select version_num from alembic_version")).scalar()
    assert after == before and links == [(None, None), (None, None)]                  # no row changed; nothing was filled in from a guess
    from alembic.script import ScriptDirectory

    assert version == "o8e0a2c4d6f8" and "o8e0a2c4d6f8" in {r.revision for r in ScriptDirectory.from_config(cfg).walk_revisions()}
    with engine.begin() as conn:                                                      # two jobs with no link never clash; one item can't link two jobs
        conn.execute(sa.text("update jobs set monday_board_id='1', monday_item_id='9' where ref='JOB-OLD-1'"))
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(sa.text("update jobs set monday_board_id='1', monday_item_id='9' where ref='JOB-OLD-2'"))


def test_the_migration_is_safe_to_run_twice_and_goes_back(scratch):
    cfg, engine = scratch
    command.upgrade(cfg, "head")
    command.stamp(cfg, "n7d9f1a3b5c7")                                                # as if it had never been recorded: the columns are already there
    command.upgrade(cfg, "head")                                                      # a second run changes nothing and raises nothing
    assert "ux_jobs_monday_item" in {i["name"] for i in sa.inspect(engine).get_indexes("jobs")}
    command.downgrade(cfg, "n7d9f1a3b5c7")
    assert "monday_item_id" not in {c["name"] for c in sa.inspect(engine).get_columns("jobs")}
    command.upgrade(cfg, "head")
    assert "monday_item_id" in {c["name"] for c in sa.inspect(engine).get_columns("jobs")}


# ------------------------------------------------------------------ the behaviour
@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"jl-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("OPERATOR", "VIEWER", "MANAGER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r

    return call


@pytest.fixture()
def projects(monkeypatch):
    pid = str(uuid.uuid4().int % 10**9)
    monkeypatch.setattr(settings, "MONDAY_PROJECTS_BOARD_ID", pid)
    items = [{"id": str(700 + i), "name": n, "group": "p", "group_title": "Active", "values": {"cust": CUSTOMER}} for i, n in enumerate([f"Alpha {TAG}", f"Beta {TAG}", f"Gamma {TAG}"])]
    with boards._lock:
        boards._mirror[pid] = {"board_id": pid, "name": "Projects", "role": "projects", "read_at": "now", "groups": [{"id": "p", "title": "Active", "color": ""}],
                               "columns": [{"id": "cust", "title": "Customer", "type": "text"}], "items": items}
    yield pid
    with boards._lock:
        boards._mirror.pop(pid, None)


def _job(api, title):
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": title}).json()
    ref = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()["ref"]
    api("PATCH", f"/records/job/{ref}", json={"field": "title", "text": title})
    return ref


def _row(api, ref):
    return next(r for r in api("GET", "/board-edit/boards/jobs/items").json()["items"] if r["id"] == ref)


def test_link_unlink_and_undo_go_through_the_records_layer_and_one_item_links_one_job(api, projects):
    a, b = _job(api, f"Link A {TAG}"), _job(api, f"Link B {TAG}")
    r = api("POST", f"/board-edit/jobs/{a}/link", json={"item_id": "700"}).json()
    assert r["linked"] and r["now"] == "700"
    row = _row(api, a)
    assert row["link_state"] == "linked" and row["on_monday"] and row["monday_item_id"] == "700" and row["monday_board_id"] == projects and row["monday_item_name"] == f"Alpha {TAG}" and row["actions"][0]["label"] == "Unlink"
    assert "already linked to" in api("POST", f"/board-edit/jobs/{b}/link", json={"item_id": "700"}, expect=400).json()["detail"] and a in api("POST", f"/board-edit/jobs/{b}/link", json={"item_id": "700"}, expect=400).json()["detail"]
    assert "Unlink it first" in api("POST", f"/board-edit/jobs/{a}/link", json={"item_id": "701"}, expect=400).json()["detail"]
    api("POST", f"/board-edit/jobs/{b}/link", json={"item_id": "99999"}, expect=404)
    api("POST", f"/board-edit/jobs/{a}/link", json={"item_id": "700"}, role="VIEWER", expect=403)
    assert api("POST", f"/board-edit/jobs/{a}/unlink").json()["linked"] is False and _row(api, a)["link_state"] != "linked"
    undone = api("POST", "/records/undo").json()                                                       # undo of the unlink puts the link back
    assert undone["undone"] and _row(api, a)["link_state"] == "linked"
    api("POST", "/records/undo")                                                                       # undo of the link removes it again
    assert _row(api, a)["link_state"] != "linked" and jobs_board.stored_links().get(a) is None


def test_the_stored_link_wins_over_a_title_match_and_a_title_guess_is_only_a_suggestion_never_saved(api, projects):
    j = _job(api, f"Beta {TAG}")                                                                       # its title matches monday item 701 exactly
    row = _row(api, j)
    assert row["link_state"] == "suggested" and row["monday_item_id"] == "701" and row["actions"][0]["label"] == "Link it?" and row["actions"][0]["body"] == {"item_id": "701"}
    assert jobs_board.stored_links().get(j) is None                                                    # reading the board saved nothing
    other = _job(api, f"Unrelated {TAG}")
    api("POST", f"/board-edit/jobs/{other}/link", json={"item_id": "701"})                             # a person links that item to a different job
    assert _row(api, other)["link_state"] == "linked" and _row(api, other)["monday_item_id"] == "701"
    after = _row(api, j)
    assert after["link_state"] == "none" and after["monday_item_id"] is None                           # the stored link keeps the item: the title guess is dropped for the other job
    api("POST", f"/board-edit/jobs/{j}/link", json={"item_id": "701"}, expect=400)


def test_making_a_job_from_a_monday_item_stores_the_link_and_a_second_click_is_the_same_job(api, projects):
    made = api("POST", "/board-edit/jobs/from-monday/702").json()
    assert made["linked"] is True and made["already"] is False and jobs_board.stored_links()[made["ref"]]["item_id"] == "702"
    row = _row(api, made["ref"])
    assert row["link_state"] == "linked" and row["monday_item_id"] == "702"
    again = api("POST", "/board-edit/jobs/from-monday/702").json()
    assert again["ref"] == made["ref"] and again["already"] is True
    page = api("GET", f"/jobs/{made['ref']}/page").json()
    assert page["monday"]["state"] == "linked" and page["monday"]["monday_item_id"] == "702" and page["monday"]["actions"][0]["label"] == "Unlink"


def test_a_job_put_on_a_board_is_linked_to_the_real_item_when_the_row_is_pushed(api, projects, monkeypatch):
    j = _job(api, f"Push me {TAG}")
    target = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[target] = {"board_id": target, "name": "Target", "role": "reference", "read_at": "now", "groups": [], "columns": [{"id": "t", "title": "Customer", "type": "text"}], "items": []}
    try:
        staged = create_anything.to_monday_stage("job", j, target, None, OP)
        assert staged["staged"] and jobs_board.stored_links().get(j) is None                          # staging alone links nothing

        class Sender(_Sender):
            def create_item(self, board, name, cols, key=None, group_id=None):
                return "888001"

        monkeypatch.setattr("app.core.container.get_monday_client", lambda: Sender())
        monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)                                      # the fake sender only: the real setting is never touched
        out = monday_push.push(None, who="boss@alliedweldingsystems.com", board_id=target)
        assert out["ok"]
        assert jobs_board.stored_links()[j] == {"board_id": target, "item_id": "888001"}
        row = _row(api, j)
        assert row["link_state"] == "linked" and row["monday_item_id"] == "888001" and row["monday_board_id"] == target and row["on_monday"]
    finally:
        with boards._lock:
            boards._mirror.pop(target, None)


def test_records_show_the_link_as_a_field_with_its_audit(api, projects):
    j = _job(api, f"Field {TAG}")
    f = {x["field"]: x for x in api("GET", f"/records/job/{j}/fields").json()["fields"]}
    assert f["monday_item_id"]["editable"] and f["monday_item_id"]["value"] == ""
    api("PATCH", f"/records/job/{j}", json={"field": "monday_item_id", "text": "700"})
    assert {x["field"]: x for x in api("GET", f"/records/job/{j}/fields").json()["fields"]}["monday_item_id"]["value"] == "700"
    api("PATCH", f"/records/job/{j}", json={"field": "monday_item_id", "text": "424242"}, expect=404)
    from app.core.database import SessionLocal
    from app.models.db_models import AuditLogDB

    with SessionLocal() as db:
        assert db.query(AuditLogDB).filter(AuditLogDB.action == "RECORD_FIELD_CHANGED", AuditLogDB.resource_id == j, AuditLogDB.detail.like("%monday_item_id%")).count() == 1


def test_search_finds_jobs_by_their_monday_item_name_and_pending_rows_and_says_where_each_hit_is(api, projects):
    from app.services import board_edit
    from app.services.search import search

    j = _job(api, f"Searchable job {TAG}")
    api("POST", f"/board-edit/jobs/{j}/link", json={"item_id": "700"})                                  # linked to the item named "Alpha <tag>"
    got = search(f"alpha {TAG}", 8)
    jobs_hits = next(g for g in got["groups"] if g["type"] == "job")["hits"]
    assert any(h["ref"] == j and "Alpha" in h["sub"] for h in jobs_hits) and all(h["where"] for g in got["groups"] for h in g["hits"])
    assert any(g["type"] == "board_item" for g in got["groups"])                                          # the monday item itself is a board row hit
    board = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "Find Board", "role": "reference", "read_at": "now", "groups": [{"id": "g", "title": "Todo", "color": ""}],
                                 "columns": [{"id": "n", "title": "Notes", "type": "text"}], "items": []}
    try:
        a = board_edit.add_row(board, f"Zebra pending {TAG}", "todo", {"Notes": f"quokka {TAG}"}, OP)
        by_name = search(f"zebra {TAG}", 8)
        grp = next(g for g in by_name["groups"] if g["type"] == "pending_row")
        assert grp["hits"][0]["ref"] == a["item_id"] and "waiting to be pushed" in grp["hits"][0]["sub"] and "not pushed to monday yet" in grp["hits"][0]["where"] and "Find Board" in grp["hits"][0]["where"]
        by_cell = search(f"quokka {TAG}", 8)
        assert not any(g["type"] == "pending_row" for g in by_cell["groups"])
        board_edit.archive_row(board, a["item_id"], OP)
        assert not any(g["type"] == "pending_row" for g in search(f"zebra {TAG}", 8)["groups"])           # a dropped row is not found
    finally:
        with boards._lock:
            boards._mirror.pop(board, None)
    assert search("a", 8)["total"] == 0 and next(g for g in search(f"searchable {TAG}", 8)["groups"] if g["type"] == "job")["hits"][0]["where"] == "Job, on the Jobs board"


def test_make_jobs_from_several_monday_items_at_once_is_once_only_per_item_with_an_answer_each(api, projects):
    with boards._lock:
        for it in boards._mirror[projects]["items"]:
            it["name"] = f"Bulk {it['id']} {uuid.uuid4().hex[:6]}"                                      # names no earlier job in this file has
    out = api("POST", "/board-edit/jobs/from-monday/bulk", json={"ids": ["700", "701", 702, "700", "99999", "monday:701"]}).json()
    assert out["made"] == 3 and out["failed"] == 1 and out["already"] == 0 and [r["id"] for r in out["results"]] == ["700", "701", "702", "99999"]
    ok = {r["id"]: r for r in out["results"] if r["ok"]}
    assert len({r["ref"] for r in ok.values()}) == 3 and all(r["linked"] and not r["already"] for r in ok.values())
    bad = next(r for r in out["results"] if not r["ok"])
    assert bad["ref"] is None and "not on the projects board" in bad["message"]
    again = api("POST", "/board-edit/jobs/from-monday/bulk", json={"ids": ["700", "701", "702"]}).json()
    assert again["made"] == 0 and again["already"] == 3 and {r["ref"] for r in again["results"]} == {r["ref"] for r in ok.values()} and "already had one" in again["message"]
    for body in ({"ids": []}, {"ids": "x" * 3 and 5}, {"ids": [[1]]}, {"ids": list(range(101))}, {}):
        api("POST", "/board-edit/jobs/from-monday/bulk", json=body, expect=400)
    api("POST", "/board-edit/jobs/from-monday/bulk", json={"ids": ["700"]}, role="VIEWER", expect=403)


def test_audit_entries_and_exceptions_come_with_plain_words(api):
    from app.api.v1.endpoints.audit import plain_entry

    e = {"actor_email": "kim.roe@alliedweldingsystems.com", "action": "RECORD_FIELD_CHANGED", "resource_type": "job", "resource_id": "JOB-2026-0001",
         "detail": '{"field": "title", "label": "Title", "before": "Old", "after": "New"}'}
    assert plain_entry(e) == "Kim Roe record field changed job JOB-2026-0001: Title: Old -> New."
    hexy = {"actor_email": "system", "action": "SAGE_50_EXPORT", "resource_type": "SAGE_SO_CSV", "resource_id": "a" * 32, "detail": "Generated file 0123456789abcdef0123 for 4 orders"}
    out = plain_entry(hexy)
    assert "aaaaaaaa" not in out and "0123456789abcdef" not in out and out.startswith("System sage 50 export sage so csv") and "for 4 orders" in out
    from app.services.exception_queue_service import ExceptionItem

    x = ExceptionItem(exception_type="INVOICE_COST_VARIANCE", severity="HIGH", title="t", description="d", reference_id="r", vendor_or_customer="v")
    assert x.type_word == "Bill is higher than the order" and x.severity_word == "Important" and x.status_word == "Needs a person"
    assert ExceptionItem(exception_type="SOMETHING_NEW_HERE", title="t", description="d", reference_id="r", vendor_or_customer="v").type_word == "Something new here"
    got = api("GET", "/audit", role="MANAGER").json()
    assert got["entries"] and all("text" in en and en["text"].endswith(".") for en in got["entries"])
