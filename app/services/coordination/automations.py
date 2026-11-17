"""Every automation in one list, with one switch.

The app has four kinds of automation: timed plays, instant reactions, people's own recipes, and the background checks. They keep their
own code, but a person sees one list ("when / then / who reviews / mode") and uses one switch (off, suggest, auto). A play's or a
reaction's mode is saved in the database (it used to be an environment setting that needed a restart); a recipe's is on the recipe;
the checks always run and cannot be switched here."""

from datetime import datetime, timezone
from typing import Any, Dict, List

from app.core.database import SessionLocal
from app.models.db_models import AutomationModeDB
from app.services.coordination import autopilot, events, recipes

MODES = ("off", "suggest", "auto")
CHECKS = [
    ("vendors", "Every hour, for each vendor line", "Raises late, at-risk, unconfirmed and check-in alerts"),
    ("rfqs", "Every hour, for each open RFQ", "Raises due-soon and overdue alerts and chases quiet vendors"),
    ("schedule", "Every hour, for each project milestone", "Raises due-soon, overdue and no-owner alerts"),
    ("ball", "Every hour, for each item someone is waiting on", "Reminds the holder, then the owner, then leadership"),
    ("shipments", "Every hour, for each shipment", "Raises silent, late and free-time alerts"),
]


def overrides(prefix: str) -> Dict[str, str]:
    """Modes saved with the switch, for 'play:' or 'reaction:' keys, keyed by the bare name."""
    try:
        with SessionLocal() as db:
            return {r.key.split(":", 1)[1]: r.mode for r in db.query(AutomationModeDB).filter(AutomationModeDB.key.like(f"{prefix}:%")).all()
                    if r.mode in MODES}
    except Exception:  # noqa: BLE001 - before the table exists (first start after an update), the settings still apply
        return {}


def listing() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    plays = autopilot.modes()
    for name, p in autopilot.PLAYS.items():
        out.append({"id": f"play:{name}", "kind": "play", "name": name, "when": "On a timer", "then": p.about,
                    "who_reviews": "A person approves each message" if plays[name] == "suggest" else ("Nobody: it acts on its own" if plays[name] == "auto" else "Switched off"),
                    "mode": plays[name], "switchable": True, "last_run": autopilot.last_run.get(name)})
    reacts = events.modes()
    for name, r in events.REACTIONS.items():
        out.append({"id": f"reaction:{name}", "kind": "reaction", "name": name, "when": f"When {r.event.replace('.', ' ').replace('_', ' ')}", "then": r.about,
                    "who_reviews": "A person approves each message" if reacts[name] == "suggest" else ("Nobody: it acts on its own" if reacts[name] == "auto" else "Switched off"),
                    "mode": reacts[name], "switchable": True, "last_run": None})
    for r in recipes.list_recipes():
        mode = r["mode"] if r.get("enabled", True) else "off"
        out.append({"id": f"recipe:{r['id']}", "kind": "recipe", "name": r["name"], "when": recipes.describe(r), "then": "Tell people" if r["action"] == "notify" else "Draft an email",
                    "who_reviews": "A person approves each message" if mode == "suggest" else ("Nobody: it acts on its own" if mode == "auto" else "Switched off"),
                    "mode": mode, "switchable": True, "last_run": None})
    for key, when, then in CHECKS:
        out.append({"id": f"check:{key}", "kind": "check", "name": key, "when": when, "then": then, "who_reviews": "Nobody needed: it only tells people",
                    "mode": "always on", "switchable": False, "last_run": None})
    return out


def set_mode(ident: str, mode: str, user: Dict[str, Any]) -> Dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"The mode must be one of: {', '.join(MODES)}.")
    kind, _, name = (ident or "").partition(":")
    if kind == "play" and name in autopilot.PLAYS or kind == "reaction" and name in events.REACTIONS:
        with SessionLocal() as db:
            row = db.get(AutomationModeDB, ident)
            if row is None:
                row = AutomationModeDB(key=ident, mode=mode)
                db.add(row)
            row.mode, row.changed_by, row.changed_at = mode, user.get("email"), datetime.now(timezone.utc)
            db.commit()
    elif kind == "recipe" and name.isdigit():
        recipes.update(int(name), {"enabled": mode != "off", **({"mode": mode} if mode != "off" else {})}, user)
    elif kind == "check":
        raise ValueError("The background checks always run; they only tell people.")
    else:
        raise LookupError(f"No automation {ident}.")
    return next(a for a in listing() if a["id"] == ident)
