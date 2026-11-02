"""Vendor jobs: will each vendor's date still let the project finish on time?

Each job is one vendor PO tied to a project. Its ship-by date (need_by) is
worked back from the project's due date: due - door-to-door transit for the
mode - a safety buffer. The job's expected ship date is the vendor's current
promise; with no promise yet, the PO date plus what this vendor has really
taken before (the 80th percentile of past jobs), or its quote + 20% while
there are too few past jobs to trust.

Vendors are scored against their FIRST promise, so re-promising does not
hide lateness, and against the date Allied asked for.

Jobs come from a monday vendor board (MONDAY_VENDOR_BOARD_ID, one item per
PO) or are entered here. Date changes made here are written back to monday.
"""

import statistics
import threading
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import MondayMilestoneDB, MondayProjectDB, VendorJobDB
from app.services.coordination import notify as alerts
from app.services.coordination import workdays
from app.services.coordination.shipments import _date_ok, _day

MODES = ("SEA", "AIR", "TRUCK")
DATE_FIELDS = ("ordered_on", "requested_on", "acknowledged_on", "promised_on", "shipped_on", "need_by", "customer_due")
TEXT_FIELDS = ("vendor", "description", "project_item_id", "po_number", "owner_email", "status")
ALERT_KEYS = ("vendor-late:", "vendor-risk:", "vendor-noack:", "vendor-checkin:")
# The last read of the vendor board, so other checks on the same board don't read it again.
last_board: Dict[str, Any] = {"board": None, "at": 0.0, "items": []}


def _transit(mode: str) -> int:
    return {
        "AIR": settings.VENDOR_TRANSIT_DAYS_AIR,
        "TRUCK": settings.VENDOR_TRANSIT_DAYS_TRUCK,
    }.get((mode or "SEA").upper(), settings.VENDOR_TRANSIT_DAYS_SEA)


def _mode(value: Optional[str]) -> str:
    v = (value or "").strip().upper()
    if v.startswith("AIR"):
        return "AIR"
    if v.startswith(("TRUCK", "ROAD", "LAND")):
        return "TRUCK"
    return "SEA"


def _default_mode(vendor: str) -> str:
    """With no mode on the board: truck for vendors listed as domestic, otherwise sea."""
    domestic = {v.strip().casefold() for v in (settings.VENDOR_DOMESTIC_VENDORS or "").split(",") if v.strip()}
    return "TRUCK" if (vendor or "").strip().casefold() in domestic else "SEA"


def _as_dict(j: VendorJobDB) -> Dict[str, Any]:
    d = {k: v for k, v in j.__dict__.items() if not k.startswith("_")}
    for k in ("created_at", "updated_at"):
        d[k] = d[k].isoformat() if d.get(k) else None
    return d


def _p80(values: List[int]) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.8 * (len(ordered) - 1))))]


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------


