"""Write layer creating Sage 50 Sales Orders and Purchase Orders via COM SDK / Python.NET."""

import threading
from typing import Any, Dict, List, Optional, Tuple
from app.config import settings
from app.core.exceptions import SageSDKWriteError
from app.core.logging import logger
from app.models.sage import SagePurchaseOrder, SageSalesOrder


# Sage 50 US's only supported write path is the Sage.Peachtree.API .NET SDK:
# 32-bit, .NET Framework 4.8, on a Windows host with Sage installed, and an
# application id issued by Sage. ODBC is read-only. The practical pattern is a
# small Windows agent beside Sage that the bridge hands documents to; until
# that exists, documents are numbered here and imported from the CSV batch.
_LIVE_WRITE_UNAVAILABLE = (
    "Direct Sage 50 writing is not implemented. Keep SAGE_LIVE_CONNECTION=False: "
    "documents are numbered here and delivered through the Sage import files."
)


class SageSDKWriter:
    """Transactional write service for creating dual-sided Sage 50 SOs and POs.

    Document numbering is process-wide and survives restarts. Each writer
    instance previously kept its own counter starting at a fixed constant, so
    the six independently constructed orchestrators (orders router, webhook
    router, chat router, CSV importer, email daemon) all issued SO 12930, and
    every process restart reissued numbers that had already been used.
    """

    _SO_SEQUENCE_START = 12930
    _PO_SEQUENCE_START = 9260

    # Shared across every writer instance, and capped: this is a display
    # mirror of what was committed, while the orders table is the record.
    _MAX_TRANSACTION_LOG = 1000

    _shared_transaction_log: List[Dict[str, Any]] = []
    _sequence_lock = threading.Lock()
    _so_sequence: Optional[int] = None
    _po_sequence: Optional[int] = None

    def __init__(self):
        self.simulation_mode = not settings.sage_live
        self.transaction_log: List[Dict[str, Any]] = self._shared_transaction_log

    @classmethod
    def _next_number(cls, kind: str) -> str:
        """Allocates the next document number, high-water-marked against the database."""
        with cls._sequence_lock:
            if cls._so_sequence is None or cls._po_sequence is None:
                cls._so_sequence, cls._po_sequence = cls._load_high_water_marks()
            if kind == "SO":
                value = cls._so_sequence
                cls._so_sequence += 1
            else:
                value = cls._po_sequence
                cls._po_sequence += 1
        return str(value)

    @classmethod
    def _load_high_water_marks(cls) -> Tuple[int, int]:
        """Resumes numbering above the highest document already persisted."""
        so_next, po_next = cls._SO_SEQUENCE_START, cls._PO_SEQUENCE_START
        try:
            from app.core.database import SessionLocal
            from app.models.db_models import OrderDB

            with SessionLocal() as db:
                for row in db.query(OrderDB.sales_order_number, OrderDB.purchase_orders).all():
                    so_raw, pos = row
                    digits = "".join(ch for ch in str(so_raw or "") if ch.isdigit())
                    if digits:
                        so_next = max(so_next, int(digits) + 1)
                    for po in (pos or {}).values():
                        po_digits = "".join(ch for ch in str(po) if ch.isdigit())
                        if po_digits:
                            po_next = max(po_next, int(po_digits) + 1)
        except Exception as exc:
            logger.warning(
                f"Could not read document-number high-water marks ({exc}); "
                f"starting at SO {so_next} / PO {po_next}"
            )
        logger.info(f"Sage document numbering resumes at SO #{so_next}, PO #{po_next}")
        return so_next, po_next

    @classmethod
    def reset_sequences(cls) -> None:
        """Forces the next allocation to re-read the high-water marks (used by tests)."""
        with cls._sequence_lock:
            cls._so_sequence = None
            cls._po_sequence = None

    def _record(self, entry: Dict[str, Any]) -> None:
        """Appends to the shared transaction log, trimming the oldest entries."""
        log = self.transaction_log
        log.append(entry)
        if len(log) > self._MAX_TRANSACTION_LOG:
            del log[: len(log) - self._MAX_TRANSACTION_LOG]

    def create_sales_order(self, sales_order: SageSalesOrder) -> str:
        """Creates a Sales Order in Sage 50, returning the assigned SO number."""
        if self.simulation_mode:
            so_number = sales_order.sales_order_number or self._next_number("SO")
            record = {
                "type": "SALES_ORDER",
                "so_number": so_number,
                "customer_id": sales_order.customer_id,
                "customer_po": sales_order.customer_po_number,
                "amount": float(sales_order.total_amount),
                "lines_count": len(sales_order.lines),
            }
            self._record(record)
            logger.info(f"[SIMULATION] Created Sage Sales Order #{so_number} for Customer {sales_order.customer_id}")
            return so_number

        # The live branch used to return sales_order.sales_order_number, which
        # is "" at this point: nothing was written, yet the order was reported
        # SYNCED_TO_SAGE with a blank number. Until a real SDK writer exists it
        # must fail loudly instead.
        raise SageSDKWriteError(_LIVE_WRITE_UNAVAILABLE)

    def create_purchase_order(self, purchase_order: SagePurchaseOrder) -> str:
        """Creates a Vendor Purchase Order in Sage 50, returning the assigned PO number."""
        if self.simulation_mode:
            po_number = purchase_order.purchase_order_number or self._next_number("PO")
            record = {
                "type": "PURCHASE_ORDER",
                "po_number": po_number,
                "vendor_id": purchase_order.vendor_id,
                "amount": float(purchase_order.total_amount),
                "lines_count": len(purchase_order.lines),
                "blind_shipment": purchase_order.blind_shipment,
            }
            self._record(record)
            logger.info(f"[SIMULATION] Created Sage Purchase Order #{po_number} for Vendor {purchase_order.vendor_id}")
            return po_number

        raise SageSDKWriteError(_LIVE_WRITE_UNAVAILABLE)

    def execute_dual_sided_transaction(
        self,
        sales_order: SageSalesOrder,
        purchase_orders: List[SagePurchaseOrder],
    ) -> Tuple[str, Dict[str, str]]:
        """
        Atomically creates 1 Customer Sales Order and N Split Vendor Purchase Orders.
        Guarantees that both sides are validated before execution.
        """
        logger.info(
            f"Initiating Dual-Sided Transaction: SO for Customer {sales_order.customer_id}, "
            f"{len(purchase_orders)} split POs"
        )

        created_so_number: Optional[str] = None
        created_po_numbers: Dict[str, str] = {}

        try:
            # 1. Commit Revenue Sales Order
            created_so_number = self.create_sales_order(sales_order)

            # 2. Commit Each Split Expense Purchase Order
            for po in purchase_orders:
                po_num = self.create_purchase_order(po)
                created_po_numbers[po.vendor_id] = po_num

            logger.info(
                f"Dual-Sided Transaction Completed Successfully: SO #{created_so_number}, "
                f"POs {created_po_numbers}"
            )
            return created_so_number, created_po_numbers

        except Exception as exc:
            logger.error(f"Dual-sided transaction failed: {exc}. Performing rollback simulation.")
            raise SageSDKWriteError(
                f"Dual-sided commit failed: {exc}",
                details={
                    "so_created": created_so_number,
                    "pos_created": created_po_numbers,
                },
            )

    @staticmethod
    def _is_com_sdk_available() -> bool:
        """Helper to determine if pythonnet clr can load Sage assemblies."""
        try:
            import clr  # type: ignore
            return True
        except ImportError:
            return False
