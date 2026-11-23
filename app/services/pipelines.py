"""The three fulfilment pipelines, kept beside a shipment (the shipments table is not changed):
  US_OFFICE_PARCEL      re-pack, ship by UPS: the tracking number is stored (and checked: a UPS 1Z number has a check digit),
  US_MOUNTAIN_MOVERS    blind drop-ship by truck: quote options, the bill of lading, pickup and delivery dates are read out of Mountain Movers' emails,
  CN_FREIGHT_FORWARDER  the vendor sends its invoice and packing list to the forwarder, Allied sends its own packing list; a weekly forwarder digest moves the dates.
Everything read from an email or a scan is a SUGGESTION flagged "needs a person to confirm": a date with two readings (03/04/26) is never applied; an ETA is applied only when the shipment matches exactly one
container or reference in the digest. Nothing is sent anywhere."""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.models.db_models import ShipmentDB, ShipmentPipelineDB

PIPELINES = ("US_OFFICE_PARCEL", "US_MOUNTAIN_MOVERS", "CN_FREIGHT_FORWARDER")
CHECK = "needs a person to confirm against the email or image"


class PipelineError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ------------------------------------------------------------------ UPS tracking numbers

def ups_valid(number: str) -> bool:
    """A UPS 1Z tracking number: '1Z' + 15 letters or digits + a check digit (UPS's own rule: digits count as themselves, letters as (ord - 63) mod 10, odd places x1 and even places x2)."""
    n = re.sub(r"\s+", "", str(number or "")).upper()
    if not re.fullmatch(r"1Z[0-9A-Z]{16}", n):
        return False
    body, check = n[2:-1], n[-1]
    total = 0
    for i, ch in enumerate(body, start=1):
        v = int(ch) if ch.isdigit() else (ord(ch) - 63) % 10
        total += v * (2 if i % 2 == 0 else 1)
    return check.isdigit() and (10 - total % 10) % 10 == int(check)


def ups_make(body15: str) -> str:
    """The full tracking number for 15 characters (used by the tests to make a valid synthetic one)."""
    total = 0
    for i, ch in enumerate(body15.upper(), start=1):
        v = int(ch) if ch.isdigit() else (ord(ch) - 63) % 10
        total += v * (2 if i % 2 == 0 else 1)
    return "1Z" + body15.upper() + str((10 - total % 10) % 10)


# ------------------------------------------------------------------ dates and money in an email

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def read_date(text: str) -> Dict[str, Any]:
    """{iso, ambiguous}: an unmistakable date as ISO, or ambiguous=True (not applied) when two readings are possible; iso None when it is not a date."""
    t = (text or "").strip().strip(".,;")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    elif re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", t):
        a, b, y = [int(x) for x in re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", t).groups()]
        y += 2000 if y < 100 else 0
        if a <= 12 and b <= 12 and a != b:
            return {"iso": None, "ambiguous": True}
        mo, d = (a, b) if a <= 12 else (b, a)
    else:
        m = re.fullmatch(r"([A-Za-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})", t) or None
        if m:
            mo, d, y = _MONTHS.get(m.group(1).lower(), 0), int(m.group(2)), int(m.group(3))
        else:
            m = re.fullmatch(r"(\d{1,2})[ -]([A-Za-z]{3})[a-z]*\.?[ -](\d{4})", t)
            if not m:
                return {"iso": None, "ambiguous": False}
            d, mo, y = int(m.group(1)), _MONTHS.get(m.group(2).lower(), 0), int(m.group(3))
    try:
        found = date(y, mo, d)
    except ValueError:
        return {"iso": None, "ambiguous": False}
    today = date.today()
    if not (today - timedelta(days=366) <= found <= today + timedelta(days=3 * 366)):               # a date a century away is a typo or a hostile row, never a shipping date
        return {"iso": None, "ambiguous": False}
    return {"iso": found.isoformat(), "ambiguous": False}


_DATE_RX = r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|[A-Za-z]{3,9}\.? \d{1,2},? \d{4}|\d{1,2}[ -][A-Za-z]{3,9}\.?[ -]\d{4})"


