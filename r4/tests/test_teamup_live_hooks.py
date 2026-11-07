"""Pins for the teamup live-visibility hooks (TUI team visibility wave, 2026-09-27).

The hooks are how the console follows each agent's work live: a status hook
(thinking/idle/error), full-content agent messages (consumers bound their own
previews), and per-agent prompt/model rows on ``get_status()`` — the verbatim
prompt each agent runs under, with its source, never a paraphrase.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from r4.teamup.roles import AgentRole, get_role_spec
from r4.teamup.session import (
    AgentState,
    AgentStatus,
    SessionStatus,
    TeamupSession,
    TeamupSessionConfig,
)


def _client(text: str = "PM: proceed"):
    def _chat(**kwargs):
        return SimpleNamespace(content=text, tool_calls=[], usage={})

    return SimpleNamespace(chat=_chat)


def _session(**kwargs) -> TeamupSession:
    cfg = TeamupSessionConfig(name="t", roles=[AgentRole.PM], **kwargs)
    session = TeamupSession(cfg, _client())

    class _FakeCompanion:
        def on_event(self, cb):
            pass

        def send(self, context, *, extra_system, tools, stream):
            return "PM: proceed"

    session._subcompanion_factory = lambda *a, **kw: _FakeCompanion()
    return session


def test_status_hook_fires_thinking_then_idle_sync():
    session = _session()
    seen: list[tuple[AgentRole, str]] = []
    session._on_agent_status = lambda role, status: seen.append((role, status))
    session._run_agent_turn_sync(AgentRole.PM)
    assert seen == [(AgentRole.PM, "thinking"), (AgentRole.PM, "idle")]


def test_message_hook_receives_full_content_untruncated_sync():
    long_text = "x" * 1500
    session = _session()

    class _FakeCompanion:
        def on_event(self, cb):
            pass

        def send(self, context, *, extra_system, tools, stream):
            return long_text

    session._subcompanion_factory = lambda *a, **kw: _FakeCompanion()
    seen: list[str] = []
    session._on_agent_message = lambda role, content: seen.append(content)
    session._run_agent_turn_sync(AgentRole.PM)
    assert seen == [long_text]


def test_status_hook_fires_error_when_the_turn_raises_sync():
    session = _session()

    class _Boom:
        def on_event(self, cb):
            pass

        def send(self, context, *, extra_system, tools, stream):
            raise RuntimeError("model down")

    session._subcompanion_factory = lambda *a, **kw: _Boom()
    session.status = SessionStatus.RUNNING
    seen: list[tuple[AgentRole, str]] = []
    session._on_agent_status = lambda role, status: seen.append((role, status))
    out = session._run_turns_sync()
    assert seen == [(AgentRole.PM, "thinking"), (AgentRole.PM, "error")]
    assert out[AgentRole.PM].startswith("[ERROR:")
    assert session.agent_states["pm"].status == AgentStatus.ERROR


def test_status_hook_fires_error_async():
    session = _session()

    class _Boom:
        def on_event(self, cb):
            pass

        def send(self, context, *, extra_system, tools, stream):
            raise RuntimeError("model down")

    session._subcompanion_factory = lambda *a, **kw: _Boom()
    session.status = SessionStatus.RUNNING
    seen: list[tuple[AgentRole, str]] = []
    session._on_agent_status = lambda role, status: seen.append((role, status))
    asyncio.run(session._run_agent_turns())
    assert seen == [(AgentRole.PM, "thinking"), (AgentRole.PM, "error")]


def test_hooks_unset_are_inert():
    session = _session()
    session._run_agent_turn_sync(AgentRole.PM)  # no callbacks wired → no-op
    assert session.agent_states["pm"].status == AgentStatus.IDLE
    assert session.agent_states["pm"].messages_sent == 1


def test_get_status_agents_carry_verbatim_default_prompt_and_no_model():
    session = _session()
    row = session.get_status()["agents"]["pm"]
    assert row["prompt"] == get_role_spec(AgentRole.PM).system_prompt
    assert row["prompt_source"] == "default"
    assert row["model"] is None  # broker default, honest absence


def test_get_status_agents_carry_override_prompt_and_model():
    session = _session(role_prompts={"pm": "You plan research."}, role_models={"pm": "plan-model"})
    row = session.get_status()["agents"]["pm"]
    assert row["prompt"] == "You plan research."
    assert row["prompt_source"] == "override"
    assert row["model"] == "plan-model"


def test_agent_state_to_dict_has_no_prompt_keys():
    # Snapshot persistence (session.json) must stay unchanged: prompts are a
    # get_status()-time projection, not stored state.
    row = AgentState(role=AgentRole.PM).to_dict()
    assert "prompt" not in row
    assert "prompt_source" not in row
    assert "model" not in row
