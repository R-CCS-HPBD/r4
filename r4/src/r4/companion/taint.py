"""Taint registry — companion-side provenance tracker (ADR-039).

The companion session registers excerpts of untrusted tool output
(knowledge/RAG/lab/MCP/web surfaces) here; the PermissionManager's taint
fence consults it so tool-call arguments *derived from* that content
require explicit per-call approval (never-auto).

The registry is deliberately lightweight for the minimal fence round:

- ring-buffer bounded (default 1024 entries) with a TTL (default 2h);
- matching is by normalized substring of the registered excerpts (first
  120 chars, ≥24 chars) against string leaves in the arguments; a
  result yields up to 32 excerpts (its start, each paragraph, each line
  of a multi-line paragraph, each later sentence of a paragraph, then
  120-character windows of a long one), so each retrieved passage, and
  text deep inside a single long paragraph, is matchable;
- all producers are explicit opt-in via :data:`TOOL_TAINT_CLASSES` or a
  dynamic-name prefix (``taint_class_for_tool``: first-class MCP tools
  ``mcp.<server>.<tool>``) — a tool not on the map never taints, except a
  call that runs local code and reads the network
  (:func:`r4.companion.taint_exec.exec_fetches_network`: its program text,
  or the script it runs): its result is ``web``;
- downloads that land on disk are followed (2026-10-30 review): the paths
  such a call wrote, and scripts written with network-reading code, are
  recorded, and a later ``file_read`` / exec / search call that names one
  (or searches a directory holding one) yields ``web`` content too;
  absence of a registry never blocks.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque
from dataclasses import replace
from typing import Any, Iterable

from r4_contracts.taint import (
    MAX_EXCERPTS_PER_RESULT,
    MIN_TAINT_EXCERPT_LEN,
    TaintedSegment,
    taint_class_for_tool,
)

from . import taint_exec
from .taint_exec import EXEC_PROGRAM_ARGS, exec_fetches_network

logger = logging.getLogger(__name__)

#: Up to MAX_EXCERPTS_PER_RESULT (32) excerpts per result: the ring keeps
#: the fence's memory at dozens of full results, not eight (ADR-093 D-10).
DEFAULT_MAX_ENTRIES = 1024
#: Paths that hold network-derived data or network-reading code (oldest dropped).
DEFAULT_MAX_PATHS = 4096
_MATCH_EXCERPT_CHARS = 120
_MAX_EXCERPT_CHARS = 512
_BLANK_LINE_RE = re.compile(r"\n[ \t\r\f\v]*\n")
#: Where a later sentence of a (whitespace-collapsed) paragraph starts.
_SENTENCE_START_RE = re.compile(r"(?<=[.!?;:])\s+(?=\S)")
#: A rendered passage header ('[n] <name> › <section> (score) id=<handle>',
#: ADR-093 D-10 search_docs): metadata R4 printed, never document text, so it
#: must not start the probe of the passage below it.
_PASSAGE_HEADER_RE = re.compile(r"^\[\d+\] .*\(-?\d+(?:\.\d+)?\) id=\S+$")
#: A paragraph or line excerpt needs this many words: a handle (node id, uuid,
#: URL, path) names content without copying it, and a later call that uses it
#: (fetch that passage, read that file) is navigation, not derived content.
_MIN_PROSE_WORDS = 3
#: A T-213 boundary wrapper (``r4.agent.untrusted_scanner.wrap_external_content``)
#: around a whole result: a ``[X_START] meta`` line, the content, ``[X_END]``.
#: The markers are R4's, not content; a wrapped JSON result is read as JSON.
_BOUNDARY_RE = re.compile(
    r"\A\s*\[(TOOL_OUTPUT|RAG_CHUNK|EXTERNAL_CONTENT)_START\][^\n]*\n(.*)\n\[\1_END\]\s*\Z",
    re.DOTALL,
)


def _normalize(text: str) -> str:
    """Collapse all whitespace runs to single spaces and strip."""
    return " ".join(text.split())


def _default_min_excerpt_len() -> int:
    """Floor from R4_TAINT_MIN_LEN (WP-19 knob); contract default otherwise."""
    raw = os.environ.get("R4_TAINT_MIN_LEN", "").strip()
    if not raw:
        return MIN_TAINT_EXCERPT_LEN
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid R4_TAINT_MIN_LEN=%r — using %d", raw, MIN_TAINT_EXCERPT_LEN)
        return MIN_TAINT_EXCERPT_LEN
    if value < 1:
        logger.warning("Negative R4_TAINT_MIN_LEN=%r — using %d", raw, MIN_TAINT_EXCERPT_LEN)
        return MIN_TAINT_EXCERPT_LEN
    return value


class TaintRegistry:
    """Bounded store of untrusted-content excerpts for the session."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl_seconds: float = 7200.0,
        min_excerpt_len: int = 0,
    ) -> None:
        self._entries: deque[TaintedSegment] = deque(maxlen=max_entries)
        self._ttl = float(ttl_seconds)
        self._min_len = min_excerpt_len if min_excerpt_len > 0 else _default_min_excerpt_len()
        # ADR-092 D-6 (ADR-039): the lead's registry is written from parallel
        # tool workers (``_finish_tool_call`` registers each result, and
        # ``team_plan_result`` imports a plan's excerpts). ``import_segments``
        # rebuilds the ring, so an unlocked ``register`` landing between its
        # copy and its swap was lost, and an iteration racing an append
        # raised "deque mutated during iteration". Every access holds this.
        # Re-entrant: ``register`` and ``__len__`` purge under it.
        self._lock = threading.RLock()
        # Where downloaded data (or a fetch script) lives: path -> recorded at.
        self._network_paths: dict[str, float] = {}
        # ADR-111 D-4: untrusted content entered this session at some point.
        # Sticky (no TTL, survives a resume through the session metadata):
        # the memory-write rung must keep asking after the excerpts expire.
        self.ever_tainted = False

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._entries)

    def register(self, tool_name: str, result_text: Any, arguments: Any = None) -> int:
        """Register excerpts from a tool result. Returns count added.

        Only calls :func:`result_taint_class` classifies contribute (the
        contract map, ``mcp.<server>.<tool>``, an exec call whose
        ``arguments`` read the network); others are ignored. Up to
        :data:`MAX_EXCERPTS_PER_RESULT` excerpts are kept per result
        (``_excerpts_of`` says which, in priority order). Paths a
        network-reading call wrote are recorded first, whatever it printed.
        """
        self._record_paths(taint_exec.written_fetch_scripts(tool_name, arguments))
        taint_class = self.classify(tool_name, arguments)
        if taint_class == "web":
            # Recorded even when the call printed nothing (wget saves a file).
            self._record_paths(taint_exec.write_targets(tool_name, arguments))
        if taint_class is None:
            return 0
        self.ever_tainted = True
        if not isinstance(result_text, str) or not result_text.strip():
            return 0
        if len(_normalize(result_text)) < self._min_len:
            return 0
        excerpts = self._excerpts_of(result_text)
        ts = time.time()
        with self._lock:
            self._purge()
            for excerpt in excerpts:
                self._entries.append(
                    TaintedSegment(
                        text_excerpt=excerpt,
                        taint_class=taint_class,
                        source_tool=tool_name,
                        registered_at=ts,
                    )
                )
        return len(excerpts)

    def _excerpts_of(self, raw: str) -> list[str]:
        """A bounded set of probe excerpts from one raw (unnormalised) result.

        In priority order, first come first kept up to the cap: the start of
        the whole result; the start of every paragraph, so each passage of a
        multi-passage result (search_docs, rag_query, search_agentic) is
        matchable on its own (ADR-093 D-10, receipt 6); then each line of a
        multi-line paragraph, so an instruction on a line of its own is too.
        Paragraphs are split BEFORE whitespace collapses (collapsing first
        left one paragraph, the result itself), a JSON result is read as its
        string values (its escaped text matches no argument), and rendered
        passage headers are dropped.

        Then, so a result that is one long paragraph is not one excerpt (the
        ADR-092 slice-1 open item; ADR-039's fence saw only its first 120
        characters): every later sentence of a paragraph, from where it
        starts, and last 120-character windows across a paragraph longer
        than one probe. An argument quoting a sentence, or any run of 240
        characters, of such a paragraph then holds a whole probe.
        """
        blocks = _blocks_of(raw)
        paragraphs = [_normalize(" ".join(lines)) for lines in blocks]
        lines = [_normalize(line) for block in blocks if len(block) > 1 for line in block]
        sentences = [tail for paragraph in paragraphs for tail in _sentence_tails(paragraph)]
        windows = [window for paragraph in paragraphs for window in _windows(paragraph)]
        candidates = [
            _normalize(raw),
            *filter(_is_prose, paragraphs),
            *filter(_is_prose, lines),
            *filter(_is_prose, sentences),
            *filter(_is_prose, windows),
        ]
        return self._bounded(candidates)

    def _bounded(self, candidates: Iterable[str]) -> list[str]:
        """Excerpts long enough to attribute, one per distinct probe, capped."""
        excerpts: list[str] = []
        probes: set[str] = set()
        for candidate in candidates:
            excerpt = candidate[:_MAX_EXCERPT_CHARS]
            probe = excerpt[:_MATCH_EXCERPT_CHARS]
            if len(excerpt) < self._min_len or probe in probes:
                continue
            probes.add(probe)
            excerpts.append(excerpt)
            if len(excerpts) >= MAX_EXCERPTS_PER_RESULT:
                break
        return excerpts

    def is_tainted(self, args: Any) -> str | None:
        """Return the taint_class of the first tainted match, or None.

        Matched against a copy taken under the lock, so a parallel
        ``register`` never breaks the iteration (ADR-092 D-6).
        """
        with self._lock:
            self._purge()
            entries = tuple(self._entries)
        if not entries:
            return None
        for value in _walk_strings(args):
            if len(value) < self._min_len:
                continue
            arg = _normalize(value)
            for entry in entries:
                probe = entry.text_excerpt[:_MATCH_EXCERPT_CHARS]
                if probe and probe in arg:
                    return entry.taint_class
        return None

    def export_segments(self) -> tuple[TaintedSegment, ...]:
        """Every live excerpt, oldest first (ADR-092 D-6: taint follows the data).

        The plan broker exports an agent's excerpts with its result, so they
        can be imported into each downstream agent that reads that output
        and into the lead's registry when the result comes back.
        """
        with self._lock:
            self._purge()
            return tuple(self._entries)

    def import_segments(self, segments: Iterable[Any]) -> int:
        """Adopt excerpts another registry exported. Returns how many were taken.

        Each one is re-stamped ``registered_at`` now: the TTL runs from when
        this registry learnt of it, and ``_purge`` relies on oldest-first
        order. A re-imported excerpt replaces its older copy. The ring grows
        to fit, so an import never evicts an entry this registry already
        holds: a large plan result must not push the lead's own taint out.

        The copy and the swap run under the registry's lock: a ``register``
        from a parallel tool worker between them was dropped with the old
        ring, which let that excerpt's content past the fence (ADR-092 D-6).
        """
        now = time.time()
        incoming: dict[tuple[str, str, str], TaintedSegment] = {}
        for value in segments:
            segment = _as_segment(value)
            if segment is not None:
                incoming[_segment_key(segment)] = replace(segment, registered_at=now)
        if not incoming:
            return 0
        self.ever_tainted = True
        with self._lock:
            self._purge()
            kept = [entry for entry in self._entries if _segment_key(entry) not in incoming]
            size = len(kept) + len(incoming)
            capacity = max(self._entries.maxlen or 0, size)
            self._entries = deque([*kept, *incoming.values()], maxlen=capacity)
        return len(incoming)

    def classify(self, tool_name: str, arguments: Any = None) -> str | None:
        """This call's result taint class: by name or program
        (:func:`result_taint_class`), else ``web`` when it reads a path a
        network-reading call wrote (or a fetch script), else None."""
        by_call = result_taint_class(tool_name, arguments)
        if by_call is not None:
            return by_call
        with self._lock:
            self._purge()
            recorded = tuple(self._network_paths)
        if not recorded:
            return None
        if taint_exec.names_recorded(taint_exec.read_paths(tool_name, arguments), recorded):
            return "web"
        searched = taint_exec.searched_dirs(tool_name, arguments)
        return "web" if taint_exec.holds_recorded(searched, recorded) else None

    def _record_paths(self, paths: Iterable[str]) -> None:
        """Remember paths holding network data; re-recording refreshes one."""
        paths = list(paths)
        if not paths:
            return
        now = time.time()
        with self._lock:
            for path in paths:
                self._network_paths.pop(path, None)
                self._network_paths[path] = now
            while len(self._network_paths) > DEFAULT_MAX_PATHS:
                del self._network_paths[next(iter(self._network_paths))]

    def _purge(self) -> None:
        """Drop expired entries and paths; callers hold ``self._lock``."""
        cutoff = time.time() - self._ttl
        while self._entries and self._entries[0].registered_at < cutoff:
            self._entries.popleft()
        # Insertion order is recording order (a refresh re-inserts at the end).
        for path, recorded_at in list(self._network_paths.items()):
            if recorded_at >= cutoff:
                break
            del self._network_paths[path]


