"""Pydantic models for Enterprise RAG Knowledge Copilot, Citations, and Action Handoffs."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """A single turn in the conversational thread."""
    role: str = Field(..., description="'user' | 'assistant' | 'system'")
    content: str = Field(..., description="Message text content")


class RAGQueryRequest(BaseModel):
    """Inbound question submitted to the Allied RAG Copilot."""
    query: str = Field(..., min_length=1, description="Natural language question or quote query")
    conversation_history: List[ChatMessage] = Field(default_factory=list, description="Past conversation turns")
    customer_hint: Optional[str] = Field(None, description="Optional customer filter context")
    dev_bypass: bool = Field(False, description="Whether to run in local deterministic mode")
    agentic_mode: bool = Field(False, description="If true, copilot auto-executes suggested backend actions.")
    uploaded_pdf_path: Optional[str] = Field(None, description="Path to an uploaded PDF for vision parsing context")


class RAGSourceCitation(BaseModel):
    """Attribution metadata pointing back to internal documents, quotes, CSV masters, or MTRs."""
    source_id: str = Field(..., description="Unique ID of source document / quote / WPS / catalog")
    source_type: str = Field(..., description="'HISTORICAL_ESTIMATE' | 'WELDING_WPS' | 'SUPPLIER_AGREEMENT' | 'ASTM_STANDARD' | 'MTR_CERT' | 'CACHED_ORDER' | 'ITEM_MASTER' | 'CUSTOMER_MASTER' | 'VENDOR_MASTER' | 'INTERNAL_DOC' | 'SCANNED_DOC'")
    title: str = Field(..., description="Human-readable title of document or job")
    excerpt: str = Field(..., description="Relevant text snippet used in synthesis")
    relevance_score: float = Field(..., ge=0.0, le=1.0, description="Normalized similarity or matching score")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional contextual fields (e.g. SKUs, heats, costs)")


class ActionPayload(BaseModel):
    """Interactive button payload enabling 1-click execution in Sage 50, CSV import, or order form."""
    action_type: str = Field(..., description="'STAGE_SAGE_ORDER' | 'STAGE_OPERATIONS' | 'VIEW_MTR' | 'COPY_PROPOSAL' | 'RUN_DRY_RUN' | 'EXECUTE_LEAD_SCRAPE' | 'EXECUTE_OEM_UPGRADE' | 'EXECUTE_VENDOR_REPRICE' | 'EXECUTE_CRM_FORM_REQUEST' | 'EXECUTE_OUTREACH_EMAIL' | 'EXECUTE_END_TO_END_ORDER' | 'SCAN_INTERNAL_DOCUMENT' | 'EXECUTE_CSV_IMPORT'")
    button_label: str = Field(..., description="Display text for the button")
    prefill_data: Dict[str, Any] = Field(default_factory=dict, description="Pre-filled order form or MTR payload")

class ActionExecutionRequest(BaseModel):
    action_type: str
    payload: Dict[str, Any]


class RAGChatResponse(BaseModel):
    """Grounded answer synthesized by the Allied RAG Copilot with citations and actions."""
    answer: str = Field(..., description="Grounded markdown formatted answer")
    model_used: str = Field(..., description="e.g. 'Ollama (llama3.2)' or 'Deterministic Business Engine'")
    citations: List[RAGSourceCitation] = Field(default_factory=list, description="List of source citations")
    suggested_actions: List[ActionPayload] = Field(default_factory=list, description="Action buttons (e.g. Stage Order)")
    confidence_score: float = Field(..., ge=0.0, le=1.0)


class KnowledgeBaseSummary(BaseModel):
    """Status summary of all indexed knowledge bases."""
    total_documents: int
    categories: Dict[str, int]
    system_status: str
