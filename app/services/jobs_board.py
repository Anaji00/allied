"""The Jobs board: the app's jobs shown and edited as a board, next to the monday boards. One word, "job".

Each row is a job (reference, title, stage, customer, owner, due, who has the ball, purchase order and shipment state, next step). Where a job matches an item on the monday projects
board (by its reference or its name), the row says so (`on_monday`, `monday_item_id`); where it does not, it can be put on a board with the usual to-monday staging. Projects-board items
that no job matches show as rows "Not a job yet", with a one-click way to make the job.

A cell on this board edits the JOB through the records layer, so the same locks, reasons, audit trail and undo apply as anywhere else; nothing here goes through monday staging.
Reading the board changes nothing; the only write is making a job from a monday item, which is the normal job creation.
"""

import threading
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core import status_words
from app.services import records

_make_lock = threading.RLock()
BOARD_ID = "jobs"
NOT_A_JOB = "not_a_job"

# (id, title, type, editable)
_COLS = [("ref", "Job", "text", False), ("title", "Title", "text", True), ("stage", "Stage", "status", False), ("customer", "Customer", "text", True), ("owner", "Owner", "text", False),
         ("due", "Due", "date", False), ("ball_with", "Waiting on", "text", False), ("po_status", "Purchase orders", "text", False), ("shipment_status", "Shipments", "text", False),
         ("next_action", "Next step", "text", False), ("on_monday", "On monday", "checkbox", False), ("monday_item_id", "monday item", "text", False)]
_RECORD_FIELD = {"title": "title", "customer": "customer"}


def is_jobs(board_id: Any) -> bool:
    return str(board_id) == BOARD_ID


def _stage_labels() -> Dict[str, str]:
    from app.services.coordination import jobs

    return {status_words.word("job_stage", s): "" for s in jobs.STAGES}


def _groups() -> List[Dict[str, Any]]:
    from app.services.coordination import jobs

    return [{"id": s, "title": status_words.word("job_stage", s), "color": ""} for s in jobs.STAGES] + [{"id": NOT_A_JOB, "title": "Not a job yet", "color": ""}]


def columns() -> List[Dict[str, Any]]:
    out = []
    for cid, title, ctype, editable in _COLS:
        c = {"id": cid, "title": title, "type": ctype, "editable": editable, "hint": "Edits the job itself (the same rules as the job screen)" if editable else "Worked out from the job"}
        if ctype == "status":
            c["labels"] = [{"label": k, "color": ""} for k in _stage_labels()]
        out.append(c)
    return out


def info() -> Dict[str, Any]:
    return {"board_id": BOARD_ID, "name": "Jobs", "role": "jobs", "editable": True, "read_at": None, "last_read": {"at": None, "minutes_ago": None, "text": "Live"}, "virtual": True, "columns": columns(), "groups": _groups(),
            "items": len(_rows()), "note": "Cells edit the job itself. A job is put on a monday board with 'Put on a monday board'."}


def listing_entry() -> Dict[str, Any]:
    return {"board_id": BOARD_ID, "name": "Jobs", "role": "jobs", "label": "Jobs", "read_at": None, "last_read": {"at": None, "minutes_ago": None, "text": "Live"}, "items": len(_rows()), "editable": True, "virtual": True}


def _projects_items() -> List[Dict[str, Any]]:
    from app.services.coordination import boards

    pid = str(settings.MONDAY_PROJECTS_BOARD_ID or "")
    b = boards._mirror.get(pid) if pid else None
    if not b:
        return []
    cust_col = next((c["id"] for c in b.get("columns", []) if c["title"].casefold() in ("customer", "client", "company")), None)
    return [{"id": str(i["id"]), "name": i.get("name") or "", "customer": (i.get("values") or {}).get(cust_col, "") if cust_col else "", "group": i.get("group_title")} for i in b.get("items", [])]


def projects_board_id() -> str:
    return str(settings.MONDAY_PROJECTS_BOARD_ID or "")


def stored_links() -> Dict[str, Dict[str, str]]:
    """{job ref: {board_id, item_id}} for the jobs a person has linked to a monday item (or made from one, or pushed to one)."""
    from app.core.database import SessionLocal
    from app.models.db_models import JobDB

    with SessionLocal() as db:
        return {r.ref: {"board_id": r.monday_board_id or "", "item_id": r.monday_item_id} for r in db.query(JobDB).filter(JobDB.monday_item_id.isnot(None))}


