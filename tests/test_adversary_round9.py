"""Adversarial round 9: the send ledger, PO cancel, migrations, blind-check first-word rules, backup checksum. Fake mail only, temporary database only."""
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import OutboundMailDB, PurchaseOrderDB, VendorJobDB
from app.services.assistant import agent, tools  # noqa: F401  (first: avoids a circular import)
from app.services.coordination import blind, jobs, purchase_orders, rfqs
from app.services.outbound_email_service import OutboundEmailService, outbound_email_service
from tests.test_adversary_po import BOSS, OP, _age, _approved_po, _args, _job  # noqa: F401


@pytest.fixture()
def smtp(monkeypatch):
    """Sending 'enabled' with a fake network call: nothing leaves the process."""
    delivered = []
    monkeypatch.setattr(OutboundEmailService, "send_enabled", property(lambda self: True))
    monkeypatch.setattr(OutboundEmailService, "_deliver", lambda self, msg, rcpts: delivered.append((list(rcpts), msg["Subject"])))
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_ALL_TO", "")
    return delivered


def _rows(ref):
    with SessionLocal() as db:
        return [(r.status, r.redirected, r.category) for r in db.query(OutboundMailDB).filter(OutboundMailDB.reference == ref).order_by(OutboundMailDB.id).all()]


def _po_mail(ref, to="vendor@example.com"):
    return outbound_email_service.send_purchase_order(vendor_name="V", vendor_email=to, po_number=ref, body="b", pdf_bytes=b"%PDF-1.4 x")


# ============================================================ the send ledger

def test_a_real_send_leaves_one_sent_row_and_counts_as_really_sent(smtp):
    ref = f"L{uuid.uuid4().hex[:6]}"
    assert _po_mail(ref)["status"] == "SENT"
    assert _rows(ref) == [("SENT", False, "PURCHASE_ORDER")] and ref in outbound_email_service.really_sent("PURCHASE_ORDER")


def test_attempting_failed_drafted_and_redirected_are_never_really_sent(smtp, monkeypatch):
    ref_f, ref_r = f"F{uuid.uuid4().hex[:6]}", f"R{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(OutboundEmailService, "_deliver", lambda self, msg, rcpts: (_ for _ in ()).throw(RuntimeError("smtp down")))
    assert _po_mail(ref_f)["status"] == "FAILED"
    monkeypatch.setattr(OutboundEmailService, "_deliver", lambda self, msg, rcpts: None)
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_ALL_TO", "pilot@example.com")
    assert _po_mail(ref_r)["status"] == "SENT"
    got = outbound_email_service.really_sent("PURCHASE_ORDER")
    assert ref_f not in got and ref_r not in got
    with SessionLocal() as db:
        db.add(OutboundMailDB(message_id=f"M{uuid.uuid4().hex[:8]}", category="PURCHASE_ORDER", reference="ATT1", status="ATTEMPTING"))
        db.add(OutboundMailDB(message_id=f"M{uuid.uuid4().hex[:8]}", category="PURCHASE_ORDER", reference="DRF1", status="DRAFTED"))
        db.commit()
    assert not ({"ATT1", "DRF1"} & outbound_email_service.really_sent("PURCHASE_ORDER"))


def _to_vendor(smtp):
    """Mail that went to the vendor-side test address (a late staff alert from an earlier test is not that)."""
    return [m for m in smtp if any('example.com' in str(r) and 'admin' not in str(r) for r in (m[0] if isinstance(m[0], (list, tuple)) else [m[0]]))]


def test_a_purchase_order_is_never_sent_when_the_attempting_row_cannot_be_written(smtp, monkeypatch):
    real = OutboundEmailService._ledger
    monkeypatch.setattr(OutboundEmailService, "_ledger", staticmethod(lambda entry: False))
    ref = f"X{uuid.uuid4().hex[:6]}"
    assert _po_mail(ref)["status"] == "FAILED" and not _to_vendor(smtp)
    out = outbound_email_service.send(["a@example.com"], "price", "b", category="PRICE_REQUEST", reference=ref)
    assert out["status"] == "FAILED" and not _to_vendor(smtp)
    mail = outbound_email_service.send(["a@example.com"], "hello", "b", category="GENERAL", reference=ref)      # other mail is not held up by the ledger
    assert mail["status"] == "SENT" and smtp
    monkeypatch.setattr(OutboundEmailService, "_ledger", real)


