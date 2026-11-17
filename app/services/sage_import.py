"""Import from Sage 50 through its export files. The app never opens a Sage company file and never writes to Sage: a person exports a report or list
from Sage 50 (to CSV or Excel), and this reads that file.

Two steps. PREVIEW reads the file and says what it would do (new, changed, unchanged, problem rows, and why) and saves nothing. COMMIT, with the
token the preview gave, saves it. The same file twice adds nothing (a file is known by its kind and its hash, and rows by their Sage number).

Eight kinds. Three feed the master lists through the existing catalogue importer (parts, customers, vendors). Five are read-only mirrors in their own
tables (invoices, payments, open sales orders, open purchase orders, vendor bills), used to show paid or unpaid on an order, to flag a vendor bill
Sage already holds, and to flag a sales order or purchase order that is already open in Sage.

Headers are matched by tolerant aliases (case, spaces, punctuation, a BOM, CSV or Excel all ignored). When the headers are not recognised the file is
refused, naming the columns that were not understood and the ones that are missing. Nothing is guessed."""
import csv
import hashlib
import io
import zipfile
import re
import secrets
import threading
import time
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.core.logging import logger
from sqlalchemy.exc import IntegrityError
from app.models.db_models import SageBillDB, SageImportDB, SageInvoiceDB, SageOpenOrderDB, SagePaymentDB

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLUMNS = 60
MAX_CELL = 400
PREVIEW_ROWS = 25
PREVIEW_PROBLEMS = 200
TOKEN_MINUTES = 30
MAX_UNZIPPED = 40 * 1024 * 1024        # an Excel file is a zip: what it unpacks to is capped too, not only what was uploaded
PREVIEWS_PER_USER = 10
PREVIEWS_IN_ALL = 200
YEAR_FROM, YEAR_TO = 1990, 2100


class ImportRefused(ValueError):
    """The file was refused, for a reason a person can read. Nothing was read into the app."""


def _n(text: Any) -> str:
    """A header reduced for comparing: no BOM, no case, no spaces or punctuation ("Invoice/CM #" and "invoice cm" are one header)."""
    t = unicodedata.normalize("NFKC", str(text or "")).replace("﻿", "").lower()
    t = re.sub(r"\((?:x)\)\s*$", "", t.strip())                  # "Customer Name(X)": the mark for an export-only column is not part of the heading
    return re.sub(r"[^a-z0-9]+", "", t)


_SHOW: Dict[str, str] = {}          # the first (plain) spelling of each column, for messages: "Invoice/CM #", not "invoicecm"


def _aliases(*names: str) -> List[str]:
    _SHOW.setdefault(_n(names[0]), names[0])
    return [_n(x) for x in names]


# kind -> {field: [aliases]}. The names are the ones Sage 50 US writes in its reports and exports, plus the plain ones.
FIELDS: Dict[str, Dict[str, List[str]]] = {
    "parts": {
        "item_id": _aliases("Item ID", "Item Number", "Item", "Part Number", "Part #", "SKU"),
        "inactive": _aliases("Inactive"),
        "description": _aliases("Description", "Item Description", "Item Name", "Name"),
        "standard_cost": _aliases("Cost", "Last Unit Cost", "Unit Cost", "Standard Cost", "Cost Price"),
        "base_price": _aliases("Sales Price/Billing Rate 1", "Price Level 1", "Sales Price", "Selling Price", "Base Price", "Price"),
        "preferred_vendor": _aliases("Vendor ID", "Preferred Vendor", "Primary Vendor", "Last Vendor"),
        "uom": _aliases("Stocking U/M", "Stocking UM", "Unit of Measure", "UOM", "U/M"),
        "category": _aliases("Item Class", "Category", "Class"),
        "stock_on_hand": _aliases("Qty on Hand", "Quantity on Hand", "On Hand", "Stock on Hand"),
        "min_stock_level": _aliases("Minimum Stock", "Min Stock", "Minimum Qty", "Reorder Point"),
        "gl_sales_account": _aliases("GL Sales Acct", "GL Sales Account", "Sales Account"),
        "gl_inventory_account": _aliases("GL Inventory/Wage Acct", "GL Inventory Acct", "GL Inventory Account", "Inventory Account"),
    },
    "customers": {
        "customer_id": _aliases("Customer ID", "Customer Number", "Cust ID", "ID"),
        "inactive": _aliases("Inactive"),
        "customer_name": _aliases("Customer Name", "Customer", "Name", "Company"),
        "bill_line1": _aliases("Bill to Address-Line One", "Bill to Address 1", "Billing Address", "Address-Line One", "Address 1"),
        "bill_city": _aliases("Bill to City", "City"),
        "bill_state": _aliases("Bill to State", "State"),
        "bill_zip": _aliases("Bill to Zip", "Bill to Zip Code", "Zip", "Zip Code", "Postal Code"),
        "ship_line1": _aliases("Ship to Address-Line One", "Ship to Address 1 - Line One", "Ship to Address 1", "Shipping Address"),
        "ship_city": _aliases("Ship to City", "Ship to City 1"),
        "ship_state": _aliases("Ship to State", "Ship to State 1"),
        "ship_zip": _aliases("Ship to Zip", "Ship to Zip Code", "Ship to Zipcode", "Ship to Zipcode 1"),
        "payment_terms": _aliases("Terms", "Payment Terms", "Terms Description"),
        "sales_rep": _aliases("Sales Representative ID", "Sales Rep ID", "Sales Rep", "Salesperson"),
        "email": _aliases("Customer Email", "E-mail", "Email", "E-mail Address", "Customer E-mail"),
        "contact_name": _aliases("Contact", "Contact Name"),
        "shipping_method": _aliases("Ship Via", "Shipping Method"),
    },
    "vendors": {
        "vendor_id": _aliases("Vendor ID", "Vendor Number", "ID"),
        "inactive": _aliases("Inactive"),
        "vendor_name": _aliases("Vendor Name", "Vendor", "Name", "Company"),
        "addr_line1": _aliases("Address-Line 1", "Address-Line One", "Address 1", "Vendor Address", "Remit to Address-Line One"),
        "addr_city": _aliases("City"),
        "addr_state": _aliases("State"),
        "addr_zip": _aliases("Zip", "Zip Code", "Postal Code"),
        "payment_terms": _aliases("Terms", "Payment Terms", "Terms Description"),
        "email": _aliases("Vendor Email", "E-mail", "Email", "E-mail Address", "Vendor E-mail"),
        "phone": _aliases("Telephone 1", "Phone Number", "Phone", "Phone 1", "Telephone"),
    },
    "invoices": {
        "invoice_number": _aliases("Invoice/CM #", "Invoice #", "Invoice Number", "Invoice No", "Invoice", "Inv #"),
        "customer_id": _aliases("Customer ID", "Customer Number", "Cust ID"),
        "customer_name": _aliases("Customer", "Customer Name", "Name"),
        "date": _aliases("Date", "Invoice Date", "Invoice/CM Date"),
        "due_date": _aliases("Due Date", "Date Due"),
        "customer_po": _aliases("Customer PO", "Customer PO #", "Customer PO Number", "Reference", "P.O. Number", "PO Number"),
        "sales_order": _aliases("SO/Proposal Number", "Sales Order Number", "Sales Order #", "SO #", "SO Number", "Sales Order"),
        "credit_memo": _aliases("Credit Memo"),
        "voided": _aliases("Voided by Transaction"),
        "total": _aliases("Accounts Receivable Amount", "Invoice Total", "Invoice Amount", "Total", "Original Amount"),      # not a bare "Amount": that is a payment's or a bill's column
        "amount_due": _aliases("Amount Due", "Balance", "Open Balance", "Balance Due", "Remaining Balance", "Outstanding"),
    },
    "payments": {
        "receipt_number": _aliases("Receipt Number", "Receipt #", "Check Number", "Check #", "Reference", "Payment Reference"),
        "date": _aliases("Date", "Payment Date", "Receipt Date", "Deposit Date"),
        "customer_id": _aliases("Customer ID", "Customer Number", "Cust ID"),
        "invoice_number": _aliases("Invoice/CM #", "Invoice #", "Invoice Number", "Applied to Invoice", "Invoice Paid", "Invoice"),
        "amount": _aliases("Amount Paid", "Payment", "Payment Amount", "Amount", "Receipt Amount"),
    },
    "open_sales_orders": {
        "so_number": _aliases("SO/Proposal Number", "Sales Order Number", "Sales Order #", "SO #", "SO Number", "Sales Order"),
        "closed": _aliases("Closed"),
        "customer_id": _aliases("Customer ID", "Customer Number"),
        "customer_name": _aliases("Customer", "Customer Name", "Name"),
        "date": _aliases("Date", "Order Date", "Sales Order Date", "SO Date"),
        "customer_po": _aliases("Customer PO", "Customer PO #", "Customer PO Number", "P.O. Number", "Reference"),
        "amount": _aliases("Accounts Receivable Amount", "Order Total", "Sales Order Total", "Total", "Amount"),
        "open_amount": _aliases("Open Amount", "Remaining Amount", "Balance", "Amount Open"),
    },
    "open_purchase_orders": {
        "po_number": _aliases("P.O. #", "Purchase Order Number", "PO Number", "P.O. Number", "Purchase Order"),
        "closed": _aliases("Closed"),
        "vendor_id": _aliases("Vendor ID", "Vendor Number"),
        "vendor_name": _aliases("Vendor", "Vendor Name", "Name"),
        "date": _aliases("Date", "Order Date", "PO Date", "Purchase Order Date"),
        "amount": _aliases("Accounts Payable Amount", "Order Total", "PO Total", "Total", "Amount"),
        "open_amount": _aliases("Open Amount", "Remaining Amount", "Balance", "Amount Open"),
    },
    "bills": {
        "bill_number": _aliases("Vendor Invoice #", "Vendor Invoice Number", "Invoice/CM #", "Invoice #", "Invoice Number", "Bill #", "Bill Number", "Reference"),
        "vendor_id": _aliases("Vendor ID", "Vendor Number"),
        "vendor_name": _aliases("Vendor", "Vendor Name", "Name"),
        "date": _aliases("Date", "Invoice Date", "Bill Date"),
        "due_date": _aliases("Due Date", "Date Due"),
        "amount": _aliases("Accounts Payable Amount", "Invoice Amount", "Bill Amount", "Total", "Original Amount", "Amount"),
        "credit_memo": _aliases("Credit Memo"),
        "amount_due": _aliases("Amount Due", "Balance", "Open Balance", "Balance Due", "Remaining Balance", "Outstanding"),
        "po_number": _aliases("Purchase Order Number", "PO Number", "PO #", "P.O. Number"),
    },
}
REQUIRED: Dict[str, Tuple[str, ...]] = {
    "parts": ("item_id", "description"), "customers": ("customer_id", "customer_name"), "vendors": ("vendor_id", "vendor_name"),
    "invoices": ("invoice_number", "customer_id", "date", "total"), "payments": ("date", "amount", "invoice_number"),
    "open_sales_orders": ("so_number", "date"), "open_purchase_orders": ("po_number", "date"),
    "bills": ("bill_number", "vendor_id", "date", "amount"),
}
LABELS = {"parts": "Parts", "customers": "Customers", "vendors": "Vendors", "invoices": "Invoices", "payments": "Payments",
          "open_sales_orders": "Open sales orders", "open_purchase_orders": "Open purchase orders", "bills": "Vendor bills"}
