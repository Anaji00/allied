"""What the app did on its own for one job: drafts it made, alerts it raised, plays it ran, in plain sentences with when. Read from the audit trail, the drafts queue and the alerts; nothing is
written. A viewer is not shown this (it names people's drafts and alerts)."""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.core.logging import logger


def _iso(d: Any) -> str:
    if d is None:
        return ""
    if isinstance(d, str):
        return d
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()


def log(job: str, limit: int = 100) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.models.db_models import AssistantActionDB, AuditLogDB, NotificationDB
    from app.services.coordination import jobs

    j = jobs.get(job, staff=True)
    rfq = (j.get("rfq") or {}).get("number")
    keys = {j["ref"]} | ({rfq} if rfq else set()) | {str(p["po_number"]) for p in j["purchase_orders"] if p.get("po_number")}       # references only: a bare id would match any text with that digit in it
    out: List[Dict[str, Any]] = []

    def add(at: Any, sentence: str, source: str) -> None:
        iso = _iso(at)
        if iso:
            out.append({"at": iso, "sentence": " ".join(sentence.split())[:300], "source": source})

    try:
        with SessionLocal() as db:
            for a in db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT").order_by(AssistantActionDB.created_at.desc()).limit(800):
                args = a.args or {}
                if args.get("rfq_number") == rfq and rfq or args.get("_rfq_id") == (j.get("rfq") or {}).get("id") and j.get("rfq") or args.get("_job_id") == j["id"] or args.get("po_number") in keys:
                    word = {"PENDING": "is waiting for a person's OK", "DONE": "was sent after a person's OK", "CANCELLED": "was dismissed", "FAILED": "failed to send", "EXPIRED": "expired unsent", "RUNNING": "is being sent"}.get(a.status, a.status.lower())
                    add(a.created_at, f"The app drafted: {a.summary.splitlines()[0]} ({word}).", "draft")
            for n in db.query(NotificationDB).order_by(NotificationDB.id.desc()).limit(1500):
                text = f"{n.title} {n.body or ''} {n.dedup_key or ''}"
                if any(k and re.search(r"(?<![\w-])" + re.escape(k) + r"(?![\w-])", text) for k in keys):
                    add(n.created_at, f"The app raised an alert: {n.title}" + (" (handled)" if n.acknowledged_at else ""), "alert")
            for a in db.query(AuditLogDB).filter(AuditLogDB.resource_id.in_(sorted(keys)), AuditLogDB.actor_email.in_(("autopilot@alliedweldingsystems.com", "system", "autopilot", "monday-push"))).order_by(AuditLogDB.id.desc()).limit(200):
                add(a.timestamp, f"The app did this by itself: {str(a.action).replace('_', ' ').lower()}" + (f" ({a.detail})" if a.detail else ""), "play")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Job automation log: {type(exc).__name__}")
    seen, rows = set(), []
    for r in sorted(out, key=lambda r: r["at"], reverse=True):
        if (r["at"][:19], r["sentence"]) not in seen:
            seen.add((r["at"][:19], r["sentence"]))
            rows.append(r)
    rows = rows[:max(1, min(int(limit), 300))]
    return {"job": j["ref"], "count": len(rows), "items": rows, "text": f"What the app did on its own for {j['ref']}:\n" + ("\n".join(f"- {r['at'][:16].replace('T', ' ')} {r['sentence']}" for r in rows) if rows else "Nothing yet.")}
