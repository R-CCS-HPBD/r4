"""``open_passage``: read one retrieved passage with its context (K1.4).

A read-only agent tool over ``client.rag.fetch_passage``
(``GET /gw/rag/passages/{node_id}``, ADR-093 D-2). The server applies the
retrieve verb's access rule (project membership, ADR-097; shared corpora,
ADR-080) and answers 404 for a passage the caller may not see, so this tool
adds no access path of its own. The model names a passage by the label a
knowledge tool gave it (``S3``) or by its passage id; the project is the one
the label was retrieved in, never one the model names.

Output is bounded: at most two neighbouring chunks each side and
:data:`OPEN_PASSAGE_MAX_CHARS` characters of text. The opened passage is
recorded in the session's evidence ledger under its label (a new one for an
id the session has not seen), so the answer can cite what it read.
"""

from __future__ import annotations

from typing import Any

from r4_contracts.retrieval import RetrievedPassage

from .evidence_ledger import LedgerEntry, label_number
from .tool_evidence import label_passages

OPEN_PASSAGE_MAX_CHARS = 3000
OPEN_PASSAGE_MAX_WINDOW = 2


def _bounded_window(window: Any) -> int:
    try:
        value = int(window)
    except (TypeError, ValueError):
        return 1
    return max(0, min(OPEN_PASSAGE_MAX_WINDOW, value))


def _entry_for(evidence: Any, ref: str) -> LedgerEntry | None:
    if evidence is None:
        return None
    try:
        entry: LedgerEntry | None = evidence.entry(ref)
    except Exception:  # noqa: BLE001 - an unreadable ledger is an unknown label
        return None
    return entry


def _target(evidence: Any, ref: str) -> tuple[str, str] | None:
    """``(passage id, project)`` for ``ref``; ``None`` for an unknown label."""
    entry = _entry_for(evidence, ref)
    if entry is not None and entry.passage.source_id:
        return entry.passage.source_id, entry.project_id
    if entry is None and label_number(ref) is not None:
        return None
    return ref, ""


def _fetch_error(ref: str, exc: Exception) -> str:
    if getattr(exc, "status_code", None) == 404:
        return f"Passage {ref} is not visible to you or no longer exists."
    return f"Error opening passage {ref}: {exc}"


def _as_passage(data: dict[str, Any], node_id: str) -> RetrievedPassage:
    from r4_contracts.context_pack import sanitize_text

    text = sanitize_text(str(data.get("text") or "")).strip()
    return RetrievedPassage(
        rank=1,
        source_id=str(data.get("source_id") or node_id),
        artifact_id=str(data.get("artifact_id") or ""),
        source_name=str(data.get("source_name") or ""),
        text=text[:OPEN_PASSAGE_MAX_CHARS],
    )


def _render(passage: RetrievedPassage, tag: str, data: dict[str, Any]) -> str:
    from r4_contracts.context_pack import sanitize_name

    name = sanitize_name(passage.source_name or passage.artifact_id or passage.source_id)
    chunks = data.get("chunks")
    where = f" chunks {chunks[0]}-{chunks[-1]}" if isinstance(chunks, list) and chunks else ""
    clipped = " …" if len(str(data.get("text") or "")) > OPEN_PASSAGE_MAX_CHARS else ""
    return f"[{tag}] {name} id={passage.source_id}{where}\n{passage.text}{clipped}"


def open_passage(client: Any, evidence: Any, passage: str, window: Any = 1) -> str:
    """The passage ``passage`` names, with ``window`` chunks each side."""
    ref = str(passage or "").strip().strip("[]").strip()
    if not ref:
        return "Error: name a passage by its label (S3) or its passage id."
    target = _target(evidence, ref)
    if target is None:
        return f"No passage is labelled {ref} in this session; search first, or pass a passage id."
    node_id, project = target
    try:
        data = client.rag.fetch_passage(
            node_id,
            project_id=project or None,
            window=_bounded_window(window),
            max_chars=OPEN_PASSAGE_MAX_CHARS + 1,
        )
    except Exception as exc:  # noqa: BLE001 - reported to the agent
        return _fetch_error(ref, exc)
    record = _as_passage(data if isinstance(data, dict) else {}, node_id)
    labels = label_passages(evidence, [record], project_id=project)
    return _render(record, labels[0] if labels else "passage", data)


def build_open_passage_tool(client: Any, evidence: Any = None) -> dict[str, Any]:
    """The ``open_passage`` tool descriptor (read-only, concurrency-safe)."""

    def _execute(passage: str, window: int = 1) -> str:
        return open_passage(client, evidence, passage, window)

    return {
        "name": "open_passage",
        "description": (
            "Open a retrieved passage by its label (S3) or passage id, with up to "
            "2 neighbouring chunks each side. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "passage": {"type": "string", "description": "Label (S3) or passage id"},
                "window": {"type": "integer", "description": "Chunks each side, 0-2"},
            },
            "required": ["passage"],
        },
        "execute": _execute,
        "concurrency_safe": True,
    }


__all__ = [
    "OPEN_PASSAGE_MAX_CHARS",
    "OPEN_PASSAGE_MAX_WINDOW",
    "build_open_passage_tool",
    "open_passage",
]
