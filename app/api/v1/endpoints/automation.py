"""Shows what the system is doing on its own, and what still needs a person.

Every other screen reports on records. This one reports on the automation
itself: which links are live, which are drafting only, and how much hand-work
is left on each order as a result. Staff need to know whether a drafted
dispute actually reached the supplier, and that answer lives here.
"""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from app.core.security import require_operator, require_viewer
from app.services.mailbox_poller import mailbox_poller
from app.services.outbound_email_service import outbound_email_service

router = APIRouter()


# The hand-work model quoted to Allied in the operating guide, kept here so
# the screen and the document cannot drift apart.
#
# An order absorbs about 120 minutes by hand. The core system -- reading the
# order, matching it, splitting it, running the checks, writing the Sage
# documents and the packing slip -- removes 98 of those on its own. The four
# links below account for the 22 that are left, of which 5 is the review a
# person should always keep.
_BASELINE_MINUTES = 120
_CORE_MINUTES_REMOVED = 98
_REVIEW_MINUTES = 5
_LINK_MINUTES = {
    "mailbox": 2,          # somebody forwards the order email
    "sage_write": 2,       # somebody clicks import in Sage
    "outbound_email": 5,   # somebody emails the POs to suppliers
    "invoice_scanning": 8, # somebody keys the supplier bill in to get it checked
}


@router.get("/status", summary="What is automated right now")
def automation_status(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    """Reports each automated link, whether it is live, and the hand-work left."""
    from app.config import settings

    mail = outbound_email_service.status()
    inbox = mailbox_poller.status()

    links: List[Dict[str, Any]] = [
        {
            "key": "invoice_scanning",
            "name": "Supplier bills checked on arrival",
            "live": True,
            "detail": (
                "Reading a bill runs the line-by-line match against the purchase order "
                "that authorised it, including freight."
            ),
            "saves_minutes": _LINK_MINUTES["invoice_scanning"],
            "needs": None,
        },
        {
            "key": "mailbox",
            "name": "Orders collected from the mailbox",
            "live": bool(inbox["enabled"] and inbox["running"]),
            "detail": inbox["detail"],
            "saves_minutes": _LINK_MINUTES["mailbox"],
            "needs": None if inbox["enabled"] else "A mailbox address and password.",
        },
        {
            "key": "outbound_email",
            "name": "Purchase orders and disputes sent",
            "live": mail["send_enabled"],
            "detail": mail["detail"],
            "saves_minutes": _LINK_MINUTES["outbound_email"],
            "needs": None if mail["send_enabled"] else "A mail server to send through.",
        },
        {
            "key": "sage_write",
            "name": "Written straight into Sage",
            "live": not settings.SAGE_SIMULATION_MODE,
            "detail": (
                "Orders are written directly into Sage."
                if not settings.SAGE_SIMULATION_MODE
                else "Sage files are produced correctly; someone still clicks import."
            ),
            "saves_minutes": _LINK_MINUTES["sage_write"],
            "needs": None if not settings.SAGE_SIMULATION_MODE else "Sage 50 desktop on this machine.",
        },
        {
            # Not part of the per-order minutes: the board is how work is
            # tracked, not a step somebody performs on every order. It is
            # reported because the dashboard must not claim it is connected
            # when it is not.
            "key": "monday",
            "name": "Monday board updated",
            "live": settings.monday_live,
            "detail": (
                "Job status and customer updates post to the board automatically."
                if settings.monday_live
                else "Board updates are recorded here rather than posted."
            ),
            "saves_minutes": 0,
            "needs": None if settings.MONDAY_API_KEY else "A Monday API key and the board id.",
        },
    ]

    # What the core system removes is fixed; the links chip away at the rest.
    # The review never goes, which is deliberate and worth showing as a floor
    # rather than as something still to be automated.
    unconnected = _BASELINE_MINUTES - _CORE_MINUTES_REMOVED
    saved_by_links = sum(link["saves_minutes"] for link in links if link["live"])
    remaining = max(_REVIEW_MINUTES, unconnected - saved_by_links)
    best_possible = _BASELINE_MINUTES - _CORE_MINUTES_REMOVED - sum(_LINK_MINUTES.values())

    return {
        "links": links,
        "links_live": sum(1 for link in links if link["live"]),
        "links_total": len(links),
        "baseline_minutes_per_order": _BASELINE_MINUTES,
        "hand_minutes_remaining": remaining,
        "hand_minutes_if_all_connected": max(_REVIEW_MINUTES, best_possible),
        "review_minutes": _REVIEW_MINUTES,
        "busy_work_removed_pct": round(
            (_BASELINE_MINUTES - remaining) / _BASELINE_MINUTES * 100, 1
        ),
        "mail": mail,
        "mailbox": inbox,
    }


@router.get("/outbox", summary="Mail the system has written")
def outbox(
    limit: int = Query(50, ge=1, le=200),
    _user: dict = Depends(require_viewer),
) -> Dict[str, Any]:
    """Every message drafted, whether or not it was sent, and why."""
    return {
        "status": outbound_email_service.status(),
        "messages": outbound_email_service.get_outbox(limit=limit),
    }


@router.post("/mailbox/poll", summary="Check the mailbox now")
def poll_mailbox(_user: dict = Depends(require_operator)) -> Dict[str, Any]:
    """Reads the order mailbox immediately rather than waiting for the timer."""
    if not mailbox_poller.enabled:
        return {
            "ok": False,
            "detail": mailbox_poller.status()["detail"],
            "ingested": 0,
            "orders_created": 0,
        }
    try:
        result = mailbox_poller.poll_once()
        return {"ok": True, "detail": "Mailbox checked.", **result}
    except Exception as exc:  # noqa: BLE001 - reported, never raised at the user
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}", "ingested": 0, "orders_created": 0}
