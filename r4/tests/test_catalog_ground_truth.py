"""Tests for catalog ground-truth auto-read helpers."""

from r4.search.catalog_ground_truth import enrich_plan_with_raw_reads, query_needs_ground_truth
from r4.search.planner import SearchStep, StepType


def test_query_needs_ground_truth_detects_columns() -> None:
    assert query_needs_ground_truth("What columns are in batch-3?")
    assert not query_needs_ground_truth("Summarize the project")


def test_enrich_plan_adds_lab_file_read_for_ground_truth_query() -> None:
    plan = [
        SearchStep(
            step_type=StepType.LAB_CATALOG_SEARCH,
            query="batch-3 columns",
            order=0,
        )
    ]
    enriched = enrich_plan_with_raw_reads(
        plan,
        "What columns are in batch-3 CSV?",
        enable_raw_read=True,
    )
    step_types = [step.step_type for step in enriched]
    assert StepType.LAB_CATALOG_SEARCH in step_types
    assert StepType.LAB_FILE_READ in step_types


def test_enrich_plan_skips_when_raw_read_disabled() -> None:
    plan = [
        SearchStep(
            step_type=StepType.LAB_CATALOG_SEARCH,
            query="batch-3 columns",
            order=0,
        )
    ]
    enriched = enrich_plan_with_raw_reads(
        plan,
        "What columns are in batch-3 CSV?",
        enable_raw_read=False,
    )
    assert len(enriched) == 1
