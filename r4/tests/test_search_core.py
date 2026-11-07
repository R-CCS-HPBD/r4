"""Tests for ``r4.search`` module — core types, iteration, planner, trace, pipeline contracts.

Covers:
- SearchConfig / SearchResult dataclass contracts
- SearchScope / SearchDepth / SourceType enums
- Source serialization
- IterationConfig / IterationState / IterationDecision
- Gap detection data model
- TraceEvent / TraceCollector lifecycle
- ToolResult / ToolStatus
- SearchStep / StepType
- Pipeline construction and event wiring
"""

from __future__ import annotations

from datetime import datetime

# ── Core type tests ─────────────────────────────────────────────────────────


class TestSearchEnums:
    """Verify search enums have expected values."""

    def test_search_scope_values(self):
        from r4.search.core import SearchScope

        assert SearchScope.RAG_ONLY.value == "rag_only"
        assert SearchScope.LAB_ONLY.value == "lab_only"
        assert SearchScope.ALL.value == "all"

    def test_search_depth_values(self):
        from r4.search.core import SearchDepth

        assert SearchDepth.SHALLOW.value == "shallow"
        assert SearchDepth.MODERATE.value == "moderate"
        assert SearchDepth.DEEP.value == "deep"

    def test_source_type_values(self):
        from r4.search.core import SourceType

        assert SourceType.RAG_DOCUMENT.value == "rag_document"
        assert SourceType.WEB_PAGE.value == "web_page"
        assert SourceType.CODE_FILE.value == "code_file"


class TestSearchConfig:
    """SearchConfig dataclass defaults and serialization."""

    def test_defaults(self):
        from r4.search.core import SearchConfig, SearchDepth, SearchScope

        config = SearchConfig()
        assert config.scope == SearchScope.ALL
        assert config.depth == SearchDepth.MODERATE
        assert config.max_steps == 5
        assert config.include_citations is True
        assert config.enable_web is True
        assert config.enable_rag is True

    def test_to_dict(self):
        from r4.search.core import SearchConfig

        d = SearchConfig().to_dict()
        assert d["scope"] == "all"
        assert d["depth"] == "moderate"
        assert isinstance(d["max_steps"], int)

    def test_custom_config(self):
        from r4.search.core import SearchConfig, SearchDepth, SearchScope

        config = SearchConfig(
            scope=SearchScope.CODE_ONLY,
            depth=SearchDepth.DEEP,
            max_steps=10,
            enable_web=False,
            project_id="proj-123",
        )
        assert config.scope == SearchScope.CODE_ONLY
        assert config.max_steps == 10
        assert config.enable_web is False
        assert config.project_id == "proj-123"

    def test_lab_only_scope_disables_external_sources(self):
        from r4.search.core import SearchConfig, SearchScope

        config = SearchConfig(
            scope=SearchScope.LAB_ONLY,
            enable_web=True,
            enable_code=True,
            enable_papers=True,
        )
        config.apply_scope()

        assert config.enable_catalog is True
        assert config.enable_raw_read is True
        assert config.enable_rag is True
        assert config.enable_web is False
        assert config.enable_code is False
        assert config.enable_papers is False

    def test_all_scope_enables_lab_tools(self):
        from r4.search.core import SearchConfig, SearchScope

        config = SearchConfig(scope=SearchScope.ALL)
        config.apply_scope()

        assert config.enable_catalog is True
        assert config.enable_raw_read is True
        assert config.enable_rag is True
        assert config.enable_web is True
        assert config.enable_code is True


class TestSource:
    """Source data model."""

    def test_source_to_dict(self):
        from r4.search.core import Source, SourceType

        src = Source(
            type=SourceType.WEB_PAGE,
            title="Example",
            url="https://example.com",
            content="Hello world " * 100,
            relevance_score=0.9,
        )
        d = src.to_dict()
        assert d["type"] == "web_page"
        assert d["title"] == "Example"
        assert d["url"] == "https://example.com"
        assert d["relevance_score"] == 0.9
        # Content truncated at 500 chars
        assert len(d["content"]) <= 503  # 500 + "..."

    def test_source_without_url(self):
        from r4.search.core import Source, SourceType

        src = Source(type=SourceType.RAG_DOCUMENT, title="Internal doc")
        d = src.to_dict()
        assert d["url"] is None


