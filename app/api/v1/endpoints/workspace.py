"""Project templates, "my items" and saved filters."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status

from app.core.audit import audit_log
from app.core.security import require_manager, require_operator, require_viewer
from app.services import exports, my_views
from app.services.coordination import project_templates as pt

router = APIRouter()


def _is_manager(user: dict) -> bool:
    return user.get("role") in ("ADMIN", "MANAGER")


def _nf(exc: Exception):
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))


def _bad(exc: Exception):
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ------------------------------------------------------------------ project templates

@router.get("/project-templates", summary="Project templates: milestones with due dates as working days after the start")
def templates(_user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    return pt.load()


@router.get("/project-templates/{template_id}", summary="One project template")
def template(template_id: str, _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        return pt.get(template_id)
    except LookupError as exc:
        _nf(exc)


@router.post("/project-templates", status_code=status.HTTP_201_CREATED, summary="Add a project template")
def add_template(body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        saved = pt.save({**body, "id": None})
    except (pt.TemplateError, TypeError, AttributeError, ValueError) as exc:
        _bad(exc)
    audit_log("PROJECT_TEMPLATE_ADDED", "project_template", saved["id"], actor_email=user.get("email", "unknown"), actor_role=user.get("role", ""))
    return saved


@router.put("/project-templates/{template_id}", summary="Change a project template (replaces it)")
def change_template(template_id: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        saved = pt.save({**body, "id": template_id})
    except (pt.TemplateError, TypeError, AttributeError, ValueError) as exc:
        _bad(exc)
    except LookupError as exc:
        _nf(exc)
    audit_log("PROJECT_TEMPLATE_CHANGED", "project_template", template_id, actor_email=user.get("email", "unknown"), actor_role=user.get("role", ""))
    return saved


@router.delete("/project-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a project template")
def remove_template(template_id: str, user: dict = Depends(require_manager)) -> None:
    try:
        pt.remove(template_id)
    except LookupError as exc:
        _nf(exc)
    audit_log("PROJECT_TEMPLATE_REMOVED", "project_template", template_id, actor_email=user.get("email", "unknown"), actor_role=user.get("role", ""))


@router.post("/project-templates/{template_id}/plan", summary="The dated milestones for a job starting on a given day (nothing is created in monday)")
def plan(template_id: str, body: Dict[str, Any] = Body(...), _user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    try:
        return pt.plan(template_id, str(body.get("start", "")), str(body.get("project", "")), body.get("owners") or {})
    except (pt.TemplateError, TypeError, AttributeError, ValueError) as exc:
        _bad(exc)
    except LookupError as exc:
        _nf(exc)


@router.post("/project-templates/{template_id}/plan/file", summary="The same plan as an Excel file monday can import")
def plan_file(template_id: str, body: Dict[str, Any] = Body(...), format: str = Query("xlsx", pattern="^(xlsx|csv|pdf)$"),
              _user: dict = Depends(require_operator)) -> Response:
    rows = plan(template_id, body, _user)
    cols = [("project", "Project"), ("milestone", "Milestone"), ("due", "Due"), ("owner_role", "Owner role"), ("owner", "Owner"), ("form", "Form")]
    data = exports.render(format, "Project plan", cols, rows)
    return Response(content=data, media_type=exports.FORMATS[format],
                    headers={"Content-Disposition": f'attachment; filename="allied_project_plan.{format}"', "Cache-Control": "no-store"})


# ------------------------------------------------------------------ my items

@router.get("/my/items", summary="Everything waiting on me: ball, mentions, unread notes, my RFQs and vendor jobs, drafts to approve")
def mine(user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return my_views.my_items(user.get("email", ""))


# ------------------------------------------------------------------ saved filters

@router.get("/views", summary="My saved filters and the shared ones")
def views(screen: Optional[str] = Query(None, max_length=24), user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    from app.services import organize

    pins = organize.pinned_ids(user.get("email", ""))
    rows = my_views.list_views(user.get("email", ""), screen)
    return sorted(({**v, "pinned": v["id"] in pins} for v in rows), key=lambda v: not v["pinned"])                  # pinned ones first, then the usual order


@router.post("/views/{view_id}/pin", summary="Pin a saved view to the top of my lists (only mine)")
def pin_view(view_id: str, user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services import organize

    if not any(v["id"] == view_id for v in my_views.list_views(user.get("email", ""))):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That view is gone.")
    return organize.pin_view(user.get("email", ""), view_id, True)


@router.delete("/views/{view_id}/pin", summary="Unpin a saved view")
def unpin_view(view_id: str, user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.services import organize

    return organize.pin_view(user.get("email", ""), view_id, False)


@router.post("/views", status_code=status.HTTP_201_CREATED, summary="Save a filter (the same name on the same screen replaces it)")
def save(body: Dict[str, Any] = Body(...), user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        return my_views.save_view(user.get("email", ""), body.get("name"), str(body.get("screen", "")), body.get("filters"),
                                  bool(body.get("shared", False)), _is_manager(user))
    except my_views.ViewError as exc:
        _bad(exc)


@router.delete("/views/{view_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a saved filter")
def delete(view_id: str, user: dict = Depends(require_viewer)) -> None:
    try:
        my_views.delete_view(user.get("email", ""), view_id, _is_manager(user))
    except LookupError as exc:
        _nf(exc)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
