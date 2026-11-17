"""Updates: one comment thread per order, RFQ, vendor job, shipment, milestone or board item, with @mentions.

Replaces back-and-forth emails and chat: everything said about an item sits on the item, and naming someone
(`@athish`, `@Doug`, or picking them from the list) alerts them with a link back to it. One level of replies.
Nothing here writes to monday; the thread is the app's own. Deleting hides a comment; the row stays.
"""

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.models.db_models import ItemUpdateDB, OrderDB, RfqDB, ShipmentDB, StaffContactDB, UserDB, VendorJobDB  # noqa: F401
from app.services.coordination import notify

ITEM_TYPES = ("order", "rfq", "vendor_job", "shipment", "milestone", "board_item")
MAX_BODY = 4000
_MENTION = re.compile(r"(?<![A-Za-z0-9_.@])@([^\W\d_][\w.'\-]{0,40})")      # any letters: Nguyễn, 李明, Müller
MAX_MENTIONS = 20
_CJK_START = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class UpdateError(ValueError):
    """Something a person can read about why a comment can't be saved."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ people

def people() -> List[Dict[str, str]]:
    """Everyone who can be named: active staff contacts and active accounts, one row per email."""
    out: Dict[str, Dict[str, str]] = {}
    with SessionLocal() as db:
        for u in db.query(UserDB).filter(UserDB.is_active.is_(True)):
            out[u.email.lower()] = {"email": u.email.lower(), "name": u.full_name or u.email}
        for c in db.query(StaffContactDB).filter(StaffContactDB.active.is_(True)):
            out[c.email.lower()] = {"email": c.email.lower(), "name": c.name or c.email}
    from app.services import bridge

    langs = bridge.languages_of(list(out))
    return sorted(({**p, "language": langs.get(p["email"], "en")} for p in out.values()), key=lambda p: p["name"].lower())


def fold(text: str) -> str:
    """Lower case without accents, so 'Nguyễn' and 'nguyen' are the same word (Chinese characters are left as they are)."""
    text = unicodedata.normalize("NFKD", str(text or "").casefold().replace("đ", "d"))
    return "".join(c for c in text if not unicodedata.combining(c))


def search_people(q: str = "", limit: int = 8) -> List[Dict[str, str]]:
    q = fold(q).strip()
    rows = [p for p in people() if not q or q in fold(p["name"]) or q in p["email"]]
    return rows[:limit]


def _handles(p: Dict[str, str]) -> set:
    """Every way someone can be named after an @: email name, whole name, or any one part of it (accents ignored)."""
    name = fold(p["name"])
    parts = [x for x in re.split(r"[\s.\-]+", name) if x]
    return {fold(p["email"].split("@")[0]), name.replace(" ", ""), name.replace(" ", "."), *[x for x in parts if len(x) >= 2 or not x.isascii()]}


def resolve_mentions(body: str, picked: Optional[List[str]] = None) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]]]:
    """The people named in `body` (and picked from the list), and any @name that matched nobody or several."""
    directory = people()
    by_email = {p["email"]: p for p in directory}
    found: Dict[str, Dict[str, str]] = {}
    for e in picked or []:
        if str(e).lower() in by_email:
            found[str(e).lower()] = by_email[str(e).lower()]
    unresolved: List[Dict[str, Any]] = []
    handles = {p["email"]: _handles(p) for p in directory}
    for m in _MENTION.finditer(body):
        word = fold(m.group(1).rstrip(".'-"))
        hits = [p for p in directory if word in handles[p["email"]]]
        if not hits:
            # Chinese text can follow a name with no space ("@Athish确认", "@李明请确认"): try the name part of the word alone
            first_cjk = _CJK_START.search(word)
            heads = [word[:first_cjk.start()]] if first_cjk and first_cjk.start() > 0 else []
            heads += [word[:i] for i in range(len(word) - 1, 1, -1)] if first_cjk and first_cjk.start() == 0 else []
            for head in heads:
                hits = [p for p in directory if head in handles[p["email"]]]
                if hits:
                    word = head
                    break
        if len(hits) == 1:
            found[hits[0]["email"]] = hits[0]
        elif word and not any(u["word"] == word for u in unresolved):
            unresolved.append({"word": word, "matches": [{"email": h["email"], "name": h["name"]} for h in hits[:5]]})
    return list(found.values()), unresolved


# ------------------------------------------------------------------ items

def link_for(item_type: str, ref: str) -> str:
    """The screen an item lives on, worked out without touching the database (so it works for an item that has since been deleted)."""
    return {"order": "/orders", "rfq": "/rfqs", "vendor_job": "/vendors", "shipment": "/shipments",
            "milestone": f"/projects?milestone={ref}"}.get(item_type, "/boards")


def _resolve_item(item_type: str, ref: str) -> Tuple[str, str, str]:
    """(the item's reference as stored, a short label for alerts, the screen to open). LookupError if it doesn't exist."""
    if item_type not in ITEM_TYPES:
        raise UpdateError(f"Comments can go on: {', '.join(ITEM_TYPES)}.")
    ref = str(ref or "").strip()
    if not ref or len(ref) > 64:
        raise UpdateError("Say which item the comment is about.")
    with SessionLocal() as db:
        if item_type == "order":
            so = ref.removeprefix("SO-")
            o = db.query(OrderDB).filter(OrderDB.sales_order_number.in_([so, f"SO-{so}"])).first()
            if o is None:
                raise LookupError(f"There is no order {ref}.")
            return o.sales_order_number, f"order for PO {o.customer_po_number}", "/orders"
        if item_type == "rfq":
            r = db.query(RfqDB).filter((RfqDB.number == ref.upper()) | (RfqDB.id == (int(ref) if ref.isdigit() else -1))).first()
            if r is None:
                raise LookupError(f"There is no RFQ {ref}.")
            return r.number, f"RFQ {r.number}", "/rfqs"
        if item_type == "vendor_job":
            j = db.get(VendorJobDB, int(ref)) if ref.isdigit() else None
            if j is None:
                raise LookupError(f"There is no vendor job {ref}.")
            return str(j.id), f"{j.vendor} job {j.po_number or j.description}".strip(), "/vendors"
        if item_type == "shipment":
            s = db.get(ShipmentDB, int(ref)) if ref.isdigit() else None
            if s is None:
                raise LookupError(f"There is no shipment {ref}.")
            return str(s.id), f"shipment {s.reference or s.container_no or s.id}", "/shipments"
    if item_type == "milestone":
        return ref, "a project milestone", f"/projects?milestone={ref}"
    return ref, "a board item", "/boards"


# ------------------------------------------------------------------ writing

def _clean(body: str) -> str:
    text = _CTRL.sub("", str(body or "")).replace("\r\n", "\n").strip()
    if not text:
        raise UpdateError("Write something first.")
    if len(text) > MAX_BODY:
        raise UpdateError(f"That's too long (up to {MAX_BODY:,} characters).")
    return text


def _name_of(email: str) -> str:
    return next((p["name"] for p in people() if p["email"] == email.lower()), email)


def _tell(update: ItemUpdateDB, label: str, link: str, people_to_tell: List[str], kind: str, why: str) -> None:
    excerpt = update.body if len(update.body) <= 300 else update.body[:297] + "..."
    for email in people_to_tell:
        notify.notify(kind, f"{kind}:{update.id}:{email}", f"{update.author_name or update.author_email} {why} {label}",
                      excerpt, to=[email], link=link)


def post(item_type: str, item_ref: str, body: str, user: Dict[str, Any], parent_id: Optional[int] = None,
         mentions: Optional[List[str]] = None) -> Dict[str, Any]:
    text = _clean(body)
    ref, label, link = _resolve_item(item_type, item_ref)
    if parent_id is not None:
        try:
            parent_id = int(parent_id)
            if not 1 <= parent_id <= 2_147_483_647:
                raise ValueError
        except (TypeError, ValueError):
            raise UpdateError("That reply isn't attached to a comment I can find.")
    if mentions is not None and (not isinstance(mentions, list) or len(mentions) > 50):
        raise UpdateError("The people to alert must be a short list (up to 50) of email addresses.")
    author = user.get("email", "unknown").lower()
    named, unresolved = resolve_mentions(text, mentions)
    if len(named) > MAX_MENTIONS:
        raise UpdateError(f"Name at most {MAX_MENTIONS} people in one comment.")
    with SessionLocal() as db:
        parent = None
        if parent_id is not None:
            parent = db.get(ItemUpdateDB, int(parent_id))
            if parent is None or parent.item_type != item_type or parent.item_ref != ref or parent.deleted_at:
                raise UpdateError("That comment isn't on this item any more.")
            if parent.parent_id is not None:
                parent_id = parent.parent_id                      # replies stay one level deep
                parent = db.get(ItemUpdateDB, parent_id)
        row = ItemUpdateDB(item_type=item_type, item_ref=ref, parent_id=parent_id, author_email=author, author_name=_name_of(author),
                           body=text, mentions=[p["email"] for p in named])
        db.add(row)
        db.commit()
        db.refresh(row)
        reply_to = parent.author_email if parent is not None else None
    to_tell = [p["email"] for p in named if p["email"] != author]
    _tell(row, label, link, to_tell, "mention", "mentioned you on")
    if reply_to and reply_to != author and reply_to not in to_tell:
        _tell(row, label, link, [reply_to], "reply", "replied to you on")
    audit_log("ITEM_UPDATE_POSTED", "item_update", str(row.id), actor_email=author, actor_role=user.get("role", ""),
              detail=f"{item_type} {ref}; mentions {len(named)}")
    return {**_as_dict(row, author), "unresolved": unresolved}


def edit(update_id: int, body: str, user: Dict[str, Any], mentions: Optional[List[str]] = None) -> Dict[str, Any]:
    text = _clean(body)
    me = user.get("email", "unknown").lower()
    with SessionLocal() as db:
        row = db.get(ItemUpdateDB, update_id)
        if row is None or row.deleted_at:
            raise LookupError("That comment is gone.")
        if row.author_email != me:
            raise PermissionError("Only the person who wrote a comment can change it.")
        named, unresolved = resolve_mentions(text, mentions)
        fresh = [p["email"] for p in named if p["email"] not in (row.mentions or []) and p["email"] != me]
        row.body, row.mentions, row.edited_at = text, [p["email"] for p in named], _now()
        db.commit()
        db.refresh(row)
        item_type, item_ref = row.item_type, row.item_ref
    if fresh:
        _, label, link = _resolve_item(item_type, item_ref)
        _tell(row, label, link, fresh, "mention", "mentioned you on")
    return {**_as_dict(row, me), "unresolved": unresolved}


def delete(update_id: int, user: Dict[str, Any]) -> None:
    me = user.get("email", "unknown").lower()
    with SessionLocal() as db:
        row = db.get(ItemUpdateDB, update_id)
        if row is None or row.deleted_at:
            raise LookupError("That comment is gone.")
        if row.author_email != me and user.get("role") not in ("ADMIN", "MANAGER"):
            raise PermissionError("Only the author or a manager can remove a comment.")
        row.deleted_at = _now()
        db.commit()
    audit_log("ITEM_UPDATE_REMOVED", "item_update", str(update_id), actor_email=me, actor_role=user.get("role", ""))


# ------------------------------------------------------------------ reading

def _as_dict(r: ItemUpdateDB, me: str = "", names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    names = names if names is not None else {p["email"]: p["name"] for p in people()}
    gone = r.deleted_at is not None
    return {"id": r.id, "item_type": r.item_type, "item_ref": r.item_ref, "parent_id": r.parent_id,
            "author_email": r.author_email, "author_name": r.author_name or r.author_email,
            "body": "(removed)" if gone else r.body, "removed": gone,
            "mentions": [] if gone else [{"email": e, "name": names.get(e, e)} for e in (r.mentions or [])],
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "edited_at": r.edited_at.isoformat() if r.edited_at else None, "mine": r.author_email == me.lower()}


def _translate_thread(out: List[Dict[str, Any]], lang: str, me: str, budget: int = 8) -> None:
    """Add `author_language`, and for comments written in another language than the reader's, `translation` (or
    `needs_translation` once this read has spent its budget of new engine calls; open the thread again for more)."""
    from app.services import bridge

    flat = [c for top in out for c in [top] + top.get("replies", [])]
    langs = bridge.languages_of([c["author_email"] for c in flat])
    for c in flat:
        c["author_language"] = langs.get(c["author_email"].lower(), "en")
        c["translation"], c["needs_translation"] = None, False
        if c["removed"] or c["author_language"] == lang or not c["body"].strip():
            continue
        r = bridge.translate(c["body"], c["author_language"], lang, me or "system") if budget > 0 else None
        if r is not None:
            c["translation"] = {"text": r["text"], "unsure": r["unsure"], "to": lang}
            budget -= 0 if r["cached"] else 1
        else:
            c["needs_translation"] = True
            budget -= 1                                    # a busy engine is not asked again for every comment


def _notes_for(item_type: str, ref: str, me: str, lang: Optional[str]) -> List[Dict[str, Any]]:
    """Management's engineering notes on this job, shaped like comments so one thread shows both."""
    from app.services.coordination import eng_notes

    item_id = ref
    if item_type == "vendor_job":
        with SessionLocal() as db:
            j = db.get(VendorJobDB, int(ref)) if ref.isdigit() else None
            item_id = j.monday_item_id if j and j.monday_item_id else ""
    elif item_type not in ("board_item", "milestone"):
        return []
    if not item_id:
        return []
    names = {p["email"]: p["name"] for p in people()}
    out = []
    for n in eng_notes.list_notes(item_id=item_id):
        tr = ((n.get("translations") or {}).get(lang or "") or {}).get("text") if lang and lang != "en" else None
        out.append({"kind": "note", "id": f"note-{n['id']}", "note_id": n["id"], "item_type": item_type, "item_ref": ref,
                    "parent_id": None, "author_email": n["author"], "author_name": names.get(n["author"], n["author"]),
                    "body": n["text"], "removed": False, "mentions": [{"email": e, "name": names.get(e, e)} for e in n["recipients"]],
                    "created_at": n["created_at"], "edited_at": None, "mine": n["author"].lower() == me.lower(),
                    "urgent": n["urgent"], "unread": me.lower() in [u.lower() for u in n["unread"]], "voice": n["voice"],
                    "author_language": "en", "translation": {"text": tr, "unsure": [], "to": lang} if tr else None,
                    "needs_translation": False, "replies": []})
    return out


