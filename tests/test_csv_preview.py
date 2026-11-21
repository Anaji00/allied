"""A dry run of a CSV import writes nothing: no master file, no orders, no audit row beyond a "previewed" note. Temporary data folder (conftest); fakes only."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services.csv_import_service import csv_import_service

ITEMS = ("item_id,description,standard_cost,base_price\n"
         "PREV-NEW-1,Preview plate,10.00,20.00\n"
         ",No id,1,1\n"
         "PREV-BAD-2,Bad number,abc,5\n")


def _bytes(p):
    p = Path(p)
    return p.read_bytes() if p.exists() else b""


def test_preview_of_items_writes_nothing_and_says_what_each_row_would_do():
    before = _bytes(settings.MASTER_ITEMS_PATH)
    out = csv_import_service.preview_csv(ITEMS, "items", "me@alliedweldingsystems.com")
    assert _bytes(settings.MASTER_ITEMS_PATH) == before
    assert "PREV-NEW-1" not in csv_import_service.matcher.items_cache
    assert out["new"] == 1 and out["skipped"] == 2 and out["token"] and out["errors"]
    by_row = {r["row"]: r for r in out["rows"]}
    assert by_row[1]["action"] == "new" and {c["field"] for c in by_row[1]["changes"]} >= {"item_id", "base_price"}
    assert by_row[2]["reason"] and by_row[3]["reason"]


def test_a_second_identical_row_set_is_a_no_change_after_a_real_import_and_the_token_binds_the_sheet(auth_enforced):
    from app.main import app
    from tests.conftest import staff_token

    c = TestClient(app, raise_server_exceptions=False)
    h = {"Authorization": "Bearer " + staff_token("prev-mgr@alliedweldingsystems.com", "MANAGER")}
    body = {"csv_text": "item_id,description,base_price\nPREV-TOK-1,Tok plate,5.00\n", "import_type": "ITEMS"}
    pv = c.post("/api/v1/csv/preview", json=body, headers=h)
    assert pv.status_code == 200 and pv.json()["new"] == 1
    bad = c.post("/api/v1/csv/import", json={**body, "csv_text": body["csv_text"] + "PREV-TOK-2,Other,9\n", "token": pv.json()["token"]}, headers=h)
    assert bad.status_code == 409
    ok = c.post("/api/v1/csv/import", json={**body, "token": pv.json()["token"]}, headers=h)
    assert ok.status_code == 200 and ok.json()["records_created"] == 1
    again = c.post("/api/v1/csv/preview", json=body, headers=h).json()
    assert again["rows"][0]["action"] == "skipped" and again["rows"][0]["reason"] == "Already there, no change."
    viewer = {"Authorization": "Bearer " + staff_token("prev-v@alliedweldingsystems.com", "VIEWER")}
    assert c.post("/api/v1/csv/preview", json=body, headers=viewer).status_code == 403


def test_preview_of_a_batch_lists_the_orders_and_creates_none(monkeypatch):
    made = []
    monkeypatch.setattr(csv_import_service.orchestrator, "process_inbound_order", lambda *a, **k: made.append(1))
    text = "customer_name,customer_po_number,item_id_or_text,quantity,unit_price\nAcme,PO-PREV-1,WIDGET,2,10\nAcme,PO-PREV-1,WIDGET2,1,5.50\nAcme,PO-PREV-2,X,zero,1\n"
    out = csv_import_service.preview_csv(text, "ORDERS_BATCH", "me@alliedweldingsystems.com")
    assert made == [] and out["new"] == 1 and out["skipped"] == 1
    order = next(r for r in out["rows"] if r["action"] == "new")
    assert order["customer_po_number"] == "PO-PREV-1" and order["total"] == pytest.approx(25.5)
