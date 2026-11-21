"""Which job an RFQ, a sales order or a purchase order belongs to, so a screen never has to search by number. null when there is none.

An RFQ belongs to the job made from it; a purchase order records its job; a sales order is the one the job made or was linked to. Reading only."""

from typing import Any, Dict, Iterable, Optional, Tuple

from app.core.database import SessionLocal
from app.models.db_models import JobDB

Link = Optional[Tuple[int, str]]


def maps() -> Dict[str, Dict[Any, Tuple[int, str]]]:
    with SessionLocal() as db:
        jobs = db.query(JobDB.id, JobDB.ref, JobDB.rfq_id, JobDB.sales_order_number).all()
    return {"rfq": {rfq: (i, ref) for i, ref, rfq, _so in jobs if rfq}, "so": {str(so).removeprefix("SO-"): (i, ref) for i, ref, _r, so in jobs if so},
            "ref": {ref: (i, ref) for i, ref, _r, _s in jobs}, "id": {i: (i, ref) for i, ref, _r, _s in jobs}}


def fields(link: Link) -> Dict[str, Any]:
    return {"job_id": link[0] if link else None, "job_ref": link[1] if link else None}


def attach_orders(items: Iterable[Dict[str, Any]]) -> None:
    """Adds job_id and job_ref to each order dict (by its sales order number)."""
    m = maps()["so"]
    for it in items:
        it.update(fields(m.get(str(it.get("sales_order_number") or "").removeprefix("SO-"))))


def for_rfq(rfq_id: Any) -> Dict[str, Any]:
    return fields(maps()["rfq"].get(rfq_id))


def for_job_id(job_id: Any) -> Dict[str, Any]:
    return fields(maps()["id"].get(job_id))
