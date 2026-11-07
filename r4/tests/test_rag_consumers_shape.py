"""The R4 RAG consumers read the real shapes (ADR-093 C11, D-10; gap D-28).

Before this, ``search_agentic``'s RAG leg and the workflow search read
``results.documents`` (no such field on ``RAGResponse``), the agent RAG step
called ``source.to_dict()`` (no such method on ``RAGSource``), the
LangChain/LlamaIndex adapters read fields that never existed, the research
orchestrator titled every source "" and the local research loop showed the
model the first 30 node labels, unranked. Every test here runs against a real
``RAGResponse`` / ``RetrieveResult`` and fails on that code.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest
from r4_contracts.retrieval import RetrievalSufficiency, RetrievedPassage, RetrieveResult

from r4.chat_types import ChatResponse
from r4.rag import RAGResponse, RAGSource

GOAL = "Develop a digital filter for MERMAID seismic peak extraction"


def _passage(rank: int, *, artifact: str = "", **overrides: Any) -> RetrievedPassage:
    fields: dict[str, Any] = {
        "rank": rank,
        "source_id": f"node-{rank}",
        "artifact_id": artifact or f"art-{rank}",
        "source_name": f"paper-{rank}.pdf",
        "text": f"passage {rank}: a 1 Hz high-pass isolates P-wave onsets",
        "section": "Methods",
        "scores": {"dense": 0.95 - rank / 100},
        "lanes": ("dense",),
    }
    fields.update(overrides)
    return RetrievedPassage(**fields)


def _result(*passages: RetrievedPassage, status: str = "sufficient") -> RetrieveResult:
    return RetrieveResult(
        query="q",
        passages=passages,
        sufficiency=RetrievalSufficiency(status=status, basis="z_null", value=4.0, threshold=3.0),
        searched=9412,
    )


class _Rag:
    """Answers ``retrieve`` only: a consumer still on the composing
    ``query``/``search`` path fails loudly."""

    def __init__(self, result: RetrieveResult):
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def retrieve(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        return self.result


def _client(result: RetrieveResult, **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(rag=_Rag(result), config=SimpleNamespace(project_id="proj"), **extra)


# -- rag.py shapes ---------------------------------------------------------------


def test_rag_source_to_dict_carries_name_and_section():
    response = RAGResponse.from_api_response(
        {
            "answer": "a",
            "sources": [
                {
                    "document_id": "node-1",
                    "content": "text",
                    "score": 0.9,
                    "metadata": {"source_name": "mermaid.pdf", "section": "Methods"},
                }
            ],
        }
    )

    out = response.sources[0].to_dict()

    assert out["source_name"] == "mermaid.pdf"
    assert out["section"] == "Methods"
    assert out["document_id"] == "node-1"
    assert out["metadata"]["source_name"] == "mermaid.pdf"


def test_rag_source_name_falls_back_to_the_document_id():
    assert RAGSource(document_id="node-9", content="", score=0.1).source_name == "node-9"


def test_rag_response_documents_is_a_read_only_alias_of_sources():
    response = RAGResponse(answer="", sources=[RAGSource("d", "c", 0.5)])

    assert response.documents is response.sources
    with pytest.raises(AttributeError):
        response.documents = []  # type: ignore[misc]


# -- search_agentic's RAG leg -------------------------------------------------------


def test_rag_search_tool_returns_passages_titled_by_source_name():
    from r4.search.core import SourceType
    from r4.search.executor import RAGSearchTool, ToolStatus

    client = _client(_result(_passage(1), _passage(2)))
    tool = RAGSearchTool(client, "proj", top_k=5)

    outcome = asyncio.run(tool.execute("p wave filter"))

    assert outcome.status == ToolStatus.SUCCESS
    assert [s.title for s in outcome.sources] == ["paper-1.pdf", "paper-2.pdf"]
    first = outcome.sources[0]
    assert first.type == SourceType.RAG_DOCUMENT
    assert first.content.startswith("passage 1")
    assert client.rag.calls == [{"query": "p wave filter", "project_id": "proj", "k_max": 5}]


def test_rag_sources_carry_the_handle_section_and_server_score():
    from r4.search.core import sources_from_retrieve

    (source,) = sources_from_retrieve(_result(_passage(1)))

    assert source.metadata["source_id"] == "node-1"
    assert source.metadata["section"] == "Methods"
    assert source.metadata["sufficiency"] == "sufficient"
    assert source.relevance_score == pytest.approx(0.94)


def test_rag_search_tool_follows_an_insufficient_verdict():
    from r4.search.executor import RAGSearchTool, ToolStatus

    tool = RAGSearchTool(_client(_result(_passage(1), status="insufficient")), "proj")

    outcome = asyncio.run(tool.execute("best pizza in Naples"))

    assert outcome.status == ToolStatus.PARTIAL
    assert outcome.sources == []
    assert "insufficient" in outcome.message


def test_agentic_search_rag_leg_returns_sources():
    from r4.search.core import AgenticSearch, SearchConfig

    search = AgenticSearch(_client(_result(_passage(1))), SearchConfig(project_id="proj"))

    sources = asyncio.run(search.search_rag("p wave filter"))

    assert [s.title for s in sources] == ["paper-1.pdf"]


def test_filter_keeps_two_passages_of_one_document_and_does_not_recut_them():
    from r4.search.core import AgenticSearch, SearchConfig, sources_from_retrieve

    low = {"rerank_p": 0.2}  # kept by the server's rerank cut, below min_relevance 0.5
    sources = sources_from_retrieve(
        _result(
            _passage(1, artifact="a", source_name="same.pdf", scores=low),
            _passage(2, artifact="a", source_name="same.pdf", scores=low),
        )
    )
    search = AgenticSearch(_client(_result()), SearchConfig(project_id="proj"))

    kept = search._filter_sources(sources)

    assert [s.metadata["source_id"] for s in kept] == ["node-1", "node-2"]


def _catalog_sources(count: int, score: float) -> list[Any]:
    from r4.search.core import Source, SourceType

    return [
        Source(
            type=SourceType.CATALOG_ENTRY,
            title=f"cat{i}",
            url=f"s3://cat{i}",
            relevance_score=score,
        )
        for i in range(count)
    ]


def _server_ranked_sources() -> list[Any]:
    """Server rank 1-3 whose display scores disagree with that rank (fused mode)."""
    from r4.search.core import sources_from_retrieve

    return sources_from_retrieve(
        _result(
            _passage(1, scores={"dense": 0.62, "fused": 0.032}),
            _passage(2, scores={"dense": 0.81, "fused": 0.031}),
            _passage(3, scores={"dense": 0.55, "fused": 0.030}),
        )
    )


def test_filter_keeps_the_server_order_of_rag_passages():
    from r4.search.core import AgenticSearch, SearchConfig

    search = AgenticSearch(_client(_result()), SearchConfig(project_id="proj"))

    kept = search._filter_sources(_server_ranked_sources())

    assert [s.metadata["source_id"] for s in kept] == ["node-1", "node-2", "node-3"]


def test_filter_never_cuts_a_server_passage_for_higher_scored_other_sources():
    from r4.search.core import AgenticSearch, SearchConfig, SourceType

    search = AgenticSearch(_client(_result()), SearchConfig(project_id="proj"))

    kept = search._filter_sources(_catalog_sources(9, 0.9) + _server_ranked_sources())

    rag = [s.metadata["source_id"] for s in kept if s.type == SourceType.RAG_DOCUMENT]
    assert rag == ["node-1", "node-2", "node-3"]
    assert len(kept) == search.config.max_sources  # the others fill the room left
    assert [s.title for s in kept if s.type != SourceType.RAG_DOCUMENT] == [
        f"cat{i}" for i in range(7)
    ]


def _web_sources(count: int, score: float) -> list[Any]:
    from r4.search.core import Source, SourceType

    return [
        Source(
            type=SourceType.WEB_PAGE, title=f"web{i}", url=f"https://w{i}", relevance_score=score
        )
        for i in range(count)
    ]


def test_server_passages_that_fill_max_sources_leave_room_for_other_lanes():
    """fix-regressions-10: two RAG steps of 5 passages filled max_sources
    (10), and every web, paper, catalog and code hit the plan asked for was
    dropped, even at relevance 0.95."""
    from r4.search.core import AgenticSearch, SearchConfig, SourceType, sources_from_retrieve

    passages = sources_from_retrieve(_result(*(_passage(rank) for rank in range(1, 11))))
    search = AgenticSearch(_client(_result()), SearchConfig(project_id="proj"))

    kept = search._filter_sources(passages + _web_sources(4, 0.95))

    rag = [s.metadata["source_id"] for s in kept if s.type == SourceType.RAG_DOCUMENT]
    assert rag == [f"node-{rank}" for rank in range(1, 11)]  # uncut, in server order
    assert [s.title for s in kept if s.type == SourceType.WEB_PAGE] == ["web0", "web1", "web2"]


@pytest.mark.parametrize(
    ("max_sources", "passages", "room"),
    [(10, 0, 10), (10, 7, 3), (10, 10, 3), (10, 25, 3), (4, 4, 2), (1, 3, 1), (0, 0, 0)],
)
def test_other_sources_room(max_sources, passages, room):
    from r4.search.core import other_sources_room

    assert other_sources_room(max_sources, passages) == room


def test_filter_still_ranks_and_cuts_the_other_sources():
    from r4.search.core import AgenticSearch, SearchConfig, Source, SourceType

    search = AgenticSearch(_client(_result()), SearchConfig(project_id="proj", max_sources=2))
    low = Source(type=SourceType.WEB_PAGE, title="low", url="u-low", relevance_score=0.3)
    others = [*_catalog_sources(1, 0.6), low, *_catalog_sources(3, 0.9)[1:]]

    kept = search._filter_sources(others)

    assert [s.title for s in kept] == ["cat1", "cat2"]


# -- the agent workflow RAG step ------------------------------------------------------


def test_workflow_rag_step_outputs_passages_with_names():
    from r4.agent.core import AgentConfig, WorkflowExecutor
    from r4.agent.egress import EgressGuard
    from r4.agent.models import StepStatus, StepType, WorkflowStep

    client = _client(_result(_passage(1)))
    executor = WorkflowExecutor(client, AgentConfig(), Mock(spec=EgressGuard))
    step = WorkflowStep(
        id="rag",
        description="find filter evidence",
        step_type=StepType.RAG_QUERY,
        config={"query": "p wave filter"},
    )

    outcome = executor._execute_step(step, {})

    assert outcome.status == StepStatus.COMPLETED, outcome.error
    assert outcome.output["sufficiency"]["status"] == "sufficient"
    (source,) = outcome.output["sources"]
    assert (source["source_name"], source["source_id"]) == ("paper-1.pdf", "node-1")
    assert client.rag.calls[0]["query"] == "p wave filter"


# -- the integrations ------------------------------------------------------------------


def test_langchain_retriever_builds_documents_from_passages():
    pytest.importorskip("langchain_core")
    from r4.integrations.langchain import R4Retriever

    fake = _client(_result(_passage(1), _passage(2)))
    with patch("r4.Client", return_value=fake):
        retriever = R4Retriever(collection="proj", top_k=3, score_threshold=0.6)

    docs = retriever.invoke("p wave filter")
    adocs = asyncio.run(retriever.ainvoke("p wave filter"))

    assert [d.metadata["source"] for d in docs] == ["paper-1.pdf", "paper-2.pdf"]
    assert docs[0].page_content.startswith("passage 1")
    assert docs[0].metadata["source_id"] == "node-1"
    assert [d.metadata["source_id"] for d in adocs] == ["node-1", "node-2"]
    call = fake.rag.calls[0]
    assert (call["project_id"], call["k_max"], call["threshold"]) == ("proj", 3, 0.6)


def test_llamaindex_vector_store_queries_by_text():
    pytest.importorskip("llama_index.core")
    from llama_index.core.vector_stores import VectorStoreQuery

    from r4.integrations.llamaindex import R4VectorStore

    fake = _client(_result(_passage(1), _passage(2)))
    with patch("r4.Client", return_value=fake):
        store = R4VectorStore(collection="proj")

    out = store.query(VectorStoreQuery(query_str="p wave filter", similarity_top_k=2))

    assert out.ids == ["node-1", "node-2"]
    assert out.nodes[0].text.startswith("passage 1")
    assert out.nodes[0].metadata["source_name"] == "paper-1.pdf"
    assert out.similarities[0] == pytest.approx(0.94)
    assert fake.rag.calls[0] == {"query": "p wave filter", "project_id": "proj", "k_max": 2}


def test_llamaindex_vector_store_refuses_a_bare_embedding():
    pytest.importorskip("llama_index.core")
    from llama_index.core.vector_stores import VectorStoreQuery

    from r4.integrations.llamaindex import R4VectorStore

    with patch("r4.Client", return_value=_client(_result())):
        store = R4VectorStore(collection="proj")

    with pytest.raises(ValueError, match="searches by text"):
        store.query(VectorStoreQuery(query_embedding=[0.1, 0.2]))


# -- the research orchestrator ---------------------------------------------------------


def test_orchestrator_context_search_uses_retrieve_and_names_sources():
    from r4.agent.research_orchestrator import ResearchOrchestrator

    captured: dict[str, Any] = {}

    class _Loop:
        def __init__(self, *, search_fn, **_kwargs):
            captured["search_fn"] = search_fn

        def run(self, goal):
            captured["sources"] = captured["search_fn"](goal)
            return SimpleNamespace(synthesis="ok")

    client = _client(_result(_passage(1), _passage(2)))
    with patch("r4.agent.research_orchestrator.DeepResearchLoop", _Loop):
        ResearchOrchestrator(client)._gather_initial_context(GOAL)

    assert [s.title for s in captured["sources"]] == ["paper-1.pdf", "paper-2.pdf"]
    assert captured["sources"][0].snippet.startswith("passage 1")
    assert client.rag.calls == [{"query": GOAL}]


# -- the local research loop's digest -----------------------------------------------------


def _loop_client(result: RetrieveResult, reply: dict[str, Any]) -> SimpleNamespace:
    import json

    graph = {"nodes": [{"id": "n1", "type": "paper", "name": "unrelated label"}], "edges": []}
    return _client(
        result,
        knowledge=SimpleNamespace(get_graph=lambda _project: graph),
        chat_completion=Mock(return_value=ChatResponse(content=json.dumps(reply), model="m")),
    )


def test_loop_prompt_carries_goal_ranked_passages_not_node_labels():
    from r4.runtime.agent_executor import LocalAgentIterationExecutor

    client = _loop_client(
        _result(_passage(1), _passage(2)),
        {"hypothesis": "a 1 Hz high-pass helps", "evidence_ids": ["[2]", "1", "n1"]},
    )
    executor = LocalAgentIterationExecutor(client, "proj")

    summary = executor("loop-x", 1, GOAL)

    prompt = client.chat_completion.call_args.args[0][1].content
    assert "[1] paper-1.pdf › Methods" in prompt
    assert "passage 2: a 1 Hz high-pass" in prompt
    assert "unrelated label" not in prompt
    assert prompt.rstrip().endswith(f"Research goal: {GOAL}\nIteration: 1")
    assert client.rag.calls[0] == {"query": GOAL, "project_id": "proj", "k_max": 4}
    assert summary["evidence_ids"] == ["node-2", "node-1", "n1"]
    assert summary["knowledge_basis"] == "sufficient"


def test_loop_retrieval_reads_ahead_with_the_last_hypothesis():
    from r4.runtime.agent_executor import LocalAgentIterationExecutor

    client = _loop_client(_result(_passage(1)), {"hypothesis": "h-one", "evidence_ids": []})
    executor = LocalAgentIterationExecutor(client, "proj")

    executor("loop-x", 1, GOAL)
    executor("loop-x", 2, GOAL)

    assert client.rag.calls[1]["query"] == f"{GOAL} h-one"


def _reader_args(call: dict[str, Any]) -> tuple[Any, Any]:
    return call.get("profile"), call.get("reader_model")


def test_loop_retrieve_names_the_model_that_reads_it():
    """r4-scale-router-10: D-14 'sends the resolved profile with reader_model
    on every retrieve'; the loop sent neither, so UMRS sized a kimi-k3 read
    with the site's small defaults (pool, merge, partial cap)."""
    from r4.runtime.agent_executor import LocalAgentIterationExecutor

    client = _loop_client(_result(_passage(1)), {"hypothesis": "h", "evidence_ids": []})
    client.config.model = "kimi-k3"  # chat_completion(model=None) reads with this

    LocalAgentIterationExecutor(client, "proj")._retrieve(GOAL)
    LocalAgentIterationExecutor(client, "proj", model="llama3.2:3b")._retrieve(GOAL)

    assert [_reader_args(call) for call in client.rag.calls] == [
        ("large", "kimi-k3"),
        ("small", "llama3.2:3b"),
    ]
    assert client.rag.calls[0]["k_max"] == 4  # the loop's own bound stays


