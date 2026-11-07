"""A stopped turn keeps what it streamed (stabilization sweep 2026-10-21,
w3-py#2): the TUI showed the partial reply while the stored history, which
the model reads next turn, held "(Generation stopped.)" alone."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.companion.agent import CompanionEventType
from r4.companion.cancellation import STOPPED_MESSAGE, TurnCancelledError, is_stopped_reply
from r4.companion.citation_check import _checkable
from r4.companion.session import CompanionSessionStore

QUESTION = "Which band gap did the films reach?"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("R4_RAG_ROUTER", "R4_RAG_EXPAND", "R4_RAG_PROFILE", "R4_RAG_VERIFY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def session(tmp_path):
    client = MagicMock()
    client._config.model = "test-model"
    client.config = SimpleNamespace(
        profile="default", gateway_url="http://gw.test", project_id="proj-1", model="test-model"
    )
    client.list_endpoints.return_value = {
        "endpoints": [{"instance_id": "ep", "model": "test-model", "status": "ready"}],
        "total": 1,
    }
    made = CompanionSessionStore(db_path=tmp_path / "s.db").create_session(client, name="R")
    made._harness = MagicMock()
    made._harness.feedforward.return_value = ""
    return made


def _streams(agent, *rounds: str, stop: bool):
    """An agentic turn that streams ``rounds`` (one round each), then stops."""

    def run(**_kw):
        for text in rounds:
            agent._emit(CompanionEventType.RESPONSE_START, "")
            for word in text.split(" "):
                agent._emit(CompanionEventType.RESPONSE_CHUNK, word + " ")
        if stop:
            raise TurnCancelledError()
        return rounds[-1]

    return run


def test_a_stopped_turn_stores_the_partial_reply_before_the_marker(session) -> None:
    agent = session.agent
    agent._run_agentic_turn = _streams(
        agent, "Let me check the films.", "The band gap reached 1.6", stop=True
    )
    reply = session.send(QUESTION)
    expected = f"Let me check the films.\n\nThe band gap reached 1.6\n\n{STOPPED_MESSAGE}"
    assert reply == expected
    assert session.messages[-1].content == expected


def test_the_next_turn_starts_without_the_previous_partial(session) -> None:
    agent = session.agent
    agent._run_agentic_turn = _streams(agent, "Half an answer", stop=True)
    session.send(QUESTION)
    agent._run_agentic_turn = _streams(agent, stop=True)
    assert session.send("again") == STOPPED_MESSAGE


def test_a_stopped_partial_is_still_a_stopped_reply() -> None:
    partial = f"The band gap reached 1.6\n\n{STOPPED_MESSAGE}"
    assert is_stopped_reply(partial) and is_stopped_reply(STOPPED_MESSAGE)
    assert not is_stopped_reply("The band gap is 1.62 eV.")
    # The citation check never judges a reply the user cut off.
    assert not _checkable(partial)