class TestSearchResult:
    """SearchResult data model."""

    def test_result_to_dict(self):
        from r4.search.core import SearchResult

        result = SearchResult(
            query="test query",
            answer="test answer",
            confidence=0.85,
            search_steps=3,
            tokens_used=500,
        )
        d = result.to_dict()
        assert d["query"] == "test query"
        assert d["confidence"] == 0.85
        assert d["search_steps"] == 3

    def test_result_format_with_citations(self):
        from r4.search.core import SearchResult, Source, SourceType

        result = SearchResult(
            query="q",
            answer="The answer is 42.",
            sources=[
                Source(
                    type=SourceType.WEB_PAGE,
                    title="Source A",
                    url="https://a.com",
                ),
                Source(
                    type=SourceType.RAG_DOCUMENT,
                    title="Internal B",
                ),
            ],
        )
        formatted = result.format_with_citations()
        assert "The answer is 42." in formatted
        assert "[1]" in formatted
        assert "[2]" in formatted
        assert "Source A" in formatted
        assert "Internal B" in formatted

    def test_result_no_sources_no_citations(self):
        from r4.search.core import SearchResult

        result = SearchResult(query="q", answer="No data.")
        formatted = result.format_with_citations()
        assert "Sources" not in formatted

    def test_result_warnings(self):
        from r4.search.core import SearchResult

        result = SearchResult(
            query="q",
            answer="partial",
            warnings=["Low confidence", "Missing sources"],
        )
        assert len(result.warnings) == 2


# ── Iteration controller tests ─────────────────────────────────────────────


class TestIterationModels:
    """A-909: Iteration control data models."""

    def test_gap_to_dict(self):
        from r4.search.iteration import Gap

        gap = Gap(
            description="Missing details about GPU memory",
            importance=0.8,
            suggested_query="GPU memory management techniques",
        )
        d = gap.to_dict()
        assert d["importance"] == 0.8
        assert d["suggested_query"] == "GPU memory management techniques"

    def test_iteration_decision_no_iterate(self):
        from r4.search.iteration import IterationDecision

        decision = IterationDecision(
            should_iterate=False,
            confidence=0.9,
            explanation="Sufficient coverage",
        )
        assert not decision.should_iterate
        d = decision.to_dict()
        assert d["confidence"] == 0.9
        assert d["reason"] is None

    def test_iteration_decision_iterate(self):
        from r4.search.iteration import Gap, IterationDecision, IterationReason

        gap = Gap(description="Missing X", importance=0.7)
        decision = IterationDecision(
            should_iterate=True,
            reason=IterationReason.GAP_DETECTED,
            gaps=[gap],
            confidence=0.4,
        )
        assert decision.should_iterate
        d = decision.to_dict()
        assert d["reason"] == "gap_detected"
        assert len(d["gaps"]) == 1

    def test_iteration_config_defaults(self):
        from r4.search.iteration import IterationConfig

        config = IterationConfig()
        assert config.max_iterations == 10
        assert config.min_confidence == 0.7
        assert config.gap_importance_threshold == 0.5

    def test_iteration_state_tracking(self):
        from r4.search.iteration import IterationState

        state = IterationState()
        assert state.iteration_number == 0
        assert state.total_queries == 0
        assert state.no_improvement_count == 0

        state.iteration_number += 1
        state.total_queries += 3
        state.confidence_history.append(0.5)
        assert state.iteration_number == 1
        assert len(state.confidence_history) == 1

    def test_iteration_reason_values(self):
        from r4.search.iteration import IterationReason

        assert IterationReason.GAP_DETECTED.value == "gap_detected"
        assert IterationReason.LOW_CONFIDENCE.value == "low_confidence"
        assert IterationReason.CONFLICTING_INFO.value == "conflicting"


# ── Planner types tests ────────────────────────────────────────────────────


class TestSearchStep:
    """A-902: Search step data model."""

    def test_step_to_dict(self):
        from r4.search.planner import SearchStep, StepType

        step = SearchStep(
            step_type=StepType.RAG_QUERY,
            query="internal docs about auth",
            reasoning="Check internal docs first",
            order=0,
        )
        d = step.to_dict()
        assert d["step_type"] == "rag_query"
        assert d["query"] == "internal docs about auth"
        assert d["order"] == 0

    def test_step_type_values(self):
        from r4.search.planner import StepType

        assert StepType.RAG_QUERY.value == "rag_query"
        assert StepType.LAB_CATALOG_SEARCH.value == "lab_catalog_search"
        assert StepType.LAB_FILE_READ.value == "lab_file_read"
        assert StepType.WEB_SEARCH.value == "web_search"
        assert StepType.CODE_SEARCH.value == "code_search"
        assert StepType.SYNTHESIZE.value == "synthesize"

    def test_step_with_dependencies(self):
        from r4.search.planner import SearchStep, StepType

        step = SearchStep(
            step_type=StepType.FOLLOW_UP,
            query="follow up on previous results",
            depends_on=[0, 1],
        )
        assert step.depends_on == [0, 1]


