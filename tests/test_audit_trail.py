"""Unit and integration tests for Immutable SOC 2 Audit Trail and Security Logging."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.services.database_service import DatabaseService

client = TestClient(app)


def test_audit_log_convenience_function():
    """Verify audit_log records events with actor context and facility."""
    audit_log(
        action="TEST_MUTATION",
        resource_type="ORDER",
        resource_id="SO-AUDIT-9921",
        actor_email="operator_lanett@allied.com",
        actor_role="OPERATOR",
        facility="HQ",
        detail="Updated payment terms on SO-AUDIT-9921",
    )

    with SessionLocal() as db:
        entries = DatabaseService.get_audit_log(db, resource_type="ORDER")
        matching = next((e for e in entries if e["resource_id"] == "SO-AUDIT-9921"), None)
        assert matching is not None
        assert matching["actor_email"] == "operator_lanett@allied.com"
        assert matching["facility"] == "HQ"
        assert matching["action"] == "TEST_MUTATION"


def test_audit_log_api_endpoint():
    """Verify /api/v1/audit endpoint queries logged records."""
    audit_log(
        action="SAGE_SYNC_TEST",
        resource_type="SAGE_EXPORT",
        resource_id="BATCH-001",
        actor_email="admin@alliedweldingsystems.com",
        actor_role="ADMIN",
        facility="EDMONTON",
        detail="Synchronized 4 orders to Sage 50 Peachtree database",
    )

    response = client.get("/api/v1/audit?resource_type=SAGE_EXPORT&limit=10")
    assert response.status_code == 200
    records = response.json().get("entries", [])
    assert len(records) >= 1
    assert any(r["resource_id"] == "BATCH-001" for r in records)
