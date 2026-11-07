"""ADR-099 D-5/D-6 (RS-1 lane Q): a quota refusal ends the turn with one line.

The inference proxy refuses a call over the user's daily token cap with a
typed 429 whose message r4 reads ("Rate limit exceeded: daily token limit
reached for user alice ..."). Before: r4 treated it as a transient 429,
retried it three times with backoff, and the turn ended with the raw
provider text. Now: the daily (and session) cap is never retried, and the
turn's error is the readable notice. ``quota.rpm_exceeded`` stays transient
and honours ``Retry-After``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from r4._http import raise_for_status
from r4.chat_types import ChatResponse, Usage
from r4.companion.agent import ResearchCompanion, _is_transient_provider_error
from r4.companion.quota_notice import QuotaRefusedError, quota_notice
from r4.errors import RateLimitError

TOKENS_BODY = {
    "error": "quota.tokens_per_day_exceeded",
    "message": "daily token limit reached for user alice at rccs: 2000310 of 2000000 "
    "tokens; resets 2026-10-24T00:00:00Z",
    "scope": "user",
    "subject": "alice",
    "limit": 2000000,
    "used": 2000310,
    "window": "day",
    "resets_at": "2026-10-24T00:00:00Z",
}
TOKENS_LINE = (
    "Daily token limit reached (2,000,000 tokens/day for user alice); resets "
    "2026-10-24 00:00 UTC. A platform admin can raise it."
)
RPM_TEXT = (
    "Rate limit exceeded: request rate limit reached for user alice at rccs: "
    "60 of 60 requests per minute; retry in 12s"
)


def _proxy_error(body: dict[str, Any], retry_after: str = "31512") -> Exception:
    """What r4's HTTP layer raises for the proxy's 429."""
    response = httpx.Response(
        429,
        json=body,
        headers={"x-platform-error-code": body["error"], "Retry-After": retry_after},
    )
    with pytest.raises(Exception) as exc:
        raise_for_status(response)
    return exc.value


class TestQuotaNotice:
    def test_daily_tokens_from_the_proxy_429(self):
        assert quota_notice(str(_proxy_error(TOKENS_BODY))) == TOKENS_LINE

    def test_project_scope(self):
        text = (
            "daily token limit reached for project example-project-b at rccs: 10 of 10 tokens; "
            "resets 2026-10-23T15:00:00Z"
        )
        assert quota_notice(text) == (
            "Daily token limit reached (10 tokens/day for project example-project-b); resets "
            "2026-10-23 15:00 UTC. A platform admin can raise it."
        )

    def test_concurrent_sessions_from_the_start_refusal(self):
        text = (
            "Rate limit exceeded: concurrent session limit reached for user alice at rccs: "
            "12 of 12 live sessions; stop one (r4 session stop <id>) or ask a platform "
            "admin to raise the limit"
        )
        notice = quota_notice(text)
        assert notice.startswith("Concurrent session limit reached (12 live remote sessions")
        assert "r4 session stop" in notice

    def test_rpm(self):
        assert quota_notice(RPM_TEXT).startswith(
            "Request rate limit reached (60 requests/minute for user alice); try again in 12s"
        )

    def test_bare_code(self):
        assert quota_notice("x-platform-error-code: quota.tokens_per_day_exceeded").startswith(
            "Daily token limit reached."
        )

    @pytest.mark.parametrize(
        "text", ["", "Rate limit exceeded: slow down", "API error: 500", "quota.unavailable"]
    )
    def test_other_errors_answer_none(self, text):
        assert quota_notice(text) is None


def _reply(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        model="m",
        usage=Usage(prompt_tokens=5, completion_tokens=2, total_tokens=7),
        raw_response={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


class _Client:
    """Every request is refused with ``error``; counts what was sent."""

    def __init__(self, error: Exception, *, then: str | None = None):
        self._config = MagicMock(model="m", endpoint_url="")
        self.error = error
        self.then = then
        self.blocking_calls = 0
        self.stream_calls = 0

    def _request(self, payload: dict[str, Any], **_kw: Any) -> ChatResponse:
        self.blocking_calls += 1
        if self.then is not None and self.blocking_calls > 1:
            return _reply(self.then)
        raise self.error

    def _stream_request(self, payload: dict[str, Any], **_kw: Any):
        self.stream_calls += 1
        raise self.error
        yield ""  # pragma: no cover — a generator that refuses at once


def _agent(client: _Client, monkeypatch) -> ResearchCompanion:
    monkeypatch.setenv("R4_COMPANION_TRANSIENT_RETRIES", "3")
    agent = ResearchCompanion(client, model="m", call_type="main")
    monkeypatch.setattr(agent, "_cancelable_sleep", lambda _s: None)
    return agent


class TestTurnEndsWithTheNotice:
    def test_daily_cap_is_not_retried_and_the_error_is_the_notice(self, monkeypatch):
        client = _Client(_proxy_error(TOKENS_BODY))
        agent = _agent(client, monkeypatch)
        with pytest.raises(QuotaRefusedError) as exc:
            agent._request_with_transient_retry({"model": "m", "messages": []})
        assert client.blocking_calls == 1
        assert str(exc.value) == TOKENS_LINE

    def test_hard_markers_name_the_quota_codes(self):
        assert _is_transient_provider_error(RateLimitError(str(TOKENS_BODY))) is None
        assert _is_transient_provider_error(RateLimitError(TOKENS_BODY["message"])) is None

    def test_streamed_turn_without_tools_ends_with_the_notice(self, monkeypatch):
        client = _Client(_proxy_error(TOKENS_BODY))
        agent = _agent(client, monkeypatch)
        with pytest.raises(QuotaRefusedError) as exc:
            agent.send("hello", stream=True)
        assert str(exc.value) == TOKENS_LINE and client.stream_calls == 1

    def test_rpm_is_retried_after_retry_after(self, monkeypatch):
        client = _Client(RateLimitError(RPM_TEXT, retry_after=12), then="answer")
        agent = _agent(client, monkeypatch)
        delays: list[float] = []
        monkeypatch.setattr(agent, "_cancelable_sleep", delays.append)
        result = agent._request_with_transient_retry({"model": "m", "messages": []})
        assert result.content == "answer" and client.blocking_calls == 2
        assert delays and delays[0] >= 12 * 0.8

    def test_rpm_still_refused_after_the_retries_reads_the_notice(self, monkeypatch):
        client = _Client(RateLimitError(RPM_TEXT, retry_after=1))
        agent = _agent(client, monkeypatch)
        with pytest.raises(QuotaRefusedError) as exc:
            agent._request_with_transient_retry({"model": "m", "messages": []})
        assert client.blocking_calls == 3
        assert str(exc.value).startswith("Request rate limit reached")

    def test_other_errors_are_untouched(self, monkeypatch):
        client = _Client(RateLimitError("Rate limit exceeded: provider busy", retry_after=1))
        agent = _agent(client, monkeypatch)
        with pytest.raises(RateLimitError):
            agent._request_with_transient_retry({"model": "m", "messages": []})
        assert client.blocking_calls == 3


def test_the_contract_body_round_trips():
    # The proxy's body is JSON the r4 HTTP layer reads ``message`` from.
    assert json.loads(json.dumps(TOKENS_BODY))["message"] in str(_proxy_error(TOKENS_BODY))
