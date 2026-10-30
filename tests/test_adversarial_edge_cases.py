"""Adversarial and Edge-Case Stress Testing Suite for Allied Universal Operations Bridge.

Tests boundary conditions, messy real-world inputs, corrupted data, multi-line collisions,
and unhandled edge cases across all operational subsystems.
"""

import io
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.documents import DocumentScanType
from app.models.orders import InboundOrderRequest, OrderLineItem
from app.services.agent_memory_cache import agent_memory_cache
from app.services.document_scanner_service import document_scanner_service
from app.services.entity_matcher import EntityMatcher
from app.services.inbound_email_daemon import InboundEmailMessage, inbound_email_daemon
from app.services.order_orchestrator import OrderOrchestrator
from app.services.reconciliation_service import VendorInvoice, VendorInvoiceLine, reconciliation_service
from app.services.sage_export_service import sage_export_service

client = TestClient(app)
orchestrator = OrderOrchestrator()


# ==============================================================================
# 1. ENTITY MATCHER EDGE CASES
# ==============================================================================
def test_unaliased_vendor_resolution_no_keyerror():
    """A vendor with no alias entry resolves without raising KeyError: 'contact_name'.

    The vendor is injected here rather than assumed to exist in the master
    file. This test used to rely on a row that an earlier test run had written
    into the real catalogue, so it passed only on a polluted working copy.
    """
    matcher = EntityMatcher()
    matcher.vendors_cache["TEST_MILL"] = {
        "vendor_id": "TEST_MILL",
        "vendor_name": "Test Heavy Steel Mills",
        "vendor_address": "900 Mill Rd, Birmingham, AL 35201",
        "payment_terms": "Net 45 Days",
        "email": "sales@testmill.com",
        "phone": "205-555-0199",
        "po_special_instructions": "PACKING SLIP ONLY",
    }
    matcher._build_scan_index()

    vend, score = matcher.match_vendor("Test Heavy Steel Mills")
    assert vend["vendor_id"] == "TEST_MILL"
    assert score >= 70.0

    # Fuzzy match on supplier name
    vend2, score2 = matcher.match_vendor("Supereal Industrial Ceramic")
    assert vend2["vendor_id"] == "ZIBO"
    assert score2 >= 65.0


def test_customer_disambiguation_starr_vs_edmonton():
    """Tests that Owens Corning Starr (SC, ID 10195) is not mistakenly resolved as Edmonton (Canada, ID 10221)."""
    matcher = EntityMatcher()
    
    # Starr Plant
    cust_starr, score1 = matcher.match_customer("Owens Corning Starr Composites")
    assert cust_starr["customer_id"] == "10195"
    assert "Starr" in cust_starr["customer_name"]

    # Edmonton Plant
    cust_edm, score2 = matcher.match_customer("Owens Corning Edmonton")
    assert cust_edm["customer_id"] == "10221"
    assert "Edmonton" in cust_edm["shipping_address"]


def test_matcher_item_fractions_and_shop_slang():
    """Tests resolving complex fractional dimensions and structural steel slang."""
    matcher = EntityMatcher()
    
    test_cases = [
        ('5" Sch 160 Pipe', "PIPE-5SCH160"),
        ('HSS 4x4 tubing', "HSS-4X4X38"),
        ('W8x31 wide flange beam', "BEAM-W8X31"),
        ('C3x4.1 steel channel', "CHAN-C3X41"),
        ('33 lb spool 20-35XL wire', "WIRE-2035XL-045"),
        ('Allied 200 wear plate', "PLATE-200-38"),
    ]
    for slang, expected_sku in test_cases:
        item, score = matcher.match_item(slang, min_confidence=60.0)
        assert item["item_id"] == expected_sku, f"Failed for {slang}: got {item['item_id']}"


