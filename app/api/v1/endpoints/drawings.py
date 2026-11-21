"""Compare two CAD drawings: customer against vendor, or one revision against another. Nothing is saved or sent anywhere."""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Response, UploadFile, status

from app.config import settings
from app.core.security import require_operator
from app.services import drawing_compare, drawing_files
from app.services.coordination import updates

router = APIRouter()
_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".dxf")
_KNOWN_BUT_UNREADABLE = (".dwg", ".step", ".stp", ".igs", ".iges")   # these get their own plain explanation from the comparer


def _read(f: UploadFile) -> bytes:
    limit = settings.SUBMISSION_MAX_MB * 1024 * 1024
    data = f.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"{f.filename} is larger than {settings.SUBMISSION_MAX_MB} MB.")
    if f.filename and not f.filename.lower().endswith(_EXT + _KNOWN_BUT_UNREADABLE):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"{f.filename}: I can't read that kind of file. Formats that work: PDF, PNG, JPG, TIFF or DXF (for DWG, save as DXF first).")
    return data


def _run(a: UploadFile, b: UploadFile, page_a: int, page_b: int) -> Dict[str, Any]:
    try:
        return drawing_compare.compare(_read(a), a.filename or "first", _read(b), b.filename or "second", page_a, page_b)
    except drawing_compare.CompareError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# Plain `def`: the rendering is CPU work, so it runs in a worker thread and doesn't hold up other requests.
@router.post("/compare", summary="What changed between two drawings: facts, warnings, differing text and numbers, and a red/blue overlay picture")
def compare(first: UploadFile = File(...), second: UploadFile = File(...), page_first: int = Form(1, ge=1, le=500),
            page_second: int = Form(1, ge=1, le=500), _user: dict = Depends(require_operator)) -> Dict[str, Any]:
    return _run(first, second, page_first, page_second)


@router.post("/compare/overlay.png", summary="Just the overlay picture (red = only on the first, blue = only on the second)")
def overlay(first: UploadFile = File(...), second: UploadFile = File(...), page_first: int = Form(1, ge=1, le=500),
            page_second: int = Form(1, ge=1, le=500), _user: dict = Depends(require_operator)) -> Response:
    return Response(content=drawing_compare.overlay_png(_run(first, second, page_first, page_second)), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


def _by_files(a: Any, b: Any, pa: Any, pb: Any) -> Dict[str, Any]:
    try:
        return drawing_files.compare(a, b, pa, pb)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    except drawing_files.FileCompareError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/compare-files", summary="Compare two files already attached to items (by file number), without uploading them again; optionally post the result to the item's thread")
def compare_files(body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    out = _by_files(body.get("file_a"), body.get("file_b"), body.get("page_a", 1), body.get("page_b", 1))
    out["summary"] = drawing_files.summary(out)
    if body.get("post_to_thread") is True:
        try:
            out["thread"] = drawing_files.post_to_thread(out, user)
        except (LookupError, updates.UpdateError) as exc:
            out["thread"] = {"posted": False, "problem": str(exc).strip("'\"")}
    return out


@router.get("/compare-files/overlay.png", summary="Just the overlay picture for two attached files (red = only on the first, blue = only on the second)")
def compare_files_overlay(file_a: int = Query(..., ge=1, le=2_147_483_647), file_b: int = Query(..., ge=1, le=2_147_483_647), page_a: int = Query(1, ge=1, le=500),
                          page_b: int = Query(1, ge=1, le=500), _user: dict = Depends(require_operator)) -> Response:
    return Response(content=drawing_compare.overlay_png(_by_files(file_a, file_b, page_a, page_b)), media_type="image/png", headers={"Cache-Control": "no-store"})
