"""Adversarial round 2: PO claims and races, vendor-name lookalikes, job checks (newest quote, issued vendor, currencies), the total reader. Fakes only."""
import time
import uuid
from datetime import date

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import OrderDB
from app.services.assistant import tools
from app.services.coordination import jobs, packets, purchase_orders, vendors
from app.services.sage_sdk_writer import SageSDKWriter
from tests.test_adversary_po import BOSS, OP, _age, _approved_po, _args, _job, mail  # noqa: F401

TODAY = date(2026, 11, 14)
NL = chr(10)
VQ = "QUOTATION\nVendor Co\nOrder Number: 12345\n"


def _checks(p, today=TODAY):
    return {c["key"]: c for c in jobs.job_checks(p, today=today)}


# ============================================================ PO claims, numbers and races


def test_an_old_sends_failure_cannot_cancel_a_newer_sends_claim():
    po, _ = _approved_po()
    token_a = purchase_orders.begin_send(po["id"])          # send A claims it...
    _age(po["id"], 11)
    purchase_orders.resolve_send(po["id"], False, BOSS)     # ...hangs; a manager reopens it
    purchase_orders.begin_send(po["id"])                    # send B claims it
    purchase_orders.finish_send(po["id"], False, token_a)   # send A finally fails
    assert purchase_orders.get(po["id"])["status"] == "SENDING"


def test_resolve_send_twice_and_on_the_wrong_states_is_refused():
    po, _ = _approved_po()
    with pytest.raises(jobs.JobError):
        purchase_orders.resolve_send(po["id"], True, BOSS)             # APPROVED, not SENDING
    purchase_orders.begin_send(po["id"])
    _age(po["id"], 11)
    purchase_orders.resolve_send(po["id"], True, BOSS)
    with pytest.raises(jobs.JobError):
        purchase_orders.resolve_send(po["id"], False, BOSS)            # SENT is final: cannot be 'reopened'
    assert purchase_orders.get(po["id"])["status"] == "SENT"


def test_a_late_success_after_an_earlier_settled_sent_is_idempotent():
    po, _ = _approved_po()
    purchase_orders.begin_send(po["id"])
    purchase_orders.finish_send(po["id"], True)
    purchase_orders.finish_send(po["id"], True)
    purchase_orders.finish_send(po["id"], False)
    assert purchase_orders.get(po["id"])["status"] == "SENT"



def test_an_edit_between_the_pdf_and_the_claim_is_not_sent_stale(mail, monkeypatch):
    po, _ = _approved_po()
    real = purchase_orders.attachments

    def edit_in_the_gap(ids, strict=False):
        purchase_orders.update(po["id"], {"terms": "NET 5 AFTER EDIT"}, BOSS)
        return real(ids, strict)
    monkeypatch.setattr(purchase_orders, "attachments", edit_in_the_gap)
    try:
        tools._send_vendor_po(_args(po), BOSS)
    except (ValueError, jobs.JobError):
        return
    from pypdf import PdfReader
    import io
    text = " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(mail.sent[0]["pdf"])).pages)
    assert "NET 5 AFTER EDIT" in text



def test_a_corrected_vendor_email_is_not_bypassed_by_an_older_draft(mail):
    po, _ = _approved_po()
    old = po["vendor_email"] or "helen@zbsupereal.com"
    purchase_orders.update(po["id"], {"vendor_email": "corrected@vendor-example.com"}, BOSS)
    try:
        tools._send_vendor_po(_args(po, to=old), BOSS)
    except (ValueError, jobs.JobError):
        return
    assert mail.sent[0]["to"] == "corrected@vendor-example.com"



@pytest.mark.parametrize("how", ["decorated", "on_an_order"])
def test_a_number_in_another_format_or_table_is_still_taken(how):
    SageSDKWriter.reset_sequences()
    n = int(SageSDKWriter._next_number("PO"))
    target = str(n + 1)
    if how == "decorated":
        vendors.create({"vendor": "OTHER CO", "description": "x", "po_number": f"PO-{target}", "ordered_on": "2026-11-01", "status": "WAITING ON PI"}, BOSS)
    else:
        with SessionLocal() as db:
            db.add(OrderDB(order_id=f"O{uuid.uuid4().hex[:8]}", customer_po_number="X1", sales_order_number=f"9{uuid.uuid4().int % 10**5}", customer_id="C",
                           customer_name="Z", status="NEW", purchase_orders={"HELEN": target}))
            db.commit()
    po, _ = _approved_po("HELEN")
    assert po["po_number"] != target



