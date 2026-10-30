"""Vendor Bill & Invoice 3-Way Reconciliation Service.

Cross-checks incoming vendor invoices from suppliers (Nibrock, Zibo, KWS)
against authorized Sage 50 Purchase Orders and customer Sales Orders,
identifying cost/quantity discrepancies, freight add-ons, and auto-approving matches.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
import re
import threading
import uuid
from pydantic import BaseModel, Field

from app.config import settings
from app.core.logging import logger
from app.services.monday_client import MondayClient
from app.core.database import SessionLocal
from app.services.database_service import DatabaseService
from app.core.audit import audit_log
from app.services.outbound_webhook_service import outbound_webhook_service
from app.core.container import get_monday_client


# Cap on the in-memory history mirror; the database holds the full audit trail.
_MAX_HISTORY = 500

# Rounding allowance on money comparisons. Same figure the document scanner
# uses when it checks that an invoice's lines add up to its stated total.
_AMOUNT_TOLERANCE = Decimal("0.05")

# Statuses that need a person, not a letter to the supplier: the PO is unknown,
# the bill was already approved once, or the bill is from someone other than
# the PO's supplier. Writing to "the vendor" in any of those cases is writing
# to the wrong party or about the wrong thing.
_NEEDS_PERSON_STATUSES = ("UNMATCHED_NO_PO", "DUPLICATE_INVOICE", "VENDOR_MISMATCH")


def _vendor_tokens(name: Optional[str]) -> List[str]:
    return re.findall(r"[A-Z0-9]+", (name or "").upper())


def _same_vendor(a: Optional[str], b: Optional[str]) -> bool:
    """True when two vendor names denote the same supplier.

    'ZIBO' and 'Zibo Supereal Ceramics Co., Ltd.' match (one is a leading run
    of the other's words); 'KWS' and 'ZIBO' do not, and neither does a blank.
    """
    ta, tb = _vendor_tokens(a), _vendor_tokens(b)
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return long_[: len(short)] == short


def _dispute_key(vendor_name: str, invoice_number: str) -> Tuple[str, str]:
    return (" ".join(_vendor_tokens(vendor_name)), (invoice_number or "").strip().upper())


class VendorInvoiceLine(BaseModel):
    """Line item on an incoming vendor bill."""
    item_id: str
    description: Optional[str] = None
    quantity_billed: Decimal
    unit_cost_billed: Decimal
    total_amount: Decimal


class VendorInvoice(BaseModel):
    """Incoming vendor invoice received from supplier accounts receivable."""
    invoice_number: str = Field(..., description="Vendor's official invoice number")
    vendor_name: str = Field(..., description="NIBROCK, ZIBO, KWS, or supplier name")
    po_number: str = Field(..., description="Referenced Allied PO Number")
    invoice_date: date = Field(default_factory=date.today)
    lines: List[VendorInvoiceLine] = Field(default_factory=list)
    freight_billed: Decimal = Field(default=Decimal("0.00"), description="Additional freight on vendor bill")
    total_amount_billed: Decimal = Field(..., description="Total payable requested by vendor")


class ReconciliationResult(BaseModel):
    """Comprehensive 3-way match audit outcome."""
    reconciliation_id: str
    invoice_number: str
    vendor_name: str
    po_number: str
    reconciliation_status: str  # RECONCILED_APPROVED, DISCREPANCY_PRICE, DISCREPANCY_QTY, DISCREPANCY_FREIGHT
    authorized_po_total: Decimal
    actual_billed_total: Decimal
    cost_variance_amount: Decimal
    cost_variance_pct: float
    discrepancies: List[str] = Field(default_factory=list)
    suggested_action: str
    dispute_email_draft: Optional[str] = None
    reconciled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReconciliationService:
    """Performs mathematical and contractual 3-way matching."""

    def __init__(self, monday_client: Optional[MondayClient] = None):
        self.monday = monday_client or get_monday_client()
        self._history: List[Dict[str, Any]] = []
        # Dispute letters already dispatched this process, keyed by
        # (vendor, invoice), so a re-submitted invoice is not disputed twice.
        self._dispute_log: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
        self._dispute_lock = threading.Lock()
        self._invoice_locks: Dict[Tuple[str, str], threading.Lock] = {}
        # Resolved lazily: the matcher loads the catalogues, and constructing
        # this service must not pay that cost when no dispute is ever sent.
        self._matcher = None
        # Pre-seed sample known PO costs for simulation
        self._authorized_pos: Dict[str, Dict[str, Any]] = {
            "PO-NIBROCK-9921": {
                "vendor": "NIBROCK",
                "sales_order": "SO-12930",
                "lines": {
                    "PLATE-300XL-38": {"qty": Decimal("4"), "unit_cost": Decimal("110.00")},
                },
                "authorized_freight": Decimal("125.00"),
            },
            "PO-ZIBO-4412": {
                "vendor": "ZIBO",
                "sales_order": "SO-12931",
                "lines": {
                    "HOSE-CERAMIC-4IN": {"qty": Decimal("2"), "unit_cost": Decimal("420.00")},
                },
                "authorized_freight": Decimal("85.00"),
            },
            "PO-KWS-8819": {
                "vendor": "KWS",
                "sales_order": "SO-12932",
                "lines": {
                    "SCREEN-250": {"qty": Decimal("2"), "unit_cost": Decimal("650.00")},
                },
                "authorized_freight": Decimal("0.00"),
            },
        }
        if settings.SEED_DEMO_DATA:
            self._seed_sample_reconciliations()

    def _seed_sample_reconciliations(self) -> None:
        """Seeds realistic historical reconciliations."""
        # 1. Clean Match
        inv_clean = VendorInvoice(
            invoice_number="INV-ZIBO-7721",
            vendor_name="ZIBO",
            po_number="PO-ZIBO-4412",
            lines=[
                VendorInvoiceLine(
                    item_id="HOSE-CERAMIC-4IN",
                    description="4 inch Ceramic Lined Slurry Hose",
                    quantity_billed=Decimal("2"),
                    unit_cost_billed=Decimal("420.00"),
                    total_amount=Decimal("840.00"),
                )
            ],
            freight_billed=Decimal("85.00"),
            total_amount_billed=Decimal("925.00"),
        )
        self.reconcile_vendor_invoice(inv_clean)

        # 2. Price Discrepancy
        inv_disc = VendorInvoice(
            invoice_number="INV-NIBROCK-8840",
            vendor_name="NIBROCK",
            po_number="PO-NIBROCK-9921",
            lines=[
                VendorInvoiceLine(
                    item_id="PLATE-300XL-38",
                    description="Allied 300XL Wear Plate 3/8 x 48 x 96",
                    quantity_billed=Decimal("4"),
                    unit_cost_billed=Decimal("128.50"),  # Billed $128.50 instead of $110.00
                    total_amount=Decimal("514.00"),
                )
            ],
            freight_billed=Decimal("195.00"),  # Billed $195 instead of $125 authorized
            total_amount_billed=Decimal("709.00"),
        )
        self.reconcile_vendor_invoice(inv_disc)

    def reconcile_vendor_invoice(self, invoice: VendorInvoice) -> ReconciliationResult:
        """
        Validates invoice line items against authorized PO costs and freight rules.

        One bill (supplier + invoice number) is checked at a time, so the same
        bill arriving twice at once (email and upload) cannot pass the
        duplicate check twice before either result is saved.
        """
        key = _dispute_key(invoice.vendor_name, invoice.invoice_number)
        with self._dispute_lock:
            lock = self._invoice_locks.setdefault(key, threading.Lock())
        with lock:
            return self._reconcile(invoice)

    def _reconcile(self, invoice: VendorInvoice) -> ReconciliationResult:
        logger.info(f"Auditing vendor invoice [{invoice.invoice_number}] from {invoice.vendor_name} for PO #{invoice.po_number}")

        rec_id = f"REC-{uuid.uuid4().hex[:8].upper()}"
        po_data = self._authorized_pos.get(invoice.po_number.upper())

        # If PO is not in synthetic mock table, check agent_memory_cache for real executed orders
        ambiguous_matches = 0
        if not po_data:
            from app.services.agent_memory_cache import agent_memory_cache
            inv_po_clean = invoice.po_number.strip().upper()
            matches: List[Dict[str, Any]] = []
            for ord_data in agent_memory_cache.get_all_orders():
                pos = ord_data.get("purchase_orders", {})
                cust_po = str(ord_data.get("customer_po_number", "")).upper()
                so_num = str(ord_data.get("sales_order_number", "")).upper()
                po_values = [str(v).upper() for v in pos.values()] if isinstance(pos, dict) else [str(v).upper() for v in pos]

                # Exact identifier matches only. A substring test such as
                # `cust_po in inv_po_clean` matched "PO-1" against "PO-12" and
                # could reconcile an invoice against the wrong customer's order.
                if inv_po_clean in po_values or inv_po_clean in (cust_po, so_num):
                    matches.append(ord_data)

            if len(matches) > 1:
                # Two customers can share a PO number; picking the first match
                # would check this bill against whichever happened to be cached.
                ambiguous_matches = len(matches)
            matching_order = matches[0] if len(matches) == 1 else None

            if matching_order:
                lines_dict = {}
                order_vendors = set()
                pos = matching_order.get("purchase_orders", {})
                if isinstance(pos, dict):
                    order_vendors.update(str(k).upper() for k in pos.keys())
                for it in matching_order.get("line_items", []):
                    v_pref = (it.get("preferred_vendor") or "").upper()
                    if v_pref:
                        order_vendors.add(v_pref)
                    if _same_vendor(v_pref, invoice.vendor_name) or v_pref == "STOCK":
                        lines_dict[it.get("item_id")] = {
                            "qty": Decimal(str(it.get("quantity", 1))),
                            "unit_cost": Decimal(str(it.get("unit_cost", 0))),
                        }
                order_vendors.discard("STOCK")
                # Freight is only authorised when the order carries a figure
                # for it. A flat $100 allowance let unbacked freight through.
                freight = matching_order.get("authorized_freight", matching_order.get("freight_amount"))
                po_data = {
                    "vendor": next(
                        (v for v in sorted(order_vendors) if _same_vendor(v, invoice.vendor_name)),
                        ", ".join(sorted(order_vendors)),
                    ),
                    "sales_order": matching_order.get("sales_order_number", "SO-ALLIED"),
                    "lines": lines_dict,
                    "authorized_freight": Decimal(str(freight)) if freight is not None else Decimal("0.00"),
                }

        # No authorized PO could be located. Previously the "authorized" figures
        # were synthesized from the invoice itself, which made every unmatched
        # invoice reconcile to a zero variance and auto-approve for payment.
        po_found = po_data is not None
        discrepancies: List[str] = []
        if not po_found:
            po_data = {
                "vendor": invoice.vendor_name,
                "sales_order": "",
                "lines": {},
                "authorized_freight": Decimal("0.00"),
            }
            discrepancies.append(
                f"No authorized purchase order found for PO #{invoice.po_number} — "
                f"cannot verify any billed amount against an approved commitment."
                if not ambiguous_matches
                else f"PO #{invoice.po_number} matches {ambiguous_matches} different orders — "
                f"cannot tell which commitment this bill is against."
            )

        # The bill must come from the supplier the PO was placed with. A KWS
        # bill against a Zibo PO was previously approved on matching lines.
        vendor_mismatch = po_found and not _same_vendor(po_data.get("vendor"), invoice.vendor_name)
        if vendor_mismatch:
            discrepancies.append(
                f"Vendor mismatch: invoice is from '{invoice.vendor_name}' but PO #{invoice.po_number} "
                f"was placed with '{po_data.get('vendor') or 'unknown'}'."
            )

        # Same supplier, same invoice number, already approved once: paying it
        # again is the single most expensive mistake this check exists to stop.
        prior_rows = self._prior_reconciliations(invoice)
        prior_approved = [r for r in prior_rows if r.get("reconciliation_status") == "RECONCILED_APPROVED"]
        if prior_approved:
            discrepancies.append(
                f"Duplicate invoice: #{invoice.invoice_number} from {invoice.vendor_name} was already "
                f"approved ({prior_approved[0].get('reconciliation_id')})."
            )

        # Calculate Authorized PO Total from the billed items
        auth_lines_total = Decimal("0.00")
        auth_freight = po_data.get("authorized_freight", Decimal("0.00"))

        # Aggregate billed quantities and audit unit costs
        billed_by_item: Dict[str, Dict[str, Any]] = {}
        for line in invoice.lines:
            if line.item_id not in billed_by_item:
                billed_by_item[line.item_id] = {
                    "total_qty": Decimal("0.00"),
                    "lines": [],
                }
            billed_by_item[line.item_id]["total_qty"] += line.quantity_billed
            billed_by_item[line.item_id]["lines"].append(line)

        # 1. Audit each billed item against PO
        for item_id, billed_info in billed_by_item.items():
            expected = po_data["lines"].get(item_id)
            if not expected:
                # An unauthorized SKU contributes nothing to the authorized
                # total. Adding the billed amount here (as it previously did)
                # cancelled the item out of the variance, so a line for goods
                # that were never ordered reported a $0.00 exposure.
                unauthorized_value = sum(
                    l.quantity_billed * l.unit_cost_billed for l in billed_info["lines"]
                )
                discrepancies.append(
                    f"Unexpected item SKU on invoice: {item_id} (Not on authorized PO) — "
                    f"${unauthorized_value:,.2f} billed without authorization"
                )
                continue

            expected_qty = expected["qty"]
            expected_cost = expected["unit_cost"]
            total_qty_billed = billed_info["total_qty"]
            auth_lines_total += (expected_qty * expected_cost)

            # Check total quantity billed across all lines for this item
            if total_qty_billed != expected_qty:
                discrepancies.append(
                    f"Quantity Variance on {item_id}: Billed {total_qty_billed} vs PO {expected_qty}"
                )

            # Check unit cost and the extended amount on each line. The line
            # total is what gets paid, so it is checked against the authorised
            # price in its own right, not only the unit cost printed beside it.
            for line in billed_info["lines"]:
                authorised_line = line.quantity_billed * expected_cost
                if line.unit_cost_billed > expected_cost:
                    cost_diff = line.unit_cost_billed - expected_cost
                    discrepancies.append(
                        f"Price Surcharge on {item_id}: Billed ${line.unit_cost_billed:.2f} vs PO ${expected_cost:.2f} (+${cost_diff:.2f}/unit)"
                    )
                # Only when the unit price looked right, so one overcharge is not reported twice.
                elif line.total_amount > authorised_line + _AMOUNT_TOLERANCE:
                    discrepancies.append(
                        f"Line Total Overbilled on {item_id}: Billed ${line.total_amount:,.2f} vs "
                        f"authorised ${authorised_line:,.2f} for {line.quantity_billed} units"
                    )

        # Check freight
        if invoice.freight_billed > auth_freight:
            freight_diff = invoice.freight_billed - auth_freight
            discrepancies.append(
                f"Unauthorized Freight Add-On: Billed ${invoice.freight_billed:.2f} vs Agreed ${auth_freight:.2f} (+${freight_diff:.2f})"
            )

        authorized_total = auth_lines_total + auth_freight
        actual_total = invoice.total_amount_billed
        cost_variance = actual_total - authorized_total
        variance_pct = float((cost_variance / authorized_total) * 100) if authorized_total > Decimal("0.00") else 0.0

        # The payable total is what is actually paid. It was never compared,
        # so a $50,000 bill whose lines matched a $925 PO was approved.
        # A backstop: flagged only when no line or freight finding already explains it.
        if po_found and not discrepancies and actual_total > authorized_total + _AMOUNT_TOLERANCE:
            discrepancies.append(
                f"Invoice Total Exceeds Authorised: Billed ${actual_total:,.2f} vs authorised "
                f"${authorized_total:,.2f} (+${cost_variance:,.2f})"
            )
        billed_sum = sum((l.total_amount for l in invoice.lines), Decimal("0.00")) + invoice.freight_billed
        if abs(actual_total - billed_sum) > _AMOUNT_TOLERANCE:
            discrepancies.append(
                f"Invoice does not add up: lines plus freight are ${billed_sum:,.2f} but the total "
                f"billed is ${actual_total:,.2f}"
            )

        dispute_draft = None
        if not po_found:
            status = "UNMATCHED_NO_PO"
            suggested = (
                f"Hold Sage AP payment release. No approved PO #{invoice.po_number} exists — "
                f"confirm the commitment with purchasing before any payment."
            )
            dispute_draft = self._generate_dispute_email(invoice, discrepancies, cost_variance)
        elif prior_approved:
            status = "DUPLICATE_INVOICE"
            suggested = (
                f"Do not pay. Invoice #{invoice.invoice_number} from {invoice.vendor_name} was already "
                f"approved; confirm with AP before any further action."
            )
        elif vendor_mismatch:
            status = "VENDOR_MISMATCH"
            suggested = (
                f"Hold Sage AP payment release. The bill is not from the supplier on PO "
                f"#{invoice.po_number}; a person must confirm who is owed before any payment."
            )
            dispute_draft = self._generate_dispute_email(invoice, discrepancies, cost_variance)
        elif discrepancies:
            status = "DISCREPANCY_FLAGGED"
            suggested = f"Hold Sage AP payment release. Send dispute notice to {invoice.vendor_name} AR."
            dispute_draft = self._generate_dispute_email(invoice, discrepancies, cost_variance)
        else:
            status = "RECONCILED_APPROVED"
            suggested = "Exact match verified. Approved for Sage 50 Accounts Payable release."

        if status != "RECONCILED_APPROVED":
            # Post warning to Monday.com
            try:
                self.monday.post_pulse_update(
                    0,
                    f"⚠️ Vendor Invoice Discrepancy on PO #{invoice.po_number}: {'; '.join(discrepancies)}"
                )
            except Exception:
                pass

        result = ReconciliationResult(
            reconciliation_id=rec_id,
            invoice_number=invoice.invoice_number,
            vendor_name=invoice.vendor_name,
            po_number=invoice.po_number,
            reconciliation_status=status,
            authorized_po_total=authorized_total,
            actual_billed_total=actual_total,
            cost_variance_amount=cost_variance,
            cost_variance_pct=variance_pct,
            discrepancies=discrepancies,
            suggested_action=suggested,
            dispute_email_draft=dispute_draft,
        )

        # In-memory history for fast API responses
        rec_dict = result.model_dump()
        self._history.insert(0, rec_dict)
        del self._history[_MAX_HISTORY:]

        # Persistent SQLite storage
        try:
            with SessionLocal() as db:
                DatabaseService.persist_reconciliation(db, rec_dict)
                if status != "RECONCILED_APPROVED":
                    audit_log(
                        db=db,
                        action="INVOICE_DISCREPANCY_FLAGGED",
                        resource_type="RECONCILIATION",
                        resource_id=rec_id,
                        detail=f"[{status}] Vendor {invoice.vendor_name} invoice {invoice.invoice_number} has {len(discrepancies)} discrepancies. Variance: ${cost_variance:,.2f}",
                    )
                else:
                    # An approval releases money, so it is audited as much as a flag.
                    audit_log(
                        db=db,
                        action="INVOICE_APPROVED_FOR_PAYMENT",
                        resource_type="RECONCILIATION",
                        resource_id=rec_id,
                        detail=(
                            f"Vendor {invoice.vendor_name} invoice {invoice.invoice_number} against PO "
                            f"#{invoice.po_number} approved: ${actual_total:,.2f} billed vs "
                            f"${authorized_total:,.2f} authorised."
                        ),
                    )
        except Exception as e:
            logger.warning(f"Could not persist reconciliation to database: {e}")

        # Outbound webhook notification for discrepancies
        if discrepancies:
            try:
                outbound_webhook_service.notify_discrepancy(
                    invoice_number=invoice.invoice_number,
                    vendor=invoice.vendor_name,
                    discrepancies=discrepancies,
                    variance_amount=float(cost_variance),
                )
            except Exception as e:
                logger.warning(f"Could not send outbound webhook notification: {e}")

            if status in _NEEDS_PERSON_STATUSES:
                # No letter to the supplier: these go to a person.
                self._queue_for_review(result)
            else:
                # The dispute letter reaches the supplier's billing department
                # rather than only the log. Sending is off until configured, in
                # which case this records the draft and nothing leaves. A
                # re-submitted invoice that was already disputed is not
                # disputed again.
                already_disputed = any(r.get("dispute_email_draft") and
                                       r.get("reconciliation_status") == "DISCREPANCY_FLAGGED"
                                       for r in prior_rows)
                try:
                    self._dispatch_dispute(
                        invoice.vendor_name, invoice.invoice_number, dispute_draft,
                        skip_if_attempted=True, already_disputed=already_disputed,
                    )
                except Exception as e:
                    logger.warning(f"Could not queue dispute letter: {e}")

        return result

    def _prior_reconciliations(self, invoice: VendorInvoice) -> List[Dict[str, Any]]:
        """Earlier reconciliations of the same vendor + invoice number."""
        number = (invoice.invoice_number or "").strip().upper()
        rows: List[Dict[str, Any]] = []
        try:
            from sqlalchemy import func
            from app.models.db_models import ReconciliationDB

            with SessionLocal() as db:
                found = (
                    db.query(ReconciliationDB)
                    .filter(func.upper(func.trim(ReconciliationDB.invoice_number)) == number)
                    .order_by(ReconciliationDB.reconciled_at.desc())
                    .all()
                )
                rows = [
                    {
                        "reconciliation_id": r.reconciliation_id,
                        "vendor_name": r.vendor_name,
                        "reconciliation_status": r.reconciliation_status,
                        "dispute_email_draft": r.dispute_email_draft,
                    }
                    for r in found
                ]
        except Exception as e:
            logger.warning(f"Could not read prior reconciliations; using in-memory history: {e}")
            rows = [
                h for h in self._history
                if str(h.get("invoice_number", "")).strip().upper() == number
            ]
        return [r for r in rows if _same_vendor(r.get("vendor_name"), invoice.vendor_name)]

    def _queue_for_review(self, result: ReconciliationResult) -> None:
        """Puts an invoice that needs a person on the operations exception queue."""
        try:
            from app.services.exception_queue_service import (
                MANUAL_RESOLUTION_ACTION,
                ExceptionItem,
                exception_queue_service,
            )

            titles = {
                "UNMATCHED_NO_PO": "Invoice cites no known PO",
                "DUPLICATE_INVOICE": "Invoice already approved once",
                "VENDOR_MISMATCH": "Invoice is not from the PO's supplier",
            }
            exception_queue_service.add_exception(
                ExceptionItem(
                    exception_id=f"EXC-{result.reconciliation_id}",
                    exception_type=f"INVOICE_{result.reconciliation_status}",
                    severity="HIGH",
                    title=f"{titles.get(result.reconciliation_status, 'Invoice needs review')}: "
                          f"#{result.invoice_number}",
                    description="; ".join(result.discrepancies),
                    reference_id=result.invoice_number,
                    vendor_or_customer=result.vendor_name,
                    variance_amount=float(result.cost_variance_amount),
                    available_actions=[
                        {"action": MANUAL_RESOLUTION_ACTION, "label": "Reviewed and handled with AP"},
                    ],
                )
            )
        except Exception as e:
            logger.warning(f"Could not queue invoice {result.invoice_number} for review: {e}")

    def send_dispute_for_invoice(self, invoice_number: str, vendor_name: str) -> Optional[Dict[str, Any]]:
        """Sends the drafted dispute for an invoice on an operator's request.

        Returns the outbox entry (status SENT / DRAFTED / FAILED), or None when
        there is no dispute draft or no address on file. An invoice whose
        dispute was already SENT is not sent again; that entry is returned.
        """
        number = (invoice_number or "").strip().upper()
        draft: Optional[str] = None
        try:
            from sqlalchemy import func
            from app.models.db_models import ReconciliationDB

            with SessionLocal() as db:
                for r in (
                    db.query(ReconciliationDB)
                    .filter(func.upper(func.trim(ReconciliationDB.invoice_number)) == number)
                    .order_by(ReconciliationDB.reconciled_at.desc())
                    .all()
                ):
                    if (r.dispute_email_draft and r.reconciliation_status == "DISCREPANCY_FLAGGED"
                            and _same_vendor(r.vendor_name, vendor_name)):
                        draft = r.dispute_email_draft
                        break
        except Exception as e:
            logger.warning(f"Could not look up dispute draft for {invoice_number}: {e}")
        if draft is None:
            draft = next(
                (h.get("dispute_email_draft") for h in self._history
                 if str(h.get("invoice_number", "")).strip().upper() == number
                 and h.get("reconciliation_status") == "DISCREPANCY_FLAGGED"
                 and _same_vendor(h.get("vendor_name"), vendor_name)
                 and h.get("dispute_email_draft")),
                None,
            )
        if not draft:
            return None
        return self._dispatch_dispute(vendor_name, invoice_number, draft)

    def _dispatch_dispute(
        self,
        vendor_name: str,
        invoice_number: str,
        draft: Optional[str],
        skip_if_attempted: bool = False,
        already_disputed: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Hands the drafted dispute to the outbound mailer.

        A dispute is only ever queued automatically; it is not an irreversible
        act in the sending sense, but it is one in the relationship sense, so
        the recipient is resolved from the vendor master rather than from
        anything on the invoice itself. An invoice that names its own dispute
        address is exactly the invoice you would not want to trust.

        Deduplicated per vendor + invoice: a letter already SENT is never sent
        again, and the automatic path does not retry one already attempted
        (here or, per ``already_disputed``, in an earlier run).
        """
        if not draft:
            return None

        key = _dispute_key(vendor_name, invoice_number)
        with self._dispute_lock:
            if key in self._dispute_log:
                prior = self._dispute_log[key]
                if (prior and prior.get("status") == "SENT") or skip_if_attempted:
                    return prior
            elif skip_if_attempted and already_disputed:
                logger.info(f"Dispute for invoice {invoice_number} from {vendor_name} already raised; not resending.")
                return None
            # Claim the key before sending so a concurrent call does not also send.
            self._dispute_log[key] = None

        from app.services.outbound_email_service import outbound_email_service

        if self._matcher is None:
            from app.core.container import get_entity_matcher

            self._matcher = get_entity_matcher()

        vendor_email = ""
        try:
            rec, _ = self._matcher.match_vendor(vendor_name)
            vendor_email = (rec or {}).get("email", "") or ""
        except Exception:
            vendor_email = ""

        if not vendor_email:
            logger.info(
                f"No billing address on file for {vendor_name}; dispute for "
                f"invoice {invoice_number} recorded without a recipient."
            )
            return None

        entry = outbound_email_service.send_dispute(
            vendor_name=vendor_name,
            vendor_email=vendor_email,
            invoice_number=invoice_number,
            body=draft,
        )
        with self._dispute_lock:
            self._dispute_log[key] = entry
        return entry

    def _generate_dispute_email(self, invoice: VendorInvoice, discrepancies: List[str], variance: Decimal) -> str:
        """Generates formal billing dispute notice for vendor Accounts Receivable."""
        bullet_points = "\n".join([f"  - {d}" for d in discrepancies])
        return (
            f"Subject: Billing Discrepancy Notice - Invoice #{invoice.invoice_number} (Ref: Allied PO #{invoice.po_number})\n"
            f"To: {invoice.vendor_name} Accounts Receivable\n\n"
            f"Dear {invoice.vendor_name} Billing Department,\n\n"
            f"During our automated 3-way invoice matching for PO #{invoice.po_number}, "
            f"our accounting system identified the following discrepancies on Invoice #{invoice.invoice_number}:\n\n"
            f"{bullet_points}\n\n"
            f"Total Unreconciled Variance: ${variance:,.2f}\n\n"
            f"Please reissue a corrected invoice reflecting our contractually agreed purchase order pricing, "
            f"or provide credit memo reference so we may release payment.\n\n"
            f"Thank you,\n"
            f"Allied Welding Systems Accounts Payable\n"
            f"ap@alliedweldingsystems.com | (800) 555-ALLIED"
        )

    def get_reconciliation_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Returns historical reconciliation records from DB with in-memory fallback."""
        try:
            with SessionLocal() as db:
                records = DatabaseService.get_reconciliation_history(db, limit=limit)
                if records:
                    return records
        except Exception:
            pass
        return self._history[:limit]


# Global instance
reconciliation_service = ReconciliationService()
