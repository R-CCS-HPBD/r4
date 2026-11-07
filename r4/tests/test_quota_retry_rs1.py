"""RS-1 fix Q5 (ADR-099 D-5/D-6): quota refusals on the client.

- A request-rate refusal (``quota.rpm_exceeded``) waits its full
  Retry-After, at most 60 s (the 20 s backoff cap re-sent it early, into
  another refusal).
- A daily-token refusal of a streamed round ends the turn with its notice
  (the round was re-sent blocking: a second request the proxy refused too).
- The quota notice keeps the console's rate-limit hint (the bridge showed
  it beside a RateLimitError, and lost it once the error became a notice).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from r4.chat_types import ChatResponse, Usage
from r4.companion.agent import ResearchCompanion
from r4.companion.quota_notice import (
    RATE_LIMIT_HINT,
    QuotaRefusedError,
    raise_quota_notice,
    rpm_wait_seconds,
)
from r4.errors import RateLimitError

TOKENS_TEXT = (
    "Rate limit exceeded: daily token limit reached for user alice at rccs: 2000310 of "
    "2000000 tokens; resets 2026-10-24T00:00:00Z"
)


def _rpm(retry: int, retry_after: int | None) -> RateLimitError:
    return RateLimitError(
        "Rate limit exceeded: request rate limit reached for user alice at rccs: "
        f"60 of 60 requests per minute; retry in {retry}s",
        retry_after=retry_after,
    )


def _reply(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        model="m",
        usage=Usage(prompt_tokens=5, completion_tokens=2, total_tokens=7),
        raw_response={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


class _Client:
    """Streams and blocking requests refused with ``error``; counted."""

    def __init__(self, error: Exception, *, then: str | None = None) -> None:
        self._config = MagicMock(model="m", endpoint_url="")
        self.error = error
        self.then = then
        self.blocking_calls = 0
        self.stream_calls = 0

    def _request(self, payload: dict[str, Any], **_kw: Any) -> ChatResponse:
        self.blocking_calls += 1
        if self.then is not None:
            return _reply(self.then)
        raise self.error

    def _stream_request_collect(self, payload: dict[str, Any], _acc: Any, **_kw: Any):
        self.stream_calls += 1
        raise self.error
        yield ""  # pragma: no cover — a generator that refuses at once


def _agent(client: _Client, monkeypatch: pytest.MonkeyPatch, delays: list[float]) -> Any:
    monkeypatch.setenv("R4_COMPANION_TRANSIENT_RETRIES", "3")
    agent = ResearchCompanion(client, model="m", call_type="main")
    monkeypatch.setattr(agent, "_cancelable_sleep", delays.append)
    return agent


_TOOLS = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}]


# ── the full Retry-After ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("error", "expected"),
    [(_rpm(45, 45), 45.0), (_rpm(45, 300), 60.0), (_rpm(30, None), 30.0)],
)
def test_rpm_waits_the_full_retry_after_bounded(
    monkeypatch: pytest.MonkeyPatch, error: RateLimitError, expected: float
) -> None:
    client = _Client(error)
    delays: list[float] = []
    agent = _agent(client, monkeypatch, delays)
    calls = {"n": 0}

    def _request(payload: dict[str, Any], **_kw: Any) -> ChatResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise error
        return _reply("answer")

    client._request = _request  # type: ignore[method-assign]
    assert agent._request_with_transient_retry({"model": "m", "messages": []}).content == "answer"
    assert delays == [expected]


def test_rpm_wait_answers_none_for_other_errors() -> None:
    assert rpm_wait_seconds(RateLimitError("Rate limit exceeded: provider busy", 5)) is None
    assert rpm_wait_seconds(RateLimitError(TOKENS_TEXT, 31512)) is None


# ── a streamed daily-token refusal costs one request ───────────────────────


def test_a_streamed_daily_token_refusal_is_not_resent_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(RateLimitError(TOKENS_TEXT, retry_after=31512))
    agent = _agent(client, monkeypatch, [])
    with pytest.raises(QuotaRefusedError) as refused:
        agent._agentic_round_response([{"role": "user", "content": "hi"}], _TOOLS, True)
    assert (client.stream_calls, client.blocking_calls) == (1, 0)
    assert str(refused.value).startswith("Daily token limit reached")


def test_a_streamed_rpm_refusal_waits_before_the_blocking_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(_rpm(40, 40), then="answer")
    delays: list[float] = []
    agent = _agent(client, monkeypatch, delays)
    _streamed, response = agent._agentic_round_response(
        [{"role": "user", "content": "hi"}], _TOOLS, True
    )
    assert delays == [40.0] and client.blocking_calls == 1
    assert response["choices"][0]["message"]["content"] == "answer"


# ── the console's hint survives the conversion ─────────────────────────────


def test_the_quota_notice_keeps_the_rate_limit_hint() -> None:
    from r4.companion.ui.bridge_server import _bridge_error_response

    with pytest.raises(QuotaRefusedError) as refused:
        raise_quota_notice(RateLimitError(TOKENS_TEXT, retry_after=31512))
    assert refused.value.hint == RATE_LIMIT_HINT
    assert refused.value.details["retry_after"] == 31512
    error = _bridge_error_response(1, refused.value)["error"]
    assert error["hint"] == RATE_LIMIT_HINT
    assert error["message"].startswith("Daily token limit reached")
