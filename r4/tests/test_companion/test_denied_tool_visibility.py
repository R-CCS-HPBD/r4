"""After a policy denial the turn must never end silently (2026-10-06 report).

r4 TUI chat renders ``response_chunk`` events; a terminal answer that only
fires ``response_done`` is invisible there. Pins: every turn-terminal answer
after a denied tool call ALSO rides a chunk, and the model-facing contract
text keeps telling the model to explain + proceed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from r4.companion.agent import CompanionEventType, ResearchCompanion
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry


def _resp(content, tool_calls=None, model="test-model"):
    msg = {"content": content, "role": "assistant"}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return MagicMock(
        content=content or "",
        model=model,
        usage=MagicMock(prompt_tokens=18, completion_tokens=4, total_tokens=22),
        raw_response={"choices": [{"message": msg, "finish_reason": "stop"}]},
    )


def _tool_call(name: str, args: str, call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def _shell_tool() -> dict:
    return {
        "name": "shell_exec",
        "description": "Run a shell command",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
        "execute": lambda **_kw: "ok",
    }


def _chunk_or_done_texts(seen) -> list[str]:
    return [
        message
        for kind, message in seen
        if kind in (CompanionEventType.RESPONSE_CHUNK, CompanionEventType.RESPONSE_DONE)
    ]


def _model_calls_with(client, marker: str) -> int:
    return sum(1 for call in client._request.call_args_list if marker in str(call))


def _thinking_texts(seen) -> list[str]:
    return [message for kind, message in seen if kind == CompanionEventType.THINKING]


def _agent(client) -> ResearchCompanion:
    return ResearchCompanion(
        client,
        context_manager=ContextManager(client=client),
        skill_registry=SkillRegistry(),
        # AUTO_SAFE + no approval callback = deny-closed (the "blocked even
        # without asked" lane: no prompt is ever shown for this class).
        permission_manager=PermissionManager(mode=PermissionMode.AUTO_SAFE),
        model="test-model",
    )


def _events(agent: ResearchCompanion) -> list[tuple[CompanionEventType, str]]:
    captured: list[tuple[CompanionEventType, str]] = []
    agent.on_event(lambda ev: captured.append((ev.event_type, str(ev.message))))
    return captured


class TestDeniedToolExplainAndProceed:
    def test_denial_then_explanation_reaches_done(self):
        client = MagicMock()
        client._config.model = "test-model"
        client._request.side_effect = [
            _resp(None, tool_calls=[_tool_call("shell_exec", '{"command": "ls"}', "c1")]),
            _resp("shell_exec is blocked here — continuing without it: …"),
        ]
        agent = _agent(client)
        seen = _events(agent)

        answer = agent.send("list files", tools=[_shell_tool()], stream=True)

        assert answer.startswith("shell_exec is blocked here")
        kinds = [k for k, _ in seen]
        assert CompanionEventType.TOOL_BLOCKED in kinds
        # The explanation must be terminal-visible: as chunks (stream slice)
        # or the done payload itself.
        rendered = _chunk_or_done_texts(seen)
        assert any("continuing without it" in message for message in rendered)
        # Denial carries the model-facing contract: do not retry, proceed.
        assert _model_calls_with(client, "permission_denied") >= 1, (
            "model must see the permission_denied contract text"
        )

    def test_hard_roof_wrapup_streams_a_chunk(self, monkeypatch):
        """The wrap-up digest must ride a CHUNK, not a bare DONE."""
        monkeypatch.setenv("R4_COMPANION_TOTAL_TOOL_ROUNDS", "2")
        client = MagicMock()
        client._config.model = "test-model"
        client._request.side_effect = [
            # Two model rounds, each re-requesting the denied tool (models
            # do retry despite the contract), then the wrap-up round asks
            # for prose and gets nothing back.
            _resp(None, tool_calls=[_tool_call("shell_exec", '{"command": "ls a"}', "c1")]),
            _resp(None, tool_calls=[_tool_call("shell_exec", '{"command": "ls b"}', "c2")]),
            _resp(""),
        ]
        agent = _agent(client)
        seen = _events(agent)

        answer = agent.send("snoop around", tools=[_shell_tool()], stream=True)

        assert answer.strip(), "hard-roof turn must still return text, never ''"
        chunk_texts = [m for kind, m in seen if kind == CompanionEventType.RESPONSE_CHUNK]
        assert any("checkpoint" in message.lower() for message in chunk_texts), (
            f"wrap-up digest never streamed a chunk: {[k.value for k, _ in seen]}"
        )

    def test_denied_tool_is_hidden_after_first_denial(self):
        client = MagicMock()
        client._config.model = "test-model"
        client._request.side_effect = [
            _resp(None, tool_calls=[_tool_call("shell_exec", '{"command": "ls"}', "c1")]),
            _resp("proceeding in plain text"),
        ]
        agent = _agent(client)
        seen = _events(agent)

        agent.send("list files", tools=[_shell_tool()])

        assert "shell_exec" in agent._session_denied_tools
        assert any("hidden for the rest of this session" in m for m in _thinking_texts(seen))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("R4_COMPANION_TOTAL_TOOL_ROUNDS", raising=False)