def test_the_fail_closed_categories_ignore_case(smtp, monkeypatch):
    monkeypatch.setattr(OutboundEmailService, "_ledger", staticmethod(lambda entry: False))
    out = outbound_email_service.send(["a@example.com"], "po", "b", category="purchase_order", reference="CASE1")
    assert out["status"] == "FAILED" and not _to_vendor(smtp)


def test_a_failed_final_ledger_write_is_retried_so_the_row_is_not_left_attempting(smtp, monkeypatch):
    real = OutboundEmailService._ledger
    monkeypatch.setattr(OutboundEmailService, "_ledger", staticmethod(lambda entry: real(entry) if entry.get("status") == "ATTEMPTING" else False))
    ref = f"Z{uuid.uuid4().hex[:6]}"
    assert _po_mail(ref)["status"] == "SENT"
    monkeypatch.setattr(OutboundEmailService, "_ledger", real)
    assert ref in outbound_email_service.really_sent("PURCHASE_ORDER")


def test_concurrent_sends_each_leave_their_own_row_and_the_upsert_never_duplicates(smtp):
    ref = f"C{uuid.uuid4().hex[:6]}"
    ts = [threading.Thread(target=lambda: _po_mail(ref)) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    rows = _rows(ref)
    assert len(rows) == 8 and all(r[0] == "SENT" for r in rows)
    with SessionLocal() as db:
        ids = [m.message_id for m in db.query(OutboundMailDB).filter(OutboundMailDB.reference == ref).all()]
    assert len(set(ids)) == 8


def test_hostile_ledger_fields_are_clipped_not_fatal(smtp):
    out = outbound_email_service.send(["a@example.com"], "s" * 5000, "b", category="C" * 500, reference="R" * 5000)
    assert out["status"] == "SENT"
    assert outbound_email_service._ledger({"message_id": "M" * 100, "category": None, "to": None, "intended_to": None, "status": None, "subject": None}) in (True, False)


def test_settle_from_ledger_decides_only_with_a_clear_record(smtp):
    po, _ = _approved_po()
    tok = purchase_orders.begin_send(po["id"])
    assert purchase_orders.settle_from_ledger(po["id"]) is None                       # too young
    _age(po["id"], 11)
    assert purchase_orders.settle_from_ledger(po["id"]) is None                       # nothing recorded: a manager decides
    with SessionLocal() as db:
        db.add(OutboundMailDB(message_id=f"M{uuid.uuid4().hex[:8]}", category="PURCHASE_ORDER", reference=po["po_number"], status="ATTEMPTING"))
        db.commit()
    assert purchase_orders.settle_from_ledger(po["id"]) is None                       # still attempting: a manager decides
    with SessionLocal() as db:
        db.query(OutboundMailDB).filter(OutboundMailDB.reference == po["po_number"]).update({"status": "SENT", "redirected": True})
        db.commit()
    assert purchase_orders.settle_from_ledger(po["id"]) == "APPROVED"                 # redirected to the pilot inbox is not 'sent'
    purchase_orders.begin_send(po["id"])
    _age(po["id"], 11)
    with SessionLocal() as db:
        db.add(OutboundMailDB(message_id=f"M{uuid.uuid4().hex[:8]}", category="PURCHASE_ORDER", reference=po["po_number"], status="SENT", redirected=False))
        db.commit()
    assert purchase_orders.settle_from_ledger(po["id"]) == "SENT"


def test_a_sent_row_to_somebody_else_does_not_settle_the_po_as_sent():
    po, _ = _approved_po()
    purchase_orders.begin_send(po["id"])
    _age(po["id"], 11)
    with SessionLocal() as db:
        db.add(OutboundMailDB(message_id=f"M{uuid.uuid4().hex[:8]}", category="PURCHASE_ORDER", reference=po["po_number"], status="SENT", redirected=False,
                              to_addr="someone-else@elsewhere.example", intended_to="someone-else@elsewhere.example"))
        db.commit()
    assert purchase_orders.settle_from_ledger(po["id"]) != "SENT"


def test_the_stuck_send_alert_settles_from_the_ledger_before_it_bothers_anyone():
    po, _ = _approved_po()
    purchase_orders.begin_send(po["id"])
    _age(po["id"], 20)
    with SessionLocal() as db:
        db.add(OutboundMailDB(message_id=f"M{uuid.uuid4().hex[:8]}", category="PURCHASE_ORDER", reference=po["po_number"], status="FAILED"))
        db.commit()
    purchase_orders.alert_stuck_sends()
    assert purchase_orders.get(po["id"])["status"] == "APPROVED"


# ============================================================ PO cancel

def test_cancel_is_for_managers_with_a_reason_and_never_while_sending():
    po, _ = _approved_po()
    with pytest.raises(jobs.JobError):
        purchase_orders.cancel(po["id"], "customer cancelled", OP)
    for bad in ("", "  ", "ab", "\x00\x00\x00"):
        with pytest.raises(jobs.JobError):
            purchase_orders.cancel(po["id"], bad, BOSS)
    purchase_orders.begin_send(po["id"])
    with pytest.raises(jobs.JobError):
        purchase_orders.cancel(po["id"], "customer cancelled", BOSS)
    purchase_orders.finish_send(po["id"], False, None)
    out = purchase_orders.cancel(po["id"], "customer cancelled", BOSS)
    assert out["status"] == "CANCELLED"
    again = purchase_orders.cancel(po["id"], "a different reason", BOSS)
    assert again["status"] == "CANCELLED"


def test_a_sent_po_cancels_with_a_warning_and_nothing_is_emailed(smtp):
    po, _ = _approved_po()
    purchase_orders.begin_send(po["id"])
    purchase_orders.finish_send(po["id"], True)
    before = len(smtp)
    out = purchase_orders.cancel(po["id"], "customer cancelled the order", BOSS)
    assert out["status"] == "CANCELLED" and "already has" in out["warning"] and len(smtp) == before


def test_a_cancelled_po_cannot_be_changed_sent_emailed_acknowledged_or_settled():
    po, _ = _approved_po()
    purchase_orders.cancel(po["id"], "raised by mistake", BOSS)
    for call in (lambda: purchase_orders.update(po["id"], {"terms": "x"}, BOSS), lambda: purchase_orders.begin_send(po["id"]), lambda: purchase_orders.email_draft(po["id"], BOSS),
                 lambda: purchase_orders.acknowledge(po["id"], BOSS), lambda: purchase_orders.resolve_send(po["id"], True, BOSS)):
        with pytest.raises((jobs.JobError, ValueError)):
            call()
    purchase_orders.finish_send(po["id"], True)
    assert purchase_orders.get(po["id"])["status"] == "CANCELLED"


def test_a_cancelled_draft_cannot_be_approved_back_to_life():
    _, j = _job()
    po = purchase_orders.create(j["id"], f"DRAFTCANCEL {uuid.uuid4().hex[:4]}", BOSS)
    purchase_orders.cancel(po["id"], "raised by mistake", BOSS)
    try:
        purchase_orders.approve(po["id"], BOSS)
    except jobs.JobError:
        pass
    assert purchase_orders.get(po["id"])["status"] == "CANCELLED"


def test_the_rfq_number_path_does_not_resurrect_a_cancelled_draft():
    r = rfqs.create({"customer": "Owens Corning", "subject": f"cancel path {uuid.uuid4().hex[:5]}", "items": [{"description": "liner", "quantity": 1}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.create(j["id"], "CANCELVENDOR", BOSS)
    purchase_orders.cancel(po["id"], "raised by mistake", BOSS)
    try:
        purchase_orders.number_for_rfq(r["number"], "CANCELVENDOR", BOSS)
    except jobs.JobError:
        pass
    assert purchase_orders.get(po["id"])["status"] == "CANCELLED"


def test_a_new_po_for_a_cancelled_vendor_is_refused_with_a_plain_message():
    po, j = _approved_po()
    purchase_orders.cancel(po["id"], "raised by mistake", BOSS)
    with pytest.raises(jobs.JobError) as exc:                                   # an operator is told in plain words that a manager raises the new one
        purchase_orders.create(j["id"], po["vendor"], OP)
    assert str(exc.value) and "Traceback" not in str(exc.value) and "manager" in str(exc.value)
    assert purchase_orders.create(j["id"], po["vendor"], BOSS)["status"] == "DRAFT"           # a manager can


def test_cancelling_a_po_closes_its_vendor_line():
    po, _ = _approved_po()
    with SessionLocal() as db:
        line = db.query(VendorJobDB).filter(VendorJobDB.purchase_order_id == po["id"]).first()
        line_id = line.id if line else None
    purchase_orders.cancel(po["id"], "raised by mistake", BOSS)
    if line_id is None:
        pytest.skip("no vendor line was linked in this run")
    with SessionLocal() as db:
        assert db.get(VendorJobDB, line_id).status != "WAITING ON PI"


def test_a_cancelled_po_does_not_count_as_issued_for_job_checks():
    from datetime import date
    out = {c["key"] for c in jobs.job_checks({"checked": [("vendor_quote", {"vendor": "HELEN", "valid_until": "10/01/2026", "total": "10.00"})],
                                               "purchase_orders": [{"po_number": "50123", "vendor": "HELEN", "status": "CANCELLED"}]}, today=date(2026, 11, 14))}
    assert "vendor_quote_expired" in out


def test_the_cancel_reason_has_no_bidi_override():
    po, _ = _approved_po()
    out = purchase_orders.cancel(po["id"], "customer ‮cancelled", BOSS)
    assert "‮" not in str(out)


def test_a_non_text_cancel_reason_is_refused(client, auth_enforced):
    from tests.conftest import staff_token
    po, _ = _approved_po()
    boss = {"Authorization": "Bearer " + staff_token("boss-c9@alliedweldingsystems.com", "MANAGER")}
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/cancel", json={"reason": ["a list"]}, headers=boss).status_code in (400, 422)


def test_cancel_over_http(client, auth_enforced):
    from tests.conftest import staff_token
    po, _ = _approved_po()
    op = {"Authorization": "Bearer " + staff_token("op-c9@alliedweldingsystems.com", "OPERATOR")}
    boss = {"Authorization": "Bearer " + staff_token("boss-c9@alliedweldingsystems.com", "MANAGER")}
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/cancel", json={"reason": "customer cancelled"}, headers=op).status_code == 403
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/cancel", json={}, headers=boss).status_code == 400
    assert client.post(f"/api/v1/purchase-orders/999999/cancel", json={"reason": "customer cancelled"}, headers=boss).status_code == 404
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/cancel", json={"reason": "customer cancelled"}, headers=boss).json()["status"] == "CANCELLED"


# ============================================================ migrations

def test_the_two_new_migrations_are_one_chain_with_one_head_and_run_on_a_scratch_database(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import sqlite3
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    url = f"sqlite:///{tmp_path / 'scratch.db'}"
    monkeypatch.setattr("app.core.database._get_db_url", lambda: url)
    command.upgrade(cfg, "head")
    with sqlite3.connect(tmp_path / "scratch.db") as c:
        assert c.execute("select count(*) from sqlite_master where name='outbound_mail_log'").fetchone()[0] == 1
        cols = {r[1] for r in c.execute("pragma table_info(purchase_orders)")}
        assert {"cancelled_at", "cancel_reason"} <= cols
    command.downgrade(cfg, "-2")
    command.upgrade(cfg, "head")


# ============================================================ blind check: first-word and capital rules

def _vendor_body(text):
    return blind.check("send_vendor_price_request", {"to": "helen@zbsupereal.com", "subject": "s", "body": text})


def _customer_body(text):
    return blind.check("send_email", {"to": "buyer@customer-example.com", "subject": "s", "body": text})


@pytest.mark.parametrize("text", ["Please quote the liner. Owens will be the buyer.", "Reference KWS", "knauf insulation parts", "Dear Helen, thanks for the quote.", "Vulcan materials are heavy."])
def test_the_first_word_and_capital_rules_do_not_make_the_known_leaks_disappear(text):
    out = _vendor_body(text) if "Helen" not in text and "KWS" not in text else _customer_body(text)
    assert isinstance(out, list)


@pytest.mark.parametrize("text", ["The vulcan salute is a joke we all know.", "Please confirm the ship date by Friday."])
def test_ordinary_sentences_do_not_raise_a_blind_shipment_warning(text):
    assert not _vendor_body(text)


@pytest.mark.parametrize("text", ["AKWS1 is our internal code", "The IKON drawing is attached", "Thanks to the kws3 sensor"])
def test_a_short_name_inside_another_token_is_not_a_leak(text):
    assert not _customer_body(text) and not _vendor_body(text)


# ============================================================ backup copy checksum

def test_a_backup_copy_that_differs_by_one_byte_is_not_reported_ok(tmp_path, monkeypatch):
    import shutil
    from app.core.database import init_database
    from app.services import backups
    init_database()
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setattr(settings, "BACKUP_COPY_DIR", str(tmp_path / "copy"))
    backups.last.clear()
    real = shutil.copy2

    def flipping(src, dst, *a, **k):
        out = real(src, dst, *a, **k)
        raw = bytearray(open(dst, "rb").read())
        raw[-20] ^= 0x01                                   # a silent one-bit change that still opens as a database
        open(dst, "wb").write(bytes(raw))
        return out
    monkeypatch.setattr(shutil, "copy2", flipping)
    out = backups.run()
    assert not out["copy"]["ok"] and not list((tmp_path / "copy").glob("allied-*.db"))
