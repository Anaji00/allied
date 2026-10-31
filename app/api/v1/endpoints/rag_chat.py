"""RAG Conversational Chat & Knowledge Search API Endpoints."""

from typing import Dict, List
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from app.core.logging import logger
from app.core.security import require_operator, require_viewer
from app.core.uploads import ensure_upload_dir, resolve_upload_path, safe_upload_name
from app.models.rag_chat import KnowledgeBaseSummary, RAGChatResponse, RAGQueryRequest, ActionExecutionRequest
from app.services.rag_service import rag_service
from app.services.lead_agent import LeadAgent, LeadSearchUnavailable
from app.services.monday_client import MondayClient
from app.services.sales_service import SalesService
from app.core.container import get_orchestrator
from app.services.vendor_reprice import VendorRepriceHaltError, vendor_reprice_service
from app.models.orders import InboundOrderRequest, OrderLineItem

router = APIRouter()
lead_agent = LeadAgent()
monday_client = MondayClient()
sales_service = SalesService()
order_orchestrator = get_orchestrator()

MAX_PDF_BYTES = 25 * 1024 * 1024


@router.post("/query", response_model=RAGChatResponse)
def query_rag_copilot(
    request: RAGQueryRequest,
    _user: dict = Depends(require_viewer),
) -> RAGChatResponse:
    """Answers estimating, metallurgical, compliance, and order questions using hybrid RAG retrieval."""
    # uploaded_pdf_path arrives from the client, so it is resolved against the
    # upload directory and rejected if it escapes, before it reaches any open().
    if request.uploaded_pdf_path:
        try:
            request.uploaded_pdf_path = str(resolve_upload_path(request.uploaded_pdf_path))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    try:
        return rag_service.answer_query(request)
    except Exception as e:
        logger.error(f"RAG query failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Copilot query failed. See server logs.")


