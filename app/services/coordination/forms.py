"""Task forms: a template per kind of task that must be filled in properly before it can be submitted.

Managers keep the templates in the app (a small file in the data folder, like the carrier rate card), so
what engineers must confirm can change without a developer. A form is a list of fields:
  text / longtext   a written answer; `min_chars` stops "ok" or "n/a"
  choice            one of `options`
  number / date     a number or a YYYY-MM-DD date
  yesno             a statement the engineer confirms ("I checked the drawing revision against the PO");
                    `must_be_yes` means the task can't be submitted until it is ticked
A template says what it `applies_to` (words in the milestone's name). When a design submission matches an
enabled template, the form is required; the answers are saved with the submission and written into its note,
so reviewers see exactly what the engineer confirmed.
"""

import json
import math
import os
import re
import tempfile
import threading
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

FIELD_TYPES = ("text", "longtext", "choice", "number", "date", "yesno")
_lock = threading.Lock()

_STARTER = [{
    "id": "design-release",
    "name": "Design documents release",
    "enabled": True,
    "applies_to": [],          # empty = the milestones that accept design documents (the MILESTONE_SUBMISSION_KEYWORDS setting)
    "instructions": "Before you submit: check every item below against the customer's order (or RFQ) and the drawing. "
                    "If something doesn't match, don't submit: ask the customer or your manager first.",
    "fields": [
        {"key": "drawing_revision", "label": "Drawing number and revision", "type": "text", "required": True, "min_chars": 2,
         "help": "Exactly as printed on the drawing, e.g. D-4410 Rev C."},
        {"key": "po_checked", "label": "I checked item, quantity, material and size against the customer's order", "type": "yesno",
         "required": True, "must_be_yes": True},
        {"key": "mtr_checked", "label": "I checked the material certificate (MTR) and heat number", "type": "yesno",
         "required": True, "must_be_yes": True},
        {"key": "customer_approval", "label": "Customer approval of this revision", "type": "choice", "required": True,
         "options": ["Approved in writing", "Not required by the customer", "Still waiting"],
         "help": "Pick 'Still waiting' only if you are submitting for internal review first."},
        {"key": "welding_procedure", "label": "Welding procedure", "type": "choice", "required": True,
         "options": ["WPS attached", "Customer-specified procedure", "No welding on this job"]},
        {"key": "special_requirements", "label": "Special requirements from the customer", "type": "longtext", "required": True,
         "min_chars": 4, "help": "Write 'None' if there are none. Don't leave it blank."},
        {"key": "open_questions", "label": "Anything you are unsure about", "type": "longtext", "required": False},
    ],
}]


class FormError(ValueError):
    """The form isn't complete: `fields` maps each field key to a plain sentence saying what to fix."""

    def __init__(self, fields: Dict[str, str], message: str = ""):
        self.fields = fields
        super().__init__(message or "The form isn't complete: " + "; ".join(fields.values()))


class TemplateError(ValueError):
    """A template a manager saved isn't valid; the message says what to fix."""


def _path() -> Path:
    return Path(settings.DATA_DIR) / "task_templates.json"


def load() -> List[Dict[str, Any]]:
    p = _path()
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list) and all(isinstance(t, dict) and t.get("id") for t in data):
                return data
    except (OSError, ValueError):
        pass
    return json.loads(json.dumps(_STARTER))


