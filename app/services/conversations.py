"""Conversations: one list of threads, one per item, gathering what was said about it (comments, engineering notes), the drafts waiting about it and who it is waiting on, with how many
messages are unread for the signed-in person. "Read" is kept per person in a small file next to the app's other saved settings (no database change): marking a thread read records the time,
and anything written by someone else after that is unread. A person who has never opened a thread sees the last two weeks of other people's messages as unread.
"""

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.logging import logger

_lock = threading.RLock()
FIRST_LOOK_DAYS = 14


def _path() -> Path:
    return Path(settings.DATA_DIR) / "conversation_reads.json"


def _load() -> Dict[str, Dict[str, str]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: Dict[str, Dict[str, str]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".conversation_reads.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _aware(d: Any) -> Optional[datetime]:
    if d is None:
        return None
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d)
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _key(item_type: str, item_ref: str) -> str:
    return f"{item_type}:{item_ref}"


def _link(item_type: str, ref: str) -> str:
    return {"rfq": f"/orders?kind=rfq&ref={ref}", "order": f"/orders?kind=so&ref={ref}", "vendor_job": "/vendors", "shipment": f"/jobs?shipment={ref}", "board_item": "/boards", "milestone": f"/projects?milestone={ref}"}.get(item_type, "/")


def listing(user: Dict[str, Any], only_unread: bool = False, limit: int = 100) -> Dict[str, Any]:
    """Threads, newest first: {total, unread_total, items:[{key, item_type, item_ref, title, link, job_ref, last_at, last_by, last_sentence, messages, unread, waits_on, drafts_waiting}]}."""
    from app.core.database import SessionLocal
    from app.models.db_models import AssistantActionDB, EngineeringNoteDB, ItemBallDB, ItemUpdateDB
    from app.services import job_links
    from app.services.coordination import updates

    me = (user.get("email") or "").strip().lower()
    role = user.get("role") or "VIEWER"
    staff = role in ("ADMIN", "MANAGER", "OPERATOR")                                  # a viewer sees that a thread exists and who it is about, never what was said
    reads = _load().get(me, {})
    horizon = datetime.now(timezone.utc) - timedelta(days=FIRST_LOOK_DAYS)
    threads: Dict[str, Dict[str, Any]] = {}

    def thread(item_type: str, ref: str) -> Dict[str, Any]:
        return threads.setdefault(_key(item_type, ref), {"key": _key(item_type, ref), "item_type": item_type, "item_ref": ref, "title": ref, "link": _link(item_type, ref), "job_ref": None, "last_at": None,
                                                         "last_by": "", "last_sentence": "", "messages": 0, "unread": 0, "waits_on": None, "drafts_waiting": 0})

    def touch(t: Dict[str, Any], at: Any, by: str, sentence: str) -> None:
        a = _aware(at)
        if a and (not t["last_at"] or a > _aware(t["last_at"])):
            t["last_at"], t["last_by"], t["last_sentence"] = a.isoformat(), by, " ".join(sentence.split())[:200]

    names = {p["email"]: p["name"] for p in updates.people()}
    with SessionLocal() as db:
        for r in db.query(ItemUpdateDB).filter(ItemUpdateDB.deleted_at.is_(None)).order_by(ItemUpdateDB.id.desc()).limit(3000):
            t = thread(r.item_type, r.item_ref)
            t["messages"] += 1
            touch(t, r.created_at, names.get(r.author_email, r.author_name or r.author_email) if staff else "", r.body if staff else "")
            seen = _aware(reads.get(_key(r.item_type, r.item_ref)))
            made = _aware(r.created_at)
            if r.author_email != me and made and made >= (seen or horizon):
                t["unread"] += 1
        for n in db.query(EngineeringNoteDB).order_by(EngineeringNoteDB.id.desc()).limit(500):
            if not staff or not (role == "ADMIN" or me in (n.recipients or []) or (n.author_email or "").lower() == me):          # a note is for the people it was sent to (and its author)
                continue
            t = thread("board_item", str(n.item_id))
            t["title"] = n.item_name or t["title"]
            t["messages"] += 1
            touch(t, n.created_at, names.get(n.author_email, n.author_email), f"Engineering note: {n.text}")
            if me in (n.recipients or []) and me not in (n.read_by or {}):
                t["unread"] += 1
        for b in db.query(ItemBallDB).filter(ItemBallDB.resolved_at.is_(None)).limit(1000):
            t = thread(b.item_type, b.item_ref)
            if not staff:
                continue
            t["waits_on"] = {"kind": b.ball_with, "name": names.get(str(b.ball_person or "").lower(), b.ball_person or b.ball_with), "note": b.note or "", "since": _aware(b.ball_since).isoformat() if b.ball_since else None}
            if b.history:
                last = b.history[-1]
                touch(t, last.get("at"), names.get(str(last.get("by") or ""), str(last.get("by") or "")), f"Ball: {last.get('to') or ''}" + (f" ({last['note']})" if last.get("note") else ""))
        drafts = [d for d in db.query(AssistantActionDB).filter(AssistantActionDB.status == "PENDING").order_by(AssistantActionDB.created_at.desc()).limit(300)]
    from app.services.assistant import agent

    visible = {d["id"] for d in agent.pending(user)}
    for d in drafts:
        origin = ((d.args or {}).get("_origin") or {}).get("thread")
        if d.id in visible and origin:
            t = thread(origin[0], str(origin[1]))
            t["drafts_waiting"] += 1
            touch(t, d.created_at, "Draft", f"Draft waiting: {d.summary.splitlines()[0]}")
    rfq_by_number, so_links = {}, job_links.maps()["so"]
    try:
        from app.models.db_models import JobDB, RfqDB

        with SessionLocal() as db:
            rfq_by_number = {n: j for n, j in db.query(RfqDB.number, JobDB.ref).join(JobDB, JobDB.rfq_id == RfqDB.id)}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Conversations: job refs unavailable: {type(exc).__name__}")
    out = []
    for t in threads.values():
        try:
            if t["title"] == t["item_ref"]:
                t["title"] = updates._resolve_item(t["item_type"], t["item_ref"])[1]
        except Exception:  # noqa: BLE001 - a thread whose item has gone still lists, under its reference
            pass
        t["job_ref"] = rfq_by_number.get(t["item_ref"]) if t["item_type"] == "rfq" else (so_links.get(str(t["item_ref"]).removeprefix("SO-"), (None, None))[1] if t["item_type"] == "order" else None)
        if t["last_at"] and (not only_unread or t["unread"]):
            out.append(t)
    out.sort(key=lambda t: t["last_at"], reverse=True)
    unread_total = sum(t["unread"] for t in out)
    return {"total": len(out), "unread_total": unread_total, "unread_threads": sum(1 for t in out if t["unread"]), "items": out[:max(1, min(int(limit), 300))]}


def mark_read(user: Dict[str, Any], item_type: str, item_ref: str) -> Dict[str, Any]:
    """Marks one thread read for this person (now), and the engineering notes in it that were sent to them."""
    from app.services.coordination import eng_notes, updates

    me = (user.get("email") or "").strip().lower()
    ref, _label, _link2 = updates._resolve_item(item_type, str(item_ref))
    with _lock:
        data = _load()
        data.setdefault(me, {})[_key(item_type, ref)] = datetime.now(timezone.utc).isoformat()
        _write(data)
    if item_type == "board_item":
        for n in eng_notes.list_notes(item_id=ref, for_email=me):
            if me in n["unread"]:
                try:
                    eng_notes.mark_read(n["id"], user)
                except (LookupError, PermissionError):
                    pass
    return {"key": _key(item_type, ref), "read": True}


def mark_all_read(user: Dict[str, Any]) -> Dict[str, Any]:
    n = 0
    for t in listing(user, only_unread=True, limit=300)["items"]:
        try:
            mark_read(user, t["item_type"], t["item_ref"])
            n += 1
        except (LookupError, ValueError):
            continue
    return {"threads_marked_read": n}
