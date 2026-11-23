"""Direct Sage 50 US (Peachtree) ERP CSV Batch Export Adapter.

Formats sales orders and multi-vendor purchase orders into official Sage 50
import CSV schemas with multi-facility, tri-currency (USD, CAD, CNY), and
granular General Ledger (GL) account assignment.
"""

import csv
import io
import re
import threading
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
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
    purchase_by_currency: Dict[str, Decimal] = Field(default_factory=dict)   # what the supplier lines add up to in each order's own currency
    sales_label: str = ""                                                   # the sales total in words, per currency, with the converted figure marked "about"
    purchase_label: str = ""
    by_currency: Dict[str, Decimal] = Field(default_factory=dict)      # the sales total in each order's own currency: never one "$" sum of mixed currencies


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
    # A trailing country token is not part of the locality: a known country word, or any word with no digits that follows a part holding the postcode.
    last = parts[-1]
    known = {"USA", "US", "U.S.A.", "CANADA", "CHINA", "CN", "CA", "MEXICO", "MX", "VIETNAM", "VN", "INDIA", "IN", "JAPAN", "JP", "GERMANY", "DE", "UK", "UNITED KINGDOM",
             "KOREA", "KR", "TAIWAN", "TW", "THAILAND", "TH", "BRAZIL", "BR", "AUSTRALIA", "AU", "ITALY", "IT", "FRANCE", "FR", "SPAIN", "ES"}
    if len(parts) > 2 and (last.upper() in known or (not any(ch.isdigit() for ch in last) and len(last) > 3 and any(ch.isdigit() for ch in parts[-2]))):
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


def _dec(value: Any) -> Optional[Decimal]:
    """A plain finite number from an order field, or None. ASCII digits only; text like 'N/A', '$12.50', '1,5' or 'NaN' is not a number."""
    text = str(value).strip() if value is not None else ""
    if not text or not text.isascii():
        return None
    try:
        d = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and abs(d) < Decimal("1e9") else None


def safe_text(value: Any, limit: int = 200) -> str:
    """Text for a Sage import cell: one line, no control characters, and never something a spreadsheet would run as a formula (a leading = + - @)."""
    text = " ".join(re.sub(r"[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]+", " ", str(value if value is not None else "")).split())[:limit]
    return "'" + text if text[:1] in ("=", "+", "-", "@") and text != "-" else text


def sage_date(value: Any) -> str:
    """Formats a date the way Sage 50 US import expects it: mm/dd/yy.

    ISO dates (2026-10-27) were written previously; Sage reads those as
    invalid or, worse, reorders the parts.
    """
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%m/%d/%y")
    text = str(value or "").strip()
    if not text:
        return date.today().strftime("%m/%d/%y")            # an order with no date at all is dated today
    for fmt, size in (("%Y-%m-%d", 10), ("%m/%d/%Y", 10), ("%m/%d/%y", 8)):
        try:
            return datetime.strptime(text[:size] if fmt == "%Y-%m-%d" else text, fmt).strftime("%m/%d/%y")
        except ValueError:
            continue
    raise ValueError(f"The order date '{text[:30]}' is not a date Sage can take (use 2026-11-20 or 11/20/2026).")


def _mark_column(kind: str):
    """The order column that records each kind of export: "so" sales orders, "po" their vendor POs."""
    from app.models.db_models import OrderDB

    marks = {"so": OrderDB.sage_exported_at, "po": OrderDB.sage_po_exported_at}
    if kind not in marks:
        raise ValueError(f"Unknown export kind '{kind}'.")
    return marks[kind]


