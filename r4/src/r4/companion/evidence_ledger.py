"""Evidence ledger: the passages a session's turns were shown (ADR-093 D-8, D-9).

The pre-retrieval router records every passage it puts into a user turn here,
under the label the model saw (``S4``). Labels are session-monotonic, so an
answer that cites ``[S2]`` three turns later still names the same passage.
The citation check reads the ledger back; it never asks the model.

The knowledge tools (``search_docs``, ``rag_query``, ``knowledge_query``,
``open_passage``) record what they return too (knowledge plan K1.4,
2026-10-24): :meth:`EvidenceLedger.assign` labels their passages, so a turn
that only called a tool still gets its citation block and footer. A passage
already in the ledger keeps its label.

The ledger is a ring of 64 passages held in memory: local session state, no
second store (the next label lives in the session's metadata so a resumed
session keeps counting). Each passage is registered with the session's
:class:`~r4.companion.taint.TaintRegistry` as ``context_pack`` (ADR-039), so a
tool argument that copies retrieved text needs per-call approval, the same as
text a ``search_docs`` call returned.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from r4_contracts.context_pack import sanitize_name
from r4_contracts.retrieval import RetrievedPassage

logger = logging.getLogger(__name__)

LEDGER_CAPACITY = 64
LEDGER_TAINT_SOURCE = "context_pack"  # r4_contracts.taint.TOOL_TAINT_CLASSES key
LABEL_PREFIX = "S"


@dataclass(frozen=True)
class LedgerEntry:
    """One passage a turn was shown, under its label (``S4``, no brackets)."""

    sid: str
    passage: RetrievedPassage
    turn_id: str = ""
    status: str = ""  # the server's sufficiency when it was retrieved
    project_id: str = ""  # the project a tool retrieved it in ("" = the client's)

    @property
    def name(self) -> str:
        """The source's display name, from UMRS artifact metadata only."""
        return sanitize_name(self.passage.source_name)


def label_number(sid: str) -> int | None:
    """``"S4"`` / ``"[S4]"`` → 4; anything else → ``None``."""
    core = str(sid).strip().strip("[]").strip()
    if not core.upper().startswith(LABEL_PREFIX):
        return None
    digits = core[len(LABEL_PREFIX) :]
    return int(digits) if digits.isdigit() else None


class EvidenceLedger:
    """Bounded, label-keyed record of the passages shown in this session."""

    def __init__(
        self,
        *,
        capacity: int = LEDGER_CAPACITY,
        next_label: int = 1,
        taint_registry: Any = None,
    ) -> None:
        self._capacity = max(1, int(capacity))
        self._entries: OrderedDict[str, LedgerEntry] = OrderedDict()
        self._next = max(1, int(next_label))
        self._taint = taint_registry
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def next_label(self) -> int:
        """The number the next rendered passage is labelled with."""
        return self._next

    def bind_taint_registry(self, registry: Any) -> None:
        """Attach the session's taint registry (the agent's, which the fence reads)."""
        self._taint = registry

    def record(
        self,
        citations: Mapping[str, str],
        passages: Sequence[RetrievedPassage],
        *,
        turn_id: str = "",
        status: str = "",
    ) -> list[LedgerEntry]:
        """Record the passages a rendered pack showed, by its citation map.

        ``citations`` is ``render_pack``'s ``{"[S4]": source_id}``: only the
        passages that survived the token cap are recorded. Each is registered
        with the taint registry; the next label moves past the highest used.
        """
        by_id = {passage.source_id: passage for passage in passages}
        recorded: list[LedgerEntry] = []
        for tag, source_id in citations.items():
            number = label_number(tag)
            passage = by_id.get(source_id)
            if number is None or passage is None:
                continue
            entry = LedgerEntry(f"{LABEL_PREFIX}{number}", passage, turn_id, status)
            with self._lock:
                self._put(entry, number)
            recorded.append(entry)
        for entry in recorded:
            self._register_taint(entry.passage.text)
        return recorded

    def assign(
        self,
        passages: Sequence[RetrievedPassage],
        *,
        turn_id: str = "",
        project_id: str = "",
    ) -> list[LedgerEntry]:
        """Label the passages a knowledge tool returned, in order (K1.4).

        A passage whose ``source_id`` the ledger already holds keeps its
        label (and is shown again this turn, with this text); any other
        takes the next label. Each is taint-registered like a pack passage.
        """
        with self._lock:
            entries = [self._assign_one(p, turn_id, project_id) for p in passages]
        for entry in entries:
            self._register_taint(entry.passage.text)
        return entries

    def _assign_one(self, passage: RetrievedPassage, turn_id: str, project_id: str) -> LedgerEntry:
        """One passage's entry (the lock is held)."""
        number = self._number_of(passage.source_id) or self._next
        entry = LedgerEntry(f"{LABEL_PREFIX}{number}", passage, turn_id, "", project_id)
        self._put(entry, number)
        return entry

    def _number_of(self, source_id: str) -> int | None:
        """The label number a passage already has (the lock is held)."""
        if not source_id:
            return None
        for entry in self._entries.values():
            if entry.passage.source_id == source_id:
                return label_number(entry.sid)
        return None

    def _put(self, entry: LedgerEntry, number: int) -> None:
        """Store ``entry`` newest, evicting the oldest past capacity (lock held)."""
        self._entries.pop(entry.sid, None)
        self._entries[entry.sid] = entry
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)
        self._next = max(self._next, number + 1)

    def _register_taint(self, text: str) -> None:
        registry = self._taint
        if registry is None:
            return
        try:
            registry.register(LEDGER_TAINT_SOURCE, text)
        except Exception as exc:  # the fence never breaks a turn's retrieval
            logger.debug("context_pack taint registration skipped", exc_info=exc)

    def get(self, sid: str) -> LedgerEntry | None:
        number = label_number(sid)
        if number is None:
            return None
        with self._lock:
            return self._entries.get(f"{LABEL_PREFIX}{number}")

    def entries(self) -> tuple[LedgerEntry, ...]:
        """Every live entry, oldest first."""
        with self._lock:
            return tuple(self._entries.values())

    def turn_sids(self, turn_id: str) -> tuple[str, ...]:
        """The labels shown in ``turn_id``, in label order."""
        if not turn_id:
            return ()
        return tuple(entry.sid for entry in self.entries() if entry.turn_id == turn_id)

    def by_name(self, name: str) -> list[LedgerEntry]:
        """Entries whose source name matches ``name`` (case-insensitive;
        either one containing the other, so ``report`` finds ``report.pdf``)."""
        wanted = " ".join(str(name).split()).casefold()
        if not wanted:
            return []
        return [entry for entry in self.entries() if _names_match(wanted, entry.name.casefold())]


