"""Quote validity, customer-order vs Allied-quote match, and the margin check. Only printed or entered figures are compared; blanks mean no check."""
from datetime import date

import pytest

from app.config import settings
from app.services.coordination import jobs, packets
from tests.test_hardening import BOSS, _rfq

TODAY = date(2026, 11, 14)


def _keys(docs, **kw):
    return {c["key"]: c for c in jobs.job_checks({"docs": docs}, today=TODAY, **kw)}


def test_an_expired_quotation_warns_and_a_valid_one_does_not():
    assert "vendor_quote_expired" in _keys([("vendor_quote", {"vendor": "ZIBO", "valid_until": "2026-11-01"})])
    assert _keys([("vendor_quote", {"vendor": "ZIBO", "valid_until": "2026-12-31"})]) == {}
    assert "allied_quote_expiring" in _keys([("allied_quote", {"valid_until": "2026-11-18"})])
    assert _keys([("vendor_quote", {"vendor": "ZIBO"})]) == {}                       # no date printed: no check, no guess


@pytest.mark.parametrize("text,expected", [("Valid until: Dec 1, 2026", "Dec 1, 2026"), ("Quote expires on 12/01/26", "12/01/26"), ("good through 2026-12-01", "2026-12-01")])
def test_a_printed_validity_date_is_read_and_a_missing_one_is_not(text, expected):
    assert packets.extract("vendor_quote", "QUOTATION\nVendor\nOrder Number: 0234268\n" + text + "\n")["valid_until"] == expected
    assert "valid_until" not in packets.extract("vendor_quote", "QUOTATION\nVendor\nOrder Number: 0234268\nPayment valid for 30 days\n")


def test_customer_order_total_not_equal_to_the_quote_warns():
    assert "customer_po_differs" in _keys([("allied_quote", {"total": "1,000.00"}), ("customer_po", {"total": "1,200.00"})])
    assert "customer_po_differs" not in _keys([("allied_quote", {"total": "1,000.00"}), ("customer_po", {"total": "1000.00"})])
    assert _keys([("customer_po", {"total": "1,200.00"})]) == {}                     # nothing to compare with


def test_margin_loss_warns_and_a_threshold_is_a_warning_only(monkeypatch):
    docs = [("customer_po", {"total": "1,000.00"}), ("vendor_quote", {"vendor": "A", "total": "1,200.00", "currency": "USD"})]
    assert "loss of 20.0%" in _keys(docs)["margin_low"]["text"]
    ok = [("customer_po", {"total": "1,000.00"}), ("vendor_quote", {"vendor": "A", "total": "900.00", "currency": "USD"})]
    assert "margin_low" not in _keys(ok)
    monkeypatch.setattr(settings, "MIN_MARGIN_PCT", 15.0)
    assert "under the 15% you set" in _keys(ok)["margin_low"]["text"]
    cheaper = ok + [("vendor_quote", {"vendor": "B", "total": "700.00", "currency": "USD"})]
    assert "margin_low" not in _keys(cheaper)                                        # the cheaper vendor's price decides


def test_different_currencies_are_not_compared():
    docs = [("customer_po", {"total": "1,000.00"}), ("vendor_quote", {"vendor": "A", "total": "9,000.00", "currency": "CNY"})]
    k = _keys(docs)
    assert "margin_low" not in k and "margin_currency" in k


def test_the_checks_show_on_the_job_and_a_bad_validity_date_is_refused(client):
    j = jobs.from_rfq(_rfq()["id"], BOSS)
    out = client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={"vendor": "Check Co", "total": "500", "valid_until": "2020-01-01"}).json()
    assert [c["key"] for c in out["checks"]] == ["vendor_quote_expired"]
    assert client.post(f"/api/v1/jobs/{j['id']}/vendor-quotes", json={"vendor": "Check Co", "valid_until": "someday"}).status_code == 400
    assert client.get(f"/api/v1/jobs/{j['id']}").json()["checks"][0]["level"] == "warn"


def test_a_vendor_price_with_no_stated_currency_is_never_compared_with_dollars():
    docs = [("customer_po", {"total": "3,000.00"}), ("vendor_quote", {"vendor": "A", "total": "8,000.00"})]       # 8,000 could be RMB
    k = _keys(docs)
    assert "margin_low" not in k and "margin_currency" in k
    assert "margin_low" in _keys([("customer_po", {"total": "3,000.00"}), ("vendor_quote", {"vendor": "A", "total": "8,000.00", "currency": "USD"})])


@pytest.mark.parametrize("text,expected", [
    ("Item A 100.00\nSub-total 900.00\nTotal Weight 12.50 kg\nTotal Tax 85.00\nTotal Freight 150.00\nGrand Total USD 1,135.00\n", ("1,135.00", "USD")),
    ("Total Qty 12\nSubtotal 500.00\nTOTAL 500.00\n", ("500.00", None)),
    ("Page total 40.00\nTotal 1,200.00\nPage 2 of 2\n", ("1,200.00", None)),
    ("Total Weight 12.50 kg\nSub-total 10.00\n", (None, None)),
])
def test_the_printed_total_is_the_grand_total_not_a_weight_tax_or_subtotal(text, expected):
    f = packets._total(text)
    assert (f.get("total"), f.get("currency")) == expected


def test_the_last_page_of_a_quote_decides_its_total():
    docs = packets.split(["ALLIED\nQUOTATION\nQuote Number: 05082026-2\nTotal 100.00\n", "ALLIED\nQUOTATION\nQuote Number: 05082026-2\nPage 2\nGrand Total 450.00\n"])
    assert len(docs) == 1 and docs[0]["fields"]["total"] == "450.00"


def test_the_newest_quote_and_the_right_vendor_are_compared_and_old_expiries_go_quiet():
    two = [("allied_quote", {"total": "1,000.00"}), ("allied_quote", {"total": "1,200.00"}), ("customer_po", {"total": "1,200.00"})]
    assert "customer_po_differs" not in _keys(two)                                                  # the revised quote is the one in force
    expired = [("vendor_quote", {"vendor": "Z", "valid_until": "2026-01-01"})]
    assert "vendor_quote_expired" in _keys(expired)
    assert "vendor_quote_expired" not in {c["key"]: c for c in jobs.job_checks({"docs": expired, "purchase_orders": [{"po_number": "9001", "vendor": "Z"}]}, today=TODAY)}


def test_facts_nobody_has_checked_do_not_raise_warnings():
    p = {"docs": [("vendor_quote", {"vendor": "Z", "valid_until": "2026-01-01"})], "checked": []}
    assert jobs.job_checks(p, today=TODAY) == []
    p["checked"] = p["docs"]
    assert jobs.job_checks(p, today=TODAY)[0]["key"] == "vendor_quote_expired"
