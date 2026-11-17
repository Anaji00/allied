"""Adversarial round 12: cumulative billing, customs refusals, the monday release-PO path. Fakes and temporary database only."""
import threading
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import OrderDB
from app.services.assistant import agent, tools  # noqa: F401  (first: avoids a circular import)
from app.services.coordination import jobs, purchase_orders
from tests.test_adversary_po import BOSS, _age, _approved_po, _job  # noqa: F401

CA_ADDR = "1 Rd, Calgary, AB T2P 5G3, Canada"


@pytest.fixture(autouse=True)
def _tidy_orders():
    with SessionLocal() as db:
        before = {r[0] for r in db.query(OrderDB.order_id).all()}
    yield
    with SessionLocal() as db:
        db.query(OrderDB).filter(~OrderDB.order_id.in_(before)).delete(synchronize_session=False)
        db.commit()


# ============================================================ cumulative billing

def _po_name():
    return f"R12PO{uuid.uuid4().hex[:8]}".upper()


def _svc(po, lines, vendor="KWS", freight="0.00"):
    from app.services.reconciliation_service import ReconciliationService
    svc = ReconciliationService()
    svc._authorized_pos[po] = {"vendor": vendor, "sales_order": "SO-R12", "lines": {k: {"qty": Decimal(q), "unit_cost": Decimal(c)} for k, (q, c) in lines.items()},
                               "authorized_freight": Decimal(freight)}
    return svc


def _bill(po, items, number=None, vendor="KWS", currency="USD", freight="0.00"):
    from app.services.reconciliation_service import VendorInvoice, VendorInvoiceLine
    lines = [VendorInvoiceLine(item_id=k, quantity_billed=Decimal(q), unit_cost_billed=Decimal(c), total_amount=Decimal(q) * Decimal(c)) for k, (q, c) in items.items()]
    total = sum((l.total_amount for l in lines), Decimal("0.00")) + Decimal(freight)
    return VendorInvoice(invoice_number=number or f"INV-{uuid.uuid4().hex[:8]}", vendor_name=vendor, po_number=po, lines=lines, total_amount_billed=total, currency=currency, freight_billed=Decimal(freight))


def test_two_partial_lots_pass_and_a_third_that_takes_the_po_past_its_total_is_flagged():
    po = _po_name()
    svc = _svc(po, {"X": ("10", "5.00")})
    assert svc.reconcile_vendor_invoice(_bill(po, {"X": ("5", "5.00")})).reconciliation_status == "RECONCILED_APPROVED"
    assert svc.reconcile_vendor_invoice(_bill(po, {"X": ("5", "5.00")})).reconciliation_status == "RECONCILED_APPROVED"
    assert svc.reconcile_vendor_invoice(_bill(po, {"X": ("5", "5.00")})).reconciliation_status != "RECONCILED_APPROVED"


def test_a_cheap_line_billed_twice_is_flagged_even_while_the_po_total_has_room():
    po = _po_name()
    svc = _svc(po, {"A": ("1", "100.00"), "B": ("100", "1.00")})
    assert svc.reconcile_vendor_invoice(_bill(po, {"B": ("100", "1.00")})).reconciliation_status == "RECONCILED_APPROVED"
    assert svc.reconcile_vendor_invoice(_bill(po, {"B": ("100", "1.00")})).reconciliation_status != "RECONCILED_APPROVED"


def test_two_full_bills_arriving_at_once_are_not_both_approved():
    po = _po_name()
    svc = _svc(po, {"X": ("10", "5.00")})
    out = []
    gate = threading.Barrier(2)

    def go(n):
        gate.wait()
        out.append(svc.reconcile_vendor_invoice(_bill(po, {"X": ("10", "5.00")}, number=f"RACE-{n}-{uuid.uuid4().hex[:4]}")).reconciliation_status)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out.count("RECONCILED_APPROVED") <= 1


def test_a_credit_memo_is_not_auto_approved():
    po = _po_name()
    svc = _svc(po, {"X": ("10", "5.00")})
    credit = _bill(po, {"X": ("-5", "5.00")})
    assert svc.reconcile_vendor_invoice(credit).reconciliation_status != "RECONCILED_APPROVED"