MASTER = {"parts": "ITEMS", "customers": "CUSTOMERS", "vendors": "VENDORS"}
DATE_FIELDS = {"date", "due_date"}
BOOL_FIELDS = {"closed", "inactive", "credit_memo"}
MONEY_FIELDS = {"standard_cost", "base_price", "total", "amount_due", "amount", "open_amount"}


def kinds() -> List[Dict[str, Any]]:
    """What can be imported, and which columns are needed (for the screen's help text)."""
    return [{"kind": k, "label": LABELS[k], "required": list(REQUIRED[k]), "columns": {f: _SHOW.get(a[0], a[0]) for f, a in FIELDS[k].items()}} for k in FIELDS]


# ----------------------------------------------------------------------------- reading the file

def _decode(data: bytes) -> Tuple[str, bool]:
    """(text, mixed). A UTF-8 file reads as UTF-8. Otherwise each line is read as UTF-8 if it is, else as Windows-1252, so one odd line does not garble the
    good ones; `mixed` is True when both kinds of line were found."""
    try:
        return data.decode("utf-8-sig"), False
    except UnicodeDecodeError:
        pass
    out, fallback, utf8_extra = [], 0, 0
    for line in data.split(b"\n"):
        try:
            text = line.decode("utf-8")
            utf8_extra += any(ord(ch) > 127 for ch in text)
        except UnicodeDecodeError:
            fallback += 1
            try:
                text = line.decode("cp1252")
            except UnicodeDecodeError:
                text = line.decode("latin-1")
        out.append(text)
    return "\n".join(out).lstrip("\ufeff"), bool(fallback and utf8_extra)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):                                  # an Excel date cell
        return value.date().isoformat() if hasattr(value, "date") else value.isoformat()
    if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return " ".join(str(value).replace("\x00", " ").split())[:MAX_CELL]


def _pick_delimiter(text: str) -> str:
    """The separator that makes the heading row appear. A title line above the headings may hold commas, so the first line alone is not trusted."""
    head = "\n".join(text.splitlines()[:30])
    best, score = None, 0
    known = {a for k in FIELDS.values() for al in k.values() for a in al}
    for d in (",", "\t", ";", "|"):
        try:
            rows = list(csv.reader(io.StringIO(head), delimiter=d))
        except csv.Error:
            continue
        h = _header_row(rows)
        if h >= 0:
            hit = sum(1 for c in rows[h] if _n(c) in known)
            if hit > score:
                best, score = d, hit
    if best:
        return best
    first = text.splitlines()[0] if text.splitlines() else ""
    return max((",", "\t", ";", "|"), key=lambda d: first.count(d))


