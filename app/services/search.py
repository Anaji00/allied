"""One search box for everything: orders, RFQs, vendor jobs, shipments, monday board rows, comments and people.

monday's own complaint list includes "too many boards, hard to find anything": here a single search looks across all of them
(the app's own copy, so it is instant and reads nothing from monday). Results are grouped by kind, each with where to open it.
Matching ignores case (and accents for names); the search words must all appear in the item (any order).
"""

import re
from typing import Any, Dict, List

from sqlalchemy import func, or_

from app.core.database import SessionLocal
from app.models.db_models import ItemFileDB, ItemUpdateDB, JobDB, OrderDB, RfqDB, ShipmentDB, VendorJobDB

PER_KIND = 8


def _terms(q: str) -> List[str]:
    return [t for t in re.split(r"\s+", (q or "").strip().casefold()) if t][:6]


def _like(col: Any, term: str):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return func.lower(func.coalesce(col, "")).like(f"%{escaped}%", escape="\\")


def _all_terms(cols: List[Any], terms: List[str]):
    """Every word must appear in at least one of the columns."""
    return [or_(*[_like(c, t) for c in cols]) for t in terms]


def item_link(item_type: str, ref: str) -> str:
    """The screen address of one record, so a result opens the record itself and not just the list it lives in."""
    from urllib.parse import quote

    r = quote(str(ref), safe="")
    return {"order": f"/orders?order={r}", "rfq": f"/rfqs?id={r}", "vendor_job": f"/vendors?job={r}", "shipment": f"/shipments?id={r}",
            "milestone": f"/projects?milestone={r}", "job": f"/jobs/{r}"}.get(item_type, "")


def _hit(kind: str, title: str, sub: str, ref: str, link: str) -> Dict[str, Any]:
    return {"type": kind, "title": title[:140], "sub": sub[:160], "ref": ref, "link": link}


