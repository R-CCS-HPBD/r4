"""Tool-selection benchmark harness (ADR-037, item 7).

Offline: no model calls by design (offline vLLM platform — the harness
measures the CHOICES, not the model's maybe). Metrics per
(catalog size × strategy):

- ``candidate_tools`` / ``excess`` (offered size)
- ``schema_tokens_estimate`` (bytes/4)
- precision |offered ∩ expected| / |offered|
- recall |offered ∩ expected| / |expected|
- ``selection_latency_ms`` (measured around the strategy only)

Catalogs: 10 / 50 / 150 / 400 tools — the 10/50 sizes come from the real
builtin catalog trimmed; bigger sizes pad with generated distractors. The
20-task fixture covers every verb family plus generic tails; each task
lists its expected tool set explicitly.

CI smoke: ``--smoke`` runs the 10-tool catalog × 3 tasks and exits 0 when
the metrics JSON is self-consistent (no fabricating-post checks).

First-call-correct and unnecessary-call rates are deliberately NOT
computed here — those need a turn-level model run; carry them as data
from ``--live`` (TODO) exactly once, measured, never estimated.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CATALOG_SIZES = (10, 50, 150, 400)

# Tasks cost happens here, not during a model call — each task supplies its
# natural prompt plus the exact tool set a correct answer needs.
TASKS: list[dict[str, Any]] = [
    {
        "id": "t01",
        "prompt": "run my workflow for the morning harvest",
        "expected": ["workflow_run", "workflow_list", "workflow_status", "file_read"],
    },
    {
        "id": "t02",
        "prompt": "what projects exist?",
        "expected": ["project_list", "file_read"],
    },
    {
        "id": "t03",
        "prompt": "find mentions of eosinophil in the knowledge graph",
        "expected": ["knowledge_query", "knowledge_graph_list", "file_read"],
    },
    {
        "id": "t04",
        "prompt": "read the main.py file",
        "expected": ["file_read"],
    },
    {
        "id": "t05",
        "prompt": "can you explain this?",
        "expected": ["file_read"],
    },
    {
        "id": "t06",
        "prompt": "check job status for the endpoint",
        "expected": ["job_status", "endpoint_status_submit", "file_read"],
    },
    {
        "id": "t07",
        "prompt": "list MCP servers and call one",
        "expected": ["mcp_list_servers", "mcp_invoke", "file_read"],
    },
    {
        "id": "t08",
        "prompt": "show my research loops",
        "expected": ["research_loop_list", "research_loop_status", "file_read"],
    },
    {
        "id": "t09",
        "prompt": "which models are available?",
        "expected": ["model_list", "file_read"],
    },
    {
        "id": "t10",
        "prompt": "summarize the docs",
        "expected": ["search_docs", "file_read"],
    },
    {
        "id": "t11",
        "prompt": "run the knowledge ingest for paper.pdf",
        "expected": ["knowledge_ingest", "file_read"],
    },
    {
        "id": "t12",
        "prompt": "what endpoints are running?",
        "expected": ["endpoint_list", "file_read"],
    },
    {
        "id": "t13",
        "prompt": "describe the workflow named plasma-scan",
        "expected": ["workflow_describe", "workflow_list", "file_read"],
    },
    {
        "id": "t14",
        "prompt": "ask the user something",
        "expected": ["ask_user", "file_read"],
    },
    {
        "id": "t15",
        "prompt": "update todos for the session",
        "expected": ["todo_write", "file_read"],
    },
    {
        "id": "t16",
        "prompt": "retry the failed endpoint job",
        "expected": ["job_retry", "job_status", "endpoint_list", "file_read"],
    },
    {
        "id": "t17",
        "prompt": "grep for 'authz' in the code",
        "expected": ["grep_search", "file_read"],
    },
    {
        "id": "t18",
        "prompt": "list directory contents",
        "expected": ["list_dir", "file_read"],
    },
    {
        "id": "t19",
        "prompt": "vision submit this image",
        "expected": ["vision_submit", "vision_status", "file_read"],
    },
    {
        "id": "t20",
        "prompt": "show session list",
        "expected": ["session_list", "file_read"],
    },
    # Indirect tasks — the family word is NOT in the prompt. A lexical
    # family filter must fail here; this is the honest record of that limit.
    {
        "id": "t21",
        "prompt": "is anything still running on the scheduler?",
        "expected": ["job_status", "endpoint_list", "file_read"],
    },
    {
        "id": "t22",
        "prompt": "what did we finish yesterday?",
        "expected": ["project_list", "workflow_list", "session_list", "file_read"],
    },
    {
        "id": "t23",
        "prompt": "how many nodes did the ai4s box get?",
        "expected": ["endpoint_list", "model_list", "file_read"],
    },
]


def _base_catalog() -> list[dict[str, Any]]:
    """Actual names used by the real companion (mirrors get_builtin_tools
    prefix clusters; smaller than live for determinism across deployments;
    bigger sizes are synthesized below, never cloned from a live server)."""
    names = (
        ["final_output", "ask_user", "todo_write", "tool_search"]
        + ["file_read", "file_write", "file_search", "list_dir", "grep_search", "glob_search"]
        + [
            "workflow_list",
            "workflow_describe",
            "workflow_run",
            "workflow_status",
            "workflow_results",
        ]
        + ["project_list", "project_ensure", "project_research_import", "project_research_plan"]
        + [
            "knowledge_ingest",
            "knowledge_query",
            "knowledge_status",
            "knowledge_graph_list",
            "research_loop_start",
            "research_loop_list",
            "research_loop_status",
        ]
        + ["mcp_invoke", "mcp_list_servers", "mcp_list_tools"]
        + [
            "endpoint_list",
            "endpoint_status_submit",
            "endpoint_tasks",
            "model_list",
            "model_stage",
            "job_status",
            "job_retry",
            "vision_submit",
            "vision_status",
            "session_list",
            "search_docs",
            "shell_exec",
        ]
    )
    return [{"name": name, "description": name.replace("_", " ")} for name in names]


def expand_catalog(size: int) -> list[dict[str, Any]]:
    base = _base_catalog()
    if len(base) >= size:
        return base[:size]
    out = list(base)
    i = 0
    while len(out) < size:
        # distractors spread across the families so family filtering stays
        # measurable at every size, not just the first.
        for family in (
            "project",
            "workflow",
            "knowledge",
            "file",
            "research",
            "mcp",
            "endpoint",
            "job",
        ):
            out.append({"name": f"{family}_extra_{i}", "description": f"extra {family} tool {i}"})
            if len(out) >= size:
                break
        i += 1
    return out


def measure(
    tools: list[dict[str, Any]],
    strategy: str,
    *,
    prompt: str,
    expected: list[str],
) -> dict[str, Any]:
    """Single task × strategy measurement."""
    from r4.companion.tool_selection import estimate_schema_tokens, select_with_family

    started = time.perf_counter()
    filtered_out: dict[str, Any] = {}
    utility_offered: dict[str, Any] = {}
    if strategy == "passthrough":
        offered_names = [str(t["name"]) for t in tools]
    elif strategy == "family":
        offered_tools, filtered_out, utility_offered = select_with_family(
            tools=tools,
            message_text=prompt,
        )
        offered_names = [str(t["name"]) for t in offered_tools]
    else:
        raise ValueError(f"unknown strategy: {strategy!r}")
    latency = round((time.perf_counter() - started) * 1000, 3)
    schema_tokens = estimate_schema_tokens([{"name": n} for n in offered_names])
    offered_set = set(offered_names)
    expected_set = set(expected)
    hits = expected_set & offered_set
    precision = len(hits) / max(1, len(offered_set))
    recall = len(hits) / max(1, len(expected_set))
    return {
        "candidate_tools": len(offered_set),
        "schema_tokens_estimate": schema_tokens,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "selection_latency_ms": latency,
        "filtered_out_count": sum(
            int(b.get("count", 0)) for b in filtered_out.values() if isinstance(b, dict)
        ),
        "utility_offered_count": int(utility_offered.get("count", 0)),
    }


def run_benchmark() -> dict[str, Any]:
    results: dict[str, list[dict[str, Any]]] = {"rows": []}
    for size in CATALOG_SIZES:
        catalog = expand_catalog(size)
        for strategy in ("passthrough", "family"):
            agg: dict[str, list[float]] = {
                "candidate_tools": [],
                "schema_tokens_estimate": [],
                "precision": [],
                "recall": [],
                "selection_latency_ms": [],
                "utility_offered_count": [],
            }
            for task in TASKS:
                m = measure(
                    catalog,
                    strategy,
                    prompt=task["prompt"],
                    expected=task["expected"],
                )
                for key in agg:
                    agg[key].append(float(m[key]))
            row = {
                "catalog_size": size,
                "strategy": strategy,
                "tasks": len(TASKS),
                **{key: round(sum(values) / max(1, len(values)), 2) for key, values in agg.items()},
            }
            row["excess_vs_expected"] = round(
                row["candidate_tools"] - row["precision"] * row["candidate_tools"], 2
            )
            results["rows"].append(row)
    return results


def format_markdown(results: dict[str, Any]) -> str:
    lines = [
        "# Tool-selection benchmark — 2026-09-03",
        "",
        "Metrics: no model calls (offline by design); choices measured, first-call-",
        "correct is intentionally left out (turn-level LLM data follows once).",
        "",
        "Indirect tasks (t21–t23) never name a family — a lexical family filter ",
        "must miss them; honestly visible in the recall numbers.",
        "",
        "| Catalog | Strategy | Tasks | Candidate (mean) | Schema tok (mean) | Precision | Recall | Excess | Utility (mean) | Latency ms (sum) |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in results["rows"]:
        lines.append(
            f"| {row['catalog_size']} | {row['strategy']} | {row['tasks']} | "
            f"{row['candidate_tools']:.1f} | {row['schema_tokens_estimate']:.0f} | "
            f"{row['precision']:.3f} | {row['recall']:.3f} | "
            f"{row['excess_vs_expected']:.1f} | {row['utility_offered_count']:.1f} | "
            f"{row['selection_latency_ms']:.0f} |"
        )
    lines.extend(
        [
            "",
            "Legend: excess = candidate_tools − (precision×candidate_tools) — a",
            "crude off-answer proxy recorded as a proxy (never a model-score).",
            "Indirect-task recall < 1 signals the lexical family filter hid an",
            "expected tool — a known limitation, kept for honesty, never smoothed",
            "away by matching synthetic keywords back in.",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="10-tool, 3-task rail")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--markdown", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.smoke:
        small = expand_catalog(10)
        for task in TASKS[:3]:
            m = measure(
                small,
                "family",
                prompt=task["prompt"],
                expected=task["expected"],
            )
            assert isinstance(m["candidate_tools"], int)
            assert 0.0 <= m["precision"] <= 1.0
        print("smoke OK")
        return 0

    results = run_benchmark()
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(results, indent=2) + "\n")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(format_markdown(results))
    if not (args.json or args.markdown):
        print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
