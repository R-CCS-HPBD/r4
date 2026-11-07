"""
R4 Gateway HTTP client for benchmarks.

Calls the gateway's OpenAI-compatible /gw/llm/v1/chat/completions endpoint.
Loads auth from r4 token store.
"""

from __future__ import annotations

import json
import os
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class ChatChoice:
    index: int
    message: ChatMessage
    finish_reason: Optional[str] = None


@dataclass
class ChatUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatResponse:
    """OpenAI-compatible chat completion response."""

    choices: list[ChatChoice] = field(default_factory=list)
    model: str = ""
    usage: Optional[ChatUsage] = None
    raw_json: dict = field(default_factory=dict)

    @property
    def content(self) -> str:
        if self.choices:
            return self.choices[0].message.content
        return ""


@dataclass
class CompletionTiming:
    """Timing metrics for a single completion."""

    time_to_first_token_ms: float = 0.0
    total_time_ms: float = 0.0
    output_tokens: int = 0
    tokens_per_second: float = 0.0


@dataclass
class StreamTiming:
    """Timing metrics for a streaming completion."""

    time_to_first_token_ms: float = 0.0
    total_time_ms: float = 0.0
    output_tokens: int = 0
    prompt_tokens: int = 0
    tokens_per_second: float = 0.0
    time_per_output_token_ms: float = 0.0
    decode_time_ms: float = 0.0
    content: str = ""


