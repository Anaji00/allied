"""The two paper forms Allied works from, as PDFs the app can make: the vendor PURCHASE ORDER (Allied letterhead, what to make, terms,
blind-shipment notes) and the internal SALES ORDER INFORMATION sheet (the job's cover sheet: customer, their reference, up to four
vendors with PO number, dates and totals, freight). They follow Allied's own examples (PO 9248, SO 12889).
The PO is for a vendor, so it never names the customer or Allied's sales order. Its blind-shipment note names the VENDOR (the vendor's own markings stay off the shipment). The sheet is internal and is never sent to a customer."""

import io
import threading
import unicodedata
import re
from datetime import date
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

LETTERHEAD = ("<b>ALLIED WELDING SYSTEMS</b><br/>P.O. BOX 795459 &bull; DALLAS, TEXAS 75379-5459<br/>"
              "(972) 248-8838 &bull; FAX (972) 248-8890<br/>E-mail: info@alliedweldingsystems.com")
def default_notes(vendor_name: str) -> List[str]:
    """The two standing notes on Allied's PO (from PO 9248). A blind shipment hides the VENDOR from Allied's customer, so the first note names
    the vendor: nothing of the vendor's, no markings or papers, goes in or on the shipment except the packing list stuck on the outside."""
    who = (vendor_name or "THE VENDOR").strip().upper()
    return [f"NO MARKINGS OR DOCUMENTS OF {who} OF ANY KIND INSIDE OR ANYWHERE ON SHIPMENT BESIDES PACKING LIST (MUST BE ATTACHED OUTSIDE OF BOX).",
            "MUST CONTACT ALLIED ASAP IF ANY CHANGES TO ORDER OR LEAD TIME ORIGINALLY PROVIDED."]


_CJK_RUN = re.compile(r"([\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]+)")
_cjk_font = {"name": None}


def _x(v: Any) -> str:
    """Escaped text for a PDF paragraph, with each run of Chinese set in the Chinese font (the standard fonts print boxes for it)."""
    if v in (None, ""):
        return ""
    text = str(v)
    if not _CJK_RUN.search(text):
        return escape(text)
    return "".join(f'<font name="{_cjk_font["name"] or "Helvetica"}">{escape(part)}</font>' if i % 2 else escape(part) for i, part in enumerate(_CJK_RUN.split(text)))


def _base_font(*blobs: Any) -> str:
    """Helvetica (Allied's usual look) unless the document holds accented or Chinese text: then a Unicode-capable font, so Vietnamese accents and
    Chinese names print as letters, not boxes."""
    text = " ".join(str(b) for b in blobs if b)
    if all(ord(ch) < 256 for ch in text) and not _CJK_RUN.search(text):
        return "Helvetica"
    try:
        from app.services import exports

        latin, cjk = exports._pdf_fonts()
    except Exception:  # noqa: BLE001 - no Unicode font here: the standard fonts still make a PDF (accents may print as boxes)
        return "Helvetica"
    _cjk_font["name"] = cjk
    return latin if any(ord(ch) >= 256 and not _CJK_RUN.match(ch) for ch in text) or _CJK_RUN.search(text) else "Helvetica"


def _money(v: Any) -> str:
    """An amount as printed, blank when it is not a readable number (never "nan" or a value read from "1e5")."""
    n = money(v)[0] if v not in (None, "") else None
    return f"{n:,.2f}" if n is not None else ""


def num(v: Any) -> Any:
    """A finite, sensible number from text ("1,200.50", "2"), else None. "1e999", "nan", "inf" and absurd sizes are not numbers here."""
    import math

    s = str(v if v is not None else "").strip()
    if not re.fullmatch(r"[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?", s, re.ASCII):       # plain digits only: no "1_000", "1e3", other scripts' digits
        return None
    x = float(s.replace(",", ""))
    return x if math.isfinite(x) and abs(x) < 1e9 else None


_CURRENCY = re.compile(r"(USD|CAD|CNY|RMB|EUR|GBP|JPY|US\$|\$|\u00a5|\uffe5|\u20ac|\u00a3)", re.I)
_CODES = {"$": "USD", "US$": "USD", "\u00a5": "CNY", "\uffe5": "CNY", "RMB": "CNY", "\u20ac": "EUR", "\u00a3": "GBP"}


