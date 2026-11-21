"""A job page in ONE call: the job, its comments, what changed since yesterday, the meeting-prep facts and the actions that apply. Each part fails on its own, so one broken source
never blanks the page. What a person sees depends on their role: a viewer gets the job and its comments; staff also get what changed, the prep facts and the actions; money (PO totals)
only for managers. Reading only."""

from typing import Any, Dict

from app.core.logging import logger


def page(job: str, user: Dict[str, Any], since: str = "yesterday") -> Dict[str, Any]:
    from app.services import actions, my_day
    from app.services.coordination import jobs, updates

    role = user.get("role") or "VIEWER"
    staff = role in ("ADMIN", "MANAGER", "OPERATOR")
    manager = role in ("ADMIN", "MANAGER")
    j = jobs.get(job, staff=staff)                                            # unknown job: LookupError, the route says 404
    out: Dict[str, Any] = {"job": j, "thread": [], "since": None, "prep": None, "actions": None, "errors": []}

    def part(name: str, fn) -> None:
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001 - one broken part never blanks the page
            out["errors"].append(name)
            logger.warning(f"Job page: {name} unavailable: {type(exc).__name__}")

    if j.get("rfq"):
        part("thread", lambda: updates.thread("rfq", j["rfq"]["number"], user.get("email", ""))[-30:])
    if staff:
        part("since", lambda: my_day.since("job", j["ref"], since, manager))
        part("prep", lambda: my_day.prep("job", j["ref"], manager))
        part("actions", lambda: actions.available("job", j["ref"]))
    if not staff:                                                                  # a viewer gets the job itself: no vendors, purchase orders, drafts, internal comments or documents
        for k in ("purchase_orders", "drafts", "documents", "research", "checks", "waiting", "shipments", "next_steps", "ball"):
            j.pop(k, None)
        out["thread"] = []
    out["files"] = (j.get("design") or {}).get("files", []) if staff else []

    def link() -> Dict[str, Any]:
        from app.services import jobs_board

        return jobs_board.link_of({"ref": j["ref"], "id": j["id"], "title": j.get("title"), "customer": j.get("customer"), "stage": j.get("stage")})

    if staff:
        part("monday", link)
    out["staff"] = staff
    return out
