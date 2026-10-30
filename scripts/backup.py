"""Backs up the Allied Bridge database, and verifies the copy is readable.

A backup nobody has restored is a hope, not a backup. This script therefore
does three things rather than one:

  1. copies the database using SQLite's own backup API, which is safe to run
     while the service is live (a plain file copy of a database being written
     to can produce a corrupt copy),
  2. opens the copy and runs an integrity check plus a row count,
  3. deletes copies older than the retention window.

    python scripts/backup.py                      # back up to data/backups
    python scripts/backup.py --dest D:\AlliedBackups
    python scripts/backup.py --keep 30
    python scripts/backup.py --verify-only <file>

Exit code is 0 on success and 1 on failure, so Windows Task Scheduler can
alert on it.
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "allied_enterprise.db"
DEFAULT_DEST = ROOT / "data" / "backups"
TABLES = ("orders", "reconciliations", "exceptions", "audit_log", "users")


def verify(path: Path) -> bool:
    """Opens a backup and confirms it is readable and internally consistent."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        print(f"  FAIL  cannot open {path.name}: {exc}")
        return False

    try:
        # A file that is not a database at all raises here rather than
        # returning a result, so this has to be caught, not just compared.
        try:
            result = con.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            print(f"  FAIL  {path.name} is not a readable database: {exc}")
            return False

        if result != "ok":
            print(f"  FAIL  integrity check on {path.name}: {result}")
            return False

        counts = []
        for table in TABLES:
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                counts.append(f"{table}={n}")
            except sqlite3.Error:
                counts.append(f"{table}=absent")
        print("  OK    integrity check passed")
        print(f"        {', '.join(counts)}")
        return True
    finally:
        con.close()


def prune(dest: Path, keep_days: int) -> int:
    """Removes backups older than the retention window."""
    cutoff = datetime.now() - timedelta(days=keep_days)
    removed = 0
    for old in dest.glob("allied_enterprise_*.db"):
        try:
            stamp = datetime.strptime(old.stem.split("_", 2)[2], "%Y%m%d-%H%M%S")
        except (ValueError, IndexError):
            continue
        if stamp < cutoff:
            old.unlink()
            removed += 1
    return removed


def backup(db: Path, dest: Path, keep_days: int) -> int:
    if not db.exists():
        print(f"FAIL  no database at {db}")
        return 1

    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"allied_enterprise_{datetime.now():%Y%m%d-%H%M%S}.db"

    print(f"Source      {db}")
    print(f"Destination {target}")
    print()

    # SQLite's online backup API, so this is safe while the service is running.
    # A plain file copy of a live database can capture a half-written page.
    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    except sqlite3.Error as exc:
        print(f"  FAIL  backup failed: {exc}")
        target.unlink(missing_ok=True)
        return 1
    finally:
        source.close()

    size_kb = target.stat().st_size / 1024
    print(f"  OK    copied {size_kb:,.0f} KB")

    if not verify(target):
        print("\nFAIL  the copy did not verify; it has been kept for inspection")
        return 1

    removed = prune(dest, keep_days)
    kept = len(list(dest.glob("allied_enterprise_*.db")))
    print(f"  OK    retention: {kept} backups kept, {removed} older than {keep_days} days removed")
    print("\nBackup complete and verified.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Back up and verify the Allied Bridge database.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST,
                    help="where backups are written (use a second drive in production)")
    ap.add_argument("--keep", type=int, default=30, help="retention in days (default 30)")
    ap.add_argument("--verify-only", type=Path, metavar="FILE",
                    help="verify an existing backup instead of making one")
    args = ap.parse_args()

    if args.verify_only:
        print(f"Verifying {args.verify_only}\n")
        return 0 if verify(args.verify_only) else 1

    return backup(args.db, args.dest, args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
