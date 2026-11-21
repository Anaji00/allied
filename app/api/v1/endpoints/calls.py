"""A call, noted on a job: the existing call summary, put on the job as a draft note for a person to confirm. Nothing is posted or sent until they do."""

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.core.security import require_operator
from app.services import my_day
from app.services.ai.providers import AIUnavailableError

router = APIRouter()


def _run(fn, *args):
    try:
        return fn(*args)
    except AIUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'\""))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/to-job", summary="Summarise a call (its text) and draft a note on the job for a person to confirm")
def to_job(body: Dict[str, Any] = Body(...), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    for k in ("job", "text", "from_number"):
        if body.get(k) is not None and not isinstance(body[k], str):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{k}' must be text.")
    return _run(my_day.call_to_job, str(body.get("job") or ""), str(body.get("text") or ""), user, str(body.get("from_number") or ""))


@router.post("/to-job/audio", summary="Transcribe a call recording, summarise it and draft a note on the job for a person to confirm")
def to_job_audio(job: str = Form(..., max_length=64), upload: UploadFile = File(...), from_number: str = Form("", max_length=40), user: dict = Depends(require_operator)) -> Dict[str, Any]:
    from app.services.ai import speech
    from app.services.coordination import eng_notes

    limit = settings.SUBMISSION_MAX_MB * 1024 * 1024
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"The recording is larger than {settings.SUBMISSION_MAX_MB} MB.")

    def work() -> Dict[str, Any]:
        suffix = eng_notes.check_audio(data, upload.filename or "")
        heard = speech.transcribe(data, suffix=suffix)
        return my_day.call_to_job(job, (heard.get("text") or "").strip(), user, from_number)

    return _run(work)