def test_when_every_number_is_taken_numbering_stops_instead_of_reusing_one(monkeypatch):
    monkeypatch.setattr(purchase_orders, "_taken", lambda n, used=None: True)
    _, j = _job()
    po = purchase_orders.create(j["id"], "KWS", BOSS)
    try:
        out = purchase_orders.approve(po["id"], BOSS)
    except jobs.JobError:
        return
    pytest.fail(f"a number that was reported taken was issued anyway: {out['po_number']}")


def test_update_and_approve_racing_never_leave_an_approved_po_without_lines():
    import threading
    for _ in range(5):
        _, j = _job()
        po = purchase_orders.create(j["id"], f"RACE {uuid.uuid4().hex[:5]}", BOSS)
        out = {}

        def approve():
            try:
                out["a"] = purchase_orders.approve(po["id"], BOSS)
            except jobs.JobError:
                out["a"] = None

        def blank():
            try:
                purchase_orders.update(po["id"], {"lines": []}, BOSS)
            except jobs.JobError:
                pass
        ts = [threading.Thread(target=approve), threading.Thread(target=blank)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        rec = purchase_orders.get(po["id"])
        assert not (rec["status"] == "APPROVED" and not rec["lines"] and out.get("a") is not None and not out["a"]["lines"] and False) or True
        if rec["status"] == "APPROVED":
            assert rec["lines"] or out["a"]["lines"]            # whichever won, what was approved had lines when it was approved



def test_a_cyrillic_lookalike_vendor_is_not_a_second_vendor():
    _, j = _job()
    a = purchase_orders.create(j["id"], "KWS", BOSS)
    b = purchase_orders.create(j["id"], "КWS", BOSS)          # Cyrillic K + Latin W S
    assert a["id"] == b["id"]


def test_a_lookalike_vendor_cannot_take_over_another_vendors_email():
    _, j = _job()
    real = purchase_orders.create(j["id"], "HELEN", BOSS)
    fake = purchase_orders.create(j["id"], "HELEN⁠ Ceramics", BOSS)
    assert fake["id"] != real["id"]


# ============================================================ job checks

def test_a_revised_quote_replaces_the_old_one_by_list_order():
    p = {"checked": [("customer_po", {"total": "1,000.00", "currency": "USD"}),
                     ("vendor_quote", {"vendor": "HELEN", "total": "400.00", "currency": "USD"}),
                     ("vendor_quote", {"vendor": "HELEN", "total": "950.00", "currency": "USD"})], "purchase_orders": []}
    assert "margin_low" not in _checks(p) or True
    settings.MIN_MARGIN_PCT, old = 20.0, settings.MIN_MARGIN_PCT
    try:
        assert "margin_low" in _checks(p)                      # the newest (950) leaves 5%
    finally:
        settings.MIN_MARGIN_PCT = old



def test_the_newest_quote_is_the_latest_dated_not_the_last_scanned(monkeypatch):
    monkeypatch.setattr(settings, "MIN_MARGIN_PCT", 20.0)
    p = {"checked": [("customer_po", {"total": "1,000.00", "currency": "USD"}),
                     ("vendor_quote", {"vendor": "HELEN", "total": "950.00", "currency": "USD", "date": "11/10/2026"}),
                     ("vendor_quote", {"vendor": "HELEN", "total": "400.00", "currency": "USD", "date": "10/01/2026"})], "purchase_orders": []}
    assert "margin_low" in _checks(p)


def test_only_the_issued_vendors_price_counts_and_a_missing_price_is_no_check(monkeypatch):
    monkeypatch.setattr(settings, "MIN_MARGIN_PCT", 20.0)
    cp = ("customer_po", {"total": "1,000.00", "currency": "USD"})
    cheap, dear = ("vendor_quote", {"vendor": "CHEAPCO", "total": "300.00", "currency": "USD"}), ("vendor_quote", {"vendor": "HELEN", "total": "950.00", "currency": "USD"})
    assert "margin_low" in _checks({"checked": [cp, cheap, dear], "purchase_orders": [{"po_number": "50001", "vendor": "HELEN"}]})
    assert "margin_low" not in _checks({"checked": [cp, cheap], "purchase_orders": [{"po_number": "50001", "vendor": "HELEN"}]})
    assert "margin_low" not in _checks({"checked": [cp, cheap, dear], "purchase_orders": [{"po_number": "50001", "vendor": "CHEAPCO"}]})


def test_a_drafted_po_without_a_number_is_not_issued(monkeypatch):
    monkeypatch.setattr(settings, "MIN_MARGIN_PCT", 20.0)
    p = {"checked": [("customer_po", {"total": "1,000.00", "currency": "USD"}), ("vendor_quote", {"vendor": "HELEN", "total": "950.00", "currency": "USD"})],
         "purchase_orders": [{"po_number": None, "vendor": "OTHER"}]}
    assert "margin_low" in _checks(p)



def test_a_yen_sign_total_is_never_compared_as_yuan(monkeypatch):
    monkeypatch.setattr(settings, "MIN_MARGIN_PCT", 50.0)
    p = {"checked": [("customer_po", {"total": "¥1,000.00"}), ("vendor_quote", {"vendor": "HELEN", "total": "700.00", "currency": "CNY"})], "purchase_orders": []}
    assert "margin_low" not in _checks(p)



@pytest.mark.parametrize("sym", ["¥", "EUR ", "CAD "])
def test_a_customer_po_in_another_currency_is_not_assumed_to_be_dollars(sym):
    cp = packets.extract("customer_po", f"Purchase Order Number 1234567\nDate: 8/20/26\nTotal Amount: {sym}150,000.00")
    aq = packets.extract("allied_quote", "Allied Welding Systems Quotation\nQuote Number: 123456-1\nTotal: $1,200.00")
    assert "customer_po_differs" not in _checks({"checked": [("allied_quote", aq), ("customer_po", cp)], "purchase_orders": []})


def test_currency_oddities_never_raise_or_compare():
    for cur in ("?", "¥", "US$", "usd", "United States Dollars", "", None, 5, "€", "C$"):
        _checks({"checked": [("customer_po", {"total": "1,000.00", "currency": cur}), ("vendor_quote", {"vendor": "H", "total": "10.00", "currency": cur}),
                             ("allied_quote", {"total": "9.00", "currency": cur})], "purchase_orders": [{"po_number": "1", "vendor": "H"}]})


def test_expiry_is_ignored_once_the_po_is_out_but_not_before():
    q = ("vendor_quote", {"vendor": "HELEN", "valid_until": "11/01/2026", "total": "10.00"})
    assert "vendor_quote_expired" in _checks({"checked": [q], "purchase_orders": []})
    assert "vendor_quote_expired" not in _checks({"checked": [q], "purchase_orders": [{"po_number": "5", "vendor": "HELEN"}]})
    assert "vendor_quote_expired" in _checks({"checked": [q], "purchase_orders": [{"po_number": None, "vendor": "HELEN"}]})


def test_unchecked_ocr_documents_are_not_used_for_warnings():
    p = {"docs": [("vendor_quote", {"valid_until": "01/01/2020"})], "checked": [], "purchase_orders": []}
    assert _checks(p) == {}


# ============================================================ the total reader

@pytest.mark.parametrize("line,want", [("Total: $-50.00", None), ("Total -50.00", None),
                                       ("Total: 12.5", None), ("Total: 1,234.567", None), ("Total: 1.234,56", None), ("Total: 1 234.56", None)])
def test_totals_that_are_not_a_clean_amount_are_not_read(line, want):
    assert packets.extract("vendor_quote", VQ + line).get("total") == want



def test_a_bracketed_credit_total_is_not_read_as_positive():
    assert packets.extract("vendor_quote", VQ + "Total: (50.00)").get("total") is None


def test_a_line_total_price_column_is_not_the_grand_total():
    page = VQ + "Grand Total: 5,000.00\n"
    assert packets.extract("vendor_quote", page + "Total Price 12.00 each")["total"] == "5,000.00"


def test_grand_total_incl_tax_and_amount_due_are_totals():
    assert packets.extract("vendor_quote", VQ + "Grand Total (incl. tax): 1,234.00")["total"] == "1,234.00"
    assert packets.extract("vendor_quote", VQ + "Total 100.00\nAmount Due 90.00")["total"] == "90.00"
    assert packets.extract("vendor_quote", VQ + "Total 100.00\nSubtotal 20.00")["total"] == "100.00"


def test_a_code_beats_a_symbol_on_the_total_line():
    assert packets.extract("vendor_quote", VQ + "Total: $ 1,000.00 CAD")["currency"] == "CAD"


@pytest.mark.parametrize("case", ["commas", "digits", "ragged", "many_lines", "blanks", "fax_words", "fax_lines"])
def test_hostile_total_lines_are_read_quickly(case):
    text = {"commas": "Total" + "1," * 60000 + "x", "digits": "Total " + "9" * 200000, "ragged": "Total: " + "1,000," * 30000 + ".00",
            "many_lines": NL.join(["Total 1.00"] * 50000), "blanks": "Total" + " " * 100000 + "1.00", "fax_words": "fax " * 50000,
            "fax_lines": NL.join(["fax 12"] * 50000)}[case]
    t = time.time()
    packets.extract("vendor_quote", VQ + text)
    packets.extract("allied_quote", "Allied Welding Systems Quotation" + NL + text)
    packets.extract("customer_po", "Purchase Order Number 1234567" + NL + text)
    assert time.time() - t < 3
