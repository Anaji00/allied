"""What runs on its own for each monday board, in monday's "When this, then that" words.

monday lists a board's automations as recipes. Allied's automations run in this
app, never in monday: the autopilot's timed plays, the event reactions and the
behind-schedule checks. This module describes them board by board, finds the
drafts each item is waiting on, and lets a person draft one item's message now.

Nothing here writes to monday, and nothing leaves the building: a draft waits in
the confirm queue (the assistant's actions) until a person sends or cancels it.
"""

import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB
from app.services.coordination import autopilot, boards, events, rfqs, vendors
from app.services.coordination import notify as alerts

# How far back a weekly draft (one key per week) is still shown against its item.
WEEKS_SHOWN = 6
# Plays that only read the app's own trackers, so they can be asked "what would you draft now?" cheaply.
PURE_PLAYS = {"vendors": ["vendor_chaser"], "rfqs": ["rfq_vendor_chaser", "quote_followup"]}
EVENT_PREFIX = {"projects": ("milestone.",), "vendors": ("order.", "vendor_job."), "rfqs": ("rfq.",)}


def _recipes(role: str) -> List[Dict[str, str]]:
    """Each recipe: name (the play or reaction), kind, when, then. Wording follows monday's builder."""
    follow = " and ".join((settings.QUOTE_FOLLOWUP_DAYS or "").split(",")) or "a few"
    on_check = settings.SCHEDULE_CHECK_SECONDS > 0
    if role == "projects":
        return [
            {"name": "milestone_due", "kind": "check", "when": f"When a milestone is due within {settings.DUE_SOON_DAYS} days",
             "then": "remind its owners", "mode": "auto" if on_check else "off"},
            {"name": "milestone_overdue", "kind": "check", "when": "When a milestone passes its date",
             "then": f"alert its owners, and leadership after {settings.ESCALATE_AFTER_DAYS} days", "mode": "auto" if on_check else "off"},
            {"name": "review_waiting", "kind": "check", "when": "When submitted design documents wait too long for review",
             "then": "remind the reviewers", "mode": "auto" if on_check else "off"},
            {"name": "project_late", "kind": "check", "when": "When a project passes its due date",
             "then": "alert its owners and leadership", "mode": "auto" if on_check else "off"},
        ]
    if role == "vendors":
        return [
            {"name": "vendor_chaser", "kind": "play",
             "when": "When an order hasn't shipped its wait after the PO (its quoted lead time, or set on the order)",
             "then": "draft one follow-up asking the vendor for its ship date; after that, staff follow up from the row"},
            {"name": "vendor_late_chase", "kind": "reaction", "when": "When an order turns late and its wait after the PO is over",
             "then": "draft that same follow-up straight away"},
            {"name": "order_photos", "kind": "reaction", "when": "When Status changes to waiting on photos",
             "then": "tell staff to check the photos against the drawing"},
            {"name": "order_shipped", "kind": "reaction", "when": "When Status changes to SHIPPED",
             "then": "start tracking the shipment"},
            {"name": "orders_watch", "kind": "play", "when": "Every week",
             "then": "list orders still owing payment and photos still waiting"},
            {"name": "vendor_checks", "kind": "check", "when": "When an order would make its project late",
             "then": "alert the order's owner", "mode": "auto" if on_check else "off"},
        ]
    if role == "rfqs":
        return [
            {"name": "rfq_board", "kind": "play", "when": "When this board changes (or every hour)",
             "then": "read it into the RFQ tracker"},
            {"name": "rfq_acknowledge", "kind": "reaction", "when": "When a request arrives by email or phone",
             "then": "draft a receipt to the customer with the quote date"},
            {"name": "rfq_ask_vendors", "kind": "reaction", "when": "When Status changes to waiting on a vendor",
             "then": "draft a price request to each bidding vendor"},
            {"name": "rfq_vendor_chaser", "kind": "play",
             "when": f"When a vendor's price is {settings.RFQ_VENDOR_CHASE_DAYS} working days late",
             "then": "draft a reminder to that vendor"},
            {"name": "quote_followup", "kind": "play", "when": f"When a sent quote has no answer after {follow} working days",
             "then": "draft a follow-up to the customer"},
            {"name": "rfq_won", "kind": "reaction", "when": "When a quote is won",
             "then": "tell the owner and leadership to raise the order"},
            {"name": "rfq_checks", "kind": "check", "when": "When a quote deadline is near or past",
             "then": "alert the RFQ's owner", "mode": "auto" if on_check else "off"},
        ]
    return []


