"""Shared catalog search hit shape for agent tools."""

from __future__ import annotations

from typing import Any, Literal

ReadAs = Literal["content", "extracted_text", "raw_bytes"]
GatewaySource = Literal["auto", "raw", "extracted"]

_READ_AS_TO_SOURCE: dict[str, GatewaySource] = {
    "content": "auto",
    "extracted_text": "extracted",
    "raw_bytes": "raw",
}

_SOURCE_TO_REPRESENTATION: dict[str, ReadAs] = {
    "raw": "raw_bytes",
    "extracted": "extracted_text",
    "auto": "content",
}


def has_extracted_text(extracted_text_ref: str | None) -> bool:
    """True when ingestion stored extracted text for a document."""
    return bool((extracted_text_ref or "").strip())


def suggest_read(
    modality: str | None,
    extracted_text_ref: str | None,
) -> ReadAs:
    """Suggest ``lab_file_read(as=...)`` from catalog metadata."""
    mod = (modality or "").strip()
    if mod == "document":
        return "content" if has_extracted_text(extracted_text_ref) else "raw_bytes"
    return "content"


def map_read_as_to_source(as_param: str) -> GatewaySource:
    """Map agent ``as`` parameter to gateway ``source`` query param."""
    normalized = (as_param or "content").strip().lower()
    if normalized not in _READ_AS_TO_SOURCE:
        allowed = ", ".join(sorted(_READ_AS_TO_SOURCE))
        raise ValueError(f"as must be one of: {allowed}")
    return _READ_AS_TO_SOURCE[normalized]


def format_catalog_entry(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a gateway ``/gw/catalog/search`` hit for agent consumption."""
    s3_key = str(item.get("s3_key") or "")
    extracted_text_ref = item.get("extracted_text_ref")
    modality = item.get("detected_modality")
    return {
        "s3_key": s3_key,
        "state": item.get("state"),
        "summary": item.get("summary") or "",
        "deterministic_summary": item.get("deterministic_summary") or "",
        "perception_summary": item.get("perception_summary"),
        "perception_model": item.get("perception_model"),
        "detected_modality": modality,
        "tags": list(item.get("tags") or []),
        "filename_signals": list(item.get("filename_signals") or []),
        "extracted_text_ref": extracted_text_ref,
        "has_extracted_text": has_extracted_text(extracted_text_ref),
        "suggested_read": suggest_read(modality, extracted_text_ref),
        "relevance_score": float(item.get("relevance_score", 0.0) or 0.0),
    }


EMPTY_CATALOG_HINT = (
    "Zero hits in the governed Gateway catalog (JWT project_id). "
    "Do not retry the same query. Local RC1 files (r4 project import / F4) "
    "live in a different store — use knowledge_show or r4 project verify."
)


def format_catalog_search_response(
    query: str,
    results: list[dict[str, Any]],
    *,
    hint: str | None = None,
) -> dict[str, Any]:
    """Build a lab_catalog_search tool payload with consistent fields and guidance."""
    payload: dict[str, Any] = {
        "query": query,
        "count": len(results),
        "results": [format_catalog_entry(item) for item in results],
        "usage": (
            "Catalog results are metadata only — pick s3_key(s) from the hits, then "
            "call lab_file_read(s3_key, as=suggested_read) for file content. Do not "
            "answer numeric or structural questions from summary or perception_summary "
            "alone."
        ),
    }
    if hint is None and not results:
        hint = EMPTY_CATALOG_HINT
    if hint:
        payload["hint"] = hint
    return payload


def format_lab_file_read_response(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize gateway ``/gw/catalog/read`` payload for agent consumption."""
    read_source = str(data.get("read_source") or "raw")
    representation = _SOURCE_TO_REPRESENTATION.get(read_source, "raw_bytes")
    text = data.get("text")
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    return {
        "s3_key": data.get("s3_key"),
        "representation": representation,
        "read_source": read_source,
        "modality": meta.get("detected_modality"),
        "text": text,
        "binary": text is None,
        "length": data.get("length", 0),
        "total_size": data.get("total_size"),
        "truncated": bool(data.get("truncated", False)),
        "offset": data.get("offset", 0),
        "extracted_text_ref": data.get("extracted_text_ref"),
    }
