"""Pins: the agentic-search LLM legs ride the session's effective model
(2026-09-29 rivault/kimi-k3 class — `No active endpoint for model
'rivault-gpt-oss-static'` inside search synthesis while chat ran fine on the
public endpoint). Every synthesis/planner chat call must carry the threaded
model; None stays a client-default decision (honest absence).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from r4.search.core import SearchConfig, SearchDepth, SearchScope
from r4.search.planner import SearchPlanner
from r4.search.synthesizer import ResultSynthesizer


class _RecordingClient:
    """Chat stub that captures every model kwarg it receives."""

    def __init__(self, content: str):
        self.calls: list[dict] = []
        self._content = content

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self._content, tool_calls=[], usage={})


def test_synthesizer_knowledge_fallback_carries_the_configured_model() -> None:
    client = _RecordingClient("model-knowledge answer")
    config = SearchConfig(model="kimi-k3")
    synthesizer = ResultSynthesizer(client, config)
    answer, _sources, _confidence = asyncio.run(
        synthesizer._llm_knowledge_fallback("what is the ridge defect class?")
    )
    assert answer.startswith("model-knowledge answer")  # honesty note appended
    assert client.calls[-1]["model"] == "kimi-k3"


def test_synthesizer_source_synthesis_carries_the_configured_model() -> None:
    client = _RecordingClient("[1] the answer with citation")
    config = SearchConfig(model="kimi-k3")
    synthesizer = ResultSynthesizer(client, config)
    from r4.search.core import Source, SourceType

    source = Source(
        type=SourceType.CATALOG_ENTRY,
        title="demo",
        content="demo body",
        relevance_score=0.9,
    )
    answer, _citations, _confidence, _warnings = asyncio.run(
        synthesizer.synthesize("query", [source])
    )
    assert "answer with citation" in answer
    assert client.calls[-1]["model"] == "kimi-k3"


def test_planner_llm_plan_carries_the_configured_model() -> None:
    client = _RecordingClient("[]")
    config = SearchConfig(model="kimi-k3", depth=SearchDepth.MODERATE, scope=SearchScope.ALL)
    planner = SearchPlanner(client, config)
    plan = asyncio.run(planner._llm_plan("characterize the defect class"))
    assert plan == []
    assert client.calls[-1]["model"] == "kimi-k3"


def test_absent_model_stays_absent_honestly() -> None:
    client = _RecordingClient("x")
    synthesizer = ResultSynthesizer(client, SearchConfig())
    asyncio.run(synthesizer._llm_knowledge_fallback("q"))
    assert client.calls[-1]["model"] is None


def test_tool_closure_threads_session_model_into_search_config(monkeypatch) -> None:
    """The full get_builtin_tools chain binds session_model into the tool."""
    import r4.companion.tools as tools_mod

    captured: dict = {}

    class _StubSearch:
        def __init__(self, client, cfg):
            captured["model"] = cfg.model

        async def search(self, query):
            return SimpleNamespace(
                answer="stub", confidence=1.0, warnings=[], citations=[], sources=[]
            )

    monkeypatch.setattr("r4.search.AgenticSearch", _StubSearch)
    client = SimpleNamespace(_config=SimpleNamespace(project_id="p1"))
    tools = tools_mod._client_lab_tools(client, session_model="kimi-k3")
    tool = next(t for t in tools if t["name"] == "search_agentic")
    result = tool["execute"](query="q", depth="shallow", scope="lab_only")
    assert "stub" in result
    assert captured["model"] == "kimi-k3"
