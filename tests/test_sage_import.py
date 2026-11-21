"""Import from Sage 50 export files: tolerant headers, strict values, a preview that saves nothing, an idempotent commit, hostile files, roles, and the
three places the mirrors are used (paid or unpaid on an order, a bill Sage already holds, an order already open in Sage).
Synthetic files, the throwaway test database and the throwaway catalogue copies only. Nothing opens a Sage company file."""
import io
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.db_models import AuditLogDB, SageBillDB, SageImportDB, SageInvoiceDB, SageOpenOrderDB, SagePaymentDB
from app.services import sage_import as si
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:6].upper()
SAMPLES = Path(__file__).resolve().parent.parent / "scripts" / "sage_samples"
USER = {"email": "importer@alliedweldingsystems.com", "role": "MANAGER"}


def _csv(*lines: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _counts() -> dict:
    with SessionLocal() as db:
        return {m.__tablename__: db.query(m).count() for m in (SageImportDB, SageInvoiceDB, SagePaymentDB, SageOpenOrderDB, SageBillDB)}


def _commit(data: bytes, kind: str = "auto", name: str = "f.csv", **kw) -> dict:
    shown = si.preview(data, name, kind, USER, **kw)
    return si.commit(shown["token"], USER)


# ===================================================================== headers

def test_headers_match_whatever_the_case_spacing_punctuation_or_bom():
    assert si._n("﻿Invoice/CM #") == si._n("  invoice  cm# ") == si._n("INVOICE/CM #") == "invoicecm"
    wide = _csv("﻿invoice number ; CUSTOMER id;DATE;invoice total".replace(";", ","), "A-1,C1,2026-11-20,10.00")
    assert si.parse(wide, "x.csv")["kind"] == "invoices"
    tabbed = b"Invoice #\tCustomer ID\tDate\tInvoice Total\nA-1\tC1\t2026-11-20\t10.00\n"
    assert si.parse(tabbed, "x.txt")["kind"] == "invoices"
    semi = b"Invoice #;Customer ID;Date;Invoice Total\nA-1;C1;2026-11-20;10.00\n"
    assert si.parse(semi, "x.csv")["rows"][0]["values"]["total"] == "10.00"


def test_an_excel_file_reads_like_a_csv_including_its_date_cells():
    import datetime

    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Sage 50 report"])                                         # a title line above the headings, as Sage reports have
    sheet.append(["Invoice/CM #", "Customer ID", "Date", "Invoice Total", "Amount Due"])
    sheet.append(["X-1", "C1", datetime.datetime(2026, 11, 20), 1200.5, 0])
    buf = io.BytesIO()
    book.save(buf)
    p = si.parse(buf.getvalue(), "report.xlsx")
    row = p["rows"][0]
    assert p["kind"] == "invoices" and row["values"]["date"] == "2026-11-20" and row["values"]["total"] == "1200.50" and row["values"]["amount_due"] == "0.00"


def test_all_eight_kinds_have_required_columns_listed():
    listed = {k["kind"]: k for k in si.kinds()}
    assert set(listed) == {"parts", "customers", "vendors", "invoices", "payments", "open_sales_orders", "open_purchase_orders", "bills"}
    assert "Invoice/CM #" in listed["invoices"]["columns"].values()


@pytest.mark.parametrize("name", ["parts", "customers", "vendors", "invoices", "payments", "open_sales_orders", "open_purchase_orders", "bills"])
def test_every_sample_file_is_recognised_and_clean(name):
    p = si.parse((SAMPLES / f"{name}.csv").read_bytes(), f"{name}.csv")
    assert p["kind"] == name and p["rows"] and all(not r["problems"] for r in p["rows"]), [r["problems"] for r in p["rows"]]
    assert not p["unmapped"] or name == "parts"


def test_unrecognised_headings_are_refused_naming_the_columns_never_guessed():
    with pytest.raises(si.ImportRefused) as e:
        si.parse(_csv("Foo,Bar,Baz", "1,2,3"), "x.csv")
    assert "'Foo'" in str(e.value) and "'Bar'" in str(e.value)
    with pytest.raises(si.ImportRefused) as e:                                  # some recognised, a required one missing
        si.parse(_csv("Invoice #,Date,Mystery Column", "A-1,2026-11-20,x"), "x.csv", "invoices")
    assert "Missing columns" in str(e.value) and "Customer ID" in str(e.value) and "Mystery Column" in str(e.value) and "Nothing was read" in str(e.value)
    with pytest.raises(si.ImportRefused) as e:                                  # fits two kinds equally: the person must say which
        si.parse(_csv("Invoice #,Customer ID,Vendor ID,Date,Amount", "A-1,C1,V1,2026-11-20,5.00"), "x.csv")
    assert "Choose which one" in str(e.value)
    assert si.parse(_csv("Invoice #,Customer ID,Vendor ID,Date,Amount", "A-1,C1,V1,2026-11-20,5.00"), "x.csv", "bills")["kind"] == "bills"
    with pytest.raises(si.ImportRefused):
        si.parse(_csv("Invoice #,Customer ID,Date,Invoice Total", "A-1,C1,2026-11-20,5.00"), "x.csv", "no_such_kind")


# ===================================================================== values

def test_dates_are_read_strictly_and_an_ambiguous_one_is_not_guessed():
    base = "Invoice #,Customer ID,Date,Invoice Total"
    ok = si.parse(_csv(base, "A-1,C1,11/25/2026,1.00", "A-2,C1,02/03/2026,1.00"), "x.csv")          # 11/25 settles the file as month-first
    assert ok["date_order"] == "mdy" and [r["values"]["date"] for r in ok["rows"]] == ["2026-11-25", "2026-02-03"]
    amb = si.parse(_csv(base, "A-1,C1,03/04/2026,1.00"), "x.csv")                                   # nothing settles it
    assert amb["rows"][0]["problems"] and "month/day or day/month" in amb["rows"][0]["problems"][0]
    forced = si.parse(_csv(base, "A-1,C1,03/04/2026,1.00"), "x.csv", date_order="mdy")
    assert forced["rows"][0]["values"]["date"] == "2026-03-04"
    day_first = si.parse(_csv(base, "A-1,C1,25/11/2026,1.00", "A-2,C1,03/04/2026,1.00"), "x.csv")
    assert day_first["date_order"] == "dmy" and day_first["rows"][1]["values"]["date"] == "2026-04-03"
    mixed = si.parse(_csv(base, "A-1,C1,25/11/2026,1.00", "A-2,C1,11/25/2026,1.00"), "x.csv")
    assert all(r["problems"] for r in mixed["rows"]) and "both month-first and day-first" in mixed["rows"][0]["problems"][0]
    bad = si.parse(_csv(base, "A-1,C1,2026-02-30,1.00", "A-2,C1,13/45/2026,1.00", "A-3,C1,yesterday,1.00", "A-4,C1,,1.00"), "x.csv")
    assert all(r["problems"] for r in bad["rows"])
    with pytest.raises(si.ImportRefused):
        si.parse(_csv(base, "A-1,C1,11/25/2026,1.00"), "x.csv", date_order="ymd")


def test_money_goes_through_the_one_reader_and_europe_or_text_is_a_problem_row():
    base = "Invoice #,Customer ID,Date,Invoice Total"
    p = si.parse(_csv(base, 'A-1,C1,2026-11-20,"$1,200.50"', "A-2,C1,2026-11-20,(300.00)", "A-3,C1,2026-11-20,1.234,50".replace("1.234,50", '"1.234,50"'),
                      "A-4,C1,2026-11-20,about 50", "A-5,C1,2026-11-20,1e999", "A-6,C1,2026-11-20,NaN"), "x.csv")
    values = [r["values"].get("total") for r in p["rows"]]
    assert values[0] == "1200.50" and values[1] == "-300.00"
    assert all(r["problems"] for r in p["rows"][2:]), [r["problems"] for r in p["rows"][2:]]


def test_spreadsheet_formulas_in_text_are_neutralised_and_control_characters_dropped():
    p = si.parse(_csv("Invoice #,Customer ID,Customer,Date,Invoice Total", '=HYPERLINK("http://x"),C1,"+cmd|\' /c calc",2026-11-20,1.00',
                      "A-2,C1,@SUM(1+1),2026-11-20,1.00"), "x.csv")
    first, second = (r["values"] for r in p["rows"])
    assert first["invoice_number"].startswith("'=") and first["customer_name"].startswith("'+") and second["customer_name"].startswith("'@")
    nul = si.parse(b"Invoice #,Customer ID,Customer,Date,Invoice Total\nA-1,C1,Bad\x07Name\x1f,2026-11-20,1.00\n", "x.csv")
    assert "\x07" not in nul["rows"][0]["values"]["customer_name"]


def test_a_blank_required_value_and_a_duplicate_are_problem_rows_with_reasons():
    p = si.parse(_csv("Invoice #,Customer ID,Date,Invoice Total", "A-1,C1,2026-11-20,5.00", "A-1,C1,2026-11-21,6.00", ",C1,2026-11-20,5.00", "A-3,,2026-11-20,5.00"), "x.csv")
    reasons = [" ".join(r["problems"]) for r in p["rows"]]
    assert reasons[0] == "" and "appears twice" in reasons[1] and "invoice number is blank" in reasons[2] and "customer id is blank" in reasons[3]


# ===================================================================== hostile files

def test_hostile_files_are_refused_plainly(monkeypatch):
    with pytest.raises(si.ImportRefused):
        si.parse(b"", "x.csv")
    with pytest.raises(si.ImportRefused):
        si.parse(b"   \r\n  \r\n", "x.csv")
    with pytest.raises(si.ImportRefused):
        si.parse(b"MZ\x00\x00\x03\x00\x00\x00\x04\x00" + b"\x00" * 3000, "x.csv")                  # a program, not a table
    with pytest.raises(si.ImportRefused):
        si.parse(b"<html><body>Not a report</body></html>", "x.csv")
    with pytest.raises(si.ImportRefused):
        si.parse(b"PK\x03\x04 this is not a real workbook", "x.xlsx")
    with pytest.raises(si.ImportRefused):
        si.parse(_csv("Invoice #,Customer ID,Date,Invoice Total"), "x.csv")                                # headings only
    monkeypatch.setattr(si, "MAX_BYTES", 200)
    with pytest.raises(si.ImportRefused) as e:
        si.parse(b"x" * 500, "x.csv")
    assert "larger than" in str(e.value)
    monkeypatch.setattr(si, "MAX_BYTES", 5 * 1024 * 1024)
    monkeypatch.setattr(si, "MAX_ROWS", 3)
    with pytest.raises(si.ImportRefused) as e:
        si.parse(_csv("Invoice #,Customer ID,Date,Invoice Total", *[f"A-{i},C1,2026-11-20,1.00" for i in range(30)]), "x.csv")
    assert "More than" in str(e.value)


def test_a_huge_cell_is_cut_and_a_bom_latin1_file_still_reads():
    long = "x" * 5000
    p = si.parse(_csv("Invoice #,Customer ID,Customer,Date,Invoice Total", f"A-1,C1,{long},2026-11-20,1.00"), "x.csv")
    assert len(p["rows"][0]["values"]["customer_name"]) <= 200
    latin = "Invoice #,Customer ID,Customer,Date,Invoice Total\nA-1,C1,Caf\xe9 M\xfcller,2026-11-20,1.00\n".encode("latin-1")
    assert si.parse(latin, "x.csv")["rows"][0]["values"]["customer_name"] == "Café Müller"


# ===================================================================== preview saves nothing, commit is idempotent

def test_preview_saves_nothing_and_commit_saves_once():
    data = _csv("Invoice/CM #,Customer ID,Customer,Date,Reference,Sales Order Number,Invoice Total,Amount Due",
                f"IV-{TAG}-1,C1,Demo Co,2026-11-20,PO-{TAG},,\"$2,000.00\",$500.00", f"IV-{TAG}-2,C1,Demo Co,2026-11-21,,,100.00,100.00", f"IV-{TAG}-3,C1,Demo Co,not a date,,,5.00,5.00")
    before = _counts()
    shown = si.preview(data, "inv.csv", "invoices", USER)
    assert _counts() == before and shown["token"]                                                  # nothing saved by a preview
    assert shown["counts"] == {"new": 2, "changed": 0, "unchanged": 0, "problems": 1} and shown["rows"]["problems"][0]["row"] == 4
    assert "not a date" in shown["rows"]["problems"][0]["reason"] and shown["kind"] == "invoices" and not shown["already_imported"]

    done = si.commit(shown["token"], USER)
    after = _counts()
    assert done["created"] == 2 and done["problems"] == 1 and after["sage_invoices"] == before["sage_invoices"] + 2 and after["sage_imports"] == before["sage_imports"] + 1

    again = si.preview(data, "inv.csv", "invoices", USER)                                         # the same file again: nothing new
    assert again["already_imported"] and again["counts"]["unchanged"] == 2
    second = si.commit(again["token"], USER)
    assert second["already_imported"] and second["created"] == 0 and _counts() == after
    with pytest.raises(si.ImportRefused):
        si.commit(shown["token"], USER)                                                           # a used token is gone

    with SessionLocal() as db:
        who = db.query(AuditLogDB).filter(AuditLogDB.action == "SAGE_IMPORT").order_by(AuditLogDB.id.desc()).first()
    assert who is not None and who.actor_email == USER["email"] and "inv.csv" in who.detail
    assert si.history(5)[0]["file_name"] == "inv.csv" and si.history(5)[0]["imported_by"] == USER["email"]


def test_a_changed_row_updates_in_place_and_a_missing_column_leaves_the_stored_value_alone():
    head = "Invoice/CM #,Customer ID,Date,Invoice Total,Amount Due"
    _commit(_csv(head, f"CH-{TAG},C1,2026-11-20,1000.00,1000.00"))
    shown = si.preview(_csv(head, f"CH-{TAG},C1,2026-11-20,1000.00,250.00"), "paid_some.csv", "invoices", USER)
    assert shown["counts"]["changed"] == 1 and shown["rows"]["changed"][0]["changes"] == [{"field": "amount_due", "old": "1000.00", "new": "250.00"}]
    si.commit(shown["token"], USER)
    with SessionLocal() as db:
        assert db.query(SageInvoiceDB).filter(SageInvoiceDB.invoice_number == f"CH-{TAG}").count() == 1
        assert db.query(SageInvoiceDB).filter(SageInvoiceDB.invoice_number == f"CH-{TAG}").one().amount_due == Decimal("250.00")
    thin = si.preview(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"CH-{TAG},C1,2026-11-20,1000.00"), "thin.csv", "invoices", USER)
    assert thin["counts"]["unchanged"] == 1                                                         # no Amount Due column in this file: the stored 250.00 stays


