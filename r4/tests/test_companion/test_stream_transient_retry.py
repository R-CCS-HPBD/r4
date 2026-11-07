"""A streamed round refused with a transient status is retried
(stabilization sweep 2026-10-21, chat#10).

The streaming path opened the stream once and let an HTTP 500 escape, while
the TUI said the backend failed "(already retried as transient)". A 429/5xx
before anything streamed now falls back to the blocking round, which goes
through ``_request_with_transient_retry``. Timeouts and mid-stream drops
still surface at once.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from r4.chat_types import ChatResponse, Usage
from r4.companion.agent import ResearchCompanion
from r4.errors import APIError

_TOOLS = [{"type": "function", "function": {"name": "list_dir", "parameters": {}}}]


def _reply(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        model="m",
        usage=Usage(prompt_tokens=5, completion_tokens=2, total_tokens=7),
        raw_response={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


class _Client:
    def __init__(self, stream_error: Exception, partial: list[str] | None = None):
        self._config = MagicMock(model="m", endpoint_url="")
        self.stream_error = stream_error
        self.partial = partial or []
        self.blocking_calls = 0

    def _stream_request_collect(self, payload, acc, **_kw):
        def gen():
            yield from self.partial
            raise self.stream_error

        return gen()

    def _request(self, payload: dict[str, Any], **_kw: Any) -> ChatResponse:
        self.blocking_calls += 1
        return _reply("recovered answer")


def _round(client: _Client) -> dict[str, Any]:
    agent = ResearchCompanion(client, model="m", call_type="main")
    _streamed, response = agent._agentic_round_response(
        [{"role": "user", "content": "q"}], _TOOLS, stream=True
    )
    return response


def test_streamed_500_before_any_chunk_retries_blocking() -> None:
    client = _Client(APIError("API error: 500: upstream exploded", status_code=500))
    response = _round(client)
    assert client.blocking_calls == 1
    assert response["choices"][0]["message"]["content"] == "recovered answer"


def test_streamed_read_timeout_is_not_resent() -> None:
    client = _Client(httpx.ReadTimeout("timed out"))
    with pytest.raises(httpx.ReadTimeout):
        _round(client)
    assert client.blocking_calls == 0


def test_error_after_streamed_text_is_not_resent() -> None:
    client = _Client(APIError("API error: 500", status_code=500), partial=["half an "])
    with pytest.raises(APIError):
        _round(client)
    assert client.blocking_calls == 0