def _grid(data: bytes, filename: str) -> Tuple[List[List[str]], List[str]]:
    """The file as rows of text, and notes for the person. CSV (comma, tab, semicolon or bar) or an Excel .xlsx; size caps; control characters dropped."""
    if not data or not data.strip():
        raise ImportRefused("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ImportRefused(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB. Export a shorter date range or split it.")
    rows: List[List[str]] = []
    notes: List[str] = []
    if data[:4] == b"PK\x03\x04" or filename.lower().endswith((".xlsx", ".xlsm")):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                parts = z.infolist()
                if len(parts) > 300 or sum(i.file_size for i in parts) > MAX_UNZIPPED:
                    raise ImportRefused(f"That Excel file unpacks to more than {MAX_UNZIPPED // (1024 * 1024)} MB, which a Sage export never does. Export it again as CSV.")
        except zipfile.BadZipFile as exc:
            raise ImportRefused("That Excel file could not be read. Save it again as .xlsx or CSV from Excel or Sage.") from exc
        try:
            import openpyxl

            book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            sheet = book.worksheets[0]
            for i, row in enumerate(sheet.iter_rows(values_only=True)):
                if i > MAX_ROWS + 20:
                    raise ImportRefused(f"More than {MAX_ROWS:,} rows. Export a shorter date range or split it.")
                rows.append([_cell(c) for c in row[:MAX_COLUMNS + 1]])
        except ImportRefused:
            raise
        except Exception as exc:  # noqa: BLE001 - a damaged or hostile workbook
            logger.warning(f"Sage import: could not read a workbook: {type(exc).__name__}")
            raise ImportRefused("That Excel file could not be read. Save it again as .xlsx or CSV from Excel or Sage.") from exc
    else:
        text, mixed = _decode(data)
        if "\x00" in text[:2000]:
            raise ImportRefused("That is not a text or Excel file. Export the report to CSV or Excel from Sage 50.")
        if mixed:
            notes.append("This file mixes two text encodings (some lines UTF-8, some Windows-1252). Each line was read in its own encoding; check accented names, or export it again from Sage.")
        delim = _pick_delimiter(text)
        try:
            for i, row in enumerate(csv.reader(io.StringIO(text), delimiter=delim)):
                if i > MAX_ROWS + 20:
                    raise ImportRefused(f"More than {MAX_ROWS:,} rows. Export a shorter date range or split it.")
                rows.append([_cell(c) for c in row[:MAX_COLUMNS + 1]])
        except csv.Error as exc:
            raise ImportRefused("The file could not be read as a table: " + str(exc)[:80]) from exc
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        raise ImportRefused("The file has no rows.")
    return rows, notes


def _header_row(rows: List[List[str]]) -> int:
    """Sage reports often start with title lines: the header is the first of the top rows that names at least two known columns."""
    known = {a for k in FIELDS.values() for al in k.values() for a in al}
    for i, row in enumerate(rows[:12]):
        if sum(1 for c in row if _n(c) in known) >= 2:
            return i
    return -1


def _map_columns(headers: List[str], kind: str) -> Tuple[Dict[int, str], List[str]]:
    rank = {a: (f, k) for f, al in FIELDS[kind].items() for k, a in enumerate(al)}
    best: Dict[str, Tuple[int, int]] = {}                         # field -> (alias rank, column)
    for i, h in enumerate(headers):
        hit = rank.get(_n(h))
        if hit and (hit[0] not in best or hit[1] < best[hit[0]][0]):
            best[hit[0]] = (hit[1], i)
    mapped = {col: f for f, (_r, col) in best.items()}
    unmapped = [h for i, h in enumerate(headers) if h and i not in mapped]
    return mapped, unmapped


def _detect(headers: List[str], wanted: str) -> Tuple[str, Dict[int, str], List[str]]:
    """The kind of file and its column mapping. A kind that is named must fit; with "auto" the kind whose required columns are all present and which
    matches the most columns wins, and a tie is refused (the person says which)."""
    if wanted != "auto":
        if wanted not in FIELDS:
            raise ImportRefused("The kind must be one of: " + ", ".join(FIELDS) + ", or auto.")
        mapped, unmapped = _map_columns(headers, wanted)
        _need(wanted, mapped, headers, unmapped)
        return wanted, mapped, unmapped
    fits = []
    for kind in FIELDS:
        mapped, unmapped = _map_columns(headers, kind)
        if set(REQUIRED[kind]) <= set(mapped.values()):
            fits.append((len(mapped), kind, mapped, unmapped))
    if not fits:
        raise ImportRefused("Those columns do not match any Sage export Allied reads. Not understood: " + _list([h for h in headers if h]) +
                            ". Choose the kind of file, or export again from Sage 50 with the usual column headings.")
    fits.sort(key=lambda f: -f[0])
    if len(fits) > 1 and fits[0][0] == fits[1][0]:
        raise ImportRefused("The columns fit more than one kind of file (" + ", ".join(LABELS[f[1]] for f in fits if f[0] == fits[0][0]) + "). Choose which one this is.")
    return fits[0][1], fits[0][2], fits[0][3]


def _list(items: List[str], limit: int = 12) -> str:
    shown = [f"'{str(i)[:40]}'" for i in items[:limit]]
    return ", ".join(shown) + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _need(kind: str, mapped: Dict[int, str], headers: List[str], unmapped: List[str]) -> None:
    missing = [f for f in REQUIRED[kind] if f not in mapped.values()]
    if missing:
        names = [_SHOW.get(FIELDS[kind][f][0], f) for f in missing]
        raise ImportRefused(f"This does not look like an export of {LABELS[kind].lower()}. Missing columns: " + _list(names) +
                            (". Not understood in the file: " + _list(unmapped) if unmapped else "") + ". Nothing was read.")


# ----------------------------------------------------------------------------- reading the values

_PLAIN_SIGNED = re.compile(r"^[+-][A-Za-z]{0,3}\d[\w.,-]*$")            # -X1, +A22, -12.50: an id or a number, not a formula
_PHONE = re.compile(r"^\+\d[\d\s().-]*$")                                # +1 555 0100