def test_agentic_search_rag_leg_names_the_synthesis_model():
    from r4.search.core import AgenticSearch, SearchConfig

    client = _client(_result(_passage(1)))
    client.config.model = "llama3.2:3b"

    asyncio.run(AgenticSearch(client, SearchConfig(project_id="proj")).search_rag("q"))
    config = SearchConfig(project_id="proj", model="glm-5.3")
    asyncio.run(AgenticSearch(client, config).search_rag("q"))

    assert [_reader_args(call) for call in client.rag.calls] == [
        ("small", "llama3.2:3b"),
        ("large", "glm-5.3"),
    ]


def test_search_agentic_rag_tool_names_the_synthesis_model():
    """The main search_agentic RAG leg (ToolExecutor -> RAGSearchTool) reads
    with SearchConfig.model, else the client's model (r4-scale-router-10)."""
    from r4.search.core import SearchConfig
    from r4.search.executor import ToolExecutor

    client = _client(_result(_passage(1)))
    client.config.model = "llama3.2:3b"
    for config in (
        SearchConfig(project_id="proj"),
        SearchConfig(project_id="proj", model="kimi-k3"),
    ):
        tool = ToolExecutor(client, config)._tools["rag_query"]
        asyncio.run(tool.execute("p wave filter"))

    assert [_reader_args(call) for call in client.rag.calls] == [
        ("small", "llama3.2:3b"),
        ("large", "kimi-k3"),
    ]
    assert client.rag.calls[0]["k_max"] == 5  # the leg's own bound stays


