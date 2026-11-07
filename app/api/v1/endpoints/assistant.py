"""The chat assistant: questions answered through tools, changes confirmed by a person."""

import json
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.core.logging import logger
from app.core.security import require_viewer
from app.services.ai.providers import AIRateLimitedError, AIUnavailableError
from app.services.assistant import agent

router = APIRouter()


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=8000)


class ChatIn(BaseModel):
    messages: List[Message] = Field(..., min_length=1, max_length=40)


def _fail(exc: Exception):
    if isinstance(exc, AIRateLimitedError):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    if isinstance(exc, AIUnavailableError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    raise exc


# Plain defs: FastAPI runs them in a worker thread while the AI answers.
@router.post("/chat", summary="Ask the assistant; changes come back as proposals to confirm")
def chat(body: ChatIn, user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        return agent.chat([m.model_dump() for m in body.messages], user)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


def _plain_error(exc: Exception) -> str:
    """A sentence a person can read, for a failure while the answer was being made."""
    if isinstance(exc, AIRateLimitedError):
        return "The assistant is busy for a moment. Try again in a few seconds."
    if isinstance(exc, AIUnavailableError):
        return "The assistant isn't available right now. You can still use the screens."
    if isinstance(exc, (LookupError, ValueError, PermissionError)):
        return str(exc)
    return "Something went wrong and nothing was changed. Please try again."


@router.post("/chat/stream", summary="Ask the assistant and watch it work: step lines, then the reply and cards (server-sent events)")
def chat_stream(body: ChatIn, user: dict = Depends(require_viewer)):
    """Same question and same rules as /chat. Each event is one JSON object on a `data:` line:
    {"type":"step","text":...} as each step starts, then {"type":"done", ...the /chat reply} or
    {"type":"error","message":...}. A `: ping` comment goes out every 10 seconds so proxies keep the
    connection open. Nothing changes until a click, exactly as with /chat."""
    import queue
    import threading

    from fastapi.responses import StreamingResponse

    events: "queue.Queue" = queue.Queue()
    messages = [m.model_dump() for m in body.messages]

    def work() -> None:
        try:
            events.put({"type": "done", **agent.chat(messages, user, progress=lambda t: events.put({"type": "step", "text": t}))})
        except Exception as exc:  # noqa: BLE001 - told to the person in plain words
            if not isinstance(exc, (AIUnavailableError, LookupError, ValueError, PermissionError)):
                logger.warning(f"Assistant stream failed: {type(exc).__name__}: {exc}")
            events.put({"type": "error", "message": _plain_error(exc)})

    def stream():
        threading.Thread(target=work, daemon=True, name="assistant-stream").start()
        # At once, so the screen has something to show.
        yield 'data: {"type":"step","text":"Reading your request…"}\n\n'
        while True:
            try:
                event = events.get(timeout=10)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(event, default=str)}\n\n"
            if event["type"] in ("done", "error"):
                return

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


class QuickIn(BaseModel):
    args: Dict[str, Any] = Field(default_factory=dict)


@router.post("/lookup/{tool}", summary="Run one lookup directly (works with the AI off)")
def lookup(tool: str, body: QuickIn, user: dict = Depends(require_viewer)):
    try:
        return agent.quick(tool, body.args, user)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


class DecisionIn(BaseModel):
    confirm: bool


class BatchIn(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=50)
    confirm: bool


@router.post("/actions/batch", summary="Send or cancel several drafts at once (keyboard review)")
def decide_batch(body: BatchIn, user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    """Each one is decided on its own: one that fails or was already decided doesn't stop the rest."""
    out = []
    for action_id in dict.fromkeys(body.ids):  # each id once
        try:
            out.append(agent.decide(action_id, body.confirm, user))
        except (LookupError, ValueError, PermissionError) as exc:
            out.append({"id": action_id, "status": "SKIPPED", "error": str(exc)})
    return {"results": out, "done": sum(1 for r in out if r["status"] in ("DONE", "CANCELLED"))}


class EditIn(BaseModel):
    changes: Dict[str, Any] = Field(..., description="The fields to change, e.g. {subject, body} for an email draft")


@router.patch("/actions/{action_id}", summary="Edit a waiting draft or proposal before sending it")
def edit(action_id: str, body: EditIn, user: dict = Depends(require_viewer)):
    try:
        return agent.edit_action(action_id, body.changes, user)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


@router.post("/voice-command", summary="Speak a task: written out, done by the assistant, answered briefly with cards")
def voice_command(file: Optional[UploadFile] = File(None, description="The recording (webm, ogg, m4a, mp4, mp3, wav)"),
                  transcript: str = Form("", max_length=4000, description="Or the words already dictated"),
                  messages: str = Form("", max_length=60000, description="The conversation so far, as JSON"),
                  confirm_ids: str = Form("", max_length=2000, description="Waiting proposals a spoken 'confirm' would carry out"),
                  user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services.assistant import voice
    from app.services.coordination import eng_notes

    audio = None
    if file is not None:
        chunks, size = [], 0
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > eng_notes.MAX_AUDIO_BYTES:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Keep voice commands under 25 MB.")
            chunks.append(chunk)
        audio = b"".join(chunks) or None
    try:
        history = json.loads(messages) if messages.strip() else []
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="messages must be JSON.")
    try:
        return voice.command(user, audio, (file.filename if file else "") or "", transcript, history if isinstance(history, list) else [],
                             [x.strip() for x in confirm_ids.split(",") if x.strip()])
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


@router.post("/actions/{action_id}", summary="Confirm or cancel a change the assistant proposed")
def decide(action_id: str, body: DecisionIn, user: dict = Depends(require_viewer)):
    try:
        return agent.decide(action_id, body.confirm, user)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


@router.get("/actions", summary="My proposals still waiting for a decision")
def pending(user: dict = Depends(require_viewer)):
    return {"actions": agent.pending(user)}


@router.get("/connectors", summary="What the assistant can reach, and whether each connection works")
def connectors(user: dict = Depends(require_viewer)):
    return {"connectors": agent.connectors(user)}


# ---------------------------------------------------------------------------
# Approve from the phone: a signed link per draft and reviewer (see services/assistant/quick.py)
# ---------------------------------------------------------------------------

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Allied: draft to approve</title><style>
body{{font:16px/1.45 system-ui,sans-serif;margin:0;padding:16px;max-width:640px;background:#fafaf9;color:#1c1917}}
h1{{font-size:18px;margin:0 0 12px}} .box{{background:#fff;border:1px solid #e7e5e4;border-radius:8px;padding:12px;margin:0 0 12px}}
pre{{white-space:pre-wrap;font:inherit;margin:0}} ul{{margin:0;padding-left:18px}} .meta{{color:#57534e;font-size:14px}}
button{{font:inherit;padding:12px 18px;border-radius:8px;border:0;margin-right:8px}} .send{{background:#166534;color:#fff}}
.cancel{{background:#e7e5e4}} @media (prefers-color-scheme:dark){{body{{background:#1c1917;color:#f5f5f4}}
.box{{background:#292524;border-color:#44403c}} .meta{{color:#a8a29e}} .cancel{{background:#44403c;color:#f5f5f4}}}}
</style></head><body>{content}</body></html>"""


def _html(content: str, code: int = 200):
    from fastapi.responses import HTMLResponse

    return HTMLResponse(_PAGE.format(content=content), status_code=code,
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _quick_user(action_id: str, e: str, x: str, s: str):
    from app.services.assistant import quick

    email = quick.check(action_id, e, x, s)
    who = quick.reviewer(email) if email else None
    return who


@router.get("/quick/{action_id}", include_in_schema=False)
def quick_page(action_id: str, e: str = "", x: str = "", s: str = ""):
    """Shows the draft and why; opening it decides nothing."""
    from html import escape

    from app.core.database import SessionLocal
    from app.models.db_models import AssistantActionDB

    who = _quick_user(action_id, e, x, s)
    if who is None:
        return _html("<h1>This link has lapsed or isn't valid.</h1><p>Open Allied Operations to see the draft.</p>", 403)
    with SessionLocal() as db:
        row = db.get(AssistantActionDB, action_id)
        if row is None or not agent._yours(row, who):
            return _html("<h1>No such draft.</h1>", 404)
        status_, summary, args = row.status, row.summary, dict(row.args or {})
    if status_ != "PENDING":
        return _html(f"<h1>Already {escape(status_.lower())}.</h1><p>Nothing more to do.</p>")
    shown = {k: v for k, v in args.items() if not k.startswith("_")}
    email_part = ""
    if {"to", "subject", "body"} <= set(shown):
        email_part = (f"<div class='box'><div class='meta'>To {escape(str(shown['to']))}</div>"
                      f"<p><b>{escape(str(shown['subject']))}</b></p><pre>{escape(str(shown['body']))}</pre></div>")
    why = "".join(f"<li>{escape(str(w))}</li>" for w in args.get("_why") or [])
    hidden = "".join(f"<input type='hidden' name='{k}' value='{escape(v)}'>" for k, v in (("e", e), ("x", x), ("s", s)))
    return _html(f"<h1>{escape(summary)}</h1>{email_part}"
                 + (f"<div class='box'><div class='meta'>Why</div><ul>{why}</ul></div>" if why else "")
                 + f"<form method='post'>{hidden}<button class='send' name='decision' value='send'>Send</button>"
                   f"<button class='cancel' name='decision' value='cancel'>Cancel</button></form>")


@router.post("/quick/{action_id}", include_in_schema=False)
def quick_decide(action_id: str, e: str = Form(""), x: str = Form(""), s: str = Form(""), decision: str = Form("")):
    """The decision itself (from the page's buttons or the push's Send button)."""
    from html import escape

    who = _quick_user(action_id, e, x, s)
    if who is None:
        return _html("<h1>This link has lapsed or isn't valid.</h1>", 403)
    if decision not in ("send", "cancel"):
        return _html("<h1>Choose Send or Cancel.</h1>", 400)
    try:
        out = agent.decide(action_id, decision == "send", who)
    except (LookupError, ValueError, PermissionError) as exc:
        return _html(f"<h1>{escape(str(exc))}</h1>", 409)
    said = {"DONE": "Done.", "CANCELLED": "Cancelled. Nothing was sent.", "FAILED": "It failed; nothing was sent."}
    detail = (out.get("result") or {}).get("done") or (out.get("result") or {}).get("error") or ""
    return _html(f"<h1>{said.get(out['status'], out['status'])}</h1><p>{escape(str(detail))}</p>")
