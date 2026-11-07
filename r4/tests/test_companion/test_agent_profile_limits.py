"""H11 + H2 hook (TB 4.0 harness plan 2026-10-24): the terminal profile, agent side.

``R4_AGENT_PROFILE=terminal`` is an ablation arm, never the default. Under it the
agent keeps less history (tool-history budget 24k tokens instead of 64k;
arguments of rounds older than the latest 3 clipped to 600 chars), uses
``agent_profile.core_prompt()`` instead of the platform system prompt, skips the
``<project_state>`` block, and offers only ``agent_profile.tool_allowlist()``.
Unset — also with ``R4_AUTONOMOUS=1`` — everything stays as it was.
``agent_profile`` belongs to another lane, so the agent reads it defensively.
"""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from r4.companion.agent import COMPANION_SYSTEM_PROMPT, ResearchCompanion

_MODEL = "glm-5.3@rikyu"
_PROFILE_MODULE = "r4.companion.agent_profile"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("R4_AGENT_PROFILE", raising=False)
    monkeypatch.delenv("R4_COMPANION_ARG_MAX_CHARS", raising=False)
    monkeypatch.setenv("R4_MODEL_CONTEXT_WINDOW", "200000")


@pytest.fixture
def fake_profile(monkeypatch) -> ModuleType:
    module = ModuleType(_PROFILE_MODULE)
    module.active = lambda: True  # type: ignore[attr-defined]
    module.core_prompt = lambda: "CORE: work unattended; build, verify, finish."  # type: ignore[attr-defined]
    module.tool_allowlist = lambda: frozenset({"shell_exec", "file_read"})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, _PROFILE_MODULE, module)
    monkeypatch.setenv("R4_AGENT_PROFILE", "terminal")
    return module


def _agent() -> ResearchCompanion:
    agent = ResearchCompanion(SimpleNamespace(_config=SimpleNamespace(model=_MODEL)), model=_MODEL)
    agent._load_project_state = lambda: "PROJECT STATE"  # type: ignore[method-assign]
    return agent


def _rounds(count: int) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": "s"}, {"role": "user", "content": "t"}]
    for i in range(count):
        args = json.dumps({"command": f"{i}" + "x" * 3_000})
        call = {"id": f"c{i}", "type": "function", "function": {"name": "f", "arguments": args}}
        messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})
    return messages


def _arg_sizes(messages: list[dict]) -> list[int]:
    return [
        len(m["tool_calls"][0]["function"]["arguments"]) for m in messages if m.get("tool_calls")
    ]


def test_profile_shrinks_the_tool_history_budget(monkeypatch) -> None:
    assert _agent()._tool_history_budget_chars() == int(50_000 * 3.5)
    monkeypatch.setenv("R4_AGENT_PROFILE", "terminal")
    assert _agent()._tool_history_budget_chars() == int(24_000 * 3.5)


def test_profile_clips_arguments_after_three_rounds(monkeypatch) -> None:
    monkeypatch.setenv("R4_AGENT_PROFILE", "terminal")
    sizes = _arg_sizes(_agent()._bounded_history(_rounds(5)))
    assert all(size < 800 for size in sizes[:2])
    assert all(size > 3_000 for size in sizes[2:])


def test_default_argument_bounds_are_unchanged() -> None:
    sizes = _arg_sizes(_agent()._bounded_history(_rounds(5)))
    assert all(1_500 < size < 2_100 for size in sizes[:4])
    assert sizes[4] > 3_000


def test_profile_uses_the_core_prompt_without_project_state(fake_profile) -> None:
    system = _agent()._build_messages("task", None, "")[0]["content"]
    assert system.startswith("CORE: work unattended")
    assert COMPANION_SYSTEM_PROMPT not in system
    assert "PROJECT STATE" not in system


def test_default_prompt_is_unchanged(monkeypatch) -> None:
    """Wave 80: the project state rides the turn's user message, after the
    stable system prefix; the old switch puts it back at the system's end."""
    monkeypatch.setenv("R4_AUTONOMOUS", "1")
    messages = _agent()._build_messages("task", None, "EXTRA")
    expected = COMPANION_SYSTEM_PROMPT + "\n\nEXTRA"
    assert messages[0]["content"].startswith(expected)
    assert "PROJECT STATE" not in messages[0]["content"]
    assert messages[-1]["content"].startswith("task\n\n<turn_context>")
    assert "<project_state>\nPROJECT STATE\n</project_state>" in messages[-1]["content"]
    monkeypatch.setenv("R4_COMPANION_STABLE_PREFIX", "0")
    system = _agent()._build_messages("task", None, "EXTRA")[0]["content"]
    assert system.startswith(expected)
    assert system.endswith("<project_state>\nPROJECT STATE\n</project_state>")


def test_profile_offers_only_the_allowlist(fake_profile) -> None:
    tools = [{"name": "shell_exec"}, {"name": "file_read"}, {"name": "teamup_start"}]
    offered, filtered = _agent()._offered_tools(tools, {}, None)
    assert [t["name"] for t in offered] == ["shell_exec", "file_read"]
    assert filtered["profile"] == {"count": 1, "sample": ["teamup_start"]}


def test_without_profile_every_tool_stays_offered(monkeypatch) -> None:
    monkeypatch.setenv("R4_AUTONOMOUS", "1")
    tools = [{"name": "shell_exec"}, {"name": "teamup_start"}]
    offered, filtered = _agent()._offered_tools(tools, {}, None)
    assert [t["name"] for t in offered] == ["shell_exec", "teamup_start"]
    assert "profile" not in filtered
