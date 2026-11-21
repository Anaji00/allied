"""The Jobs board: jobs as rows of a board, edited through the records layer; monday projects items that are not jobs; pending rows carry every column. Temporary database, nothing sent."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AuditLogDB
from app.services import board_edit, monday_push
from app.services.coordination import boards
from tests.conftest import staff_token
from tests.test_board_edit import OP, TODAY, _clean, board  # noqa: F401  (fixtures)

TAG = uuid.uuid4().hex[:5]
CUSTOMER = "Owens Corning Starr Composites"


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"jb-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r

    return call


@pytest.fixture()
def projects(monkeypatch):
    pid = str(uuid.uuid4().int % 10**9)
    monkeypatch.setattr(settings, "MONDAY_PROJECTS_BOARD_ID", pid)
    with boards._lock:
        boards._mirror[pid] = {"board_id": pid, "name": "Projects", "role": "projects", "read_at": "now", "groups": [{"id": "p", "title": "Active", "color": ""}],
                               "columns": [{"id": "cust", "title": "Customer", "type": "text"}], "items": []}
    yield pid
    with boards._lock:
        boards._mirror.pop(pid, None)


def _job(api, title):
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": title}).json()
    job = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()
    api("PATCH", f"/records/job/{job['ref']}", json={"field": "title", "text": title})
    return job["ref"]


def _rows(api):
    return api("GET", "/board-edit/boards/jobs/items").json()["items"]


def test_the_jobs_board_is_listed_and_its_rows_are_jobs_with_the_columns_asked_for(api, projects):
    ref = _job(api, f"Hose job {TAG}")
    listed = api("GET", "/board-edit/boards").json()
    assert listed[0]["board_id"] == "jobs" and listed[0]["virtual"] and listed[0]["editable"]
    assert any(b["board_id"] == "jobs" for b in api("GET", "/boards").json())
    got = api("GET", "/board-edit/boards/jobs/items").json()
    assert got["board_id"] == "jobs" and [c["id"] for c in got["columns"]][:4] == ["ref", "title", "stage", "customer"] and got["groups"][-1]["id"] == "not_a_job"
    row = next(r for r in got["items"] if r["id"] == ref)
    assert row["job"] and row["on_monday"] is False and row["monday_item_id"] is None and row["values"]["customer"] == CUSTOMER and row["group"] in {g["id"] for g in got["groups"]}
    assert row["cells"]["title"]["editable"] and not row["cells"]["stage"]["editable"] and row["cells"]["stage"]["labels"]
    assert api("GET", "/boards/jobs").json()["items"]                                                    # the existing board view serves it too
    assert api("GET", "/board-edit/boards/jobs/columns").json()["virtual"]


def test_a_cell_on_the_jobs_board_edits_the_job_through_records_with_its_locks_audit_and_undo(api):
    ref = _job(api, f"Edit me {TAG}")
    r = api("POST", f"/board-edit/boards/jobs/items/{ref}/cell", json={"column": "Title", "text": f"Renamed {TAG}"}).json()
    assert r["saved"] and not r["staged"] and r["becomes"] == f"Renamed {TAG}" and "saved on the job" in r["message"]
    assert next(x for x in _rows(api) if x["id"] == ref)["values"]["title"] == f"Renamed {TAG}"
    with SessionLocal() as db:
        rows = [a for a in db.query(AuditLogDB).filter(AuditLogDB.action == "RECORD_FIELD_CHANGED", AuditLogDB.resource_id == ref)]
    assert rows                                                                                          # the same audit as the records layer
    undone = api("POST", "/board-edit/undo", json={"board_id": "jobs"}).json()
    assert undone["undone"] and f"Edit me {TAG}" in next(x for x in _rows(api) if x["id"] == ref)["values"]["title"]
    assert all(c["board_id"] != "jobs" for c in monday_push.changes("STAGED"))                           # nothing was staged for monday
    for col, text in (("Stage", "done"), ("Owner", "x"), ("Nope", "x"), ("Title", " ")):
        api("POST", f"/board-edit/boards/jobs/items/{ref}/cell", json={"column": col, "text": text}, expect=400)
    api("POST", "/board-edit/boards/jobs/items/JOB-9999-9999/cell", json={"column": "Title", "text": "x"}, expect=404)
    api("POST", f"/board-edit/boards/jobs/items/{ref}/cell", json={"column": "Title", "text": "x"}, role="VIEWER", expect=403)
    api("POST", f"/jobs/{ref}/design/approve", role="MANAGER")                                            # a locked field keeps its reason: the customer once a PO has gone out
    po = api("POST", f"/jobs/{ref}/purchase-orders", json={"vendor": f"Jb Vendor {TAG}", "lines": [{"description": "x", "quantity": 1, "unit_cost": 5, "revision": "A"}]}).json()
    with SessionLocal() as db:
        from app.models.db_models import PurchaseOrderDB

        db.get(PurchaseOrderDB, po["id"]).status = "SENT"
        db.commit()
    bad = api("POST", f"/board-edit/boards/jobs/items/{ref}/cell", json={"column": "Customer", "text": "Someone Else Inc"}, expect=400).json()["detail"]
    assert "purchase order has gone to a vendor" in bad
    assert "1 sent" in next(x for x in _rows(api) if x["id"] == ref)["values"]["po_status"]


def test_other_board_actions_on_the_jobs_board_say_what_to_do_instead(api):
    ref = _job(api, f"Guard {TAG}")
    for path, body in ((f"items/{ref}/archive", {}), (f"items/{ref}/move", {"group": "x"}), (f"items/{ref}/duplicate", {}), ("columns", {"title": "x", "type": "text"}), ("groups", {"title": "x"}),
                       ("paste", {"top_left": {"item_id": ref, "column": "title"}, "rows": [["a"]]}), (f"items/{ref}/comment", {"text": "x"})):
        r = api("POST", f"/board-edit/boards/jobs/{path}", json=body, expect=400).json()["detail"]
        assert "edited cell by cell" in r
    made = api("POST", "/board-edit/boards/jobs/items", json={"name": f"Row job {TAG}", "cells": {"Customer": CUSTOMER}}).json()
    assert made["saved"] and made["ref"].startswith("JOB-")


def test_a_job_that_matches_a_projects_item_is_on_monday_and_an_item_without_a_job_can_become_one(api, projects):
    ref = _job(api, f"Matched {TAG}")
    with boards._lock:
        boards._mirror[projects]["items"] += [{"id": "501", "name": f"Matched {TAG}", "group": "p", "group_title": "Active", "values": {"cust": CUSTOMER}},
                                             {"id": "502", "name": f"Orphan item {TAG}", "group": "p", "group_title": "Active", "values": {"cust": "Nibrock Inc"}}]
    items = _rows(api)
    mine = next(r for r in items if r["id"] == ref)
    assert mine["on_monday"] is True and mine["monday_item_id"] == "501" and mine["values"]["on_monday"] in ("yes", "maybe")
    orphan = next(r for r in items if r["id"] == "monday:502")
    assert orphan["not_a_job_yet"] and orphan["group"] == "not_a_job" and orphan["actions"][0]["path"].endswith("/jobs/from-monday/502") and not orphan["cells"]["title"]["editable"]
    api("POST", "/board-edit/boards/jobs/items/monday:502/cell", json={"column": "Title", "text": "x"}, expect=400)
    made = api("POST", "/board-edit/jobs/from-monday/502").json()
    assert made["ref"].startswith("JOB-") and made["monday_item_id"] == "502"
    again = api("POST", "/board-edit/jobs/from-monday/502").json()
    assert again["ref"] == made["ref"]                                                                     # a second click is the same job
    after = _rows(api)
    assert not any(r["id"] == "monday:502" for r in after) and next(r for r in after if r["id"] == made["ref"])["on_monday"] is True
    api("POST", "/board-edit/jobs/from-monday/999", expect=404)
    api("POST", "/board-edit/jobs/from-monday/502", role="VIEWER", expect=403)


def test_a_pending_row_carries_every_column_with_type_labels_and_a_blank_value_and_a_late_column_appears(board):
    a = board_edit.add_row(board, "Full schema", "this", {"Status": "stuck", "Qty": "3"}, OP, TODAY)
    view = board_edit.board_items(board)
    row = next(r for r in view["items"] if r["id"] == a["item_id"])
    ids = {c["id"] for c in view["columns"]}
    assert set(row["cells"]) == ids and row["cells"]["status"]["labels"] and row["cells"]["status"]["value"] == "Stuck" and row["cells"]["due"]["value"] == "" and row["cells"]["due"]["type"] == "date"
    assert row["cells"]["calc"]["editable"] is False and row["cells"]["notes"]["editable"] is True
    with boards._lock:
        boards._mirror[board]["columns"].append({"id": "late", "title": "Added later", "type": "text"})
    again = next(r for r in board_edit.board_items(board)["items"] if r["id"] == a["item_id"])
    assert "late" in again["cells"] and again["cells"]["late"]["value"] == ""
    out = board_edit.edit_cell(board, a["item_id"], "Added later", "filled", OP, TODAY)
    assert out["pending_row"] and monday_push.changes("STAGED")[0]["payload"]["fields"]["late"] == "filled"
    for col, text in (("Checked", "maybe"), ("Due", "someday"), ("Status", "banana")):
        with pytest.raises(ValueError):
            board_edit.edit_cell(board, a["item_id"], col, text, OP, TODAY)
    assert board_edit.edit_cell(board, a["item_id"], "Checked", "yes", OP, TODAY)["becomes"] == "yes"