def test_an_empty_bill_is_not_approved_for_payment():
    po = _po_name()
    svc = _svc(po, {"X": ("10", "5.00")})
    empty = _bill(po, {"X": ("0", "5.00")})
    assert svc.reconcile_vendor_invoice(empty).reconciliation_status != "RECONCILED_APPROVED"


@pytest.mark.parametrize("fmt", ["INV{n}", "INV {n}", "inv-{n}.", "INV-0{n}"])
def test_a_reformatted_invoice_number_is_still_a_duplicate(fmt):
    po = _po_name()
    n = str(uuid.uuid4().int % 10**6 + 100000)
    svc = _svc(po, {"X": ("10", "5.00")})
    assert svc.reconcile_vendor_invoice(_bill(po, {"X": ("5", "5.00")}, number=f"INV-{n}")).reconciliation_status == "RECONCILED_APPROVED"
    assert svc.reconcile_vendor_invoice(_bill(po, {"X": ("5", "5.00")}, number=fmt.format(n=n))).reconciliation_status == "DUPLICATE_INVOICE"


@pytest.mark.parametrize("cur", ["CNY", "rmb", "EUR", "US$", "$", "USDT", "usd ", " USD", None])
def test_a_bill_not_plainly_in_dollars_is_held_for_a_person(cur):
    po = _po_name()
    svc = _svc(po, {"X": ("10", "5.00")})
    got = svc.reconcile_vendor_invoice(_bill(po, {"X": ("10", "5.00")}, currency=cur if cur is not None else "USD")).reconciliation_status
    if cur is None or cur.strip().upper() == "USD":
        assert got == "RECONCILED_APPROVED"
    else:
        assert got != "RECONCILED_APPROVED"


def test_freight_over_the_agreed_amount_and_a_total_that_does_not_add_up_are_held():
    po = _po_name()
    svc = _svc(po, {"X": ("10", "5.00")}, freight="10.00")
    assert svc.reconcile_vendor_invoice(_bill(po, {"X": ("10", "5.00")}, freight="25.00")).reconciliation_status != "RECONCILED_APPROVED"
    bad = _bill(po, {"X": ("10", "5.00")}, freight="10.00")
    bad.total_amount_billed += Decimal("1.00")
    assert svc.reconcile_vendor_invoice(bad).reconciliation_status != "RECONCILED_APPROVED"


# ============================================================ customs refusals

def _cci(items, currency="USD", po="PO-9", origin="ZIBO"):
    from app.services.logistics_dispatcher import LogisticsDispatcher
    return LogisticsDispatcher().generate_customs_invoice("123456", "Cust", CA_ADDR, origin, items, currency, po)


@pytest.mark.parametrize("line", [{"unit_price": 0, "quantity": 1}, {"unit_price": -5, "quantity": 1}, {"unit_price": 5, "quantity": 0}, {"unit_price": 5, "quantity": -2}])
def test_a_zero_or_negative_declared_value_is_refused(line):
    with pytest.raises(ValueError):
        _cci([{"item_id": "OC755507", **line}])


@pytest.mark.parametrize("bad", [{"unit_price": float("nan"), "quantity": 1}, {"unit_price": float("inf"), "quantity": 1}, {"unit_price": 5, "quantity": float("nan")}])
def test_a_nan_or_infinite_value_is_refused_not_declared(bad):
    with pytest.raises((ValueError, TypeError)):
        _cci([{"item_id": "OC755507", **bad}])


@pytest.mark.parametrize("bad", [{"unit_price": "abc", "quantity": 1}, {"unit_price": 5, "quantity": "ten"}, {"unit_price": None, "quantity": 1}])
def test_a_garbled_value_is_refused_not_declared(bad):
    with pytest.raises((ValueError, TypeError)):
        _cci([{"item_id": "OC755507", **bad}])


