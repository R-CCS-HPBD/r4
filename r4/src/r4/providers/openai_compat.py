"""OpenAI-compatible provider — generic wrapper for any OpenAI-API endpoint."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

from . import (
    ModelCapabilities,
    Provider,
    ProviderHealth,
    ProviderStatus,
)

logger = logging.getLogger(__name__)


def _venue_token_fallback() -> str | None:
    """Frontend-venue session token from the mounted secret file (R1-5).

    Read at provider construction (never stored in env). None outside a
    venue worker — local sessions are untouched (R4_VENUE_SESSION_TOKEN_FILE
    only exists in the provisioned pod).
    """
    try:
        from ..venue import read_venue_token

        return read_venue_token()
    except Exception:  # noqa: BLE001 — venue helpers are additive; never block
        return None


def _parse_sse_line(raw_line: bytes) -> str | None:
    """Parse one SSE line from an OpenAI stream.

    Returns ``None`` for the ``[DONE]`` terminator, ``""`` for lines to skip
    (blank, malformed JSON, non-data), and the delta content otherwise.
    """
    import json as _json

    line = raw_line.decode("utf-8", errors="replace").strip()
    if not line or not line.startswith("data: "):
        return ""
    data_str = line[6:]
    if data_str == "[DONE]":
        return None
    try:
        chunk = _json.loads(data_str)
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        return delta.get("content", "")
    except (_json.JSONDecodeError, IndexError, KeyError):
        return ""


class OpenAICompatibleProvider(Provider):
    """Provider for any endpoint that speaks the OpenAI chat completions API.

    This is the escape-hatch provider: if a user has a custom vLLM,
    text-generation-inference, or any OpenAI-compatible server, they
    point this provider at it.
    """

    def __init__(
        self,
        provider_name: str = "openai-compat",
        base_url: str = "http://localhost:8000",
        api_key: str | None = None,
        default_model: str = "default",
        timeout: int = 300,
        is_local: bool = False,
        cost: float = 0.0,
    ):
        self._provider_name = provider_name
        # Strip trailing /v1 so chat()/health() can append /v1/... without
        # producing a double /v1/v1 path (e.g. a source registered with
        # "https://api.example.com/v1").
        url = base_url.rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        self._base_url = url
        self._api_key = api_key or _venue_token_fallback()
        self._default_model = default_model
        self._timeout = timeout
        self._is_local = is_local
        self._cost = cost

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def is_local(self) -> bool:
        return self._is_local

    @property
    def dial_url(self) -> str:
        return self._base_url

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def health(self) -> ProviderHealth:
        import json as _json
        import urllib.request

        url = f"{self._base_url}/v1/models"
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=self._headers(), method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode())
            latency = (time.time() - t0) * 1000
            models = [m.get("id", "") for m in data.get("data", [])]
            return ProviderHealth(
                status=ProviderStatus.HEALTHY,
                latency_ms=latency,
                models_available=models,
            )
        except Exception as exc:
            latency = (time.time() - t0) * 1000
            return ProviderHealth(
                status=ProviderStatus.UNAVAILABLE,
                latency_ms=latency,
                error=str(exc),
            )

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        import json as _json
        import urllib.error
        import urllib.request

        from ._json import read_json_response

        url = f"{self._base_url}/v1/chat/completions"
        body: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        body.update(kwargs)

        payload = _json.dumps(body).encode()
        req = urllib.request.Request(url, data=payload, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return read_json_response(resp, label=self._provider_name)
        except urllib.error.HTTPError as exc:
            from ..errors import APIError

            err_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise APIError(
                f"API error: {exc.code}",
                status_code=exc.code,
                response_body=err_body,
            ) from exc

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        import json as _json
        import urllib.request

        url = f"{self._base_url}/v1/chat/completions"
        body: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        body.update(kwargs)

        payload = _json.dumps(body).encode()
        req = urllib.request.Request(url, data=payload, headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            for raw_line in resp:
                content = _parse_sse_line(raw_line)
                if content is None:
                    break
                if content:
                    yield content

    def cost_per_1k(self, model: str | None = None) -> float:
        del model  # interface arg — flat per-1k price on this provider
        return self._cost

    def model_capabilities(self, model: str | None = None) -> ModelCapabilities:
        del model  # per-model flags not needed here — capabilities declared globally
        return ModelCapabilities(
            supports_tool_calling=True,
            supports_streaming=True,
        )
