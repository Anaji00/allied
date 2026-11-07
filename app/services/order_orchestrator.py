"""Central order orchestration service executing dual-sided transactions (1 SO -> Split POs)."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.core.exceptions import DualSidedTransactionError
from app.core.logging import logger
from app.models.orders import (
    CustomerDetails,
    DualSidedResult,
    InboundOrderRequest,
    MasterOrder,
    OrderArchetype,
    OrderLineItem,
    OrderStatus,
    ShippingDetails,
)
from app.models.sage import (
    SagePurchaseOrder,
    SagePurchaseOrderLine,
    SageSalesOrder,
    SageSalesOrderLine,
)
from app.services.agent_memory_cache import agent_memory_cache
from app.services.monday_client import MondayClient
from app.services.mtr_service import MTRService
from app.services.packing_slips import PackingSlipGenerator
from app.services.sage_odbc import SageODBCReader
from app.services.sage_sdk_writer import SageSDKWriter
from app.services.sales_service import SalesService
from app.services.entity_matcher import EntityMatcher
from app.core.container import get_entity_matcher, get_monday_client, get_mtr_service, get_packing_slip_generator


# Destination markers used to route an order to its fulfilling facility.
_CANADA_MARKERS = ("CANADA", ", AB", " AB ", "ALBERTA", "EDMONTON", "ONTARIO", ", ON ", "QUEBEC")
_CHINA_MARKERS = ("CHINA", "SHANDONG", "ZIBO")


class OrderOrchestrator:
    """Orchestrates ingestion, catalog matching, math, dual-sided generation, and compliance."""

    def __init__(
        self,
        entity_matcher: Optional[EntityMatcher] = None,
        odbc_reader: Optional[SageODBCReader] = None,
        sdk_writer: Optional[SageSDKWriter] = None,
        packing_slips: Optional[PackingSlipGenerator] = None,
        monday_client: Optional[MondayClient] = None,
        mtr_service: Optional[MTRService] = None,
        sales_service: Optional[SalesService] = None,
    ):
        self.entity_matcher = entity_matcher or get_entity_matcher()
        self.odbc_reader = odbc_reader or SageODBCReader(self.entity_matcher)
        self.sdk_writer = sdk_writer or SageSDKWriter()
        self.packing_slips = packing_slips or get_packing_slip_generator()
        self.monday_client = monday_client or get_monday_client()
        self.mtr_service = mtr_service or get_mtr_service()
        self.sales_service = sales_service or SalesService(matcher=self.entity_matcher)
        # Shared across orchestrators so a price approved in the operations
        # console unblocks every entry point, not just the one that raised it.

    @staticmethod
    def resolve_facility_and_currency(shipping_address: str, vendors: List[str]) -> Tuple[str, str]:
        """Derives the fulfilling facility and transaction currency for an order.

        MasterOrder previously kept its "HQ"/"USD" field defaults because nothing
        ever set them, so a Canadian shipment was recorded, exported and
        customs-declared as a domestic US order.
        """
        address = (shipping_address or "").upper()

        # Destination country drives the facility that fulfils the order.
        if any(marker in address for marker in _CANADA_MARKERS):
            facility = "EDMONTON"
        elif any(marker in address for marker in _CHINA_MARKERS) or "ZIBO" in {v.upper() for v in vendors}:
            facility = "ZIBO"
        else:
            facility = settings.DEFAULT_FACILITY

        currency = settings.FACILITIES.get(facility, {}).get("currency", settings.DEFAULT_CURRENCY)
        if currency not in settings.SUPPORTED_CURRENCIES:
            currency = settings.DEFAULT_CURRENCY
        return facility, currency

    @staticmethod
    def _find_existing_order(customer_po_number: str, customer_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Returns an already-committed order for this customer PO, if any.

        Deliberately queries the database only. The in-memory cache also holds
        seeded historical reference orders for the AI copilot, which are not
        transactions this system committed -- treating those as duplicates
        would block legitimate new orders that reuse a historical PO number.
        """
        po = (customer_po_number or "").strip().upper()
        if not po:
            return None

        try:
            from sqlalchemy import func

            from app.core.database import SessionLocal
            from app.models.db_models import OrderDB

            with SessionLocal() as db:
                # Normalised on both sides: " po-1 " and "PO-1" are one PO.
                query = db.query(OrderDB).filter(func.upper(func.trim(OrderDB.customer_po_number)) == po)
                if customer_id:  # two customers can use the same PO number
                    query = query.filter(OrderDB.customer_id == str(customer_id))
                row = query.first()
                if row:
                    return {
                        "order_id": row.order_id,
                        "sales_order_number": row.sales_order_number,
                        "customer_po_number": row.customer_po_number,
                        "customer_id": row.customer_id,
                        "purchase_orders": row.purchase_orders or {},
                        "total_revenue": row.total_revenue,
                        "total_expense": row.total_expense,
                        "gross_margin_pct": row.gross_margin_pct,
                        "packing_slip_path": row.packing_slip_path,
                        "facility": row.facility,
                        "currency": row.currency,
                    }
        except Exception as exc:
            # A lookup failure must not block a legitimate new order.
            logger.warning(f"Could not check for an existing order on PO {po}: {exc}")
        return None

    @staticmethod
    def _result_from_cached(order: Dict[str, Any]) -> DualSidedResult:
        """Rebuilds the result for an order that was already committed."""
        return DualSidedResult(
            order_id=order.get("order_id", ""),
            customer_po_number=order.get("customer_po_number", ""),
            sales_order_number=order.get("sales_order_number", ""),
            purchase_orders=order.get("purchase_orders") or {},
            customer_id=order.get("customer_id", ""),
            total_revenue=Decimal(str(order.get("total_revenue") or 0)),
            total_expense=Decimal(str(order.get("total_expense") or 0)),
            gross_margin_pct=Decimal(str(order.get("gross_margin_pct") or 0)),
            packing_slip_path=order.get("packing_slip_path"),
            facility=order.get("facility", "HQ"),
            currency=order.get("currency", "USD"),
            status=OrderStatus.SYNCED_TO_SAGE,
            messages=[
                f"Customer PO #{order.get('customer_po_number')} was already processed as "
                f"Sales Order #{order.get('sales_order_number')}. No duplicate was created."
            ],
        )

    def process_inbound_order(
        self,
        request: InboundOrderRequest,
        pulse_id: Optional[int] = None,
        actor_email: str = "system",
        board_id: Optional[Any] = None,
    ) -> DualSidedResult:
        """
        Main pipeline:
        1. Validate non-empty payload.
        2. Resolve customer account and terms.
        3. Resolve each line item against catalog & match vendor.
        4. Determine order archetype.
        5. Validate margin & compliance rules.
        6. Build Sage Sales Order & Split Vendor Purchase Orders (1 SO -> up to 3+ POs).
        7. Commit dual-sided transaction via Sage SDK.
        8. Generate white-label packing slip PDF.
        9. Return DualSidedResult.
        """
        if not request.line_items:
            raise DualSidedTransactionError("Inbound order must contain at least one line item.")
        from app.core.keyed_lock import keyed_lock

        # The same PO arriving twice at once (the email and the webhook, a double submit) waits
        # here for the first; it then finds the order already committed and returns that.
        # Keyed by the matched customer, as the duplicate check is, so two spellings of one
        # customer ("Owens Corning", "Owens Corning Insulation") share the lock.
        matched, _ = self.entity_matcher.match_customer(request.customer_name)
        with keyed_lock("customer-po", f"{matched.get('customer_id') or request.customer_name}|{request.customer_po_number}"):
            return self._process(request, pulse_id, actor_email, board_id)

    def _process(self, request: InboundOrderRequest, pulse_id: Optional[int], actor_email: str,
                 board_id: Optional[Any]) -> DualSidedResult:
        # Idempotency: a customer PO is committed once. The webhook redelivers,
        # the email daemon can see the same message twice, and users retry
        # submissions -- each of which previously raised a second sales order
        # and a second set of vendor POs for the same purchase.
        matched_customer, _ = self.entity_matcher.match_customer(request.customer_name)
        existing = self._find_existing_order(request.customer_po_number, matched_customer.get("customer_id"))
        if existing:
            logger.info(
                f"Customer PO #{request.customer_po_number} is already committed as "
                f"SO #{existing.get('sales_order_number')}; returning the existing order."
            )
            return self._result_from_cached(existing)

        order_id = str(uuid.uuid4())[:8].upper()
        logger.info(f"Processing Inbound Order [{order_id}] for PO #{request.customer_po_number}")

        # 1. Resolve Customer
        cust_record, cust_conf = self.entity_matcher.match_customer(request.customer_name)
        customer_id = cust_record["customer_id"]
        customer_name = cust_record["customer_name"]
        billing_address = cust_record.get("billing_address") or request.shipping_address
        payment_terms = request.payment_terms or cust_record.get("payment_terms", "Net 30 Days")
        sales_rep = cust_record.get("sales_rep", "Karissa M. Rakestraw")

        customer_details = CustomerDetails(
            customer_id=customer_id,
            customer_name=customer_name,
            billing_address=billing_address,
            email=cust_record.get("email"),
            contact_name=cust_record.get("contact_name"),
            payment_terms=payment_terms,
            sales_rep=sales_rep,
        )

        shipping_details = ShippingDetails(
            recipient_name=request.recipient_name or customer_name,
            shipping_address=request.shipping_address,
            shipping_method=request.shipping_method or cust_record.get("shipping_method", "MOTOR FRT"),
            blind_shipping_required=request.blind_shipping,
        )

        # 2. Resolve Line Items
        resolved_lines: List[OrderLineItem] = []
        has_drawings = False
        collected_heat_numbers: List[str] = []

        for raw_item in request.line_items:
            if raw_item.quantity <= Decimal("0.00"):
                raise DualSidedTransactionError(
                    f"Line item quantity must be strictly positive: {raw_item.quantity}"
                )

            # Query matcher
            search_query = raw_item.item_id or raw_item.raw_item_text
            item_record, item_conf = self.entity_matcher.match_item(search_query)

            matched_item_id = item_record["item_id"]
            preferred_vendor = item_record.get("preferred_vendor", "STOCK")
            std_cost = Decimal(str(item_record.get("standard_cost", "0.00")))
            base_price = Decimal(str(item_record.get("base_price", "0.00")))
            uom = item_record.get("uom", "EA")

            # If unit price not provided in request, use catalog base price
            unit_price = raw_item.unit_price if raw_item.unit_price > Decimal("0.00") else base_price
            unit_cost = raw_item.unit_cost if (raw_item.unit_cost and raw_item.unit_cost > Decimal("0.00")) else std_cost

            if raw_item.drawing_ref or "DWG" in raw_item.raw_item_text.upper():
                has_drawings = True

            # Collect heat numbers for CofC generation
            if hasattr(raw_item, 'heat_number') and raw_item.heat_number:
                collected_heat_numbers.append(raw_item.heat_number)

            resolved_line = OrderLineItem(
                item_id=matched_item_id,
                raw_item_text=raw_item.raw_item_text,
                description=item_record.get("description", raw_item.raw_item_text),
                quantity=raw_item.quantity,
                unit_price=unit_price,
                unit_cost=unit_cost,
                uom=uom,
                preferred_vendor=preferred_vendor,
                drawing_ref=raw_item.drawing_ref,
                revision=raw_item.revision,
                cut_length_inches=raw_item.cut_length_inches,
                confidence_score=item_conf,
            )
            resolved_lines.append(resolved_line)

        # 2c. Partition resolved lines by vendor
        vendor_partition: Dict[str, List[OrderLineItem]] = {}
        for resolved_line in resolved_lines:
            v = resolved_line.preferred_vendor or "STOCK"
            if v not in vendor_partition:
                vendor_partition[v] = []
            vendor_partition[v].append(resolved_line)

        # 3. Determine Order Archetype
        if has_drawings:
            archetype = OrderArchetype.BLUEPRINT_ASSEMBLY
        elif any(v in ("ZIBO",) for v in vendor_partition.keys()):
            archetype = OrderArchetype.CROSS_BORDER
        elif any(v != "STOCK" for v in vendor_partition.keys()):
            archetype = OrderArchetype.BLIND_DROPSHIP
        else:
            archetype = OrderArchetype.STANDARD_STOCK

        facility, currency = self.resolve_facility_and_currency(
            shipping_details.shipping_address, list(vendor_partition.keys())
        )

        master_order = MasterOrder(
            order_id=order_id,
            customer_po_number=request.customer_po_number,
            po_date=request.po_date or date.today(),
            customer=customer_details,
            shipping=shipping_details,
            line_items=resolved_lines,
            archetype=archetype,
            internal_notes=request.notes,
            status=OrderStatus.PROCESSING,
            facility=facility,
            currency=currency,
        )

        # 4. Validate Gross Profit Margin Compliance
        margin_pct = master_order.gross_margin_pct
        if master_order.total_revenue > Decimal("0.00") and margin_pct < Decimal(str(settings.MIN_GROSS_MARGIN_PCT)):
            logger.warning(
                f"Order [{order_id}] margin {margin_pct:.2f}% is below minimum threshold {settings.MIN_GROSS_MARGIN_PCT}%"
            )

        # 5. Build Sage Sales Order (Revenue Side)
        so_lines = [
            SageSalesOrderLine(
                item_id=item.item_id or "CUSTOM",
                description=item.description or item.raw_item_text,
                quantity=item.quantity,
                unit_price=item.unit_price,
                amount=(item.quantity * item.unit_price),
                uom=item.uom,
            )
            for item in resolved_lines
        ]

        sales_order_dto = SageSalesOrder(
            sales_order_number="",  # Assigned by SDK
            customer_id=customer_id,
            customer_po_number=request.customer_po_number,
            order_date=master_order.po_date,
            payment_terms=payment_terms,
            sales_rep=sales_rep,
            ship_to_name=shipping_details.recipient_name,
            ship_to_address=shipping_details.shipping_address,
            bill_to_address=customer_details.billing_address or shipping_details.shipping_address,
            lines=so_lines,
            subtotal=master_order.total_revenue,
            total_amount=master_order.total_revenue,
            internal_notes=f"Allied Universal Bridge Order {order_id}",
        )

        # 6. Build Split Sage Purchase Orders (Expense Side) - 1 SO -> up to 3+ POs
        purchase_orders_to_create: List[SagePurchaseOrder] = []

        for vendor_code, vendor_lines in vendor_partition.items():
            if vendor_code == "STOCK":
                # Internal stock / engineering service, no external PO needed
                continue

            vend_record = self.entity_matcher.vendors_cache.get(vendor_code, {})
            vend_name = vend_record.get("vendor_name", f"Vendor {vendor_code}")
            vend_address = vend_record.get("vendor_address", "USA")
            vend_terms = vend_record.get("payment_terms", "Net 30 Days")
            special_inst = vend_record.get("po_special_instructions", "BLIND SHIPMENT ONLY.")

            po_lines = [
                SagePurchaseOrderLine(
                    item_id=line.item_id or "CUSTOM",
                    description=line.description or line.raw_item_text,
                    quantity=line.quantity,
                    unit_cost=line.unit_cost or Decimal("0.00"),
                    amount=(line.quantity * (line.unit_cost or Decimal("0.00"))),
                    uom=line.uom,
                    drawing_reference=line.drawing_ref,
                )
                for line in vendor_lines
            ]

            po_total = sum(l.amount for l in po_lines)

            # Blind shipping instructions
            notes = [
                f"ORDER REF: ALLIED SO FOR PO {request.customer_po_number}",
                special_inst,
                "CRITICAL: BLIND SHIPMENT. DO NOT INCLUDE VENDOR INVOICE OR PRICING IN PACKING CRATES.",
            ]

            po_dto = SagePurchaseOrder(
                purchase_order_number="",
                vendor_id=vendor_code,
                order_date=master_order.po_date,
                payment_terms=vend_terms,
                vendor_name=vend_name,
                vendor_address=vend_address,
                lines=po_lines,
                total_amount=po_total,
                special_notes=notes,
                blind_shipment=True,
            )
            purchase_orders_to_create.append(po_dto)

        # 7. Commit Dual-Sided Transaction via SDK
        so_num, po_nums = self.sdk_writer.execute_dual_sided_transaction(
            sales_order=sales_order_dto,
            purchase_orders=purchase_orders_to_create,
        )

        # 8. Generate Sanitized White-Label PDF Packing Slip
        # Queued with the order's details and made now, as its own job: a slip that can't be made is
        # retried and flagged, and never stops the order. The slip shows the customer's PO.
        from app.services import packing_slip_queue

        pdf_path = packing_slip_queue.enqueue_and_run(self.packing_slips, master_order, so_num)

        # 9. Auto-Generate Certificate of Conformance (CofC)
        cofc_id = None
        if not collected_heat_numbers:
            # Generate default heat numbers for MTR-relevant items
            for line in resolved_lines:
                item_upper = (line.item_id or "").upper()
                if any(k in item_upper for k in ["PLATE", "BEAM", "CHAN", "BAR", "OC", "KN", "HSS", "PIPE"]):
                    collected_heat_numbers.append(f"HT-{line.item_id}-AUTO")

        if collected_heat_numbers:
            try:
                cofc = self.mtr_service.generate_cofc(
                    sales_order_number=so_num,
                    customer_id=customer_id,
                    customer_name=customer_name,
                    customer_po_number=request.customer_po_number,
                    heat_numbers=collected_heat_numbers,
                )
                cofc_id = cofc.cofc_id
                logger.info(f"Auto-generated CofC [{cofc_id}] for SO #{so_num}")
            except Exception as exc:
                logger.warning(f"CofC generation skipped for SO #{so_num}: {exc}")

        # 10. (Upsells removed)

        # 11. Order record: persisted to the database and mirrored into Agent
        # Memory for AI Copilot retrieval.
        cache_data: Dict[str, Any] = {
            "order_id": order_id,
            "sales_order_number": so_num,
            "customer_po_number": request.customer_po_number,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "po_date": str(master_order.po_date),
            "payment_terms": payment_terms,
            "shipping_method": shipping_details.shipping_method,
            "shipping_address": shipping_details.shipping_address,
            "blind_shipping": shipping_details.blind_shipping_required,
            "drawing_reference": next((l.drawing_ref for l in resolved_lines if l.drawing_ref), "N/A"),
            "heat_numbers": collected_heat_numbers,
            "line_items": [
                {
                    "item_id": l.item_id,
                    "description": l.description or l.raw_item_text,
                    "quantity": float(l.quantity),
                    "unit_price": float(l.unit_price),
                    "unit_cost": float(l.unit_cost or 0),
                    "preferred_vendor": l.preferred_vendor,
                }
                for l in resolved_lines
            ],
            "purchase_orders": po_nums,
            "total_revenue": float(master_order.total_revenue),
            "total_expense": float(master_order.total_expense),
            "gross_margin_pct": float(master_order.gross_margin_pct.quantize(Decimal("0.01"))),
            "facility": master_order.facility,
            "currency": master_order.currency,
            "archetype": archetype.value,
            "packing_slip_path": str(pdf_path),
            "internal_notes": request.notes,
            "status": "SYNCED_TO_SAGE",
            # Ties the order to the board item that raised it, so later
            # status changes on that item find this order directly.
            "monday_item_id": pulse_id,
            "monday_board_id": board_id,
        }

        # The cache is best-effort. A cache failure used to skip persistence
        # while still reporting success, so the order existed only in Sage.
        try:
            agent_memory_cache.cache_order(cache_data)
            logger.info(f"Cached order [{order_id}] (SO #{so_num}) in Agent Memory for AI Copilot retrieval")
        except Exception as exc:
            logger.warning(f"Order caching skipped for [{order_id}]: {exc}")

        # 11b. Persist the order so it survives a restart. The in-memory cache
        # alone meant the orders table stayed empty and every downstream reader
        # (Sage export, document numbering) lost history on every restart.
        try:
            from app.core.database import SessionLocal
            from app.services.database_service import DatabaseService

            with SessionLocal() as db:
                DatabaseService.persist_order(db, cache_data, actor_email=actor_email)
        except Exception as exc:
            # Not survivable silently: without the row, the next trigger
            # passes the duplicate-PO check and raises a second sales
            # order, and later status changes cannot find this one.
            logger.error(f"Failed to persist order [{order_id}] to database: {exc}")
            raise DualSidedTransactionError(
                f"Sales Order #{so_num} was numbered but could not be saved; "
                f"check the database before retrying PO {request.customer_po_number}.",
                details={"sales_order_number": so_num, "purchase_orders": po_nums},
            )

        # 12. Formulate Result
        messages = [
            f"Dual-sided generation complete: Sales Order #{so_num} created for {customer_name}.",
            f"{len(po_nums)} split Purchase Order(s) generated: {po_nums}.",
            f"Gross Margin: {master_order.gross_margin_pct:.2f}%.",
            f"Packing slip generated: {pdf_path.name}.",
        ]
        if cofc_id:
            messages.append(f"📋 Certificate of Conformance {cofc_id} auto-generated with {len(collected_heat_numbers)} heat cert(s).")

        result = DualSidedResult(
            order_id=order_id,
            customer_po_number=request.customer_po_number,
            sales_order_number=so_num,
            purchase_orders=po_nums,
            customer_id=customer_id,
            total_revenue=master_order.total_revenue,
            total_expense=master_order.total_expense,
            gross_margin_pct=master_order.gross_margin_pct.quantize(Decimal("0.01")),
            packing_slip_path=str(pdf_path),
            facility=master_order.facility,
            currency=master_order.currency,
            status=OrderStatus.SYNCED_TO_SAGE,
            messages=messages,
        )

        logger.info(f"Order [{order_id}] completed successfully: SO #{so_num}, Split POs: {po_nums}")
        return result
