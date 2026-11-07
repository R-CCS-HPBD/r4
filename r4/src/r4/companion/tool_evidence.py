"""Knowledge tools record their passages in the session's evidence ledger.

Knowledge plan K1.4 (2026-10-24): only the pre-retrieval router (off by
default) wrote to the ledger, so an answer grounded by ``search_docs`` showed
no citation block and no footer. A session now hands its tools a
:class:`ToolEvidence`; ``search_docs``, ``rag_query``, ``knowledge_query`` and
``open_passage`` label what they return with the ledger's session labels
(``S4``), and the citation check reads those passages back as it reads the
router's (ADR-093 D-9).

A tool built without one (a team role, a recipe, a test) numbers its hits as
before. Labelling never costs a tool its answer: a failure here only loses
the labels.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from r4_contracts.retrieval import RetrievedPassage

from .evidence_ledger import LedgerEntry, remember_next_label, session_ledger

logger = logging.getLogger(__name__)


class ToolEvidence:
    """A session's ledger as its knowledge tools see it (bound per session)."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def label(self, passages: Sequence[RetrievedPassage], *, project_id: str = "") -> list[str]:
        """Record ``passages`` as shown in the current turn; their labels, in order."""
        if not passages:
            return []
        ledger = session_ledger(self._session, create=True)
        if ledger is None:
            return []
        turn_id = str(getattr(self._session, "_current_turn_id", "") or "")
        entries = ledger.assign(passages, turn_id=turn_id, project_id=project_id or "")
        remember_next_label(self._session, ledger)
        return [entry.sid for entry in entries]

    def entry(self, ref: str) -> LedgerEntry | None:
        """The ledger entry labelled ``ref`` (``S3`` or ``[S3]``), if any."""
        ledger = session_ledger(self._session)
        return ledger.get(ref) if ledger is not None else None


def label_passages(
    evidence: Any, passages: Sequence[RetrievedPassage], *, project_id: str = ""
) -> list[str]:
    """One label per passage, or ``[]`` (no session ledger, or it failed)."""
    if evidence is None or not passages:
        return []
    try:
        labels = list(evidence.label(passages, project_id=project_id))
    except Exception as exc:  # noqa: BLE001 - labelling never costs the tool its answer
        logger.debug("evidence labelling skipped", exc_info=exc)
        return []
    return labels if len(labels) == len(passages) else []


def passage_from_rag_source(rank: int, source: Any) -> RetrievedPassage:
    """A ``rag_query`` source as a ledger passage. On the spine the source's
    ``document_id`` is the chunk's node id, the passage handle."""
    meta = getattr(source, "metadata", None) or {}
    handle = str(getattr(source, "document_id", "") or "")
    return RetrievedPassage(
        rank=rank,
        source_id=handle,
        artifact_id=str(meta.get("artifact_id") or ""),
        source_name=str(getattr(source, "source_name", "") or handle),
        text=str(getattr(source, "content", "") or ""),
        section=str(meta.get("section") or ""),
    )


def passage_from_hit(rank: int, hit: Mapping[str, Any]) -> RetrievedPassage:
    """A ``knowledge_query`` hit (as the tool shows it) as a ledger passage."""
    return RetrievedPassage(
        rank=rank,
        source_id=str(hit.get("source_id") or ""),
        artifact_id="",
        source_name=str(hit.get("title") or hit.get("source_id") or ""),
        text=str(hit.get("content") or ""),
    )


__all__ = [
    "ToolEvidence",
    "label_passages",
    "passage_from_hit",
    "passage_from_rag_source",
]
