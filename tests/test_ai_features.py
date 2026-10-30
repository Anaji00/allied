"""AI layer: provider switching, every feature, review queue, and email sorting.

A fake provider stands in for the model so these run without Ollama. The live
check against a real local model is test_ollama_live at the bottom, skipped
unless RUN_OLLAMA_TESTS=1.
"""

import json
import os
import uuid

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import OrderDB
from app.services.ai import drawings, features, store
from app.services.ai.drawings import Dimension, DrawingSpec, compare
from app.services.ai.providers import AIProvider, AIUnavailableError, OllamaProvider, _inline_refs, use_provider


class FakeProvider(AIProvider):
    """Returns queued answers by schema name; records what it was asked."""

    name = "fake"

    def __init__(self, answers=None, can_see=False):
        self.answers = answers or {}
        self.calls = []
        self._see = can_see

    @property
    def model(self):
        return "fake-1"

    @property
    def can_see_images(self):
        return self._see

    def structured(self, system, prompt, schema, images=None):
        self.calls.append({"schema": schema.__name__, "system": system, "prompt": prompt, "images": bool(images)})
        answer = self.answers[schema.__name__]
        if isinstance(answer, list):
            answer = answer.pop(0)
        return schema.model_validate(answer)

    def text(self, system, prompt):
        self.calls.append({"schema": "text", "system": system, "prompt": prompt, "images": False})
        return self.answers.get("text", "ok")


@pytest.fixture
def fake():
    provider = FakeProvider()
    use_provider(provider)
    yield provider
    use_provider(None)


@pytest.fixture
def ai_settings():
    saved = {k: getattr(settings, k) for k in ("AI_PROVIDER", "AI_EMAIL_TRIAGE_ENABLED", "PHONE_WEBHOOK_TOKEN")}
    yield
    for k, v in saved.items():
        setattr(settings, k, v)


def _triage(**over):
    base = {
        "category": "order", "language": "en", "summary": "Orders plate.", "customer_name": "Owens Corning",
        "po_number": f"PO-AI-{uuid.uuid4().hex[:6].upper()}", "requested_date": "",
        "lines": [{"description": "AR400 wear plate", "quantity": 2, "unit": "EA", "unit_price": 0}],
        "uncertain_fields": [], "confidence": 0.95,
    }
    base.update(over)
    return base


# -- provider ------------------------------------------------------------------


def test_off_switch_says_so_instead_of_answering(client, ai_settings):
    settings.AI_PROVIDER = "off"
    resp = client.post("/api/v1/ai/translate", json={"text": "hello", "target": "vi"})
    assert resp.status_code == 503
    assert "switched off" in resp.json()["detail"]


def test_ollama_down_is_reported_not_raised(ai_settings):
    status = OllamaProvider(host="http://127.0.0.1:9").status()
    assert status["ready"] is False and "not reachable" in status["detail"]
    with pytest.raises(AIUnavailableError):
        OllamaProvider(host="http://127.0.0.1:9").structured("s", "p", features.Translation)


def test_schema_refs_are_inlined_for_ollama():
    schema = _inline_refs(features.EmailTriage.model_json_schema())
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)


def test_status_endpoint(client, fake):
    body = client.get("/api/v1/ai/status").json()
    assert body["engine"]["provider"] == "fake" and set(body["languages"]) == {"en", "vi", "zh"}


# -- features ------------------------------------------------------------------


def test_translation_uses_only_approved_glossary_terms(client, fake, tmp_path, ai_settings):
    gloss = tmp_path / "g.json"
    gloss.write_text(json.dumps({"terms": [
        {"en": "wear plate", "vi": "tam chiu mai mon", "approved": True},
        {"en": "tolerance", "vi": "dung sai", "approved": False},
    ]}), encoding="utf-8")
    settings.AI_GLOSSARY_PATH = gloss
    try:
        fake.answers["text"] = 'Translation: "xin chao [[tolerance]]"'
        resp = client.post("/api/v1/ai/translate", json={"text": "Wear plate tolerance", "target": "vi"})
    finally:
        settings.AI_GLOSSARY_PATH = None
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["translation"] == "xin chao [[tolerance]]" and result["unsure_terms"] == ["tolerance"]
    system = fake.calls[-1]["system"]
    assert "tam chiu mai mon" in system and "dung sai" not in system
    assert store.get(resp.json()["suggestion_id"])["status"] == "INFO"


