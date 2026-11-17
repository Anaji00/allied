"""Round 3 hardening: a PO is SENT only when mail really went, Allied's own PO form needs Allied-only marks, interrupted pushes never double-create."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import SessionLocal
from app.services import monday_push
from app.services.assistant import tools
from app.services.coordination import packets, purchase_orders
from tests.test_hardening import BOSS, _approved_po, mailer  # noqa: F401
from tests.test_board_changes import OP, _two_boards, live  # noqa: F401


def test_a_po_whose_mail_only_became_a_draft_is_not_marked_sent(mailer, monkeypatch):  # noqa: F811
    from app.services.outbound_email_service import outbound_email_service

    r, j, po = _approved_po()
    monkeypatch.setattr(outbound_email_service, "send_purchase_order", lambda **kw: {"status": "DRAFTED"})
    out = tools.send_vendor_po({"to": "helen@zbsupereal.com", "vendor_name": "HELEN", "po_number": po["po_number"], "po_id": po["id"], "body": "b"}, BOSS)
    assert "not marked sent" in out["done"] and purchase_orders.get(po["id"])["status"] == "APPROVED"


def test_a_send_that_raises_puts_the_po_back_to_approved(mailer, monkeypatch):  # noqa: F811
    from app.services.outbound_email_service import outbound_email_service

    r, j, po = _approved_po()

    def boom(**kw):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(outbound_email_service, "send_purchase_order", boom)
    with pytest.raises(RuntimeError):
        tools.send_vendor_po({"to": "helen@zbsupereal.com", "vendor_name": "HELEN", "po_number": po["po_number"], "po_id": po["id"], "body": "b"}, BOSS)
    assert purchase_orders.get(po["id"])["status"] == "APPROVED"


def test_a_po_being_sent_cannot_be_edited_or_sent_again():
    r, j, po = _approved_po()
    purchase_orders.begin_send(po["id"])
    with pytest.raises(ValueError, match="being sent"):
        purchase_orders.begin_send(po["id"])
    with pytest.raises(Exception, match="sent"):
        purchase_orders.update(po["id"], {"notes": "x"}, BOSS)
    purchase_orders.finish_send(po["id"], True)
    assert purchase_orders.get(po["id"])["status"] == "SENT"


def test_a_customers_to_allied_page_is_not_an_allied_po():
    assert packets.classify("TO: Allied Welding Systems\nPurchase Order No.: 77\nDate Issued: 9/2/26\n") == "customer_po"
    assert packets.classify("Fax 214-555-0100\nALLIED\nWELDING SYSTEMS\nPurchase Order No.: 9301\nDate Issued: 9/2/26\n") == "allied_po"


def test_an_interrupted_create_is_not_retried_blindly(live):  # noqa: F811
    a, _ = _two_boards()
    cid = monday_push.stage_new_item(a, "ROW", {"Notes": "n"}, OP)
    with SessionLocal() as db:
        row = db.get(monday_push.MondayChangeDB, cid)
        row.status, row.updated_at = "PUSHING", datetime.now(timezone.utc) - timedelta(minutes=30)
        db.commit()
    monday_push.push(who="boss")
    with pytest.raises(ValueError, match="may already be on monday"):
        monday_push.retry(cid)
    assert monday_push.skip(cid, "boss")["status"] == "SKIPPED"


def test_an_interrupted_send_is_never_resent_on_a_guess_and_a_manager_settles_it():
    r, j, po = _approved_po()
    purchase_orders.begin_send(po["id"])
    with SessionLocal() as db:
        row = db.get(purchase_orders.PurchaseOrderDB, po["id"])
        row.sent_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        db.commit()
    with pytest.raises(ValueError, match="may have gone out"):
        purchase_orders.begin_send(po["id"])
    assert purchase_orders.resolve_send(po["id"], False, BOSS)["status"] == "APPROVED"
    with pytest.raises(Exception, match="interrupted"):
        purchase_orders.resolve_send(po["id"], True, BOSS)


def test_only_an_approved_numbered_po_can_start_sending():
    r, j, po = _approved_po()
    j2 = jobs_from_rfq()
    draft = purchase_orders.create(j2, "DRAFTY", BOSS, lines=[{"quantity": 1, "description": "x"}])
    with pytest.raises(ValueError, match="approved, numbered"):
        purchase_orders.begin_send(draft["id"])
    assert purchase_orders.get(draft["id"])["status"] == "DRAFT"


def test_a_redirected_pilot_send_is_not_marked_sent(mailer, monkeypatch):  # noqa: F811
    from app.services.outbound_email_service import outbound_email_service

    r, j, po = _approved_po()
    monkeypatch.setattr(outbound_email_service, "send_purchase_order", lambda **kw: {"status": "SENT", "redirected": True})
    tools.send_vendor_po({"to": "helen@zbsupereal.com", "vendor_name": "HELEN", "po_number": po["po_number"], "po_id": po["id"], "body": "b"}, BOSS)
    assert purchase_orders.get(po["id"])["status"] == "APPROVED"


def jobs_from_rfq():
    from app.services.coordination import jobs
    from tests.test_hardening import _rfq

    return jobs.from_rfq(_rfq()["id"], BOSS)["id"]
