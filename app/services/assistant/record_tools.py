"""Copilot tools for the "add anything" and "edit anything" work: each one is proposed as a card the person confirms (nothing happens before the click),
and anything for monday is only ever STAGED (a manager pushes). The words people say are tidied here ("quote request" is an RFQ, "part" is a product), so a
spoken request and a typed one end in the same card."""

from typing import Any, Dict, List

from app.services.assistant.tools import Tool, _s

_KIND_WORDS = {"job": "job", "jobs": "job", "rfq": "rfq", "quote": "rfq", "quote request": "rfq", "request for quote": "rfq", "rfq": "rfq",
               "product": "product", "part": "product", "item": "product", "order": "order", "sales order": "order", "so": "order", "customer order": "order",
               "po": "po", "purchase order": "po", "vendor order": "po", "shipment": "shipment", "container": "shipment", "board row": "board_row", "row": "board_row",
               "board_row": "board_row"}
_RECORD_WORDS = {**_KIND_WORDS, "sales order": "order", "purchase order": "po"}


def _kind(text: Any, words: Dict[str, str]) -> str:
    said = " ".join(str(text or "").casefold().replace("_", " ").split())
    if said in words:
        return words[said]
    if said.endswith("s") and said[:-1] in words:
        return words[said[:-1]]
    raise ValueError("I can add: a job, an RFQ (quote request), a part (product), an order, a purchase order, a shipment or a board row. Which one?")


def _fields(a: Dict[str, Any]) -> Dict[str, Any]:
    f = a.get("fields")
    if isinstance(f, dict):
        return f
    if f in (None, ""):
        return {}
    raise ValueError("Give the facts as {name: value}.")


def _board_for(a: Dict[str, Any]) -> None:
    if a.get("board"):
        from app.services.assistant.board_tools import resolve_board

        a["board"] = resolve_board(str(a["board"]))


def _check_add(a: Dict[str, Any], user: Dict[str, Any]) -> None:
    a["kind"] = _kind(a.get("kind"), _KIND_WORDS)
    f = _fields(a)
    need = {"rfq": ["customer"], "job": ["customer"], "product": ["item_id"], "order": ["customer", "customer_po_number", "shipping_address", "items"], "po": ["job", "vendor"],
            "shipment": [], "board_row": ["board_id", "name"]}[a["kind"]]
    missing = [k for k in need if not f.get(k) and not (k == "board_id" and a.get("board")) and not (k == "item_id" and (f.get("part_number") or f.get("name")))]
    if missing:
        raise ValueError(f"To add a {a['kind'].replace('_', ' ')} I still need: {', '.join(missing)}. Ask the user.")
    if a["kind"] == "shipment" and not any(f.get(k) for k in ("reference", "container_no", "awb", "forwarder", "carrier")):
        raise ValueError("To add a shipment I need a reference, container number, air waybill or forwarder.")
    _board_for(a)
    if a["kind"] == "board_row" and a.get("board"):
        a["fields"] = {**f, "board_id": a["board"]}


def _summary_add(a: Dict[str, Any]) -> str:
    f = a.get("fields") if isinstance(a.get("fields"), dict) else {}
    lines = "\n".join(f"{k.replace('_', ' ').capitalize()}: {str(v)[:120]}" for k, v in f.items() if v not in (None, "", [], {}))
    where = ""
    if a.get("board") and a.get("kind") != "board_row":
        try:
            from app.services import create_anything

            m = create_anything.predict_monday(a["kind"], f, a["board"], a.get("group"))
            where = (f"\nAlso staged on monday (a manager pushes it): board {m['board']}" + (f", group {m['group']}" if m["group"] else "") +
                     "\n  Columns filled: " + ("; ".join(m["fills"]) or "none") + ("\n  Left out (no column of that name): " + ", ".join(m["left_out"]) if m["left_out"] else ""))
        except (ValueError, LookupError) as exc:
            where = f"\n(the monday step would fail: {exc})"
    return f"Add a {str(a.get('kind') or '').replace('_', ' ')}\n{lines}{where}"