def test_workflow_rag_step_names_the_model_of_its_prompt_step():
    from r4.agent.core import AgentConfig, WorkflowExecutor
    from r4.agent.egress import EgressGuard
    from r4.agent.models import StepType, WorkflowStep

    client = _client(_result(_passage(1)))
    step = WorkflowStep(
        id="rag", description="d", step_type=StepType.RAG_QUERY, config={"query": "q"}
    )
    for model in ("glm-5.3", "llama3.2:3b"):
        executor = WorkflowExecutor(client, AgentConfig(model=model), Mock(spec=EgressGuard))
        executor._execute_step(step, {})

    assert [_reader_args(call) for call in client.rag.calls] == [
        ("large", "glm-5.3"),
        ("small", "llama3.2:3b"),
    ]


def test_orchestrator_context_search_names_the_synthesis_model():
    from r4.agent.core import AgentConfig, AgentMode
    from r4.agent.research_orchestrator import ResearchOrchestrator

    class _Loop:
        def __init__(self, *, search_fn, **_kwargs):
            self.search_fn = search_fn

        def run(self, goal):
            self.search_fn(goal)
            return SimpleNamespace(synthesis="ok")

    client = _client(_result(_passage(1)))
    config = AgentConfig(mode=AgentMode.AUTONOMOUS, model="kimi-k3")
    with patch("r4.agent.research_orchestrator.DeepResearchLoop", _Loop):
        ResearchOrchestrator(client, config)._gather_initial_context(GOAL)

    assert [_reader_args(call) for call in client.rag.calls] == [("large", "kimi-k3")]


