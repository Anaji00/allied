"""Monday.com webhook ingress, inbox console and replay.

The ingress does three things and nothing else: authenticate, store the event
durably (deduplicated), acknowledge. Business work happens off the request in
app.services.monday_events, so a slow Sage write can never push monday past
its delivery timeout and into a retry storm.
"""

import json
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status

from app.config import settings
from app.core.audit import audit_log
from app.core.logging import logger
from app.core.security import require_operator, require_viewer, verify_monday_webhook
from app.services import monday_events

router = APIRouter()


@router.post(
    "/monday",
    status_code=status.HTTP_200_OK,
    summary="Monday.com Webhook Ingress Handler",
)
async def monday_webhook_ingress(
    request: Request,
    background_tasks: BackgroundTasks,
    auth_method: str = Depends(verify_monday_webhook),
) -> Dict[str, Any]:
    """Echoes the setup challenge; otherwise stores the event and returns at once."""
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Body is not JSON.")
    if not isinstance(body, dict):
        return {"status": "ignored"}

    if body.get("challenge"):
        logger.info("Acknowledging monday.com webhook challenge.")
        return {"challenge": body["challenge"]}

    event = body.get("event")
    if not isinstance(event, dict) or not (event.get("pulseId") or event.get("boardId")):
        return {"status": "ignored"}

    event_id, duplicate = monday_events.record_event(event, raw)
    if duplicate:
        # monday retries anything it thinks failed; acknowledging the repeat
        # stops the retries without doing the work twice.
        logger.info(f"Duplicate monday delivery for event #{event_id}; acknowledged.")
        return {"status": "duplicate", "event_id": event_id}

    background_tasks.add_task(monday_events.process_event, event_id)
    return {"status": "enqueued", "event_id": event_id, "pulse_id": event.get("pulseId"), "auth": auth_method}


@router.post(
    "/monday/simulate-trigger",
    summary="Simulate Monday.com Status Change Event",
)
def simulate_monday_status_trigger(
    pulse_id: int,
    status_label: str,
    background_tasks: BackgroundTasks,
    pulse_name: Optional[str] = None,
    _user: dict = Depends(require_operator),
):
    """Runs a status change through the real inbox, as if monday had sent it.

    Requires an authenticated operator: this path skips webhook
    authentication, so leaving it open would let anyone trigger shipments.
    """
    boards = [b.strip() for b in str(settings.MONDAY_BOARD_ID or "").split(",") if b.strip()]
    simulated_event = {
        "type": "update_column_value",
        "triggerUuid": f"sim-{uuid.uuid4().hex}",
        "pulseId": pulse_id,
        "pulseName": pulse_name or f"Simulated item {pulse_id}",
        "boardId": boards[0] if boards else 0,
        "columnId": settings.MONDAY_STATUS_COLUMN_ID,
        "columnType": "color",
        "value": {"label": {"text": status_label}},
        "app": "simulator",
    }
    event_id, _ = monday_events.record_event(simulated_event)
    background_tasks.add_task(monday_events.process_event, event_id)
    return {
        "status": "simulation_enqueued",
        "event_id": event_id,
        "pulse_id": pulse_id,
        "simulated_status": status_label,
        "routed_to": monday_events.decide(simulated_event)[0],
    }


@router.get("/monday/events", summary="Recent monday.com events and their outcome")
def list_monday_events(
    limit: int = Query(50, ge=1, le=500),
    status_filter: Optional[str] = Query(None, alias="status"),
    _user: dict = Depends(require_viewer),
) -> Dict[str, Any]:
    return {"stats": monday_events.stats(), "events": monday_events.list_events(limit, status_filter)}


@router.post("/monday/events/{event_id}/replay", summary="Re-run an event after fixing the board item")
def replay_monday_event(
    event_id: int,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_operator),
) -> Dict[str, Any]:
    if not monday_events.replay(event_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Event is not in a replayable state.")
    audit_log(
        action="REPLAY",
        resource_type="MONDAY_EVENT",
        resource_id=str(event_id),
        actor_email=user.get("email", "system"),
        actor_role=user.get("role", "SYSTEM"),
        detail="Operator replayed a monday.com webhook event",
    )
    background_tasks.add_task(monday_events.process_event, event_id)
    return {"status": "requeued", "event_id": event_id}


@router.get("/monday/setup", summary="What the monday.com link needs and has")
def monday_setup(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    """A checklist the console renders, so missing configuration is visible."""
    checks = [
        {
            "key": "api_key",
            "label": "API token",
            "ok": bool(settings.MONDAY_API_KEY),
            "hint": "Set MONDAY_API_KEY (a token whose owner can edit the order board).",
        },
        {
            "key": "live",
            "label": "Board writes switched on",
            "ok": settings.monday_live,
            "hint": "Set MONDAY_SIMULATION_MODE=False once the token and board are confirmed.",
        },
        {
            "key": "auth",
            "label": "Webhook authentication",
            "ok": bool(settings.MONDAY_WEBHOOK_SECRET or settings.MONDAY_WEBHOOK_TOKEN),
            "hint": "Set MONDAY_WEBHOOK_TOKEN and register the URL as .../webhooks/monday?token=<value>, "
            "or MONDAY_WEBHOOK_SECRET for an app-created webhook.",
        },
        {
            "key": "board",
            "label": "Order board restricted",
            "ok": bool(settings.MONDAY_BOARD_ID),
            "hint": "Set MONDAY_BOARD_ID so events from other boards are ignored.",
        },
        {
            "key": "loop_guard",
            "label": "Own changes ignored",
            "ok": bool(settings.MONDAY_BOT_USER_ID),
            "hint": "Set MONDAY_BOT_USER_ID to the token owner's monday user id.",
        },
        {
            "key": "write_back",
            "label": "Sales order number written to the board",
            "ok": bool(settings.MONDAY_SO_COLUMN_ID),
            "hint": "Set MONDAY_SO_COLUMN_ID to a text column id.",
        },
    ]
    return {
        "checks": checks,
        "ready": all(c["ok"] for c in checks[:3]),
        "routing": {
            "status_column": settings.MONDAY_STATUS_COLUMN_ID,
            "create_order_labels": sorted(settings.label_set(settings.MONDAY_LABELS_CREATE_ORDER)),
            "release_po_labels": sorted(settings.label_set(settings.MONDAY_LABELS_RELEASE_PO)),
            "dispatch_labels": sorted(settings.label_set(settings.MONDAY_LABELS_DISPATCH)),
            "create_on_new_item": settings.MONDAY_CREATE_ORDER_ON_NEW_ITEM,
        },
        "api_version": settings.MONDAY_API_VERSION,
        "worker_running": monday_events.monday_inbox_worker.running,
        # Allied's system finds board changes itself; monday runs no automations.
        "board_reader": {
            "every_seconds": settings.MONDAY_POLL_SECONDS,
            "last_read_at": monday_events.monday_inbox_worker.last_read_at,
            "last_read": monday_events.monday_inbox_worker.last_read,
        },
    }
