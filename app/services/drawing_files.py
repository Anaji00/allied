"""Compare two drawings that are already attached to items (by file id), without uploading them again. The comparing itself is app/services/drawing_compare.py,
unchanged; this only fetches the files, says plainly when one is missing, hidden or unreadable, and writes the result down (a comment on the item, an
overlay picture to attach to a draft). Nothing here changes a file."""

from typing import Any, Dict, List, Tuple

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.models.db_models import ItemFileDB
from app.services import drawing_compare
from app.services.coordination import files, updates

_READABLE = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".dxf", ".dwg", ".step", ".stp", ".igs", ".iges")


_COMPARABLE = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".dxf")


class FileCompareError(ValueError):
    pass


def _load(file_id: Any, label: str) -> Tuple[bytes, str, ItemFileDB]:
    if isinstance(file_id, bool) or not isinstance(file_id, int) or not 0 < file_id < 2**31:
        raise FileCompareError(f"Say which file the {label} is (its file number).")
    try:
        path, name, _ctype = files.open_file(file_id)          # a removed (hidden) file, or one gone from the disk, is a plain LookupError
    except LookupError:
        raise LookupError(f"The {label} (file {file_id}) is gone: it was removed or can't be found on the server.") from None
    if not name.lower().endswith(_READABLE):
        raise FileCompareError(f"{name}: I can't read that kind of file. Formats that work: PDF, PNG, JPG, TIFF or DXF (for DWG, save as DXF first).")
    limit = settings.SUBMISSION_MAX_MB * 1024 * 1024
    if path.stat().st_size > limit:
        raise FileCompareError(f"{name} is larger than {settings.SUBMISSION_MAX_MB} MB.")
    with SessionLocal() as db:
        row = db.get(ItemFileDB, file_id)
        db.expunge(row)
    return path.read_bytes(), name, row


def compare(file_a: Any, file_b: Any, page_a: int = 1, page_b: int = 1) -> Dict[str, Any]:
    if file_a == file_b:
        raise FileCompareError("Choose two different files to compare.")
    for p in (page_a, page_b):
        if isinstance(p, bool) or not isinstance(p, int) or not 1 <= p <= 500:
            raise FileCompareError("A page number must be a whole number from 1 to 500.")
    da, na, ra = _load(file_a, "first file")
    db_, nb, rb = _load(file_b, "second file")
    try:
        out = drawing_compare.compare(da, na, db_, nb, page_a, page_b)
    except drawing_compare.CompareError as exc:
        raise FileCompareError(str(exc)) from None
    return {**out, "file_a": {"id": file_a, "name": na, "item_type": ra.item_type, "item_ref": ra.item_ref}, "file_b": {"id": file_b, "name": nb, "item_type": rb.item_type, "item_ref": rb.item_ref}}


def summary(result: Dict[str, Any]) -> str:
    """The result as plain words for a comment or a spoken answer: the verdict, how much changed, the warnings and the first differences."""
    a, b = result["file_a"]["name"], result["file_b"]["name"]
    lines = [f"Drawing comparison: {a} against {b}", f"Verdict: {result['verdict']} ({result['changed_percent']}% of the drawing differs)."]
    for w in result.get("warnings", [])[:6]:
        lines.append(f"Warning: {w}")
    ta, tb = result["text"]["only_on_first"][:5], result["text"]["only_on_second"][:5]
    if ta:
        lines.append(f"Only on {a}: " + "; ".join(ta))
    if tb:
        lines.append(f"Only on {b}: " + "; ".join(tb))
    return "\n".join(lines)[:3500]


def post_to_thread(result: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """The summary as a comment on the item the first file is attached to (for a job's drawings, the job's own thread)."""
    a = result["file_a"]
    posted = updates.post(a["item_type"], a["item_ref"], summary(result), user)
    audit_log("DRAWING_COMPARE_POSTED", "item_file", f"{a['id']}:{result['file_b']['id']}", actor_email=user.get("email", ""), actor_role=user.get("role", ""),
              detail=f"{a['item_type']} {a['item_ref']}: {result['verdict']}")
    return {"posted": True, "comment_id": posted.get("id"), "item_type": a["item_type"], "item_ref": a["item_ref"]}


def overlay_for_pair(pair: str) -> bytes:
    """The overlay picture for "idA:idB" (as an email attachment is named). Built now, from the files as they are."""
    try:
        a, b = (int(x) for x in str(pair).split(":", 1))
    except ValueError:
        raise FileCompareError("A drawing overlay is named by two file numbers, like 12:15.") from None
    return drawing_compare.overlay_png(compare(a, b))


def both_design_files_of_one_item(pair: str) -> bool:
    """True when both files are marked as design files and belong to the same item (the rule for sending a comparison to a vendor)."""
    try:
        a, b = (int(x) for x in str(pair).split(":", 1))
    except ValueError:
        return False
    with SessionLocal() as db:
        rows = [db.get(ItemFileDB, i) for i in (a, b)]
    return all(r is not None and not r.deleted_at and r.design for r in rows) and (rows[0].item_type, rows[0].item_ref) == (rows[1].item_type, rows[1].item_ref)


def latest_two(job: Any) -> List[Dict[str, Any]]:
    """The two newest files on a job (by when they were attached), newest last, for "compare the two latest drawings"."""
    from app.services.coordination import jobs

    j = jobs.get(job, staff=True)
    rows = [f for f in j["design"]["files"] if str(f["name"]).lower().endswith(_COMPARABLE)]          # a DWG or STEP file can't be compared, so it is not one of "the latest two"
    rows.sort(key=lambda f: f["id"])
    return rows[-2:]
