"""G12 + G20 (2026-10-29): pressure levels, the compaction circuit breaker,
and the reset-vs-compaction bench lane (R4_COMPANION_CONTEXT_RESET)."""

from __future__ import annotations

import pytest

from r4.companion.context import ContextManager
from r4.companion.session_turns import compact_turn_history


def _manager(**kwargs) -> ContextManager:
    return ContextManager(max_context_tokens=1_000, **kwargs)


def test_pressure_level_boundaries() -> None:
    manager = _manager()
    quiet = [{"role": "user", "content": "x" * 100}]
    level1 = [{"role": "user", "content": "x" * 2_500}]  # ~0.70 of 1000
    level2 = [{"role": "user", "content": "x" * 2_900}]  # ~0.83 (over 0.80)
    level3 = [{"role": "user", "content": "x" * 3_400}]  # ~0.97
    assert manager.pressure_level(quiet) == 0
    assert manager.pressure_level(level1) == 1
    assert manager.pressure_level(level2) == 2
    assert manager.pressure_level(level3) == 3


def test_breaker_stops_burning_calls_after_a_failure_streak() -> None:
    calls = 0

    def boom(_messages, **_kw):
        nonlocal calls
        calls += 1
        raise RuntimeError("summariser down")

    manager = _manager()
    history = [{"role": "user", "content": f"turn {i} body"} for i in range(20)]
    for _ in range(3):
        manager.compact_turn(history, request=boom, keep_recent=6)
    assert calls == 3
    # Fourth compaction never reaches the request — deterministic digest only.
    assert manager.compact_turn(history, request=boom, keep_recent=6)
    assert calls == 3


_SUMMARY_USAGE = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})


class _Resp:
    content = "a summary"
    usage = _SUMMARY_USAGE()
    model = "test-model"


def test_success_resets_the_streak_before_it_trips() -> None:
    calls = 0

    def flaky(_messages, **_kw):
        nonlocal calls
        calls += 1
        if calls % 2 == 1:
            raise RuntimeError("intermittent")
        return _Resp()

    manager = _manager()
    history = [{"role": "user", "content": f"turn {i} body"} for i in range(20)]
    for _ in range(4):
        manager.compact_turn(history, request=flaky, keep_recent=6)
    assert manager._compaction_failures == 0  # fail, succeed, fail, succeed


def test_env_changes_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R4_COMPANION_COMPACTION_BREAKER", "1")
    calls = 0

    def boom(_messages, **_kw):
        nonlocal calls
        calls += 1
        raise RuntimeError("down")

    manager = _manager()
    history = [{"role": "user", "content": f"turn {i} body"} for i in range(20)]
    manager.compact_turn(history, request=boom, keep_recent=6)
    manager.compact_turn(history, request=boom, keep_recent=6)
    assert calls == 1, "limit 1 trips after one failure; the retry never calls"


class _Agent:
    def __init__(self) -> None:
        self.events: list = []

    class _EventType:
        CONTEXT_COMPACT = "context_compact"

    def _emit(self, event_type, message, data=None):
        self.events.append((str(event_type), message))


def test_reset_lane_drops_without_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R4_COMPANION_CONTEXT_RESET", "1")

    def request(_messages, **_kw):
        raise AssertionError("reset lane must not call the summariser")

    agent = _Agent()
    agent.compaction_request = request
    turns = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 3_000} for i in range(30)
    ]
    manager = _manager()
    history, error = compact_turn_history(
        context_manager=manager,
        agent=agent,
        user_message="next",
        extra_system="",
        history=turns,
        active_tools=[],
        summary_system="",
    )
    assert error is None
    assert history[0]["content"].startswith("[context reset")
    assert len(history) <= 7
    assert agent.events, "the reset is announced"


def test_reset_lane_off_is_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("R4_COMPANION_CONTEXT_RESET", raising=False)
    manager = _manager()
    called = []

    def request(messages, **_kw):
        called.append(messages)
        return type("R", (), {"content": "s", "usage": _SUMMARY_USAGE(), "model": "m"})()

    agent = _Agent()
    agent.compaction_request = request
    turns = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 3_000} for i in range(30)
    ]
    history, _ = compact_turn_history(
        context_manager=manager,
        agent=agent,
        user_message="next",
        extra_system="",
        history=turns,
        active_tools=[],
        summary_system="",
    )
    assert called, "default path still summarizes"
    assert any("summary" in str(m.get("content", "")).lower() for m in history)
