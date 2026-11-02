"""Notes from management to the engineers on a job, with optional Mandarin/Vietnamese."""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.security import require_manager, require_viewer
from app.services.coordination import eng_notes

router = APIRouter()


class NoteIn(BaseModel):
    item_id: str = Field(..., min_length=1, max_length=32, description="The monday item (engineering job)")
    text: str = Field(..., min_length=1, max_length=4000)
    languages: List[Literal["zh", "vi"]] = Field(default_factory=list, description="Translations to attach")
    urgent: bool = Field(False, description="Also send as a text and phone push, at any hour")
    to: Optional[List[str]] = Field(None, max_length=30, description="Engineers to send to; blank = the job's owners")


def _fail(exc: Exception):
    code = {LookupError: status.HTTP_404_NOT_FOUND, PermissionError: status.HTTP_403_FORBIDDEN,
            ValueError: status.HTTP_400_BAD_REQUEST}
    for kind, c in code.items():
        if isinstance(exc, kind):
            raise HTTPException(status_code=c, detail=str(exc))
    raise exc


@router.post("", summary="Send a note to the engineers on a job (managers)", status_code=status.HTTP_201_CREATED)
def post_note(body: NoteIn, user: dict = Depends(require_manager)) -> Dict[str, Any]:
    try:
        return eng_notes.post(body.item_id, body.text, user, body.languages, body.urgent, body.to)
    except (LookupError, ValueError) as exc:
        _fail(exc)


@router.post("/voice", summary="Send a spoken note: written out, translated if asked, and kept to play",
             status_code=status.HTTP_201_CREATED)
def post_voice_note(file: UploadFile = File(..., description="The recording (webm, ogg, m4a, mp4, mp3, wav, ...)"),
                    item_id: str = Form(..., max_length=32), text: str = Form("", max_length=4000),
                    languages: str = Form("", max_length=10, description="zh, vi or zh,vi"),
                    urgent: bool = Form(False), to: str = Form("", max_length=2000, description="Emails, comma-separated"),
                    user: dict = Depends(require_manager)) -> Dict[str, Any]:
    data, size = [], 0
    while chunk := file.file.read(1024 * 1024):          # stop as soon as it passes the limit
        size += len(chunk)
        if size > eng_notes.MAX_AUDIO_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Keep voice notes under 25 MB.")
        data.append(chunk)
    langs = [x.strip() for x in languages.split(",") if x.strip()]
    people = [x.strip() for x in to.split(",") if x.strip()] or None
    try:
        return eng_notes.post_voice(item_id, b"".join(data), file.filename or "", user, text, langs, urgent, people)
    except (LookupError, ValueError) as exc:
        _fail(exc)


@router.get("/{note_id}/audio", summary="Play a note's voice recording (phone or desktop)")
def play(note_id: int, _user: dict = Depends(require_viewer)):
    try:
        path, mime = eng_notes.audio_path(note_id)
    except LookupError as exc:
        _fail(exc)
    # Served as a file with its own type, so phone and desktop browsers play it in place.
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


@router.get("", summary="Notes on a job or a board, or the ones sent to me (mine=true)")
def list_notes(item_id: Optional[str] = Query(None, max_length=32), board_id: Optional[str] = Query(None, max_length=32),
               mine: bool = False, days: int = Query(90, ge=1, le=365), user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    return eng_notes.list_notes(item_id, board_id, user.get("email") if mine else None, days)


@router.post("/{note_id}/read", summary="Mark a note sent to me as read")
def mark_read(note_id: int, user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        return eng_notes.mark_read(note_id, user)
    except (LookupError, PermissionError) as exc:
        _fail(exc)
