"""The local loop's ask policy, approvals list and wall clock (ADR-092 slice 6).

A local loop that runs a team plan per iteration is governed like a
server-hosted one, by the same vocabulary:

- **Ask policy (ADR-066).** The loop's ``mode`` is ``auto``, ``ask`` or
  ``profile``. An iteration's action is gated before it runs: ``ask`` parks
  at every action, ``profile`` at an action whose trigger classes meet the
  loop's ``pause_triggers``, ``auto`` never. A plan run is the action
  ``plan_run``, classed ``cost``: the class names and the kind → class
  table are ``r4_contracts.ask_policy``'s, which UMRS's
  ``umrs.knowledge.ask_policy`` reads too (one table, both venues). A loop
  that runs a plan and names no mode asks.
- **The approvals list (ADR-036 (a), ADR-066).** A parked action, or a plan
  gate raised inside the iteration's plan, is one pending approval held on
  the loop's own record (``metadata.awaiting_approval``), the way the
  server's driver holds its marker. The human answers it through the
  existing verbs (``research_loop_approvals`` / ``research_loop_decide``,
  ``r4 project loop approvals|decide``): ``once`` or ``always`` approve,
  ``no`` rejects. Only :meth:`LoopApprovals.decide` flips a record, and the
  loop's own code never calls it, so nothing is self-approved. Every answer,
  and every question withdrawn because the loop stopped, is journalled on
  the record (``metadata.approvals_log``, the last 50).
- **Wall clock (ADR-067).** ``max_wall_clock_hours`` caps the loop from its
  start; what is left is handed to the iteration (a plan's own wall clock is
  the smaller of the two).

Nothing here is a second job store (CONSTITUTION §6): the loop record is
the local runtime's own, and an approval lives and dies with it.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from r4_contracts.ask_policy import ACTION_KIND_CLASSES, PLAN_RUN
from r4_contracts.ask_policy import TRIGGER_CLASSES as _CLASS_NAMES

MODES = ("auto", "ask", "profile")
TRIGGER_CLASSES = frozenset(_CLASS_NAMES)
PLAN_GATE = "plan_gate"
#: ADR-066 classes of the actions a local loop gates by its mode: the shared
#: table. A plan's own gates (``plan_gate``) are not in it: they always ask
#: (ADR-092 slice 2).
ACTION_CLASSES: Mapping[str, frozenset] = ACTION_KIND_CLASSES
APPROVAL_PREFIX = "local-appr-"
DECISIONS = {"once": "approved", "always": "approved", "no": "rejected"}
MAX_APPROVAL_LOG = 50
_TERMINAL = frozenset({"stopped", "completed", "error"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── policy ──────────────────────────────────────────────────────────────────


def policy_metadata(
    mode: str,
    pause_triggers: Any,
    *,
    plan_loop: bool,
    iteration_plan: str = "",
    max_wall_clock_hours: float = 0.0,
) -> Dict[str, Any]:
    """The loop-metadata keys of its ask policy and caps; a plan loop with no
    mode asks (fail closed: a plan run costs). Unknown modes and trigger
    classes are refused by name."""
    chosen = _mode(mode, plan_loop)
    triggers = _triggers(pause_triggers)
    out: Dict[str, Any] = {"mode": chosen} if chosen else {}
    if chosen == "profile":
        out["pause_profile"] = {"triggers": triggers}
    if iteration_plan:
        out["iteration_plan"] = iteration_plan
    if max_wall_clock_hours and float(max_wall_clock_hours) > 0:
        out["max_wall_clock_hours"] = float(max_wall_clock_hours)
    return out


def _mode(mode: str, plan_loop: bool) -> str:
    chosen = str(mode or "").strip().lower() or ("ask" if plan_loop else "")
    if chosen and chosen not in MODES:
        raise ValueError(f"unknown loop mode {chosen!r} (expected one of {list(MODES)})")
    return chosen


def _triggers(pause_triggers: Any) -> List[str]:
    triggers = sorted({str(t).strip() for t in pause_triggers or () if str(t).strip()})
    unknown = sorted(set(triggers) - TRIGGER_CLASSES)
    if unknown:
        raise ValueError(f"unknown pause trigger(s) {unknown} (known: {sorted(TRIGGER_CLASSES)})")
    return triggers


def action_classes(action: Mapping[str, Any]) -> frozenset:
    """The ADR-066 trigger classes an action carries (unknown kinds: none)."""
    return ACTION_CLASSES.get(str(action.get("kind") or ""), frozenset())


def needs_human(metadata: Mapping[str, Any], action: Mapping[str, Any]) -> bool:
    """Whether the loop's mode parks ``action`` for a human (ADR-066)."""
    mode = str(metadata.get("mode") or "auto")
    if mode == "ask":
        return True
    if mode != "profile":
        return False
    profile = metadata.get("pause_profile")
    triggers = set((profile or {}).get("triggers") or ()) if isinstance(profile, Mapping) else set()
    return bool(action_classes(action) & triggers)


