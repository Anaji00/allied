"""Task forms: what an engineer must fill in properly before a task can be submitted.

Managers edit the templates. `check` tells the screen what is still missing, live, without submitting anything."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.core.audit import audit_log
from app.core.security import require_manager, require_operator, require_viewer
from app.services.coordination import forms

router = APIRouter()


def _audit(action: str, template_id: str, user: dict) -> None:
    audit_log(action, "task_form", template_id, actor_email=user.get("email", "unknown"), actor_role=user.get("role", ""))


@router.get("/templates", summary="Every form, with its fields and instructions")
def templates(_user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    return forms.load()


@router.get("/for-milestone", summary="The form a milestone's submission must use, if any")
def for_milestone(name: str = Query(..., max_length=300), _user: dict = Depends(require_viewer)) -> Optional[Dict[str, Any]]:
    return forms.for_milestone(name)


@router.get("/templates/{template_id}", summary="One form")
def one(template_id: str, _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        return forms.get(template_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))


@router.get("/templates/{template_id}/translated", summary="A form's instructions, questions and choices in another language (the answers stay as they are)")
def translated(template_id: str, lang: str = Query(..., pattern="^(zh|vi)$"), user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services import bridge

    try:
        t = forms.get(template_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    texts, where = [t.get("instructions", "")], [("instructions", None, None)]
    for f in t["fields"]:
        texts += [f["label"], f.get("help", "")] + list(f.get("options") or [])
        where += [(f["key"], "label", None), (f["key"], "help", None)] + [(f["key"], "option", o) for o in f.get("options") or []]
    done = bridge.translate_many(texts, "en", lang, user.get("email", "system"))
    out: Dict[str, Any] = {"lang": lang, "name": bridge.translate_many([t["name"]], "en", lang)[0], "instructions": done[0], "fields": {}}
    for (key, part, opt), text in zip(where[1:], done[1:]):
        f = out["fields"].setdefault(key, {"label": None, "help": None, "options": {}})
        if part == "option":
            f["options"][opt] = text
        else:
            f[part] = text
    out["complete"] = all(v is not None for v in done if v is not None) and all(
        d is not None for d, tx in zip(done, texts) if tx.strip())
    return out


@router.post("/templates/{template_id}/check", summary="What is still missing from this filled-in form (nothing is submitted)")
def check(template_id: str, answers: Dict[str, Any] = Body(...), _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    try:
        template = forms.get(template_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    try:
        forms.validate(template, answers)
        return {"ok": True, "fields": {}}
    except forms.FormError as exc:
        return {"ok": False, "fields": exc.fields}


@router.post("/templates", status_code=status.HTTP_201_CREATED, summary="Add a form")
def add(body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        saved = forms.save({**body, "id": None})
    except (forms.TemplateError, TypeError, AttributeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    _audit("TASK_FORM_ADDED", saved["id"], user)
    return saved


@router.put("/templates/{template_id}", summary="Change a form (replaces it)")
def change(template_id: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        saved = forms.save({**body, "id": template_id})
    except (forms.TemplateError, TypeError, AttributeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    _audit("TASK_FORM_CHANGED", template_id, user)
    return saved


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a form")
def remove(template_id: str, user: dict = Depends(require_manager)) -> None:
    try:
        forms.remove(template_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    _audit("TASK_FORM_REMOVED", template_id, user)
