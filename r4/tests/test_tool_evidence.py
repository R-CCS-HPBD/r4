"""Sources always shown (knowledge plan K1.4, 2026-10-24).

Only the pre-retrieval router (off by default) wrote to the evidence ledger,
so an answer grounded by ``search_docs`` had no citation block and no footer:
``turn_citations`` was empty. The knowledge tools now label what they return
with the session's ledger labels (``S4``), ``open_passage`` reads a passage
with its context under the server's access check, and ``citation.open``
lets the console's citation chip open the passage.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from r4_contracts.retrieval import RetrievalSufficiency, RetrievedPassage, RetrieveResult
from r4_contracts.taint import TOOL_TAINT_CLASSES

from r4.companion.citation_check import check_session_turn, turn_citations
from r4.companion.evidence_ledger import EvidenceLedger, session_ledger
from r4.companion.knowledge_graph_tools import build_knowledge_graph_tools
from r4.companion.passage_tool import OPEN_PASSAGE_MAX_CHARS, open_passage
from r4.companion.permissions import SAFE_TOOLS
from r4.companion.session_store import CompanionSessionStore
from r4.companion.tool_availability import is_platform_tool
from r4.companion.tool_evidence import ToolEvidence, label_passages
from r4.companion.tools import _client_tools, _rag_query_payload
from r4.companion.ui.citation_bridge import open_citation
from r4.errors import APIError
from r4.tui.commands import _knowledge_result_lines


def _passage(rank: int, **overrides: Any) -> RetrievedPassage:
    fields: dict[str, Any] = {
        "rank": rank,
        "source_id": f"node-{rank}",
        "artifact_id": f"art-{rank}",
        "source_name": f"paper-{rank}.pdf",
        "text": f"Vitamin D lowers hip fracture risk in trial {rank}.",
        "section": "Results",
        "scores": {"dense": 0.9},
    }
    fields.update(overrides)
    return RetrievedPassage(**fields)


def _result(*passages: RetrievedPassage) -> RetrieveResult:
    return RetrieveResult(
        query="q",
        passages=tuple(passages),
        sufficiency=RetrievalSufficiency(status="sufficient"),
        searched=10,
    )


def _fake_session(turn_id: str = "t1") -> SimpleNamespace:
    return SimpleNamespace(metadata={}, _current_turn_id=turn_id, agent=None, messages=[])


def _tool(tools: list[dict[str, Any]], name: str):
    return next(t for t in tools if t["name"] == name)["execute"]


class _Rag:
    def __init__(self, result: RetrieveResult | None = None, passage: Any = None):
        self._result = result
        self._passage = passage
        self.fetches: list[dict[str, Any]] = []

    def retrieve(self, query, **_kwargs):
        return self._result

    def fetch_passage(self, node_id, **kwargs):
        self.fetches.append({"node_id": node_id, **kwargs})
        if isinstance(self._passage, Exception):
            raise self._passage
        return self._passage or {
            "source_id": node_id,
            "artifact_id": "art-1",
            "source_name": "paper-1.pdf",
            "text": "Before. Vitamin D lowers hip fracture risk in trial 1. After.",
            "chunks": [3, 5],
        }


# ------------------------------------------------------------ the ledger ---


def test_assign_labels_in_order_and_continues_after_the_router():
    ledger = EvidenceLedger()
    ledger.record({"[S1]": "node-9"}, [_passage(9)], turn_id="t0")

    entries = ledger.assign([_passage(1), _passage(2)], turn_id="t1", project_id="p")

    assert [e.sid for e in entries] == ["S2", "S3"]
    assert ledger.turn_sids("t1") == ("S2", "S3")
    assert ledger.get("S3").project_id == "p"
    assert ledger.next_label == 4


def test_a_passage_the_session_has_seen_keeps_its_label():
    ledger = EvidenceLedger()
    ledger.assign([_passage(1)], turn_id="t1")

    again = ledger.assign([_passage(2), _passage(1, text="wider text")], turn_id="t2")

    assert [e.sid for e in again] == ["S2", "S1"]
    assert ledger.get("S1").passage.text == "wider text"
    assert ledger.turn_sids("t2") == ("S2", "S1")


def test_assign_registers_taint_like_a_pack():
    registry = MagicMock()
    ledger = EvidenceLedger(taint_registry=registry)
    ledger.assign([_passage(1)])
    registry.register.assert_called_once_with("context_pack", _passage(1).text)


def test_tool_evidence_records_for_the_current_turn_and_persists_the_counter():
    session = _fake_session("t7")
    labels = ToolEvidence(session).label([_passage(1)], project_id="p")
    assert labels == ["S1"]
    assert session_ledger(session).turn_sids("t7") == ("S1",)
    assert session.metadata["evidence_ledger"] == {"next_label": 2}


def test_label_passages_without_a_session_gives_no_labels():
    assert label_passages(None, [_passage(1)]) == []
    broken = SimpleNamespace(label=MagicMock(side_effect=RuntimeError("x")))
    assert label_passages(broken, [_passage(1)]) == []


# ----------------------------------------------------------- the tools ---


def test_search_docs_labels_its_hits_with_the_session_ledger():
    session = _fake_session()
    client = SimpleNamespace(rag=_Rag(_result(_passage(1), _passage(2))))

    out = _tool(_client_tools(client, evidence=ToolEvidence(session)), "search_docs")("q")

    assert out.startswith("[S1] paper-1.pdf › Results (0.900) id=node-1")
    assert "[S2] paper-2.pdf › Results" in out
    assert session_ledger(session).turn_sids("t1") == ("S1", "S2")


def test_search_docs_without_a_session_numbers_its_hits_as_before():
    client = SimpleNamespace(rag=_Rag(_result(_passage(1))))
    out = _tool(_client_tools(client), "search_docs")("q")
    assert out.startswith("[1] paper-1.pdf › Results")


def test_rag_query_sources_carry_their_labels():
    session = _fake_session()
    source = SimpleNamespace(
        document_id="node-4",
        content="Vitamin D lowers hip fracture risk.",
        score=0.8,
        metadata={"source_name": "trial.pdf", "artifact_id": "art-4"},
        source_name="trial.pdf",
        section="",
    )
    result = SimpleNamespace(answer="Yes [1].", sources=[source], raw_response={})

    payload = _rag_query_payload(result, ToolEvidence(session))

    assert payload["sources"][0]["sid"] == "S1"
    entry = session_ledger(session).get("S1")
    assert entry.passage.source_id == "node-4" and entry.passage.artifact_id == "art-4"
    assert "sid" not in _rag_query_payload(result)["sources"][0]


def test_knowledge_query_hits_carry_their_labels():
    session = _fake_session()
    knowledge = SimpleNamespace(
        graphs=lambda **_kw: [
            {"graph_id": "g-a", "name": "a", "kind": "project", "project_id": "proj-a"}
        ],
        query=lambda q, **kw: {
            "query": {
                "results": [
                    {"title": "trial", "score": 0.9, "source_id": "node-8", "content": "text"},
                    {"title": "entity only", "score": 0.5},
                ]
            }
        },
    )
    client = SimpleNamespace(knowledge=knowledge, config=SimpleNamespace(project_id="proj-a"))
    tools = build_knowledge_graph_tools(client, evidence=ToolEvidence(session))

    out = json.loads(_tool(tools, "knowledge_query")(query="vitamin D"))

    assert out["results"][0]["sid"] == "S1"
    assert "sid" not in out["results"][1]  # no content: nothing to cite
    assert session_ledger(session).get("S1").project_id == "proj-a"


# ----------------------------------------------------------- the turn ---


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
    mock.rag = _Rag(_result(_passage(1), _passage(2)))
    return mock


def _session_answering(tmp_path, client, answer: str):
    session = CompanionSessionStore(db_path=tmp_path / "s.db").create_session(client, name="K")
    session._harness = MagicMock()
    session._harness.feedforward.return_value = ""
    search = _tool(session.tools, "search_docs")

    def fake_send(user_message, **_kw):
        search(query=user_message)  # the model calls search_docs, then answers
        return answer

    session.agent.send = fake_send
    return session


def test_a_turn_that_used_search_docs_has_citations_without_the_router(
    tmp_path, client, monkeypatch
):
    monkeypatch.delenv("R4_RAG_ROUTER", raising=False)
    monkeypatch.delenv("R4_RAG_VERIFY", raising=False)
    session = _session_answering(
        tmp_path, client, "Vitamin D lowers hip fracture risk in trial 1 [S1]."
    )

    reply = session.send("Does vitamin D lower hip fracture risk?")

    rows = turn_citations(session, session._current_turn_id)
    assert [(r["sid"], r["kind"], r["title"]) for r in rows] == [("S1", "passage", "paper-1.pdf")]
    assert "Sources: [S1] paper-1.pdf" in reply


def test_an_uncited_answer_lists_what_was_retrieved(tmp_path, client, monkeypatch):
    monkeypatch.delenv("R4_RAG_ROUTER", raising=False)
    monkeypatch.delenv("R4_RAG_VERIFY", raising=False)
    session = _session_answering(tmp_path, client, "Probably yes.")

    reply = session.send("Does vitamin D lower hip fracture risk?")

    rows = turn_citations(session, session._current_turn_id)
    assert [(r["sid"], r["kind"]) for r in rows] == [("S1", "retrieved"), ("S2", "retrieved")]
    assert "Retrieved: [S1] paper-1.pdf; [S2] paper-2.pdf" in reply


def test_retrieved_rows_are_capped():
    session = _fake_session()
    ToolEvidence(session).label([_passage(n) for n in range(1, 13)])
    check_session_turn(session, "q", "No citation here.")
    assert len(turn_citations(session, "t1")) == 8


# ------------------------------------------------------- open_passage ---


def test_open_passage_by_label_fetches_in_the_retrieval_project_with_context():
    session = _fake_session()
    evidence = ToolEvidence(session)
    evidence.label([_passage(1)], project_id="proj-a")
    client = SimpleNamespace(rag=_Rag())

    out = open_passage(client, evidence, "[S1]", window=5)

    assert client.rag.fetches == [
        {
            "node_id": "node-1",
            "project_id": "proj-a",
            "window": 2,
            "max_chars": OPEN_PASSAGE_MAX_CHARS + 1,
        }
    ]
    assert out.startswith("[S1] paper-1.pdf id=node-1 chunks 3-5\nBefore. Vitamin D")
    assert session_ledger(session).get("S1").passage.text.startswith("Before.")


def test_open_passage_output_is_bounded():
    big = {"source_id": "n", "source_name": "x.pdf", "text": "w " * 5000}
    client = SimpleNamespace(rag=_Rag(passage=big))
    out = open_passage(client, None, "n")
    body = out.split("\n", 1)[1]
    assert len(body) <= OPEN_PASSAGE_MAX_CHARS + 2 and body.endswith(" …")


def test_open_passage_by_id_uses_the_clients_project_and_labels_it():
    session = _fake_session()
    client = SimpleNamespace(rag=_Rag())
    out = open_passage(client, ToolEvidence(session), "node-77", window=0)
    assert client.rag.fetches[0]["node_id"] == "node-77"
    assert client.rag.fetches[0]["project_id"] is None
    assert out.startswith("[S1] ")


def test_open_passage_refuses_an_unknown_label_without_fetching():
    client = SimpleNamespace(rag=_Rag())
    out = open_passage(client, ToolEvidence(_fake_session()), "S9")
    assert "No passage is labelled S9" in out
    assert client.rag.fetches == []


def test_open_passage_reports_a_passage_the_server_hides():
    client = SimpleNamespace(rag=_Rag(passage=APIError("nope", status_code=404)))
    out = open_passage(client, None, "node-1")
    assert out == "Passage node-1 is not visible to you or no longer exists."


def test_open_passage_is_a_safe_read_only_platform_tool():
    tools = _client_tools(SimpleNamespace(rag=_Rag()))
    tool = next(t for t in tools if t["name"] == "open_passage")
    assert tool["concurrency_safe"] is True
    assert "open_passage" in SAFE_TOOLS
    assert TOOL_TAINT_CLASSES["open_passage"] == "rag_chunk"
    assert is_platform_tool(tool)


# ------------------------------------------------------ citation.open ---


def test_citation_open_fetches_the_labelled_passage_and_records_nothing():
    session = _fake_session()
    ToolEvidence(session).label([_passage(1)], project_id="proj-a")
    client = SimpleNamespace(rag=_Rag())

    out = open_citation(session, client, {"sid": "S1", "window": 9})

    assert out["sid"] == "S1" and out["title"] == "paper-1.pdf"
    assert out["text"].startswith("Before. Vitamin D") and out["chunks"] == [3, 5]
    assert client.rag.fetches[0]["project_id"] == "proj-a"
    assert client.rag.fetches[0]["window"] == 2
    assert session_ledger(session).get("S1").passage.text == _passage(1).text
    assert session.metadata["evidence_ledger"] == {"next_label": 2}


def test_citation_open_refuses_an_unknown_label():
    with pytest.raises(ValueError, match="no passage labelled S4"):
        open_citation(_fake_session(), SimpleNamespace(rag=_Rag()), {"sid": "S4"})
    with pytest.raises(ValueError, match="needs the citation's sid"):
        open_citation(_fake_session(), SimpleNamespace(rag=_Rag()), {})


# ------------------------------------------------------ /knowledge <q> ---


def test_knowledge_results_show_a_snippet_for_the_top_hits_only():
    """Review of K1 part A: each hit took three lines (title, an id no user
    command takes, a snippet), so 20 hits printed 60 lines."""
    hit = {"title": "MOF-5 notes", "score": 0.91, "source_id": "node-3", "content": "a  b " * 60}
    lines = _knowledge_result_lines(1, hit)
    assert lines[0] == "  [1] MOF-5 notes  (score: 0.91)"
    assert len(lines) == 2
    assert lines[1].startswith("      a b a b") and lines[1].endswith(" …")
    assert _knowledge_result_lines(6, hit) == ["  [6] MOF-5 notes  (score: 0.91)"]
    assert _knowledge_result_lines(2, {"title": "t"}) == ["  [2] t  (score: ?)"]
