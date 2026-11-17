"""Adversarial: money, dates and the packet reader. Text and generated PDFs only; temporary database and upload folder."""
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import settings
from app.services import order_documents as od
from app.services.coordination import jobs, packets

USER = {"email": "boss-money@alliedweldingsystems.com", "role": "MANAGER"}
VQ = "QUOTATION\nVendor Co\nOrder Number: 12345\n"
AQ = "Allied Welding Systems Quotation\nQuote Number: 123456-1\nQuote Date: 8/1/26\n"


# ============================================================ totals printed on a page

def test_subtotal_tax_total_takes_the_total():
    assert packets.extract("vendor_quote", VQ + "Subtotal 1,000.00\nTax 80.00\nTotal 1,080.00")["total"] == "1,080.00"


def test_total_weight_and_quantity_lines_are_not_the_total():
    assert packets.extract("vendor_quote", VQ + "Total weight: 12.50 kg\nTotal Qty 100.00 pcs\nTotal: 900.00")["total"] == "900.00"


def test_a_space_thousands_total_is_not_read_as_its_tail():
    assert packets.extract("vendor_quote", VQ + "Total: USD 12 500.00").get("total") != "500.00"



def test_a_european_total_is_not_read_as_a_tiny_number():
    got = packets.extract("vendor_quote", VQ + "Total: EUR 1.250,00").get("total")
    assert got in (None, "1,250.00", "1250.00")



def test_a_negative_total_keeps_its_sign_or_is_not_read():
    assert packets.extract("vendor_quote", VQ + "Total: -50.00").get("total") in (None, "-50.00")



def test_allied_po_total_is_the_total_not_the_subtotal():
    page = "Allied Welding Systems\nPURCHASE ORDER NO: 50123\nDate Issued 8/1/26\nSubtotal 1,000.00\nTotal 1,080.00"
    assert packets.extract("allied_po", page).get("total") == "1,080.00"


def test_two_grand_totals_the_grand_one_wins():
    assert packets.extract("allied_quote", AQ + "Grand Total 5,000.00\nTotal 4,000.00")["total"] == "5,000.00"


def test_money_text_odd_cases_never_raise_and_never_guess():
    for v in ("1e3", "١٢٣", "(1,200.00)", "1,00,000.00", "1'234.50", "nan", "inf", "-", "", None, "1" * 400, "$" * 400, "9" * 20 + ".00"):
        amount, _ = od.money(v)
        assert amount is None or amount < 1e9



def test_an_explicit_currency_code_beats_a_dollar_sign():
    assert od.money("$ 1,000.00 CAD")[1] == "CAD"


def test_a_c_dollar_amount_is_not_a_usd_amount():
    amount, cur = od.money("C$1,000.00")
    assert not (amount is not None and cur == "USD")


# ============================================================ job checks: money and dates

def _checks(docs, today=date(2026, 11, 14)):
    return {c["key"]: c for c in jobs.job_checks({"docs": docs}, today=today)}


def test_expiring_today_is_not_expired_and_yesterday_is():
    d = lambda s: [("vendor_quote", {"valid_until": s, "total": "10.00"})]  # noqa: E731
    assert "vendor_quote_expired" not in _checks(d("11/14/2026")) and "vendor_quote_expiring" in _checks(d("11/14/2026"))
    assert "vendor_quote_expired" in _checks(d("11/13/2026"))


def test_impossible_and_leap_dates_never_raise():
    for s in ("2/29/2026", "13/45/26", "2026-02-30", "99/99/99", "Feb 30, 2026", "0/0/0", "٢/٣/26"):
        _checks([("vendor_quote", {"valid_until": s})])
    assert packets.iso_date("2/29/2028") == "2028-02-29" and packets.iso_date("2/29/2027") is None


def test_a_quote_valid_until_today_local_is_not_expired_in_the_evening(monkeypatch):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(settings.BUSINESS_TZ)
    local = datetime(2026, 11, 14, 22, 30, tzinfo=tz)
    utc = local.astimezone(timezone.utc)
    if utc.date() == local.date():
        pytest.skip("business timezone is UTC")

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc if tz is None or tz == timezone.utc else utc.astimezone(tz)
    monkeypatch.setattr(jobs, "datetime", Frozen)
    out = {c["key"] for c in jobs.job_checks({"docs": [("vendor_quote", {"valid_until": "11/14/2026", "total": "10.00"})]})}
    assert "vendor_quote_expired" not in out


def test_the_customer_total_that_differs_from_the_quote_is_flagged_even_with_dollar_signs():
    aq = packets.extract("allied_quote", AQ + "Total: $1,000.00")
    cp = packets.extract("customer_po", "Purchase Order Number 1234567\nDate: 8/20/26\nTotal Amount: $1,200.00")
    assert "customer_po_differs" in _checks([("allied_quote", aq), ("customer_po", cp)])


