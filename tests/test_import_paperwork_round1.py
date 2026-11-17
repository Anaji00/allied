"""Import paperwork (commercial invoice and packing list for goods from China or Canada) and the PO approval limit. Temporary database only."""
import uuid

import pytest
from reportlab import rl_config

from app.config import settings
from app.services import import_paperwork as ip
from app.services.coordination import jobs, purchase_orders, rfqs
from tests.conftest import staff_token

BOSS = {"email": "boss-imp@alliedweldingsystems.com", "role": "MANAGER"}
ADMIN = {"email": "admin-imp@alliedweldingsystems.com", "role": "ADMIN"}

FULL = {"quantity": 4, "item": "LINER-1", "description": "Mill liner", "unit_cost": 25, "hts_code": "7326.90.8588", "origin": "PRC", "uom": "PC", "net_kg": 12.5}


def _po(lines, vendor=None, incoterm="FOB"):
    r = rfqs.create({"customer": "Owens Corning", "subject": f"imp {uuid.uuid4().hex[:6]}", "items": [{"description": "liner", "quantity": 2}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.create(j["id"], vendor or f"HELEN-{uuid.uuid4().hex[:4]}", BOSS)
    purchase_orders.update(po["id"], {"lines": lines, "incoterm": incoterm}, BOSS)
    return po["id"]


@pytest.mark.parametrize("raw,want", [("PRC", "China"), ("cn", "China"), ("Made in China", "China"), ("P.R. China", "China"), ("ca", "Canada"), ("Canada", "Canada"),
                                      ("usa", "United States"), ("Vietnam", "Vietnam"), ("", ""), (None, "")])
def test_countries_are_named_one_way(raw, want):
    assert ip.country(raw) == want


def test_a_complete_china_po_is_ready_and_carries_the_duty_note():
    out = ip.build(_po([FULL]), "US", "USD")
    assert out["complete"] and out["missing"] == []
    assert out["origins"] == ["China"] and out["total"] == 100.0 and out["total_net_kg"] == 50.0
    assert any("China" in n and "customs broker" in n for n in out["notes"])


def test_missing_details_are_listed_never_filled_in_and_never_block():
    out = ip.build(_po([{"quantity": 1, "item": "X", "description": "no details"}], incoterm=""))
    fields = {m["field"] for m in out["missing"]}
    assert {"hts_code", "origin", "uom", "net_kg", "amount", "incoterm"} <= fields
    assert out["lines"][0]["origin"] == "" and out["lines"][0]["hts_code"] == "" and not out["complete"]


def test_canada_to_the_us_asks_about_usmca_and_china_to_canada_about_a_surtax():
    ca = ip.build(_po([{**FULL, "origin": "Canada"}]), "US")
    assert any("USMCA" in n for n in ca["notes"]) and "China" not in " ".join(ca["notes"])
    cn = ip.build(_po([FULL]), "CA")
    assert any("surtax" in n for n in cn["notes"]) and cn["destination_country"] == "Canada"


@pytest.mark.parametrize("dest,cur", [("MX", "USD"), ("US", "DOLLARS"), ("US", "1X3"), ("", "")])
def test_a_bad_destination_or_currency_is_refused_plainly(dest, cur):
    po = _po([FULL])
    if dest == "":
        assert ip.build(po, dest, cur)["destination"] == "US"          # blank means the defaults
    else:
        with pytest.raises(jobs.JobError):
            ip.build(po, dest, cur)


def test_a_cancelled_po_has_no_import_paperwork():
    po = _po([FULL])
    purchase_orders.approve(po, BOSS)
    purchase_orders.cancel(po, "customer cancelled", BOSS)
    with pytest.raises(jobs.JobError):
        ip.build(po)


def test_the_packing_list_names_no_vendor_no_prices_and_no_customer_but_the_invoice_names_the_vendor(monkeypatch):
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    po = _po([FULL], vendor="ZIBO FOUNDRY")
    info = ip.build(po, "US", "USD")
    packing, invoice = ip.packing_list_pdf(info), ip.commercial_invoice_pdf(info)
    assert packing.startswith(b"%PDF") and invoice.startswith(b"%PDF")
    assert b"ZIBO" not in packing and b"Owens" not in packing and b"25.00" not in packing and b"100.00" not in packing
    assert b"ZIBO" in invoice and b"100.00" in invoice and b"INCOMPLETE" not in invoice


def test_an_incomplete_invoice_says_so_on_its_face(monkeypatch):
    monkeypatch.setattr(rl_config, "pageCompression", 0)
    info = ip.build(_po([{"quantity": 1, "item": "X", "description": "d"}]))
    assert b"INCOMPLETE" in ip.commercial_invoice_pdf(info)


# ------------------------------------------------------------ approval limit and the creator rule

def test_over_the_limit_only_an_admin_approves(monkeypatch):
    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 50.0)
    po = _po([FULL])                                              # adds up to 100
    with pytest.raises(jobs.JobError, match="administrator"):
        purchase_orders.approve(po, BOSS)
    assert purchase_orders.get(po)["status"] == "DRAFT"
    assert purchase_orders.approve(po, ADMIN)["status"] == "APPROVED"


def test_at_or_under_the_limit_and_with_no_limit_a_manager_approves(monkeypatch):
    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 100.0)
    assert purchase_orders.approve(_po([FULL]), BOSS)["status"] == "APPROVED"          # exactly the limit
    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 0.0)
    assert purchase_orders.approve(_po([{**FULL, "unit_cost": 99999}]), BOSS)["status"] == "APPROVED"