def thread(item_type: str, item_ref: str, me: str = "", lang: Optional[str] = None, include_notes: bool = True) -> List[Dict[str, Any]]:
    """Oldest first, each top-level comment with its replies. A removed comment that has replies stays as '(removed)'.
    With `lang`, comments written in another language come with a translation into it."""
    ref, _, _ = _resolve_item(item_type, item_ref)
    with SessionLocal() as db:
        rows = db.query(ItemUpdateDB).filter(ItemUpdateDB.item_type == item_type, ItemUpdateDB.item_ref == ref) \
            .order_by(ItemUpdateDB.created_at, ItemUpdateDB.id).all()
    names = {p["email"]: p["name"] for p in people()}
    from app.services.coordination import files as _files

    attached = _files.for_thread(item_type, ref)
    replies: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        if r.parent_id is not None and not r.deleted_at:
            replies.setdefault(r.parent_id, []).append({**_as_dict(r, me, names), "files": attached.get(r.id, [])})
    out = []
    for r in rows:
        if r.parent_id is not None:
            continue
        kids = replies.get(r.id, [])
        if r.deleted_at and not kids:
            continue
        out.append({**_as_dict(r, me, names), "files": attached.get(r.id, []) if not r.deleted_at else [], "replies": kids})
    if lang:
        _translate_thread(out, lang, me)
    for c in out:
        c.setdefault("kind", "comment")
        for r in c.get("replies", []):
            r.setdefault("kind", "comment")
    if include_notes:
        try:
            out = sorted(out + _notes_for(item_type, ref, me, lang), key=lambda c: c["created_at"] or "")
        except Exception as exc:  # noqa: BLE001 - the comments still show if the notes can't be read
            from app.core.logging import logger

            logger.warning(f"Could not add engineering notes to the thread: {exc}")
    return out


