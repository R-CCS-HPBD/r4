"""Pins for the sub-agent spawn/result event contract (TUI team visibility, 2026-09-27).

``spawn_subagent`` emits AGENT_SPAWN/AGENT_RESULT with the delegation
contract in ``data``: agent_id, the verbatim task, the verbatim system
prompt, status, duration. UI surfaces render from this, never a paraphrase.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from r4.companion import agent as agent_module
from r4.companion.agent import CompanionEventType, ResearchCompanion


def _main_companion() -> ResearchCompanion:
    main = object.__new__(ResearchCompanion)
    main.client = SimpleNamespace()
    main.model = "m"
    main.temperature = 0.4
    main.max_tool_rounds = 10
    main._usage_callback = None
    main._pre_llm_call = None
    main._listeners = []
    main._emit_lock = threading.Lock()
    return main


def _collect(companion: ResearchCompanion):
    events = []
    companion.on_event(events.append) if hasattr(companion, "on_event") else None
    return events


def _patch_sub(monkeypatch, *, result: str = "done", boom: bool = False):
    captured: dict[str, object] = {}

    class FakeSub:
        def __init__(self, client, **kwargs):
            captured["client"] = client
            captured["init_kwargs"] = kwargs

        def send(self, task, *, history=None, extra_system="", tools=None):
            captured["task"] = task
            captured["extra_system"] = extra_system
            if boom:
                raise RuntimeError("model down")
            return result

    monkeypatch.setattr(agent_module, "ResearchCompanion", FakeSub)
    return captured


def _assert_spawn_event(spawn, *, task: str, prompt: str) -> None:
    assert spawn.data["task"] == task
    assert spawn.data["system_prompt"] == prompt
    assert spawn.data["agent_id"]
    assert spawn.data["parent_agent_id"] is None  # honest absence


def test_spawn_event_carries_task_and_verbatim_prompt(monkeypatch):
    main = _main_companion()
    events: list = []
    main._listeners.append(events.append)
    captured = _patch_sub(monkeypatch)

    out = main.spawn_subagent("check the ledger", system="You are careful.")

    spawn = next(e for e in events if e.event_type is CompanionEventType.AGENT_SPAWN)
    _assert_spawn_event(spawn, task="check the ledger", prompt="You are careful.")
    # The sub-agent really ran under that same prompt.
    assert (
        out == "done"
        and captured["extra_system"] == "You are careful."
        and captured["init_kwargs"]["call_type"] == "subagent"
    )


def test_spawn_default_prompt_is_the_documented_default(monkeypatch):
    main = _main_companion()
    events: list = []
    main._listeners.append(events.append)
    _patch_sub(monkeypatch)

    main.spawn_subagent("x")

    spawn = next(e for e in events if e.event_type is CompanionEventType.AGENT_SPAWN)
    assert spawn.data["system_prompt"] == "You are a sub-agent. Be concise. Return only results."


def _assert_completed_result(result, spawn, *, preview: str) -> None:
    assert result.data["agent_id"] == spawn.data["agent_id"]
    assert result.data["status"] == "completed"
    assert isinstance(result.data["duration_ms"], int)
    assert result.data["duration_ms"] >= 0
    assert result.data["result_preview"] == preview


def test_result_event_status_duration_and_matching_id(monkeypatch):
    main = _main_companion()
    events: list = []
    main._listeners.append(events.append)
    _patch_sub(monkeypatch, result="r" * 800)

    main.spawn_subagent("x")

    spawn = next(e for e in events if e.event_type is CompanionEventType.AGENT_SPAWN)
    result = next(e for e in events if e.event_type is CompanionEventType.AGENT_RESULT)
    _assert_completed_result(result, spawn, preview="r" * 800)


def test_result_preview_capped(monkeypatch):
    main = _main_companion()
    events: list = []
    main._listeners.append(events.append)
    _patch_sub(monkeypatch, result="z" * 3000)

    main.spawn_subagent("x")

    result = next(e for e in events if e.event_type is CompanionEventType.AGENT_RESULT)
    assert result.data["result_preview"] == "z" * 1200


def test_error_path_emits_error_result_and_reraises(monkeypatch):
    main = _main_companion()
    events: list = []
    main._listeners.append(events.append)
    _patch_sub(monkeypatch, boom=True)

    with pytest.raises(RuntimeError, match="model down"):
        main.spawn_subagent("x")

    result = next(e for e in events if e.event_type is CompanionEventType.AGENT_RESULT)
    assert result.data["status"] == "error"
    assert result.data["error"] == "model down"
    assert isinstance(result.data["duration_ms"], int)
