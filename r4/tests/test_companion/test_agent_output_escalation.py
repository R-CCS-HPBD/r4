"""G19a (2026-10-28): one free output-token escalation per turn.

A round that dies at ``finish_reason: "length"`` with nothing to show first
re-runs with a raised output cap — no continuation nudge spent — and only
falls back to the output-budget ladder when the higher cap still cuts.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from r4.companion.agent import ResearchCompanion
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry


def _reply(text: str, finish: str) -> MagicMock:
    return MagicMock(
        content=text,
        model="test-model",
        usage=MagicMock(prompt_tokens=3, completion_tokens=1, total_tokens=4),
        raw_response={
            "choices": [
                {"message": {"content": text, "role": "assistant"}, "finish_reason": finish}
            ]
        },
    )


def _agent(client: MagicMock) -> ResearchCompanion:
    agent = ResearchCompanion(
        client,
        context_manager=ContextManager(client=client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )
    agent.model_output_limit = 4_096
    return agent


def _max_tokens_seen(client: MagicMock) -> list[int | None]:
    return [call.args[0].get("max_tokens") for call in client._request.call_args_list]


def test_length_escalates_once_before_spending_a_nudge(monkeypatch) -> None:
    monkeypatch.delenv("R4_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("R4_COMPANION_OUTPUT_ESCALATION", raising=False)
    client = MagicMock()
    client._config.model = "test-model"
    client._request.side_effect = [
        _reply("", "length"),
        _reply("", "length"),
        _reply("done", "stop"),
    ]
    agent = _agent(client)
    assert agent.send("hi", stream=False) == "done"
    assert _max_tokens_seen(client) == [4_096, 12_288, 12_288]
    assert agent._length_budget_nudges == 1


def test_escalation_resets_each_turn(monkeypatch) -> None:
    monkeypatch.delenv("R4_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("R4_COMPANION_OUTPUT_ESCALATION", raising=False)
    client = MagicMock()
    client._config.model = "test-model"
    client._request.side_effect = [
        _reply("", "length"),
        _reply("first", "stop"),
        _reply("", "length"),
        _reply("second", "stop"),
    ]
    agent = _agent(client)
    assert agent.send("hi", stream=False) == "first"
    assert agent.send("again", stream=False) == "second"
    assert _max_tokens_seen(client) == [4_096, 12_288, 4_096, 12_288]
    assert agent._length_budget_nudges == 0


def test_user_output_pin_stays_authoritative(monkeypatch) -> None:
    monkeypatch.setenv("R4_MAX_OUTPUT_TOKENS", "2_048".replace("_", ""))
    client = MagicMock()
    client._config.model = "test-model"
    client._request.side_effect = [
        _reply("", "length"),
        _reply("", "length"),
        _reply("done", "stop"),
    ]
    agent = _agent(client)
    assert agent.send("hi", stream=False) == "done"
    assert _max_tokens_seen(client) == [2_048, 2_048, 2_048]
    assert agent._length_budget_nudges == 2


def test_escalation_env_off_restores_the_old_ladder(monkeypatch) -> None:
    monkeypatch.delenv("R4_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setenv("R4_COMPANION_OUTPUT_ESCALATION", "0")
    client = MagicMock()
    client._config.model = "test-model"
    client._request.side_effect = [
        _reply("", "length"),
        _reply("", "length"),
        _reply("done", "stop"),
    ]
    agent = _agent(client)
    assert agent.send("hi", stream=False) == "done"
    assert _max_tokens_seen(client) == [4_096, 4_096, 4_096]
    assert agent._length_budget_nudges == 2
