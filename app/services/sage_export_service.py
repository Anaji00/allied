"""Direct Sage 50 US (Peachtree) ERP CSV Batch Export Adapter.

Formats sales orders and multi-vendor purchase orders into official Sage 50
import CSV schemas with multi-facility, tri-currency (USD, CAD, CNY), and
granular General Ledger (GL) account assignment.
"""

import csv
import io
import re
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from app.config import settings
from app.core.logging import logger
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.services.database_service import DatabaseService
from app.services.agent_memory_cache import agent_memory_cache


class SageExportSummary(BaseModel):
    """Summary metrics of generated Sage 50 CSV export batch."""
    sales_orders_count: int
    purchase_orders_count: int
    total_sales_volume: Decimal
    total_purchase_volume: Decimal
    currency: str = "USD"
    facility: str = "ALL"
    generated_at: str
    export_files: List[str] = Field(default_factory=list)


def split_shipping_address(address: Optional[str]) -> Tuple[str, str, str, str]:
    """Splits a free-text shipping address into (line1, city, state, postcode).

    Sage 50's import schema has discrete city/state/zip columns. Every exported
    row previously carried the literal "Industrial Park / AL / 36854" regardless
    of destination, so Canadian and out-of-state shipments imported into the ERP
    with an Alabama address.
    """
    text = (address or "").strip()
    if not text:
        return "", "", "", ""

    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) == 1:
        return parts[0], "", "", ""

    line1 = parts[0]
    # A trailing country token ("USA", "Canada") is not part of the locality.
    if parts[-1].upper() in {"USA", "US", "U.S.A.", "CANADA", "CHINA", "CN", "CA"} and len(parts) > 2:
        parts = parts[:-1]

    tail = parts[-1]
    city = parts[-2] if len(parts) >= 2 else ""

    # Trailing region + postcode, e.g. "TX 76065" or "AB T6S 1A1".
    region_match = re.match(r"^([A-Za-z]{2})\s+(.+)$", tail)
    if region_match:
        state, postcode = region_match.group(1).upper(), region_match.group(2).strip()
    else:
        state, postcode = ("", tail) if any(ch.isdigit() for ch in tail) else (tail.upper(), "")

    if len(parts) > 3:
        line1 = ", ".join(parts[:-2])
    return line1, city, state, postcode


_CENT = Decimal("0.01")


def money_line(qty: Any, unit: Any) -> Tuple[Decimal, Decimal]:
    """Returns (unit rounded to cents, qty x rounded unit rounded to cents).

    Amount used to be computed from the unrounded price while Unit Price was
    printed rounded, so the two columns could disagree by a cent in Sage.
    """
    unit_rounded = Decimal(str(unit)).quantize(_CENT, rounding=ROUND_HALF_UP)
    amount = (Decimal(str(qty)) * unit_rounded).quantize(_CENT, rounding=ROUND_HALF_UP)
    return unit_rounded, amount


def sage_date(value: Any) -> str:
    """Formats a date the way Sage 50 US import expects it: mm/dd/yy.

    ISO dates (2026-10-27) were written previously; Sage reads those as
    invalid or, worse, reorders the parts.
    """
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%m/%d/%y")
    text = str(value or "").strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%m/%d/%y")
    except ValueError:
        logger.warning(f"Order date '{value}' is not a date; using today for the Sage import.")
        return date.today().strftime("%m/%d/%y")


