"""Round 19 (adversary): the Sage 50 export importer (app/services/sage_import.py, /api/v1/sage-import/*). Temporary database and temporary catalogue copies
only (tests/conftest.py); synthetic files; nothing opens Sage. Confirmed holes are marked xfail(strict=False, reason="ADV19-Tn ...")."""
import io
import threading
import time
import uuid
import zipfile

import pytest

from app.core.database import SessionLocal
from app.models.db_models import SageBillDB, SageImportDB, SageInvoiceDB, SageOpenOrderDB, SagePaymentDB
from app.services import sage_import as si
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:6].upper()
MGR = {"email": "mgr-a19@alliedweldingsystems.com", "role": "MANAGER"}
OPR = {"email": "op-a19@alliedweldingsystems.com", "role": "OPERATOR"}
BASE = "/api/v1/sage-import"


def _csv(*lines: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _u(prefix="X"):
    return f"{prefix}{TAG}{uuid.uuid4().hex[:5].upper()}"


def _h(email, role):
    return {"Authorization": f"Bearer {staff_token(email, role)}"}


def _commit(data, kind="auto", name="f.csv", user=MGR, confirm=False, **kw):
    shown = si.preview(data, name, kind, user, **kw)
    return si.commit(shown["token"], MGR, confirm_close=confirm)


def _hist(file_hash):
    with SessionLocal() as db:
        return db.query(SageImportDB).filter(SageImportDB.file_hash == file_hash).count()


def _xlsx(rows, extra_sheet_xml=None, dimension=None):
    import openpyxl

    book = openpyxl.Workbook()
    for r in rows:
        book.active.append(r)
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


# =============================================================== values

@pytest.mark.parametrize("cell", ["CAD 1,000.00", "¥500,000", "€1,000.00", "RMB 5000", "GBP 20.00"])
def test_t1_an_amount_in_another_currency_is_not_saved_as_dollars(cell):
    p = si.parse(_csv("Vendor Invoice #,Vendor ID,Date,Invoice Amount", f"{_u('B')},V1,2026-11-20,{cell}"), "b.csv")
    row = p["rows"][0]
    assert row["problems"] or "amount" not in row["values"]


@pytest.mark.parametrize("val", [1234.5600000000002, 0.1 + 0.2, 99.99999999999999, 1e-9 + 5.0])
def test_excel_float_noise_in_money_is_rounded_not_refused(val):
    data = _xlsx([["Invoice/CM #", "Customer ID", "Date", "Invoice Total"], [_u("I"), "C1", "2026-11-20", val]])
    row = si.parse(data, "a.xlsx")["rows"][0]
    assert not row["problems"] and row["values"]["total"]


@pytest.mark.parametrize("cell", ["1e308", "NaN", "inf", "-inf", "Infinity", "1e999", "9" * 400, "0x10", "1_000", "１２００", "1,2,3", "1..2", "--5", "5-", "$", "()", "(", ")"])
def test_hostile_money_is_a_problem_row_never_a_crash_or_a_saved_value(cell):
    p = si.parse(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,2026-11-20,\"{cell}\""), "i.csv")
    row = p["rows"][0]
    assert row["problems"] and "total" not in row["values"]


@pytest.mark.parametrize("cell,want", [("(1,200.50)", "-1200.50"), ("-1,200.5", "-1200.50"), ("1,200", "1200.00"), ("$1,200.50", "1200.50")])
def test_negative_parenthesised_and_thousands_money_reads_right(cell, want):
    row = si.parse(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,2026-11-20,\"{cell}\""), "i.csv")["rows"][0]
    assert not row["problems"] and row["values"]["total"] == want


