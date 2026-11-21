"""The book: one table of rows for Orders (RFQs, sales orders and purchase orders) or Jobs, each cell with its type and whether it can be edited in place.

The row's `kind` and `ref` are what the record routes take: PATCH /records/{kind}/{ref} changes a cell, GET /records/{kind}/{ref}/fields lists the rest.
Reading only; nothing here changes anything.
"""

from datetime import date as _date
from typing import Any, Dict, List, Optional

AREAS = ("orders", "jobs")
KINDS = ("rfq", "so", "po", "all")

# (field, label, type, editable). "editable" is for the column; a row can still be locked (a sent purchase order).
_COLUMNS: Dict[str, List[tuple]] = {
    "rfq": [("number", "RFQ", "text", False), ("customer", "Customer", "text", True), ("subject", "Subject", "text", False), ("status", "Status", "text", False),
            ("owner_email", "Owner", "email", True), ("complexity", "Complexity", "choice", True)],
    "so": [("sales_order_number", "Sales order", "text", False), ("customer_name", "Customer", "text", False), ("customer_po_number", "Customer's PO", "text", False),
           ("status_word", "Status", "text", False), ("total_revenue", "Total", "number", False), ("internal_notes", "Notes", "long_text", True)],
    "po": [("po_number", "PO", "text", False), ("vendor", "Vendor", "text", False), ("status", "Status", "text", False), ("job", "Job", "text", False),
           ("promised_on", "Promised", "date", False)],
    "job": [("ref", "Job", "text", False), ("title", "Title", "text", True), ("customer", "Customer", "text", True), ("stage_word", "Stage", "text", False)],
}
_RECORD_KIND = {"rfq": "rfq", "so": "order", "po": "po", "job": "job"}
_LINK = {"rfq": "/orders?kind=rfq&ref={ref}", "so": "/orders?kind=so&ref={ref}", "po": "/orders?kind=po&ref={ref}", "job": "/jobs/{ref}"}
_CHOICES = {"complexity": ["SIMPLE", "STANDARD", "COMPLEX"]}


def _columns(kind: str) -> List[Dict[str, Any]]:
    return [{"field": f, "label": lab, "type": t, "editable": e, **({"choices": _CHOICES[f]} if f in _CHOICES else {})} for f, lab, t, e in _COLUMNS[kind]]


_ORDER_ERRORS = ("VALIDATION_ERROR", "SAGE_ERROR")


_EMPTY: Dict[str, Any] = {"rfq": {}, "so": {}, "ref": {}, "id": {}}


def _needs_me(kind: str, data: Dict[str, Any], me: str) -> bool:
    """Whether this row is waiting on the person asking: it is theirs (the RFQ owner, the person who has a job's ball) or it is an order of theirs that needs fixing."""
    me = (me or "").strip().casefold()
    if not me:
        return False
    if kind == "rfq":
        return str(data.get("owner_email") or "").casefold() == me and str(data.get("status") or "").upper() not in ("CLOSED", "WON", "LOST")
    if kind == "job":
        b = data.get("ball") or {}
        return me in (str(b.get("ball_person") or "").casefold(), str(b.get("owner_email") or "").casefold()) and data.get("stage") != "CLOSED"
    if kind == "so":
        return data.get("status") in _ORDER_ERRORS and str(data.get("created_by") or "").casefold() in (me, "")
    return False


def _plain_text(kind: str, field: str, ctype: str, v: Any) -> str:
    """The value as a person reads it: a status as its plain word, a date as "Fri 20 Nov", money as $1,234.50; anything else as it is."""

    from app.core import status_words

    if v in (None, ""):
        return ""
    if field == "status" and kind in ("rfq", "po"):
        return status_words.word(kind, str(v)) or str(v)
    if ctype == "date" or field in ("promised_on", "due"):
        try:
            d = _date.fromisoformat(str(v)[:10])
            return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}" + (f" {d.year}" if d.year != _date.today().year else "")
        except ValueError:
            return str(v)
    if field in ("total_revenue", "total"):
        try:
            return f"${float(v):,.2f}"
        except (TypeError, ValueError):
            return str(v)
    return str(v)


def _job_of(kind: str, data: Dict[str, Any], links: Dict[str, Any]) -> Dict[str, Any]:
    """The job this row belongs to: an RFQ by the job made from it, a PO by its job, a sales order by the job that made it, a job row is itself. Both null when there is none."""
    link = None
    if kind == "job":
        link = links["ref"].get(data.get("ref"))
    elif kind == "rfq":
        link = links["rfq"].get(data.get("id"))
    elif kind == "so":
        link = links["so"].get(str(data.get("sales_order_number") or "").removeprefix("SO-"))
    elif kind == "po":
        link = links["ref"].get(data.get("job")) or links["id"].get(data.get("job_id"))
    return {"job_id": link[0] if link else None, "job_ref": link[1] if link else None}