def _save(templates: List[Dict[str, Any]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".task_templates.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(templates, f, indent=2, ensure_ascii=False)
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
    raise LookupError(f"No form called '{template_id}'.")


def for_milestone(milestone_name: str, template_id: str = "") -> Optional[Dict[str, Any]]:
    """The form a submission for this milestone must use: the one asked for, else an enabled one whose words match."""
    name = (milestone_name or "").casefold()
    from app.services.coordination import project_templates

    templates = {t["id"]: t for t in load() if t.get("enabled", True)}
    for pt in project_templates.load():                          # a form a project template attached to this very milestone
        for m in pt.get("milestones") or []:
            if m.get("form") and m["name"].casefold() == name and m["form"] in templates:
                return templates[m["form"]]
    for t in templates.values():
        words = t.get("applies_to") or []
        if words and any(re.search(rf"\b{re.escape(w.casefold())}", name) for w in words):
            return t
        if not words:
            from app.services.coordination import projects

            if projects._needs_submission(milestone_name or "", {}):
                return t
    if template_id and template_id in templates:                 # asked for by name, but never instead of a matching form
        return templates[template_id]
    return None


# ---------------------------------------------------------------------------
# Checking an engineer's answers
# ---------------------------------------------------------------------------


def validate(template: Dict[str, Any], answers: Dict[str, Any]) -> Dict[str, Any]:
    """The cleaned answers, or FormError saying what to fix, one plain sentence per field."""
    answers = answers if isinstance(answers, dict) else {}
    problems: Dict[str, str] = {}
    clean: Dict[str, Any] = {}
    for f in template["fields"]:
        key, label, kind = f["key"], f["label"], f.get("type", "text")
        raw = answers.get(key)
        blank = raw is None or (isinstance(raw, str) and not raw.strip())
        if kind == "yesno":
            yes = raw is True or (isinstance(raw, str) and raw.strip().lower() in ("yes", "true", "y", "1"))
            if f.get("must_be_yes") and not yes:
                problems[key] = f"Tick this once you have done it: {label}."
            clean[key] = yes
            continue
        if blank:
            if f.get("required"):
                problems[key] = f"{label} is required."
            clean[key] = ""
            continue
        if kind in ("text", "longtext"):
            text = re.sub(r"\s+", " ", str(raw)).strip()
            if len(text) < int(f.get("min_chars") or 0):
                problems[key] = f"{label}: please write a proper answer (at least {f['min_chars']} characters)."
            elif len(text) > (4000 if kind == "longtext" else 300):
                problems[key] = f"{label} is too long."
            clean[key] = text
        elif kind == "choice":
            raw = str(raw).strip()
            if raw not in (f.get("options") or []):
                problems[key] = f"{label}: choose one of {', '.join(f.get('options') or [])}."
            clean[key] = raw
        elif kind == "number":
            try:
                num = float(raw)
                if not math.isfinite(num):
                    raise ValueError
                clean[key] = num
            except (TypeError, ValueError):
                problems[key] = f"{label} must be a number."
        elif kind == "date":
            try:
                clean[key] = date.fromisoformat(str(raw)).isoformat()
            except ValueError:
                problems[key] = f"{label} must be a date like 2026-11-20."
    if problems:
        raise FormError(problems)
    return clean


def render_note(template: Dict[str, Any], clean: Dict[str, Any], extra: str = "") -> str:
    """The answers as plain lines, to go in the submission's note so reviewers see what was confirmed."""
    lines = [f"Form: {template['name']}"]
    for f in template["fields"]:
        v = clean.get(f["key"], "")
        if f.get("type") == "yesno":
            v = "Yes, confirmed" if v else "No"
        if v not in ("", None):
            lines.append(f"- {f['label']}: {v}")
    if extra and extra.strip():
        lines.append(f"Note: {extra.strip()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Managers editing templates
# ---------------------------------------------------------------------------


def _words(value: Any) -> List[Any]:
    """A list of words, whether given as a list or as one comma-separated string."""
    if isinstance(value, str):
        return value.split(",")
    return list(value) if isinstance(value, (list, tuple)) else []


def _check_template(t: Dict[str, Any]) -> Dict[str, Any]:
    name = str(t.get("name") or "").strip()
    if not name or len(name) > 80:
        raise TemplateError("Give the form a name (up to 80 characters).")
    fields = t.get("fields")
    if not isinstance(fields, list) or not fields or len(fields) > 40:
        raise TemplateError("A form needs between 1 and 40 fields.")
    seen, out = set(), []
    for f in fields:
        if not isinstance(f, dict):
            raise TemplateError("Every field needs a label and a type.")
        label = str(f.get("label") or "").strip()
        kind = f.get("type", "text")
        if not label or kind not in FIELD_TYPES:
            raise TemplateError(f"Every field needs a label and a type ({', '.join(FIELD_TYPES)}).")
        key = re.sub(r"[^a-z0-9]+", "_", str(f.get("key") or label).lower()).strip("_")[:40] or "field"
        base, n = key, 2
        while key in seen:
            key, n = f"{base}_{n}", n + 1
        seen.add(key)
        item: Dict[str, Any] = {"key": key, "label": label[:200], "type": kind, "required": bool(f.get("required", False)),
                                "help": str(f.get("help") or "")[:300]}
        if kind == "choice":
            opts = [str(o).strip() for o in (f.get("options") or []) if str(o).strip()]
            if len(opts) < 2:
                raise TemplateError(f"'{label}' needs at least two choices.")
            item["options"] = opts
        if kind in ("text", "longtext"):
            try:
                item["min_chars"] = max(0, min(int(f.get("min_chars") or 0), 500))
            except (TypeError, ValueError):
                raise TemplateError(f"'{label}': the minimum length must be a whole number.")
        if kind == "yesno":
            item["must_be_yes"] = bool(f.get("must_be_yes", False))
        out.append(item)
    return {"id": t.get("id"), "name": name, "enabled": bool(t.get("enabled", True)),
            "applies_to": [str(w).strip() for w in _words(t.get("applies_to")) if str(w).strip()][:20],
            "instructions": str(t.get("instructions") or "")[:1000], "fields": out}


def save(template: Dict[str, Any]) -> Dict[str, Any]:
    """Add a new form (no id) or replace one (its id)."""
    checked = _check_template(template)
    with _lock:
        all_t = load()
        if checked["id"]:
            for i, t in enumerate(all_t):
                if t["id"] == checked["id"]:
                    all_t[i] = checked
                    _save(all_t)
                    return checked
            raise LookupError(f"No form called '{checked['id']}'.")
        base = re.sub(r"[^a-z0-9]+", "-", checked["name"].lower()).strip("-")[:40] or "form"
        taken, new_id, n = {t["id"] for t in all_t}, base, 2
        while new_id in taken:
            new_id, n = f"{base}-{n}", n + 1
        checked["id"] = new_id
        all_t.append(checked)
        _save(all_t)
        return checked


def remove(template_id: str) -> None:
    with _lock:
        all_t = load()
        kept = [t for t in all_t if t["id"] != template_id]
        if len(kept) == len(all_t):
            raise LookupError(f"No form called '{template_id}'.")
        _save(kept)
