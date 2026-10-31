"""Where AI answers come from.

Every feature asks for one of two things: free text, or JSON that fits a
pydantic model. The provider decides which engine produces it:

- ``groq`` (default): Groq's free tier over its OpenAI-compatible API. Fast
  and free, but rate limited (per minute and per day) and prompts are sent to
  Groq (US servers; nothing kept by default). Structured answers use strict
  JSON-schema output. A rate-limited call can fall back to AI_FALLBACK_PROVIDER.
- ``ollama``: local models, nothing leaves the building and nothing is
  billed, but slow on a CPU. Structured answers use Ollama's ``format``.
- ``claude``: the Anthropic API, for when local models are not good enough
  (drawings as images, careful translation). Needs ANTHROPIC_API_KEY and the
  ``anthropic`` package; never the default.
- ``off``: every call raises AIUnavailableError, so features say plainly that
  AI is off instead of returning something that looks like an answer.

Providers never decide anything; callers validate what comes back and keep a
person in the loop for anything binding.
"""

import base64
import copy
import json
import time
from typing import Any, Dict, List, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.core.logging import logger

T = TypeVar("T", bound=BaseModel)


class AIUnavailableError(Exception):
    """AI could not answer: switched off, engine down, model missing, or a bad answer."""


class AIRateLimitedError(AIUnavailableError):
    """The engine's usage limit is reached for now; another engine may still answer."""


