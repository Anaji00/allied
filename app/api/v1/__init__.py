"""API v1 router configuration."""

from fastapi import APIRouter
from app.api.v1.endpoints import (
    ai,
    assistant,
    auth,
    automation,
    audit,
    cache,
    compliance,
    coordination,
    csv_import,
    documents,
    exceptions,
    health,
    inbound_email,
    leads,
    orders,
    rag_chat,
    reconciliation,
    sage_export,
    sales,
    webhooks,
)

api_v1_router = APIRouter()

api_v1_router.include_router(health.router, prefix="/health", tags=["Health & Diagnostics"])
api_v1_router.include_router(automation.router, prefix="/automation", tags=["Automation Status"])
api_v1_router.include_router(auth.router, prefix="/auth", tags=["Authentication & RBAC"])
api_v1_router.include_router(audit.router, prefix="/audit", tags=["Immutable Audit Trail"])
api_v1_router.include_router(orders.router, prefix="/orders", tags=["Orders & Transactions"])
api_v1_router.include_router(documents.router, prefix="/documents", tags=["Document Scanning & OCR"])
api_v1_router.include_router(inbound_email.router, prefix="/inbox", tags=["Autonomous Inbound Email"])
api_v1_router.include_router(reconciliation.router, prefix="/reconciliation", tags=["Vendor Bill 3-Way Reconciliation"])
api_v1_router.include_router(sage_export.router, prefix="/sage/export", tags=["Sage 50 ERP CSV Export"])
api_v1_router.include_router(csv_import.router, prefix="/csv", tags=["Master Data & Batch Order CSV Imports"])
api_v1_router.include_router(exceptions.router, prefix="/exceptions", tags=["Operations Exceptions Queue"])
api_v1_router.include_router(leads.router, prefix="/leads", tags=["Lead Generation & Sales Agent"])
api_v1_router.include_router(rag_chat.router, prefix="/chat", tags=["Enterprise AI Knowledge Copilot"])
api_v1_router.include_router(cache.router, prefix="/cache", tags=["Agent Fast Memory Cache"])
api_v1_router.include_router(compliance.router, prefix="/compliance", tags=["Compliance & MTR Mill Certs"])
api_v1_router.include_router(sales.router, prefix="/sales", tags=["Sales Pipeline & Predictive Wear"])
api_v1_router.include_router(webhooks.router, prefix="/webhooks", tags=["Monday.com Webhooks"])
api_v1_router.include_router(ai.router, prefix="/ai", tags=["AI Tools"])
api_v1_router.include_router(assistant.router, prefix="/assistant", tags=["Assistant"])
api_v1_router.include_router(coordination.projects_router, prefix="/projects", tags=["Projects & Milestones"])
api_v1_router.include_router(coordination.shipments_router, prefix="/shipments", tags=["Shipments"])
api_v1_router.include_router(coordination.alerts_router, prefix="/alerts", tags=["Staff Alerts"])
api_v1_router.include_router(coordination.vendors_router, prefix="/vendors", tags=["Vendor Lead Times"])
api_v1_router.include_router(coordination.rfqs_router, prefix="/rfqs", tags=["Customer RFQs"])
api_v1_router.include_router(coordination.autopilot_router, prefix="/autopilot", tags=["Autopilot"])

__all__ = ["api_v1_router"]
