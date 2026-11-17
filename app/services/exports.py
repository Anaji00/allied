"""Downloads of the app's lists as Excel (.xlsx), CSV or a printable PDF.

Read-only: every export is a copy of what the screens already show, built here, never sent anywhere.
Cells that start with = + - @ are written as plain text so a spreadsheet can never run them as a formula
(a customer name or email subject is untrusted text). Money and dates keep their value.
"""

import csv
import io
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core.database import SessionLocal

Column = Tuple[str, str]                       # (key in the row, heading people read)
MAX_ROWS = 5000
FORMATS = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv; charset=utf-8",
           "pdf": "application/pdf"}
_FORMULA = re.compile(r"^[=+\-@\t\r]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")      # characters a spreadsheet file cannot hold


class ExportError(ValueError):
    """Something a person can read: why this can't be downloaded."""


def _cell(value: Any) -> Any:
    """A value fit for a spreadsheet cell."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value        # the database keeps UTC without saying so
        return aware.astimezone().replace(tzinfo=None).isoformat(sep=" ", timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(_cell(v)) for v in value)
    elif isinstance(value, dict):
        value = "; ".join(f"{k}: {_cell(v)}" for k, v in value.items())
    if isinstance(value, str):
        value = _CONTROL.sub("", value)
        return "'" + value if _FORMULA.match(value) else value
    return value


def _grid(columns: List[Column], rows: List[Dict[str, Any]]) -> List[List[Any]]:
    return [[_cell(r.get(k)) for k, _ in columns] for r in rows[:MAX_ROWS]]


def to_csv(columns: List[Column], rows: List[Dict[str, Any]]) -> bytes:
    out = io.StringIO(newline="")
    w = csv.writer(out)
    w.writerow([_cell(label) for _, label in columns])
    w.writerows(_grid(columns, rows))
    return ("﻿" + out.getvalue()).encode("utf-8")           # the marker makes Excel read accents correctly


def to_xlsx(title: str, columns: List[Column], rows: List[Dict[str, Any]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r"[\[\]\*\?/\\:]", " ", title)[:31] or "Export"
    ws.append([_cell(label) for _, label in columns])
    grid = _grid(columns, rows)
    for r in grid:
        ws.append(r)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F")
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for i, (_, label) in enumerate(columns, 1):
        widest = max([len(label)] + [len(str(r[i - 1])) for r in grid[:200]])
        ws.column_dimensions[get_column_letter(i)].width = min(60, max(10, widest + 2))
    ws.freeze_panes = "A2"
    if columns and grid:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(grid) + 1}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_CJK = re.compile(r"([\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]+)")
_LATIN_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf")
_fonts: Dict[str, str] = {}


def _pdf_fonts() -> Tuple[str, str]:
    """(font for Latin text including Vietnamese accents, font for Chinese). Falls back to the built-in Helvetica when no
    accented-capable font is installed (the Docker image installs DejaVu); Chinese uses reportlab's built-in CID font."""
    if not _fonts:
        from pathlib import Path

        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.ttfonts import TTFont

        _fonts["latin"] = "Helvetica"
        for path in _LATIN_FONTS:
            if Path(path).is_file():
                try:
                    pdfmetrics.registerFont(TTFont("AlliedSans", path))
                    _fonts["latin"] = "AlliedSans"
                    break
                except Exception:  # noqa: BLE001 - try the next one
                    continue
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            _fonts["cjk"] = "STSong-Light"
        except Exception:  # noqa: BLE001
            _fonts["cjk"] = _fonts["latin"]
    return _fonts["latin"], _fonts["cjk"]


def _pdf_text(text: str, cjk_font: str) -> str:
    """Escaped text with each run of Chinese characters set in the Chinese font."""
    from xml.sax.saxutils import escape

    return "".join(f'<font name="{cjk_font}">{escape(part)}</font>' if i % 2 else escape(part) for i, part in enumerate(_CJK.split(text)))


