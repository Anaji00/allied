"""A purchase order side by side with the supplier bills that were checked against it. Read only.

The checks themselves are made by the reconciliation service when a bill is read in; what it stores for each bill is the totals and a list of plain sentences. This reads those back and turns the
sentences into figures (quantity, unit price, freight) so a screen can show them next to the PO's lines. A bill read in since the lines were kept also carries them (`detail`), set beside the PO's lines; an older bill has `detail` null and only the findings."""
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from app.core.database import SessionLocal
from app.models.db_models import PurchaseOrderDB, ReconciliationDB

_QTY = re.compile(r"Quantity Variance on (?P<item>.+?): Billed (?P<billed>[\d.,]+) vs PO (?P<po>[\d.,]+)")
_PRICE = re.compile(r"Price Surcharge on (?P<item>.+?): Billed \$(?P<billed>[\d.,]+) vs PO \$(?P<po>[\d.,]+)")
_LINE = re.compile(r"Line Total Overbilled on (?P<item>.+?): Billed \$(?P<billed>[\d.,]+) vs authorised \$(?P<po>[\d.,]+)")
_FREIGHT = re.compile(r"Unauthorized Freight Add-On: Billed \$(?P<billed>[\d.,]+) vs Agreed \$(?P<po>[\d.,]+)")
_UNEXPECTED = re.compile(r"Unexpected item SKU on invoice: (?P<item>.+?) \(Not on authorized PO\)")


def _num(s: str) -> Optional[Decimal]:
    try:
        return Decimal(s.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _s(d: Optional[Decimal]) -> Optional[str]:
    return None if d is None else f"{d:f}"


def _key(s: Any) -> str:
    return "".join(ch for ch in str(s or "").casefold() if ch.isalnum())


def _same_vendor(bill_vendor: Any, po_vendor: Any) -> bool:
    """False when the bill's supplier name is not the PO's (one name inside the other still counts as the same, to allow 'Zibo Ltd' and 'Zibo')."""
    a, b = _key(bill_vendor), _key(po_vendor)
    return bool(a and b and (a in b or b in a))


def findings(discrepancies: Any) -> List[Dict[str, Any]]:
    """The structured version of a bill's difference sentences. A sentence that is not one of the known kinds is kept as text, never dropped."""
    out: List[Dict[str, Any]] = []
    for text in discrepancies or []:
        text = str(text)
        for kind, rx in (("quantity", _QTY), ("unit_price", _PRICE), ("line_total", _LINE), ("freight", _FREIGHT)):
            m = rx.search(text)
            if m:
                billed, po = _num(m.group("billed")), _num(m.group("po"))
                out.append({"kind": kind, "item": (m.groupdict().get("item") or None), "billed": _s(billed), "po": _s(po), "variance": _s(billed - po) if billed is not None and po is not None else None, "text": text})
                break
        else:
            m = _UNEXPECTED.search(text)
            out.append({"kind": "unexpected_item" if m else "other", "item": m.group("item") if m else None, "billed": None, "po": None, "variance": None, "text": text})
    return out


def _dec(v: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(v).replace(",", "")) if v not in (None, "") else None
    except InvalidOperation:
        return None


def side_by_side(detail: Any, po_lines: Any) -> Optional[Dict[str, Any]]:
    """Each line of the bill next to the PO line it is for (matched on the item, else the description), with the differences. None for a bill checked before its lines were kept."""
    if not isinstance(detail, dict) or not isinstance(detail.get("lines"), list):
        return None
    pol = [ln for ln in (po_lines or []) if isinstance(ln, dict)]
    used: set = set()
    out = []
    for bl in detail["lines"]:
        key_i, key_d = _key(bl.get("item_id")), _key(bl.get("description"))
        hit = next((i for i, p in enumerate(pol) if i not in used and key_i and key_i in (_key(p.get("item")), _key(p.get("sku")))), None)
        if hit is None:
            hit = next((i for i, p in enumerate(pol) if i not in used and key_d and key_d == _key(p.get("description"))), None)
        p = pol[hit] if hit is not None else None
        if hit is not None:
            used.add(hit)
        bq, bc, ba = _dec(bl.get("quantity")), _dec(bl.get("unit_cost")), _dec(bl.get("amount"))
        pq, pc, pa = (_dec(p.get("qty") if p.get("qty") not in (None, "") else p.get("quantity")), _dec(p.get("unit_cost")), _dec(p.get("amount"))) if p else (None, None, None)
        diff = lambda a, b: _s(a - b) if a is not None and b is not None else None  # noqa: E731
        out.append({"item": bl.get("item_id"), "description": bl.get("description"), "bill": {"quantity": _s(bq), "unit_cost": _s(bc), "amount": _s(ba)},
                    "po": {"line": hit + 1 if hit is not None else None, "quantity": _s(pq), "unit_cost": _s(pc), "amount": _s(pa)} if p else None,
                    "matched": p is not None, "variance": {"quantity": diff(bq, pq), "unit_cost": diff(bc, pc), "amount": diff(ba, pa)} if p else None})
    unbilled = [i + 1 for i in range(len(pol)) if i not in used]
    return {"lines": out, "po_lines_not_billed": unbilled, "freight": _s(_dec(detail.get("freight"))), "currency": detail.get("currency"), "invoice_date": detail.get("invoice_date")}


def for_po(po_id: int, po: Dict[str, Any]) -> Dict[str, Any]:
    """The PO (as given, already trimmed for the caller's role) next to its checked bills, newest first."""
    with SessionLocal() as db:
        row = db.get(PurchaseOrderDB, po_id)
        number = (row.po_number or "").strip().upper() if row else ""
        rows = db.query(ReconciliationDB).filter(func.upper(func.trim(ReconciliationDB.po_number)) == number).order_by(ReconciliationDB.reconciled_at.desc()).limit(200).all() if number else []
        bills = []
        for r in rows:
            bills.append({"reconciliation_id": r.reconciliation_id, "invoice_number": r.invoice_number, "vendor": r.vendor_name, "vendor_mismatch": not _same_vendor(r.vendor_name, row.vendor if row else ""), "status": r.reconciliation_status,
                          "po_total": _s(r.authorized_po_total), "billed_total": _s(r.actual_billed_total), "variance": _s(r.cost_variance_amount), "variance_pct": float(r.cost_variance_pct or 0),
                          "findings": findings(r.discrepancies), "detail": side_by_side(r.bill_detail, po.get("lines")), "suggested_action": r.suggested_action, "checked_at": r.reconciled_at.isoformat() if r.reconciled_at else None})
    return {"po_id": po_id, "po_number": po.get("po_number"), "vendor": po.get("vendor"), "po_lines": po.get("lines") or [], "po_total": po.get("total"), "bills": bills,
            "has_bills": bool(bills), "note": "" if bills else "No supplier bill has been checked against this PO yet." if number else "This PO has no number yet, so no bill can be matched to it."}