def mentions_of(email: str, days: int = 30, limit: int = 100) -> List[Dict[str, Any]]:
    """Comments that name this person, newest first: their 'you were mentioned' list."""
    email = email.lower()
    since = _now() - timedelta(days=days)
    with SessionLocal() as db:
        rows = db.query(ItemUpdateDB).filter(ItemUpdateDB.created_at >= since, ItemUpdateDB.deleted_at.is_(None)) \
            .order_by(ItemUpdateDB.created_at.desc()).limit(2000).all()
    out = [_as_dict(r, email) for r in rows if email in (r.mentions or []) and r.author_email != email]
    return out[:limit]


def counts(item_type: str, refs: List[str]) -> Dict[str, int]:
    """How many live comments each item has, for a badge on a list."""
    if item_type not in ITEM_TYPES:
        raise UpdateError(f"Comments can go on: {', '.join(ITEM_TYPES)}.")
    refs = [str(r).removeprefix("SO-") if item_type == "order" else str(r) for r in refs[:200]]
    with SessionLocal() as db:
        rows = db.query(ItemUpdateDB.item_ref).filter(ItemUpdateDB.item_type == item_type, ItemUpdateDB.item_ref.in_(refs),
                                                      ItemUpdateDB.deleted_at.is_(None)).all()
    out: Dict[str, int] = {}
    for (r,) in rows:
        out[r] = out.get(r, 0) + 1
    return out
