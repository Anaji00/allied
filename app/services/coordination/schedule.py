"""Behind-schedule checks, the leadership summary, and the worker that runs them.

Who is told what (owner first, leadership only when it matters):

| Situation                                   | Who                  | How urgent |
| ------------------------------------------- | -------------------- | ---------- |
| Milestone due within DUE_SOON_DAYS          | milestone owners     | info       |
| Milestone overdue                           | owners + project owner | warning  |
| Overdue by ESCALATE_AFTER_DAYS or more      | + leadership         | warning    |
| Design docs waiting for review too long     | reviewers            | info       |
| Project past its due date                   | owners + leadership  | critical   |
| Shipment late, slipped, missed departure,   | owner (+ leadership  | warning    |
|   or forwarder silent                       |   when late)         |            |
| Port free time ending (demurrage)           | owner + leadership   | critical   |

Each alert has a key per item and date, so it is sent once, not every check,
and it closes itself when the condition clears (done, delivered, deleted).
"""

import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.logging import logger
from app.services.coordination import notify as alerts
from app.services.coordination import projects, rfqs, shipments, vendors


def _when(days: int) -> str:
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"


def _late(days: int) -> str:
    return f"{days} day{'s' if days != 1 else ''} late"


def _docs_note(m: Dict[str, Any]) -> str:
    if not m.get("missing_docs"):
        return ""
    latest = m.get("latest_submission")
    if latest and latest["status"] == "CHANGES_REQUESTED":
        return "design docs sent back for changes"
    return "no design docs submitted"