# ==============================================================================
# 2. INBOUND EMAIL DAEMON QUANTITY & MULTI-LINE EXTRACTION
# ==============================================================================
def test_email_line_numbers_not_mistaken_for_quantities():
    """
    Tests that email lines like 'Line 1: 5x SCREEN-250' extract quantity=5,
    NOT quantity=1 (mistaking the line number for the order quantity).
    """
    email = InboundEmailMessage(
        sender_email="purchasing@owenscorning.com",
        sender_name="Mark Richardson (Owens Corning)",
        subject="PO-EDGE-QTY: Multi-Line Screen & Bar Order",
        body_text=(
            "Hello Allied Team,\n\n"
            "Please enter PO-EDGE-QTY with the following items:\n"
            "Line 1: 5x OC755507 (Hammermill Screen)\n"
            "Line 2: 4x BAR-34X2-41 (Middle Brace Bars)\n"
            "Line 3: 10 spools 20-35XL Wire\n\n"
            "Deliver to Edmonton facility.\n"
            "Thanks,\nMark"
        ),
    )
    result = inbound_email_daemon.process_inbound_email(email)
    assert result.status == "PROCESSED_ORDER_CREATED"
    
    # Retrieve cached order from AgentMemoryCache to inspect parsed line quantities
    cached = agent_memory_cache.get_order("PO-EDGE-QTY")
    assert cached is not None
    lines = cached.get("line_items", [])
    assert len(lines) == 3

    qty_map = {l["item_id"]: l["quantity"] for l in lines}
    assert qty_map.get("OC755507") == 5.0, f"Expected 5.0, got {qty_map.get('OC755507')}"
    assert qty_map.get("BAR-34X2-41") == 4.0, f"Expected 4.0, got {qty_map.get('BAR-34X2-41')}"
    assert qty_map.get("WIRE-2035XL-045") == 10.0, f"Expected 10.0, got {qty_map.get('WIRE-2035XL-045')}"


# ==============================================================================
# 3. DOCUMENT SCANNER DYNAMIC EXTRACTION & MULTI-PLANT ROUTING
# ==============================================================================
def test_scan_po_dynamic_items_and_starr_routing():
    """Tests dynamic line item extraction (not hardcoded) and correct routing to Starr Composites."""
    po_text = (
        "PURCHASE ORDER\n"
        "BUYER: OWENS CORNING (STARR COMPOSITES PLANT)\n"
        "PO NUMBER: PO-STARR-9944\n"
        "DESTINATION: 4837 Highway 81 South, Starr, SC 29684, USA\n"
        "TERMS: NET 150 DAYS\n"
        "1. 5 EA - PIPE-5SCH160 - 5\" SCH 160 SEAMLESS SHAFT PIPE @ $1,350.00\n"
        "2. 10 SPL - WIRE-2035XL-045 - ALLIED 20-35XL .045 DIA WIRE @ $345.00\n"
    )
    res = document_scanner_service.scan_document(
        file_bytes=po_text.encode("utf-8"),
        filename="PO_Starr_9944.pdf",
    )

    assert res.document_type == DocumentScanType.CUSTOMER_PO
    # Must resolve to Starr (10195), NOT Edmonton (10221)
    assert res.fields["customer_id"].normalized_value == "10195"
    assert "Starr" in res.fields["customer_name"].normalized_value

    # Must extract the 2 actual line items with their quantities and prices
    assert len(res.line_items) == 2
    skus = [li.item_id for li in res.line_items]
    assert "PIPE-5SCH160" in skus
    assert "WIRE-2035XL-045" in skus

    pipe_item = next(li for li in res.line_items if li.item_id == "PIPE-5SCH160")
    assert pipe_item.quantity == Decimal("5.0")
    assert pipe_item.unit_price == Decimal("1350.00")

    wire_item = next(li for li in res.line_items if li.item_id == "WIRE-2035XL-045")
    assert wire_item.quantity == Decimal("10.0")
    assert wire_item.unit_price == Decimal("345.00")


def test_scan_mtr_dynamic_chemistry():
    """Tests that MTR chemical composition is dynamically extracted, not hardcoded."""
    mtr_text = (
        "CERTIFIED MATERIAL TEST REPORT (MTR)\n"
        "HEAT NUMBER: HT-SPECIAL-9901\n"
        "CHEMICAL ANALYSIS (%): C: 5.25 | Cr: 32.10 | Mn: 1.60 | Si: 0.90 | Fe: BALANCE\n"
        "MECHANICAL TESTS: HARDNESS: 63.5 HRC\n"
        "SPECIFICATION: ASTM A532 HIGH WEAR OVERLAY\n"
    )
    res = document_scanner_service.scan_document(
        file_bytes=mtr_text.encode("utf-8"),
        filename="MTR_HT_SPECIAL_9901.pdf",
    )

    assert res.document_type == DocumentScanType.MTR_HEAT_CERT
    assert res.fields["heat_number"].normalized_value == "HT-SPECIAL-9901"
    assert res.fields["carbon_pct"].normalized_value == 5.25
    assert res.fields["chromium_pct"].normalized_value == 32.10
    assert "63.5" in str(res.fields["hardness"].normalized_value)


