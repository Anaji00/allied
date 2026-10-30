"""Pydantic data models for multi-modal document scanning, OCR transcribing, and structured entity extraction."""

from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class DocumentScanType(str, Enum):
    """Supported document types for multi-modal scanning and transcription."""
    AUTO = "AUTO"
    CUSTOMER_PO = "CUSTOMER_PO"
    VENDOR_INVOICE = "VENDOR_INVOICE"
    CAD_DRAWING = "CAD_DRAWING"
    MTR_HEAT_CERT = "MTR_HEAT_CERT"
    RFQ_PROPOSAL = "RFQ_PROPOSAL"
    GENERAL_DOC = "GENERAL_DOC"


class ExtractedDocumentField(BaseModel):
    """Individual extracted field with confidence and source context."""
    field_name: str
    raw_value: str
    normalized_value: Any
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    source_line: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class ScannedLineItem(BaseModel):
    """Line item extracted from an uploaded document, drawing, or PO."""
    item_id: Optional[str] = None
    description: str
    quantity: Decimal = Decimal("1.0")
    unit_price: Optional[Decimal] = None
    unit_cost: Optional[Decimal] = None
    dimensions: Optional[str] = None
    drawing_ref: Optional[str] = None
    heat_number: Optional[str] = None
    preferred_vendor: Optional[str] = "STOCK"
    confidence: float = 0.90

    model_config = ConfigDict(extra="ignore")


class ActionRoutingPayload(BaseModel):
    """Actionable prefilled data payload ready to dispatch to platform endpoints."""
    target_action: str  # STAGE_SAGE_ORDER, STAGE_OPERATIONS, REGISTER_MTR, RAG_INDEX
    label: str
    route_url: str
    prefill_data: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


class DocumentScanResponse(BaseModel):
    """Complete structured response produced by the Document Scanner & OCR engine."""
    scan_id: str
    filename: str
    document_type: DocumentScanType
    detected_mime: str
    file_size_bytes: int
    raw_transcribed_text: str
    confidence_score: float
    processing_time_ms: float
    fields: Dict[str, ExtractedDocumentField] = Field(default_factory=dict)
    line_items: List[ScannedLineItem] = Field(default_factory=list)
    suggested_actions: List[ActionRoutingPayload] = Field(default_factory=list)
    compliance_notes: List[str] = Field(default_factory=list)
    ocr_engine: str = "Allied Deterministic Vision & Multi-Modal Engine"
    # Present only on a supplier invoice: the three-way match that ran against
    # the authorising purchase order the moment the document was read. This is
    # the whole point of scanning an invoice rather than filing it.
    reconciliation: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="ignore")


class BatchScanResult(BaseModel):
    """Summary of batch document scan processing."""
    total_files: int
    successful_scans: int
    failed_scans: int
    scans: List[DocumentScanResponse] = Field(default_factory=list)
    batch_summary_notes: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class DocumentUploadSample(BaseModel):
    """Ready-to-test sample document metadata."""
    sample_id: str
    title: str
    document_type: DocumentScanType
    description: str
    sample_filename: str
    sample_text: str
    preview_url: Optional[str] = None

    model_config = ConfigDict(extra="ignore")