def result_taint_class(tool_name: str, arguments: Any = None) -> str | None:
    """The taint class of this call's result, or None (it never taints)."""
    by_name: str | None = taint_class_for_tool(tool_name)
    if by_name is not None:
        return by_name
    return "web" if exec_fetches_network(tool_name, arguments) else None


def _unwrapped(raw: str) -> str:
    """The content inside a whole-result boundary wrapper, else ``raw``."""
    match = _BOUNDARY_RE.match(raw)
    return match.group(2) if match else raw


def _text_segments(raw: str) -> list[str]:
    """The string values of a JSON result (keys and escapes are not content),
    else the result itself. A truncated JSON result is read as text. A
    boundary-wrapped result (an MCP result) is read without its markers."""
    raw = _unwrapped(raw)
    if raw.lstrip()[:1] not in ("{", "["):
        return [raw]
    try:
        parsed = json.loads(raw)
    except ValueError:
        return [raw]
    return list(_walk_strings(parsed))


def _blocks_of(raw: str) -> list[list[str]]:
    """Every blank-line-separated paragraph of every text segment, as its
    non-empty lines, rendered passage headers dropped."""
    blocks: list[list[str]] = []
    for segment in _text_segments(raw):
        for block in _BLANK_LINE_RE.split(segment):
            lines = [line.strip() for line in block.splitlines()]
            kept = [line for line in lines if line and not _PASSAGE_HEADER_RE.match(line)]
            if kept:
                blocks.append(kept)
    return blocks


