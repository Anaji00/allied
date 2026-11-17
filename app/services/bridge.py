"""The language bridge: everyone reads the app in their own language (English, Mandarin or Vietnamese).

Each person has a language setting. A comment written by someone who works in Mandarin shows to an English reader
in English, and the reply comes back to them in Mandarin. Translation uses the same engine and engineering glossary
as the Translate tool; results are kept in a small file so the same sentence is never translated twice (it also keeps
the free AI quota for real work). A failed translation never blocks anything: the original text is shown.

What it will not do: it can't promise a translation is right. Terms it is unsure of are returned in `unsure` so the
screen can mark them, and the original is always one tap away.
"""

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import UserDB

LANGS = {"en": "English", "zh": "中文 (Mandarin)", "vi": "Tiếng Việt (Vietnamese)"}
_lock = threading.Lock()
_MAX_CACHE = 4000
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s*(.*)$")


def _path() -> Path:
    return Path(settings.DATA_DIR) / "translation_cache.json"


def _load() -> Dict[str, Any]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(cache: Dict[str, Any]) -> None:
    if len(cache) > _MAX_CACHE:
        for k in list(cache)[: len(cache) - _MAX_CACHE]:       # oldest first (dicts keep insertion order)
            cache.pop(k, None)
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".translation_cache.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _key(text: str, source: str, target: str) -> str:
    return hashlib.sha1(f"{source}>{target}|{text}".encode("utf-8")).hexdigest()


def language_of(email: str) -> str:
    """The language this person works in (English if they have no account or haven't chosen)."""
    with SessionLocal() as db:
        u = db.query(UserDB).filter(UserDB.email == (email or "").lower()).first()
        return u.language if u and u.language in LANGS else "en"


def languages_of(emails: List[str]) -> Dict[str, str]:
    emails = [e.lower() for e in emails if e]
    with SessionLocal() as db:
        found = {u.email.lower(): u.language for u in db.query(UserDB).filter(UserDB.email.in_(emails))} if emails else {}
    return {e: (found.get(e) if found.get(e) in LANGS else "en") for e in emails}


def set_language(email: str, language: str) -> str:
    if language not in LANGS:
        raise ValueError(f"Choose one of: {', '.join(f'{k} ({v})' for k, v in LANGS.items())}.")
    with SessionLocal() as db:
        u = db.query(UserDB).filter(UserDB.email == email.lower()).first()
        if u is None:
            raise LookupError("There is no account for that person, so there is nowhere to save a language.")
        u.language = language
        db.commit()
    return language


def _ask(text: str, source: str, target: str, actor: str):
    from app.services.ai import features

    result, _ = features.translate(text, target, source, actor=actor)
    return result


def translate(text: str, source: str, target: str, actor: str = "system") -> Optional[Dict[str, Any]]:
    """{text, unsure, source, target, cached} or None when it can't be translated (same language, empty, or the AI is busy)."""
    text = (text or "").strip()
    if not text or source == target or source not in LANGS or target not in LANGS:
        return None
    k = _key(text, source, target)
    with _lock:
        hit = _load().get(k)
    if hit:
        return {**hit, "source": source, "target": target, "cached": True}
    try:
        r = _ask(text[:4000], source, target, actor)
    except Exception as exc:  # noqa: BLE001 - the original text still shows
        logger.warning(f"Translation {source}->{target} unavailable ({type(exc).__name__}).")
        return None
    out = {"text": (r.translation or "").strip(), "unsure": list(r.unsure_terms or [])}
    if not out["text"]:
        return None                                                  # an empty answer is a failure, not a translation
    with _lock:
        cache = _load()
        cache[k] = out
        _save(cache)
    return {**out, "source": source, "target": target, "cached": False}


def translate_many(texts: List[str], source: str, target: str, actor: str = "system") -> List[Optional[str]]:
    """Several short strings in one engine call (a form's labels). Anything that can't be matched back falls back
    to one call each; a string that can't be translated comes back as None."""
    clean = [(t or "").strip() for t in texts]
    out: List[Optional[str]] = [None] * len(clean)
    todo = [i for i, t in enumerate(clean) if t and source != target]
    if not todo:
        return out
    cache = _load()
    pending = []
    for i in todo:
        hit = cache.get(_key(clean[i], source, target))
        if hit:
            out[i] = hit["text"]
        else:
            pending.append(i)
    if len(pending) > 1:
        block = "\n".join(f"{n}. {clean[i].replace(chr(10), ' ')}" for n, i in enumerate(pending, 1))
        try:
            lines = {int(m.group(1)): m.group(2).strip() for l in _ask(block, source, target, actor).translation.splitlines()
                     if (m := _NUMBERED.match(l))}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Batch translation unavailable ({type(exc).__name__}).")
            return out
        if set(lines) == set(range(1, len(pending) + 1)) and all(lines.values()):
            with _lock:
                cache = _load()
                for n, i in enumerate(pending, 1):
                    out[i] = lines[n]
                    cache[_key(clean[i], source, target)] = {"text": lines[n], "unsure": []}
                _save(cache)
            return out
    for i in pending:                                           # one at a time: slower, but each is exact
        r = translate(clean[i], source, target, actor)
        out[i] = r["text"] if r else None
    return out
