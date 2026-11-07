"""``citation.open``: the console's citation chip opens its passage (K1.4).

A chat answer lists the passages it cited (``send_message``'s
``citations``: ``turn_citations``); a row carries the session label (``S3``)
and a 200-character preview. Opening the chip asks for the passage itself:
this verb looks the label up in the session's evidence ledger and fetches the
passage with ``client.rag.fetch_passage`` (``GET /gw/rag/passages/{id}``)
in the project it was retrieved in. The server applies the retrieve verb's
access rule (ADR-097 membership, ADR-080 shared corpora) and answers 404 for
a passage the caller may not see: no access path of its own here.

Read-only: the user reading a passage is not the model reading it, so nothing
is recorded in the ledger. Bounded: ``window`` 0-2 chunks each side, at most
:data:`CITATION_OPEN_MAX_CHARS` characters.

A row also carries its passage handle (``source_id``, ``project_id``): when
the ledger no longer holds the label (64 labels kept, or the companion
restarted under the console) the verb fetches by the handle the row names,
under the same server access check (review of K1 part A, 2026-10-24).
"""

from __future__ import annotations

import re
from typing import Any

from ..evidence_ledger import LedgerEntry, session_ledger
from ..passage_tool import OPEN_PASSAGE_MAX_WINDOW

CITATION_OPEN_MAX_CHARS = 6000


def _window(params: dict[str, Any]) -> int:
    try:
        value = int(params.get("window", 1))
    except (TypeError, ValueError):
        return 1
    return max(0, min(OPEN_PASSAGE_MAX_WINDOW, value))


def _entry(session: Any, sid: str, params: dict[str, Any]) -> LedgerEntry:
    """The ledger's entry for *sid*, else one built from the row's handle."""
    ledger = session_ledger(session)
    entry = ledger.get(sid) if ledger is not None else None
    if entry is not None and entry.passage.source_id:
        return entry
    handle = str(params.get("source_id") or "").strip()
    if not handle:
        raise ValueError(f"no passage labelled {sid} in this session")
    from r4_contracts.retrieval import RetrievedPassage

    name = re.sub(r"^\[S\d+\]\s*", "", str(params.get("title") or ""))
    passage = RetrievedPassage(rank=1, source_id=handle, artifact_id="", source_name=name, text="")
    return LedgerEntry(sid, passage, project_id=str(params.get("project_id") or ""))


def _chunks(data: dict[str, Any]) -> list[int]:
    chunks = data.get("chunks")
    return [int(c) for c in chunks if isinstance(c, int)] if isinstance(chunks, list) else []


def open_citation(session: Any, client: Any, params: dict[str, Any]) -> dict[str, Any]:
    """The passage a citation row names, with its context."""
    from r4_contracts.context_pack import sanitize_text

    sid = str(params.get("sid") or "").strip()
    if not sid:
        raise ValueError("citation.open needs the citation's sid (S3)")
    entry = _entry(session, sid, params)
    window = _window(params)
    data = client.rag.fetch_passage(
        entry.passage.source_id,
        project_id=entry.project_id or None,
        window=window,
        max_chars=CITATION_OPEN_MAX_CHARS,
    )
    data = data if isinstance(data, dict) else {}
    text = sanitize_text(str(data.get("text") or entry.passage.text))
    return {
        "sid": entry.sid,
        "title": entry.name or entry.sid,
        "artifact_id": str(data.get("artifact_id") or entry.passage.artifact_id or ""),
        "source_id": entry.passage.source_id,
        "text": text[:CITATION_OPEN_MAX_CHARS],
        "window": window,
        "chunks": _chunks(data),
    }


class CitationBridgeMixin:
    """``citation.open`` on the companion bridge."""

    def citation_open(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self.session  # type: ignore[attr-defined]
        return open_citation(session, session.client, params)


__all__ = ["CITATION_OPEN_MAX_CHARS", "CitationBridgeMixin", "open_citation"]
