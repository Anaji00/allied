"""Landed cost: the vendor's quote + tariffs (duty) + freight, then a multi-tier markup sheet (xlsx) for the people who pick the price. All money is Decimal.

Honest about what it does not know: a missing tariff rate is "duty unknown" and never counts as zero; a missing freight figure is "freight unknown"; the landed total and every tier price then say what is left
out (and are None when you asked for a firm figure). The tariff table is editable with an as-of date per rate and a warning when it has not been looked at for TARIFF_STALE_DAYS.
Figures read from a scan (OCR) arrive flagged "needs a person to confirm against the image". Local only; nothing is sent anywhere."""
import io
import os
import re
import tempfile
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.core.money import parse_amount, safe_text
from app.models.db_models import LandedCostSheetDB, TariffRateDB

CENT = Decimal("0.01")
NEEDS_CONFIRM = "needs a person to confirm against the image"


class CostError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _dec(v: Any, what: str, allow_none: bool = False) -> Optional[Decimal]:
    try:
        return parse_amount(v, what, none_ok=allow_none)
    except ValueError as exc:
        raise CostError(422, str(exc))


def _currency(value: Any) -> str:
    """A real currency code from the configured list; anything else is refused (lines in one currency and freight in another never add up silently)."""
    allowed = {c.strip().upper() for c in str(getattr(settings, "LANDED_CURRENCIES", "USD,CAD,EUR,GBP,CNY,MXN")).split(",") if c.strip()}
    cur = str(value or "USD").strip().upper()
    if cur not in allowed:
        raise CostError(422, f"The currency must be one of: {', '.join(sorted(allowed))}.")
    return cur


def _tier(value: Any) -> Decimal:
    try:
        d = parse_amount(value, "markup")
    except ValueError as exc:
        raise CostError(422, str(exc))
    if d > 1000:
        raise CostError(422, "A markup must be between 0 and 1000 percent.")
    return d


def _age_days(updated_at: Any) -> Optional[int]:
    try:
        t = datetime.fromisoformat(str(updated_at))
    except (TypeError, ValueError):
        return None
    return (datetime.now(timezone.utc) - (t if t.tzinfo else t.replace(tzinfo=timezone.utc))).days


def _q(d: Decimal) -> Decimal:
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def _s(d: Optional[Decimal]) -> Optional[str]:
    return None if d is None else f"{d:.2f}"


# ------------------------------------------------------------------ tariff table

def _rate(r: TariffRateDB) -> Dict[str, Any]:
    return {"id": r.id, "category": r.category, "hs_code": r.hs_code, "country": r.country, "rate_percent": f"{r.rate_percent:.2f}", "effective_from": r.effective_from, "note": r.note,
            "updated_by": r.updated_by, "updated_at": r.updated_at.isoformat() if r.updated_at else None}


def set_rate(category: str, rate_percent: Any, user: Dict[str, Any], hs_code: str = "", country: str = "", effective_from: str = "", note: str = "") -> Dict[str, Any]:
    cat = " ".join(str(category or "").split())[:120]
    if not cat:
        raise CostError(422, "Give the product category (or HS code) this rate is for.")
    pct = _dec(rate_percent, "rate")
    if pct > Decimal("500"):
        raise CostError(422, "A duty rate above 500 percent is not believable; check it.")
    eff = effective_from or date.today().isoformat()
    try:
        date.fromisoformat(eff)
    except ValueError:
        raise CostError(422, "The effective date must be YYYY-MM-DD.")
    with SessionLocal() as db:
        r = TariffRateDB(category=cat, hs_code=(hs_code or "").strip()[:16] or None, country=(country or "").strip().upper()[:2] or None, rate_percent=pct.quantize(CENT), effective_from=eff,
                         note=(note or "")[:300] or None, updated_by=user.get("email"), updated_at=datetime.now(timezone.utc))
        db.add(r)
        db.commit()
        db.refresh(r)
        out = _rate(r)
    audit_log(action="TARIFF_RATE_SET", resource_type="TARIFF_RATE", resource_id=str(out["id"]), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=f"{cat} {out['rate_percent']}% from {eff}")
    return out