def _names_match(wanted: str, have: str) -> bool:
    if not have or wanted == have:
        return bool(have)
    shorter, longer = sorted((wanted, have), key=len)
    return len(shorter) >= 3 and shorter in longer


# ---------------------------------------------------------- session glue ---

SESSION_LEDGER_ATTR = "_evidence_ledger"
METADATA_KEY = "evidence_ledger"  # {"next_label": n}: the only persisted part


def _taint_registry_of(session: Any) -> Any:
    """The registry the fence reads: the agent's (it re-binds the permission
    manager's to its own before every tool call), else the manager's."""
    registry = getattr(getattr(session, "agent", None), "_taint_registry", None)
    if registry is None:
        registry = getattr(getattr(session, "permissions", None), "taint_registry", None)
    return registry


def _stored_next_label(session: Any) -> int:
    metadata = getattr(session, "metadata", None)
    stored = metadata.get(METADATA_KEY) if isinstance(metadata, dict) else None
    try:
        return max(1, int((stored or {}).get("next_label") or 1))
    except (AttributeError, TypeError, ValueError):
        return 1


def session_ledger(session: Any, *, create: bool = False) -> EvidenceLedger | None:
    """The session's ledger; with ``create`` one is made on first use, its
    labels continuing from the session's metadata."""
    ledger = getattr(session, SESSION_LEDGER_ATTR, None)
    if isinstance(ledger, EvidenceLedger):
        ledger.bind_taint_registry(_taint_registry_of(session))
        return ledger
    if not create:
        return None
    ledger = EvidenceLedger(
        next_label=_stored_next_label(session), taint_registry=_taint_registry_of(session)
    )
    setattr(session, SESSION_LEDGER_ATTR, ledger)
    return ledger


def remember_next_label(session: Any, ledger: EvidenceLedger) -> None:
    """Persist the label counter in the session's metadata (resume keeps it)."""
    metadata = getattr(session, "metadata", None)
    if isinstance(metadata, dict):
        metadata[METADATA_KEY] = {"next_label": ledger.next_label}


__all__ = [
    "LABEL_PREFIX",
    "LEDGER_CAPACITY",
    "LEDGER_TAINT_SOURCE",
    "METADATA_KEY",
    "SESSION_LEDGER_ATTR",
    "EvidenceLedger",
    "LedgerEntry",
    "label_number",
    "remember_next_label",
    "session_ledger",
]
