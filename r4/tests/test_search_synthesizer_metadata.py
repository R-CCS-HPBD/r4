"""Tests for search synthesizer catalog metadata in source context."""

from __future__ import annotations

from r4.search.core import Source, SourceType
from r4.search.synthesizer import ResultSynthesizer


def test_build_source_context_includes_catalog_metadata():
    synthesizer = ResultSynthesizer(client=None, config=None)  # type: ignore[arg-type]
    sources = [
        Source(
            type=SourceType.CATALOG_ENTRY,
            title="scan_001.dcm.png",
            content="Chest CT with bilateral infiltrates.",
            metadata={
                "s3_key": "proj-1/uploads/scan_001.dcm.png",
                "state": "ready",
                "detected_modality": "image",
                "tags": ["vision_captioned", "chest"],
                "deterministic_summary": "Image file scan_001.dcm.png",
                "perception_summary": "Chest CT with bilateral infiltrates.",
                "perception_model": "Kimi-K2.7",
            },
            relevance_score=2.5,
        )
    ]

    context = synthesizer._build_source_context(sources)

    assert "s3_key: proj-1/uploads/scan_001.dcm.png" in context
    assert "modality: image" in context
    assert "tags: vision_captioned, chest" in context
    assert "perception_summary: Chest CT with bilateral infiltrates." in context
    assert "relevance_score: 2.50" in context
    assert "Chest CT with bilateral infiltrates." in context


def test_source_to_detail_dict_exposes_catalog_fields():
    source = Source(
        type=SourceType.CATALOG_ENTRY,
        title="batch.csv",
        content="Yield results for batch 3.",
        metadata={
            "s3_key": "proj-1/data/batch.csv",
            "tags": ["col_a", "col_b"],
            "deterministic_summary": "columns: col_a, col_b",
            "perception_summary": "Batch 3 protein yield run.",
        },
        relevance_score=1.0,
    )

    detail = source.to_detail_dict()

    assert detail["s3_key"] == "proj-1/data/batch.csv"
    assert detail["tags"] == ["col_a", "col_b"]
    assert detail["deterministic_summary"] == "columns: col_a, col_b"
    assert detail["perception_summary"] == "Batch 3 protein yield run."
    assert detail["type"] == "catalog_entry"
