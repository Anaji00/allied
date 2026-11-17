"""Starts the app on a DEMO database made by scripts/demo_seed.py. It refuses any database that script did not make.

    python scripts/demo_run.py --db C:/demo/allied_demo.db --port 8010
    python scripts/demo_run.py --db C:/demo/allied_demo.db --check        # shows what it would use, starts nothing

The settings come from <name>.demo.env next to the database. They are put in this process's environment before the app loads, empty values
included (a shell cannot do that on Windows: an empty variable there means "not set", and the real .env would then win). Mail sending, monday, Sage
and the outside connections are off, so approving a draft in the demo reaches nobody. The real database, uploads, output folder and
catalogues are never opened."""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.demo_seed import MARKER_TABLE, _is_inside, _paths, go_quiet  # noqa: E402


def read_env_file(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value
    return out


def check_demo_db(db_arg: str) -> Path:
    path = Path(db_arg).expanduser()
    real = ROOT / "data" / "allied_enterprise.db"
    if path.resolve() == real.resolve() or _is_inside(path, ROOT / "data") or _is_inside(path, ROOT / "output"):
        raise SystemExit("Refused: that is real data. The demo runs only on a database made by scripts/demo_seed.py outside data/ and output/.")
    if not path.is_file():
        raise SystemExit(f"Refused: {path} does not exist. Make it first with scripts/demo_seed.py.")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            marked = con.execute("select name from sqlite_master where type='table' and name=?", (MARKER_TABLE,)).fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError:
        marked = None
    if not marked:
        raise SystemExit(f"Refused: {path} was not made by scripts/demo_seed.py (no demo marker), so the demo will not run on it.")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the app on a demo database.")
    ap.add_argument("--db", required=True)
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--check", action="store_true", help="show what would be used and stop")
    args = ap.parse_args(argv)
    path = check_demo_db(args.db)
    env_file = _paths(path)["env"]
    if not env_file.is_file():
        raise SystemExit(f"Refused: {env_file} is missing. Run scripts/demo_seed.py again (with --replace).")
    env = read_env_file(env_file)
    os.environ.update(env)
    from app.config import settings

    if Path(str(settings.DATABASE_URL).replace("sqlite:///", "")).resolve() != path.resolve():
        raise SystemExit("Refused: the app did not take the demo database path, so nothing was started.")
    go_quiet(settings, env)                                         # blank values are applied to the loaded settings: the real .env cannot win
    if args.check:
        blank = lambda v: "off" if not str(v or "").strip() else "SET"          # noqa: E731  (never prints a value)
        print(f"database : {settings.DATABASE_URL}\nuploads  : {settings.DB_DIR}\noutput   : {settings.OUTPUT_DIR}\ncatalogs : {settings.DATA_DIR}")
        print(f"email sending enabled: {settings.EMAIL_SEND_ENABLED} | SMTP host: {blank(settings.SMTP_HOST)} | monday read-only: {settings.MONDAY_READ_ONLY}"
              f" | monday simulation: {settings.MONDAY_SIMULATION_MODE} | AI key: {blank(settings.GROQ_API_KEY)} | mailbox polling: {settings.IMAP_POLL_ENABLED}")
        return 0
    import uvicorn

    print(f"Demo running on http://127.0.0.1:{args.port} with {path}. Point the frontend at it (NEXT_PUBLIC_API_URL=http://localhost:{args.port}).")
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
