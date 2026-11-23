"""AI_LOCAL_ONLY (default False): when on, AI is OFF (templates and rules only): no hosted or local model is called, whatever keys are set. A fake HTTP layer records every call; for each AI feature
(email triage, vendor replies, drafts, translation, the assistant, knowledge search, drawing reading, call audio) there must be none. Default behaviour is unchanged. No AI service is called."""
import httpx
import pytest

from app.config import settings
from app.services.ai import drawings, features, providers, speech


@pytest.fixture()
def recorder(monkeypatch):
    calls = []

    def note(*a, **k):
        url = str(a[0] if a and isinstance(a[0], str) else k.get("url", a[1] if len(a) > 1 else ""))
        calls.append(url)
        raise httpx.ConnectError("recorded, not sent")

    monkeypatch.setattr(httpx, "post", note)
    monkeypatch.setattr(httpx, "get", note)
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, *a, **k: note(url))
    monkeypatch.setattr(httpx.Client, "get", lambda self, url, *a, **k: note(url))
    monkeypatch.setattr(httpx.Client, "request", lambda self, method, url, *a, **k: note(url))
    monkeypatch.setattr(httpx.Client, "send", lambda self, req, *a, **k: note(str(req.url)))
    return calls


def test_default_is_off_and_groq_stays_the_configured_engine(monkeypatch):
    assert settings.AI_LOCAL_ONLY is False
    monkeypatch.setattr(settings, "AI_PROVIDER", "groq")
    monkeypatch.setattr(providers, "_override", None)
    assert providers.get_provider().name == "groq"


def test_local_only_means_ai_off_and_groq_speech_off(monkeypatch):
    monkeypatch.setattr(providers, "_override", None)
    monkeypatch.setattr(settings, "AI_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(settings, "AI_FALLBACK_PROVIDER", "claude")
    assert speech._use_groq() is True
    monkeypatch.setattr(settings, "AI_LOCAL_ONLY", True)
    p = providers.get_provider()
    assert p.name == "off" and p.status()["ready"] is False and speech._use_groq() is False


def test_with_local_only_on_and_a_groq_key_set_no_feature_makes_any_call(monkeypatch, recorder):
    monkeypatch.setattr(providers, "_override", None)
    monkeypatch.setattr(settings, "AI_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(settings, "AI_FALLBACK_PROVIDER", "claude")
    monkeypatch.setattr(settings, "AI_LOCAL_ONLY", True)
    from app.models.rag_chat import RAGQueryRequest
    from app.services.assistant import agent
    from app.services.rag_service import rag_service

    attempts = [
        lambda: features.triage_email("a@x.test", "RFQ", "Please quote 10 plates at $1,250.00"),
        lambda: features.read_vendor_reply("v@x.test", "Re: PO", "Ships Dec 1, Net 30", "2026-11-18"),
        lambda: features.draft_email("followup", "PO 123 is late", "en", "v@x.test"),
        lambda: features.translate("hello", "zh", "en"),
        lambda: agent.chat([{"role": "user", "content": "what is late?"}], {"email": "a@allied.test", "role": "OPERATOR"}),
        lambda: rag_service.answer_query(RAGQueryRequest(query="price of wear plate", dev_bypass=True)),
        lambda: drawings.read_drawing(b"%PDF-1.4 not really", "d.pdf"),
        lambda: speech.transcribe(b"RIFF....WAVE", suffix=".wav"),
    ]
    for go in attempts:
        try:
            go()
        except Exception:  # noqa: BLE001 - "AI is off" in whatever form the feature reports it; only the calls matter
            pass
    assert recorder == [], recorder


def test_the_recorder_does_see_groq_calls_when_the_switch_is_off(monkeypatch, recorder):
    """Control: without the switch the same fake layer records the Groq call, so the zero above means something."""
    monkeypatch.setattr(providers, "_override", None)
    monkeypatch.setattr(settings, "AI_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_test")
    monkeypatch.setattr(settings, "AI_FALLBACK_PROVIDER", "")
    monkeypatch.setattr(settings, "AI_LOCAL_ONLY", False)
    try:
        features.triage_email("a@x.test", "RFQ", "Please quote 10 plates")
    except Exception:  # noqa: BLE001
        pass
    assert any("groq" in u.lower() for u in recorder), recorder