def scorecard(jobs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """Per vendor: on-time %, days late, real lead times against the quote."""
    jobs = jobs if jobs is not None else _all()
    out: Dict[str, Dict[str, Any]] = {}
    for j in jobs:
        v = out.setdefault(j["vendor"], {"vendor": j["vendor"], "open": 0, "shipped": 0, "on_time_promise": 0,
                                         "measured_promise": 0, "on_time_requested": 0, "measured_requested": 0,
                                         "late_days": [], "lead_days": [], "quoted_days": [], "slips": 0})
        v["slips"] += j.get("slips") or 0
        shipped = _day(j.get("shipped_on"))
        if not shipped:
            v["open"] += 1
            continue
        v["shipped"] += 1
        first = _day(j.get("promised_original") or j.get("promised_on"))
        if first:
            v["measured_promise"] += 1
            v["on_time_promise"] += int(shipped <= first)
            if shipped > first:
                v["late_days"].append((shipped - first).days)
        asked = _day(j.get("requested_on"))
        if asked:
            v["measured_requested"] += 1
            v["on_time_requested"] += int(shipped <= asked)
        ordered = _day(j.get("ordered_on"))
        if ordered and shipped >= ordered:
            v["lead_days"].append((shipped - ordered).days)
            if j.get("quoted_lead_days"):
                v["quoted_days"].append(int(j["quoted_lead_days"]))
    for v in out.values():
        lead, quoted = v.pop("lead_days"), v.pop("quoted_days")
        late = v.pop("late_days")
        v["otd_promise"] = round(v["on_time_promise"] / v["measured_promise"], 3) if v["measured_promise"] else None
        v["otd_requested"] = round(v["on_time_requested"] / v["measured_requested"], 3) if v["measured_requested"] else None
        v["avg_days_late"] = round(sum(late) / len(late), 1) if late else 0
        v["lead_median"] = int(statistics.median(lead)) if lead else None
        v["lead_p80"] = _p80(lead) if lead else None
        v["quoted_median"] = int(statistics.median(quoted)) if quoted else None
        v["history_jobs"] = len(lead)
        v["below_target"] = bool(v["otd_promise"] is not None and v["otd_promise"] < settings.VENDOR_OTD_TARGET)
        v["slower_than_quoted"] = bool(v["lead_median"] and v["quoted_median"]
                                       and v["lead_median"] > v["quoted_median"] * 1.1)
    return out


# ---------------------------------------------------------------------------
# Where each job stands
# ---------------------------------------------------------------------------


def _projects() -> Dict[str, Dict[str, Any]]:
    """What a vendor job can be linked to on monday: a project item, or a milestone item.

    On a groups-layout board (Allied's Engineering Team Board) each drawing job is a
    milestone and the "project" is just its group, so a vendor job links to the job
    itself and takes its date from the job's due date."""
    with SessionLocal() as db:
        out = {p.item_id: {"name": p.name, "due_date": p.due_date, "owner_emails": p.owner_emails or [], "done": p.done}
               for p in db.query(MondayProjectDB)}
        for m in db.query(MondayMilestoneDB):
            out.setdefault(m.item_id, {"name": m.name, "due_date": m.due_date, "owner_emails": m.owner_emails or [],
                                       "done": m.done, "drawing": True, "status": m.status})
        return out


def assess(j: Dict[str, Any], project: Optional[Dict[str, Any]], card: Optional[Dict[str, Any]], on: date) -> Dict[str, Any]:
    """state: shipped / late / will_miss / at_risk / no_ack / checkin / on_track / no_dates."""
    need = _day(j.get("need_by"))
    need_source = "set by hand" if need else None
    if not need and project and _day(project.get("due_date")):
        need = _day(project["due_date"]) - timedelta(days=_transit(j.get("mode")) + settings.VENDOR_BUFFER_DAYS)
        need_source = f"project due {project['due_date']} - {_transit(j.get('mode'))} days transit - {settings.VENDOR_BUFFER_DAYS} days buffer"
    elif not need and _day(j.get("customer_due")):
        transit = _transit(j.get("mode")) if settings.VENDOR_CUSTOMER_DUE_IS_DELIVERY else 0
        need = _day(j["customer_due"]) - timedelta(days=transit + settings.VENDOR_BUFFER_DAYS)
        need_source = (f"customer due {j['customer_due']} - {transit} days transit - {settings.VENDOR_BUFFER_DAYS} days buffer"
                       if transit else f"customer ship date {j['customer_due']} - {settings.VENDOR_BUFFER_DAYS} days buffer")
    promised = _day(j.get("promised_on"))
    ordered = _day(j.get("ordered_on"))
    expected, basis = promised, "vendor's promise" if promised else None
    history = (card or {}).get("history_jobs") or 0
    forecast = None
    if ordered:
        if history >= settings.VENDOR_HISTORY_MIN_JOBS and card.get("lead_p80") is not None:
            forecast = ordered + timedelta(days=card["lead_p80"])
            forecast_basis = f"this vendor's usual lead time ({card['lead_p80']} days, 8 of 10 past jobs)"
        elif j.get("quoted_lead_days"):
            forecast = ordered + timedelta(days=int(round(int(j["quoted_lead_days"]) * 1.2)))
            forecast_basis = f"quoted {j['quoted_lead_days']} days + 20% (not enough history yet)"
        if expected is None and forecast:
            expected, basis = forecast, forecast_basis

    out: Dict[str, Any] = {"need_by": need.isoformat() if need else None, "need_source": need_source,
                           "expected": expected.isoformat() if expected else None, "expected_basis": basis,
                           "slack_days": (need - expected).days if (need and expected) else None,
                           "days_late": None, "reasons": []}
    if _day(j.get("shipped_on")):
        out["state"] = "shipped"
        return out
    reasons = out["reasons"]
    state = "on_track"
    # With no promise, the vendor's word is the PO date plus its quoted lead time.
    committed = promised or (ordered + timedelta(days=int(j["quoted_lead_days"]))
                             if ordered and j.get("quoted_lead_days") is not None else None)
    if committed and committed < on:
        state = "late"
        out["days_late"] = (on - committed).days
        said = "promised" if promised else f"quoted {j['quoted_lead_days']} days from {ordered.isoformat()}, so due"
        reasons.append(f"{said} {committed.isoformat()}, not shipped ({out['days_late']} days late)")
    if need and expected and expected > need:
        if state != "late":
            state = "will_miss"
        whose = "the customer" if need_source and need_source.startswith("customer") else "the project"
        reasons.append(f"expected {expected.isoformat()} but must ship by {need.isoformat()} for {whose}")
    elif need and promised and forecast and history >= settings.VENDOR_HISTORY_MIN_JOBS and forecast > need and state == "on_track":
        state = "at_risk"
        reasons.append(f"promise is in time, but this vendor usually takes longer (forecast {forecast.isoformat()})")
    elif need and expected and state == "on_track" and (need - expected).days < settings.VENDOR_BUFFER_DAYS // 2:
        state = "at_risk"
        reasons.append(f"only {(need - expected).days} days of slack before the ship-by date")
    # A board with no confirmation column can't show one, so it isn't chased for it.
    tracks_ack = j.get("source") != "monday" or bool(settings.VENDOR_ACK_COLUMN_ID)
    if tracks_ack and not j.get("acknowledged_on") and not promised and ordered:
        due = workdays.workdays_after(ordered, settings.VENDOR_ACK_BUSINESS_DAYS)
        if on > due:
            if state == "on_track":
                state = "no_ack"
            reasons.append(f"PO sent {ordered.isoformat()}, vendor has not confirmed it or given a date")
    if state == "on_track" and promised and 0 <= (promised - on).days <= settings.VENDOR_CHECKIN_DAYS:
        state = "checkin"
        reasons.append(f"ships {promised.isoformat()}; ask the vendor to reconfirm")
    if state == "on_track" and not expected:
        state = "no_dates"
        reasons.append("no PO date, promise or quoted lead time yet")
    out["state"] = state
    return out


def _all() -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        return [_as_dict(j) for j in db.query(VendorJobDB).order_by(VendorJobDB.id)]


def _blocked_by(j: Dict[str, Any], p: Optional[Dict[str, Any]], on: date) -> Optional[Dict[str, Any]]:
    """An order linked to a drawing job that isn't finished waits on that drawing. A drawing
    already past its date makes the order late too, so it is said in the order's reasons."""
    if not p or not p.get("drawing") or p.get("done") or j.get("state") == "shipped":
        return None
    due = _day(p.get("due_date"))
    overdue = bool(due and due < on)
    j["reasons"].append(f"waits on drawing {p['name']} ({p.get('status') or 'not started'}"
                        + (f", {(on - due).days} days past its date {due.isoformat()})" if overdue
                           else f", due {due.isoformat()})" if due else ")"))
    return {"item_id": j.get("project_item_id"), "name": p["name"], "status": p.get("status"),
            "due_date": p.get("due_date"), "overdue": overdue}


def _local_day(at) -> str:
    """Allied's own calendar day for a stored UTC time (an evening send is still today)."""
    return workdays.as_utc(at).astimezone(alerts.now_local().tzinfo).date().isoformat()


def _chasers() -> Dict[int, Dict[str, Optional[str]]]:
    """For each vendor job, when a chaser was last sent and last drafted (from the confirm queue)."""
    from app.models.db_models import AssistantActionDB

    out: Dict[int, Dict[str, Optional[str]]] = {}
    with SessionLocal() as db:
        rows = (db.query(AssistantActionDB.args, AssistantActionDB.status, AssistantActionDB.created_at,
                         AssistantActionDB.decided_at)
                .filter(AssistantActionDB.user_role == "AUTOPILOT", AssistantActionDB.tool == "send_email",
                        # A year covers any open PO; older drafts would only make every read slower.
                        AssistantActionDB.created_at >= workdays.now() - timedelta(days=365)).all())
    for args, status, created, decided in rows:
        jid = (args or {}).get("_job_id")
        if jid is None:
            continue
        seen = out.setdefault(int(jid), {"last": None, "drafted": None, "for_po": set(), "undated": []})
        # A follow-up counts for the PO it was about, once it was sent, is waiting, or a person
        # cancelled it. One that failed or lapsed doesn't (it is offered again). Chasers from the
        # old weekly rule carry no _po_date: they count for the PO only if made on or after its date.
        if status in ("DONE", "PENDING", "RUNNING", "CANCELLED"):
            if (args or {}).get("_po_date"):
                seen["for_po"].add(args["_po_date"])
            else:
                seen["undated"].append(_local_day(created))
        if status == "DONE" and decided:
            seen["last"] = max(filter(None, [seen["last"], _local_day(decided)]))
        elif status == "PENDING":
            seen["drafted"] = max(filter(None, [seen["drafted"], _local_day(created)]))
    return out


def follow_up_wait(j: Dict[str, Any]) -> Tuple[int, str]:
    """Days after its PO an order may go unshipped before the vendor is followed up, and why that many."""
    if j.get("follow_up_days"):
        return int(j["follow_up_days"]), "set by hand"
    if j.get("quoted_lead_days"):
        return int(j["quoted_lead_days"]), "the vendor's quoted lead time"
    return settings.VENDOR_FOLLOWUP_DAYS, "the usual wait; no lead time was quoted"


def first_follow_up(j: Dict[str, Any]) -> Optional[date]:
    """The day the vendor is first followed up: the PO date plus the wait, or the day after the
    vendor's confirmed ship date if that is later (no point asking a question it already answered).
    None once shipped, or with no PO date."""
    ordered = _day(j.get("ordered_on"))
    if not ordered or _day(j.get("shipped_on")):
        return None
    first = ordered + timedelta(days=follow_up_wait(j)[0])
    promised = _day(j.get("promised_on"))
    return max(first, promised + timedelta(days=1)) if promised else first


def _follow_up(j: Dict[str, Any], chased: Dict[str, Any], on: date) -> Optional[Dict[str, Any]]:
    """When this order's vendor is followed up: only once the order hasn't shipped its wait after the
    PO (set by hand, else the quoted lead time, else VENDOR_FOLLOWUP_DAYS). The first follow-up is
    drafted on its own; after it, staff follow up again on command from the board."""
    first = first_follow_up(j)
    if first is None:
        return None
    days, basis = follow_up_wait(j)
    started = j["ordered_on"] in (chased.get("for_po") or set()) or any(
        made >= j["ordered_on"] for made in chased.get("undated") or [])
    reached = first <= on
    if first > _day(j["ordered_on"]) + timedelta(days=days):
        basis = f"{basis}; held until after the vendor's confirmed date {j['promised_on']}"
    reason = (f"PO sent {j['ordered_on']}, not shipped after {(on - _day(j['ordered_on'])).days} days (wait {days} days: {basis})"
              if reached else f"first follow-up {first.isoformat()} ({basis})")
    return {"last": chased.get("last"), "drafted": chased.get("drafted"), "first": first.isoformat(),
            "wait_days": days, "wait_basis": basis, "manual_days": j.get("follow_up_days"),
            "next": None if started else first.isoformat(), "due": reached and not started,
            "started": started, "owner": j.get("owner_email"), "reason": reason}


def overview(on: Optional[date] = None, include_shipped: bool = False) -> Dict[str, Any]:
    on = on or alerts.today()
    jobs = _all()
    cards = scorecard(jobs)
    projects = _projects()
    chased = _chasers()
    out = []
    counts = {"late": 0, "will_miss": 0, "at_risk": 0, "no_ack": 0, "checkin": 0}
    for j in jobs:
        p = projects.get(j.get("project_item_id") or "")
        j.update(assess(j, p, cards.get(j["vendor"]), on))
        j["project_name"] = p["name"] if p else None
        j["project_due"] = p["due_date"] if p else None
        j["blocked_by"] = _blocked_by(j, p, on)
        j["follow_up"] = _follow_up(j, chased.get(j["id"], {}), on)
        if j["state"] in counts:
            counts[j["state"]] += 1
        if include_shipped or j["state"] != "shipped":
            out.append(j)
    order = {"late": 0, "will_miss": 1, "at_risk": 2, "no_ack": 3, "checkin": 4, "no_dates": 5, "on_track": 6, "shipped": 7}
    out.sort(key=lambda j: (order.get(j["state"], 9), j.get("expected") or "9999"))
    return {"today": on.isoformat(), "counts": counts, "jobs": out,
            "vendors": sorted(cards.values(), key=lambda v: (v["otd_promise"] is None, v["otd_promise"] or 0)),
            "otd_target": settings.VENDOR_OTD_TARGET, "board_connected": bool(settings.MONDAY_VENDOR_BOARD_ID)}


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------


def _log(row: VendorJobDB, who: str, notes: List[str]) -> None:
    if notes:
        row.history = list(row.history or []) + [{"at": workdays.now().isoformat(), "by": who, "change": "; ".join(notes)}]
    row.updated_at = workdays.now()


def _apply(row: VendorJobDB, data: Dict[str, Any], who: str) -> List[str]:
    """Sets fields, keeping the first promise and counting every later move as a slip."""
    notes = []
    for f in TEXT_FIELDS:
        if f not in data or data[f] is None:
            continue
        value = str(data[f]).strip()
        if value != (getattr(row, f) or ""):
            setattr(row, f, value if f == "vendor" else (value or None))
            notes.append(f"{f.replace('_', ' ')} set")
    if data.get("mode"):
        row.mode = _mode(data["mode"])
    if "follow_up_days" in data and data["follow_up_days"] is not None:
        days = int(data["follow_up_days"])
        if days < 0 or days > 365:
            raise ValueError("Follow-up wait must be 0-365 days (0 goes back to the usual wait).")
        if (days or None) != row.follow_up_days:
            row.follow_up_days = days or None
            notes.append(f"follow-up wait {'set to ' + str(days) + ' days' if days else 'back to the usual'}")
    if "quoted_lead_days" in data and data["quoted_lead_days"] not in (None, ""):
        days = int(data["quoted_lead_days"])
        if days < 0 or days > 730:
            raise ValueError("Quoted lead time must be 0-730 days.")
        row.quoted_lead_days = days
    for f in DATE_FIELDS:
        if f not in data or data[f] in (None, ""):
            continue
        value = _date_ok(str(data[f]))
        if value is None:
            raise ValueError(f"{f.replace('_', ' ')} must be a real date as YYYY-MM-DD.")
        old = getattr(row, f)
        if value == old:
            continue
        if f == "promised_on":
            if not row.promised_original:
                row.promised_original = value
            elif old and value > old:
                row.slips = (row.slips or 0) + 1
        notes.append(f"{f.replace('_', ' ')} {old or 'none'} -> {value}")
        setattr(row, f, value)
    if not row.vendor:
        raise ValueError("Say which vendor.")
    return notes


# One change to vendor orders at a time (a person's save, the board read). Each save rewrites
# the order's history list, so two at once could lose one person's note. One process runs the
# app, so a lock here is enough.
_write_lock = threading.RLock()


def create(data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    who = user.get("email", "system")
    with SessionLocal() as db:
        row = VendorJobDB(vendor="", description="", history=[], source="manual", owner_email=who)
        notes = _apply(row, data, who)
        _log(row, who, ["added"] + notes)
        db.add(row)
        db.commit()
        db.refresh(row)
        out = _as_dict(row)
    audit_log(action="CREATE", resource_type="VENDOR_JOB", resource_id=str(out["id"]), actor_email=who,
              actor_role=user.get("role", ""), detail=f"{out['vendor']}: {out['description']}")
    return out


def update(job_id: int, data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    with _write_lock:
        return _update(job_id, data, user)


def _update(job_id: int, data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    who = user.get("email", "system")
    with SessionLocal() as db:
        row = db.get(VendorJobDB, job_id)
        if row is None:
            raise LookupError("No such vendor job.")
        before = {f: getattr(row, f) for f in DATE_FIELDS}
        notes = _apply(row, data, who)
        if data.get("note"):
            notes.append(str(data["note"])[:500])
        _log(row, who, notes)
        db.commit()
        db.refresh(row)
        out = _as_dict(row)
    changed = {f: out[f] for f in DATE_FIELDS if out[f] != before[f]}
    if out.get("monday_item_id") and changed:
        _write_back(out, changed)
    if out.get("shipped_on"):
        alerts.resolve([f"{k}{job_id}:" for k in ALERT_KEYS])
    audit_log(action="UPDATE", resource_type="VENDOR_JOB", resource_id=str(job_id), actor_email=who,
              actor_role=user.get("role", ""), detail="; ".join(notes) or "no change")
    return out


_COLUMN_FOR = {
    "ordered_on": "VENDOR_ORDERED_COLUMN_ID", "requested_on": "VENDOR_REQUESTED_COLUMN_ID",
    "acknowledged_on": "VENDOR_ACK_COLUMN_ID", "promised_on": "VENDOR_PROMISED_COLUMN_ID",
    "shipped_on": "VENDOR_SHIPPED_COLUMN_ID",
}


def _write_back(job: Dict[str, Any], changed: Dict[str, str]) -> None:
    """monday stays the record: dates changed here go onto the vendor board item."""
    values = {getattr(settings, _COLUMN_FOR[f]): {"date": v} for f, v in changed.items()
              if f in _COLUMN_FOR and getattr(settings, _COLUMN_FOR[f])
              and not getattr(settings, _COLUMN_FOR[f]).startswith("formula")}  # monday works formulas out itself
    if not values or not job.get("board_id"):
        return
    try:
        from app.core.container import get_monday_client

        get_monday_client().update_column_values(
            int(job["board_id"]), int(job["monday_item_id"]), values,
            f"vendor-job-{job['id']}-{job['updated_at']}",
        )
    except Exception as exc:  # noqa: BLE001 - the change is kept here; the next sync shows any gap
        logger.warning(f"Could not write vendor job {job['id']} dates to monday: {exc}")


# ---------------------------------------------------------------------------
# monday vendor board
# ---------------------------------------------------------------------------


def _customer_due(col) -> Optional[str]:
    """The customer's date: a date column, or a start date plus a number of weeks."""
    from app.services.coordination.projects import _due, _text

    if settings.VENDOR_CUSTOMER_DUE_COLUMN_ID:
        found = _due(col("VENDOR_CUSTOMER_DUE_COLUMN_ID"))
        if found:
            return found
    if settings.VENDOR_CUSTOMER_FROM_COLUMN_ID and settings.VENDOR_CUSTOMER_WEEKS_COLUMN_ID:
        start = _day(_due(col("VENDOR_CUSTOMER_FROM_COLUMN_ID")))
        try:
            weeks = float(_text(col("VENDOR_CUSTOMER_WEEKS_COLUMN_ID")).split()[0])
        except (ValueError, IndexError):
            weeks = None
        if start and weeks is not None and 0 <= weeks <= 104:
            return (start + timedelta(days=round(weeks * 7))).isoformat()
    return None


def _linked_id(cv: Optional[Dict[str, Any]]) -> Optional[str]:
    from app.services.coordination.projects import _json

    value = _json(cv) or {}
    ids = value.get("linkedPulseIds") or [] if isinstance(value, dict) else []
    return str(ids[0].get("linkedPulseId")) if ids and isinstance(ids[0], dict) else None


def _person_promise_holds(row: VendorJobDB, from_board: Optional[str]) -> bool:
    """A ship date a person confirmed in the app (say, from the vendor's reply) is kept while
    monday still shows the date it showed then. monday is read only, so its VENDOR ETS can't
    be updated from here; once someone changes it on monday, monday's date wins again.

    The person's own change ("promised on OLD -> NEW") records OLD, which is what the board
    showed at that moment."""
    if not from_board:
        return False
    for h in reversed(row.history or []):
        for change in str(h.get("change") or "").split("; "):
            if not change.startswith("promised on ") or "->" not in change:
                continue
            if h.get("by") == "monday":
                return False  # the board moved since; its date stands
            board_then = change[len("promised on "):].split("->")[0].strip()
            return from_board == board_then and from_board != row.promised_on
    return False


def sync_vendor_board(client=None) -> Dict[str, Any]:
    with _write_lock:
        return _sync_vendor_board(client)


def _sync_vendor_board(client=None) -> Dict[str, Any]:
    """Refreshes vendor jobs from the monday vendor board. Reads only."""
    from app.services.coordination.projects import _client, _cols, _due, _people, _text, _user_emails
    from app.services.monday_client import fetch_projects_board

    board = (settings.MONDAY_VENDOR_BOARD_ID or "").strip()
    if not board:
        return {"skipped": "MONDAY_VENDOR_BOARD_ID is not set"}
    client = client or _client()
    if not client.live:
        return {"skipped": "monday is in simulation; nothing to read"}
    users = _user_emails(client)
    items = fetch_projects_board(client, int(board), with_subitems=False)
    last_board.update(board=board, at=time.monotonic(), items=items)
    if items:
        from app.services.coordination import boards

        boards.remember(board, items, client, "vendors")  # every order, not only the open ones the app tracks
    done = {p.strip().casefold() for p in settings.VENDOR_DONE_LABELS.split(",") if p.strip()}
    open_groups = {g.strip().casefold() for g in settings.VENDOR_OPEN_GROUPS.split(",") if g.strip()}
    seen, added, changed = set(), 0, 0
    moved: List[tuple] = []
    skipped = 0
    with SessionLocal() as db:
        for item in items:
            cols = _cols(item)
            col = lambda name: cols.get(getattr(settings, name)) if getattr(settings, name) else None  # noqa: E731
            vendor = _text(col("VENDOR_VENDOR_COLUMN_ID")) or (item.get("group") or {}).get("title") or "Vendor"
            status = _text(col("VENDOR_STATUS_COLUMN_ID"))
            group = ((item.get("group") or {}).get("title") or "").strip().casefold()
            finished = status.casefold() in done or bool(open_groups and group not in open_groups)
            data: Dict[str, Any] = {
                "vendor": vendor, "status": status or None,
                "description": " - ".join(x for x in (item.get("name") or "", _text(col("VENDOR_DESCRIPTION_COLUMN_ID"))) if x),
                "po_number": _text(col("VENDOR_PO_COLUMN_ID")) or None,
                "project_item_id": _linked_id(col("VENDOR_PROJECT_COLUMN_ID")),
                "mode": _text(col("VENDOR_MODE_COLUMN_ID")) or _default_mode(vendor),
            }
            owners = _people(col("VENDOR_OWNER_COLUMN_ID"), users) if settings.VENDOR_OWNER_COLUMN_ID else []
            if owners:
                data["owner_email"] = owners[0]
            for f, setting in _COLUMN_FOR.items():
                data[f] = _due(col(setting)) if getattr(settings, setting) else None
            lead = _text(col("VENDOR_LEAD_DAYS_COLUMN_ID"))
            if lead:
                try:
                    data["quoted_lead_days"] = int(float(lead.split()[0]))
                except ValueError:
                    pass
            data["customer_due"] = _customer_due(col)
            item_id = str(item["id"])
            row = db.query(VendorJobDB).filter(VendorJobDB.monday_item_id == item_id).first()
            if finished and not data.get("shipped_on"):
                if row is None:
                    # Finished before Allied's system first saw it: there is no
                    # real ship date to learn from, so it is left out.
                    continue
                data["shipped_on"] = row.shipped_on or alerts.today().isoformat()
            seen.add(item_id)
            if row is None:
                row = VendorJobDB(monday_item_id=item_id, board_id=board, vendor=vendor, history=[], source="monday")
                db.add(row)
                added += 1
            before = row.status if row.id else None
            if row.id and _person_promise_holds(row, data.get("promised_on")):
                data.pop("promised_on", None)
            try:
                notes = _apply(row, {k: v for k, v in data.items() if v is not None}, "monday")
            except ValueError as exc:
                # One mistyped cell (a lead time of 900, a bad date) must not stop the whole board.
                logger.warning(f"Vendor board item {item_id} skipped: {exc}")
                if row.id is None:
                    db.expunge(row)
                    added -= 1
                else:
                    db.refresh(row)  # drop anything half-applied; the row stays as it was
                skipped += 1
                continue
            if row.id and (row.status or "") != (before or ""):
                moved.append((row.id, before, row.status))
            if notes and row.id:
                changed += 1
            _log(row, "monday", notes)
        gone = [r for r in db.query(VendorJobDB).filter(VendorJobDB.board_id == board) if r.monday_item_id not in seen]
        if gone and not items:
            raise RuntimeError(f"monday returned no items for vendor board {board}; nothing was removed.")
        for r in gone:
            db.delete(r)
        db.commit()
        rows = {r.id: _as_dict(r) for r in db.query(VendorJobDB).filter(VendorJobDB.id.in_([m[0] for m in moved]))} if moved else {}
    if moved:
        from app.services.coordination import events

        for jid, old, new in moved:
            if jid in rows:
                events.emit("order.status_changed", {"job": rows[jid], "from": old, "to": new},
                            f"{rows[jid]['vendor']} PO {rows[jid].get('po_number') or jid}: {old} -> {new}")
    return {"jobs": len(seen), "added": added, "changed": changed, "removed": len(gone), "skipped": skipped}


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


def _chaser_note(j: Dict[str, Any]) -> str:
    from app.services.coordination import autopilot, events

    if not settings.AUTOPILOT_ENABLED or (events.modes().get("vendor_late_chase") == "off"
                                          and autopilot.modes().get("vendor_chaser") == "off"):
        return ""
    return autopilot.chaser_note(j, alerts.today())


def run_checks(on: Optional[date] = None) -> Dict[str, int]:
    on = on or alerts.today()
    data = overview(on, include_shipped=True)
    sent = {"vendor_late": 0, "vendor_risk": 0, "vendor_noack": 0, "vendor_checkin": 0}
    alerts.resolve_missing(ALERT_KEYS, (str(j["id"]) for j in data["jobs"] if j["state"] != "shipped"))
    projects = _projects()
    digest: Dict[str, List[Dict[str, Any]]] = {}
    for j in data["jobs"]:
        jid, state = j["id"], j["state"]
        keep = {"late": "vendor-late:", "will_miss": "vendor-risk:", "at_risk": "vendor-risk:",
                "no_ack": "vendor-noack:", "checkin": "vendor-checkin:"}.get(state)
        alerts.resolve([f"{k}{jid}:" for k in ALERT_KEYS if k != keep])
        if state in ("shipped", "on_track", "no_dates"):
            continue
        p = projects.get(j.get("project_item_id") or "") or {}
        owners = [j["owner_email"]] if j.get("owner_email") else []
        project_owners = list(p.get("owner_emails") or [])
        label = f"{j['vendor']}: {j['description'] or j.get('po_number') or 'job ' + str(jid)}"
        on_project = f" for {p['name']}" if p.get("name") else ""
        link = f"/vendors?job={jid}"
        why = "\n".join(f"- {r}" for r in j["reasons"])
        if state == "late":
            late = j["days_late"] or 0
            nid = alerts.notify(
                "vendor_late", f"vendor-late:{jid}:{j['promised_on']}",
                f"Vendor late: {label} ({late} days)",
                f"{j['vendor']} {'promised to ship ' + j['promised_on'] if j.get('promised_on') else 'was due to ship'}"
                f"{on_project} and has not.\n{why}\n"
                "Get a firm new date and update it here so the project dates can be checked."
                + (f"\n\n{note}" if (note := _chaser_note(j)) else ""),
                severity="warning", to=owners + project_owners,
                include_leadership=late >= settings.ESCALATE_AFTER_DAYS or bool(j.get("slack_days") is not None and j["slack_days"] < 0),
                link=link, project_item_id=j.get("project_item_id"), replaces=[f"vendor-late:{jid}:"],
            )
            sent["vendor_late"] += int(bool(nid))
            if nid:
                from app.services.coordination import events

                events.emit("vendor_job.late", {"job": j}, f"{label} is {late} days late")
        elif state in ("will_miss", "at_risk") and not p:
            # No project linked: only the customer's date is at stake. These go in one
            # daily list per person rather than one alert each.
            alerts.resolve([f"vendor-risk:{jid}:"])
            for who in owners or [""]:
                digest.setdefault(who, []).append({**j, "label": label})
        elif state in ("will_miss", "at_risk"):
            critical = state == "will_miss"
            nid = alerts.notify(
                "vendor_risk", f"vendor-risk:{jid}:{j['expected']}:{j['need_by']}",
                f"{'Will make the project late' if critical else 'At risk'}: {label}{on_project}",
                f"{why}\nShip-by date: {j['need_by']} ({j['need_source']}).\n"
                "Options: expedite with the vendor, split the shipment, or move to air freight.",
                severity="critical" if critical else "warning", to=owners + project_owners,
                include_leadership=critical, link=link, project_item_id=j.get("project_item_id"),
                replaces=[f"vendor-risk:{jid}:"],
            )
            sent["vendor_risk"] += int(bool(nid))
        elif state == "no_ack":
            nid = alerts.notify(
                "vendor_noack", f"vendor-noack:{jid}:{j['ordered_on']}",
                f"Vendor has not confirmed PO: {label}",
                f"{why}\nAsk {j['vendor']} to confirm the PO and give a ship date.",
                to=owners, link=link, project_item_id=j.get("project_item_id"), replaces=[f"vendor-noack:{jid}:"],
            )
            sent["vendor_noack"] += int(bool(nid))
        elif state == "checkin":
            nid = alerts.notify(
                "vendor_checkin", f"vendor-checkin:{jid}:{j['promised_on']}",
                f"Reconfirm with vendor: {label} ships {j['promised_on']}",
                f"{j['vendor']} is due to ship {j['promised_on']}{on_project}. Ask them to confirm it is still on.",
                to=owners, link=link, project_item_id=j.get("project_item_id"), replaces=[f"vendor-checkin:{jid}:"],
            )
            sent["vendor_checkin"] += int(bool(nid))
    for who, jobs in digest.items():
        miss = [x for x in jobs if x["state"] == "will_miss"]
        lines = [f"- {x['label']}: {'; '.join(x['reasons']) or x['state']} (ship by {x['need_by']})"
                 for x in sorted(jobs, key=lambda x: (x["state"] != "will_miss", x.get("need_by") or ""))]
        nid = alerts.notify(
            "vendor_risk", f"vendor-risk-digest:{who or 'unowned'}:{on.isoformat()}",
            f"{len(jobs)} order(s) at risk of missing the customer's date" + (f", {len(miss)} will miss" if miss else ""),
            "Not linked to a project, so checked against the customer's date:\n" + "\n".join(lines[:60])
            + "\n\nOptions: expedite with the vendor, split the shipment, or move to air freight. "
              "Link an order to its drawing job to get its own alert.",
            severity="warning", to=[who] if who else [], include_leadership=not who, link="/vendors?state=will_miss",
        )
        sent["vendor_risk"] += int(bool(nid))
    # Earlier days' lists, and lists for people with nothing at risk any more, are closed.
    alerts.resolve_others("vendor-risk-digest:", {f"vendor-risk-digest:{who or 'unowned'}:{on.isoformat()}" for who in digest})
    return sent


def risks_for_summary(on: Optional[date] = None) -> List[str]:
    data = overview(on)
    return [f"{j['vendor']}: {j['description'] or j.get('po_number') or 'job'}"
            f"{' (' + j['project_name'] + ')' if j.get('project_name') else ''}: {j['reasons'][0]}"
            for j in data["jobs"] if j["state"] in ("late", "will_miss") and j["reasons"]]


def find(text: str) -> List[Dict[str, Any]]:
    """Jobs whose vendor, description, PO or project name contains the text."""
    t = (text or "").casefold()
    return [j for j in overview(include_shipped=True)["jobs"]
            if not t or any(t in str(j.get(k) or "").casefold() for k in ("vendor", "description", "po_number", "project_name"))]


# ---------------------------------------------------------------------------
# Linking orders to their drawing jobs
# ---------------------------------------------------------------------------

_STOP = {"the", "and", "for", "with", "inc", "llc", "ltd", "corp", "co", "of", "to", "a", "an", "new", "rev", "po",
         "job", "order", "drawing", "drawings", "assy", "assembly"}


def _tokens(text: str) -> set:
    import re

    return {t for t in re.findall(r"[a-z0-9]+", (text or "").casefold()) if len(t) >= 3 and t not in _STOP}


def _same(name: str) -> str:
    """A customer name reduced for comparing: "OC-Irving" and "OC - IRVING" are the same plant."""
    import re

    return re.sub(r"[^a-z0-9]", "", (name or "").casefold())


def link_suggestions(limit: int = 3, min_score: float = 0.25) -> List[Dict[str, Any]]:
    """For each open order with no drawing linked, the unfinished drawing jobs it most
    likely belongs to: same customer, and words shared between the two names. Saving a
    link is a person's call (PATCH the order with project_item_id); nothing is linked here."""
    from app.services.coordination import boards

    customers: Dict[str, str] = {}
    if settings.MONDAY_PROJECTS_BOARD_ID:
        try:
            view = boards.view(settings.MONDAY_PROJECTS_BOARD_ID, hide_done=True)
            col = next((c["id"] for c in view["columns"] if "customer" in c["title"].casefold()), None)
            if col:
                customers = {r["id"]: r["values"].get(col) or "" for r in view["items"]}
        except LookupError:
            pass
    with SessionLocal() as db:
        drawings = [{"item_id": m.item_id, "name": m.name, "status": m.status, "due_date": m.due_date}
                    for m in db.query(MondayMilestoneDB).filter(MondayMilestoneDB.done.is_(False))]
    for d in drawings:
        d["customer"] = customers.get(d["item_id"], "")
        d["_name"] = _tokens(d["name"])
        d["_cust"] = _same(d["customer"])
    out = []
    for j in overview()["jobs"]:
        if j.get("project_item_id"):
            continue
        # The orders board names an order "CUSTOMER - what it is".
        head, _, rest = (j.get("description") or "").partition(" - ")
        customer = _same(head) if rest else ""
        words = _tokens(rest or head)
        if not words:
            continue
        scored = []
        for d in drawings:
            if customer and d["_cust"] and customer != d["_cust"]:
                continue  # a drawing for another customer (or another of its plants)
            shared = words & d["_name"]
            if not shared:
                continue
            score = len(shared) / max(1, min(len(words), len(d["_name"])))
            if customer and customer == d["_cust"]:
                score += 0.5
            if score >= min_score:
                scored.append({"item_id": d["item_id"], "name": d["name"], "customer": d["customer"] or None,
                               "status": d["status"], "due_date": d["due_date"], "score": round(min(score, 1.5), 2),
                               "shared": sorted(shared)})
        if scored:
            scored.sort(key=lambda s: -s["score"])
            out.append({"job_id": j["id"], "vendor": j["vendor"], "po_number": j.get("po_number"),
                        "description": j.get("description"), "state": j["state"], "suggestions": scored[:limit]})
    out.sort(key=lambda x: -x["suggestions"][0]["score"])
    return out