@router.post("/upload-pdf")
def upload_chat_pdf(
    file: UploadFile = File(...),
    _user: dict = Depends(require_operator),
):
    """Uploads a PDF into the chat context for Vision Parsing."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    # The client controls this filename, so only its sanitized basename is used
    # and a random prefix keeps concurrent uploads of the same name apart.
    file_path = ensure_upload_dir() / f"{uuid.uuid4().hex[:8]}_{safe_upload_name(file.filename)}"
    written = 0
    try:
        with file_path.open("wb") as buffer:
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_PDF_BYTES:
                    raise ValueError(
                        f"PDF exceeds the {MAX_PDF_BYTES // (1024 * 1024)}MB upload limit."
                    )
                buffer.write(chunk)
        return {"status": "success", "file_path": str(file_path)}
    except ValueError as e:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=str(e))
    except Exception as e:
        file_path.unlink(missing_ok=True)
        logger.error(f"PDF upload failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to store the uploaded file.")


@router.post("/execute-action")
def execute_system_action(
    request: ActionExecutionRequest,
    current_user: dict = Depends(require_operator),
):
    """Executes a systemic action from the Copilot (e.g., scrape leads)."""
    try:
        if request.action_type == "EXECUTE_LEAD_SCRAPE":
            titles = request.payload.get("job_titles", [])
            industries = request.payload.get("industries", [])
            location = request.payload.get("location", "USA")
            results = lead_agent.scrape_web_for_leads(titles, industries, location)
            return {"status": "success", "data": results}
        elif request.action_type == "EXECUTE_OEM_UPGRADE":
            part = request.payload.get("current_part", "Part")
            mat = request.payload.get("oem_material", "A36")
            res = lead_agent.generate_oem_upgrade_proposal(part, mat)
            return {"status": "success", "data": res}
        elif request.action_type == "EXECUTE_MONDAY_PULL":
            board_id = request.payload.get("board_id")
            if not board_id:
                raise ValueError("board_id is required for Monday Pull")
            limit = int(request.payload.get("limit", 10))
            result = monday_client.fetch_board_items(int(board_id), limit)
            return {"status": "success", "data": result}
        elif request.action_type == "EXECUTE_INGEST_RFQ_EMAIL":
            # Routed to the inbound email daemon, which already performs
            # customer resolution, line-item extraction and order creation.
            # This branch previously imported a model and called a service
            # method that do not exist, so it always returned a 500.
            from app.services.inbound_email_daemon import (
                InboundEmailMessage,
                inbound_email_daemon,
            )

            sender_email = request.payload.get("sender_email")
            if not sender_email:
                raise ValueError("sender_email is required to ingest an RFQ email")
            message = InboundEmailMessage(
                sender_email=sender_email,
                sender_name=request.payload.get("customer_hint"),
                subject=request.payload.get("subject_line") or "Inbound RFQ",
                body_text=request.payload.get("raw_rfq_body") or "",
            )
            res = inbound_email_daemon.process_inbound_email(message)
            return {"status": "success", "data": res.model_dump()}
        elif request.action_type == "EXECUTE_WEAR_SCAN":
            discount = float(request.payload.get("discount_pct", 5.0))
            weeks = int(request.payload.get("delivery_weeks", 3))
            from fastapi import BackgroundTasks
            bt = BackgroundTasks()
            res = scan_wear_and_alert_direct(bt, discount, weeks)
            return {"status": "success", "data": res}
        elif request.action_type == "EXECUTE_END_TO_END_ORDER":
            # Pass payload directly to InboundOrderRequest
            req = InboundOrderRequest(**request.payload)
            res = order_orchestrator.process_inbound_order(req)
            return {"status": "success", "data": res.model_dump()}
        elif request.action_type == "EXECUTE_VENDOR_REPRICE":
            vendor = request.payload.get("vendor", "NIBROCK")
            item_id = request.payload.get("item_id", "PLATE-300XL-38")
            po_number = request.payload.get("po_number", "PO-REPRICE-CHECK")
            dummy_line = OrderLineItem(
                item_id=item_id,
                raw_item_text=item_id,
                quantity=1,
                unit_price=100.0,
                preferred_vendor=vendor,
            )
            try:
                vendor_reprice_service.validate_pricing_freshness({vendor: [dummy_line]}, po_number)
                return {
                    "status": "success",
                    "freshness": "VALID",
                    "detail": f"Pricing from {vendor} for {item_id} is certified fresh (<60 days old). Order can proceed.",
                }
            except VendorRepriceHaltError as err:
                return {
                    "status": "halted_for_reprice",
                    "freshness": "STALE",
                    "vendor": err.vendor,
                    "items": err.items,
                    "detail": str(err),
                    "action_taken": f"Urgent RFQ email drafted to {err.vendor} and Monday.com status posted.",
                }
        elif request.action_type == "EXECUTE_CRM_FORM_REQUEST":
            pulse_id = int(request.payload.get("pulse_id", 0))
            lead_email = request.payload.get("lead_email", "")
            res = lead_agent.send_automated_crm_form_request(pulse_id, lead_email)
            return {"status": "success", "data": res}
        elif request.action_type == "EXECUTE_OUTREACH_EMAIL":
            from app.services.lead_agent import LeadProfile
            profile = LeadProfile(
                name=request.payload.get("name", "Maintenance Lead"),
                title=request.payload.get("title", "Maintenance Supervisor"),
                company=request.payload.get("company", "Bulk Processing Facility"),
                industry=request.payload.get("industry", "Cement & Mining"),
                email=request.payload.get("email", "maintenance@plant.com"),
            )
            email_body = lead_agent.generate_outreach_email(profile)
            return {"status": "success", "data": {"email_body": email_body, "recipient": profile.model_dump()}}
        elif request.action_type == "EXECUTE_THREE_WAY_MATCH":
            from app.services.reconciliation_service import reconciliation_service, VendorInvoice, VendorInvoiceLine
            required = ("invoice_number", "vendor_name", "po_number", "lines")
            missing = [k for k in required if not request.payload.get(k)]
            if missing:
                raise ValueError(
                    f"Three-way match requires {missing}; refusing to reconcile a sample invoice."
                )
            lines = [VendorInvoiceLine(**l) for l in request.payload["lines"]]
            invoice = VendorInvoice(
                invoice_number=request.payload["invoice_number"],
                vendor_name=request.payload["vendor_name"],
                po_number=request.payload["po_number"],
                lines=lines,
                freight_billed=request.payload.get("freight_billed", 0.0),
                total_amount_billed=request.payload.get(
                    "total_amount_billed",
                    sum(float(l["total_amount"]) for l in request.payload["lines"]),
                ),
            )
            res = reconciliation_service.reconcile_vendor_invoice(invoice)
            return {"status": "success", "data": res.model_dump()}
        elif request.action_type == "EXECUTE_SAGE_CSV_EXPORT":
            from app.services.sage_export_service import sage_export_service
            summary = sage_export_service.get_batch_summary()
            return {
                "status": "success",
                "data": {
                    "summary": summary.model_dump(),
                    "download_links": {
                        "sales_orders_csv": "/api/v1/sage/export/sales-orders",
                        "purchase_orders_csv": "/api/v1/sage/export/purchase-orders",
                    },
                    "action": "Download CSV files for direct import into Sage 50 US Accounting.",
                }
            }
        elif request.action_type == "EXECUTE_PACKING_LIST_DISPATCH":
            from app.services.logistics_dispatcher import logistics_dispatcher, LogisticsDispatchRequest
            so_num = request.payload.get("sales_order_number")
            if not so_num:
                raise ValueError(
                    "sales_order_number is required; refusing to dispatch a default order."
                )
            carrier = request.payload.get("carrier_name", "TForce Freight")
            res = logistics_dispatcher.dispatch_shipment(
                LogisticsDispatchRequest(
                    sales_order_number=so_num,
                    carrier_name=carrier,
                    recipient_email=request.payload.get("recipient_email"),
                )
            )
            return {"status": "success", "data": res.model_dump()}
        elif request.action_type == "RESOLVE_EXCEPTION":
            from app.services.exception_queue_service import exception_queue_service, ResolveExceptionRequest
            exc_id = request.payload.get("exception_id")
            if not exc_id:
                raise ValueError("exception_id is required to resolve an exception.")
            act_type = request.payload.get("resolution_action", "APPROVE")
            res = exception_queue_service.resolve_exception(
                exc_id,
                ResolveExceptionRequest(
                    action_type=act_type,
                    resolution_notes=request.payload.get("notes", "Resolved via Copilot"),
                ),
                actor_email=current_user["email"],
            )
            return {"status": "success", "data": res}
        elif request.action_type == "SCAN_INTERNAL_DOCUMENT":
            from app.services.document_scanner_service import document_scanner_service
            raw_text = request.payload.get("document_text", "")
            sample_id = request.payload.get("sample_id", "")
            filename = request.payload.get("filename", "internal_document.txt")
            if sample_id:
                for s in document_scanner_service.get_sample_documents():
                    if s.sample_id == sample_id:
                        raw_text = s.sample_text
                        filename = s.sample_filename
                        break
            if not raw_text:
                samples = document_scanner_service.get_sample_documents()
                raw_text = samples[0].sample_text
                filename = samples[0].sample_filename
            scan_res = document_scanner_service.scan_document(raw_text.encode("utf-8"), filename)
            return {"status": "success", "data": scan_res.model_dump()}
        elif request.action_type == "EXECUTE_CSV_IMPORT":
            from app.services.csv_import_service import csv_import_service
            import_type = request.payload.get("import_type", "ITEMS").upper()
            csv_text = request.payload.get("csv_text", "")
            if not csv_text:
                templates = csv_import_service.get_templates()
                return {
                    "status": "ready_for_input",
                    "template": templates.get(import_type, templates["ITEMS"]),
                    "message": f"Please provide CSV data for import type '{import_type}'.",
                }
            result = csv_import_service.import_csv(csv_text, import_type)
            return {"status": "success", "data": result.model_dump()}
        else:
            return {"status": "unsupported", "detail": f"Action {request.action_type} not supported directly yet."}

    except LeadSearchUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sources", response_model=KnowledgeBaseSummary)
def get_knowledge_base_sources() -> KnowledgeBaseSummary:
    """Returns document counts across all indexed knowledge bases."""
    return rag_service.get_summary()


@router.get("/templates")
def get_business_prompt_templates() -> List[Dict[str, str]]:
    """Returns one-touch prompt starters for estimators, sales reps, and operations."""
    return [
        {
            "id": "SCAN_PO_DOC",
            "title": "Scan Internal PO Document",
            "prompt": "Scan internal document SAMPLE-PO-OC from Owens Corning Edmonton and show extracted line items and Sage 50 staging.",
            "category": "Document Scanner",
        },
        {
            "id": "SCAN_CAD_DWG",
            "title": "Scan CAD Blueprint Drawing",
            "prompt": "Scan internal document SAMPLE-DWG-CAD for drawing OC-ED-218-R2 and show dimensions, material overlay, and operations routing.",
            "category": "Document Scanner",
        },
        {
            "id": "MASTER_CSV_LOOKUP",
            "title": "Master Catalog CSV Lookup",
            "prompt": "Look up item PLATE-300XL-38 and OC755507 in the master CSV catalog and show stock, list price, wholesale cost, and gross margin.",
            "category": "Catalog & Pricing",
        },
        {
            "id": "HISTORICAL_QUOTE",
            "title": "Historical Quote Recall",
            "prompt": "What did we charge Owens Corning for the 2-1/2 inch hole hammermill screen on PO-9921 last year, what was our margin, and what vendor did we use?",
            "category": "Estimating",
        },
        {
            "id": "WPS_HARDFACING",
            "title": "Welding Parameters & Plasma Specs",
            "prompt": "What are the preheat requirements, shielding gas, and plasma cutting directions for Allied 300XL clad wear plate?",
            "category": "Shop Operations",
        },
        {
            "id": "SUPPLIER_RULES",
            "title": "Supplier Lead Times & Terms",
            "prompt": "What are our wholesale discount, payment terms, and lead times for Nibrock Mining & Wear Solutions?",
            "category": "Procurement & Logistics",
        },
        {
            "id": "METALLURGY_COMPARE",
            "title": "Allied 300XL vs AR500 Metallurgy",
            "prompt": "What is the chromium carbide chemistry and Rockwell hardness of Allied 300XL compared to standard AR500 steel?",
            "category": "Metallurgy & QA",
        },
    ]


def scan_wear_and_alert_direct(background_tasks, discount_pct: float, delivery_weeks: int):
    """Helper to run wear scan without the router dependency."""
    equipment_list = sales_service.get_predictive_equipment(None)
    alerts_dispatched = []
    skipped_healthy = 0

    from app.models.sales_pipeline import DraftQuoteFromWearRequest

    for equip in equipment_list:
        if equip.alert_level in ("HEALTHY", "UNKNOWN"):
            skipped_healthy += 1
            continue
        try:
            draft_req = DraftQuoteFromWearRequest(
                equipment_tag=equip.equipment_tag,
                apply_volume_discount_pct=discount_pct,
                target_delivery_weeks=delivery_weeks,
            )
            quote = sales_service.draft_quote_from_wear(draft_req)
        except Exception:
            quote = {"quote_number": "Q-ERROR", "quoted_unit_price": 0}
            
        severity = "🔴 CRITICAL" if equip.alert_level == "CRITICAL_REPLACE" else "⚠️ WARNING"
        alert_msg = (
            f"{severity}: {equip.component_name} at {equip.customer_name} ({equip.plant_location})\\n"
            f"Wear Level: {equip.estimated_wear_pct:.1f}% ({equip.days_in_service} days in service)\\n"
            f"Replacement SKU: {equip.suggested_replacement_sku}\\n"
            f"Draft Quote: {quote.get('quote_number', 'N/A')} — ${quote.get('quoted_unit_price', 0):,.2f}\\n"
            f"Lead Time: {delivery_weeks} weeks\\n"
            f"Action: Send quote to customer before component failure."
        )
        
        alerts_dispatched.append({
            "equipment_tag": equip.equipment_tag,
            "customer_name": equip.customer_name,
            "alert_level": equip.alert_level,
            "wear_pct": equip.estimated_wear_pct,
            "quote_number": quote.get("quote_number"),
            "quoted_price": quote.get("quoted_unit_price"),
            "monday_alert_message": alert_msg,
        })
        background_tasks.add_task(monday_client.post_pulse_update, 0, alert_msg)
        
    return {
        "scan_summary": f"Scanned {len(equipment_list)} equipment components",
        "alerts_dispatched": len(alerts_dispatched),
        "healthy_skipped": skipped_healthy,
        "alerts": alerts_dispatched,
    }