def test_payments_without_a_receipt_number_are_keyed_by_their_content_and_a_repeat_is_flagged():
    head = "Date,Customer ID,Invoice #,Amount Paid"
    p = si.parse(_csv(head, "2026-11-20,C1,INV-1,100.00", "2026-11-20,C1,INV-1,100.00", "2026-11-20,C1,INV-2,0.00"), "x.csv")
    assert p["kind"] == "payments" and not p["rows"][1]["problems"] and p["rows"][0]["key"] != p["rows"][1]["key"]       # two identical payments with no receipt are two payments
    assert "zero" in " ".join(p["rows"][2]["problems"])


def test_parts_customers_and_vendors_go_through_the_catalogue_importer_and_a_second_run_changes_nothing():
    cust = _csv("Customer ID,Customer,Bill to Address-Line One,Bill to City,Bill to State,Bill to Zip,Terms,E-mail",
                f"SI-{TAG},Sage Import Test {TAG},1 Main St,Dallas,TX,75001,Net 30 Days,buyer@test.example")
    shown = si.preview(cust, "customers.csv", "customers", USER)
    assert shown["counts"]["new"] == 1
    done = si.commit(shown["token"], USER)
    assert done["created"] == 1
    from app.core.container import get_entity_matcher

    assert any(r.get("customer_id") == f"SI-{TAG}" for r in get_entity_matcher().customers_cache.values()) or f"SI-{TAG}" in get_entity_matcher().customers_cache
    cust2 = _csv("Customer ID,Customer,Bill to Address-Line One,Bill to City,Bill to State,Bill to Zip,Terms,E-mail",
                 f"SI-{TAG},Sage Import Test {TAG},1 Main St,Dallas,TX,75001,Net 30 Days,buyer@test.example,")
    again = si.preview(cust2.replace(b",\r\n", b"\r\n"), "customers_again.csv", "customers", USER)
    assert again["counts"]["unchanged"] == 1 and again["counts"]["new"] == 0
    parts = _csv("Item ID,Description,Last Unit Cost,Price Level 1", f"SIP-{TAG},SAGE IMPORT TEST PART,10.50,19.99")
    assert si.preview(parts, "parts.csv", "parts", USER)["counts"]["new"] == 1
    assert si.commit(si.preview(parts, "parts.csv", "parts", USER)["token"], USER)["created"] == 1
    assert si.preview(_csv("Item ID,Description,Last Unit Cost,Price Level 1", f"SIP-{TAG},SAGE IMPORT TEST PART,10.50,19.99"), "parts2.csv", "parts", USER)["counts"]["unchanged"] == 1


