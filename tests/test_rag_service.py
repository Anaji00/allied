"""Unit tests for the Enterprise RAG Knowledge & Historical Estimating Copilot."""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models.rag_chat import RAGQueryRequest
from app.services.rag_service import rag_service

client = TestClient(app)


def test_rag_knowledge_summary_endpoint():
    """Verify document index summary across all 6 knowledge bases."""
    response = client.get("/api/v1/chat/sources")
    assert response.status_code == 200
    data = response.json()
    assert data["total_documents"] >= 10
    assert "HISTORICAL_ESTIMATE" in data["categories"]
    assert "WELDING_WPS" in data["categories"]
    assert "SUPPLIER_AGREEMENT" in data["categories"]


def test_rag_prompt_templates_endpoint():
    """Verify preloaded business prompt starters."""
    response = client.get("/api/v1/chat/templates")
    assert response.status_code == 200
    templates = response.json()
    assert len(templates) >= 4
    assert any(t["id"] == "HISTORICAL_QUOTE" for t in templates)


def test_rag_query_historical_estimate():
    """Verify historical quote recall for Owens Corning Hammermill Screen."""
    req = RAGQueryRequest(
        query="What did we charge Owens Corning for the 2-1/2 inch hole hammermill screen on PO-9921?",
        dev_bypass=True,
    )
    res = rag_service.answer_query(req)
    assert res is not None
    assert "3,772" in res.answer or "OC755507" in res.answer or "Owens Corning" in res.answer
    assert len(res.citations) > 0
    assert res.citations[0].source_type in ["HISTORICAL_ESTIMATE", "CACHED_ORDER"]
    assert len(res.suggested_actions) > 0
    assert res.suggested_actions[0].action_type == "STAGE_SAGE_ORDER"


def test_rag_query_welding_wps_and_plasma():
    """Verify technical WPS parameters and plasma cutting directions."""
    req = RAGQueryRequest(
        query="What are the preheat requirements and plasma cutting directions for Allied 300XL clad plate?",
        dev_bypass=True,
    )
    res = rag_service.answer_query(req)
    assert res is not None
    assert "250°F" in res.answer or "preheat" in res.answer.lower()
    assert "mild steel" in res.answer.lower() or "plasma" in res.answer.lower()
    assert any(c.source_type == "WELDING_WPS" for c in res.citations)


def test_rag_query_supplier_blind_shipping():
    """Verify supplier terms and blind-shipping white-label rules."""
    req = RAGQueryRequest(
        query="What are our blind shipping rules for Nibrock and Zibo ceramic hoses?",
        dev_bypass=True,
    )
    res = rag_service.answer_query(req)
    assert res is not None
    assert "blind" in res.answer.lower()
    assert len(res.citations) > 0


def test_cache_api_endpoints():
    """Verify REST API cache inspection endpoints."""
    # Summary
    sum_resp = client.get("/api/v1/cache/summary")
    assert sum_resp.status_code == 200
    assert sum_resp.json()["cache_status"] == "ONLINE_FAST_MEMORY"

    # Orders search
    ord_resp = client.get("/api/v1/cache/orders?customer=Owens")
    assert ord_resp.status_code == 200
    assert len(ord_resp.json()) >= 1

    # Vendor context
    vend_resp = client.get("/api/v1/cache/vendors/NIBROCK")
    assert vend_resp.status_code == 200
    assert vend_resp.json()["vendor_id"] == "NIBROCK"


def test_rag_execute_vendor_reprice_action():
    """Verify EXECUTE_VENDOR_REPRICE tool execution via Copilot."""
    resp = client.post(
        "/api/v1/chat/execute-action",
        json={
            "action_type": "EXECUTE_VENDOR_REPRICE",
            "payload": {
                "vendor": "NIBROCK",
                "item_id": "PLATE-300XL-38",
                "po_number": "PO-TEST-REPRICE",
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ["success", "halted_for_reprice"]
    assert "freshness" in data


def test_rag_execute_crm_form_request_action():
    """Verify EXECUTE_CRM_FORM_REQUEST tool execution via Copilot."""
    resp = client.post(
        "/api/v1/chat/execute-action",
        json={
            "action_type": "EXECUTE_CRM_FORM_REQUEST",
            "payload": {
                "pulse_id": 99123,
                "lead_email": "maintenance@cementplant.com",
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "form_link" in data["data"]


def test_rag_execute_outreach_email_action():
    """Verify EXECUTE_OUTREACH_EMAIL tool execution via Copilot."""
    resp = client.post(
        "/api/v1/chat/execute-action",
        json={
            "action_type": "EXECUTE_OUTREACH_EMAIL",
            "payload": {
                "name": "Tom Davis",
                "title": "Plant Operations Lead",
                "company": "Texas Quarry Systems",
                "industry": "Aggregates",
                "email": "tdavis@texasquarry.com",
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "Tom Davis" in data["data"]["email_body"]
    assert "Allied Welding Systems" in data["data"]["email_body"]

