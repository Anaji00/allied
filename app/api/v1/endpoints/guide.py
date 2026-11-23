"""Plain-language helpers for the screens: what needs attention, the Secretary start page, what I may do, the task finder, the glossary, and a typed request that makes drafts only."""
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.security import require_manager, require_operator, require_viewer
from app.services import guide

router = APIRouter()


class SecretaryIn(BaseModel):
    prompt: str = Field(..., max_length=500, description="What you want, in your own words")


class TermIn(BaseModel):
    plain: str = Field(..., min_length=5, max_length=400)


@router.get("/control/attention", summary="The Control room in one call: quiet shipments, payments due (managers), gates waiting, drawings not yet verified, drafts waiting, backups")
def control_attention(user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return guide.control_attention(user)


@router.get("/secretary/summary", summary="The Secretary start page in one call: mail needing a reply, drafts waiting, order emails, bills to check and money owed to us (managers), late orders")
def secretary_summary(user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return guide.secretary_summary(user)


@router.post("/ai/secretary", summary="Type what you want (reply to someone, chase what is owed, my summary): drafts only, nothing is sent")
def ai_secretary(body: SecretaryIn, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return guide.secretary_request(body.prompt, user)


@router.get("/me/can", summary="What the signed-in person may do, as plain flags with the reason for each no")
def me_can(user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return guide.me_can(user)


@router.get("/search/tasks", summary="Search by what you want to DO: plain-language tasks with where to go and whether you may")
def search_tasks(q: str = Query("", max_length=100), user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return {"q": q, "tasks": guide.tasks_for(q, user)}


@router.get("/glossary/app", summary="Plain definitions of the words the app uses, for tooltips")
def glossary(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return {"terms": guide.glossary()}


@router.put("/glossary/app/{term}", summary="A manager changes or adds a plain definition")
def put_term(term: str, body: TermIn, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return guide.set_term(term, body.plain, user)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
