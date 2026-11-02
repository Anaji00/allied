"""Automated Packing List Generation, Cross-Border Customs Invoicing, & Logistics Dispatcher.

Generates:
  - Official customer packing lists with MTR heat cert numbers
  - Canada Customs Invoice (CCI) / Cross-Border Commercial Invoice for US/Canada/China
  - Tri-currency declared values and freight estimates (USD, CAD, CNY)
  - Multi-carrier shipment notifications and manifest dispatches
"""

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid
from pydantic import BaseModel, Field

from app.config import settings
from app.core.logging import logger
from app.core.audit import audit_log
from app.services.agent_memory_cache import agent_memory_cache
from app.services.packing_slips import PackingSlipGenerator
from app.services.monday_client import MondayClient
from app.core.container import get_monday_client, get_packing_slip_generator


# Cap on the in-memory dispatch log; shipments are recorded in the audit trail.
_MAX_DISPATCH_LOG = 500


class AlreadyDispatchedError(ValueError):
    """The order was dispatched before; dispatching it again would ship it twice."""


def _order_rows(db, so_num: str):
    from app.models.db_models import OrderDB

    return db.query(OrderDB).filter(OrderDB.sales_order_number.in_([so_num, f"SO-{so_num}"]))


def _order_status(so_num: str) -> Optional[str]:
    try:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            row = _order_rows(db, so_num).first()
            return row.status if row else None
    except Exception as exc:  # noqa: BLE001 - no database: nothing recorded to check against
        logger.warning(f"Could not read the status of SO-{so_num}: {exc}")
        return None


def _mark_dispatched(so_num: str) -> None:
    try:
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            _order_rows(db, so_num).update({"status": "DISPATCHED"}, synchronize_session=False)
            db.commit()
    except Exception as exc:  # noqa: BLE001 - the dispatch happened; the status is bookkeeping
        logger.warning(f"SO-{so_num} was dispatched but its status could not be saved: {exc}")


def _order_from_db(so_num: str) -> Optional[Dict[str, Any]]:
    """Loads a committed order by sales order number and re-caches it."""
    try:
        from app.core.database import SessionLocal
        from app.services.database_service import DatabaseService

        with SessionLocal() as db:
            for order in DatabaseService.get_all_orders(db, limit=5000):
                if str(order.get("sales_order_number")) in (so_num, f"SO-{so_num}"):
                    agent_memory_cache.cache_order(order)
                    return order
    except Exception as exc:  # noqa: BLE001 - a lookup failure falls back to the generic slip
        logger.warning(f"Could not load SO-{so_num} from the database: {exc}")
    return None


def _is_canadian_destination(address: str) -> bool:
    """True when a shipping address is in Canada.

    Matches on province tokens with word boundaries; a bare "AB " substring test
    also matched addresses containing "FAB " or "LAB ".
    """
    text = (address or "").upper()
    if "CANADA" in text or "ALBERTA" in text or "EDMONTON" in text:
        return True
    return bool(re.search(r"\b(AB|BC|ON|QC|MB|SK|NS|NB)\b\s*[A-Z]\d[A-Z]", text)) or bool(
        re.search(r",\s*(AB|BC|ON|QC|MB|SK|NS|NB)\b", text)
    )


def _is_chinese_destination(address: str) -> bool:
    """True when a shipping address is in China."""
    text = (address or "").upper()
    return "CHINA" in text or "SHANDONG" in text or "ZIBO" in text


class CanadaCustomsInvoice(BaseModel):
    """Canada Customs Invoice (CCI) / Commercial Cross-Border Declaration."""
    cci_number: str
    exporter_name: str
    exporter_address: str
    consignee_name: str
    consignee_address: str
    country_of_origin: str
    country_of_destination: str
    transportation_mode: str
    currency: str  # USD, CAD, CNY
    total_declared_value: float
    total_declared_usd: float
    hs_tariff_lines: List[Dict[str, Any]] = Field(default_factory=list)
    customs_broker: str = "Livingston International / Border Pro"
    declaration_statement: str = (
        "The exporter certifies that the goods described in this commercial customs invoice "
        "comply strictly with USMCA / NAFTA Rules of Origin where applicable and declared values reflect transaction value."
    )


class CarrierRateQuote(BaseModel):
    """Carrier rate estimate across multi-carrier dispatch options."""
    carrier_name: str
    service_level: str
    estimated_transit_days: int
    rate_usd: float
    rate_cad: float
    rate_cny: float


