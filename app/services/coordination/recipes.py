"""People's own automation recipes, the way monday builds them: "when this happens, then do that".

A recipe belongs to one board (projects, vendor orders or quote requests) and has:

- a trigger: an item's status becomes a label (status_is); an item has sat in one status
  for a set number of days (status_for); or a date column is a set number of days away or
  already past (date_within). The last two are checked on the hourly run;
- an action: tell people in the app (notify), or draft an email (draft_email) that
  waits for a person to send it, unless the recipe is on auto and mail is switched on.

Recipes run inside Allied's system like every other automation. They read the board
copy the app keeps and never write to monday. Each recipe fires once per item and
trigger (a status reached again after leaving it counts again; a date counts once).
"""

import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import AutomationRecipeDB
from app.services.coordination import notify as alerts

ROLES = ("projects", "vendors", "rfqs")
TRIGGERS = ("status_is", "status_for", "date_within")
ACTIONS = ("notify", "draft_email")
EVENT_ROLE = {"milestone.status_changed": "projects", "order.status_changed": "vendors", "rfq.status_changed": "rfqs"}


def board_for(role: str) -> Optional[str]:
    return {"projects": settings.MONDAY_PROJECTS_BOARD_ID, "vendors": settings.MONDAY_VENDOR_BOARD_ID,
            "rfqs": settings.MONDAY_RFQ_BOARD_ID}.get(role) or None


def _as_dict(r: AutomationRecipeDB) -> Dict[str, Any]:
    d = {k: getattr(r, k) for k in ("id", "name", "board_role", "trigger", "column", "value", "action", "recipients",
                                    "email_to", "subject", "body", "mode", "enabled", "created_by")}
    d["board_id"] = board_for(r.board_role)
    d["created_at"] = r.created_at.isoformat() if r.created_at else None
    d["updated_at"] = r.updated_at.isoformat() if r.updated_at else None
    d["description"] = describe(d)
    return d


def describe(r: Dict[str, Any]) -> str:
    when = {"status_is": f"When status becomes {r['value']}",
            "status_for": f"When an item has been {r.get('column')} for {r['value']} days",
            }.get(r["trigger"], f"When {r.get('column') or 'the date'} is {r['value']} days away or past")
    then = (f"tell {r.get('recipients') or 'the owner'}" if r["action"] == "notify"
            else f"draft an email to {r.get('email_to') or '?'} ({'sent at once' if r['mode'] == 'auto' else 'a person sends it'})")
    return f"{when}, {then}."


def _check(data: Dict[str, Any]) -> Dict[str, Any]:
    clean = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}
    if clean.get("board_role") not in ROLES:
        raise ValueError(f"board_role must be one of {', '.join(ROLES)}.")
    if clean.get("trigger") not in TRIGGERS:
        raise ValueError(f"trigger must be one of {', '.join(TRIGGERS)}.")
    if clean.get("action") not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}.")
    if not clean.get("name"):
        raise ValueError("Give the recipe a name.")
    if not clean.get("value"):
        raise ValueError("Say which status label, or how many days.")
    if clean["trigger"] == "status_for":
        if not str(clean["value"]).isdigit() or not 1 <= int(clean["value"]) <= 365:
            raise ValueError("For a status-for recipe, value is a number of days (1 to 365).")
        if not clean.get("column"):
            raise ValueError("Say which status (its label goes in column).")
    if clean["trigger"] == "date_within":
        if not str(clean["value"]).lstrip("-").isdigit() or not -60 <= int(clean["value"]) <= 365:
            raise ValueError("For a date recipe, value is a number of days (0 means on the day).")
        if not clean.get("column"):
            raise ValueError("Say which date column.")
    if clean["action"] == "draft_email" and not clean.get("email_to"):
        raise ValueError("Say who the email goes to: an address, or vendor / customer.")
    if clean.get("mode", "suggest") not in ("suggest", "auto"):
        raise ValueError("mode must be suggest or auto.")
    return clean


