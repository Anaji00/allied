"""Tests for Central Operations Exceptions & Discrepancies Queue."""

from fastapi.testclient import TestClient
from app.main import app
from app.services.exception_queue_service import (
    ExceptionItem,
    ResolveExceptionRequest,
    exception_queue_service,
)

client = TestClient(app)


def test_exception_queue_service_lifecycle():
    """Verify adding, listing, and resolving exceptions."""
    new_exc = ExceptionItem(
        exception_id="EXC-TEST-99",
        exception_type="INVOICE_COST_VARIANCE",
        severity="HIGH",
        title="Test Price Variance",
        description="Testing vendor price difference",
        reference_id="INV-TEST-001",
        vendor_or_customer="NIBROCK",
        variance_amount=50.0,
    )
    exception_queue_service.add_exception(new_exc)

    items = exception_queue_service.list_exceptions(status_filter="OPEN")
    assert any(i.exception_id == "EXC-TEST-99" for i in items)

    # Resolve
    res = exception_queue_service.resolve_exception(
        "EXC-TEST-99",
        ResolveExceptionRequest(action_type="ACCEPT_PRICE_OVERRIDE", resolution_notes="Audited & approved"),
    )
    assert res["status"] == "success"
    assert res["action_executed"] == "ACCEPT_PRICE_OVERRIDE"


def test_exception_queue_api_endpoints():
    """Verify REST API listing and resolving exceptions."""
    # Resolve an exception this test owns. Picking whatever happened to be
    # first in the list meant resolving an item an earlier test had already
    # resolved, which the service now correctly refuses.
    exception_queue_service.add_exception(
        ExceptionItem(
            exception_id="EXC-API-1",
            exception_type="UNMATCHED_SKU",
            severity="MEDIUM",
            title="API endpoint probe",
            description="Created by the API test so it has an open item to resolve",
            reference_id="PO-API-1",
            vendor_or_customer="Owens Corning",
        )
    )

    resp = client.get("/api/v1/exceptions")
    assert resp.status_code == 200
    excs = resp.json()
    assert any(e["exception_id"] == "EXC-API-1" for e in excs)

    res_resp = client.post(
        "/api/v1/exceptions/EXC-API-1/resolve",
        json={"action_type": "RESOLVE_TEST", "resolution_notes": "API test resolution"},
    )
    assert res_resp.status_code == 200
    assert res_resp.json()["status"] == "success"

    # Resolving it a second time is refused rather than silently repeated.
    again = client.post(
        "/api/v1/exceptions/EXC-API-1/resolve",
        json={"action_type": "RESOLVE_TEST", "resolution_notes": "duplicate"},
    )
    assert again.status_code == 422


def test_concurrent_resolution_runs_the_action_exactly_once():
    """Two operators resolving the same exception must not both succeed.

    Without a lock around the check-then-set, both callers read status=OPEN
    before either wrote, so the resolution action ran twice -- for a
    stale-price override or a supplier dispute, that is a duplicated
    side effect.
    """
    from concurrent.futures import ThreadPoolExecutor

    exception_queue_service.add_exception(
        ExceptionItem(
            exception_id="EXC-RACE-1",
            exception_type="INVOICE_COST_VARIANCE",
            severity="HIGH",
            title="Race condition probe",
            description="Resolved concurrently by two callers",
            reference_id="INV-RACE-1",
            vendor_or_customer="NIBROCK",
        )
    )

    def resolve():
        try:
            exception_queue_service.resolve_exception(
                "EXC-RACE-1",
                ResolveExceptionRequest(action_type="ACCEPT", resolution_notes="concurrent"),
            )
            return "ok"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [f.result() for f in [pool.submit(resolve) for _ in range(8)]]

    assert results.count("ok") == 1, f"resolution ran {results.count('ok')} times, expected once"
    assert results.count("rejected") == 7
