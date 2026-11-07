"""Review of K1 part A (knowledge plan, 2026-10-24): the r4 findings.

Each test fails on the code before the review fixes:

* security M1: the agent's ``rag_upload`` sent no actor, so UMRS read it as
  the person's upload (no proposal, rows established);
* security M2: the per-call agent flag was save/restore on the shared
  client, so a team thread's request went out as ``user`` once the lead
  agent's call ended first;
* security m4: an ``r4`` CLI run through ``shell_exec`` read as a human, so
  the agent could ``r4 knowledge review establish`` its own rows;
* product 3: once external search is off, an ingest with search terms
  reported failure though its job was queued (the job id was lost);
* product 7: ``rag_query`` with an explicit project labelled its passages
  under the current project, so opening one fetched in the wrong project.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from r4.companion.knowledge_graph_tools import agent_mutation
from r4.companion.knowledge_tools import build_knowledge_tools
from r4.knowledge import KnowledgeClient

#: ``r4.knowledge.COMPANION_SHELL_ENV`` (spelled out: the old code has no constant).
COMPANION_SHELL_ENV = "R4_COMPANION_SESSION"


class _Recorder:
    _api_prefix = "/gw"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return {}

    def post(self, url: str, json: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"method": "POST", "url": url, "json": json, **kwargs})
        return {"graph_id": "g", "job_id": "j"}


@pytest.fixture(autouse=True)
def _not_a_companion_shell(monkeypatch):
    monkeypatch.delenv(COMPANION_SHELL_ENV, raising=False)


# ── M2: the agent mark is per thread ───────────────────────────────────────


def test_a_team_thread_keeps_its_agent_mark_when_the_lead_finishes_first():
    knowledge = KnowledgeClient(_Recorder())  # type: ignore[arg-type]
    client = SimpleNamespace(knowledge=knowledge)
    sent: list[tuple[str, str | None]] = []
    lead_inside = threading.Event()
    team_inside = threading.Event()
    lead_done = threading.Event()

    def _team() -> None:
        lead_inside.wait(5)
        with agent_mutation(client):  # enters second, while the lead is inside
            team_inside.set()
            sent.append(("team:1", knowledge.actor))
            lead_done.wait(5)
            sent.append(("team:upload2", knowledge.actor))

    thread = threading.Thread(target=_team)
    thread.start()
    with agent_mutation(client):  # the lead enters first and leaves first
        lead_inside.set()
        team_inside.wait(5)
        sent.append(("lead:1", knowledge.actor))
    lead_done.set()
    thread.join(5)
    assert dict(sent) == {"team:1": "agent", "lead:1": "agent", "team:upload2": "agent"}
    assert knowledge.actor is None  # nothing sticks after both calls


def test_another_thread_outside_the_mutation_stays_the_person():
    knowledge = KnowledgeClient(_Recorder())  # type: ignore[arg-type]
    seen: list[str | None] = []
    with agent_mutation(SimpleNamespace(knowledge=knowledge)):
        worker = threading.Thread(target=lambda: seen.append(knowledge.actor))
        worker.start()
        worker.join(5)
        assert knowledge.actor == "agent"
    assert seen == [None]


# ── M1: the agent's rag_upload is the agent's ──────────────────────────────


def test_rag_upload_headers_carry_the_actor_session_and_proposal():
    from r4.rag import RAGClient

    knowledge = KnowledgeClient(_Recorder())  # type: ignore[arg-type]
    knowledge.session_id = "s-1"
    parent = SimpleNamespace(_build_headers=lambda: {"Authorization": "x"}, knowledge=knowledge)
    rag = RAGClient.__new__(RAGClient)
    rag._parent = parent  # type: ignore[attr-defined]
    with knowledge.agent_scope():
        headers = rag._upload_headers("prop-7")
    assert headers == {
        "Authorization": "x",
        "x-r4-actor": "agent",
        "x-r4-session-id": "s-1",
        "x-proposal-id": "prop-7",
    }
    assert "x-r4-actor" not in rag._upload_headers(None)


def test_the_rag_upload_tool_uploads_as_the_agent(tmp_path):
    from r4.companion.tools import _client_tools

    knowledge = KnowledgeClient(_Recorder())  # type: ignore[arg-type]
    seen: list[tuple[str | None, str | None]] = []

    def _upload(_path: str, **kwargs: Any) -> Any:
        seen.append((knowledge.actor, kwargs.get("proposal_id")))
        return SimpleNamespace(document_id="d", filename="f", status="queued-central")

    client = SimpleNamespace(knowledge=knowledge, rag=SimpleNamespace(upload=_upload))
    tool = next(t for t in _client_tools(client) if t["name"] == "rag_upload")
    tool["execute"](file_path=str(tmp_path / "a.md"), proposal_id="prop-1")
    assert seen == [("agent", "prop-1")]
    assert knowledge.actor is None
    assert "proposal_id" in tool["parameters"]["properties"]


# ── m4: the agent's shell is the agent's ───────────────────────────────────


def test_tool_subprocesses_are_marked_as_the_companions():
    from r4.companion.tools import _tool_spawn_env

    assert _tool_spawn_env()[COMPANION_SHELL_ENV] == "1"


def test_a_companion_shell_cli_sends_its_decisions_as_the_agent(monkeypatch):
    monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    inner = _Recorder()
    knowledge = KnowledgeClient(inner)  # type: ignore[arg-type]
    knowledge.establish("g-1", "n-1")
    knowledge.graph_decide("gp-1", approved=True)
    assert [c["headers"].get("x-r4-actor") for c in inner.calls] == ["agent", "agent"]


@pytest.mark.parametrize(
    "argv", [["approve", "gp-1"], ["reject", "gp-1"], ["establish", "n-1", "-p", "x"]]
)
def test_the_review_cli_refuses_to_decide_in_a_companion_shell(monkeypatch, capsys, argv):
    from r4._cli.knowledge import KnowledgeCommand
    from r4.cli import _build_parser

    monkeypatch.setenv(COMPANION_SHELL_ENV, "1")
    monkeypatch.setattr(
        "r4.client.Client", lambda: pytest.fail("no platform call from the agent's shell")
    )
    args = _build_parser().parse_args(["knowledge", "review", *argv])
    assert KnowledgeCommand().handle(args) == 2
    assert "person's decision" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    [
        "r4 knowledge review establish art-1",
        "r4 knowledge review -p proj approve gp-1",
        "R4_COMPANION_SESSION= r4 knowledge review reject gp-2",
    ],
)
def test_review_decisions_through_the_shell_always_ask(command):
    from r4.companion.permissions import _shell_command_fence

    assert _shell_command_fence("shell_exec", {"command": command}) == "never_auto"
    assert _shell_command_fence("shell_exec", {"command": "r4 knowledge review"}) is None


# ── product 3: a refused external search keeps the queued ingest ────────────


class _Knowledge:
    actor = None

    def __init__(self) -> None:
        self.ingested: list[list[str]] = []

    def ingest_paths(self, _target: str, paths: list[str], **_kw: Any) -> dict[str, Any]:
        self.ingested.append(paths)
        return {"job_id": "job-123", "status": "queued"}

    def ingest(self, _project: str, files: list[str]) -> dict[str, Any]:
        self.ingested.append(files)
        return {"job_id": "job-123"}

    def external_search(self, _project: str, _query: str) -> Any:
        raise RuntimeError("403 Forbidden: external_search_disabled")


def test_an_ingest_with_refused_search_terms_reports_the_queued_job(monkeypatch):
    import r4.companion.knowledge_tools as kt

    monkeypatch.setattr(kt, "_project_id_for", lambda _c, _p: "proj-a")
    monkeypatch.setattr(kt, "resolve_ingest_target", lambda _c, g: (g, None))
    knowledge = _Knowledge()
    client = SimpleNamespace(knowledge=knowledge)
    tool = next(t for t in build_knowledge_tools(client) if t["name"] == "knowledge_ingest")
    out = json.loads(tool["execute"](files=["paper.pdf"], sources=["MOF CO2 capture"]))
    assert out["ingest_job"]["job_id"] == "job-123"
    assert out["external_search"]["skipped"] == "external_search_disabled"
    assert knowledge.ingested == [["paper.pdf"]]


def test_the_studio_import_keeps_its_ingest_when_search_is_refused():
    from r4.acp import _external_search_probes

    out = _external_search_probes(
        SimpleNamespace(knowledge=_Knowledge()), "proj-a", ["MOF CO2 capture"]
    )
    assert out["skipped"] == "external_search_disabled"
    assert out["queries"] == ["MOF CO2 capture"]


# ── product 7: rag_query labels under the project it queried ────────────────


def test_rag_query_labels_its_passages_under_the_named_project():
    from r4.companion.evidence_ledger import session_ledger
    from r4.companion.tool_evidence import ToolEvidence
    from r4.companion.tools import _client_tools

    session = SimpleNamespace(metadata={}, _current_turn_id="t1", agent=None, messages=[])
    source = SimpleNamespace(
        document_id="node-7",
        content="text",
        score=0.8,
        metadata={"artifact_id": "art-7"},
        source_name="b.pdf",
        section="",
    )
    rag = SimpleNamespace(
        query=lambda *_a, **_k: SimpleNamespace(answer="A", sources=[source], raw_response={})
    )
    tools = _client_tools(SimpleNamespace(rag=rag), evidence=ToolEvidence(session))
    tool = next(t for t in tools if t["name"] == "rag_query")
    json.loads(tool["execute"](query="q", project_id="proj-B"))
    assert session_ledger(session).get("S1").project_id == "proj-B"


# ── product 15: a citation chip still opens once its label is gone ──────────


def test_a_citation_opens_by_its_handle_when_the_ledger_lost_the_label():
    from r4.companion.ui.citation_bridge import open_citation

    fetched: list[dict[str, Any]] = []

    def _fetch(node_id: str, **kwargs: Any) -> dict[str, Any]:
        fetched.append({"node_id": node_id, **kwargs})
        return {"text": "the passage", "chunks": [3, 4]}

    session = SimpleNamespace(metadata={}, _current_turn_id="t9", agent=None, messages=[])
    out = open_citation(
        session,
        SimpleNamespace(rag=SimpleNamespace(fetch_passage=_fetch)),
        {"sid": "S3", "source_id": "node-3", "project_id": "proj-B", "title": "[S3] b.pdf"},
    )
    assert fetched[0]["node_id"] == "node-3" and fetched[0]["project_id"] == "proj-B"
    assert (out["text"], out["title"], out["source_id"]) == ("the passage", "b.pdf", "node-3")


def test_citation_rows_carry_the_passage_handle():
    from r4_contracts.retrieval import RetrievedPassage

    from r4.companion.citation_check import _citation_row
    from r4.companion.evidence_ledger import LedgerEntry

    passage = RetrievedPassage(
        rank=1, source_id="node-3", artifact_id="art-3", source_name="b.pdf", text="t"
    )
    row = _citation_row(LedgerEntry("S3", passage, project_id="proj-B"))
    assert (row["source_id"], row["project_id"]) == ("node-3", "proj-B")