def _assign(listed: List[Dict[str, Any]], items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """{job ref: the monday item it SEEMS to be}, for jobs that have no stored link, and only from items no job is linked to. A job whose reference is in the item's name has first claim; then a job
    whose title is the item's name (and whose customer is the same, if both say), the oldest first. A monday item is suggested to at most one job. This is a suggestion; it is never saved."""
    from app.services.coordination import jobs

    taken: Dict[str, str] = {}
    out: Dict[str, Dict[str, Any]] = {}
    for j in listed:
        for it in items:
            if it["id"] not in taken and j["ref"].casefold() in it["name"].casefold():
                taken[it["id"]], out[j["ref"]] = j["ref"], it
                break
    for j in sorted(listed, key=lambda x: x["id"]):
        if j["ref"] in out or j.get("stage") == "CLOSED":                              # a closed job is not the job a monday item is about now
            continue
        title = jobs.norm_name(j.get("title") or "")
        for it in items:
            same_customer = not (it.get("customer") or "").strip() or not (j.get("customer") or "").strip() or jobs.same_customer(it["customer"], j["customer"])
            if title and same_customer and it["id"] not in taken and jobs.norm_name(it["name"]) == title:
                taken[it["id"]], out[j["ref"]] = j["ref"], it
                break
    return out


def link_for(listed: List[Dict[str, Any]], items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """{job ref: {state: "linked"|"suggested", item: {...}|None, board_id, item_id}} the stored links first, then suggestions for the jobs without one."""
    stored = stored_links()
    pid = projects_board_id()
    by_id = {it["id"]: it for it in items}
    out: Dict[str, Dict[str, Any]] = {}
    for ref, l in stored.items():
        out[ref] = {"state": "linked", "item": by_id.get(l["item_id"]) if l["board_id"] == pid else None, "board_id": l["board_id"], "item_id": l["item_id"]}
    free = [it for it in items if it["id"] not in {l["item_id"] for l in stored.values() if l["board_id"] == pid}]            # a link to another board never claims this board's items
    for ref, it in _assign([j for j in listed if j["ref"] not in stored], free).items():
        out[ref] = {"state": "suggested", "item": it, "board_id": projects_board_id(), "item_id": it["id"]}
    return out


def validate_link(ref: str, item_id: str, board_id: Optional[str] = None) -> Dict[str, str]:
    """The checks before a job is linked to a monday item: the item exists on that board, no other job has it, and this job is not linked already."""
    from app.core.database import SessionLocal
    from app.models.db_models import JobDB
    from app.services.coordination import boards

    board = str(board_id or projects_board_id())
    if not board:
        raise ValueError("No monday projects board is set up, so there is nothing to link to.")
    b = boards._mirror.get(board)
    if not b or not any(str(i["id"]) == str(item_id) for i in b.get("items", [])):
        raise LookupError("That monday item is not on the board as the app last read it. Refresh the board first.")
    with SessionLocal() as db:
        me = db.query(JobDB).filter(JobDB.ref == ref).first()
        if me is None:
            raise LookupError(f"No job {ref}.")
        if me.monday_item_id and (me.monday_board_id, me.monday_item_id) != (board, str(item_id)):
            raise ValueError("This job is already linked to a monday item. Unlink it first.")
        other = db.query(JobDB).filter(JobDB.monday_board_id == board, JobDB.monday_item_id == str(item_id), JobDB.id != me.id).first()
        if other is not None:
            raise ValueError(f"That monday item is already linked to {other.ref}.")
    return {"board_id": board, "item_id": str(item_id)}


def set_link(ref: str, item_id: Optional[str], board_id: Optional[str] = None) -> None:
    """Saves (or, with no item, clears) the link. Called by the records layer after the checks, so the change is audited there."""
    from sqlalchemy.exc import IntegrityError

    from app.core.database import SessionLocal
    from app.models.db_models import JobDB

    with SessionLocal() as db:
        row = db.query(JobDB).filter(JobDB.ref == ref).first()
        if row is None:
            raise LookupError(f"No job {ref}.")
        if item_id:
            link = validate_link(ref, item_id, board_id)
            row.monday_board_id, row.monday_item_id = link["board_id"], link["item_id"]
        else:
            row.monday_board_id = row.monday_item_id = None
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise ValueError("That monday item is already linked to another job.") from None


def store_link_after_push(ref: str, board_id: str, item_id: str, who: str = "monday-push") -> bool:
    """When a job that was put on a monday board is pushed, the real monday item becomes its link (only if the job has none and no other job has that item). Never raises."""
    from app.core.audit import audit_log
    from app.core.database import SessionLocal
    from app.models.db_models import JobDB

    try:
        with SessionLocal() as db:
            row = db.query(JobDB).filter(JobDB.ref == ref).first()
            if row is None or row.monday_item_id or db.query(JobDB).filter(JobDB.monday_board_id == str(board_id), JobDB.monday_item_id == str(item_id)).count():
                return False
            row.monday_board_id, row.monday_item_id = str(board_id), str(item_id)
            db.commit()
        audit_log("JOB_MONDAY_LINKED", "job", ref, actor_email=who, detail=f"linked to monday item {item_id} on board {board_id} (pushed)")
        return True
    except Exception:  # noqa: BLE001 - the push is done; the link is a convenience
        return False


def _rows(limit: int = 60, offset: int = 0) -> List[Dict[str, Any]]:
    jobs_part, monday_part = _split(limit, offset)
    return jobs_part + monday_part


def _split(limit: int = 60, offset: int = 0):
    """(this page's job rows, ALL the monday items that are not jobs yet): the two are one list for paging, jobs first, which board_items slices."""
    from app.core.database import SessionLocal
    from app.models.db_models import PurchaseOrderDB, ShipmentDB
    from app.services.coordination import jobs

    listed = jobs.list_jobs("", "", "", limit, offset)["items"]
    ids = [j["id"] for j in listed]
    po_by_job: Dict[int, List[Any]] = {}
    ship_for: Dict[str, str] = {}
    with SessionLocal() as db:
        for p in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.job_id.in_(ids or [0])):
            po_by_job.setdefault(p.job_id, []).append(p)
        for s in db.query(ShipmentDB).filter(ShipmentDB.reference.like("PO %")):
            ship_for[str(s.reference)] = status_words.word("shipment", s.status)
    monday = _projects_items()
    linked = link_for(listed, monday)
    matched, out = {l["item_id"] for l in linked.values() if (l["board_id"] or projects_board_id()) == projects_board_id()}, []
    for j in listed:
        pos = po_by_job.get(j["id"], [])
        lk = linked.get(j["ref"])
        hit = (lk["item"] if lk else None)
        if lk and not hit:                                                              # linked to an item this app has not read (another board, or since removed): still linked
            hit = {"id": lk["item_id"], "name": ""}
        b = j.get("ball") or {}
        counts: Dict[str, int] = {}
        for p in pos:
            counts[p.status] = counts.get(p.status, 0) + 1
        po_text = ", ".join(f"{n} {status_words.word('po', st).lower()}" for st, n in sorted(counts.items()))
        ships = sorted({ship_for[f"PO {p.po_number}"] for p in pos if p.po_number and f"PO {p.po_number}" in ship_for})
        out.append({"id": j["ref"], "name": j["title"] or j["ref"], "group": j["stage"], "group_title": j["stage_word"], "pending": False, "staged_id": None, "job": True, "not_a_job_yet": False,
                    "on_monday": bool(lk), "link_state": lk["state"] if lk else "none", "monday_item_id": hit["id"] if hit else None, "monday_board_id": (lk or {}).get("board_id") or None,
                    "monday_item_name": (hit or {}).get("name") or None, "created_at": None,
                    "actions": ([{"label": "Link it?", "method": "POST", "path": f"/api/v1/board-edit/jobs/{j['ref']}/link", "body": {"item_id": hit["id"]}}] if lk and lk["state"] == "suggested" else
                                [{"label": "Unlink", "method": "POST", "path": f"/api/v1/board-edit/jobs/{j['ref']}/unlink", "body": {}}] if lk else []),
                    "values": {"ref": j["ref"], "title": j["title"], "stage": j["stage_word"], "customer": j.get("customer") or "", "owner": b.get("owner_name") or b.get("owner_email") or "",
                               "due": b.get("due_at") or "", "ball_with": (f"{b.get('ball_label')}: {b.get('ball_name')}" if b.get("ball_with") else ""), "po_status": po_text,
                               "shipment_status": ", ".join(ships), "next_action": (j.get("next_steps") or [{}])[0].get("text", ""), "on_monday": ("yes" if lk["state"] == "linked" else "maybe") if lk else "",
                               "monday_item_id": hit["id"] if hit else ""}})
    not_jobs: List[Dict[str, Any]] = []
    for it in monday:
        if it["id"] not in matched:
            not_jobs.append({"id": f"monday:{it['id']}", "name": it["name"], "group": NOT_A_JOB, "group_title": "Not a job yet", "pending": False, "staged_id": None, "job": False, "not_a_job_yet": True,
                        "on_monday": True, "monday_item_id": it["id"], "created_at": None,
                        "values": {"title": it["name"], "customer": it["customer"], "on_monday": "yes", "monday_item_id": it["id"]},
                        "actions": [{"label": "Make a job", "method": "POST", "path": f"/api/v1/board-edit/jobs/from-monday/{it['id']}", "body": {}}]})
    return out, not_jobs


def rows(limit: int = 60, offset: int = 0) -> List[Dict[str, Any]]:
    return _rows(limit, offset)


def board_items(limit: int = 60, offset: int = 0) -> Dict[str, Any]:
    """The Jobs board as ONE list for paging: the jobs (newest first), then the monday items that are not jobs yet. `offset` and `limit` slice that whole list, so no row appears on two pages;
    `total_rows` counts both kinds and `more` says there is another page."""
    from app.core.database import SessionLocal
    from app.models.db_models import JobDB

    limit, offset = max(1, min(int(limit), 200)), max(0, min(int(offset), 10**9))
    with SessionLocal() as db:
        total = db.query(JobDB).count()
    job_rows, monday_rows = _split(limit, offset)
    rs = list(job_rows)
    if len(rs) < limit:                                                              # this page runs past the jobs: it continues into the monday items, from where the last page stopped
        start = max(0, offset - total)
        rs += monday_rows[start:start + (limit - len(rs))]
    total_rows = total + len(monday_rows)
    cols = columns()
    for r in rs:
        r["cells"] = {c["id"]: {"title": c["title"], "type": c["type"], "editable": bool(c["editable"] and r["job"]), "value": r["values"].get(c["id"], ""),
                                **({"labels": c["labels"]} if c.get("labels") else {})} for c in cols}
    empty = [c["id"] for c in cols if rs and all(not str(r["values"].get(c["id"], "") or "").strip() for r in rs)]
    return {"board_id": BOARD_ID, "name": "Jobs", "role": "jobs", "editable": True, "read_at": None, "last_read": {"at": None, "minutes_ago": None, "text": "Live"}, "virtual": True, "columns": cols, "groups": _groups(), "items": rs, "pending_rows": 0, "empty_columns": empty, "total_jobs": total, "total_rows": total_rows, "offset": offset, "more": offset + limit < total_rows}


def edit_cell(item_id: str, column: str, text: str, user: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    """A cell of the Jobs board is a field of the job: records.patch decides (its rules, locks, reasons, audit and undo)."""
    if str(item_id).startswith("monday:"):
        raise ValueError("This is a monday item that is not a job yet. Make the job first (Make a job), then edit it.")
    col = next((c for c in columns() if c["id"] == column or c["title"].casefold() == str(column).casefold()), None)
    if col is None:
        raise ValueError(f"There is no column '{str(column)[:30]}' on the Jobs board.")
    if not col["editable"]:
        raise ValueError(f"{col['title']} is worked out from the job and can't be changed here.")
    out = records.patch("job", str(item_id), _RECORD_FIELD[col["id"]], text, user, today)
    return {"staged": False, "saved": True, "pending": False, "pending_row": False, "board_id": BOARD_ID, "item_id": str(item_id), "item": str(item_id), "column": col["title"], "column_id": col["id"],
            "type": col["type"], "understood": out["understood"], "was": out["was"], "becomes": out["becomes"], "message": f"{out['understood']} (saved on the job)"}


def create_jobs_from_monday(ids: Any, user: Dict[str, Any]) -> Dict[str, Any]:
    """Make jobs from several monday items at once ("make jobs from the selected rows"). Each item is once-only on its own, so a repeat or an item that already has a job comes back as already.
    One item failing never stops the others; each answer is its own."""
    if isinstance(ids, str):
        ids = [x.strip() for x in ids.replace("\n", ",").split(",") if x.strip()]
    if not isinstance(ids, list) or not ids or len(ids) > 100 or not all(isinstance(i, (str, int)) and not isinstance(i, bool) for i in ids):
        raise ValueError("Give the monday items to make jobs from as a list of their ids (at most 100).")
    results: List[Dict[str, Any]] = []
    for raw in dict.fromkeys(str(i).removeprefix("monday:") for i in ids):
        try:
            made = create_job_from_monday(raw, user)
            results.append({"id": raw, "ok": True, "ref": made["ref"], "already": bool(made.get("already")), "linked": bool(made.get("linked")), "message": made["message"]})
        except (LookupError, ValueError) as exc:
            results.append({"id": raw, "ok": False, "ref": None, "already": False, "linked": False, "message": str(exc).strip("'\"")})
    made_n = sum(1 for r in results if r["ok"] and not r["already"])
    return {"results": results, "made": made_n, "already": sum(1 for r in results if r["ok"] and r["already"]), "failed": sum(1 for r in results if not r["ok"]),
            "message": f"{made_n} job(s) made" + (f", {sum(1 for r in results if r['ok'] and r['already'])} already had one" if any(r["ok"] and r["already"] for r in results) else "")
            + (f", {sum(1 for r in results if not r['ok'])} could not be made" if any(not r["ok"] for r in results) else "") + "."}


def create_job_from_monday(item_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """The one-click "Make a job" for a projects-board item that has none: the job takes the item's name (and customer if the board has such a column), so the row then matches it."""
    from app.services import create_anything
    from app.services.coordination import jobs

    with _make_lock:                                                                   # once only on the monday item itself, whoever clicks and whenever
        items = _projects_items()
        it = next((i for i in items if i["id"] == str(item_id)), None)
        if it is None:
            raise LookupError("That monday item is not on the projects board as the app last read it.")
        listed = jobs.list_jobs("", "", "", 500, 0)["items"]
        have = next((ref for ref, l in link_for(listed, items).items() if l["item_id"] == it["id"] and (l["board_id"] or projects_board_id()) == projects_board_id()), None)
        if have:
            j = next(x for x in listed if x["ref"] == have)
            return {"kind": "job", "ref": have, "id": j["id"], "title": f"{have} {j['title']}".strip(), "link": f"/jobs/{have}", "monday_item_id": it["id"], "already": True,
                    "message": f"'{it['name']}' is already job {have}."}
        made = create_anything.create("job", {"customer": it["customer"] or "(not set)", "title": it["name"], "subject": it["name"], "client_key": f"from-monday-{item_id}-{uuid.uuid4().hex[:6]}"}, user)
        linked = False
        try:
            records.patch("job", made["ref"], "monday_item_id", it["id"], user)                 # the link is saved (and audited, undoable) when the job is made from the item
            linked = True
        except (ValueError, LookupError):
            pass
        return {**made, "monday_item_id": it["id"], "linked": linked, "already": False, "message": f"Job {made['ref']} made from the monday item '{it['name']}'."}


def link_of(job: Dict[str, Any]) -> Dict[str, Any]:
    """One job's link state without reading the whole board: {state: linked|suggested|none, monday_item_id, monday_board_id, monday_item_name, actions}."""
    ref = job["ref"]
    items = _projects_items()
    stored = stored_links().get(ref)
    if stored:
        it = next((i for i in items if i["id"] == stored["item_id"]), None)
        return {"state": "linked", "monday_item_id": stored["item_id"], "monday_board_id": stored["board_id"], "monday_item_name": (it or {}).get("name"),
                "actions": [{"label": "Unlink", "method": "POST", "path": f"/api/v1/board-edit/jobs/{ref}/unlink", "body": {}}]}
    taken = {l["item_id"] for l in stored_links().values() if l["board_id"] == projects_board_id()}
    sug = _assign([job], [i for i in items if i["id"] not in taken]).get(ref)
    if sug:
        return {"state": "suggested", "monday_item_id": sug["id"], "monday_board_id": projects_board_id(), "monday_item_name": sug["name"],
                "actions": [{"label": "Link it?", "method": "POST", "path": f"/api/v1/board-edit/jobs/{ref}/link", "body": {"item_id": sug["id"]}}]}
    return {"state": "none", "monday_item_id": None, "monday_board_id": None, "monday_item_name": None, "actions": []}