def test_the_limit_also_holds_when_a_po_is_numbered_straight_from_an_rfq(monkeypatch):
    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 50.0)
    po = purchase_orders.get(_po([FULL]))
    with pytest.raises(jobs.JobError):
        purchase_orders._approve_row(po["id"], BOSS, jobs.get(po["job_id"]))


def test_a_po_approved_by_the_person_who_drafted_it_is_flagged():
    po = _po([FULL])
    assert purchase_orders.get(po)["approved_by_creator"] is False
    assert purchase_orders.approve(po, BOSS)["approved_by_creator"] is True          # BOSS created and approved it
    other = _po([FULL])
    assert purchase_orders.approve(other, ADMIN)["approved_by_creator"] is False


# ------------------------------------------------------------ the HTTP side

def _h(email, role):
    return {"Authorization": f"Bearer {staff_token(email, role)}"}


def test_the_paperwork_endpoints_are_staff_only_and_give_plain_errors(client, auth_enforced):
    po = _po([FULL])
    op, viewer = _h("op-imp@alliedweldingsystems.com", "OPERATOR"), _h("viewer-imp@alliedweldingsystems.com", "VIEWER")
    for path in ("import-paperwork", "commercial-invoice.pdf", "packing-list.pdf"):
        assert client.get(f"/api/v1/purchase-orders/{po}/{path}", headers=viewer).status_code == 403
        assert client.get(f"/api/v1/purchase-orders/{po}/{path}?destination=MX", headers=op).status_code == 400
        assert client.get(f"/api/v1/purchase-orders/999999/{path}", headers=op).status_code == 404
    facts = client.get(f"/api/v1/purchase-orders/{po}/import-paperwork?destination=CA&currency=CNY", headers=op)
    assert facts.status_code == 200 and facts.json()["complete"] is True and facts.json()["destination"] == "CA"
    for path in ("commercial-invoice.pdf", "packing-list.pdf"):
        r = client.get(f"/api/v1/purchase-orders/{po}/{path}", headers=op)
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")


def test_a_missing_currency_is_listed_not_assumed_and_the_date_is_the_approval_date():
    po = _po([FULL])
    out = ip.build(po)
    assert out["currency"] == "" and any(m["field"] == "currency" for m in out["missing"]) and out["date"] == ""
    purchase_orders.approve(po, BOSS)
    assert ip.build(po, "US", "USD")["date"] and ip.build(po, "US", "USD")["date"] == ip.build(po, "US", "USD")["date"]


def test_an_unpriced_po_cannot_be_approved_by_a_manager_once_a_limit_is_set(monkeypatch):
    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", 50.0)
    po = _po([{"quantity": 1, "item": "X", "description": "no price yet"}])
    with pytest.raises(jobs.JobError, match="no prices"):
        purchase_orders.approve(po, BOSS)
    assert purchase_orders.approve(po, ADMIN)["status"] == "APPROVED"


def test_the_job_warns_when_a_po_was_approved_by_its_drafter_some_time_later():
    from datetime import datetime, timedelta, timezone

    from app.core.database import SessionLocal
    from app.models.db_models import PurchaseOrderDB

    po = _po([FULL])
    purchase_orders.approve(po, BOSS)
    job_id = purchase_orders.get(po)["job_id"]
    assert not [c for c in jobs.get(job_id)["checks"] if c["key"] == "self_approved"]          # drafted and approved in one go: nothing to look over
    with SessionLocal() as db:
        row = db.get(PurchaseOrderDB, po)
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.commit()
    assert [c for c in jobs.get(job_id)["checks"] if c["key"] == "self_approved"]
