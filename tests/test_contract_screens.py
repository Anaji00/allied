"""One call per screen: the per-cell staged state on a board, plain words beside /book values, and the job page in one call (by role). Temporary database; nothing is sent."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import board_edit
from tests.conftest import staff_token
from tests.test_board_edit import OP, TODAY, _clean, board  # noqa: F401  (fixtures)

TAG = uuid.uuid4().hex[:5]
CUSTOMER = "Owens Corning Starr Composites"


@pytest.fixture()
def api(auth_enforced, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 0.0)
    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"cs-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r

    return call


def test_each_real_cell_says_whether_a_change_is_waiting_and_each_row_says_what_is_staged(board):
    board_edit.edit_cell(board, "1", "Status", "done", OP, TODAY)
    board_edit.edit_cell(board, "1", "Due", "fri", OP, TODAY)
    board_edit.move_row(board, "2", "done and", OP)
    board_edit.archive_row(board, "2", OP)
    view = board_edit.board_items(board)
    one = next(r for r in view["items"] if r["id"] == "1")
    assert one["has_staged_changes"] and one["cells"]["status"]["staged"] and one["cells"]["status"]["staged_value"] == "Done" and one["cells"]["status"]["was"] == "Stuck"
    assert one["cells"]["due"]["staged_value"] == "2026-11-20" and one["cells"]["notes"]["staged"] is False and "staged_value" not in one["cells"]["notes"]
    two = next(r for r in view["items"] if r["id"] == "2")
    assert two["staged"] == {"move_to": "g3", "archive": True} and two["has_staged_changes"] and all(not c["staged"] for c in two["cells"].values())
    board_edit.undo_last(OP, board)
    board_edit.undo_last(OP, board)
    again = board_edit.board_items(board)
    assert not any(r["has_staged_changes"] for r in again["items"] if r["id"] == "2")
    a = board_edit.add_row(board, "New", "this", {}, OP, TODAY)
    new = next(r for r in board_edit.board_items(board)["items"] if r["id"] == a["item_id"])
    assert new["staged"] == {"new_row": True} and new["has_staged_changes"]


def test_book_values_come_with_plain_words_and_dates(api):
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Words {TAG}{uuid.uuid4().hex[:3]}"}).json()["ref"]
    rows = api("GET", "/book", params={"area": "orders", "kind": "rfq", "q": rfq}).json()["rows"]
    cell = rows[0]["cells"]["status"]
    assert cell["value"] and cell["text"] and cell["text"] != cell["value"] and cell["text"][0].isupper()                 # a code and its plain word
    assert rows[0]["cells"]["customer"]["text"] == CUSTOMER
    so = api("GET", "/book", params={"area": "orders", "kind": "so"}).json()["rows"]
    if so:
        assert so[0]["cells"]["total_revenue"]["text"].startswith("$") or so[0]["cells"]["total_revenue"]["text"] == ""
    pos = api("GET", "/book", params={"area": "orders", "kind": "po"}).json()["rows"]
    assert all("text" in r["cells"]["status"] for r in pos)
    from app.services.book import _plain_text

    assert _plain_text("job", "due", "date", "2026-11-20").startswith("Fri 20 Nov") and _plain_text("job", "due", "date", "not a date") == "not a date" and _plain_text("so", "total_revenue", "number", 2770) == "$2,770.00"


def test_the_job_page_is_one_call_and_what_it_holds_depends_on_the_role(api):
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Page {TAG}{uuid.uuid4().hex[:3]}"}).json()
    ref = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()["ref"]
    api("POST", f"/jobs/{ref}/design/approve", role="MANAGER")
    api("POST", f"/jobs/{ref}/purchase-orders", json={"vendor": f"Page Vendor {TAG}", "lines": [{"description": "x", "quantity": 2, "unit_cost": 2500, "revision": "A"}]})
    api("POST", f"/actions/job/{ref}/share", json={"body": f"Page comment {TAG}"})
    op = api("GET", f"/jobs/{ref}/page").json()
    assert op["job"]["ref"] == ref and any(f"Page comment {TAG}" in c["body"] for c in op["thread"]) and op["since"]["count"] >= 1 and op["prep"]["pos"] and op["actions"]["actions"] and op["errors"] == [] and op["staff"]
    assert op["prep"]["pos"][0]["total"] is None                                                       # an operator gets no money
    mg = api("GET", f"/jobs/{ref}/page", role="MANAGER").json()
    assert mg["prep"]["pos"][0]["total"] == 5000
    vw = api("GET", f"/jobs/{ref}/page", role="VIEWER").json()
    assert vw["thread"] == [] and "purchase_orders" not in vw["job"] and vw["since"] is None and vw["prep"] is None and vw["actions"] is None and vw["staff"] is False and vw["job"]["sales_order"] is None
    api("GET", "/jobs/JOB-9999-9999/page", expect=404)
    api("GET", f"/jobs/{ref}/page", params={"since": "someday"}, expect=200)                           # a bad "since" only empties that part