def _sentence_tails(paragraph: str) -> list[str]:
    """The paragraph from the start of each of its later sentences."""
    return [paragraph[match.end() :] for match in _SENTENCE_START_RE.finditer(paragraph)]


def _windows(paragraph: str) -> list[str]:
    """The paragraph from every probe-width offset past its start."""
    step = _MATCH_EXCERPT_CHARS
    return [paragraph[start:] for start in range(step, len(paragraph) - step + 1, step)]


def _is_prose(text: str) -> bool:
    return len(text.split()) >= _MIN_PROSE_WORDS


def _segment_key(segment: TaintedSegment) -> tuple[str, str, str]:
    return (segment.text_excerpt, segment.taint_class, segment.source_tool)


def _as_segment(value: Any) -> TaintedSegment | None:
    """A segment, or one rebuilt from its dict form (a journal record), or None."""
    if isinstance(value, TaintedSegment):
        return value
    if not isinstance(value, dict):
        return None
    excerpt = value.get("text_excerpt")
    if not isinstance(excerpt, str) or not excerpt:
        return None
    return TaintedSegment(
        text_excerpt=excerpt,
        taint_class=str(value.get("taint_class") or "generic"),
        source_tool=str(value.get("source_tool") or ""),
        registered_at=0.0,
    )


def _walk_strings(value: Any, _depth: int = 0) -> Any:
    """Yield every string leaf inside dict/list/scalar arguments."""
    if _depth > 8:
        return
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for val in value.values():
            yield from _walk_strings(val, _depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item, _depth + 1)


__all__ = ["EXEC_PROGRAM_ARGS", "TaintRegistry", "exec_fetches_network", "result_taint_class"]