def _text(value: str, limit: int = 200) -> str:
    """Text for a stored cell: one line, with control characters dropped. Text a spreadsheet could run as a formula (a leading = or @, or a + or - that
    is not an id, a number or a phone number) gets a leading apostrophe; real ids and numbers like -X1 or +1 555 0100 are left exactly as written."""
    t = " ".join(re.sub(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]+", " ", str(value or "")).split())[:limit]
    if t[:1] in ("=", "@"):
        return "'" + t
    if t[:1] in ("+", "-") and not (_PLAIN_SIGNED.fullmatch(t) or _PHONE.fullmatch(t)):
        return "'" + t
    return t


def _money(value: str) -> Tuple[Optional[str], str]:
    """(amount as "1234.50", "") or (None, the plain reason). The app's one money reader; amounts round half up like the rest of the app. An amount that
    names a currency other than US dollars is refused (Allied's books are in dollars), and so is a European-written amount or anything not a plain number."""
    from app.services import order_documents

    t = (value or "").strip()
    if not t:
        return None, "is blank"
    if t.startswith("(") and t.endswith(")"):                                  # (1,200.50) is how an accountant writes a negative
        t = "-" + t[1:-1]
    n, cur = order_documents.money(t)
    if cur not in (None, "USD"):
        return None, f"is written in {cur}. Allied's books are in US dollars, so it was not read: export it in dollars or convert it first"
    if n is None:
        return None, "is not a plain amount"
    try:
        return str(Decimal(str(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)), ""
    except InvalidOperation:
        return None, "is not a plain amount"


_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?)?$")


def _flag(cell: str) -> Optional[bool]:
    """Sage writes a yes/no column as blank, TRUE, True, FALSE or False."""
    t = (cell or "").strip().lower()
    return False if t in ("", "false", "0", "no") else True if t in ("true", "1", "yes") else None


def _date_order(values: List[str]) -> Optional[str]:
    """Which way round the slash dates in this file are written, when the file itself settles it: "mdy", "dmy", "mixed", or None (nothing settles it)."""
    mdy = dmy = False
    for v in values:
        m = _SLASH.match(v or "")
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b > 12 and a <= 12:
                mdy = True
            if a > 12 and b <= 12:
                dmy = True
    return "mixed" if mdy and dmy else "mdy" if mdy else "dmy" if dmy else None


def _date(value: str, order: Optional[str]) -> Tuple[Optional[str], str]:
    """(ISO date, "") or (None, the plain reason). A date that could be read two ways is not guessed: the file or the caller must settle it. Anything after
    the date other than a real time, and a year outside 1990-2100, is refused."""
    v = (value or "").strip()
    if not v:
        return None, "no date"
    m = _ISO.match(v)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh, mi, ss = (int(m.group(k) or 0) for k in (4, 5, 6))
        if hh > 23 or mi > 59 or ss > 59:
            return None, f"'{v[:30]}' has a time that is not real"
        if not YEAR_FROM <= y <= YEAR_TO:
            return None, f"'{v[:20]}': the year {y} is outside {YEAR_FROM}-{YEAR_TO}"
        try:
            return date(y, mo, d).isoformat(), ""
        except ValueError:
            return None, f"'{v[:20]}' is not a real date"
    m = _SLASH.match(v)
    if not m:
        return None, f"'{v[:20]}' is not a date (write it as 2026-11-20 or 11/20/2026)"
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y += 2000 if y < 100 else 0
    if order == "mixed":
        return None, f"'{v}': this file writes dates both month-first and day-first, so none are guessed"
    if order is None:
        if a <= 12 and b <= 12 and a != b:
            return None, f"'{v}' could be month/day or day/month. Say which the file uses (date order) and read it again"
        order = "mdy"
    month, day = (a, b) if order == "mdy" else (b, a)
    if not YEAR_FROM <= y <= YEAR_TO:
        return None, f"'{v}': the year {y} is outside {YEAR_FROM}-{YEAR_TO}"
    try:
        return date(y, month, day).isoformat(), ""
    except ValueError:
        return None, f"'{v}' is not a real date written {'month' if order == 'mdy' else 'day'} first"


def _key_of(kind: str, v: Dict[str, Any]) -> str:
    if kind == "parts":
        return v["item_id"]
    if kind == "customers":
        return v["customer_id"]
    if kind == "vendors":
        return v["vendor_id"]
    if kind == "invoices":
        return v["invoice_number"]
    if kind == "payments":
        return v.get("receipt_number") and f"{v['receipt_number']}|{v['invoice_number']}" or hashlib.sha1("|".join(
            str(v.get(k) or "") for k in ("date", "customer_id", "invoice_number", "amount")).encode()).hexdigest()[:40]
    if kind == "open_sales_orders":
        return "SO|" + v["so_number"]
    if kind == "open_purchase_orders":
        return "PO|" + v["po_number"]
    return f"{(v.get('vendor_id') or '').strip().upper()}|{v['bill_number']}"


def parse(data: bytes, filename: str, kind: str = "auto", date_order: Optional[str] = None) -> Dict[str, Any]:
    """The file read and checked, with nothing saved: {kind, mapping, rows: [{row, key, values, problems}]}. Raises ImportRefused for a file that cannot be used."""
    if date_order not in (None, "", "mdy", "dmy"):
        raise ImportRefused("The date order must be mdy (month first, like Sage US) or dmy.")
    grid, notes = _grid(data, filename or "")
    h = _header_row(grid)
    if h < 0:
        raise ImportRefused("No column headings Allied knows were found in the top of this file. Not understood: " + _list([c for c in grid[0] if c]) +
                            ". Export the list or report from Sage 50 again with its usual headings.")
    headers = grid[h]
    kind, mapped, unmapped = _detect(headers, (kind or "auto").strip().lower())
    body = grid[h + 1:]
    if not body:
        raise ImportRefused("The file has headings but no rows.")
    order = date_order or _date_order([r[i] for r in body for i, f in mapped.items() if f in DATE_FIELDS and i < len(r)])
    seen: Dict[str, int] = {}
    same_payment: Dict[str, int] = {}
    merged = 0
    rows = []
    for n, raw in enumerate(body, h + 2):
        values: Dict[str, Any] = {}
        problems: List[str] = []
        for i, f in mapped.items():
            cell = raw[i] if i < len(raw) else ""
            if f in DATE_FIELDS:
                if cell:
                    iso, why = _date(cell, order)
                    if iso:
                        values[f] = iso
                    else:
                        problems.append(f"{f.replace('_', ' ')}: {why}")
            elif f in MONEY_FIELDS:
                if cell:
                    m, why = _money(cell)
                    if m is None:
                        problems.append(f"{f.replace('_', ' ')}: '{cell[:20]}' {why}")
                    else:
                        values[f] = m
            elif f in BOOL_FIELDS:
                flag = _flag(cell)
                if flag is None:
                    problems.append(f"{f.replace('_', ' ')}: '{cell[:20]}' is not TRUE or FALSE")
                else:
                    values[f] = flag
            elif cell:
                values[f] = _text(cell, 64 if f.endswith(("_id", "_number")) or f in ("item_id", "customer_po", "sales_order") else 200)
        for f in REQUIRED[kind]:
            if not values.get(f) and not any(p.startswith(f.replace("_", " ")) for p in problems):
                problems.append(f"{f.replace('_', ' ')} is blank")
        if values.get("inactive"):
            problems.append("marked inactive in Sage: not imported")
        if values.get("credit_memo"):
            problems.append("a credit memo, not an invoice or bill: not imported")
        if values.get("voided"):
            problems.append("voided in Sage: not imported")
        if kind in ("open_sales_orders", "open_purchase_orders"):
            if values.get("closed"):
                values["open_amount"] = "0.00"                         # closed in Sage: kept, but nothing is open on it
            elif "open_amount" not in values and values.get("amount"):
                values["open_amount"] = values["amount"]
        for flag_field in ("closed", "inactive", "credit_memo", "voided"):
            values.pop(flag_field, None)
        key = ""
        if not problems or all(values.get(f) for f in REQUIRED[kind] if f != "date"):
            try:
                key = _key_of(kind, values)
            except KeyError:
                key = ""
        if kind == "payments" and key and not values.get("receipt_number"):
            nth = same_payment.get(key, 0) + 1                           # two identical payments on one day, with no receipt number, are two payments
            same_payment[key] = nth
            if nth > 1:
                key = f"{key}#{nth}"
        if key:
            if key in seen:
                first = next(r for r in rows if r["key"] == key and "extra line" not in " ".join(r["problems"]))
                if kind != "payments" and not problems and first["values"] == values:
                    merged += 1                                          # a Sage journal has one row per line: the same invoice again, with the same heading values
                    continue
                differ = sorted(k for k in set(values) | set(first["values"]) if values.get(k) != first["values"].get(k))
                problems.append(f"appears twice in the file (first on row {seen[key]})" + (f", with different {', '.join(d.replace('_', ' ') for d in differ[:4])}" if differ and kind != "payments" else ""))
            else:
                seen[key] = n
        if kind == "payments" and values.get("amount") and Decimal(values["amount"]) == 0:
            problems.append("a payment of zero is not imported")
        rows.append({"row": n, "key": key, "values": values, "problems": problems})
    return {"kind": kind, "headers": headers, "mapped": {headers[i]: f for i, f in mapped.items()}, "unmapped": unmapped, "rows": rows, "date_order": order, "merged_lines": merged, "notes": notes}


# ----------------------------------------------------------------------------- what a row would change

_MIRROR = {
    "invoices": (SageInvoiceDB, "invoice_number", {"customer_id": "customer_id", "customer_name": "customer_name", "date": "invoice_date", "due_date": "due_date",
                                                   "customer_po": "customer_po", "sales_order": "sales_order", "total": "total", "amount_due": "amount_due"}),
    "payments": (SagePaymentDB, "pay_key", {"receipt_number": "receipt_number", "date": "payment_date", "customer_id": "customer_id",
                                            "invoice_number": "invoice_number", "amount": "amount"}),
    "open_sales_orders": (SageOpenOrderDB, None, {"customer_id": "party_id", "customer_name": "party_name", "date": "order_date", "customer_po": "customer_po",
                                                  "amount": "amount", "open_amount": "open_amount"}),
    "open_purchase_orders": (SageOpenOrderDB, None, {"vendor_id": "party_id", "vendor_name": "party_name", "date": "order_date", "amount": "amount",
                                                     "open_amount": "open_amount"}),
    "bills": (SageBillDB, "bill_key", {"bill_number": "bill_number", "vendor_id": "vendor_id", "vendor_name": "vendor_name", "date": "bill_date",
                                       "due_date": "due_date", "amount": "amount", "amount_due": "amount_due", "po_number": "po_number"}),
}
_MONEY_COLS = {"total", "amount_due", "amount", "open_amount"}


def _stored(model: Any, kind: str, key: str) -> Optional[Any]:
    with SessionLocal() as db:
        if kind in ("open_sales_orders", "open_purchase_orders"):
            tag, number = key.split("|", 1)
            row = db.query(SageOpenOrderDB).filter(SageOpenOrderDB.kind == tag, SageOpenOrderDB.number == number).first()
        else:
            col = {"invoices": SageInvoiceDB.invoice_number, "payments": SagePaymentDB.pay_key, "bills": SageBillDB.bill_key}[kind]
            row = db.query(model).filter(col == key).first()
        if row is None:
            return None
        db.expunge(row)
        return row


def _as_text(attr: str, value: Any) -> str:
    if value is None:
        return ""
    if attr in _MONEY_COLS:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    return str(value)


def _mirror_changes(kind: str, row: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]]]:
    model, _k, fmap = _MIRROR[kind]
    have = _stored(model, kind, row["key"])
    if have is None:
        return "new", []
    changes = []
    for field, attr in fmap.items():
        if field not in row["values"]:
            continue                                                  # a column the file did not carry leaves the stored value alone
        old, new = _as_text(attr, getattr(have, attr, None)), _as_text(attr, row["values"][field])
        if old != new:
            changes.append({"field": field, "old": old, "new": new})
    return ("changed" if changes else "unchanged"), changes