def test_unsure_or_order_emails_wait_for_a_person(client, fake):
    fake.answers["EmailTriage"] = [_triage(confidence=0.4), _triage(category="junk", confidence=0.99)]
    unsure = client.post("/api/v1/ai/email/triage", json={"subject": "PO", "body": "please ship"}).json()
    junk = client.post("/api/v1/ai/email/triage", json={"subject": "win", "body": "prize"}).json()
    assert unsure["needs_review"] is True
    assert junk["needs_review"] is False


def test_drafts_that_promise_something_need_approval(client, fake):
    fake.answers["text"] = "Subject: Order received\n\nThank you, we have received your order."
    plain = client.post("/api/v1/ai/email/draft", json={"kind": "order_acknowledgement", "facts": "PO"}).json()
    fake.answers["text"] = "Subject: Delivery\n\nIt ships on Nov 14 at $12 per foot."
    promise = client.post("/api/v1/ai/email/draft", json={"kind": "delivery_update", "facts": "Friday"}).json()
    assert plain["result"]["subject"] == "Order received" and plain["needs_review"] is False
    # Decided by rule (money, date, number), not by the model's own say-so.
    assert promise["needs_review"] is True and promise["result"]["states_commitment"] is True


def test_item_suggestion_ignores_invented_items_and_remembers_approved_ones(client, fake):
    candidates = features.candidate_items("AR400 plate")
    real = candidates[0]["item_id"]
    fake.answers["ItemChoice"] = [
        {"item_id": "NOT-IN-CATALOGUE", "reason": "made up", "confidence": 0.9},
        {"item_id": real, "reason": "closest", "confidence": 0.9},
    ]
    invented = client.post("/api/v1/ai/items/suggest", json={"raw_text": "AR400 plate"}).json()
    assert invented["result"]["item_id"] == ""

    raw = f"shop slang {uuid.uuid4().hex[:6]}"
    chosen = client.post("/api/v1/ai/items/suggest", json={"raw_text": raw}).json()
    assert chosen["result"]["item_id"] == real
    review = client.post(f"/api/v1/ai/suggestions/{chosen['suggestion_id']}/review", json={"approve": True})
    assert review.status_code == 200
    from app.core.container import get_entity_matcher

    assert get_entity_matcher().alias_dict.get(raw.upper()) == real
    again = client.post(f"/api/v1/ai/suggestions/{chosen['suggestion_id']}/review", json={"approve": True})
    assert again.status_code == 409


