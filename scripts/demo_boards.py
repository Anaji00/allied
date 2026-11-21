"""Sample monday boards for the DEMO only: three believable boards put into the app's board copy so the editable grid has something to show when there is no
monday connection. Used only by scripts/demo_run.py, after it has checked the database is a demo one; real runs never import this.

Nothing here talks to monday. The boards live in the app's in-memory board copy (the same place the real boards are read into), so editing them stages
changes in monday_changes like any board, and a push says that nothing was sent (monday is off in the demo and MONDAY_READ_ONLY stays on).
"""
from typing import Any, Dict, List

BOARD_IDS = ("9000000001", "9000000002", "9000000003")


def _cols(*items) -> List[Dict[str, Any]]:
    return [{"id": i, "title": t, "type": ty, **({"labels": lab} if lab else {})} for i, t, ty, lab in items]


def _item(i: int, name: str, group: str, titles: Dict[str, str], **values) -> Dict[str, Any]:
    return {"id": str(i), "name": name, "group": group, "group_title": titles[group], "created_at": "2026-11-01T09:00:00+00:00", "values": {k: v for k, v in values.items()}}


def sample_boards(read_at: str) -> Dict[str, Dict[str, Any]]:
    eng_groups = [{"id": "g_week", "title": "This week", "color": "#579bfc"}, {"id": "g_next", "title": "Next week", "color": "#a25ddc"}, {"id": "g_done", "title": "Done", "color": "#00c875"}]
    et = {g["id"]: g["title"] for g in eng_groups}
    eng = {"board_id": BOARD_IDS[0], "name": "Engineering Team Board (sample)", "role": "reference", "read_at": read_at, "groups": eng_groups,
           "columns": _cols(("owner", "Owner", "people", None), ("state", "Status", "status", {"Need to Scan": "#fdab3d", "Scanned": "#579bfc", "Drafting": "#a25ddc", "In review": "#ffcb00", "Done": "#00c875", "Stuck": "#e2445c"}),
                            ("scan", "Scan Date", "date", None), ("due", "Due", "date", None), ("hours", "Hours", "numbers", None), ("prio", "Priority", "dropdown", None),
                            ("checked", "Checked", "checkbox", None), ("notes", "Notes", "text", None)),
           "items": [_item(9001, "Hecla spool 109", "g_week", et, owner="Paul Wang", state="Need to Scan", scan="", due="2026-11-24", hours="6", prio="High", checked="", notes="Customer drawing is a photo"),
                     _item(9002, "Nibrock flange adapter", "g_week", et, owner="Kim Roe", state="Drafting", scan="2026-11-12", due="2026-11-21", hours="12", prio="Medium", checked="", notes=""),
                     _item(9003, "OC262064 flex hose", "g_week", et, owner="Paul Wang", state="In review", scan="2026-11-10", due="2026-11-19", hours="8", prio="High", checked="v", notes="Waiting on revision B"),
                     _item(9004, "Maple bracket set", "g_next", et, owner="", state="Need to Scan", scan="", due="2026-12-02", hours="3", prio="Low", checked="", notes=""),
                     _item(9005, "Starr tank vent", "g_next", et, owner="Kim Roe", state="Scanned", scan="2026-11-14", due="2026-12-04", hours="5", prio="Medium", checked="", notes=""),
                     _item(9006, "Zibo ceramic liner", "g_done", et, owner="Paul Wang", state="Done", scan="2026-10-28", due="2026-11-05", hours="9", prio="Medium", checked="v", notes="Sent to vendor"),
                     _item(9007, "Weld cap 3in", "g_done", et, owner="Kim Roe", state="Done", scan="2026-10-30", due="2026-11-06", hours="2", prio="Low", checked="v", notes=""),
                     _item(9008, "Old fixture rework", "g_week", et, owner="Paul Wang", state="Stuck", scan="2026-11-03", due="2026-11-15", hours="4", prio="High", checked="", notes="Needs customer call")]}
    rfq_groups = [{"id": "r_new", "title": "New requests", "color": "#579bfc"}, {"id": "r_quoted", "title": "Quoted", "color": "#00c875"}, {"id": "r_lost", "title": "Lost", "color": "#e2445c"}]
    rt = {g["id"]: g["title"] for g in rfq_groups}
    rfq = {"board_id": BOARD_IDS[1], "name": "Open RFQs (sample)", "role": "reference", "read_at": read_at, "groups": rfq_groups,
           "columns": _cols(("customer", "Customer", "text", None), ("contact", "Contact", "email", None), ("state", "Status", "status", {"New": "#579bfc", "Pricing": "#ffcb00", "Quoted": "#00c875", "Lost": "#e2445c"}),
                            ("received", "Received", "date", None), ("qty", "Qty", "numbers", None), ("value", "Est. value", "numbers", None), ("site", "Drawing link", "link", None), ("notes", "Notes", "long_text", None)),
           "items": [_item(9101, "Flex hose for forming line", "r_new", rt, customer="Owens Corning Starr Composites", contact="buyer@owens.example.test", state="New", received="2026-11-16", qty="2", value="5000", notes=""),
                     _item(9102, "Ceramic liner, 12 pcs", "r_new", rt, customer="Nibrock Inc", contact="ap@nibrock.example.test", state="Pricing", received="2026-11-12", qty="12", value="18000", notes="Asked Zibo for a price"),
                     _item(9103, "Bracket set", "r_quoted", rt, customer="Maple Fab Ltd", contact="", state="Quoted", received="2026-11-03", qty="40", value="1600", notes=""),
                     _item(9104, "Tank vent", "r_lost", rt, customer="Hecla Mining", contact="", state="Lost", received="2026-10-20", qty="1", value="900", notes="Went with a local shop")]}
    ord_groups = [{"id": "o_open", "title": "Open", "color": "#579bfc"}, {"id": "o_shipped", "title": "Shipped", "color": "#00c875"}]
    ot = {g["id"]: g["title"] for g in ord_groups}
    orders = {"board_id": BOARD_IDS[2], "name": "2026 Orders (sample)", "role": "reference", "read_at": read_at, "groups": ord_groups,
              "columns": _cols(("customer", "Customer", "text", None), ("po", "Customer PO", "text", None), ("state", "Status", "status", {"Open": "#579bfc", "In production": "#ffcb00", "Shipped": "#00c875", "Invoiced": "#a25ddc"}),
                               ("ship", "Ship date", "date", None), ("value", "Order value", "numbers", None), ("owner", "Owner", "people", None), ("notes", "Notes", "text", None)),
              "items": [_item(9201, "OC order 12931", "o_open", ot, customer="Owens Corning Starr Composites", po="4500865993", state="In production", ship="2026-12-20", value="2770", owner="Kim Roe", notes=""),
                        _item(9202, "Nibrock order 12940", "o_open", ot, customer="Nibrock Inc", po="NB-2288", state="Open", ship="2027-01-15", value="18000", owner="Paul Wang", notes="Over the PO limit"),
                        _item(9203, "Maple order 12902", "o_shipped", ot, customer="Maple Fab Ltd", po="MF-77", state="Shipped", ship="2026-11-08", value="1600", owner="Kim Roe", notes="")]}
    return {b["board_id"]: b for b in (eng, rfq, orders)}


def install(boards_module: Any, now_iso: str) -> int:
    """Put the sample boards into the app's board copy. Only the demo runner calls this."""
    made = sample_boards(now_iso)
    with boards_module._lock:
        for bid, board in made.items():
            boards_module._mirror[bid] = board
    return len(made)
