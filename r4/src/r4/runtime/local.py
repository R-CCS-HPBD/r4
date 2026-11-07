"""Local ResearchRuntime — SQLite + in-process execution threads.

Scope: CI, demos, offline development, prototyping, small local loops.
Same :class:`ResearchRuntime` API as :class:`UmrsRuntime`; the difference is
durability class (SQLite on local filesystem, single process) and scale
(iterations run in daemon threads instead of Slurm/K8s jobs).

Authority: a loop created here is owned by this runtime's SQLite store.
It is never mirrored to UMRS implicitly; moving a loop across runtimes is
an explicit future export/import operation.

v1 execution model: ``start_loop`` registers durable state and launches one
daemon thread that advances iterations. Each iteration invokes the caller's
``executor`` callback (the work of the loop — in production wiring, the
Companion's agent). The default executor is a no-op placeholder so CI can
exercise lifecycle transitions without a model.

ADR-092 slice 6 (the D-29 tail): an iteration may run a team plan. Such an
executor (``is_loop_iteration_executor``) declares its action, which the
loop gates by its ADR-066 ask policy before it runs (an ask-mode loop parks
on its own approvals list until a human decides through the existing decide
path); it gets the loop's remaining ADR-067 wall clock and a way to ask the
loop's human (its plan's gates); and the loop acts on the continue / publish
/ stop decision it returns. Still one loop and one driver: this thread (see
``local_policy``). A stop or pause made from another runtime instance (every
chat tool call builds a fresh one) is read from the store at each iteration
boundary and while an iteration waits on a human.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import local_policy
from .api import (
    DECISION_CONTINUE,
    DECISION_PUBLISH,
    STATUS_COMPLETED,
    STATUS_ERROR,
    STATUS_PAUSED,
    STATUS_RUNNING,
    STATUS_STOPPED,
    TERMINAL_STATUSES,
    IterationContext,
    IterationState,
    LoopHalted,
    LoopSpec,
    LoopState,
    RuntimeCapabilities,
    is_loop_iteration_executor,
    loop_decision_of,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS loops (
    loop_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    research_goal TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    current_iteration INTEGER NOT NULL,
    max_iterations INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS iterations (
    iteration_id TEXT PRIMARY KEY,
    loop_id TEXT NOT NULL,
    iteration_number INTEGER NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    summary_json TEXT NOT NULL DEFAULT '{}'
);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_db_path() -> Path:
    override = os.environ.get("R4_RUNTIME_DB")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "r4" / "runtime" / "local.db"


def _default_executor(loop_id: str, iteration_number: int, research_goal: str) -> Dict:
    """Placeholder iteration: no work, just a record (CI-friendly default)."""
    del loop_id, research_goal
    return {"note": "no executor bound (local dev runtime)", "iteration": iteration_number}


class _LoopControl:
    """In-memory stop/pause signals for one running loop thread."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.resumed = threading.Event()
        self.resumed.set()


