"""ADR-093 D-8 review fixes (2026-10-10) that span the router, the send
paths and the agent.

- r4-harness-10: a stop during pre-retrieval ends the turn as stopped. The
  router re-raises the turn's :class:`TurnCancelledError` (it never reports
  "Retrieval unavailable (TurnCancelledError: )"), and both send paths end
  the turn without preparing a payload or calling the model.
- r4-harness-8: with the router on, the model reads the context pack, but
  the agent's intent heuristics (the "user asked for structured output"
  safeguard, family tool selection) read the user's own message: a passage
  that mentions CSV or JSON no longer disables the safeguard.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from r4_contracts.retrieval import RetrievalSufficiency, RetrievedPassage, RetrieveResult

from r4.companion import retrieval_router as rr
from r4.companion.agent import CompanionEventType, ResearchCompanion
from r4.companion.cancellation import STOPPED_MESSAGE, TurnCancelledError
from r4.companion.context import ContextManager
from r4.companion.context_budget import RetrievalProfile
from r4.companion.evidence_ledger import EvidenceLedger
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.session import CompanionSessionStore
from r4.companion.session_flow import send_turn
from r4.companion.skills import SkillRegistry

SMALL = RetrievalProfile(size_class="small", k_max=3, max_tokens=1_200)
QUESTION = "Which band gap did the perovskite films reach after annealing?"
PASSAGE = "The annealed perovskite films reached a band gap of 1.62 eV; raw data in a CSV file."


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("R4_RAG_ROUTER", "R4_RAG_EXPAND", "R4_RAG_PROFILE", "R4_RAG_VERIFY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("R4_HARNESS_ABLATE_KNOWLEDGE", raising=False)
    monkeypatch.delenv("R4_TOOL_SELECTION_STRATEGY", raising=False)


def _sufficient() -> RetrieveResult:
    passage = RetrievedPassage(
        rank=1, source_id="node-1", artifact_id="art-1", source_name="films.pdf", text=PASSAGE
    )
    return RetrieveResult(
        query=QUESTION,
        passages=(passage,),
        sufficiency=RetrievalSufficiency(status="sufficient", basis="z_null"),
        searched=500,
    )


class _Retrieve:
    """``client.rag.retrieve``: replays ``outcomes`` in order (an exception
    is raised)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, _query, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Clean:
    def scan(self, _text, **_kwargs):
        return SimpleNamespace(risk_level="none", is_blocked=False)


def _router(*outcomes) -> rr.RetrievalRouter:
    return rr.RetrievalRouter(_Retrieve(*outcomes), EvidenceLedger(), scanner=_Clean())


# ── r4-harness-10: the router re-raises a stop ──────────────────────────────


def test_a_stop_during_pre_retrieval_is_raised_not_reported_as_unavailable():
    router = _router(TurnCancelledError())
    with pytest.raises(TurnCancelledError):
        router.route(QUESTION, mode="on", project_id="proj-1", profile=SMALL)


def test_a_stop_during_the_history_retry_is_raised_too():
    partial = _sufficient()
    partial = RetrieveResult(
        query=partial.query,
        passages=partial.passages,
        sufficiency=RetrievalSufficiency(status="partial", basis="z_null"),
        searched=500,
    )
    router = _router(partial, TurnCancelledError())
    with pytest.raises(TurnCancelledError):
        router.route(
            "and what about its gastrointestinal side effects?",
            mode="on",
            project_id="proj-1",
            profile=SMALL,
            previous_user="Is metformin safe?",
        )


def test_send_turn_ends_a_turn_stopped_during_routing_without_the_model():
    order: list[tuple] = []

    def stopped(_message):
        raise TurnCancelledError()

    reply = send_turn(
        user_message="raw",
        start_turn=lambda m: [],
        evaluate_turn_endpoint_guard=lambda m, tools: {"state": "ok", "message": ""},
        prepare_turn_payload=lambda **kw: order.append(("prepare",)) or (("s", [], []), None),
        run_agent_turn=lambda **kw: order.append(("run",)) or "answer",
        finish_turn=lambda m, r: order.append(("finish", m, r)) or r,
        route_turn=stopped,
        check_turn=lambda m, r, s: order.append(("check",)) or r,
    )
    assert reply == STOPPED_MESSAGE
    assert order == [("finish", "raw", STOPPED_MESSAGE)]


# ── r4-harness-10: both session send paths ──────────────────────────────────


@pytest.fixture
def client():
    mock = MagicMock()
    mock._config.model = "test-model"
    mock.config = SimpleNamespace(
        profile="default", gateway_url="http://gw.test", project_id="proj-1", model="test-model"
    )
    mock.list_endpoints.return_value = {
        "endpoints": [{"instance_id": "ep", "model": "test-model", "status": "ready"}],
        "total": 1,
    }
    return mock


def _session(tmp_path, client, *outcomes):
    session = CompanionSessionStore(db_path=tmp_path / "s.db").create_session(client, name="R")
    session._harness = MagicMock()
    session._harness.feedforward.return_value = ""
    client.rag.retrieve = _Retrieve(*outcomes)
    sent: list[str] = []
    session.agent.send = lambda user_message, **_kw: sent.append(user_message) or "An answer."
    events: list = []
    session.agent.on_event(events.append)
    return session, sent, events