def _row(kind: str, ref: str, data: Dict[str, Any], locked: bool = False, me: str = "", links: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cells = {}
    for f, _lab, t, e in _COLUMNS[kind]:
        v = data.get(f)
        cells[f] = {"value": "" if v is None else v, "text": _plain_text(kind, f, t, v), "type": t, "editable": bool(e and not locked)}
    extra: Dict[str, Any] = {}
    if kind == "so":                                                                  # an order's money carries its currency in words; a mismatch with where it ships is flagged, never fixed
        from app.services import currency_check

        cur = str(data.get("currency") or "").upper() or None
        cells["total_revenue"]["text"] = currency_check.money_text(data.get("total_revenue"), cur)
        flag, why = currency_check.check(data)
        extra = {"currency": cur, "amount_usd_approx": currency_check.usd_approx(data.get("total_revenue"), cur), "check_currency": flag, "check_currency_reason": why or None}
    return {"kind": kind, "record_kind": _RECORD_KIND[kind], "ref": str(ref), "link": _LINK[kind].format(ref=ref), "needs_me": _needs_me(kind, data, me), "late": _late(kind, data), **_job_of(kind, data, links or _EMPTY), **extra,
            "days_waiting": (data.get("ball") or {}).get("days_waiting") if kind == "job" else None, "cells": cells}


def _match(q: str, data: Dict[str, Any]) -> bool:
    return not q or q.casefold() in " ".join(str(v) for v in data.values() if v is not None).casefold()


def _sort_key(row: Dict[str, Any], field: str):
    v = (row["cells"].get(field) or {}).get("value")
    try:
        return (0, float(str(v).replace(",", "").replace("$", "")), "")
    except (TypeError, ValueError):
        return (1, 0.0, str(v if v is not None else "").casefold())


LATE_DAYS = 3


def _late(kind: str, data: Dict[str, Any]) -> bool:
    """A job whose ball has been with someone more than three days (and which is not closed)."""
    b = data.get("ball") or {}
    return kind == "job" and not b.get("resolved") and (b.get("days_waiting") or 0) > LATE_DAYS and data.get("stage") != "CLOSED"


from app.services import monday_state  # noqa: E402


def rows(area: str, kind: str = "all", q: str = "", limit: int = 200, status: str = "", sort: str = "", desc: bool = False, mine: bool = False, me: str = "", late: bool = False) -> Dict[str, Any]:
    if area not in AREAS:
        raise ValueError(f"The area must be one of: {', '.join(AREAS)}.")
    if kind not in KINDS:
        raise ValueError(f"The kind must be one of: {', '.join(KINDS)}.")
    limit = max(1, min(int(limit), 500))
    q = " ".join(str(q or "").split())[:100]
    status = " ".join(str(status or "").split())[:40].casefold()
    out: List[Dict[str, Any]] = []
    from app.services import job_links

    links = job_links.maps()
    shown = ["job"] if area == "jobs" else (["rfq", "so", "po"] if kind == "all" else [kind])
    if "job" in shown:
        from app.services.coordination import jobs

        for j in jobs.list_jobs("", "", q, limit, 0)["items"]:
            out.append(_row("job", j["ref"], j, me=me, links=links))
    if "rfq" in shown:
        from app.services.coordination import rfqs

        for r in rfqs.list_rfqs(open_only=False, limit=5000 if q or status or mine else limit):
            if _match(q, r):
                out.append(_row("rfq", r["number"], r, locked=r.get("status") in ("CLOSED", "LOST", "WON"), me=me, links=links))
    if "so" in shown:
        from app.core.database import SessionLocal
        from app.services.database_service import DatabaseService

        with SessionLocal() as db:
            for o in DatabaseService.get_all_orders(db, limit=5000 if q or status or mine else limit):
                if _match(q, o):
                    out.append(_row("so", o["sales_order_number"], o, me=me, links=links))
    if "po" in shown:
        from app.services.coordination import purchase_orders

        for p in purchase_orders.unified(q=q, staff=True)[:limit]:
            ref = p.get("purchase_order_id") or p["po_number"]
            out.append(_row("po", ref, {**p, "po_number": p["po_number"]}, locked=True, me=me, links=links))      # the row's cells are read only; its record (PATCH /records/po/{ref}) has the editable fields
    look = monday_state.safe_lookup()                                                  # the "On monday / Waiting to send / Not on monday" chip, one pass for all rows
    for r in out:
        k = r["kind"]
        r["monday"] = (None if look is None else look.job(r["ref"]) if k == "job" else look.rfq(r["ref"]) if k == "rfq" else look.job(r["job_ref"]) if k == "po"
                       else look.order(r["ref"], str((r["cells"].get("customer_po_number") or {}).get("value") or ""), r["job_ref"]))
    if status:
        out = [r for r in out if status in str((r["cells"].get("status") or r["cells"].get("status_word") or r["cells"].get("stage_word") or {}).get("value", "")).casefold()]
    if mine:
        out = [r for r in out if r["needs_me"]]
    if late:
        out = [r for r in out if r["late"]]
    if sort:
        known = {f for k in dict.fromkeys(r["kind"] for r in out) for f, *_ in _COLUMNS[k]}
        if sort not in known:
            raise ValueError(f"Sort by one of: {', '.join(sorted(known))}.")
        out.sort(key=lambda r: _sort_key(r, sort), reverse=bool(desc))
    out = out[:limit] if kind != "all" or area == "jobs" else out[:limit * 3]
    return {"area": area, "kind": kind if area == "orders" else "job", "columns": {k: _columns(k) for k in dict.fromkeys(r["kind"] for r in out)} or {k: _columns(k) for k in shown},
            "rows": out, "count": len(out), "needs_me": sum(1 for r in out if r["needs_me"])}