@pytest.mark.parametrize("bad", [{"unit_price": "abc", "quantity": 1}, {"unit_price": None, "quantity": 1}])
def test_the_customs_refusal_message_is_plain(bad):
    try:
        _cci([{"item_id": "OC755507", **bad}])
    except Exception as exc:  # noqa: BLE001
        assert "float" not in str(exc).lower() and "convert" not in str(exc).lower() and "NoneType" not in str(exc)


@pytest.mark.parametrize("code", ["7208.39.0030", "7208390030", "7208.39", "7208"])
def test_a_real_tariff_number_is_used_as_entered(code):
    inv = _cci([{"item_id": "OC755507", "quantity": 1, "unit_price": 10, "hts_code": code, "description": "Hot-rolled clad plate"}])
    assert inv.hs_tariff_lines[0]["hs_tariff_code"] == code and inv.hs_tariff_lines[0]["description"] == "Hot-rolled clad plate"


@pytest.mark.parametrize("code", ["banana", "<b>7208</b>", "7208.39.0030; DROP", "‮7208", "9" * 40])
def test_a_junk_tariff_number_never_reaches_the_customs_invoice(code):
    try:
        inv = _cci([{"item_id": "OC755507", "quantity": 1, "unit_price": 10, "hts_code": code}])
    except ValueError:
        return
    assert inv.hs_tariff_lines[0]["hs_tariff_code"] != code


@pytest.mark.parametrize("cur,rate_ok", [("USD", True), ("usd", True), (" cad ", True), ("CNY", True), ("EUR", False), ("XXX", False)])
def test_currency_with_and_without_a_rate(cur, rate_ok):
    try:
        inv = _cci([{"item_id": "OC755507", "quantity": 1, "unit_price": 1000}], currency=cur)
    except ValueError:
        assert not rate_ok or cur not in ("USD", "usd", "")
        return
    if cur.strip().upper() in ("USD", ""):
        assert inv.total_declared_usd == 1000
    else:
        assert inv.total_declared_usd != 1000


def test_the_origin_statement_follows_the_origin_and_the_number_stays_internal_free():
    for origin, country in (("ZIBO", "China"), ("EDMONTON", "Canada"), ("GARY", "United States")):
        inv = _cci([{"item_id": "OC755507", "quantity": 1, "unit_price": 10}], origin=origin)
        assert inv.country_of_origin == country and "123456" not in inv.cci_number
        if country == "China":
            assert "USMCA" not in inv.declaration_statement


def test_a_big_customs_invoice_and_odd_text_are_handled():
    items = [{"item_id": f"OC{i}", "quantity": 1, "unit_price": 1.5, "hts_code": "7208.39.0030", "description": "中文 ไทย <b>&</para>"} for i in range(300)]
    inv = _cci(items)
    assert len(inv.hs_tariff_lines) == 300 and inv.total_declared_value == 450.0


# ============================================================ monday: release purchase orders

def _event_order(vendor_code="ZIBO", po_number=None, so=None, mail_to=None):
    item = int(uuid.uuid4().int % 10**9) + 10**9
    po_number = po_number or f"7{uuid.uuid4().int % 10**5}"
    so = so or f"8{uuid.uuid4().int % 10**5}"
    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"O{uuid.uuid4().hex[:8]}", customer_po_number=f"CUST-{uuid.uuid4().hex[:5]}", sales_order_number=so, customer_id="C", customer_name="Z", status="NEW",
                       monday_item_id=str(item), purchase_orders={vendor_code: po_number},
                       line_items=[{"item_id": "OC755507", "description": "screen", "quantity": 1, "unit_price": "10.00", "unit_cost": "5.00", "preferred_vendor": vendor_code}]))
        db.commit()
    return {"pulseId": item, "pulseName": ""}, po_number, so


