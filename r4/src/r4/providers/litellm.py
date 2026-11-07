"""LiteLLM provider — delegates to the platform gateway / LiteLLM proxy."""

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


class LiteLLMProvider(Provider):
    """Provider that routes through the R4 gateway → LiteLLM → vLLM stack.

    This is the default provider for all gateway-mediated deployments.
    """

    # Default context length when model is unknown.
    # Most platform models (Qwen3.x, GLM-5.2, etc.) support 128k+.
    _DEFAULT_MAX_CONTEXT = 131072

    def __init__(
        self,
        gateway_url: str = "http://localhost:8080",
        api_key: Optional[str] = None,
        default_model: str = "rivault-gpt-oss-static",
        timeout: int = 300,
    ):
        self._gateway_url = gateway_url.rstrip("/")
        self._api_key = api_key
        self._default_model = default_model
        self._timeout = timeout
        self._cached_capabilities: Optional[ModelCapabilities] = None
        self._prefix: Optional[str] = None

    @property
    def _api_prefix(self) -> str:
        """'/gw' behind the edge gateway, '/umrs' on a direct-UMRS base.

        r4._http.probe_api_prefix over urllib (this provider speaks urllib
        throughout; 2026-10-20, plan H-10.2): a direct UMRS sidecar 404s
        /ready and 200s /health. Any failure keeps the historical edge
        default.
        """
        if self._prefix is None:
            self._prefix = "/gw"
            import urllib.error
            import urllib.request

            try:
                req = urllib.request.Request(f"{self._gateway_url}/ready", method="GET")
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    try:
                        req = urllib.request.Request(f"{self._gateway_url}/health", method="GET")
                        with urllib.request.urlopen(req, timeout=5):
                            self._prefix = "/umrs"
                    except Exception:  # noqa: BLE001 - edge default stands
                        pass
            except Exception:  # noqa: BLE001 - unreachable: edge default stands
                pass
        return self._prefix

    @property
    def name(self) -> str:
        return "litellm"

    @property
    def is_local(self) -> bool:
        return False

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def health(self) -> ProviderHealth:
        import json as _json
        import urllib.error
        import urllib.request

        url = f"{self._gateway_url}{self._api_prefix}/llm/v1/models"
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
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        import json as _json
        import urllib.error
        import urllib.request

        from ._json import read_json_response

        url = f"{self._gateway_url}{self._api_prefix}/llm/v1/chat/completions"
        body: Dict[str, Any] = {
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
                return read_json_response(resp, label="gateway")
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

        url = f"{self._gateway_url}{self._api_prefix}/llm/v1/chat/completions"
        body: Dict[str, Any] = {
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
        # LiteLLM proxied models — cost depends on the backend.
        # Default to a moderate value; smart router uses rolling averages.
        return 0.002

    def model_capabilities(self, model: Optional[str] = None) -> ModelCapabilities:
        caps = ModelCapabilities(
            supports_tool_calling=True,
            supports_streaming=True,
        )

        # Try to resolve the actual context length.
        # Priority: cached > UMRS catalog > known context patterns > default.
        ctx = self._resolve_context(model)
        if ctx:
            caps.max_context_length = ctx
        else:
            # No model specified — return the platform default (128k).
            caps.max_context_length = self._DEFAULT_MAX_CONTEXT

        return caps

    def _resolve_context(self, model: Optional[str]) -> Optional[int]:
        """Resolve the real context length for a model name.

        Priority:
        1. UMRS model catalog (via GET /umrs/models/{id})
        2. Known context patterns from model_context
        3. None (falls back to provider default of 4096)
        """
        import json as _json
        import urllib.error
        import urllib.request

        # 1. Try UMRS model catalog
        if model:
            try:
                # URL-encode model name properly (no quotes)
                import urllib.parse as _urlparse

                encoded = _urlparse.quote(model, safe="")
                url = f"{self._gateway_url}{self._api_prefix}/models/{encoded}"
                req = urllib.request.Request(url, headers=self._headers(), method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = _json.loads(resp.read().decode())
                caps_data = data.get("capabilities", {})
                mp = caps_data.get("max_position_embeddings")
                if mp and isinstance(mp, int):
                    return mp
                profile = data.get("serving_profile", {})
                mml = profile.get("max_model_len")
                if mml and isinstance(mml, int) and mml != 4096:
                    return mml
            except Exception:
                pass  # Fall through to known patterns

        # 2. Known context patterns
        try:
            from r4.model_context import known_context_length

            ctx = known_context_length(model) if model else None
            if ctx:
                return ctx
        except Exception:
            pass

        return None
