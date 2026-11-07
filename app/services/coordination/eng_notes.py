"""Notes from management to the engineers on one engineering job.

A manager writes the note in English on a job (a monday item: a drawing job on the
Engineering board, or any board item). The engineers on that job (its owners on the board,
or the people the manager picks) are told in the app and by email; an urgent note also goes
as a text and a phone push, at any hour. Mandarin and Vietnamese translations are optional:
the ones asked for are made with Allied's welding glossary and attached under the English,
which stays the version that counts. Engineers mark a note read, so management can see who
has seen it. Nothing is written to monday.
"""

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import EngineeringNoteDB, MondayMilestoneDB
from app.services.coordination import notify as alerts

LANGS = {"zh": "中文 (Mandarin)", "vi": "Tiếng Việt (Vietnamese)"}
# What phones and browsers record: Chrome/Android webm or ogg, iPhone Safari mp4/m4a, uploads mp3/wav.
AUDIO_TYPES = {".webm": "audio/webm", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
               ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".mp3": "audio/mpeg", ".mpeg": "audio/mpeg",
               ".wav": "audio/wav", ".aac": "audio/aac", ".3gp": "audio/3gpp", ".amr": "audio/amr", ".caf": "audio/x-caf"}
MAX_AUDIO_BYTES = 25 * 1024 * 1024    # Groq's speech-to-text limit
# Two engineers marking one note read at the same moment must both be recorded.
_read_lock = threading.Lock()


def _job(item_id: str) -> Dict[str, Any]:
    """The job's name, board and engineers: from the Engineering board copy, else any board read."""
    with SessionLocal() as db:
        m = db.get(MondayMilestoneDB, str(item_id))
        if m is not None:
            return {"name": m.name, "board_id": m.board_id, "engineers": list(m.owner_emails or [])}
    from app.services.coordination import boards

    with boards._lock:   # the raw board copy: no need to build every board's full view to find one item
        for b in boards._mirror.values():
            row = next((r for r in b["items"] if r["id"] == str(item_id)), None)
            if row is not None:
                return {"name": row["name"], "board_id": b["board_id"], "engineers": []}
    raise LookupError(f"No job {item_id} on the boards read so far.")


def _translate(text: str, lang: str, author: str) -> Dict[str, Any]:
    try:
        from app.services.ai import features

        out, _ = features.translate(text, lang, "en", actor=author)
        return {"text": out.translation, "unsure": list(out.unsure_terms or [])}
    except Exception as exc:  # noqa: BLE001 - the English note still goes; the gap is said plainly
        logger.warning(f"Engineering note translation to {lang} failed: {type(exc).__name__}")
        return {"text": None, "error": "Translation unavailable just now; the English note was sent."}


def _as_dict(n: EngineeringNoteDB) -> Dict[str, Any]:
    audio = n.audio or None
    return {"id": n.id, "board_id": n.board_id, "item_id": n.item_id, "item_name": n.item_name, "author": n.author_email,
            "text": n.text, "translations": n.translations or {}, "recipients": n.recipients or [], "urgent": n.urgent,
            "read_by": n.read_by or {}, "unread": [r for r in (n.recipients or []) if r not in (n.read_by or {})],
            "voice": ({"url": f"/api/v1/engineering-notes/{n.id}/audio", "mime": audio.get("mime"),
                       "seconds": audio.get("seconds"), "transcript": n.transcript} if audio else None),
            "created_at": n.created_at.isoformat() if n.created_at else None}


def _body(note: Dict[str, Any]) -> str:
    parts = [note["text"]]
    if note.get("voice"):
        parts.append("A voice note is attached: open the job to listen to it."
                     + ("" if note["voice"].get("transcript") else " It couldn't be written out, so please play it."))
    for lang, t in (note["translations"] or {}).items():
        if t.get("text"):
            parts.append(f"{LANGS.get(lang, lang)}:\n{t['text']}")
    parts.append("The English note is the one that counts. Open the job to mark it read.")
    return "\n\n".join(parts)


def check_audio(data: bytes, filename: str) -> str:
    """The recording's extension, if it is one phones and browsers make and within the size limit."""
    from app.services.ai import speech

    suffix = speech.safe_suffix(filename, "")
    if suffix not in AUDIO_TYPES:
        raise ValueError(f"Record or upload a voice file ({', '.join(sorted(t.lstrip('.') for t in AUDIO_TYPES))}).")
    if not data:
        raise ValueError("The recording is empty.")
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError("Keep voice notes under 25 MB (about 20 minutes).")
    return suffix


