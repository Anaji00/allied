"""The Groq provider, with Groq's HTTP API faked: request shape, strict JSON,
rate limits, fallback and transcription. No real calls are made."""

import json
from typing import List, Optional

import pytest
from pydantic import BaseModel, Field

from app.config import settings
from app.services.ai import providers, speech
from app.services.ai.providers import (
    AIRateLimitedError,
    AIUnavailableError,
    FallbackProvider,
    GroqProvider,
    _strict_schema,
)


class Line(BaseModel):
    item: str
    qty: int = 1


class Order(BaseModel):
    po: Optional[str] = None
    lines: List[Line] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class FakeResponse:
    def __init__(self, status, body=None, headers=None, text=""):
        self.status_code, self._body, self.headers, self.text = status, body, headers or {}, text or json.dumps(body)

    def json(self):
        return self._body


def _answer(content, finish="stop"):
    return FakeResponse(200, {"choices": [{"message": {"content": content}, "finish_reason": finish}]})


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_test")
    calls, replies = [], []

    def post(url, json=None, headers=None, timeout=None, **kw):
        calls.append({"url": url, "json": json, "headers": headers, **kw})
        return replies.pop(0)

    monkeypatch.setattr(providers.httpx, "post", post)
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    return calls, replies


def test_strict_schema_closes_every_object_and_requires_every_field():
    s = _strict_schema(Order.model_json_schema())
    assert s["additionalProperties"] is False and set(s["required"]) == {"po", "lines", "confidence"}
    line = s["properties"]["lines"]["items"]
    assert line["additionalProperties"] is False and set(line["required"]) == {"item", "qty"}
    assert "$defs" not in json.dumps(s) and '"default"' not in json.dumps(s)


def test_structured_answer_uses_strict_json_schema(groq):
    calls, replies = groq
    replies.append(_answer('{"po": "PO-1", "lines": [{"item": "AR400", "qty": 2}], "confidence": 0.9}'))
    out = GroqProvider().structured("sys", "read this", Order)
    assert out.po == "PO-1" and out.lines[0].qty == 2
    body = calls[0]["json"]
    assert calls[0]["url"].endswith("/chat/completions")
    assert calls[0]["headers"]["Authorization"] == "Bearer gsk_test"
    assert body["model"] == settings.GROQ_TEXT_MODEL and body["temperature"] == 0
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["reasoning_effort"] == "low"


def test_schema_groq_refuses_falls_back_to_json_mode(groq):
    calls, replies = groq
    replies += [FakeResponse(400, {"error": {"message": "unsupported schema"}}),
                _answer('{"po": null, "lines": [], "confidence": 0.5}')]
    out = GroqProvider().structured("sys", "read this", Order)
    assert out.po is None
    assert calls[1]["json"]["response_format"] == {"type": "json_object"}
    assert "matching this schema" in calls[1]["json"]["messages"][0]["content"]


def test_short_rate_limit_waits_once_long_one_is_reported(groq):
    calls, replies = groq
    replies += [FakeResponse(429, {}, {"retry-after": "2"}), _answer("hello")]
    assert GroqProvider().text("sys", "hi") == "hello" and len(calls) == 2
    replies += [FakeResponse(429, {}, {"retry-after": "3600"})]
    with pytest.raises(AIRateLimitedError, match="free-tier limit"):
        GroqProvider().text("sys", "hi")


def test_bad_key_and_cut_off_answers_are_plain_errors(groq):
    _, replies = groq
    replies.append(FakeResponse(401, {}))
    with pytest.raises(AIUnavailableError, match="GROQ_API_KEY"):
        GroqProvider().text("sys", "hi")
    replies.append(_answer('{"po": "PO', finish="length"))
    with pytest.raises(AIUnavailableError, match="cut off"):
        GroqProvider().structured("sys", "x", Order)


def test_images_go_to_the_vision_model_as_data_urls(groq):
    calls, replies = groq
    replies.append(_answer('{"po": "PO-9", "lines": [], "confidence": 0.8}'))
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    GroqProvider().structured("sys", "read the drawing", Order, images=[png] * 5)
    body = calls[0]["json"]
    parts = body["messages"][1]["content"]
    assert body["model"] == settings.GROQ_VISION_MODEL
    images = [p for p in parts if p["type"] == "image_url"]
    assert len(images) == 3 and images[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_no_key_says_so_and_status_is_not_ready(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    assert GroqProvider().status()["ready"] is False
    with pytest.raises(AIUnavailableError, match="GROQ_API_KEY"):
        GroqProvider().text("sys", "hi")


def test_rate_limited_call_falls_back_to_the_second_engine():
    class Busy(providers.AIProvider):
        name = "groq"

        def status(self):
            return {"ready": True}

        def text(self, system, prompt):
            raise AIRateLimitedError("limit")

    class Local(providers.AIProvider):
        name = "ollama"

        def text(self, system, prompt):
            return "local answer"

    assert FallbackProvider(Busy(), Local()).text("s", "p") == "local answer"


def test_provider_choice_and_fallback_setting(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "groq")
    monkeypatch.setattr(settings, "AI_FALLBACK_PROVIDER", "")
    assert isinstance(providers.get_provider(), GroqProvider)
    monkeypatch.setattr(settings, "AI_FALLBACK_PROVIDER", "ollama")
    chosen = providers.get_provider()
    assert isinstance(chosen, FallbackProvider) and chosen.fallback.name == "ollama"
    monkeypatch.setattr(settings, "AI_PROVIDER", "ollama")
    assert isinstance(providers.get_provider(), providers.OllamaProvider)


def test_recordings_are_transcribed_by_groq_whisper(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_test")
    sent = {}

    def post(url, headers=None, data=None, files=None, timeout=None):
        sent.update(url=url, data=data, name=files["file"][0])
        return FakeResponse(200, {"text": " Where is my order? ", "language": "en", "duration": 4.24})

    monkeypatch.setattr(speech.httpx, "post", post)
    assert speech.status()["ready"]
    out = speech.transcribe(b"RIFF....WAVE", suffix=".wav", language="en")
    assert out == {"text": "Where is my order?", "language": "en", "duration_seconds": 4.2}
    assert sent["url"].endswith("/audio/transcriptions") and sent["data"]["model"] == settings.GROQ_SPEECH_MODEL
    assert sent["name"] == "recording.wav" and sent["data"]["language"] == "en"
    with pytest.raises(AIUnavailableError, match="25 MB"):
        speech.transcribe(b"0" * (25 * 1024 * 1024 + 1))