def list_recipes(role: Optional[str] = None) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        q = db.query(AutomationRecipeDB).order_by(AutomationRecipeDB.id)
        if role:
            q = q.filter(AutomationRecipeDB.board_role == role)
        return [_as_dict(r) for r in q]


def create(data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    clean = _check(data)
    now = datetime.now(timezone.utc)
    fields = {k: clean.get(k) for k in ("name", "board_role", "trigger", "column", "value", "action", "recipients",
                                        "email_to", "subject", "body")}
    with SessionLocal() as db:
        row = AutomationRecipeDB(**fields, mode=clean.get("mode") or "suggest", enabled=bool(clean.get("enabled", True)),
                                 created_by=user.get("email"), created_at=now, updated_at=now)
        db.add(row)
        db.commit()
        db.refresh(row)
        return _as_dict(row)


def update(recipe_id: int, data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(AutomationRecipeDB, recipe_id)
        if row is None:
            raise LookupError("No such recipe.")
        merged = {**_as_dict(row), **{k: v for k, v in data.items() if v is not None}}
        clean = _check(merged)
        for k in ("name", "board_role", "trigger", "column", "value", "action", "recipients", "email_to", "subject",
                  "body", "mode"):
            setattr(row, k, clean.get(k))
        row.enabled = bool(clean.get("enabled", True))
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(row)
        return _as_dict(row)


def delete(recipe_id: int) -> None:
    with SessionLocal() as db:
        row = db.get(AutomationRecipeDB, recipe_id)
        if row is None:
            raise LookupError("No such recipe.")
        db.delete(row)
        db.commit()


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def _shape(view: Dict[str, Any], r: Dict[str, Any]) -> Dict[str, Any]:
    titles = {c["id"]: c["title"] for c in view["columns"]}
    return {"id": r["id"], "name": r["name"], "board": view["name"],
            "values": {titles.get(k, k): v for k, v in r["values"].items()}, "app": r.get("app") or {}}


def _row(role: str, item_id: str) -> Optional[Dict[str, Any]]:
    """The item as the board shows it (column titles -> values), plus the app's view."""
    from app.services.coordination import boards

    board = board_for(role)
    if not board:
        return None
    try:
        view = boards.view(board)
    except LookupError:
        return None
    return next((_shape(view, r) for r in view["items"] if r["id"] == str(item_id)), None)


def _fill(text: str, row: Dict[str, Any], extra: Dict[str, str]) -> str:
    out = text or ""
    for key, value in {**row["values"], **extra, "item": row["name"], "board": row["board"]}.items():
        out = out.replace("{" + str(key) + "}", str(value or ""))
    return out


def _people(spec: str, row: Dict[str, Any]) -> List[str]:
    from app.services.coordination.autopilot import reviewers

    out: List[str] = []
    for part in [p.strip() for p in (spec or "owner").split(",") if p.strip()]:
        key = part.casefold()
        if key == "owner":
            out += list(row["app"].get("owners") or []) or ([row["app"]["owner_email"]] if row["app"].get("owner_email") else [])
        elif key == "leadership":
            out += alerts.leadership()
        elif key in ("reviewers", "management"):
            out += reviewers()
        elif "@" in part:
            out.append(part)
    return out or reviewers()


def _email_to(spec: str, role: str, row: Dict[str, Any]) -> Optional[str]:
    from app.services.coordination.autopilot import vendor_email

    key = (spec or "").strip().casefold()
    if "@" in key:
        return spec.strip()
    if key == "vendor":
        vendor = row["values"].get("Vendor") or row["values"].get("Vendor Name") or ""
        return vendor_email(vendor) if vendor else None
    if key == "customer" and role == "rfqs" and row["app"].get("rfq_id"):
        from app.services.coordination import rfqs

        return rfqs.get(row["app"]["rfq_id"]).get("contact_email")
    return None


def _intent(recipe: Dict[str, Any], row: Dict[str, Any], key: str, extra: Dict[str, str]):
    from app.services.coordination.autopilot import Intent

    title = _fill(recipe.get("subject") or f"{recipe['name']}: {{item}}", row, extra)
    body = _fill(recipe.get("body") or f"{describe(recipe)}\nItem: {{item}} on {{board}}.", row, extra)
    if recipe["action"] == "notify":
        return Intent("alert", key, f"{recipe['name']}: {row['name']}", to=_people(recipe.get("recipients"), row),
                      title=title, body=body)
    to = _email_to(recipe.get("email_to"), recipe["board_role"], row)
    if not to:
        return Intent("alert", key, f"{recipe['name']}: no address for {recipe.get('email_to')}",
                      to=_people(recipe.get("recipients"), row), title=f"{title} (no email address on file)",
                      body=f"{body}\n\nNo email address was found for '{recipe.get('email_to')}', so nothing was drafted.")
    from app.services.coordination.autopilot import reviewers

    return Intent("draft", key, f"{recipe['name']}: email {to} about {row['name']}",
                  to=_people(recipe.get("recipients"), row) if recipe.get("recipients") else reviewers(),
                  tool="send_email", args={"to": to, "subject": title, "body": body, "_item": row["id"],
                                           "_why": _why(recipe, row, extra)})


# Board columns worth showing a reviewer beside a draft, when the board has them.
_CONTEXT_COLUMNS = ("Status", "Vendor", "Description", "Allied PO#", "Customer PO#", "VENDOR ETS", "Target Date",
                    "Customer", "Engineer", "Estimate finish", "Bidding vendors", "Last follow up")


def _why(recipe: Dict[str, Any], row: Dict[str, Any], extra: Dict[str, str]) -> List[str]:
    """Why this draft exists, in the board's words, so a reviewer can judge it without opening monday."""
    out = [f"Recipe: {describe(recipe)}"]
    if extra.get("days"):
        out.append(f"{extra.get('status') or 'Date'}: {extra['days']} days")
    out += [f"{c}: {row['values'][c]}" for c in _CONTEXT_COLUMNS if row["values"].get(c)]
    notes = (row["values"].get("Notes") or "").strip().splitlines()
    if notes:
        out.append(f"Latest note: {notes[0][:200]}")
    return out


def wanted(event: str) -> bool:
    """True when an enabled status recipe could fire on this event."""
    role = EVENT_ROLE.get(event)
    return bool(role) and any(r["enabled"] and r["trigger"] == "status_is" for r in list_recipes(role))


def on_event(event: str, payload: Dict[str, Any], dry_run: bool = False) -> List[Dict[str, Any]]:
    """Status recipes for a status change on a board. Called by the event handler."""
    from app.services.coordination import autopilot

    role = EVENT_ROLE.get(event)
    if not role:
        return []
    label = str(payload.get("to") or "").strip()
    item = payload.get("job") or payload.get("milestone") or payload.get("rfq") or {}
    item_id = str(item.get("monday_item_id") or item.get("item_id") or "")
    chosen = [r for r in list_recipes(role) if r["enabled"] and r["trigger"] == "status_is"]
    if not chosen:
        return []
    # Read the board once per event, not once per recipe.
    row = _row(role, item_id) if item_id else None
    if row is None:
        row = {"id": item_id or str(item.get("id")), "name": item.get("description") or item.get("name") or item.get("subject") or "item",
               "board": role, "values": {"Vendor": item.get("vendor") or ""},
               "app": {"owners": item.get("owner_emails") or [], "owner_email": item.get("owner_email")}}
    labels = {label.casefold()}
    if role == "rfqs":
        row["app"]["rfq_id"] = item.get("id")
        # An RFQ's event carries the app's status (WAITING_VENDOR); people name the board's label.
        if row["values"].get("Status"):
            labels.add(str(row["values"]["Status"]).strip().casefold())
    # One per move and day: the same move handled twice (a retry, two readers) is drafted once,
    # and a status reached again on a later day counts again.
    move = f"{str(payload.get('from') or '').casefold()}>{label.casefold()}:{alerts.today().isoformat()}"
    did: List[Dict[str, Any]] = []
    for recipe in chosen:
        if recipe["value"].strip().casefold() not in labels:
            continue
        key = f"recipe-{recipe['id']}:{row['id']}:{move}"
        did += autopilot.carry_out(f"recipe_{recipe['id']}", recipe["mode"], [_intent(recipe, row, key, {"status": label})], dry_run)
    return did


def play_dates(on: date):
    """Date recipes: items whose date column is within N days (or past), once per item and date."""
    from app.services.coordination import boards

    out = []
    for recipe in [r for r in list_recipes() if r["enabled"] and r["trigger"] == "date_within"]:
        board = board_for(recipe["board_role"])
        if not board:
            continue
        try:
            view = boards.view(board, hide_done=True)
        except LookupError:
            continue
        col = next((c for c in view["columns"] if recipe["column"] in (c["id"], c["title"])), None)
        if col is None:
            continue
        for r in view["items"]:
            try:
                due = datetime.strptime((r["values"].get(col["id"]) or "")[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if (due - on).days > int(recipe["value"]):
                continue
            out.append((recipe, _intent(recipe, _shape(view, r), f"recipe-{recipe['id']}:{r['id']}:{due}",
                                        {"date": due.isoformat(), "days": str((due - on).days)})))
    return out


def _local_day(at: str) -> date:
    """The local (Allied) day of a logged UTC time, to compare with alerts.today()."""
    return datetime.fromisoformat(at).astimezone(alerts.now_local().tzinfo).date()


def play_status_for(on: date):
    """Items that have sat in one status for N days or more, once per item and stay."""
    from app.services.coordination import activity, boards

    out = []
    for recipe in [r for r in list_recipes() if r["enabled"] and r["trigger"] == "status_for"]:
        board = board_for(recipe["board_role"])
        if not board:
            continue
        try:
            view = boards.view(board)
            since = activity.in_status_since(board, view["status_column"])
        except LookupError:
            continue
        label = recipe["column"].strip().casefold()
        for r in view["items"]:
            now = (r["values"].get(view["status_column"]) or "").strip()
            moved = since.get(r["id"])
            if now.casefold() != label or not moved or (moved["status"] or "").strip().casefold() != label:
                continue
            start = _local_day(moved["since"])
            days = (on - start).days
            if days < int(recipe["value"]):
                continue
            out.append((recipe, _intent(recipe, _shape(view, r), f"recipe-{recipe['id']}:{r['id']}:{start}",
                                        {"status": now, "days": str(days), "date": start.isoformat()})))
    return out


def run_dates(on: Optional[date] = None, dry_run: bool = False) -> List[Dict[str, Any]]:
    """The hourly recipes: dates coming up, and items sitting too long in one status."""
    from app.services.coordination import autopilot

    on = on or alerts.today()
    did = []
    for recipe, intent in play_dates(on) + play_status_for(on):
        did += autopilot.carry_out(f"recipe_{recipe['id']}", recipe["mode"], [intent], dry_run)
    if not dry_run:
        autopilot.tell_reviewers(did)
    return did


def safe_on_event(event: str, payload: Dict[str, Any], dry_run: bool = False) -> List[Dict[str, Any]]:
    try:
        return on_event(event, payload, dry_run)
    except Exception as exc:  # noqa: BLE001 - a bad recipe must not stop the event's other reactions
        logger.error(f"Recipes for {event} failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Allied's starter recipes
# ---------------------------------------------------------------------------
# Each comes from what the boards showed when read on 31 Oct 2026 (`why`, dated by WHY_AS_OF);
# starters(with_preview) adds `live`, the same fact worked out from the board now. A manager
# adds one with a click; none is on until then. Jane and Athish appear because the boards'
# own status labels name them (SEND TO JANE, Athish to Follow); who is told is set per recipe. Templates use
# the boards' own column titles, so {Allied PO#} is the order's PO number.

_SIGN = "\n\nThank you,\nAllied Welding Systems"
STARTERS: List[Dict[str, Any]] = [
    {"slug": "orders-ask-photos", "why": "Orders went to EXPECTING PICS 12 times and waited a median of 3 days for photos.",
     "saves": "One email per order, and the note saying it was sent.",
     "recipe": {"name": "Ask the vendor for photos", "board_role": "vendors", "trigger": "status_is", "value": "EXPECTING PICS",
                "action": "draft_email", "email_to": "vendor", "subject": "Photos before crating: our PO {Allied PO#}",
                "body": "Hello,\n\nBefore {Description} (our PO {Allied PO#}) is crated, please send photos of the finished "
                        "part with the main dimensions measured, and the nameplate or marking if it has one." + _SIGN}},
    {"slug": "orders-ask-pi", "why": "WAITING ON PI came up 33 times; three OC-AMARILLO orders waited there 80 days.",
     "saves": "The proforma request email, written once instead of per order.",
     "recipe": {"name": "Ask the vendor for the proforma invoice", "board_role": "vendors", "trigger": "status_is",
                "value": "WAITING ON PI", "action": "draft_email", "email_to": "vendor",
                "subject": "Proforma invoice please: our PO {Allied PO#}",
                "body": "Hello,\n\nPlease send the proforma invoice for our PO {Allied PO#} ({Description}) so we can "
                        "arrange the deposit and keep your production slot." + _SIGN}},
    {"slug": "orders-pi-stuck", "why": "The same three OC-AMARILLO orders sat 80 days in WAITING ON PI with nobody alerted.",
     "saves": "Finding stalled orders by scrolling the board.",
     "recipe": {"name": "Proforma still missing after 5 days", "board_role": "vendors", "trigger": "status_for",
                "column": "WAITING ON PI", "value": "5", "action": "notify", "recipients": "reviewers",
                "subject": "{item} {Description}: no proforma after {days} days (PO {Allied PO#}, {Vendor})"}},
    {"slug": "orders-ask-invoice", "why": "23 orders moved to AWAITING INVOICE in the last 30 days.",
     "saves": "About 23 \"please send the invoice and shipping documents\" emails a month.",
     "recipe": {"name": "Ask the vendor for the final invoice and documents", "board_role": "vendors",
                "trigger": "status_is", "value": "AWAITING INVOICE", "action": "draft_email", "email_to": "vendor",
                "subject": "Final invoice and shipping documents: our PO {Allied PO#}",
                "body": "Hello,\n\nPlease send the final commercial invoice, packing list and bill of lading for our PO "
                        "{Allied PO#} ({Description})." + _SIGN}},
    {"slug": "orders-drawing-approval", "why": "WAITING ON DRAWING APPROVAL came up 35 times: median 6 days, longest 21.",
     "saves": "Remembering which customer approvals to chase.",
     "recipe": {"name": "Customer drawing approval overdue", "board_role": "vendors", "trigger": "status_for",
                "column": "WAITING ON DRAWING APPROVAL", "value": "7", "action": "notify", "recipients": "reviewers",
                "subject": "{item} has not approved the drawing for {Description} ({days} days)",
                "body": "{item} has had the drawing for {Description} for {days} days (customer PO {Customer PO#}). "
                        "Every day here moves the ship date. Chase the approval."}},
    {"slug": "orders-long-fabrication",
     "why": "Fabrication runs a median of 15 days, but a NOL-TEC order has sat there 73 days and an OC-IRVING one 67.",
     "saves": "Spotting the order that has quietly stalled at the vendor.",
     "recipe": {"name": "Fabrication running long", "board_role": "vendors", "trigger": "status_for", "column": "FABRICATION",
                "value": "45", "action": "notify", "recipients": "reviewers",
                "subject": "{item} {Description}: {days} days in fabrication at {Vendor}",
                "body": "{Vendor} promised {VENDOR ETS}; the customer's date is {Target Date}. Ask for photos of progress."}},
    {"slug": "orders-stuck", "why": "Orders went to STUCK 3 times this quarter, for up to 17 days.",
     "saves": "A stuck order waiting for someone to notice it.",
     "recipe": {"name": "Order marked stuck", "board_role": "vendors", "trigger": "status_is", "value": "STUCK",
                "action": "notify", "recipients": "reviewers, leadership",
                "subject": "Stuck: {item} {Description} ({Vendor}, PO {Allied PO#})", "body": "Notes: {Notes}"}},
    {"slug": "orders-send-to-jane", "why": "SEND TO JANE came up 24 times; each is a hand-off someone has to pass on.",
     "saves": "The message telling Jane an order is hers.", "needs": "recipients",
     "recipe": {"name": "Order is for Jane", "board_role": "vendors", "trigger": "status_is", "value": "SEND TO JANE",
                "action": "notify", "subject": "For you: {item} {Description} (PO {Allied PO#})",
                "body": "{item} {Description} is marked SEND TO JANE. Vendor {Vendor}, customer PO {Customer PO#}."}},
    {"slug": "eng-review-waiting", "why": "Drawings sit in Sent for Review for a median of 26 days, the longest for 52.",
     "saves": "Reviews that wait because nobody was reminded.",
     "recipe": {"name": "Drawing waiting for review", "board_role": "projects", "trigger": "status_for",
                "column": "Sent for Review", "value": "10", "action": "notify", "recipients": "owner, reviewers",
                "subject": "{item} ({Customer}) has waited {days} days for review"}},
    {"slug": "eng-scanned-idle", "why": "Large Sand Auger Trough and Auger Trough have sat at Scanned for 81 days.",
     "saves": "Scans that never turn into drawings.",
     "recipe": {"name": "Scanned but not started", "board_role": "projects", "trigger": "status_for", "column": "Scanned",
                "value": "14", "action": "notify", "recipients": "owner, reviewers",
                "subject": "{item} ({Customer}) scanned {days} days ago, not started"}},
    {"slug": "eng-athish-follow", "why": "Jobs are handed to Athish by status (Athish to Follow).",
     "saves": "The message telling Athish a job is his.", "needs": "recipients",
     "recipe": {"name": "Job handed to Athish", "board_role": "projects", "trigger": "status_is", "value": "Athish to Follow",
                "action": "notify", "subject": "For you: {item} ({Customer})"}},
    {"slug": "rfq-vendor-quiet", "why": "Liners and Bottom Hopper waited 60 days for a vendor quote.",
     "saves": "Quotes lost because the vendor never answered and nobody noticed.",
     "recipe": {"name": "Vendor quote overdue", "board_role": "rfqs", "trigger": "status_for",
                "column": "Waiting on vendor quote", "value": "10", "action": "notify", "recipients": "reviewers",
                "subject": "{item} for {Customer}: no quote from {Bidding vendors} in {days} days",
                "body": "Last follow up: {Last follow up}. Chase the vendor or find another, or tell {Customer} it will be late."}},
    {"slug": "rfq-markup", "why": "Need markup is where the price is decided, and that stays with a person.",
     "saves": "A priced RFQ waiting for someone to set the margin.",
     "recipe": {"name": "Ready for markup", "board_role": "rfqs", "trigger": "status_for", "column": "Need markup",
                "value": "1", "action": "notify", "recipients": "reviewers",
                "subject": "{item} for {Customer} is priced and needs markup"}},
]


WHY_AS_OF = "2026-10-31"


def _same_rule(r: Dict[str, Any]) -> tuple:
    return r["board_role"], r["trigger"], (r.get("column") or "").casefold(), str(r["value"]).casefold()


def starters(with_preview: bool = False) -> List[Dict[str, Any]]:
    """The starter recipes, each saying whether one like it (same board, trigger and label) is
    already added and, with_preview, which items it would act on today."""
    have = {_same_rule(r) for r in list_recipes()}
    out, times = [], {}
    for s in STARTERS:
        row = {**{k: v for k, v in s.items() if k != "recipe"}, "why_as_of": WHY_AS_OF,
               "recipe": {**s["recipe"], "description": describe({"mode": "suggest", **s["recipe"]})},
               "board_id": board_for(s["recipe"]["board_role"]), "added": _same_rule(s["recipe"]) in have}
        if with_preview:
            row["today"] = preview(s["recipe"])
            row["live"] = _live_fact(s["recipe"], times)
        out.append(row)
    return out


def _live_fact(recipe: Dict[str, Any], cache: Dict[str, Any]) -> Optional[str]:
    """The starter's reason worked out from the board's history now, so it doesn't go stale like
    the written one (which says the date it was read). None when the history isn't read yet."""
    from app.services.coordination import activity, boards

    board = board_for(recipe["board_role"])
    if not board or recipe["trigger"] == "date_within":
        return None
    if board not in cache:
        try:
            view = boards.view(board)
            cache[board] = activity.status_times(board, view["status_column"], boards._done_labels(view["role"]))
        except LookupError:
            cache[board] = None
    times = cache[board]
    if not times:
        return None
    label = (recipe.get("column") if recipe["trigger"] == "status_for" else recipe["value"]).strip().casefold()
    hit = next((b for b in times["by_status"] if (b["status"] or "").strip().casefold() == label), None)
    if hit is None:
        return f"No item has moved out of {recipe.get('column') or recipe['value']} in the last ~90 days."
    return (f"In the last ~90 days, items left {hit['status']} {hit['times_left']} times, after a median of "
            f"{hit['median_days']} days (longest {hit['longest_days']}).")


def preview(recipe: Dict[str, Any], on: Optional[date] = None) -> List[Dict[str, Any]]:
    """What a recipe would act on today, without saving it or doing anything: the items now
    in its status (status_is), sitting past its days (status_for), or with its date near."""
    from app.services.coordination import activity, boards

    on = on or alerts.today()
    board = board_for(recipe["board_role"])
    try:
        # A status recipe can name a finished status (AWAITING INVOICE), so only date recipes skip finished items.
        view = boards.view(board, hide_done=recipe["trigger"] == "date_within") if board else None
    except LookupError:
        view = None
    if view is None:
        return []
    status_id = view["status_column"]
    out = []
    if recipe["trigger"] == "date_within":
        col = next((c for c in view["columns"] if recipe.get("column") in (c["id"], c["title"])), None)
        for r in view["items"] if col else []:
            try:
                due = datetime.strptime((r["values"].get(col["id"]) or "")[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if (due - on).days <= int(recipe["value"]):
                out.append({"item_id": r["id"], "item": r["name"], "detail": f"{col['title']} {due.isoformat()}"})
        return out
    label = (recipe.get("column") if recipe["trigger"] == "status_for" else recipe["value"]).strip().casefold()
    try:
        since = activity.in_status_since(board, status_id) if recipe["trigger"] == "status_for" else {}
    except LookupError:
        return []  # the board's history isn't read yet (just started, or monday in simulation)
    for r in view["items"]:
        if (r["values"].get(status_id) or "").strip().casefold() != label:
            continue
        if recipe["trigger"] == "status_for":
            moved = since.get(r["id"])
            if not moved or (moved["status"] or "").strip().casefold() != label:
                continue
            days = (on - _local_day(moved["since"])).days
            if days < int(recipe["value"]):
                continue
            out.append({"item_id": r["id"], "item": r["name"], "detail": f"{days} days"})
        else:
            out.append({"item_id": r["id"], "item": r["name"], "detail": "in this status now"})
    return out


_starter_lock = threading.Lock()


def add_starter(slug: str, overrides: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    s = next((x for x in STARTERS if x["slug"] == slug), None)
    if s is None:
        raise LookupError(f"No starter recipe {slug}.")
    if s.get("needs") and not overrides.get(s["needs"]):
        raise ValueError(f"This one needs {s['needs']}: say who it goes to (an email address).")
    with _starter_lock:  # two managers clicking Add at once make one recipe
        if _same_rule(s["recipe"]) in {_same_rule(r) for r in list_recipes()}:
            raise ValueError("That recipe is already added.")
        return create({**s["recipe"], **{k: v for k, v in overrides.items() if v not in (None, "")}}, user)


# ---------------------------------------------------------------------------
# Point and click: everything the rule form needs as choices, nothing typed
# ---------------------------------------------------------------------------

_DATE_HINTS = ("date", "ets", "eta", "finish", "due", "deadline", "follow up", "issued", "received", "start")


def options(role: str) -> Dict[str, Any]:
    """The choices for a rule on one board: its status labels (in monday's order and colours),
    its date columns, the column names a message can quote, who can be told, and where an
    email can go. Read from the board copy; empty lists until the board has been read."""
    from app.services.coordination import boards
    from app.services.coordination.autopilot import reviewers

    if role not in ROLES:
        raise ValueError(f"board_role must be one of {', '.join(ROLES)}.")
    board = board_for(role)
    view = None
    if board:
        try:
            view = boards.view(board)
        except LookupError:
            view = None
    labels, dates, fields = [], [], []
    if view:
        status_col = next((c for c in view["columns"] if c["id"] == view["status_column"]), {})
        labels = [{"label": k, "color": v} for k, v in (status_col.get("labels") or {}).items()]
        for c in view["columns"]:
            fields.append(c["title"])
            sample = next((r["values"].get(c["id"]) for r in view["items"] if r["values"].get(c["id"])), "") or ""
            looks_dated = len(sample) >= 10 and sample[4:5] == "-" and sample[7:8] == "-"
            if c["type"] in ("date", "timeline") or (c["type"] == "formula" and looks_dated) or \
                    (looks_dated and any(h in c["title"].lower() for h in _DATE_HINTS)):
                dates.append({"id": c["id"], "title": c["title"]})
    people = [{"value": "owner", "label": "The item's owner (from the board)"},
              {"value": "reviewers", "label": "Reviewers"}, {"value": "leadership", "label": "Leadership"}]
    seen = set()
    for email in reviewers() + alerts.leadership() + [c["email"] for c in alerts.contacts().values() if c.get("active")]:
        if email.lower() not in seen:
            seen.add(email.lower())
            name = (alerts.contacts().get(email.lower()) or {}).get("name")
            people.append({"value": email, "label": f"{name} ({email})" if name else email})
    email_to = [{"value": "vendor", "label": "The order's vendor"}] if role == "vendors" else []
    if role == "rfqs":
        email_to.append({"value": "customer", "label": "The RFQ's customer contact"})
    return {
        "board_role": role, "board_id": board, "board_read": view is not None,
        "triggers": [
            {"value": "status_is", "label": "When the status becomes...", "needs": ["value"], "value_from": "labels"},
            {"value": "status_for", "label": "When an item sits in a status for N days", "needs": ["column", "value"],
             "column_from": "labels", "value_hint": "days, 1 to 365"},
            {"value": "date_within", "label": "When a date is N days away (or past)", "needs": ["column", "value"],
             "column_from": "dates", "value_hint": "days, 0 = on the day"},
        ],
        "actions": [{"value": "notify", "label": "Tell people in the app"},
                    {"value": "draft_email", "label": "Draft an email for a person to send"}],
        "modes": [{"value": "suggest", "label": "A person sends each email"},
                  {"value": "auto", "label": "Send on its own (only once trusted)"}],
        "labels": labels, "dates": dates, "fields": ["item", "board", "status", "date", "days"] + fields,
        "people": people, "email_to": email_to,
    }