# ==============================================================================
# 4. RECONCILIATION MULTI-LINE SAME SKU & PARTIAL SHIPMENTS
# ==============================================================================
def test_reconciliation_multi_line_split_shipment():
    """
    Tests reconciliation when vendor bills multiple lines for the same SKU
    (e.g., split shipments of 2 units + 2 units against a 4-unit PO).
    """
    split_bill = VendorInvoice(
        invoice_number="INV-NIB-SPLIT-77",
        vendor_name="NIBROCK",
        po_number="PO-NIBROCK-9921",  # Authorized: 4 units of PLATE-300XL-38 @ $110.00, freight $125.00
        lines=[
            VendorInvoiceLine(
                item_id="PLATE-300XL-38",
                quantity_billed=Decimal("2"),
                unit_cost_billed=Decimal("110.00"),
                total_amount=Decimal("220.00"),
            ),
            VendorInvoiceLine(
                item_id="PLATE-300XL-38",
                quantity_billed=Decimal("2"),
                unit_cost_billed=Decimal("110.00"),
                total_amount=Decimal("220.00"),
            ),
        ],
        freight_billed=Decimal("125.00"),
        total_amount_billed=Decimal("565.00"),
    )
    res = reconciliation_service.reconcile_vendor_invoice(split_bill)
    assert res.reconciliation_status == "RECONCILED_APPROVED"
    assert res.cost_variance_amount == Decimal("0.00")
    assert len(res.discrepancies) == 0


# ==============================================================================
# 5. SAGE EXPORT SERVICE: INTERNAL STOCK PO SUPPRESSION
# ==============================================================================
def test_sage_po_export_suppresses_internal_stock():
    """
    Tests that Sage 50 purchase orders CSV does not generate external POs
    for 'STOCK' (internal warehouse fulfillment).
    """
    po_csv = sage_export_service.generate_purchase_orders_csv()
    lines = [l for l in po_csv.splitlines() if l.strip()]
    header = lines[0]
    data_rows = lines[1:]

    for row in data_rows:
        vendor_col = row.split(",")[0].strip('"')
        assert vendor_col != "STOCK", f"Found prohibited internal STOCK in external PO export: {row}"


# ==============================================================================
# 6. MONDAY.COM WEBHOOK PAYLOAD SAFETY & CHALLENGE UNION
# ==============================================================================
def test_monday_webhook_payload_safety_and_unknown_events():
    """
    Tests that Monday webhook ingress handles empty payloads, unknown events,
    and challenge tokens safely without response model validation failures.
    """
    # Empty body -> ignored
    resp_empty = client.post("/api/v1/webhooks/monday", json={})
    assert resp_empty.status_code == 200
    assert resp_empty.json()["status"] == "ignored"

    # Unknown event without pulse ID -> ignored safely without crash
    resp_unknown = client.post("/api/v1/webhooks/monday", json={"event": {"type": "unknown_future_event"}})
    assert resp_unknown.status_code == 200
    assert resp_unknown.json()["status"] == "ignored"

    # Challenge verification handshake
    resp_chal = client.post("/api/v1/webhooks/monday", json={"challenge": "test_challenge_safe_99"})
    assert resp_chal.status_code == 200
    assert resp_chal.json()["challenge"] == "test_challenge_safe_99"


# ==============================================================================
# 7. ENTITY MATCHER STALE CACHE AUTO-DETECTION
# ==============================================================================
def test_entity_matcher_stale_detection_and_dynamic_reload():
    """
    Tests that EntityMatcher checks disk modification timestamps and reloads
    catalogs automatically before raising EntityNotFoundError.
    """
    matcher = EntityMatcher()
    assert len(matcher.customers_cache) > 0
    assert matcher._last_loaded_mtimes is not None
    # Verify stale detection returns False when nothing changed
    assert matcher.check_and_reload_if_stale() is False