# ===================================================================== roles over HTTP

@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"sage-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("ADMIN", "MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1/sage-import" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r
    return call


def test_roles_the_operator_previews_the_manager_imports_nobody_else_gets_in(api):
    data = _csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"RL-{TAG},C1,2026-11-20,10.00")
    files = lambda: {"file": ("inv.csv", data, "text/csv")}                                         # noqa: E731
    api("POST", "/preview", role="VIEWER", expect=403, files=files(), data={"kind": "auto"})
    api("GET", "/history", role="VIEWER", expect=403)
    api("GET", "/kinds", role="VIEWER", expect=403)
    before = _counts()
    shown = api("POST", "/preview", role="OPERATOR", files=files(), data={"kind": "auto"}).json()
    assert _counts() == before and shown["kind"] == "invoices" and shown["counts"]["new"] == 1
    api("POST", "/commit", role="OPERATOR", expect=403, json={"token": shown["token"]})
    api("POST", "/commit", role="VIEWER", expect=403, json={"token": shown["token"]})
    assert _counts() == before
    done = api("POST", "/commit", role="MANAGER", json={"token": shown["token"]}).json()
    assert done["created"] == 1
    assert api("GET", "/history", role="OPERATOR").json()[0]["file_name"] == "inv.csv"
    assert len(api("GET", "/kinds", role="OPERATOR").json()) == 8
    api("POST", "/commit", role="MANAGER", expect=422, json={"token": "not-a-token"})
    api("POST", "/commit", role="MANAGER", expect=422, json={})