def run_checks(on: Optional[date] = None) -> Dict[str, int]:
    on = on or alerts.today()
    sent = {"due_soon": 0, "overdue": 0, "escalated": 0, "review_waiting": 0, "project_overdue": 0, "shipments": 0,
            "unplanned": 0}
    data = projects.overview(on)

    def count(key: str, nid: Optional[int]) -> None:
        if nid:
            sent[key] += 1

    milestone_keys = ("ms-due-soon:", "ms-overdue:", "ms-escalate:")
    alerts.resolve_missing(milestone_keys, (m["item_id"] for p in data["projects"] for m in p["milestones"]))
    alerts.resolve_missing(("proj-overdue:",), (p["item_id"] for p in data["projects"]))

    for p in data["projects"]:
        if p.get("done"):
            alerts.resolve([f"proj-overdue:{p['item_id']}:"]
                           + [f"{k}{m['item_id']}:" for m in p["milestones"] for k in milestone_keys])
            continue
        if not p.get("overdue"):
            alerts.resolve([f"proj-overdue:{p['item_id']}:"])
        owners = p.get("owner_emails") or []
        link = f"/projects?project={p['item_id']}"
        for m in p["milestones"]:
            mid, name, state, days = m["item_id"], m["name"], m["state"], m["days"]
            mlink = f"/projects?milestone={mid}"
            who = m.get("owner_emails") or owners
            if state in ("done", "on_hold"):  # finished, or waiting on someone else: nothing to chase
                alerts.resolve([f"{k}{mid}:" for k in milestone_keys])
                continue
            if state != "due_soon":
                alerts.resolve([f"ms-due-soon:{mid}:"])
            docs = {
                "no design docs submitted": " Design documents: not submitted.",
                "design docs sent back for changes": " Design documents: sent back for changes, not resubmitted.",
            }.get(_docs_note(m), "")
            if state == "due_soon":
                count("due_soon", alerts.notify(
                    "milestone_due_soon", f"ms-due-soon:{mid}:{m['due_date']}",
                    f"Due {_when(days)}: {name} ({p['name']})",
                    f"'{name}' on project {p['name']} is due {m['due_date']}.{docs}",
                    to=who, link=mlink, project_item_id=p["item_id"], replaces=[f"ms-due-soon:{mid}:", f"ms-overdue:{mid}:", f"ms-escalate:{mid}:"],
                ))
            elif state == "overdue":
                late = -days
                owner_line = f"Owner: {', '.join(who) or 'nobody assigned on monday'}."
                if late >= settings.ESCALATE_AFTER_DAYS:
                    # One alert, not two: past the escalation point, the owner
                    # and leadership get the same single message.
                    count("escalated", alerts.notify(
                        "milestone_escalated", f"ms-escalate:{mid}:{m['due_date']}",
                        f"Escalated: {name} ({p['name']}) is {_late(late)}",
                        f"'{name}' on project {p['name']} was due {m['due_date']} and is still not done.{docs}\n{owner_line}",
                        severity="warning", to=list(who) + list(owners), include_leadership=True,
                        link=mlink, project_item_id=p["item_id"], replaces=[f"ms-due-soon:{mid}:", f"ms-overdue:{mid}:", f"ms-escalate:{mid}:"],
                    ))
                else:
                    count("overdue", alerts.notify(
                        "milestone_overdue", f"ms-overdue:{mid}:{m['due_date']}",
                        f"Behind schedule: {name} ({p['name']}) is {_late(late)}",
                        f"'{name}' on project {p['name']} was due {m['due_date']} and is not done.{docs}\n{owner_line}",
                        severity="warning", to=list(who) + list(owners), link=mlink, project_item_id=p["item_id"],
                        replaces=[f"ms-due-soon:{mid}:", f"ms-overdue:{mid}:", f"ms-escalate:{mid}:"],
                    ))
            if state == "waiting_review" and m.get("latest_submission"):
                sub = m["latest_submission"]
                waited = (on - shipments._local_day(sub["submitted_at"])).days
                if waited >= settings.REVIEW_WAIT_DAYS:
                    count("review_waiting", alerts.notify(
                        "review_waiting", f"review-wait:{sub['id']}:",
                        f"Waiting {waited} days for review: {name} ({p['name']})",
                        f"{sub['submitted_by']} submitted design documents for '{name}' {waited} days ago. "
                        "They are waiting for approval or changes.",
                        to=projects.reviewers_for(m, p), link=mlink, project_item_id=p["item_id"],
                    ))
        if p.get("overdue"):
            open_ms = [m["name"] for m in p["milestones"] if m["state"] != "done"]
            count("project_overdue", alerts.notify(
                "project_overdue", f"proj-overdue:{p['item_id']}:{p['due_date']}",
                f"Project past due: {p['name']}",
                f"Project {p['name']} was due {p['due_date']}. Still open: {', '.join(open_ms) or 'none listed'}.",
                severity="critical", to=owners, include_leadership=True, link=link, project_item_id=p["item_id"],
                replaces=[f"proj-overdue:{p['item_id']}:"],
            ))

    for s in shipments.list_shipments(include_finished=False):
        shipments.resolve_cleared(s)
        label, link, owner = shipments._label(s), f"/shipments?id={s['id']}", [s["owner_email"]] if s.get("owner_email") else []
        for flag in s["flags"]:
            if flag == "late":
                nid = alerts.notify("shipment_late", f"ship-late:{s['id']}:{s['eta_current']}",
                                    f"Shipment late: {label}", f"It was due {s['eta_current']} and has not arrived. "
                                    f"Last status: {s['status']}.", severity="warning", to=owner,
                                    include_leadership=True, link=link, project_item_id=s.get("project_item_id"),
                                    replaces=[f"ship-late:{s['id']}:"])
            elif flag.startswith("slipped"):
                nid = alerts.notify("shipment_slipped", f"ship-slip:{s['id']}:{s['eta_current']}",
                                    f"Shipment {flag}: {label}", f"ETA was {s['eta_original']}, now {s['eta_current']}.",
                                    severity="warning", to=owner, link=link, project_item_id=s.get("project_item_id"),
                                    replaces=[f"ship-slip:{s['id']}:"])
            elif flag == "missed departure":
                nid = alerts.notify("shipment_rolled", f"ship-rolled:{s['id']}:{s['etd']}",
                                    f"Missed departure: {label}", f"It was meant to leave {s['etd']} and is still "
                                    f"'{s['status']}'. It may have been rolled to a later sailing; ask the forwarder.",
                                    severity="warning", to=owner, link=link, project_item_id=s.get("project_item_id"),
                                    replaces=[f"ship-rolled:{s['id']}:"])
            elif flag.startswith("no update"):
                nid = alerts.notify("shipment_silent", f"ship-silent:{s['id']}:{s['last_update_at'][:10]}",
                                    f"Forwarder silent: {label}", f"{flag.capitalize()} (status {s['status']}). "
                                    "Ask the forwarder for an update.", to=owner, link=link,
                                    project_item_id=s.get("project_item_id"), replaces=[f"ship-silent:{s['id']}:"])
            else:  # port free time ending
                nid = alerts.notify("shipment_free_time", f"ship-free-time:{s['id']}:{s['free_time_ends']}",
                                    f"Port free time ends {s['free_time_ends']}: {label}",
                                    "After that, the port charges demurrage every day (often $75-300). "
                                    "Arrange pickup now.", severity="critical", to=owner, include_leadership=True,
                                    link=link, project_item_id=s.get("project_item_id"),
                                    replaces=[f"ship-free-time:{s['id']}:"])
            count("shipments", nid)
    sent["unplanned"] = _unplanned_checks(data, on)
    for name, check in (("vendors", vendors.run_checks), ("rfqs", rfqs.run_checks)):
        try:
            sent.update(check(on) if name == "vendors" else check())
        except Exception as exc:  # noqa: BLE001 - one failing check must not stop the others
            logger.error(f"{name} checks failed: {exc}")
    return sent


