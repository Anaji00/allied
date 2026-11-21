"""Follow an item and see what changed since you looked. One person's follows and "seen" marks never affect another's. Reading the app's own audit, comments, files and staged edits: nothing is
sent and nothing in monday or Sage is touched. Times are shown in the business time zone by the callers; stored times are UTC.

Kinds: job (anyone who can open the job, including viewers), order, rfq and board_item (staff only: a viewer cannot follow what they cannot see). A board item's ref is "<board id>:<item id>".
"""

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.db_models import AuditLogDB, ItemFollowDB, ItemSeenDB, ItemUpdateDB, MondayChangeDB

KINDS = ("job", "order", "rfq", "board_item")
STAFF = ("ADMIN", "MANAGER", "OPERATOR")


class FollowError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _me(user: Dict[str, Any]) -> str:
    email = (user.get("email") or "").strip().lower()
    if not email:
        raise FollowError(401, "Sign in first.")
    return email


def _aware(d: Any) -> Optional[datetime]:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _check(user: Dict[str, Any], kind: str, ref: str) -> Tuple[str, str, str]:
    """(ref as stored, plain title, link) when this person may follow it; a plain error otherwise."""
    if kind not in KINDS:
        raise FollowError(422, f"Follow a job, order, rfq or board_item, not '{kind}'.")
    ref = " ".join(str(ref or "").split())
    if not ref or len(ref) > 120:
        raise FollowError(422, "Say which one.")
    if kind != "job" and user.get("role") not in STAFF:
        raise FollowError(403, "Only staff can follow that.")
    try:
        if kind == "job":
            from app.services.coordination import jobs

            j = jobs.get(ref, staff=user.get("role") in STAFF)
            return j["ref"], f"{j['ref']} {j.get('title') or ''}".strip(), f"/jobs/{j['ref']}"
        if kind == "rfq":
            from app.services.coordination import rfqs

            r = rfqs.get(ref) if hasattr(rfqs, "get") else None
            if not r:
                raise LookupError(ref)
            return str(r["number"]), f"{r['number']} {r.get('subject') or ''}".strip(), f"/orders?kind=rfq&ref={r['number']}"
        if kind == "order":
            from app.models.db_models import OrderDB

            so = ref.removeprefix("SO-")
            with SessionLocal() as db:
                o = db.query(OrderDB).filter(OrderDB.sales_order_number == so).first()
                if o is None:
                    raise LookupError(ref)
                return so, f"Order {o.customer_po_number or so} {o.customer_name or ''}".strip(), f"/orders?kind=so&ref={so}"
        board, _, item = ref.partition(":")
        from app.services.coordination import boards

        b = boards._mirror.get(board) or {}
        it = next((i for i in b.get("items", []) if str(i["id"]) == item), None)
        if it is None:
            raise LookupError(ref)
        return ref, f"{it.get('name') or item} ({b.get('name') or 'board'})", "/boards"
    except LookupError:
        raise FollowError(404, "No such item.") from None


def follow(user: Dict[str, Any], kind: str, ref: str) -> Dict[str, Any]:
    me = _me(user)
    ref, title, link = _check(user, kind, ref)
    with SessionLocal() as db:
        if not db.query(ItemFollowDB).filter_by(user_email=me, item_kind=kind, item_ref=ref).first():
            db.add(ItemFollowDB(user_email=me, item_kind=kind, item_ref=ref))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()                                                       # a double click: already following
    return {"followed": True, "item_kind": kind, "item_ref": ref, "title": title, "link": link}


def unfollow(user: Dict[str, Any], kind: str, ref: str) -> Dict[str, Any]:
    me = _me(user)
    ref = " ".join(str(ref or "").split())
    with SessionLocal() as db:
        db.query(ItemFollowDB).filter_by(user_email=me, item_kind=kind, item_ref=ref).delete()
        db.commit()
    return {"followed": False, "item_kind": kind, "item_ref": ref}


