"""Owner and "ball is with": whose turn it is on an item, since when, and chasing that runs by itself.

Every item can have an owner (responsible for it getting done) and a ball holder (the one who has to act next:
one of our people, or an outsider such as a vendor, a customer or a carrier). Moving the ball restarts the clock,
tells the person receiving it, and writes a line in the item's comment thread. Chasing, in working days since
the ball moved (settings BALL_NUDGE_DAYS / BALL_ESCALATE_DAYS / BALL_LEADERSHIP_DAYS):
  nudge      remind whoever holds the ball (for an outsider: tell the owner it's time to follow up)
  escalate   tell the owner too
  leadership tell leadership
Open RFQs and vendor jobs that nobody has set by hand still show in the lists with a ball worked out from what the
app already knows (asked a vendor, waiting on the customer, with us); those are already chased by the autopilot
plays, so only hand-set balls are chased here. Nothing is written to monday.
"""

from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import ItemBallDB
from app.services.coordination import notify, updates, workdays

INTERNAL = ("us", "engineer", "manager")
KINDS = {"us": "us", "engineer": "engineering", "manager": "management", "vendor": "a vendor", "customer": "the customer",
         "carrier": "a carrier", "other": "someone else"}


class BallError(ValueError):
    """Something a person can read about why the ball can't be set."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(at: Optional[datetime]) -> Optional[datetime]:
    return at if at is None or at.tzinfo else at.replace(tzinfo=timezone.utc)


_names_var: ContextVar[Dict[str, str]] = ContextVar("ball_names", default={})   # per request/thread, so the chase worker and a page never share one


def _load_names() -> None:
    _names_var.set({p["email"]: p["name"] for p in updates.people()})


def _name(email_or_text: Optional[str]) -> str:
    if email_or_text and "@" in email_or_text:
        return _names_var.get().get(email_or_text.lower(), email_or_text)
    return email_or_text or ""


def _days(since: Optional[datetime]) -> float:
    since = _aware(since)
    return round(workdays.days_between(since, _now()), 1) if since else 0.0


def level_for(days: float, overdue: bool = False) -> int:
    level = 3 if days >= settings.BALL_LEADERSHIP_DAYS else 2 if days >= settings.BALL_ESCALATE_DAYS else 1 if days >= settings.BALL_NUDGE_DAYS else 0
    return max(level, 2) if overdue else level


def _view(r: ItemBallDB, label: str = "") -> Dict[str, Any]:
    days = _days(r.ball_since)
    overdue = bool(r.due_at and r.due_at < date.today().isoformat() and not r.resolved_at)
    return {"item_type": r.item_type, "item_ref": r.item_ref, "label": label, "derived": False,
            "owner_email": r.owner_email, "owner_name": _name(r.owner_email),
            "ball_with": r.ball_with, "ball_label": KINDS.get(r.ball_with, r.ball_with), "ball_person": r.ball_person,
            "ball_name": _name(r.ball_person), "note": r.note or "", "due_at": r.due_at, "overdue": overdue,
            "since": _aware(r.ball_since).isoformat(), "days_waiting": days, "level": r.escalation_level,
            "status": ("done" if r.resolved_at else {0: "on track", 1: "reminded", 2: "owner told", 3: "leadership told"}[r.escalation_level]),
            "resolved": bool(r.resolved_at), "history": r.history or []}


def _label(item_type: str, ref: str) -> str:
    try:
        return updates._resolve_item(item_type, ref)[1]
    except Exception:  # noqa: BLE001 - a label is a nicety
        return f"{item_type} {ref}"


# ----------------------------------------------------------------------------- writing

def set_ball(item_type: str, item_ref: str, user: Dict[str, Any], ball_with: Optional[str] = None, ball_person: Optional[str] = None,
             owner: Optional[str] = None, note: Optional[str] = None, due: Optional[str] = None) -> Dict[str, Any]:
    """Set the owner and/or move the ball. Moving it (a different holder) restarts the clock and the chasing."""
    ref, label, _ = updates._resolve_item(item_type, item_ref)
    me = user.get("email", "unknown").lower()
    _load_names()
    directory = {p["email"]: p for p in updates.people()}
    for field, value in (("owner", owner), ("person", ball_person), ("note", note), ("due date", due), ("kind", ball_with)):
        if value is not None and not isinstance(value, str):
            raise BallError(f"The {field} must be text.")
    if owner:
        owner = owner.strip().lower()
        if owner not in directory:
            raise BallError(f"I don't know {owner}. Pick the owner from the list of people.")
    if due:
        try:
            due = date.fromisoformat(due).isoformat()
        except ValueError:
            raise BallError("The due date must look like 2026-11-20.")
    if isinstance(ball_with, str):                    # "Vendor", " vendor " and the label a screen shows ("a vendor") all mean the same holder
        said = " ".join(ball_with.split()).casefold()
        ball_with = said if said in KINDS else next((k for k, label in KINDS.items() if label.casefold() == said), said)
    if ball_with is not None and ball_with not in KINDS:
        raise BallError(f"Whose turn is it? One of: {', '.join(KINDS)}.")
    with SessionLocal() as db:
        row = db.query(ItemBallDB).filter(ItemBallDB.item_type == item_type, ItemBallDB.item_ref == ref).first()
        if row is None:
            if not ball_with:
                raise BallError("Say whose turn it is.")
            row = ItemBallDB(item_type=item_type, item_ref=ref, ball_with="us", history=[], escalation_level=0, ball_since=_now())
            db.add(row)
            try:
                db.flush()
            except IntegrityError:                                     # a double click made the row a moment ago: use it
                db.rollback()
                row = db.query(ItemBallDB).filter(ItemBallDB.item_type == item_type, ItemBallDB.item_ref == ref).one()
        kind = ball_with or row.ball_with
        person = (ball_person.strip() if ball_person is not None else row.ball_person) or None
        if kind in INTERNAL:
            person = (person or owner or row.owner_email or me).lower()
            if person not in directory:
                raise BallError(f"I don't know {person}. Pick who has the ball from the list of people.")
        elif not person:
            raise BallError(f"Say who has the ball ({KINDS[kind].lower()}: their name).")
        moved = row.resolved_at is not None or (kind, (person or "")) != (row.ball_with, row.ball_person or "") or not row.history
        old = f"{KINDS.get(row.ball_with, row.ball_with)}: {_name(row.ball_person)}" if row.history else "nobody"
        if owner:
            row.owner_email = owner
        elif not row.owner_email:
            row.owner_email = me if me in directory else None
        if note is not None:
            row.note = note.strip()[:300] or None
        if due is not None:
            row.due_at = due or None
        row.set_by = me
        if moved:
            row.ball_with, row.ball_person, row.ball_since = kind, person, _now()
            row.escalation_level, row.last_escalated_at, row.resolved_at = 0, None, None
            row.history = (row.history or []) + [{"at": _now().isoformat(), "by": me, "from": old,
                                                  "to": f"{KINDS[kind]}: {_name(person)}", "note": row.note or ""}]
        db.commit()
        db.refresh(row)
        out = _view(row, label)
    if moved:
        _announce(item_type, ref, user, out)
        audit_log("BALL_MOVED", "item_ball", f"{item_type}:{ref}", actor_email=me, actor_role=user.get("role", ""),
                  detail=f"to {out['ball_label']}: {out['ball_name']}")
    return out


def _announce(item_type: str, ref: str, user: Dict[str, Any], v: Dict[str, Any]) -> None:
    """A line in the item's thread; naming the person who now has the ball alerts them."""
    text = f"Ball is now with {v['ball_name']}, {v['ball_label']}" + (f": {v['note']}" if v["note"] else "") + \
           (f". Due {v['due_at']}." if v["due_at"] else ".")
    named = v["ball_person"] if v["ball_person"] and "@" in v["ball_person"] else None
    try:
        updates.post(item_type, ref, text, user, mentions=[named] if named else [])
    except Exception as exc:  # noqa: BLE001 - the ball moved; a missing thread line must not undo that
        logger.warning(f"Could not note the ball move in the thread: {exc}")


