"""Contracts-layer taint DTO tests (stdib-only surface, ADR-005/ADR-039)."""

from __future__ import annotations

from r4_contracts.taint import (
    MAX_EXCERPTS_PER_RESULT,
    MIN_TAINT_EXCERPT_LEN,
    TAINT_CLASSES,
    TOOL_TAINT_CLASSES,
    TOOL_TAINT_PREFIXES,
    TaintedSegment,
    taint_class_for_tool,
)


def test_dto_holds_provenance() -> None:
    segment = TaintedSegment(
        text_excerpt="x" * 100,
        taint_class="rag_chunk",
        source_tool="knowledge_query",
        registered_at=0.0,
    )
    assert segment.taint_class == "rag_chunk"
    assert segment.source_tool == "knowledge_query"


def test_taint_class_map_only_uses_declared_classes() -> None:
    for taint_class in TOOL_TAINT_CLASSES.values():
        assert taint_class in TAINT_CLASSES


def test_bounds_are_sane() -> None:
    assert MIN_TAINT_EXCERPT_LEN >= 24
    assert MAX_EXCERPTS_PER_RESULT >= 1


def test_prefix_classes_are_declared() -> None:
    for _prefix, taint_class in TOOL_TAINT_PREFIXES:
        assert taint_class in TAINT_CLASSES


def test_first_class_mcp_names_classify_as_mcp_output() -> None:
    assert taint_class_for_tool("mcp.labdata.search_database") == "mcp_output"
    assert taint_class_for_tool("mcp_invoke") == "mcp_output"
    assert taint_class_for_tool("web_fetch") == "web"


def test_unknown_and_bare_prefix_names_do_not_classify() -> None:
    assert taint_class_for_tool("file_read") is None
    assert taint_class_for_tool("mcp.") is None
    assert taint_class_for_tool("mcp_refresh") is None
