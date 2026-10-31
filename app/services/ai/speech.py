"""Server-side speech to text for recordings (phone calls, voice notes).

Live dictation happens in the browser and needs nothing here. This is for
audio files. With AI_PROVIDER=groq and a GROQ_API_KEY, Groq's Whisper does it
(fast, free tier, 25 MB per file; the recording is sent to Groq). Otherwise
faster-whisper runs locally so recordings never leave the building; that is
off until AI_WHISPER_MODEL is set and the package is installed.
"""

import os
import re
import tempfile
import threading
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.services.ai.providers import AIRateLimitedError, AIUnavailableError

_GROQ_MAX_BYTES = 25 * 1024 * 1024  # Groq's free-tier file limit

_model = None
_model_lock = threading.Lock()


def _use_groq() -> bool:
    return (settings.AI_PROVIDER or "").strip().lower() == "groq" and bool(settings.GROQ_API_KEY)


def status() -> Dict[str, Any]:
    if _use_groq():
        return {"ready": True, "detail": f"Groq Whisper ({settings.GROQ_SPEECH_MODEL}); recordings up to 25 MB are sent to Groq."}
    if not settings.AI_WHISPER_MODEL:
        return {"ready": False, "detail": "Recording transcription is off. Live dictation in the browser still works."}
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return {"ready": False, "detail": "Install faster-whisper (`pip install faster-whisper`) to transcribe recordings."}
    return {"ready": True, "detail": f"Local Whisper model '{settings.AI_WHISPER_MODEL}'."}


def _load():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(settings.AI_WHISPER_MODEL, device="cpu", compute_type="int8")
        return _model


def safe_suffix(filename: Optional[str], default: str = ".wav") -> str:
    """A short alphanumeric extension from a client filename, e.g. '.m4a'.

    The filename comes from the client; a '/' or '..' in it would make mkstemp
    fail or write outside the temp directory.
    """
    name = filename or ""
    ext = re.sub(r"[^A-Za-z0-9]", "", name.rsplit(".", 1)[-1]) if "." in name else ""
    return f".{ext[:8].lower()}" if ext else default


def _transcribe_groq(audio: bytes, suffix: str, language: Optional[str]) -> Dict[str, Any]:
    if len(audio) > _GROQ_MAX_BYTES:
        raise AIUnavailableError("Recordings over 25 MB cannot be sent to Groq's free tier; shorten or compress it.")
    data: Dict[str, str] = {"model": settings.GROQ_SPEECH_MODEL, "response_format": "verbose_json", "temperature": "0"}
    if language:
        data["language"] = language
    try:
        response = httpx.post(
            f"{settings.GROQ_BASE_URL.rstrip('/')}/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
            data=data,
            files={"file": (f"recording{suffix}", audio)},
            timeout=settings.AI_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise AIUnavailableError(f"Groq is not reachable for transcription ({type(exc).__name__}).")
    if response.status_code == 429:
        raise AIRateLimitedError("Groq's free-tier audio limit is reached; try again later.")
    if response.status_code >= 400:
        raise AIUnavailableError(f"Groq could not transcribe it (HTTP {response.status_code}): {response.text[:200]}")
    body = response.json()
    return {
        "text": (body.get("text") or "").strip(),
        "language": body.get("language"),
        "duration_seconds": round(float(body.get("duration") or 0), 1),
    }


def transcribe(audio: bytes, suffix: str = ".wav", language: Optional[str] = None) -> Dict[str, Any]:
    if _use_groq():
        return _transcribe_groq(audio, safe_suffix("x" + (suffix or ""), ".wav"), language)
    if not status()["ready"]:
        raise AIUnavailableError(status()["detail"])
    fd, path = tempfile.mkstemp(suffix=safe_suffix("x" + (suffix or ""), ".wav"))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        segments, info = _load().transcribe(path, language=language or None, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments).strip()
        return {"text": text, "language": info.language, "duration_seconds": round(info.duration, 1)}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