@pytest.mark.parametrize("d", ["11/20/2026", "11/20/26", "2026-11-20", "2026-11-20 10:00:00", "2026-11-20T10:00"])
def test_good_dates_read(d):
    row = si.parse(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,{d},5.00"), "i.csv")["rows"][0]
    assert not row["problems"] and row["values"]["date"] == "2026-11-20"


@pytest.mark.parametrize("d", ["01/02/2026", "3/4/2026", "2026-13-01", "2026-02-30", "00/00/0000", "32/13/2026", "yesterday", "20261120", "Nov 20 2026", "45615"])
def test_ambiguous_or_odd_dates_are_problems_not_guesses(d):
    row = si.parse(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,{d},5.00"), "i.csv")["rows"][0]
    assert row["problems"], d


@pytest.mark.parametrize("cell,want", [("12.345", "12.35"), ("0.005", "0.01"), ("2.675", "2.68")])
def test_t17_money_rounds_half_up_like_the_rest_of_the_app(cell, want):
    row = si.parse(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,2026-11-20,\"{cell}\""), "i.csv")["rows"][0]
    assert row["values"]["total"] == want


@pytest.mark.parametrize("d", ["2026-11-20 garbage", "2026-11-20T99:99", "2026-11-20;DROP"])
def test_t3_text_after_an_iso_date_is_refused(d):
    row = si.parse(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,{d},5.00"), "i.csv")["rows"][0]
    assert row["problems"]


@pytest.mark.parametrize("d", ["0001-01-01", "9999-12-31", "1850-05-05", "2200-01-01"])
def test_t4_an_absurd_year_is_a_problem(d):
    row = si.parse(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,{d},5.00"), "i.csv")["rows"][0]
    assert row["problems"]


@pytest.mark.parametrize("field_header,value", [("Item ID", "-X1"), ("Item ID", "+A22"), ("Description", "+1 555 0100")])
def test_t5_defusing_formula_text_does_not_change_ids_or_numbers_that_are_not_formulas(field_header, value):
    head = ["Item ID", "Description"] if field_header != "Item ID" else ["Item ID", "Description"]
    row_vals = [value, "plain"] if field_header == "Item ID" else ["ID" + _u(), value]
    row = si.parse(_csv(",".join(head), ",".join(row_vals)), "p.csv", "parts")["rows"][0]
    got = row["values"]["item_id"] if field_header == "Item ID" else row["values"]["description"]
    assert got == value


@pytest.mark.parametrize("cell", ["=CMD|' /C calc'!A0", "@SUM(1+1)", "=HYPERLINK(\"http://evil\")", "+cmd", "-2+3"])
def test_formula_cells_are_defused_in_every_stored_text_field(cell):
    row = si.parse(_csv("Item ID,Description", f"{_u('P')},\"{cell}\""), "p.csv", "parts")["rows"][0]
    assert row["values"]["description"][:1] not in ("=", "@", "+", "-")


def test_duplicate_and_odd_headers_do_not_crash_and_name_what_is_unused():
    p = si.parse(_csv("Invoice/CM #,Customer ID,Date,Date,Invoice Total,Invoice Total,,,", f"{_u('I')},C1,2026-11-20,2026-12-25,5.00,9.00,x,y,z"), "i.csv")
    assert p["kind"] == "invoices" and p["unmapped"]
    assert p["rows"][0]["values"]["date"] == "2026-11-20" and p["rows"][0]["values"]["total"] == "5.00"


@pytest.mark.parametrize("raw", [b"\xef\xbb\xbfInvoice/CM #,Customer ID,Date,Invoice Total\r\nA1,C1,2026-11-20,5\r\n", b"\xff\xfeI\x00n\x00v\x00", b"\x00\x01\x02binary", b"\xd0\xcf\x11\xe0old xls", b"   \n  ", b""])
def test_bom_binary_and_empty_files_are_refused_or_read_without_a_crash(raw):
    try:
        out = si.parse(raw, "x.csv")
        assert out["kind"] == "invoices"
    except si.ImportRefused:
        pass


def test_t6_mixed_encodings_do_not_garble_the_good_part():
    data = "Customer ID,Customer Name\r\nC1,Müller GmbH\r\nC2,".encode("utf-8") + b"Caf\x92 Co\r\n"
    rows = si.parse(data, "c.csv", "customers")["rows"]
    assert rows[0]["values"]["customer_name"] == "Müller GmbH"


def test_t7_the_separator_is_chosen_from_the_heading_row_not_the_title_line():
    data = ("Allied Welding Systems, Inc.\r\nInvoice Journal\r\n" + "Invoice/CM #\tCustomer ID\tDate\tInvoice Total\r\n" + f"{_u('I')}\tC1\t2026-11-20\t5.00\r\n").encode()
    assert si.parse(data, "j.txt")["kind"] == "invoices"


def test_t8_two_identical_same_day_payments_are_both_kept():
    inv = _u("I")
    p = si.parse(_csv("Date,Customer ID,Invoice #,Amount Paid", f"2026-11-20,C1,{inv},500.00", f"2026-11-20,C1,{inv},500.00"), "p.csv", "payments")
    assert [r for r in p["rows"] if not r["problems"]].__len__() == 2


# =============================================================== bombs and size

def _raw_xlsx(sheet_xml, shared=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        z.writestr("xl/sharedStrings.xml", shared or '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1"><si><t>x</t></si></sst>')
    return buf.getvalue()


def test_a_wide_sheet_declaration_cannot_make_the_preview_slow():
    rows = "".join(f'<row r="{i}"><c r="A{i}" t="inlineStr"><is><t>x</t></is></c></row>' for i in range(1, 3001))
    data = _raw_xlsx(f'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:XFD1048576"/><sheetData>{rows}</sheetData></worksheet>')
    assert len(data) < 100_000
    t = time.monotonic()
    try:
        si.parse(data, "w.xlsx")
    except si.ImportRefused:
        pass
    assert time.monotonic() - t < 3.0


def test_t10_an_xlsx_that_unzips_to_hundreds_of_megabytes_is_refused_fast():
    big = "A" * (150 * 1024 * 1024)
    shared = f'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1"><si><t>{big}</t></si></sst>'
    data = _raw_xlsx('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>', shared)
    del shared
    assert len(data) < 1_000_000
    t = time.monotonic()
    with pytest.raises(si.ImportRefused) as e:
        si.parse(data, "bomb.xlsx")
    assert time.monotonic() - t < 3.0 and "could not be read" not in str(e.value)


def test_a_csv_with_more_than_the_row_cap_or_a_giant_field_or_too_many_columns_is_refused_plainly():
    head = "Invoice/CM #,Customer ID,Date,Invoice Total\r\n"
    for body in ("".join(f"I{i},C,2026-11-20,1\r\n" for i in range(si.MAX_ROWS + 50)), "A," + "x" * 200_000 + ",2026-11-20,1\r\n"):
        with pytest.raises(si.ImportRefused):
            si.parse((head + body).encode(), "big.csv")
    wide = ",".join(f"c{i}" for i in range(500))
    out = si.parse(_csv("Invoice/CM #,Customer ID,Date,Invoice Total," + wide, "A1,C1,2026-11-20,5," + ",".join("v" for _ in range(500))), "w.csv")
    assert out["rows"][0]["values"]["total"] == "5.00"


# =============================================================== replay, tokens, races

def test_t11_a_file_imported_with_the_wrong_date_order_can_be_imported_again_with_the_right_one():
    inv = _u("I")
    data = _csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{inv},C1,03/04/2026,5.00")
    first = _commit(data, date_order="mdy")
    assert first["created"] == 1
    second = _commit(data, date_order="dmy")
    assert not second["already_imported"]


def test_the_same_file_twice_and_a_commit_without_a_preview_and_a_forged_or_reused_token_change_nothing():
    inv = _u("I")
    data = _csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{inv},C1,2026-11-20,5.00")
    shown = si.preview(data, "a.csv", "auto", OPR)
    assert si.commit(shown["token"], MGR)["created"] == 1
    with pytest.raises(si.ImportRefused):
        si.commit(shown["token"], MGR)                       # reused
    assert _commit(data)["already_imported"] is True         # replayed
    for bad in ("", "x", "../../etc", shown["token"] + "A", shown["token"][:-1], "A" * 5000, "\x00", None):
        with pytest.raises(si.ImportRefused):
            si.commit(bad, MGR)
    with SessionLocal() as db:
        assert db.query(SageInvoiceDB).filter(SageInvoiceDB.invoice_number == inv).count() == 1


def test_t12_one_user_cannot_evict_anothers_pending_preview():
    mine = si.preview(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,2026-11-20,5.00"), "m.csv", "auto", MGR)
    for _ in range(25):
        si.preview(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,2026-11-20,5.00"), "o.csv", "auto", OPR)
    assert si.commit(mine["token"], MGR)["created"] == 1


@pytest.mark.parametrize("kind_rows", ["invoices", "bills", "parts"])
def test_two_commits_of_one_token_at_once_import_once_and_never_crash(kind_rows):
    if kind_rows == "invoices":
        data, kind = _csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,2026-11-20,5.00"), "invoices"
    elif kind_rows == "bills":
        data, kind = _csv("Vendor Invoice #,Vendor ID,Date,Invoice Amount", f"{_u('B')},V1,2026-11-20,5.00"), "bills"
    else:
        data, kind = _csv("Item ID,Description,Cost", f"{_u('P')},race part,1.00"), "parts"
    shown = si.preview(data, "r.csv", kind, MGR)
    out, errs = [], []

    def go():
        try:
            out.append(si.commit(shown["token"], MGR))
        except si.ImportRefused as e:
            errs.append("refused")
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)

    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not [e for e in errs if e != "refused"], errs
    assert _hist(shown["file"]["sha256"]) == 1
    assert sum(1 for o in out if not o["already_imported"]) == 1


def test_two_previews_of_one_file_committed_at_once_import_once_and_never_crash():
    data = _csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,2026-11-20,5.00")
    toks = [si.preview(data, "r.csv", "auto", MGR) for _ in range(4)]
    errs = []

    def go(t):
        try:
            si.commit(t["token"], MGR)
        except si.ImportRefused:
            pass
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)

    ts = [threading.Thread(target=go, args=(t,)) for t in toks]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    assert _hist(toks[0]["file"]["sha256"]) == 1


# =============================================================== using the mirrors

def test_t13_another_customers_invoice_with_the_same_po_number_is_not_shown_as_mine():
    po = _u("PO")
    _commit(_csv("Invoice/CM #,Customer ID,Date,Invoice Total,Customer PO", f"{_u('I')},OTHERCUST,2026-11-20,9999.00,{po}"))
    got = si.order_money_status([{"sales_order_number": _u("SO"), "customer_po_number": po, "customer_id": "MYCUST", "customer": "My Customer"}])
    assert not got


def test_t14_an_open_order_that_is_gone_from_the_newest_open_orders_export_stops_warning():
    po, so1, so2 = _u("CP"), _u("S"), _u("S")
    _commit(_csv("SO/Proposal Number,Date,Customer ID,Customer PO,Amount", f"{so1},2026-11-01,C1,{po},100.00", f"{so2},2026-11-01,C1,{_u('CP')},50.00"), "open_sales_orders")
    _commit(_csv("SO/Proposal Number,Date,Customer ID,Customer PO,Amount", f"{so2},2026-11-01,C1,{_u('CP')},50.00", f"{_u('S')},2026-11-02,C1,{_u('CP')},5.00"), "open_sales_orders", name="g.csv", confirm=True)     # a person confirms the close (the shared test DB holds other open orders too)
    assert not si.already_open_in_sage(po)


def test_t15_a_refund_reduces_what_the_invoice_shows_as_paid():
    inv, so = _u("I"), _u("S")
    _commit(_csv("Invoice/CM #,Customer ID,Date,Invoice Total,SO/Proposal Number,Amount Due", f"{inv},C1,2026-11-01,100.00,{so},", ))
    _commit(_csv("Date,Customer ID,Invoice #,Amount Paid,Receipt Number", f"2026-11-02,C1,{inv},100.00,R1{TAG}{inv[-4:]}"), "payments", name="p1.csv")
    _commit(_csv("Date,Customer ID,Invoice #,Amount Paid,Receipt Number", f"2026-11-03,C1,{inv},-100.00,R2{TAG}{inv[-4:]}"), "payments", name="p2.csv")
    assert si.order_money_status([{"sales_order_number": so}])[so]["paid_status"] != "PAID"


# =============================================================== catalogue kinds

@pytest.mark.parametrize("stock", ["abc", "1e999", "NaN", "-5", "(5)", "5 pcs"])
def test_odd_stock_numbers_on_parts_never_crash_a_commit(stock):
    pid = _u("P")
    data = _csv("Item ID,Description,Cost,Qty on Hand", f"{pid},part,1.00,{stock}")
    try:
        out = _commit(data, "parts", name=f"p{stock[:3]}.csv")
        assert out["created"] + out["unchanged"] + out["updated"] + out["problems"] >= 1
    except si.ImportRefused:
        pass


# =============================================================== HTTP: roles and plain errors

def test_roles_and_odd_uploads_over_http(client, auth_enforced):
    viewer, op, mgr = _h("v-a19@alliedweldingsystems.com", "VIEWER"), _h("o-a19@alliedweldingsystems.com", "OPERATOR"), _h("m-a19@alliedweldingsystems.com", "MANAGER")
    good = _csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,2026-11-20,5.00")
    for path, call in ((f"{BASE}/kinds", lambda h: client.get(f"{BASE}/kinds", headers=h)), (f"{BASE}/history", lambda h: client.get(f"{BASE}/history", headers=h)),
                       (f"{BASE}/preview", lambda h: client.post(f"{BASE}/preview", files={"file": ("a.csv", good)}, headers=h))):
        assert client.get(path).status_code in (401, 403) if path.endswith(("kinds", "history")) else client.post(path, files={"file": ("a.csv", good)}).status_code in (401, 403)
        assert call(viewer).status_code == 403
        assert call(op).status_code == 200
    shown = client.post(f"{BASE}/preview", files={"file": ("a.csv", good)}, headers=op).json()
    assert client.post(f"{BASE}/commit", json={"token": shown["token"]}, headers=viewer).status_code == 403
    assert client.post(f"{BASE}/commit", json={"token": shown["token"]}, headers=op).status_code == 403
    assert client.post(f"{BASE}/commit", json={"token": "nope"}, headers=mgr).status_code == 422
    assert client.post(f"{BASE}/commit", json={}, headers=mgr).status_code == 422
    assert client.post(f"{BASE}/commit", json={"token": {"a": 1}}, headers=mgr).status_code in (200, 422)
    assert client.post(f"{BASE}/commit", json={"token": shown["token"]}, headers=mgr).status_code == 200
    assert client.post(f"{BASE}/commit", json={"token": shown["token"]}, headers=mgr).status_code == 422
    for files, data in (({"file": ("a.csv", b"")}, {}), ({"file": ("a.csv", good)}, {"kind": "bogus"}), ({"file": ("a.csv", good)}, {"date_order": "xyz"}),
                        ({"file": ("a.csv", b"x" * (si.MAX_BYTES + 10))}, {}), ({"file": ("../../evil\n.csv", good)}, {}), ({"file": ("a.xlsx", b"not a zip")}, {})):
        r = client.post(f"{BASE}/preview", files=files, data=data, headers=op)
        assert r.status_code in (200, 413, 422), (data, r.status_code, r.text[:100])
    assert client.post(f"{BASE}/preview", headers=op).status_code == 422
    assert client.get(f"{BASE}/history?limit=0", headers=op).status_code == 422
    assert client.get(f"{BASE}/history?limit=99999", headers=op).status_code == 422
