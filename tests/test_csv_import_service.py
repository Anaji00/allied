"""Unit and Integration Tests for CSV Import Service, RAG Master CSV Indexing, and On-Demand Document Scanning."""

import csv
import io
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

from app.main import app
from app.services.csv_import_service import csv_import_service
from app.services.rag_service import rag_service
from app.models.rag_chat import RAGQueryRequest

client = TestClient(app)


def test_csv_get_templates():
    """Verify that all standard CSV templates are available and populated."""
    templates = csv_import_service.get_templates()
    assert "ITEMS" in templates
    assert "CUSTOMERS" in templates
    assert "VENDORS" in templates
    assert "ORDERS_BATCH" in templates
    assert "item_id" in templates["ITEMS"]
    assert "customer_id" in templates["CUSTOMERS"]
    assert "vendor_id" in templates["VENDORS"]
    assert "customer_po_number" in templates["ORDERS_BATCH"]


def test_csv_templates_endpoint():
    """Verify GET /api/v1/csv/templates endpoint returns templates."""
    response = client.get("/api/v1/csv/templates")
    assert response.status_code == 200
    data = response.json()
    assert "templates" in data
    assert "ITEMS" in data["templates"]
    assert "ORDERS_BATCH" in data["templates"]


def test_csv_import_items_and_rag_indexing():
    """Verify importing items updates master_items.csv and is indexed in RAG copilot."""
    csv_payload = (
        "item_id,description,gl_sales_account,gl_inventory_account,standard_cost,base_price,preferred_vendor,uom,category,stock_on_hand,min_stock_level\n"
        'TEST-PLATE-999,"TEST 300XL WEAR PLATE 48X96",4000-00,1200-00,1500.00,3200.00,NIBROCK,SHT,Clad Plate,5.0,2.0\n'
    )
    res = csv_import_service.import_csv(csv_payload, "ITEMS")
    assert res.status == "SUCCESS"
    assert res.rows_processed == 1
    assert (res.records_created + res.records_updated) >= 1

    # Verify RAG service immediately knows about the imported item
    rag_service.reload_knowledge_bases()
    query_res = rag_service.answer_query(RAGQueryRequest(
        query="What is the list price, wholesale cost, and vendor for TEST-PLATE-999?",
        dev_bypass=True,
    ))
    assert "TEST-PLATE-999" in query_res.answer or any("TEST-PLATE-999" in c.title for c in query_res.citations)
    assert query_res.confidence_score > 0.4


def test_csv_import_customers():
    """Verify importing customers via endpoint."""
    csv_payload = (
        "customer_id,customer_name,billing_address,shipping_address,payment_terms,sales_rep,email,contact_name,shipping_method\n"
        'CUST-TEST-101,"Test Processing Plant Ltd","100 Industrial Way, Dallas, TX 75201","100 Industrial Way, Dallas, TX 75201",Net 60 Days,Karissa M. Rakestraw,ap@testplant.com,John Doe,MOTOR FRT\n'
    )
    response = client.post(
        "/api/v1/csv/import",
        json={"import_type": "CUSTOMERS", "csv_text": csv_payload}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["SUCCESS", "PARTIAL_SUCCESS"]
    assert data["rows_processed"] == 1


def test_csv_import_vendors():
    """Verify importing vendors via endpoint."""
    csv_payload = (
        "vendor_id,vendor_name,vendor_address,payment_terms,email,phone,po_special_instructions\n"
        'TEST_MILL,"Test Heavy Steel Mills","900 Mill Rd, Birmingham, AL 35201",Net 45 Days,sales@testmill.com,205-555-0199,"PACKING SLIP ONLY"\n'
    )
    response = client.post(
        "/api/v1/csv/import",
        json={"import_type": "VENDORS", "csv_text": csv_payload}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["SUCCESS", "PARTIAL_SUCCESS"]
    assert data["rows_processed"] == 1


def test_copilot_scan_internal_doc_on_prompt():
    """Verify Copilot chat dynamically scans internal documents on any given prompt."""
    prompt = (
        "Please scan internal document SAMPLE-PO-OC from Owens Corning Edmonton and show line items."
    )
    req = RAGQueryRequest(query=prompt, dev_bypass=True)
    res = rag_service.answer_query(req)

    # Must contain scanned document citation with 1.0 relevance
    scanned_citation = next((c for c in res.citations if c.source_type == "SCANNED_DOC"), None)
    assert scanned_citation is not None
    assert scanned_citation.relevance_score == 1.0

    # Must produce formatted document analysis in answer
    assert "Scanned Document Analysis" in res.answer or "OC755507" in res.answer
    assert any(a.action_type in ["STAGE_SAGE_ORDER", "STAGE_OPERATIONS", "EXECUTE_END_TO_END_ORDER"] for a in res.suggested_actions)


def test_copilot_scan_pasted_raw_document():
    """Verify Copilot chat dynamically scans raw multi-line document text pasted into prompt."""
    raw_doc = (
        "PURCHASE ORDER\n"
        "BUYER: KNAUF INSULATION (LANETT PLANT)\n"
        "PO NUMBER: PO-KNF-LAN-8821\n"
        "TERMS: NET 30 DAYS\n"
        "1. 1 EA | KN5218554 | 16\" DIA X 16\" PITCH X 14' LG SECTIONAL SCREW | $35,378.14 EA"
    )
    req = RAGQueryRequest(query=raw_doc, dev_bypass=True)
    res = rag_service.answer_query(req)

    assert any(c.source_type == "SCANNED_DOC" for c in res.citations)
    assert "KN5218554" in res.answer or "Knauf" in res.answer or "Customer Purchase Order" in res.answer


def test_copilot_execute_csv_import_action():
    """Verify Copilot action execution for EXECUTE_CSV_IMPORT."""
    csv_text = (
        "item_id,description,gl_sales_account,gl_inventory_account,standard_cost,base_price,preferred_vendor,uom,category,stock_on_hand,min_stock_level\n"
        'BAR-COPILOT-01,"COPILOT BRACE BAR 1X2",4000-00,1200-00,85.00,195.00,STOCK,EA,Fabrication,10.0,2.0\n'
    )
    response = client.post(
        "/api/v1/chat/execute-action",
        json={
            "action_type": "EXECUTE_CSV_IMPORT",
            "payload": {"import_type": "ITEMS", "csv_text": csv_text}
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["data"]["status"] == "SUCCESS"
