"""Adversarial: the purchase-order lifecycle and the vendor send. Fake mail only; temporary database only."""
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import SessionLocal
from app.models.db_models import PurchaseOrderDB
from app.services import order_documents
from app.services.assistant import tools
from app.services.coordination import jobs, purchase_orders, rfqs, vendors
from app.services.sage_sdk_writer import SageSDKWriter

BOSS = {"email": "boss-adv@alliedweldingsystems.com", "role": "MANAGER"}
OP = {"email": "op-adv@alliedweldingsystems.com", "role": "OPERATOR"}


def _job(approved=True):
    r = rfqs.create({"customer": "Owens Corning", "subject": f"adv part {uuid.uuid4().hex[:6]}",
                     "items": [{"description": "liner", "quantity": 2}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    if approved:
        jobs.approve_design(j["id"], BOSS)
    return r, j


def _approved_po(vendor="HELEN", job=None):
    j = job or _job()[1]
    po = purchase_orders.create(j["id"], vendor, BOSS)
    return purchase_orders.approve(po["id"], BOSS), j


class FakeMail:
    def __init__(self, status="SENT", boom=False, delay=0.0):
        self.sent, self.status, self.boom, self.delay = [], status, boom, delay

    def send_purchase_order(self, vendor_name, vendor_email, po_number, body, pdf_bytes=None, extra_attachments=None):
        if self.delay:
            time.sleep(self.delay)
        if self.boom:
            raise RuntimeError("smtp down")
        self.sent.append({"vendor": vendor_name, "to": vendor_email, "po": po_number, "pdf": pdf_bytes})
        return {"status": self.status}


@pytest.fixture()
def mail(monkeypatch):
    from app.services.outbound_email_service import outbound_email_service
    fake = FakeMail()
    monkeypatch.setattr(outbound_email_service, "send_purchase_order", fake.send_purchase_order)
    return fake


def _args(po, **over):
    return {"to": "helen@zbsupereal.com", "vendor_name": po["vendor"], "po_number": po["po_number"], "po_id": po["id"], "body": "Please see {po_number}", **over}


def test_a_send_that_raises_puts_the_po_back_and_it_can_be_sent_again(mail):
    po, _ = _approved_po()
    mail.boom = True
    with pytest.raises(RuntimeError):
        tools._send_vendor_po(_args(po), BOSS)
    assert purchase_orders.get(po["id"])["status"] == "APPROVED"
    mail.boom = False
    tools._send_vendor_po(_args(po), BOSS)
    assert purchase_orders.get(po["id"])["status"] == "SENT" and len(mail.sent) == 1


def test_a_second_send_of_a_sent_po_is_refused(mail):
    po, _ = _approved_po()
    tools._send_vendor_po(_args(po), BOSS)
    with pytest.raises(ValueError):
        tools._send_vendor_po(_args(po), BOSS)
    assert len(mail.sent) == 1


def test_two_sends_at_once_email_the_vendor_once(mail):
    po, _ = _approved_po()
    mail.delay = 0.3
    res = []

    def go():
        try:
            tools._send_vendor_po(_args(po), BOSS)
            res.append("ok")
        except Exception as exc:  # noqa: BLE001
            res.append(type(exc).__name__)
    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(mail.sent) == 1 and res.count("ok") == 1


def test_six_pos_approved_at_once_get_six_different_numbers():
    _, j = _job()
    pos = [purchase_orders.create(j["id"], f"VENDOR NUMBER {i} {uuid.uuid4().hex[:4]}", BOSS) for i in range(6)]
    out = []
    ts = [threading.Thread(target=lambda p=p: out.append(purchase_orders.approve(p["id"], BOSS)["po_number"])) for p in pos]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(out) == 6 and len(set(out)) == 6


def test_a_sending_po_cannot_be_edited_re_emailed_or_acknowledged():
    po, _ = _approved_po()
    purchase_orders.begin_send(po["id"])
    for call in (lambda: purchase_orders.update(po["id"], {"terms": "Net 1"}, BOSS), lambda: purchase_orders.email_draft(po["id"], BOSS),
                 lambda: purchase_orders.acknowledge(po["id"], BOSS), lambda: purchase_orders.begin_send(po["id"]),
                 lambda: purchase_orders.resolve_send(po["id"], True, BOSS)):
        with pytest.raises((jobs.JobError, ValueError)):
            call()


def _age(po_id, minutes):
    with SessionLocal() as db:
        db.get(PurchaseOrderDB, po_id).sent_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        db.commit()


def test_an_interrupted_send_is_never_resent_on_a_guess_until_a_manager_settles_it():
    po, _ = _approved_po()
    purchase_orders.begin_send(po["id"])
    _age(po["id"], 11)
    with pytest.raises(ValueError, match="interrupted"):
        purchase_orders.begin_send(po["id"])
    assert purchase_orders.resolve_send(po["id"], False, BOSS)["status"] == "APPROVED"
    purchase_orders.begin_send(po["id"])



def test_a_slow_send_that_finishes_after_it_was_reopened_is_still_recorded_as_sent():
    po, _ = _approved_po()
    purchase_orders.begin_send(po["id"])
    _age(po["id"], 11)
    purchase_orders.resolve_send(po["id"], False, BOSS)       # a manager reopens it while the first send is still running
    purchase_orders.finish_send(po["id"], True)               # ...and the first send then completes: the vendor HAS the mail
    assert purchase_orders.get(po["id"])["status"] == "SENT"



def test_the_email_must_describe_the_po_record_it_carries(mail):
    po, _ = _approved_po()
    try:
        tools._send_vendor_po(_args(po, po_number="99999", vendor_name="SOME OTHER VENDOR"), BOSS)
    except (ValueError, LookupError):
        return
    assert mail.sent[0]["po"] == po["po_number"] and mail.sent[0]["vendor"] == po["vendor"]



def test_a_sent_po_cannot_be_sent_again_through_a_draft_that_lacks_the_record_id(mail):
    po, _ = _approved_po()
    tools._send_vendor_po(_args(po), BOSS)
    args = _args(po)
    args.pop("po_id")
    try:
        tools._send_vendor_po(args, BOSS)
    except (ValueError, LookupError):
        pass
    assert len(mail.sent) == 1



def test_editing_po_id_on_a_waiting_draft_to_another_vendors_po_is_refused(mail):
    a, _ = _approved_po("HELEN")
    b, _ = _approved_po("KWS")
    try:
        tools._send_vendor_po({**_args(a), "po_id": b["id"]}, BOSS)
    except (ValueError, LookupError):
        return
    assert mail.sent[0]["po"] == b["po_number"]       # sent HELEN's number with KWS's PDF



def test_a_new_po_number_never_equals_a_po_number_already_on_a_vendor_line():
    SageSDKWriter.reset_sequences()
    nxt = int(SageSDKWriter._next_number("PO"))                  # consumes nxt, the next will be nxt+1
    vendors.create({"vendor": "OTHER CO", "description": "typed by hand", "po_number": str(nxt + 1), "ordered_on": "2026-11-01", "status": "WAITING ON PI"}, BOSS)
    po, _ = _approved_po("HELEN")
    assert po["po_number"] != str(nxt + 1)


def test_a_number_the_database_refuses_is_retried_and_a_draft_never_looks_approved(monkeypatch):
    first, _ = _approved_po("HELEN")
    _, j = _job()
    p2 = purchase_orders.create(j["id"], "KWS", BOSS)
    monkeypatch.setattr(purchase_orders, "_next_number", lambda: first["po_number"])         # always a taken number
    with pytest.raises(jobs.JobError):
        purchase_orders.approve(p2["id"], BOSS)
    assert purchase_orders.get(p2["id"])["status"] == "DRAFT" and purchase_orders.get(p2["id"])["po_number"] is None



def test_vendor_names_that_differ_only_in_punctuation_are_one_vendor():
    _, j = _job()
    a = purchase_orders.create(j["id"], "KWS", BOSS)
    b = purchase_orders.create(j["id"], "K.W.S.", BOSS)
    assert a["id"] == b["id"]


def test_lookalike_vendors_are_not_merged():
    _, j = _job()
    a = purchase_orders.create(j["id"], "Shanghai Steel", BOSS)
    b = purchase_orders.create(j["id"], "Shanghai Metal", BOSS)
    c = purchase_orders.create(j["id"], "Shanghai Steel Works", BOSS)
    assert len({a["id"], b["id"], c["id"]}) == 3



def test_an_absurd_vendor_name_is_refused_or_clipped():
    _, j = _job()
    try:
        po = purchase_orders.create(j["id"], "V" * 20000 + "\x00\x1b[31m", BOSS)
    except jobs.JobError:
        return
    assert len(po["vendor"]) <= 256 and "\x00" not in po["vendor"]


def test_a_po_with_many_lines_and_odd_scripts_still_makes_a_pdf():
    _, j = _job()
    po = purchase_orders.create(j["id"], "BIGCO", BOSS)
    lines = [{"quantity": 1, "description": "liner ‮ RTL 中文 ไทย", "unit_cost": "1.00"} for _ in range(100)]
    purchase_orders.update(po["id"], {"lines": lines}, BOSS)
    assert purchase_orders.pdf(po["id"]).startswith(b"%PDF")



def test_one_huge_line_description_cannot_make_a_po_unsendable():
    _, j = _job()
    po = purchase_orders.create(j["id"], "BIGCO2", BOSS)
    try:
        purchase_orders.update(po["id"], {"lines": [{"quantity": 1, "description": "long " * 4000, "unit_cost": "1.00"}]}, BOSS)
    except jobs.JobError:
        return                                            # refusing it up front is fine
    assert purchase_orders.pdf(po["id"]).startswith(b"%PDF")



def test_negative_po_quantities_are_refused():
    for bad in ({"quantity": -5, "description": "x", "unit_cost": "10"}, {"quantity": 0, "description": "y", "unit_cost": "10"}, {"quantity": 1, "description": "z", "unit_cost": "-3"}):
        with pytest.raises(ValueError):
            order_documents.po_lines([bad], strict=True)              # lines a person typed; lines converted from an order keep credits



def test_line_amount_is_exact_money():
    assert order_documents.po_lines([{"quantity": 3, "description": "x", "unit_cost": "0.10"}])[0]["amount"] in (0.3, "0.30")


def test_vendor_email_with_a_second_address_or_header_injection_is_refused():
    po, _ = _approved_po()
    for bad in ("a@b.com, c@d.com", "a@b.com\nBcc: x@y.com", "a@b.com;c@d.com", "Name <a@b.com>", "a@b"):
        with pytest.raises(jobs.JobError):
            purchase_orders.update(po["id"], {"vendor_email": bad}, BOSS)


def test_an_operator_cannot_redirect_an_approved_po():
    po, _ = _approved_po()
    with pytest.raises(jobs.JobError):
        purchase_orders.update(po["id"], {"vendor_email": "evil@example.com"}, OP)
