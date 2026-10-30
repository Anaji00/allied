"""Unit and Integration tests for Multi-Modal Document Scanner, OCR Transcribing, and Entity Extraction."""

import io
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.documents import DocumentScanType
from app.services.document_scanner_service import DocumentScannerService, document_scanner_service

client = TestClient(app)


def test_scan_customer_po_document():
    """Tests parsing a customer PO document into structured entities and order routing."""
    po_text = (
        "PURCHASE ORDER\n"
        "BUYER: OWENS CORNING (EDMONTON PLANT)\n"
        "PO NUMBER: PO-OC-EDM-9912\n"
        "TERMS: NET 150 DAYS | SHIP VIA: MOTOR FRT\n"
        "DESTINATION: 831 HAYTER RD NW, EDMONTON, AB T6S 1A1, CANADA\n"
        "1. (2) EA - OC755507 - 2-1/2\" DIA HOLES HAMMERMILL SCREEN ALLIED 300XL @ $3,772.91\n"
        "2. (4) EA - BAR-34X2-41 - 3/4\" X 2\" X 41-3/4\" C1045 MIDDLE BRACE BARS @ $225.00\n"
        "HEAT NUMBER: HT-300XL-8819\n"
    )
    res = document_scanner_service.scan_document(
        file_bytes=po_text.encode("utf-8"),
        filename="PO_OwensCorning_9912.pdf",
    )

    assert res.document_type == DocumentScanType.CUSTOMER_PO
    assert "customer_po_number" in res.fields
    assert res.fields["customer_po_number"].normalized_value == "PO-OC-EDM-9912"
    assert "customer_name" in res.fields
    assert "Owens Corning" in res.fields["customer_name"].normalized_value
    assert len(res.line_items) >= 2
    assert res.line_items[0].item_id == "OC755507"
    assert any(a.target_action == "STAGE_SAGE_ORDER" for a in res.suggested_actions)


def test_scan_cad_drawing_takeoff():
    """Tests parsing a CAD engineering drawing blueprint scan."""
    dwg_text = (
        "ALLIED WELDING SYSTEMS - ENGINEERING DRAWING TAKEOFF\n"
        "DWG #: OC-ED-218-R2\n"
        "REV: 2 | SAP CODE: 755507\n"
        "TITLE: 2-1/2\" DIA HOLES HAMMER MILL SCREEN (ALLIED 300XL)\n"
        "MATERIAL: 3/8\" ALLIED 300XL CHROMIUM CARBIDE OVERLAY ON A36 BASE\n"
        "DIMENSIONS: 41\" ARC LENGTH X 24\" WIDTH X 3/8\" THICKNESS\n"
    )
    res = document_scanner_service.scan_document(
        file_bytes=dwg_text.encode("utf-8"),
        filename="CAD_OC_ED_218_R2.pdf",
    )

    assert res.document_type == DocumentScanType.CAD_DRAWING
    assert res.fields["drawing_number"].normalized_value == "OC-ED-218-R2"
    assert res.fields["revision"].normalized_value == "2"
    assert res.fields["sap_code"].normalized_value == "755507"
    assert any(a.target_action == "STAGE_OPERATIONS" for a in res.suggested_actions)


def test_scan_mtr_mill_test_certificate():
    """Tests parsing an MTR heat cert for chemical composition and ASTM compliance."""
    mtr_text = (
        "CERTIFIED MATERIAL TEST REPORT (MTR) & QUALITY CERTIFICATE\n"
        "HEAT NUMBER: HT-300XL-8819\n"
        "CHEMICAL ANALYSIS (%): C: 4.80 | Cr: 29.50 | Mn: 1.40 | Si: 0.85 | Fe: BALANCE\n"
        "MECHANICAL TESTS: HARDNESS: 60.5 HRC (650 HBW)\n"
        "COMPLIANCE: ASTM A532 CHROMIUM CARBIDE WEAR OVERLAY\n"
    )
    res = document_scanner_service.scan_document(
        file_bytes=mtr_text.encode("utf-8"),
        filename="MTR_HT_300XL_8819.pdf",
    )

    assert res.document_type == DocumentScanType.MTR_HEAT_CERT
    assert res.fields["heat_number"].normalized_value == "HT-300XL-8819"
    assert res.fields["carbon_pct"].normalized_value == 4.80
    assert res.fields["chromium_pct"].normalized_value == 29.50
    assert any(a.target_action == "REGISTER_MTR" for a in res.suggested_actions)


