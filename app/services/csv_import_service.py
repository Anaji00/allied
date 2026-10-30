"""CSV Import Service for Master Data and Batch Orders.

Handles parsing, schema validation, persistent CSV file updates, and in-memory
catalog reloading for items, customers, vendors, and batch customer POs.
"""

import csv
import io
import os
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from app.config import settings
from app.core.audit import audit_log
from app.core.logging import logger
from app.models.orders import InboundOrderRequest, OrderLineItem
from app.services.entity_matcher import EntityMatcher
from app.services.order_orchestrator import OrderOrchestrator
from app.core.container import get_entity_matcher


class CSVImportResult(BaseModel):
    """Structured outcome of a CSV import operation."""
    status: str  # SUCCESS, PARTIAL_SUCCESS, FAILED
    import_type: str  # ITEMS, CUSTOMERS, VENDORS, ORDERS_BATCH
    rows_processed: int
    records_updated: int
    records_created: int
    errors: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)
    message: str


def _atomic_write_csv(
    target_path: Path, fieldnames: List[str], records: List[Dict[str, Any]]
) -> None:
    """Rewrites a master catalog CSV atomically.

    The whole catalog is rewritten on every import, so writing in place meant a
    crash or a concurrent read mid-write could truncate or tear the master data.
    The replacement is staged in a temp file in the same directory and then
    moved into place, which is atomic on both POSIX and Windows.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.", suffix=".tmp", dir=str(target_path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for rec in records:
                writer.writerow({k: rec.get(k, "") for k in fieldnames})
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _field(row: Dict[str, Any], key: str, default: str = "") -> str:
    """Reads one CSV cell as a clean string.

    csv.DictReader yields None for columns a short row does not reach, and ""
    for present-but-blank cells. `row.get(key, default).strip()` therefore both
    crashed on ragged rows and silently ignored the default for blank cells.
    """
    value = row.get(key)
    if value is None:
        return default
    if isinstance(value, list):  # extra unnamed columns land in a list
        value = " ".join(str(v) for v in value if v is not None)
    text = str(value).strip()
    return text if text else default


def _parse_decimal(text: str) -> Optional[Decimal]:
    """Parses a CSV money/quantity cell, or None when it is not a finite number."""
    try:
        value = Decimal((text or "").replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


class CSVImportService:
    """Enterprise CSV data importer for Allied Welding Systems."""

    def __init__(
        self,
        matcher: Optional[EntityMatcher] = None,
        orchestrator: Optional[OrderOrchestrator] = None,
    ):
        self.matcher = matcher or get_entity_matcher()
        self.orchestrator = orchestrator or OrderOrchestrator(entity_matcher=self.matcher)
        self.data_dir = settings.DATA_DIR

    def _notify_rag_reload(self):
        try:
            from app.services.rag_service import rag_service
            rag_service.reload_knowledge_bases()
        except Exception as exc:
            logger.warning(f"Failed to reload RAG knowledge bases: {exc}")

    def get_templates(self) -> Dict[str, str]:
        """Returns standard CSV header templates with sample rows."""
        return {
            "ITEMS": (
                "item_id,description,gl_sales_account,gl_inventory_account,standard_cost,base_price,preferred_vendor,uom,category,stock_on_hand,min_stock_level\n"
                'PLATE-400XL-12,"ALLIED 400XL 1/2"" WEAR PLATE 60"" X 120""",4000-00,1200-00,2450.00,4890.00,NIBROCK,SHT,Clad Plate,4.0,2.0\n'
            ),
            "CUSTOMERS": (
                "customer_id,customer_name,billing_address,shipping_address,payment_terms,sales_rep,email,contact_name,shipping_method\n"
                '10999,Holcim US Cement - Midlothian,"PO Box 88, Midlothian, TX 76065","1450 E US-287, Midlothian, TX 76065",Net 30 Days,Karissa M. Rakestraw,purchasing@holcim.com,David Martinez,MOTOR FRT\n'
            ),
            "VENDORS": (
                "vendor_id,vendor_name,vendor_address,payment_terms,email,phone,po_special_instructions\n"
                'HARDFACE_CORP,Hardfacing Specialists Inc,"PO Box 441, Birmingham, AL 35201",Net 30 Days,orders@hardfacecorp.com,(205) 555-4412,"INCLUDE ALLIED PACKING SLIP ONLY."\n'
            ),
            "ORDERS_BATCH": (
                "customer_name,customer_po_number,item_id_or_text,quantity,unit_price,shipping_address,payment_terms\n"
                'Owens Corning,PO-BATCH-101,OC755507,2,3772.91,"831 Hayter Rd NW, Edmonton, AB T6S 1A1, Canada",Net 30 Days\n'
                'Knauf Insulation,PO-BATCH-102,KN5218554,1,35378.14,"1000 E 5th St, Lanett, AL 36863",Net 30 Days\n'
            ),
        }

    def import_csv(
        self,
        csv_text: str,
        import_type: str,
        actor_email: str = "system",
        actor_role: str = "SYSTEM",
    ) -> CSVImportResult:
        """Parses, validates, and commits CSV data."""
        clean_type = import_type.strip().upper()
        clean_text = csv_text.strip()

        if not clean_text:
            return CSVImportResult(
                status="FAILED",
                import_type=clean_type,
                rows_processed=0,
                records_updated=0,
                records_created=0,
                errors=["Empty CSV content provided."],
                message="No data to import.",
            )

        try:
            reader = csv.DictReader(io.StringIO(clean_text))
            rows = list(reader)
        except Exception as e:
            return CSVImportResult(
                status="FAILED",
                import_type=clean_type,
                rows_processed=0,
                records_updated=0,
                records_created=0,
                errors=[f"CSV parsing error: {str(e)}"],
                message="Invalid CSV format.",
            )

        if not rows:
            return CSVImportResult(
                status="FAILED",
                import_type=clean_type,
                rows_processed=0,
                records_updated=0,
                records_created=0,
                errors=["CSV contains no data rows."],
                message="Empty CSV rows.",
            )

        header = {str(h).strip() for h in (reader.fieldnames or []) if h}

        if clean_type == "ITEMS":
            result = self._import_items(rows, header)
        elif clean_type == "CUSTOMERS":
            result = self._import_customers(rows, header)
        elif clean_type == "VENDORS":
            result = self._import_vendors(rows, header)
        elif clean_type == "ORDERS_BATCH":
            result = self._import_batch_orders(rows)
        else:
            return CSVImportResult(
                status="FAILED",
                import_type=clean_type,
                rows_processed=len(rows),
                records_updated=0,
                records_created=0,
                errors=[f"Unknown import type: {import_type}. Choose ITEMS, CUSTOMERS, VENDORS, or ORDERS_BATCH."],
                message="Unsupported import type.",
            )

        self._audit_import(result, actor_email, actor_role)
        return result

    @staticmethod
    def _audit_import(result: CSVImportResult, actor_email: str, actor_role: str) -> None:
        """Records a master-data rewrite or batch order commit in the audit trail."""
        if not (result.records_created or result.records_updated):
            return
        if result.import_type == "ORDERS_BATCH":
            orders = result.details.get("created_orders", [])
            refs = ", ".join(
                f"{o.get('po_number')}->{o.get('sales_order_number')}" for o in orders[:20]
            )
            action, resource = "BATCH_ORDERS_IMPORTED", "ORDER"
            detail = f"CSV batch created {len(orders)} order(s): {refs}"
        else:
            keys = result.details.get("changed_keys", [])
            shown = ", ".join(keys[:25]) + (f" (+{len(keys) - 25} more)" if len(keys) > 25 else "")
            action, resource = "MASTER_DATA_IMPORTED", f"MASTER_{result.import_type}"
            detail = (
                f"CSV import created {result.records_created}, updated "
                f"{result.records_updated} record(s): {shown}"
            )
        audit_log(
            action=action,
            resource_type=resource,
            resource_id=result.import_type,
            actor_email=actor_email,
            actor_role=actor_role,
            detail=detail,
        )

    @staticmethod
    def _merge_master_rows(
        rows: List[Dict[str, str]],
        header: set,
        existing: Dict[str, Dict[str, Any]],
        key_of: Callable[[Dict[str, str], Dict[str, Any]], Tuple[str, Optional[str]]],
        fieldnames: List[str],
        create_defaults: Callable[[str, Dict[str, str]], Dict[str, str]],
        numeric_fields: Tuple[str, ...] = (),
        upper_fields: Tuple[str, ...] = (),
    ) -> Tuple[int, int, List[str], List[str]]:
        """Applies CSV rows onto a master catalog in place.

        An update only touches the columns the CSV actually carries, and a
        blank cell keeps the value on file. Previously an update rebuilt the
        whole row from defaults, so a price-only sheet reset standard_cost to 0
        and wiped vendor email addresses. A numeric cell that does not parse
        rejects its row instead of being stored as 0.

        Returns (created, updated, errors, changed_keys).
        """
        created = updated = 0
        errors: List[str] = []
        changed: List[str] = []

        for i, row in enumerate(rows, start=1):
            key, key_error = key_of(row, existing)
            if key_error:
                errors.append(f"Row {i}: {key_error}")
                continue

            values: Dict[str, str] = {}
            row_errors: List[str] = []
            for f in fieldnames:
                if f not in header:
                    continue
                cell = _field(row, f, "")
                if not cell:
                    continue
                if f in numeric_fields:
                    num = _parse_decimal(cell)
                    if num is None or num < 0:
                        row_errors.append(f"'{f}' must be a non-negative number (got '{cell}')")
                        continue
                    cell = str(num)
                if f in upper_fields:
                    cell = cell.upper()
                values[f] = cell

            if row_errors:
                errors.append(f"Row {i} ({key}): rejected; " + "; ".join(row_errors))
                continue

            if key in existing:
                record = {f: existing[key].get(f, "") for f in fieldnames}
                record.update(values)
                updated += 1
            else:
                record = create_defaults(key, values)
                record.update(values)
                created += 1
            existing[key] = record
            changed.append(key)

        return created, updated, errors, changed

    def _write_master(
        self,
        target_path: Path,
        fieldnames: List[str],
        records: Dict[str, Dict[str, Any]],
        changed: List[str],
        errors: List[str],
    ) -> bool:
        """Writes the merged catalog and reloads caches; False on a disk error."""
        if not changed:
            return True
        try:
            _atomic_write_csv(target_path, fieldnames, list(records.values()))
            # Reload matcher memory & RAG knowledge base
            self.matcher.load_catalogs()
            self._notify_rag_reload()
            return True
        except Exception as exc:
            errors.append(f"Disk write error: {str(exc)}")
            return False

    def _import_items(self, rows: List[Dict[str, str]], header: Optional[set] = None) -> CSVImportResult:
        target_path = settings.MASTER_ITEMS_PATH
        existing_items = dict(self.matcher.items_cache)
        header = header if header is not None else set(rows[0].keys())

        fieldnames = [
            "item_id", "description", "gl_sales_account", "gl_inventory_account",
            "standard_cost", "base_price", "preferred_vendor", "uom", "category",
            "stock_on_hand", "min_stock_level"
        ]

        def key_of(row, _existing):
            item_id = _field(row, "item_id", "")
            return (item_id, None) if item_id else ("", "Missing required 'item_id'.")

        def defaults(item_id, _values):
            return {
                "item_id": item_id,
                "description": item_id,
                "gl_sales_account": "4000-00",
                "gl_inventory_account": "1200-00",
                "standard_cost": "0.00",
                "base_price": "0.00",
                "preferred_vendor": "STOCK",
                "uom": "EA",
                "category": "General",
                "stock_on_hand": "0.0",
                "min_stock_level": "0.0",
            }

        created, updated, errors, changed = self._merge_master_rows(
            rows, header, existing_items, key_of, fieldnames, defaults,
            numeric_fields=("standard_cost", "base_price", "stock_on_hand", "min_stock_level"),
            upper_fields=("preferred_vendor", "uom"),
        )

        if self._write_master(target_path, fieldnames, existing_items, changed, errors):
            logger.info(f"Successfully imported items CSV: {created} created, {updated} updated")
        else:
            created = updated = 0

        status = "SUCCESS" if not errors else ("PARTIAL_SUCCESS" if (created + updated) > 0 else "FAILED")
        return CSVImportResult(
            status=status,
            import_type="ITEMS",
            rows_processed=len(rows),
            records_updated=updated,
            records_created=created,
            errors=errors,
            details={"total_catalog_items": len(existing_items), "changed_keys": changed},
            message=f"Imported {created} new items, updated {updated} items in master catalog.",
        )

    def _import_customers(self, rows: List[Dict[str, str]], header: Optional[set] = None) -> CSVImportResult:
        target_path = settings.MASTER_CUSTOMERS_PATH
        existing_custs = dict(self.matcher.customers_cache)
        header = header if header is not None else set(rows[0].keys())

        fieldnames = [
            "customer_id", "customer_name", "billing_address", "shipping_address",
            "payment_terms", "sales_rep", "email", "contact_name", "shipping_method"
        ]

        def key_of(row, existing):
            cust_id = _field(row, "customer_id", "")
            name = _field(row, "customer_name", "")
            if not cust_id and not name:
                return "", "Missing customer_id and customer_name."
            return cust_id or f"CUST-{len(existing) + 1000}", None

        def defaults(cust_id, values):
            return {
                "customer_id": cust_id,
                "customer_name": f"Customer {cust_id}",
                "billing_address": "",
                # A new account ships to its billing address unless told otherwise.
                "shipping_address": values.get("billing_address", ""),
                "payment_terms": "Net 30 Days",
                "sales_rep": "Karissa M. Rakestraw",
                "email": "",
                "contact_name": "",
                "shipping_method": "MOTOR FRT",
            }

        created, updated, errors, changed = self._merge_master_rows(
            rows, header, existing_custs, key_of, fieldnames, defaults,
        )

        if self._write_master(target_path, fieldnames, existing_custs, changed, errors):
            logger.info(f"Successfully imported customers CSV: {created} created, {updated} updated")
        else:
            created = updated = 0

        status = "SUCCESS" if not errors else ("PARTIAL_SUCCESS" if (created + updated) > 0 else "FAILED")
        return CSVImportResult(
            status=status,
            import_type="CUSTOMERS",
            rows_processed=len(rows),
            records_updated=updated,
            records_created=created,
            errors=errors,
            details={"total_customers": len(existing_custs), "changed_keys": changed},
            message=f"Imported {created} new customers, updated {updated} accounts in master catalog.",
        )

    def _import_vendors(self, rows: List[Dict[str, str]], header: Optional[set] = None) -> CSVImportResult:
        target_path = settings.MASTER_VENDORS_PATH
        existing_vends = dict(self.matcher.vendors_cache)
        header = header if header is not None else set(rows[0].keys())

        fieldnames = [
            "vendor_id", "vendor_name", "vendor_address", "payment_terms",
            "email", "phone", "po_special_instructions"
        ]

        def key_of(row, _existing):
            vend_id = _field(row, "vendor_id", "").upper()
            return (vend_id, None) if vend_id else ("", "Missing vendor_id.")

        def defaults(vend_id, _values):
            return {
                "vendor_id": vend_id,
                "vendor_name": f"Vendor {vend_id}",
                "vendor_address": "USA",
                "payment_terms": "Net 30 Days",
                "email": "",
                "phone": "",
                "po_special_instructions": "INCLUDE ALLIED PACKING SLIP ONLY.",
            }

        created, updated, errors, changed = self._merge_master_rows(
            rows, header, existing_vends, key_of, fieldnames, defaults,
            upper_fields=("vendor_id",),
        )

        if self._write_master(target_path, fieldnames, existing_vends, changed, errors):
            logger.info(f"Successfully imported vendors CSV: {created} created, {updated} updated")
        else:
            created = updated = 0

        status = "SUCCESS" if not errors else ("PARTIAL_SUCCESS" if (created + updated) > 0 else "FAILED")
        return CSVImportResult(
            status=status,
            import_type="VENDORS",
            rows_processed=len(rows),
            records_updated=updated,
            records_created=created,
            errors=errors,
            details={"total_vendors": len(existing_vends), "changed_keys": changed},
            message=f"Imported {created} new vendors, updated {updated} vendors in master catalog.",
        )

    def _import_batch_orders(self, rows: List[Dict[str, str]]) -> CSVImportResult:
        """Groups rows by customer PO and runs dual-sided transaction generation.

        Every row is validated before any order is created. Previously the line
        models were built outside any error handling, so one bad row raised
        mid-batch after earlier groups had already been committed, and an
        unparseable quantity or price was silently booked as 1 at $0.00.
        """
        orders_by_key: Dict[Tuple[str, str], List[OrderLineItem]] = {}
        first_row: Dict[Tuple[str, str], Dict[str, str]] = {}
        errors: List[str] = []
        created_orders = []

        for i, row in enumerate(rows, start=1):
            cust = _field(row, "customer_name", "")
            po = _field(row, "customer_po_number", "")
            row_errors: List[str] = []
            if not cust or not po:
                row_errors.append("missing customer_name or customer_po_number")

            raw_item = _field(row, "item_id_or_text") or _field(row, "item_id", "")
            if not raw_item:
                row_errors.append("missing item_id_or_text")

            qty_text = _field(row, "quantity", "")
            qty = _parse_decimal(qty_text)
            if qty is None or qty <= 0:
                row_errors.append(f"quantity must be a positive number (got '{qty_text}')")

            price_text = _field(row, "unit_price", "")
            price = _parse_decimal(price_text)
            if price is None or price < 0:
                row_errors.append(f"unit_price must be a non-negative number (got '{price_text}')")

            line = None
            if not row_errors:
                try:
                    line = OrderLineItem(raw_item_text=raw_item, quantity=qty, unit_price=price)
                except ValidationError as exc:
                    row_errors.append(
                        "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
                    )

            if row_errors:
                errors.append(f"Row {i}: " + "; ".join(row_errors))
                continue

            key = (cust, po)
            orders_by_key.setdefault(key, []).append(line)
            first_row.setdefault(key, row)

        requests: List[Tuple[str, str, InboundOrderRequest]] = []
        if not errors:
            for (cust, po), line_items in orders_by_key.items():
                head = first_row[(cust, po)]
                try:
                    requests.append((cust, po, InboundOrderRequest(
                        customer_name=cust,
                        customer_po_number=po,
                        shipping_address=_field(head, "shipping_address", "Customer Plant Receiving Dock"),
                        payment_terms=_field(head, "payment_terms", "Net 30 Days"),
                        line_items=line_items,
                        notes="Imported via CSV Batch Upload",
                    )))
                except ValidationError as exc:
                    errors.append(f"Order [{cust} / {po}]: {exc.errors()[0].get('msg', exc)}")

        if errors:
            # Nothing is committed when any row is invalid, so a corrected
            # sheet can be re-uploaded without creating duplicates.
            return CSVImportResult(
                status="FAILED",
                import_type="ORDERS_BATCH",
                rows_processed=len(rows),
                records_updated=0,
                records_created=0,
                errors=errors,
                details={"created_orders": []},
                message=f"No orders created: {len(errors)} row(s) failed validation. Fix them and re-upload.",
            )

        for cust, po, req in requests:
            try:
                res = self.orchestrator.process_inbound_order(req)
                created_orders.append({
                    "customer": cust,
                    "po_number": po,
                    "sales_order_number": res.sales_order_number,
                    "purchase_orders": res.purchase_orders,
                    "revenue": float(res.total_revenue),
                })
            except Exception as e:
                errors.append(f"Order [{cust} / {po}] orchestration error: {str(e)}")

        status = "SUCCESS" if not errors else ("PARTIAL_SUCCESS" if created_orders else "FAILED")
        return CSVImportResult(
            status=status,
            import_type="ORDERS_BATCH",
            rows_processed=len(rows),
            records_updated=0,
            records_created=len(created_orders),
            errors=errors,
            details={"created_orders": created_orders},
            message=f"Processed {len(created_orders)} batch orders successfully ({len(rows)} lines).",
        )


csv_import_service = CSVImportService()