class _VisibleText(HTMLParser):
    """The text a person would SEE in an HTML email: text inside display:none, visibility:hidden, font-size:0, hidden or aria-hidden elements, and script/style, is dropped."""

    _VOID = {"br", "hr", "img", "input", "meta", "link", "wbr", "area", "base", "col", "embed", "source", "track"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.hidden = 0
        self.stack: List[bool] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._VOID:
            if tag in ("br", "hr"):
                self.out.append("\n")
            return
        a = {k: (v or "") for k, v in attrs}
        style = a.get("style", "").lower().replace(" ", "")
        hid = tag in ("script", "style", "head", "title") or "display:none" in style or "visibility:hidden" in style or "font-size:0" in style or "hidden" in a or a.get("aria-hidden", "").lower() == "true"
        self.stack.append(hid)
        if hid:
            self.hidden += 1
        if tag in ("p", "div", "tr", "li", "table"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self._VOID or not self.stack:
            return
        if self.stack.pop():
            self.hidden -= 1
        if tag in ("p", "div", "tr", "li", "td"):
            self.out.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.out.append(data)


def visible_text(text: str) -> str:
    """Plain text as it is; an HTML email reduced to what is visible (hidden spans are not read)."""
    t = (text or "")[:300000]
    if not re.search(r"<\s*[a-zA-Z/!][^>]{0,200}>", t):
        return t
    p = _VisibleText()
    try:
        p.feed(t)
        p.close()
    except Exception:  # noqa: BLE001 - broken HTML: fall back to the text with every tag cut out
        return re.sub(r"<[^>]{0,400}>", " ", t)
    return "".join(p.out)


_WROTE = re.compile(r"(?im)^\s*(?:on\s.{3,160}wrote\s*:|-{2,}\s*original message\s*-{2,}|-{2,}\s*forwarded message\s*-{2,}|from:\s.{3,120}\n\s*sent:\s)")


def strip_quoted(text: str) -> str:
    """Only the new part of a reply: everything from 'On ... wrote:', 'Original Message' or a forwarded header down is dropped, and so is any line that starts with '>'."""
    t = text or ""
    m = _WROTE.search(t)
    if m:
        t = t[:m.start()]
    return "\n".join(l for l in t.splitlines() if not l.lstrip().startswith(">"))


def clean_email_text(text: str) -> str:
    return strip_quoted(visible_text(text))


def _money(text: str) -> Optional[str]:
    try:
        return f"{Decimal(text.replace(',', '').replace('$', '')):.2f}"
    except (InvalidOperation, ValueError):
        return None


# ------------------------------------------------------------------ Mountain Movers

def parse_mountain_movers(text: str) -> Dict[str, Any]:
    """Quote options (service, price, transit), BOL numbers, pickup and delivery dates from a Mountain Movers email. Every figure is flagged for a person to confirm."""
    t = clean_email_text(text)
    options = []
    for m in re.finditer(r"(?im)^\s*(?:option\s*(\d+)|quote\s*(\d+))\s*[:.\-)]\s*(?P<svc>[A-Za-z][A-Za-z /&]{2,40}?)\s*[,\-:]?\s*\$\s*(?P<price>[0-9][0-9,]*\.?[0-9]{0,2})(?P<rest>[^\n]*)", t):
        transit = re.search(r"(\d{1,2})(?:\s*-\s*(\d{1,2}))?\s*(?:business\s+|working\s+)?days?", m.group("rest"), re.I)
        options.append({"option": int(m.group(1) or m.group(2)), "service": m.group("svc").strip(), "price": _money(m.group("price")),
                        "transit_days": f"{transit.group(1)}-{transit.group(2)}" if transit and transit.group(2) else (transit.group(1) if transit else None)})
    bol = [b.upper() for b in re.findall(r"(?i)\bBOL\s*(?:#|no\.?|number)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{4,20})", t) if any(ch.isdigit() for ch in b)]       # a BOL number has a digit in it: "attached", "copy" and "pending" are words
    events, ambiguous = [], []
    for kind, rx in (("pickup", r"(?i)pick-?\s?up(?:\s+date)?\s*[:\-]?\s*" + _DATE_RX), ("delivery", r"(?i)deliver(?:y|ed)(?:\s+(?:date|on))?\s*[:\-]?\s*" + _DATE_RX)):
        for m in re.finditer(rx, t):
            d = read_date(m.group(1))
            (ambiguous if d["ambiguous"] else events).append({"kind": kind, "text": m.group(1), "date": d["iso"]} if not d["ambiguous"] else {"kind": kind, "text": m.group(1)})
    events = [e for e in events if e["date"]]
    return {"quote_options": options, "bol_numbers": sorted(set(bol)), "events": events, "ambiguous_dates": ambiguous, "check": CHECK}


# ------------------------------------------------------------------ forwarder weekly digest

def parse_forwarder_digest(text: str) -> Dict[str, Any]:
    """Rows of a forwarder's weekly update: a container number (4 letters + 7 digits) or a booking reference, with its ETA ('ETA 2026-12-01', 'ETA: 1 Dec 2026'). A row with a date that has two readings is listed
    as ambiguous and never applied."""
    rows, ambiguous = [], []
    today = date.today()
    for line in clean_email_text(text).splitlines():
        ref = re.search(r"\b([A-Z]{4}\d{7})\b", line) or re.search(r"(?i)\b(?:booking|ref|reference|b/l)\s*(?:no\.?|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{5,20})\b", line)
        eta = re.search(r"(?i)\beta\b\s*[:\-]?\s*" + _DATE_RX, line)
        if not (ref and eta):
            continue
        d = read_date(eta.group(1))
        row = {"reference": ref.group(1).upper(), "eta_text": eta.group(1)}
        if d["ambiguous"] or not d["iso"] or not (today - timedelta(days=30) <= date.fromisoformat(d["iso"]) <= today + timedelta(days=730)):
            ambiguous.append({**row, "reason": "The date can be read two ways, so it was not used." if d["ambiguous"] else "That is not a usable date, or it is outside the last 30 days to the next 2 years."})
        else:
            rows.append({**row, "eta": d["iso"]})
    return {"rows": rows, "ambiguous": ambiguous, "check": CHECK}


def _match_shipments(reference: str) -> List[int]:
    with SessionLocal() as db:
        ref = reference.strip().upper()
        return [s.id for s in db.query(ShipmentDB).filter((ShipmentDB.container_no == ref) | (ShipmentDB.reference == ref) | (ShipmentDB.awb == ref)).all()]


def _shipment_view(shipment_id: int) -> Any:
    """(the shipment's ETA now, the job it belongs to by its PO reference or None)."""
    from app.models.db_models import JobDB, PurchaseOrderDB

    with SessionLocal() as db:
        s = db.get(ShipmentDB, shipment_id)
        if s is None:
            return None, None
        job = None
        ref = str(s.reference or "")
        if ref.upper().startswith("PO "):
            po = db.query(PurchaseOrderDB).filter(PurchaseOrderDB.po_number == ref[3:].strip()).first()
            j = db.get(JobDB, po.job_id) if po is not None else None
            job = {"id": j.id, "ref": j.ref} if j is not None else None
        return s.eta_current, job


def sender_trusted(sender: str) -> bool:
    """The mail came from a freight forwarder's own domain (FORWARDER_DOMAINS), not from any address."""
    domains = {d.strip().lower().lstrip("@") for d in str(getattr(settings, "FORWARDER_DOMAINS", "") or "").split(",") if d.strip()}
    s = str(sender or "").strip().lower()
    addr = re.sub(r"^.*<([^<>]+)>\s*$", r"\1", s)
    return "@" in addr and addr.rsplit("@", 1)[1] in domains


def apply_forwarder_digest(text: str, user: Dict[str, Any], apply: bool = False, sender: str = "") -> Dict[str, Any]:
    """Reads a digest and (only with apply=True) moves each matching shipment's ETA through the normal update (history, owner told). {rows:[{reference, eta, shipment_id, action}], ...}."""
    from app.services.coordination import shipments

    parsed = parse_forwarder_digest(text)
    if apply and not (sender_trusted(sender) or str(user.get("role", "")).upper() in ("MANAGER", "ADMIN")):
        raise PipelineError(403, "Dates are applied only from a mail sent by the freight forwarder's own address (set FORWARDER_DOMAINS), or by a manager. Show what would change instead, or ask a manager.")
    etas: Dict[str, set] = {}
    counts: Dict[str, int] = {}
    for r in parsed["rows"]:
        etas.setdefault(r["reference"], set()).add(r["eta"])
        counts[r["reference"]] = counts.get(r["reference"], 0) + 1
    out = []
    done: set = set()
    for r in parsed["rows"]:
        base = {**r, "container": r["reference"], "new_eta": r["eta"], "old_eta": None, "job": None, "flags": ["listed more than once in this update"] if counts[r["reference"]] > 1 else []}
        if len(etas[r["reference"]]) > 1:
            out.append({**base, "shipment_id": None, "flags": base["flags"] + ["two different dates"], "action": "two different dates for this one in the same update: not applied"})
            continue
        if r["reference"] in done:
            continue
        done.add(r["reference"])
        ids = _match_shipments(r["reference"])
        if len(ids) != 1:
            out.append({**base, "shipment_id": None, "action": "no shipment matches" if not ids else "more than one shipment matches: not applied"})
            continue
        sid = ids[0]
        base["old_eta"], base["job"] = _shipment_view(sid)
        r = {**base}
        action = "would set ETA"
        if apply:
            try:
                shipments.update(sid, {"eta": r["eta"], "note": "Weekly forwarder update"}, user.get("email", "system"), source="forwarder_digest")
                with SessionLocal() as db:
                    p = db.get(ShipmentPipelineDB, sid)
                    if p is not None:
                        p.forwarder_digest_at = datetime.now(timezone.utc)
                        db.commit()
                action = "ETA set"
            except (LookupError, ValueError) as exc:
                action = f"not applied: {exc}"
        out.append({**r, "shipment_id": sid, "action": action})
    if apply:
        audit_log(action="FORWARDER_DIGEST_APPLIED", resource_type="SHIPMENT", resource_id="digest", actor_email=user.get("email", ""), actor_role=user.get("role", ""),
                  detail=f"{sum(1 for o in out if o['action'] == 'ETA set')} of {len(out)} rows applied")
    return {"rows": out, "ambiguous": parsed["ambiguous"], "applied": bool(apply), "sender_trusted": sender_trusted(sender), "can_apply": bool(sender_trusted(sender) or str(user.get("role", "")).upper() in ("MANAGER", "ADMIN")), "check": CHECK}


# ------------------------------------------------------------------ the pipeline record

def _row(p: ShipmentPipelineDB) -> Dict[str, Any]:
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"shipment_id": p.shipment_id, "pipeline": p.pipeline, "tracking_no": p.tracking_no, "carrier": p.carrier, "bol_file_id": p.bol_file_id, "quote_options": list(p.quote_options or []),
            "forwarder_digest_at": iso(p.forwarder_digest_at), "freight_cost_actual": None if p.freight_cost_actual is None else f"{p.freight_cost_actual:.2f}",
            "freight_cost_quoted": None if p.freight_cost_quoted is None else f"{p.freight_cost_quoted:.2f}", "freight_populated_at": iso(p.freight_populated_at), "variance_flag": bool(p.variance_flag),
            "updated_at": iso(p.updated_at)}


def get(shipment_id: int) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        p = db.get(ShipmentPipelineDB, shipment_id)
        return _row(p) if p else None


def _clean_options(options: Any) -> List[Dict[str, Any]]:
    """Quote options are kept as {option, service, price, transit_days} only (short text, a plain price); anything else a caller sends is dropped."""
    from app.core.money import parse_amount

    out: List[Dict[str, Any]] = []
    for o in list(options or [])[:20]:
        if not isinstance(o, dict):
            continue
        try:
            price = parse_amount(o.get("price"), "price", none_ok=True)
        except ValueError:
            raise PipelineError(422, "A quote option's price is not a plain number.")
        out.append({"option": o.get("option") if isinstance(o.get("option"), int) else None, "service": str(o.get("service") or "")[:60], "price": None if price is None else f"{price:.2f}",
                    "transit_days": str(o.get("transit_days") or "")[:12] or None})
    return out


def set_pipeline(shipment_id: int, pipeline: str, user: Dict[str, Any], tracking_no: str = "", carrier: str = "", bol_file_id: Optional[int] = None, quote_options: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    pipe = str(pipeline or "").upper()
    if pipe not in PIPELINES:
        raise PipelineError(422, f"The pipeline is one of: {', '.join(PIPELINES)}.")
    track = re.sub(r"\s+", "", str(tracking_no or "")).upper()
    if track and (pipe == "US_OFFICE_PARCEL" or track.startswith("1Z")) and not ups_valid(track):
        raise PipelineError(422, "That is not a valid UPS tracking number (it starts 1Z and has 18 characters ending in a check digit). Check it against the label.")
    with SessionLocal() as db:
        if db.get(ShipmentDB, shipment_id) is None:
            raise PipelineError(404, "No such shipment.")
        p = db.get(ShipmentPipelineDB, shipment_id)
        if p is None:
            p = ShipmentPipelineDB(shipment_id=shipment_id, pipeline=pipe, quote_options=[], variance_flag=False)
            db.add(p)
        p.pipeline = pipe
        if track:
            p.tracking_no = track
            p.carrier = (carrier or ("UPS" if ups_valid(track) else p.carrier or ""))[:64] or None
        elif carrier:
            p.carrier = carrier[:64]
        if bol_file_id is not None:
            from app.models.db_models import ItemFileDB

            f = db.get(ItemFileDB, int(bol_file_id))
            if f is None or f.deleted_at or str(f.item_type) != "shipment" or str(f.item_ref) != str(shipment_id):
                raise PipelineError(422, "The bill of lading must be a file attached to this shipment.")
            p.bol_file_id = int(bol_file_id)
        if quote_options is not None:
            p.quote_options = _clean_options(quote_options)
        p.updated_at = datetime.now(timezone.utc)
        db.commit()
        out = _row(p)
    audit_log(action="SHIPMENT_PIPELINE_SET", resource_type="SHIPMENT", resource_id=str(shipment_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""), detail=f"{pipe} {track}".strip())
    return out