def post_voice(item_id: str, audio: bytes, filename: str, user: Dict[str, Any], text: str = "",
               languages: Optional[List[str]] = None, urgent: bool = False, to: Optional[List[str]] = None) -> Dict[str, Any]:
    """A spoken note: written out by speech-to-text, kept so everyone can play it on phone or desktop.
    The written-out words are the note (translated if asked), unless text is typed as well."""
    suffix = check_audio(audio, filename)
    transcript, seconds = None, None
    try:
        from app.services.ai import speech

        heard = speech.transcribe(audio, suffix=suffix)
        transcript, seconds = (heard.get("text") or "").strip() or None, heard.get("duration_seconds")
    except Exception as exc:  # noqa: BLE001 - the recording still goes; people can play it
        logger.warning(f"Voice note could not be written out: {type(exc).__name__}")
    note_text = (text or "").strip() or transcript or "Voice note: listen to it on the job."
    return post(item_id, note_text, user, languages, urgent, to,
                voice={"bytes": audio, "suffix": suffix, "seconds": seconds, "transcript": transcript})


def audio_path(note_id: int) -> tuple:
    """(file path, media type) of a note's recording."""
    with SessionLocal() as db:
        row = db.get(EngineeringNoteDB, note_id)
        if row is None or not row.audio:
            raise LookupError("No voice note here.")
        audio = dict(row.audio)
    path = _voice_dir(note_id) / audio["file"]
    if not path.is_file():
        raise LookupError("The recording is no longer on file.")
    return path, audio.get("mime") or "application/octet-stream"


def _voice_dir(note_id: int):
    from app.config import settings

    return settings.DB_DIR / "voice" / str(int(note_id))


def post(item_id: str, text: str, user: Dict[str, Any], languages: Optional[List[str]] = None,
         urgent: bool = False, to: Optional[List[str]] = None, voice: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Write the note.")
    if len(text) > 4000:
        raise ValueError("Keep the note under 4,000 characters.")
    bad = [lang for lang in languages or [] if lang not in LANGS]
    if bad:
        raise ValueError(f"Translations offered: {', '.join(LANGS)} (Mandarin, Vietnamese).")
    job = _job(item_id)
    recipients = list(dict.fromkeys(e.strip().lower() for e in (to or job["engineers"]) if e and "@" in e))
    if not recipients:
        raise ValueError("Nobody is assigned to this job on the board; choose who should get the note.")
    author = user.get("email", "unknown")
    translations = {lang: _translate(text, lang, author) for lang in dict.fromkeys(languages or [])}
    with SessionLocal() as db:
        row = EngineeringNoteDB(board_id=job["board_id"], item_id=str(item_id), item_name=job["name"], author_email=author,
                                text=text, translations=translations, recipients=recipients, urgent=bool(urgent), read_by={})
        db.add(row)
        db.commit()
        db.refresh(row)
        if voice:
            folder = _voice_dir(row.id)
            folder.mkdir(parents=True, exist_ok=True)
            name = f"note{voice['suffix']}"                    # our own name, never the client's
            (folder / name).write_bytes(voice["bytes"])
            row.audio = {"file": name, "mime": AUDIO_TYPES[voice["suffix"]], "size": len(voice["bytes"]),
                         "seconds": voice.get("seconds")}
            row.transcript = voice.get("transcript")
            db.commit()
            db.refresh(row)
        note = _as_dict(row)
    # Urgent: critical, which also texts and pushes, quiet hours or not. Otherwise a normal note.
    alerts.notify("engineering_note", f"eng-note:{note['id']}:",
                  f"{'URGENT note' if urgent else 'Note'} from {author.split('@')[0].title()}: {job['name']}"[:300],
                  _body(note), severity="critical" if urgent else "warning", to=recipients,
                  # Boards is the home page; naming the board opens the one the job is on.
                  link=f"/?board={job['board_id']}&item={item_id}" if job.get("board_id") else f"/?item={item_id}")
    return note


def list_notes(item_id: Optional[str] = None, board_id: Optional[str] = None, for_email: Optional[str] = None,
               days: int = 90) -> List[Dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with SessionLocal() as db:
        q = db.query(EngineeringNoteDB).filter(EngineeringNoteDB.created_at >= since)
        if item_id:
            q = q.filter(EngineeringNoteDB.item_id == str(item_id))
        if board_id:
            q = q.filter(EngineeringNoteDB.board_id == str(board_id))
        rows = [_as_dict(n) for n in q.order_by(EngineeringNoteDB.created_at.desc()).limit(500)]
    if for_email:
        rows = [n for n in rows if for_email.lower() in n["recipients"]]
    return rows


def mark_read(note_id: int, user: Dict[str, Any]) -> Dict[str, Any]:
    email = (user.get("email") or "").lower()
    with _read_lock, SessionLocal() as db:
        row = db.get(EngineeringNoteDB, note_id)
        if row is None:
            raise LookupError("No such note.")
        if email not in (row.recipients or []):
            raise PermissionError("This note wasn't sent to you.")
        read = dict(row.read_by or {})
        read.setdefault(email, datetime.now(timezone.utc).isoformat())
        row.read_by = read
        db.commit()
        db.refresh(row)
        out = _as_dict(row)
    if not out["unread"]:
        alerts.resolve([f"eng-note:{note_id}:"])   # everyone has read it; the ":" keeps note 1 from closing note 12
    return out
