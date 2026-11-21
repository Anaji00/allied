"""Organizing data beside the records, never in them: customer families and board spellings, tags, vendor contacts, owners, pinned views. Everything lives in NEW app tables; nothing in monday, Sage or an
existing table is changed, and a record with no tag, owner or spelling looks exactly as before. Name matching is exact after normalising: never fuzzy, never automatic."""

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.database import SessionLocal
from app.models.db_models import AliasTargetDB, CustomerFamilyDB, RecordOwnerDB, RecordTagDB, VendorContactDB, ViewPinDB

KINDS = ("job", "order", "rfq", "po", "quote", "board_item", "customer", "vendor", "shipment")
SUGGESTED_TAGS = ("senior follow", "waiting on capital", "blind ship", "air freight")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OrganizeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def norm(name: Any) -> str:
    """One key for a spelling: accents dropped, case folded, punctuation and repeated spaces gone ("CT-Kansas City" and "ct kansas  city" are the same key)."""
    t = unicodedata.normalize("NFKD", str(name or ""))
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _who(user: Dict[str, Any]) -> str:
    return (user.get("email") or "").lower()


def _text(value: Any, name: str, limit: int, required: bool = False) -> Optional[str]:
    t = " ".join(str(value or "").split())
    if required and not t:
        raise OrganizeError(422, f"Give {name}.")
    if len(t) > limit:
        raise OrganizeError(422, f"{name[0].upper() + name[1:]} can be at most {limit} characters.")
    return t or None


# ---------------------------------------------------------------- families and spellings
def _family(f: CustomerFamilyDB, spellings: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"id": f.id, "company": f.company, "plant": f.plant, "family": f.family, "spellings": spellings or [], "created_by": f.created_by, "created_at": f.created_at.isoformat() if f.created_at else None}


def list_families() -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        spell: Dict[int, List[str]] = {}
        for a in db.query(AliasTargetDB).order_by(AliasTargetDB.alias_text):
            spell.setdefault(a.family_id, []).append(a.alias_text)
        return [_family(f, spell.get(f.id, [])) for f in db.query(CustomerFamilyDB).order_by(CustomerFamilyDB.family, CustomerFamilyDB.company, CustomerFamilyDB.plant)]


