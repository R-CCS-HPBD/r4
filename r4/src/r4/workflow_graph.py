"""Workflow step-graph rendering for the CLI (U2).

Pure functions over the workflow ``describe`` payload (already available via
``WorkflowClient.describe``), producing:

  - ``layers()``   — topological levels respecting ``depends_on``
  - ``render_ascii`` — layered DAG view with status glyphs
  - ``render_mermaid`` — Mermaid ``flowchart TD`` source for docs/web

Client-side only: no new platform endpoints, no r4→UMRS contract changes.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

_STEP_ICON = {
    "mcp_call": "MCP",
    "umrs_job": "JOB",
    "rag_query": "RAG",
    "llm_prompt": "LLM",
    "shell_exec": "SH",
    "python_exec": "PY",
    "manual_gate": "GATE",
}

_STATUS_GLYPH = {
    "pending": "○",
    "waiting_approval": "⧗",
    "running": "◐",
    "completed": "●",
    "failed": "✗",
    "blocked": "▣",
    "skipped": "⊘",
}


class WorkflowGraphError(ValueError):
    """Raised when the step graph is malformed (cycles, dangling deps)."""


def _step_id(step: Mapping[str, Any], index: int) -> str:
    return str(step.get("id") or step.get("name") or f"step-{index}")


def _step_label(step: Mapping[str, Any]) -> str:
    return str(step.get("name") or step.get("id") or "step")


def _step_deps(step: Mapping[str, Any]) -> List[str]:
    deps = step.get("depends_on") or []
    return [str(d) for d in deps]


def _build_dag(
    steps: List[Mapping[str, Any]],
) -> tuple[List[str], Dict[int, List[int]], List[int]]:
    """Validate edges; return (ids, edges, indegree)."""
    ids = [_step_id(s, i) for i, s in enumerate(steps)]
    index_of = {sid: i for i, sid in enumerate(ids)}
    indegree = [0] * len(steps)
    edges: Dict[int, List[int]] = {i: [] for i in range(len(steps))}
    for i, step in enumerate(steps):
        for dep in _step_deps(step):
            if dep not in index_of:
                raise WorkflowGraphError(f"step {ids[i]!r} depends on unknown step {dep!r}")
            edges[index_of[dep]].append(i)
            indegree[i] += 1
    return ids, edges, indegree


def layers(steps: List[Mapping[str, Any]]) -> List[List[int]]:
    """Kahn's algorithm — steps grouped by dependency depth.

    Returns layers of *indices* into ``steps``. Raises
    :class:`WorkflowGraphError` on dangling edges or cycles.
    """
    ids, edges, indegree = _build_dag(steps)
    remaining = set(range(len(steps)))
    result: List[List[int]] = []
    frontier = [i for i in remaining if indegree[i] == 0]
    while frontier:
        result.append(sorted(frontier))
        frontier = _advance(frontier, edges, indegree, remaining)
    if remaining:
        cyclic = ", ".join(sorted(ids[i] for i in remaining))
        raise WorkflowGraphError(f"cyclic dependency among: {cyclic}")
    return result


def _advance(
    frontier: List[int],
    edges: Dict[int, List[int]],
    indegree: List[int],
    remaining: set,
) -> List[int]:
    """Release completed frontier; return newly unlocked step indices."""
    next_frontier: List[int] = []
    for i in frontier:
        remaining.discard(i)
        for j in edges[i]:
            indegree[j] -= 1
            if indegree[j] == 0 and j in remaining:
                next_frontier.append(j)
    return next_frontier


def render_ascii(
    steps: List[Mapping[str, Any]],
    statuses: Optional[Mapping[str, str]] = None,
    *,
    show_action: bool = True,
) -> str:
    """Layered ASCII DAG — dependency heads first.

    ``statuses`` maps step-id → StepStatus value; default glyph is pending.
    """
    if not steps:
        return "(workflow has no steps)"
    statuses = statuses or {}
    lines: List[str] = [
        _ascii_row(depth, _step_line(step, idx, statuses, show_action), idx, steps)
        for depth, layer in enumerate(layers(steps))
        for idx in layer
        for step in [steps[idx]]
    ]
    return "\n".join(lines)


def _ascii_row(depth: int, line: str, _idx: int, _steps: List[Mapping[str, Any]]) -> str:
    return f"L{depth} {line}"


def _step_line(
    step: Mapping[str, Any],
    idx: int,
    statuses: Mapping[str, str],
    show_action: bool,
) -> str:
    """One rendered DAG row (without the layer prefix)."""
    sid = _step_id(step, idx)
    glyph = _STATUS_GLYPH.get(statuses.get(sid, "pending"), "?")
    icon = _STEP_ICON.get(str(step.get("step_type", "")), "STEP")
    dep_txt = f" ← {', '.join(_step_deps(step))}" if _step_deps(step) else ""
    action_txt = f"  ({step.get('action', '')})" if show_action and step.get("action") else ""
    outputs = step.get("outputs") or []
    out_txt = f" ⇒ {len(outputs)} output(s)" if outputs else ""
    return f"{glyph} [{icon}] {_step_label(step)}{dep_txt}{action_txt}{out_txt}"


_MERMAID_DIRECTION = "TD"


def render_mermaid(
    steps: List[Mapping[str, Any]],
    *,
    title: str = "",
) -> str:
    """Mermaid flowchart — paste-friendly for docs and web views."""
    lines = [f"flowchart {_MERMAID_DIRECTION}"]
    for i, step in enumerate(steps):
        sid = _step_id(step, i)
        label = _step_label(step).replace('"', "'")
        icon = _STEP_ICON.get(str(step.get("step_type", "")), "STEP")
        shape_l, shape_r = ("[[", "]]") if icon == "GATE" else ("[", "]")
        lines.append(f'  {sid}{shape_l}"[{icon}] {label}"{shape_r}')
    for i, step in enumerate(steps):
        sid = _step_id(step, i)
        for dep in _step_deps(step):
            lines.append(f"  {dep} --> {sid}")
    if title:
        lines.append(f"  %% {title}")
    return "\n".join(lines)


def describe_status_map(execution_steps: Iterable[Mapping[str, Any]]) -> Dict[str, str]:
    """Extract ``{step_id: status}`` from an execution-status payload."""
    return {
        str(s.get("id") or s.get("name", "")): str(s.get("status", "pending"))
        for s in execution_steps
        if isinstance(s, Mapping)
    }