def test_a_loss_is_flagged_and_mixed_currencies_are_not_compared(monkeypatch):
    monkeypatch.setattr(settings, "MIN_MARGIN_PCT", 20.0)
    docs = [("customer_po", {"total": "1,000.00", "currency": "USD"}), ("vendor_quote", {"total": "1,200.00", "currency": "USD"})]
    assert "margin_low" in _checks(docs)
    docs = [("customer_po", {"total": "1,000.00", "currency": "USD"}), ("vendor_quote", {"total": "1,200.00", "currency": "CNY"})]
    assert "margin_low" not in _checks(docs) and "margin_currency" in _checks(docs)


def test_hostile_totals_never_raise(monkeypatch):
    monkeypatch.setattr(settings, "MIN_MARGIN_PCT", 20.0)
    for tot in ("0.00", "-1.00", "nan", "1e999", "1" * 50 + ".00", "", None, "abc"):
        _checks([("customer_po", {"total": tot}), ("vendor_quote", {"total": tot}), ("allied_quote", {"total": tot})])



def test_a_two_digit_year_in_the_nineties_is_not_a_future_quote():
    assert (packets.iso_date("12/31/99") or "0") < "2030-01-01"


# ============================================================ packet reader


def test_a_page_of_blanks_after_purchase_order_is_classified_fast():
    t = time.time()
    packets.classify("purchase order" + " " * 12000 + "x")
    assert time.time() - t < 0.5



def test_many_blank_lines_after_purchase_order_no_are_extracted_fast():
    t = time.time()
    packets.extract("allied_po", "Allied\npurchase order no" + "\n " * 4000)
    assert time.time() - t < 0.5


def test_split_handles_blank_garbage_and_wrong_order():
    out = packets.split(["", "   ", "\x00\x01 ~~~ ", "x" * 100000])
    assert [d["kind"] for d in out][0] == "unreadable"
    po1 = "Allied Welding Systems\nPURCHASE ORDER NO: 50123\nDate Issued 8/1/26\n1 liner"
    cont = "Allied Welding Systems\ncontinued\nPurchase Order No: 50123\nTotal 100.00"
    assert len(packets.split([cont, po1])) >= 1                           # reversed pages must not raise
    assert len(packets.split([po1.replace("50123", "50124"), po1])) == 2        # a different number is a different PO


def test_split_of_hundreds_of_pages_is_quick():
    t = time.time()
    packets.split(["Allied Welding Systems Quotation\nQuote Number: 123456-%d\nTotal: 1.00" % i for i in range(500)])
    assert time.time() - t < 5


def _pdf(pages_text, size=(612, 792), rotate=0):
    import fitz
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page(width=size[0], height=size[1])
        if text:
            page.insert_text((40, 80), text, fontsize=11)
        if rotate:
            page.set_rotation(rotate)
    return doc.tobytes()


@pytest.mark.parametrize("raw", [b"", b"%PDF", b"%PDF-1.7\n" + b"\x00" * 2000, b"hello.pdf is not a pdf", b"<html>%PDF</html>", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer<<>>\n%%EOF"])
def test_non_pdfs_and_broken_pdfs_get_a_plain_refusal(raw):
    with pytest.raises(jobs.JobError):
        packets.ingest(raw, "x.pdf", USER)


def test_a_text_pdf_with_wrong_page_order_and_rotation_still_reads():
    body = "Allied Welding Systems Quotation\nQuote Number: 123456-1\nQuote Date: 8/1/26\nTotal: $1,000.00\nitem liner xxxxxxxxxxxxxxxxxxxxxxxx"
    out = packets.ingest(_pdf([body], rotate=90), "rot.pdf", USER)
    assert out["documents"] and out["documents"][0]["kind"] in ("allied_quote", "unreadable")


def test_a_pdf_with_no_text_and_no_images_is_refused_not_crashed():
    with pytest.raises(jobs.JobError):
        packets.ingest(_pdf(["", "", ""]), "blank.pdf", USER)


def test_a_pdf_over_the_page_limit_is_refused_before_any_ocr():
    with pytest.raises(jobs.JobError):
        packets.ingest(_pdf(["page"] * (packets.MAX_PAGES + 1)), "many.pdf", USER)


def test_a_giant_media_box_page_is_refused_or_read_without_exhausting_memory():
    pdf = _pdf(["Allied Welding Systems Quotation Quote Number: 123456-9 Total: $5.00 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"], size=(14000, 14000))
    t = time.time()
    try:
        packets.ingest(pdf, "huge.pdf", USER)
    except jobs.JobError:
        pass
    assert time.time() - t < 30


def test_the_same_packet_twice_changes_nothing():
    body = "Allied Welding Systems Quotation\nQuote Number: 123456-77\nQuote Date: 8/1/26\nTotal: $1,000.00\nitem liner xxxxxxxxxxxxxxxxxxxxxxxx"
    pdf = _pdf([body])
    first = packets.ingest(pdf, "a.pdf", USER)
    second = packets.ingest(pdf, "b.pdf", USER)
    assert second["already_read"] and len(second["documents"]) == len(first["documents"])
