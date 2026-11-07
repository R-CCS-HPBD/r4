"""H8 (TB 4.0 harness plan 2026-10-24): mid-turn system nudges stay in place as user notes.

``_fit_agent_messages`` merged EVERY system message into system message 0 each
round, so a checkpoint nudge ("write 2-3 sentences to the user …") became a
permanent part of the system prompt. Only the leading system block is merged
now; a later system message stays at its position as a ``[harness note]`` user
message (GLM chat templates expect the system message first). On by default
only in unattended runs (``R4_AUTONOMOUS=1``; review 2026-10-24: the served
template's handling is checked in M1 first); ``R4_COMPANION_NUDGES_AS_USER=0``
restores the merge there and ``=1`` opts an interactive session in. A note next
to another user message is merged into it (strict-alternation templates).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.companion.agent import ResearchCompanion, _checkpoint_system_message
from r4.companion.context import ContextManager

_MODEL = "glm-5.3@rikyu"


@pytest.fixture(autouse=True)
def _default_switch(monkeypatch):
    """An unattended run with the switch unset: the default-on case."""
    monkeypatch.delenv("R4_COMPANION_NUDGES_AS_USER", raising=False)
    monkeypatch.setenv("R4_AUTONOMOUS", "1")


def _agent(with_context: bool = True) -> ResearchCompanion:
    client = SimpleNamespace(_config=SimpleNamespace(model=_MODEL))
    context = ContextManager(client=client) if with_context else None
    return ResearchCompanion(client, model=_MODEL, context_manager=context)


def _turn() -> list[dict]:
    call = {"id": "a", "type": "function", "function": {"name": "f", "arguments": "{}"}}
    return [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "a", "content": "ok"},
        _checkpoint_system_message(25),
    ]


def test_fitting_keeps_one_system_message_and_the_nudge_in_place() -> None:
    fitted = _agent()._fit_agent_messages(_turn(), None)
    assert [m["role"] for m in fitted] == ["system", "user", "assistant", "tool", "user"]
    assert fitted[0]["content"] == "SYS"
    assert fitted[-1]["content"].startswith("[harness note] Checkpoint: 25 tool rounds")


def test_without_a_context_manager_the_nudge_is_converted_too() -> None:
    fitted = _agent(with_context=False)._fit_agent_messages(_turn(), None)
    assert [m["role"] for m in fitted].count("system") == 1


def test_request_payload_has_no_mid_conversation_system_message() -> None:
    """The wrap-up and overflow paths call _chat without fitting first."""
    agent = _agent(with_context=False)
    wrap = [*_turn()[:-1], {"role": "system", "content": "Tool budget exhausted."}]
    for payload in (agent._chat_payload(wrap, None), agent._build_stream_payload(wrap, None)):
        roles = [m["role"] for m in payload["messages"]]
        assert roles == ["system", "user", "assistant", "tool", "user"]


def test_leading_system_block_still_merges() -> None:
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "system", "content": "[Conversation summary] earlier"},
        {"role": "user", "content": "task"},
    ]
    fitted = _agent()._fit_agent_messages(messages, None)
    assert [m["role"] for m in fitted] == ["system", "user"]
    assert "[Conversation summary]" in fitted[0]["content"]


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_off_switch_restores_the_merge(monkeypatch, value) -> None:
    monkeypatch.setenv("R4_COMPANION_NUDGES_AS_USER", value)
    fitted = _agent()._fit_agent_messages(_turn(), None)
    assert [m["role"] for m in fitted] == ["system", "user", "assistant", "tool"]
    assert "Checkpoint: 25 tool rounds" in fitted[0]["content"]


def test_interactive_sessions_keep_the_merge(monkeypatch) -> None:
    """Review 2026-10-24: the conversion is not a default for interactive sessions."""
    monkeypatch.delenv("R4_AUTONOMOUS")
    fitted = _agent()._fit_agent_messages(_turn(), None)
    assert [m["role"] for m in fitted] == ["system", "user", "assistant", "tool"]
    monkeypatch.setenv("R4_COMPANION_NUDGES_AS_USER", "1")
    fitted = _agent()._fit_agent_messages(_turn(), None)
    assert [m["role"] for m in fitted][-1] == "user"


def test_a_note_next_to_a_user_message_is_merged_into_it() -> None:
    """Review 2026-10-24: an answer-mode note followed by the empty-reply nudge gave
    two user messages in a row, which strict-alternation templates reject."""
    messages = [
        *_turn()[:-1],
        {"role": "system", "content": "Answer now."},
        {"role": "user", "content": "Your reply was empty."},
    ]
    fitted = _agent(with_context=False)._fit_agent_messages(messages, None)
    roles = [m["role"] for m in fitted]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    assert fitted[-1]["content"] == "[harness note] Answer now.\n\nYour reply was empty."