def test_get_document_samples_api():
    """Tests the /api/v1/documents/samples endpoint."""
    response = client.get("/api/v1/documents/samples")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 3
    sample_types = [s["document_type"] for s in data]
    assert DocumentScanType.CUSTOMER_PO in sample_types
    assert DocumentScanType.CAD_DRAWING in sample_types
    assert DocumentScanType.MTR_HEAT_CERT in sample_types



def test_scan_document_api_endpoint():
    """Tests uploading a multipart document to /api/v1/documents/scan."""
    po_content = (
        "PURCHASE ORDER\n"
        "PO NUMBER: PO-TEST-UPLOAD-101\n"
        "BUYER: OWENS CORNING\n"
        "TERMS: NET 150 DAYS\n"
        "LINE 1: 2 EA OC755507 HAMMERMILL SCREENS $3772.91\n"
    )
    file_payload = ("test_po.pdf", io.BytesIO(po_content.encode("utf-8")), "application/pdf")
    
    response = client.post(
        "/api/v1/documents/scan",
        files={"file": file_payload},
        data={"doc_type": "CUSTOMER_PO"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scan_id"].startswith("SCAN-")
    assert data["document_type"] == "CUSTOMER_PO"
    assert len(data["suggested_actions"]) >= 1


def test_batch_scan_api_endpoint():
    """Tests the /api/v1/documents/batch-scan endpoint."""
    f1 = ("po1.pdf", io.BytesIO(b"PO NUMBER: PO-BATCH-1\nBUYER: OWENS CORNING"), "application/pdf")
    f2 = ("dwg1.pdf", io.BytesIO(b"DWG #: OC-ED-218\nTITLE: SCREEN"), "application/pdf")

    response = client.post(
        "/api/v1/documents/batch-scan",
        files=[("files", f1), ("files", f2)],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_files"] == 2
    assert data["successful_scans"] == 2
    assert len(data["scans"]) == 2


def test_empty_file_upload_rejection():
    """Tests rejection of empty uploaded files."""
    empty_file = ("empty.pdf", io.BytesIO(b""), "application/pdf")
    response = client.post(
        "/api/v1/documents/scan",
        files={"file": empty_file},
    )
    assert response.status_code == 400


def test_binary_pdf_never_leaks_raw_bytes():
    """Validates that a binary PDF never dumps raw PDF/JPEG bytecode as extracted text."""
    # Construct a minimal binary PDF header
    mock_pdf_binary = b"%PDF-1.4\n1 0 obj\n<</Type /Page>>\nendobj\nstream\nJFIF\x00\x01\x02\nendstream\n%%EOF"
    res = document_scanner_service.scan_document(
        file_bytes=mock_pdf_binary,
        filename="scanned_order.pdf",
    )
    assert "%PDF-" not in res.raw_transcribed_text
    assert "stream" not in res.raw_transcribed_text
    assert "JFIF" not in res.raw_transcribed_text


def test_scan_real_scanned_po_document_if_available():
    """Tests end-to-end OCR and entity extraction on real scanned PO document."""
    from pathlib import Path
    po_path = Path.home() / "Downloads" / "PO 9255 (2) (1).pdf"
    if not po_path.exists():
        po_path = Path.home() / "Downloads" / "PO 9255 (2).pdf"
    if not po_path.exists():
        return

    with open(po_path, "rb") as f:
        pdf_bytes = f.read()

    res = document_scanner_service.scan_document(
        file_bytes=pdf_bytes,
        filename="PO 9255 (2) (1).pdf",
    )

    assert res.document_type == DocumentScanType.CUSTOMER_PO
    assert "%PDF-" not in res.raw_transcribed_text
    assert "JFIF" not in res.raw_transcribed_text
    assert "EQEQEQ" not in res.raw_transcribed_text
    assert "customer_name" in res.fields
    assert "Owens Corning" in res.fields["customer_name"].normalized_value
    assert res.fields["customer_po_number"].normalized_value in ("4518702131", "9255")
    assert len(res.line_items) >= 1
    assert any(li.item_id == "OC262064" for li in res.line_items)

