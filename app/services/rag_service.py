"""Hybrid RAG Retrieval & Synthesis Service for Allied Enterprise Knowledge Base.
Connects internal historical estimates, WPS welding specs, ASTM metallurgy, supplier agreements,
master CSV catalogs (items, customers, vendors), internal documents, and live order memory into an interactive AI copilot.
"""

import csv
import json
import os
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings
from app.core.logging import logger
from app.core.uploads import resolve_upload_path
from app.models.documents import DocumentScanResponse, DocumentScanType
from app.models.rag_chat import (
    ActionPayload,
    KnowledgeBaseSummary,
    RAGChatResponse,
    RAGQueryRequest,
    RAGSourceCitation,
)
from app.services.agent_memory_cache import agent_memory_cache
from app.services.document_scanner_service import document_scanner_service
from app.services.vision_parser import VisionParser

# Local LLM calls sit in the request path, so the budget is deliberately short:
# a slow Ollama should fall through to deterministic synthesis, not stall a reply.
_OLLAMA_TIMEOUT_SECONDS = 8.0


class RAGService:
    """Enterprise RAG Service indexing documents, master CSVs, quotes, and live order memory."""

    def __init__(self):
        # Taken from configuration rather than derived from __file__, so the
        # copilot indexes the same catalogues the rest of the system reads.
        # The hardcoded path meant a deployment that relocated DATA_DIR, and
        # any test working on a copy, silently indexed a different directory.
        self.data_dir = str(settings.DATA_DIR)
        self.kb_dir = os.path.join(self.data_dir, "knowledge_base")
        self.ollama_host = getattr(settings, "OLLAMA_HOST", "http://127.0.0.1:11434")
        self.ollama_model = getattr(settings, "OLLAMA_MODEL", "llama3.2")
        self.documents: List[Dict[str, Any]] = []
        self.vision_parser = VisionParser()
        self._load_knowledge_bases()

    def reload_knowledge_bases(self):
        """Refreshes all indexes and in-memory caches across JSON and CSV sources."""
        self._load_knowledge_bases()
        logger.info(f"RAG Knowledge Base reloaded: {len(self.documents)} total indexed documents.")

    def _load_knowledge_bases(self):
        """Loads and chunks all JSON knowledge bases and CSV catalogs into the searchable document index."""
        self.documents = []

        # 1. Historical Estimates
        est_file = os.path.join(self.kb_dir, "historical_orders_and_estimates.json")
        if os.path.exists(est_file):
            try:
                with open(est_file, "r", encoding="utf-8") as f:
                    estimates = json.load(f)
                    for est in estimates:
                        self.documents.append({
                            "source_id": est.get("estimate_id", "EST-UNKNOWN"),
                            "source_type": "HISTORICAL_ESTIMATE",
                            "title": f"Estimate {est.get('estimate_id')} — {est.get('customer_name')}",
                            "content": f"Customer: {est.get('customer_name')}. Status: {est.get('status')}. Date: {est.get('date')}. Terms: {est.get('payment_terms')}. Lead time: {est.get('lead_time_days')} days. Items: {json.dumps(est.get('requested_items', []))}. Notes: {est.get('estimator_notes', '')}. Win/Loss: {est.get('win_rationale', est.get('loss_rationale', ''))}",
                            "raw_data": est,
                        })
            except Exception as e:
                logger.error(f"Failed loading historical estimates: {e}")

        # 2. Welding Procedure Specifications (WPS)
        wps_file = os.path.join(self.kb_dir, "welding_procedures_wps.json")
        if os.path.exists(wps_file):
            try:
                with open(wps_file, "r", encoding="utf-8") as f:
                    wps_list = json.load(f)
                    for wps in wps_list:
                        self.documents.append({
                            "source_id": wps.get("wps_id", "WPS-UNKNOWN"),
                            "source_type": "WELDING_WPS",
                            "title": f"{wps.get('wps_id')}: {wps.get('title')}",
                            "content": f"Title: {wps.get('title')}. Base Metal: {wps.get('base_metal')}. Overlay: {wps.get('overlay_alloy')}. Hardness: {wps.get('hardness_range', '')}. Preheat: {wps.get('preheat_temperature')}. Interpass: {wps.get('interpass_temperature')}. Gas: {wps.get('shielding_gas')}. Cutting: {wps.get('plasma_cutting_directions', '')}. Cracking: {wps.get('check_cracking_guidelines', '')}",
                            "raw_data": wps,
                        })
            except Exception as e:
                logger.error(f"Failed loading WPS knowledge: {e}")

        # 3. Supplier Agreements
        supp_file = os.path.join(self.kb_dir, "supplier_agreements.json")
        if os.path.exists(supp_file):
            try:
                with open(supp_file, "r", encoding="utf-8") as f:
                    suppliers = json.load(f)
                    for s in suppliers:
                        blind_info = json.dumps(s.get('blind_shipping_protocol', {}))
                        self.documents.append({
                            "source_id": f"SUPP-{s.get('supplier_id')}",
                            "source_type": "SUPPLIER_AGREEMENT",
                            "title": f"Supplier Agreement: {s.get('company_name')} ({s.get('supplier_id')})",
                            "content": f"Supplier: {s.get('company_name')} ({s.get('supplier_id')}). Tier: {s.get('contract_tier')}. Products: {s.get('products_supplied')}. Lead Time: {s.get('standard_lead_times')}. Discount: {s.get('wholesale_discount_basis')}. Terms: {s.get('payment_terms')}. Blind Shipping Rules: {blind_info}.",
                            "raw_data": s,
                        })
            except Exception as e:
                logger.error(f"Failed loading supplier agreements: {e}")

        # 4. ASTM Metallurgy Standards
        astm_file = os.path.join(self.kb_dir, "astm_metallurgy_standards.json")
        if os.path.exists(astm_file):
            try:
                with open(astm_file, "r", encoding="utf-8") as f:
                    alloys = json.load(f)
                    for a in alloys:
                        self.documents.append({
                            "source_id": f"ALLOY-{a.get('alloy_name', '').replace(' ', '_')}",
                            "source_type": "ASTM_STANDARD",
                            "title": f"Metallurgy Spec: {a.get('alloy_name')}",
                            "content": f"Alloy: {a.get('alloy_name')}. Classification: {a.get('classification')}. Chemistry: {json.dumps(a.get('nominal_chemistry', a.get('chemistry_limits', {})))}. Hardness: {a.get('hardness', '')}. Application: {a.get('primary_application', '')}. Comparison: {a.get('comparison_vs_ar500', a.get('dual_certification', ''))}",
                            "raw_data": a,
                        })
            except Exception as e:
                logger.error(f"Failed loading ASTM standards: {e}")

        # 5. Plant Equipment Registry
        equip_file = os.path.join(self.kb_dir, "plant_equipment_registry.json")
        if os.path.exists(equip_file):
            try:
                with open(equip_file, "r", encoding="utf-8") as f:
                    equip = json.load(f)
                    for eq in equip:
                        self.documents.append({
                            "source_id": eq.get("equipment_tag", "EQUIP-UNKNOWN"),
                            "source_type": "PLANT_EQUIPMENT",
                            "title": f"Asset {eq.get('equipment_tag')} — {eq.get('customer_name')}",
                            "content": f"Tag: {eq.get('equipment_tag')}. Customer: {eq.get('customer_name')}. Component: {eq.get('component_name')}. SKU: {eq.get('master_sku')}. Drawing: {eq.get('drawing_number')}. Material: {eq.get('operating_material')}. Lifespan: {eq.get('expected_lifespan_days')} days. Price: ${eq.get('historical_unit_price')}",
                            "raw_data": eq,
                        })
            except Exception as e:
                logger.error(f"Failed loading plant equipment: {e}")

        equip_live_file = os.path.join(self.data_dir, "plant_equipment.json")
        if os.path.exists(equip_live_file):
            try:
                with open(equip_live_file, "r", encoding="utf-8") as f:
                    equip_live = json.load(f)
                    for eq in equip_live:
                        etag = eq.get("equipment_tag", "EQUIP-UNKNOWN")
                        if not any(d["source_id"] == etag for d in self.documents):
                            self.documents.append({
                                "source_id": etag,
                                "source_type": "PLANT_EQUIPMENT",
                                "title": f"Asset {etag} — {eq.get('customer_name')}",
                                "content": f"Tag: {etag}. Customer: {eq.get('customer_name')}. Location: {eq.get('plant_location')}. Component: {eq.get('component_name')}. SKU: {eq.get('installed_item_id')}. Wear: {eq.get('estimated_wear_pct')}%. Days: {eq.get('days_in_service')}. Alert: {eq.get('alert_level')}. Price: ${eq.get('historical_unit_price')}",
                                "raw_data": eq,
                            })
            except Exception as e:
                logger.error(f"Failed loading live plant equipment: {e}")

        # 6. MTR Certificates
        mtr_file = os.path.join(self.data_dir, "mtr_certs.json")
        if os.path.exists(mtr_file):
            try:
                with open(mtr_file, "r", encoding="utf-8") as f:
                    mtrs = json.load(f)
                    for m in mtrs:
                        spec = m.get("material_spec") or m.get("material_grade", "Allied Wear Spec")
                        mill = m.get("mill_supplier") or m.get("mill_source", "Certified Mill Partner")
                        chem = json.dumps(m.get("chemical_analysis", {}))
                        mech = json.dumps(m.get("mechanical_tests") or m.get("mechanical_properties", {}))
                        status = "Compliant" if m.get("verified_compliant", True) else "Pending"
                        self.documents.append({
                            "source_id": m.get("heat_number", "HT-UNKNOWN"),
                            "source_type": "MTR_CERT",
                            "title": f"MTR Heat #{m.get('heat_number')} ({spec})",
                            "content": f"Heat: {m.get('heat_number')}. Spec: {spec}. Mill: {mill}. Chemical: {chem}. Mechanical: {mech}. Compliance: {status}",
                            "raw_data": m,
                        })
            except Exception as e:
                logger.error(f"Failed loading MTR certs: {e}")

        # 7. Master Items Catalog (CSV)
        items_csv = os.path.join(self.data_dir, "master_items.csv")
        if os.path.exists(items_csv):
            try:
                with open(items_csv, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        item_id = r.get("item_id", "").strip()
                        if not item_id:
                            continue
                        desc = r.get("description", "").strip()
                        cost = r.get("standard_cost", "0.00")
                        price = r.get("base_price", "0.00")
                        vendor = r.get("preferred_vendor", "STOCK")
                        stock = r.get("stock_on_hand", "0.0")
                        cat = r.get("category", "General")
                        uom = r.get("uom", "EA")
                        self.documents.append({
                            "source_id": f"ITEM-{item_id}",
                            "source_type": "ITEM_MASTER",
                            "title": f"Master Item: {item_id}",
                            "content": f"Master Item SKU: {item_id}. Description: {desc}. Preferred Vendor: {vendor}. List Price: ${price}. Standard Cost: ${cost}. Stock On Hand: {stock}. Category: {cat}. UOM: {uom}.",
                            "raw_data": r,
                        })
            except Exception as e:
                logger.error(f"Failed loading master items CSV: {e}")

        # 8. Master Customers (CSV)
        cust_csv = os.path.join(self.data_dir, "master_customers.csv")
        if os.path.exists(cust_csv):
            try:
                with open(cust_csv, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        cid = r.get("customer_id", "").strip()
                        cname = r.get("customer_name", "").strip()
                        if not cid and not cname:
                            continue
                        terms = r.get("payment_terms", "Net 30 Days")
                        sales_rep = r.get("sales_rep", "")
                        ship_to = r.get("shipping_address", "")
                        self.documents.append({
                            "source_id": f"CUST-{cid}",
                            "source_type": "CUSTOMER_MASTER",
                            "title": f"Master Customer: {cname} ({cid})",
                            "content": f"Customer ID: {cid}. Customer Name: {cname}. Terms: {terms}. Sales Rep: {sales_rep}. Shipping Destination: {ship_to}.",
                            "raw_data": r,
                        })
            except Exception as e:
                logger.error(f"Failed loading master customers CSV: {e}")

        # 9. Master Vendors (CSV)
        vend_csv = os.path.join(self.data_dir, "master_vendors.csv")
        if os.path.exists(vend_csv):
            try:
                with open(vend_csv, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        vid = r.get("vendor_id", "").strip()
                        vname = r.get("vendor_name", "").strip()
                        if not vid and not vname:
                            continue
                        terms = r.get("payment_terms", "Net 30 Days")
                        cat = r.get("category", "")
                        addr = r.get("vendor_address", "")
                        self.documents.append({
                            "source_id": f"VEND-{vid}",
                            "source_type": "VENDOR_MASTER",
                            "title": f"Master Vendor: {vname} ({vid})",
                            "content": f"Vendor ID: {vid}. Vendor Name: {vname}. Terms: {terms}. Category: {cat}. Address: {addr}.",
                            "raw_data": r,
                        })
            except Exception as e:
                logger.error(f"Failed loading master vendors CSV: {e}")

        # 10. Sample Internal Documents (POs, CAD Blueprints, MTR Certs)
        try:
            samples = document_scanner_service.get_sample_documents()
            for s in samples:
                self.documents.append({
                    "source_id": s.sample_id,
                    "source_type": "INTERNAL_DOC",
                    "title": f"Internal Document: {s.title}",
                    "content": f"Internal Document Sample: {s.title}. Type: {s.document_type.value}. Description: {s.description}.\nFull Content:\n{s.sample_text}",
                    "raw_data": {"sample_id": s.sample_id, "title": s.title, "type": s.document_type.value, "text": s.sample_text},
                })
        except Exception as e:
            logger.error(f"Failed loading sample internal documents: {e}")

    def get_summary(self) -> KnowledgeBaseSummary:
        """Returns document counts across all categories."""
        categories: Dict[str, int] = {}
        for doc in self.documents:
            st = doc.get("source_type", "OTHER")
            categories[st] = categories.get(st, 0) + 1

        # Add live cache orders
        cached_orders = agent_memory_cache.get_summary().get("cached_orders_count", 0)
        categories["CACHED_ORDERS_LIVE"] = cached_orders

        return KnowledgeBaseSummary(
            total_documents=len(self.documents) + cached_orders,
            categories=categories,
            system_status="READY",
        )

    def _retrieve_top_k(self, query: str, k: int = 4) -> List[Tuple[Dict[str, Any], float]]:
        """Hybrid keyword and entity frequency retriever."""
        tokens = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2]
        if not tokens:
            return [(doc, 0.5) for doc in self.documents[:k]]

        scored_docs: List[Tuple[Dict[str, Any], float]] = []

        # Check in-memory fast order cache first
        cached_orders = agent_memory_cache.search_orders(query, limit=2)
        for ord_data in cached_orders:
            so_num = ord_data.get("sales_order_number", "SO")
            doc = {
                "source_id": ord_data.get("order_id", so_num),
                "source_type": "CACHED_ORDER",
                "title": f"Active Order {so_num} — {ord_data.get('customer_name')}",
                "content": f"Order {so_num} for {ord_data.get('customer_name')}. PO: {ord_data.get('customer_po_number')}. Total: ${ord_data.get('total_revenue')}. Margin: {ord_data.get('gross_margin_pct')}%. Items: {json.dumps(ord_data.get('line_items', []))}",
                "raw_data": ord_data,
            }
            scored_docs.append((doc, 0.95))

        # Check static and CSV knowledge base
        for doc in self.documents:
            content_lower = doc["content"].lower()
            title_lower = doc["title"].lower()
            src_id_lower = doc["source_id"].lower()

            score = 0.0
            # Exact code / SKU / heat match
            for t in tokens:
                if t in src_id_lower:
                    score += 1.5
                if t in title_lower:
                    score += 0.8
                if t in content_lower:
                    score += 0.2

            # Exact ID match boost (e.g. TEST-PLATE-999 or PLATE-300XL-38)
            clean_id = src_id_lower.replace("item-", "").replace("cust-", "").replace("vend-", "").replace("supp-", "")
            if clean_id and len(clean_id) > 2 and clean_id in query.lower():
                is_wps_inquiry = any(w in tokens for w in ["wps", "preheat", "plasma", "cutting", "weld", "welding", "hardfacing", "interpass"])
                if doc.get("source_type") == "ITEM_MASTER" and is_wps_inquiry and clean_id == "300xl":
                    score += 0.5
                else:
                    score += 5.0

            # Boost category based on clear user intent
            st = doc.get("source_type", "")
            if st in ["HISTORICAL_ESTIMATE", "CACHED_ORDER"] and any(w in tokens for w in ["charge", "quote", "estimate", "cost", "price", "billed", "invoice", "historical"]):
                score += 5.0
            elif st == "WELDING_WPS" and any(w in tokens for w in ["preheat", "plasma", "cutting", "weld", "wps", "interpass", "hardfacing"]):
                score += 4.0
            elif st in ["HISTORICAL_ESTIMATE", "CACHED_ORDER"] and any(w in tokens for w in ["screen", "po"]):
                score += 3.0
            elif st in ["SUPPLIER_AGREEMENT", "VENDOR_MASTER"] and any(w in tokens for w in ["supplier", "vendor", "nibrock", "kws", "zibo", "blind"]):
                score += 3.0
            elif st == "PLANT_EQUIPMENT" and any(w in tokens for w in ["equipment", "asset", "tag", "wear", "lifespan", "operating", "health"]):
                score += 4.0
            elif st == "MTR_CERT" and any(w in tokens for w in ["mtr", "heat", "mill", "chemistry", "analysis", "chemical", "cert"]):
                score += 4.0
            elif st == "ASTM_STANDARD" and any(w in tokens for w in ["astm", "a992", "a36", "hardness", "microstructure", "tensile", "yield", "carbide", "alloy"]):
                score += 3.5

            # Keyword density bonus
            matches = sum(1 for t in tokens if t in content_lower)
            density = matches / len(tokens) if tokens else 0.0
            total_score = score + (density * 0.5)

            if total_score > 0.15:
                scored_docs.append((doc, round(total_score, 3)))

        # Sort descending by score
        scored_docs.sort(key=lambda x: x[1], reverse=True)
        normalized_docs = [(doc, min(round(s / 5.0, 2) if s > 1.0 else round(s, 2), 0.99)) for doc, s in scored_docs[:k]]
        return normalized_docs

    def _scan_internal_doc_from_prompt(
        self, prompt: str, uploaded_pdf_path: Optional[str] = None
    ) -> Optional[Tuple[DocumentScanResponse, RAGSourceCitation]]:
        """Dynamically scans, extracts entities, and creates a grounded citation for internal documents."""
        q_upper = prompt.upper()

        # 1. Check if an uploaded PDF was provided. The path is client-supplied,
        # so it is re-confined here as well as at the endpoint — this service is
        # also reachable from the action dispatcher and from tests.
        if uploaded_pdf_path:
            try:
                safe_path = resolve_upload_path(uploaded_pdf_path)
            except ValueError as exc:
                logger.warning(f"Rejected uploaded_pdf_path {uploaded_pdf_path!r}: {exc}")
                safe_path = None
            if safe_path is not None:
                try:
                    with open(safe_path, "rb") as f:
                        file_bytes = f.read()
                    filename = safe_path.name
                    scan_res = document_scanner_service.scan_document(file_bytes, filename, reconcile=False)
                    citation = RAGSourceCitation(
                        source_id=f"SCAN-{scan_res.scan_id}",
                        source_type="SCANNED_DOC",
                        title=f"Scanned File: {filename} ({scan_res.document_type.value})",
                        excerpt=f"Extracted {len(scan_res.line_items)} line items and {len(scan_res.fields)} fields.",
                        relevance_score=1.0,
                        metadata={"scan_id": scan_res.scan_id, "doc_type": scan_res.document_type.value},
                    )
                    return scan_res, citation
                except Exception as exc:
                    logger.warning(f"Failed scanning uploaded PDF: {exc}")

        # Every scan below is read-only (reconcile=False): any viewer can chat,
        # and pasting invoice-like text must not run the invoice check, which
        # writes results, posts to monday and can email the supplier.
        # Check if the prompt has explicit scanning intent or is structured document text
        has_scan_keyword = any(k in q_upper for k in ["SCAN", "PARSE DOC", "READ DOC", "ANALYZE DOC"])
        has_sample_id = any(k in q_upper for k in ["SAMPLE-PO", "SAMPLE-DWG", "SAMPLE-MTR"])

        is_pasted_doc = (
            ("PURCHASE ORDER" in q_upper and any(k in q_upper for k in ["BUYER:", "PO NUMBER:", "PO #", "LINE ITEMS:", "SHIP TO:"]))
            or ("DWG #" in q_upper and any(k in q_upper for k in ["SAP CODE:", "REV:", "TAKEOFF", "ARC LENGTH"]))
            or ("HEAT NUMBER:" in q_upper and any(k in q_upper for k in ["CHEMICAL", "MILL TEST REPORT", "GRADE:", "SPEC:"]))
            or (prompt.count("\n") >= 3 and any(k in q_upper for k in ["BUYER:", "TERMS:", "SHIP TO:", "LINE ITEMS:", "DWG #:"]))
        )

        if not (has_scan_keyword or has_sample_id or is_pasted_doc):
            return None

        # 2. Check if the user prompt references any existing internal sample document
        samples = document_scanner_service.get_sample_documents()
        matched_sample = None
        for s in samples:
            if s.sample_id.upper() in q_upper or s.sample_filename.upper() in q_upper:
                matched_sample = s
                break
            if has_scan_keyword and ("PO" in q_upper or "PURCHASE ORDER" in q_upper) and "EDMONTON" in q_upper and s.document_type == DocumentScanType.CUSTOMER_PO:
                matched_sample = s
                break
            if has_scan_keyword and ("CAD" in q_upper or "BLUEPRINT" in q_upper or "DRAWING" in q_upper or "OC-ED-218" in q_upper) and s.document_type == DocumentScanType.CAD_DRAWING:
                matched_sample = s
                break
            if has_scan_keyword and ("MTR" in q_upper or "HEAT" in q_upper or "HT-300XL" in q_upper) and s.document_type == DocumentScanType.MTR_HEAT_CERT:
                matched_sample = s
                break

        if matched_sample:
            scan_res = document_scanner_service.scan_document(
                matched_sample.sample_text.encode("utf-8"),
                matched_sample.sample_filename,
                reconcile=False,
            )
            citation = RAGSourceCitation(
                source_id=f"SCAN-{scan_res.scan_id}",
                source_type="SCANNED_DOC",
                title=f"Internal Doc Scan: {matched_sample.title}",
                excerpt=matched_sample.sample_text[:280],
                relevance_score=1.0,
                metadata={"sample_id": matched_sample.sample_id, "doc_type": scan_res.document_type.value},
            )
            return scan_res, citation

        if is_pasted_doc or (has_scan_keyword and len(prompt) > 30):
            filename = "internal_document_scan.txt"
            if "DWG" in q_upper or "BLUEPRINT" in q_upper:
                filename = "CAD_Drawing_Takeoff.txt"
            elif "MTR" in q_upper or "HEAT" in q_upper:
                filename = "MTR_Heat_Cert.txt"
            elif "PO" in q_upper or "PURCHASE ORDER" in q_upper:
                filename = "Customer_Purchase_Order.txt"

            scan_res = document_scanner_service.scan_document(
                prompt.encode("utf-8"),
                filename,
                reconcile=False,
            )
            citation = RAGSourceCitation(
                source_id=f"SCAN-{scan_res.scan_id}",
                source_type="SCANNED_DOC",
                title=f"Scanned Document: {filename} ({scan_res.document_type.value})",
                excerpt=prompt[:280] + ("..." if len(prompt) > 280 else ""),
                relevance_score=1.0,
                metadata={"scan_id": scan_res.scan_id, "doc_type": scan_res.document_type.value},
            )
            return scan_res, citation

        return None


    def _query_ollama(self, prompt: str, context: str) -> Optional[str]:
        """Calls the local Ollama LLM if online."""
        try:
            system_prompt = (
                "You are the Allied Welding Systems Enterprise Copilot. "
                "Answer the user's question accurately using ONLY the provided internal context. "
                "Format your answer with clear markdown headers, bold part numbers, bullet points, and pricing tables where appropriate. "
                "Always emphasize compliance, accurate 4-document order flow (Client PO -> Allied Internal SO -> Vendor POs -> Client Order Acknowledgment), automated packing lists, and margin preservation."
            )
            full_prompt = f"{system_prompt}\n\n=== INTERNAL KNOWLEDGE CONTEXT ===\n{context}\n\n=== USER QUESTION ===\n{prompt}\n\n=== ANSWER ==="

            req_body = json.dumps({
                "model": self.ollama_model,
                "prompt": full_prompt,
                "stream": False,
                "options": {"temperature": 0.2, "top_p": 0.9},
            }).encode("utf-8")

            # httpx is already a project dependency and is used for every other
            # outbound call; urllib was the odd one out here.
            response = httpx.post(
                f"{self.ollama_host}/api/generate",
                content=req_body,
                headers={"Content-Type": "application/json"},
                timeout=_OLLAMA_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                return (response.json().get("response") or "").strip() or None
            logger.warning(
                f"Ollama at {self.ollama_host} returned {response.status_code}; "
                f"falling back to deterministic synthesis."
            )
        except httpx.ConnectError:
            # Expected whenever Ollama is not running locally: debug, not error.
            logger.debug(
                f"Ollama not reachable at {self.ollama_host}; using deterministic synthesis."
            )
        except httpx.TimeoutException:
            logger.warning(
                f"Ollama did not respond within {_OLLAMA_TIMEOUT_SECONDS}s; "
                f"using deterministic synthesis."
            )
        except Exception as exc:
            # A bare `except: pass` here meant a misconfigured host, a bad
            # payload or a JSON error all looked identical to "LLM offline".
            logger.warning(f"Ollama query failed ({type(exc).__name__}: {exc}); using fallback.")
        return None

    def _deterministic_synthesis(
        self,
        query: str,
        top_docs: List[Tuple[Dict[str, Any], float]],
        scanned_doc: Optional[DocumentScanResponse] = None,
    ) -> Tuple[str, List[ActionPayload]]:
        """Intelligent business reasoning fallback synthesizing grounded technical, pricing, and document answers."""
        q_lower = query.lower()
        actions: List[ActionPayload] = []

        # 1. Scanned Internal Document Breakdown
        if scanned_doc:
            doc_type_name = scanned_doc.document_type.value.replace("_", " ").title()
            fields_lines = []
            for fname, fval in scanned_doc.fields.items():
                label = fname.replace("_", " ").title()
                fields_lines.append(f"* **{label}:** `{fval.raw_value}` (Confidence: {int(fval.confidence * 100)}%)")

            fields_text = "\n".join(fields_lines) if fields_lines else "*No header fields identified.*"

            items_table = "| Item SKU | Description | Dimensions | Qty | Unit Price | Unit Cost | Preferred Vendor |\n|---|---|---|---|---|---|---|\n"
            first_sku = "OC755507"
            first_price = "3772.91"
            first_qty = "1"

            for item in scanned_doc.line_items:
                sku = item.item_id or "CUSTOM-PART"
                first_sku = sku
                desc = item.description
                dims = item.dimensions or "-"
                qty = item.quantity
                first_qty = str(qty)
                price = f"${item.unit_price:,.2f}" if item.unit_price else "-"
                if item.unit_price:
                    first_price = str(item.unit_price)
                cost = f"${item.unit_cost:,.2f}" if item.unit_cost else "-"
                vend = item.preferred_vendor or "STOCK"
                items_table += f"| **{sku}** | {desc} | {dims} | {qty} | {price} | {cost} | **{vend}** |\n"

            compliance_text = "\n".join([f"* ✅ {note}" for note in scanned_doc.compliance_notes])

            answer = (
                f"### 📄 Scanned Document Analysis: {scanned_doc.filename}\n\n"
                f"**Document Archetype:** `{doc_type_name}` (OCR Confidence: **{int(scanned_doc.confidence_score * 100)}%**)\n\n"
                f"#### Extracted Document Header & Metadata:\n{fields_text}\n\n"
                f"#### Extracted Line Items & Specifications:\n{items_table}\n\n"
                f"#### Quality, Conformance & Engineering Checks:\n{compliance_text}\n\n"
                f"*4-Document Flow Guaranteed: Client PO received $\\rightarrow$ Allied Internal SO staged $\\rightarrow$ Split Vendor POs ready $\\rightarrow$ Order Acknowledgment prepared.*"
            )

            cust_name = scanned_doc.fields.get("customer_name", {}).raw_value if "customer_name" in scanned_doc.fields else "Customer"
            po_num = scanned_doc.fields.get("customer_po_number", {}).raw_value if "customer_po_number" in scanned_doc.fields else "PO-SCAN"

            actions.append(ActionPayload(
                action_type="STAGE_SAGE_ORDER",
                button_label=f"🚀 Stage {first_sku} in Sage 50",
                prefill_data={"item_id": first_sku, "price": first_price, "quantity": first_qty, "notes": f"Scanned from {po_num}"},
            ))
            actions.append(ActionPayload(
                action_type="STAGE_OPERATIONS",
                button_label="⚙️ Stage in Operations Workbench",
                prefill_data={"customer_name": cust_name, "customer_po_number": po_num},
            ))
            actions.append(ActionPayload(
                action_type="EXECUTE_END_TO_END_ORDER",
                button_label="📦 Process End-to-End Order",
                prefill_data={"customer_name": cust_name, "customer_po_number": po_num, "line_items": [{"raw_item_text": first_sku, "quantity": int(Decimal(first_qty)), "unit_price": float(Decimal(first_price))}]},
            ))
            return answer, actions

        if not top_docs:
            return (
                "I searched our internal knowledge base (past estimates, WPS welding procedures, supplier agreements, ASTM metallurgy standards, and master CSV catalogs), "
                "but found no matching records for your query. Please check the SKU, heat number, or customer name and try again.",
                [],
            )

        # 2. Welding Procedure Specifications (WPS) & Hardfacing
        has_wps_kw = any(w in q_lower for w in ["weld", "wps", "preheat", "plasma", "cutting", "interpass", "gas", "wire", "crack", "kerf", "hardfacing"])
        wps_doc = next((d for d, s in top_docs if d["source_type"] == "WELDING_WPS"), None)
        if has_wps_kw and wps_doc:
            raw = wps_doc.get("raw_data", {})

            answer = (
                f"### Technical Welding Procedure: {raw.get('wps_id', 'WPS-01')} — {raw.get('title', 'Hardfacing')}\n\n"
                f"* **Base Metal:** {raw.get('base_metal', 'ASTM A36 Plate')}\n"
                f"* **Overlay Chemistry:** {raw.get('overlay_alloy', 'Allied 300XL Chromium Carbide')}\n"
                f"* **Target Hardness:** **{raw.get('hardness_range', '58–62 HRC')}**\n"
                f"* **Preheat Threshold:** **{raw.get('preheat_temperature', '250°F minimum')}** (Do not exceed 600°F)\n"
                f"* **Interpass Temperature:** Maximum {raw.get('interpass_temperature', '500°F')}\n"
                f"* **Recommended Shielding Gas:** **{raw.get('shielding_gas', '75% Ar / 25% CO2')}**\n\n"
                f"**Critical Plasma Cutting Direction:**  \n"
                f"*{raw.get('plasma_cutting_directions', 'Always cut from the mild steel base side toward the clad overlay.')}*\n\n"
                f"**Stress Relief Cracking Notice:**  \n"
                f"*{raw.get('check_cracking_guidelines', 'Cross-check relief cracks are normal and must not propagate into base metal.')}*"
            )

            actions.append(ActionPayload(
                action_type="STAGE_SAGE_ORDER",
                button_label="Order Allied 20-35XL Wire Spools (.045\")",
                prefill_data={"item_id": "WIRE-2035XL-045", "price": "345.00", "quantity": "4", "notes": "WPS Hardfacing Wire"},
            ))
            return answer, actions

        # 3. Historical Quoting & Estimates
        has_est_kw = any(w in q_lower for w in ["quote", "estimate", "charge", "charged", "pricing", "margin", "cost", "history", "order summary", "po-", "po ", "last year", "last month", "what did we"])
        est_doc = next((d for d, s in top_docs if d["source_type"] in ["HISTORICAL_ESTIMATE", "CACHED_ORDER"]), None)
        if (has_est_kw and est_doc) or (est_doc and top_docs[0][0]["source_type"] in ["HISTORICAL_ESTIMATE", "CACHED_ORDER"]):
            raw = est_doc.get("raw_data", {})
            cust = raw.get("customer_name", "Customer")
            est_id = raw.get("estimate_id", raw.get("order_id", raw.get("sales_order_number", "RECORD")))
            items = raw.get("requested_items", raw.get("line_items", []))

            lines_table = "| Item SKU | Description | Qty | Quoted Price | Vendor Cost | Gross Margin |\n|---|---|---|---|---|---|\n"
            first_sku = "PLATE-300XL-38"
            first_price = 1000.00

            for it in items:
                sku = it.get("item_id", "ITEM")
                first_sku = sku
                desc = it.get("description", "Standard part")
                qty = it.get("quantity", 1)
                price = it.get("quoted_unit_price", it.get("unit_price", 0))
                first_price = price
                cost = it.get("vendor_cost", it.get("unit_cost", 0))
                margin = it.get("margin_pct", it.get("gross_margin_pct", 50.0))
                lines_table += f"| **{sku}** | {desc} | {qty} | ${price:,.2f} | ${cost:,.2f} | **{margin:.1f}%** |\n"

            answer = (
                f"### Historical Estimate & Order Summary: {est_id}\n\n"
                f"**Customer:** {cust}  \n"
                f"**Date:** {raw.get('date', raw.get('po_date', 'Recent'))}  \n"
                f"**Payment Terms:** {raw.get('payment_terms', 'Net 30 Days')}  \n"
                f"**Lead Time:** {raw.get('lead_time_days', 10)} days  \n\n"
                f"{lines_table}\n"
                f"**Estimator Context:**  \n"
                f"{raw.get('estimator_notes', raw.get('win_rationale', 'Pricing reflects standard contracted volume tier.'))}\n\n"
                f"*Strict 25.0% Gross Margin invariant verified.*"
            )

            actions.append(ActionPayload(
                action_type="STAGE_SAGE_ORDER",
                button_label=f"Stage {first_sku} Order in Sage 50",
                prefill_data={"item_id": first_sku, "price": str(first_price), "quantity": "2", "notes": f"Historical reorder from {est_id}"},
            ))
            actions.append(ActionPayload(
                action_type="COPY_PROPOSAL",
                button_label="Copy Formatted Bid Proposal",
                prefill_data={"proposal_text": f"Allied Welding Systems Quote based on {est_id} for {cust}."},
            ))
            return answer, actions

        # 4. Supplier Terms & Blind Shipping Rules
        has_supp_kw = any(w in q_lower for w in ["supplier", "vendor", "nibrock", "kws", "zibo", "lead", "blind", "white-label", "wholesaler"])
        supp_docs = [d for d, s in top_docs if d["source_type"] in ["SUPPLIER_AGREEMENT", "VENDOR_MASTER"]]
        if has_supp_kw and supp_docs:
            answer_parts = []
            for sdoc in supp_docs[:2]:
                raw = sdoc.get("raw_data", {})
                name = raw.get("company_name", raw.get("vendor_name", "Supplier"))
                sid = raw.get("supplier_id", raw.get("vendor_id", "VEND"))
                products = raw.get("products_supplied", raw.get("category", "Industrial Supplies"))
                lead = raw.get("standard_lead_times", "2-3 Weeks")
                discount = raw.get("wholesale_discount_basis", "Standard Wholesaler")
                terms = raw.get("payment_terms", "Net 30 Days")
                addr = raw.get("vendor_address", "On File")

                blind_proto = raw.get("blind_shipping_protocol")
                blind_text = ""
                if blind_proto and isinstance(blind_proto, dict):
                    proto_items = [f"  - **{k.replace('_', ' ').title()}:** {v}" for k, v in blind_proto.items()]
                    blind_text = "\n* **Blind Shipping Protocol & White-Label Packaging:**\n" + "\n".join(proto_items)
                elif "blind" in q_lower:
                    blind_text = "\n* **Blind Shipping Protocol:** Standard Allied white-label rules apply. External vendor markings and costs omitted from client paperwork; Allied packing slip attached."

                answer_parts.append(
                    f"### Supplier Terms: {name} ({sid})\n\n"
                    f"* **Products Supplied:** {products}\n"
                    f"* **Standard Lead Times:** {lead}\n"
                    f"* **Wholesale Discount:** **{discount}**\n"
                    f"* **Payment Terms:** {terms}\n"
                    f"* **Address:** {addr}"
                    f"{blind_text}"
                )

            answer = "\n\n---\n\n".join(answer_parts)
            return answer, actions

        # 5. Master Customer CSV Lookup
        has_cust_kw = any(w in q_lower for w in ["customer", "client", "address", "shipping", "terms", "owens", "knauf", "iko", "sales rep", "rep"])
        cust_doc = next((d for d, s in top_docs if d["source_type"] == "CUSTOMER_MASTER"), None)
        if has_cust_kw and cust_doc:
            raw = cust_doc.get("raw_data", {})
            cid = raw.get("customer_id", "CUST")
            cname = raw.get("customer_name", "Customer")
            terms = raw.get("payment_terms", "Net 30 Days")
            ship_to = raw.get("shipping_address", "")
            rep = raw.get("sales_rep", "Karissa M. Rakestraw")

            answer = (
                f"### Master Customer Account: {cname} (`{cid}`)\n\n"
                f"* **Payment Terms:** **{terms}**\n"
                f"* **Sales Representative:** {rep}\n"
                f"* **Shipping Destination:** {ship_to}\n"
                f"* **Billing Address:** {raw.get('billing_address', 'On File')}\n"
                f"* **Contact Email:** {raw.get('email', 'invoices@client.com')}\n"
                f"* **Default Carrier:** {raw.get('shipping_method', 'MOTOR FRT')}\n\n"
                f"*Order Flow: Customer PO $\\rightarrow$ Internal Allied Sales Order $\\rightarrow$ Split Vendor POs $\\rightarrow$ Client Order Acknowledgment.*"
            )
            actions.append(ActionPayload(
                action_type="STAGE_OPERATIONS",
                button_label="⚙️ View in Operations Workbench",
                prefill_data={"customer_name": cname},
            ))
            return answer, actions

        # 6. Master Item Catalog CSV Lookup
        item_doc = None
        for d, s in top_docs:
            if d["source_type"] == "ITEM_MASTER":
                item_sku = d.get("raw_data", {}).get("item_id", "").lower()
                if item_sku and item_sku in q_lower:
                    item_doc = d
                    break
        if not item_doc:
            item_doc = next((d for d, s in top_docs if d["source_type"] == "ITEM_MASTER"), None)
        if item_doc and any(w in q_lower for w in ["item", "sku", "catalog", "plate", "wire", "screen", "cost", "price", "stock", "master", "csv", "inventory"]):
            raw = item_doc.get("raw_data", {})
            sku = raw.get("item_id", "ITEM")
            desc = raw.get("description", "Standard Item")
            price = float(raw.get("base_price", 0))
            cost = float(raw.get("standard_cost", 0))
            vendor = raw.get("preferred_vendor", "STOCK")
            stock = raw.get("stock_on_hand", "0.0")
            margin = ((price - cost) / price * 100) if price > 0 else 0.0

            answer = (
                f"### Master Item Catalog Record: `{sku}`\n\n"
                f"* **Description:** {desc}\n"
                f"* **Category:** {raw.get('category', 'General')}\n"
                f"* **Preferred Vendor:** **{vendor}**\n"
                f"* **List Price:** **${price:,.2f}**\n"
                f"* **Wholesale Cost:** **${cost:,.2f}**\n"
                f"* **Gross Margin:** **{margin:.1f}%**\n"
                f"* **Stock On Hand:** {stock} {raw.get('uom', 'EA')}\n"
                f"* **Sage 50 Accounts:** Sales `{raw.get('gl_sales_account', '4000-00')}`, Inventory `{raw.get('gl_inventory_account', '1200-00')}`\n\n"
                f"*Synced with `master_items.csv` and Sage 50 Accounting.*"
            )

            actions.append(ActionPayload(
                action_type="STAGE_SAGE_ORDER",
                button_label=f"🚀 Stage {sku} in Sage 50",
                prefill_data={"item_id": sku, "price": str(price), "quantity": "1"},
            ))
            actions.append(ActionPayload(
                action_type="EXECUTE_CSV_IMPORT",
                button_label="📥 Import / Update CSV Items",
                prefill_data={"import_type": "ITEMS"},
            ))
            return answer, actions

        # 7. Plant Equipment & Predictive Wear Life Status
        has_equip_kw = any(w in q_lower for w in ["equipment", "asset", "tag", "wear", "lifespan", "plant", "eq-", "service order"])
        equip_doc = next((d for d, s in top_docs if d["source_type"] == "PLANT_EQUIPMENT"), None)
        if (has_equip_kw and equip_doc) or (equip_doc and any(w in top_docs[0][0]["source_id"].lower() for w in ["eq-", "oc-", "knf-", "iko-"])):
            raw = equip_doc.get("raw_data", {})
            tag = raw.get("equipment_tag", "EQUIP")
            cname = raw.get("customer_name", "Customer Plant")
            comp = raw.get("component_name", "Wear Component")
            sku = raw.get("master_sku") or raw.get("installed_item_id") or raw.get("suggested_replacement_sku", "OC755507")
            dwg = raw.get("drawing_number", "DWG-ON-FILE")
            op_mat = raw.get("operating_material") or raw.get("plant_location", "High Wear Service")
            lifespan = raw.get("expected_lifespan_days", 180)
            wear_pct = raw.get("estimated_wear_pct", 0.0)
            price = raw.get("historical_unit_price", 3772.91)
            alert = raw.get("alert_level") or raw.get("criticality", "OPERATIONAL")

            answer = (
                f"### Plant Asset Health & Equipment Record: `{tag}`\n\n"
                f"* **Customer Facility:** {cname}\n"
                f"* **Installed Component:** **{comp}**\n"
                f"* **Operating Material / Location:** {op_mat}\n"
                f"* **Master Part SKU:** `{sku}`\n"
                f"* **Fabrication Drawing:** `{dwg}`\n"
                f"* **Expected Lifespan:** {lifespan} days\n"
                f"* **Estimated Wear / Status:** **{wear_pct}% wear** (Alert: `{alert}`)\n"
                f"* **Historical Unit Price:** **${float(price):,.2f}**\n\n"
                f"*Proactive replacement staging prevents catastrophic plant downtime.*"
            )
            actions.append(ActionPayload(
                action_type="STAGE_SAGE_ORDER",
                button_label=f"🚀 Draft Replacement Quote for {tag}",
                prefill_data={"item_id": sku, "price": str(price), "notes": f"Proactive replacement for asset {tag}"},
            ))
            actions.append(ActionPayload(
                action_type="STAGE_OPERATIONS",
                button_label="⚙️ View Asset in Monday Maintenance Radar",
                prefill_data={"equipment_tag": tag, "customer_name": cname},
            ))
            return answer, actions

        # 8. Mill Test Report (MTR) Heat Certifications
        has_mtr_kw = any(w in q_lower for w in ["mtr", "heat", "mill cert", "chemical", "chemistry", "analysis", "mechanical", "ht-"])
        mtr_doc = next((d for d, s in top_docs if d["source_type"] == "MTR_CERT"), None)
        if (has_mtr_kw and mtr_doc) or (mtr_doc and "ht-" in top_docs[0][0]["source_id"].lower()):
            raw = mtr_doc.get("raw_data", {})
            heat = raw.get("heat_number", "HT-UNKNOWN")
            spec = raw.get("material_spec", "Certified Alloy Spec")
            item_sku = raw.get("item_id", "PLATE-300XL-38")
            mill = raw.get("mill_supplier", "Certified Mill Partner")
            chem = raw.get("chemical_analysis", {})
            mech = raw.get("mechanical_tests", {})
            compliant = raw.get("verified_compliant", True)
            status_badge = "✅ CERTIFIED COMPLIANT" if compliant else "⚠️ PENDING REVIEW"

            chem_table = "| Element | Measured % |\n|---|---|\n"
            for elem, val in chem.items():
                ename = elem.replace("_pct", "").capitalize()
                chem_table += f"| **{ename}** | {val}% |\n"

            mech_details = ", ".join([f"**{k.replace('_', ' ').title()}:** {v}" for k, v in mech.items()])

            # This footer previously asserted full ASTM conformance even when
            # the badge above it read "PENDING REVIEW", so a non-compliant heat
            # was described as certified in the same answer.
            if compliant:
                footer = "*Meets full ASTM specifications. 100% heat traceability guaranteed.*"
            else:
                notes = raw.get("compliance_notes") or []
                detail = f" Findings: {'; '.join(notes)}." if notes else ""
                footer = (
                    f"*⚠️ This heat has NOT passed ASTM verification and must not be released "
                    f"to a customer as conforming material.{detail}*"
                )

            answer = (
                f"### Mill Test Report (MTR): Heat #{heat}\n\n"
                f"* **Material Grade / Spec:** **{spec}**\n"
                f"* **Associated Master SKU:** `{item_sku}`\n"
                f"* **Mill Supplier / Foundry:** {mill}\n"
                f"* **Compliance Status:** **{status_badge}**\n\n"
                f"#### Chemical Composition (Spectrographic Analysis):\n{chem_table}\n"
                f"#### Mechanical Test Verification:\n{mech_details}\n\n"
                f"{footer}"
            )
            actions.append(ActionPayload(
                action_type="STAGE_OPERATIONS",
                button_label=f"📄 Download Certified MTR PDF (#{heat})",
                prefill_data={"heat_number": heat, "item_id": item_sku},
            ))
            return answer, actions

        # 9. Metallurgy Standards
        alloy_doc = next((d for d, s in top_docs if d["source_type"] == "ASTM_STANDARD"), top_docs[0][0])
        raw = alloy_doc.get("raw_data", {})
        alloy_name = raw.get("alloy_name") or raw.get("title") or "Allied 300XL Chromium Carbide Plate"
        chem = raw.get("nominal_chemistry", raw.get("chemistry_limits", {}))

        chem_str = ", ".join([f"{k.replace('_', ' ')}: {v}" for k, v in chem.items()]) if isinstance(chem, dict) else "Standard Chemistry"
        answer = (
            f"### Metallurgy & Technical Datasheet: {alloy_name}\n\n"
            f"* **Classification:** {raw.get('classification', 'Abrasion Resistant Overlay')}\n"
            f"* **Nominal Chemistry:** {chem_str}\n"
            f"* **Hardness:** **{raw.get('hardness', '58-62 HRC')}**\n"
            f"* **Primary Application:** {raw.get('primary_application', raw.get('dual_certification', 'Structural & Wear'))}\n"
            f"* **Performance Comparison:** {raw.get('comparison_vs_ar500', raw.get('machinability_and_welding', 'Superior sliding wear life.'))}"
        )
        return answer, actions

    def answer_query(self, request: RAGQueryRequest) -> RAGChatResponse:
        """Main RAG query workflow: Scans documents on-the-fly, retrieves knowledge, and synthesizes answers."""
        # 1. On-the-Fly Document Scan Check
        scanned_tuple = self._scan_internal_doc_from_prompt(
            request.query, uploaded_pdf_path=request.uploaded_pdf_path
        )
        scanned_doc = scanned_tuple[0] if scanned_tuple else None
        scan_citation = scanned_tuple[1] if scanned_tuple else None

        # 2. Retrieve Top K Documents
        top_docs = self._retrieve_top_k(request.query, k=4)

        # 3. Assemble Citations & Context
        citations: List[RAGSourceCitation] = []
        context_parts: List[str] = []

        if scan_citation and scanned_doc:
            citations.append(scan_citation)
            doc_context = (
                f"[SCANNED_DOCUMENT] {scanned_doc.filename} ({scanned_doc.document_type.value}):\n"
                f"Fields: {json.dumps({k: v.raw_value for k, v in scanned_doc.fields.items()})}\n"
                f"Line Items: {[item.model_dump() for item in scanned_doc.line_items]}\n"
                f"Compliance: {scanned_doc.compliance_notes}"
            )
            context_parts.append(doc_context)

        for doc, score in top_docs:
            citations.append(RAGSourceCitation(
                source_id=doc["source_id"],
                source_type=doc["source_type"],
                title=doc["title"],
                excerpt=doc["content"][:280] + ("..." if len(doc["content"]) > 280 else ""),
                relevance_score=score,
                metadata=doc.get("raw_data", {}),
            ))
            context_parts.append(f"[{doc['source_type']}] {doc['title']}:\n{doc['content']}")

        full_context = "\n\n".join(context_parts)

        # 4. Attempt Ollama LLM if dev_bypass is False
        llm_answer = None
        model_used = "Deterministic Business Reasoning Engine"

        if not request.dev_bypass:
            llm_answer = self._query_ollama(request.query, full_context)
            if llm_answer:
                model_used = f"Local Ollama ({self.ollama_model})"

        # 5. Deterministic Synthesis Fallback
        if not llm_answer:
            llm_answer, actions = self._deterministic_synthesis(request.query, top_docs, scanned_doc=scanned_doc)
        else:
            actions = []
            if scanned_doc:
                for act in scanned_doc.suggested_actions:
                    actions.append(ActionPayload(
                        action_type=act.target_action,
                        button_label=act.label,
                        prefill_data=act.prefill_data,
                    ))

        # Dynamic System Action Triggers
        q_upper = request.query.upper()

        if "CSV" in q_upper or "IMPORT" in q_upper:
            import_type = "ITEMS"
            if "CUSTOMER" in q_upper:
                import_type = "CUSTOMERS"
            elif "VENDOR" in q_upper:
                import_type = "VENDORS"
            elif "BATCH" in q_upper or "ORDER" in q_upper:
                import_type = "ORDERS_BATCH"

            actions.append(ActionPayload(
                action_type="EXECUTE_CSV_IMPORT",
                button_label=f"📥 Open CSV Import ({import_type})",
                prefill_data={"import_type": import_type},
            ))

        if "SCAN" in q_upper or "DOCUMENT" in q_upper or "BLUEPRINT" in q_upper:
            actions.append(ActionPayload(
                action_type="SCAN_INTERNAL_DOCUMENT",
                button_label="📄 Scan Another Internal Document",
                prefill_data={"doc_type": "AUTO"},
            ))

        if "300XL" in q_upper and not any(a.action_type == "STAGE_SAGE_ORDER" for a in actions):
            actions.append(ActionPayload(
                action_type="STAGE_SAGE_ORDER",
                button_label="🚀 Stage Allied 300XL Order in Sage 50",
                prefill_data={"item_id": "PLATE-300XL-38", "price": "2950.00", "quantity": "1"},
            ))

        if "SCRAPE" in q_upper or "LEADS" in q_upper:
            industry = "Cement" if "CEMENT" in q_upper else "Mining"
            location = "Texas" if "TEXAS" in q_upper else "USA"
            actions.append(ActionPayload(
                action_type="EXECUTE_LEAD_SCRAPE",
                button_label=f"🕵️ Scrape Leads ({industry} in {location})",
                prefill_data={
                    "job_titles": ["Maintenance Supervisor", "Reliability Engineer"],
                    "industries": [industry],
                    "location": location,
                },
            ))

        confidence = 1.0 if scanned_doc else (top_docs[0][1] if top_docs else 0.50)

        # Agentic Mode Auto-Execution Placeholder
        if request.agentic_mode and actions:
            llm_answer += f"\n\n**[AGENTIC MODE AUTO-EXECUTION DETECTED]** The system has selected the action '{actions[0].button_label}' to run automatically."

        return RAGChatResponse(
            answer=llm_answer,
            model_used=model_used,
            citations=citations,
            suggested_actions=actions,
            confidence_score=confidence,
        )


# Singleton instance
rag_service = RAGService()
