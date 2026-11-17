"""File sharing on items and comments, and checks that the app doesn't repeat monday.com's most-hated behaviours
(formula columns coming out blank, caps on automation, per-seat limits). Test data only; nothing external is called."""

import io
import uuid
from datetime import date, timedelta

import pytest
from openpyxl import load_workbook

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import ItemFileDB, NotificationDB
from app.services import exports
from app.services.coordination import boards, files, notify, updates
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:6]
ME = {"email": f"eli{TAG}@allied.test", "role": "OPERATOR"}
BOSS = {"email": f"boss{TAG}@allied.test", "role": "MANAGER"}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, client):
    monkeypatch.setattr(settings, "DB_DIR", tmp_path)


def _order():
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    out = OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=f"PO-{uuid.uuid4().hex[:6].upper()}",
        shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1",
        line_items=[{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 1, "unit_price": 410}]))
    return str(out.sales_order_number)


PDF = b"%PDF-1.4 a drawing " + b"x" * 100


# ------------------------------------------------------------------ file sharing


def test_a_file_attached_to_an_item_is_listed_downloaded_and_identical():
    so = _order()
    f = files.add("order", so, "Rev C drawing.pdf", PDF, ME)
    assert f["filename"] == "Rev C drawing.pdf" and f["size"] == len(PDF) and f["uploaded_by"] == ME["email"]
    assert [x["id"] for x in files.listing("order", so)] == [f["id"]]
    path, name, ctype = files.open_file(f["id"])
    assert path.read_bytes() == PDF and name == "Rev C drawing.pdf" and ctype == "application/pdf"
    assert files.add("order", so, "copy.pdf", PDF, ME)["duplicate"] is True                    # the same file twice is kept once
    assert len(files.listing("order", so)) == 1


