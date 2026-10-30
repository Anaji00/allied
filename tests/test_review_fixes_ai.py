"""Regression tests for the code-review fixes to documents, chat scanning and the AI layer.

No model is called: a fake provider stands in, as in test_ai_features.py.
"""

import importlib
import io
import json
import threading
import uuid

import pytest
from PIL import Image

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import OrderDB
from app.services.ai import features, store
from app.services.ai.drawings import Dimension, DrawingSpec, compare
from app.services.ai.providers import AIProvider, _media_type, use_provider
from app.services.document_scanner_service import UNREADABLE_IMAGE_NOTICE, document_scanner_service
from app.services.entity_matcher import contains_term

# The package re-exports the service instance under the module's name.
scanner_module = importlib.import_module("app.services.document_scanner_service")


class FakeProvider(AIProvider):
    name = "fake"

    def __init__(self, answers):
        self.answers = answers

    @property
    def model(self):
        return "fake-1"

    def structured(self, system, prompt, schema, images=None):
        return schema.model_validate(self.answers[schema.__name__])


@pytest.fixture
def provider():
    p = FakeProvider({})
    use_provider(p)
    yield p
    use_provider(None)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buf, format="PNG")
    return buf.getvalue()


_INVOICE = (
    "SCAN this supplier bill\n"
    "VENDOR INVOICE\n"
    "INVOICE NO: INV-REVIEW-1\n"
    "PO: 4500012345\n"
    "2 EA AR400 wear plate @ 100.00\n"
    "TOTAL DUE: 200.00\n"
)


# -- 1. document scans need an operator and a size cap -------------------------


def test_document_scan_requires_auth(client, auth_enforced):
    f = ("po.pdf", io.BytesIO(b"PURCHASE ORDER\nPO NUMBER: PO-1\nBUYER: X"), "application/pdf")
    assert client.post("/api/v1/documents/scan", files={"file": f}).status_code == 401
    f = ("po.pdf", io.BytesIO(b"PURCHASE ORDER\nPO NUMBER: PO-1\nBUYER: X"), "application/pdf")
    assert client.post("/api/v1/documents/batch-scan", files=[("files", f)]).status_code == 401


def test_document_scan_refuses_oversized_upload(client, monkeypatch):
    from app.api.v1.endpoints import documents

    monkeypatch.setattr(documents, "MAX_SCAN_BYTES", 1024)
    big = ("big.pdf", io.BytesIO(b"PURCHASE ORDER " * 500), "application/pdf")
    assert client.post("/api/v1/documents/scan", files={"file": big}).status_code == 413


# -- 2. an unreadable image is reported, never made up -------------------------


def test_unreadable_image_is_not_fabricated(monkeypatch):
    monkeypatch.setattr(scanner_module, "init_tesseract", lambda: None)
    res = document_scanner_service.scan_document(_png(), "PO_Invoice_123.png")
    assert UNREADABLE_IMAGE_NOTICE in res.raw_transcribed_text
    assert "OWENS" not in res.raw_transcribed_text.upper()
    assert res.confidence_score == 0.0
    assert res.line_items == [] and res.fields == {} and res.reconciliation is None
    assert any("check it by hand" in n for n in res.compliance_notes)


# -- 3. a scan from the chat never runs the invoice check ----------------------


def test_chat_scan_of_an_invoice_is_read_only(monkeypatch):
    from app.services.rag_service import rag_service

    calls = []
    monkeypatch.setattr(
        document_scanner_service, "_reconcile_scanned_invoice", lambda **kw: calls.append(kw) or {"x": 1}
    )
    scanned = rag_service._scan_internal_doc_from_prompt(_INVOICE)
    assert scanned is not None
    res, _citation = scanned
    assert res.document_type.value == "VENDOR_INVOICE"
    assert calls == [] and res.reconciliation is None
    assert any("Read only" in n for n in res.compliance_notes)

    # The Documents page (operators) still runs the check.
    document_scanner_service.scan_document(_INVOICE.encode(), "bill.txt")
    assert len(calls) == 1


# -- 5 and 12. call summaries wait for review; one approval posts once ---------


def test_call_summary_is_posted_only_on_approval(client, provider, monkeypatch):
    po = f"PO-REV-{uuid.uuid4().hex[:5].upper()}"
    with SessionLocal() as db:
        db.add(OrderDB(order_id=uuid.uuid4().hex[:8].upper(), customer_po_number=po, sales_order_number="88001",
                       customer_id="C1", customer_name="Knauf", monday_item_id="777001"))
        db.commit()
    provider.answers["CallSummary"] = {
        "caller_name": "Sam", "company": "Knauf", "reason": "order_status", "language": "en",
        "summary": "Asked about the order.", "po_numbers": [po], "action_items": [],
        "callback_needed": False, "urgency": "normal",
    }
    posts = []

    class FakeMonday:
        def post_pulse_update(self, item_id, text):
            posts.append((item_id, text))
            return True

    import app.core.container as container

    monkeypatch.setattr(container, "get_monday_client", lambda: FakeMonday())
    saved = settings.PHONE_WEBHOOK_TOKEN
    settings.PHONE_WEBHOOK_TOKEN = "phone-secret"
    try:
        body = client.post("/api/v1/ai/calls?token=phone-secret", json={"transcript": "where is it"}).json()
    finally:
        settings.PHONE_WEBHOOK_TOKEN = saved
    assert body["monday_updates"] == [] and posts == []
    sid = body["suggestion_id"]
    assert store.get(sid)["status"] == "SUGGESTED"

    assert client.post(f"/api/v1/ai/suggestions/{sid}/review", json={"approve": True}).status_code == 200
    assert posts == [(777001, posts[0][1])] and "Sam" in posts[0][1]
    assert client.post(f"/api/v1/ai/suggestions/{sid}/review", json={"approve": True}).status_code == 409
    assert len(posts) == 1


