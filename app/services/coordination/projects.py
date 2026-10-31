"""Projects and milestones from monday, and design-document submissions.

monday holds the projects board and is the source of truth for names, owners,
dates and statuses. This module mirrors it (so schedule checks need no call
per check), works out where each milestone stands, and runs the submission
and review of design documents, writing each step back onto the monday item.
"""

import hashlib
import json
import re
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.core.logging import logger
from app.core.uploads import safe_upload_name
from app.models.db_models import MondayMilestoneDB, MondayProjectDB, SubmissionDB
from app.services.coordination import notify as alerts

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_users_cache: Dict[str, Any] = {"at": 0.0, "map": {}}
_submit_lock = threading.Lock()
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _client():
    from app.core.container import get_monday_client

    return get_monday_client()


def _labels(raw: str) -> set:
    return {p.strip().casefold() for p in (raw or "").split(",") if p.strip()}


# ---------------------------------------------------------------------------
# Reading monday column values
# ---------------------------------------------------------------------------


class _Cols(dict):
    """Column values by id, also findable by the column's title as shown on the board
    (any case), so settings can name "Engineer" instead of an id like "person4".
    "a|b" means the first of those columns that holds a value."""

    def get(self, key, default=None):
        if not key:
            return default
        if "|" in str(key):
            options = [self.get(k.strip()) for k in str(key).split("|") if k.strip()]
            options = [cv for cv in options if cv is not None]
            filled = [cv for cv in options if (cv.get("text") or "").strip() or cv.get("value") not in (None, "", "null")]
            return (filled or options or [default])[0]
        found = super().get(key)
        return found if found is not None else super().get(f"title:{str(key).strip().casefold()}", default)


