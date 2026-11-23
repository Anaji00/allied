"""After delivery is confirmed: freight goes onto the sales order, the SO is converted to an invoice, and a freight variance is flagged. Freight is deliberately NOT on the SO when it is created.
Nothing is written to Sage: this prepares a payload file (in the output folder, staged for a person to bring into Sage 50) and opens the "delivery confirmed" vendor payments."""
import json
import os
import tempfile
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.core.money import parse_amount
from app.models.db_models import ShipmentDB, ShipmentPipelineDB

CENT = Decimal("0.01")


class FreightError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _money(v: Any, what: str, none_ok: bool = False) -> Optional[Decimal]:
    try:
        d = parse_amount(v, what, none_ok=none_ok)
    except ValueError as exc:
        raise FreightError(422, str(exc))
    return None if d is None else d.quantize(CENT, rounding=ROUND_HALF_UP)


def variance(actual: Decimal, quoted: Optional[Decimal]) -> Dict[str, Any]:
    """{flag, percent, amount, note}: flagged when there was no quote to compare with, or the actual differs from the quote by more than FREIGHT_VARIANCE_PCT."""
    if quoted is None:
        return {"flag": True, "percent": None, "amount": None, "note": "No freight was quoted for this shipment, so the actual cost cannot be compared. Check it."}
    amount = (actual - quoted).quantize(CENT)
    if quoted == 0:
        return {"flag": actual != 0, "percent": None, "amount": f"{amount:.2f}", "note": "The quote was zero." if actual != 0 else ""}
    exact = (actual - quoted) / quoted * 100
    pct = exact.quantize(CENT)
    limit = Decimal(str(getattr(settings, "FREIGHT_VARIANCE_PCT", 10)))
    flag = abs(exact) > limit                                  # judged on the exact figure, not the rounded one
    return {"flag": flag, "percent": f"{pct:.2f}", "amount": f"{amount:.2f}", "note": (f"Freight was {pct:+.2f}% against the quote (limit {limit:f}%)." if flag else "")}


def confirm_delivery(shipment_id: int, user: Dict[str, Any], so_number: str, freight_actual: Any, freight_quoted: Any = None, po_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """Records the delivery as confirmed by a person, the freight actually paid, the variance, the staged SO-to-invoice payload, and opens the delivery-confirmed vendor payments of the given POs.
    Repeating it for the same shipment replaces the figures and rewrites the same payload file (nothing doubles)."""
    from app.services import payments

    so = (so_number or "").strip()
    if not so:
        raise FreightError(422, "Give the sales order number the freight goes on.")
    actual = _money(freight_actual, "freight cost")
    quoted_in = _money(freight_quoted, "quoted freight", none_ok=True)
    with SessionLocal() as db:
        ship = db.get(ShipmentDB, shipment_id)
        if ship is None:
            raise FreightError(404, "No such shipment.")
        from app.models.db_models import PurchaseOrderDB

        for po_id in po_ids or []:
            po = db.get(PurchaseOrderDB, int(po_id))
            if po is None or po.status == "CANCELLED" or str(ship.reference or "").strip().casefold() != f"po {po.po_number}".casefold():
                raise FreightError(422, f"Purchase order {po_id} is not this shipment's: only the PO the shipment is booked under can have its delivery payment opened by closing it.")
        p = db.get(ShipmentPipelineDB, shipment_id)
        if p is None:
            raise FreightError(409, "Set the shipment's pipeline first.")
        on_record = Decimal(p.freight_cost_quoted) if p.freight_cost_quoted is not None else None
        ignored_quote = quoted_in if (on_record is not None and quoted_in is not None and quoted_in != on_record) else None
        quoted = on_record if on_record is not None else quoted_in                  # the quote on record stands: closing again with a bigger "quote" cannot clear a flag
        if on_record is None and quoted_in is not None:
            p.freight_cost_quoted = quoted_in
        v = variance(actual, Decimal(quoted) if quoted is not None else None)
        previous_flag = bool(p.variance_flag)
        if previous_flag and not v["flag"] and p.freight_cost_actual is not None and Decimal(p.freight_cost_actual) == actual:
            v = {**v, "flag": True, "note": "Flagged on an earlier close; the same freight cost cannot clear it."}
        now = datetime.now(timezone.utc)
        p.freight_cost_actual, p.freight_populated_at, p.variance_flag, p.updated_at = actual, now, bool(v["flag"]), now
        db.commit()
    payload = {"kind": "sales_order_freight_and_invoice", "sales_order_number": so, "freight_line": {"description": "Freight", "amount": f"{actual:.2f}"}, "convert_to_invoice": True,
               "note": "Staged for a person to enter in Sage 50. Nothing was written to Sage."}          # the customer's invoice carries the freight line only: variance and who prepared it stay in Allied's own records
    path = _write_payload(shipment_id, so, payload)
    opened: List[Dict[str, Any]] = []
    for po in po_ids or []:
        opened += payments.fire(int(po), "DELIVERY_CONFIRMED", user)
    audit_log(action="DELIVERY_CONFIRMED", resource_type="SHIPMENT", resource_id=str(shipment_id), actor_email=user.get("email", ""), actor_role=user.get("role", ""),
              detail=f"SO {so}: freight {actual:.2f}; quoted {quoted if quoted is not None else 'none'}; variance flag {v['flag']} ({v.get('percent')}%)" + (f"; a different quote {ignored_quote:.2f} was offered and ignored" if ignored_quote is not None else "")
                     + f"; {len(opened)} payment(s) now due")
    return {"payload": payload, "payload_file": path.name, "payments_now_due": opened, "variance": v, "internal": {"prepared_by": user.get("email"), "quote_ignored": None if ignored_quote is None else f"{ignored_quote:.2f}"}}


def _write_payload(shipment_id: int, so: str, payload: Dict[str, Any]) -> Path:
    import hashlib

    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in so)[:40] + "_" + hashlib.sha1(so.encode("utf-8", "replace")).hexdigest()[:8]            # two different SO texts never share a file name
    folder = Path(settings.OUTPUT_DIR) / "sage_staged"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"so_freight_{safe}_{shipment_id}.json"
    fd, tmp = tempfile.mkstemp(prefix=".sf.", suffix=".tmp", dir=str(folder))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, target)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target