def money(v: Any) -> Any:
    """(amount, currency) from text like "$1,200.50", "USD 1200", "\u00a51,250.00": the number and the currency named (or None). An amount written the
    European way ("1.234,50") is ambiguous, so it is refused (amount None) rather than read as 1.23. Never raises."""
    text = str(v if v is not None else "").strip()
    found = _CURRENCY.findall(text)
    cur = None
    if found:
        coded = [f for f in found if f.isalpha() and len(f) == 3]          # an explicit code (CAD) beats a symbol ($)
        found = coded or found
        c = found[0].upper() if len(found[0]) > 1 else found[0]
        cur = _CODES.get(c, c)
    bare = _CURRENCY.sub(" ", text).strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+,\d{1,2}", bare) or re.fullmatch(r"\d+,\d{1,2}", bare):
        return None, cur                         # 1.234,50 or 12,5: day-to-day European writing: do not guess
    return num(bare), cur


def _qty(v: Any) -> str:
    """A quantity as printed: a clean number when it is one ("2" -> "2.00"), otherwise exactly what was written ("10 pcs"). Never fails."""
    n = num(v)
    if n is not None:
        return f"{n:.2f}"
    return (str(v).strip()[:40] or "1.00") if v not in (None, "") else "1.00"


def po_lines(items: List[Dict[str, Any]], strict: bool = False) -> List[Dict[str, Any]]:
    """Order or RFQ items -> PO lines (qty, item, description, unit cost, amount). Money is blank until a person fills it in. `strict` (lines a person
    typed) refuses a quantity of zero or less and a negative unit cost; lines converted from an order are kept as they are (a credit line is real)."""
    out = []
    for i in items or []:
        if not isinstance(i, dict):
            continue
        desc = str(i.get("description") or i.get("raw_item_text") or i.get("item_id") or "")[:500]      # one huge description must not make the PDF impossible
        if i.get("drawing_ref"):
            desc = f"{desc}\nPER DWG {str(i['drawing_ref'])[:60]}"
        qty = i.get("quantity") if i.get("quantity") not in (None, "") else 1
        unit = i.get("unit_cost")
        amount = i.get("amount")
        if strict and amount not in (None, "") and money(amount)[0] is None:
            raise ValueError("An amount must be a plain number like 1,200.50.")
        if strict and unit not in (None, "") and money(unit)[0] is None:
            raise ValueError("A unit cost must be a plain number like 25.00.")
        if amount not in (None, ""):
            amount = money(amount)[0]         # "$60,000" and "USD 500" are read; "1e5", "nan", "60_000" and fullwidth digits are not numbers here, so they are dropped, never printed
        else:
            amount = None
        if strict and num(qty) is not None and num(qty) <= 0:
            raise ValueError("A quantity must be more than zero.")
        if strict and unit not in (None, "") and num(unit) is not None and num(unit) < 0:
            raise ValueError("A unit cost cannot be negative.")
        if amount is None and unit not in (None, ""):
            u, q = num(unit), num(qty)
            if u is not None and q is not None:
                from decimal import ROUND_HALF_UP, Decimal

                amount = float((Decimal(str(u)) * Decimal(str(q))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))      # exact money: 3 x 0.10 is 0.30
            else:
                amount = None
        rev = " ".join("".join(ch for ch in re.sub(r"[\x00-\x1f\x7f]+", " ", str(i.get("revision") or i.get("drawing_rev") or "")) if unicodedata.category(ch) != "Cf").split())[:20]      # no control, invisible or text-direction characters      # the drawing revision to make it to, as its own field
        extra = {}
        for key, limit in (("hts_code", 20), ("origin", 40), ("uom", 12)):          # optional overseas details: kept only when filled, blank stays blank
            v = " ".join("".join(ch for ch in re.sub(r"[\x00-\x1f\x7f]+", " ", str(i.get(key) or "")) if unicodedata.category(ch) != "Cf").split())[:limit]
            if key == "hts_code" and v and not re.fullmatch(r"\d{4}(?:\.\d{2}(?:\.\d{2,4})?)?|\d{6,10}", v, re.ASCII):
                v = ""                              # a tariff number only: "banana", "12" and "N/A" are not kept and printed for customs
            if v:
                extra[key] = v
        kg = num(i.get("net_kg"))
        if kg is not None and 0 < kg < 1_000_000:
            extra["net_kg"] = kg
        if i.get("cert_required") is True or i.get("cert_required") == 1 or str(i.get("cert_required") or "").strip().casefold() in ("true", "yes", "y", "1", "required", "x"):
            extra["cert_required"] = True
        out.append({"qty": qty, "quantity": qty, "item": str(i.get("item") or i.get("item_id") or "")[:120], "description": desc, "unit_cost": unit, "amount": amount,
                    **({"revision": rev} if rev else {}), **extra})
    return out


