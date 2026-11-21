"""Organizing data beside the records: families and spellings, tags, vendor contacts and owners. New app tables only; nothing in monday, Sage or an existing record is changed."""

from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.core.security import require_manager, require_operator, require_viewer
from app.services import organize as svc

aliases_router = APIRouter()
tags_router = APIRouter()
vendor_contacts_router = APIRouter()
owners_router = APIRouter()


def _call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except svc.OrganizeError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


# ---- companies, plants, families and the spellings boards use for them
@aliases_router.get("", summary="Companies with their plant and family and the board spellings that mean each (staff)")
def list_families(_user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    return svc.list_families()


@aliases_router.get("/resolve", summary="What a board spelling means: {company, plant, family, matched_by: alias|exact name|null, maybe[]}")
def resolve(name: str = Query(..., min_length=1, max_length=256), _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return svc.resolve(name)


@aliases_router.post("", summary="Add a company (company, plant, family) (a manager)", status_code=201)
def add_family(body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return _call(svc.add_family, body, user)


@aliases_router.patch("/{family_id}", summary="Change a company's name, plant or family (a manager)")
def patch_family(family_id: int, body: Dict[str, Any] = Body(...), _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return _call(svc.patch_family, family_id, body)


@aliases_router.delete("/{family_id}", summary="Remove a company and its spellings (a manager); no record is changed")
def delete_family(family_id: int, _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    svc.delete_family(family_id)
    return {"removed": True, "id": family_id}


@aliases_router.post("/{family_id}/spellings", summary="Say that a board spelling means this company (a manager); a spelling already used by another company is refused", status_code=201)
def add_spelling(family_id: int, body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return _call(svc.add_spelling, family_id, str(body.get("spelling") or ""), user)


@aliases_router.delete("/{family_id}/spellings", summary="Take a spelling off this company (a manager)")
def remove_spelling(family_id: int, spelling: str = Query(..., max_length=256), _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    svc.remove_spelling(family_id, spelling)
    return {"removed": True}


# ---- tags
@tags_router.get("", summary="Tags in use with how many records carry each, and the suggested ones")
def tag_summary(_user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return svc.tag_summary()


@tags_router.get("/items", summary="The records with a tag: ?tag=blind ship (optionally ?kind=order)")
def items_with_tag(tag: str = Query(..., max_length=40), kind: str = Query("", max_length=24), _user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    return _call(svc.items_with_tag, tag, kind)


@tags_router.get("/{item_kind}/{item_ref:path}", summary="One record's tags")
def tags_for(item_kind: str, item_ref: str, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return {"item_kind": item_kind, "item_ref": item_ref, "tags": _call(lambda: svc.tags_for(svc._kind(item_kind), item_ref))}


@tags_router.post("/{item_kind}/{item_ref:path}", summary="Add a tag to a record: {tag}", status_code=201)
def add_tag(item_kind: str, item_ref: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _call(svc.add_tag, item_kind, item_ref, str(body.get("tag") or ""), user)


@tags_router.delete("/{item_kind}/{item_ref:path}", summary="Remove a tag from a record: ?tag=")
def remove_tag(item_kind: str, item_ref: str, tag: str = Query(..., max_length=40), _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _call(svc.remove_tag, item_kind, item_ref, tag)


# ---- vendor contacts
@vendor_contacts_router.get("", summary="Vendor contacts, separate from staff (staff only)")
def list_vendor_contacts(q: str = Query("", max_length=100), company: str = Query("", max_length=256), _user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    return svc.list_vendor_contacts(q, company)


@vendor_contacts_router.post("", summary="Add a vendor contact (a manager)", status_code=201)
def add_vendor_contact(body: Dict[str, Any] = Body(...), user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return _call(svc.add_vendor_contact, body, user)


@vendor_contacts_router.patch("/{contact_id}", summary="Change a vendor contact (a manager)")
def patch_vendor_contact(contact_id: int, body: Dict[str, Any] = Body(...), _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    return _call(svc.patch_vendor_contact, contact_id, body)


@vendor_contacts_router.delete("/{contact_id}", summary="Remove a vendor contact (a manager)")
def delete_vendor_contact(contact_id: int, _user: dict = Depends(require_manager)) -> Dict[str, Any]:
    svc.delete_vendor_contact(contact_id)
    return {"removed": True, "id": contact_id}


# ---- owners and ball holders kept in the app
@owners_router.get("/mine", summary="Records I own, newest first (?kind=)")
def my_owned(kind: str = Query("", max_length=24), user: dict = Depends(require_operator)) -> List[Dict[str, Any]]:
    return _call(svc.list_owned, user.get("email", ""), kind)


@owners_router.get("/{item_kind}/{item_ref:path}", summary="Who owns this record and who has the ball (null when nobody is set)")
def get_owner(item_kind: str, item_ref: str, _user: dict = Depends(require_operator)) -> Any:
    return _call(svc.get_owner, item_kind, item_ref)


@owners_router.put("/{item_kind}/{item_ref:path}", summary="Set the owner and/or who has the ball: {owner, ball_with, note}")
def set_owner(item_kind: str, item_ref: str, body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _call(svc.set_owner, item_kind, item_ref, body, user)


@owners_router.delete("/{item_kind}/{item_ref:path}", summary="Clear the owner of this record")
def clear_owner(item_kind: str, item_ref: str, _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    _call(svc.clear_owner, item_kind, item_ref)
    return {"cleared": True}