def _master_records(kind: str) -> Dict[str, Dict[str, Any]]:
    from app.core.container import get_entity_matcher

    m = get_entity_matcher()
    return {"parts": m.items_cache, "customers": m.customers_cache, "vendors": m.vendors_cache}[kind]


def _join(*parts: Optional[str]) -> str:
    return ", ".join(p for p in parts if p)


def _master_canonical(kind: str, v: Dict[str, Any]) -> Dict[str, str]:
    """A parts, customers or vendors row in the column names the catalogue importer reads."""
    if kind == "parts":
        keys = ("item_id", "description", "standard_cost", "base_price", "preferred_vendor", "uom", "category", "stock_on_hand", "min_stock_level", "gl_sales_account", "gl_inventory_account")
        return {k: v[k] for k in keys if v.get(k) not in (None, "")}
    if kind == "customers":
        out = {"customer_id": v["customer_id"], "customer_name": v["customer_name"]}
        bill = _join(v.get("bill_line1"), v.get("bill_city"), _join(v.get("bill_state"), v.get("bill_zip")).replace(", ", " "))
        ship = _join(v.get("ship_line1"), v.get("ship_city"), _join(v.get("ship_state"), v.get("ship_zip")).replace(", ", " "))
        for key, val in (("billing_address", bill), ("shipping_address", ship), ("payment_terms", v.get("payment_terms")), ("sales_rep", v.get("sales_rep")),
                         ("email", v.get("email")), ("contact_name", v.get("contact_name")), ("shipping_method", v.get("shipping_method"))):
            if val:
                out[key] = val
        return out
    out = {"vendor_id": v["vendor_id"], "vendor_name": v["vendor_name"]}
    addr = _join(v.get("addr_line1"), v.get("addr_city"), _join(v.get("addr_state"), v.get("addr_zip")).replace(", ", " "))
    for key, val in (("vendor_address", addr), ("payment_terms", v.get("payment_terms")), ("email", v.get("email")), ("phone", v.get("phone"))):
        if val:
            out[key] = val
    return out


def _master_changes(kind: str, row: Dict[str, Any]) -> Tuple[str, List[Dict[str, str]]]:
    have = _master_records(kind).get(row["key"])
    if have is None:
        return "new", []
    changes = []
    for field, new in _master_canonical(kind, row["values"]).items():
        old = str(have.get(field) if have.get(field) is not None else "")
        same = old.strip() == str(new).strip()
        if not same:
            try:
                same = Decimal(old.replace(",", "")) == Decimal(str(new).replace(",", ""))
            except InvalidOperation:
                pass
        if not same:
            changes.append({"field": field, "old": old, "new": str(new)})
    return ("changed" if changes else "unchanged"), changes


