"""The chat assistant: questions answered through tools, changes confirmed by a person."""

from typing import Any, Dict, List, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

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
