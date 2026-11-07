"""G17 (2026-10-29): subagent enrichment — per-child model override and
two-way messaging/resume on the accumulated context; G19b model fallback."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from r4.companion.agent import ResearchCompanion
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry


def _client_with(texts: list[str]) -> MagicMock:
    client = MagicMock()
    client._config.model = "test-model"
    client._request.side_effect = [
        MagicMock(
            content=text,
            model="test-model",
            usage=MagicMock(prompt_tokens=3, completion_tokens=1, total_tokens=4),
            raw_response={
                "choices": [
                    {"message": {"content": text, "role": "assistant"}, "finish_reason": "stop"}
                ]
            },
        )
        for text in texts
    ]
    return client


def _agent(client: MagicMock, **kwargs) -> ResearchCompanion:
    return ResearchCompanion(
        client,
        context_manager=ContextManager(client=client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        **kwargs,
    )


def test_model_override_reaches_the_child() -> None:
    agent = _agent(_client_with(["cheap summary"]))
    started = agent.spawn_subagent_async("summarise", model="cheaper-model")
    assert "Background agent" in started
    assert agent._async_pool.wait_idle()
    record = next(iter(agent._async_pool._records.values()))
    assert record.child is not None
    assert record.child.model == "cheaper-model"


def test_follow_up_resumes_with_accumulated_context() -> None:
    agent = _agent(_client_with(["first answer", "follow-up answer"]))
    agent.spawn_subagent_async("stage one")
    assert agent._async_pool.wait_idle()
    agent_id = next(iter(agent._async_pool._records))

    reply = agent.message_subagent(agent_id, "now stage two")
    assert "Follow-up sent" in reply
    assert agent._async_pool.wait_idle()
    follow_payload = agent.client._request.call_args_list[-1].args[0]
    texts = json.dumps(follow_payload["messages"])
    assert "stage one" in texts  # the first task rides the resumed history
    assert "first answer" in texts
    assert "now stage two" in texts
    notes = agent._async_pool.drain()
    assert any("follow-up answer" in note for note in notes)


def test_message_rejects_unknown_and_running_agents() -> None:
    agent = _agent(_client_with(["unused"]))
    assert "unknown" in json.loads(agent.message_subagent("nope123", "hi"))["error"]
    import threading

    gate = threading.Event()

    def held_client() -> MagicMock:
        client = MagicMock()
        client._config.model = "test-model"
        client._request.side_effect = lambda *a, **k: (
            gate.wait(2)
            or MagicMock(
                content="late",
                model="test-model",
                usage=MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                raw_response={
                    "choices": [{"message": {"content": "late"}, "finish_reason": "stop"}]
                },
            )
        )
        return client

    agent = _agent(held_client())
    agent.spawn_subagent_async("slow task")
    agent_id = next(iter(agent._async_pool._records))
    early = json.loads(agent.message_subagent(agent_id, "too soon"))
    assert "still running" in early["error"]
    gate.set()
    assert agent._async_pool.wait_idle()


def _failing_client(failures: int, then: str) -> MagicMock:
    client = MagicMock()
    client._config.model = "test-model"
    effects = [ConnectionError("502 upstream capacity exhausted")] * failures + [
        MagicMock(
            content=then,
            model="fallback-model",
            usage=MagicMock(prompt_tokens=3, completion_tokens=1, total_tokens=4),
            raw_response={
                "choices": [
                    {"message": {"content": then, "role": "assistant"}, "finish_reason": "stop"}
                ]
            },
        )
    ]
    client._request.side_effect = effects
    return client


def test_fallback_model_flips_once_per_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R4_COMPANION_FALLBACK_MODEL", "fallback-model")
    agent = _agent(_failing_client(3, "recovered reply"), model="failing-model")
    assert agent.send("hi", stream=False) == "recovered reply"
    assert agent.model == "fallback-model"
    # A second turn does not flip again (and must not error).
    agent.client._request.side_effect = [ConnectionError("502 again")] * 4
    with pytest.raises(ConnectionError):
        agent.send("hi again", stream=False)


def test_no_fallback_without_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("R4_COMPANION_FALLBACK_MODEL", raising=False)
    agent = _agent(_failing_client(3, "never reached"), model="failing-model")
    with pytest.raises(ConnectionError):
        agent.send("hi", stream=False)
    assert agent.model == "failing-model"


def test_no_flip_when_fallback_equals_current(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R4_COMPANION_FALLBACK_MODEL", "failing-model")
    agent = _agent(_failing_client(3, "unused"), model="failing-model")
    with pytest.raises(ConnectionError):
        agent.send("hi", stream=False)