def test_refusals_over_http_are_plain_422_and_an_oversized_upload_is_413(api, monkeypatch):
    bad = api("POST", "/preview", expect=422, files={"file": ("x.csv", b"Foo,Bar\n1,2\n", "text/csv")}, data={"kind": "auto"}).json()["detail"]
    assert "'Foo'" in bad and isinstance(bad, str)
    api("POST", "/preview", expect=422, files={"file": ("x.csv", b"", "text/csv")}, data={"kind": "auto"})
    api("POST", "/preview", expect=422, files={"file": ("x.csv", _csv("Invoice #,Customer ID,Date,Invoice Total", "A,C,2026-11-20,1"), "text/csv")}, data={"kind": "nonsense"})
    monkeypatch.setattr(si, "MAX_BYTES", 100)
    api("POST", "/preview", expect=413, files={"file": ("big.csv", b"a,b\n" * 200, "text/csv")}, data={"kind": "auto"})


# ===================================================================== using the mirrors

def test_an_order_shows_paid_part_paid_or_unpaid_and_what_is_due_from_the_imported_invoice():
    so1, so2, so3 = f"9{TAG[:4]}1", f"9{TAG[:4]}2", f"9{TAG[:4]}3"
    _commit(_csv("Invoice/CM #,Customer ID,Date,Invoice Total,Amount Due,Sales Order Number,Reference",
                 f"OM-{TAG}-1,C1,2026-11-20,1000.00,0.00,{so1},",
                 f"OM-{TAG}-2,C1,2026-11-20,1000.00,400.00,{so2},",
                 f"OM-{TAG}-3,C1,2026-11-20,1000.00,1000.00,,CPO-{TAG}"))
    got = si.order_money_status([{"sales_order_number": so1}, {"sales_order_number": so2}, {"sales_order_number": "none", "customer_po_number": f"CPO-{TAG}", "customer_id": "C1"}, {"sales_order_number": "nothing"}])
    assert got[so1]["paid_status"] == "PAID" and got[so1]["amount_due"] == "0.00"
    assert got[so2]["paid_status"] == "PART_PAID" and got[so2]["amount_due"] == "400.00" and got[so2]["invoiced_total"] == "1000.00"
    assert got["none"]["paid_status"] == "UNPAID" and "nothing" not in got                            # found by the customer's PO when no sales order is on the invoice


