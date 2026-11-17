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
    eng_notes,
    exceptions,
    ball,
    language,
    carriers,
    files,
    search,
    monday_push,
    drawings,
    exports,
    updates,
    workspace,
    forms,
    fedex,
    health,
    inbound_email,
    jobs,
    me,
    packets,
    purchase_orders,
    leads,
    mail,
    orders,
    packing_slips,
    rag_chat,
    reconciliation,
    sage_export,
    sage_import,
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
api_v1_router.include_router(sage_import.router, prefix="/sage-import", tags=["Sage 50 import files"])
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
api_v1_router.include_router(mail.router, prefix="/mail", tags=["Mail"])
api_v1_router.include_router(fedex.router, prefix="/fedex", tags=["FedEx"])
api_v1_router.include_router(carriers.router, prefix="/carriers", tags=["Carriers"])
api_v1_router.include_router(files.router, prefix="/files", tags=["Files"])
api_v1_router.include_router(search.router, prefix="/search", tags=["Search"])
api_v1_router.include_router(monday_push.router, prefix="/monday-push", tags=["monday push"])
api_v1_router.include_router(drawings.router, prefix="/drawings", tags=["Drawings"])
api_v1_router.include_router(jobs.router, prefix="/jobs", tags=["Jobs"])
api_v1_router.include_router(me.router, prefix="/me", tags=["Me"])
api_v1_router.include_router(packets.router, prefix="/packets", tags=["Job packets"])
api_v1_router.include_router(jobs.catalog_router, prefix="/catalog", tags=["Catalog"])
api_v1_router.include_router(purchase_orders.router, prefix="/purchase-orders", tags=["Purchase orders"])
api_v1_router.include_router(ball.router, prefix="/ball", tags=["Ball"])
api_v1_router.include_router(workspace.router, prefix="/workspace", tags=["Workspace"])
api_v1_router.include_router(language.router, prefix="/language", tags=["Language"])
api_v1_router.include_router(exports.router, prefix="/exports", tags=["Downloads"])
api_v1_router.include_router(updates.router, prefix="/updates", tags=["Updates"])
api_v1_router.include_router(forms.router, prefix="/forms", tags=["Task forms"])
api_v1_router.include_router(packing_slips.router, prefix="/packing-slips", tags=["Packing Slips"])
api_v1_router.include_router(eng_notes.router, prefix="/engineering-notes", tags=["Engineering Notes"])
api_v1_router.include_router(coordination.projects_router, prefix="/projects", tags=["Projects & Milestones"])
api_v1_router.include_router(coordination.shipments_router, prefix="/shipments", tags=["Shipments"])
api_v1_router.include_router(coordination.alerts_router, prefix="/alerts", tags=["Staff Alerts"])
api_v1_router.include_router(coordination.vendors_router, prefix="/vendors", tags=["Vendor Lead Times"])
api_v1_router.include_router(coordination.rfqs_router, prefix="/rfqs", tags=["Customer RFQs"])
api_v1_router.include_router(coordination.autopilot_router, prefix="/autopilot", tags=["Autopilot"])
api_v1_router.include_router(coordination.boards_router, prefix="/boards", tags=["monday Boards"])

__all__ = ["api_v1_router"]
