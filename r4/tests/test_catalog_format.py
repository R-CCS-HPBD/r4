"""Tests for shared catalog hit formatting."""

from r4.search.catalog_format import (
    format_catalog_entry,
    format_catalog_search_response,
    format_lab_file_read_response,
    map_read_as_to_source,
    suggest_read,
)


def test_format_catalog_entry_includes_suggested_read() -> None:
    entry = format_catalog_entry(
        {
            "s3_key": "proj-1/uploads/batch.csv",
            "state": "ready",
            "summary": "CSV columns: a, b",
            "deterministic_summary": "columns: a, b",
            "perception_summary": "hint",
            "perception_model": "model-x",
            "detected_modality": "structured",
            "tags": ["a", "b"],
            "filename_signals": ["batch.csv", "batch"],
            "extracted_text_ref": None,
            "relevance_score": 2.5,
        }
    )
    assert entry["s3_key"].endswith("batch.csv")
    assert entry["suggested_read"] == "content"
    assert entry["has_extracted_text"] is False


def test_suggest_read_document_with_extracted_text() -> None:
    assert suggest_read("document", "_data_catalog/extracted/x.txt") == "content"


def test_suggest_read_document_without_extracted_text() -> None:
    assert suggest_read("document", None) == "raw_bytes"


def test_map_read_as_to_source() -> None:
    assert map_read_as_to_source("content") == "auto"
    assert map_read_as_to_source("extracted_text") == "extracted"
    assert map_read_as_to_source("raw_bytes") == "raw"


def test_format_catalog_search_response_empty_tells_agent_not_to_retry() -> None:
    payload = format_catalog_search_response("batch-3", [])
    assert payload["count"] == 0
    assert "Do not retry" in payload["hint"]
    assert "knowledge_show" in payload["hint"]
    payload = format_catalog_search_response("batch", [{"s3_key": "proj-1/a.csv"}])
    assert payload["count"] == 1
    assert "lab_file_read" in payload["usage"]


def test_format_lab_file_read_response_maps_representation() -> None:
    payload = format_lab_file_read_response(
        {
            "s3_key": "proj-1/doc.pdf",
            "read_source": "extracted",
            "text": "hello",
            "length": 5,
            "truncated": False,
            "meta": {"detected_modality": "document"},
        }
    )
    assert payload["representation"] == "extracted_text"
    assert payload["binary"] is False
    assert payload["text"] == "hello"