def test_when_the_invoice_file_has_no_balance_the_payments_decide():
    so = f"8{TAG[:4]}0"
    _commit(_csv("Invoice/CM #,Customer ID,Date,Invoice Total,Sales Order Number", f"PY-{TAG},C1,2026-11-20,1000.00,{so}"))
    assert si.order_money_status([{"sales_order_number": so}])[so]["paid_status"] == "UNPAID"
    _commit(_csv("Receipt Number,Date,Customer ID,Invoice #,Amount Paid", f"RC-{TAG}-1,2026-11-25,C1,PY-{TAG},400.00"))
    assert si.order_money_status([{"sales_order_number": so}])[so]["amount_due"] == "600.00"
    _commit(_csv("Receipt Number,Date,Customer ID,Invoice #,Amount Paid", f"RC-{TAG}-2,2026-11-26,C1,PY-{TAG},600.00"))
    assert si.order_money_status([{"sales_order_number": so}])[so]["paid_status"] == "PAID"


def test_the_orders_list_carries_the_sage_state_and_never_breaks_without_it(api):
    from fastapi.testclient import TestClient as _TC
    from app.main import app
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    req = InboundOrderRequest(customer_name="Owens Corning Starr Composites", customer_po_number=f"4500{TAG}".replace("A", "1").replace("B", "2").replace("C", "3").replace("D", "4").replace("E", "5").replace("F", "6"),
                              shipping_address="4837 Highway 81 South, Starr, SC 29684", line_items=[{"raw_item_text": "OC262064", "quantity": 1, "unit_price": 1385.0}])
    so = str(OrderOrchestrator().process_inbound_order(req, actor_email="t@alliedweldingsystems.com").sales_order_number)
    token = staff_token(f"sage-viewer-{TAG}@alliedweldingsystems.com", "VIEWER")
    c = _TC(app, raise_server_exceptions=False)
    row = lambda: next(i for i in c.get("/api/v1/orders", params={"q": so}, headers={"Authorization": f"Bearer {token}"}).json()["items"] if i["sales_order_number"] == so)      # noqa: E731
    assert row()["sage"] is None
    _commit(_csv("Invoice/CM #,Customer ID,Date,Invoice Total,Amount Due,Sales Order Number", f"OL-{TAG},10195,2026-11-20,1385.00,385.00,{so}"))
    assert row()["sage"]["paid_status"] == "PART_PAID" and row()["sage"]["amount_due"] == "385.00"
    dry_po = f"DRY-{TAG}"
    _commit(_csv("Sales Order Number,Date,Customer ID,Customer PO,Open Amount", f"SOD{TAG},2026-11-20,10195,{dry_po},300.00"))
    dry = c.post("/api/v1/orders/dry-run", headers={"Authorization": "Bearer " + staff_token(f"sage-operator-{TAG}@alliedweldingsystems.com", "OPERATOR")},
                 json={"customer_name": "Owens Corning Starr Composites", "customer_po_number": dry_po, "shipping_address": "4837 Highway 81 South, Starr, SC 29684",
                       "line_items": [{"raw_item_text": "OC262064", "quantity": 1, "unit_price": 1385.0}]})
    assert dry.status_code == 200, dry.text[:300]
    assert [f["number"] for f in dry.json()["already_in_sage"]] == [f"SOD{TAG}"]                       # the new-order check warns: it is already open in Sage


