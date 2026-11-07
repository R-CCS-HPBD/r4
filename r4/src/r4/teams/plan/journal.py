"""The plan journal: an attempt-local memo of agent results (ADR-092 D-8).

Every agent call that reaches the broker appends one JSON line to
``<run_dir>/journal.jsonl``. The record's key is the content hash of what
the call sends: node path, item key, replica, rendered prompt,
instructions, schema, model and tool set. A later attempt over the same
run directory replays, at zero model cost, every ``ok`` record whose key
still matches; changed, failed and invalidated calls run live.

This is a memo of results, not a scheduler: no queue, no claiming, no
retry state, and every result labels it ``journal: local-cache`` (MD-3:
an attempt-local cache, resume kept).

Slice 7 (MD-4): the durable record is UMRS's, and this file is its cache.
When the run has a durable home (``RuntimeServices.records``), every
portable record (an agent call, a human's gate answer, a ``--from-phase``
marker) is written to the local file first and then through the home;
workspace and merge records stay local (they describe one machine's
files). Reads prefer the local file and fall back to the home per key, so
a resume on a machine without the run directory replays from UMRS, and the
records it found there are written into the local file (the cache fills).
A failure of the home is never silent: it is recorded, reported once
(``on_failure``) and named in the run's result; the run then continues
local-only, unless it requires its durable record, when ``halt_reason``
tells the engine to stop (fail closed).

Stdlib only: the plan package's import fence (ADR-092 D-2).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, BinaryIO

JOURNAL_FILE = "journal.jsonl"
JOURNAL_LABEL = "local-cache"
# Record kinds the durable home keeps; an agent record has no ``kind``.
PORTABLE_KINDS = frozenset({"agent", "gate", "invalidate"})
INVALIDATE_KIND = "invalidate"
DURABLE_FAILED = "durable_record_failed"
SCOPE_PORTABLE = "portable"  # ADR-020 §15.1: the run continues elsewhere
SCOPE_LOCAL = "engine_internal"  # this run directory, this engine only
MAX_ERROR_CHARS = 300


class PlanJournal:
    """Append-only JSONL of agent-call records for one run directory, the
    cache of the run's durable record when it has one (slice 7)."""

    def __init__(
        self,
        run_dir: str | Path,
        durable: Any = None,
        *,
        on_failure: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(run_dir) / JOURNAL_FILE
        self._durable = durable
        self._required = bool(getattr(durable, "required", False))
        self._on_failure = on_failure
        self._error: str | None = None
        self._written = 0
        self._local_only = 0
        self._from_durable = 0

    def load(self) -> dict[str, dict[str, Any]]:
        """``{call_key: record}``, the latest record per key.

        A later failure never displaces an ``ok`` record: the ok result is
        still the memo of that exact call. Only ``ok`` records replay. The
        local file wins per key; the durable home fills the keys it lacks,
        and what it filled is written to the local file.
        """
        local = _memo(_live(self._read()))
        remote = _memo(_live(self._durable_load()))
        records = dict(remote)
        for key, record in local.items():
            if records.get(key, {}).get("ok") and not record.get("ok"):
                continue
            records[key] = record
        filled = [record for key, record in records.items() if record is remote.get(key)]
        self._from_durable = sum(1 for record in filled if record.get("ok"))
        self._write_local(filled)
        return records

    def records(self) -> list[dict[str, Any]]:
        """Every readable record of the local file, in append order."""
        return list(self._read())

    def append(self, record: dict[str, Any]) -> None:
        """The local file first (a crash loses at most the call in flight),
        then the durable home for a portable record.

        A crash mid-append leaves a torn last line with no newline. Written
        straight after it, the next record would join that line and fail to
        parse with it, losing a paid ok result as well (D-8). So a record
        always starts on a line of its own: a torn tail is closed first.
        """
        self._write_local([record])
        if self._durable is None or not is_portable(record):
            return
        if self._error is not None:
            self._local_only += 1
            return
        try:
            self._durable.append(record)
        except Exception as exc:  # noqa: BLE001 — named, never silent (slice 7)
            self._local_only += 1
            self._fail("write", exc)
            return
        self._written += 1

    def close(self, status: str, summary: dict[str, Any]) -> None:
        """Tell the durable home how the attempt ended (when it still answers)."""
        if self._durable is None or self._error is not None:
            return
        try:
            self._durable.close(status, summary)
        except Exception as exc:  # noqa: BLE001 — named in the result
            self._fail("close", exc)

    @property
    def durable_error(self) -> str | None:
        return self._error

    @property
    def halt_reason(self) -> str | None:
        """Set once a run that requires its durable record lost it: stop."""
        if self._required and self._error is not None:
            return f"{DURABLE_FAILED}: {self._error}"
        return None

    @property
    def checkpoint_scope(self) -> str:
        """PORTABLE while every record reached the durable home; else local."""
        if self._durable is not None and self._error is None:
            return SCOPE_PORTABLE
        return SCOPE_LOCAL

    def report(self) -> dict[str, Any]:
        """The run result's ``durable`` block: where its records are, and any failure."""
        if self._durable is None:
            return {"home": "local", "state": "local_only", "required": False}
        report: dict[str, Any] = {"home": "umrs", **_described(self._durable)}
        report.update(
            {
                "required": self._required,
                "state": _state(self._error, self._required),
                "written": self._written,
                "local_only": self._local_only,
                "replayed_from_durable": self._from_durable,
            }
        )
        if self._error is not None:
            report["error"] = self._error
        return report

    def _durable_load(self) -> list[dict[str, Any]]:
        if self._durable is None or self._error is not None:
            return []
        try:
            loaded = self._durable.load()
        except Exception as exc:  # noqa: BLE001 — named; local records still replay
            self._fail("read", exc)
            return []
        return [record for record in loaded or () if isinstance(record, dict)]

    def _fail(self, what: str, exc: BaseException) -> None:
        """Record the home's first failure and report it once."""
        if self._error is not None:
            return
        self._error = f"{what}: {type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]
        if self._on_failure is not None:
            then = (
                "the run requires its durable record, so it stops"
                if self._required
                else "the run continues local-only; later records are in the local journal only"
            )
            self._on_failure(f"durable record {self._error}; {then}")

    def _write_local(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as fh:
            lead = b"\n" if _torn_tail(fh) else b""
            fh.write(lead + b"".join(_dumps(r).encode("utf-8") + b"\n" for r in records))
            fh.flush()
            os.fsync(fh.fileno())

    def invalidate_from_phase(self, program: dict[str, Any], phase_id: str) -> int:
        """Drop the records of ``phase_id`` and every later phase (``--from-phase``).

        Returns how many records were dropped. Records of phases the
        program no longer has are kept: their keys cannot match anyway.
        """
        dropped_phases = set(phases_from(program, phase_id))
        records = self.records()
        kept = [r for r in records if r.get("phase") not in dropped_phases]
        dropped = len(records) - len(kept)
        if dropped:
            self._rewrite(kept)
        return dropped

    def _read(self) -> Iterator[dict[str, Any]]:
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                record = _parse(line)
                if record is not None:
                    yield record

    def _rewrite(self, records: list[dict[str, Any]]) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(_dumps(record) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)


def phases_from(program: dict[str, Any], phase_id: str) -> list[str]:
    """``phase_id`` and every later phase; KeyError for a phase the plan lacks."""
    order = [phase.get("id") for phase in program.get("phases") or []]
    if phase_id not in order:
        raise KeyError(f"no phase {phase_id!r}; the plan's phases are {order}")
    return [str(phase) for phase in order[order.index(phase_id) :]]


def invalidation_marker(phases: Iterable[str], attempt: str) -> dict[str, Any]:
    """A ``--from-phase`` written to the durable home: a later read drops
    every earlier record of these phases there, as the local rewrite did."""
    return {
        "call_key": f"invalidate:{attempt}"[:64],
        "kind": INVALIDATE_KIND,
        "phases": sorted(set(phases)),
        "phase": None,
        "ok": False,  # never replayed as a result
        "attempt": attempt,
    }


def is_portable(record: dict[str, Any]) -> bool:
    return str(record.get("kind") or "agent") in PORTABLE_KINDS


def _memo(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The latest record per key; a failure never displaces an ok record."""
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record.get("call_key")
        if not isinstance(key, str):
            continue
        if out.get(key, {}).get("ok") and not record.get("ok"):
            continue
        out[key] = record
    return out


def _live(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records in order, each ``--from-phase`` marker dropping the earlier
    records of its phases."""
    kept: list[dict[str, Any]] = []
    for record in records:
        if record.get("kind") == INVALIDATE_KIND:
            phases = set(record.get("phases") or ())
            kept = [r for r in kept if r.get("phase") not in phases]
        kept.append(record)
    return kept


def _described(durable: Any) -> dict[str, Any]:
    try:
        described = durable.describe()
    except Exception:  # noqa: BLE001 — a description never fails a result
        return {}
    return dict(described) if isinstance(described, dict) else {}


def _state(error: str | None, required: bool) -> str:
    if error is None:
        return "ok"
    return "failed" if required else "degraded"


def _dumps(record: dict[str, Any]) -> str:
    """One record as a line, keys in insertion order (never sorted).

    A replayed output must be the live output, key order included:
    downstream prompts render it with ``|json`` and their keys hash the
    rendered prompt (D-8), so a reordered object would rerun every reader.
    """
    return json.dumps(record, ensure_ascii=False, default=str)


def _torn_tail(fh: BinaryIO) -> bool:
    """True when the file is not empty and does not end with a newline."""
    end = fh.seek(0, os.SEEK_END)
    if end == 0:
        return False
    fh.seek(end - 1)
    return fh.read(1) != b"\n"


def _parse(line: str) -> dict[str, Any] | None:
    """A record, or None for a blank or torn line (a crash mid-append)."""
    try:
        record = json.loads(line)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None
