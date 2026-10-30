"""In-Memory Fast Context Cache for Orders, Split Vendor POs, Vendors, MTRs, and Technical Specs.
Enables sub-millisecond retrieval of complete order and supplier context across all AI agents.
"""

from typing import Any, Dict, List, Optional
import re
import threading


class AgentMemoryCache:
    """Thread-safe singleton in-memory cache for ultra-fast agent context retrieval."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(AgentMemoryCache, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._orders: Dict[str, Dict[str, Any]] = {}     # order_id -> order (only)
        # Aliases live apart from the orders themselves. Keying customer POs in
        # the same dict as order ids meant two customers who both sent "PO-100"
        # overwrote each other, and every order appeared three times.
        self._so_index: Dict[str, str] = {}               # SO number -> order_id
        self._po_index: Dict[str, List[str]] = {}         # customer PO -> order_ids
        self._customer_index: Dict[str, List[str]] = {}  # customer_key -> list of order_ids
        self._vendor_index: Dict[str, List[str]] = {}    # vendor_id -> list of order_ids
        self._vendors: Dict[str, Dict[str, Any]] = {}
        self._heat_mtr_index: Dict[str, str] = {}        # heat_number -> order_id
        self._init_default_cache()
        self._initialized = True

    def _init_default_cache(self):
        """Populates the cache with baseline vendor agreements and historical benchmark orders."""
        # 1. Vendor Profiles & Operational Constraints
        self._vendors = {
            "NIBROCK": {
                "vendor_id": "NIBROCK",
                "name": "Nibrock Mining & Wear Solutions Inc.",
                "category": "Plates, Screens, Wear Bars",
                "primary_skus": ["PLATE-300XL-38", "PLATE-300XL-12", "PLATE-200-12", "OC755507", "BAR-34X2-41"],
                "lead_time_days": 10,
                "moq": "1 Sheet / 1 Custom Screen",
                "freight_terms": "FOB Starr, SC or Edmonton, AB via Flatbed",
                "blind_ship_rules": "Strict: Remove all Nibrock packaging, tags, and documentation. Apply Allied Welding Systems packing slip only.",
                "contact_email": "orders@nibrockwear.com",
                "contract_discount_pct": 35.0,
            },
            "KWS": {
                "vendor_id": "KWS",
                "name": "KWS Manufacturing Company, Ltd.",
                "category": "Screw Conveyors, Beams, Heavy Machining",
                "primary_skus": ["KN5218554", "BEAM-W8X31", "BEAM-W10X49", "BEAM-W12X65", "FAB-WORK"],
                "lead_time_days": 21,
                "moq": "1 Complete Assembly",
                "freight_terms": "FOB Burleson, TX via Dedicated Motor Freight",
                "blind_ship_rules": "Blind-ship crate tagging required. No KWS stenciling or logo markings on conveyor troughs or shafts.",
                "contact_email": "industrial@kwsmfg.com",
                "contract_discount_pct": 28.0,
            },
            "ZIBO": {
                "vendor_id": "ZIBO",
                "name": "Zibo Supereal Ceramics Co., Ltd.",
                "category": "Ceramic Hoses, Alumina Hex Tiles",
                "primary_skus": ["OC262064", "CERAMIC-TILE-HEX"],
                "lead_time_days": 35,
                "moq": "2 Hoses / 50 sq.ft Tiles",
                "freight_terms": "CIF Port of Houston / Customs Cleared to Starr",
                "blind_ship_rules": "Mandatory: Omit all Chinese export documentation or supplier markings from customer crating.",
                "contact_email": "export@zibosupereal.com",
                "contract_discount_pct": 42.0,
            },
            "STOCK": {
                "vendor_id": "STOCK",
                "name": "Allied Internal Warehouse Inventory",
                "category": "Hardsurfacing Wire & Scan Services",
                "primary_skus": ["WIRE-2035XL-045", "SCANNING/ENGINEERIN"],
                "lead_time_days": 1,
                "moq": "1 Spool / 1 Cut",
                "freight_terms": "Ex-Works Regional Yard (Edmonton / Starr / Lanett / Hillsboro)",
                "blind_ship_rules": "Allied branded white-label packaging.",
                "contact_email": "operations@alliedweldingsystems.com",
                "contract_discount_pct": 0.0,
            },
        }

        # 2. Benchmark Active & Historical Orders
        sample_orders = [
            {
                "order_id": "ORD-OC-EDM-2025-01",
                "sales_order_number": "SO-10881",
                "customer_po_number": "PO-9921",
                "customer_id": "CUST-OC-01",
                "customer_name": "Owens Corning (Edmonton Plant)",
                "po_date": "2025-11-14",
                "payment_terms": "Net 150 Days",
                "shipping_method": "MOTOR FRT",
                "shipping_address": "Owens Corning, 12345 121A Ave NW, Edmonton, AB T5L 2T5",
                "blind_shipping": True,
                "drawing_reference": "DWG# OC-ED-218-R2",
                "heat_numbers": ["HT-300XL-8819", "HT-C1045-9120"],
                "line_items": [
                    {
                        "item_id": "OC755507",
                        "description": "2-1/2\" DIA HOLES HAMMERMILL SCREEN MADE FROM ALLIED 300XL",
                        "quantity": 2,
                        "unit_price": 3772.91,
                        "unit_cost": 1785.00,
                        "preferred_vendor": "NIBROCK",
                        "heat_number": "HT-300XL-8819",
                    },
                    {
                        "item_id": "BAR-34X2-41",
                        "description": "3/4\" X 2\" X 41-3/4\" MIDDLE BRACE BARS (C1045 STEEL)",
                        "quantity": 4,
                        "unit_price": 284.10,
                        "unit_cost": 115.00,
                        "preferred_vendor": "NIBROCK",
                        "heat_number": "HT-C1045-9120",
                    },
                ],
                "purchase_orders": {
                    "NIBROCK": "PO-VEND-NIB-4491",
                },
                "total_revenue": 8682.22,
                "total_expense": 4030.00,
                "gross_margin_pct": 53.58,
                "status": "SYNCED_TO_SAGE",
            },
            {
                "order_id": "ORD-KN-LAN-2025-02",
                "sales_order_number": "SO-10882",
                "customer_po_number": "PO-8812",
                "customer_id": "CUST-KN-01",
                "customer_name": "Knauf Insulation (Lanett Plant)",
                "po_date": "2025-12-02",
                "payment_terms": "Net 60 Days",
                "shipping_method": "MOTOR FRT",
                "shipping_address": "Knauf Insulation, 1000 Knauf Dr, Lanett, AL 36863",
                "blind_shipping": True,
                "drawing_reference": "DWG# KN-LAN-5218",
                "heat_numbers": ["HT-300XL-9921", "HT-A992-7712"],
                "line_items": [
                    {
                        "item_id": "KN5218554",
                        "description": "300XL SECTIONAL SCREW CONVEYOR FLIGHT ASSEMBLY",
                        "quantity": 1,
                        "unit_price": 8940.00,
                        "unit_cost": 4120.00,
                        "preferred_vendor": "KWS",
                        "heat_number": "HT-300XL-9921",
                    },
                    {
                        "item_id": "BEAM-W8X31",
                        "description": "W8 X 31# WIDE FLANGE STRUCTURAL BEAM (20 FT)",
                        "quantity": 2,
                        "unit_price": 780.00,
                        "unit_cost": 420.00,
                        "preferred_vendor": "KWS",
                        "heat_number": "HT-A992-7712",
                    },
                ],
                "purchase_orders": {
                    "KWS": "PO-VEND-KWS-7719",
                },
                "total_revenue": 10500.00,
                "total_expense": 4960.00,
                "gross_margin_pct": 52.76,
                "status": "SYNCED_TO_SAGE",
            },
            {
                "order_id": "ORD-OC-STR-2026-03",
                "sales_order_number": "SO-10883",
                "customer_po_number": "PO-7704",
                "customer_id": "CUST-OC-02",
                "customer_name": "Owens Corning (Starr Plant)",
                "po_date": "2026-01-18",
                "payment_terms": "Net 150 Days",
                "shipping_method": "MOTOR FRT",
                "shipping_address": "Owens Corning, 100 Owens Corning Blvd, Starr, SC 29684",
                "blind_shipping": True,
                "drawing_reference": "DWG# OC-STR-262",
                "heat_numbers": ["HT-CER-6610"],
                "line_items": [
                    {
                        "item_id": "OC262064",
                        "description": "CERAMIC LINED SUCTION HOSE 4\" X 10FT WITH INTEGRAL FLANGES",
                        "quantity": 2,
                        "unit_price": 2450.00,
                        "unit_cost": 1050.00,
                        "preferred_vendor": "ZIBO",
                        "heat_number": "HT-CER-6610",
                    },
                ],
                "purchase_orders": {
                    "ZIBO": "PO-VEND-ZIB-2018",
                },
                "total_revenue": 4900.00,
                "total_expense": 2100.00,
                "gross_margin_pct": 57.14,
                "status": "SYNCED_TO_SAGE",
            },
        ]

        for ord_data in sample_orders:
            self.cache_order(ord_data)

    def cache_order(self, order_data: Dict[str, Any]) -> str:
        """Caches an order and updates customer, vendor, and heat number lookup indexes."""
        with self._lock:
            order_id = order_data.get("order_id") or f"ORD-{len(self._orders) + 1:04d}"
            so_num = order_data.get("sales_order_number", "")
            po_num = order_data.get("customer_po_number", "")
            cust_id = order_data.get("customer_id", "")
            cust_name = order_data.get("customer_name", "")

            self._orders[order_id] = order_data

            # Secondary key aliases. A customer PO number is only unique per
            # customer, so it maps to a list of orders.
            if so_num:
                self._so_index[str(so_num).upper()] = order_id
            if po_num:
                ids = self._po_index.setdefault(str(po_num).upper(), [])
                if order_id not in ids:
                    ids.append(order_id)

            # Customer Index (index by ID, Name, and tokens)
            keys_to_index = []
            if cust_id:
                keys_to_index.append(cust_id.upper())
            if cust_name:
                keys_to_index.append(cust_name.upper())
                for word in cust_name.upper().split():
                    if len(word) > 2:
                        keys_to_index.append(word)

            for c_key in keys_to_index:
                if c_key not in self._customer_index:
                    self._customer_index[c_key] = []
                if order_id not in self._customer_index[c_key]:
                    self._customer_index[c_key].append(order_id)

            # Vendor Index
            for vendor in order_data.get("purchase_orders", {}).keys():
                v_key = vendor.upper()
                if v_key not in self._vendor_index:
                    self._vendor_index[v_key] = []
                if order_id not in self._vendor_index[v_key]:
                    self._vendor_index[v_key].append(order_id)

            # Heat Number Index
            for heat in order_data.get("heat_numbers", []):
                self._heat_mtr_index[heat.upper()] = order_id

            return order_id

    def get_order(self, key: str, customer: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Instant (< 0.5ms) lookup by Order ID, SO Number, or Customer PO Number.

        A customer PO shared by more than one customer resolves only when
        ``customer`` (id or name) narrows it to one order; otherwise None, rather
        than whichever customer's order happened to be cached last.
        """
        if not key:
            return None
        order = self._orders.get(key) or self._orders.get(key.upper())
        if order:
            return order
        k = key.upper().strip()
        # SO numbers are filed as Sage issues them ("12930") but often asked for as "SO-12930".
        bare = k[3:] if k.startswith("SO-") else k
        for so in (k, bare, f"SO-{bare}"):
            oid = self._so_index.get(so)
            if oid and oid in self._orders:
                return self._orders[oid]
        candidates = self.get_orders_by_po(k)
        if customer:
            c = customer.upper().strip()
            candidates = [
                o for o in candidates
                if c in (str(o.get("customer_id", "")).upper(), str(o.get("customer_name", "")).upper())
            ]
        return candidates[0] if len(candidates) == 1 else None

    def get_orders_by_po(self, po_number: str) -> List[Dict[str, Any]]:
        """Every cached order carrying this customer PO number (any customer)."""
        ids = self._po_index.get((po_number or "").upper().strip(), [])
        return [self._orders[oid] for oid in ids if oid in self._orders]

    def get_orders_by_customer(self, customer_query: str) -> List[Dict[str, Any]]:
        """Returns all historical orders for a given customer name or ID."""
        q = customer_query.upper().strip()
        matched_ids = set()
        if not q:
            return []

        # One direction only: the query must appear in an indexed key. The
        # reverse test let a short indexed token such as "INC" match any query
        # that contained it.
        for key, ids in self._customer_index.items():
            if q == key or (len(q) >= 3 and q in key):
                matched_ids.update(ids)

        return [self._orders[oid] for oid in matched_ids if oid in self._orders]

    def get_orders_by_vendor(self, vendor_id: str) -> List[Dict[str, Any]]:
        """Returns all active and past purchase orders allocated to a specific vendor."""
        v_key = vendor_id.upper().strip()
        order_ids = self._vendor_index.get(v_key, [])
        return [self._orders[oid] for oid in order_ids if oid in self._orders]

    def get_vendor_context(self, vendor_id: str) -> Optional[Dict[str, Any]]:
        """Returns full vendor profile, lead times, MOQ, freight terms, and blind-ship rules."""
        return self._vendors.get(vendor_id.upper().strip())

    def get_all_vendors(self) -> Dict[str, Dict[str, Any]]:
        """Returns all registered supplier profiles."""
        return self._vendors

    def get_order_compliance_bundle(self, order_key: str) -> Optional[Dict[str, Any]]:
        """Assembles linked MTR heat certs, blind-ship verification, and profit margin in 1 call."""
        order = self.get_order(order_key)
        if not order:
            return None

        heat_numbers = order.get("heat_numbers", [])
        return {
            "order_id": order.get("order_id"),
            "sales_order_number": order.get("sales_order_number"),
            "customer_po_number": order.get("customer_po_number"),
            "customer_name": order.get("customer_name"),
            "blind_shipping_enforced": order.get("blind_shipping", True),
            "gross_margin_pct": order.get("gross_margin_pct", 0.0),
            "margin_status": "COMPLIANT" if float(order.get("gross_margin_pct", 0)) >= 25.0 else "REVIEW_REQUIRED",
            "heat_numbers": heat_numbers,
            "purchase_orders": order.get("purchase_orders", {}),
            "drawing_reference": order.get("drawing_reference", "N/A"),
        }

    def search_orders(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Fast fuzzy and keyword search across all cached orders."""
        q = query.lower().strip()
        results = []
        seen = set()
        tokens = [t for t in re.findall(r"[a-z0-9\-]+", q) if len(t) >= 3]

        for key, data in self._orders.items():
            if not isinstance(data, dict):
                continue
            # Dedupe on the order's own id, not the dict key it was found under.
            ord_id = data.get("order_id") or key
            if ord_id in seen:
                continue
            so = str(data.get("sales_order_number", "")).lower()
            po = str(data.get("customer_po_number", "")).lower()
            cust = str(data.get("customer_name", "")).lower()
            dwg = str(data.get("drawing_reference", "")).lower()
            items_str = " ".join([it.get("description", "").lower() for it in data.get("line_items", [])])

            match = False
            if not q:
                match = True
            elif q in so or q in po or q in cust or q in dwg or q in items_str:
                match = True
            elif (po and po in q) or (so and so in q) or (dwg and dwg in q):
                match = True
            elif any(t in po or t in so or t in cust for t in tokens if len(t) >= 4):
                match = True

            if match:
                results.append(data)
                seen.add(ord_id)
                if len(results) >= limit:
                    break

        return results

    def get_all_orders(self) -> List[Dict[str, Any]]:
        """Returns all unique master orders in cache."""
        seen = set()
        orders = []
        for k, v in self._orders.items():
            if not isinstance(v, dict):
                continue
            oid = v.get("order_id") or v.get("sales_order_number") or k
            if oid not in seen:
                seen.add(oid)
                orders.append(v)
        return orders

    def get_summary(self) -> Dict[str, Any]:
        """Provides high-level cache metrics."""
        unique_orders = len(self.get_all_orders())
        return {
            "cached_orders_count": unique_orders,
            "registered_vendors_count": len(self._vendors),
            "indexed_customers_count": len(self._customer_index),
            "indexed_heat_numbers_count": len(self._heat_mtr_index),
            "cache_status": "ONLINE_FAST_MEMORY",
        }


# Singleton accessor
agent_memory_cache = AgentMemoryCache()
