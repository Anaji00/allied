"""Each person's language (English, Mandarin or Vietnamese): comments, form questions and notes show in it."""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.core.security import require_manager, require_viewer
from app.services import bridge

router = APIRouter()
OPTIONS = [{"code": k, "label": v} for k, v in bridge.LANGS.items()]


@router.get("/me", summary="My language, and the choices")
def mine(user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return {"language": bridge.language_of(user.get("email", "")), "options": OPTIONS}


@router.put("/me", summary="Choose my language")
def set_mine(body: Dict[str, Any] = Body(...), user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        return {"language": bridge.set_language(user.get("email", ""), str(body.get("language", ""))), "options": OPTIONS}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.put("/people/{email}", summary="Set someone's language (managers)")
def set_theirs(email: str, body: Dict[str, Any] = Body(...), _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return {"email": email.lower(), "language": bridge.set_language(email, str(body.get("language", "")))}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