def mark_seen(user: Dict[str, Any], kind: str, ref: str) -> Dict[str, Any]:
    """This person looked at it now. Only their own mark is written."""
    me = _me(user)
    ref, _title, _link = _check(user, kind, ref)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.query(ItemSeenDB).filter_by(user_email=me, item_kind=kind, item_ref=ref).first()
        if row is None:
            db.add(ItemSeenDB(user_email=me, item_kind=kind, item_ref=ref, seen_at=now))
        else:
            row.seen_at = now
        db.commit()
    return {"item_kind": kind, "item_ref": ref, "seen_at": now.isoformat(), "changed_since_seen": False}


# ------------------------------------------------------------------------------------ when did it change
def changed_at(kind: str, refs: Iterable[str]) -> Dict[str, Optional[datetime]]:
    """The latest change to each item, from the app's own records (UTC). Batched: a few queries for the whole list."""
    refs = list(dict.fromkeys(str(r) for r in refs))
    out: Dict[str, Optional[datetime]] = {r: None for r in refs}
    if not refs:
        return out

    def bump(ref: str, at: Any) -> None:
        a = _aware(at)
        if a and (out[ref] is None or a > out[ref]):
            out[ref] = a

    with SessionLocal() as db:
        if kind == "job":
            from app.models.db_models import JobDB, RfqDB

            for ref, created, approved, closed, number in db.query(JobDB.ref, JobDB.created_at, JobDB.design_approved_at, JobDB.closed_at, RfqDB.number).outerjoin(RfqDB, RfqDB.id == JobDB.rfq_id).filter(JobDB.ref.in_(refs)):
                for at in (created, approved, closed):
                    bump(ref, at)
                if number:
                    for (at,) in db.query(ItemUpdateDB.created_at).filter(ItemUpdateDB.item_type == "rfq", ItemUpdateDB.item_ref == number, ItemUpdateDB.deleted_at.is_(None)).order_by(ItemUpdateDB.id.desc()).limit(1):
                        bump(ref, at)
            for rid, at in db.query(AuditLogDB.resource_id, AuditLogDB.timestamp).filter(AuditLogDB.resource_id.in_(refs)).order_by(AuditLogDB.id.desc()).limit(2000):
                bump(str(rid), at)
        elif kind in ("rfq", "order"):
            for ref in refs:
                for (at,) in db.query(ItemUpdateDB.created_at).filter(ItemUpdateDB.item_type == ("rfq" if kind == "rfq" else "order"), ItemUpdateDB.item_ref == ref, ItemUpdateDB.deleted_at.is_(None)).order_by(ItemUpdateDB.id.desc()).limit(1):
                    bump(ref, at)
            for rid, at in db.query(AuditLogDB.resource_id, AuditLogDB.timestamp).filter(AuditLogDB.resource_id.in_(refs)).order_by(AuditLogDB.id.desc()).limit(2000):
                bump(str(rid), at)
        else:                                                                         # a board item: its staged or pushed edits and the comments on it
            wanted = {r: r.partition(":") for r in refs}
            for ref, (board, _s, item) in wanted.items():
                for (at,) in db.query(MondayChangeDB.updated_at).filter(MondayChangeDB.board_id == board, MondayChangeDB.item_id == item).order_by(MondayChangeDB.id.desc()).limit(1):
                    bump(ref, at)
                for (at,) in db.query(ItemUpdateDB.created_at).filter(ItemUpdateDB.item_type == "board_item", ItemUpdateDB.item_ref == item, ItemUpdateDB.deleted_at.is_(None)).order_by(ItemUpdateDB.id.desc()).limit(1):
                    bump(ref, at)
    return out