def unplanned(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Open milestones nobody can be chased on: no due date, or no owner."""
    out = []
    for p in data["projects"]:
        if p.get("done"):
            continue
        for m in p["milestones"]:
            if m["state"] in ("done", "waiting_review", "on_hold"):
                continue
            missing = [w for w, gap in (("no due date", not m.get("due_date")), ("no owner", not m.get("owner_emails"))) if gap]
            if missing:
                out.append({**m, "project_name": p["name"], "missing": missing})
    return out


def _unplanned_checks(data: Dict[str, Any], on: date) -> int:
    """One list a week per owner (leadership gets the unowned ones), not one alert per job."""
    if not settings.MILESTONE_UNPLANNED_ALERTS:
        return 0
    week = f"{on.isocalendar()[0]}w{on.isocalendar()[1]}"
    by_owner: Dict[str, List[Dict[str, Any]]] = {}
    for m in unplanned(data):
        for owner in m.get("owner_emails") or ["nobody"]:
            by_owner.setdefault(owner, []).append(m)
    alerts.resolve_missing(("ms-unplanned:",), (f"{o}|{week}" for o in by_owner))
    sent = 0
    for owner, jobs in by_owner.items():
        lines = [f"- {m['name']} ({m['project_name']}): {', '.join(m['missing'])}" for m in jobs[:40]]
        if len(jobs) > 40:
            lines.append(f"- and {len(jobs) - 40} more")
        whose = "Nobody is assigned to these open jobs" if owner == "nobody" else "These open jobs are yours"
        nid = alerts.notify(
            "milestone_unplanned", f"ms-unplanned:{owner}|{week}:",
            f"{len(jobs)} open job(s) with no due date or owner",
            f"{whose}, but they can't be tracked until they have a due date and an owner on monday.\n" + "\n".join(lines),
            to=[] if owner == "nobody" else [owner], include_leadership=owner == "nobody", link="/projects",
        )
        sent += int(bool(nid))
    return sent


# ---------------------------------------------------------------------------
# Leadership summary
# ---------------------------------------------------------------------------


def build_summary(on: Optional[date] = None) -> Dict[str, Any]:
    on = on or alerts.today()
    data = projects.overview(on)
    behind, due_week, review = [], [], []
    for p in data["projects"]:
        if p.get("done"):
            continue
        for m in p["milestones"]:
            line = f"{p['name']}: {m['name']}"
            if m["state"] == "overdue":
                note = _docs_note(m)
                behind.append(f"{line} ({_late(-m['days'])}{', ' + note if note else ''})")
            elif m["state"] in ("due_soon", "on_track") and m["days"] is not None and m["days"] <= 7:
                due_week.append(f"{line} (due {m['due_date']})")
            elif m["state"] == "waiting_review":
                review.append(f"{line} (submitted by {m['latest_submission']['submitted_by']})")
    ships = [f"{shipments._label(s)}: {', '.join(s['flags'])}" for s in shipments.list_shipments(False) if s["flags"]]
    open_alerts = [n for n in alerts.inbox(open_only=True, limit=200) if n["severity"] == "critical"]
    vendor_risks = vendors.risks_for_summary(on)
    late_quotes = rfqs.for_summary()
    gaps = unplanned(data)
    from app.services.coordination import autopilot

    drafts = autopilot.drafts_waiting()
    from app.services import mail_log

    mail_waiting = mail_log.for_summary()
    no_plan = [f"{m['project_name']}: {m['name']} ({', '.join(m['missing'])})" for m in gaps[:10]]
    if len(gaps) > 10:
        no_plan.append(f"and {len(gaps) - 10} more on the Projects screen")
    sections = [
        ("Behind schedule", behind), ("Vendors putting projects at risk", vendor_risks),
        ("Quotes overdue or not acknowledged", late_quotes), ("Shipments needing attention", ships),
        ("Waiting for review", review), ("Due in the next 7 days", due_week),
        ("Open with no due date or owner", no_plan),
        ("Messages drafted, waiting for approval in the assistant", drafts),
        ("Emails waiting on a person", mail_waiting),
    ]
    lines = [f"Allied operations summary for {on:%A %d %B %Y}", ""]
    if not any(items for _, items in sections):
        lines.append("Nothing is behind schedule. Nothing needs you today.")
    for title, items in sections:
        if items:
            lines.append(f"{title} ({len(gaps) if items is no_plan else len(items)})")
            lines += [f"  - {i}" for i in items]
            lines.append("")
    if open_alerts:
        lines.append(f"{len(open_alerts)} urgent alert(s) are not yet acknowledged in the app.")
    return {"behind": behind, "vendors": vendor_risks, "rfqs": late_quotes, "shipments": ships,
            "review": review, "due_week": due_week, "unplanned": len(gaps), "drafts": len(drafts),
            "urgent_open": len(open_alerts), "mail": len(mail_waiting), "text": "\n".join(lines).strip()}


def send_summary(on: Optional[date] = None, force: bool = False) -> Optional[int]:
    on = on or alerts.today()
    s = build_summary(on)
    headline = (f"{len(s['behind'])} behind, {len(s['vendors'])} vendor risk(s), {len(s['rfqs'])} late quote(s), "
                f"{len(s['shipments'])} shipment issue(s), {len(s['review'])} waiting for review")
    return alerts.notify(
        "daily_summary", f"summary:{on}{':manual:' + str(time.time()) if force else ''}",
        f"Daily summary: {headline}", s["text"], to=alerts.summary_recipients(), link="/projects",
    )


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class CoordinationWorker:
    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._next_sync = 0.0
        self._next_check = 0.0
        self._next_autopilot = 0.0
        self._next_change_check = 0.0
        self._summary_date: Optional[date] = None
        self.last: Dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="allied-coordination", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _step(self, name: str, fn) -> None:
        try:
            self.last[name] = {"at": alerts.now_local().isoformat(), "result": fn()}
        except Exception as exc:  # noqa: BLE001 - the loop outlives any single failure
            self.last[name] = {"at": alerts.now_local().isoformat(), "error": f"{type(exc).__name__}: {exc}"}
            logger.exception(f"Coordination {name} failed: {exc}")

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            if settings.PROJECTS_POLL_SECONDS > 0 and now >= self._next_sync:
                self._next_sync = now + max(60, settings.PROJECTS_POLL_SECONDS)
                if settings.MONDAY_CHANGE_CHECK_SECONDS > 0:
                    from app.services.coordination import changes

                    # Note each board's last change first, so the change check only
                    # reads boards that move after this full read.
                    self._step("changes_mark", changes.changed)
                    self._next_change_check = now + changes.interval()
                self._step("sync", projects.sync_projects)
                self._step("vendor_sync", vendors.sync_vendor_board)
                if settings.MONDAY_RFQ_BOARD_ID:
                    from app.services.coordination import autopilot

                    self._step("rfq_board", lambda: autopilot.run("rfq_board"))
                if settings.MONDAY_REFERENCE_BOARD_IDS or settings.MONDAY_DOC_IDS:
                    from app.services.coordination import boards

                    self._step("reference_boards", boards.read_all_reference)
            elif settings.MONDAY_CHANGE_CHECK_SECONDS > 0 and now >= self._next_change_check:
                from app.services.coordination import changes

                self._next_change_check = now + changes.interval()
                self._step("changes", changes.check)
            if now >= self._next_check:
                self._next_check = now + max(60, settings.SCHEDULE_CHECK_SECONDS)
                self._step("checks", run_checks)
            if settings.AUTOPILOT_ENABLED and now >= self._next_autopilot:
                from app.services.coordination import autopilot

                self._next_autopilot = now + max(300, settings.AUTOPILOT_SECONDS)
                self._step("autopilot", lambda: {k: len(v["did"]) for k, v in autopilot.run()["plays"].items()})
                from app.services.coordination import recipes

                self._step("date_recipes", lambda: {"did": len(recipes.run_dates())})
            self._step("held", alerts.release_held)
            local = alerts.now_local()
            if local.weekday() < 5 and local.hour >= settings.DIGEST_HOUR and self._summary_date != local.date():
                # The key per date also makes this once a day across restarts.
                self._step("summary", send_summary)
                if "error" not in self.last.get("summary", {}):  # retried next loop if it failed
                    self._summary_date = local.date()
            self._stop.wait(30)


coordination_worker = CoordinationWorker()
