"""File sharing: attach files to any order, RFQ, vendor job, shipment, milestone or board item, or to a comment on it.

A drawing, a photo from the vendor, a PDF from the customer: it sits on the item where everyone looking at the item sees it,
instead of in someone's inbox. Files are kept on the server (never sent to monday), checked on the way in (allowed kinds
only, size limit, no programs even if renamed), and downloaded as attachments so a browser never runs them. Removing a file
hides it; the stored copy stays. The same file attached twice to one item is kept once.
"""

import hashlib
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.core.uploads import safe_upload_name
from app.models.db_models import ItemFileDB
from app.services.coordination import updates

MAX_PER_ITEM = 100
_PROGRAMS = (b"MZ", b"\x7fELF", b"#!", b"\xca\xfe\xba\xbe", b"MSCF")      # Windows/Linux/Mac programs, scripts, cabinet installers


class FileError(ValueError):
    """Something a person can read about why a file can't be attached."""


def allowed() -> List[str]:
    return sorted({e.strip().lower().lstrip(".") for e in settings.ATTACHMENT_EXTENSIONS.split(",") if e.strip()})


def _root(item_type: str, ref: str) -> Path:
    return Path(settings.DB_DIR) / "attachments" / safe_upload_name(item_type) / safe_upload_name(ref)


def _as_dict(f: ItemFileDB, names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    names = names or {}
    return {"id": f.id, "item_type": f.item_type, "item_ref": f.item_ref, "update_id": f.update_id, "filename": f.filename, "size": f.size, "design": bool(f.design),
            "content_type": f.content_type, "uploaded_by": f.uploaded_by, "uploaded_by_name": names.get(f.uploaded_by, f.uploaded_by),
            "created_at": f.created_at.isoformat() if f.created_at else None, "download": f"/api/v1/files/{f.id}/download"}


def add(item_type: str, item_ref: str, filename: str, data: bytes, user: Dict[str, Any], update_id: Optional[int] = None) -> Dict[str, Any]:
    ref, _, _ = updates._resolve_item(item_type, item_ref)
    name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not data:
        raise FileError(f"{name or 'The file'} is empty.")
    if ext not in allowed():
        raise FileError(f"{name or 'That file'}: .{ext or '?'} files can't be attached. Allowed: {', '.join(allowed())}.")
    if len(data) > settings.SUBMISSION_MAX_MB * 1024 * 1024:
        raise FileError(f"{name} is larger than {settings.SUBMISSION_MAX_MB} MB.")
    if any(data.startswith(p) for p in _PROGRAMS) and ext not in ("zip",):
        raise FileError(f"{name} looks like a program, not a document, so it can't be attached.")
    who = user.get("email", "unknown").lower()
    digest = hashlib.sha256(data).hexdigest()
    with SessionLocal() as db:
        if update_id is not None:
            c = db.get(updates.ItemUpdateDB, int(update_id))
            if c is None or c.deleted_at or c.item_type != item_type or c.item_ref != ref:
                raise FileError("That comment isn't on this item any more.")
        base = db.query(ItemFileDB).filter(ItemFileDB.item_type == item_type, ItemFileDB.item_ref == ref, ItemFileDB.deleted_at.is_(None))
        same = base.filter(ItemFileDB.sha256 == digest, ItemFileDB.update_id.is_(None) if update_id is None else ItemFileDB.update_id == int(update_id)).first()
        if same:
            names = {p["email"]: p["name"] for p in updates.people()}
            return {**_as_dict(same, names), "duplicate": True}
        if base.count() >= MAX_PER_ITEM:
            raise FileError(f"This item already has {MAX_PER_ITEM} files. Remove some first.")
        folder = _root(item_type, ref)
        folder.mkdir(parents=True, exist_ok=True)
        stored = folder / f"{uuid.uuid4().hex[:12]}_{safe_upload_name(name)}"
        stored.write_bytes(data)
        row = ItemFileDB(item_type=item_type, item_ref=ref, update_id=int(update_id) if update_id is not None else None, filename=name[:255],
                         stored_path=str(stored), size=len(data), sha256=digest, content_type=(mimetypes.guess_type(name)[0] or "application/octet-stream"),
                         uploaded_by=who)
        db.add(row)
        db.commit()
        db.refresh(row)
        out = _as_dict(row)
    audit_log("FILE_ATTACHED", "item_file", str(out["id"]), actor_email=who, actor_role=user.get("role", ""), detail=f"{item_type} {ref}: {name} ({len(data)} bytes)")
    return out


def listing(item_type: str, item_ref: str) -> List[Dict[str, Any]]:
    ref, _, _ = updates._resolve_item(item_type, item_ref)
    names = {p["email"]: p["name"] for p in updates.people()}
    with SessionLocal() as db:
        rows = (db.query(ItemFileDB).filter(ItemFileDB.item_type == item_type, ItemFileDB.item_ref == ref, ItemFileDB.deleted_at.is_(None))
                .order_by(ItemFileDB.created_at.desc(), ItemFileDB.id.desc()).all())
        return [_as_dict(r, names) for r in rows]


def for_thread(item_type: str, ref: str) -> Dict[int, List[Dict[str, Any]]]:
    """The files on each comment of an item, keyed by comment id (one query for the whole thread)."""
    with SessionLocal() as db:
        rows = (db.query(ItemFileDB).filter(ItemFileDB.item_type == item_type, ItemFileDB.item_ref == ref, ItemFileDB.update_id.isnot(None),
                                            ItemFileDB.deleted_at.is_(None)).order_by(ItemFileDB.id).all())
        out: Dict[int, List[Dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r.update_id, []).append(_as_dict(r))
        return out


def open_file(file_id: int) -> Tuple[Path, str, str]:
    """(path, the name to save it as, content type) for a download."""
    with SessionLocal() as db:
        f = db.get(ItemFileDB, file_id)
        if f is None or f.deleted_at:
            raise LookupError("That file is gone.")
        path = Path(f.stored_path)
        if not path.is_file():
            raise LookupError("The file is no longer on the server.")
        return path, f.filename, f.content_type or "application/octet-stream"


def remove(file_id: int, user: Dict[str, Any]) -> None:
    who = user.get("email", "unknown").lower()
    with SessionLocal() as db:
        f = db.get(ItemFileDB, file_id)
        if f is None or f.deleted_at:
            raise LookupError("That file is gone.")
        if f.uploaded_by != who and user.get("role") not in ("ADMIN", "MANAGER"):
            raise PermissionError("Only the person who attached a file, or a manager, can remove it.")
        f.deleted_at = datetime.now(timezone.utc)
        db.commit()
    audit_log("FILE_REMOVED", "item_file", str(file_id), actor_email=who, actor_role=user.get("role", ""))


def set_design(file_id: int, design: bool, user: Dict[str, Any]) -> Dict[str, Any]:
    """Marks a file as a design file (or not). Only design files are attached to emails to a vendor: the customer's own papers never are."""
    with SessionLocal() as db:
        f = db.get(ItemFileDB, file_id)
        if f is None or f.deleted_at:
            raise LookupError("That file is gone.")
        f.design = bool(design)
        db.commit()
        return _as_dict(f)