def _announced(events) -> list[str]:
    return [e.message for e in events if e.event_type is CompanionEventType.THINKING]


def test_a_chat_turn_stopped_during_routing_ends_as_stopped(tmp_path, client, monkeypatch):
    monkeypatch.setenv("R4_RAG_ROUTER", "on")
    session, sent, events = _session(tmp_path, client, TurnCancelledError())
    assert session.send(QUESTION) == STOPPED_MESSAGE
    assert sent == []  # the model is never called
    assert not any("unavailable" in m for m in _announced(events))
    assert [m.content for m in session.messages] == [QUESTION, STOPPED_MESSAGE]


def test_a_plan_turn_stopped_during_routing_ends_as_stopped(tmp_path, client, monkeypatch):
    monkeypatch.setenv("R4_RAG_ROUTER", "on")
    session, sent, events = _session(tmp_path, client, TurnCancelledError())
    session.metadata["interaction_mode"] = "plan"
    reply = session.send(QUESTION)
    assert STOPPED_MESSAGE in reply and sent == []
    assert not any("unavailable" in m for m in _announced(events))


# ── r4-harness-8: the agent's intent heuristics read the raw message ────────


def _chat_response(content: str):
    message = {"content": content, "role": "assistant"}
    return MagicMock(
        content=content,
        model="test-model",
        usage=MagicMock(prompt_tokens=18, completion_tokens=4, total_tokens=22),
        raw_response={"choices": [{"message": message, "finish_reason": "stop"}]},
    )


def _agent(payloads: list[dict]) -> tuple[ResearchCompanion, list]:
    """An agent whose model answers a JSON artifact in tool mode, then text."""
    client = MagicMock()
    client._config.model = "test-model"

    def fake_request(payload, **_kwargs):
        payloads.append(payload)
        content = '{"response": "1.62 eV"}' if len(payloads) == 1 else "1.62 eV"
        return _chat_response(content)

    client._request.side_effect = fake_request
    agent = ResearchCompanion(
        client,
        context_manager=ContextManager(client=client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )
    events: list = []
    agent.on_event(events.append)
    return agent, events


def _tool():
    return {
        "name": "list_dir",
        "description": "List dir",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        "execute": lambda path=".": "file1.py",
    }


def _pack() -> str:
    return f"<passages>\n[S1] films.pdf\n{PASSAGE}\n</passages>\n\nQuestion: {QUESTION}"


def _disabled(events) -> list:
    return [e for e in events if e.event_type is CompanionEventType.TOOLS_DISABLED]


def test_a_passage_mentioning_csv_never_disables_the_structured_output_safeguard():
    payloads: list[dict] = []
    agent, events = _agent(payloads)
    reply = agent.send(_pack(), tools=[_tool()], intent_message=QUESTION)
    assert reply == "1.62 eV" and len(_disabled(events)) == 1
    assert "tools" in payloads[0] and "tools" not in payloads[1]
    # The model reads the pack (wave 80: any per-turn <turn_context> block follows it).
    assert payloads[0]["messages"][-1]["content"].split("\n\n<turn_context>")[0] == _pack()


def test_without_an_intent_the_message_itself_is_read():
    """The pre-router behaviour stands: a user who writes CSV asked for it."""
    payloads: list[dict] = []
    agent, events = _agent(payloads)
    agent.send(_pack(), tools=[_tool()])
    assert _disabled(events) == []


def test_family_selection_reads_the_intent(monkeypatch):
    import r4.companion.tool_selection as selection

    seen: list[str] = []

    def select(*, tools, message_text):
        seen.append(message_text)
        return tools, {}, None

    monkeypatch.setenv("R4_TOOL_SELECTION_STRATEGY", "family")
    monkeypatch.setattr(selection, "select_with_family", select)
    agent, _ = _agent([])
    agent.send(_pack(), tools=[_tool()], intent_message=QUESTION)
    assert seen == [QUESTION]


def test_a_routed_session_turn_keeps_the_safeguard(tmp_path, client, monkeypatch):
    """End to end: router on, a passage mentions CSV, the model answers JSON
    in tool mode: the safeguard reads the question and fires."""
    monkeypatch.setenv("R4_RAG_ROUTER", "on")
    session = CompanionSessionStore(db_path=tmp_path / "s.db").create_session(client, name="R")
    session._harness = MagicMock()
    session._harness.feedforward.return_value = ""
    client.rag.retrieve = _Retrieve(_sufficient())
    payloads: list[dict] = []

    def fake_request(payload, **_kwargs):
        payloads.append(payload)
        content = '{"response": "1.62 eV"}' if len(payloads) == 1 else "1.62 eV"
        return _chat_response(content)

    client._request.side_effect = fake_request
    events: list = []
    session.agent.on_event(events.append)
    session.send(QUESTION)
    assert "CSV" in payloads[0]["messages"][-1]["content"]
    assert len(_disabled(events)) == 1
