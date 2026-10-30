"""Shipments with freight forwarders: status history, ETA changes, forwarder emails.

Tracking APIs cost money (Terminal49's free tier has no API), so the free way
in is the forwarder's own emails: shipping advice, ETA updates, arrival
notices. Container and air waybill numbers are matched with fixed patterns,
which are reliable; the local AI reads the status and dates, and anything it
is unsure of goes to a person instead of changing the shipment.
"""

import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.config import settings
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import ShipmentDB, ShipmentEventDB
from app.services.coordination import notify as alerts

# Ocean milestones in order (air uses the same words where they fit).
STATUSES = [
    "BOOKED", "CARGO_READY", "GATE_IN", "DEPARTED", "TRANSSHIPMENT", "ARRIVED",
    "DISCHARGED", "CUSTOMS_RELEASED", "GATE_OUT", "DELIVERED", "EMPTY_RETURNED", "EXCEPTION",
]
BEFORE_DEPARTURE = {"BOOKED", "CARGO_READY", "GATE_IN"}
AT_SEA = {"DEPARTED", "TRANSSHIPMENT"}
AT_PORT = {"ARRIVED", "DISCHARGED", "CUSTOMS_RELEASED"}
FINISHED = {"GATE_OUT", "DELIVERED", "EMPTY_RETURNED"}

