"""One merged list of what on the Orders page needs a person: unread order emails, supplier bills to check, packing slips that failed, and
orders that need checking. Each item is the same shape so the screen can show one list with a type filter. Reads only."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from app.core import status_words
from app.core.database import SessionLocal
from app.models.db_models import ExceptionDB, MailLogDB, PackingSlipDB

FILTERS = ("needs_me", "emails", "bills", "slips", "orders")


def _age(at: Optional[datetime], now: datetime) -> int:
    if at is None:
        return 0
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return max(0, (now - at).days)


def _money(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def list_items(filter: str = "needs_me", limit: int = 100, email_days: int = 14) -> Dict[str, Any]:
    """{filter, total, counts, items}: items are {kind, ref, title, status_word, amount, currency, age_days, link}, oldest first. Mixed currencies are
    never added up here; each item keeps its own."""
    if filter not in FILTERS:
        raise ValueError("The filter must be one of: " + ", ".join(FILTERS) + ".")
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=email_days)
    items: List[Dict[str, Any]] = []
    with SessionLocal() as db:
        if filter in ("needs_me", "emails"):
            for m in db.query(MailLogDB).filter(MailLogDB.needs_action.is_(True), MailLogDB.handled_at.is_(None), MailLogDB.created_at >= since).all():
                items.append({"kind": "email", "ref": str(m.id), "title": (m.subject or "(no subject)")[:200], "status_word": "Needs a reply or a look",
                              "amount": None, "currency": None, "age_days": _age(m.received_at or m.created_at, now), "link": "/orders?tab=emails"})
        if filter in ("needs_me", "bills", "orders"):
            for e in db.query(ExceptionDB).filter(ExceptionDB.status == "OPEN").all():
                is_bill = (e.exception_type or "").startswith("INVOICE_")
                if (filter == "orders" and is_bill) or (filter == "bills" and not is_bill):
                    continue
                items.append({"kind": "bill" if is_bill else "order", "ref": e.reference_id or e.exception_id, "title": (e.title or e.exception_type or "")[:200],
                              "status_word": status_words.word("exception", e.status), "amount": _money(e.variance_amount), "currency": None,
                              "age_days": _age(e.created_at, now),
                              "link": "/orders?tab=bills" if is_bill else f"/orders?order={quote(str(e.reference_id), safe='')}" if e.reference_id else "/orders?view=attention"})
        if filter in ("needs_me", "slips"):
            for s in db.query(PackingSlipDB).filter(PackingSlipDB.status == "FAILED").all():
                items.append({"kind": "slip", "ref": s.sales_order_number, "title": f"Packing slip for {s.customer_po_number or s.sales_order_number}", "status_word": status_words.word("slip", s.status),
                              "amount": None, "currency": None, "age_days": _age(s.created_at, now), "link": "/orders?tab=slips"})
    items.sort(key=lambda i: (-i["age_days"], i["kind"], i["ref"]))
    counts: Dict[str, int] = {}
    for i in items:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    return {"filter": filter, "total": len(items), "counts": counts, "items": items[:limit]}