def _inline_refs(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Resolves $ref/$defs so engines with simple schema support accept it."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def _strict_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """The form strict structured output needs: every object closed, every field required.

    Fields with defaults become required too; the model then fills them in,
    which pydantic accepts. Optional fields stay nullable (an anyOf with null).
    """
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            node = {k: walk(v) for k, v in node.items() if k not in ("default", "title")}
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(_inline_refs(schema))


def _parse(schema: Type[T], raw: str, engine: str) -> T:
    try:
        return schema.model_validate_json(raw)
    except ValidationError as exc:
        logger.warning(f"{engine} returned JSON that does not fit {schema.__name__}: {exc}")
        raise AIUnavailableError(f"The AI answer did not have the expected shape ({schema.__name__}).")


def _media_type(img: bytes) -> str:
    """The image's real type from its magic bytes; an uploaded drawing may be a JPEG."""
    if img.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if img[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if img[:4] == b"RIFF" and img[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


class AIProvider:
    name = "off"

    @property
    def model(self) -> str:
        return ""

    def status(self) -> Dict[str, Any]:
        return {"provider": self.name, "model": self.model, "ready": False, "detail": "AI is switched off."}

    def text(self, system: str, prompt: str) -> str:
        raise AIUnavailableError("AI is switched off (AI_PROVIDER=off).")

    def structured(self, system: str, prompt: str, schema: Type[T], images: Optional[List[bytes]] = None) -> T:
        raise AIUnavailableError("AI is switched off (AI_PROVIDER=off).")

    @property
    def can_see_images(self) -> bool:
        return False

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        """One turn of a tool-using conversation, in OpenAI message format.

        Returns {"content": str, "tool_calls": [{"id", "name", "arguments": dict}]}.
        """
        raise AIUnavailableError(f"{self.name} does not support the assistant's tools.")


def _openai_calls(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    calls = []
    for i, c in enumerate(message.get("tool_calls") or []):
        fn = c.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = {"_unreadable": args[:200]}
        calls.append({"id": c.get("id") or f"call_{i}", "name": fn.get("name") or "", "arguments": args or {}})
    return calls


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, host: Optional[str] = None):
        self.host = (host or settings.OLLAMA_HOST).rstrip("/")

    @property
    def model(self) -> str:
        return settings.AI_TEXT_MODEL

    @property
    def can_see_images(self) -> bool:
        return bool(settings.AI_VISION_MODEL)

    def status(self) -> Dict[str, Any]:
        try:
            tags = httpx.get(f"{self.host}/api/tags", timeout=3.0).json()
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return {
                "provider": self.name, "model": self.model, "ready": False,
                "detail": f"Ollama is not reachable at {self.host}. Start it with `ollama serve`. ({type(exc).__name__})",
            }
        installed = {m.get("name") for m in tags.get("models", [])}
        text_ready = self.model in installed
        vision = settings.AI_VISION_MODEL
        vision_ready = bool(vision) and vision in installed
        if not text_ready:
            detail = f"Model {self.model} is not installed. Run `ollama pull {self.model}`."
        else:
            detail = f"Local model {self.model} is ready. Nothing is sent outside this network."
            if vision and not vision_ready:
                detail += f" Scanned drawings need `ollama pull {vision}`."
            elif not vision:
                detail += " Scanned drawings are off (no vision model set)."
        # Text features work without the vision model, so only the text model
        # decides overall readiness.
        return {
            "provider": self.name,
            "model": self.model,
            "vision_model": vision or None,
            "vision_ready": vision_ready,
            "ready": text_ready,
            "installed_models": sorted(m for m in installed if m),
            "detail": detail,
        }

    def _chat(self, model: str, messages: List[Dict[str, Any]], fmt: Optional[Dict[str, Any]] = None) -> str:
        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            # Deterministic answers: the same email should sort the same way twice.
            "options": {
                "temperature": 0,
                "num_ctx": settings.AI_CONTEXT_TOKENS,
                "num_predict": settings.AI_MAX_OUTPUT_TOKENS,
            },
        }
        if fmt is not None:
            body["format"] = fmt
        if model.startswith("qwen3"):
            body["think"] = False  # reasoning output is not wanted in a JSON answer
        try:
            response = httpx.post(f"{self.host}/api/chat", json=body, timeout=settings.AI_TIMEOUT_SECONDS)
        except httpx.TimeoutException:
            raise AIUnavailableError(f"The local model took longer than {settings.AI_TIMEOUT_SECONDS:.0f}s.")
        except httpx.TransportError as exc:
            raise AIUnavailableError(f"Ollama is not reachable at {self.host} ({exc}).")
        if response.status_code == 404:
            raise AIUnavailableError(f"Model {model} is not installed. Run `ollama pull {model}`.")
        if response.status_code >= 400:
            raise AIUnavailableError(f"Ollama returned HTTP {response.status_code}: {response.text[:200]}")
        return (response.json().get("message") or {}).get("content") or ""

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        converted = []
        for m in messages:
            m = dict(m)
            if m.get("tool_calls"):
                m["tool_calls"] = [{"function": {"name": c["function"]["name"],
                                                 "arguments": json.loads(c["function"]["arguments"] or "{}")}}
                                   for c in m["tool_calls"]]
            converted.append(m)
        body = {"model": self.model, "messages": converted, "tools": tools, "stream": False,
                "options": {"temperature": 0, "num_ctx": settings.AI_CONTEXT_TOKENS,
                            "num_predict": settings.AI_MAX_OUTPUT_TOKENS}}
        try:
            response = httpx.post(f"{self.host}/api/chat", json=body, timeout=settings.AI_TIMEOUT_SECONDS)
        except httpx.TimeoutException:
            raise AIUnavailableError(f"The local model took longer than {settings.AI_TIMEOUT_SECONDS:.0f}s.")
        except httpx.TransportError as exc:
            raise AIUnavailableError(f"Ollama is not reachable at {self.host} ({exc}).")
        if response.status_code >= 400:
            raise AIUnavailableError(f"Ollama returned HTTP {response.status_code}: {response.text[:200]}")
        message = response.json().get("message") or {}
        return {"content": (message.get("content") or "").strip(), "tool_calls": _openai_calls(message)}

    def text(self, system: str, prompt: str) -> str:
        return self._chat(
            self.model,
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        ).strip()

    def structured(self, system: str, prompt: str, schema: Type[T], images: Optional[List[bytes]] = None) -> T:
        model = self.model
        user: Dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            if not settings.AI_VISION_MODEL:
                raise AIUnavailableError(
                    "Reading images needs a vision model. Run `ollama pull qwen2.5vl:7b` and set AI_VISION_MODEL."
                )
            model = settings.AI_VISION_MODEL
            user["images"] = [base64.b64encode(img).decode("ascii") for img in images]
        fmt = _inline_refs(schema.model_json_schema())
        raw = self._chat(model, [{"role": "system", "content": system}, user], fmt)
        return _parse(schema, raw, "Ollama")


class GroqProvider(AIProvider):
    """Groq's OpenAI-compatible API. The free tier is rate limited per model."""

    name = "groq"

    @property
    def model(self) -> str:
        return settings.GROQ_TEXT_MODEL

    @property
    def can_see_images(self) -> bool:
        return bool(settings.GROQ_VISION_MODEL)

    def status(self) -> Dict[str, Any]:
        if not settings.GROQ_API_KEY:
            return {"provider": self.name, "model": self.model, "ready": False,
                    "detail": "Set GROQ_API_KEY (free at console.groq.com) to use Groq."}
        return {
            "provider": self.name,
            "model": self.model,
            "vision_model": settings.GROQ_VISION_MODEL or None,
            "vision_ready": self.can_see_images,
            "ready": True,
            "detail": (
                f"Groq free tier ({self.model}). Prompts are sent to Groq's US servers and not kept by "
                "default; free-tier limits apply per minute and per day."
            ),
        }

    def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """One chat completion. Waits once for a short per-minute limit; longer waits are reported."""
        if not settings.GROQ_API_KEY:
            raise AIUnavailableError("Set GROQ_API_KEY to use Groq.")
        headers = {"Authorization": f"Bearer {settings.GROQ_API_KEY}"}
        url = f"{settings.GROQ_BASE_URL.rstrip('/')}/chat/completions"
        for attempt in (1, 2):
            try:
                response = httpx.post(url, json=body, headers=headers, timeout=settings.AI_TIMEOUT_SECONDS)
            except httpx.TimeoutException:
                raise AIUnavailableError(f"Groq took longer than {settings.AI_TIMEOUT_SECONDS:.0f}s.")
            except httpx.TransportError as exc:
                raise AIUnavailableError(f"Groq is not reachable from this server ({type(exc).__name__}).")
            if response.status_code == 429:
                wait = _retry_after(response.headers.get("retry-after"))
                if attempt == 1 and wait <= 10:
                    time.sleep(wait)
                    continue
                raise AIRateLimitedError(
                    f"Groq's free-tier limit is reached; try again in about {max(1, round(wait))}s."
                )
            if response.status_code in (401, 403):
                raise AIUnavailableError("Groq rejected the key (GROQ_API_KEY).")
            if response.status_code == 413:
                raise AIUnavailableError("This is too much text for Groq's free tier in one go; shorten it.")
            if response.status_code >= 400:
                raise _GroqBadRequest(response.status_code, response.text[:300])
            return response.json()
        raise AIRateLimitedError("Groq's free-tier limit is reached; try again shortly.")

    def _body(self, model: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0,  # the same email should sort the same way twice
            "max_completion_tokens": settings.AI_MAX_OUTPUT_TOKENS,
        }
        if model.startswith("openai/gpt-oss"):
            # Thinking tokens count against the answer budget and the per-minute limit.
            body["reasoning_effort"] = settings.GROQ_REASONING_EFFORT or "low"
            body["max_completion_tokens"] = settings.AI_MAX_OUTPUT_TOKENS * 2
        return body

    @staticmethod
    def _content(data: Dict[str, Any]) -> str:
        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise AIUnavailableError("The AI answer was cut off (too long); try a shorter request.")
        return ((choice.get("message") or {}).get("content") or "").strip()

    def text(self, system: str, prompt: str) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        try:
            return self._content(self._post(self._body(self.model, messages)))
        except _GroqBadRequest as exc:
            raise AIUnavailableError(f"Groq returned HTTP {exc.status}: {exc.detail}")

    def structured(self, system: str, prompt: str, schema: Type[T], images: Optional[List[bytes]] = None) -> T:
        model = self.model
        content: Any = prompt
        if images:
            if not settings.GROQ_VISION_MODEL:
                raise AIUnavailableError("Reading images needs GROQ_VISION_MODEL set.")
            model = settings.GROQ_VISION_MODEL
            content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {
                    "url": f"data:{_media_type(img)};base64,{base64.b64encode(img).decode('ascii')}"}}
                for img in images[:3]  # the vision model takes at most 3 images per request
            ]
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        strict = _strict_schema(schema.model_json_schema())
        # Strict schema first. A model or schema Groq will not constrain falls
        # back to JSON mode with the schema in the prompt; pydantic still checks it.
        formats = [
            {"type": "json_schema", "json_schema": {"name": schema.__name__, "strict": True, "schema": strict}},
            {"type": "json_object"},
        ]
        last: Optional[_GroqBadRequest] = None
        for fmt in formats:
            body = self._body(model, messages if fmt["type"] == "json_schema" else [
                {"role": "system", "content": f"{system}\n\nAnswer with JSON only, matching this schema:\n"
                                              f"{json.dumps(strict)}"},
                {"role": "user", "content": content},
            ])
            body["response_format"] = fmt
            try:
                return _parse(schema, self._content(self._post(body)), "Groq")
            except _GroqBadRequest as exc:
                last = exc
                logger.warning(f"Groq refused {fmt['type']} for {schema.__name__} on {model}: {exc.detail}")
        raise AIUnavailableError(f"Groq returned HTTP {last.status}: {last.detail}")

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        # gpt-oss on Groq makes one tool call per turn; parallel calls are not offered.
        body = self._body(self.model, messages)
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        try:
            data = self._post(body)
        except _GroqBadRequest as exc:
            raise AIUnavailableError(f"Groq returned HTTP {exc.status}: {exc.detail}")
        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise AIUnavailableError("The AI answer was cut off (too long); ask something narrower.")
        message = choice.get("message") or {}
        return {"content": (message.get("content") or "").strip(), "tool_calls": _openai_calls(message)}


