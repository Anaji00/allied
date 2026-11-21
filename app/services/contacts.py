"""Contacts: customers, vendors and carriers in one list, with when each last ordered and how many orders are open. Reading only.

Customers and vendors come from the master lists (the same ones the catalogue importer keeps); the records route edits them (kind=customer|vendor). Carriers are
the forwarders and carriers named on shipments and on the rate card.
"""

from typing import Any, Dict, List, Optional

KINDS = ("customer", "vendor", "carrier")
_ORDER_DONE = {"SHIPPED", "CLOSED", "CANCELLED", "DELIVERED", "INVOICED"}
_PO_OPEN = {"DRAFT", "APPROVED", "SENDING", "SENT", "ACKNOWLEDGED"}


def _norm(name: Any) -> str:
    from app.services.coordination import jobs

    return jobs.norm_name(str(name or ""))


def _iso(at: Any) -> Optional[str]:
    return at.isoformat() if hasattr(at, "isoformat") else (str(at) if at else None)


_PURPOSE_WORDS = (("invoices", ("invoice", "invoices", "ap", "accounts", "accountspayable", "payable", "payables", "billing", "bills", "remit", "finance")),
                  ("shipping", ("shipping", "logistics", "receiving", "traffic", "freight", "dispatch", "warehouse")),
                  ("quotes", ("quotes", "quote", "rfq", "rfqs", "sales", "purchasing", "buyer", "procurement", "estimating")))


def purpose_of(email: Any) -> str:
    """What an address is for, from its name: invoices (accounts payable, billing), shipping, quotes (sales, purchasing, RFQs) or "" when it looks like a person or is unknown. Only a guess from the
    part before the @; a customer row can carry quote_email and shipping_email columns that say it outright."""
    import re

    raw = str(email or "").split("@", 1)[0].lower()
    joined = re.sub(r"[.\-_+]", "", raw)
    parts = [t for t in re.split(r"[.\-_+]", raw) if t]
    for purpose, words in _PURPOSE_WORDS:                                          # the whole name is a role word, or only role words joined by separators (accounts.payable); never "starts with"
        if joined and (joined in words or (parts and all(t in words for t in parts))):
            return purpose
    return ""


def _customers() -> List[Dict[str, Any]]:
    from app.core.container import get_entity_matcher
    from app.core.database import SessionLocal
    from app.models.db_models import OrderDB

    last: Dict[str, Any] = {}
    openn: Dict[str, int] = {}
    with SessionLocal() as db:
        for cid, name, status, made in db.query(OrderDB.customer_id, OrderDB.customer_name, OrderDB.status, OrderDB.created_at):
            for k in {str(cid or ""), _norm(name)}:
                if k:
                    last[k] = max(last.get(k) or made, made) if made else last.get(k)
                    openn[k] = openn.get(k, 0) + (0 if status in _ORDER_DONE else 1)
    out = []
    for cid, row in get_entity_matcher().customers_cache.items():
        k = str(cid) if str(cid) in last else _norm(row.get("customer_name"))
        out.append({"kind": "customer", "ref": str(cid), "name": row.get("customer_name") or "", "email": row.get("email") or "", "phone": row.get("phone") or "",
                    "contact": row.get("contact_name") or "", "email_purpose": purpose_of(row.get("email")), "quote_email": row.get("quote_email") or "", "shipping_email": row.get("shipping_email") or "",
                    "quote_contact_note": (None if (row.get("quote_email") or purpose_of(row.get("email")) in ("", "quotes")) else "No quote contact on file: only an accounts address is known."), "last_order_at": _iso(last.get(k)), "open_orders": openn.get(k, 0), "owner": row.get("sales_rep") or ""})
    return out


def _vendors() -> List[Dict[str, Any]]:
    from app.core.container import get_entity_matcher
    from app.core.database import SessionLocal
    from app.models.db_models import PurchaseOrderDB

    last: Dict[str, Any] = {}
    openn: Dict[str, int] = {}
    with SessionLocal() as db:
        for vendor, status, made in db.query(PurchaseOrderDB.vendor, PurchaseOrderDB.status, PurchaseOrderDB.created_at):
            k = _norm(vendor)
            last[k] = max(last.get(k) or made, made) if made else last.get(k)
            openn[k] = openn.get(k, 0) + (1 if status in _PO_OPEN else 0)
    out = []
    for vid, row in get_entity_matcher().vendors_cache.items():
        k = _norm(row.get("vendor_name"))
        out.append({"kind": "vendor", "ref": str(vid), "name": row.get("vendor_name") or "", "email": row.get("email") or "", "phone": row.get("phone") or "", "contact": "",
                    "last_order_at": _iso(last.get(k)), "open_orders": openn.get(k, 0), "owner": ""})
    return out


def _carriers() -> List[Dict[str, Any]]:
    from app.services import carrier_rates
    from app.services.coordination import shipments

    seen: Dict[str, Dict[str, Any]] = {}
    for s in shipments.list_shipments():
        for name in {str(s.get("forwarder") or "").strip(), str(s.get("carrier") or "").strip()} - {""}:
            c = seen.setdefault(_norm(name), {"kind": "carrier", "ref": name, "name": name, "email": "", "phone": "", "contact": "", "last_order_at": None, "open_orders": 0, "owner": s.get("owner_email") or ""})
            if s.get("status") not in shipments.FINISHED:
                c["open_orders"] += 1
            when = s.get("created_at")
            if when and (not c["last_order_at"] or when > c["last_order_at"]):
                c["last_order_at"] = when
    for line in carrier_rates.load():
        k = _norm(line["carrier"])
        seen.setdefault(k, {"kind": "carrier", "ref": line["carrier"], "name": line["carrier"], "email": "", "phone": "", "contact": "", "last_order_at": None, "open_orders": 0, "owner": ""})
    return list(seen.values())


def listing(kind: str, q: str = "", limit: int = 200, sort: str = "name") -> Dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"The kind must be one of: {', '.join(KINDS)}.")
    if sort not in ("name", "last_order_at", "open_orders"):
        raise ValueError("Sort by name, last_order_at or open_orders.")
    rows = {"customer": _customers, "vendor": _vendors, "carrier": _carriers}[kind]()
    q = " ".join(str(q or "").split())[:100].casefold()
    if q:
        rows = [r for r in rows if q in " ".join(str(v) for v in r.values() if v is not None).casefold()]
    if sort == "name":
        rows.sort(key=lambda r: r["name"].casefold())
    elif sort == "open_orders":
        rows.sort(key=lambda r: (-r["open_orders"], r["name"].casefold()))
    else:
        rows.sort(key=lambda r: (r["last_order_at"] is None, "" if r["last_order_at"] is None else r["last_order_at"]), reverse=False)
        rows.sort(key=lambda r: r["last_order_at"] or "", reverse=True)
    limit = max(1, min(int(limit), 1000))
    return {"kind": kind, "count": len(rows[:limit]), "total": len(rows), "editable_with": f"/api/v1/records/{kind}/{{ref}}" if kind in ("customer", "vendor") else None,
            "items": [{**r, "link": f"/contacts?kind={kind}&ref={r['ref']}"} for r in rows[:limit]]}