def test_a_file_on_a_comment_shows_with_that_comment_in_the_thread():
    so = _order()
    c = updates.post("order", so, "Here is the vendor's revised drawing", ME)
    files.add("order", so, "vendor rev.dxf", b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n", ME, update_id=c["id"])
    files.add("order", so, "loose photo.png", b"\x89PNG\r\n\x1a\n" + b"p" * 50, ME)
    th = updates.thread("order", so, ME["email"])
    assert [f["filename"] for f in th[0]["files"]] == ["vendor rev.dxf"]
    assert {f["filename"] for f in files.listing("order", so)} == {"vendor rev.dxf", "loose photo.png"}


@pytest.mark.parametrize("name,data,msg", [
    ("virus.exe", b"MZ\x90\x00 program", "can't be attached"), ("script.sh", b"#!/bin/sh\nrm -rf /", "can't be attached"),
    ("renamed.pdf", b"MZ\x90\x00 an executable called pdf", "looks like a program"), ("elf.xlsx", b"\x7fELF\x02\x01", "looks like a program"),
    ("empty.pdf", b"", "is empty"), ("noext", b"hello", "can't be attached"),
])
def test_programs_empty_files_and_unknown_kinds_are_refused_plainly(name, data, msg):
    with pytest.raises(files.FileError, match=msg):
        files.add("order", _order(), name, data, ME)


def test_names_with_paths_and_odd_characters_cannot_escape_the_attachment_folder(tmp_path):
    so = _order()
    for name in ("../../../etc/passwd.pdf", "..\\..\\windows\\system.pdf", "a/b/c.pdf", "nul .pdf", "李明 图纸 v2.pdf", "x" * 400 + ".pdf"):
        f = files.add("order", so, name, PDF + name.encode()[:20], ME)
        path, saved_as, _ = files.open_file(f["id"])
        assert tmp_path in path.parents and ".." not in path.name and "/" not in saved_as and "\\" not in saved_as
    assert not (tmp_path.parent / "etc").exists()


def test_the_size_limit_and_the_per_item_cap_hold(monkeypatch):
    so = _order()
    monkeypatch.setattr(settings, "SUBMISSION_MAX_MB", 1)
    with pytest.raises(files.FileError, match="larger than 1 MB"):
        files.add("order", so, "big.pdf", b"%PDF" + b"x" * (1024 * 1024 + 10), ME)
    monkeypatch.setattr(files, "MAX_PER_ITEM", 3)
    for i in range(3):
        files.add("order", so, f"f{i}.pdf", PDF + bytes([i]), ME)
    with pytest.raises(files.FileError, match="already has 3 files"):
        files.add("order", so, "one-too-many.pdf", PDF + b"z", ME)


def test_files_only_go_on_things_that_exist_and_comments_on_that_item():
    with pytest.raises(LookupError):
        files.add("order", "99999999", "a.pdf", PDF, ME)
    a, b = _order(), _order()
    c = updates.post("order", a, "on A", ME)
    with pytest.raises(files.FileError, match="isn't on this item"):
        files.add("order", b, "x.pdf", PDF, ME, update_id=c["id"])


def test_only_the_uploader_or_a_manager_removes_a_file_and_it_disappears_from_the_lists():
    so = _order()
    f = files.add("order", so, "mine.pdf", PDF, ME)
    with pytest.raises(PermissionError):
        files.remove(f["id"], {"email": f"other{TAG}@allied.test", "role": "OPERATOR"})
    files.remove(f["id"], BOSS)
    assert files.listing("order", so) == []
    with pytest.raises(LookupError):
        files.open_file(f["id"])
    with pytest.raises(LookupError):
        files.remove(f["id"], BOSS)
    with SessionLocal() as db:
        assert db.get(ItemFileDB, f["id"]).stored_path                                            # hidden, not erased


def test_the_api_attaches_lists_downloads_safely_and_enforces_roles(auth_enforced, client):
    def tok(role, n):
        return {"Authorization": f"Bearer {staff_token(f'{n}{TAG}@alliedweldingsystems.com', role)}"}

    v, o = tok("VIEWER", "fv"), tok("OPERATOR", "fo")
    so = _order()
    up = {"item_type": "order", "item_ref": so}
    assert client.post("/api/v1/files", data=up, files={"upload": ("a.pdf", PDF, "application/pdf")}).status_code in (401, 403)
    assert client.post("/api/v1/files", data=up, files={"upload": ("a.pdf", PDF, "application/pdf")}, headers=v).status_code == 403
    made = client.post("/api/v1/files", data=up, files=[("upload", ("a.pdf", PDF, "application/pdf")), ("upload", ("b.png", b"\x89PNG" + b"1" * 20, "image/png"))], headers=o)
    assert made.status_code == 201 and len(made.json()) == 2
    fid = made.json()[0]["id"]
    assert [x["filename"] for x in client.get("/api/v1/files", params=up, headers=v).json()] == ["b.png", "a.pdf"]
    dl = client.get(f"/api/v1/files/{fid}/download", headers=v)
    assert dl.status_code == 200 and dl.content == PDF and "attachment" in dl.headers["content-disposition"] and dl.headers["x-content-type-options"] == "nosniff"
    bad = client.post("/api/v1/files", data=up, files={"upload": ("evil.exe", b"MZ", "application/octet-stream")}, headers=o)
    assert bad.status_code == 400 and "can't be attached" in bad.json()["detail"]
    assert client.post("/api/v1/files", data={"item_type": "order", "item_ref": "1"}, files={"upload": ("a.pdf", PDF, "application/pdf")}, headers=o).status_code == 404
    assert client.get("/api/v1/files/99999999/download", headers=v).status_code == 404
    assert client.get("/api/v1/files/0/download", headers=v).status_code == 422
    assert "pdf" in client.get("/api/v1/files/allowed", headers=v).json()["extensions"]
    assert client.delete(f"/api/v1/files/{fid}", headers=v).status_code == 403
    assert client.delete(f"/api/v1/files/{fid}", headers=o).status_code == 204


def test_attached_files_can_be_found_by_name_in_the_one_search():
    from app.services import search

    so = _order()
    files.add("order", so, f"vendor-quote-{TAG}.pdf", PDF, ME)
    hits = {g["type"]: g["hits"] for g in search.search(f"vendor-quote-{TAG}")["groups"]}
    assert hits["file"][0]["title"] == f"vendor-quote-{TAG}.pdf" and hits["file"][0]["link"].startswith("/api/v1/files/")


# ------------------------------------------------------------------ not repeating monday's most-hated habits


def _orders_board(monkeypatch):
    board = str(uuid.uuid4().int % 10**9)
    cols = [{"id": "status", "title": "Status", "type": "status"}, {"id": "formula_ets", "title": "VENDOR ETS", "type": "formula"},
            {"id": "formula_target", "title": "Target Date", "type": "formula"}, {"id": "formula_days", "title": "Vendor Days Left", "type": "formula"},
            {"id": "formula_cdays", "title": "Customer Days Left", "type": "formula"}, {"id": "formula_other", "title": "Margin", "type": "formula"}]
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "2026 Orders", "role": "vendors", "read_at": "now", "groups": [], "columns": cols,
                                 "items": [{"id": "1", "name": "KINGCERA cyclone", "values": {"status": "FABRICATION"}},          # formulas come through blank
                                           {"id": "2", "name": "NIBROCK screen", "values": {"status": "FABRICATION", "formula_ets": "2026-12-24"}}]}
    soon = (date.today() + timedelta(days=10)).isoformat()
    later = (date.today() + timedelta(days=20)).isoformat()
    monkeypatch.setattr(boards, "_app_view", lambda role: {"1": {"expected": soon, "need_by": later, "state": "on_track", "reasons": []},
                                                            "2": {"expected": soon, "need_by": later, "state": "late", "reasons": ["x"]}})
    return board, soon, later