def _build_chat_body(
    prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    system_prompt: Optional[str],
    *,
    stream: bool,
    tools: Optional[list[dict[str, Any]]],
    tool_choice: Optional[str | dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the OpenAI-compatible request body for either mode."""
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if stream:
        body["stream_options"] = {"include_usage": True}
    if tools is not None:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    return body


def _gateway_error(exc: Exception, start: float) -> GatewayError:
    """Translate transport/parse failures into a GatewayError."""
    elapsed = (time.perf_counter() - start) * 1000
    if isinstance(exc, HTTPError):
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8")[:1000]
        except Exception:
            pass
        return GatewayError(
            f"HTTP {exc.code}: {exc.reason}",
            status_code=exc.code,
            response_body=error_body,
            elapsed_ms=elapsed,
        )
    if isinstance(exc, URLError):
        return GatewayError(f"Connection error: {exc.reason}", elapsed_ms=elapsed)
    if isinstance(exc, GatewayError):
        return exc
    return GatewayError(f"Unexpected error: {exc}", elapsed_ms=elapsed)


def _chunk_content_pieces(chunk: dict[str, Any]) -> list[str]:
    """Extract non-empty delta content strings from one SSE chunk."""
    pieces = []
    for choice in chunk.get("choices", []):
        piece = (choice.get("delta") or {}).get("content") or ""
        if piece:
            pieces.append(piece)
    return pieces


def _iter_sse_chunks(resp: Any) -> Iterator[dict[str, Any]]:
    """Yield parsed JSON chunks from an SSE response body."""
    for raw_line in resp:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


def _consume_sse_stream(resp: Any, start: float) -> tuple[list[str], dict[str, Any], float]:
    """Read SSE chunks; return (content_parts, usage, ttft_ms)."""
    first_token_at: float | None = None
    content_parts: list[str] = []
    usage: dict[str, Any] = {}

    for chunk in _iter_sse_chunks(resp):
        if chunk.get("usage"):
            usage = chunk["usage"]
        pieces = _chunk_content_pieces(chunk)
        if pieces and first_token_at is None:
            first_token_at = time.perf_counter()
        content_parts.extend(pieces)

    total_time_ms = (time.perf_counter() - start) * 1000
    ttft_ms = (first_token_at - start) * 1000 if first_token_at is not None else total_time_ms
    return content_parts, usage, ttft_ms


def _stream_timing(
    content_parts: list[str],
    usage: dict[str, Any],
    ttft_ms: float,
    total_time_ms: float,
) -> StreamTiming:
    """Compute throughput metrics for a finished stream."""
    output_tokens = int(usage.get("completion_tokens") or 0)
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    decode_time_ms = max(total_time_ms - ttft_ms, 0.0)
    if output_tokens > 0 and decode_time_ms > 0:
        tps = output_tokens / (decode_time_ms / 1000)
        tpot_ms = decode_time_ms / output_tokens
    elif output_tokens > 0 and total_time_ms > 0:
        tps = output_tokens / (total_time_ms / 1000)
        tpot_ms = total_time_ms / output_tokens
    else:
        tps = 0.0
        tpot_ms = 0.0
    return StreamTiming(
        time_to_first_token_ms=ttft_ms,
        total_time_ms=total_time_ms,
        output_tokens=output_tokens,
        prompt_tokens=prompt_tokens,
        tokens_per_second=tps,
        time_per_output_token_ms=tpot_ms,
        decode_time_ms=decode_time_ms,
        content="".join(content_parts),
    )


def _parse_completion_response(raw: dict[str, Any], model: str) -> tuple[ChatResponse, ChatUsage]:
    """Map a non-streaming JSON payload to ``(ChatResponse, ChatUsage)``."""
    choices = []
    for c in raw.get("choices", []):
        msg = c.get("message", {})
        choices.append(
            ChatChoice(
                index=c.get("index", 0),
                message=ChatMessage(
                    role=msg.get("role", "assistant"),
                    content=msg.get("content") or "",
                ),
                finish_reason=c.get("finish_reason"),
            )
        )

    usage_raw = raw.get("usage", {})
    usage = ChatUsage(
        prompt_tokens=usage_raw.get("prompt_tokens", 0),
        completion_tokens=usage_raw.get("completion_tokens", 0),
        total_tokens=usage_raw.get("total_tokens", 0),
    )
    response = ChatResponse(
        choices=choices,
        model=raw.get("model", model),
        usage=usage,
        raw_json=raw,
    )
    return response, usage


class GatewayClient:
    """HTTP client for the R4 Platform Gateway.

    Calls POST /gw/llm/v1/chat/completions with Bearer token auth.
    """

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        api_key: Optional[str] = None,
        project_id: str = "_benchmark",
        timeout: int = 300,
        verify_ssl: bool = True,
    ):
        self._gateway_url = (
            gateway_url or os.environ.get("R4_GATEWAY_URL", "") or self._load_gateway_url()
        ).rstrip("/")
        self._api_key = api_key or self._load_api_key()
        self._project_id = project_id
        self._timeout = timeout
        self._verify_ssl = verify_ssl

        if not self._gateway_url:
            raise ValueError(
                "No gateway URL configured. Set R4_GATEWAY_URL env var, "
                "pass gateway_url parameter, or configure a profile via 'r4 config init'."
            )
        if not self._api_key:
            print(
                "[WARN] No API key found. Set R4_API_KEY env var or "
                "run 'r4 login' first. Requests may fail with 401."
            )

    @property
    def base_url(self) -> str:
        return self._gateway_url

    @property
    def chat_url(self) -> str:
        return f"{self._gateway_url}/gw/llm/v1/chat/completions"

    @staticmethod
    def _candidate_profiles() -> list[str]:
        """Active profile first, then env override, ai4s, default."""
        candidates: list[str] = []
        active = os.environ.get("R4_PROFILE") or os.environ.get("R4_ACTIVE_PROFILE")
        if active:
            candidates.append(str(active))
        try:
            from r4.config import get_config

            current = getattr(get_config(), "profile", None)
            if current:
                candidates.append(str(current))
        except Exception:
            pass
        candidates.extend(["ai4s", "default"])
        seen: list[str] = []
        for p in candidates:
            if p and p not in seen:
                seen.append(p)
        return seen

    @classmethod
    def _load_gateway_url(cls) -> Optional[str]:
        """Load gateway URL from r4 profile config store."""
        import os  # local: keep module import surface unchanged

        try:
            from r4.config import ProfileConfigStore

            store = ProfileConfigStore()
            for profile in cls._candidate_profiles():
                config = store.load(profile)
                if config and config.get("gateway_url") and config.get("api_key"):
                    return str(config["gateway_url"])
            config = store.load("default")
            if config and config.get("gateway_url"):
                return str(config["gateway_url"])
        except ImportError:
            pass
        except Exception:
            pass
        return None

    def _load_api_key(self) -> Optional[str]:
        """Load API key from environment or r4 config store."""
        # Priority: env var > profile config > token store
        env_key = os.environ.get("R4_API_KEY")
        if env_key:
            return env_key
        return self._profile_api_key() or self._token_store_key()

    def _profile_api_key(self) -> Optional[str]:
        """Try r4 profile config store — active profile first."""
        try:
            from r4.config import ProfileConfigStore

            store = ProfileConfigStore()
            for profile in self._candidate_profiles():
                config = store.load(profile)
                if config and config.get("api_key"):
                    return str(config["api_key"])
        except Exception:
            pass
        return None

    @staticmethod
    def _token_store_key() -> Optional[str]:
        """Try r4 token store."""
        try:
            from r4.auth import TokenStore

            token = TokenStore().load("default")
            if token and not token.is_expired:
                return token.access_token
        except Exception:
            pass
        return None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Project-ID": self._project_id,
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def chat(
        self,
        prompt: str,
        model: str = "rivault-gpt-oss-static",
        temperature: float = 0.0,
        max_tokens: int = 2048,
        system_prompt: Optional[str] = None,
    ) -> tuple[ChatResponse, CompletionTiming]:
        """Send a chat completion request and measure timing."""
        return self.chat_completion(
            prompt=prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
        )

    def _open_request(self, body: dict[str, Any]) -> Any:
        req = Request(
            self.chat_url,
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        if not self._verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return urlopen(req, timeout=self._timeout, context=ctx)
        return urlopen(req, timeout=self._timeout)

    def chat_stream(
        self,
        prompt: str,
        model: str = "rivault-gpt-oss-static",
        temperature: float = 0.0,
        max_tokens: int = 2048,
        system_prompt: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
    ) -> tuple[StreamTiming, dict[str, Any]]:
        """Send a streaming chat completion and measure decode throughput.

        Returns:
            Tuple of (StreamTiming, raw usage dict from the final chunk).
        """
        body = _build_chat_body(
            prompt,
            model,
            temperature,
            max_tokens,
            system_prompt,
            stream=True,
            tools=tools,
            tool_choice=tool_choice,
        )
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort

        start = time.perf_counter()
        try:
            resp = self._open_request(body)
            content_parts, usage, ttft_ms = _consume_sse_stream(resp, start)
            total_time_ms = (time.perf_counter() - start) * 1000
            return _stream_timing(content_parts, usage, ttft_ms, total_time_ms), usage
        except (HTTPError, URLError, Exception) as e:  # noqa: BLE001
            raise _gateway_error(e, start) from e

    def chat_completion(
        self,
        prompt: str,
        model: str = "rivault-gpt-oss-static",
        temperature: float = 0.0,
        max_tokens: int = 2048,
        system_prompt: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[str | dict[str, Any]] = None,
    ) -> tuple[ChatResponse, CompletionTiming]:
        """Non-streaming chat with optional tool calling."""
        body = _build_chat_body(
            prompt,
            model,
            temperature,
            max_tokens,
            system_prompt,
            stream=False,
            tools=tools,
            tool_choice=tool_choice,
        )

        start = time.perf_counter()
        try:
            resp = self._open_request(body)
            total_time = (time.perf_counter() - start) * 1000
            raw = json.loads(resp.read().decode("utf-8"))
            response, usage = _parse_completion_response(raw, model)

            output_tokens = usage.completion_tokens
            tps = output_tokens / (total_time / 1000) if total_time > 0 else 0
            timing = CompletionTiming(
                time_to_first_token_ms=total_time,
                total_time_ms=total_time,
                output_tokens=output_tokens,
                tokens_per_second=tps,
            )
            return response, timing
        except (HTTPError, URLError, Exception) as e:  # noqa: BLE001
            raise _gateway_error(e, start) from e


class GatewayError(Exception):
    """Error from gateway communication."""

    def __init__(
        self,
        message: str,
        status_code: int = 0,
        response_body: str = "",
        elapsed_ms: float = 0.0,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.elapsed_ms = elapsed_ms
