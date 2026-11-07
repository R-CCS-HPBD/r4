"""Tool-selection benchmark rail — metrics stay honest without model calls."""

from __future__ import annotations

import tools.bench_tool_selection as bench


def test_catalog_expand_size_and_families():
    big = bench.expand_catalog(150)
    assert len(big) == 150
    families = {name["name"].split("_", 1)[0] for name in big}
    assert {"project", "workflow", "knowledge", "file", "job"} <= families


def test_passthrough_reports_full_catalog():
    catalog = bench.expand_catalog(50)
    task = bench.TASKS[0]
    m = bench.measure(catalog, "passthrough", prompt=task["prompt"], expected=task["expected"])
    assert m["candidate_tools"] == 50
    assert m["filtered_out_count"] == 0


def test_family_filters_and_stays_measured():
    catalog = bench.expand_catalog(150)
    task = bench.TASKS[0]  # workflow prompt → workflow family
    m = bench.measure(catalog, "family", prompt=task["prompt"], expected=task["expected"])
    assert 0 < m["candidate_tools"] < len(catalog)
    assert m["precision"] > 0
    assert m["filtered_out_count"] > 0


def test_indirect_task_honestly_drops_recall():
    catalog = bench.expand_catalog(150)
    indirect = next(task for task in bench.TASKS if task["id"] == "t21")
    m = bench.measure(catalog, "family", prompt=indirect["prompt"], expected=indirect["expected"])
    # t21 never names 'job' or 'endpoint' — family can't route there; recall
    # must be < 1, recorded, never smoothed over.
    assert m["recall"] < 1.0


def test_unknown_strategy_raises():
    import pytest

    with pytest.raises(ValueError, match="unknown strategy"):
        bench.measure(bench.expand_catalog(10), "quantum_pick", prompt="x", expected=["file_read"])


def test_markdown_table_shape():
    results = {
        "rows": [
            {
                "catalog_size": 10,
                "strategy": "family",
                "tasks": 1,
                "candidate_tools": 5.0,
                "schema_tokens_estimate": 100,
                "precision": 0.5,
                "recall": 0.5,
                "excess_vs_expected": 2.5,
                "selection_latency_ms": 0,
                "utility_offered_count": 2.0,
            }
        ]
    }
    md = bench.format_markdown(results)
    assert "| Catalog | Strategy |" in md
    assert "2026-09-03" in md
    assert "never smoothed" in md


def test_smoke_main_run():
    assert bench.main(["--smoke"]) == 0