_CONTAINER = re.compile(r"\b([A-Z]{4}\s?\d{7})\b")
_AWB = re.compile(r"\b(\d{3})[-\s]?(\d{8})\b")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _norm(value: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _as_dict(s: ShipmentDB, events: Optional[List[ShipmentEventDB]] = None) -> Dict[str, Any]:
    d = {k: v for k, v in s.__dict__.items() if not k.startswith("_")}
    for k in ("last_update_at", "created_at"):
        d[k] = d[k].isoformat() if d.get(k) else None
    if events is not None:
        d["events"] = [
            {"at": e.at.isoformat() if e.at else None, "status": e.status, "eta": e.eta,
             "description": e.description, "source": e.source}
            for e in events
        ]
    return d


def list_shipments(include_finished: bool = True) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.query(ShipmentDB).order_by(ShipmentDB.eta_current.is_(None), ShipmentDB.eta_current).all()
        out = []
        for s in rows:
            if not include_finished and s.status in FINISHED:
                continue
            events = db.query(ShipmentEventDB).filter(ShipmentEventDB.shipment_id == s.id).order_by(ShipmentEventDB.id.desc()).limit(20).all()
            out.append(_as_dict(s, events))
    on = alerts.today()
    for d in out:
        d["flags"] = flags(d, on)
    return out


def _date_ok(value: Optional[str]) -> Optional[str]:
    """A real calendar date as YYYY-MM-DD, or None (2026-02-30 is refused, not stored)."""
    if not value or not _ISO.match(value):
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def _day(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _local_day(stamp: Optional[str]) -> Optional[date]:
    """Stored timestamps are UTC (SQLite drops the zone); compare them as business-time dates."""
    if not stamp:
        return None
    at = datetime.fromisoformat(stamp)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(alerts.now_local().tzinfo).date()


def create(data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    status = (data.get("status") or "BOOKED").upper()
    if status not in STATUSES:
        raise ValueError(f"Status must be one of {', '.join(STATUSES)}.")
    eta = _date_ok(data.get("eta"))
    with SessionLocal() as db:
        row = ShipmentDB(
            project_item_id=data.get("project_item_id") or None, forwarder=data.get("forwarder"),
            mode=(data.get("mode") or "SEA").upper(), reference=data.get("reference"),
            container_no=_norm(data.get("container_no")) or None, awb=_norm(data.get("awb")) or None,
            carrier=data.get("carrier"), origin=data.get("origin"), destination=data.get("destination"),
            etd=_date_ok(data.get("etd")), eta_original=eta, eta_current=eta,
            free_time_ends=_date_ok(data.get("free_time_ends")), status=status,
            owner_email=data.get("owner_email") or user.get("email"),
        )
        db.add(row)
        db.flush()
        db.add(ShipmentEventDB(shipment_id=row.id, status=status, eta=eta,
                               description=f"Shipment added by {user.get('email', 'system')}", source="manual"))
        db.commit()
        db.refresh(row)  # fields expire on commit
        return _as_dict(row)


def update(shipment_id: int, changes: Dict[str, Any], who: str, source: str = "manual",
           raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Applies a status/date update, records it in the history, and tells the owner of ETA moves."""
    with SessionLocal() as db:
        row = db.get(ShipmentDB, shipment_id)
        if row is None:
            raise LookupError("No such shipment.")
        notes = []
        status = (changes.get("status") or "").upper()
        if status:
            if status not in STATUSES:
                raise ValueError(f"Status must be one of {', '.join(STATUSES)}.")
            if status != row.status:
                notes.append(f"status {row.status} -> {status}")
                row.status = status
        old_eta = row.eta_current
        eta = _date_ok(changes.get("eta"))
        if eta and eta != row.eta_current:
            notes.append(f"ETA {row.eta_current or 'none'} -> {eta}")
            row.eta_original = row.eta_original or eta
            row.eta_current = eta
        for field in ("etd", "free_time_ends"):
            value = _date_ok(changes.get(field))
            if value and value != getattr(row, field):
                notes.append(f"{field.replace('_', ' ')} {getattr(row, field) or 'none'} -> {value}")
                setattr(row, field, value)
        for field in ("forwarder", "carrier", "reference", "origin", "destination", "owner_email", "project_item_id"):
            if changes.get(field):
                setattr(row, field, changes[field])
        text = changes.get("note") or ""
        row.last_update_at = datetime.now(timezone.utc)
        db.add(ShipmentEventDB(
            shipment_id=row.id, status=row.status, eta=row.eta_current, source=source, raw=raw,
            description="; ".join(filter(None, ["; ".join(notes), text])) or "Update received (no change)",
        ))
        db.commit()
        db.refresh(row)
        result = _as_dict(row)

    if eta and old_eta and eta > old_eta:
        alerts.notify(
            "shipment_eta", f"ship-eta:{shipment_id}:{eta}",
            f"Shipment ETA moved: {_label(result)}",
            f"The ETA moved from {old_eta} to {eta} (source: {source}, by {who}).",
            to=[result["owner_email"]] if result.get("owner_email") else [],
            link=f"/shipments?id={shipment_id}", project_item_id=result.get("project_item_id"),
        )
    if result["status"] in FINISHED:
        alerts.resolve([f"ship-late:{shipment_id}:", f"ship-slip:{shipment_id}:", f"ship-silent:{shipment_id}:",
                        f"ship-rolled:{shipment_id}:", f"ship-free-time:{shipment_id}:"])
    return result


def _label(s: Dict[str, Any]) -> str:
    ident = s.get("container_no") or s.get("awb") or s.get("reference") or f"#{s.get('id')}"
    return f"{ident} ({s.get('forwarder') or 'forwarder'}, {s.get('origin') or '?'} to {s.get('destination') or '?'})"


def flags(s: Dict[str, Any], on: date) -> List[str]:
    """Plain-language reasons this shipment needs attention."""
    if s["status"] in FINISHED:
        return []
    out = []
    eta = s.get("eta_current")
    if eta and eta < on.isoformat() and s["status"] not in AT_PORT:
        out.append("late")
    eta_day, original = _day(eta), _day(s.get("eta_original"))
    if eta_day and original:
        slip = (eta_day - original).days
        if slip >= settings.SHIP_SLIP_DAYS:
            out.append(f"slipped {slip} days")
    if s.get("etd") and s["etd"] < on.isoformat() and s["status"] in BEFORE_DEPARTURE:
        out.append("missed departure")
    last = _local_day(s.get("last_update_at"))
    if last:
        quiet_days = (on - last).days
        limit = settings.SHIP_PORT_SILENT_DAYS if s["status"] in AT_PORT else settings.SHIP_SILENT_DAYS
        if s["status"] in AT_SEA | AT_PORT and quiet_days >= limit:
            out.append(f"no update for {quiet_days} days")
    free = _day(s.get("free_time_ends"))
    if free and (free - on).days <= settings.SHIP_FREE_TIME_WARN_DAYS:
        out.append("port free time ending")
    return out


# ---------------------------------------------------------------------------
# Forwarder emails
# ---------------------------------------------------------------------------


class ForwarderUpdate(BaseModel):
    status: Literal[
        "BOOKED", "CARGO_READY", "GATE_IN", "DEPARTED", "TRANSSHIPMENT", "ARRIVED", "DISCHARGED",
        "CUSTOMS_RELEASED", "GATE_OUT", "DELIVERED", "EMPTY_RETURNED", "EXCEPTION", "UNKNOWN",
    ]
    references: List[str] = Field(description="Booking, bill of lading (B/L, HBL, MBL) numbers exactly as written")
    etd: str = Field(description="Departure date as YYYY-MM-DD, blank if not stated")
    eta: str = Field(description="Arrival date at destination as YYYY-MM-DD, blank if not stated")
    last_free_day: str = Field(description="Last free day at the port as YYYY-MM-DD, blank if not stated")
    summary: str = Field(description="One sentence in English: what the forwarder is telling us")
    confidence: float = Field(ge=0, le=1)


_SYSTEM = (
    "You read status emails from freight forwarders for an importer. Report only what the email states. "
    "Dates must be YYYY-MM-DD; leave a date blank if the email does not give it. "
    "Status meanings: BOOKED booking confirmed; CARGO_READY goods ready at the factory; GATE_IN container "
    "delivered to the origin port; DEPARTED vessel or flight left; TRANSSHIPMENT moved between ships; "
    "ARRIVED arrived at the destination port; DISCHARGED unloaded; CUSTOMS_RELEASED cleared customs; "
    "GATE_OUT picked up from the port; DELIVERED delivered to the consignee; EXCEPTION a problem or delay; "
    "UNKNOWN if none fits. Set confidence below 0.8 if anything is unclear."
)


def _match(text: str, refs: List[str]) -> Optional[int]:
    """The open shipment this email is about: container or air waybill first, then a reference.

    A reference only counts as a whole word of at least 5 characters, so a
    short booking number like "BK1" or "4521" never matches inside another
    shipment's container number.
    """
    containers = {_norm(c) for c in _CONTAINER.findall(text.upper())}
    awbs = {a + b for a, b in _AWB.findall(text)}
    # Whole words, and runs of up to three, so "BK 778812" matches BK778812
    # while "4521" still never matches inside MSCU1234521.
    words = [w for w in (_norm(t) for t in re.split(r"\s+", text)) if w]
    refset = {_norm(r) for r in refs if r}
    for size in (1, 2, 3):
        refset |= {"".join(words[i:i + size]) for i in range(len(words) - size + 1)}
    with SessionLocal() as db:
        open_rows = db.query(ShipmentDB).filter(ShipmentDB.status.notin_(list(FINISHED))).order_by(ShipmentDB.id).all()
        for s in open_rows:
            if (s.container_no and s.container_no in containers) or (s.awb and s.awb in awbs):
                return s.id
        for s in open_rows:
            ref = _norm(s.reference)
            if len(ref) >= 5 and ref in refset:
                return s.id
    return None


def ingest_forwarder_email(sender: str, subject: str, body: str, actor: str = "forwarder-email") -> Dict[str, Any]:
    """Turns a forwarder's email into a shipment update, or hands it to a person."""
    from app.services.ai import store
    from app.services.ai.providers import AIUnavailableError, get_provider

    text = f"{subject}\n{body}"
    provider = get_provider()
    try:
        update_ = provider.structured(_SYSTEM, f"From: {sender}\nSubject: {subject}\n\n{body[:12000]}", ForwarderUpdate)
    except AIUnavailableError as exc:
        update_ = None
        logger.warning(f"Forwarder email not read by AI: {exc}")
    shipment_id = _match(text, update_.references if update_ else [])
    sure = bool(update_ and update_.confidence >= settings.AI_CONFIDENCE_THRESHOLD and update_.status != "UNKNOWN")

    store.record(
        "forwarder_update", f"Forwarder email: {subject[:200]}",
        provider=provider.name, model=provider.model,
        input={"sender": sender, "subject": subject, "body": body[:4000]},
        output=update_.model_dump() if update_ else None,
        confidence=update_.confidence if update_ else None,
        needs_review=not (shipment_id and sure), reference=str(shipment_id or ""), actor=actor,
    )

    if shipment_id and sure:
        status = update_.status if update_.status != "UNKNOWN" else None
        if status and status != "EXCEPTION" and _behind(status, shipment_id):
            # An old email arriving late must not move a shipment backwards.
            status = None
        changes = {"status": status, "eta": update_.eta,
                   "etd": update_.etd, "free_time_ends": update_.last_free_day, "note": f"Forwarder: {update_.summary}"}
        return {"applied": True, "shipment": update(shipment_id, changes, sender, source="email",
                                                       raw={"subject": subject, "sender": sender})}

    reason = "no matching shipment" if not shipment_id else "the email was unclear"
    alerts.notify(
        "forwarder_unmatched", f"forwarder-email:{_norm(subject)[:80]}:{alerts.today()}",
        f"Forwarder email needs a person ({reason})",
        f"From {sender}: {subject}\n\n" + (f"Reads as: {update_.summary}" if update_ else "The AI could not read it."),
        to=alerts.leadership(), link="/shipments",
    )
    return {"applied": False, "reason": reason, "shipment_id": shipment_id,
            "reading": update_.model_dump() if update_ else None}


def _behind(status: str, shipment_id: int) -> bool:
    with SessionLocal() as db:
        row = db.get(ShipmentDB, shipment_id)
        current = row.status if row else None
    order = [x for x in STATUSES if x != "EXCEPTION"]
    return current in order and status in order and order.index(status) < order.index(current)


def resolve_cleared(s: Dict[str, Any]) -> None:
    """Closes this shipment's alerts whose condition has cleared (it arrived, it sailed, the forwarder wrote)."""
    sid, now = s["id"], set(s.get("flags") or [])
    cleared = []
    if "late" not in now:
        cleared.append(f"ship-late:{sid}:")
    if not any(f.startswith("slipped") for f in now):
        cleared.append(f"ship-slip:{sid}:")
    if "missed departure" not in now:
        cleared.append(f"ship-rolled:{sid}:")
    if not any(f.startswith("no update") for f in now):
        cleared.append(f"ship-silent:{sid}:")
    if "port free time ending" not in now:
        cleared.append(f"ship-free-time:{sid}:")
    alerts.resolve(cleared)


def is_forwarder(sender: str) -> bool:
    domains = {d.strip().lower().lstrip("@") for d in (settings.FORWARDER_DOMAINS or "").split(",") if d.strip()}
    return bool(domains) and (sender or "").lower().rsplit("@", 1)[-1] in domains
