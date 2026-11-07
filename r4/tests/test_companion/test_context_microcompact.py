"""G11 (2026-10-29): microcompact — bound stale oversized history bodies
below the LLM-compaction layer; only surviving pressure earns a summary."""

from __future__ import annotations

import pytest

from r4.companion.context import ContextManager
from r4.companion.session_turns import compact_turn_history


class _Agent:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    class _EventType:
        CONTEXT_COMPACT = "context_compact"

    def _emit(self, event_type, message, data=None):
        self.events.append((str(event_type), message, data or {}))


def _history(oversized_chars: int, turns: int = 10) -> list[dict]:
    messages = []
    for index in range(turns):
        body = f"turn {index} " + "b" * (oversized_chars if index == 2 else 40)
        messages.append({"role": "user" if index % 2 == 0 else "assistant", "content": body})
    return messages


def _manager(monkeypatch: pytest.MonkeyPatch) -> ContextManager:
    monkeypatch.delenv("R4_COMPANION_MICROCOMPACT", raising=False)
    monkeypatch.delenv("R4_COMPANION_MICROCOMPACT_CHARS", raising=False)
    return ContextManager(max_context_tokens=1_000)


def _flagged(messages: list[dict]) -> list[dict]:
    return [m for m in messages if "microcompact" in str(m.get("content"))]


def test_bounds_only_stale_oversized_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    messages = _history(9_000)
    bounded, removed = manager.microcompact(messages)
    assert removed > 7_000
    flagged = _flagged(bounded)
    assert len(flagged) == 1
    assert len(flagged[0]["content"]) < 4_000 // 3 + 200
    assert bounded[-1] == messages[-1]  # recent turns untouched
    assert bounded[3] == messages[3]  # small stale messages untouched


def test_microcompact_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    bounded, _ = manager.microcompact(_history(9_000))
    again, removed_again = manager.microcompact(bounded)
    assert again == bounded
    assert removed_again == 0


def test_env_disable_restores_plain_compaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R4_COMPANION_MICROCOMPACT", "0")
    manager = ContextManager(max_context_tokens=1_000)
    assert manager.microcompact(_history(9_000))[1] == 0


class _FakeSummaryResponse:
    def __init__(self) -> None:
        self.content = "a summary"
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        self.usage = type("U", (), usage)()
        self.model = "test-model"


def test_microcompact_skips_the_llm_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("R4_COMPANION_MICROCOMPACT", raising=False)
    manager = ContextManager(max_context_tokens=8_000)
    manager.user_hooks = None

    def request(messages, **_kwargs):
        raise AssertionError("LLM summary must not run when microcompact suffices")

    agent = _Agent()
    agent.compaction_request = request  # would be used by the full path only
    history, error = compact_turn_history(
        context_manager=manager,
        agent=agent,
        user_message="next question",
        extra_system="",
        history=_history(20_000),
        active_tools=[],
        summary_system="",
    )
    assert error is None
    assert any("microcompact" in content.get("content", "") for content in history)
    assert agent.events, "the turn announces the bound (never silently shrinks)"


def test_surviving_pressure_still_summarizes(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    turns = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 3_000}
        for index in range(30)
    ]

    def request(messages, **_kwargs):
        return _FakeSummaryResponse()

    agent = _Agent()
    agent.compaction_request = request
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
    assert any("summary" in str(m.get("content", "")).lower() for m in history)


def test_pre_compact_hook_fires_on_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    from r4.companion.user_hooks import UserHooks

    fired: list[dict] = []

    class _Hooks(UserHooks):
        def any(self, event: str) -> bool:
            return event == "PreCompact"

        def run(self, event: str, payload: dict):
            fired.append(payload)
            from r4.companion.user_hooks import HookDecision

            return HookDecision()

    manager = _manager(monkeypatch)
    manager.user_hooks = _Hooks()
    turns = [{"role": "user", "content": f"turn {i} body"} for i in range(20)]
    manager.compact(turns, keep_recent=6)
    # No client wired → standalone summary unavailable; hooks only fire with
    # a request path, so drive compact_turn with an explicit request.
    fired.clear()
    manager.compact_turn(
        turns, request=lambda messages, **_kwargs: _FakeSummaryResponse(), keep_recent=6
    )
    assert fired and fired[0]["older_message_count"] == 14
    assert "older_messages_text" in fired[0]