def list_rates() -> Dict[str, Any]:
    """Every rate, newest first, and whether the table is stale (no rate added or confirmed within TARIFF_STALE_DAYS)."""
    with SessionLocal() as db:
        rows = db.query(TariffRateDB).order_by(TariffRateDB.effective_from.desc(), TariffRateDB.id.desc()).all()
        newest = max((r.updated_at for r in rows if r.updated_at), default=None)
    stale_days = int(getattr(settings, "TARIFF_STALE_DAYS", 30))
    age = (datetime.now(timezone.utc) - (newest if newest.tzinfo else newest.replace(tzinfo=timezone.utc))).days if newest else None
    stale = age is None or age > stale_days
    return {"rates": [_rate(r) for r in rows], "stale": stale, "age_days": age,
            "warning": ("There are no tariff rates yet: every duty will read \"unknown\"." if age is None else f"The tariff table was last updated {age} days ago (limit {stale_days}). Check the rates before you rely on them.") if stale else None}


def delete_rate(rate_id: int, user: Dict[str, Any]) -> None:
    with SessionLocal() as db:
        r = db.get(TariffRateDB, rate_id)
        if r is None:
            raise CostError(404, "No such rate.")
        db.delete(r)
        db.commit()
    audit_log(action="TARIFF_RATE_DELETED", resource_type="TARIFF_RATE", resource_id=str(rate_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail="rate removed")


def rate_for(category: str = "", hs_code: str = "", country: str = "", as_of: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The rate in force on the as-of date for an HS code or a category (an HS code match wins; a rate for a country wins over a general one), or None: unknown."""
    as_of = as_of or date.today().isoformat()
    cat, hs, ctry = (category or "").strip().casefold(), (hs_code or "").strip().replace(".", ""), (country or "").strip().upper()
    with SessionLocal() as db:
        rows = [r for r in db.query(TariffRateDB).all() if (r.effective_from or "0000-00-00") <= as_of]

    def score(r):
        s = 0
        if hs and r.hs_code and r.hs_code.replace(".", "") == hs:
            s += 4
        elif cat and r.category.casefold() == cat:
            s += 2
        else:
            return -1
        if r.country:
            if ctry and r.country == ctry:
                s += 1
            else:
                return -1
        return s

    ranked = sorted(((score(r), r.effective_from or "", r.id, r) for r in rows if score(r) >= 0), key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return _rate(ranked[0][3]) if ranked else None


# ------------------------------------------------------------------ the sum

def compute(lines: List[Dict[str, Any]], freight: Any = None, currency: str = "USD", as_of: Optional[str] = None, tiers: Optional[List[Any]] = None) -> Dict[str, Any]:
    """{lines, goods, tariff_total, tariff_unknown, freight, freight_unknown, landed_total, landed_note, tiers[{name, markup_percent, price, margin}], needs_confirm}.
    Duty is worked on the goods value. An unknown duty or freight is flagged, never zero."""
    if not lines:
        raise CostError(422, "Add at least one line.")
    cur_code = _currency(currency)
    stale_days = int(getattr(settings, "TARIFF_STALE_DAYS", 30))
    stale_used = False
    out_lines, goods, duty_known, unknown, confirm = [], Decimal("0"), Decimal("0"), False, False
    for i, ln in enumerate(lines, start=1):
        qty, cost = _dec(ln.get("quantity"), f"quantity on line {i}"), _dec(ln.get("unit_cost"), f"unit cost on line {i}")
        if qty <= 0:
            raise CostError(422, f"The quantity on line {i} must be above zero.")
        value = _q(qty * cost)
        goods += value
        r = rate_for(ln.get("category", ""), ln.get("hs_code", ""), ln.get("country", ""), as_of) if (ln.get("category") or ln.get("hs_code")) else None
        duty = _q(value * Decimal(r["rate_percent"]) / Decimal(100)) if r else None
        age = _age_days(r["updated_at"]) if r else None
        old_rate = bool(r and (age is None or age > stale_days))
        stale_used = stale_used or old_rate
        if duty is None:
            unknown = True
        else:
            duty_known += duty
        flagged = bool(ln.get("from_ocr"))
        confirm = confirm or flagged
        out_lines.append({"line": i, "description": ln.get("description", ""), "quantity": f"{qty:f}", "unit_cost": _s(cost), "value": _s(value), "category": ln.get("category", ""), "hs_code": ln.get("hs_code", ""),
                          "country": ln.get("country", ""), "duty_rate": r["rate_percent"] if r else None, "duty": _s(duty), "duty_note": ("duty unknown: no rate on file for this product" if not r else (f"this duty rate was last checked {age} days ago" if old_rate else None)), "rate_stale": old_rate,
                          "check": NEEDS_CONFIRM if flagged else None})
    fr = _dec(freight, "freight", allow_none=True)
    freight_unknown = fr is None
    landed = _q(goods + duty_known + (fr or Decimal("0")))
    left_out = [x for x, flag in (("duty", unknown), ("freight", freight_unknown)) if flag]
    note = None if not left_out else "Landed cost EXCLUDES " + " and ".join(left_out) + " (unknown, not zero)."
    if stale_used:
        note = ((note + " ") if note else "") + f"A duty rate used here was last checked more than {stale_days} days ago: check it before relying on this."
    percents = [_tier(t) for t in (tiers if tiers else str(getattr(settings, "MARKUP_TIERS", "20,30,40")).split(","))]
    tier_rows = []
    for k, m in enumerate(percents, start=1):
        price = _q(landed * (Decimal(100) + m) / Decimal(100))
        tier_rows.append({"name": f"Tier {k}", "markup_percent": f"{m:f}", "price": _s(price), "margin": _s(price - landed), "firm": not left_out and not stale_used,
                          "note": note})
    return {"currency": cur_code, "as_of": as_of or date.today().isoformat(), "lines": out_lines, "goods": _s(goods), "tariff_total": _s(duty_known), "tariff_unknown": unknown, "freight": _s(fr),
            "freight_unknown": freight_unknown, "landed_total": _s(landed), "landed_note": note, "tiers": tier_rows, "needs_confirm": NEEDS_CONFIRM if confirm else None,
            "tariff_table": {"stale": bool(list_rates()["stale"] or stale_used), "rate_used_is_old": stale_used}}


def create_sheet(data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    calc = compute(data.get("lines") or [], data.get("freight"), data.get("currency") or "USD", data.get("as_of"), data.get("tiers"))
    with SessionLocal() as db:
        s = LandedCostSheetDB(job_id=data.get("job_id"), rfq_id=data.get("rfq_id"), vendor_quote_ref=(data.get("vendor_quote_ref") or "")[:80] or None, currency=calc["currency"], lines=calc["lines"],
                              freight=_dec(data.get("freight"), "freight", True), tariff_total=Decimal(calc["tariff_total"]), tariff_unknown=calc["tariff_unknown"], landed_total=Decimal(calc["landed_total"]),
                              tiers=calc["tiers"], created_by=user.get("email"))
        db.add(s)
        db.commit()
        db.refresh(s)
        sid = s.id
    audit_log(action="LANDED_COST_MADE", resource_type="LANDED_COST", resource_id=str(sid), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=f"landed total {calc['landed_total']}")
    return get_sheet(sid)


def get_sheet(sheet_id: int) -> Dict[str, Any]:
    with SessionLocal() as db:
        s = db.get(LandedCostSheetDB, sheet_id)
        if s is None:
            raise CostError(404, "No such landed cost sheet.")
        left_out = [x for x, flag in (("duty", s.tariff_unknown), ("freight", s.freight is None)) if flag]
        return {"id": s.id, "job_id": s.job_id, "rfq_id": s.rfq_id, "vendor_quote_ref": s.vendor_quote_ref, "currency": s.currency, "lines": s.lines or [], "freight": _s(s.freight), "freight_unknown": s.freight is None,
                "tariff_total": _s(s.tariff_total), "tariff_unknown": bool(s.tariff_unknown), "landed_total": _s(s.landed_total), "landed_note": ("Landed cost EXCLUDES " + " and ".join(left_out) + " (unknown, not zero).") if left_out else None,
                "tiers": s.tiers or [], "picked_tier": s.picked_tier, "created_by": s.created_by, "created_at": s.created_at.isoformat() if s.created_at else None}


def unconfirmed_lines(sheet: Dict[str, Any]) -> List[int]:
    return [ln["line"] for ln in sheet["lines"] if ln.get("check") and not ln.get("confirmed_by")]


def confirm_sheet(sheet_id: int, user: Dict[str, Any], note: str = "") -> Dict[str, Any]:
    """A person has checked the figures that were read from a scan against the image: they are marked confirmed (who and when), and only then can a tier be picked."""
    sheet = get_sheet(sheet_id)
    lines = []
    for ln in sheet["lines"]:
        if ln.get("check") and not ln.get("confirmed_by"):
            ln = {**ln, "confirmed_by": user.get("email"), "confirmed_at": datetime.now(timezone.utc).isoformat()}
        lines.append(ln)
    with SessionLocal() as db:
        db.get(LandedCostSheetDB, sheet_id).lines = lines
        db.commit()
    audit_log(action="LANDED_COST_CONFIRMED", resource_type="LANDED_COST", resource_id=str(sheet_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=(note or "figures checked against the image")[:200])
    return get_sheet(sheet_id)


def pick_tier(sheet_id: int, tier: str, user: Dict[str, Any]) -> Dict[str, Any]:
    sheet = get_sheet(sheet_id)
    if tier not in [t["name"] for t in sheet["tiers"]]:
        raise CostError(422, "That tier is not on this sheet.")
    if unconfirmed_lines(sheet):
        raise CostError(409, "Some figures on this sheet were read from a scan and need a person to confirm them against the image first.")
    with SessionLocal() as db:
        db.get(LandedCostSheetDB, sheet_id).picked_tier = tier
        db.commit()
    audit_log(action="MARKUP_TIER_PICKED", resource_type="LANDED_COST", resource_id=str(sheet_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=tier)
    return get_sheet(sheet_id)


def sheet_xlsx(sheet_id: int) -> bytes:
    """The markup workbook: the landed cost lines and the tiers, with unknowns shown as words and OCR figures flagged. Built in memory, so a download is never half a file."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    s = get_sheet(sheet_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Landed cost"
    ws.append(["Line", "Description", "Qty", "Unit cost", "Value", "Category / HS", "Duty %", "Duty", "Note"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for ln in s["lines"]:
        ws.append([ln["line"], safe_text(ln["description"]), float(ln["quantity"]), float(ln["unit_cost"]), float(ln["value"]), safe_text(ln.get("hs_code") or ln.get("category") or ""), ln.get("duty_rate") or "unknown",
                   float(ln["duty"]) if ln.get("duty") is not None else "unknown", safe_text(ln.get("check") or ln.get("duty_note") or "")])
    ws.append([])
    ws.append(["", "Duty total", "", "", "", "", "", float(s["tariff_total"]) + 0 if not s["tariff_unknown"] else f'{s["tariff_total"]} + unknown'])
    ws.append(["", "Freight", "", "", "", "", "", float(s["freight"]) if s["freight"] is not None else "unknown"])
    ws.append(["", "LANDED TOTAL (" + (s["currency"] or "") + ")", "", "", "", "", "", float(s["landed_total"])])
    if s["landed_note"]:
        ws.append(["", safe_text(s["landed_note"])])
    t = wb.create_sheet("Markup tiers")
    t.append(["Tier", "Markup %", "Price", "Margin", "Firm?", "Picked"])
    for c in t[1]:
        c.font = Font(bold=True)
    for row in s["tiers"]:
        t.append([row["name"], float(row["markup_percent"]), float(row["price"]), float(row["margin"]), "yes" if row.get("firm") else "NO: " + (row.get("note") or ""), "<- picked" if s["picked_tier"] == row["name"] else ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ freight RFQ from a vendor quote's weights and dimensions

_WEIGHT = re.compile(r"(?:gross\s+|net\s+)?weight[^0-9]{0,15}([0-9][0-9,]*\.?[0-9]*)\s*(kg|kgs|lb|lbs|t|tons?|tonnes?)", re.I)
_DIMS = re.compile(r"(?:dim(?:ension)?s?|size|l\s*x\s*w\s*x\s*h)[^0-9]{0,15}([0-9][0-9.,]*)\s*[x×*]\s*([0-9][0-9.,]*)\s*[x×*]\s*([0-9][0-9.,]*)\s*(mm|cm|m|in|inch|inches|ft)?", re.I)
_PIECES = re.compile(r"(?:([0-9]{1,4})\s*(?:packages?|pieces?|pcs|crates?|cartons?)\b|\b(?:packages?|pieces?|pcs|crates?|cartons?)\D{0,6}([0-9]{1,4}))", re.I)


def _cargo_number(raw: str) -> str:
    """A weight as typed: 1,250 is one thousand two hundred fifty (a comma with exactly three digits after it); 1,5 is one and a half (a decimal comma). Never both guesses at once."""
    import re as _re

    if _re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
        return raw.replace(",", "")
    if _re.fullmatch(r"\d+,\d{1,2}", raw):
        return raw.replace(",", ".")
    return raw.replace(",", "")


def parse_cargo(text: str) -> Dict[str, Any]:
    """Weights, dimensions and package counts read out of a vendor quote's text, every figure flagged for a person to confirm (they may come from a scan)."""
    t = text or ""
    weights = [{"value": _cargo_number(m.group(1)), "unit": m.group(2).lower()} for m in _WEIGHT.finditer(t)]
    dims = [{"l": m.group(1), "w": m.group(2), "h": m.group(3), "unit": (m.group(4) or "").lower() or "unknown"} for m in _DIMS.finditer(t)]
    pieces = [m.group(1) or m.group(2) for m in _PIECES.finditer(t)]
    return {"weights": weights, "dimensions": dims, "packages": pieces, "check": NEEDS_CONFIRM, "complete": bool(weights and dims)}


def freight_rfq_draft(cargo: Dict[str, Any], forwarder_email: str, origin: str, destination: str, user: Dict[str, Any], job_id: Optional[int] = None, blank_ok: bool = False) -> Dict[str, Any]:
    """A freight quote request staged as a draft email to the forwarder (a person confirms the figures and sends it). It names no customer and no price: only the cargo, the route and the dates."""
    from app.services.assistant import agent
    from app.services.assistant.tools import TOOLS

    if not re.fullmatch(r"[^@\s,;<>()\[\]\"']+@[^@\s,;<>()\[\]\"']+\.[^@\s,;<>()\[\]\"']{2,}", forwarder_email or ""):
        raise CostError(422, "Give the forwarder's email address as a plain address (name@company.com).")
    place = f"{origin or ''} {destination or ''}"
    if place.strip():
        from app.services.coordination import blind

        names = list(blind.customer_names())
        if job_id:
            try:
                from app.services.coordination import jobs as _jobs

                names.append(str(_jobs.get(int(job_id)).get("customer") or ""))
            except Exception:  # noqa: BLE001
                pass
        hits = blind.found(place, [n for n in names if n], min_len=2)
        if hits:
            raise CostError(422, "The origin or destination names the customer. A forwarder is given only a city or port, never the customer's name or address.")
    if not blank_ok and not (cargo.get("weights") and cargo.get("dimensions")):
        raise CostError(422, "The quote gave no weight and dimensions to ask about. Enter them first.")
    missing = [n for n, v in (("weight", cargo.get("weights")), ("dimensions", cargo.get("dimensions")), ("packages", cargo.get("packages")), ("origin", origin), ("destination", destination)) if not v]
    blank = "(to be filled in)"
    lines = ["Hello,", "", "Please quote the freight for this cargo.", f"From: {origin or blank}", f"To: {destination or blank}"]
    for i, w in enumerate(cargo.get("weights", [])[:6], start=1):
        lines.append(f"Weight {i}: {w['value']} {w['unit']}")
    if not cargo.get("weights"):
        lines.append(f"Weight: {blank}")
    for i, d in enumerate(cargo.get("dimensions", [])[:6], start=1):
        lines.append(f"Dimensions {i}: {d['l']} x {d['w']} x {d['h']} {d['unit']}")
    if not cargo.get("dimensions"):
        lines.append(f"Dimensions: {blank}")
    lines.append(f"Packages: {cargo['packages'][0]}" if cargo.get("packages") else f"Packages: {blank}")
    lines += ["", "Please include transit time and what is and is not included.", "", "Thank you,", "Allied Welding Systems"]
    args = {"to": forwarder_email, "subject": "Freight quote request", "body": "\n".join(lines), "_audience": "forwarder", "_job_id": job_id, "_check_figures": NEEDS_CONFIRM}
    action = agent._propose(TOOLS["send_email"], args, user)
    audit_log(action="FREIGHT_RFQ_DRAFTED", resource_type="FREIGHT_RFQ", resource_id=str(action["id"]), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail="to " + forwarder_email[:80])
    return {"drafted": True, "action_id": action["id"], "check": NEEDS_CONFIRM, "missing": missing, "cargo": cargo}


def freight_rfq_from_po(po_id: Optional[int], text: str, quote_file_id: Optional[int], forwarder_email: str, origin: str, destination: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """The same draft, with the quote's cargo read from its text or from a file of this PO's job, and a blank left (and listed) for anything not found. It names no customer."""
    from pathlib import Path

    from app.core.database import SessionLocal
    from app.models.db_models import ItemFileDB, JobDB, PurchaseOrderDB
    from app.services import vendor_payments

    job_id = None
    body = str(text or "")
    with SessionLocal() as db:
        po = db.get(PurchaseOrderDB, int(po_id)) if po_id else None
        if po_id and po is None:
            raise CostError(404, "There is no such purchase order.")
        job = db.get(JobDB, po.job_id) if po else None
        job_id = job.id if job else None
        if quote_file_id:
            f = db.get(ItemFileDB, int(quote_file_id))
            if po is None or f is None or f.deleted_at or not vendor_payments._belongs(db, f, po, job):
                raise CostError(422, "The quote file is not a file of this PO's job, so it was not read. Give the PO and a file attached to it.")
            try:
                if Path(f.stored_path).stat().st_size > 10 * 1024 * 1024:
                    raise ValueError("too big")
                raw = Path(f.stored_path).read_bytes()
                if raw[:4] == b"%PDF":
                    import fitz

                    with fitz.open(stream=raw, filetype="pdf") as pdf:
                        if pdf.needs_pass or pdf.is_encrypted:
                            raise ValueError("locked")
                        body += "\n" + "\n".join(pg.get_text() for pg in list(pdf)[:60])
                else:
                    body += "\n" + raw.decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                raise CostError(422, "That quote file could not be read (it is missing, damaged or locked). Paste the quote's text instead.")
    if not body.strip() and not po_id:
        raise CostError(422, "Give a purchase order, or the quote's text.")
    return freight_rfq_draft(parse_cargo(body[:100000]), forwarder_email, origin, destination, user, job_id, blank_ok=True)
