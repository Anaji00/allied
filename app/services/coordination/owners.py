"""Who a board person is, as an email. Names on the board's people columns map to an email in this order: a staff contact with that
name, a name in the OWNER_EMAILS setting (JSON {"Name": "email"}), else a PLACEHOLDER address that can never receive mail
(name@placeholder.invalid). Placeholders let owner-based alerts, workload and the ball work now; replace them by adding the real
email as a staff contact or in OWNER_EMAILS. Nothing is ever sent to a placeholder."""

import json
import re
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.coordination import notify as alerts

from app.services.coordination.notify import PLACEHOLDER_DOMAIN, is_placeholder  # noqa: F401  (one definition, in the alert code)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", name.strip().casefold()).strip(".") or "unknown"


def email_for(name: str) -> str:
    key = (name or "").strip().casefold()
    for email, c in alerts.contacts().items():
        if str(c.get("name") or "").strip().casefold() == key and key:
            return email
    try:
        configured = {str(k).strip().casefold(): str(v).strip() for k, v in (json.loads(settings.OWNER_EMAILS or "{}") or {}).items()}
    except (TypeError, ValueError, AttributeError):
        configured = {}
    return configured.get(key) or f"{_slug(name)}@{PLACEHOLDER_DOMAIN}"


def from_people_text(text: str) -> List[str]:
    """'Ana Lee, Bo Chen' (a people column's text) -> emails, in order."""
    return [email_for(n) for n in (text or "").split(",") if n.strip()]


def board_owner(column_values: List[Dict]) -> Optional[str]:
    """The first person on an item's first people column, as an email (a placeholder if nobody maps)."""
    for cv in column_values or []:
        if cv.get("type") in ("people", "multiple-person") and (cv.get("text") or "").strip():
            emails = from_people_text(cv["text"])
            if emails:
                return emails[0]
    return None


def owner_of(item_type: str, item_ref: str, fallback: Optional[str] = None) -> Optional[str]:
    """The one answer to "who owns this item": the owner set in the app (on its ball), else the item's own owner (`fallback`, e.g. the
    person read from the board), else nobody. A placeholder address counts as nobody for alerts but is returned as the last resort,
    so a screen can still show a name."""
    from app.services.coordination import ball

    try:
        b = ball.get(item_type, str(item_ref))
    except Exception:  # noqa: BLE001 - an unknown item type or ref means "no app owner", not an error
        b = None
    for candidate in ((b or {}).get("owner_email"), (b or {}).get("owner"), fallback):
        if candidate and "@" in str(candidate) and not is_placeholder(candidate):
            return str(candidate).lower()
    return fallback or None


def stage_owner_to_monday(board_id: str, item_id: str, email: str, user: Dict[str, Any], client=None) -> int:
    """Stage "this person owns this item" for the next push to monday (a People column change). Nothing is sent: the push stays
    behind the manager's button and the read-only switch. Needs the board's People column and the person's monday account."""
    from app.services import monday_push
    from app.services.coordination import boards
    from app.services.monday_client import fetch_user_emails

    board = boards._mirror.get(str(board_id))
    if board is None:
        raise LookupError(f"Board {board_id} has not been read yet.")
    col = next((c for c in board["columns"] if c["type"] in ("people", "multiple-person")), None)
    if col is None:
        raise ValueError("This board has no People column. Add an Owner column in monday first.")
    if client is None:
        from app.core.container import get_monday_client

        client = get_monday_client()
    if not client.live:
        raise ValueError("monday is not connected right now, so this person can't be matched to their monday account.")
    ids = {e.lower(): uid for uid, e in fetch_user_emails(client).items() if e}
    uid = ids.get((email or "").strip().lower())
    if not uid:
        raise ValueError(f"{email} has no monday account that matches this email.")
    name = next((i["name"] for i in board["items"] if str(i["id"]) == str(item_id)), item_id)
    return monday_push.stage("update_columns", board_id, item_id, {"columns": {col["id"]: {"personsAndTeams": [{"id": int(uid), "kind": "person"}]}}},
                             f"Set the owner of {name} to {email}", source="owner", by=user.get("email", "app"))