def _action_id(key: str) -> str:
    """The confirm-queue id a draft gets; the same as autopilot._draft gives it."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"allied-autopilot:{key}").hex


def _weeks() -> List[str]:
    today = alerts.today()
    return [autopilot._week(today - timedelta(weeks=n)) for n in range(WEEKS_SHOWN)]


def _rfqs_by_item() -> Dict[str, Dict[str, Any]]:
    return {r["monday_item_id"]: r for r in rfqs.list_rfqs(open_only=False, limit=5000) if r.get("monday_item_id")}


def _jobs_by_item(board_id: str) -> Dict[str, Dict[str, Any]]:
    return {j["monday_item_id"]: j for j in vendors.overview(include_shipped=True)["jobs"]
            if j.get("monday_item_id") and str(j.get("board_id") or board_id) == board_id}


def _candidate_keys(role: str, record: Dict[str, Any]) -> List[str]:
    """Every key a draft about this record could have been made under."""
    if role == "vendors":
        today = alerts.today()
        # The automatic follow-up (one per PO), the ones asked for from the board (one a day),
        # and the weekly chasers drafted before the follow-up rule.
        return ([f"vendor-followup:{record['id']}:{record.get('ordered_on')}"]
                + [f"vendor-followup:{record['id']}:{(today - timedelta(days=n)).isoformat()}" for n in range(WEEKS_SHOWN * 7)]
                + [f"vendor-chase:{record['id']}:{w}" for w in _weeks()])
    if role == "rfqs":
        rid = record["id"]
        names = [n.strip().casefold() for n in (record.get("vendor") or "").split(",") if n.strip()]
        steps = [s.strip() for s in (settings.QUOTE_FOLLOWUP_DAYS or "").split(",") if s.strip()]
        return ([f"rfq-ack:{rid}"] + [f"rfq-price-ask:{rid}:{n}" for n in names]
                + [f"rfq-vendor-chase:{rid}:{n}:{w}" for n in names for w in _weeks()]
                + [f"quote-follow:{rid}:{s}" for s in steps])
    return []


def _records(role: str, board_id: str) -> Dict[str, Dict[str, Any]]:
    if role == "vendors":
        return _jobs_by_item(board_id)
    if role == "rfqs":
        return _rfqs_by_item()
    return {}


def _record_of(key: str) -> Tuple[str, str]:
    """A draft key's record: ("job", "12") or ("rfq", "7")."""
    kind, _, rest = key.partition(":")
    return ("job" if kind in ("vendor-chase", "vendor-followup") else "rfq"), rest.split(":")[0]


def _intents(role: str, records: Dict[str, Dict[str, Any]]) -> List[Tuple[str, Any]]:
    """Every message an automation would draft for this board right now, as (recipe, intent).

    The timed plays are asked directly. Reactions only fire when something changes,
    so for an item already in the state that sets one off (an RFQ waiting on a vendor
    price, a new request not yet acknowledged) the reaction is asked again here; its
    key is the one the event would have used, so it is never drafted twice."""
    today = alerts.today()
    out: List[Tuple[str, Any]] = []
    for play in PURE_PLAYS.get(role, []):
        try:
            out += [(play, it) for it in autopilot.PLAYS[play].run(today)]
        except Exception:  # noqa: BLE001 - a play that can't run has nothing due; the board still shows
            continue
    if role == "rfqs":
        for r in records.values():
            try:
                if r["status"] == "NEW":
                    out += [("rfq_acknowledge", it) for it in events.acknowledge_rfq({"rfq": r})]
                if r["status"] == "WAITING_VENDOR":
                    out += [("rfq_ask_vendors", it) for it in events.ask_vendors_for_price({"rfq": r, "to": "WAITING_VENDOR"})]
            except Exception:  # noqa: BLE001 - an RFQ missing a field has nothing due
                continue
    return [(name, it) for name, it in out if it.kind == "draft"]