class _GroqBadRequest(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _retry_after(value: Optional[str]) -> float:
    try:
        return max(0.0, float(value)) if value else 5.0
    except ValueError:
        return 5.0


class FallbackProvider(AIProvider):
    """The main engine, and a second one for when the first is rate limited or down."""

    def __init__(self, primary: AIProvider, fallback: AIProvider):
        self.primary, self.fallback = primary, fallback
        self.name = primary.name

    @property
    def model(self) -> str:
        return self.primary.model

    @property
    def can_see_images(self) -> bool:
        return self.primary.can_see_images or self.fallback.can_see_images

    def status(self) -> Dict[str, Any]:
        out = self.primary.status()
        out["fallback"] = {"provider": self.fallback.name, "model": self.fallback.model}
        return out

    def _run(self, call):
        try:
            return call(self.primary)
        except AIUnavailableError as exc:
            if isinstance(exc, AIRateLimitedError) or not self.primary.status().get("ready"):
                logger.warning(f"{self.primary.name} unavailable ({exc}); using {self.fallback.name}.")
                return call(self.fallback)
            raise

    def text(self, system: str, prompt: str) -> str:
        return self._run(lambda p: p.text(system, prompt))

    def structured(self, system: str, prompt: str, schema: Type[T], images: Optional[List[bytes]] = None) -> T:
        return self._run(lambda p: p.structured(system, prompt, schema, images))

    def chat(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._run(lambda p: p.chat(messages, tools))


class ClaudeProvider(AIProvider):
    """Anthropic API. Opt-in only: it costs money per call."""

    name = "claude"

    def __init__(self):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            raise AIUnavailableError("AI_PROVIDER=claude needs the anthropic package (`pip install anthropic`).")
        self._client = None

    @property
    def model(self) -> str:
        return settings.AI_CLAUDE_MODEL

    @property
    def can_see_images(self) -> bool:
        return True

    def _api(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(timeout=settings.AI_TIMEOUT_SECONDS)
        return self._client

    def status(self) -> Dict[str, Any]:
        import os

        has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        return {
            "provider": self.name,
            "model": self.model,
            "ready": has_key,
            "detail": (
                f"Anthropic API ({self.model}). Every call is billed."
                if has_key else "Set ANTHROPIC_API_KEY to use the Anthropic API."
            ),
        }

    def _call(self, system: str, content: List[Dict[str, Any]], schema: Optional[Type[T]] = None):
        import anthropic

        kwargs: Dict[str, Any] = dict(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": content}],
            # A policy decline on one model is retried on a suitable other one.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        try:
            if schema is not None:
                response = self._api().beta.messages.parse(output_format=schema, **kwargs)
            else:
                response = self._api().beta.messages.create(**kwargs)
        except anthropic.RateLimitError:
            raise AIUnavailableError("The Anthropic API is rate limiting this account; try again shortly.")
        except anthropic.AuthenticationError:
            raise AIUnavailableError("The Anthropic API rejected the key (ANTHROPIC_API_KEY).")
        except anthropic.APIStatusError as exc:
            raise AIUnavailableError(f"The Anthropic API returned HTTP {exc.status_code}.")
        except anthropic.APIConnectionError:
            raise AIUnavailableError("The Anthropic API is not reachable from this server.")
        if response.stop_reason == "refusal":
            raise AIUnavailableError("The model declined this request.")
        return response

    def text(self, system: str, prompt: str) -> str:
        response = self._call(system, [{"type": "text", "text": prompt}])
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def structured(self, system: str, prompt: str, schema: Type[T], images: Optional[List[bytes]] = None) -> T:
        content: List[Dict[str, Any]] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": _media_type(img), "data": base64.standard_b64encode(img).decode()},
            }
            for img in images or []
        ]
        content.append({"type": "text", "text": prompt})
        response = self._call(system, content, schema)
        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            text = "".join(b.text for b in response.content if b.type == "text")
            return _parse(schema, text, "Claude")
        return parsed


_override: Optional[AIProvider] = None


def _named(choice: str) -> AIProvider:
    choice = (choice or "").strip().lower()
    if choice == "off":
        return AIProvider()
    if choice == "claude":
        try:
            return ClaudeProvider()
        except AIUnavailableError as exc:
            logger.error(str(exc))
            return AIProvider()
    if choice == "ollama":
        return OllamaProvider()
    return GroqProvider()


def get_provider() -> AIProvider:
    """The configured provider, read at call time so settings changes apply."""
    if _override is not None:
        return _override
    primary = _named(settings.AI_PROVIDER or "groq")
    fallback = (settings.AI_FALLBACK_PROVIDER or "").strip().lower()
    if fallback and fallback != primary.name:
        return FallbackProvider(primary, _named(fallback))
    return primary


def use_provider(provider: Optional[AIProvider]) -> None:
    """Swaps the provider (tests, or a one-off engine). None restores the setting."""
    global _override
    _override = provider


def to_json(value: Any) -> Any:
    """JSON-safe copy of a pydantic model or plain value, for storage."""
    if isinstance(value, BaseModel):
        return json.loads(value.model_dump_json())
    return json.loads(json.dumps(value, default=str))
