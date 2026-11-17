"""Adversarial round 13: the per-PO and per-invoice locks (deadlocks, ordering, growth) and the monday auto path with redirected or failing mail. Fakes and temporary database only."""
import random
import threading
import time
import uuid
from decimal import Decimal

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MondayEventDB, OrderDB
from app.services.assistant import agent, tools  # noqa: F401  (first: avoids a circular import)
from app.services.coordination import jobs, purchase_orders
from tests.test_adversary_po import BOSS, _approved_po  # noqa: F401
from tests.test_adversary_round12 import _bill, _po_name, _svc, _event_order, _release, mail  # noqa: F401


@pytest.fixture(autouse=True)
def _tidy_orders():
    with SessionLocal() as db:
        before = {r[0] for r in db.query(OrderDB.order_id).all()}
    yield
    with SessionLocal() as db:
        db.query(OrderDB).filter(~OrderDB.order_id.in_(before)).delete(synchronize_session=False)
        db.commit()


def _run_with_timeout(fn, seconds=6):
    box = {}

    def go():
        try:
            box["out"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["err"] = exc
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    return (not t.is_alive()), box


# ============================================================ the locks

def test_a_bill_is_reconciled_and_the_locks_are_released():
    po = _po_name()
    svc = _svc(po, {"X": ("10", "5.00")})
    done, box = _run_with_timeout(lambda: svc.reconcile_vendor_invoice(_bill(po, {"X": ("10", "5.00")})))
    assert done and box["out"].reconciliation_status == "RECONCILED_APPROVED"
    done, box = _run_with_timeout(lambda: svc.reconcile_vendor_invoice(_bill(po, {"X": ("10", "5.00")})))
    assert done                                                    # the PO lock was not left held


@pytest.mark.parametrize("vendor", ["PO", "po"])
def test_a_vendor_called_po_cannot_deadlock_the_reconciliation(vendor):
    po = _po_name().replace("0", "7")             # no zeros: the invoice-number reader drops zeros after letters, which would hide the collision
    svc = _svc(po, {"X": ("10", "5.00")}, vendor=vendor)
    done, _ = _run_with_timeout(lambda: svc.reconcile_vendor_invoice(_bill(po, {"X": ("10", "5.00")}, number=po, vendor=vendor)), seconds=4)
    assert done


def test_many_bills_over_many_pos_and_numbers_at_once_finish_without_deadlock():
    svc_by_po = {}
    pos = [_po_name() for _ in range(6)]
    from app.services.reconciliation_service import ReconciliationService
    svc = ReconciliationService()
    for po in pos:
        svc._authorized_pos[po] = {"vendor": "KWS", "sales_order": "SO-R13", "lines": {"X": {"qty": Decimal("10"), "unit_cost": Decimal("5.00")}}, "authorized_freight": Decimal("0.00")}
    done_count = []
    errs = []

    def go(i):
        try:
            po = random.choice(pos)
            svc.reconcile_vendor_invoice(_bill(po, {"X": ("2", "5.00")}, number=random.choice([f"SHARED-{k}" for k in range(4)] + [f"U-{i}"])))
            done_count.append(i)
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)
    ts = [threading.Thread(target=go, args=(i,), daemon=True) for i in range(40)]
    [t.start() for t in ts]
    t0 = time.time()
    [t.join(max(0.1, 25 - (time.time() - t0))) for t in ts]
    assert not any(t.is_alive() for t in ts) and not errs and len(done_count) == 40


