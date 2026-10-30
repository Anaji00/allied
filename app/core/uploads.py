"""Confinement helpers for client-supplied file paths and filenames.

Every path that originates from a request body or a multipart filename passes
through here before it reaches the filesystem. Two distinct attacks are covered:

  - a filename such as ``../../app/data/master_items.csv`` escaping the upload
    directory on write (``safe_upload_name``)
  - a path such as ``C:/Users/.../.env`` being handed back to the server for
    reading (``resolve_upload_path``)
"""

import re
from pathlib import Path

from app.config import settings

# Anchored to BASE_DIR rather than the process working directory, so the
# location does not change with however the server happens to be launched.
UPLOAD_DIR: Path = settings.BASE_DIR / "data" / "uploads"

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def ensure_upload_dir() -> Path:
    """Creates the upload directory if it does not exist and returns it."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_DIR


def safe_upload_name(filename: str) -> str:
    """Reduces a client-supplied filename to a bare, safe basename.

    Takes only the final path component (so directory separators and ``..`` are
    discarded rather than interpreted) and replaces anything outside
    ``[A-Za-z0-9._-]``.
    """
    # Split on both separators: a Windows-style name arriving on Linux, or the
    # reverse, must not survive as a single component containing a separator.
    base = re.split(r"[\\/]", filename or "")[-1]
    base = base.lstrip(".") or "upload"
    cleaned = _UNSAFE_CHARS.sub("_", base)
    return cleaned[:120]


def resolve_upload_path(candidate: str) -> Path:
    """Resolves a client-supplied path and confirms it stays inside UPLOAD_DIR.

    Raises:
        ValueError: if the path escapes the upload directory or does not exist.
    """
    upload_root = ensure_upload_dir().resolve()

    raw = (candidate or "").strip()
    if not raw:
        raise ValueError("No upload path supplied.")

    # An absolute path would otherwise replace the root entirely when joined,
    # so absolute candidates are only accepted if they already live under it.
    path = Path(raw)
    resolved = (path if path.is_absolute() else (upload_root / path)).resolve()

    if not resolved.is_relative_to(upload_root):
        raise ValueError(
            "uploaded_pdf_path must reference a file previously returned by "
            "/api/v1/chat/upload-pdf."
        )
    if not resolved.is_file():
        raise ValueError("Uploaded file not found. Upload it again before referencing it.")
    return resolved