def search(q: str, limit: int = PER_KIND) -> Dict[str, Any]:
    terms = _terms(q)
    if not terms or sum(len(t) for t in terms) < 2:
        return {"query": q, "total": 0, "groups": []}
    groups: List[Dict[str, Any]] = []
    with SessionLocal() as db:
        rows = db.query(JobDB).filter(*_all_terms([JobDB.ref, JobDB.title, JobDB.customer], terms)).order_by(JobDB.id.desc()).limit(limit).all()
        groups.append({"type": "job", "label": "Jobs", "hits": [        # a job's own sales order number is internal and is not searched or shown here
            _hit("job", f"{j.ref} · {j.title or j.customer or ''}", f"{j.customer or 'no customer'}", j.ref, item_link("job", j.ref)) for j in rows]})
        rows = db.query(OrderDB).filter(*_all_terms([OrderDB.customer_po_number, OrderDB.sales_order_number, OrderDB.customer_name,
                                                      OrderDB.shipping_address, OrderDB.internal_notes], terms)).order_by(OrderDB.created_at.desc()).limit(limit).all()
        groups.append({"type": "order", "label": "Orders", "hits": [
            _hit("order", f"PO {o.customer_po_number} · {o.customer_name}", f"{o.status} · {o.currency} {o.total_revenue}", o.sales_order_number, item_link("order", o.sales_order_number))
            for o in rows]})
        rows = db.query(RfqDB).filter(*_all_terms([RfqDB.number, RfqDB.customer, RfqDB.subject, RfqDB.summary, RfqDB.vendor, RfqDB.contact_email], terms)
                                      ).order_by(RfqDB.received_at.desc()).limit(limit).all()
        groups.append({"type": "rfq", "label": "RFQs", "hits": [
            _hit("rfq", f"{r.number} · {r.customer or r.subject}", f"{r.status} · {r.subject}", r.number, item_link("rfq", str(r.id))) for r in rows]})
        rows = db.query(VendorJobDB).filter(*_all_terms([VendorJobDB.vendor, VendorJobDB.po_number, VendorJobDB.description, VendorJobDB.status], terms)
                                            ).order_by(VendorJobDB.id.desc()).limit(limit).all()
        groups.append({"type": "vendor_job", "label": "Vendor jobs", "hits": [
            _hit("vendor_job", f"{j.vendor} · PO {j.po_number or '-'}", f"{j.status or ''} · {j.description}", str(j.id), item_link("vendor_job", str(j.id))) for j in rows]})
        rows = db.query(ShipmentDB).filter(*_all_terms([ShipmentDB.reference, ShipmentDB.container_no, ShipmentDB.awb, ShipmentDB.forwarder,
                                                         ShipmentDB.carrier, ShipmentDB.origin, ShipmentDB.destination], terms)
                                           ).order_by(ShipmentDB.created_at.desc()).limit(limit).all()
        groups.append({"type": "shipment", "label": "Shipments", "hits": [
            _hit("shipment", f"{s.reference or s.container_no or s.awb or s.id}", f"{s.status} · {s.origin or ''} to {s.destination or ''}", str(s.id), item_link("shipment", str(s.id)))
            for s in rows]})
        rows = db.query(ItemUpdateDB).filter(ItemUpdateDB.deleted_at.is_(None), *_all_terms([ItemUpdateDB.body], terms)
                                             ).order_by(ItemUpdateDB.created_at.desc()).limit(limit).all()
        groups.append({"type": "comment", "label": "Comments", "hits": [
            _hit("comment", c.body.replace("\n", " "), f"{c.author_name or c.author_email} on {c.item_type} {c.item_ref}", f"{c.item_type}:{c.item_ref}", item_link(c.item_type, c.item_ref))
            for c in rows]})
    with SessionLocal() as db:
        rows = db.query(ItemFileDB).filter(ItemFileDB.deleted_at.is_(None), *_all_terms([ItemFileDB.filename], terms)).order_by(ItemFileDB.id.desc()).limit(limit).all()
        groups.append({"type": "file", "label": "Files", "hits": [
            _hit("file", f.filename, f"{f.item_type} {f.item_ref} · {f.uploaded_by}", str(f.id), f"/api/v1/files/{f.id}/download") for f in rows]})
    groups.append(_board_rows(terms, limit))
    groups.append(_people(terms, limit))
    groups = [g for g in groups if g["hits"]]
    return {"query": q, "total": sum(len(g["hits"]) for g in groups), "groups": groups}


def _board_rows(terms: List[str], limit: int) -> Dict[str, Any]:
    """Rows of the monday boards the app has read (its own copy): name or any column's text."""
    from app.services.coordination import boards

    hits: List[Dict[str, Any]] = []
    with boards._lock:
        mirrors = list(boards._mirror.values())
    for b in mirrors:
        for it in b.get("items", []):
            hay = " ".join([str(it.get("name") or "")] + [str(v) for v in (it.get("values") or {}).values() if v]).casefold()
            if all(t in hay for t in terms):
                hits.append(_hit("board_item", str(it.get("name") or "(no name)"), f"{b.get('name')} · {it.get('group_title') or ''}".strip(" ·"),
                                 str(it.get("id")), f"/boards?board={b.get('board_id')}&item={it.get('id')}"))
                if len(hits) >= limit * 2:
                    return {"type": "board_item", "label": "monday board rows", "hits": hits}
    return {"type": "board_item", "label": "monday board rows", "hits": hits}


def _people(terms: List[str], limit: int) -> Dict[str, Any]:
    from app.services.coordination import updates

    hits = []
    for p in updates.people():
        hay = f"{updates.fold(p['name'])} {p['email']}"
        if all(updates.fold(t) in hay for t in terms):
            hits.append(_hit("person", p["name"], p["email"], p["email"], ""))
            if len(hits) >= limit:
                break
    return {"type": "person", "label": "People", "hits": hits}