def test_no_reading_model_sends_no_reader_fields():
    """An older-R4 request (no model anywhere) keeps the site's defaults."""
    from r4.model_scale import configured_reader_fields

    client = _client(_result())
    assert configured_reader_fields(None, client) == {}
    assert configured_reader_fields(None, Mock()) == {}  # a mock config's model is no name


def test_loop_insufficient_verdict_shows_no_passage():
    from r4.runtime.agent_executor import LocalAgentIterationExecutor

    client = _loop_client(
        _result(_passage(1, text="weak distractor"), status="insufficient"),
        {"hypothesis": "h", "evidence_ids": []},
    )

    summary = LocalAgentIterationExecutor(client, "proj")("loop-x", 1, GOAL)

    prompt = client.chat_completion.call_args.args[0][1].content
    assert "weak distractor" not in prompt
    assert "no passage addresses this goal" in prompt
    assert summary["knowledge_basis"] == "insufficient"


def _empty_result(status: str, searched: int, **sufficiency: Any) -> RetrieveResult:
    return RetrieveResult(
        query="q",
        passages=(),
        sufficiency=RetrievalSufficiency(status=status, **sufficiency),
        searched=searched,
    )


def test_loop_with_nothing_to_search_keeps_the_graph_digest():
    """searched=0 (no chunk vectors, or the query encoder down) is no verdict
    on the goal: the loop keeps the graph digest, as when retrieval is down."""
    from r4.runtime.agent_executor import LocalAgentIterationExecutor

    vacuous = _empty_result("insufficient", 0, basis="unavailable", detail="no query vector")
    client = _loop_client(vacuous, {"hypothesis": "h", "evidence_ids": ["n1"]})

    summary = LocalAgentIterationExecutor(client, "proj")("loop-x", 1, GOAL)

    prompt = client.chat_completion.call_args.args[0][1].content
    assert "Knowledge graph digest" in prompt
    assert "unrelated label" in prompt
    assert summary["knowledge_basis"] == "graph_digest"
    assert "no query vector" in summary["knowledge_note"]


