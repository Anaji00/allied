"""Nightly database backup, and a restore check that proves the backup is usable.

A backup is a consistent copy of the SQLite file made with SQLite's own backup call (safe while the app is running, unlike copying the file).
After each backup a restore check opens the copy, runs SQLite's integrity check and compares row counts of the main tables with the live
database, so a backup that cannot be restored is found the same night, not the day it is needed. Old backups are removed after
BACKUP_KEEP_DAYS. Nothing here writes to monday or Sage, and nothing but new backup files is ever written."""

import os
import re
import shutil
import sqlite3
import threading
import uuid
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


def _made(name: str) -> Optional[datetime]:
    """When a backup file says it was made, or None for a name that is not a real date (allied-99999999-999999.db passes the name pattern but is not a time): such a file is ignored, never an error."""
    m = _NAME.match(name or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _plausible(made: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """A real backup is not dated in the future (a planted allied-2099... file would otherwise count as the newest and make every real backup "not due")."""
    return made is not None and made <= (now or datetime.now(timezone.utc)) + timedelta(days=1)


def listing() -> List[Dict[str, Any]]:
    out = []
    for p in sorted(folder().glob("allied-*.db"), reverse=True):
        made = _made(p.name)
        if _plausible(made):
            try:
                out.append({"name": p.name, "size": p.stat().st_size, "made": made.isoformat()})
            except OSError:
                continue
    return out


def _counts(path: Path) -> Dict[str, int]:
    from contextlib import closing

    with closing(sqlite3.connect(f"file:{path}?mode=ro&immutable=1" if path != _live_path() else f"file:{path}?mode=ro", uri=True)) as c:      # closed, so the file can be moved or removed afterwards
        have = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        return {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in COUNT_TABLES if t in have}


def check(backup: Path, live: Path) -> Dict[str, Any]:
    """Opens the backup read-only: SQLite's own integrity check, and the same tables with about the same number of rows as the live one."""
    from contextlib import closing

    with closing(sqlite3.connect(f"file:{backup}?mode=ro&immutable=1", uri=True)) as c:      # immutable: opening it never creates side files
        integrity = c.execute("pragma integrity_check").fetchone()[0]
    mine, theirs = _counts(backup), _counts(live)
    # The live database may have moved on since the copy was taken, so only a table that LOST rows is a problem.
    lost = {t: {"backup": mine.get(t, 0), "live": n} for t, n in theirs.items() if mine.get(t, 0) < n - max(5, int(n * 0.05))}
    return {"integrity": integrity, "ok": integrity == "ok" and not lost, "lost": lost, "rows": mine}


def staging_dir() -> Path:
    """Where a backup is made first: the machine's own temporary disk (inside a container, its own filesystem), not the shared folder, because a copy written straight onto a bind-mounted
    folder (Docker on Windows) can fail with "disk I/O error" half way."""
    import tempfile

    d = Path(tempfile.gettempdir()) / "allied-backup-staging"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pending() -> List[Path]:
    """Backups that were made and checked but could not yet be put in the backup folder; they are kept and delivered on the next pass."""
    from app.core.database import database_problems

    try:
        found = [p for p in sorted(staging_dir().glob("allied-*.db")) if _plausible(_made(p.name))]      # a name with an impossible or future date is not a backup of ours
    except OSError:
        return []
    return [p for p in found if p.stat().st_size > 0 and not database_problems(p)]                     # and only a file that opens and passes the database check is ever delivered


def _retry(fn, tries: int = 5, waits=(0.5, 1.0, 2.0, 3.0), sleep=None):
    """Calls fn up to `tries` times with a growing wait; returns (result, None) or (None, last error)."""
    import time

    sleep = sleep or time.sleep
    last_exc: Optional[BaseException] = None
    for n in range(tries):
        try:
            return fn(), None
        except (OSError, sqlite3.Error) as exc:
            last_exc = exc
            if n < tries - 1:
                sleep(waits[min(n, len(waits) - 1)])
    return None, last_exc


def _take(live: Path, tmp: Path) -> None:
    """The consistent copy, made with SQLite's backup call into the staging file."""
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
        dst.execute("pragma journal_mode=delete")      # the live file is in WAL mode; a backup is one plain file, no -wal / -shm beside it
    finally:
        dst.close()
        src.close()


def _deliver(tmp: Path, target: Path) -> Optional[BaseException]:
    """Puts the finished, checked file in the backup folder, with retries; the folder copy is written under a temporary name and renamed, so a half file is never taken for a backup. None when done."""
    def once() -> None:
        part = target.with_name(target.name + ".part")
        part.unlink(missing_ok=True)
        shutil.copyfile(tmp, part)
        if _sha(part) != _sha(tmp):
            part.unlink(missing_ok=True)
            raise OSError("the copy in the backup folder differs from the checked file")
        part.replace(target)

    _done, err = _retry(once)
    return err


_state = {"failures": 0}


_run_lock = threading.Lock()


def run(now: Optional[datetime] = None, sleep=None) -> Dict[str, Any]:
    """One backup run at a time: the nightly worker and someone pressing "Run now" are served one after the other (see _run)."""
    with _run_lock:
        return _run(now, sleep)


def _run(now: Optional[datetime] = None, sleep=None) -> Dict[str, Any]:
    """Makes a backup, checks it, and removes the ones past the keep time. Never raises: the result says what happened.

    The backup is made and checked on the local temporary disk first, then copied into the backup folder with retries. If the folder cannot be written, the checked file is kept and tried again on the next pass
    (not a day later)."""
    live = _live_path()
    if live is None or not live.is_file():
        out = {"ok": False, "error": "The database is not a local SQLite file, so there is nothing to copy." if live is None
               else f"The database file was not found at {live.name}."}
        last.update(out, at=datetime.now(timezone.utc).isoformat())
        return out
    now = now or datetime.now(timezone.utc)
    from app.core.database import database_problems

    damage = database_problems(live)
    if damage:                                                  # a damaged database is told at once, plainly: this is not an ordinary failed try
        out = {"ok": False, "damaged": True, "error": "The live database file is damaged: " + " ".join(damage[:3])}
        logger.critical(f"Backup not made: {out['error']}")
        last.clear()
        last.update(out, at=datetime.now(timezone.utc).isoformat())
        _state["failures"] += 1
        _state["last_fail_at"] = last["at"]
        _tell(out)
        return out
    try:
        folder_ = folder()
    except OSError as exc:
        folder_ = None
        folder_error = exc
    delivered_old = []
    if folder_ is not None:
        for stale in folder_.glob("allied-*.db.part"):                         # half copies left by an interrupted copy (runs are one at a time, so none is in use)
            stale.unlink(missing_ok=True)
        for old in pending():
            if not (folder_ / old.name).exists() and _deliver(old, folder_ / old.name) is None:
                old.unlink(missing_ok=True)
                delivered_old.append(old.name)
            elif (folder_ / old.name).exists():
                old.unlink(missing_ok=True)
    name = f"allied-{now.strftime('%Y%m%d-%H%M%S')}.db"
    tmp = staging_dir() / f"{name}.{uuid.uuid4().hex[:8]}.tmp"                          # a name of its own for this run, so nothing else can hold or delete it
    out: Dict[str, Any]
    try:
        _x, err = _retry(lambda: _take(live, tmp), tries=3, waits=(0.5, 1.5), sleep=sleep)
        if err is not None:
            raise err
        verdict = check(tmp, live)
        copy_problems = database_problems(tmp)
        if copy_problems:
            verdict = {**verdict, "ok": False, "unreadable": copy_problems[:3]}
        if not verdict["ok"]:
            logger.error(f"Backup {name} failed its restore check: {verdict}")
            tmp.unlink(missing_ok=True)
            out = {"ok": False, "file": name, "check": verdict, "error": "The new backup did not pass its restore check, so it was thrown away."}
        else:
            target = folder_ / name if folder_ is not None else None
            err = _deliver(tmp, target) if target is not None else folder_error
            if err is not None:
                logger.warning(f"Backup {name} is made and checked but could not be copied to the backup folder yet: {err}")
                try:
                    os.replace(tmp, staging_dir() / name)                              # kept under its real name: pending() will deliver it on the next pass
                except OSError:
                    pass
                out = {"ok": False, "file": name, "kept_for_retry": True, "error": f"{type(err).__name__}: {err}"[:300], "check": verdict}
            else:
                tmp.unlink(missing_ok=True)
                pruned = prune(now)
                out = {"ok": True, "file": name, "size": target.stat().st_size, "check": verdict, "pruned": pruned}
                copy = copy_off_server(target, live, now)
                out["copy"] = copy
                if copy["configured"] and not copy["ok"]:
                    out["ok"] = False
                    out["error"] = f"The backup is fine, but its second copy failed: {copy.get('error')}"
    except Exception as exc:  # noqa: BLE001 - reported in the result and the system screen, never crashing the worker
        logger.exception(f"Backup failed: {exc}")
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    if delivered_old:
        out["delivered_earlier"] = delivered_old
    last.clear()
    last.update(out, at=datetime.now(timezone.utc).isoformat())
    if out["ok"]:
        _state["failures"] = 0
        _state["last_ok_at"] = datetime.now(timezone.utc).isoformat()
    else:
        _state["failures"] += 1
        _state["last_fail_at"] = datetime.now(timezone.utc).isoformat()
        from app.core.database import invalidate_check

        invalidate_check()                                                    # a failed backup may mean a bad file: the next health look checks again
        if _state["failures"] >= 3:                  # told once the problem has repeated (and then once a day), not at every try
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
        olds = sorted((p for p in target_dir.glob("allied-*.db") if _plausible(_made(p.name))), reverse=True)
        for p in olds[max(0, settings.BACKUP_KEEP_MIN):]:
            if _made(p.name) < cutoff:
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
        have = sorted((p.name for p in target_dir.glob("allied-*.db") if _plausible(_made(p.name))), reverse=True)
    except OSError as exc:
        return {"state": "not_made", "text": f"Second copy: not made. The folder cannot be read ({type(exc).__name__})."}
    if not have:
        return {"state": "not_made", "text": "Second copy: not made yet."}
    made = _made(have[0])
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
    removed, good_seen = 0, 0
    for it in items:                                                                    # newest first
        good = _is_good(folder() / it["name"])
        good_seen += int(good)
        old = datetime.fromisoformat(it["made"]) < cutoff
        keep_for_safety = good and good_seen <= max(0, settings.BACKUP_KEEP_MIN)           # the newest N GOOD backups are never deleted, whatever newer (empty or damaged) files exist
        if old and not keep_for_safety:
            for suffix in ("", "-wal", "-shm"):
                (folder() / (it["name"] + suffix)).unlink(missing_ok=True)
            removed += 1
    return removed


def _is_good(path: Path) -> bool:
    """A backup that is really there and opens: not empty, a SQLite file, and it passes the quick check."""
    from contextlib import closing

    try:
        if path.stat().st_size < 100:
            return False
        with open(path, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return False
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True, timeout=5)) as c:
            return [str(r[0]) for r in c.execute("pragma quick_check")] == ["ok"]
    except (OSError, sqlite3.Error):
        return False


def due(now: Optional[datetime] = None) -> bool:
    """True when there is no backup from the last BACKUP_EVERY_HOURS hours (so a restart never makes two, and a missed night is made up)."""
    now = now or datetime.now(timezone.utc)
    items = listing()
    if pending() or (last and last.get("ok") is False and last.get("at")):               # a checked file is waiting to be delivered, or the last pass failed: try again after a few minutes
        again = datetime.fromisoformat(last["at"]) + timedelta(minutes=settings.BACKUP_RETRY_MINUTES) if last.get("at") else now
        if now >= again:
            return True
    return not items or datetime.fromisoformat(items[0]["made"]) < now - timedelta(hours=settings.BACKUP_EVERY_HOURS)


def plain_text(now: Optional[datetime] = None) -> Dict[str, Any]:
    """"The last backup worked at <time>" or "The last backup failed at <time>; it will try again at <time>", with the raw error only in `details`."""
    when = lambda iso: datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")      # noqa: E731
    items = listing()
    ran = last.get("at")
    newer_success = bool(ran and items and datetime.fromisoformat(items[0]["made"]) > datetime.fromisoformat(ran))      # a backup file made after the failed attempt means the failure is history
    if ran and last.get("ok") is False and not newer_success:
        again = datetime.fromisoformat(ran) + timedelta(minutes=settings.BACKUP_RETRY_MINUTES)
        kept = " The finished backup is kept safely and will be copied across." if last.get("kept_for_retry") else ""
        return {"state": "failed", "text": f"The last backup failed at {when(ran)}; it will try again at {again:%H:%M} UTC.{kept}", "details": str(last.get("error") or last.get("check") or "")[:400],
                "last_good": items[0]["made"] if items else None}
    if ran and (last.get("ok") or newer_success):
        return {"state": "ok", "text": f"The last backup worked at {when(ran if last.get('ok') else items[0]['made'])}.", "details": "", "last_good": items[0]["made"] if items else None}
    if items:                                                                           # no run since the app started: judged by the age of the newest file, not called a problem
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(items[0]["made"])).total_seconds() / 3600
        fresh = age_h <= max(2, settings.BACKUP_EVERY_HOURS * 2)
        return {"state": "ok" if fresh else "stale", "text": f"The newest backup was made at {when(items[0]['made'])}." + ("" if fresh else f" That is {int(age_h)} hours ago; the next one runs on schedule."), "details": "", "last_good": items[0]["made"]}
    return {"state": "none", "text": "No backup has been made yet.", "details": "", "last_good": None}


def status() -> Dict[str, Any]:
    items = listing()
    return {"summary": plain_text(), "waiting_to_copy": [p.name for p in pending()], "enabled": settings.BACKUP_ENABLED, "every_hours": settings.BACKUP_EVERY_HOURS, "keep_days": settings.BACKUP_KEEP_DAYS,
            "count": len(items), "newest": items[0] if items else None, "last_run": {**{k: v for k, v in last.items() if k != "error"}, "failed": plain_text()["state"] == "failed"}, "backups": items[:30], "copy": copy_line()}