def add_anything(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import create_anything

    fields = dict(_fields(args))
    to_monday = {"board_id": str(args["board"]), **({"group": str(args["group"])} if args.get("group") else {})} if args.get("board") and args["kind"] != "board_row" else None
    made = create_anything.create(args["kind"], fields, user, to_monday)
    out = {"done": made["message"], "link": made["link"], "ref": made["ref"]}
    if made.get("monday"):
        out["monday"] = made["monday"]
    return out


def _check_call_note(a: Dict[str, Any], user: Dict[str, Any]) -> None:
    from app.services.coordination import jobs

    a["job"] = jobs.get(str(a.get("job") or ""), staff=True)["ref"]
    if not str(a.get("note") or "").strip():
        raise ValueError("The note is empty.")


def add_call_note(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.coordination import jobs, updates

    j = jobs.get(str(args["job"]), staff=True)
    if not j.get("rfq"):
        raise ValueError(f"{j['ref']} has no comment thread to put the note in.")
    posted = updates.post("rfq", j["rfq"]["number"], str(args["note"]), user)
    return {"done": f"Call note posted on {j['ref']}.", "comment_id": posted.get("id")}


def _check_edit(a: Dict[str, Any], user: Dict[str, Any]) -> None:
    from app.services import records

    a["kind"] = _kind(a.get("kind"), _RECORD_WORDS)
    if a["kind"] == "board_row":
        raise ValueError("To change a board row use stage_cell_edit.")
    f = records._field(a["kind"], str(a.get("field") or ""))
    got = records.understand(f, str(a.get("text") or ""))                  # a bad value is refused now, so the model can ask, not propose it
    records._read(a["kind"], str(a.get("ref") or ""))
    from app.services import records as _r

    _r.check_role(a["kind"], f["field"], user)
    a["field"], a["_understood"] = f["field"], got["understood"]
    a["text"] = str(got["value"])                    # what was read when the card was made is what is saved: "tomorrow" is a date now, not whenever it is confirmed


def edit_record(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import records

    out = records.patch(args["kind"], str(args["ref"]), str(args["field"]), str(args["text"]), user)
    return {"done": out["message"]}


def _check_push(a: Dict[str, Any], user: Dict[str, Any]) -> None:
    from app.services import actions
    from app.services.assistant.board_tools import resolve_board

    a["kind"] = _kind(a.get("kind"), {**_RECORD_WORDS, "rfq": "rfq"})
    if a["kind"] not in ("job", "rfq", "po", "shipment", "order", "product"):
        raise ValueError("That kind can't be put on a board.")
    a["board"] = resolve_board(str(a.get("board") or ""))
    actions.context(a["kind"], str(a.get("ref") or ""))
    from app.services import create_anything

    a["_plan"] = create_anything.to_monday_stage(a["kind"], str(a["ref"]), a["board"], str(a.get("group") or "") or None, user, dry_run=True)       # the card shows exactly this


def put_on_monday(args: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import create_anything

    out = create_anything.to_monday_stage(args["kind"], str(args["ref"]), args["board"], str(args.get("group") or "") or None, user)
    return {"done": out["message"], "change_id": out["change_id"]}


def _summary_push(a: Dict[str, Any]) -> str:
    m = a.get("_plan") or {}
    cols = "; ".join(f"{c['fact']} -> {c['column']}" for c in m.get("columns", [])) or "none"
    left = ", ".join(x["fact"] for x in m.get("left_out", []))
    return (f"Put {a.get('kind')} {a.get('ref')} on monday as a new row (staged; a manager pushes it)\nBoard: {m.get('board') or a.get('board')}" + (f"\nGroup: {m['group']}" if m.get("group") else "")
            + f"\nColumns filled: {cols}" + (f"\nLeft out: {left}" if left else ""))


def compare_drawings(args: Dict[str, Any], user: Dict[str, Any]) -> str:
    """Compares two attached drawings and says what changed. Reads only: nothing is posted or sent. With a job and no file numbers, the job's two newest files."""
    from app.services import drawing_files

    a, b = args.get("file_a"), args.get("file_b")
    if a in (None, "") or b in (None, ""):
        if not args.get("job"):
            raise ValueError("Say which job (or two file numbers) to compare.")
        two = drawing_files.latest_two(str(args["job"]))
        if len(two) < 2:
            raise ValueError(f"{args['job']} has fewer than two drawings attached, so there is nothing to compare.")
        a, b = two[0]["id"], two[1]["id"]
    out = drawing_files.compare(int(a), int(b), int(args.get("page_a") or 1), int(args.get("page_b") or 1))
    return drawing_files.summary(out) + "\n(The red/blue picture is on the Drawings screen: files " + f"{out['file_a']['id']} and {out['file_b']['id']}.)"


TOOLS: List[Tool] = [
    Tool("add_call_note", "Post a reviewed call summary as a comment on a job. Only ever proposed by the call screen after a person has looked at it.",
         {"job": _s("Job reference"), "note": _s("The note")}, add_call_note, write=True, min_role="OPERATOR", required=["job", "note"], offered=False, check=_check_call_note,
         summarize=lambda a: f"Post this call note on {a.get('job')}:\n{a.get('note')}"),
    Tool("compare_drawings", "Compare two drawings attached to a job (by default its two newest) and say what changed: verdict, how much differs, warnings (units, revision, projection) "
         "and the text that differs. Reads only. Use for 'compare the two latest drawings on JOB-...'.",
         {"job": _s("Job reference"), "file_a": {"type": "integer", "description": "First file number (optional)"}, "file_b": {"type": "integer", "description": "Second file number (optional)"}},
         compare_drawings, min_role="OPERATOR"),
    Tool("add_anything", "Add a job, an RFQ (quote request), a part (product), an order, a purchase order, a shipment or a board row from what the user said, spoken or typed. "
         "Put the facts in `fields` ({customer, subject, items, ...}; for an order: customer, customer_po_number, shipping_address, items:[{part, quantity}]; for a PO: job, vendor, items; "
         "for a part: item_id, description, base_price). Never invent a fact the user did not give: ask for what is missing. To also put it on a monday board give `board` (it is only "
         "staged for a manager to push).",
         {"kind": _s("job, rfq, product, order, po, shipment or board_row (a quote request is an rfq, a part is a product)"),
          "fields": {"type": "object", "description": "The facts, as said"}, "board": _s("A monday board to stage it on too (optional)"), "group": _s("The board group (optional)")},
         add_anything, write=True, min_role="OPERATOR", required=["kind", "fields"], check=_check_add, summarize=_summary_add),
    Tool("edit_record", "Change one field of a job, RFQ, order, purchase order, shipment or part from words ('move the ETA to Friday'). The value is read like typing it in the box.",
         {"kind": _s("job, rfq, order, po, shipment, product, customer or vendor"), "ref": _s("Its reference (JOB-..., RFQ-..., sales order number, PO id or number, shipment id, part number)"),
          "field": _s("The field, like eta_current, terms, contact_email"), "text": _s("What to put there, as typed")},
         edit_record, write=True, min_role="OPERATOR", required=["kind", "ref", "field", "text"], check=_check_edit,
         summarize=lambda a: f"Change {a.get('kind')} {a.get('ref')}\n{a.get('_understood') or str(a.get('field')) + ': ' + str(a.get('text'))}"),
    Tool("put_on_monday", "Stage a job, RFQ, PO, shipment or order as a new row on a monday board. Only STAGED: a manager pushes it, nothing is sent now.",
         {"kind": _s("job, rfq, po, shipment or order"), "ref": _s("Its reference"), "board": _s("Board name"), "group": _s("Group name (optional)")},
         put_on_monday, write=True, min_role="OPERATOR", required=["kind", "ref", "board"], check=_check_push,
         summarize=lambda a: _summary_push(a)),
]