def test_a_formula_column_monday_hands_over_blank_is_filled_with_the_apps_own_figure(monkeypatch):
    board, soon, later = _orders_board(monkeypatch)
    try:
        v = boards.view(board)
        first, second = v["items"][0]["values"], v["items"][1]["values"]
        assert first["formula_ets"] == soon and first["formula_target"] == later and first["formula_days"] == "10" and first["formula_cdays"] == "20"
        assert second["formula_ets"] == "2026-12-24"                                                   # a value monday did give is never replaced
        assert "formula_other" not in first or first["formula_other"] == ""                           # nothing is invented for a formula we can't work out
        flagged = {c["id"] for c in v["columns"] if c.get("computed_by_app")}
        assert flagged == {"formula_ets", "formula_target", "formula_days", "formula_cdays"}
    finally:
        with boards._lock:
            boards._mirror.pop(board, None)


def test_the_excel_download_of_a_board_has_no_blank_formula_columns_where_the_app_knows_the_value(monkeypatch, client):
    board, soon, later = _orders_board(monkeypatch)
    try:
        r = client.get(f"/api/v1/exports/boards/{board}")
        rows = list(load_workbook(io.BytesIO(r.content)).active.iter_rows(values_only=True))
        head = {h: i for i, h in enumerate(rows[0])}
        item = next(x for x in rows[1:] if x[0] == "KINGCERA cyclone")
        assert item[head["VENDOR ETS"]] == soon and item[head["Target Date"]] == later and item[head["Vendor Days Left"]] == "10"
    finally:
        with boards._lock:
            boards._mirror.pop(board, None)


def test_there_is_no_cap_on_automations_alerts_or_people():
    for name in dir(settings):
        assert not any(w in name.upper() for w in ("SEAT", "LICENSE", "AUTOMATION_ACTIONS", "ACTIONS_PER_MONTH", "MONTHLY_AUTOMATION")), name
    before = SessionLocal().query(NotificationDB).count()
    for i in range(300):                                                                                # far past monday's 250 actions a month
        assert notify.notify("test_cap", f"cap-{TAG}-{i}", f"alert {i}", "body", to=[ME["email"]]) is not None
    assert SessionLocal().query(NotificationDB).count() >= before + 300


def test_any_number_of_people_can_have_accounts_and_languages_without_a_seat_limit():
    for i in range(12):
        staff_token(f"seat{i}{TAG}@alliedweldingsystems.com", "OPERATOR")
    assert len([p for p in updates.people() if p["email"].startswith("seat") and TAG in p["email"]]) >= 12


def test_exports_carry_every_column_of_a_list_not_just_the_visible_ones():
    cols = [(f"c{i}", f"Column {i}") for i in range(30)]
    data = exports.to_xlsx("Wide", cols, [{f"c{i}": i for i in range(30)}])
    row = list(load_workbook(io.BytesIO(data)).active.iter_rows(values_only=True))[1]
    assert len(row) == 30 and row[29] == 29