def test_a_sales_order_or_po_already_open_in_sage_is_flagged_for_the_new_order_check():
    _commit(_csv("Sales Order Number,Date,Customer ID,Customer PO,Open Amount", f"SO{TAG},2026-11-20,C1,CPO-{TAG}-X,300.00"))
    _commit(_csv("Purchase Order Number,Date,Vendor ID,Open Amount", f"PO{TAG},2026-11-20,V1,50.00"))
    found = si.already_open_in_sage(f"CPO-{TAG}-X", [f"PO{TAG}", "never"])
    assert {(f["kind"], f["number"]) for f in found} == {("SO", f"SO{TAG}"), ("PO", f"PO{TAG}")}
    assert si.already_open_in_sage("nope", []) == []


def test_a_bill_sage_already_holds_is_flagged_in_the_three_way_match_by_vendor_and_number():
    from app.services.reconciliation_service import VendorInvoice, VendorInvoiceLine, reconciliation_service

    num = f"BILL-{TAG}"
    _commit(_csv("Vendor Invoice #,Vendor ID,Vendor,Date,Amount,Balance", f"{num},ZIBO,Zibo Supereal Industrial Ceramic Co. Ltd,2026-11-20,500.00,500.00"))
    assert si.bill_in_sage("ZIBO", num)["bill_number"] == num and si.bill_in_sage("Zibo Supereal Industrial Ceramic Co. Ltd", num) is not None
    assert si.bill_in_sage("NIBROCK", num) is None and si.bill_in_sage("ZIBO", "other-number") is None            # another vendor's, or another number: not it
    held = reconciliation_service.reconcile_vendor_invoice(VendorInvoice(
        invoice_number=num, vendor_name="ZIBO", po_number="PO-ZIBO-4412", total_amount_billed=Decimal("925.00"), freight_billed=Decimal("85.00"),
        lines=[VendorInvoiceLine(item_id="HOSE-CERAMIC-4IN", quantity_billed=Decimal("2"), unit_cost_billed=Decimal("420.00"), total_amount=Decimal("840.00"))]))
    assert any("Already in Sage" in d for d in held.discrepancies) and held.reconciliation_status != "RECONCILED_APPROVED"
    clean = reconciliation_service.reconcile_vendor_invoice(VendorInvoice(
        invoice_number=f"FRESH-{TAG}", vendor_name="ZIBO", po_number="PO-ZIBO-4412", total_amount_billed=Decimal("925.00"), freight_billed=Decimal("85.00"),
        lines=[VendorInvoiceLine(item_id="HOSE-CERAMIC-4IN", quantity_billed=Decimal("2"), unit_cost_billed=Decimal("420.00"), total_amount=Decimal("840.00"))]))
    assert not any("Already in Sage" in d for d in clean.discrepancies)