def add_family(data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    company = _text(data.get("company"), "the company", 256, True)
    family = _text(data.get("family") or company, "the family", 256, True)
    plant = _text(data.get("plant"), "the plant", 256)
    with SessionLocal() as db:
        same = db.query(CustomerFamilyDB).filter(CustomerFamilyDB.company == company).all()
        if any(norm(f.plant) == norm(plant) for f in same):
            raise OrganizeError(409, f"{company}{' ' + plant if plant else ''} is already listed.")
        row = CustomerFamilyDB(company=company, plant=plant, family=family, created_by=_who(user))
        db.add(row)
        db.commit()
        return _family(row)


def patch_family(family_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    with SessionLocal() as db:
        f = db.get(CustomerFamilyDB, family_id)
        if f is None:
            raise OrganizeError(404, "No such company.")
        if "company" in data:
            f.company = _text(data["company"], "the company", 256, True)
        if "family" in data:
            f.family = _text(data["family"], "the family", 256, True)
        if "plant" in data:
            f.plant = _text(data["plant"], "the plant", 256)
        db.commit()
        return _family(f)


def delete_family(family_id: int) -> None:
    with SessionLocal() as db:
        db.query(AliasTargetDB).filter(AliasTargetDB.family_id == family_id).delete()          # its spellings go with it; records are untouched
        db.query(CustomerFamilyDB).filter(CustomerFamilyDB.id == family_id).delete()
        db.commit()


def add_spelling(family_id: int, spelling: str, user: Dict[str, Any]) -> Dict[str, Any]:
    text = _text(spelling, "the spelling", 256, True)
    key = norm(text)
    if not key:
        raise OrganizeError(422, "That spelling has no letters or numbers.")
    with SessionLocal() as db:
        fam = db.get(CustomerFamilyDB, family_id)
        if fam is None:
            raise OrganizeError(404, "No such company.")
        have = db.query(AliasTargetDB).filter(AliasTargetDB.alias_norm == key).first()
        if have is not None:
            other = db.get(CustomerFamilyDB, have.family_id)
            if have.family_id == family_id:
                return _family(fam, [a.alias_text for a in db.query(AliasTargetDB).filter(AliasTargetDB.family_id == family_id)])
            raise OrganizeError(409, f"The spelling '{have.alias_text}' already means {other.company if other else 'another company'}"
                                     f"{' (' + other.family + ')' if other and other.family != other.company else ''}. Remove it there first; two companies are never merged by a spelling.")
        db.add(AliasTargetDB(alias_norm=key, alias_text=text, family_id=family_id, created_by=_who(user)))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise OrganizeError(409, "That spelling was just added by someone else.") from None
        return _family(fam, [a.alias_text for a in db.query(AliasTargetDB).filter(AliasTargetDB.family_id == family_id).order_by(AliasTargetDB.alias_text)])


def remove_spelling(family_id: int, spelling: str) -> None:
    with SessionLocal() as db:
        db.query(AliasTargetDB).filter(AliasTargetDB.family_id == family_id, AliasTargetDB.alias_norm == norm(spelling)).delete()
        db.commit()


def resolve(name: str) -> Dict[str, Any]:
    """{company, plant, family, matched_by, maybe}: a listed spelling gives its company ("alias"); a name equal to a listed company gives it ("exact name"); anything else is unknown. `maybe` lists companies
    whose name only CONTAINS the spelling, for a person to confirm: nothing is ever matched by guesswork."""
    key = norm(name)
    empty = {"company": None, "plant": None, "family": None, "matched_by": None, "maybe": []}
    if not key:
        return empty
    with SessionLocal() as db:
        a = db.query(AliasTargetDB).filter(AliasTargetDB.alias_norm == key).first()
        if a is not None:
            f = db.get(CustomerFamilyDB, a.family_id)
            if f is not None:
                return {"company": f.company, "plant": f.plant, "family": f.family, "matched_by": "alias", "maybe": []}
        fams = db.query(CustomerFamilyDB).all()
        exact = next((f for f in fams if norm(f.company) == key or norm(f"{f.company} {f.plant or ''}") == key), None)
        if exact is not None:
            return {"company": exact.company, "plant": exact.plant, "family": exact.family, "matched_by": "exact name", "maybe": []}
        maybe = [{"company": f.company, "plant": f.plant, "family": f.family} for f in fams if len(key) >= 4 and (key in norm(f.company) or norm(f.company) in key)][:5]
        return {**empty, "maybe": maybe}


# ---------------------------------------------------------------- tags
def _tag(t: Any) -> str:
    tag = " ".join(str(t or "").split()).casefold()
    if not tag or len(tag) > 40:
        raise OrganizeError(422, "A tag is 1 to 40 characters.")
    return tag


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise OrganizeError(422, f"The kind must be one of: {', '.join(KINDS)}.")
    return kind


def add_tag(kind: str, ref: str, tag: str, user: Dict[str, Any]) -> Dict[str, Any]:
    kind, ref, tag = _kind(kind), _text(ref, "the reference", 120, True), _tag(tag)
    with SessionLocal() as db:
        if not db.query(RecordTagDB).filter_by(item_kind=kind, item_ref=ref, tag=tag).first():
            db.add(RecordTagDB(item_kind=kind, item_ref=ref, tag=tag, added_by=_who(user)))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
    return {"item_kind": kind, "item_ref": ref, "tags": tags_for(kind, ref)}


def remove_tag(kind: str, ref: str, tag: str) -> Dict[str, Any]:
    kind, ref, tag = _kind(kind), _text(ref, "the reference", 120, True), _tag(tag)
    with SessionLocal() as db:
        db.query(RecordTagDB).filter_by(item_kind=kind, item_ref=ref, tag=tag).delete()
        db.commit()
    return {"item_kind": kind, "item_ref": ref, "tags": tags_for(kind, ref)}


def tags_for(kind: str, ref: str) -> List[str]:
    with SessionLocal() as db:
        return [r.tag for r in db.query(RecordTagDB).filter_by(item_kind=kind, item_ref=str(ref)).order_by(RecordTagDB.tag)]


def tags_of_many(kind: str, refs: List[str]) -> Dict[str, List[str]]:
    """The tags of many records in one query, for a list screen."""
    out: Dict[str, List[str]] = {}
    if not refs:
        return out
    with SessionLocal() as db:
        for r in db.query(RecordTagDB).filter(RecordTagDB.item_kind == kind, RecordTagDB.item_ref.in_([str(x) for x in refs])).order_by(RecordTagDB.tag):
            out.setdefault(r.item_ref, []).append(r.tag)
    return out


def items_with_tag(tag: str, kind: str = "") -> List[Dict[str, Any]]:
    tag = _tag(tag)
    with SessionLocal() as db:
        q = db.query(RecordTagDB).filter(RecordTagDB.tag == tag)
        if kind:
            q = q.filter(RecordTagDB.item_kind == _kind(kind))
        return [{"item_kind": r.item_kind, "item_ref": r.item_ref, "tag": r.tag, "added_by": r.added_by, "added_at": r.added_at.isoformat() if r.added_at else None} for r in q.order_by(RecordTagDB.id.desc()).limit(500)]


def tag_summary() -> Dict[str, Any]:
    """Every tag in use with how many records carry it, plus the suggested ones."""
    from sqlalchemy import func

    with SessionLocal() as db:
        used = [{"tag": t, "count": n} for t, n in db.query(RecordTagDB.tag, func.count(RecordTagDB.id)).group_by(RecordTagDB.tag).order_by(func.count(RecordTagDB.id).desc(), RecordTagDB.tag)]
    return {"used": used, "suggested": [t for t in SUGGESTED_TAGS]}


# ---------------------------------------------------------------- vendor contacts
def _vc(v: VendorContactDB) -> Dict[str, Any]:
    return {"id": v.id, "name": v.name, "company": v.company, "country": v.country, "email": v.email, "phone": v.phone, "notes": v.notes, "created_by": v.created_by,
            "created_at": v.created_at.isoformat() if v.created_at else None}


def _vc_fields(data: Dict[str, Any], partial: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if "name" in data or not partial:
        out["name"] = _text(data.get("name"), "a name", 256, True)
    for key, label, limit in (("company", "the company", 256), ("country", "the country", 80), ("phone", "the phone number", 60), ("notes", "the notes", 500)):
        if key in data:
            out[key] = _text(data.get(key), label, limit)
    if "email" in data:
        e = _text(data.get("email"), "the email", 320)
        if e and not _EMAIL.match(e):
            raise OrganizeError(422, "That email address does not look right.")
        out["email"] = e.lower() if e else None
    return out


def list_vendor_contacts(q: str = "", company: str = "", limit: int = 500) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        query = db.query(VendorContactDB)
        if company:
            query = query.filter(VendorContactDB.company.ilike(company))
        rows = [r for r in query.order_by(VendorContactDB.company, VendorContactDB.name).limit(2000)]
    needle = norm(q)
    return [_vc(r) for r in rows if not needle or needle in norm(f"{r.name} {r.company} {r.country} {r.email}")][:max(1, min(limit, 1000))]


def add_vendor_contact(data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    f = _vc_fields(data)
    with SessionLocal() as db:
        row = VendorContactDB(**f, created_by=_who(user))
        db.add(row)
        db.commit()
        return _vc(row)


def patch_vendor_contact(contact_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    f = _vc_fields(data, partial=True)
    with SessionLocal() as db:
        row = db.get(VendorContactDB, contact_id)
        if row is None:
            raise OrganizeError(404, "No such vendor contact.")
        for k, v in f.items():
            setattr(row, k, v)
        db.commit()
        return _vc(row)


def delete_vendor_contact(contact_id: int) -> None:
    with SessionLocal() as db:
        db.query(VendorContactDB).filter(VendorContactDB.id == contact_id).delete()
        db.commit()


# ---------------------------------------------------------------- owners
def _owner(r: RecordOwnerDB) -> Dict[str, Any]:
    return {"item_kind": r.item_kind, "item_ref": r.item_ref, "owner": r.owner, "ball_with": r.ball_with, "note": r.note, "updated_by": r.updated_by, "updated_at": r.updated_at.isoformat() if r.updated_at else None}


def set_owner(kind: str, ref: str, data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    kind, ref = _kind(kind), _text(ref, "the reference", 120, True)
    owner, ball, note = _text(data.get("owner"), "the owner", 320), _text(data.get("ball_with"), "who has the ball", 80), _text(data.get("note"), "the note", 300)
    if not (owner or ball):
        raise OrganizeError(422, "Give an owner, or who has the ball, or both.")
    with SessionLocal() as db:
        row = db.query(RecordOwnerDB).filter_by(item_kind=kind, item_ref=ref).first()
        if row is None:
            row = RecordOwnerDB(item_kind=kind, item_ref=ref)
            db.add(row)
        row.owner, row.ball_with, row.note, row.updated_by, row.updated_at = owner, ball, note, _who(user), _now()
        db.commit()
        return _owner(row)


def get_owner(kind: str, ref: str) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        row = db.query(RecordOwnerDB).filter_by(item_kind=_kind(kind), item_ref=str(ref)).first()
        return _owner(row) if row else None


def clear_owner(kind: str, ref: str) -> None:
    with SessionLocal() as db:
        db.query(RecordOwnerDB).filter_by(item_kind=_kind(kind), item_ref=str(ref)).delete()
        db.commit()


def owners_of_many(kind: str, refs: List[str]) -> Dict[str, Dict[str, Any]]:
    if not refs:
        return {}
    with SessionLocal() as db:
        return {r.item_ref: _owner(r) for r in db.query(RecordOwnerDB).filter(RecordOwnerDB.item_kind == kind, RecordOwnerDB.item_ref.in_([str(x) for x in refs]))}


def list_owned(owner: str, kind: str = "") -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        q = db.query(RecordOwnerDB).filter(RecordOwnerDB.owner == owner.strip().lower())
        if kind:
            q = q.filter(RecordOwnerDB.item_kind == _kind(kind))
        return [_owner(r) for r in q.order_by(RecordOwnerDB.updated_at.desc()).limit(500)]


# ---------------------------------------------------------------- pinned views
def pinned_ids(email: str) -> set:
    try:
        with SessionLocal() as db:
            return {r.view_id for r in db.query(ViewPinDB).filter(ViewPinDB.user_email == (email or "").lower())}
    except SQLAlchemyError:                                  # the new tables are not there yet (the migration has not run): the views still list, just unpinned
        return set()


def pin_view(email: str, view_id: str, pinned: bool = True) -> Dict[str, Any]:
    email = (email or "").lower()
    with SessionLocal() as db:
        if pinned:
            if not db.query(ViewPinDB).filter_by(view_id=view_id, user_email=email).first():
                db.add(ViewPinDB(view_id=view_id[:40], user_email=email))
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
        else:
            db.query(ViewPinDB).filter_by(view_id=view_id, user_email=email).delete()
            db.commit()
    return {"view_id": view_id, "pinned": pinned}


def resolver():
    """A function name -> {company, plant, family} or None, built from one read of the spellings and companies (exact normalised matches only), for annotating a whole board without a query per row."""
    with SessionLocal() as db:
        fams = {f.id: f for f in db.query(CustomerFamilyDB)}
        by_norm: Dict[str, Any] = {}
        for a in db.query(AliasTargetDB):
            f = fams.get(a.family_id)
            if f is not None:
                by_norm[a.alias_norm] = f
        for f in fams.values():
            by_norm.setdefault(norm(f.company), f)
            if f.plant:
                by_norm.setdefault(norm(f"{f.company} {f.plant}"), f)

    def resolve_one(name: Any) -> Optional[Dict[str, Any]]:
        f = by_norm.get(norm(name))
        return {"company": f.company, "plant": f.plant, "family": f.family} if f is not None else None

    return resolve_one


def annotate_rows(rows: List[Dict[str, Any]], tag_kind: str, ref_of, name_of) -> None:
    """Adds `family`, `company`, `plant` and `tags` to each row (read only; the rows are otherwise untouched). One read of the spellings and one query for the tags, whatever the number of rows."""
    if not rows:
        return
    refs = [ref_of(r) for r in rows]
    try:
        tags = tags_of_many(tag_kind, [x for x in refs if x])
        res = resolver()
    except SQLAlchemyError:                                  # the new tables are not there yet: the rows are returned as they were, without family or tags
        return
    for r, ref in zip(rows, refs):
        hit = res(name_of(r)) if name_of(r) else None
        r["tags"] = tags.get(str(ref), []) if ref else []
        r["family"] = hit["family"] if hit else None
        r["company"] = hit["company"] if hit else None
        r["plant"] = hit["plant"] if hit else None


def annotate_board_rows(user: Dict[str, Any], board_id: str, items: List[Dict[str, Any]]) -> None:
    """Family, company, plant and tags beside each row of a board, for staff only (a viewer's rows are left as they are). Used by both board screens' payloads; nothing in a row is changed."""
    if (user or {}).get("role") not in ("ADMIN", "MANAGER", "OPERATOR"):
        return

    def customer_of(row):                                   # the customer column if the board has one, else the row's own name
        for c in (row.get("cells") or {}).values():
            if str(c.get("title") or "").casefold() in ("customer", "client", "company", "account") and str(c.get("value") or "").strip():
                return str(c["value"])
        return row.get("name")

    is_jobs = board_id == "jobs"
    annotate_rows(items, "job" if is_jobs else "board_item",
                  lambda r: None if r.get("pending") else ((r["id"] if r.get("job") else None) if is_jobs else f"{board_id}:{r['id']}"), customer_of)