class LocalRuntime:
    """ResearchRuntime executing loops in-process with SQLite-durable state."""

    def __init__(self, db_path: Optional[Path] = None, *, poll_seconds: float = 0.5) -> None:
        self._db_path = Path(db_path) if db_path else _default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._controls: Dict[str, _LoopControl] = {}
        self._poll_seconds = poll_seconds
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        self._approvals = local_policy.LoopApprovals(self._db_path)

    def capabilities(self) -> RuntimeCapabilities:
        # ADR-092 slice 6: a loop that runs a plan parks on its own record's
        # approvals list, so this runtime has an inbox (process-local).
        return RuntimeCapabilities(
            location="local", durable=True, distributed=False, backends=(), approvals=True
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    # ── lifecycle ────────────────────────────────────────────────────

    def start_loop(self, spec: LoopSpec, executor=None) -> LoopState:
        if not spec.research_goal.strip():
            raise ValueError("research_goal must be non-empty")
        if spec.max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        if spec.max_tracks > 1:
            raise ValueError(
                "parallel hypothesis tracks (max_tracks > 1) run on the UMRS engine "
                "only — the local runtime is single-branch"
            )
        policy = local_policy.policy_metadata(
            spec.mode,
            spec.pause_triggers,
            plan_loop=bool(spec.iteration_plan) or is_loop_iteration_executor(executor),
            iteration_plan=spec.iteration_plan,
            max_wall_clock_hours=spec.max_wall_clock_hours,
        )
        state = LoopState(
            loop_id=f"local-{uuid.uuid4().hex[:12]}",
            project_id=spec.project_id,
            research_goal=spec.research_goal,
            status=STATUS_RUNNING,
            phase="planning",
            current_iteration=0,
            max_iterations=spec.max_iterations,
            started_at=_utcnow(),
            message=f"Local loop started for project {spec.project_id}",
            metadata={
                "simulated": executor is None,
                "requires": dict(spec.requires),
                **dict(spec.metadata),
                **policy,
            },
        )
        self._insert_loop(state)
        control = _LoopControl()
        self._controls[state.loop_id] = control
        worker = threading.Thread(
            target=self._run_loop,
            args=(state.loop_id, executor or _default_executor, control),
            daemon=True,
            name=f"r4-local-loop-{state.loop_id}",
        )
        worker.start()
        # Return the start-time snapshot: re-reading the row here races the
        # worker (a loop with a trivial executor can already be completed),
        # which made "start returns running" timing-dependent. Callers poll
        # get_loop for progress either way.
        return state

    def stop_loop(self, loop_id: str) -> LoopState:
        control = self._controls.get(loop_id)
        if control is not None:
            control.stop.set()
            control.resumed.set()  # unblock a paused worker so it can exit
        return self._transition_terminal(loop_id, STATUS_STOPPED)

    def pause_loop(self, loop_id: str) -> LoopState:
        self._require_active(loop_id)
        control = self._controls.get(loop_id)
        if control is not None:
            control.resumed.clear()
        self._set_status(loop_id, STATUS_PAUSED)
        return self.get_loop(loop_id)

    def list_approvals(self, loop_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Pending approvals of running local loops (ADR-092 slice 6): a plan
        run an ask-mode loop parked at, or a gate its plan raised."""
        return self._approvals.pending(loop_id)

    def decide_approval(self, approval_id: str, decision: str) -> Dict[str, Any]:
        """The human's answer (``once|always|no``). This is the only path
        that decides; the loop itself never does. An id not pending here is
        a LookupError naming UMRS (server-hosted loops' inbox)."""
        return self._approvals.decide(approval_id, decision)

    def resume_loop(self, loop_id: str) -> LoopState:
        state = self.get_loop(loop_id)
        if state.status != STATUS_PAUSED:
            raise ValueError(f"cannot resume loop in status {state.status}")
        control = self._controls.get(loop_id)
        if control is not None:
            control.resumed.set()
        self._set_status(loop_id, STATUS_RUNNING)
        return self.get_loop(loop_id)

    # ── introspection ──────────────────────────────────────────────────

    def get_loop(self, loop_id: str) -> LoopState:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM loops WHERE loop_id = ?", (loop_id,)).fetchone()
        if row is None:
            raise LookupError(f"unknown loop_id for this runtime: {loop_id}")
        return LoopState(
            loop_id=row["loop_id"],
            project_id=row["project_id"],
            research_goal=row["research_goal"],
            status=row["status"],
            phase=row["phase"],
            current_iteration=row["current_iteration"],
            max_iterations=row["max_iterations"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            message=row["message"],
            metadata=json.loads(row["metadata_json"]),
        )

    def list_loops(self, project_id: Optional[str] = None) -> List[LoopState]:
        query = "SELECT loop_id FROM loops"
        args: tuple = ()
        if project_id:
            query += " WHERE project_id = ?"
            args = (project_id,)
        with self._connect() as conn:
            ids = [row["loop_id"] for row in conn.execute(query, args).fetchall()]
        return [self.get_loop(loop_id) for loop_id in ids]

    def list_iterations(self, loop_id: str, limit: int = 50) -> List[IterationState]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM iterations WHERE loop_id = ? ORDER BY iteration_number DESC LIMIT ?",
                (loop_id, limit),
            ).fetchall()
        return [
            IterationState(
                iteration_id=row["iteration_id"],
                loop_id=row["loop_id"],
                iteration_number=row["iteration_number"],
                phase=row["phase"],
                status=row["status"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                summary=json.loads(row["summary_json"]),
            )
            for row in rows
        ]

    # ── internal ────────────────────────────────────────────────────────

    def _insert_loop(self, state: LoopState) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO loops (loop_id, project_id, research_goal, status, phase,"
                " current_iteration, max_iterations, started_at, completed_at, message,"
                " metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    state.loop_id,
                    state.project_id,
                    state.research_goal,
                    state.status,
                    state.phase,
                    state.current_iteration,
                    state.max_iterations,
                    state.started_at,
                    state.completed_at,
                    state.message,
                    json.dumps(state.metadata),
                ),
            )

    def _set_status(self, loop_id: str, status: str, phase: Optional[str] = None) -> None:
        with self._lock, self._connect() as conn:
            if phase is None:
                conn.execute("UPDATE loops SET status = ? WHERE loop_id = ?", (status, loop_id))
            else:
                conn.execute(
                    "UPDATE loops SET status = ?, phase = ? WHERE loop_id = ?",
                    (status, phase, loop_id),
                )

    def _require_active(self, loop_id: str) -> None:
        state = self.get_loop(loop_id)
        if state.status != STATUS_RUNNING:
            raise ValueError(f"cannot pause loop in status {state.status}")

    def _transition_terminal(self, loop_id: str, status: str) -> LoopState:
        state = self.get_loop(loop_id)
        if state.status in (STATUS_STOPPED, STATUS_COMPLETED, STATUS_ERROR):
            return state  # idempotent: already terminal
        # Phase flips in the same write so a reader can never observe
        # terminal=status with a mid-iteration phase between two UPDATEs.
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE loops SET status = ?, completed_at = ?, phase = ? WHERE loop_id = ?",
                (status, _utcnow(), status, loop_id),
            )
        return self.get_loop(loop_id)

    def _run_loop(self, loop_id: str, executor, control: _LoopControl) -> None:
        try:
            self._advance_iterations(loop_id, executor, control)
        except Exception as exc:  # noqa: BLE001 - loop must never kill the process
            self._transition_terminal(loop_id, STATUS_ERROR)
            with self._connect() as conn:
                conn.execute(
                    "UPDATE loops SET message = ? WHERE loop_id = ?",
                    (f"local loop failed: {exc}", loop_id),
                )
        finally:
            self._controls.pop(loop_id, None)

    def _advance_iterations(self, loop_id: str, executor, control: _LoopControl) -> None:
        state = self.get_loop(loop_id)
        for number in range(state.current_iteration + 1, state.max_iterations + 1):
            halt = self._boundary(loop_id, control)
            if halt:
                self._end(loop_id, STATUS_STOPPED, halt)
                return
            summary = self._run_iteration(loop_id, number, state.research_goal, executor, control)
            decision = _decision(executor, summary)
            if decision["decision"] != DECISION_CONTINUE:
                self._end_by_decision(loop_id, number, decision)
                return
        self._transition_terminal(loop_id, STATUS_COMPLETED)
        self._set_message(loop_id, "local loop completed")

    def _boundary(self, loop_id: str, control: _LoopControl) -> str:
        """Wait out a pause; ``""`` to go on, else why the loop stops here:
        a stop (this instance's or the store's) or the ADR-067 wall clock."""
        control.resumed.wait()
        while self._store_status(loop_id) == STATUS_PAUSED and not control.stop.is_set():
            time.sleep(self._poll_seconds)
        if self._stop_requested(loop_id, control):
            return "stopped"
        remaining = self._remaining_wallclock(loop_id)
        if remaining is not None and remaining <= 0:
            return "budget_exhausted:wall_clock"
        return ""

    def _end_by_decision(self, loop_id: str, number: int, decision: Dict[str, str]) -> None:
        """The iteration's own decision ends the loop (ADR-092 slice 6): a
        publish completes it, a stop stops it; the reason is recorded."""
        if decision["decision"] == DECISION_PUBLISH:
            self._end(loop_id, STATUS_COMPLETED, f"published:{decision['reason']}", number)
        else:
            self._end(loop_id, STATUS_STOPPED, decision["reason"] or "stopped", number)

    def _end(self, loop_id: str, status: str, reason: str, number: int = 0) -> None:
        """Terminal transition with its ``state_reason``; a loop already
        ended (stopped by a person) keeps its own record."""
        if self._store_status(loop_id) in TERMINAL_STATUSES:
            return
        if reason != "stopped":

            def _note(meta: Dict[str, Any]) -> None:
                meta["state_reason"] = reason
                if number:
                    meta["decided_at_iteration"] = number

            self._approvals.update_metadata(loop_id, _note)
            self._set_message(loop_id, f"local loop ended: {reason}"[:500])
        self._transition_terminal(loop_id, status)

    def _set_message(self, loop_id: str, message: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE loops SET message = ? WHERE loop_id = ?", (message, loop_id))

    def _store_status(self, loop_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT status FROM loops WHERE loop_id = ?", (loop_id,)).fetchone()
        return row["status"] if row else STATUS_STOPPED

    def _stop_requested(self, loop_id: str, control: _LoopControl) -> bool:
        return control.stop.is_set() or self._store_status(loop_id) in TERMINAL_STATUSES

    def _remaining_wallclock(self, loop_id: str) -> Optional[float]:
        state = self.get_loop(loop_id)
        return local_policy.remaining_wallclock_sec(state.metadata, state.started_at)

    def _run_iteration(
        self, loop_id: str, number: int, goal: str, executor, control: _LoopControl
    ) -> Dict[str, Any]:
        iteration_id = f"{loop_id}-it{number}"
        started = _utcnow()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE loops SET current_iteration = ?, phase = ? WHERE loop_id = ?",
                (number, "experiment_execution", loop_id),
            )
        status = "completed"
        if is_loop_iteration_executor(executor):
            status, summary = self._run_governed(loop_id, number, goal, executor, control)
        else:
            summary = executor(loop_id, number, goal)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO iterations (iteration_id, loop_id, iteration_number, phase,"
                " status, started_at, completed_at, summary_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    iteration_id,
                    loop_id,
                    number,
                    "evaluation",
                    status,
                    started,
                    _utcnow(),
                    json.dumps(summary or {}),
                ),
            )
        return summary or {}

    # ── ADR-092 slice 6: an iteration under the loop's policy ──────────────

    def _run_governed(
        self, loop_id: str, number: int, goal: str, executor, control: _LoopControl
    ) -> tuple:
        """Gate the executor's action by the loop's ask policy, then run it
        with the loop's context; ``(iteration status, summary)``."""
        ctx = self._context(loop_id, number, goal, control)
        action = executor.iteration_action(ctx)
        try:
            if action and not self._cleared(loop_id, number, action, control):
                kind = str(action.get("kind") or "action")
                return "rejected", _ended(number, action, f"approval_rejected:{kind}")
            return "completed", executor.run_iteration(ctx)
        except LoopHalted as exc:
            return "halted", _ended(number, action or {}, f"halted:{exc}")

    def _context(
        self, loop_id: str, number: int, goal: str, control: _LoopControl
    ) -> IterationContext:
        state = self.get_loop(loop_id)
        return IterationContext(
            loop_id=loop_id,
            project_id=state.project_id,
            iteration_number=number,
            research_goal=goal,
            remaining_wallclock_sec=self._remaining_wallclock(loop_id),
            ask_human=lambda action: self._ask_human(loop_id, number, action, control),
            stop_requested=lambda: self._stop_requested(loop_id, control),
        )

    def _cleared(self, loop_id: str, number: int, action: Dict[str, Any], control) -> bool:
        """The loop's mode lets the action run, or a human said yes (ADR-066).
        Read from the store each time: an ``always`` answer turns it auto."""
        if not local_policy.needs_human(self.get_loop(loop_id).metadata, action):
            return True
        return self._ask_human(loop_id, number, action, control)

    def _ask_human(self, loop_id: str, number: int, action: Dict[str, Any], control) -> bool:
        """Park on the loop's approvals list until a human decides; never
        decides itself. A stop first withdraws the question (LoopHalted)."""
        record = self._approvals.post(loop_id, number, action)
        self._set_phase(loop_id, "awaiting_approval")
        try:
            while True:
                verdict = self._approvals.verdict(loop_id, record["id"])
                if verdict is not None:
                    return verdict
                if self._stop_requested(loop_id, control):
                    self._approvals.withdraw(loop_id, record["id"], "loop_stopped")
                    raise LoopHalted("the loop was stopped while it waited on a human")
                if _host_exiting():
                    # A plan gate waits on a non-daemon pool thread: without
                    # this an unanswered question would hold the process open.
                    self._approvals.withdraw(loop_id, record["id"], "host_exit")
                    raise LoopHalted("the process hosting the loop is exiting")
                time.sleep(self._poll_seconds)
        finally:
            self._set_phase(loop_id, "experiment_execution")

    def _set_phase(self, loop_id: str, phase: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE loops SET phase = ? WHERE loop_id = ? AND status NOT IN (?, ?, ?)",
                (phase, loop_id, *sorted(TERMINAL_STATUSES)),
            )


def _host_exiting() -> bool:
    """The interpreter is shutting down (its main thread has finished)."""
    return not threading.main_thread().is_alive()


def _decision(executor: Any, summary: Dict[str, Any]) -> Dict[str, str]:
    """The iteration's loop decision; only a governed executor makes one (a
    plain executor's summary is a record, never a control signal)."""
    if not is_loop_iteration_executor(executor):
        return {"decision": DECISION_CONTINUE, "reason": ""}
    return loop_decision_of(summary)


def _ended(number: int, action: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """The summary of an iteration that did not run: the loop stops."""
    return {
        "iteration": number,
        "phase": "not_run",
        "action": dict(action),
        "loop_decision": {"decision": "stop", "reason": reason},
    }