def test_the_app_never_writes_to_sage_through_this_module():
    import inspect

    source = inspect.getsource(si)
    for banned in ("sage_odbc", "sage_sdk_writer", "pyodbc", "SAGE_COMPANY_PATH", "monday"):
        assert banned not in source, banned


def test_the_migration_adds_only_new_tables_goes_down_and_up_and_runs_twice(tmp_path, monkeypatch):
    import sqlite3

    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    monkeypatch.setattr("app.core.database._get_db_url", lambda: f"sqlite:///{tmp_path / 'scratch.db'}")
    db = tmp_path / "scratch.db"

    def tables():
        with sqlite3.connect(db) as c:
            return {r[0] for r in c.execute("select name from sqlite_master where type='table'")}

    mirrors = {"sage_imports", "sage_invoices", "sage_payments", "sage_open_orders", "sage_bills"}
    command.upgrade(cfg, "m6c8e0f2a4b6")
    before = tables()
    assert not (mirrors & before)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")                                                    # twice changes nothing
    assert mirrors <= tables() and before <= tables()                                # only new tables; every old one is still there
    command.downgrade(cfg, "m6c8e0f2a4b6")
    assert not (mirrors & tables()) and before <= tables()
    command.upgrade(cfg, "head")
    assert mirrors <= tables()
    with sqlite3.connect(db) as c:                                                  # the one-file-once rule is in the database too
        c.execute("insert into sage_imports (kind, file_name, file_hash) values ('invoices', 'a', 'h1')")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("insert into sage_imports (kind, file_name, file_hash) values ('invoices', 'b', 'h1')")


def test_the_preview_has_a_one_block_summary_with_the_first_five_differences():
    rows = [f"SUM-{TAG}-{i},C1,2026-11-20,{100 + i}.00" for i in range(7)]
    first = si.preview(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", *rows), "s1.csv", "invoices", USER)
    sm = first["summary"]
    assert sm["will_add"] == 7 and sm["will_change"] == 0 and sm["will_skip"] == 0 and len(sm["differences"]) == 5 and sm["text"].startswith("Will add 7, change 0, skip 0")
    si.commit(first["token"], USER)
    again = si.preview(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", *rows[:5], f"SUM-{TAG}-5,C1,2026-11-20,999.00", f"SUM-{TAG}-6,C1,not a date,5.00"), "s2.csv", "invoices", USER)
    sm = again["summary"]
    assert sm["will_change"] == 1 and sm["unchanged"] == 5 and sm["problems"] == 1 and sm["will_skip"] == 6
    assert sm["differences"][0]["kind"] == "changed" and "total" in sm["differences"][0]["what"] and "999.00" in sm["differences"][0]["what"]
