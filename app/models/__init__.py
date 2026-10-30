"""Pydantic V2 data models and DTOs."""

from app.models.orders import (
    CustomerDetails,
    DualSidedResult,
    InboundOrderRequest,
    MasterOrder,
    MondayChallengeRequest,
    MondayChallengeResponse,
    MondayWebhookPayload,
    OrderArchetype,
    OrderLineItem,
    OrderStatus,
    ShippingDetails,
)
from app.models.sage import (
    SageCustomer,
    SageGLAccount,
    SageInventoryItem,
    SagePurchaseOrder,
    SagePurchaseOrderLine,
    SageSalesOrder,
    SageSalesOrderLine,
    SageVendor,
)
from app.models.blueprints import (
    BOMItem,
    DrawingTakeoff,
)
from app.models.compliance_mtr import (
    ChemicalComposition,
    MechanicalProperties,
    MTRRecord,
    MTRVerifyRequest,
    CertificateOfConformance,
)
from app.models.sales_pipeline import (
    PredictiveWearEquipment,
    DraftQuoteFromWearRequest,
)
from app.models.rag_chat import (
    ChatMessage,
    RAGQueryRequest,
    RAGSourceCitation,
    ActionPayload,
    RAGChatResponse,
    KnowledgeBaseSummary,
)

from app.models.documents import (
    ActionRoutingPayload,
    BatchScanResult,
    DocumentScanResponse,
    DocumentScanType,
    DocumentUploadSample,
    ExtractedDocumentField,
    ScannedLineItem,
)

__all__ = [
    "CustomerDetails",
    "DualSidedResult",
    "InboundOrderRequest",
    "MasterOrder",
    "MondayChallengeRequest",
    "MondayChallengeResponse",
    "MondayWebhookPayload",
    "OrderArchetype",
    "OrderLineItem",
    "OrderStatus",
    "ShippingDetails",
    "SageCustomer",
    "SageGLAccount",
    "SageInventoryItem",
    "SagePurchaseOrder",
    "SagePurchaseOrderLine",
    "SageSalesOrder",
    "SageSalesOrderLine",
    "SageVendor",
    "BOMItem",
    "DrawingTakeoff",
    "ChemicalComposition",
    "MechanicalProperties",
    "MTRRecord",
    "MTRVerifyRequest",
    "CertificateOfConformance",
    "PredictiveWearEquipment",
    "DraftQuoteFromWearRequest",
    "ChatMessage",
    "RAGQueryRequest",
    "RAGSourceCitation",
    "ActionPayload",
    "RAGChatResponse",
    "KnowledgeBaseSummary",
    "DocumentScanType",
    "ExtractedDocumentField",
    "ScannedLineItem",
    "ActionRoutingPayload",
    "DocumentScanResponse",
    "BatchScanResult",
    "DocumentUploadSample",
]
