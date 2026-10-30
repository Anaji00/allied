"""Server-side speech to text for recordings (phone calls, voice notes).

Live dictation happens in the browser and needs nothing here. This is for
audio files, using faster-whisper locally so recordings never leave the
building. Off until AI_WHISPER_MODEL is set and the package is installed.
"""

import os
import re
import tempfile
import threading
from typing import Any, Dict, Optional

from app.config import settings
from app.services.ai.providers import AIUnavailableError

_model = None
_model_lock = threading.Lock()


def status() -> Dict[str, Any]:
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


def transcribe(audio: bytes, suffix: str = ".wav", language: Optional[str] = None) -> Dict[str, Any]:
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
