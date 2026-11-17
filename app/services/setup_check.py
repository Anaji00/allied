"""The "set these first" list: the few settings that must be right, what stops working while each is blank, and how to fix it.
Reports only whether something is set, never its value, so no secret ever leaves this module."""

from typing import Any, Dict, List

from app.config import settings
from app.services.coordination import notify


def _has(name: str) -> bool:
    v = getattr(settings, name, "")
    return bool(str(v).strip()) if not isinstance(v, (int, float)) or isinstance(v, bool) else bool(v)


def _item(key: str, level: str, label: str, ok: bool, stops: str, fix: str) -> Dict[str, Any]:
    return {"key": key, "level": level, "label": label, "ok": bool(ok), "if_blank": stops, "how_to_fix": fix}


def _backups_ok() -> bool:
    from datetime import datetime, timedelta, timezone

    from app.services import backups

    try:
        newest = backups.listing()
        return bool(settings.BACKUP_ENABLED and newest and datetime.fromisoformat(newest[0]["made"]) > datetime.now(timezone.utc) - timedelta(days=2))
    except Exception:  # noqa: BLE001 - an unreadable folder counts as "not running"
        return False


def checklist() -> Dict[str, Any]:
    contacts = notify.contacts()
    leaders = bool(notify.leadership())
    items: List[Dict[str, Any]] = [
        _item("leadership", "needed", "Leadership emails", leaders,
              "Alerts nobody owns go to the admin address alone.", "Set LEADERSHIP_EMAILS, or add leadership staff contacts (System > Alerts)."),
        _item("staff_contacts", "recommended", "Staff contacts", bool(contacts),
              "Board people can't be matched to real emails, so owner alerts and drafts use placeholders.", "Add each person under System > Alerts, or list them in OWNER_EMAILS."),
        _item("mail_in", "needed", "Mailbox login (reading mail)", _has("IMAP_HOST") and _has("IMAP_USERNAME") and _has("IMAP_PASSWORD") and settings.IMAP_POLL_ENABLED,
              "RFQs, orders and vendor replies are not read from email.", "Set IMAP_HOST, IMAP_USERNAME and IMAP_PASSWORD (a Gmail app password) and IMAP_POLL_ENABLED=True."),
        _item("mail_out", "needed", "Outgoing mail", _has("SMTP_HOST"),
              "Approved drafts are recorded but never sent.", "Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD."),
        _item("monday_token", "needed", "monday connection", _has("MONDAY_API_KEY"),
              "No board is read; board screens stay empty.", "Set MONDAY_API_KEY."),
        _item("orders_board", "needed", "Orders board", _has("MONDAY_VENDOR_BOARD_ID"),
              "Vendor lines, late-vendor alerts and the order board are not read.", "Set MONDAY_VENDOR_BOARD_ID to the 2026 Orders board id."),
        _item("rfq_board", "needed", "Open RFQs board", _has("MONDAY_RFQ_BOARD_ID"),
              "RFQ rows can't be staged for the board.", "Set MONDAY_RFQ_BOARD_ID."),
        _item("vendor_columns", "needed", "Vendor board columns (PO, status, promised date)",
              _has("VENDOR_PO_COLUMN_ID") and _has("VENDOR_STATUS_COLUMN_ID") and _has("VENDOR_PROMISED_COLUMN_ID"),
              "Late and at-risk checks can't run: PO number, status and promised date are not read.", "Set VENDOR_PO_COLUMN_ID, VENDOR_STATUS_COLUMN_ID and VENDOR_PROMISED_COLUMN_ID."),
        _item("owner_column", "recommended", "An owner column on the boards", _has("VENDOR_OWNER_COLUMN_ID"),
              "The app looks for the board's first People column instead; if there is none, items have no owner.", "Add an Owner (People) column in monday, then set VENDOR_OWNER_COLUMN_ID."),
        _item("po_floor", "needed", "PO number floor (above Sage's last PO)", _has("PO_NUMBER_FLOOR"),
              "New PO numbers could clash with ones the accountant already used in Sage.", "Set PO_NUMBER_FLOOR to the last PO number used in Sage 50."),
        _item("so_floor", "needed", "Sales order number floor (above Sage's last SO)", _has("SO_NUMBER_FLOOR"),
              "New sales order numbers could clash with Sage's.", "Set SO_NUMBER_FLOOR to the last sales order number used in Sage 50."),
        _item("ai", "recommended", "AI key (Groq free tier)", _has("GROQ_API_KEY"),
              "Emails are not read by AI: keyword sorting only, no summaries, no scans of messy PDFs.", "Set GROQ_API_KEY."),
        _item("app_address", "recommended", "App address people can open", settings.APP_BASE_URL not in ("", "http://localhost:3000"),
              "Links in alerts and approve-from-phone buttons point at this computer only.", "Set APP_BASE_URL to the address phones can reach."),
        _item("backups", "needed", "Database backups are running", _backups_ok(),
              "A broken disk or a bad update could lose everything, with no copy to go back to.", "Leave BACKUP_ENABLED on; check System > Backups, or run one now."),
        _item("fedex", "optional", "FedEx", _has("FEDEX_CLIENT_ID"), "No live FedEx prices; the carrier rate card is used.", "Set the FedEx client id, secret and account number."),
        _item("push", "optional", "Phone push alerts (ntfy)", _has("NTFY_URL") and _has("NTFY_TOPIC"),
              "Urgent alerts stay in the app and email; no phone push notifications.", "Set NTFY_URL and NTFY_TOPIC."),
        _item("sms", "optional", "Critical SMS alerts (Twilio)", _has("TWILIO_ACCOUNT_SID") and _has("TWILIO_AUTH_TOKEN") and _has("TWILIO_FROM"),
              "Critical alerts do not send text messages.", "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM."),
    ]
    order = {"needed": 0, "recommended": 1, "optional": 2}
    items.sort(key=lambda i: (i["ok"], order[i["level"]]))
    todo = [i for i in items if not i["ok"] and i["level"] != "optional"]
    return {"items": items, "needs_setting": len(todo), "needed_first": [i["key"] for i in todo if i["level"] == "needed"],
            "safety": {"monday_read_only": bool(settings.MONDAY_READ_ONLY), "monday_simulation": bool(settings.MONDAY_SIMULATION_MODE)}}
