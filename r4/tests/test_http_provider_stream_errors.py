"""Streamed chat errors keep the provider's reason (companion sweep 2026-10-21).

Driven through the real JSONL bridge against an OpenAI-compatible source:

- the usage chunk ``stream_options.include_usage`` asks for has
  ``"choices": []``; the text-only stream indexed it and the turn failed
  with "list index out of range" (the answer-mode round after a tool loop);
- a streamed error response's body was never read, so a 400 "maximum
  context length is 8192 tokens" surfaced as a bare "API error: 400", and
  the OpenAI error shape ``{"error": {"message": ...}}`` was not parsed at
  all;
- 401/403/429 dropped the body entirely, and a public provider's 401 told
  the user to sign in to the gateway (/connect), which cannot fix a
  provider API key.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from r4._http import PROVIDER_AUTH_HINT, HTTPCore
from r4.errors import APIError, AuthenticationError, RateLimitError

_URL = "https://provider.example/v1/chat/completions"


def _sse(*payloads: object) -> bytes:
    lines = [f"data: {json.dumps(p) if not isinstance(p, str) else p}\n\n" for p in payloads]
    return "".join(lines).encode()


def _core(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> HTTPCore:
    http = HTTPCore(SimpleNamespace(api_key="t", gateway_url="https://gw.example"))

    @contextmanager
    def _client(_timeout=None):
        transport = httpx.MockTransport(lambda _req: response)
        with httpx.Client(transport=transport) as client:
            yield client

    monkeypatch.setattr(http, "_client", _client)
    return http


def _content(http: HTTPCore) -> list[str]:
    return list(http.iter_sse_content(_URL, json_body={}, provider_request=True))


def test_text_stream_skips_the_usage_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _sse(
        {"choices": [{"index": 0, "delta": {"content": "hel"}}]},
        {"choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}},
        "[DONE]",
    )
    http = _core(monkeypatch, httpx.Response(200, content=body))
    assert _content(http) == ["hel", "lo"]


def test_text_stream_skips_null_choices_and_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _sse(
        {"choices": None},
        {"choices": [{"index": 0, "delta": None}]},
        {"choices": [{"index": 0, "delta": {"content": "ok"}}]},
        "[DONE]",
    )
    http = _core(monkeypatch, httpx.Response(200, content=body))
    assert _content(http) == ["ok"]


def test_streamed_400_keeps_the_providers_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    error = {"error": {"message": "maximum context length is 8192 tokens", "type": "invalid"}}
    http = _core(monkeypatch, httpx.Response(400, json=error))
    with pytest.raises(APIError) as caught:
        _content(http)
    assert "maximum context length is 8192 tokens" in str(caught.value)
    assert "maximum context length" in (caught.value.details["response_body"] or "")


def test_provider_401_names_the_api_key_not_the_gateway_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = _core(monkeypatch, httpx.Response(401, json={"error": {"message": "Invalid API key"}}))
    with pytest.raises(AuthenticationError) as caught:
        _content(http)
    assert str(caught.value) == "Authentication failed: Invalid API key"
    assert caught.value.tui_hint == PROVIDER_AUTH_HINT
    assert caught.value.hint == PROVIDER_AUTH_HINT


def test_streamed_429_keeps_the_reason_and_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(
        429, json={"error": {"message": "Rate limit reached"}}, headers={"Retry-After": "7"}
    )
    http = _core(monkeypatch, response)
    with pytest.raises(RateLimitError) as caught:
        _content(http)
    assert str(caught.value) == "Rate limit exceeded: Rate limit reached"
    assert caught.value.retry_after == 7


def test_gateway_401_keeps_the_sign_in_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _core(monkeypatch, httpx.Response(401, json={"detail": "token expired"}))
    with pytest.raises(AuthenticationError) as caught:
        list(http.iter_sse_content(_URL, json_body={}))
    assert str(caught.value) == "Authentication failed: token expired"
    assert caught.value.tui_hint != PROVIDER_AUTH_HINT
