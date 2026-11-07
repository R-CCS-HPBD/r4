"""Tests for r4.workflow_graph (U2 step-DAG rendering)."""

from __future__ import annotations

import pytest

from r4.workflow_graph import (
    WorkflowGraphError,
    describe_status_map,
    layers,
    render_ascii,
    render_mermaid,
)


def _steps():
    return [
        {"id": "a", "name": "ingest", "step_type": "umrs_job", "action": "ingest"},
        {
            "id": "b",
            "name": "extract",
            "step_type": "llm_prompt",
            "depends_on": ["a"],
        },
        {
            "id": "c",
            "name": "approve",
            "step_type": "manual_gate",
            "depends_on": ["b"],
            "outputs": [{"name": "signoff"}],
        },
        {"id": "d", "name": "notify", "step_type": "mcp_call", "depends_on": ["a"]},
    ]


def test_layers_topological_order():
    assert layers(_steps()) == [[0], [1, 3], [2]]


def test_layers_empty():
    assert layers([]) == []


def test_layers_dangling_dep_raises():
    with pytest.raises(WorkflowGraphError, match="unknown step"):
        layers([{"id": "a", "depends_on": "missing"}])  # type: ignore[list-item]


def test_layers_cycle_detected():
    with pytest.raises(WorkflowGraphError, match="cyclic"):
        layers(
            [
                {"id": "a", "depends_on": ["b"]},
                {"id": "b", "depends_on": ["a"]},
            ]
        )


def test_render_ascii_layers_icons_outputs():
    out = render_ascii(_steps())
    lines = out.splitlines()
    assert lines[0].startswith("L0 ○ [JOB] ingest")
    assert "L1 ○ [LLM] extract ← a" in lines[1]
    assert "L1 ○ [MCP] notify ← a" in lines[2]
    assert "[GATE] approve ← b" in lines[3]
    assert "⇒ 1 output(s)" in lines[3]


def test_render_ascii_statuses_change_glyphs():
    out = render_ascii(_steps(), statuses={"a": "completed", "b": "running", "c": "failed"})
    assert "L0 ● [JOB] ingest" in out
    assert "◐ [LLM] extract" in out
    assert "✗ [GATE] approve" in out


def test_render_ascii_no_steps():
    assert "no steps" in render_ascii([])


def test_render_mermaid_nodes_edges_gate_shape():
    out = render_mermaid(_steps(), title="demo")
    assert out.startswith("flowchart TD")
    assert 'a["[JOB] ingest"]' in out
    assert 'c[["[GATE] approve"]]' in out
    assert "a --> b" in out
    assert "a --> d" in out
    assert "b --> c" in out


def test_describe_status_map():
    payload = [
        {"id": "a", "status": "completed"},
        {"id": "b", "status": "running"},
        "garbage-row",
    ]
    assert describe_status_map(payload) == {"a": "completed", "b": "running"}