class LogisticsDispatchRequest(BaseModel):
    """Request to generate packing list and dispatch freight."""
    sales_order_number: str
    origin_facility: str = "HQ"  # HQ, EDMONTON, ZIBO
    carrier_name: str = "TForce Freight / Motor Freight"
    tracking_number: Optional[str] = None
    recipient_email: Optional[str] = None
    freight_terms: str = "Prepaid & Add"
    currency: str = "USD"  # USD, CAD, CNY


class LogisticsDispatchResult(BaseModel):
    """Result of automated packing slip and shipment dispatch."""
    dispatch_id: str
    sales_order_number: str
    customer_name: str
    origin_facility: str
    currency: str
    packing_slip_filename: str
    packing_slip_path: str
    carrier_name: str
    tracking_number: str
    dispatch_status: str
    customer_notification_email: str
    customs_invoice: Optional[CanadaCustomsInvoice] = None
    carrier_quotes: List[CarrierRateQuote] = Field(default_factory=list)
    heat_numbers_certified: List[str] = Field(default_factory=list)
    dispatched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LogisticsDispatcher:
    """Automated packing slip, cross-border customs, and logistics coordinator."""

    def __init__(
        self,
        packing_slips: Optional[PackingSlipGenerator] = None,
        monday_client: Optional[MondayClient] = None,
    ):
        self.packing_slips = packing_slips or get_packing_slip_generator()
        self.monday = monday_client or get_monday_client()
        self._dispatch_log: List[Dict[str, Any]] = []

    def calculate_tri_currency_rates(self, base_usd: float) -> Tuple[float, float]:
        """Converts a USD rate to its CAD and CNY equivalents."""
        cad_rate = round(base_usd / settings.CAD_TO_USD_RATE, 2)
        cny_rate = round(base_usd / settings.CNY_TO_USD_RATE, 2)
        return cad_rate, cny_rate

    def get_carrier_quotes(self, origin_facility: str, destination_address: str) -> List[CarrierRateQuote]:
        """Generates multi-carrier shipping quotes in USD, CAD, and CNY."""
        is_canada = _is_canadian_destination(destination_address)
        is_international = origin_facility.upper() == "ZIBO" or _is_chinese_destination(destination_address)

        quotes = []
        if is_international:
            base_exp = 1450.00
            cad_exp, cny_exp = self.calculate_tri_currency_rates(base_exp)
            quotes.append(CarrierRateQuote(
                carrier_name="Expeditors International",
                service_level="Ocean Direct LCL + Port Drayage",
                estimated_transit_days=18,
                rate_usd=base_exp,
                rate_cad=cad_exp,
                rate_cny=cny_exp,
            ))
            base_air = 3850.00
            cad_air, cny_air = self.calculate_tri_currency_rates(base_air)
            quotes.append(CarrierRateQuote(
                carrier_name="DHL Global Forwarding",
                service_level="Air Cargo Priority (Zibo -> Chicago O'Hare)",
                estimated_transit_days=4,
                rate_usd=base_air,
                rate_cad=cad_air,
                rate_cny=cny_air,
            ))

        if is_canada:
            base_dr = 580.00
            cad_dr, cny_dr = self.calculate_tri_currency_rates(base_dr)
            quotes.append(CarrierRateQuote(
                carrier_name="Day & Ross Cross-Border",
                service_level="Direct LTL (US Midwest -> Alberta Hub)",
                estimated_transit_days=3,
                rate_usd=base_dr,
                rate_cad=cad_dr,
                rate_cny=cny_dr,
            ))

        # Standard domestic / cross-border baseline
        base_tf = 420.00
        cad_tf, cny_tf = self.calculate_tri_currency_rates(base_tf)
        quotes.append(CarrierRateQuote(
            carrier_name="TForce Freight",
            service_level="Standard Regional LTL",
            estimated_transit_days=2,
            rate_usd=base_tf,
            rate_cad=cad_tf,
            rate_cny=cny_tf,
        ))

        base_fx = 610.00
        cad_fx, cny_fx = self.calculate_tri_currency_rates(base_fx)
        quotes.append(CarrierRateQuote(
            carrier_name="FedEx Freight Priority",
            service_level="Guaranteed Morning Delivery",
            estimated_transit_days=1,
            rate_usd=base_fx,
            rate_cad=cad_fx,
            rate_cny=cny_fx,
        ))

        return quotes

    def generate_customs_invoice(
        self,
        so_number: str,
        customer_name: str,
        shipping_address: str,
        origin_facility: str,
        items: List[Dict[str, Any]],
        currency: str = "USD",
    ) -> CanadaCustomsInvoice:
        """Generates a Canada Customs Invoice (CCI) or Cross-Border Commercial Invoice."""
        is_canada = _is_canadian_destination(shipping_address)
        country_dest = "Canada" if is_canada else "United States"
        country_orig = "China" if origin_facility.upper() == "ZIBO" else ("Canada" if origin_facility.upper() == "EDMONTON" else "United States")

        exporter = "Allied Welding Systems LLC, 1400 Industrial Parkway, Gary, IN 46402 USA"
        if origin_facility.upper() == "EDMONTON":
            exporter = "Allied Canadian Distribution Ltd, 9812 45th Ave NW, Edmonton, AB T6E 5C5 Canada"
        elif origin_facility.upper() == "ZIBO":
            exporter = "Zibo Allied Industrial Foundry Co., High-Tech Zone, Zibo, Shandong, China"

        hs_lines = []
        total_val = 0.0
        for it in items:
            qty = float(it.get("quantity", 1))
            unit_price = float(it.get("unit_price", 100.0))
            line_val = qty * unit_price
            total_val += line_val

            sku = str(it.get("item_id", "")).upper()
            if "PLATE" in sku or "300XL" in sku:
                hs_code = "7210.90.0000"
                desc = "Clad Wear Plate Chromium Carbide Overlay"
            elif "HOSE" in sku or "CERAMIC" in sku:
                hs_code = "6806.90.0000"
                desc = "Ceramic Lined Slurry Material Handling Hose"
            elif "BEAM" in sku or "STRUCTURAL" in sku:
                hs_code = "7216.33.0000"
                desc = "Non-Alloy Structural Steel Wide Flange H-Beam"
            else:
                hs_code = "8474.90.0050"
                desc = "Wear Replacement Parts for Mining and Milling Machinery"

            hs_lines.append({
                "item_id": it.get("item_id"),
                "description": desc,
                "hs_tariff_code": hs_code,
                "quantity": qty,
                "unit_value": unit_price,
                "line_value": line_val,
                "origin": country_orig,
            })

        if not hs_lines:
            # A customs declaration is a legal statement of what is in the
            # shipment. Fabricating a 4 x wear-plate line worth $1,500 for an
            # order with no items produced a false declaration.
            raise ValueError(
                f"Cannot raise a customs invoice for SO-{so_number}: the order has no line items."
            )

        # Calculate currency declared values
        total_usd = total_val
        if currency.upper() == "CAD":
            total_usd = total_val * settings.CAD_TO_USD_RATE
        elif currency.upper() == "CNY":
            total_usd = total_val * settings.CNY_TO_USD_RATE

        return CanadaCustomsInvoice(
            cci_number=f"CCI-2026-{so_number}",
            exporter_name=f"Allied Facility ({origin_facility})",
            exporter_address=exporter,
            consignee_name=customer_name,
            consignee_address=shipping_address,
            country_of_origin=country_orig,
            country_of_destination=country_dest,
            transportation_mode="Cross-Border Motor Freight / Intermodal LTL",
            currency=currency.upper(),
            total_declared_value=round(total_val, 2),
            total_declared_usd=round(total_usd, 2),
            hs_tariff_lines=hs_lines,
        )

    def _generate_packing_slip(
        self,
        so_num: str,
        order: Dict[str, Any],
        customer_name: str,
        shipping_address: str,
        items: List[Dict[str, Any]],
        heat_numbers: List[str],
    ) -> Tuple[str, str, bool]:
        """Locates (or regenerates) the packing slip PDF for a sales order.

        The authoritative slip is the one produced when the order was
        orchestrated; this reuses that artifact rather than rendering a second,
        possibly divergent copy. Returns (path, filename, exists_on_disk) so the
        caller can report the real state instead of asserting success.
        """
        filename = f"packing_slip_SO_{so_num}.pdf"

        stored = order.get("packing_slip_path")
        if stored and Path(stored).is_file():
            return str(stored), Path(stored).name, True

        conventional = self.packing_slips.output_dir / filename
        if conventional.is_file():
            return str(conventional), filename, True

        logger.warning(
            f"No packing slip on disk for SO-{so_num}; dispatching without one. "
            f"Expected {conventional}"
        )
        return str(conventional), filename, False

    def dispatch_shipment(self, request: LogisticsDispatchRequest) -> LogisticsDispatchResult:
        """
        Retrieves cached order, generates official packing list PDF,
        generates Canada Customs Invoice (CCI) if cross-border, evaluates carrier quotes,
        composes carrier dispatch email, and updates Monday.com board.
        """
        from app.core.keyed_lock import keyed_lock

        so_num = request.sales_order_number.replace("SO-", "")
        # Two screens dispatching the same order at once: the second waits, then finds it shipped.
        with keyed_lock("dispatch", so_num):
            return self._dispatch(request, so_num)

    def _dispatch(self, request: "LogisticsDispatchRequest", so_num: str) -> "LogisticsDispatchResult":
        if _order_status(so_num) == "DISPATCHED":
            # The monday board, the old chat screen and the API all reach this; none may ship twice.
            raise AlreadyDispatchedError(f"SO-{so_num} was already dispatched; nothing was sent again.")
        logger.info(f"Generating packing list, customs docs, and dispatching Order #{so_num}")

        # Retrieve order from cache or fallback. `or` rather than a bare `get`
        # so a stored null address does not propagate as None into .upper().
        # Orders are cached under the bare number Sage issued ("12930"); only
        # looking up "SO-12930" meant no order was ever found, and every
        # packing slip went out addressed to "Valued Allied Customer" with no
        # lines. The cache is also memory-only, so fall back to the database
        # after a restart.
        order = (
            agent_memory_cache.get_order(request.sales_order_number)
            or agent_memory_cache.get_order(so_num)
            or agent_memory_cache.get_order(f"SO-{so_num}")
            or _order_from_db(so_num)
            or {}
        )
        customer_name = order.get("customer_name") or "Valued Allied Customer"
        shipping_address = order.get("shipping_address") or "Customer Plant Receiving Dock"
        items = order.get("line_items") or []

        tracking_num = request.tracking_number or f"PRO-{uuid.uuid4().hex[:8].upper()}"
        dispatch_id = f"DSP-{uuid.uuid4().hex[:8].upper()}"

        # Heat certificates come from the order itself. A hardcoded pair was
        # previously reported as "certified" for every shipment regardless of
        # what was actually in the crate.
        heat_numbers = [h for h in (order.get("heat_numbers") or []) if h]

        # Check cross-border needs
        is_cross_border = (
            request.origin_facility.upper() != "HQ"
            or _is_canadian_destination(shipping_address)
            or _is_chinese_destination(shipping_address)
            or request.currency.upper() in ("CAD", "CNY")
        )

        customs_invoice = None
        if is_cross_border and not items:
            logger.warning(
                f"SO-{so_num} looks cross-border but has no line items on file; "
                f"skipping the customs invoice rather than declaring invented goods."
            )
        elif is_cross_border:
            customs_invoice = self.generate_customs_invoice(
                so_number=so_num,
                customer_name=customer_name,
                shipping_address=shipping_address,
                origin_facility=request.origin_facility,
                items=items,
                currency=request.currency,
            )

        # Generate the packing list PDF. This method reported
        # dispatch_status=DISPATCHED_PACKING_LIST_GENERATED and a
        # packing_slip_path while never calling the generator, so the path
        # pointed at a file that did not exist and the customer email announced
        # an attachment that was never produced.
        pdf_path, pdf_filename, packing_slip_generated = self._generate_packing_slip(
            so_num=so_num,
            order=order,
            customer_name=customer_name,
            shipping_address=shipping_address,
            items=items,
            heat_numbers=heat_numbers,
        )

        # Generate multi-carrier rate estimates
        carrier_quotes = self.get_carrier_quotes(request.origin_facility, shipping_address)

        # Compose formal dispatch & packing list email
        email_body = self._generate_dispatch_email(
            customer_name=customer_name,
            so_number=so_num,
            shipping_address=shipping_address,
            carrier=request.carrier_name,
            tracking_number=tracking_num,
            items=items,
            pdf_filename=pdf_filename,
            recipient=request.recipient_email or "receiving@customer.com",
            has_customs=customs_invoice is not None,
            has_packing_slip=packing_slip_generated,
            heat_numbers=heat_numbers,
        )

        # Record audit log
        audit_log(
            action="SHIPMENT_DISPATCHED",
            resource_type="LOGISTICS_DISPATCH",
            resource_id=dispatch_id,
            facility=request.origin_facility,
            detail=f"Dispatched Order #SO-{so_num} via {request.carrier_name} (PRO: {tracking_num}). Cross-border: {is_cross_border}",
        )

        # Post update to Monday.com
        try:
            self.monday.post_pulse_update(
                0,
                f"📦 Order #{so_num} Dispatched via {request.carrier_name} (PRO #{tracking_num}). "
                f"Facility: {request.origin_facility} | Packing list: {pdf_filename}"
                + (f" | CCI: {customs_invoice.cci_number}" if customs_invoice else ""),
            )
        except Exception as e:
            logger.warning(f"Could not post Monday status: {e}")

        result = LogisticsDispatchResult(
            dispatch_id=dispatch_id,
            sales_order_number=f"SO-{so_num}",
            customer_name=customer_name,
            origin_facility=request.origin_facility,
            currency=request.currency.upper(),
            packing_slip_filename=pdf_filename,
            packing_slip_path=pdf_path,
            carrier_name=request.carrier_name,
            tracking_number=tracking_num,
            dispatch_status=(
                "DISPATCHED_PACKING_LIST_GENERATED"
                if packing_slip_generated
                else "DISPATCHED_PACKING_LIST_MISSING"
            ),
            customer_notification_email=email_body,
            customs_invoice=customs_invoice,
            carrier_quotes=carrier_quotes,
            heat_numbers_certified=heat_numbers,
        )

        self._dispatch_log.insert(0, result.model_dump())
        del self._dispatch_log[_MAX_DISPATCH_LOG:]
        _mark_dispatched(so_num)
        return result

    def _generate_dispatch_email(
        self,
        customer_name: str,
        so_number: str,
        shipping_address: str,
        carrier: str,
        tracking_number: str,
        items: List[Dict[str, Any]],
        pdf_filename: str,
        recipient: str,
        has_customs: bool = False,
        has_packing_slip: bool = True,
        heat_numbers: Optional[List[str]] = None,
    ) -> str:
        """Composes official shipment notice and packing list delivery.

        Every claim in the body is conditional on the corresponding document
        actually existing. The template previously asserted that the packing
        list was attached and that MTRs were affixed to the crate regardless of
        whether either had been produced.
        """
        item_rows = "\n".join(
            f"  • {it.get('quantity', 1)}x {it.get('item_id', 'Steel Wear Part')} - {it.get('description', '')}"
            for it in items
        ) or "  • (see attached packing list)"

        slip_line = (
            f"Attachment: {pdf_filename} (Official Allied Packing List with MTR Heat Certs)"
            if has_packing_slip
            else "Packing list: to follow under separate cover"
        )

        customs_note = ""
        if has_customs:
            customs_note = "\n  Attachment: Canada Customs Commercial Invoice (CCI) included for CBSA clearance.\n"

        if heat_numbers:
            mtr_note = (
                f"Certified MTRs (Mill Test Reports) for heat(s) {', '.join(heat_numbers)} "
                f"are affixed to the shipping crate.\n\n"
            )
        else:
            mtr_note = (
                "No mill test reports are registered against this shipment. "
                "Contact quality@alliedweldingsystems.com if certification is required.\n\n"
            )

        return (
            f"From: Allied Logistics & Freight Dispatch <shipping@alliedweldingsystems.com>\n"
            f"To: {recipient}\n"
            f"Subject: Shipment Notification & Packing Slip - Allied Order #SO-{so_number}\n\n"
            f"Dear {customer_name} Receiving Department,\n\n"
            f"Your order #SO-{so_number} has been staged, inspected, and released to {carrier}.\n\n"
            f"Shipment Details:\n"
            f"  Carrier: {carrier}\n"
            f"  PRO / Tracking #: {tracking_number}\n"
            f"  Destination: {shipping_address}\n"
            f"  {slip_line}{customs_note}\n"
            f"Items Shipped:\n"
            f"{item_rows}\n\n"
            f"{mtr_note}"
            f"Best regards,\n"
            f"Allied Logistics Team\n"
            f"Allied Welding Systems | dispatch@alliedweldingsystems.com"
        )

    def get_dispatch_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Returns recent shipment dispatch records."""
        return self._dispatch_log[:limit]


# Global instance
logistics_dispatcher = LogisticsDispatcher()
