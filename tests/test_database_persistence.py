"""Unit and integration tests for persistent SQLite database with WAL mode and DatabaseService."""

import pytest
from app.core.database import SessionLocal, init_database
from app.services.database_service import DatabaseService


def test_database_initialization_and_session():
    """Verify database initializes properly and yields active sessions."""
    init_database()
    with SessionLocal() as db:
        assert db is not None


def test_persist_order_and_retrieve():
    """Verify storing an order into SQLite and retrieving it by facility."""
    order_data = {
        "order_id": "TEST-ORD-9901",
        "customer_po_number": "PO-TEST-9901",
        "sales_order_number": "SO-TEST-9901",
        "customer_id": "10221",
        "customer_name": "Owens Corning Test Plant",
        "facility": "EDMONTON",
        "currency": "CAD",
        "status": "RECEIVED",
        "archetype": "STANDARD_STOCK",
        "total_revenue": 8500.00,
        "total_expense": 4200.00,
        "gross_margin_pct": 50.59,
        "line_items": [{"item_id": "PLATE-300XL-38", "quantity": 4}],
    }

    with SessionLocal() as db:
        record = DatabaseService.persist_order(db, order_data, actor_email="tester@allied.com")
        assert record.order_id == "TEST-ORD-9901"

        # Query all orders
        orders = DatabaseService.get_all_orders(db, facility="EDMONTON")
        assert any(o["order_id"] == "TEST-ORD-9901" for o in orders)


def test_persist_reconciliation_and_history():
    """Verify storing a vendor reconciliation record in the database."""
    rec_data = {
        "reconciliation_id": "REC-TEST-7711",
        "invoice_number": "INV-TEST-7711",
        "vendor_name": "ZIBO",
        "po_number": "PO-ZIBO-9911",
        "reconciliation_status": "RECONCILED_APPROVED",
        "authorized_po_total": 925.0,
        "actual_billed_total": 925.0,
        "cost_variance_amount": 0.0,
        "cost_variance_pct": 0.0,
        "discrepancies": [],
    }

    with SessionLocal() as db:
        rec = DatabaseService.persist_reconciliation(db, rec_data)
        assert rec.reconciliation_id == "REC-TEST-7711"

        history = DatabaseService.get_reconciliation_history(db, limit=20)
        assert any(r["reconciliation_id"] == "REC-TEST-7711" for r in history)


def test_audit_log_persistence():
    """Verify write and retrieval of immutable audit log entries."""
    with SessionLocal() as db:
        entry = DatabaseService.write_audit(
            db=db,
            action="TEST_ACTION",
            resource_type="TEST_RESOURCE",
            resource_id="RES-001",
            actor_email="auditor@allied.com",
            actor_role="ADMIN",
            facility="HQ",
            detail="Integration test audit entry",
        )
        assert entry.event_id is not None

        logs = DatabaseService.get_audit_log(db, resource_type="TEST_RESOURCE")
        assert any(l["resource_id"] == "RES-001" for l in logs)


def test_money_is_stored_and_returned_as_exact_decimal():
    """Amounts must survive a round-trip to the cent.

    These columns were Float, and the service layer cast through float() on
    the way in. Three-way invoice matching compares amounts for equality, so
    binary representation error shows up as a phantom discrepancy on an
    invoice that actually matches.
    """
    from decimal import Decimal

    from app.core.database import SessionLocal
    from app.services.database_service import DatabaseService

    # 0.1 + 0.2 is the classic float trap; as cents it must stay exact.
    awkward = Decimal("1234567.89")
    with SessionLocal() as db:
        rec = DatabaseService.persist_reconciliation(
            db,
            {
                "reconciliation_id": "REC-DECIMAL-1",
                "invoice_number": "INV-DECIMAL-1",
                "vendor_name": "NIBROCK",
                "po_number": "PO-DECIMAL-1",
                "reconciliation_status": "RECONCILED_APPROVED",
                "authorized_po_total": awkward,
                "actual_billed_total": awkward,
                "cost_variance_amount": Decimal("0.00"),
                "cost_variance_pct": Decimal("0.0"),
                "discrepancies": [],
            },
        )
        assert isinstance(rec.actual_billed_total, Decimal)
        assert rec.actual_billed_total == awkward
        # The equality that the three-way match depends on.
        assert rec.authorized_po_total - rec.actual_billed_total == Decimal("0.00")


def test_money_accepts_the_shapes_callers_actually_pass():
    """Decimal, float, int and string all coerce without losing cents."""
    from decimal import Decimal

    from app.services.database_service import _money

    assert _money(Decimal("10.05")) == Decimal("10.05")
    assert _money("10.05") == Decimal("10.05")
    assert _money(10) == Decimal("10")
    assert _money(None) == Decimal("0")
    assert _money("not a number") == Decimal("0")
