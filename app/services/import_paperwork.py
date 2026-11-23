"""Customs paperwork for goods coming in from an overseas vendor (China) or a Canadian one: a commercial invoice and a packing list made from the
saved purchase order, and a plain list of what is still missing. It reads only; nothing is sent and nothing is saved.

Blanks stay blank and are listed (never filled in or guessed): a missing price or tariff number is a customs problem for a person to fix, so the
documents are marked INCOMPLETE rather than refused. The packing list is the one stuck on the outside of a blind shipment, so it carries no vendor
name and no customer, only Allied's PO number and what is in the box."""
import io
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services import order_documents as docs
from app.services.coordination import jobs, purchase_orders

DESTINATIONS = {"US": "United States", "CA": "Canada"}
_COUNTRY_ALIASES = {
    "China": {"china", "cn", "prc", "p.r.c.", "p.r. china", "peoples republic of china", "people's republic of china", "made in china", "中国", "中华人民共和国"},
    "Canada": {"canada", "ca", "can", "made in canada"},
    "United States": {"us", "usa", "u.s.", "u.s.a.", "united states", "united states of america", "made in usa"},
}
_COUNTRY_WORDS = {"China": ("china", "中国", "chn"), "Canada": ("canada", "canadian"), "United States": ("united states", "usa")}
_CURRENCIES = {"USD", "CAD", "CNY", "EUR", "GBP", "JPY", "MXN", "AUD"}
_FIELDS = (("hts_code", "tariff number"), ("origin", "country of origin"), ("uom", "unit of measure"), ("net_kg", "net weight"))


def country(value: Any) -> str:
    """The country as one standard name ("PRC", "CN", "Made in China" -> "China"); anything else is kept as written, tidied. Blank stays blank."""
    text = unicodedata.normalize("NFKC", " ".join(str(value or "").replace("\n", " ").split()))          # fullwidth letters become plain ones
    low = text.casefold().strip(".")
    for name, aliases in _COUNTRY_ALIASES.items():
        if low in {a.strip(".") for a in aliases}:
            return name
    if re.search(r"\b(prc|chn)\b", low.replace(".", "")):
        return "China"
    for name, words in _COUNTRY_WORDS.items():              # "Shenzhen, China", "Product of China", "中国制造": the country named inside a longer phrase
        if any(w in low for w in words):
            return name
    return text[:40]


_CA_PLACES = ("CANADA", "VANCOUVER", "PRINCE RUPERT", "TORONTO", "MONTREAL", "HALIFAX", "EDMONTON", "CALGARY", "WINNIPEG", "OTTAWA", "SASKATOON", "HAMILTON, ON")
_US_PLACES = ("USA", "U.S.A", "UNITED STATES", "HOUSTON", "LONG BEACH", "LOS ANGELES", "SAVANNAH", "SEATTLE", "TACOMA", "NEW YORK", "NEWARK", "CHARLESTON", "NORFOLK",
              "OAKLAND", "MOBILE", "NEW ORLEANS", "MIAMI", "BALTIMORE", "JACKSONVILLE", "DALLAS", "CHICAGO", "MEMPHIS")
_CA_PROVINCE = re.compile(r"\b(?:AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\b[ ,]+[A-Z]\d[A-Z] ?\d[A-Z]\d\b")      # "Edmonton, AB T6S 1A1"
_US_STATE = re.compile(r"\b[A-Z]{2}\b[ ,]+\d{5}(?:-\d{4})?\b")                                                  # "Starr, SC 29684"


def _country_of(text: Any) -> Optional[str]:
    """US or CA when the place text clearly says which; None when it does not."""
    t = " ".join(str(text or "").upper().replace("\n", " ").split())
    if not t:
        return None
    if _CA_PROVINCE.search(t) or any(w in t for w in _CA_PLACES):
        return "CA"
    if _US_STATE.search(t) or any(w in t for w in _US_PLACES):
        return "US"
    return None


