"""Helpers for when agentic search must read lab file content."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .planner import SearchStep

_GROUND_TRUTH_PHRASES = (
    "column",
    "columns",
    "value",
    "values",
    "exact",
    "highest",
    "lowest",
    "how many",
    "count",
    "measure",
    "measured",
    "uptake",
    "yield",
    "read the",
    "show me the",
    "what is the",
    "what's the",
    "how much",
    "total",
    "average",
    "mean",
    "median",
)

_RAW_READ_MODALITIES = frozenset({"structured", "notebook", "code", "text", "document"})

_TABULAR_GROUND_TRUTH_PHRASES = (
    "column",
    "columns",
    "value",
    "values",
    "highest",
    "lowest",
    "how many",
    "count",
    "uptake",
    "yield",
    "how much",
    "total",
    "average",
    "mean",
    "median",
)

_TABULAR_RAW_MODALITIES = frozenset({"structured", "notebook", "code"})

_MAX_AUTO_RAW_READS = 2


def query_needs_ground_truth(query: str) -> bool:
    """True when the user question likely needs file content, not metadata."""
    lowered = query.lower()
    return any(phrase in lowered for phrase in _GROUND_TRUTH_PHRASES)


def query_needs_tabular_ground_truth(query: str) -> bool:
    """True when the question likely needs CSV/structured bytes, not documents."""
    lowered = query.lower()
    return any(phrase in lowered for phrase in _TABULAR_GROUND_TRUTH_PHRASES)


def catalog_hit_needs_raw_read(modality: str | None) -> bool:
    """True when a catalog hit modality should trigger an automatic file read."""
    return (modality or "") in _RAW_READ_MODALITIES


def pick_catalog_keys_for_raw_read(
    catalog_sources: list,
    *,
    query: str,
    already_read: set[str],
    max_reads: int = _MAX_AUTO_RAW_READS,
) -> list[str]:
    """Choose s3_keys from catalog search hits for follow-up lab_file_read."""
    if not query_needs_ground_truth(query):
        return []

    ranked: list[tuple[float, str, str]] = []
    for source in catalog_sources:
        meta = source.metadata or {}
        s3_key = meta.get("s3_key")
        if not s3_key or s3_key in already_read:
            continue
        modality = meta.get("detected_modality", "")
        if not catalog_hit_needs_raw_read(modality):
            continue
        ranked.append((float(source.relevance_score or 0.0), s3_key, modality))

    if query_needs_tabular_ground_truth(query):
        tabular = [item for item in ranked if item[2] in _TABULAR_RAW_MODALITIES]
        if tabular:
            ranked = tabular

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [key for _, key, _ in ranked[:max_reads]]


def enrich_plan_with_raw_reads(
    plan: list["SearchStep"],
    query: str,
    *,
    enable_raw_read: bool,
) -> list["SearchStep"]:
    """Append a placeholder lab_file_read step when the plan has catalog but no file read."""
    if not enable_raw_read or not query_needs_ground_truth(query):
        return plan

    from .planner import SearchStep, StepType

    has_catalog = any(step.step_type == StepType.LAB_CATALOG_SEARCH for step in plan)
    has_raw = any(step.step_type == StepType.LAB_FILE_READ for step in plan)
    if not has_catalog or has_raw:
        return plan

    order = max((step.order for step in plan), default=-1) + 1
    enriched = list(plan)
    enriched.append(
        SearchStep(
            step_type=StepType.LAB_FILE_READ,
            query="",
            reasoning="Ground-truth read for catalog hits (s3_key resolved at runtime)",
            parameters={"auto": True},
            order=order,
        )
    )
    return enriched