def to_pdf(title: str, columns: List[Column], rows: List[Dict[str, Any]]) -> bytes:
    """A landscape table for printing. Wide lists keep their first columns; long text is shortened."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    cols = columns[:9]
    latin, cjk = _pdf_fonts()
    style = getSampleStyleSheet()["BodyText"]
    style.fontName, style.fontSize, style.leading = latin, 7, 8.5
    grid = [[Paragraph(f"<b>{_pdf_text(label, cjk)}</b>", style) for _, label in cols]]
    for r in _grid(cols, rows)[:1500]:
        grid.append([Paragraph(_pdf_text(str(v if len(str(v)) <= 60 else str(v)[:57] + "..."), cjk), style) for v in r])
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), leftMargin=24, rightMargin=24, topMargin=28, bottomMargin=28, title=title)
    table = Table(grid, repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCE6F2")), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F8FB")])]))
    note = "" if len(columns) <= 9 else f" (first 9 of {len(columns)} columns; use Excel for all)"
    if len(rows) > 1500:
        note += f" (first 1,500 of {len(rows):,} rows; use Excel for all)"
    doc.build([Paragraph(f"<b>{escape(title)}</b> &nbsp; {date.today().isoformat()}{note}", getSampleStyleSheet()["Heading4"]),
               Spacer(1, 6), table])
    return buf.getvalue()


def render(fmt: str, title: str, columns: List[Column], rows: List[Dict[str, Any]]) -> bytes:
    fmt = (fmt or "xlsx").lower()
    if fmt == "xlsx":
        return to_xlsx(title, columns, rows)
    if fmt == "csv":
        return to_csv(columns, rows)
    if fmt == "pdf":
        return to_pdf(title, columns, rows)
    raise ExportError(f"Choose a format: {', '.join(FORMATS)}.")


# --------------------------------- what can be downloaded ---------------------------------


def _orders(manager: bool) -> Tuple[List[Column], List[Dict[str, Any]]]:
    from app.models.db_models import OrderDB

    cols = [("customer_po_number", "Customer PO"), ("customer_name", "Customer"), ("sales_order_number", "Our order no."),
            ("facility", "Facility"), ("currency", "Currency"), ("status", "Status"), ("po_date", "PO date"),
            ("total_revenue", "Order total"), ("shipping_address", "Ship to"), ("created_at", "Created")]
    if manager:
        cols[8:8] = [("total_expense", "Cost"), ("gross_margin_pct", "Margin %")]
    with SessionLocal() as db:
        rows = [{k: getattr(o, k) for k, _ in cols} for o in db.query(OrderDB).order_by(OrderDB.created_at.desc()).limit(MAX_ROWS)]
    return cols, rows


def _slips(manager: bool):
    from app.services import packing_slip_queue

    cols = [("customer_po_number", "Customer PO"), ("sales_order_number", "Our order no."), ("status", "Status"),
            ("attempts", "Tries"), ("last_error", "Last problem"), ("created_at", "Queued"), ("ready_at", "Made"),
            ("printed_at", "Printed"), ("printed_by", "Printed by")]
    return cols, packing_slip_queue.list_queue(limit=MAX_ROWS)["jobs"]


def _rates(manager: bool):
    from app.services import carrier_rates

    cols = [("carrier", "Carrier"), ("service", "Service"), ("regions", "Applies to"), ("rate_usd", "Price (USD)"),
            ("per_lb_usd", "Per pound (USD)"), ("transit_days", "Transit days"), ("as_of", "Last checked"), ("status", "Status"),
            ("enabled", "In use"), ("note", "Note")]
    return cols, carrier_rates.view()


def _vendor_jobs(manager: bool):
    from app.services.coordination import vendors

    cols = [("vendor", "Vendor"), ("description", "Description"), ("po_number", "PO"), ("status", "Status"), ("state", "Where it stands"),
            ("ordered_on", "Ordered"), ("acknowledged_on", "Acknowledged"), ("promised_original", "First promise"),
            ("promised_on", "Current promise"), ("expected", "Expected"), ("need_by", "Need by"), ("shipped_on", "Shipped"),
            ("owner_email", "Owner")]
    return cols, vendors.overview(include_shipped=True)["jobs"]


def _rfqs(manager: bool):
    from app.services.coordination import rfqs

    cols = [("number", "RFQ"), ("customer", "Customer"), ("subject", "Subject"), ("status", "Status"), ("state", "Where it stands"),
            ("owner_email", "Owner"), ("vendor", "Vendor"), ("received_at", "Received"), ("effective_due_at", "Due"),
            ("quote_number", "Quote no."), ("quote_amount", "Quote amount")]
    return cols, rfqs.list_rfqs(open_only=False, limit=MAX_ROWS)


def _shipments(manager: bool):
    from app.models.db_models import ShipmentDB

    cols = [("reference", "Booking"), ("forwarder", "Forwarder"), ("mode", "Mode"), ("carrier", "Carrier"), ("container_no", "Container"),
            ("awb", "Air waybill"), ("origin", "From"), ("destination", "To"), ("etd", "Departs"), ("eta_current", "Arrives"),
            ("free_time_ends", "Free time ends"), ("status", "Status"), ("owner_email", "Owner")]
    with SessionLocal() as db:
        rows = [{k: getattr(s, k) for k, _ in cols} for s in db.query(ShipmentDB).order_by(ShipmentDB.created_at.desc()).limit(MAX_ROWS)]
    return cols, rows


DATASETS: Dict[str, Tuple[str, Callable[[bool], Tuple[List[Column], List[Dict[str, Any]]]]]] = {
    "orders": ("Orders", _orders), "packing-slips": ("Packing slips", _slips), "carrier-prices": ("Carrier prices", _rates),
    "vendor-jobs": ("Vendor jobs", _vendor_jobs), "rfqs": ("RFQs", _rfqs), "shipments": ("Shipments", _shipments),
}


def dataset(name: str, manager: bool = False) -> Tuple[str, List[Column], List[Dict[str, Any]]]:
    if name not in DATASETS:
        raise LookupError(f"There is no '{name}' list to download. Choose one of: {', '.join(DATASETS)}.")
    title, loader = DATASETS[name]
    cols, rows = loader(manager)
    return title, cols, rows


def board(board_id: str) -> Tuple[str, List[Column], List[Dict[str, Any]]]:
    """A monday board as the app holds it (read-only copy): every column monday shows, plus the app's view of the row."""
    from app.services.coordination import boards

    view = boards.view(board_id)                   # raises LookupError with a plain sentence if it hasn't been read
    cols: List[Column] = [("_name", "Item"), ("_group", "Group")] + [(f"v:{c['id']}", c["title"]) for c in view["columns"]]
    has_app = any(i.get("app") for i in view["items"])
    if has_app:
        cols += [("_state", "App: where it stands"), ("_reasons", "App: why")]
    groups = {g.get("id"): g.get("title") for g in view.get("groups") or []}
    rows = []
    for it in view["items"]:
        row = {"_name": it.get("name"), "_group": it.get("group_title") or groups.get(it.get("group")) or ""}
        row.update({f"v:{k}": v for k, v in (it.get("values") or {}).items()})
        app = it.get("app") or {}
        row["_state"], row["_reasons"] = app.get("state"), app.get("reasons")
        rows.append(row)
    return f"{view['name']}", cols, rows
