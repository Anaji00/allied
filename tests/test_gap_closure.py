"""Guards for the automation gaps closed after the October review.

Each test here pins a behaviour that did not exist before: reading a supplier
invoice and checking it in one step, sending the mail the system drafts,
polling a mailbox, reading a drawing without inventing its contents, and
deriving wear from elapsed service life rather than a frozen number.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.config import settings
from app.models.documents import DocumentScanType
from app.services.document_scanner_service import DocumentScannerService
from app.services.outbound_email_service import OutboundEmailService
from app.services.sales_service import SalesService


# --------------------------------------------------------------- invoices

INVOICE = b"""NIBROCK, INC.
VENDOR INVOICE
Invoice No: NIB-99812
Invoice Date: 2026-10-24
Your PO Number: PO-NIBROCK-9921
REMIT TO: Nibrock Inc, PO Box 99, Chunchula AL 36521

4 EA Chromium Carbide Overlay Plate @ 128.50
Freight Charge: 240.00
TOTAL DUE: 754.00
"""


@pytest.fixture
def scanner():
    return DocumentScannerService()


def test_supplier_invoice_is_recognised_not_mistaken_for_an_order(scanner):
    """A supplier bill quotes our PO, so it used to classify as a customer order.

    Misread that way, an incoming bill becomes an order to fulfil. This is the
    worst available misclassification and it has to stay fixed.
    """
    res = scanner.scan_document(INVOICE, "INV-NIB-99812.txt")
    assert res.document_type == DocumentScanType.VENDOR_INVOICE
    assert res.document_type != DocumentScanType.CUSTOMER_PO


def test_customer_po_that_mentions_invoicing_is_still_a_customer_po(scanner):
    """The negative case: 'invoice' on a PO must not flip it to a bill."""
    po = (
        b"PURCHASE ORDER\n"
        b"PO Number: 4518695782-2\n"
        b"BUYER: Owens Corning Edmonton\n"
        b"Please invoice us at accounts@owenscorning.com on despatch.\n"
        b"2 EA Hammermill Screens @ 3772.91\n"
    )
    assert scanner.scan_document(po, "PO_4518695782.txt").document_type == DocumentScanType.CUSTOMER_PO


def test_scanning_an_invoice_runs_the_three_way_match(scanner):
    """Reading the bill and checking the bill are one step.

    Separated, the check is a job somebody has to remember, which is exactly
    why it gets skipped in the weeks the big bills arrive.
    """
    res = scanner.scan_document(INVOICE, "INV-NIB-99812.txt")

    assert res.fields["invoice_number"].normalized_value == "NIB-99812"
    assert res.fields["po_number"].normalized_value == "PO-NIBROCK-9921"
    assert res.reconciliation is not None, "an invoice citing a known PO must be checked on scan"
    assert res.reconciliation["reconciliation_status"] != "RECONCILED_APPROVED"
    assert Decimal(str(res.reconciliation["cost_variance_amount"])) > 0
    assert res.reconciliation["dispute_email_draft"]


def test_invoice_without_a_purchase_order_is_refused_not_matched(scanner):
    """A bill citing no Allied PO cannot be checked, and must say so."""
    orphan = (
        b"ACME SUPPLY CO\nINVOICE NUMBER: ACM-5511\nAMOUNT DUE: 48000.00\n"
        b"2 EA Consulting Services @ 24000.00\n"
    )
    res = scanner.scan_document(orphan, "INV-ACM-5511.txt")
    assert res.document_type == DocumentScanType.VENDOR_INVOICE
    assert res.reconciliation is None
    assert any("purchase order" in n.lower() for n in res.compliance_notes)


def test_invoice_total_that_disagrees_with_its_lines_is_flagged(scanner):
    """A total that does not equal lines plus freight means something was missed.

    Reconciling against a misread total produces a confident wrong answer,
    which is worse than declining to answer.
    """
    bad = (
        b"NIBROCK, INC.\nInvoice No: NIB-7777\nYour PO Number: PO-NIBROCK-9921\n"
        b"REMIT TO: Nibrock\n4 EA Chromium Carbide Overlay Plate @ 100.00\n"
        b"Freight Charge: 50.00\nTOTAL DUE: 9999.00\n"
    )
    res = scanner.scan_document(bad, "INV-NIB-7777.txt")
    assert any("not read" in n.lower() for n in res.compliance_notes)


# ------------------------------------------------------------------ email

def test_mail_is_recorded_but_not_sent_when_sending_is_off(monkeypatch):
    """A fresh install drafts and records. It does not send."""
    monkeypatch.setattr(settings, "SMTP_HOST", "", raising=False)
    monkeypatch.setattr(settings, "EMAIL_SEND_ENABLED", False, raising=False)

    svc = OutboundEmailService()
    entry = svc.send_dispute("NIBROCK", "ar@nibrockinc.com", "NIB-1", "Body of the letter.")

    assert entry["status"] == "DRAFTED"
    assert svc.send_enabled is False
    assert svc.status()["mode"] == "DRAFT_ONLY"
    # The draft still exists. "We never sent it" and "we never wrote it" are
    # different failures and the outbox has to tell them apart.
    assert svc.get_outbox()[0]["body"] == "Body of the letter."


def test_redirect_overrides_every_recipient(monkeypatch):
    """A pilot can run against real suppliers without reaching them."""
    sent = {}

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test", raising=False)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "ops@example.test", raising=False)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "test-app-password", raising=False)
    monkeypatch.setattr(settings, "EMAIL_SEND_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_ALL_TO", "pilot@alliedweldingsystems.com", raising=False)

    svc = OutboundEmailService()
    svc._deliver = lambda msg, recipients: sent.update(to=recipients, subject=msg["Subject"])

    entry = svc.send_purchase_order("NIBROCK", "dparker@nibrockinc.com", "PO-1", "Body")

    assert entry["status"] == "SENT"
    assert sent["to"] == ["pilot@alliedweldingsystems.com"]
    assert "dparker@nibrockinc.com" not in sent["to"]
    # The real recipient is still recorded, so the pilot can be audited.
    assert entry["intended_to"] == ["dparker@nibrockinc.com"]
    assert entry["redirected"] is True


def test_a_mail_failure_never_raises(monkeypatch):
    """An order that is otherwise complete must not roll back over a mail error."""
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test", raising=False)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "ops@example.test", raising=False)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "test-app-password", raising=False)
    monkeypatch.setattr(settings, "EMAIL_SEND_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_ALL_TO", "", raising=False)

    svc = OutboundEmailService()

    def boom(msg, recipients):
        raise ConnectionRefusedError("mail server down")

    svc._deliver = boom
    entry = svc.send_dispute("NIBROCK", "ar@nibrockinc.com", "NIB-2", "Body")

    assert entry["status"] == "FAILED"
    assert "ConnectionRefusedError" in entry["detail"]


# ---------------------------------------------------------------- mailbox

def test_mailbox_polling_is_off_unless_configured():
    from app.services.mailbox_poller import MailboxPoller

    poller = MailboxPoller()
    assert poller.enabled is False
    assert poller.start() is False, "polling must not start without a configured mailbox"


# The mailbox reader's own behaviour (every folder, each message once, nothing marked read)
# is tested in tests/test_mail_reader.py.


# ------------------------------------------------------------- cad drawings

def test_drawing_fields_are_read_not_invented(scanner):
    dwg = (
        b"ALLIED WELDING SYSTEMS - ENGINEERING DRAWING\n"
        b"DWG #: OC-ED-218\nREV: 2\nSAP CODE: 755507\n"
        b"MATERIAL: 3/8\" Allied 300XL Chromium Carbide Plate on A36 Base\n"
        b"ARC LENGTH: 41\"\nWIDTH: 24\"\nTHK: 3/8\"\n"
    )
    res = scanner.scan_document(dwg, "DWG_OC-ED-218.txt")

    assert res.document_type == DocumentScanType.CAD_DRAWING
    assert res.fields["drawing_number"].normalized_value == "OC-ED-218"
    assert res.fields["arc_length"].normalized_value.startswith("41")
    assert res.fields["thickness"].normalized_value.startswith("3/8")
    # "ARC LENGTH" must not also register as a plain length of 41.
    assert "length" not in res.fields or res.fields["length"].normalized_value != res.fields["arc_length"].normalized_value


def test_an_unreadable_drawing_invents_nothing(scanner):
    """The old parser filled every unread field with a real-looking default.

    A scan with no text layer then produced a confident, entirely wrong
    takeoff attributed to a genuine drawing number.
    """
    res = scanner.scan_document(b"DRAWING NO: ZZ-9999\n", "blank.txt")
    assert res.fields["drawing_number"].normalized_value == "ZZ-9999"
    for invented in ("OC-ED-218-R2", "755507"):
        assert all(
            str(f.normalized_value) != invented for f in res.fields.values()
        ), f"{invented} was invented for a drawing that does not contain it"
    assert any("could not read" in n.lower() for n in res.compliance_notes)


def test_drawing_reference_reaches_the_packing_slip():
    """A fabricated part is only identifiable by the drawing it was cut to."""
    import inspect

    from app.services import packing_slips

    source = inspect.getsource(packing_slips.PackingSlipGenerator.generate_pdf)
    assert "drawing_ref" in source, "the packing slip must carry the drawing reference"


# ----------------------------------------------------------------- wear

def test_wear_is_computed_from_elapsed_time_not_stored(tmp_path):
    """Two identical parts installed at different times must differ in wear."""
    import json

    life = 100
    records = [
        {
            "equipment_tag": "FRESH-01", "customer_id": "C1", "customer_name": "Test Co",
            "plant_location": "Site", "component_name": "Liner", "installed_item_id": "X1",
            "install_date": (date.today() - timedelta(days=10)).isoformat(),
            "expected_lifespan_days": life, "suggested_replacement_sku": "X1",
            "historical_unit_price": 100.0, "last_service_order": "SO-1",
        },
        {
            "equipment_tag": "WORN-01", "customer_id": "C1", "customer_name": "Test Co",
            "plant_location": "Site", "component_name": "Liner", "installed_item_id": "X1",
            "install_date": (date.today() - timedelta(days=98)).isoformat(),
            "expected_lifespan_days": life, "suggested_replacement_sku": "X1",
            "historical_unit_price": 100.0, "last_service_order": "SO-2",
        },
    ]
    path = tmp_path / "equipment.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    svc = SalesService(equipment_json_path=path)
    by_tag = {e.equipment_tag: e for e in svc.get_predictive_equipment()}

    assert by_tag["FRESH-01"].estimated_wear_pct == pytest.approx(10.0, abs=1.0)
    assert by_tag["FRESH-01"].alert_level == "HEALTHY"
    assert by_tag["WORN-01"].estimated_wear_pct == pytest.approx(98.0, abs=1.0)
    assert by_tag["WORN-01"].alert_level == "CRITICAL_REPLACE"
    assert by_tag["WORN-01"].days_remaining < by_tag["FRESH-01"].days_remaining


def test_wear_states_the_basis_of_its_own_projection(tmp_path):
    """A projection is worth what its basis is worth, so the basis travels with it."""
    import json

    records = [{
        "equipment_tag": "E1", "customer_id": "C1", "customer_name": "Test Co",
        "plant_location": "Site", "component_name": "Screen", "installed_item_id": "NEVER-REORDERED",
        "install_date": (date.today() - timedelta(days=30)).isoformat(),
        "expected_lifespan_days": 120, "suggested_replacement_sku": "X",
        "historical_unit_price": 10.0, "last_service_order": "SO-1",
    }]
    path = tmp_path / "equipment.json"
    path.write_text(json.dumps(records), encoding="utf-8")

    equip = SalesService(equipment_json_path=path).get_predictive_equipment()[0]
    assert equip.lifespan_basis == "CATALOGUE"
    assert "120" in equip.lifespan_basis_detail
    assert equip.projected_replacement_date is not None


def test_restocking_is_not_mistaken_for_a_wear_cycle():
    """Ordering the same part twice in a fortnight is buying two, not wearing one out.

    Without this filter, a demo database full of same-week repeat orders
    reported a 135-day screen as failing in 16 days.
    """
    svc = SalesService()
    summaries = svc._observed_service_lives()
    for (_, item_id), summary in summaries.items():
        for gap in summary.get("gaps", []):
            assert gap > 0, f"{item_id} produced a non-positive interval"


def test_a_mail_server_with_no_login_stays_draft_only(monkeypatch):
    """Gmail with the app password not filled in yet: nothing is tried, the screen says what is missing."""
    from app.services.outbound_email_service import outbound_email_service

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.gmail.com", raising=False)
    monkeypatch.setattr(settings, "EMAIL_SEND_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "", raising=False)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "", raising=False)
    assert not outbound_email_service.send_enabled
    assert "SMTP_PASSWORD" in outbound_email_service.status()["detail"]


def test_an_office_relay_with_no_login_still_sends(monkeypatch):
    """A relay that accepts mail by address (no username) is used as it is; only hosted services need a login."""
    from app.services.outbound_email_service import outbound_email_service

    monkeypatch.setattr(settings, "EMAIL_SEND_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "", raising=False)
    monkeypatch.setattr(settings, "SMTP_HOST", "postfix", raising=False)
    assert outbound_email_service.send_enabled
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.office365.com", raising=False)
    assert not outbound_email_service.send_enabled
