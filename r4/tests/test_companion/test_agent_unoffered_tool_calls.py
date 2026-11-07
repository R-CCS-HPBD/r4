"""Calls to tools a turn does not offer never reach the approval prompt
(companion sweep 2026-10-21, driven through the real JSONL bridge).

- A made-up name (``rm_everything``), an empty name, or a tool hidden after
  the user denied it asked the human to approve it; "yes" ran nothing, and
  "always" granted the model-chosen name for the session.
- A streamed ``tool_calls`` delta with ``index: 5`` left placeholder slots
  0..4 (no id, no name, no arguments); each became a call named "" and
  asked its own approval ("Call tool: ").
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from r4.companion.agent import ResearchCompanion, _streamed_round_payload
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client._config.model = "test-model"
    return client


def _agent(mock_client, asked: list[str]) -> ResearchCompanion:
    perms = PermissionManager(mode=PermissionMode.AUTO_SAFE)

    def _approve(name: str, _description: str, _arguments: dict) -> bool:
        asked.append(name)
        return True

    perms.set_approval_callback(_approve)
    return ResearchCompanion(
        mock_client,
        context_manager=ContextManager(client=mock_client),
        skill_registry=SkillRegistry(),
        permission_manager=perms,
    )


def _shell_tool(ran: list[str]) -> dict:
    def _execute(command: str = "") -> str:
        ran.append(command)
        return "ok"

    return {
        "name": "shell_exec",
        "description": "Run a shell command",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
        "execute": _execute,
    }


def _call(name: str, arguments: str = '{"command": "touch x"}') -> dict:
    return {
        "id": f"call-{name}",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def test_made_up_tool_name_is_refused_without_an_approval_prompt(mock_client) -> None:
    asked: list[str] = []
    agent = _agent(mock_client, asked)
    result = agent._execute_tool_call(_call("rm_everything", '{"path": "/"}'), [_shell_tool([])])
    assert "unknown tool 'rm_everything'" in result
    assert asked == []
    assert "rm_everything" not in agent._permissions.session_approved_names


def test_empty_tool_name_is_refused_without_an_approval_prompt(mock_client) -> None:
    asked: list[str] = []
    agent = _agent(mock_client, asked)
    result = agent._execute_tool_call(_call(""), [_shell_tool([])])
    assert "unknown tool" in result
    assert asked == []


def test_a_tool_hidden_by_this_turn_is_not_asked_for(mock_client) -> None:
    """shell_exec exists but this turn offers only list_dir (it was hidden
    after a denial): the old order asked the user, then ran nothing."""
    asked: list[str] = []
    agent = _agent(mock_client, asked)
    offered = [{"name": "list_dir", "description": "", "parameters": {}, "execute": lambda: ""}]
    result = agent._execute_tool_call(_call("shell_exec"), offered)
    assert "unknown tool 'shell_exec'" in result
    assert asked == []


def test_an_offered_risky_tool_still_asks_and_runs(mock_client) -> None:
    asked: list[str] = []
    ran: list[str] = []
    agent = _agent(mock_client, asked)
    result = agent._execute_tool_call(_call("shell_exec"), [_shell_tool(ran)])
    assert asked == ["shell_exec"]
    assert ran == ["touch x"]
    assert result == "ok"


def test_index_gap_placeholders_are_not_tool_calls() -> None:
    placeholder = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
    real = {
        "id": "c5",
        "type": "function",
        "function": {"name": "file_read", "arguments": '{"path": "x"}'},
    }
    payload = _streamed_round_payload("", "", [dict(placeholder) for _ in range(5)] + [real], None)
    assert payload["choices"][0]["message"]["tool_calls"] == [real]


def test_a_named_call_without_arguments_is_kept() -> None:
    call = {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": ""}}
    payload = _streamed_round_payload("", "", [call], None)
    assert payload["choices"][0]["message"]["tool_calls"] == [call]