def infer_destination(po: Dict[str, Any]) -> Tuple[str, str]:
    """Where the goods are going, and how we know: the route of the shipment tied to this purchase order, else the ship-to of the job's sales order,
    else the United States (Allied's own yard). Returns (US or CA, a plain reason)."""
    from app.core.database import SessionLocal
    from app.models.db_models import JobDB, OrderDB, ShipmentDB

    with SessionLocal() as db:
        if po.get("po_number"):
            for sh in db.query(ShipmentDB).filter(ShipmentDB.reference == f"PO {po['po_number']}").order_by(ShipmentDB.id.desc()).all():
                found = _country_of(sh.destination)
                if found:
                    return found, f"the shipment's route ({sh.destination})"
        job = db.get(JobDB, po["job_id"]) if po.get("job_id") else None
        order = db.query(OrderDB).filter(OrderDB.sales_order_number == job.sales_order_number).first() if job and job.sales_order_number else None
        found = _country_of(order.shipping_address) if order else None
        if found:
            return found, "the sales order's ship-to address"
    return "US", "no route or ship-to address says otherwise"


def build(po_id: int, destination: str = "", currency: str = "") -> Dict[str, Any]:
    """The import paperwork facts for one purchase order: lines with their customs details, totals, what is missing (never blocks) and notes.
    Without a destination it follows the shipment's route (Houston is the US), then the order's ship-to, then the US."""
    given = str(destination or "").strip().upper()
    if given and given not in DESTINATIONS:
        raise jobs.JobError("The destination must be US or CA.")
    cur = str(currency or "").strip().upper()
    if cur and cur not in _CURRENCIES:
        raise jobs.JobError("The currency must be one of: " + ", ".join(sorted(_CURRENCIES)) + ".")
    po = purchase_orders.get(po_id)
    if not cur and po.get("currency") in _CURRENCIES:                  # the invoice follows the purchase order's own currency unless one is chosen here
        cur = po["currency"]
    dest, dest_source = (given, "chosen by you") if given else infer_destination(po)
    if po.get("status") == "CANCELLED":
        raise jobs.JobError("This purchase order was cancelled; there is nothing to import.")
    lines: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    origins = set()
    blind_terms = [t for t in {str(po.get("vendor") or "").strip()} if len(t) >= 3]
    try:
        customer = str((jobs.get(po["job_id"]) or {}).get("customer") or "").strip()
        blind_terms += [customer] if len(customer) >= 3 else []
    except Exception:  # noqa: BLE001 - no job name to hide is not an error
        pass
    for i, ln in enumerate(po.get("lines") or [], 1):
        ln = ln if isinstance(ln, dict) else {}
        origin = country(ln.get("origin"))
        if origin:
            origins.add(origin)
        row = {"line": i, "item": ln.get("item") or "", "description": str(ln.get("description") or ""), "quantity": ln.get("quantity", ln.get("qty")),
               "unit_cost": ln.get("unit_cost"), "amount": ln.get("amount"), "hts_code": ln.get("hts_code") or "", "origin": origin,
               "uom": ln.get("uom") or "", "net_kg": ln.get("net_kg"), "cert_required": bool(ln.get("cert_required"))}
        lines.append(row)
        for key, word in _FIELDS:
            if not row[key]:
                missing.append({"line": i, "field": key, "text": f"Line {i}: no {word}."})
        if docs.money(row["amount"])[0] is None:
            missing.append({"line": i, "field": "amount", "text": f"Line {i}: no price, so the invoice cannot state a value."})
        if row["net_kg"] and docs.num(row["quantity"]) is None:
            missing.append({"line": i, "field": "quantity", "text": f"Line {i}: the quantity is not a number, so the total weight cannot be worked out."})
    if not lines:
        missing.append({"line": 0, "field": "lines", "text": "The purchase order has no lines."})
    if not cur:
        missing.append({"line": 0, "field": "currency", "text": "The currency is not stated. Say which currency the prices are in (the purchase order does not record it)."})
    if not po.get("incoterm"):
        missing.append({"line": 0, "field": "incoterm", "text": "No Incoterm on the purchase order."})
    notes: List[str] = []
    if "China" in origins:
        notes.append("Goods made in China can carry extra duties as well as the normal rate. Confirm the rate with your customs broker before the goods ship.")
    if "Canada" in origins and dest == "US":
        notes.append("Canadian-made goods enter the US duty-free only with a USMCA certification. Mark a line's certificate as required if you hold one.")
    if "China" in origins and dest == "CA":
        notes.append("Goods made in China may carry a Canadian surtax. Confirm with your customs broker.")
    if dest == "CA":
        notes.append("Canada needs the invoice values in a stated currency and the vendor's name and address on the invoice.")
    kg = sum(float(r["net_kg"]) * docs.num(r["quantity"]) for r in lines if docs.num(r["net_kg"]) is not None and (docs.num(r["quantity"]) or 0) > 0)
    for r in lines:
        if any(t.casefold() in f"{r['item']} {r['description']}".casefold() for t in blind_terms):
            notes.append(f"Line {r['line']} names the vendor or the customer in its item or description. The packing list hides those names; check the wording on the invoice too.")
    return {"po_id": po.get("id"), "po_number": po.get("po_number"), "vendor": po.get("vendor"), "vendor_address": purchase_orders._vendor_address(po.get("vendor") or ""),
            "destination": dest, "destination_source": dest_source, "destination_country": DESTINATIONS[dest], "currency": cur, "incoterm": " ".join(x for x in (po.get("incoterm"), po.get("incoterm_place")) if x),
            "lines": lines, "total": purchase_orders.lines_total(po.get("lines")), "total_net_kg": round(kg, 3), "origins": sorted(origins),
            "missing": missing, "notes": notes, "complete": not missing, "draft": po.get("status") == "DRAFT", "date": _us_date(po.get("approved_at")), "blind_terms": blind_terms}