def classify(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Each row as new, changed, unchanged or a problem, with the plain reason. Reads the database; saves nothing."""
    kind = parsed["kind"]
    out = {"new": [], "changed": [], "unchanged": [], "problems": []}
    for row in parsed["rows"]:
        if row["problems"] or not row["key"]:
            out["problems"].append({"row": row["row"], "key": row["key"], "reason": "; ".join(row["problems"]) or "no usable number"})
            continue
        state, changes = (_master_changes if kind in MASTER else _mirror_changes)(kind, row)
        entry = {"row": row["row"], "key": row["key"], "changes": changes}
        out[{"new": "new", "changed": "changed", "unchanged": "unchanged"}[state]].append(entry)
    return out


# ----------------------------------------------------------------------------- preview and commit

_PREVIEWS: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def _remember(entry: Dict[str, Any]) -> str:
    """Keeps a preview for commit. Each person has their own room (10 previews), so one person's previews never push out another's."""
    now = time.monotonic()
    with _lock:
        for t in [t for t, e in _PREVIEWS.items() if e["expires"] < now]:
            _PREVIEWS.pop(t, None)
        mine = [t for t, e in _PREVIEWS.items() if e["user"] == entry["user"]]
        while len(mine) >= PREVIEWS_PER_USER:
            _PREVIEWS.pop(min(mine, key=lambda t: _PREVIEWS[t]["expires"]), None)
            mine = [t for t, e in _PREVIEWS.items() if e["user"] == entry["user"]]
        while len(_PREVIEWS) >= PREVIEWS_IN_ALL:
            _PREVIEWS.pop(min(_PREVIEWS, key=lambda t: _PREVIEWS[t]["expires"]), None)
        token = secrets.token_urlsafe(18)
        _PREVIEWS[token] = {**entry, "expires": now + TOKEN_MINUTES * 60}
        return token


_commit_lock = threading.RLock()          # one commit at a time: a double click or two managers cannot both write the same file


def _identity(file_hash: str, date_order: Optional[str]) -> str:
    """What makes a file "the same file": its bytes, and the date order it was read with, so a file read with the wrong order can be read again with the right one."""
    return file_hash if not date_order else hashlib.sha256(f"{file_hash}|date_order={date_order}".encode()).hexdigest()


def _open_tag(kind: str) -> Optional[str]:
    return {"open_sales_orders": "SO", "open_purchase_orders": "PO"}.get(kind)


def _open_rows_missing(db, kind: str, parsed: Dict[str, Any]) -> List[SageOpenOrderDB]:
    """The orders the mirror holds as open that this (newer) open-orders export no longer lists: they have been closed or filled in Sage."""
    tag = _open_tag(kind)
    if not tag:
        return []
    present = {r["key"].split("|", 1)[1] for r in parsed["rows"] if r["key"]}
    return [x for x in db.query(SageOpenOrderDB).filter(SageOpenOrderDB.kind == tag).all()
            if x.number not in present and (x.open_amount is None or Decimal(str(x.open_amount)) > 0)]


def already_imported(kind: str, file_hash: str) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        row = db.query(SageImportDB).filter(SageImportDB.kind == kind, SageImportDB.file_hash == file_hash).first()
        return _history_row(row) if row else None


def preview(data: bytes, filename: str, kind: str, user: Dict[str, Any], date_order: Optional[str] = None) -> Dict[str, Any]:
    """What importing this file would do. Saves nothing; hands back a token for commit (kept 30 minutes, in memory)."""
    parsed = parse(data, filename, kind, date_order)
    result = classify(parsed)
    file_hash = hashlib.sha256(data).hexdigest()
    identity = _identity(file_hash, date_order or None)
    prior = already_imported(parsed["kind"], identity)
    counts = {k: len(v) for k, v in result.items()}
    with SessionLocal() as db:
        closing = [x.number for x in _open_rows_missing(db, parsed["kind"], parsed)]
    token = _remember({"data": data, "filename": (filename or "")[:255], "kind": parsed["kind"], "date_order": date_order or None, "user": user.get("email", ""), "hash": identity})
    unmapped = parsed["unmapped"]
    return {
        "token": token, "kind": parsed["kind"], "kind_label": LABELS[parsed["kind"]], "already_imported": prior,
        "file": {"name": (filename or "")[:255], "size": len(data), "sha256": file_hash, "rows": len(parsed["rows"]), "merged_lines": parsed.get("merged_lines", 0)},
        "warnings": _warnings(data, filename, parsed),
        "columns": {"mapped": [{"header": h, "field": f} for h, f in parsed["mapped"].items()], "unmapped": unmapped, "date_order": parsed["date_order"]},
        "counts": counts,
        "will_close": {"count": len(closing), "sample": closing[:PREVIEW_ROWS]},
        "rows": {"new": result["new"][:PREVIEW_ROWS], "changed": result["changed"][:PREVIEW_ROWS], "unchanged": counts["unchanged"], "problems": result["problems"][:PREVIEW_PROBLEMS]},
        "message": (("This exact file was already imported; importing it again changes nothing. " if prior else "") +
                    f"{counts['new']} new, {counts['changed']} changed, {counts['unchanged']} unchanged, {counts['problems']} to look at. " +
                    (f"{len(closing)} order(s) held as open from an earlier file are not in this one and will be marked closed. " if closing else "") + "Nothing is saved until you import it." +
                    (f" Columns not used: {_list(unmapped)}." if unmapped else "")),
    }


def _warnings(data: bytes, filename: str, parsed: Dict[str, Any]) -> List[str]:
    out = list(parsed.get("notes") or [])
    if data[:4] == b"PK\x03\x04" or (filename or "").lower().endswith((".xlsx", ".xlsm")):
        out.append("This came through Excel. Excel can drop leading zeros (01 becomes 1) and turn codes like 10-25 into dates. If a number looks wrong, export the CSV straight from Sage 50 and use that.")
    if parsed.get("merged_lines"):
        out.append(f"{parsed['merged_lines']} extra row(s) were more lines of an invoice, bill or order already read (a Sage journal has one row per line) and were combined.")
    if parsed["kind"] == "payments":
        out.append("Payment amounts are read as amounts received; a negative amount is kept as a refund. Check the first few against Sage before you import.")
    return out


def _history_row(r: SageImportDB) -> Dict[str, Any]:
    return {"id": r.id, "kind": r.kind, "kind_label": LABELS.get(r.kind, r.kind), "file_name": r.file_name, "file_hash": r.file_hash, "rows_total": r.rows_total,
            "created": r.created, "updated": r.updated, "unchanged": r.unchanged, "problems": r.problems, "imported_by": r.imported_by,
            "imported_at": r.imported_at.isoformat() if r.imported_at else None}


def history(limit: int = 50) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        return [_history_row(r) for r in db.query(SageImportDB).order_by(SageImportDB.id.desc()).limit(max(1, min(int(limit), 200))).all()]


def _money_value(v: Optional[str]) -> Optional[Decimal]:
    return Decimal(v) if v not in (None, "") else None


def _save_mirror(db, kind: str, parsed: Dict[str, Any], rows: List[Dict[str, Any]], import_id: int) -> Tuple[int, int, int]:
    model, _k, fmap = _MIRROR[kind]
    created = updated = unchanged = 0
    for row in rows:
        v = row["values"]
        if kind in ("open_sales_orders", "open_purchase_orders"):
            tag, number = row["key"].split("|", 1)
            have = db.query(SageOpenOrderDB).filter(SageOpenOrderDB.kind == tag, SageOpenOrderDB.number == number).first()
        else:
            col = {"invoices": SageInvoiceDB.invoice_number, "payments": SagePaymentDB.pay_key, "bills": SageBillDB.bill_key}[kind]
            have = db.query(model).filter(col == row["key"]).first()
        fields = {}
        for field, attr in fmap.items():
            if field in v:
                fields[attr] = _money_value(v[field]) if attr in _MONEY_COLS else v[field]
        if have is None:
            ident = {"invoices": {"invoice_number": row["key"]}, "payments": {"pay_key": row["key"]},
                     "open_sales_orders": {"kind": "SO", "number": row["key"].split("|", 1)[-1]},
                     "open_purchase_orders": {"kind": "PO", "number": row["key"].split("|", 1)[-1]}, "bills": {"bill_key": row["key"]}}[kind]
            db.add(model(**ident, **fields, import_id=import_id))
            created += 1
            continue
        changed = False
        for attr, val in fields.items():
            if _as_text(attr, getattr(have, attr, None)) != _as_text(attr, val):
                setattr(have, attr, val)
                changed = True
        if changed:
            have.import_id = import_id
            updated += 1
        else:
            unchanged += 1
    return created, updated, unchanged


def commit(token: str, user: Dict[str, Any], confirm_close: bool = False) -> Dict[str, Any]:
    """Saves what the preview showed. A token that has run out or never existed is refused; the same file twice is accepted and changes nothing."""
    with _commit_lock:
        return _commit_locked(token, user, confirm_close)


def _already_answer(prior: Dict[str, Any]) -> Dict[str, Any]:
    return {"already_imported": True, "history": prior, "created": 0, "updated": 0, "unchanged": prior["rows_total"], "problems": 0, "closed": 0,
            "message": "This exact file was already imported on " + str(prior["imported_at"])[:10] + ". Nothing was changed."}


def _commit_locked(token: str, user: Dict[str, Any], confirm_close: bool = False) -> Dict[str, Any]:
    with _lock:
        entry = _PREVIEWS.get(str(token or ""))
        if entry is None or entry["expires"] < time.monotonic():
            raise ImportRefused("That preview has expired. Choose the file again to see what it would do, then import it.")
    kind, data, name, file_hash = entry["kind"], entry["data"], entry["filename"], entry["hash"]
    who = user.get("email", "system")
    prior = already_imported(kind, file_hash)
    if prior and confirm_close and _open_tag(kind):
        # the file was imported before but its close step was held back: a person now confirms, so only the close step runs
        with SessionLocal() as db:
            newer = db.query(SageImportDB).filter(SageImportDB.kind == kind, SageImportDB.id > prior["id"]).count()
        if newer:
            with _lock:
                _PREVIEWS.pop(token, None)
            return {**_already_answer(prior), "closed": 0, "not_closed": 0, "message": "A newer open-orders file has been imported since this one, so nothing was closed: closing from this older file could close orders the newer one opened. Export the open orders from Sage again and import that file."}
        parsed = parse(data, name, kind, entry.get("date_order"))
        with SessionLocal() as db:
            gone = _open_rows_missing(db, kind, parsed)
            for x in gone:
                x.open_amount, x.import_id = Decimal("0.00"), prior["id"]
            db.commit()
            numbers = [x.number for x in gone]
        if numbers:
            audit_log(action="SAGE_IMPORT", resource_type=f"SAGE_{kind.upper()}", resource_id=str(prior["id"]), actor_email=who, actor_role=user.get("role", ""),
                      detail=f"{LABELS[kind]} from file {name[:60]}: {len(numbers)} no longer open (close confirmed)")
        with _lock:
            _PREVIEWS.pop(token, None)
        return {**_already_answer(prior), "already_imported": False, "closed": len(numbers), "message": f"{len(numbers)} order(s) marked closed. The rows of this file were already imported."}
    if prior:
        with _lock:
            _PREVIEWS.pop(token, None)
        return _already_answer(prior)
    parsed = parse(data, name, kind, entry.get("date_order"))
    sorted_rows = classify(parsed)
    good_keys = {r["key"] for bucket in ("new", "changed", "unchanged") for r in sorted_rows[bucket]}
    rows = [r for r in parsed["rows"] if r["key"] in good_keys and not r["problems"]]
    problems = len(sorted_rows["problems"])
    closed: List[str] = []
    skipped_close = 0
    try:
        if kind in MASTER:
            created, updated, unchanged = _commit_master(kind, rows, who, user.get("role", "SYSTEM"))
            import_id = _write_history(kind, name, file_hash, len(parsed["rows"]), created, updated, unchanged, problems, who)
        else:
            with SessionLocal() as db:
                hist = SageImportDB(kind=kind, file_name=name, file_hash=file_hash, rows_total=len(parsed["rows"]), created=0, updated=0, unchanged=0, problems=problems, imported_by=who)
                db.add(hist)
                db.flush()
                created, updated, unchanged = _save_mirror(db, kind, parsed, rows, hist.id)
                missing = _open_rows_missing(db, kind, parsed)
                total_open = len([x for x in db.query(SageOpenOrderDB).filter(SageOpenOrderDB.kind == _open_tag(kind)).all() if x.open_amount is None or Decimal(str(x.open_amount)) > 0]) if missing else 0
                if missing and not confirm_close and (len(missing) > 20 or len(missing) * 2 > total_open):
                    skipped_close = len(missing)                                # looks like a cut-off or partial export: close nothing unless a person confirms
                    missing = []
                for x in missing:
                    x.open_amount, x.import_id = Decimal("0.00"), hist.id
                    closed.append(x.number)
                hist.created, hist.updated, hist.unchanged = created, updated, unchanged
                db.commit()
                import_id = hist.id
    except IntegrityError:                                                   # another commit of this same file got there first: that is the answer, not a server error
        again = already_imported(kind, file_hash)
        if again:
            with _lock:
                _PREVIEWS.pop(token, None)
            return _already_answer(again)
        raise
    audit_log(action="SAGE_IMPORT", resource_type=f"SAGE_{kind.upper()}", resource_id=str(import_id), actor_email=who, actor_role=user.get("role", ""),
              detail=f"{LABELS[kind]} from file {name[:60]} ({file_hash[:12]}): {created} new, {updated} changed, {unchanged} unchanged, {problems} left out"
                     + (f", {len(closed)} no longer open" if closed else ""))
    with _lock:
        _PREVIEWS.pop(token, None)
    return {"already_imported": False, "import_id": import_id, "kind": kind, "created": created, "updated": updated, "unchanged": unchanged, "problems": problems,
            "closed": len(closed), "not_closed": skipped_close, "message": f"Imported {LABELS[kind].lower()}: {created} new, {updated} changed, {unchanged} unchanged"
            + (f"; {len(closed)} no longer open" if closed else "")
            + (f"; {skipped_close} held orders are missing from this file, which is more than half of those open, so none were closed (import again with confirm_close if the file is complete; every open order not in the file is closed, not only these)" if skipped_close else "") + (f"; {problems} row(s) were left out." if problems else ".")}


def _write_history(kind: str, name: str, file_hash: str, rows_total: int, created: int, updated: int, unchanged: int, problems: int, who: str) -> int:
    with SessionLocal() as db:
        hist = SageImportDB(kind=kind, file_name=name, file_hash=file_hash, rows_total=rows_total, created=created, updated=updated, unchanged=unchanged,
                            problems=problems, imported_by=who)
        db.add(hist)
        db.commit()
        return hist.id


def _commit_master(kind: str, rows: List[Dict[str, Any]], who: str, role: str) -> Tuple[int, int, int]:
    """Parts, customers and vendors go through the existing catalogue importer (it merges by id, writes the file safely and audits)."""
    from app.services.csv_import_service import csv_import_service

    before = {r["key"]: _master_changes(kind, r)[0] for r in rows}
    todo = [r for r in rows if before[r["key"]] != "unchanged"]
    created = sum(1 for r in todo if before[r["key"]] == "new")
    updated = len(todo) - created
    if todo:
        canon = [_master_canonical(kind, r["values"]) for r in todo]
        columns = list(dict.fromkeys(k for c in canon for k in c))
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(canon)
        result = csv_import_service.import_csv(buf.getvalue(), MASTER[kind], actor_email=who, actor_role=role)
        if result.status == "FAILED" and not result.records_created and not result.records_updated:
            raise ImportRefused("The catalogue importer refused the rows: " + "; ".join(result.errors)[:300])
    return created, updated, len(rows) - len(todo)


# ----------------------------------------------------------------------------- using the mirrors

def _due(row: SageInvoiceDB, paid: Dict[str, Decimal]) -> Decimal:
    if row.amount_due is not None:
        return Decimal(str(row.amount_due))
    return max(Decimal("0.00"), Decimal(str(row.total)) - paid.get(row.invoice_number, Decimal("0.00")))


def _same_party(row_id: Optional[str], row_name: Optional[str], cid: str, cname: str) -> bool:
    """The same customer (or vendor): the same id, or the same name once cleaned. A PO number alone never decides, two customers can quote the same one."""
    from app.services.coordination import jobs

    if cid and row_id and str(cid).strip().upper() == str(row_id).strip().upper():
        return True
    return bool(cname and row_name and jobs.same_customer(cname, row_name))


def order_money_status(orders: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """For a page of orders: the Sage invoice for each (by sales order number, else the customer's PO), whether it is paid, and what is still due; and
    whether the order is already open in Sage. Reads the mirrors only; empty when nothing has been imported."""
    sos = {str(o.get("sales_order_number")) for o in orders if o.get("sales_order_number")}
    pos = {str(o.get("customer_po_number")) for o in orders if o.get("customer_po_number")}
    out: Dict[str, Dict[str, Any]] = {}
    if not sos and not pos:
        return out
    with SessionLocal() as db:
        invoices = db.query(SageInvoiceDB).filter((SageInvoiceDB.sales_order.in_(sos)) | (SageInvoiceDB.customer_po.in_(pos))).all() if (sos or pos) else []
        opens = [x for x in db.query(SageOpenOrderDB).filter(SageOpenOrderDB.kind == "SO", (SageOpenOrderDB.number.in_(sos)) | (SageOpenOrderDB.customer_po.in_(pos))).all()
                 if x.open_amount is None or Decimal(str(x.open_amount)) > 0]
        numbers = [i.invoice_number for i in invoices]
        paid: Dict[str, Decimal] = {}
        if numbers:
            for inv, amt in db.query(SagePaymentDB.invoice_number, SagePaymentDB.amount).filter(SagePaymentDB.invoice_number.in_(numbers)).all():
                paid[inv] = paid.get(inv, Decimal("0.00")) + Decimal(str(amt))
        for o in orders:
            so, po = str(o.get("sales_order_number") or ""), str(o.get("customer_po_number") or "")
            cid, cname = str(o.get("customer_id") or ""), str(o.get("customer_name") or o.get("customer") or "")
            mine = [i for i in invoices if (so and i.sales_order == so)] or [i for i in invoices if po and i.customer_po == po and _same_party(i.customer_id, i.customer_name, cid, cname)]
            info: Dict[str, Any] = {}
            if mine:
                total = sum((Decimal(str(i.total)) for i in mine), Decimal("0.00"))
                due = sum((_due(i, paid) for i in mine), Decimal("0.00"))
                status = "PAID" if due <= 0 else "PART_PAID" if due < total else "UNPAID"
                info.update({"invoice_numbers": [i.invoice_number for i in mine], "invoiced_total": str(total), "amount_due": str(due), "paid_status": status})
            elsewhere = [x for x in opens if x.number == so or (po and x.customer_po == po and _same_party(x.party_id, x.party_name, cid, cname))]
            if elsewhere:
                info["open_in_sage"] = [{"number": x.number, "open_amount": str(x.open_amount) if x.open_amount is not None else None} for x in elsewhere]
            if info:
                out[so or po] = info
    return out


def bill_in_sage(vendor_name: str, bill_number: str) -> Optional[Dict[str, Any]]:
    """The vendor bill Sage already holds under this number from this vendor (by vendor name or id), or None."""
    from app.services.coordination import jobs

    num = str(bill_number or "").strip()
    if not num:
        return None
    with SessionLocal() as db:
        for row in db.query(SageBillDB).filter(SageBillDB.bill_number == num).all():
            if jobs.canon_vendor(row.vendor_name or "") == jobs.canon_vendor(vendor_name or "") or jobs.canon_vendor(row.vendor_id or "") == jobs.canon_vendor(vendor_name or ""):
                return {"bill_number": row.bill_number, "vendor": row.vendor_name or row.vendor_id, "date": row.bill_date, "amount": str(row.amount),
                        "amount_due": str(row.amount_due) if row.amount_due is not None else None}
    return None


def already_open_in_sage(customer_po: str = "", po_numbers: Optional[List[str]] = None, customer_id: str = "", customer_name: str = "") -> List[Dict[str, Any]]:
    """Open Sage orders that match a customer's PO number (a sales order) or Allied PO numbers (purchase orders): what the new-order check warns about."""
    po_numbers = [str(p) for p in (po_numbers or []) if p]
    found: List[Dict[str, Any]] = []
    with SessionLocal() as db:
        if customer_po:
            for x in db.query(SageOpenOrderDB).filter(SageOpenOrderDB.kind == "SO", SageOpenOrderDB.customer_po == str(customer_po)).all():
                if x.open_amount is not None and Decimal(str(x.open_amount)) <= 0:
                    continue                                                      # closed in Sage
                if (customer_id or customer_name) and not _same_party(x.party_id, x.party_name, customer_id, customer_name):
                    continue                                                      # another customer's order with the same PO number
                found.append({"kind": "SO", "number": x.number, "party": x.party_name, "open_amount": str(x.open_amount) if x.open_amount is not None else None})
        if po_numbers:
            for x in db.query(SageOpenOrderDB).filter(SageOpenOrderDB.kind == "PO", SageOpenOrderDB.number.in_(po_numbers)).all():
                if x.open_amount is not None and Decimal(str(x.open_amount)) <= 0:
                    continue
                found.append({"kind": "PO", "number": x.number, "party": x.party_name, "open_amount": str(x.open_amount) if x.open_amount is not None else None})
    return found
