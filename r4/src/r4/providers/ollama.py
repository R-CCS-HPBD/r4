"""Ollama provider — connects to a local Ollama instance."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterator, List, Optional

from . import (
    ModelCapabilities,
    Provider,
    ProviderHealth,
    ProviderStatus,
)

logger = logging.getLogger(__name__)


class OllamaProvider(Provider):
    """Provider for a local Ollama instance (zero cost, no egress)."""

    # Default context length when model is unknown.
    _DEFAULT_MAX_CONTEXT = 8192  # Ollama models typically have smaller contexts

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        default_model: str = "llama3:8b",
        timeout: int = 120,
    ):
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def is_local(self) -> bool:
        return True

    @property
    def dial_url(self) -> str:
        return self._base_url

    def health(self) -> ProviderHealth:
        import json as _json
        import urllib.request

        url = f"{self._base_url}/api/tags"
        t0 = time.time()
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = _json.loads(resp.read().decode())
            latency = (time.time() - t0) * 1000
            models = [m.get("name", "") for m in data.get("models", [])]
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
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        import json as _json
        import urllib.request

        url = f"{self._base_url}/v1/chat/completions"
        body: Dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        # Ollama local models don't support the OpenAI tools parameter —
        # see model_capabilities(). Drop tools to avoid hallucinated
        # tool-call JSON leaking into the response text.
        # if tools:
        #     body["tools"] = tools

        payload = _json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return _json.loads(resp.read().decode())

    def stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        import json as _json
        import urllib.request

        url = f"{self._base_url}/v1/chat/completions"
        body: Dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        # Ollama local models don't support the OpenAI tools parameter.
        # if tools:
        #     body["tools"] = tools

        payload = _json.dumps(body).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = _json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (_json.JSONDecodeError, IndexError, KeyError):
                    continue

    def cost_per_1k(self, model: Optional[str] = None) -> float:
        return 0.0  # Local model — free

    def model_capabilities(self, model: Optional[str] = None) -> ModelCapabilities:
        # Local Ollama models (llama3.2, qwen2.5, etc.) do not reliably support
        # the OpenAI ``tools`` parameter — small models hallucinate tool-call
        # JSON as plain text content instead of emitting structured
        # ``tool_calls``. Disabling tool calling here makes the agent skip
        # the ``tools`` field and respond conversationally.
        caps = ModelCapabilities(
            supports_tool_calling=False,
            supports_streaming=True,
        )

        # Try to resolve the actual context length.
        ctx = self._resolve_context(model)
        if ctx:
            caps.max_context_length = ctx
        else:
            # No model specified — return the provider default.
            caps.max_context_length = self._DEFAULT_MAX_CONTEXT

        return caps

    def _resolve_context(self, model: Optional[str]) -> Optional[int]:
        """Resolve the real context length for an Ollama model name.

        Priority: known context patterns > None (falls back to provider default).
        """
        try:
            from r4.model_context import known_context_length

            ctx = known_context_length(model) if model else None
            if ctx:
                return ctx
        except Exception:
            pass

        return None