def _cols(item: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out = _Cols()
    for cv in item.get("column_values") or []:
        out[cv.get("id")] = cv
        title = ((cv.get("column") or {}).get("title") or "").strip().casefold()
        if title:
            out.setdefault(f"title:{title}", cv)  # the first column with that title wins
    return out


def _json(cv: Optional[Dict[str, Any]]) -> Any:
    try:
        return json.loads((cv or {}).get("value") or "null")
    except (TypeError, ValueError):
        return None


def _text(cv: Optional[Dict[str, Any]]) -> str:
    return str((cv or {}).get("text") or "").strip()


def _people(cv: Optional[Dict[str, Any]], users: Dict[str, str]) -> List[str]:
    value = _json(cv) or {}
    ids = [str(p.get("id")) for p in value.get("personsAndTeams") or [] if p.get("kind", "person") == "person"]
    return [users[i] for i in ids if users.get(i)]


def _due(cv: Optional[Dict[str, Any]]) -> Optional[str]:
    """Date columns give {"date": ...}; timeline columns give {"from", "to"} (the end counts)."""
    value = _json(cv) or {}
    for key in ("to", "date"):
        if isinstance(value, dict) and value.get(key) and _DATE.match(str(value[key])):
            return str(value[key])[:10]
    found = _DATE.findall(_text(cv))
    return found[-1] if found else None


def _checked(cv: Optional[Dict[str, Any]]) -> bool:
    value = _json(cv)
    return bool(isinstance(value, dict) and str(value.get("checked")).lower() == "true")


def _needs_submission(name: str, cols: Dict[str, Dict[str, Any]]) -> bool:
    if settings.MILESTONE_SUBMISSION_COLUMN_ID:
        return _checked(cols.get(settings.MILESTONE_SUBMISSION_COLUMN_ID))
    words = _labels(settings.MILESTONE_SUBMISSION_KEYWORDS)
    lowered = (name or "").casefold()
    return any(w in lowered for w in words)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def _user_emails(client) -> Dict[str, str]:
    from app.services.monday_client import fetch_user_emails

    if time.monotonic() - _users_cache["at"] > 3600 or not _users_cache["map"]:
        _users_cache["map"] = fetch_user_emails(client)
        _users_cache["at"] = time.monotonic()
    return _users_cache["map"]


def sync_projects(client=None) -> Dict[str, Any]:
    """Refreshes the mirror of the projects board. Reads only; writes nothing to monday."""
    from app.services.monday_client import fetch_projects_board

    client = client or _client()
    board = (settings.MONDAY_PROJECTS_BOARD_ID or "").strip()
    if not board:
        return {"skipped": "MONDAY_PROJECTS_BOARD_ID is not set"}
    if not client.live:
        return {"skipped": "monday is in simulation; nothing to read"}

    users = _user_emails(client)
    layout = (settings.MONDAY_PROJECTS_LAYOUT or "subitems").lower()
    items = fetch_projects_board(client, int(board), with_subitems=(layout == "subitems"))
    if not items:
        with SessionLocal() as db:
            mirrored = db.query(MondayProjectDB.item_id).filter(MondayProjectDB.board_id == board).count()
        if mirrored:
            # An empty answer for a board we already hold is far more likely a
            # permissions or API problem than every project being deleted.
            raise RuntimeError(
                f"monday returned no items for board {board}, which held {mirrored} project(s). "
                "Nothing was removed; check the board id and the API token's access."
            )
    done = _labels(settings.MILESTONE_DONE_LABELS)
    now = datetime.now(timezone.utc)

    projects: Dict[str, Dict[str, Any]] = {}
    milestones: Dict[str, Dict[str, Any]] = {}

    def milestone(ms: Dict[str, Any], project_id: str, board_id: str) -> None:
        cols = _cols(ms)
        status = _text(cols.get(settings.MILESTONE_STATUS_COLUMN_ID))
        reviewers = (
            _people(cols.get(settings.MILESTONE_REVIEWER_COLUMN_ID), users)
            if settings.MILESTONE_REVIEWER_COLUMN_ID else []
        )
        milestones[str(ms["id"])] = dict(
            item_id=str(ms["id"]), project_item_id=project_id, board_id=board_id, name=ms.get("name") or "",
            owner_emails=_people(cols.get(settings.MILESTONE_OWNER_COLUMN_ID), users),
            reviewer_emails=reviewers, status=status or None,
            due_date=_due(cols.get(settings.MILESTONE_DUE_COLUMN_ID)),
            done=status.casefold() in done, needs_submission=_needs_submission(ms.get("name") or "", cols),
            synced_at=now,
        )

    for item in items:
        cols = _cols(item)
        group = item.get("group") or {}
        if layout == "groups":
            pid = f"g{board}-{group.get('id')}"
            projects.setdefault(pid, dict(
                item_id=pid, board_id=board, name=group.get("title") or "Project", group_title=group.get("title"),
                owner_emails=[], status=None, due_date=None, done=False, synced_at=now,
            ))
            milestone(item, pid, board)
            owners = projects[pid]["owner_emails"]
            for e in milestones[str(item["id"])]["owner_emails"]:
                if e not in owners:
                    owners.append(e)
            continue
        status = _text(cols.get(settings.PROJECT_STATUS_COLUMN_ID))
        pid = str(item["id"])
        projects[pid] = dict(
            item_id=pid, board_id=board, name=item.get("name") or "", group_title=group.get("title"),
            owner_emails=_people(cols.get(settings.PROJECT_OWNER_COLUMN_ID), users),
            status=status or None, due_date=_due(cols.get(settings.PROJECT_DUE_COLUMN_ID)),
            done=status.casefold() in done, synced_at=now,
        )
        for sub in item.get("subitems") or []:
            milestone(sub, pid, str((sub.get("board") or {}).get("id") or ""))

    moved = []
    with SessionLocal() as db:
        for model, rows in ((MondayProjectDB, projects), (MondayMilestoneDB, milestones)):
            for key, values in rows.items():
                row = db.get(model, key)
                if row is None:
                    db.add(model(**values))
                else:
                    if model is MondayMilestoneDB and (row.status or "") != (values.get("status") or ""):
                        moved.append((values, row.status))
                    for k, v in values.items():
                        setattr(row, k, v)
        # Projects deleted from the board leave the mirror with their milestones.
        gone = [p.item_id for p in db.query(MondayProjectDB).filter(MondayProjectDB.board_id == board) if p.item_id not in projects]
        if gone:
            db.query(MondayMilestoneDB).filter(MondayMilestoneDB.project_item_id.in_(gone)).delete(synchronize_session=False)
            db.query(MondayProjectDB).filter(MondayProjectDB.item_id.in_(gone)).delete(synchronize_session=False)
        stale = [m.item_id for m in db.query(MondayMilestoneDB).filter(MondayMilestoneDB.project_item_id.in_(list(projects))) if m.item_id not in milestones]
        if stale:
            db.query(MondayMilestoneDB).filter(MondayMilestoneDB.item_id.in_(stale)).delete(synchronize_session=False)
        db.commit()
    if moved:
        from app.services.coordination import events

        for values, old in moved:
            events.emit("milestone.status_changed", {"milestone": {k: v for k, v in values.items() if k != "synced_at"},
                                                     "from": old, "to": values.get("status")},
                        f"{values['name']}: {old or 'no status'} -> {values.get('status') or 'no status'}")
    return {"projects": len(projects), "milestones": len(milestones), "removed": len(gone)}


# ---------------------------------------------------------------------------
# Where things stand
# ---------------------------------------------------------------------------


def _days_until(due: Optional[str], on: date) -> Optional[int]:
    if not due:
        return None
    try:
        return (date.fromisoformat(due) - on).days
    except ValueError:
        return None


def _sub_dict(s: SubmissionDB) -> Dict[str, Any]:
    return {
        "id": s.id, "version": s.version, "status": s.status, "note": s.note,
        "files": [{"name": f["name"], "size": f["size"]} for f in s.files or []],
        "submitted_by": s.submitted_by, "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        "reviewed_by": s.reviewed_by, "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
        "review_note": s.review_note,
    }


def milestone_state(m: Dict[str, Any], latest: Optional[Dict[str, Any]], on: date) -> Dict[str, Any]:
    """done / waiting_review / overdue / due_soon / on_track / no_date, plus days to or past due."""
    days = _days_until(m.get("due_date"), on)
    if m.get("done") or (latest and latest["status"] == "APPROVED"):
        state = "done"
    elif latest and latest["status"] == "SUBMITTED":
        state = "waiting_review"
    elif days is None:
        state = "no_date"
    elif days < 0:
        state = "overdue"
    elif days <= settings.DUE_SOON_DAYS:
        state = "due_soon"
    else:
        state = "on_track"
    missing_docs = bool(m.get("needs_submission") and state not in ("done", "waiting_review")
                        and (latest is None or latest["status"] == "CHANGES_REQUESTED"))
    return {"state": state, "days": days, "missing_docs": missing_docs}


def overview(on: Optional[date] = None) -> Dict[str, Any]:
    on = on or alerts.today()
    with SessionLocal() as db:
        projects = [p.__dict__.copy() for p in db.query(MondayProjectDB).order_by(MondayProjectDB.due_date.is_(None), MondayProjectDB.due_date)]
        mss = [m.__dict__.copy() for m in db.query(MondayMilestoneDB).order_by(MondayMilestoneDB.due_date.is_(None), MondayMilestoneDB.due_date)]
        subs: Dict[str, List[Dict[str, Any]]] = {}
        for s in db.query(SubmissionDB).order_by(SubmissionDB.id.desc()):
            subs.setdefault(s.milestone_item_id, []).append(_sub_dict(s))
    counts = {"overdue": 0, "due_soon": 0, "waiting_review": 0, "missing_docs": 0}
    by_project: Dict[str, List[Dict[str, Any]]] = {}
    for m in mss:
        m.pop("_sa_instance_state", None)
        history = subs.get(m["item_id"], [])
        latest = history[0] if history else None
        m.update(milestone_state(m, latest, on), latest_submission=latest, submissions=history)
        if m["state"] in counts:
            counts[m["state"]] += 1
        counts["missing_docs"] += int(m["missing_docs"])
        by_project.setdefault(m["project_item_id"], []).append(m)
    out = []
    for p in projects:
        p.pop("_sa_instance_state", None)
        p["milestones"] = by_project.get(p["item_id"], [])
        p["behind"] = sum(1 for m in p["milestones"] if m["state"] == "overdue")
        days = _days_until(p.get("due_date"), on)
        p["overdue"] = bool(days is not None and days < 0 and not p.get("done"))
        p["synced_at"] = p["synced_at"].isoformat() if p.get("synced_at") else None
        for m in p["milestones"]:
            m["synced_at"] = m["synced_at"].isoformat() if m.get("synced_at") else None
        out.append(p)
    out.sort(key=lambda p: (p.get("done"), -p["behind"], -int(p["overdue"]), p.get("due_date") or "9999"))
    return {"today": on.isoformat(), "counts": counts, "projects": out}


def _milestone(item_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    with SessionLocal() as db:
        m = db.get(MondayMilestoneDB, item_id)
        if m is None:
            raise LookupError("That milestone is not on the projects board (try syncing).")
        p = db.get(MondayProjectDB, m.project_item_id)
        ms = {k: v for k, v in m.__dict__.items() if not k.startswith("_")}
        pr = {k: v for k, v in (p.__dict__ if p else {}).items() if not k.startswith("_")}
    return ms, pr


def reviewers_for(m: Dict[str, Any], p: Dict[str, Any]) -> List[str]:
    return list(m.get("reviewer_emails") or []) or list(p.get("owner_emails") or []) or alerts.leadership()


def may_review(user: Dict[str, Any], m: Dict[str, Any], p: Dict[str, Any]) -> bool:
    """The milestone's reviewers (or project owners, or leadership) review; admins and managers can step in."""
    if user.get("role") in ("ADMIN", "MANAGER"):
        return True
    email = (user.get("email") or "").lower()
    return email in {e.lower() for e in reviewers_for(m, p)} or email in {e.lower() for e in alerts.leadership()}


def _stored_name(name: str, taken: set) -> str:
    """A safe file name that no other file in this version already uses."""
    safe = safe_upload_name(name)
    stem, dot, ext = safe.rpartition(".")
    if not dot:
        stem, ext = safe, ""
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    candidate, n = f"{stem}{dot}{ext}", 2
    while candidate.lower() in taken:
        candidate, n = f"{stem}-{n}{dot}{ext}", n + 1
    taken.add(candidate.lower())
    return candidate


# ---------------------------------------------------------------------------
# Design-document submissions
# ---------------------------------------------------------------------------


def _storage(milestone_item_id: str, version: int) -> Path:
    folder = settings.DB_DIR / "submissions" / safe_upload_name(milestone_item_id) / f"v{version}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write_back(m: Dict[str, Any], label: str, update: str, idem: str) -> None:
    client = _client()
    client.post_pulse_update(int(m["item_id"]), update, idempotency_key=idem)
    if m.get("board_id") and settings.MILESTONE_STATUS_COLUMN_ID:
        client.update_column_values(
            int(m["board_id"]), int(m["item_id"]), {settings.MILESTONE_STATUS_COLUMN_ID: {"label": label}}, idem + "-status"
        )


def submit(milestone_item_id: str, files: List[Tuple[str, bytes]], note: str, user: Dict[str, Any]) -> Dict[str, Any]:
    if not files:
        raise ValueError("Attach at least one design document.")
    allowed = {e.strip().lower() for e in settings.SUBMISSION_EXTENSIONS.split(",") if e.strip()}
    limit = settings.SUBMISSION_MAX_MB * 1024 * 1024
    for name, data in files:
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in allowed:
            raise ValueError(f"{name}: .{ext or '?'} files are not accepted. Allowed: {', '.join(sorted(allowed))}.")
        if not data:
            raise ValueError(f"{name} is empty.")
        if len(data) > limit:
            raise ValueError(f"{name} is larger than {settings.SUBMISSION_MAX_MB} MB.")

    m, p = _milestone(milestone_item_id)
    who = user.get("email", "unknown")
    # One submission at a time, so two uploads cannot both become the same version.
    with _submit_lock, SessionLocal() as db:
        open_sub = (
            db.query(SubmissionDB.id)
            .filter(SubmissionDB.milestone_item_id == milestone_item_id, SubmissionDB.status == "SUBMITTED")
            .first()
        )
        if open_sub:
            raise ValueError("A submission for this milestone is already waiting for review.")
        version = db.query(SubmissionDB).filter(SubmissionDB.milestone_item_id == milestone_item_id).count() + 1
        folder = _storage(milestone_item_id, version)
        stored, taken = [], set()
        for name, data in files:
            safe = _stored_name(name, taken)
            (folder / safe).write_bytes(data)
            stored.append({"name": name, "stored": str(folder / safe), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        row = SubmissionDB(
            milestone_item_id=milestone_item_id, project_item_id=m["project_item_id"], version=version,
            files=stored, note=note or None, submitted_by=who,
        )
        db.add(row)
        db.commit()
        sub = _sub_dict(row)

    names = ", ".join(f["name"] for f in stored)
    _write_back(
        m, settings.MILESTONE_SUBMITTED_LABEL,
        f"Design documents submitted (v{version}) by {who}: {names}." + (f"\nNote: {note}" if note else "")
        + f"\nWaiting for review by {', '.join(reviewers_for(m, p))}.",
        f"submission-{sub['id']}",
    )
    alerts.notify(
        "submission_ready", f"submission-ready:{sub['id']}",
        f"Review needed: {m['name']} ({p.get('name', '')})",
        f"{who} submitted v{version} of the design documents for '{m['name']}' on project {p.get('name', '')}: {names}."
        + (f"\nNote: {note}" if note else "") + "\nPlease review and approve or request changes.",
        to=reviewers_for(m, p), link=f"/projects?milestone={milestone_item_id}", project_item_id=m["project_item_id"],
    )
    audit_log(action="SUBMIT", resource_type="DESIGN_SUBMISSION", resource_id=str(sub["id"]), actor_email=who,
              actor_role=user.get("role", ""), detail=f"{m['name']} v{version}: {names}")
    return sub


def review(submission_id: int, approve: bool, note: str, user: Dict[str, Any]) -> Dict[str, Any]:
    who = user.get("email", "unknown")
    with SessionLocal() as db:
        row = db.get(SubmissionDB, submission_id)
        if row is None:
            raise LookupError("No such submission.")
        milestone_item_id, project_item_id = row.milestone_item_id, row.project_item_id
    on_board = True
    try:
        m, p = _milestone(milestone_item_id)
    except LookupError:
        on_board = False
        # Removed from the board since it was submitted: still reviewable, and
        # the submitter is still told; there is just no monday item to update.
        m = {"item_id": milestone_item_id, "name": f"milestone {milestone_item_id}", "board_id": None,
             "project_item_id": project_item_id, "reviewer_emails": []}
        p = {}
    if not may_review(user, m, p):
        raise PermissionError("Only this milestone's reviewers, the project owners or leadership can review it.")
    with SessionLocal() as db:
        row = db.get(SubmissionDB, submission_id)
        if row.status != "SUBMITTED":
            raise ValueError("This submission has already been reviewed.")
        if row.submitted_by.lower() == who.lower():
            # Four eyes: nobody approves their own work.
            raise PermissionError("Someone other than the submitter has to review it.")
        if not approve and not (note or "").strip():
            raise ValueError("Say what needs to change.")
        row.status = "APPROVED" if approve else "CHANGES_REQUESTED"
        row.reviewed_by, row.reviewed_at, row.review_note = who, datetime.now(timezone.utc), note or None
        db.commit()
        sub = _sub_dict(row)
        submitter = row.submitted_by

    if approve:
        with SessionLocal() as db:
            ms = db.get(MondayMilestoneDB, milestone_item_id)
            if ms is not None:
                ms.done, ms.status = True, settings.MILESTONE_APPROVED_LABEL
                db.commit()
    verdict = "approved" if approve else "sent back for changes"
    if on_board:
        _write_back(
            m, settings.MILESTONE_APPROVED_LABEL if approve else settings.MILESTONE_CHANGES_LABEL,
            f"Design documents v{sub['version']} {verdict} by {who}." + (f"\n{note}" if note else ""),
            f"review-{submission_id}",
        )
    alerts.resolve([f"submission-ready:{submission_id}", f"review-wait:{submission_id}:"])
    alerts.notify(
        "submission_reviewed", f"submission-reviewed:{submission_id}",
        f"{m['name']}: {verdict}",
        f"{who} {verdict} your design documents (v{sub['version']}) for '{m['name']}' on {p.get('name', '')}."
        + (f"\n\nWhat to change: {note}" if (note and not approve) else (f"\n\n{note}" if note else "")),
        severity="info" if approve else "warning",
        to=[submitter], link=f"/projects?milestone={milestone_item_id}", project_item_id=m["project_item_id"],
    )
    audit_log(action="APPROVE" if approve else "REJECT", resource_type="DESIGN_SUBMISSION",
              resource_id=str(submission_id), actor_email=who, actor_role=user.get("role", ""),
              detail=f"{m['name']} v{sub['version']}: {verdict}" + (f" | {note}" if note else ""))
    return sub


def submission_file(submission_id: int, index: int) -> Tuple[str, Path]:
    with SessionLocal() as db:
        row = db.get(SubmissionDB, submission_id)
        if row is None or index < 0 or index >= len(row.files or []):
            raise LookupError("No such file.")
        f = row.files[index]
    path = Path(f["stored"])
    if not path.is_file():
        raise LookupError("The stored file is missing.")
    return f["name"], path
