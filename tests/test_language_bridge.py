"""Each person reads the app in their own language. The translation engine is a fake; no AI is called."""

import uuid
from types import SimpleNamespace

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import StaffContactDB
from app.services import bridge
from app.services.coordination import forms, updates
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
ZH, VI, EN = (f"{n}{TAG}@alliedweldingsystems.com" for n in ("li", "linh", "ann"))


class Engine:
    """Stands in for the AI: wraps the text in the target language code so tests can see what was translated."""

    def __init__(self):
        self.calls = []

    def __call__(self, text, source, target, actor):
        self.calls.append((text, source, target))
        if "BOOM" in text:
            raise RuntimeError("AI busy")
        lines = [f"{l.split('. ', 1)[0]}. [{target}] {l.split('. ', 1)[1]}" if ". " in l[:4] and "\n" in text else f"[{target}] {l}"
                 for l in text.splitlines()]
        return SimpleNamespace(translation="\n".join(lines), unsure_terms=["weld"] if "weld" in text else [])


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, client):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    eng = Engine()
    monkeypatch.setattr(bridge, "_ask", eng)
    for e in (ZH, VI, EN):
        staff_token(e, "OPERATOR")
        with SessionLocal() as db:
            if db.get(StaffContactDB, e) is None:
                db.add(StaffContactDB(email=e, name=e.split("@")[0], role_tag="staff", channels=["email"], active=True))
            db.commit()
    bridge.set_language(ZH, "zh"), bridge.set_language(VI, "vi"), bridge.set_language(EN, "en")
    return eng


def _order():
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    out = OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=f"PO-{uuid.uuid4().hex[:6].upper()}",
        shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1",
        line_items=[{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 1, "unit_price": 410}]))
    return str(out.sales_order_number)


def test_a_person_has_a_language_and_bad_choices_are_refused():
    assert bridge.language_of(ZH) == "zh" and bridge.language_of("nobody@x.com") == "en"
    with pytest.raises(ValueError, match="Choose one of"):
        bridge.set_language(ZH, "fr")
    with pytest.raises(LookupError, match="no account"):
        bridge.set_language("ghost@x.com", "zh")


def test_translation_is_cached_skips_the_same_language_and_never_raises(_env):
    a = bridge.translate("Check the heat number", "en", "zh")
    b = bridge.translate("Check the heat number", "en", "zh")
    assert a["text"] == "[zh] Check the heat number" and not a["cached"] and b["cached"] and len(_env.calls) == 1
    assert bridge.translate("anything", "en", "en") is None and bridge.translate("", "en", "zh") is None
    assert bridge.translate("BOOM", "en", "zh") is None                              # the AI is busy: no error, no translation
    assert bridge.translate("weld it", "en", "vi")["unsure"] == ["weld"]


def test_a_comment_in_mandarin_reaches_an_english_reader_and_the_reply_comes_back(_env):
    so = _order()
    first = updates.post("order", so, "我们需要确认图纸版本", {"email": ZH, "role": "OPERATOR"})
    reply = updates.post("order", so, "Please confirm revision C", {"email": EN, "role": "OPERATOR"}, parent_id=first["id"])
    en_view = updates.thread("order", so, EN, "en")
    assert en_view[0]["author_language"] == "zh" and en_view[0]["translation"]["text"] == "[en] 我们需要确认图纸版本"
    assert en_view[0]["replies"][0]["translation"] is None                           # English to an English reader: nothing to do
    zh_view = updates.thread("order", so, ZH, "zh")
    assert zh_view[0]["translation"] is None and zh_view[0]["replies"][0]["translation"]["text"] == "[zh] Please confirm revision C"
    vi_view = updates.thread("order", so, VI, "vi")
    assert vi_view[0]["translation"]["text"].startswith("[vi]") and vi_view[0]["replies"][0]["translation"]["to"] == "vi"
    n = len(_env.calls)
    updates.thread("order", so, EN, "en")
    assert len(_env.calls) == n                                                      # second look: all from the cache
    assert reply["id"]


