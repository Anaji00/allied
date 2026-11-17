"""Nightly database backup, and a restore check that proves the backup is usable.

A backup is a consistent copy of the SQLite file made with SQLite's own backup call (safe while the app is running, unlike copying the file).
After each backup a restore check opens the copy, runs SQLite's integrity check and compares row counts of the main tables with the live
database, so a backup that cannot be restored is found the same night, not the day it is needed. Old backups are removed after
BACKUP_KEEP_DAYS. Nothing here writes to monday or Sage, and nothing but new backup files is ever written."""

import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.logging import logger

COUNT_TABLES = ("orders", "rfqs", "vendor_jobs", "purchase_orders", "jobs", "notifications", "assistant_actions", "audit_log", "outbound_mail_log")
_NAME = re.compile(r"^allied-(\d{8}-\d{6})\.db$")
last: Dict[str, Any] = {}


def _live_path() -> Optional[Path]:
    from app.core.database import _get_db_url

    url = _get_db_url()
    if not url.startswith("sqlite:///"):
        return None
    return Path(url[len("sqlite:///"):])


def folder() -> Path:
    live = _live_path()
    base = Path(settings.BACKUP_DIR) if settings.BACKUP_DIR else (live.parent / "backups" if live else Path("backups"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def listing() -> List[Dict[str, Any]]:
    out = []
    for p in sorted(folder().glob("allied-*.db"), reverse=True):
        m = _NAME.match(p.name)
        if m:
            out.append({"name": p.name, "size": p.stat().st_size, "made": datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc).isoformat()})
    return out


def _counts(path: Path) -> Dict[str, int]:
    with sqlite3.connect(f"file:{path}?mode=ro&immutable=1" if path != _live_path() else f"file:{path}?mode=ro", uri=True) as c:
        have = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        return {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in COUNT_TABLES if t in have}


def check(backup: Path, live: Path) -> Dict[str, Any]:
    """Opens the backup read-only: SQLite's own integrity check, and the same tables with about the same number of rows as the live one."""
    with sqlite3.connect(f"file:{backup}?mode=ro&immutable=1", uri=True) as c:      # immutable: opening it never creates side files
        integrity = c.execute("pragma integrity_check").fetchone()[0]
    mine, theirs = _counts(backup), _counts(live)
    # The live database may have moved on since the copy was taken, so only a table that LOST rows is a problem.
    lost = {t: {"backup": mine.get(t, 0), "live": n} for t, n in theirs.items() if mine.get(t, 0) < n - max(5, int(n * 0.05))}
    return {"integrity": integrity, "ok": integrity == "ok" and not lost, "lost": lost, "rows": mine}


def run(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Makes a backup, checks it, and removes the ones past the keep time. Never raises: the result says what happened."""
    live = _live_path()
    if live is None or not live.is_file():
        out = {"ok": False, "error": "The database is not a local SQLite file, so there is nothing to copy." if live is None
               else f"The database file was not found at {live.name}."}
        last.update(out, at=datetime.now(timezone.utc).isoformat())
        return out
    now = now or datetime.now(timezone.utc)
    target = folder() / f"allied-{now.strftime('%Y%m%d-%H%M%S')}.db"
    try:
        src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
            dst.execute("pragma journal_mode=delete")      # the live file is in WAL mode; a backup is one plain file, no -wal / -shm beside it
        finally:
            dst.close()
            src.close()
        verdict = check(target, live)
        pruned = prune(now)
        out = {"ok": verdict["ok"], "file": target.name, "size": target.stat().st_size, "check": verdict, "pruned": pruned}
        copy = copy_off_server(target, live, now) if verdict["ok"] else {"configured": bool(settings.BACKUP_COPY_DIR), "ok": False, "error": "not copied: the backup did not pass its check"}
        out["copy"] = copy
        if copy["configured"] and not copy["ok"]:
            out["ok"] = False
            out["error"] = f"The backup is fine, but its second copy failed: {copy.get('error')}"
        if not verdict["ok"]:
            logger.error(f"Backup {target.name} failed its restore check: {verdict}")
    except Exception as exc:  # noqa: BLE001 - reported in the result and the system screen, never crashing the worker
        logger.exception(f"Backup failed: {exc}")
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    last.clear()
    last.update(out, at=datetime.now(timezone.utc).isoformat())
    if not out["ok"]:
        _tell(out)
    return out


def _sha(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_dir() -> Optional[Path]:
    return Path(settings.BACKUP_COPY_DIR) if settings.BACKUP_COPY_DIR else None


def copy_off_server(backup: Path, live: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """A second copy of a checked backup in BACKUP_COPY_DIR (a different disk, a network share, a synced folder): a copy on the same machine is lost with
    it. The copy is written under a temporary name, opened and integrity-checked, then renamed, so a half-copied file is never taken for a backup.
    Never raises. Blank setting = not set up."""
    target_dir = copy_dir()
    if target_dir is None:
        return {"configured": False, "ok": False}
    try:
        mine = folder().resolve()
        there = target_dir.resolve()
        synced = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "box", "pcloud", "mega", "nextcloud", "sharepoint", "sync", "tresorit")
        if not settings.BACKUP_COPY_ALLOW_SYNCED and any(part.casefold().startswith(synced) for part in there.parts):
            return {"configured": True, "ok": False, "error": "the second copy folder looks like a cloud-synced folder, and the copy holds the whole database including logins. "
                                                                 "Use another disk or a share you control (or set BACKUP_COPY_ALLOW_SYNCED=True if you accept that)"}
        if there == mine or mine in there.parents or there in mine.parents:
            return {"configured": True, "ok": False, "error": "the second copy folder is the backup folder itself (or inside it, or around it), so it is not a second copy"}
        target_dir.mkdir(parents=True, exist_ok=True)
        for old in target_dir.glob("allied-*.db.part"):          # half copies left by an interrupted copy
            if old.name != backup.name + ".part":
                old.unlink(missing_ok=True)
        tmp, final = target_dir / (backup.name + ".part"), target_dir / backup.name
        shutil.copy2(backup, tmp)
        if _sha(tmp) != _sha(backup):                      # the copy is byte for byte the backup, not just a database that opens
            tmp.unlink(missing_ok=True)
            return {"configured": True, "ok": False, "error": "the copy is not identical to the backup (its checksum differs)"}
        c = sqlite3.connect(f"file:{tmp}?mode=ro&immutable=1", uri=True)
        try:
            verdict = c.execute("pragma integrity_check").fetchone()[0]
        finally:
            c.close()                                   # closed before the rename: Windows will not rename a file that is still open
        if verdict != "ok":
            tmp.unlink(missing_ok=True)
            return {"configured": True, "ok": False, "error": "the copy did not pass the integrity check"}
        tmp.replace(final)
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=max(1, settings.BACKUP_KEEP_DAYS))
        olds = sorted((p for p in target_dir.glob("allied-*.db") if _NAME.match(p.name)), reverse=True)
        for p in olds[max(0, settings.BACKUP_KEEP_MIN):]:
            if datetime.strptime(_NAME.match(p.name).group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc) < cutoff:
                p.unlink(missing_ok=True)
        return {"configured": True, "ok": True, "file": final.name, "at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:  # noqa: BLE001 - reported in the result and the health line
        logger.exception(f"Second backup copy failed: {exc}")
        return {"configured": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}


def copy_line(now: Optional[datetime] = None) -> Dict[str, Any]:
    """The one line for the health page: is there a second copy of last night's backup? (made / not made / not set up)."""
    now = now or datetime.now(timezone.utc)
    target_dir = copy_dir()
    if target_dir is None:
        return {"state": "not_set_up", "text": "No second copy: set BACKUP_COPY_DIR to another disk or a synced folder, so a lost server does not take the backups with it."}
    try:
        have = sorted((p.name for p in target_dir.glob("allied-*.db") if _NAME.match(p.name)), reverse=True)
    except OSError as exc:
        return {"state": "not_made", "text": f"Second copy: not made. The folder cannot be read ({type(exc).__name__})."}
    if not have:
        return {"state": "not_made", "text": "Second copy: not made yet."}
    made = datetime.strptime(_NAME.match(have[0]).group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    fresh = made > now - timedelta(hours=settings.BACKUP_EVERY_HOURS * 2)
    return {"state": "made" if fresh else "not_made", "newest": have[0], "made": made.isoformat(),
            "text": f"Second copy: made {made:%Y-%m-%d %H:%M} UTC." if fresh else f"Second copy: NOT made recently (the newest is from {made:%Y-%m-%d %H:%M} UTC)."}


def _tell(out: Dict[str, Any]) -> None:
    """A backup that failed or cannot be restored is told to leadership (or the admin) once a day."""
    try:
        from app.services.coordination import notify

        notify.notify("backup_failed", f"backup-failed:{datetime.now(timezone.utc).date().isoformat()}", "The database backup did not work",
                      "Last night's backup failed or did not pass its restore check: " + str(out.get("error") or out.get("check") or "unknown")[:400]
                      + " Make one by hand (System > Backups) and tell whoever looks after the server.", severity="critical", include_leadership=True)
    except Exception:  # noqa: BLE001 - telling people is best effort; the failure is already logged and in the status
        logger.exception("Could not raise the backup alert")


def prune(now: Optional[datetime] = None) -> int:
    """Removes backups older than BACKUP_KEEP_DAYS, but always keeps the newest few so a long gap never leaves none."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, settings.BACKUP_KEEP_DAYS))
    items = listing()
    removed = 0
    for it in items[max(0, settings.BACKUP_KEEP_MIN):]:
        if datetime.fromisoformat(it["made"]) < cutoff:
            for suffix in ("", "-wal", "-shm"):
                (folder() / (it["name"] + suffix)).unlink(missing_ok=True)
            removed += 1
    return removed


def due(now: Optional[datetime] = None) -> bool:
    """True when there is no backup from the last BACKUP_EVERY_HOURS hours (so a restart never makes two, and a missed night is made up)."""
    now = now or datetime.now(timezone.utc)
    items = listing()
    return not items or datetime.fromisoformat(items[0]["made"]) < now - timedelta(hours=settings.BACKUP_EVERY_HOURS)


def status() -> Dict[str, Any]:
    items = listing()
    return {"enabled": settings.BACKUP_ENABLED, "every_hours": settings.BACKUP_EVERY_HOURS, "keep_days": settings.BACKUP_KEEP_DAYS,
            "count": len(items), "newest": items[0] if items else None, "last_run": dict(last), "backups": items[:30], "copy": copy_line()}