# ── wall clock ──────────────────────────────────────────────────────────────


def remaining_wallclock_sec(metadata: Mapping[str, Any], started_at: str) -> Optional[float]:
    """Seconds of the loop's ADR-067 wall clock left; ``None`` when uncapped.

    An unparseable start stamp counts as just started (the cap still holds
    from now on), as the server's driver reads it."""
    try:
        cap = float(metadata.get("max_wall_clock_hours") or 0.0)
    except (TypeError, ValueError):
        return None
    if cap <= 0:
        return None
    return cap * 3600.0 - _elapsed_sec(started_at)


def _elapsed_sec(started_at: str) -> float:
    try:
        started = datetime.fromisoformat(str(started_at))
    except (TypeError, ValueError):
        return 0.0
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - started).total_seconds())


# ── the approvals list on the loop record ───────────────────────────────────


class LoopApprovals:
    """Pending approvals held on local loop records (``loops.metadata_json``).

    Every change is one ``BEGIN IMMEDIATE`` transaction, so the loop's worker
    and a decision made from another process or runtime instance never
    overwrite each other's metadata.
    """

    def __init__(self, db_path: Any) -> None:
        self._db_path = str(db_path)

    def post(self, loop_id: str, iteration: int, action: Mapping[str, Any]) -> Dict[str, Any]:
        """Park one action on the loop: a pending approval, returned."""
        record = {
            "id": f"{APPROVAL_PREFIX}{uuid.uuid4().hex[:16]}",
            "loop_id": loop_id,
            "iteration": int(iteration),
            "action": dict(action),
            "status": "pending",
            "created_at": _utcnow(),
        }

        def _post(meta: Dict[str, Any]) -> Dict[str, Any]:
            meta["awaiting_approval"] = record
            return record

        return self.update_metadata(loop_id, _post)

    def verdict(self, loop_id: str, approval_id: str) -> Optional[bool]:
        """True approved, False rejected, None still pending (or withdrawn)."""
        meta = self._metadata(loop_id)
        for record in reversed(meta.get("approvals_log") or []):
            if isinstance(record, dict) and record.get("id") == approval_id:
                status = record.get("status")
                return True if status == "approved" else False if status == "rejected" else None
        return None

    def withdraw(self, loop_id: str, approval_id: str, reason: str) -> None:
        """Close a question nobody answered (the loop stopped): journalled as
        ``withdrawn``, never as an answer."""

        def _withdraw(meta: Dict[str, Any]) -> None:
            record = meta.get("awaiting_approval")
            if isinstance(record, dict) and record.get("id") == approval_id:
                record.update(status="withdrawn", reason=reason, closed_at=_utcnow())
                _journal(meta, record)

        self.update_metadata(loop_id, _withdraw)

    def decide(self, approval_id: str, decision: str) -> Dict[str, Any]:
        """The human's answer to one pending approval (``once|always|no``).

        ``always`` on a mode-gated action (a plan run) also turns the loop's
        mode to ``auto``, as the server's inbox does; on a plan gate it is a
        yes to that gate only (gates always ask, ADR-092 slice 2)."""
        status = DECISIONS.get(str(decision))
        if status is None:
            raise ValueError(f"decision must be one of {sorted(DECISIONS)}")
        for loop_id in self._loops_mentioning(approval_id):
            decided = self.update_metadata(loop_id, _decider(approval_id, str(decision), status))
            if decided is not None:
                return dict(decided)
        raise LookupError(
            f"no pending approval {approval_id!r} on the local runtime "
            "(approvals of server-hosted loops live on UMRS, ADR-036)"
        )

    def pending(self, loop_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Every pending approval of a running loop (one loop, or all)."""
        query = "SELECT loop_id, project_id, status, metadata_json FROM loops"
        args: tuple = ()
        if loop_id:
            query += " WHERE loop_id = ?"
            args = (loop_id,)
        rows = self._read(query, args)
        out = []
        for row in rows:
            if row[2] in _TERMINAL:
                continue
            record = json.loads(row[3] or "{}").get("awaiting_approval")
            if isinstance(record, dict) and record.get("status") == "pending":
                out.append({**record, "project_id": row[1], "durability": "local"})
        return out

    # ── storage ──────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=30, isolation_level=None)

    def _read(self, query: str, args: tuple) -> List[Any]:
        with closing(self._connect()) as conn:
            return conn.execute(query, args).fetchall()

    def _metadata(self, loop_id: str) -> Dict[str, Any]:
        rows = self._read("SELECT metadata_json FROM loops WHERE loop_id = ?", (loop_id,))
        return json.loads(rows[0][0] or "{}") if rows else {}

    def _loops_mentioning(self, approval_id: str) -> List[str]:
        rows = self._read(
            "SELECT loop_id FROM loops WHERE metadata_json LIKE ?", (f"%{approval_id}%",)
        )
        return [row[0] for row in rows]

    def update_metadata(self, loop_id: str, change: Callable[[Dict[str, Any]], Any]) -> Any:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT metadata_json FROM loops WHERE loop_id = ?", (loop_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"unknown loop_id for this runtime: {loop_id}")
            meta = json.loads(row[0] or "{}")
            out = change(meta)
            conn.execute(
                "UPDATE loops SET metadata_json = ? WHERE loop_id = ?",
                (json.dumps(meta), loop_id),
            )
            conn.execute("COMMIT")
            return out
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def _decider(approval_id: str, decision: str, status: str) -> Callable[[Dict[str, Any]], Any]:
    def _decide(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        record = meta.get("awaiting_approval")
        if not (
            isinstance(record, dict)
            and record.get("id") == approval_id
            and record.get("status") == "pending"
        ):
            return None
        record.update(status=status, decision=decision, decided_by="human", decided_at=_utcnow())
        if decision == "always" and action_classes(record.get("action") or {}):
            meta["mode"] = "auto"
            meta.pop("pause_profile", None)
        _journal(meta, record)
        return record

    return _decide


def _journal(meta: Dict[str, Any], record: Dict[str, Any]) -> None:
    """Move the closed record from ``awaiting_approval`` to the log."""
    log = [r for r in meta.get("approvals_log") or [] if isinstance(r, dict)]
    meta["approvals_log"] = (log + [record])[-MAX_APPROVAL_LOG:]
    meta.pop("awaiting_approval", None)


__all__ = [
    "ACTION_CLASSES",
    "APPROVAL_PREFIX",
    "DECISIONS",
    "MODES",
    "PLAN_GATE",
    "PLAN_RUN",
    "LoopApprovals",
    "action_classes",
    "needs_human",
    "policy_metadata",
    "remaining_wallclock_sec",
]