def test_a_busy_translator_leaves_the_original_and_a_flag_and_a_big_thread_is_translated_in_instalments(_env):
    so = _order()
    updates.post("order", so, "BOOM please", {"email": ZH, "role": "OPERATOR"})
    t = updates.thread("order", so, EN, "en")
    assert t[0]["translation"] is None and t[0]["needs_translation"] is True and t[0]["body"] == "BOOM please"
    so2 = _order()
    for i in range(12):
        updates.post("order", so2, f"评论 {i}", {"email": ZH, "role": "OPERATOR"})
    first = updates.thread("order", so2, EN, "en")
    assert sum(1 for c in first if c["translation"]) == 8 and sum(1 for c in first if c["needs_translation"]) == 4
    second = updates.thread("order", so2, EN, "en")
    assert all(c["translation"] for c in second)


def test_a_removed_comment_is_not_translated(_env):
    so = _order()
    c = updates.post("order", so, "删除我", {"email": ZH, "role": "OPERATOR"})
    updates.post("order", so, "child", {"email": EN, "role": "OPERATOR"}, parent_id=c["id"])
    updates.delete(c["id"], {"email": ZH, "role": "OPERATOR"})
    top = updates.thread("order", so, EN, "en")[0]
    assert top["body"] == "(removed)" and top["translation"] is None and not top["needs_translation"]


def test_a_forms_questions_come_back_in_another_language_in_one_call(_env):
    t = forms.get("design-release")
    out = bridge.translate_many([t["instructions"], t["fields"][0]["label"], t["fields"][0]["help"]], "en", "zh")
    assert all(x.startswith("[zh]") or "[zh]" in x for x in out) and len(_env.calls) == 1
    again = bridge.translate_many([t["instructions"], t["fields"][0]["label"]], "en", "zh")
    assert again == out[:2] and len(_env.calls) == 1                                  # cached


def test_the_api_language_choice_translated_thread_and_translated_form(auth_enforced, client):
    def tok(email, role="OPERATOR"):
        return {"Authorization": f"Bearer {staff_token(email, role)}"}

    z, e = tok(ZH), tok(EN)
    assert client.get("/api/v1/language/me", headers=z).json()["language"] == "zh"
    assert client.put("/api/v1/language/me", json={"language": "vi"}, headers=z).json()["language"] == "vi"
    assert client.put("/api/v1/language/me", json={"language": "fr"}, headers=z).status_code == 400
    client.put("/api/v1/language/me", json={"language": "zh"}, headers=z)
    assert client.put(f"/api/v1/language/people/{VI}", json={"language": "zh"}, headers=z).status_code == 403
    assert client.put(f"/api/v1/language/people/{VI}", json={"language": "vi"}, headers=tok(EN, "MANAGER")).status_code == 200
    assert client.put("/api/v1/language/people/ghost@x.com", json={"language": "zh"}, headers=tok(EN, "MANAGER")).status_code == 404
    so = _order()
    client.post("/api/v1/updates", json={"item_type": "order", "item_ref": so, "body": "请检查"}, headers=z)
    th = client.get("/api/v1/updates", params={"item_type": "order", "item_ref": so}, headers=e).json()
    assert th[0]["translation"]["text"] == "[en] 请检查"
    off = client.get("/api/v1/updates", params={"item_type": "order", "item_ref": so, "lang": "zh"}, headers=e).json()
    assert off[0]["translation"] is None                                                # asked for Mandarin: already Mandarin
    assert client.get("/api/v1/updates", params={"item_type": "order", "item_ref": so, "lang": "fr"}, headers=e).status_code == 422
    f = client.get("/api/v1/forms/templates/design-release/translated", params={"lang": "zh"}, headers=z).json()
    assert f["instructions"].startswith("[zh]") and f["fields"]["customer_approval"]["options"]["Approved in writing"].startswith("[zh]")
    assert client.get("/api/v1/forms/templates/design-release/translated", params={"lang": "en"}, headers=z).status_code == 422
    assert client.get("/api/v1/forms/templates/nope/translated", params={"lang": "zh"}, headers=z).status_code == 404