def _us_date(iso: Any) -> str:
    """The purchase order's approval date as MM/DD/YY, blank when it has none (so a download never gets a new date each time)."""
    try:
        return datetime.fromisoformat(str(iso)).strftime("%m/%d/%y") if iso else ""
    except ValueError:
        return ""


def _hide(text: Any, terms: List[str]) -> Any:
    """The text with the vendor's and customer's names taken out (a blind shipment's packing list never carries them)."""
    out = str(text or "")
    for t in terms:
        pattern = r"[\s\-_.,]*".join(re.escape(w) for w in re.split(r"[\s\-_.,]+", t.strip()) if w)          # "ZIBO FOUNDRY" also catches ZIBO-FOUNDRY-77
        out = re.sub(pattern, "[name removed]", out, flags=re.I) if pattern else out
    return out


def _styles(font: str):
    base = getSampleStyleSheet()["Normal"]
    small = ParagraphStyle("s", parent=base, fontName=font, fontSize=8, leading=10)
    return small, ParagraphStyle("r", parent=small, alignment=2), ParagraphStyle("t", parent=base, fontName="Helvetica-Bold", fontSize=18, leading=22, alignment=2)


def _kg(v: Any) -> str:
    n = docs.num(v)
    return f"{n:g}" if n is not None else ""


