"""Resets the local database to a clean state before a demonstration.

Run this if the screens are cluttered with records from earlier rehearsals.
It deletes the SQLite file only. Master catalogues, generated packing slips
and source code are untouched, and the schema is rebuilt from migrations the
next time the service starts.

    python demo_reset.py           # ask first
    python demo_reset.py --yes     # no prompt
"""

import json
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "allied_enterprise.db"
SLIPS = ROOT / "output" / "packing_slips"
EQUIPMENT = ROOT / "app" / "data" / "plant_equipment.json"

# Where each demo part should sit on its wear curve. Wear is computed from the
# install date against the part's service life, so without re-anchoring, a
# fixture authored in October reads as long past due by February.
WEAR_TARGETS = {
    "OC-EDM-HM-01": 0.98,     # past the replace line
    "KNF-LAN-SC-02": 0.82,    # at the warning line
    "OC-STR-FLEX-01": 0.60,
    "OC-EDM-CH-03": 0.45,
    "IKO-HSB-GUIDE-04": 0.35,
}


def reanchor_equipment() -> int:
    """Moves install dates so the wear register shows a sensible spread today."""
    if not EQUIPMENT.exists():
        return 0
    records = json.loads(EQUIPMENT.read_text(encoding="utf-8"))
    today = date.today()
    for entry in records:
        life = int(entry.get("expected_lifespan_days", 0) or 0)
        if not life:
            continue
        frac = WEAR_TARGETS.get(entry.get("equipment_tag", ""), 0.5)
        entry["install_date"] = (today - timedelta(days=round(life * frac))).isoformat()
    EQUIPMENT.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return len(records)


def main() -> int:
    if not DB.exists():
        print(f"No database at {DB} — already clean.")
        return 0

    size_kb = DB.stat().st_size / 1024
    print(f"About to reset: {DB}  ({size_kb:,.0f} KB)")
    print("Master catalogues and source code are NOT touched.")

    if "--yes" not in sys.argv:
        try:
            answer = input("Type 'reset' to continue: ").strip().lower()
        except EOFError:
            # No terminal attached: never destroy data on a guess.
            print("Cancelled (no input available). Re-run with --yes to skip the prompt.")
            return 1
        if answer != "reset":
            print("Cancelled.")
            return 1

    # Keep a timestamped copy rather than destroying it outright.
    backup = DB.with_name(f"allied_enterprise.{datetime.now():%Y%m%d-%H%M%S}.bak")
    shutil.copy2(DB, backup)
    DB.unlink()
    # SQLite in WAL mode leaves these beside the database.
    for suffix in ("-wal", "-shm"):
        sidecar = DB.with_name(DB.name + suffix)
        if sidecar.exists():
            sidecar.unlink()

    print(f"Reset. Previous database saved as {backup.name}")

    slips = sorted(SLIPS.glob("packing_slip_SO_*.pdf")) if SLIPS.exists() else []
    if slips:
        print(f"\n{len(slips)} packing slips remain in output/packing_slips/.")
        print("Leave them — they make the Documents screen look lived-in.")

    moved = reanchor_equipment()
    if moved:
        print(f"\nRe-anchored {moved} equipment records so wear reads correctly today.")

    print("\nStart the service and it will rebuild the schema from migrations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