def test_call_summary_notes_the_linked_monday_job(client, fake, ai_settings):
    po = f"PO-CALL-{uuid.uuid4().hex[:5].upper()}"
    with SessionLocal() as db:
        db.add(OrderDB(order_id=uuid.uuid4().hex[:8].upper(), customer_po_number=po, sales_order_number="77001",
                       customer_id="C1", customer_name="Knauf", monday_item_id="555123"))
        db.commit()
    fake.answers["CallSummary"] = {
        "caller_name": "Sarah", "company": "Knauf", "reason": "order_status", "language": "en",
        "summary": "Asked when her order ships.", "po_numbers": [po], "action_items": [],
        "callback_needed": False, "urgency": "normal",
    }
    settings.PHONE_WEBHOOK_TOKEN = "phone-secret"
    resp = client.post("/api/v1/ai/calls?token=phone-secret", json={"transcript": "where is my order", "from_number": "+1555"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched_orders"][0]["sales_order_number"] == "77001"
    # Nothing is posted to the job until a person approves the summary.
    assert body["monday_updates"] == [] and body["needs_review"] is True
    # The read-only lookup gives status, never money.
    status = client.get(f"/api/v1/ai/order-status?number={po}&token=phone-secret").json()
    assert status["found"] and "total_revenue" not in status["orders"][0]


def test_phone_webhook_rejects_a_wrong_token(client, fake, auth_enforced, ai_settings):
    settings.PHONE_WEBHOOK_TOKEN = "phone-secret"
    resp = client.post("/api/v1/ai/calls?token=nope", json={"transcript": "hi"})
    assert resp.status_code == 401


# -- drawings ------------------------------------------------------------------


def _spec(**over):
    base = dict(
        drawing_number="DWG-100", revision="B", title="Liner", materials=["ASTM A572 Gr 50"],
        dimensions=[Dimension(label="length", value=1000, unit="mm", tolerance="+/-1"),
                    Dimension(label="width", value=500, unit="mm", tolerance="")],
        welds=["6mm fillet"], standards=["AWS D1.1"], notes_english=[], unreadable=[],
    )
    base.update(over)
    return DrawingSpec(**base)


def test_identical_drawings_show_no_differences_but_are_never_approved():
    result = compare(_spec(), _spec())
    assert result["differences"] == 0 and result["verdict"] == "no_differences_found"


def test_drawing_differences_are_listed():
    supplier = _spec(revision="C", dimensions=[
        Dimension(label="L", value=39.37, unit="in", tolerance="+/-1"),  # 1000 mm, matches
        Dimension(label="W", value=505, unit="mm", tolerance=""),        # wrong
    ])
    rows = {r["field"]: r for r in compare(_spec(), supplier)["rows"]}
    assert rows["Revision"]["status"] == "different"
    assert rows["Dimensions"]["status"] == "different"
    assert "500.0" in rows["Dimensions"]["note"] and "505.0" in rows["Dimensions"]["note"]


def test_chinese_grades_count_only_when_engineering_approved_the_mapping(tmp_path):
    grade = tmp_path / "grades.json"
    supplier = _spec(materials=["Q355B"])
    grade.write_text(json.dumps({"mappings": [{"gb": "Q355B", "astm": "ASTM A572 Gr 50", "approved": False}]}))
    settings.AI_GRADE_MAP_PATH = grade
    try:
        unapproved = {r["field"]: r for r in compare(_spec(), supplier)["rows"]}["Material"]
        grade.write_text(json.dumps({"mappings": [{"gb": "Q355B", "astm": "ASTM A572 Gr 50", "approved": True}]}))
        approved = {r["field"]: r for r in compare(_spec(), supplier)["rows"]}["Material"]
    finally:
        settings.AI_GRADE_MAP_PATH = None
    assert unapproved["status"] == "different" and "not in the approved grade table" in unapproved["note"]
    assert approved["status"] == "match"


def test_missing_values_count_as_differences():
    rows = {r["field"]: r for r in compare(_spec(), _spec(revision="", unreadable=["title block smudged"]))["rows"]}
    assert rows["Revision"]["status"] == "unreadable"
    assert rows["Unreadable on supplier"]["status"] == "unreadable"


def _pdf(text: str) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_drawing_endpoint_reads_both_and_compares(client, fake):
    fake.answers["DrawingSpec"] = [_spec().model_dump(), _spec(revision="C").model_dump()]
    text = "DWG-100 REV B LINER 1000 x 500 ASTM A572 GR 50 AWS D1.1 6mm fillet weld both sides"
    resp = client.post(
        "/api/v1/ai/drawings/compare",
        files={"master": ("m.pdf", _pdf(text), "application/pdf"), "supplier": ("s.pdf", _pdf(text), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["verdict"] == "differences_found" and result["read_as"] == {"master": "text", "supplier": "text"}
    assert store.get(resp.json()["suggestion_id"])["needs_review"] is True


def test_scanned_drawing_without_a_vision_model_explains_what_is_needed(fake):
    with pytest.raises(AIUnavailableError, match="vision model"):
        drawings.read_drawing(_pdf(""), "scan.pdf")


# -- email intake --------------------------------------------------------------


def _daemon():
    from app.services.inbound_email_daemon import InboundEmailDaemon, InboundEmailMessage

    return InboundEmailDaemon(), InboundEmailMessage


def test_email_sorting_files_non_orders_without_creating_one(fake, ai_settings):
    settings.AI_EMAIL_TRIAGE_ENABLED = True
    daemon, Msg = _daemon()
    fake.answers["EmailTriage"] = _triage(category="supplier_invoice", po_number="", lines=[])
    result = daemon.process_inbound_email(Msg(sender_email="ar@nibrock.com", subject="Invoice 88", body_text="Due"))
    assert result.status == "SORTED_SUPPLIER_INVOICE" and result.sales_order_number is None


def test_confident_ai_order_is_raised_with_the_ai_po(fake, ai_settings):
    settings.AI_EMAIL_TRIAGE_ENABLED = True
    daemon, Msg = _daemon()
    answer = _triage()
    fake.answers["EmailTriage"] = answer
    result = daemon.process_inbound_email(
        Msg(sender_email="buyer@owenscorning.com", subject="Order", body_text="Ship two plates please")
    )
    assert result.customer_po_number == answer["po_number"]
    assert result.status == "PROCESSED_ORDER_CREATED"


def test_unsure_ai_order_goes_to_a_person(fake, ai_settings):
    settings.AI_EMAIL_TRIAGE_ENABLED = True
    daemon, Msg = _daemon()
    fake.answers["EmailTriage"] = _triage(confidence=0.5, uncertain_fields=["quantity"])
    result = daemon.process_inbound_email(Msg(sender_email="b@x.com", subject="Order?", body_text="maybe some plate"))
    assert result.status == "FLAGGED_FOR_REVIEW" and result.sales_order_number is None


def test_email_intake_falls_back_to_patterns_when_ai_is_down(ai_settings):
    settings.AI_EMAIL_TRIAGE_ENABLED = True
    use_provider(AIProvider())  # "off" raises AIUnavailableError
    try:
        daemon, Msg = _daemon()
        result = daemon.process_inbound_email(Msg(
            sender_email="procurement@knaufinsulation.com", subject="Reorder",
            body_text=f"Customer PO Number: KNF-{uuid.uuid4().hex[:6].upper()}. 2 units of Ceramic Hose.",
        ))
    finally:
        use_provider(None)
    assert result.status in ("PROCESSED_ORDER_CREATED", "FLAGGED_FOR_REVIEW")
    assert not result.status.startswith("SORTED_")


# -- live local model ----------------------------------------------------------


@pytest.mark.skipif(os.environ.get("RUN_OLLAMA_TESTS") != "1", reason="set RUN_OLLAMA_TESTS=1 with Ollama running")
def test_ollama_live():
    use_provider(None)
    triage, _ = features.triage_email(
        "purchasing@owenscorning.com",
        "PO 4471 - wear plate",
        "Hi, please supply 6 sheets of AR400 wear plate 1/2in x 96in x 48in under our PO 4471. Need by Nov 14.",
    )
    assert triage.category == "order" and "4471" in triage.po_number
    translated, _ = features.translate("Check the weld before painting.", "vi")
    assert translated.translation.strip()


def test_vietnamese_that_drifts_into_chinese_is_retried_then_refused(fake):
    class Drifting(FakeProvider):
        def __init__(self, outputs):
            super().__init__()
            self.outputs = outputs

        def text(self, system, prompt):
            self.calls.append({"system": system})
            return self.outputs.pop(0)

    good_after_retry = Drifting(["Chào chị, 商务邮件", "Chào chị, cảm ơn."])
    use_provider(good_after_retry)
    try:
        result, _ = features.translate("Hello, thank you.", "vi")
        assert result.translation == "Chào chị, cảm ơn." and "ONLY in Vietnamese" in good_after_retry.calls[-1]["system"]
        use_provider(Drifting(["Chào 商务", "vẫn 中文"]))
        with pytest.raises(AIUnavailableError, match="mixed Chinese"):
            features.translate("Hello", "vi")
    finally:
        use_provider(None)


def test_tolerances_are_compared_per_shared_dimension():
    same = _spec(dimensions=[Dimension(label="L", value=1000, unit="mm", tolerance="±1 mm")])
    assert {r["field"]: r for r in compare(_spec(), same)["rows"]}["Tolerances"]["status"] == "match"
    loose = _spec(dimensions=[
        Dimension(label="L", value=1000, unit="mm", tolerance="±3"),
        Dimension(label="W", value=500, unit="mm", tolerance=""),
    ])
    row = {r["field"]: r for r in compare(_spec(), loose)["rows"]}["Tolerances"]
    assert row["status"] == "different" and "1000.0 mm" in row["note"]


def test_reader_output_is_cleaned_of_holes_and_general_tolerances():
    from app.services.ai.drawings import _clean

    raw = _spec(
        welds=["2x Ø22 THRU", "6mm FILLET WELD BOTH SIDES (TYP)"],
        dimensions=[Dimension(label="W", value=500, unit="mm", tolerance="±2 mm (UNTOLERANCED)"),
                    Dimension(label="L", value=1000, unit="mm", tolerance="±1")],
    )
    cleaned = _clean(raw)
    assert cleaned.welds == ["6mm FILLET WELD BOTH SIDES (TYP)"]
    assert [d.tolerance for d in cleaned.dimensions] == ["", "±1"]