def commercial_invoice_pdf(info: Dict[str, Any]) -> bytes:
    with docs._pdf_lock:
        font = docs._base_font(info.get("vendor"), info.get("vendor_address"), *[f"{r['item']} {r['description']} {r['origin']}" for r in info["lines"]])
        small, right, title = _styles(font)
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.5 * inch, rightMargin=0.5 * inch, topMargin=0.5 * inch, bottomMargin=0.5 * inch,
                                title=f"Commercial Invoice {info.get('po_number') or ''}")
        x = docs._x
        head = Table([[Paragraph("<b>COMMERCIAL INVOICE</b>", title), Paragraph(f"Invoice for PO <b>{x(info.get('po_number') or '(not numbered yet)')}</b><br/>Date: {x(info.get('date') or '(not approved yet)')}<br/>"
                                                                                    f"Currency: <b>{x(info['currency'] or '(not stated)')}</b>", right)]], colWidths=[4.0 * inch, 3.5 * inch])
        parties = Table([[Paragraph("<b>Exporter (vendor):</b>", small), Paragraph("<b>Importer / consignee:</b>", small)],
                         [Paragraph(f"<b>{x(info.get('vendor'))}</b><br/>{x(info.get('vendor_address')).replace(chr(10), '<br/>')}", small), Paragraph(docs.LETTERHEAD, small)]],
                        colWidths=[3.75 * inch, 3.75 * inch])
        terms = Table([["Incoterm", "Destination", "Country(ies) of origin"],
                       [Paragraph(x(info.get("incoterm")), small), Paragraph(x(info["destination_country"]), small), Paragraph(x(", ".join(info["origins"])), small)]],
                      colWidths=[2.4 * inch, 2.4 * inch, 2.7 * inch])
        terms.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0"))]))
        rows = [["Qty", "Item / description", "HTS no.", "Origin", "Unit price", "Amount"]]
        for r in info["lines"]:
            rows.append([docs._qty(r["quantity"]), Paragraph(f"{x(str(r['item'])[:120])}<br/>{x(str(r['description'])[:500]).replace(chr(10), '<br/>')}", small), Paragraph(x(r["hts_code"]), small),
                         Paragraph(x(r["origin"]), small), docs._money(r["unit_cost"]), docs._money(r["amount"])])
        rows.append(["", "", "", "", Paragraph("<b>TOTAL</b>", small), docs._money(info["total"]) if info["total"] else ""])
        lines = Table(rows, colWidths=[0.6 * inch, 3.0 * inch, 1.0 * inch, 0.9 * inch, 0.9 * inch, 1.1 * inch], repeatRows=1)
        lines.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")), ("ALIGN", (4, 1), (-1, -1), "RIGHT")]))
        els: List[Any] = [head, Spacer(1, 8)]
        if info.get("draft"):
            els += [Paragraph("<b>DRAFT: the purchase order is not approved yet.</b>", small), Spacer(1, 4)]
        if not info["complete"]:
            els += [Paragraph("<b>INCOMPLETE:</b> " + x("; ".join(m["text"] for m in info["missing"][:12])), small), Spacer(1, 6)]
        els += [parties, Spacer(1, 8), terms, Spacer(1, 8), lines, Spacer(1, 12),
                Paragraph("I declare that the information on this invoice is true and correct and that the goods are of the origin stated.", small), Spacer(1, 24),
                Paragraph("Authorized Signature: ______________________________", small)]
        doc.build(els)
        return buf.getvalue()


def packing_list_pdf(info: Dict[str, Any]) -> bytes:
    """No vendor name, no customer, no prices and no Allied sales order: it is attached outside a blind shipment."""
    with docs._pdf_lock:
        font = docs._base_font(*[f"{r['item']} {r['description']} {r['origin']}" for r in info["lines"]])
        small, right, title = _styles(font)
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.5 * inch, rightMargin=0.5 * inch, topMargin=0.5 * inch, bottomMargin=0.5 * inch,
                                title=f"Packing List {info.get('po_number') or ''}")
        x = docs._x
        head = Table([[Paragraph("<b>PACKING LIST</b>", title), Paragraph(f"Ref: PO <b>{x(info.get('po_number') or '(not numbered yet)')}</b><br/>Date: {x(info.get('date') or '(not approved yet)')}", right)]],
                     colWidths=[4.0 * inch, 3.5 * inch])
        rows = [["Qty", "Item / description", "Unit", "Net kg each", "Made in"]]
        for r in info["lines"]:
            terms = info.get("blind_terms") or []
            rows.append([docs._qty(r["quantity"]), Paragraph(f"{x(_hide(str(r['item'])[:120], terms))}<br/>{x(_hide(str(r['description'])[:500], terms)).replace(chr(10), '<br/>')}", small),
                         Paragraph(x(r["uom"]), small), _kg(r["net_kg"]), Paragraph(x(r["origin"]), small)])
        rows.append(["", Paragraph("<b>TOTAL NET WEIGHT (kg)</b>", small), "", f"{info['total_net_kg']:g}" if info["total_net_kg"] else "", ""])
        lines = Table(rows, colWidths=[0.7 * inch, 3.6 * inch, 0.8 * inch, 1.2 * inch, 1.2 * inch], repeatRows=1)
        lines.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")), ("ALIGN", (3, 1), (3, -1), "RIGHT")]))
        els: List[Any] = [head, Spacer(1, 8)]
        if info.get("draft"):
            els += [Paragraph("<b>DRAFT: the purchase order is not approved yet.</b>", small), Spacer(1, 4)]
        if not info["complete"]:
            els += [Paragraph("<b>INCOMPLETE:</b> " + x("; ".join(m["text"] for m in info["missing"][:12])), small), Spacer(1, 4)]
        els += [Paragraph("<b>Ship to:</b>", small), Paragraph(docs.LETTERHEAD, small), Spacer(1, 10), lines]
        doc.build(els)
        return buf.getvalue()
