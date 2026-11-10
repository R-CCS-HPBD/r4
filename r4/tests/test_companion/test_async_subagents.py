"""G9 (2026-10-29): async background subagents + round-boundary delivery.

The pool runs children on daemon threads under a concurrency cap; completed
results become ``<subagent-notification>`` user blocks drained into the
parent loop at the next round boundary.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from r4.companion.agent import CompanionEventType, ResearchCompanion
from r4.companion.async_agents import AsyncAgentPool
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import ALWAYS_ON_TOOL_NAMES, SkillRegistry


def _pool() -> AsyncAgentPool:
    return AsyncAgentPool(max_running=2)


def test_pool_cycle_and_delivered_once() -> None:
    pool = _pool()
    record = pool.spawn("count primes", lambda _r: "found 4")
    assert not isinstance(record, dict)
    assert pool.wait_idle()
    notes = pool.drain()
    assert len(notes) == 1
    assert "<status>completed</status>" in notes[0]
    assert "found 4" in notes[0]
    assert record.agent_id in notes[0]
    assert pool.drain() == []  # delivered once, never twice


def test_pool_cap_and_failure_notification() -> None:
    pool = _pool()
    hold = pytest.importorskip("threading").Event()
    pool.spawn("one", lambda _r: hold.wait(2) or "one")
    pool.spawn("two", lambda _r: hold.wait(2) or "two")
    over = pool.spawn("three", lambda _r: "three")
    assert "error" in over and "cap 2" in over["error"]
    hold.set()
    boom = AsyncAgentPool(max_running=1)

    def raise_it(_r):
        raise RuntimeError("broken subtask")

    record = boom.spawn("bad task", raise_it)
    assert boom.wait_idle()
    notes = boom.drain()
    assert "<status>failed</status>" in notes[0]
    assert "broken subtask" in notes[0]
    statuses = {row["agent_id"]: row["status"] for row in boom.status()}
    assert statuses[record.agent_id] == "failed"


def _agent(responses: list) -> tuple[ResearchCompanion, MagicMock]:
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
        for text in responses
    ]
    agent = ResearchCompanion(
        client,
        context_manager=ContextManager(client=client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )
    return agent, client


def test_notification_reaches_the_next_turn(monkeypatch) -> None:
    agent, client = _agent(["child result body", "parent answer"])
    events: list = []
    agent.on_event(lambda event: events.append(event))
    started = agent.spawn_subagent_async("summarise the corpus")
    assert "Background agent" in started
    assert agent._async_pool.wait_idle()

    answer = agent.send("now the parent task", stream=False)
    assert answer == "parent answer"
    parent_payload = client._request.call_args_list[-1].args[0]
    last_user = parent_payload["messages"][-1]["content"]
    assert "<subagent-notification>" in last_user
    assert "child result body" in last_user
    assert any(
        event.event_type is CompanionEventType.AGENT_RESULT
        and event.data.get("status") == "completed"
        for event in events
    )


def test_spawn_and_status_are_always_on_tools() -> None:
    assert {"async_agent_spawn", "async_agent_status"} <= set(ALWAYS_ON_TOOL_NAMES)


def test_wired_beside_any_tool_set() -> None:
    agent, _ = _agent(["unused"])
    assert agent._with_async_agent_tools([]) == []  # bare chat stays tool-less
    wired = agent._with_async_agent_tools([{"name": "file_read"}])
    names = {tool["name"] for tool in wired}
    assert {"file_read", "async_agent_spawn", "async_agent_status"} <= names
    assert agent._with_async_agent_tools(wired) == wired  # idempotent
    spawn_tool = next(tool for tool in wired if tool["name"] == "async_agent_spawn")
    assert spawn_tool["concurrency_safe"] is True


def test_acceptance_hook_precedes_work_and_never_runs_over_cap() -> None:
    import threading

    pool = AsyncAgentPool(max_running=1)
    release = threading.Event()
    order: list[str] = []

    def runner(_record) -> str:
        order.append("work")
        release.wait(2)
        return "done"

    pool.spawn("first", runner, before_start=lambda _record: order.append("accepted"))
    pool.spawn(
        "rejected",
        runner,
        before_start=lambda _record: order.append("must-not-register"),
    )
    assert order == ["accepted", "work"]
    release.set()
    assert pool.wait_idle()


def test_acceptance_failure_does_not_start_or_register() -> None:
    pool = AsyncAgentPool(max_running=1)
    ran: list[str] = []

    def refuse(_record) -> None:
        raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        pool.spawn("task", lambda _record: ran.append("work") or "done", before_start=refuse)
    assert ran == []
    assert pool.status() == []
