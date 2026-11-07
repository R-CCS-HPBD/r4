"""final_output is the turn's terminal (round-2 findings B + C, 2026-09-05).

Before: the companion loop ignored the is_final marker — after the model
called final_output the loop kept inviting more rounds, which surfaced as
the triple closing (report, "closing out" note, second report) and left the
final answer only inside the 300-char tool-card preview.

Now: the first final_output call ends the turn; its content is emitted as
the closing paragraph and returned as the turn's answer.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from r4.companion.agent import CompanionEvent, CompanionEventType, ResearchCompanion
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry


@pytest.fixture
def mock_client():
    client = MagicMock()
    client._config.model = "test-model"
    return client


def _final_output_tool() -> dict[str, Any]:
    def _execute(summary: str = "", result: str = "") -> str:
        output = summary
        if result:
            output += f"\n\n{result}"
        return output

    return {
        "name": "final_output",
        "description": "terminal answer",
        "parameters": {"type": "object", "properties": {}},
        "is_final": True,
        "execute": _execute,
    }


def _build_agent(mock_client: MagicMock) -> ResearchCompanion:
    return ResearchCompanion(
        mock_client,
        context_manager=ContextManager(client=mock_client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )


def test_final_output_terminates_turn_and_promotes_content(mock_client) -> None:
    def round_stream(payload: dict, acc: list, **kw: Any) -> Any:
        # Narration WITH the final_output call in one streamed round.
        acc.extend(
            [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "final_output",
                        "arguments": '{"summary": "Done", "result": "THE ANSWER"}',
                    },
                }
            ]
        )
        return iter(["Working on it… "])

    mock_client._stream_request_collect.side_effect = round_stream
    mock_client._config.model = "test-model"

    agent = _build_agent(mock_client)
    events: list[CompanionEvent] = []
    agent.on_event(events.append)
    response = agent.send("do the thing", tools=[_final_output_tool()], stream=True)

    # Turn ends with the final_output content — no further model round.
    assert response.endswith("THE ANSWER")
    assert mock_client._stream_request_collect.call_count == 1
    mock_client._request.assert_not_called()

    types = [e.event_type for e in events]
    assert CompanionEventType.RESPONSE_DONE in types
    # Content emitted as closing text (lands in the terminal bubble, not only
    # in the tool-card preview).
    chunk_text = "".join(
        e.message for e in events if e.event_type == CompanionEventType.RESPONSE_CHUNK
    )
    assert "THE ANSWER" in chunk_text


def test_failed_tool_then_final_output_produces_one_closing(mock_client) -> None:
    """Round-2 guard: last tool erroring ('unavailable') followed by
    final_output must end the turn in exactly one closing — the final answer.
    """
    rounds = {"n": 0}

    def round_stream(payload: dict, acc: list, **kw: Any) -> Any:
        rounds["n"] += 1
        if rounds["n"] == 1:
            acc.extend(
                [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "boom_search", "arguments": "{}"},
                    }
                ]
            )
            return iter(["Let me query the index. "])
        acc.extend(
            [
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {
                        "name": "final_output",
                        "arguments": '{"summary": "Final", "result": "ONLY CLOSING"}',
                    },
                }
            ]
        )
        return iter(["The index route is down (503); closing with what I have. "])

    def _boom(**kwargs: Any) -> str:
        raise RuntimeError("HTTP 503 from gateway")

    boom_tool = {
        "name": "boom_search",
        "description": "errors like a 503",
        "parameters": {"type": "object", "properties": {}},
        "execute": _boom,
    }
    mock_client._stream_request_collect.side_effect = round_stream
    mock_client._config.model = "test-model"

    agent = _build_agent(mock_client)
    events: list[CompanionEvent] = []
    agent.on_event(events.append)
    response = agent.send("find X", tools=[boom_tool, _final_output_tool()], stream=True)

    # Exactly one closing: the final_output content. Turn stops there — no
    # third model round inviting a second report.
    assert response.endswith("ONLY CLOSING")
    assert rounds["n"] == 2
    assert mock_client._stream_request_collect.call_count == 2
    mock_client._request.assert_not_called()

    # One terminal RESPONSE_DONE per closing round…
    done_events = [e for e in events if e.event_type == CompanionEventType.RESPONSE_DONE]
    assert done_events, "terminal event must fire"
    assert done_events[-1].message.endswith("ONLY CLOSING")


def test_nudge_suppressed_once_final_output_used(mock_client) -> None:
    """If final_output already executed and failures then streak, the mode
    control must not inject a corrective 'change approach or concede' nudge —
    that prompt is what re-invited a second full report (finding C).
    """
    agent = _build_agent(mock_client)
    steps = [
        {"name": "final_output", "arguments": "{}", "result": "done"},
        {"name": "boom_search", "arguments": "{}", "result": "Error executing boom: 503"},
        {"name": "boom_search", "arguments": "{}", "result": "Error executing boom: 503"},
        {"name": "boom_search", "arguments": "{}", "result": "Error executing boom: 503"},
    ]
    messages: list[dict[str, Any]] = []
    force, cont, nudged = agent._post_tool_round_mode_control(
        messages,
        steps,
        force_text_response=False,
        corrective_nudge_sent=False,
    )
    if cont is False:
        # answer-mode flip — acceptable as long as NO nudge text landed
        assert not any("concede" in str(m.get("content", "")).lower() for m in messages)
    else:
        assert nudged is True and all("concede" not in str(m).lower() for m in messages)


def test_system_prompt_states_tool_domain_and_terminal() -> None:
    """E.2/C.1: the companion prompt disambiguates local file tools from
    platform-storage tools and names final_output the terminal."""
    from r4.companion.agent import COMPANION_SYSTEM_PROMPT

    assert "LOCAL filesystem" in COMPANION_SYSTEM_PROMPT
    assert "_platform" in COMPANION_SYSTEM_PROMPT
    assert "lab_catalog_search" in COMPANION_SYSTEM_PROMPT
    assert "final_output is the turn's terminal" in COMPANION_SYSTEM_PROMPT


def test_system_prompt_routes_decisions_to_ask_user() -> None:
    """2026-10-02: kimi-k3 asked a design-decision question as flat prose, so
    the TUI answering card (ask_user bridge) never rendered. The prompt must
    name ask_user as the only channel for decide/confirm questions."""
    from r4.companion.agent import COMPANION_SYSTEM_PROMPT

    assert "call ask_user" in COMPANION_SYSTEM_PROMPT
    assert "chat prose" in COMPANION_SYSTEM_PROMPT


def test_turn_continues_when_no_final_output(mock_client) -> None:
    """Existing behavior pinned: non-terminal tools keep the loop alive."""
    rounds = {"n": 0}

    def round_stream(payload: dict, acc: list, **kw: Any) -> Any:
        rounds["n"] += 1
        if rounds["n"] == 1:
            acc.extend(
                [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "list_dir", "arguments": "{}"},
                    }
                ]
            )
            return iter(["Checking the directory. "])
        return iter(["Files are: a.py, b.py."])

    list_dir_tool = {
        "name": "list_dir",
        "description": "list",
        "parameters": {"type": "object", "properties": {}},
        "execute": lambda: "a.py\nb.py",
    }
    mock_client._stream_request_collect.side_effect = round_stream
    mock_client._config.model = "test-model"

    agent = _build_agent(mock_client)
    response = agent.send("list", tools=[list_dir_tool], stream=True)

    assert response == "Files are: a.py, b.py."
    assert rounds["n"] == 2
