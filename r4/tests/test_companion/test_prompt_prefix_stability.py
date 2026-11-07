"""Wave (80): the companion request prefix stays stable across turns.

vLLM automatic prefix caching reuses KV blocks only for a byte-identical
prefix. These tests drive a real ``CompanionSession`` against a recording
client and pin: one system message and one tools array for the session
(through a plan-mode toggle and a skill activation), each request starting
with the previous request's messages, the per-turn state riding the turn's
user message, mode limits enforced at execution, and a compaction request
that reuses the turn prefix.
"""

from __future__ import annotations

import json
from itertools import count
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from r4.companion.cancellation import STOPPED_MESSAGE
from r4.companion.session import CompanionSessionStore
from r4.companion.skills import Skill
from r4.companion.turn_context import TURN_CONTEXT_OPEN

_COMPACTION_MARK = "[compaction]"


def _usage() -> dict[str, int]:
    return {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


def _reply(message: dict[str, Any]) -> MagicMock:
    return MagicMock(
        content=message.get("content") or "",
        model="test-model",
        usage=MagicMock(**_usage()),
        raw_response={
            "usage": _usage(),
            "choices": [{"message": message, "finish_reason": "stop"}],
        },
    )


def _text(content: str) -> dict[str, Any]:
    return {"role": "assistant", "content": content}


def _tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    call = {"id": "call-1", "type": "function"}
    call["function"] = {"name": name, "arguments": json.dumps(arguments)}
    return {"role": "assistant", "content": "", "tool_calls": [call]}


class _Recorder:
    """``client._request`` stand-in: snapshots each payload, replies from a script."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.script: list[dict[str, Any]] = []
        self._answers = count(1)

    def __call__(self, payload: dict[str, Any], **_kwargs: Any) -> MagicMock:
        self.payloads.append(json.loads(json.dumps(payload, default=str)))
        if self.script:
            return _reply(self.script.pop(0))
        return _reply(_text(f"Answer {next(self._answers)}."))

    def turns(self) -> list[dict[str, Any]]:
        return [p for p in self.payloads if not _is_compaction(p)]


def _is_compaction(payload: dict[str, Any]) -> bool:
    return str(payload["messages"][-1].get("content", "")).startswith(_COMPACTION_MARK)


def _client(recorder: _Recorder) -> MagicMock:
    client = MagicMock()
    client._request.side_effect = recorder
    client._config.model = "test-model"
    client.config = SimpleNamespace(
        profile="default",
        gateway_url="http://gateway.test",
        project_id="project-alpha",
        model="test-model",
    )
    endpoint = {"instance_id": "ep-test", "model": "test-model", "status": "ready"}
    client.list_endpoints.return_value = {"endpoints": [endpoint], "total": 1}
    return client


@pytest.fixture
def recorded(monkeypatch, tmp_path):
    """A session whose project state changes every turn, plus its recorder."""
    states = count(1)
    monkeypatch.setattr(
        "r4.companion.status_report.build_project_status",
        lambda client, include_local=False: f"PROJECT: alpha\nJOBS: {next(states)} running",
    )
    recorder = _Recorder()
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    session = store.create_session(_client(recorder), name="prefix")
    probe = Skill(
        name="prefix-probe",
        description="probe skill",
        system_prompt="Probe skill body.",
        tool_names=["file_read"],
    )
    session.skills.register(probe)
    return session, recorder


def _drive_four_turns(session) -> None:
    session.send("hello")
    session.metadata["interaction_mode"] = "plan"
    session.send("plan the cleanup")
    session.metadata["interaction_mode"] = "chat"
    session.skills.activate("prefix-probe")
    session.send("now explain it")
    session.send("thanks")


def _system(payload: dict[str, Any]) -> str:
    first = payload["messages"][0]
    assert first["role"] == "system"
    return first["content"]


def _tools(payload: dict[str, Any]) -> str:
    return json.dumps(payload.get("tools"), sort_keys=True)


def _assert_prefix(prev: list[dict[str, Any]], nxt: list[dict[str, Any]]) -> None:
    """``nxt`` replays ``prev``: every message but the last unchanged, then the
    stored user text — the head of ``prev``'s last message (its turn block is
    wire-only)."""
    head = len(prev) - 1
    assert nxt[:head] == prev[:head]
    stored = nxt[head]
    assert stored["role"] == prev[head]["role"] == "user"
    assert prev[head]["content"].startswith(stored["content"])
    assert TURN_CONTEXT_OPEN not in stored["content"]


def test_system_and_tools_are_identical_across_turns(recorded):
    session, recorder = recorded
    _drive_four_turns(session)
    turns = recorder.turns()
    assert len(turns) == 4
    assert len({_system(p) for p in turns}) == 1
    assert len({_tools(p) for p in turns}) == 1
    assert "<project_state>" not in _system(turns[0])


def test_each_request_starts_with_the_previous_one(recorded):
    session, recorder = recorded
    _drive_four_turns(session)
    turns = recorder.turns()
    for prev, nxt in zip(turns, turns[1:]):
        _assert_prefix(prev["messages"], nxt["messages"])


def test_volatile_state_rides_the_turn_message(recorded):
    session, recorder = recorded
    _drive_four_turns(session)
    lasts = [p["messages"][-1]["content"] for p in recorder.turns()]
    assert lasts[0].startswith("hello\n\n" + TURN_CONTEXT_OPEN)
    assert "JOBS: 1 running" in lasts[0]
    assert "JOBS: 2 running" in lasts[1]
    assert "Plan mode is active" in lasts[1]
    assert "Plan mode is active" not in lasts[2]
    assert "Probe skill body." in lasts[2]
    assert "[Tool limit]" in lasts[2]


def test_plan_mode_refuses_a_write_at_execution(recorded):
    session, recorder = recorded
    session.send("hello")
    session.metadata["interaction_mode"] = "plan"
    recorder.script = [
        _tool_call("file_write", {"path": "x.txt", "content": "y"}),
        _text("Plan: write x.txt."),
    ]
    session.send("plan writing x.txt")
    first, plan_call, plan_final = recorder.turns()
    assert _tools(plan_call) == _tools(first) == _tools(plan_final)
    tool_result = plan_final["messages"][-1]
    assert tool_result["role"] == "tool"
    assert "cannot run this turn" in tool_result["content"]
    assert "plan mode" in tool_result["content"]


def _compaction_after_four_turns(session) -> None:
    """Compact once the history passes 6 messages, unless a summary leads it."""

    def needs(**kwargs: Any) -> bool:
        history = kwargs["history"]
        summarised = bool(history) and "[Conversation summary]" in history[0]["content"]
        return len(history) > 6 and not summarised

    session.context.needs_compaction_for_payload = needs


def test_compaction_request_shares_the_turn_prefix(recorded):
    session, recorder = recorded
    _compaction_after_four_turns(session)
    for message in ("one", "two", "three", "four", "five"):
        session.send(message)
    compactions = [p for p in recorder.payloads if _is_compaction(p)]
    assert len(compactions) == 1
    request = compactions[0]
    fourth = recorder.turns()[3]
    assert _system(request) == _system(fourth)
    assert _tools(request) == _tools(fourth)
    assert request.get("tool_choice") == "none"
    replayed = request["messages"][1:-1]
    assert replayed == fourth["messages"][1 : 1 + len(replayed)]


def test_compaction_summary_is_reused_on_later_turns(recorded):
    session, recorder = recorded
    _compaction_after_four_turns(session)
    for message in ("one", "two", "three", "four", "five", "six"):
        session.send(message)
    assert len([p for p in recorder.payloads if _is_compaction(p)]) == 1
    fifth, sixth = recorder.turns()[4:]
    assert _system(fifth) == _system(sixth)
    _assert_prefix(fifth["messages"], sixth["messages"])


def _on_compaction(session, recorder, action) -> list[int]:
    """Run ``action(session)`` on each compaction request (it may raise);
    other requests go to the recorder. Returns the compaction call counter."""
    calls: list[int] = []

    def side_effect(payload: dict[str, Any], **kwargs: Any) -> MagicMock:
        if not _is_compaction(payload):
            return recorder(payload, **kwargs)
        calls.append(len(calls) + 1)
        action(session, len(calls))
        return recorder(payload, **kwargs)

    session.client._request.side_effect = side_effect
    return calls


def _stop(session, _call: int) -> None:
    session._turn_cancel.request()
    raise RuntimeError("connection reset while stopping")


def test_stop_during_compaction_ends_the_turn_as_stopped(recorded):
    """Review (80) major: a Stop pressed while the turn compacts ends the turn
    as stopped (a stored reply), never as an exception from ``send``."""
    session, recorder = recorded
    _compaction_after_four_turns(session)
    for message in ("one", "two", "three", "four"):
        session.send(message)
    _on_compaction(session, recorder, _stop)
    assert session.send("five") == STOPPED_MESSAGE
    assert [m.role for m in session.messages][-2:] == ["user", "assistant"]
    assert session.messages[-1].content == STOPPED_MESSAGE


def _fail_first(_session, call: int) -> None:
    if call == 1:
        raise ValueError("summariser unavailable")


def test_failed_compaction_is_not_reused(recorded):
    """Review (80) minor: a fallback digest from a failed summary call is not
    cached, so the next turn over the threshold asks for a summary again."""
    session, recorder = recorded
    _compaction_after_four_turns(session)
    calls = _on_compaction(session, recorder, _fail_first)
    for message in ("one", "two", "three", "four", "five", "six"):
        session.send(message)
    assert calls == [1, 2]


def _tool_names(payload: dict[str, Any]) -> list[str]:
    return [t["function"]["name"] for t in payload.get("tools") or []]


def test_tool_limit_note_names_every_callable_tool(recorded):
    """Review (80) minor: ``tool_result_fetch`` rides every tools array and is
    never refused, so the ``[Tool limit]`` note lists it."""
    session, recorder = recorded
    session.skills.activate("prefix-probe")
    session.send("read it")
    (turn,) = recorder.turns()
    assert "tool_result_fetch" in _tool_names(turn)
    note = next(
        line
        for line in turn["messages"][-1]["content"].splitlines()
        if line.startswith("[Tool limit]")
    )
    assert "tool_result_fetch" in note


def test_switch_off_restores_the_old_layout(recorded, monkeypatch):
    """``R4_COMPANION_STABLE_PREFIX=0``: state in the system message, the plan
    turn's array narrowed to the read set, the user text alone."""
    monkeypatch.setenv("R4_COMPANION_STABLE_PREFIX", "0")
    session, recorder = recorded
    session.send("hello")
    session.metadata["interaction_mode"] = "plan"
    session.send("plan the cleanup")
    chat, plan = recorder.turns()
    assert "JOBS: 1 running" in _system(chat)
    assert "Plan mode is active" in _system(plan)
    assert chat["messages"][-1]["content"] == "hello"
    assert len(plan["tools"]) < len(chat["tools"])


def test_a_fresh_agent_has_the_plan_gate_off():
    """The plan gate's flags are declared on the agent (wave 80 CI fix): before
    the session wires them, the gate is off rather than an attribute error."""
    from r4.companion.agent import ResearchCompanion

    agent = ResearchCompanion(MagicMock())
    assert (agent._plan_gate_required, agent._plan_gate_approved) == (False, False)
    assert agent._plan_gate_permitted() is None