class SageExportService:
    """Generates standard Peachtree / Sage 50 US import CSVs."""

    def __init__(self):
        # Peachtree / Sage 50 Chart of Accounts
        self.gl_sales_steel = "40000-00-FAB"    # Wear Steel Sales
        self.gl_sales_ceramic = "41000-00-CER"  # Ceramic Composites Sales
        self.gl_cogs_steel = "50000-00-STEEL"   # Steel Material Cost
        self.gl_cogs_ceramic = "51000-00-CER"   # Ceramic Material Cost
        self.gl_freight = "52000-00-FREIGHT"    # Inbound Freight Cost

    _tls = threading.local()

    @property
    def last_skipped(self) -> List[str]:
        """What the last export on THIS thread left out (two exports at once each see their own list)."""
        return self._tls.__dict__.setdefault("skipped", [])

    @last_skipped.setter
    def last_skipped(self, value: List[str]) -> None:
        self._tls.skipped = value

    def _skip(self, why: str) -> None:
        """An order or line left out of a file is said so (log and `last_skipped`), never silently."""
        logger.warning(f"Sage export: {why}")
        self.last_skipped.append(why)

    @staticmethod
    def _rate(currency: Any) -> Decimal:
        """Units of the order's currency per US dollar (1 for USD). A currency with no configured rate is an error, never treated as dollars."""
        code = str(currency or settings.DEFAULT_CURRENCY).upper()
        if code == "USD":
            return Decimal(1)
        rate = _dec(settings.FX_RATES.get(code))
        if not rate or rate <= 0:
            raise ValueError(f"there is no exchange rate for {code[:10]} (set FX_RATES)")
        return rate

    def _get_orders(
        self, facility: Optional[str] = None, only_new: bool = False, kind: str = "so"
    ) -> List[Dict[str, Any]]:
        """Retrieves orders from SQLite database with fallback to in-memory cache.

        only_new leaves out orders already handed to Sage in an earlier file of
        this kind ("so" for sales orders, "po" for their vendor POs).
        """
        mark = _mark_column(kind).key
        try:
            with SessionLocal() as db:
                # No row cap for only_new: an unexported order older than the
                # newest 200 would otherwise never reach Sage.
                db_orders = DatabaseService.get_all_orders(
                    db, facility=facility, limit=1_000_000 if only_new else 200
                )
                if only_new:
                    return [o for o in db_orders if not o.get(mark)]
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
    def claim_for_export(sales_order_numbers: List[str], kind: str = "so") -> List[str]:
        """Atomically stamps unexported orders and returns exactly those stamped.

        Each row is claimed with a conditional UPDATE ... WHERE <mark> IS NULL,
        so two overlapping exports can never both include an order: whichever
        claims it first exports it, the other leaves it out.
        """
        from app.models.db_models import OrderDB

        if not sales_order_numbers:
            return []
        column = _mark_column(kind)
        now = datetime.now(timezone.utc)
        claimed: List[str] = []
        with SessionLocal() as db:
            for so_num in dict.fromkeys(sales_order_numbers):
                n = (
                    db.query(OrderDB)
                    .filter(OrderDB.sales_order_number == so_num, column.is_(None))
                    .update({column: now}, synchronize_session=False)
                )
                if n:
                    claimed.append(so_num)
            db.commit()
        return claimed

    @staticmethod
    def unmark_exported(sales_order_numbers: List[str], kind: str = "so", actor: str = "") -> List[str]:
        """Puts orders back in the next "only new" file, for a download that never arrived."""
        from app.models.db_models import OrderDB

        if not sales_order_numbers:
            return []
        column = _mark_column(kind)
        with SessionLocal() as db:
            rows = (
                db.query(OrderDB)
                .filter(OrderDB.sales_order_number.in_(list(dict.fromkeys(sales_order_numbers))), column.isnot(None))
                .all()
            )
            reset = [r.sales_order_number for r in rows]
            for r in rows:
                setattr(r, column.key, None)
            db.commit()
        if reset:
            audit_log(
                action="SAGE_50_EXPORT_UNDONE",
                resource_type="SAGE_SO_CSV" if kind == "so" else "SAGE_PO_CSV",
                actor_email=actor or "system",
                detail=f"Export mark cleared for {len(reset)} order(s): {', '.join(reset)}",
            )
        return reset

    def generate_sales_orders_csv(
        self,
        customer_filter: Optional[str] = None,
        facility: Optional[str] = None,
        only_new: bool = False,
        mark_exported: bool = False,
    ) -> str:
        return self.export_sales_orders(customer_filter, facility, only_new, mark_exported, audit=mark_exported)[0]      # a look at the file (the Sage tab's preview) leaves no audit line; marking it exported does

    def export_sales_orders(
        self,
        customer_filter: Optional[str] = None,
        facility: Optional[str] = None,
        only_new: bool = False,
        mark_exported: bool = False,
        audit: bool = True,
    ) -> Tuple[str, List[str]]:
        """
        Generates official Sage 50 Sales Order import CSV format:
        Customer ID,SO Number,Date,Ship to Name,Ship to Address-Line One,Ship to City,Ship to State,Ship to Zipcode,Terms,Item ID,Description,Quantity,Unit Price,Amount,GL Account
        """
        orders = self._get_orders(facility=facility, only_new=only_new, kind="so")
        customer_filter = str(customer_filter or "").strip()          # blank or spaces means no filter, not "match nothing"
        if customer_filter:
            orders = [o for o in orders if customer_filter.lower() in str(o.get("customer_name") or "").lower()]

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

        self.last_skipped = []
        seen_numbers: set = set()
        for o in orders:
            rows: List[List[Any]] = []
            cust_id = safe_text(o.get("customer_id") or o.get("customer_name") or "")
            so_num = str(o.get("sales_order_number") or "").strip()
            if not cust_id.strip():
                self._skip(f"order {so_num or '(no number)'} has no customer, so it was not exported (a made-up customer id would create a wrong customer in Sage)")
                continue
            if not so_num:
                self._skip(f"an order for {o.get('customer_name') or 'a customer'} has no sales order number, so it was not exported")
                continue
            if so_num in seen_numbers:
                self._skip(f"sales order {so_num} appears twice; only the first was exported")
                continue
            order_date = o.get("po_date") if str(o.get("po_date") or "").strip() not in ("", "None") else o.get("created_at")      # no PO date: the day the order was entered
            if not str(order_date or "").strip():
                self._skip(f"order {so_num} has no order date at all, so it was not exported (an invented date could put it in the wrong accounting period)")
                continue
            try:
                po_date = sage_date(order_date)
                rate = self._rate(o.get("currency"))
            except ValueError as exc:
                self._skip(f"order {so_num} was not exported: {exc}")
                continue
            seen_numbers.add(so_num)
            cur = str(o.get("currency") or settings.DEFAULT_CURRENCY).upper()
            # Sage groups rows into one document by number and needs every row
            # to repeat how many lines the document has.
            exportable = [
                ln for ln in o.get("line_items", [])
                if ln.get("item_id") and ln.get("unit_price") is not None
            ]
            distribution = 0
            ship_name = safe_text(o.get("customer_name") or "Plant Receiving")
            ship_line1, ship_city, ship_state, ship_zip = (safe_text(x) for x in split_shipping_address(
                o.get("shipping_address")
            ))
            terms = safe_text(o.get("payment_terms") or "Net 30 Days")

            for line in o.get("line_items", []):
                item_id = line.get("item_id")
                if not item_id:
                    self._skip(f"a line on sales order {so_num} has no item id, so that line was left out")
                    continue
                desc = safe_text(line.get("description") or item_id)
                price = line.get("unit_price")
                if price is None:
                    self._skip(f"{safe_text(item_id, 40)} on sales order {so_num} has no unit price, so that line was left out")
                    continue
                qty_d = _dec(line.get("quantity") if line.get("quantity") is not None else 1)
                price_d = _dec(price)
                if qty_d is None or price_d is None:
                    self._skip(f"a line on sales order {so_num} ({safe_text(item_id, 40)}) has a quantity or price that is not a number, so that line was left out")
                    continue
                if qty_d == 0:
                    self._skip(f"a line on sales order {so_num} ({safe_text(item_id, 40)}) has quantity 0, so that line was left out")
                    continue
                if rate != 1:                               # Sage books in dollars: other currencies are converted at the configured rate and the original is kept in the description
                    desc = safe_text(f"{desc} (orig. {cur} {price_d:.2f} each)")
                    price_d = (price_d / rate).quantize(_CENT, rounding=ROUND_HALF_UP)
                unit_price, amount = money_line(qty_d, price_d)
                gl_account = self._resolve_sales_gl(item_id)
                distribution += 1

                rows.append([
                    cust_id,
                    so_num,
                    po_date,
                    safe_text(o.get("customer_po_number") or ""),
                    len(exportable),
                    distribution,
                    ship_name,
                    ship_line1,
                    ship_city,
                    ship_state,
                    ship_zip,
                    terms,
                    safe_text(item_id, 64),
                    desc,
                    f"{qty_d:f}",
                    f"{unit_price:.2f}",
                    f"{amount:.2f}",
                    gl_account,
                ])
            if distribution:
                for r in rows:                              # every row says how many lines the document REALLY has (lines left out above are not counted)
                    r[4] = distribution
                order_rows.append((so_num, rows))
            else:
                self._skip(f"sales order {so_num} has no usable line, so it was not exported")

        marked: List[str] = []
        try:
            if mark_exported:
                # Marking happens here, when the file is generated, not when it is
                # delivered. Claiming first and exporting only what was claimed
                # makes the mark atomic: an overlapping run cannot also export an
                # order, and nothing is marked that is not in this file.
                claimed = set(self.claim_for_export([so for so, _ in order_rows], kind="so"))
                marked = [so for so, _ in order_rows if so in claimed]
                order_rows = [(so, rows) for so, rows in order_rows if so in claimed]

            for _, rows in order_rows:
                writer.writerows(rows)

            csv_content = output.getvalue()
            output.close()

            # Audit log entry: only when the file is taken for use (downloaded or marked exported), never for a look
            audit and audit_log(
                action="SAGE_50_EXPORT",
                resource_type="SAGE_SO_CSV",
                detail=(
                    f"Generated Sage 50 SO Import CSV ({len(order_rows)} orders, facility={facility or 'ALL'}"
                    + (", marked exported" if mark_exported else "")
                    + (f"; {len(self.last_skipped)} left out of the file: " + " | ".join(str(w)[:140] for w in self.last_skipped[:3]) + (" | ..." if len(self.last_skipped) > 3 else "") if self.last_skipped else "")
                    + ")"
                ),
            )
        except Exception:
            if marked:                                  # nothing reached the caller: the orders go back in the next "only new" file
                self.unmark_exported(marked, kind="so", actor="system")
            raise

        return csv_content, marked

    def generate_purchase_orders_csv(
        self,
        vendor_filter: Optional[str] = None,
        facility: Optional[str] = None,
        only_new: bool = False,
        mark_exported: bool = False,
    ) -> str:
        return self.export_purchase_orders(vendor_filter, facility, only_new, mark_exported, audit=mark_exported)[0]

    def export_purchase_orders(
        self,
        vendor_filter: Optional[str] = None,
        facility: Optional[str] = None,
        only_new: bool = False,
        mark_exported: bool = False,
        audit: bool = True,
    ) -> Tuple[str, List[str]]:
        """
        Generates official Sage 50 Purchase Order import CSV format:
        Vendor ID,PO Number,Date,Number of Distributions,PO Distribution,Ship to Name,Ship to Address-Line One,Ship to City,Ship to State,Ship to Zipcode,Item ID,Description,Quantity,Unit Cost,Amount,GL Account

        As in the sales-order file, Sage groups rows into one PO by number and
        needs every row to say how many lines that PO has and which one it is.

        only_new / mark_exported work as for sales orders, with their own mark,
        so each order's POs reach Sage once. A vendor filter never marks: the
        order's other vendors' POs would then never be exported.
        """
        orders = self._get_orders(facility=facility, only_new=only_new, kind="po")
        mark_exported = mark_exported and not vendor_filter
        order_rows: List[Tuple[str, List[List[Any]]]] = []

        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")

        # Sage 50 US Purchase Order Header
        writer.writerow([
            "Vendor ID",
            "PO Number",
            "Date",
            "Number of Distributions",
            "PO Distribution",
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

        self.last_skipped = []
        for o in orders:
            rows: List[List[Any]] = []
            try:
                po_date = sage_date(o.get("po_date"))
            except ValueError as exc:
                self._skip(f"the purchase orders of order {o.get('sales_order_number')} were not exported: {exc}")
                continue
            ship_name = safe_text(o.get("customer_name") or "Allied Facility")
            ship_line1, ship_city, ship_state, ship_zip = (safe_text(x) for x in split_shipping_address(
                o.get("shipping_address")
            ))
            issued_pos = o.get("purchase_orders") or {}

            for line in o.get("line_items", []):
                vend_id = line.get("preferred_vendor")
                vend_id = str(vend_id).strip() if vend_id is not None else ""
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
                desc = safe_text(line.get("description") or item_id)
                qty_d, cost_d = _dec(line.get("quantity") if line.get("quantity") is not None else 1), _dec(cost)
                if qty_d is None or cost_d is None or qty_d == 0:
                    self._skip(f"a {safe_text(vend_id, 40)} line on order {o.get('sales_order_number')} has a quantity or cost that is not usable, so that line was left out")
                    continue
                qty = qty_d
                unit_cost, amount = money_line(qty_d, cost_d)
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

                rows.append([
                    safe_text(vend_id, 64),
                    safe_text(po_num, 64),
                    po_date,
                    ship_name,
                    ship_line1,
                    ship_city,
                    ship_state,
                    ship_zip,
                    safe_text(item_id, 64),
                    desc,
                    f"{qty:f}",
                    f"{unit_cost:.2f}",
                    f"{amount:.2f}",
                    gl_account,
                ])
            if rows:
                # Line counts per PO number: one order can raise POs to several
                # vendors. Each PO's lines are kept together, in order.
                first_seen = {po: i for i, po in reversed(list(enumerate(r[1] for r in rows)))}
                rows.sort(key=lambda r: first_seen[r[1]])
                totals: Dict[str, int] = {}
                for r in rows:
                    totals[r[1]] = totals.get(r[1], 0) + 1
                seen: Dict[str, int] = {}
                for r in rows:
                    seen[r[1]] = seen.get(r[1], 0) + 1
                    r[3:3] = [totals[r[1]], seen[r[1]]]
                order_rows.append((o.get("sales_order_number"), rows))

        marked: List[str] = []
        try:
            if mark_exported:
                claimed = set(self.claim_for_export([so for so, _ in order_rows], kind="po"))
                marked = [so for so, _ in order_rows if so in claimed]
                order_rows = [(so, rows) for so, rows in order_rows if so in claimed]
            po_lines_written = 0
            for _, rows in order_rows:
                writer.writerows(rows)
                po_lines_written += len(rows)

            csv_content = output.getvalue()
            output.close()

            # Audit log entry: only when the file is taken for use (downloaded or marked exported), never for a look
            audit and audit_log(
                action="SAGE_50_EXPORT",
                resource_type="SAGE_PO_CSV",
                detail=(
                    f"Generated Sage 50 PO Import CSV ({po_lines_written} lines, vendor={vendor_filter or 'ALL'}"
                    + (", marked exported" if mark_exported else "")
                    + ")"
                ),
            )
        except Exception:
            if marked:
                self.unmark_exported(marked, kind="po", actor="system")
            raise

        return csv_content, marked

    @staticmethod
    def to_usd(amount: Decimal, currency: str) -> Decimal:
        """Converts an amount in a supported currency to USD.

        Uses the configured FX_RATES table, which was previously declared in
        settings but never read anywhere.
        """
        code = (currency or settings.DEFAULT_CURRENCY).upper()
        if code == "USD":
            return amount
        from app.services import fx

        if code in ("CAD", "CNY"):                                       # the same rates the Orders screen uses (live when switched on, else the settings), so the two totals agree
            return (amount * Decimal(str(fx.cad_to_usd() if code == "CAD" else fx.cny_to_usd()))).quantize(Decimal("0.01"))
        rate = settings.FX_RATES.get(code)
        if not rate:
            raise ValueError(f"No exchange rate is configured for '{code}' (set FX_RATES); the amount is not treated as dollars.")
        # FX_RATES holds units-per-USD, so dividing converts back to USD.
        return (amount / Decimal(str(rate))).quantize(Decimal("0.01"))

    def get_batch_summary(self, facility: Optional[str] = None, only_new: bool = False) -> SageExportSummary:
        """Computes summary stats for the current export batch.

        Totals are normalised to USD. Adding raw CAD and CNY figures to USD ones
        (as this previously did) produced a single meaningless number, and the
        reported currency was hardcoded to USD regardless of the orders.
        """
        orders = self._get_orders(facility=facility, only_new=only_new, kind="so")             # only_new: the orders not yet marked as sent in a sales order file
        po_orders = self._get_orders(facility=facility, only_new=only_new, kind="po")          # the supplier lines not yet marked as sent in a purchase order file (a separate mark)
        by_currency: Dict[str, Decimal] = {}
        purchase_by_currency: Dict[str, Decimal] = {}
        total_sales = Decimal("0.00")
        total_purchases = Decimal("0.00")
        po_count = 0
        currencies = set()

        unconverted = 0
        for o in orders:
            order_currency = str(o.get("currency") or settings.DEFAULT_CURRENCY).upper()
            currencies.add(order_currency)
            revenue = _dec(o.get("total_revenue") or o.get("total_amount") or 0)
            if revenue is not None:
                by_currency[order_currency] = by_currency.get(order_currency, Decimal("0.00")) + revenue
            try:
                if revenue is not None:
                    total_sales += self.to_usd(revenue, order_currency)
            except ValueError:
                unconverted += 1                          # no exchange rate: left out of the totals, and said so below
        for o in po_orders:
            order_currency = str(o.get("currency") or settings.DEFAULT_CURRENCY).upper()
            currencies.add(order_currency)
            for line in o.get("line_items", []):
                vend_id = str(line.get("preferred_vendor") or "").upper()
                if not vend_id or vend_id == "STOCK":
                    continue
                cost, qty = _dec(line.get("unit_cost")), _dec(line.get("quantity") if line.get("quantity") is not None else 1)
                if cost is None or qty is None:
                    continue
                purchase_by_currency[order_currency] = purchase_by_currency.get(order_currency, Decimal("0.00")) + qty * cost
                try:
                    total_purchases += self.to_usd(qty * cost, order_currency)
                except ValueError:
                    unconverted += 1
                    continue
                po_count += 1

        def words(by: Dict[str, Decimal], usd: Decimal) -> str:
            if not by:
                return "Nothing ready."
            per = "; ".join(f"{c} {v:,.2f}" for c, v in sorted(by.items()))
            return per if len(by) == 1 else f"{per} (about ${usd:,.0f} if converted at today's rates)"

        # Report the batch currency honestly: USD once normalised, and say so
        # explicitly when the batch spans more than one source currency.
        if len(currencies) > 1:
            batch_currency = f"USD (normalised from {'/'.join(sorted(currencies))})"
        else:
            batch_currency = "USD"
        if unconverted:
            batch_currency += f"; {unconverted} order(s) left out of the totals: no exchange rate"

        return SageExportSummary(
            sales_orders_count=len(orders),
            purchase_orders_count=po_count,
            total_sales_volume=total_sales,
            total_purchase_volume=total_purchases,
            currency=batch_currency,
            facility=facility or "ALL",
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            export_files=["SO_IMPORT.CSV", "PO_IMPORT.CSV"],
            by_currency=by_currency,
            purchase_by_currency=purchase_by_currency,
            sales_label=words(by_currency, total_sales),
            purchase_label=words(purchase_by_currency, total_purchases),
        )


# Global instance
sage_export_service = SageExportService()



def overview(limit: int = 10) -> dict:
    """The Sage export page in one call, in plain words: how many sales orders and vendor POs are ready for the next file, when the last file was made and by whom, the recent history, and the
    orders already marked as sent that can be put back (if a file never arrived). Reading only; nothing is exported or unmarked here."""
    from app.core.database import SessionLocal
    from app.models.db_models import AuditLogDB, OrderDB

    with SessionLocal() as db:
        ready_rows = db.query(OrderDB).filter(OrderDB.sage_exported_at.is_(None), OrderDB.status.notin_(("VALIDATION_ERROR", "SAGE_ERROR", "CANCELLED"))).all()
        so_ready = len(ready_rows)
        ready_totals: dict = {}
        for o in ready_rows:                                                         # per currency, never one "$" sum
            cur = str(o.currency or settings.DEFAULT_CURRENCY).upper()
            ready_totals[cur] = round(ready_totals.get(cur, 0.0) + float(o.total_revenue or 0), 2)
        po_ready = sum(1 for o in db.query(OrderDB).filter(OrderDB.sage_po_exported_at.is_(None), OrderDB.status.notin_(("VALIDATION_ERROR", "SAGE_ERROR", "CANCELLED"))) if o.purchase_orders)
        po_rows = [o for o in db.query(OrderDB).filter(OrderDB.sage_po_exported_at.is_(None), OrderDB.status.notin_(("VALIDATION_ERROR", "SAGE_ERROR", "CANCELLED"))) if o.purchase_orders]
        po_lines = sum(len([ln for ln in (o.line_items or []) if str(ln.get("preferred_vendor") or "").upper() not in ("", "STOCK")]) for o in po_rows)
        rows = db.query(AuditLogDB).filter(AuditLogDB.action.in_(("SAGE_50_EXPORT", "SAGE_50_EXPORT_UNDONE"))).order_by(AuditLogDB.id.desc()).limit(max(1, min(int(limit), 50))).all()
        history = [{"at": a.timestamp.isoformat() if a.timestamp else None, "by": a.actor_email,
                    "what": ("A Sage file for sales orders was made" if a.resource_type == "SAGE_SO_CSV" else "A Sage file for vendor purchase orders was made") if a.action == "SAGE_50_EXPORT"
                    else "Orders were put back for the next file", "detail": a.detail or "", "marked_sent": "marked exported" in (a.detail or "")} for a in rows]
        sent_so = [o for o in db.query(OrderDB).filter(OrderDB.sage_exported_at.isnot(None)).order_by(OrderDB.sage_exported_at.desc()).limit(20)]
        sent_po = [o for o in db.query(OrderDB).filter(OrderDB.sage_po_exported_at.isnot(None)).order_by(OrderDB.sage_po_exported_at.desc()).limit(20)]
        unmark = ([{"kind": "sales-orders", "sales_order_number": o.sales_order_number, "customer": o.customer_name, "sent_at": o.sage_exported_at.isoformat()} for o in sent_so]
                  + [{"kind": "purchase-orders", "sales_order_number": o.sales_order_number, "customer": o.customer_name, "sent_at": o.sage_po_exported_at.isoformat()} for o in sent_po])
    last = next((h for h in history if h["marked_sent"]), None)
    text = (f"{so_ready} sales order{'s' if so_ready != 1 else ''} ({', '.join(f'{c} {v:,.2f}' for c, v in sorted(ready_totals.items())) or 'no total'}) and {po_ready} order{'s' if po_ready != 1 else ''} with vendor purchase orders ({po_lines} supplier line{'s' if po_lines != 1 else ''}) are ready for the next Sage file. "
            + (f"The last file was made {str(last['at'])[:16].replace('T', ' ')} by {last['by']}." if last else "No file has been made yet."))
    return {"ready_to_send": {"sales_orders": so_ready, "purchase_orders": po_ready, "purchase_lines": po_lines, "sales_totals": ready_totals}, "last_export": last, "history": history, "unmark": unmark, "text": text,
            "how": {"download_sales_orders": "/api/v1/sage/export/sales-orders?only_new=true&mark_exported=true", "download_purchase_orders": "/api/v1/sage/export/purchase-orders?only_new=true&mark_exported=true",
                    "unmark": {"method": "POST", "path": "/api/v1/sage/export/unmark", "body": {"kind": "sales-orders|purchase-orders", "sales_order_numbers": ["..."]}}}}