def _overseas_text(ln: Dict[str, Any]) -> str:
    """The optional overseas details of a line, printed only when they were filled in (HTS code, origin, unit, net weight)."""
    bits = [f"HTS {_x(ln['hts_code'])}" if ln.get("hts_code") else "", f"ORIGIN {_x(ln['origin'])}" if ln.get("origin") else "",
            f"UNIT {_x(ln['uom'])}" if ln.get("uom") else "", f"NET {_x(ln['net_kg'])} KG" if ln.get("net_kg") else ""]
    bits = [b for b in bits if b]
    return "<br/>" + " &bull; ".join(bits) if bits else ""


_pdf_lock = threading.Lock()      # the Chinese font name is shared while a PDF is built: one at a time


def vendor_po_pdf(po: Dict[str, Any]) -> bytes:
    with _pdf_lock:
        return _vendor_po_pdf(po)


def _vendor_po_pdf(po: Dict[str, Any]) -> bytes:
    """po: po_number, date, vendor_name, vendor_address, ship_to, good_thru, ship_via, account_no, terms, lines, notes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.5 * inch, rightMargin=0.5 * inch, topMargin=0.5 * inch, bottomMargin=0.5 * inch,
                            title=f"Purchase Order {po.get('po_number', '')}")
    base = getSampleStyleSheet()["Normal"]
    font = _base_font(po.get("vendor_name"), po.get("vendor_address"), po.get("ship_to"), po.get("terms"), po.get("ship_via"),
                      *[f"{ln.get('item')} {ln.get('description')}" for ln in (po.get("lines") or [])], *(po.get("notes") or []))
    small = ParagraphStyle("s", parent=base, fontName=font, fontSize=8, leading=10)
    right = ParagraphStyle("r", parent=small, alignment=2)
    title = ParagraphStyle("t", parent=base, fontName="Helvetica-Bold", fontSize=18, leading=22, alignment=2)
    head = Table([[Paragraph(LETTERHEAD, small),
                   Paragraph(f"<font size='9'>Purchase Order No.: <b>{_x(po.get('po_number'))}</b><br/>Date Issued: "
                             f"{_x(po.get('date') or date.today().strftime('%m/%d/%y'))}</font>", right)],
                  ["", Paragraph("PURCHASE ORDER", title)]], colWidths=[4.2 * inch, 3.3 * inch])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    ship_to = "<br/>".join(_x(part) for part in str(po["ship_to"]).splitlines()) if po.get("ship_to") else "ALLIED WELDING SYSTEMS<br/>TO ARRANGE SHIPPING"
    parties = Table([[Paragraph("<b>To:</b>", small), Paragraph("<b>Ship To:</b>", small)],
                     [Paragraph(f"<b>{_x(po.get('vendor_name'))}</b><br/>{_x(po.get('vendor_address')).replace(chr(10), '<br/>')}", small),
                      Paragraph(ship_to, small)]], colWidths=[3.75 * inch, 3.75 * inch])
    terms = Table([["Good Thru", "Ship Via", "Account No.", "Terms"],
                   [Paragraph(_x(po.get("good_thru")), small), Paragraph(_x(po.get("ship_via")), small), Paragraph(_x(po.get("account_no")), small),
                    Paragraph(_x(po.get("terms")), small)]],
                  colWidths=[1.6 * inch, 1.9 * inch, 1.9 * inch, 2.1 * inch])
    terms.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0"))]))
    rows = [["Quantity", "Item", "Description", "Unit Cost", "Amount"]]
    total = 0.0
    for ln in po.get("lines") or []:
        if ln.get("amount") not in (None, ""):
            try:
                total += float(ln["amount"])
            except (TypeError, ValueError):
                pass
        rows.append([_qty(ln.get("qty") or ln.get("quantity") or 1), Paragraph(_x(str(ln.get("item") or "")[:120]), small),
                     Paragraph(_x(str(ln.get("description") or "")[:500]).replace("\n", "<br/>") + (f"<br/><b>DRAWING REV {_x(ln.get('revision'))}</b>" if ln.get("revision") else "")
                               + _overseas_text(ln), small),
                     _money(ln.get("unit_cost")), _money(ln.get("amount"))])
    rows.append(["", "", Paragraph("<b>TOTAL</b>", small), "", _money(total) if total else ""])
    lines = Table(rows, colWidths=[0.8 * inch, 1.3 * inch, 3.6 * inch, 0.9 * inch, 0.9 * inch], repeatRows=1)
    lines.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")), ("ALIGN", (3, 1), (-1, -1), "RIGHT")]))
    notes = po.get("notes") or default_notes(po.get("vendor_name") or "")
    els = [head, Spacer(1, 8), parties, Spacer(1, 8), terms, Spacer(1, 8)]
    if po.get("incoterm"):                                  # printed only when filled in
        els += [Paragraph(f"<b>Incoterm:</b> {_x(po['incoterm'])}", small), Spacer(1, 8)]
    if po.get("currency"):                                  # printed only when stated: a blank is never filled with dollars
        els += [Paragraph(f"<b>All prices and amounts are in {_x(po['currency'])}.</b>", small), Spacer(1, 8)]
    els += [lines, Spacer(1, 12), Paragraph("<b>IMPORTANT NOTES:</b>", small)]
    els += [Paragraph(f"{n}) {_x(t)}", small) for n, t in enumerate(notes, 1)]
    els += [Spacer(1, 24), Paragraph("Authorized Signature: ______________________________", small)]
    doc.build(els)
    return buf.getvalue()


def sales_order_sheet_pdf(info: Dict[str, Any]) -> bytes:
    with _pdf_lock:
        return _sales_order_sheet_pdf(info)


def _sales_order_sheet_pdf(info: Dict[str, Any]) -> bytes:
    """The internal cover sheet. info: customer_name, customer_reference, date, ship_to, quantity, description, sales_order_number,
    so_total, vendors[{po_number, po_date, vendor, drawing_approval_date, po_total, ship_date, arrival_date}] (up to four shown),
    carrier_notified, bill_of_lading, freight_to_bill."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                            title="Sales Order Information")
    base = getSampleStyleSheet()["Normal"]
    s = ParagraphStyle("s", parent=base, fontName=_base_font(str(info.get("customer_name")), str(info.get("description")), str(info.get("ship_to")),
                                                                *[str(v.get("vendor")) for v in (info.get("vendors") or [])]), fontSize=9, leading=12)
    h = ParagraphStyle("h", parent=base, fontName="Helvetica-Bold", fontSize=14, leading=18)

    def box(rows):
        t = Table(rows, colWidths=[3.65 * inch, 3.65 * inch])
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    def f(label, value):
        text = str(value) if value not in (None, "") else ""
        if len(text) > 700:                        # a box taller than the page cannot be laid out; the full text stays in the app
            text = text[:697] + "..."
        return Paragraph(f"<b>{label}:</b> {_x(text)}", s)

    els = [Paragraph("SALES ORDER INFORMATION  (internal: not for customers or vendors)", h), Spacer(1, 6),
           box([[f("CUSTOMER NAME", info.get("customer_name")), f("CUSTOMER REFERENCE (their PO)", info.get("customer_reference"))],
                [f("SHIP TO ADDRESS", info.get("ship_to")), f("DATE", info.get("date"))],
                [f("QUANTITY", info.get("quantity")), f("DESCRIPTION", info.get("description"))]]), Spacer(1, 6)]
    vendors = list(info.get("vendors") or [])[:4]
    for n in range(4):
        v = vendors[n] if n < len(vendors) else {}
        els += [box([[f(f"VENDOR {n + 1} ALLIED PO#", v.get("po_number")), f("VENDOR", v.get("vendor"))],
                     [f("PO DATE", v.get("po_date")), f("DRAWING APPROVAL DATE", v.get("drawing_approval_date"))],
                     [f("ESTIMATE SHIP DATE", v.get("ship_date")), f("ESTIMATE ARRIVAL DATE", v.get("arrival_date"))],
                     [f("PO TOTAL AMOUNT", _money(v.get("po_total"))),
                      f("SALES ORDER #" if n == 0 else "SALES ORDER #", info.get("sales_order_number") if n == 0 else "")]]), Spacer(1, 4)]
    if len(info.get("vendors") or []) > 4:
        els += [Paragraph(f"(and {len(info['vendors']) - 4} more vendor purchase orders on this job)", s), Spacer(1, 4)]
    els += [box([[f("SO TOTAL AMOUNT", _money(info.get("so_total"))), f("CARRIER NOTIFIED", "yes" if info.get("carrier_notified") else "")],
                 [f("BILL OF LADING RECEIVED / DATE", info.get("bill_of_lading")), f("CUSTOMER TOTAL FREIGHT AMOUNT TO BILL", _money(info.get("freight_to_bill")))]])]
    doc.build(els)
    return buf.getvalue()
