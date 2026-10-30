"""Read layer accessing Sage 50 / Actian Zen PSQL via pyodbc with simulation fallback."""

from decimal import Decimal
from typing import Any, Dict, Optional
from app.config import settings
from app.core.exceptions import SageODBCError
from app.core.logging import logger
from app.models.sage import SageCustomer, SageInventoryItem, SageVendor
from app.services.entity_matcher import EntityMatcher
from app.core.container import get_entity_matcher


class SageODBCReader:
    """High-speed read layer querying Actian Zen PSQL / Sage 50 data tables."""

    def __init__(self, entity_matcher: Optional[EntityMatcher] = None):
        self.entity_matcher = entity_matcher or get_entity_matcher()
        self.simulation_mode = settings.SAGE_SIMULATION_MODE

    def test_connection(self) -> Dict[str, Any]:
        """Tests connectivity to ODBC DSN or validates simulation mode."""
        if self.simulation_mode:
            return {
                "status": "connected",
                "mode": "simulation",
                "dsn": settings.SAGE_ODBC_DSN,
                "cached_items_count": len(self.entity_matcher.items_cache),
                "cached_customers_count": len(self.entity_matcher.customers_cache),
                "cached_vendors_count": len(self.entity_matcher.vendors_cache),
            }

        try:
            import pyodbc
            conn_str = f"DSN={settings.SAGE_ODBC_DSN};UID={settings.SAGE_USER};PWD={settings.SAGE_PASSWORD}"
            with pyodbc.connect(conn_str, timeout=5) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                return {
                    "status": "connected",
                    "mode": "live_odbc",
                    "dsn": settings.SAGE_ODBC_DSN,
                }
        except Exception as exc:
            logger.error(f"Sage ODBC connection failed: {exc}")
            raise SageODBCError(f"Failed to connect to Sage ODBC DSN '{settings.SAGE_ODBC_DSN}': {exc}")

    def get_inventory_item(self, item_id: str) -> Optional[SageInventoryItem]:
        """Queries on-hand stock and GL codes for an item."""
        if self.simulation_mode or not self.is_odbc_available():
            item_data = self.entity_matcher.items_cache.get(item_id)
            if not item_data:
                return None
            return SageInventoryItem(
                item_id=item_data["item_id"],
                description=item_data["description"],
                gl_sales_account=item_data.get("gl_sales_account", "4000-00"),
                gl_inventory_account=item_data.get("gl_inventory_account", "1200-00"),
                gl_cogs_account="5000-00",
                standard_cost=Decimal(str(item_data.get("standard_cost", "0.00"))),
                last_unit_cost=Decimal(str(item_data.get("standard_cost", "0.00"))),
                price_level1=Decimal(str(item_data.get("base_price", "0.00"))),
                quantity_on_hand=Decimal(str(item_data.get("stock_on_hand", "0.00"))),
                preferred_vendor_id=item_data.get("preferred_vendor"),
                uom=item_data.get("uom", "EA"),
            )

        try:
            import pyodbc
            conn_str = f"DSN={settings.SAGE_ODBC_DSN};UID={settings.SAGE_USER};PWD={settings.SAGE_PASSWORD}"
            with pyodbc.connect(conn_str, timeout=5) as conn:
                cursor = conn.cursor()
                query = "SELECT ItemID, Description, QuantityOnHand, PriceLevel1 FROM LineItem WHERE ItemID = ?"
                cursor.execute(query, (item_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                return SageInventoryItem(
                    item_id=row[0],
                    description=row[1],
                    quantity_on_hand=Decimal(str(row[2])),
                    price_level1=Decimal(str(row[3])),
                )
        except Exception as exc:
            logger.error(f"ODBC query error for item {item_id}: {exc}")
            raise SageODBCError(f"Error querying Sage inventory for item {item_id}: {exc}")

    def get_customer(self, customer_id: str) -> Optional[SageCustomer]:
        """Queries customer account details and credit terms."""
        cust_data = self.entity_matcher.customers_cache.get(customer_id)
        if not cust_data:
            return None
        return SageCustomer(
            customer_id=cust_data["customer_id"],
            name=cust_data["customer_name"],
            bill_address_line1=cust_data.get("billing_address", ""),
            ship_address_line1=cust_data.get("shipping_address", ""),
            terms_description=cust_data.get("payment_terms", "Net 30 Days"),
            sales_rep_id=cust_data.get("sales_rep", "Karissa M. Rakestraw"),
        )

    def get_vendor(self, vendor_id: str) -> Optional[SageVendor]:
        """Queries vendor account details."""
        vend_data = self.entity_matcher.vendors_cache.get(vendor_id)
        if not vend_data:
            return None
        return SageVendor(
            vendor_id=vend_data["vendor_id"],
            name=vend_data["vendor_name"],
            address_line1=vend_data.get("vendor_address", ""),
            terms_description=vend_data.get("payment_terms", "Net 30 Days"),
            email=vend_data.get("email"),
        )

    @staticmethod
    def is_odbc_available() -> bool:
        """Helper to determine if pyodbc module and driver are present."""
        try:
            import pyodbc  # noqa: F401  # type: ignore
            return True
        except ImportError:
            return False