def test_the_same_invoice_number_on_two_pos_at_once_does_not_deadlock():
    p1, p2 = _po_name(), _po_name()
    svc = _svc(p1, {"X": ("10", "5.00")})
    svc._authorized_pos[p2] = dict(svc._authorized_pos[p1])
    n = f"SAME-{uuid.uuid4().hex[:5]}"
    ts = [threading.Thread(target=lambda p=p: svc.reconcile_vendor_invoice(_bill(p, {"X": ("10", "5.00")}, number=n)), daemon=True) for p in (p1, p2, p1, p2)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert not any(t.is_alive() for t in ts)


def test_a_bill_that_raises_inside_the_lock_still_releases_it():
    po = _po_name()
    svc = _svc(po, {"X": ("10", "5.00")})
    bad = _bill(po, {"X": ("10", "5.00")})
    orig = svc._reconcile
    state = {"n": 0}

    def flaky(inv):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("boom")
        return orig(inv)
    svc._reconcile = flaky
    with pytest.raises(RuntimeError):
        svc.reconcile_vendor_invoice(bad)
    done, box = _run_with_timeout(lambda: svc.reconcile_vendor_invoice(_bill(po, {"X": ("10", "5.00")})))
    assert done and "out" in box


def test_the_lock_table_does_not_grow_with_every_invoice_number():
    po = _po_name()
    svc = _svc(po, {"X": ("10", "5.00")})
    for i in range(300):
        svc.reconcile_vendor_invoice(_bill(po, {"X": ("1", "5.00")}, number=f"FLOOD-{i}-{uuid.uuid4().hex[:4]}"))
    assert len(svc._invoice_locks) < 100


# ============================================================ monday auto path: redirected and failing mail

def _record_event(item_id, result):
    from app.services.monday_events import RELEASE_PO
    with SessionLocal() as db:
        db.add(MondayEventDB(dedup_key=uuid.uuid4().hex, event_type="release_po", item_id=str(item_id), payload={}, status="DONE", action=RELEASE_PO, result=result))
        db.commit()


def test_a_redirected_pilot_mail_leaves_the_po_sendable(monkeypatch, mail):
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "auto")
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_ALL_TO", "pilot@example.com")
    po, j = _approved_po("Zibo Supereal Industrial Ceramic Co. Ltd")
    event, _, so = _event_order(po_number=po["po_number"])
    jobs.link_order(j["id"], so)
    out = _release(event)
    assert mail and mail[0]["to"] == ["pilot@example.com"]
    assert purchase_orders.get(po["id"])["status"] == "APPROVED"                  # the vendor did not get it
    assert out


def test_a_pilot_redirect_does_not_count_as_sent_on_the_next_release(monkeypatch, mail):
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "auto")
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_ALL_TO", "pilot@example.com")
    po, j = _approved_po("Zibo Supereal Industrial Ceramic Co. Ltd")
    event, _, so = _event_order(po_number=po["po_number"])
    jobs.link_order(j["id"], so)
    first = _release(event)
    _record_event(event["pulseId"], first)                        # what the event log keeps of that run
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_ALL_TO", "")     # the pilot is over
    before = len(mail)
    _release(event)
    assert len(mail) == before + 1 and mail[-1]["to"] == ["helen@zbsupereal.com"]


def test_a_mail_that_fails_puts_the_po_back_and_the_next_release_sends_it_once(monkeypatch):
    from app.services.outbound_email_service import OutboundEmailService
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "auto")
    monkeypatch.setattr(OutboundEmailService, "send_enabled", property(lambda self: True))
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_ALL_TO", "")
    sent = []
    state = {"fail": True}

    def deliver(self, msg, rcpts):
        if state["fail"] and "helen@zbsupereal.com" in rcpts:
            raise OSError("smtp down")
        if "helen@zbsupereal.com" in rcpts:                 # only the vendor's copy (a failure also alerts staff by mail)
            sent.append(list(rcpts))
    monkeypatch.setattr(OutboundEmailService, "_deliver", deliver)
    po, j = _approved_po("Zibo Supereal Industrial Ceramic Co. Ltd")
    event, _, so = _event_order(po_number=po["po_number"])
    jobs.link_order(j["id"], so)
    _release(event)
    assert purchase_orders.get(po["id"])["status"] == "APPROVED" and not sent
    state["fail"] = False
    _release(event)
    assert purchase_orders.get(po["id"])["status"] == "SENT" and len(sent) == 1
    _release(event)
    assert len(sent) == 1


def test_a_po_being_sent_by_the_queue_is_not_sent_by_the_board_at_the_same_time(monkeypatch, mail):
    from app.services.assistant import tools as t
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "auto")
    po, j = _approved_po("Zibo Supereal Industrial Ceramic Co. Ltd")
    event, _, so = _event_order(po_number=po["po_number"])
    jobs.link_order(j["id"], so)
    purchase_orders.begin_send(po["id"])
    out = _release(event)
    assert not mail and "sent" in str(out).lower() or not mail


def test_the_queue_and_the_board_racing_send_exactly_one_mail(monkeypatch, mail):
    from app.services.assistant import tools as t
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "auto")
    po, j = _approved_po("Zibo Supereal Industrial Ceramic Co. Ltd")
    event, _, so = _event_order(po_number=po["po_number"])
    jobs.link_order(j["id"], so)
    gate = threading.Barrier(2)

    def board():
        gate.wait()
        try:
            _release(event)
        except Exception:  # noqa: BLE001
            pass

    def queue():
        gate.wait()
        try:
            t._send_vendor_po({"to": "helen@zbsupereal.com", "vendor_name": po["vendor"], "po_number": po["po_number"], "po_id": po["id"], "body": "b"}, BOSS)
        except (ValueError, jobs.JobError):
            pass
    ts = [threading.Thread(target=board), threading.Thread(target=queue)]
    [x.start() for x in ts]
    [x.join(20) for x in ts]
    assert len(mail) == 1 and purchase_orders.get(po["id"])["status"] == "SENT"
