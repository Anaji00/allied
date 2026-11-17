"""Project templates: a new job starts with its milestones, owners and forms already laid out.

A template is a list of milestones, each due a number of working days after the start, each with a default owner
role and (optionally) the task form engineers fill in for it. Managers keep the templates in the app (a small file
in the data folder). Starting a job from a template gives the dated plan to review, and an Excel file that monday can
import: the app does not create anything in monday (it is read-only here). The starter template is an example with
guessed durations: edit it to match how Allied really runs a job.
"""

import json
import os
import re
import tempfile
import threading
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.coordination import workdays

ROLES = ("engineer", "manager", "sales", "operations", "vendor", "customer")
_lock = threading.Lock()

_STARTER = [{
    "id": "wear-part-job",
    "name": "Wear part: design to delivery",
    "milestones": [
        {"name": "Customer kickoff", "offset_days": 0, "owner_role": "sales", "form": ""},
        {"name": "Design drawings", "offset_days": 5, "owner_role": "engineer", "form": "design-release"},
        {"name": "Customer drawing approval", "offset_days": 10, "owner_role": "sales", "form": ""},
        {"name": "Material and MTR check", "offset_days": 12, "owner_role": "engineer", "form": ""},
        {"name": "Vendor PO released", "offset_days": 14, "owner_role": "operations", "form": ""},
        {"name": "Manufacturing in China", "offset_days": 45, "owner_role": "vendor", "form": ""},
        {"name": "Pre-shipment photos and inspection", "offset_days": 50, "owner_role": "engineer", "form": ""},
        {"name": "Freight booked", "offset_days": 52, "owner_role": "operations", "form": ""},
        {"name": "Delivered to customer", "offset_days": 75, "owner_role": "operations", "form": ""},
    ],
}]


class TemplateError(ValueError):
    """A template isn't valid; the message says what to fix."""


def _path() -> Path:
    return Path(settings.DATA_DIR) / "project_templates.json"


def load() -> List[Dict[str, Any]]:
    try:
        p = _path()
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list) and all(isinstance(t, dict) and t.get("id") for t in data):
                return data
    except (OSError, ValueError):
        pass
    return json.loads(json.dumps(_STARTER))


def _write(items: List[Dict[str, Any]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".project_templates.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get(template_id: str) -> Dict[str, Any]:
    for t in load():
        if t["id"] == template_id:
            return t
    raise LookupError(f"No project template called '{template_id}'.")


def _check(t: Dict[str, Any]) -> Dict[str, Any]:
    name = str(t.get("name") or "").strip()
    if not name or len(name) > 80:
        raise TemplateError("Give the template a name (up to 80 characters).")
    ms = t.get("milestones")
    if not isinstance(ms, list) or not ms or len(ms) > 60:
        raise TemplateError("A template needs between 1 and 60 milestones.")
    out, last = [], -1
    from app.services.coordination import forms

    known_forms = {f["id"] for f in forms.load()}
    for m in ms:
        if not isinstance(m, dict):
            raise TemplateError("Every milestone needs a name.")
        mname = str(m.get("name") or "").strip()
        if not mname or len(mname) > 120:
            raise TemplateError("Every milestone needs a name.")
        try:
            off = int(m.get("offset_days"))
        except (TypeError, ValueError):
            raise TemplateError(f"'{mname}': days after the start must be a whole number.")
        if not 0 <= off <= 730:
            raise TemplateError(f"'{mname}': days after the start must be between 0 and 730.")
        if off < last:
            raise TemplateError(f"'{mname}' is due before the milestone above it. Put them in order.")
        last = off
        role = m.get("owner_role") or "engineer"
        if role not in ROLES:
            raise TemplateError(f"'{mname}': owner must be one of {', '.join(ROLES)}.")
        form = str(m.get("form") or "")
        if form and form not in known_forms:
            raise TemplateError(f"'{mname}': there is no form called '{form}'.")
        out.append({"name": mname, "offset_days": off, "owner_role": role, "form": form})
    return {"id": t.get("id"), "name": name, "milestones": out}


def save(t: Dict[str, Any]) -> Dict[str, Any]:
    checked = _check(t)
    with _lock:
        items = load()
        if checked["id"]:
            for i, x in enumerate(items):
                if x["id"] == checked["id"]:
                    items[i] = checked
                    _write(items)
                    return checked
            raise LookupError(f"No project template called '{checked['id']}'.")
        base = re.sub(r"[^a-z0-9]+", "-", checked["name"].lower()).strip("-")[:40] or "template"
        taken, new, n = {x["id"] for x in items}, base, 2
        while new in taken:
            new, n = f"{base}-{n}", n + 1
        checked["id"] = new
        items.append(checked)
        _write(items)
        return checked


def remove(template_id: str) -> None:
    with _lock:
        items = load()
        kept = [x for x in items if x["id"] != template_id]
        if len(kept) == len(items):
            raise LookupError(f"No project template called '{template_id}'.")
        _write(kept)


def plan(template_id: str, start: str, project_name: str = "", owners: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """The dated milestones for a job starting on `start` (working days, holidays skipped), with owners filled in by role."""
    t = get(template_id)
    try:
        day = date.fromisoformat(start)
    except (TypeError, ValueError):
        raise TemplateError("The start date must look like 2026-11-20.")
    owners = owners if isinstance(owners, dict) else {}
    start_day = day if workdays.is_workday(day) else workdays.workdays_after(day, 1)       # a weekend start begins on Monday
    out = []
    for m in t["milestones"]:
        due = start_day if m["offset_days"] == 0 else workdays.workdays_after(start_day, m["offset_days"])
        out.append({"project": project_name or t["name"], "milestone": m["name"], "due": due.isoformat(),
                    "owner_role": m["owner_role"], "owner": owners.get(m["owner_role"], ""), "form": m["form"]})
    return out