def _due(role: str, records: Dict[str, Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Per item: what could be drafted now, and the chasers a person has to send by hand
    (no email on file for the vendor, so nothing can be drafted)."""
    by_record = {str(r["id"]): item for item, r in records.items()}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for name, it in _intents(role, records):
        item = by_record.get(_record_of(it.key)[1])
        if item:
            out.setdefault(item, []).append({"play": name, "key": it.key, "summary": it.summary, "action_id": _action_id(it.key)})
    if role == "vendors":
        today = alerts.today()
        for item, j in records.items():
            if (j.get("follow_up") or {}).get("due") and not autopilot.vendor_email(j["vendor"]):
                out.setdefault(item, []).append({
                    "play": "vendor_chaser", "key": None, "manual": True, "action_id": None,
                    "summary": f"No email on file for {j['vendor']}: send this chaser yourself",
                    "body": autopilot.vendor_chase_body(j, today)})
    return out


def _rules(role: str) -> List[Dict[str, Any]]:
    """People's own recipes for this board (recipes.py), newest last."""
    try:
        from app.services.coordination import recipes
    except ImportError:  # an older backend without people's recipes
        return []
    return recipes.list_recipes(role)


def _rule_intents(role: str, board_id: str) -> List[Tuple[str, Any, str]]:
    """What people's date and "sat in a status" recipes would draft on this board now, as
    (recipe name, intent, item id).

    Their keys are recipe-{id}:{item}:{date}, so the item is in the key. Status-change
    recipes fire on a change, with the time in the key, so they are found by their _item instead."""
    try:
        from app.services.coordination import recipes

        if recipes.board_for(role) != board_id:
            return []
        today = alerts.today()
        pairs = recipes.play_dates(today) + (recipes.play_status_for(today) if hasattr(recipes, "play_status_for") else [])
    except Exception:  # noqa: BLE001 - a recipe that can't be checked has nothing due
        return []
    return [(f"recipe_{r['id']}", it, it.key.split(":")[1]) for r, it in pairs
            if r["board_role"] == role and it.kind == "draft" and it.key.count(":") >= 2]


def _draft_view(row: AssistantActionDB, user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.assistant.agent import _ttl, _yours

    args = row.args or {}
    expired = row.status == "PENDING" and autopilot.workdays.as_utc(row.created_at) < autopilot.workdays.now() - _ttl(row)
    return {"id": row.id, "play": args.get("_play"), "summary": row.summary, "status": "EXPIRED" if expired else row.status,
            "to": args.get("to"), "subject": args.get("subject"), "body": args.get("body"),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            "reviewer": row.user_email, "yours": _yours(row, user)}


def for_board(board_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """The board's recipes with their modes and last runs, recent events, and each item's drafts."""
    role = next((b["role"] for b in boards.boards() if b["board_id"] == str(board_id)), None)
    if role is None:
        raise LookupError(f"Board {board_id} has not been read yet.")
    play_modes, reaction_modes = autopilot.modes(), events.modes()
    recent = [e for e in events.recent if e["event"].startswith(EVENT_PREFIX.get(role, ("-",)))]
    recipes = []
    for r in _recipes(role):
        kind = r["kind"]
        mode = r.get("mode") or (play_modes if kind == "play" else reaction_modes).get(r["name"], "off")
        if kind != "check" and not settings.AUTOPILOT_ENABLED:
            mode = "off"
        last: Optional[Dict[str, Any]] = None
        if kind == "play" and r["name"] in autopilot.last_run:
            run = autopilot.last_run[r["name"]]
            last = {"at": run.get("at"), "did": run.get("did", [])[:10], "error": run.get("error")}
        elif kind == "reaction":
            hit = next((e for e in recent if any(x["name"] == r["name"] for x in e["reactions"])), None)
            if hit:
                did = next(x for x in hit["reactions"] if x["name"] == r["name"])
                last = {"at": hit["at"], "did": did.get("did", [])[:10], "error": did.get("error")}
        recipes.append({**{k: v for k, v in r.items() if k != "mode"}, "mode": mode, "last": last,
                        "can_preview": kind == "play"})

    records = _records(role, str(board_id))
    rules = _rules(role)
    rule_intents = _rule_intents(role, str(board_id))
    keys = {item: _candidate_keys(role, rec) for item, rec in records.items()}
    ids = {_action_id(k): item for item, ks in keys.items() for k in ks}
    ids.update({_action_id(it.key): item for _, it, item in rule_intents})
    rule_names = {f"recipe_{r['id']}" for r in rules}
    cutoff = autopilot.workdays.now() - timedelta(days=14)
    drafts: Dict[str, List[Dict[str, Any]]] = {}
    with SessionLocal() as db:
        rows = db.query(AssistantActionDB).filter(AssistantActionDB.id.in_(list(ids))).all() if ids else []
        # A status recipe's draft carries its item (the key has the time in it, so it can't be recomputed).
        seen = {r.id for r in rows}
        rows += [r for r in db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT",
                                                               AssistantActionDB.created_at >= cutoff - timedelta(days=30))
                 if r.id not in seen and (r.args or {}).get("_play") in rule_names and (r.args or {}).get("_item")]
        for row in sorted(rows, key=lambda r: autopilot.workdays.as_utc(r.created_at), reverse=True):
            if row.status != "PENDING" and (row.decided_at is None or autopilot.workdays.as_utc(row.decided_at) < cutoff):
                continue
            drafts.setdefault(ids.get(row.id) or str(row.args["_item"]), []).append(_draft_view(row, user))
    known = {d["id"] for ds in drafts.values() for d in ds if d["status"] != "EXPIRED"}
    due_all = _due(role, records)
    names = {f"recipe_{r['id']}": r["name"] for r in rules}
    for name, it, item in rule_intents:
        due_all.setdefault(item, []).append({"play": name, "rule": names.get(name), "key": it.key, "summary": it.summary,
                                             "action_id": _action_id(it.key)})
    due = {item: [d for d in ds if d.get("manual") or d["action_id"] not in known] for item, ds in due_all.items()}
    follow_ups = {}
    if role == "vendors":
        today = alerts.today()
        for item, j in records.items():
            f = j.get("follow_up")
            if not f:
                continue
            # Once the first follow-up's day has come, staff can follow up again on command.
            can = date.fromisoformat(f["first"]) <= today and bool(autopilot.vendor_email(j["vendor"]))
            follow_ups[item] = {**f, "job_id": j["id"], "now_key": f"vendor-followup:{j['id']}:now" if can else None}
    return {
        "board_id": str(board_id), "role": role, "enabled": settings.AUTOPILOT_ENABLED, "follow_ups": follow_ups,
        "every_seconds": settings.AUTOPILOT_SECONDS, "recipes": recipes, "rules": rules, "recent": recent[:20],
        "drafts": drafts, "due": {k: v for k, v in due.items() if v},
        "waiting": sum(1 for ds in drafts.values() for d in ds if d["status"] == "PENDING"),
    }


def draft_now(board_id: str, item_id: str, key: str) -> Dict[str, Any]:
    """Drafts one item's message now, for a person to read and send. Never sends."""
    role = next((b["role"] for b in boards.boards() if b["board_id"] == str(board_id)), None)
    if role is None:
        raise LookupError(f"Board {board_id} has not been read yet.")
    if key.startswith("recipe-"):
        for name, it, item in _rule_intents(role, str(board_id)):
            if it.key == key and item == str(item_id):
                did = autopilot.carry_out(name, "suggest", [it])
                autopilot.tell_reviewers(did)
                return {"play": name, "action_id": _action_id(key), "did": did}
        raise LookupError("There is nothing to draft for this item any more.")
    if role not in PURE_PLAYS:
        raise LookupError("Nothing can be drafted from this board.")
    record = _records(role, str(board_id)).get(str(item_id))
    if record is None or _record_of(key)[1] != str(record["id"]):
        raise LookupError("That draft doesn't belong to this item.")
    today = alerts.today()
    if role == "vendors" and key == f"vendor-followup:{record['id']}:now":
        # "Follow up now": staff's call, any time after the first follow-up's day. The draft is keyed
        # by the day it is made (here, not when the board was read), so a double press makes one.
        first = vendors.first_follow_up(record)
        if first is None or first > today:
            raise LookupError("This order isn't due a follow-up yet.")
        it = autopilot.vendor_chase(record, today, manual=True)
        if it is None:
            raise LookupError(f"There is no email on file for {record['vendor']}.")
        did = autopilot.carry_out("vendor_chaser", "suggest", [it])
        autopilot.tell_reviewers(did)
        return {"play": "vendor_chaser", "action_id": _action_id(key), "did": did}
    for name, it in _intents(role, {str(item_id): record}):
        if it.key == key:
            did = autopilot.carry_out(name, "suggest", [it])
            autopilot.tell_reviewers(did)
            return {"play": name, "action_id": _action_id(key), "did": did}
    raise LookupError("There is nothing to draft for this item any more.")
