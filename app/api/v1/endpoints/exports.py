"""Downloads: orders, packing slips, carrier prices, vendor jobs, RFQs, shipments and any monday board
as Excel, CSV or a printable PDF. Read-only copies; nothing is changed or sent anywhere."""

import re
from datetime import date
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.core.audit import audit_log
from app.core.security import require_operator, require_viewer
from app.services import exports

router = APIRouter()


def _file(fmt: str, name: str, title: str, cols, rows, user: dict) -> Response:
    try:
        data = exports.render(fmt, title, cols, rows)
    except exports.ExportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    audit_log("EXPORT_DOWNLOADED", "export", name, actor_email=user.get("email", "unknown"), actor_role=user.get("role", ""),
              detail=f"{fmt}, {min(len(rows), exports.MAX_ROWS)} rows")
    fname = f"allied_{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}_{date.today().isoformat()}.{fmt.lower()}"
    return Response(content=data, media_type=exports.FORMATS[fmt.lower()],
                    headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store"})


@router.get("", summary="What can be downloaded, and in which formats")
def available(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    return {"lists": [{"id": k, "name": v[0]} for k, v in exports.DATASETS.items()], "formats": list(exports.FORMATS),
            "boards": "GET /exports/boards/{board_id}"}


@router.get("/boards/{board_id}", summary="A monday board (the app's read-only copy) as Excel, CSV or PDF")
def board_file(board_id: str, format: str = Query("xlsx", max_length=8), user: dict = Depends(require_viewer)) -> Response:
    try:
        title, cols, rows = exports.board(board_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    return _file(format, f"board-{title}", title, cols, rows, user)


@router.get("/{name}", summary="One list as Excel, CSV or PDF (margins and costs only for managers)")
def list_file(name: str, format: str = Query("xlsx", max_length=8), user: dict = Depends(require_operator)) -> Response:
    try:
        title, cols, rows = exports.dataset(name, manager=user.get("role") in ("ADMIN", "MANAGER"))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    return _file(format, name, title, cols, rows, user)