def test_loop_empty_verdict_reports_the_server_status():
    from r4.runtime.agent_executor import LocalAgentIterationExecutor

    client = _loop_client(_empty_result("unknown", 50), {"hypothesis": "h", "evidence_ids": []})

    summary = LocalAgentIterationExecutor(client, "proj")("loop-x", 1, GOAL)

    prompt = client.chat_completion.call_args.args[0][1].content
    assert "(sufficiency: unknown)" in prompt
    assert "unrelated label" not in prompt
    assert summary["knowledge_basis"] == "unknown"


def test_loop_falls_back_to_the_graph_digest_when_retrieval_fails():
    from r4.runtime.agent_executor import LocalAgentIterationExecutor

    client = _loop_client(_result(), {"hypothesis": "h", "evidence_ids": ["n1"]})

    def _down(*_args, **_kwargs):
        raise ConnectionError("gateway down")

    client.rag.retrieve = _down

    summary = LocalAgentIterationExecutor(client, "proj")("loop-x", 1, GOAL)

    prompt = client.chat_completion.call_args.args[0][1].content
    assert "unrelated label" in prompt
    assert summary["knowledge_basis"] == "graph_digest"
    assert "gateway down" in summary["knowledge_note"]
    assert summary["evidence_ids"] == ["n1"]