def _baselines(me: str, kind: str, refs: List[str]) -> Dict[str, Optional[datetime]]:
    """When this person last looked, or (if they follow it and never looked) when they started following: the moment changes are counted from."""
    base: Dict[str, Optional[datetime]] = {r: None for r in refs}
    with SessionLocal() as db:
        for ref, at in db.query(ItemFollowDB.item_ref, ItemFollowDB.created_at).filter(ItemFollowDB.user_email == me, ItemFollowDB.item_kind == kind, ItemFollowDB.item_ref.in_(refs)):
            base[ref] = _aware(at)
        for ref, at in db.query(ItemSeenDB.item_ref, ItemSeenDB.seen_at).filter(ItemSeenDB.user_email == me, ItemSeenDB.item_kind == kind, ItemSeenDB.item_ref.in_(refs)):
            a = _aware(at)
            if base[ref] is None or a > base[ref]:
                base[ref] = a
    return base


def annotate(user: Dict[str, Any], kind: str, rows: List[Dict[str, Any]], ref_of) -> None:
    """Adds changed_at (ISO, UTC), followed and changed_since_seen to each row of a list. Only this person's own marks are read."""
    me = (user.get("email") or "").strip().lower()
    refs = [str(ref_of(r)) for r in rows if ref_of(r) is not None]
    when = changed_at(kind, refs)
    base = _baselines(me, kind, refs) if me else {}
    followed = set()
    if me and refs:
        with SessionLocal() as db:
            followed = {r for (r,) in db.query(ItemFollowDB.item_ref).filter(ItemFollowDB.user_email == me, ItemFollowDB.item_kind == kind, ItemFollowDB.item_ref.in_(refs))}
    for r in rows:
        ref = str(ref_of(r))
        at, seen = when.get(ref), base.get(ref)
        r["changed_at"] = at.isoformat() if at else None
        r["followed"] = ref in followed
        r["changed_since_seen"] = bool(at and seen and at > seen)


def following(user: Dict[str, Any]) -> Dict[str, Any]:
    """Everything this person follows, newest change first, each with changed_since_seen."""
    me = _me(user)
    with SessionLocal() as db:
        mine = [(k, r, c) for k, r, c in db.query(ItemFollowDB.item_kind, ItemFollowDB.item_ref, ItemFollowDB.created_at).filter(ItemFollowDB.user_email == me)]
    out: List[Dict[str, Any]] = []
    for kind in KINDS:
        rows = [{"item_kind": kind, "item_ref": r} for k, r, _c in mine if k == kind]
        if not rows:
            continue
        annotate(user, kind, rows, lambda x: x["item_ref"])
        for row in rows:
            try:
                _ref, title, link = _check(user, kind, row["item_ref"])
            except FollowError:
                title, link = row["item_ref"], "/"                                    # the item has gone or is no longer visible: still listed, so it can be unfollowed
            out.append({**row, "title": title, "link": link})
    out.sort(key=lambda x: x["changed_at"] or "", reverse=True)
    return {"count": len(out), "changed": sum(1 for x in out if x["changed_since_seen"]), "items": out}


def sentence(user: Dict[str, Any], kind: str, ref: str) -> str:
    """One plain sentence about what changed since this person looked ("" when nothing can be said). Money only for managers."""
    me = (user.get("email") or "").strip().lower()
    base = _baselines(me, kind, [ref]).get(ref)
    if base is None:
        return ""
    manager = user.get("role") in ("MANAGER", "ADMIN")
    try:
        if kind == "job":
            from app.services import my_day

            got = my_day.since("job", ref, base.isoformat().replace("T", " ")[:19], manager)
            return got["changes"][0]["sentence"] if got["changes"] else ""
        if kind == "board_item":
            board, _s, item = ref.partition(":")
            with SessionLocal() as db:
                row = db.query(MondayChangeDB).filter(MondayChangeDB.board_id == board, MondayChangeDB.item_id == item, MondayChangeDB.updated_at > base).order_by(MondayChangeDB.id.desc()).first()
            return (row.summary if row else "")[:200]
    except Exception:  # noqa: BLE001 - a sentence is a nicety; the row still shows
        return ""
    return ""
