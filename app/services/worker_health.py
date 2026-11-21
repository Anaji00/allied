"""Is the background work really running? One answer for the health page: each worker (the coordination loop, the mailbox reader, the monday inbox worker) with its last success,
its last error, and whether it is red. A worker is red when its last step failed, or when its last success is older than three times how often it should run.
One alert to the managers says which are red, and it closes itself when they are all well again. Reading only, apart from that one alert.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.logging import logger

ALERT_KEY = "worker-health"
_STEP_SECONDS = {"sync": "PROJECTS_POLL_SECONDS", "vendor_sync": "PROJECTS_POLL_SECONDS", "checks": "SCHEDULE_CHECK_SECONDS", "autopilot": "AUTOPILOT_SECONDS"}


def _at(value: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _late(iso: Any, interval: float, now: datetime) -> bool:
    d = _at(iso)
    return bool(d) and (now - d).total_seconds() > 3 * max(1.0, interval)


def _worker(name: str, label: str, expected: bool, running: bool, ok_at: Any, err: Optional[str], err_at: Any, interval: float, now: datetime, detail: str = "") -> Dict[str, Any]:
    status, why = "off", detail
    if expected:
        failed_since_ok = bool(err) and (not _at(ok_at) or (_at(err_at) or now) >= _at(ok_at))
        if failed_since_ok:
            status, why = "red", f"The last step failed: {err}"
        elif not running:
            status, why = "red", "It is not running."
        elif ok_at and _late(ok_at, interval, now):
            status, why = "red", f"No success since {str(ok_at)[:16].replace('T', ' ')} (it should run every {int(interval)} s)."
        else:
            status, why = "green", detail or "Working."
    return {"name": name, "label": label, "expected": expected, "running": running, "status": status, "why": why, "last_success_at": ok_at, "last_error": err, "last_error_at": err_at, "interval_seconds": int(interval)}


def snapshot() -> Dict[str, Any]:
    from app.services.coordination.schedule import coordination_worker
    from app.services.mailbox_poller import mailbox_poller
    from app.services.monday_events import monday_inbox_worker

    now = datetime.now(timezone.utc)
    out: List[Dict[str, Any]] = []

    last = dict(coordination_worker.last)
    steps = []
    for step, rec in last.items():
        interval = float(getattr(settings, _STEP_SECONDS.get(step, ""), 0) or 0)
        steps.append({"step": step, "at": rec.get("at"), "error": rec.get("error"), "interval": interval})
    bad = [s for s in steps if s["error"]]
    stale = [s for s in steps if not s["error"] and s["interval"] and _late(s["at"], s["interval"], now)]
    good_at = max((s["at"] for s in steps if not s["error"] and s["at"]), default=None)
    worst = (bad or stale or [None])[0]
    c = _worker("coordination", "Boards, checks and autopilot loop", bool(steps) or coordination_worker.running, coordination_worker.running or bool(steps), good_at,
                (f"{worst['step']}: {worst['error']}" if bad else None), worst["at"] if bad else None, 1800, now, "Working.")
    if stale and c["status"] != "red":
        c.update(status="red", why=f"The {stale[0]['step']} step has not succeeded since {str(stale[0]['at'])[:16].replace('T', ' ')}.")
    c["steps"] = [{"step": s["step"], "ok": not s["error"], "at": s["at"], "error": s["error"]} for s in steps]
    out.append(c)

    st = mailbox_poller.status()
    out.append(_worker("mailbox_poller", "Mailbox reader", bool(st.get("enabled")), bool(st.get("running")), st.get("last_ok_at"), st.get("last_error"), st.get("last_error_at"),
                       max(15, settings.IMAP_POLL_SECONDS), now, st.get("detail") or ""))

    out.append(_worker("monday_inbox_worker", "monday inbox worker", monday_inbox_worker.running or bool(monday_inbox_worker.last_sweep_at), monday_inbox_worker.running,
                       monday_inbox_worker.last_sweep_at, getattr(monday_inbox_worker, "last_error", None), getattr(monday_inbox_worker, "last_error_at", None),
                       max(5, settings.WEBHOOK_SWEEP_SECONDS), now, "Working."))
    red = [w for w in out if w["status"] == "red"]
    return {"status": "red" if red else ("green" if any(w["status"] == "green" for w in out) else "idle"), "red": [w["name"] for w in red], "workers": out, "checked_at": now.isoformat()}


def check_and_alert() -> Dict[str, Any]:
    """Raises ONE manager alert while any worker is red (the same alert, not a new one each time) and closes it when they are all well. Called on every pass of the coordination loop."""
    from app.services.coordination import notify

    snap = snapshot()
    try:
        if snap["red"]:
            lines = "; ".join(f"{w['label']}: {w['why']}" for w in snap["workers"] if w["status"] == "red")
            notify.notify("worker_health", ALERT_KEY, "A background job has stopped working", lines[:900], severity="warning", include_leadership=True, link="/system")
        else:
            notify.resolve([ALERT_KEY])
    except Exception as exc:  # noqa: BLE001 - reporting must never break the loop it reports on
        logger.warning(f"Worker health alert failed: {type(exc).__name__}")
    return {"status": snap["status"], "red": snap["red"]}