def resolve(item_type: str, item_ref: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """Nobody needs to act any more: the item is done (or no longer waiting)."""
    ref, label, _ = updates._resolve_item(item_type, item_ref)
    me = user.get("email", "unknown").lower()
    _load_names()
    with SessionLocal() as db:
        row = db.query(ItemBallDB).filter(ItemBallDB.item_type == item_type, ItemBallDB.item_ref == ref).first()
        if row is None or row.resolved_at:
            raise LookupError("Nobody has the ball on that item.")
        row.resolved_at, row.set_by = _now(), me
        row.history = (row.history or []) + [{"at": _now().isoformat(), "by": me, "from": f"{KINDS[row.ball_with]}: {_name(row.ball_person)}",
                                              "to": "done", "note": ""}]
        db.commit()
        db.refresh(row)
        out = _view(row, label)
    try:
        updates.post(item_type, ref, "Done: nobody is waiting on this any more.", user)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not note the ball being cleared: {exc}")
    return out


# ----------------------------------------------------------------------------- reading

def get(item_type: str, item_ref: str) -> Optional[Dict[str, Any]]:
    ref, label, _ = updates._resolve_item(item_type, item_ref)
    _load_names()
    with SessionLocal() as db:
        row = db.query(ItemBallDB).filter(ItemBallDB.item_type == item_type, ItemBallDB.item_ref == ref).first()
        return _view(row, label) if row else None


def _derived() -> List[Dict[str, Any]]:
    """Open RFQs and vendor jobs nobody has set by hand, with a ball worked out from what the app knows."""
    from app.services.coordination import rfqs, vendors

    out: List[Dict[str, Any]] = []

    def entry(item_type, ref, label, owner, kind, person, since, note):
        since = _aware(since)
        days = _days(since)
        out.append({"item_type": item_type, "item_ref": ref, "label": label, "derived": True, "owner_email": owner,
                    "owner_name": _name(owner), "ball_with": kind, "ball_label": KINDS[kind], "ball_person": person,
                    "ball_name": _name(person), "note": note, "due_at": None, "overdue": False,
                    "since": since.isoformat() if since else None, "days_waiting": days, "level": level_for(days),
                    "status": "worked out from the item", "resolved": False, "history": []})

    try:
        for r in rfqs.list_rfqs(open_only=True, limit=500):
            if r.get("waiting_since"):
                entry("rfq", r["number"], f"RFQ {r['number']} {r.get('customer', '')}".strip(), r.get("owner_email"), "vendor",
                      r.get("vendor") or "the vendor", workdays.as_utc(_parse(r["waiting_since"])), "Waiting for the vendor's price")
            elif r.get("paused_at"):
                entry("rfq", r["number"], f"RFQ {r['number']} {r.get('customer', '')}".strip(), r.get("owner_email"), "customer",
                      r.get("customer") or "the customer", workdays.as_utc(_parse(r["paused_at"])), "Waiting on the customer")
            else:
                entry("rfq", r["number"], f"RFQ {r['number']} {r.get('customer', '')}".strip(), r.get("owner_email"), "us",
                      r.get("owner_email") or "", workdays.as_utc(_parse(r.get("received_at"))), "Needs a quote")
        for j in vendors.overview()["jobs"]:
            since = _parse(j.get("requested_on") or j.get("ordered_on"))
            if j["state"] != "shipped" and since:
                entry("vendor_job", str(j["id"]), f"{j['vendor']} PO {j.get('po_number') or j['id']}", j.get("owner_email"), "vendor",
                      j["vendor"], workdays.as_utc(since), j["reasons"][0] if j.get("reasons") else "Not shipped yet")
    except Exception as exc:  # noqa: BLE001 - the list still works without the worked-out ones
        logger.warning(f"Could not work out balls from RFQs and vendor jobs: {exc}")
    return out


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        text = str(value)
        return datetime.fromisoformat(text if "T" in text or " " in text else text + "T09:00:00")
    except ValueError:
        return None


def list_open(who: str = "all", me: str = "", kind: str = "", late_only: bool = False, include_derived: bool = True) -> List[Dict[str, Any]]:
    """Everything with a ball, longest wait first. who='me' keeps what I own or hold."""
    me = me.lower()
    _load_names()
    with SessionLocal() as db:
        rows = db.query(ItemBallDB).filter(ItemBallDB.resolved_at.is_(None)).all()
        explicit = [_view(r, _label(r.item_type, r.item_ref)) for r in rows]
    keys = {(e["item_type"], e["item_ref"]) for e in explicit}
    items = explicit + ([d for d in _derived() if (d["item_type"], d["item_ref"]) not in keys] if include_derived else [])
    if who == "me":
        items = [i for i in items if me and me in ((i["owner_email"] or "").lower(), (i["ball_person"] or "").lower())]
    if kind:
        items = [i for i in items if i["ball_with"] == kind]
    if late_only:
        items = [i for i in items if i["days_waiting"] >= settings.BALL_NUDGE_DAYS or i["overdue"]]
    return sorted(items, key=lambda i: -i["days_waiting"])


def _casing(names: List[str]) -> Dict[str, str]:
    """One spelling for each name that is written in several cases (Helen, helen, HELEN): a mixed-case spelling if there is one, else the most common; an all-lowercase single spelling becomes Title Case."""
    groups: Dict[str, List[str]] = {}
    for n in names:
        groups.setdefault(str(n).casefold(), []).append(str(n))
    out: Dict[str, str] = {}
    for key, variants in groups.items():
        mixed = [v for v in variants if v != v.lower() and v != v.upper()]
        pick = max(set(mixed), key=variants.count) if mixed else max(set(variants), key=variants.count)
        if pick == pick.lower() and "@" not in pick:
            pick = pick.title()
        out[key] = pick
    return out


def summary() -> Dict[str, Any]:
    """The management view: where the ball sits across the business, who is holding the most, what's oldest. `late_means` says in words what late and escalated mean; each holder has waiting_days (their oldest wait)."""
    items = list_open()
    spell = _casing([i["ball_name"] or i["ball_person"] or "?" for i in items])
    for i in items:
        who_ = i["ball_name"] or i["ball_person"] or "?"
        i["ball_name"] = spell.get(str(who_).casefold(), who_) if i["ball_name"] else i["ball_name"]
    by_kind: Dict[str, Dict[str, Any]] = {}
    people: Dict[str, Dict[str, Any]] = {}
    for i in items:
        k = by_kind.setdefault(i["ball_with"], {"kind": i["ball_with"], "label": i["ball_label"], "count": 0, "late": 0, "oldest_days": 0.0})
        k["count"] += 1
        k["late"] += i["days_waiting"] >= settings.BALL_NUDGE_DAYS
        k["oldest_days"] = max(k["oldest_days"], i["days_waiting"])
        who = i["ball_name"] or i["ball_person"] or "?"
        who = spell.get(str(who).casefold(), who)
        p = people.setdefault((i["ball_with"], str(who).casefold()), {"name": who, "kind": i["ball_with"], "count": 0, "late": 0, "oldest_days": 0.0})
        p["count"] += 1
        p["late"] += i["days_waiting"] >= settings.BALL_NUDGE_DAYS
        p["oldest_days"] = max(p["oldest_days"], i["days_waiting"])
    return {"open": len(items), "late": sum(i["days_waiting"] >= settings.BALL_NUDGE_DAYS for i in items),
            "escalated": sum(i["level"] >= 2 for i in items), "by_kind": sorted(by_kind.values(), key=lambda k: -k["count"]),
            "by_holder": [{**p, "waiting_days": int(p["oldest_days"])} for p in sorted(people.values(), key=lambda p: (-p["late"], -p["count"]))[:15]], "oldest": items[:10],
            "late_means": (f"Late means someone has had the ball for {settings.BALL_NUDGE_DAYS} working days or more. Escalated means {settings.BALL_ESCALATE_DAYS} days or more (the owner is told), "
                           f"and {settings.BALL_LEADERSHIP_DAYS} days or more reaches leadership. Every escalated item is also late and also open: the three are not separate piles."),
            "rules": {"nudge_days": settings.BALL_NUDGE_DAYS, "escalate_days": settings.BALL_ESCALATE_DAYS,
                      "leadership_days": settings.BALL_LEADERSHIP_DAYS}}


def waiting_on(email: str) -> List[Dict[str, Any]]:
    """For a person's morning brief: hand-set items where they hold the ball, longest first."""
    return [i for i in list_open("me", email, include_derived=False) if i["ball_person"] and i["ball_person"].lower() == email.lower()]


# ----------------------------------------------------------------------------- chasing

def run() -> Dict[str, int]:
    """Climb the ladder for hand-set balls that have waited long enough. Each rung is told once per move of the ball."""
    sent = {"nudged": 0, "escalated": 0, "leadership": 0}
    _load_names()
    with SessionLocal() as db:
        rows = db.query(ItemBallDB).filter(ItemBallDB.resolved_at.is_(None)).all()
        todo = []
        for r in rows:
            try:
                updates._resolve_item(r.item_type, r.item_ref)
            except LookupError:                                   # the item was deleted: nobody can act on it, and its number may be reused
                r.resolved_at = _now()
                r.history = (r.history or []) + [{"at": _now().isoformat(), "by": "system", "from": f"{KINDS[r.ball_with]}: {_name(r.ball_person)}",
                                                  "to": "done", "note": "The item no longer exists."}]
                continue
            except Exception:  # noqa: BLE001 - can't tell; chase it
                pass
            days = _days(r.ball_since)
            overdue = bool(r.due_at and r.due_at < date.today().isoformat())
            level = level_for(days, overdue)
            if level > r.escalation_level:
                todo.append((r.id, level, days))
        db.commit()
    for row_id, level, days in todo:
        try:
            if _climb(row_id, level, days):
                sent[{1: "nudged", 2: "escalated", 3: "leadership"}[level]] += 1
        except Exception as exc:  # noqa: BLE001 - one item that can't be chased must never stop the rest
            logger.warning(f"Could not chase ball {row_id}: {type(exc).__name__}: {exc}")
    return sent


def _climb(row_id: int, level: int, days: float) -> bool:
    """Tell the people for one rung of one ball. False if it was already told (or has been dealt with)."""
    if True:
        with SessionLocal() as db:
            r = db.get(ItemBallDB, row_id)
            if r is None or r.resolved_at or level <= r.escalation_level:
                return False
            v = _view(r, _label(r.item_type, r.item_ref))
            since_key = int(_aware(r.ball_since).timestamp() * 1_000_000)   # a new hand-over is a new key, even within one second
            holder = r.ball_person.lower() if r.ball_with in INTERNAL and r.ball_person else None
            owner = (r.owner_email or "").lower() or None
            link = updates.link_for(r.item_type, r.item_ref)
            who = f"{v['ball_name']}, {v['ball_label']}"
            n = round(days)
            what = f"{v['label']}: {v['note']}" if v["note"] else v["label"]
            if level == 1:
                to = [holder] if holder else [owner] if owner else []
                title = (f"Still with you: {v['label']} ({n} working day{'s' if n != 1 else ''})" if holder
                         else f"Time to follow up with {who}: {v['label']} ({n} days)")
                body = f"{what}. It has been with {who} for {n} working days."
                kw = {"severity": "info"}
            elif level == 2:
                to = [e for e in (owner, holder) if e]
                title = f"{n} days waiting on {who}: {v['label']}"
                body = f"{what}. Nothing has moved for {n} working days" + (f" and it was due {r.due_at}." if v["overdue"] else ".")
                kw = {"severity": "warning"}
            else:
                to = [e for e in (owner, holder) if e]
                title = f"Stuck {n} days with {who}: {v['label']}"
                body = f"{what}. This has been with {who} for {n} working days. Owner: {v['owner_name'] or 'nobody set'}."
                kw = {"severity": "critical", "include_leadership": True}
            r.escalation_level, r.last_escalated_at = level, _now()
            db.commit()
        notify.notify("ball_chase", f"ball:{row_id}:{since_key}:{level}", title, body, to=to, link=link, **kw)
        return True
