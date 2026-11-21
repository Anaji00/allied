"""A read-only report of orders that look like test orders or duplicates, so the owner can decide what to do. It never deletes, edits or marks anything.

A TEST order: its sales order number, customer PO number or customer name says test/demo/sample/dummy. A DUPLICATE group: two or more orders for the same customer with the same total, the same
lines (part and quantity) and either the same customer PO number or created within a few days of each other; the first one made is shown as the original."""

import re
from datetime import timedelta
from typing import Any, Dict, List

from app.core.database import SessionLocal
from app.models.db_models import OrderDB

_TEST = re.compile(r"(?<![a-z])(test|demo|sample|dummy|fake)(?![a-z])", re.I)
DUPLICATE_DAYS = 7


def _lines(o: OrderDB) -> tuple:
    out = []
    for ln in (o.line_items or []):
        if isinstance(ln, dict):
            out.append((str(ln.get("item_id") or ln.get("raw_item_text") or ln.get("description") or "").strip().casefold(), str(ln.get("quantity") or ln.get("qty") or "")))
    return tuple(sorted(out))


def _row(o: OrderDB) -> Dict[str, Any]:
    return {"sales_order_number": o.sales_order_number, "customer": o.customer_name, "customer_po": o.customer_po_number, "total": str(o.total_revenue) if o.total_revenue is not None else None,
            "currency": o.currency, "status": o.status, "created_at": o.created_at.isoformat() if o.created_at else None, "sent_to_sage": bool(o.sage_exported_at)}


def suspected(limit: int = 2000) -> Dict[str, Any]:
    with SessionLocal() as db:
        orders = db.query(OrderDB).order_by(OrderDB.created_at).limit(limit).all()
    tests: List[Dict[str, Any]] = []
    for o in orders:
        why = [f"{label} says '{m.group(1)}'" for label, text in (("the sales order number", o.sales_order_number), ("the customer PO number", o.customer_po_number), ("the customer name", o.customer_name))
               for m in [_TEST.search(str(text or ""))] if m]
        if why:
            tests.append({**_row(o), "reason": "Looks like a test order: " + "; ".join(why) + "."})
    test_ids = {t["sales_order_number"] for t in tests}
    groups: Dict[tuple, List[OrderDB]] = {}
    for o in orders:
        if o.sales_order_number in test_ids or o.total_revenue is None:
            continue
        groups.setdefault((str(o.customer_name or "").casefold(), str(o.total_revenue), _lines(o)), []).append(o)
    dupes: List[Dict[str, Any]] = []
    for (_c, total, _l), rows in groups.items():
        if len(rows) < 2:
            continue
        first = rows[0]
        twins = [o for o in rows[1:] if (o.customer_po_number and o.customer_po_number == first.customer_po_number)
                 or (o.created_at and first.created_at and abs(o.created_at - first.created_at) <= timedelta(days=DUPLICATE_DAYS))]
        if twins:
            same_po = all(o.customer_po_number == first.customer_po_number for o in twins)
            dupes.append({"original": _row(first), "duplicates": [_row(o) for o in twins],
                          "reason": f"{len(twins) + 1} orders for {first.customer_name} with the same total ({total}) and the same lines, " + ("and the same customer PO number" if same_po else f"made within {DUPLICATE_DAYS} days of each other") + ". The first one made is shown as the original."})
    text = (f"{len(tests)} order(s) look like tests and {len(dupes)} group(s) of orders look like duplicates. Nothing was changed; this is a list for a person to decide from.")
    return {"test_orders": tests, "duplicate_groups": dupes, "text": text, "changed_anything": False}