@pytest.fixture()
def mail(monkeypatch):
    """Sending 'enabled' with a fake network call, so the real send ledger is written exactly as in production."""
    from app.services.outbound_email_service import OutboundEmailService
    sent = []
    monkeypatch.setattr(OutboundEmailService, "send_enabled", property(lambda self: True))
    monkeypatch.setattr(OutboundEmailService, "_deliver", lambda self, msg, rcpts: None if all(str(r).endswith("@alliedweldingsystems.com") for r in rcpts)      # a late staff alert from an earlier test is not vendor mail
                        else sent.append({"to": list(rcpts), "subject": str(msg["Subject"])}))
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_ALL_TO", "")
    return sent


def _release(event):
    from app.services import monday_events
    return monday_events.handle_release_po(event)


def test_a_cancelled_po_is_never_released(monkeypatch, mail):
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "auto")
    po, j = _approved_po("Zibo Supereal Industrial Ceramic Co. Ltd")
    purchase_orders.cancel(po["id"], "customer changed the order", BOSS)
    event, _, so = _event_order(po_number=po["po_number"])
    jobs.link_order(j["id"], so)
    out = _release(event)
    assert not mail and "cancel" in str(out).lower()


def test_an_unlinked_item_is_a_permanent_error():
    from app.services.monday_events import PermanentEventError
    for ev in ({"pulseId": 99999999991, "pulseName": ""}, {}, {"pulseId": "abc"}):
        with pytest.raises(PermanentEventError):
            _release(ev)


def test_a_po_that_went_out_is_not_sent_again_by_a_second_release(monkeypatch, mail):
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "auto")
    event, po, so = _event_order()
    _release(event)
    assert len(mail) == 1
    _release(event)
    assert len(mail) == 1


def test_the_board_release_and_the_queue_cannot_both_send_the_same_po(monkeypatch, mail):
    from app.services.assistant import tools as t
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "auto")
    po, j = _approved_po("Zibo Supereal Industrial Ceramic Co. Ltd")
    event, _, so = _event_order(po_number=po["po_number"])
    jobs.link_order(j["id"], so)
    _release(event)
    n_before = len(mail)
    try:
        t._send_vendor_po({"to": "helen@zbsupereal.com", "vendor_name": po["vendor"], "po_number": po["po_number"], "po_id": po["id"], "body": "b"}, BOSS)
    except (ValueError, jobs.JobError):
        return
    assert len(mail) == n_before


def test_a_po_stuck_in_sending_is_not_released_again(monkeypatch, mail):
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "auto")
    po, j = _approved_po("Zibo Supereal Industrial Ceramic Co. Ltd")
    event, _, so = _event_order(po_number=po["po_number"])
    jobs.link_order(j["id"], so)
    purchase_orders.begin_send(po["id"])
    out = _release(event)
    assert not mail and out


def test_a_sending_po_is_not_called_sent_on_the_board(monkeypatch, mail):
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "auto")
    po, j = _approved_po("Zibo Supereal Industrial Ceramic Co. Ltd")
    event, _, so = _event_order(po_number=po["po_number"])
    jobs.link_order(j["id"], so)
    purchase_orders.begin_send(po["id"])
    assert "sent earlier" not in str(_release(event)).lower()


def test_a_cancelled_job_po_with_a_new_live_one_releases_the_live_number(monkeypatch, mail):
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "auto")
    old, j = _approved_po("Zibo Supereal Industrial Ceramic Co. Ltd")
    purchase_orders.cancel(old["id"], "customer changed the order", BOSS)
    new = purchase_orders.approve(purchase_orders.create(j["id"], "Zibo Supereal Industrial Ceramic Co. Ltd", BOSS)["id"], BOSS)
    event, _, so = _event_order(po_number=old["po_number"])
    jobs.link_order(j["id"], so)
    _release(event)
    assert all(old["po_number"] not in m["subject"] for m in mail)


def test_drafting_mode_queues_one_draft_for_a_double_click(monkeypatch, mail):
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "draft")
    event, po, so = _event_order()
    _release(event)
    _release(event)
    assert not mail
    reviewer = {"email": settings.ADMIN_EMAIL, "role": "ADMIN"}
    waiting = [p for p in agent.pending(reviewer) if p["tool"] == "send_vendor_po" and p["args"].get("po_number") == po]
    assert len(waiting) <= 1
