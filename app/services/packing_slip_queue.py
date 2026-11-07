"""The packing slip queue: every sales order gets its slip queued the moment it is created.

The slip is made straight away, but as a job on its own: if making the PDF fails, the order
still stands and the job is retried (after 1, 5 and 15 minutes), then flagged for a person.
People see what is queued, ready, failed or printed, can retry one, and mark one printed.
The slip shows the customer's PO, never Allied's internal sales order number.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.database import SessionLocal
from app.core.keyed_lock import keyed_lock
from app.core.logging import logger
from app.models.db_models import PackingSlipDB

# Waits before the 2nd, 3rd and 4th try; after the 4th the job is FAILED and a person is told.
_BACKOFF_MINUTES = (1, 5, 15)
MAX_ATTEMPTS = len(_BACKOFF_MINUTES) + 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_dict(r: PackingSlipDB) -> Dict[str, Any]:
    return {"sales_order_number": r.sales_order_number, "customer_po_number": r.customer_po_number, "status": r.status,
            "attempts": r.attempts, "last_error": r.last_error, "ready": bool(r.path and Path(r.path).is_file()),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "ready_at": r.ready_at.isoformat() if r.ready_at else None,
            "printed_at": r.printed_at.isoformat() if r.printed_at else None, "printed_by": r.printed_by,
            "download": f"/api/v1/orders/{r.sales_order_number}/packing-slip"}


def _expected(generator, so_num: str) -> Path:
    return Path(generator.output_dir) / f"packing_slip_SO_{so_num}.pdf"


def enqueue_and_run(generator, master_order, so_num: str) -> Path:
    """Queue the slip for this sales order and make it now. Returns where the PDF is (or will be);
    never raises for a slip problem, so the order itself is never held up."""
    so_num = str(so_num)
    try:
        with SessionLocal() as db:
            row = db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so_num).first()
            if row is None:
                db.add(PackingSlipDB(sales_order_number=so_num, customer_po_number=master_order.customer_po_number,
                                     status="QUEUED", attempts=0, next_try_at=_now(),
                                     payload=master_order.model_dump(mode="json")))
                db.commit()
    except Exception as exc:  # noqa: BLE001 - no queue table (an old database): make the slip directly, as before
        logger.warning(f"Packing slip queue unavailable ({type(exc).__name__}); making SO {so_num}'s slip directly.")
        return Path(generator.generate_pdf(order=master_order, sales_order_number=so_num))
    run(so_num, generator)
    return _expected(generator, so_num)


def run(so_num: str, generator=None) -> Dict[str, Any]:
    """Make one queued slip. One at a time per order; a job already made or being made is left alone."""
    from app.models.orders import MasterOrder

    if generator is None:
        from app.core.container import get_packing_slip_generator

        generator = get_packing_slip_generator()
    with keyed_lock("packing-slip", so_num):
        with SessionLocal() as db:
            row = db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so_num).first()
            if row is None:
                raise LookupError(f"No packing slip queued for SO {so_num}.")
            if row.status in ("READY", "PRINTED") and row.path and Path(row.path).is_file():
                return _as_dict(row)
            payload, attempts = row.payload, row.attempts
        try:
            if not payload:
                raise ValueError("The order details for this slip weren't kept, so it can't be remade.")
            path = generator.generate_pdf(order=MasterOrder.model_validate(payload), sales_order_number=so_num)
            error = None
        except Exception as exc:  # noqa: BLE001 - recorded on the job, retried later
            path, error = None, f"{type(exc).__name__}: {exc}"[:500]
        with SessionLocal() as db:
            row = db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so_num).one()
            row.attempts = attempts + 1
            if error is None:
                row.status, row.path, row.ready_at, row.last_error, row.next_try_at = "READY", str(path), _now(), None, None
            elif row.attempts >= MAX_ATTEMPTS:
                row.status, row.last_error, row.next_try_at = "FAILED", error, None
            else:
                row.status, row.last_error = "QUEUED", error
                row.next_try_at = _now() + timedelta(minutes=_BACKOFF_MINUTES[min(row.attempts, len(_BACKOFF_MINUTES)) - 1])
            db.commit()
            out = _as_dict(row)
    if error is not None:
        logger.warning(f"Packing slip for SO {so_num} failed (try {out['attempts']}): {error}")
        if out["status"] == "FAILED":
            _alert(out)
    return out


def _alert(job: Dict[str, Any]) -> None:
    from app.services.coordination import notify
    from app.services.coordination.autopilot import reviewers

    notify.notify("packing_slip_failed", f"slip-failed:{job['sales_order_number']}:",
                  f"Packing slip couldn't be made: PO {job['customer_po_number'] or job['sales_order_number']}",
                  f"It failed {job['attempts']} times: {job['last_error']}\nOpen the packing slip queue and press Retry, "
                  "or make the slip by hand before the order ships.", severity="warning", to=reviewers(), link="/orders")


def retry_due(generator=None) -> Dict[str, int]:
    """Retry the slips whose wait is over; run by the background worker every half minute."""
    now = _now()
    with SessionLocal() as db:
        due = [r.sales_order_number for r in db.query(PackingSlipDB).filter(
            PackingSlipDB.status == "QUEUED", PackingSlipDB.next_try_at.isnot(None), PackingSlipDB.next_try_at <= now).limit(20)]
    made = failed = 0
    for so in due:
        try:
            made += run(so, generator)["status"] == "READY"
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the others
            failed += 1
            logger.warning(f"Packing slip retry for SO {so} failed: {exc}")
    return {"due": len(due), "made": made, "failed": failed}


def retry(so_num: str) -> Dict[str, Any]:
    """A person presses Retry: start the job over now, whatever its state."""
    with keyed_lock("packing-slip", so_num):
        with SessionLocal() as db:
            row = db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so_num).first()
            if row is None:
                raise LookupError(f"No packing slip queued for SO {so_num}.")
            row.status, row.attempts, row.last_error, row.path, row.next_try_at = "QUEUED", 0, None, None, _now()
            db.commit()
    return run(so_num)


def mark_printed(so_num: str, who: str) -> Dict[str, Any]:
    with SessionLocal() as db:
        row = db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so_num).first()
        if row is None:
            raise LookupError(f"No packing slip queued for SO {so_num}.")
        if row.status not in ("READY", "PRINTED"):
            raise ValueError("That slip isn't ready yet.")
        row.status, row.printed_at, row.printed_by = "PRINTED", _now(), who
        db.commit()
        return _as_dict(row)


def list_queue(status: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    with SessionLocal() as db:
        counts: Dict[str, int] = {}
        for (st,) in db.query(PackingSlipDB.status):
            counts[st] = counts.get(st, 0) + 1
        q = db.query(PackingSlipDB)
        if status:
            q = q.filter(PackingSlipDB.status == status.upper())
        rows: List[PackingSlipDB] = q.order_by(PackingSlipDB.created_at.desc()).limit(limit).all()
        return {"counts": counts, "jobs": [_as_dict(r) for r in rows]}
