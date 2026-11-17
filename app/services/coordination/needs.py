"""One call for "what needs me": the drafts waiting for my OK, my open alerts, and the items where I hold the ball. One count for the
sidebar badge, one list for the Today screen. Reads only. Each line carries a link and a plain status word, and `items` is the same
lines merged (oldest first) so a screen can show one list; `total` counts every item, while each list shows only the first `limit`."""

from datetime import datetime, timezone
from typing import Any, Dict, List

from app.core import status_words
from app.services.assistant import agent
from app.services.coordination import ball, notify

_BALL_LINK = {"rfq": "/rfqs", "project": "/projects", "shipment": "/shipments", "vendor_job": "/vendors", "job": "/jobs", "order": "/orders"}


def _days_since(iso: Any) -> int:
    try:
        at = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return 0
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - at).days)


def _waiting_word(days: Any) -> str:
    try:
        n = int(days or 0)
    except (TypeError, ValueError):
        n = 0
    return "Waiting on you" + (f" for {n} day{'s' if n != 1 else ''}" if n > 0 else "")


def for_person(user: Dict[str, Any], limit: int = 20) -> Dict[str, Any]:
    email = (user.get("email") or "").strip().lower()
    drafts = agent.pending(user)
    alerts = [n for n in notify.inbox(email, open_only=True, limit=200)]
    held: List[Dict[str, Any]] = ball.waiting_on(email) if email else []
    counts = {"drafts": len(drafts), "alerts": len(alerts), "ball": len(held)}
    draft_lines = [{"id": d["id"], "summary": d["summary"], "kind": d.get("kind"), "status_word": status_words.word("draft", d.get("status") or "PENDING"),
                    "link": "/inbox", "age_days": _days_since(d.get("created_at"))} for d in drafts[:limit]]
    alert_lines = [{"id": n["id"], "title": n["title"], "severity": n["severity"], "status_word": status_words.word("alert", n["severity"]),
                    "link": n["link"] or "/alerts", "created_at": n["created_at"], "age_days": _days_since(n["created_at"]), "assignee": n.get("assignee")}
                   for n in alerts[:limit]]
    ball_lines = [{"item_type": b["item_type"], "item_ref": b["item_ref"], "label": b.get("label"), "days_waiting": b.get("days_waiting"), "note": b.get("note"),
                   "status_word": _waiting_word(b.get("days_waiting")),
                   "link": f"{_BALL_LINK.get(str(b['item_type']), '/ball')}/{b['item_ref']}" if str(b["item_type"]) in _BALL_LINK else "/ball",
                   "age_days": int(b.get("days_waiting") or 0)} for b in held[:limit]]
    merged = ([{"kind": "draft", "ref": str(d["id"]), "title": d["summary"], "status_word": d["status_word"], "link": d["link"], "age_days": d["age_days"]} for d in draft_lines]
              + [{"kind": "alert", "ref": str(a["id"]), "title": a["title"], "status_word": a["status_word"], "link": a["link"], "age_days": a["age_days"]} for a in alert_lines]
              + [{"kind": "ball", "ref": f"{b['item_type']}:{b['item_ref']}", "title": b.get("label") or b["item_ref"], "status_word": b["status_word"], "link": b["link"], "age_days": b["age_days"]}
                 for b in ball_lines])
    merged.sort(key=lambda i: -i["age_days"])
    return {"total": sum(counts.values()), "counts": counts, "drafts": draft_lines, "alerts": alert_lines, "ball": ball_lines, "items": merged}
