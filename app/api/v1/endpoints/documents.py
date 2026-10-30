"""FastAPI endpoints for Multi-Modal Document Scanning, OCR Transcription, and Entity Extraction."""

from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core.logging import logger
from app.core.security import require_operator
from app.models.documents import (
    BatchScanResult,
    DocumentScanResponse,
    DocumentScanType,
    DocumentUploadSample,
)
from app.services.document_scanner_service import document_scanner_service

router = APIRouter()

# Same ceiling as the chat PDF upload and the AI tools.
MAX_SCAN_BYTES = 25 * 1024 * 1024


def _read_capped(upload: UploadFile) -> bytes:
    """Reads an upload in chunks, refusing it once it passes MAX_SCAN_BYTES."""
    chunks, size = [], 0
    while chunk := upload.file.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_SCAN_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{upload.filename or 'File'} exceeds the {MAX_SCAN_BYTES // (1024 * 1024)}MB upload limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/scan",
    response_model=DocumentScanResponse,
    status_code=status.HTTP_200_OK,
    summary="Scan & Transcribe Document (PDF / Image / Drawing / MTR / PO)",
)
def scan_document_upload(
    file: UploadFile = File(..., description="Uploaded PDF, PNG, JPG, or drawing file"),
    doc_type: Optional[str] = Form("AUTO", description="Optional document type override (AUTO, CUSTOMER_PO, CAD_DRAWING, MTR_HEAT_CERT)"),
    _user: dict = Depends(require_operator),
) -> DocumentScanResponse:
    """
    Accepts uploaded customer POs, CAD drawings, or MTR mill certs.
    Runs multi-modal OCR transcription and extracts structured entities.

    Operators only: scanning a supplier invoice runs the invoice check, which
    records the result, posts to monday and can email the supplier.
    """
    try:
        file_bytes = _read_capped(file)
        if not file_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )

        try:
            target_type = DocumentScanType(doc_type.upper()) if doc_type else DocumentScanType.AUTO
        except ValueError:
            target_type = DocumentScanType.AUTO

        filename = file.filename or "uploaded_document.pdf"
        result = document_scanner_service.scan_document(
            file_bytes=file_bytes,
            filename=filename,
            doc_type_override=target_type,
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error scanning document: {exc}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Document scan failed: {str(exc)}",
        )


@router.get(
    "/samples",
    response_model=List[DocumentUploadSample],
    summary="Get Ready-to-Test Document Samples",
)
def get_document_samples() -> List[DocumentUploadSample]:
    """Returns sample industrial documents (Customer POs, CAD Blueprints, MTR Mill Certs)."""
    return document_scanner_service.get_sample_documents()


@router.post(
    "/batch-scan",
    response_model=BatchScanResult,
    summary="Batch Scan Multiple Documents",
)
def batch_scan_documents(
    files: List[UploadFile] = File(..., description="Multiple PDF or image files to scan in batch"),
    _user: dict = Depends(require_operator),
) -> BatchScanResult:
    """Batch scans a collection of uploaded documents and returns aggregated extractions."""
    try:
        processed_files = []
        for f in files:
            b = _read_capped(f)
            if b:
                processed_files.append((b, f.filename or "document.pdf"))

        if not processed_files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No non-empty files provided in batch.",
            )

        return document_scanner_service.batch_scan_documents(processed_files)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Batch scan failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Batch scan failed: {str(exc)}",
        )