def test_racing_reviews_run_the_follow_up_once():
    ran = []
    feature = f"race_{uuid.uuid4().hex[:6]}"
    store.on_approve(feature)(lambda row: ran.append(row["id"]))
    sid = store.record(feature, "race", provider="fake", model="", input={}, output={})
    barrier = threading.Barrier(4)
    results = []

    def approve():
        barrier.wait()
        results.append(store.review(sid, True, {"email": "a@x", "role": "OPERATOR"}))

    threads = [threading.Thread(target=approve) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r is not None for r in results) == 1
    assert ran == [sid]


# -- 6 and 7. drawings: tolerance units, repeated values, small changes --------


def _spec(dims):
    return DrawingSpec(
        drawing_number="D-1", revision="A", title="Part", materials=["A36"], dimensions=dims,
        welds=[], standards=[], notes_english=[], unreadable=[],
    )


def _tol_row(master, supplier):
    return {r["field"]: r for r in compare(_spec(master), _spec(supplier))["rows"]}["Tolerances"]


def test_tolerances_are_compared_in_mm():
    mm = [Dimension(label="L", value=25.4, unit="mm", tolerance="±0.5mm")]
    inch = [Dimension(label="L", value=1.0, unit="in", tolerance="±0.5in")]
    assert _tol_row(mm, inch)["status"] == "different"
    same = [Dimension(label="L", value=1.0, unit="in", tolerance="±0.019685")]
    assert _tol_row(mm, same)["status"] == "match"


def test_every_tolerance_at_a_repeated_value_is_compared():
    master = [Dimension(label="A", value=50, unit="mm", tolerance="±1"),
              Dimension(label="B", value=50, unit="mm", tolerance="±0.1")]
    supplier = [Dimension(label="A", value=50, unit="mm", tolerance="±1"),
                Dimension(label="B", value=50, unit="mm", tolerance="±1")]
    assert _tol_row(master, supplier)["status"] == "different"
    assert _tol_row(master, list(reversed(master)))["status"] == "match"


def test_small_dimension_changes_are_reported():
    rows = {r["field"]: r for r in compare(
        _spec([Dimension(label="L", value=10.0, unit="mm", tolerance="")]),
        _spec([Dimension(label="L", value=10.04, unit="mm", tolerance="")]),
    )["rows"]}
    assert rows["Dimensions"]["status"] == "different"


# -- 8. aliases: validated on approval, matched as whole terms -----------------


def test_vague_aliases_are_refused():
    assert features.alias_problem("AR")
    assert features.alias_problem("  a r ")
    assert features.alias_problem("wear plate")
    assert features.alias_problem("AR400 slang") is None


def test_approving_a_short_match_does_not_create_an_alias(client, provider):
    from app.core.container import get_entity_matcher

    real = features.candidate_items("AR")[0]["item_id"]
    provider.answers["ItemChoice"] = {"item_id": real, "reason": "closest", "confidence": 0.9}
    sid = client.post("/api/v1/ai/items/suggest", json={"raw_text": "AR"}).json()["suggestion_id"]
    assert client.post(f"/api/v1/ai/suggestions/{sid}/review", json={"approve": True}).status_code == 200
    assert "AR" not in get_entity_matcher().alias_dict
    # The file stays valid JSON after a real alias is written.
    raw = f"review slang {uuid.uuid4().hex[:6]}"
    real = features.candidate_items(raw)[0]["item_id"]
    provider.answers["ItemChoice"] = {"item_id": real, "reason": "closest", "confidence": 0.9}
    sid = client.post("/api/v1/ai/items/suggest", json={"raw_text": raw}).json()["suggestion_id"]
    client.post(f"/api/v1/ai/suggestions/{sid}/review", json={"approve": True})
    assert json.loads(settings.ALIAS_DICT_PATH.read_text(encoding="utf-8"))[raw.upper()] == real


def test_alias_matching_is_word_bounded():
    assert not contains_term("4 EA WEAR PLATE", "AR")
    assert not contains_term("2 EA BAR STOCK", "AR")
    assert contains_term("2 EA AR PLATE", "AR")
    assert contains_term("2 EA HSS 4X4 TUBE", "HSS 4X4")


# -- 11. image media type -------------------------------------------------------


def test_claude_image_media_type_is_detected():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="JPEG")
    assert _media_type(buf.getvalue()) == "image/jpeg"
    assert _media_type(_png()) == "image/png"


def test_oversized_call_recording_is_refused_before_it_is_read(client, monkeypatch):
    from app.api.v1.endpoints import ai as ai_api

    monkeypatch.setattr(settings, "PHONE_WEBHOOK_TOKEN", "phone-test-token")
    monkeypatch.setattr(ai_api, "_MAX_UPLOAD_BYTES", 1024)
    seen = []
    real_form = ai_api.Request.form

    def spy(self, *a, **k):
        seen.append(True)
        return real_form(self, *a, **k)

    monkeypatch.setattr(ai_api.Request, "form", spy)
    resp = client.post("/api/v1/ai/calls/audio?token=phone-test-token",
                       files={"file": ("call.wav", b"\0" * 200_000, "audio/wav")})
    assert resp.status_code == 413 and not seen