class SageExportService:
    """Generates standard Peachtree / Sage 50 US import CSVs."""

    def __init__(self):
        # Peachtree / Sage 50 Chart of Accounts
        self.gl_sales_steel = "40000-00-FAB"    # Wear Steel Sales
        self.gl_sales_ceramic = "41000-00-CER"  # Ceramic Composites Sales
        self.gl_cogs_steel = "50000-00-STEEL"   # Steel Material Cost
        self.gl_cogs_ceramic = "51000-00-CER"   # Ceramic Material Cost
        self.gl_freight = "52000-00-FREIGHT"    # Inbound Freight Cost

    def _get_orders(self, facility: Optional[str] = None, only_new: bool = False) -> List[Dict[str, Any]]:
        """Retrieves orders from SQLite database with fallback to in-memory cache.

        only_new leaves out orders already handed to Sage in an earlier file.
        """
        try:
            with SessionLocal() as db:
                # No row cap for only_new: an unexported order older than the
                # newest 200 would otherwise never reach Sage.
                db_orders = DatabaseService.get_all_orders(
                    db, facility=facility, limit=1_000_000 if only_new else 200
                )
                if only_new:
                    return [o for o in db_orders if not o.get("sage_exported_at")]
                if db_orders:
                    return db_orders
        except Exception as e:
            logger.warning(f"Could not read orders from database: {e}")
            if only_new:
                # The cache has no export marks; falling back would hand Sage
                # every order again.
                raise

        # Fallback
        cached = agent_memory_cache.get_all_orders()
        if facility:
            cached = [o for o in cached if o.get("facility", "HQ").upper() == facility.upper()]
        return cached

    def _resolve_sales_gl(self, item_id: str) -> str:
        """Determines appropriate Peachtree revenue account based on item class."""
        sku = (item_id or "").upper()
        if "CERAMIC" in sku or "HOSE" in sku or "ALUMINA" in sku:
            return self.gl_sales_ceramic
        return self.gl_sales_steel

    def _resolve_cogs_gl(self, item_id: str) -> str:
        """Determines appropriate Peachtree COGS expense account."""
        sku = (item_id or "").upper()
        if "CERAMIC" in sku or "HOSE" in sku or "ALUMINA" in sku:
            return self.gl_cogs_ceramic
        return self.gl_cogs_steel

    @staticmethod
    def claim_for_export(sales_order_numbers: List[str]) -> List[str]:
        """Atomically stamps unexported orders and returns exactly those stamped.

        Each row is claimed with a conditional UPDATE ... WHERE sage_exported_at
        IS NULL, so two overlapping exports can never both include an order:
        whichever claims it first exports it, the other leaves it out.
        """
        from app.models.db_models import OrderDB

        if not sales_order_numbers:
            return []
        now = datetime.now(timezone.utc)
        claimed: List[str] = []
        with SessionLocal() as db:
            for so_num in dict.fromkeys(sales_order_numbers):
                n = (
                    db.query(OrderDB)
                    .filter(OrderDB.sales_order_number == so_num, OrderDB.sage_exported_at.is_(None))
                    .update({OrderDB.sage_exported_at: now}, synchronize_session=False)
                )
                if n:
                    claimed.append(so_num)
            db.commit()
        return claimed

    @staticmethod
    def mark_exported(sales_order_numbers: List[str]) -> int:
        """Stamps orders as handed to Sage so the next only_new export skips them."""
        from app.models.db_models import OrderDB

        if not sales_order_numbers:
            return 0
        with SessionLocal() as db:
            n = (
                db.query(OrderDB)
                .filter(OrderDB.sales_order_number.in_(sales_order_numbers), OrderDB.sage_exported_at.is_(None))
                .update({OrderDB.sage_exported_at: datetime.now(timezone.utc)}, synchronize_session=False)
            )
            db.commit()
        return n

    def generate_sales_orders_csv(
        self,
        customer_filter: Optional[str] = None,
        facility: Optional[str] = None,
        only_new: bool = False,
        mark_exported: bool = False,
    ) -> str:
        """
        Generates official Sage 50 Sales Order import CSV format:
        Customer ID,SO Number,Date,Ship to Name,Ship to Address-Line One,Ship to City,Ship to State,Ship to Zipcode,Terms,Item ID,Description,Quantity,Unit Price,Amount,GL Account
        """
        orders = self._get_orders(facility=facility, only_new=only_new)
        if customer_filter:
            orders = [o for o in orders if customer_filter.lower() in o.get("customer_name", "").lower()]

        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")

        # Rows are built per order first and written once it is known which
        # orders this export owns (see claim_for_export below).
        order_rows: List[Tuple[str, List[List[Any]]]] = []

        # Sage 50 US Sales Order Header
        writer.writerow([
            "Customer ID",
            "SO Number",
            "Date",
            "Customer PO",
            "Number of Distributions",
            "SO Distribution",
            "Ship to Name",
            "Ship to Address-Line One",
            "Ship to City",
            "Ship to State",
            "Ship to Zipcode",
            "Terms",
            "Item ID",
            "Description",
            "Quantity",
            "Unit Price",
            "Amount",
            "GL Account",
        ])

        for o in orders:
            rows: List[List[Any]] = []
            cust_id = o.get("customer_id") or o.get("customer_name", "CUST-GEN")
            so_num = o.get("sales_order_number", "SO-12000")
            po_date = sage_date(o.get("po_date"))
            # Sage groups rows into one document by number and needs every row
            # to repeat how many lines the document has.
            exportable = [
                ln for ln in o.get("line_items", [])
                if ln.get("item_id") and ln.get("unit_price") is not None
            ]
            distribution = 0
            ship_name = o.get("customer_name") or "Plant Receiving"
            ship_line1, ship_city, ship_state, ship_zip = split_shipping_address(
                o.get("shipping_address")
            )
            terms = o.get("payment_terms", "Net 30 Days")

            for line in o.get("line_items", []):
                item_id = line.get("item_id")
                if not item_id:
                    logger.warning(
                        f"Skipping a line on {so_num}: no item_id. Defaulting it to a "
                        f"wear-plate SKU would export the wrong part to Sage."
                    )
                    continue
                desc = line.get("description") or item_id
                qty = line.get("quantity") or 1
                price = line.get("unit_price")
                if price is None:
                    logger.warning(f"Skipping {item_id} on {so_num}: no unit price on the order line.")
                    continue
                unit_price, amount = money_line(qty, price)
                gl_account = self._resolve_sales_gl(item_id)
                distribution += 1

                rows.append([
                    cust_id,
                    so_num,
                    po_date,
                    o.get("customer_po_number") or "",
                    len(exportable),
                    distribution,
                    ship_name,
                    ship_line1,
                    ship_city,
                    ship_state,
                    ship_zip,
                    terms,
                    item_id,
                    desc,
                    qty,
                    f"{unit_price:.2f}",
                    f"{amount:.2f}",
                    gl_account,
                ])
            if distribution:
                order_rows.append((so_num, rows))

        if mark_exported:
            # Marking happens here, when the file is generated, not when it is
            # delivered. Claiming first and exporting only what was claimed
            # makes the mark atomic: an overlapping run cannot also export an
            # order, and nothing is marked that is not in this file.
            claimed = set(self.claim_for_export([so for so, _ in order_rows]))
            order_rows = [(so, rows) for so, rows in order_rows if so in claimed]

        for _, rows in order_rows:
            writer.writerows(rows)

        csv_content = output.getvalue()
        output.close()

        # Audit log entry
        audit_log(
            action="SAGE_50_EXPORT",
            resource_type="SAGE_SO_CSV",
            detail=(
                f"Generated Sage 50 SO Import CSV ({len(order_rows)} orders, facility={facility or 'ALL'}"
                + (", marked exported" if mark_exported else "")
                + ")"
            ),
        )

        return csv_content

    def generate_purchase_orders_csv(
        self,
        vendor_filter: Optional[str] = None,
        facility: Optional[str] = None,
    ) -> str:
        """
        Generates official Sage 50 Purchase Order import CSV format:
        Vendor ID,PO Number,Date,Ship to Name,Ship to Address-Line One,Ship to City,Ship to State,Ship to Zipcode,Item ID,Description,Quantity,Unit Cost,Amount,GL Account
        """
        orders = self._get_orders(facility=facility)

        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")

        # Sage 50 US Purchase Order Header
        writer.writerow([
            "Vendor ID",
            "PO Number",
            "Date",
            "Ship to Name",
            "Ship to Address-Line One",
            "Ship to City",
            "Ship to State",
            "Ship to Zipcode",
            "Item ID",
            "Description",
            "Quantity",
            "Unit Cost",
            "Amount",
            "GL Account",
        ])

        po_lines_written = 0
        for o in orders:
            po_date = sage_date(o.get("po_date"))
            ship_name = o.get("customer_name") or "Allied Facility"
            ship_line1, ship_city, ship_state, ship_zip = split_shipping_address(
                o.get("shipping_address")
            )
            issued_pos = o.get("purchase_orders") or {}

            for line in o.get("line_items", []):
                vend_id = line.get("preferred_vendor")
                if not vend_id:
                    logger.warning(
                        f"Skipping a line on {o.get('sales_order_number')}: no vendor. "
                        f"Defaulting it to NIBROCK would raise a PO against the wrong supplier."
                    )
                    continue
                if vend_id.upper() == "STOCK":
                    continue
                if vendor_filter and vend_id.upper() != vendor_filter.upper():
                    continue

                item_id = line.get("item_id")
                cost = line.get("unit_cost")
                if not item_id or cost is None:
                    logger.warning(
                        f"Skipping a {vend_id} line on {o.get('sales_order_number')}: "
                        f"missing item_id or unit_cost."
                    )
                    continue
                desc = line.get("description") or item_id
                qty = line.get("quantity") or 1
                unit_cost, amount = money_line(qty, cost)
                # Use the PO number Sage actually issued for this vendor, so the
                # export reconciles against the 3-way match. Synthesising
                # "PO-<vendor>-<customerPO>" invented a number that matched nothing.
                po_num = issued_pos.get(vend_id) or issued_pos.get(vend_id.upper())
                if not po_num:
                    logger.warning(
                        f"Skipping {item_id} for {vend_id} on {o.get('sales_order_number')}: "
                        f"no issued purchase order number on the order record."
                    )
                    continue
                gl_account = self._resolve_cogs_gl(item_id)

                writer.writerow([
                    vend_id,
                    po_num,
                    po_date,
                    ship_name,
                    ship_line1,
                    ship_city,
                    ship_state,
                    ship_zip,
                    item_id,
                    desc,
                    qty,
                    f"{unit_cost:.2f}",
                    f"{amount:.2f}",
                    gl_account,
                ])
                po_lines_written += 1

        csv_content = output.getvalue()
        output.close()

        # Audit log entry
        audit_log(
            action="SAGE_50_EXPORT",
            resource_type="SAGE_PO_CSV",
            detail=f"Generated Sage 50 PO Import CSV ({po_lines_written} lines, vendor={vendor_filter or 'ALL'})",
        )

        return csv_content

    @staticmethod
    def to_usd(amount: Decimal, currency: str) -> Decimal:
        """Converts an amount in a supported currency to USD.

        Uses the configured FX_RATES table, which was previously declared in
        settings but never read anywhere.
        """
        code = (currency or settings.DEFAULT_CURRENCY).upper()
        rate = settings.FX_RATES.get(code)
        if not rate:
            logger.warning(f"No FX rate configured for '{code}'; treating the amount as USD.")
            return amount
        # FX_RATES holds units-per-USD, so dividing converts back to USD.
        return (amount / Decimal(str(rate))).quantize(Decimal("0.01"))

    def get_batch_summary(self, facility: Optional[str] = None) -> SageExportSummary:
        """Computes summary stats for the current export batch.

        Totals are normalised to USD. Adding raw CAD and CNY figures to USD ones
        (as this previously did) produced a single meaningless number, and the
        reported currency was hardcoded to USD regardless of the orders.
        """
        orders = self._get_orders(facility=facility)
        total_sales = Decimal("0.00")
        total_purchases = Decimal("0.00")
        po_count = 0
        currencies = set()

        for o in orders:
            order_currency = (o.get("currency") or settings.DEFAULT_CURRENCY).upper()
            currencies.add(order_currency)

            revenue = Decimal(str(o.get("total_revenue") or o.get("total_amount") or 0))
            total_sales += self.to_usd(revenue, order_currency)

            for line in o.get("line_items", []):
                vend_id = (line.get("preferred_vendor") or "").upper()
                if not vend_id or vend_id == "STOCK":
                    continue
                cost = line.get("unit_cost")
                if cost is None:
                    continue
                qty = Decimal(str(line.get("quantity") or 1))
                total_purchases += self.to_usd(qty * Decimal(str(cost)), order_currency)
                po_count += 1

        # Report the batch currency honestly: USD once normalised, and say so
        # explicitly when the batch spans more than one source currency.
        if len(currencies) > 1:
            batch_currency = f"USD (normalised from {'/'.join(sorted(currencies))})"
        else:
            batch_currency = "USD"

        return SageExportSummary(
            sales_orders_count=len(orders),
            purchase_orders_count=po_count,
            total_sales_volume=total_sales,
            total_purchase_volume=total_purchases,
            currency=batch_currency,
            facility=facility or "ALL",
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            export_files=["SO_IMPORT.CSV", "PO_IMPORT.CSV"],
        )


# Global instance
sage_export_service = SageExportService()
