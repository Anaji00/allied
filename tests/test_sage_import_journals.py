"""Sage 50's own Import/Export template files (the journals and lists) read the way Sage writes them: one row per line of an invoice, bill or order, the
"(X)" mark on export-only headings, TRUE/FALSE columns, credit memos, closed orders. Synthetic files only (scripts/sage_samples/journal); no real data."""
import io
from pathlib import Path

import pytest

from app.services import sage_import as si

JOURNAL = Path(__file__).resolve().parent.parent / "scripts" / "sage_samples" / "journal"
USER = {"email": "journal@alliedweldingsystems.com", "role": "MANAGER"}


def _csv(*lines: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _read(name: str) -> dict:
    return si.parse((JOURNAL / name).read_bytes(), name)


@pytest.mark.parametrize("name, kind", [
    ("customer_list.csv", "customers"), ("vendor_list.csv", "vendors"), ("item_list.csv", "parts"), ("sales_journal.csv", "invoices"),
    ("receipts_journal.csv", "payments"), ("so_journal.csv", "open_sales_orders"), ("po_journal.csv", "open_purchase_orders"), ("purchase_journal.csv", "bills")])
def test_each_sage_template_file_is_recognised_without_being_told(name, kind):
    assert _read(name)["kind"] == kind


def test_the_export_only_mark_is_not_part_of_a_heading():
    assert si._n("Customer Name(X)") == si._n("Customer Name") == "customername"
    assert si._n("Accounts Payable Amount(X)") == si._n("accounts payable amount")


def test_the_lines_of_one_invoice_become_one_invoice_with_the_receivable_amount_as_its_total():
    p = _read("sales_journal.csv")
    good = [r for r in p["rows"] if not r["problems"]]
    assert p["merged_lines"] == 1 and len(good) == 1
    v = good[0]["values"]
    assert good[0]["key"] == "INV-J-1" and v["total"] == "3000.00" and v["sales_order"] == "J12930" and v["customer_po"] == "4500912345" and v["due_date"] == "2026-12-20"
    assert "amount" not in v                                                      # the line "Amount" column is not the invoice total
    memo = next(r for r in p["rows"] if r["key"] == "CM-J-2")
    assert "credit memo" in memo["problems"][0]


def test_a_closed_order_is_kept_with_nothing_open_and_an_open_one_gets_its_whole_amount_open():
    so = _read("so_journal.csv")
    po = _read("po_journal.csv")
    open_so, closed_so = (r["values"] for r in so["rows"])
    assert open_so["open_amount"] == "2500.00" and open_so["amount"] == "2500.00" and closed_so["open_amount"] == "0.00" and so["merged_lines"] == 1
    assert [r["values"]["open_amount"] for r in po["rows"]] == ["2080.00", "0.00"] and po["merged_lines"] == 1


def test_receipts_are_one_payment_per_invoice_paid_and_each_keeps_its_reference():
    p = _read("receipts_journal.csv")
    assert [r["key"] for r in p["rows"]] == ["R-J-1|INV-J-1", "R-J-1|INV-J-9"] and [r["values"]["amount"] for r in p["rows"]] == ["1000.00", "500.00"]
    assert not any(r["problems"] for r in p["rows"])


def test_an_inactive_customer_is_left_out_with_a_reason_and_the_others_read_normally():
    p = _read("customer_list.csv")
    assert p["rows"][0]["problems"] == [] and p["rows"][0]["values"]["email"] == "buyer@journal-demo.example.test" and p["rows"][0]["values"]["sales_rep"] == "KMR"
    assert "inactive" in p["rows"][1]["problems"][0]
    item = _read("item_list.csv")["rows"][0]["values"]
    assert item["base_price"] == "18.50" and item["standard_cost"] == "9.25" and item["stock_on_hand"] == "350" and item["preferred_vendor"] == "J-V001"


def test_rows_of_one_invoice_that_disagree_are_a_problem_not_a_silent_merge():
    head = "Customer ID,Invoice/CM #,Date,Accounts Receivable Amount,Amount"
    p = si.parse(_csv(head, "C1,INV-1,11/25/2026,100.00,-60.00", "C1,INV-1,11/25/2026,100.00,-40.00", "C1,INV-2,11/25/2026,100.00,-100.00", "C1,INV-2,11/26/2026,100.00,-100.00"), "x.csv", "invoices")
    assert p["merged_lines"] == 1                                                  # INV-1: same heading values, different line amounts: one invoice
    bad = [r for r in p["rows"] if r["problems"]]
    assert len(bad) == 1 and "different date" in bad[0]["problems"][0]


def test_when_a_file_has_both_a_total_and_a_line_amount_the_total_wins_whatever_the_column_order():
    a = si.parse(_csv("Vendor ID,Invoice #,Date,Amount,Accounts Payable Amount", "V1,B-1,11/25/2026,10.00,99.00"), "x.csv", "bills")
    b = si.parse(_csv("Vendor ID,Invoice #,Date,Accounts Payable Amount,Amount", "V1,B-1,11/25/2026,99.00,10.00"), "x.csv", "bills")
    assert a["rows"][0]["values"]["amount"] == b["rows"][0]["values"]["amount"] == "99.00"
    assert "Amount" in a["unmapped"] and "Amount" in b["unmapped"]


def test_true_false_columns_accept_sages_spellings_and_refuse_nonsense():
    head = "Customer ID,Invoice/CM #,Date,Accounts Receivable Amount,Credit Memo"
    p = si.parse(_csv(head, "C1,A-1,11/25/2026,5.00,False", "C1,A-2,11/25/2026,5.00,", "C1,A-3,11/25/2026,5.00,TRUE", "C1,A-4,11/25/2026,5.00,maybe"), "x.csv")
    assert [bool(r["problems"]) for r in p["rows"]] == [False, False, True, True]
    assert "TRUE or FALSE" in p["rows"][3]["problems"][0]


def test_the_preview_warns_about_excel_combined_lines_and_how_payments_are_read():
    sales = si.preview((JOURNAL / "sales_journal.csv").read_bytes(), "sales.csv", "auto", USER)
    assert any("combined" in w for w in sales["warnings"]) and sales["file"]["merged_lines"] == 1
    pay = si.preview((JOURNAL / "receipts_journal.csv").read_bytes(), "receipts.csv", "auto", USER)
    assert any("Payment amounts" in w for w in pay["warnings"])
    import openpyxl

    book = openpyxl.Workbook()
    book.active.append(["Invoice/CM #", "Customer ID", "Date", "Invoice Total"])
    book.active.append(["X-1", "C1", "2026-11-20", 5])
    buf = io.BytesIO()
    book.save(buf)
    assert any("Excel" in w and "leading zeros" in w for w in si.preview(buf.getvalue(), "r.xlsx", "auto", USER)["warnings"])


def test_importing_the_journals_gives_paid_state_and_open_flags_and_a_closed_order_is_not_open():
    for name in ("sales_journal.csv", "receipts_journal.csv", "so_journal.csv", "po_journal.csv", "purchase_journal.csv"):
        shown = si.preview((JOURNAL / name).read_bytes(), name, "auto", USER)
        si.commit(shown["token"], USER)
    status = si.order_money_status([{"sales_order_number": "J12930", "customer_po_number": "4500912345"}])["J12930"]
    assert status["paid_status"] == "PART_PAID" and status["amount_due"] == "2000.00" and status["invoiced_total"] == "3000.00"
    assert [f["number"] for f in si.already_open_in_sage("4500777001", ["PO-J-1", "PO-J-2"])] == ["SO-J-1", "PO-J-1"]          # the closed order and closed PO are not flagged
    assert si.already_open_in_sage("4500777002", []) == []
    assert si.bill_in_sage("ZIBO", "ZB-J-1")["amount"] == "2080.00"