# ── Executor types tests ───────────────────────────────────────────────────


class TestToolResult:
    """A-903: Tool result data model."""

    def test_tool_status_values(self):
        from r4.search.executor import ToolStatus

        assert ToolStatus.SUCCESS.value == "success"
        assert ToolStatus.FAILED.value == "failed"
        assert ToolStatus.SKIPPED.value == "skipped"

    def test_tool_result_creation(self):
        from r4.search.executor import ToolResult, ToolStatus

        result = ToolResult(
            status=ToolStatus.SUCCESS,
            sources=[],
            message="Found 3 results",
        )
        assert result.status == ToolStatus.SUCCESS
        assert result.error is None

    def test_tool_result_failure(self):
        from r4.search.executor import ToolResult, ToolStatus

        result = ToolResult(
            status=ToolStatus.FAILED,
            sources=[],
            error="Connection timeout",
        )
        assert result.status == ToolStatus.FAILED
        assert result.error == "Connection timeout"


# ── Trace tests ────────────────────────────────────────────────────────────


class TestTraceModels:
    """A-911: Trace data models."""

    def test_trace_event_type_values(self):
        from r4.search.trace import TraceEventType

        assert TraceEventType.SEARCH_START.value == "search_start"
        assert TraceEventType.TOOL_CALL_START.value == "tool_call_start"
        assert TraceEventType.GAP_DETECTED.value == "gap_detected"

    def test_trace_event_to_dict(self):
        from r4.search.trace import TraceEvent, TraceEventType

        event = TraceEvent(
            event_type=TraceEventType.SEARCH_START,
            timestamp=datetime(2026, 1, 1, 12, 0, 0),
            message="Starting search",
            data={"query": "test"},
        )
        d = event.to_dict()
        assert d["event_type"] == "search_start"
        assert d["message"] == "Starting search"
        assert d["data"]["query"] == "test"

    def test_trace_event_auto_id(self):
        from r4.search.trace import TraceEvent, TraceEventType

        event = TraceEvent(
            event_type=TraceEventType.INFO,
            timestamp=datetime(2026, 1, 1),
            message="test",
        )
        assert event.event_id  # Should be auto-generated

    def test_tool_call_trace(self):
        from r4.search.trace import ToolCallTrace

        trace = ToolCallTrace(
            tool_name="rag_search",
            arguments={"query": "auth docs"},
            result={"sources": []},
        )
        assert trace.tool_name == "rag_search"
        assert trace.error is None

    def test_create_tracer(self):
        from r4.search.trace import create_tracer

        tracer = create_tracer(session_id="test-session", query="test query")
        assert tracer is not None

    def test_tracer_records_events(self):
        from r4.search.trace import TraceEventType, create_tracer

        tracer = create_tracer(session_id="test-session", query="test query")
        tracer.event(TraceEventType.SEARCH_START, "begin search")
        tracer.event(TraceEventType.SEARCH_END, "done")
        events = tracer.trace.events
        assert len(events) == 2
        assert events[0].event_type == TraceEventType.SEARCH_START


# ── Synthesizer types tests ────────────────────────────────────────────────


class TestSynthesizerModels:
    """A-904: Synthesizer data models."""

    def test_citation_creation(self):
        from r4.search.synthesizer import Citation

        citation = Citation(
            index=0,
            source_title="Example",
            source_url="https://example.com",
            quote="referenced text",
        )
        assert citation.index == 0
        assert citation.source_title == "Example"

    def test_citation_format_inline(self):
        from r4.search.synthesizer import Citation

        citation = Citation(index=1, source_title="A", source_url=None)
        assert citation.format_inline() == "[1]"

    def test_citation_format_reference_with_url(self):
        from r4.search.synthesizer import Citation

        citation = Citation(index=1, source_title="Docs", source_url="https://docs.example.com")
        ref = citation.format_reference()
        assert "[1]" in ref
        assert "Docs" in ref
        assert "https://docs.example.com" in ref

    def test_citation_format_reference_without_url(self):
        from r4.search.synthesizer import Citation

        citation = Citation(index=2, source_title="Internal", source_url=None)
        ref = citation.format_reference()
        assert "[2]" in ref
        assert "Internal" in ref

    def test_citation_to_dict(self):
        from r4.search.synthesizer import Citation

        citation = Citation(index=0, source_title="A", source_url="https://a.com")
        d = citation.to_dict()
        assert d["index"] == 0
        assert d["source_title"] == "A"
