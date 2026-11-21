"""The secretary's free-standing email drafts: a draft that is not tied to a record, waiting in the same approval queue. Nothing here sends mail."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.security import require_operator
from app.services import actions

router = APIRouter()


class SecretaryDraft(BaseModel):
    to: str = Field(..., max_length=2000)
    subject: str = Field(..., max_length=300)
    body: str = Field(..., max_length=9000)
    audience: str = Field("internal", max_length=16)
    link_kind: str = Field("", max_length=16)
    link_ref: str = Field("", max_length=80)


@router.post("/drafts", summary="Make an email draft that is not tied to a record; it waits in the approval queue and is never sent here", status_code=201)
def make_draft(body: SecretaryDraft, user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        return actions.free_email_draft(user, body.to, body.subject, body.body, body.audience, body.link_kind, body.link_ref)
    except actions.ActionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\""))