# -- the companion rag_query tool -------------------------------------------------------


_COMPOSED = {
    "answer": "A 1 Hz high-pass [1].",
    "metadata": {"citations": {"[1]": "node-1"}, "abstained": False},
    "sources": [
        {
            "document_id": "node-1",
            "content": "A 1 Hz high-pass isolates P-wave onsets.",
            "score": 0.91,
            "metadata": {"source_name": "mermaid.pdf", "section": "Methods"},
        }
    ],
}


class _AnswerRag:
    def __init__(self):
        self.kwargs: dict[str, Any] = {}

    def query(self, query, **kwargs):
        self.kwargs = kwargs
        return RAGResponse.from_api_response(_COMPOSED)


def _rag_query_tool(client: Any) -> dict[str, Any]:
    from r4.companion.tools import _client_tools

    return next(t for t in _client_tools(client) if t["name"] == "rag_query")


def test_rag_query_keeps_source_name_section_and_citations():
    import json

    client = SimpleNamespace(rag=_AnswerRag())

    out = json.loads(_rag_query_tool(client)["execute"](query="p wave filter"))

    assert out["answer"] == "A 1 Hz high-pass [1]."
    (source,) = out["sources"]
    assert (source["source_name"], source["section"]) == ("mermaid.pdf", "Methods")
    assert out["citations"] == {"[1]": "node-1"}
    assert out["abstained"] is False


def test_rag_query_sends_no_threshold_unless_asked():
    client = SimpleNamespace(rag=_AnswerRag())
    tool = _rag_query_tool(client)

    tool["execute"](query="q")
    assert client.rag.kwargs["threshold"] is None
    tool["execute"](query="q", threshold=0.5)
    assert client.rag.kwargs["threshold"] == 0.5
    assert "default" not in tool["parameters"]["properties"]["threshold"]
