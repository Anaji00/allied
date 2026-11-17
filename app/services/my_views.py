"""My items, and saved filters.

`my_items` gathers what is waiting on one person across the app in one call: items where they hold the ball or own
the item, comments that name them, engineering notes they haven't read, RFQs and vendor jobs they own, and drafts
waiting for their OK. Saved filters are views a person keeps (a name, the screen, and the filter settings the screen
understands; stored as given), plus views a manager shares with everyone. The app's own record; nothing is sent anywhere.
"""

import json
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.logging import logger

SCREENS = ("orders", "rfqs", "vendors", "shipments", "boards", "projects", "ball", "inbox", "packing-slips")
MAX_PER_PERSON, MAX_BYTES = 30, 4000
_lock = threading.Lock()


class ViewError(ValueError):
    """Something a person can read about why a view can't be saved."""


def my_items(email: str) -> Dict[str, Any]:
    """Everything waiting on this person, each part safe to fail on its own so one broken source never blanks the page."""
    email = email.lower()
    out: Dict[str, Any] = {"ball": [], "mentions": [], "notes": [], "rfqs": [], "vendor_jobs": [], "drafts_waiting": 0}

    def part(name, fn):
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"My items: {name} unavailable: {exc}")

    from app.services.coordination import autopilot, ball, eng_notes, rfqs, updates, vendors

    part("ball", lambda: ball.list_open("me", email))
    part("mentions", lambda: updates.mentions_of(email, 14, 30))
    part("notes", lambda: [{**n, "body": n["text"]} for n in eng_notes.list_notes(for_email=email) if email in [u.lower() for u in n["unread"]]])
    part("rfqs", lambda: [{"number": r["number"], "customer": r["customer"], "subject": r.get("subject") or "", "state": r["state"],
                           "due": r.get("effective_due_at")}
                          for r in rfqs.list_rfqs(open_only=True, owner=email)])
    part("vendor_jobs", lambda: [{"id": j["id"], "vendor": j["vendor"], "po": j.get("po_number"), "po_number": j.get("po_number"),
                                  "description": j.get("description") or "", "state": j["state"],
                                  "reason": (j.get("reasons") or [""])[0]}
                                 for j in vendors.overview()["jobs"] if (j.get("owner_email") or "").lower() == email])
    part("drafts_waiting", lambda: autopilot.waiting_for(email))
    out["counts"] = {k: (len(v) if isinstance(v, list) else v) for k, v in out.items() if k != "counts"}
    return out


# ------------------------------------------------------------------------ saved filters

def _path() -> Path:
    return Path(settings.DATA_DIR) / "saved_views.json"


def _load() -> List[Dict[str, Any]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(items: List[Dict[str, Any]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".saved_views.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def list_views(email: str, screen: Optional[str] = None) -> List[Dict[str, Any]]:
    """Mine and the shared ones, for one screen (or all)."""
    email = email.lower()
    rows = [v for v in _load() if (v["owner"] == email or v.get("shared")) and (not screen or v["screen"] == screen)]
    return [{**v, "mine": v["owner"] == email} for v in sorted(rows, key=lambda v: (not v.get("shared"), v["name"].lower()))]


def save_view(email: str, name: str, screen: str, filters: Any, shared: bool = False, is_manager: bool = False) -> Dict[str, Any]:
    email = email.lower()
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not name or len(name) > 60:
        raise ViewError("Give the view a name (up to 60 characters).")
    if screen not in SCREENS:
        raise ViewError(f"Views can be saved for: {', '.join(SCREENS)}.")
    if not isinstance(filters, dict) or len(json.dumps(filters)) > MAX_BYTES:
        raise ViewError("The filter settings are missing or too large.")
    if shared and not is_manager:
        raise ViewError("Only a manager can share a view with everyone.")
    with _lock:
        items = _load()
        mine = [v for v in items if v["owner"] == email]
        same = next((v for v in mine if v["screen"] == screen and v["name"].lower() == name.lower()), None)
        if same is None and len(mine) >= MAX_PER_PERSON:
            raise ViewError(f"You can keep {MAX_PER_PERSON} views. Remove one first.")
        view = {"id": same["id"] if same else uuid.uuid4().hex[:10], "name": name, "screen": screen, "filters": filters,
                "owner": email, "shared": bool(shared)}
        items = [v for v in items if v["id"] != view["id"]] + [view]
        _write(items)
    return {**view, "mine": True}


def delete_view(email: str, view_id: str, is_manager: bool = False) -> None:
    with _lock:
        items = _load()
        v = next((x for x in items if x["id"] == view_id), None)
        if v is None:
            raise LookupError("That view is gone.")
        if v["owner"] != email.lower() and not is_manager:
            raise PermissionError("Only the person who saved a view can remove it.")
        _write([x for x in items if x["id"] != view_id])
