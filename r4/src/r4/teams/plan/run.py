"""One attempt of a team plan (ADR-092 D-3, D-4, D-7, D-8).

The walk is phases in order, then steps in order within a phase.
Parallelism comes only from the data: the replicas of a fan-out and the
items of a map run concurrently, under one semaphore of ``concurrency``
slots shared by every agent call of the run. A map runs each item's stages
as one coroutine chain, so stage n+1 of an item starts as soon as stage n
of THAT item is done. There is no barrier between stages across items; the
map step completes when every item has finished.

Failures never raise. A failed, refused, cancelled or schema-invalid agent
is a ``null`` with a recorded reason (D-4), and the run ends ``success``,
``partial``, ``failed`` or ``cancelled`` with exactly one terminal event.

Budgets (D-7): the AgentBroker charges tokens per model round into
``services.budget``; this engine never charges. Before starting an agent
it refuses when no tokens remain, when ``max_agents`` agents have started
(a backstop, since the validator already bounds the plan), when the wall
clock is spent, or when the summed tool calls reached ``max_tool_calls``.

Taint (ADR-039, D-6) follows the data: an agent's ``taint_out`` is
imported into every later agent whose prompt reads that step's output, into
every stage of a map over it, and through every reduce (top-level or stage)
that reads it. Where a cap bounds the excerpts carried, the cut is recorded
(a log line and ``taint_truncated`` in result.json), never silent.

Slice 2 (D-4): a ``repeat`` runs its body in a frame of its own per
iteration (its body's outputs and taint never leak to later steps; only its
own output does) until the ``collect`` step reports nothing outside the
seen-set, or ``max_iterations``. A nested ``plan`` runs its inlined program
in a frame with its own params and steps. A ``gate`` asks the broker for a
human answer (``human_input_requested``); an answer is journalled, so a
resume replays it rather than asking again, while a refusal for want of a
human is not an answer and is asked again. A gate that is not approved
stops the run: it ends ``cancelled`` with the gate's reason. The time a gate
waits for a human is not charged to the wall-clock ceiling.

Isolation (slice 5): an agent of an ``agent`` or ``map`` step with
``isolation: worktree`` or ``copy`` runs in a workspace of its own under the
run directory (each replica its own; a map item's stages share one), made
when it first runs live and named to the broker in the request's metadata.
A mode the project cannot have (no git repository, a tree over the copy cap)
fails the run by name before any agent starts. A ``merge_back`` step asks a
human, through the gate path, to approve the change set of its isolated
steps' workspaces, then applies it to the project root as file writes; a
conflict is refused by name, never resolved. The approval and the applied
merge are journalled, so a resume neither asks again nor applies twice. At
run end every workspace is removed unless a merge-back that names its step
is still pending.

Cancel (D-3): a plan stops as a unit. ``cancel()`` and a cancelled run task
(Ctrl-C ending ``asyncio.run`` cancels every task) both cancel the broker,
whose own cancel reaches turns running in worker threads, and every started
agent settles with exactly one AGENT_COMPLETED before the terminal event.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
import unicodedata
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from r4_contracts.taint import TaintedSegment
from r4_contracts.team_engine.events import TERMINAL_EVENT_KINDS, EventKind, TeamEvent
from r4_contracts.team_engine.plan import (
    DEFAULT_MAX_AGENTS,
    ISOLATED_MODES,
    MERGE_BACK_CLASS,
    apply_reduce,
    canonical_hash,
    item_key,
    iter_agent_parts,
    iter_steps,
    profile_for,
    render,
    resolve_path,
    step_isolation,
    template_refs,
    template_roots,
    validate_output,
)
from r4_contracts.team_engine.results import (
    CheckpointRef,
    ExecutionGraph,
    NodeState,
    RunMetrics,
    TeamResult,
)
from r4_contracts.team_engine.services import (
    AgentTurnRequest,
    AgentTurnResult,
    GateDecision,
    GateRequest,
)
from r4_teamengine_spi.context import RuntimeServices
from r4_teamengine_spi.engine import EngineRun
from r4_teamengine_spi.errors import IncompatibleCheckpoint

from .isolation import MergeBatch, RunWorkspaces, merge_prompt
from .journal import DURABLE_FAILED, JOURNAL_LABEL, PlanJournal
from .workspace import PROJECT_ROOT_KEY, WorkspaceError, preflight, workspace_id

if TYPE_CHECKING:
    from .engine import PlanProgram

logger = logging.getLogger(__name__)

ENGINE_NAME = "plan"
ENGINE_VERSION = "0.1.0"

BUDGET_WARN_FRACTION = 0.8
DEFAULT_CONCURRENCY = 4  # plan.py's default when a plan names none
MAX_LABEL_CHARS = 80
MAX_LOG_CHARS = 2000
MAX_SUMMARY_CHARS = 4000
# Slice 8: the declared outputs ride the durable close (UMRS keeps at most
# 16 KiB of summary): what a server-hosted run's client reads as its result.
MAX_CLOSE_OUTPUT_BYTES = 12_000
MAX_NULL_REASONS = 20
MAX_TAINT_IN = 128
MAX_RUN_TAINT = 256
MAX_REASON_CHARS = 160
MAX_GATE_PROMPT_CHARS = 4000
MAX_RETRIEVAL_REFS = 16
# ADR-092 slice 3: an AGENT_COMPLETED event carries a bounded preview of the
# agent's answer (its output, or a null's text), so a surface can show each
# agent's result as it lands; result.json and the journal stay the record.
MAX_EVENT_PREVIEW_CHARS = 600
RUN_TASK_CANCELLED = "run task cancelled"
RUN_TAINT_KEY = "(run)"  # result.json taint_dropped key for the run-level cap
# How long an engine error or a cancelled run task waits for the turns it
# halted to settle; a broker that ignores cancellation cannot hold it longer.
DRAIN_TIMEOUT_SEC = 5.0

_ENGINE_ACTOR = ("engine", ENGINE_NAME)
_RUNNERS = {
    "agent": "_agent_step",
    "map": "_map_step",
    "reduce": "_reduce_step",
    "repeat": "_repeat_step",
    "gate": "_gate_step",
    "plan": "_nested_plan_step",
    "merge_back": "_merge_back_step",
}
_GUARDS = (
    ("tokens", "_tokens_out"),
    ("agents", "_agents_out"),
    ("wallclock", "_wallclock_out"),
    ("tool_calls", "_tool_calls_out"),
)
_TERMINAL = {
    "success": EventKind.RUN_COMPLETED,
    "partial": EventKind.RUN_COMPLETED,
    "failed": EventKind.RUN_FAILED,
    "cancelled": EventKind.RUN_CANCELLED,
}
_RUN_STATE = {"success": "done", "partial": "done", "failed": "failed", "cancelled": "cancelled"}

# The note the broker appends to ``AgentTurnResult.text`` when its fork
# refused a tool (``[refused tools: a, b; why]``, agent_broker._Turn); it is
# always the text's last line, so only a note at the very end counts.
_REFUSED_NOTE_RE = re.compile(r"\[refused tools: ([^;\]\n]*);[^\]\n]*\]\s*\Z")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
# Brackets read as the approval card's ``[agent <label> · plan <id>]`` frame.
_LABEL_NEUTRAL = str.maketrans({"[": "(", "]": ")", "\uff3b": "(", "\uff3d": ")", "\u00b7": "-"})

Taint = tuple[TaintedSegment, ...]


@dataclass(frozen=True)
class _Call:
    """Where one agent call sits in the walk: its step, item, stage and replica."""

    step_id: str  # the top-level step (the map, for a stage)
    phase_id: str
    node: str  # journal node path: "<id>" or "<id>.stages[<n>]"
    step: dict[str, Any]  # the agent step or stage
    scope: dict[str, Any]
    label: str  # the label template
    item_index: int | None = None
    item_key: str | None = None
    stage: int | None = None
    replica: int | None = None
    taint: Taint = ()
    head: str = ""  # the frame's label prefix ("<plan step>/")
    tail: str = ""  # the frame's label suffix ("@<iteration>")
    # Slice 5: the step's (or its map's) isolation, and the key of the
    # workspace this call runs in (a map item's stages share one).
    isolation: str = "none"
    ws_key: str | None = None


@dataclass
class _Frame:
    """Where a step runs: the scope it renders in and writes its output to,
    the taint of the steps it can read, and how its agents are named.

    The run's own frame is the plan's top level. A repeat iteration and a
    nested plan each get a frame of their own, so their steps' outputs and
    taint stay inside (D-4: only the repeat's or the plan step's own output
    is visible after it). ``owner`` is the top-level step whose tally, node
    and journal ``step`` field count the frame's agents; ``phase`` is the
    top-level phase, so ``--from-phase`` reaches into nested frames (D-8).
    ``roots`` is the taint of agent-derived roots other than ``steps``
    (a repeat's ``seen``).
    """

    scope: dict[str, Any]
    taint: dict[str, Taint]
    prefix: str = ""
    owner: str | None = None
    phase: str | None = None
    head: str = ""
    tail: str = ""
    roots: dict[str, Taint] = field(default_factory=dict)

    def node(self, step_id: str) -> str:
        return f"{self.prefix}{step_id}"

    def owner_of(self, step_id: str) -> str:
        return self.owner or step_id

    def phase_of(self, phase: dict[str, Any]) -> str:
        return self.phase or str(phase.get("id"))


@dataclass
class _Tally:
    """Per-step ok and null counts with the distinct null reasons (D-9)."""

    ok: int = 0
    null: int = 0
    replayed: int = 0
    dropped: int = 0
    reasons: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    def add(self, ok: bool, reason: str = "", *, replayed: bool = False) -> None:
        if ok:
            self.ok += 1
            self.replayed += int(replayed)
            return
        self.null += 1
        if reason not in self.reasons and len(self.reasons) < MAX_NULL_REASONS:
            self.reasons.append(reason)

    def add_refused(self, tools: Iterable[Any]) -> None:
        """Tools the broker refused this step's agents, ok or not (D-6)."""
        for tool in tools:
            if isinstance(tool, str) and tool not in self.refused:
                self.refused.append(tool)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "null": self.null,
            "null_reasons": list(self.reasons),
            "replayed": self.replayed,
        }
        if self.dropped:
            out["dropped_items"] = self.dropped
        if self.refused:
            out["refused_tools"] = sorted(self.refused)
        return out

    def node_status(self) -> str:
        return "failed" if self.null and not self.ok else "done"


@dataclass
class _Usage:
    """Broker-reported usage, summed over this attempt's live turns."""

    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0

    def add(self, usage: dict[str, Any] | None) -> int:
        """Add one turn's usage; return its tokens."""
        for key in ("input_tokens", "output_tokens", "llm_calls", "tool_calls"):
            setattr(self, key, getattr(self, key) + _as_int((usage or {}).get(key)))
        return _as_int((usage or {}).get("input_tokens")) + _as_int(
            (usage or {}).get("output_tokens")
        )

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class PlanRun(EngineRun):
    """One attempt of a plan run over ``services.run.working_dir``.

    Execution starts on the first ``events()`` iteration (ADR-020). The run
    directory holds ``plan.json`` and ``params.json`` (written at start),
    ``journal.jsonl`` (one record per live agent call) and ``result.json``
    (written once, at the end).
    """

    def __init__(self, plan: PlanProgram, services: RuntimeServices) -> None:
        self._plan = plan
        self._program = plan.program
        self._svc = services
        self._dir = Path(services.run.working_dir)
        # Slice 7: the journal is the cache of the run's durable record (UMRS)
        # when the services name one; its failure is reported here.
        self._journal = PlanJournal(
            self._dir, getattr(services, "records", None), on_failure=self._durable_failed
        )
        self._durable_halt: str | None = None
        self._durable_notes: list[str] = []
        self._budget: dict[str, Any] = dict(self._program.get("budget") or {})
        self._scope: dict[str, Any] = {
            "params": dict(self._program.get("_bound_params") or {}),
            "steps": {},
        }
        self._status = "pending"
        self._task: asyncio.Future[None] | None = None
        self._result: TeamResult | None = None
        self._error: str | None = None
        self._cancel_reason: str | None = None
        self._task_cancelled = False
        self._broker_stopped = False
        self._taint_dropped: dict[str, int] = {}
        self._resumed_from: str | None = None
        self._sem: asyncio.Semaphore | None = None
        self._inflight: set[asyncio.Future[AgentTurnResult]] = set()
        self._memo: dict[str, dict[str, Any]] = {}
        self._tallies: dict[str, _Tally] = {}
        self._nodes: dict[str, str] = {}
        self._taint: dict[str, Taint] = {}
        self._top = _Frame(self._scope, self._taint)
        self._gate_wait = 0.0
        self._humans = 0
        self._gates: dict[str, dict[str, Any]] = {}
        self._usage = _Usage()
        self._models: set[str] = set()
        self._agents_started = 0
        self._exhausted: set[str] = set()
        self._warned = False
        self._checkpoints = 0
        self._t0: float | None = None
        self._t1: float | None = None
        self._first_seq: int | None = None
        self._last_seq = -1
        self._terminal = False
        self._log: list[TeamEvent] = []
        self._queues: list[asyncio.Queue[TeamEvent | None]] = []
        # Slice 5: the project root, its isolated workspaces, the merge-backs
        # done (applied or declined by a human) and the cleanup report.
        self._root: str | None = (plan.spec.context or {}).get(PROJECT_ROOT_KEY) or None
        self._ws = RunWorkspaces(self._dir, self._journal, self._root)
        self._ws_lock: asyncio.Lock | None = None
        self._merged: set[str] = set()
        self._ws_report: dict[str, Any] = {}

    @property
    def run_id(self) -> str:
        return self._svc.run.run_id

    # ── events ──────────────────────────────────────────────────────────────

    async def events(self) -> AsyncIterator[TeamEvent]:  # type: ignore[override]
        """This run's events until its terminal one; re-iterable (replays).

        A sink with ``subscribe()`` (FileEventSink, MemoryEventSink) is
        followed directly, so events a broker emits into it arrive too;
        events emitted to the sink before this run's first one are skipped.
        Any other sink is followed through the run's own fan-out.
        """
        queue, from_sink = self._subscribe()
        if self._task is None and self._status == "pending":
            self._task = asyncio.ensure_future(self._execute())
        while True:
            event = await queue.get()
            if event is None:
                return
            if from_sink and not self._is_own(event):
                continue
            yield event
            if event.kind in TERMINAL_EVENT_KINDS:
                return

    def _subscribe(self) -> tuple[asyncio.Queue[TeamEvent | None], bool]:
        subscribe = getattr(self._svc.events, "subscribe", None)
        if callable(subscribe):
            return subscribe(), True
        queue: asyncio.Queue[TeamEvent | None] = asyncio.Queue()
        for event in self._log:
            queue.put_nowait(event)
        self._queues.append(queue)
        return queue, False

    def _is_own(self, event: TeamEvent) -> bool:
        return self._first_seq is not None and event.seq >= self._first_seq

    def _emit(self, kind: EventKind, actor: tuple[str, str], payload: dict[str, Any]) -> None:
        """Emit through the sink; nothing follows the terminal event."""
        if self._terminal:
            return
        event = self._svc.events.emit(kind.value, actor, payload)
        self._terminal = kind in TERMINAL_EVENT_KINDS
        if self._first_seq is None:
            self._first_seq = event.seq
        self._last_seq = event.seq
        self._log.append(event)
        for queue in self._queues:
            queue.put_nowait(event)

    def _message(self, payload: dict[str, Any]) -> None:
        """A progress message (phase, log, budget); silent once cancelled."""
        if self._cancel_reason is None:
            self._emit(EventKind.AGENT_MESSAGE, _ENGINE_ACTOR, payload)

    def _emit_agent(
        self, kind: EventKind, call: _Call, request: AgentTurnRequest, **extra: Any
    ) -> None:
        payload: dict[str, Any] = {
            "step": call.step_id,
            "phase": call.phase_id,
            "label": request.label,
            "item_index": call.item_index,
            "replica": call.replica,
            "stage": call.stage,
            "model": request.model,
            "ok": None,
            "reason": "",
            "tokens": 0,
            "replayed": False,
            "call_key": request.call_key,
            "refused_tools": [],
            "preview": "",
        }
        payload.update(extra)
        self._emit(kind, ("agent", request.label), payload)

    # ── the walk ────────────────────────────────────────────────────────────

    async def _execute(self) -> None:
        """The walk, and exactly one terminal event however it ends.

        A cancelled run task and an engine error both stop the broker and
        let the halted turns settle (``_drain``) before the terminal event,
        so no started agent loses its AGENT_COMPLETED or its journal record
        (D-3, D-8).
        """
        try:
            self._begin()
            if self._durable_halt is not None:
                self._fail(self._durable_halt)
                return
            if self._svc.agents is None:
                self._fail("agent broker not wired")
                return
            refused = await asyncio.to_thread(self._workspace_preflight)
            if refused is not None:
                self._fail(refused)
                return
            await self._walk()
            if self._task_cancelled:  # a turn absorbed the run task's cancel
                raise asyncio.CancelledError
            self._finish()
        except asyncio.CancelledError:
            self._cancel_from_task()
            await self._drain()
            self._finish()
            raise
        except Exception as exc:  # noqa: BLE001 — the engine boundary must not leak a crash
            self._error = f"engine_error: {type(exc).__name__}: {exc}"
            self._stop_turns(self._error)
            await self._drain()
            self._fail(self._error)

    def _begin(self) -> None:
        self._status = "running"
        self._t0 = time.monotonic()
        self._memo = self._journal.load()
        self._ws.load()
        self._emit(EventKind.RUN_STARTED, _ENGINE_ACTOR, self._started_payload())
        for text in self._durable_notes:  # a failure while loading, reported once started
            self._message({"type": "durable", "text": text})
        self._durable_notes.clear()
        _write_json(self._dir / "plan.json", self._program)
        _write_json(self._dir / "params.json", self._scope["params"])

    def _started_payload(self) -> dict[str, Any]:
        return {
            "plan": self._program.get("name"),
            "plan_hash": self._plan.plan_hash,
            "attempt": self._svc.run.attempt_id,
            "resumed_from": self._resumed_from,
            "agent_bound": self._plan.agent_bound,
            "concurrency": self._concurrency(),
            "budget": self._budget,
            "journal": JOURNAL_LABEL,
            "replayable": sum(1 for record in self._memo.values() if record.get("ok")),
            "durable": self._journal.report(),
            "warnings": [f"{w.field}: {w.message}" for w in self._plan.warnings],
        }

    async def _walk(self) -> None:
        for phase in self._program.get("phases") or []:
            self._message({"type": "phase", "id": phase.get("id"), "title": phase.get("title", "")})
            for step in phase.get("steps") or []:
                await self._run_step(phase, step, self._top)

    async def _run_step(self, phase: dict[str, Any], step: dict[str, Any], frame: _Frame) -> None:
        step_id = step["id"]
        top = frame.owner is None
        if top:
            self._nodes[step_id] = "running"
        tally = self._tally(frame.owner_of(step_id))
        if step.get("log"):
            text = render(step["log"], frame.scope, envelope=False)
            self._message({"type": "log", "step": step_id, "text": text[:MAX_LOG_CHARS]})
        output = await getattr(self, _RUNNERS[step["kind"]])(phase, step, frame)
        frame.scope["steps"][step_id] = {"output": output}
        if top:
            self._nodes[step_id] = tally.node_status()

    def _base_call(self, phase: dict[str, Any], step: dict[str, Any], frame: _Frame) -> _Call:
        return _Call(
            step_id=frame.owner_of(step["id"]),
            phase_id=frame.phase_of(phase),
            node=frame.node(step["id"]),
            step=step,
            scope=frame.scope,
            label=step.get("label") or step["id"],
            head=frame.head,
            tail=frame.tail,
        )

    async def _agent_step(self, phase: dict[str, Any], step: dict[str, Any], frame: _Frame) -> Any:
        call = replace(
            self._base_call(phase, step, frame),
            taint=self._refs_taint(frame, frame.owner_of(step["id"]), step),
            isolation=step_isolation(step),
        )
        output, taint = await self._fanned(call)
        frame.taint[step["id"]] = taint
        return output

    async def _fanned(self, call: _Call) -> tuple[Any, Taint]:
        """One call, or independent replicas: a list with a null per failed one."""
        replicas = [_placed(c) for c in _replicas(call)]
        if not replicas:
            return await self._call_agent(_placed(call))
        results = await asyncio.gather(*(self._call_agent(c) for c in replicas))
        return [out for out, _taint in results], self._capped(
            call.step_id, *(t for _out, t in results)
        )

    async def _map_step(
        self, phase: dict[str, Any], step: dict[str, Any], frame: _Frame
    ) -> list[Any]:
        items = self._map_items(step, frame)
        source = self._path_taint(frame, step["over"])
        base = self._base_call(phase, step, frame)
        results = await asyncio.gather(
            *(self._chain(base, frame, index, item, source) for index, item in enumerate(items))
        )
        owner = frame.owner_of(step["id"])
        frame.taint[step["id"]] = self._capped(owner, source, *(t for _out, t in results))
        return [out for out, _taint in results]

    def _map_items(self, step: dict[str, Any], frame: _Frame) -> list[Any]:
        value = resolve_path(frame.scope, step["over"])
        tally = self._tally(frame.owner_of(step["id"]))
        if value is None:
            # A map that ran nothing because it read nothing is a named null,
            # never a silent success (D-4: a null always carries its reason).
            tally.add(False, self._over_null_reason(step["over"], frame))
            return []
        if not isinstance(value, list):
            tally.add(False, f"over_not_a_list:{type(value).__name__}")
            return []
        limit = int(step["max_items"])
        tally.dropped = max(0, len(value) - limit)
        if tally.dropped:
            text = f"{step['id']}: {len(value)} items; the first {limit} run (max_items)"
            self._message({"type": "log", "step": step["id"], "text": text})
        return value[:limit]

    def _over_null_reason(self, path: str, frame: _Frame) -> str:
        """``upstream_null:<id>`` when the step ``over`` reads failed (its own
        null is counted too), else ``over_unresolved:<path>``: a path that
        does not reach a value in what that step returned (a schemaless
        step answers text; a key may be misspelt), which the validator,
        checking only that ``steps.<id>`` is an earlier step, cannot see."""
        source = _path_step(path)
        if source is not None and resolve_path(frame.scope, f"steps.{source}.output") is None:
            return f"upstream_null:{source}"
        return f"over_unresolved:{path}"[:MAX_REASON_CHARS]

    async def _chain(
        self, base: _Call, frame: _Frame, index: int, item: Any, taint: Taint
    ) -> tuple[Any, Taint]:
        """One item through every stage, with no barrier against other items.
        A stage's output is the next stage's ``prev``; a null ends the chain."""
        scope = {**frame.scope, "item": item, "item_index": index, "prev": None}
        key = canonical_hash(item)
        base = replace(base, scope=scope, item_index=index, item_key=key)
        if step_isolation(base.step) in ISOLATED_MODES:
            # One workspace per item, shared by its stages (slice 5); the
            # index keeps two equal items apart (they run in parallel).
            ws_key = f"{base.node}[{index}:{key[:16]}]"
            base = replace(base, isolation=step_isolation(base.step), ws_key=ws_key)
        for n, stage in enumerate(base.step["stages"]):
            stage_call = replace(
                base, node=f"{base.node}.stages[{n}]", step=stage, scope=scope, stage=n
            )
            output, taint = await self._stage(stage_call, taint, frame)
            if output is None:
                return None, taint
            scope = {**scope, "prev": output}
        return scope["prev"], taint

    async def _stage(self, call: _Call, taint: Taint, frame: _Frame) -> tuple[Any, Taint]:
        """One stage of one item. A stage reduce may read another step
        (``input``/``inputs``, or a ``partition``'s or ``rank``'s ``items``):
        that step's taint joins the chain, as a top-level reduce's does, or
        the next stage's ``{{prev}}`` would carry its text with no taint at
        all (D-6)."""
        stage = call.step
        if stage["kind"] == "reduce":
            has_input = stage.get("input") or stage.get("inputs")
            source = _reduce_source(stage, call.scope) if has_input else call.scope["prev"]
            read = self._capped(
                call.step_id, taint, *self._reduce_taints(frame, stage), limit=MAX_TAINT_IN
            )
            return apply_reduce(stage, source, call.scope), read
        call = replace(
            call,
            label=stage.get("label") or call.label,
            taint=self._capped(
                call.step_id,
                taint,
                self._refs_taint(frame, call.step_id, stage),
                limit=MAX_TAINT_IN,
            ),
        )
        output, out_taint = await self._fanned(call)
        return output, self._capped(call.step_id, taint, out_taint, limit=MAX_TAINT_IN)

    async def _reduce_step(self, phase: dict[str, Any], step: dict[str, Any], frame: _Frame) -> Any:
        owner = frame.owner_of(step["id"])
        frame.taint[step["id"]] = self._capped(owner, *self._reduce_taints(frame, step))
        return apply_reduce(step, _reduce_source(step, frame.scope), frame.scope)

    def _reduce_taints(self, frame: _Frame, step: dict[str, Any]) -> list[Taint]:
        paths = [step.get("input"), step.get("items"), *(step.get("inputs") or [])]
        return [self._path_taint(frame, path) for path in paths]

    # ── repeat: loop-until-dry (slice 2, D-4) ───────────────────────────────

    async def _repeat_step(
        self, phase: dict[str, Any], step: dict[str, Any], frame: _Frame
    ) -> dict[str, Any]:
        """The body again, at most ``max_iterations`` times, until dry.

        Each iteration runs in a fresh frame over the steps before the
        repeat, ``seen`` (the seed and every item collected so far) and
        ``iteration``; its body's keys hash the rendered prompts, so a resume
        replays an unchanged iteration whole (D-8). After the iteration the
        ``collect`` step's list is checked against the seen-set (``key``): no
        new item is dry, and the loop ends. A collect that is not a list (its
        step failed) stops the loop with a named reason rather than looping
        on the same failure.
        """
        loop = _Loop(step, frame.scope)
        owner = frame.owner_of(step["id"])
        seen_taint = self._path_taint(frame, step.get("seed"))
        for index in range(int(step["max_iterations"])):
            if self._cancel_reason is not None:
                loop.stopped = "cancelled"
                break
            inner = self._iteration_frame(frame, step, loop, index, seen_taint)
            for body in step["steps"]:
                await self._run_step(phase, body, inner)
            loop.iterations = index + 1
            seen_taint = self._capped(
                owner, seen_taint, self._path_taint(inner, step["collect"]), limit=MAX_TAINT_IN
            )
            if not loop.absorb(resolve_path(inner.scope, step["collect"])):
                break
        if loop.stopped and loop.stopped != "cancelled":
            self._tally(owner).add(False, f"repeat_stopped:{loop.stopped}")
        frame.taint[step["id"]] = seen_taint
        return loop.output()

    def _iteration_frame(
        self, frame: _Frame, step: dict[str, Any], loop: _Loop, index: int, seen_taint: Taint
    ) -> _Frame:
        scope = {
            **frame.scope,
            "steps": dict(frame.scope["steps"]),
            "seen": list(loop.seen),
            "iteration": index,
        }
        return _Frame(
            scope=scope,
            taint=dict(frame.taint),
            prefix=f"{frame.node(step['id'])}.iterations[{index}].",
            owner=frame.owner_of(step["id"]),
            phase=frame.phase,
            head=frame.head,
            tail=f"{frame.tail}@{index}",
            roots={**frame.roots, "seen": seen_taint},
        )

    # ── nested plan (slice 2, D-4) ──────────────────────────────────────────

    async def _nested_plan_step(
        self, phase: dict[str, Any], step: dict[str, Any], frame: _Frame
    ) -> dict[str, Any]:
        """The inlined template's phases in a frame of its own: its params,
        its steps, labels ``<step>/<label>``. Its output is its own declared
        outputs (else its last step's), as a plan's result is."""
        program = step["program"]
        inner = _Frame(
            scope={"params": dict(program.get("_bound_params") or {}), "steps": {}},
            taint={},
            prefix=f"{frame.node(step['id'])}/",
            owner=frame.owner_of(step["id"]),
            phase=frame.phase_of(phase),
            head=f"{frame.head}{step['id']}/",
            tail=frame.tail,
        )
        for sub_phase in program.get("phases") or []:
            title = sub_phase.get("title", "")
            self._message(
                {"type": "phase", "id": f"{step['id']}/{sub_phase.get('id')}", "title": title}
            )
            for sub in sub_phase.get("steps") or []:
                await self._run_step(sub_phase, sub, inner)
        frame.taint[step["id"]] = self._capped(inner.owner or step["id"], *inner.taint.values())
        return _program_outputs(program, inner.scope)

    # ── gate (slice 2, ADR-066 classes) ─────────────────────────────────────

    async def _gate_step(
        self, phase: dict[str, Any], step: dict[str, Any], frame: _Frame
    ) -> dict[str, Any]:
        """Ask a human, or replay the human's journalled answer (D-8).

        Never auto-approved: only a broker's answer from a human passes a
        gate, and a gate the run cannot ask (no human, a spent budget, a
        cancelled run) is refused. A gate that is not approved stops the
        run (``cancelled``, with the gate's reason); the rest refuse.
        """
        node = frame.node(step["id"])
        when = step.get("when")
        if when and not _raised(resolve_path(frame.scope, when)):
            self._message({"type": "gate", "gate": node, "raised": False})
            return {"raised": False, "approved": None, "class": step["class"]}
        request = self._gate_request(phase, step, frame)
        record = self._memo.get(request.call_key)
        if self._cancel_reason is None and record and record.get("answered"):
            decision = _decision_from(record)
            self._message(_gate_note(node, step, decision, replayed=True))
        else:
            decision = await self._ask_gate(request, frame.phase_of(phase), frame)
        return self._gate_outcome(node, step, frame, decision)

    def _gate_request(
        self, phase: dict[str, Any], step: dict[str, Any], frame: _Frame
    ) -> GateRequest:
        node = frame.node(step["id"])
        prompt = _gate_text(render(step["prompt"], frame.scope, envelope=True))
        key = canonical_hash({"gate": node, "class": step["class"], "prompt": prompt})
        return GateRequest(
            call_key=key,
            gate=node,
            trigger_class=step["class"],
            prompt=prompt,
            run_id=self.run_id,
            metadata={
                "plan": self._program.get("name"),
                "attempt": self._svc.run.attempt_id,
                "step": frame.owner_of(step["id"]),
                "phase": frame.phase_of(phase),
            },
        )

    async def _ask_gate(
        self, request: GateRequest, phase_id: str, frame: _Frame, *, budgeted: bool = True
    ) -> GateDecision:
        """The broker's answer, journalled when a human gave it; the wait is
        not charged to the wall clock (a human reads at human speed).

        A merge-back (``budgeted=False``) is asked after a spent budget too:
        it spends no tokens, and its agents' work is already paid for."""
        reason = self._refusal() if budgeted else self._halted()
        if reason is not None:
            return GateDecision(approved=False, answered=False, reason=reason)
        payload = {
            "gate": request.gate,
            "step": request.metadata.get("step"),
            "phase": phase_id,
            "class": request.trigger_class,
            "prompt": request.prompt,
            "call_key": request.call_key,
        }
        self._emit(EventKind.HUMAN_INPUT_REQUESTED, _ENGINE_ACTOR, payload)
        started = time.monotonic()
        try:
            decision = await self._gate_answer(request)
        finally:
            self._gate_wait += time.monotonic() - started
        if decision.answered:
            self._humans += 1
            self._journal.append(
                _gate_record(request, phase_id, decision, self._svc.run.attempt_id)
            )
        self._message(_gate_note(request.gate, {"class": request.trigger_class}, decision))
        return decision

    async def _gate_answer(self, request: GateRequest) -> GateDecision:
        """The broker's ``ask_gate`` as a task ``cancel()`` can stop; a broker
        without one, or one that fails, refuses (a gate fails closed)."""
        ask = getattr(self._svc.agents, "ask_gate", None)
        if not callable(ask):
            return GateDecision(approved=False, answered=False, reason="no_human")
        task = asyncio.ensure_future(ask(request))
        self._inflight.add(task)
        try:
            decision = await task
        except asyncio.CancelledError:
            if not self._stopped_on_purpose():
                self._cancel_from_task()
            return GateDecision(approved=False, answered=False, reason="cancelled")
        except Exception as exc:  # noqa: BLE001 — a broken lane refuses, never passes
            return GateDecision(
                approved=False, answered=False, reason=f"broker_error:{type(exc).__name__}"
            )
        finally:
            self._inflight.discard(task)
        if not isinstance(decision, GateDecision):
            return GateDecision(approved=False, answered=False, reason="bad_answer")
        return decision

    def _gate_outcome(
        self, node: str, step: dict[str, Any], frame: _Frame, decision: GateDecision
    ) -> dict[str, Any]:
        tally = self._tally(frame.owner_of(step["id"]))
        out = {
            "raised": True,
            "approved": bool(decision.approved and decision.answered),
            "class": step["class"],
            "decided_by": decision.decided_by or None,
        }
        self._gates[node] = {**out, "reason": decision.reason or None}
        if out["approved"]:
            tally.add(True)
            return out
        reason = _gate_reason(node, decision)
        tally.add(False, reason)
        if self._cancel_reason is None:
            self._cancel_reason = reason
        return out

    # ── merge-back (slice 5) ────────────────────────────────────────────────

    async def _merge_back_step(
        self, phase: dict[str, Any], step: dict[str, Any], frame: _Frame
    ) -> dict[str, Any] | None:
        """Apply the isolated steps' workspace changes to the project root,
        only after a human approved exactly that change set.

        Never auto-approved: it asks through the gate path (``team_plan_gate``).
        A done merge (applied, or declined by a human) is journalled and
        replayed by a resume, which neither asks again nor applies twice. A
        conflict, an unsafe or unsupported file, or two workspaces changing
        one file differently is a null naming each, with nothing written and
        the workspaces kept for a resume. A human no stops the run, as a
        gate's does. Nothing here runs agent-authored code (D-11).
        """
        node = frame.node(step["id"])
        tally = self._tally(frame.owner_of(step["id"]))
        done = self._memo.get(_merge_key(node))
        if self._cancel_reason is None and done and done.get("kind") == "merge_back":
            return self._merge_replayed(node, tally, done)
        halted = self._halted()
        if halted is not None:
            tally.add(False, halted)
            return None
        batch = await asyncio.to_thread(self._ws.batch, step["id"], step["from"])
        if batch.problems:
            return self._merge_refused(node, tally, batch.problems)
        if not batch.changed:
            return self._merge_done(node, phase, tally, _merge_output(batch, applied=False))
        decision = await self._merge_decision(phase, step, frame, batch)
        if not (decision.approved and decision.answered):
            return self._merge_declined(node, phase, tally, batch, decision)
        return await self._merge_apply(node, phase, tally, batch, decision)

    async def _merge_decision(
        self, phase: dict[str, Any], step: dict[str, Any], frame: _Frame, batch: MergeBatch
    ) -> GateDecision:
        """The human's answer to this exact change set: journalled, so a
        resume replays it; a different change set asks again."""
        node = frame.node(step["id"])
        prompt = _gate_text(merge_prompt(self._root or "?", batch))
        key = canonical_hash({"merge_back": node, "class": MERGE_BACK_CLASS, "set": batch.digest})
        request = GateRequest(
            call_key=key,
            gate=node,
            trigger_class=MERGE_BACK_CLASS,
            prompt=prompt,
            run_id=self.run_id,
            metadata={
                "plan": self._program.get("name"),
                "attempt": self._svc.run.attempt_id,
                "step": frame.owner_of(step["id"]),
                "phase": frame.phase_of(phase),
                "files": batch.files(),
            },
        )
        record = self._memo.get(key)
        if record and record.get("answered"):
            decision = _decision_from(record)
            self._message(_gate_note(node, {"class": MERGE_BACK_CLASS}, decision, replayed=True))
            return decision
        return await self._ask_gate(request, frame.phase_of(phase), frame, budgeted=False)

    async def _merge_apply(
        self,
        node: str,
        phase: dict[str, Any],
        tally: _Tally,
        batch: MergeBatch,
        decision: GateDecision,
    ) -> dict[str, Any] | None:
        """Re-check (the root may have moved while the human read), then write."""
        problems = await asyncio.to_thread(self._ws.recheck, batch)
        if problems:
            return self._merge_refused(node, tally, problems)
        try:
            written = await asyncio.to_thread(self._ws.apply, batch)
        except OSError as exc:
            reason = f"merge_back_failed:{node}:{type(exc).__name__}: {exc}"
            tally.add(False, reason[:MAX_REASON_CHARS])
            return None
        output = _merge_output(batch, applied=True, decided_by=decision.decided_by)
        output["written"] = written
        return self._merge_done(node, phase, tally, output)

    def _merge_done(
        self, node: str, phase: dict[str, Any], tally: _Tally, output: dict[str, Any]
    ) -> dict[str, Any]:
        tally.add(True)
        return self._merge_record(node, phase, output)

    def _merge_record(
        self, node: str, phase: dict[str, Any], output: dict[str, Any]
    ) -> dict[str, Any]:
        """A finished merge-back (applied, nothing to apply, or declined by a
        human), journalled under its node so a resume replays it (D-8)."""
        self._merged.add(node)
        self._journal.append(
            {
                "call_key": _merge_key(node),
                "kind": "merge_back",
                "node": node,
                "step": node,
                "phase": phase.get("id"),
                "ok": True,
                "output": output,
                "attempt": self._svc.run.attempt_id,
            }
        )
        self._message({"type": "merge_back", "step": node, **_merge_note(output)})
        return output

    def _merge_declined(
        self,
        node: str,
        phase: dict[str, Any],
        tally: _Tally,
        batch: MergeBatch,
        decision: GateDecision,
    ) -> dict[str, Any] | None:
        """A human no is a done merge-back that applied nothing and stops the
        run; no human to ask leaves it pending (its workspaces are kept)."""
        if decision.answered:
            reason = f"merge_back_rejected:{node}"[:MAX_REASON_CHARS]
            output = _merge_output(batch, applied=False, decided_by=decision.decided_by)
            output["rejected"] = True
            self._merge_record(node, phase, output)
            tally.add(False, reason)
        else:
            reason = f"merge_back_refused:{node}:{decision.reason or 'no_human'}"
            reason = reason[:MAX_REASON_CHARS]
            tally.add(False, reason)
            output = None
        if self._cancel_reason is None:
            self._cancel_reason = reason
        return output

    def _merge_refused(self, node: str, tally: _Tally, problems: list[str]) -> None:
        """Nothing written; each problem named; the merge-back stays pending."""
        named = ", ".join(problems[:8]) + (f" (+{len(problems) - 8})" if len(problems) > 8 else "")
        reason = f"merge_back_conflict:{node}:{named}"
        tally.add(False, reason[:MAX_REASON_CHARS])
        self._message({"type": "merge_back", "step": node, "applied": False, "problems": problems})
        logger.warning("plan run %s: merge-back %s refused: %s", self.run_id, node, named)
        return None

    def _merge_replayed(self, node: str, tally: _Tally, record: dict[str, Any]) -> Any:
        """A journalled merge-back: its output again, nothing asked or written."""
        output = record.get("output") or {}
        self._merged.add(node)
        self._message({"type": "merge_back", "step": node, "replayed": True, **_merge_note(output)})
        if output.get("rejected"):
            reason = f"merge_back_rejected:{node}"[:MAX_REASON_CHARS]
            tally.add(False, reason)
            if self._cancel_reason is None:
                self._cancel_reason = reason
            return output
        tally.add(True, replayed=True)
        return output

    # ── workspaces (slice 5) ────────────────────────────────────────────────

    def _workspace_preflight(self) -> str | None:
        """Refuse, by name and before any spend, an isolation mode this
        project cannot have (no git repository, a tree over the copy cap)."""
        modes = sorted(
            {step_isolation(part.step) for part in iter_agent_parts(self._program)} & ISOLATED_MODES
        )
        for mode in modes:
            try:
                preflight(mode, self._root, self._dir)
            except WorkspaceError as exc:
                return f"{exc.reason} (isolation: {mode})"
        return None

    def _pending_merges(self) -> set[str]:
        """The isolated steps a merge-back that has not finished still names."""
        pending: set[str] = set()
        for _phase, step in iter_steps(self._program):
            if step.get("kind") == "merge_back" and step["id"] not in self._merged:
                pending.update(step.get("from") or ())
        return pending

    def _halted(self) -> str | None:
        """Why nothing more may start, budgets aside: an engine error or a
        cancel (a merge-back is not an agent and spends no tokens)."""
        if self._error is not None:
            return "engine_error"
        if self._cancel_reason is not None:
            halted = self._cancel_reason.startswith(("gate_", "merge_back_"))
            return "halted_by_gate" if halted else "cancelled"
        if self._durable_halt is not None:
            return DURABLE_FAILED
        return None

    # ── one agent call ──────────────────────────────────────────────────────

    async def _call_agent(self, call: _Call) -> tuple[Any, Taint]:
        """Replay a journalled ok result, or refuse, or run one governed turn."""
        request = self._request(call)
        record = self._memo.get(request.call_key)
        if self._cancel_reason is None and record and record.get("ok"):
            return self._replay(call, request, record)
        reason = self._refusal()
        if reason is None:
            try:
                async with self._slots():
                    request, reason = await self._in_workspace(call, request)
                    if reason is None:
                        return await self._live(call, request)
            except asyncio.CancelledError:
                # Only the slot wait can raise it (_turn absorbs its own): the
                # run task is being cancelled while this agent waited. It is a
                # null like any refused agent, so the walk ends as a unit (D-3).
                self._cancel_from_task()
                reason = "cancelled"
        self._tally(call.step_id).add(False, reason)
        self._mark_workspace(call, ok=False)
        return None, ()

    async def _in_workspace(
        self, call: _Call, request: AgentTurnRequest
    ) -> tuple[AgentTurnRequest, str | None]:
        """The request with its isolated workspace named (made on first use),
        or the reason it may not start: a refusal, or a workspace that could
        not be made (slice 5)."""
        reason = self._refusal()
        if reason is not None or call.ws_key is None:
            return request, reason
        try:
            ws = await self._workspace_of(call)
        except WorkspaceError as exc:
            return request, exc.reason[:MAX_REASON_CHARS]
        metadata = {**request.metadata, "workspace": ws.root, "workspace_id": ws.id}
        return replace(request, metadata=metadata), None

    async def _workspace_of(self, call: _Call) -> Any:
        """The call's workspace, made once (serialised: git locks its refs)."""
        ws_id = workspace_id(call.ws_key or call.node)
        if self._ws_lock is None:
            self._ws_lock = asyncio.Lock()
        async with self._ws_lock:
            ws = self._ws.get(ws_id)
            if ws is not None:
                return ws
            ws = await asyncio.to_thread(
                self._ws.create,
                ws_id,
                call.isolation,
                run_id=self.run_id,
                step=call.step_id,
                node=call.ws_key or call.node,
            )
            self._ws.created(ws, self._svc.run.attempt_id)
        text = f"{call.step_id}: workspace {ws.id} ({ws.mode}) at {ws.path}"
        self._message({"type": "workspace", "step": call.step_id, "id": ws.id, "text": text})
        return ws

    def _mark_workspace(self, call: _Call, *, ok: bool) -> None:
        if call.ws_key is not None:
            self._ws.mark(workspace_id(call.ws_key), call.step_id, ok)

    async def _live(self, call: _Call, request: AgentTurnRequest) -> tuple[Any, Taint]:
        self._agents_started += 1
        self._emit_agent(EventKind.AGENT_STARTED, call, request)
        result = await self._turn(request)
        return self._settle(call, request, result)

    async def _turn(self, request: AgentTurnRequest) -> AgentTurnResult:
        """The broker call as a task ``cancel()`` can stop; a broker crash is a named null.

        It never raises CancelledError: a started agent always settles
        (one AGENT_COMPLETED and a journal record, D-3/D-8). Only cancel()
        and an engine error halt a turn on purpose, and both name a reason
        first; a cancel without one is the run task itself being cancelled
        (Ctrl-C ending ``asyncio.run``), so the whole run stops with it.
        """
        agents = self._svc.agents
        if agents is None:  # _execute fails the run before any walk; kept for type safety
            return AgentTurnResult(ok=False, reason="agent broker not wired")
        task = asyncio.ensure_future(agents.run_turn(request))
        self._inflight.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            if not self._stopped_on_purpose():
                self._cancel_from_task()
            return AgentTurnResult(ok=False, reason="cancelled")
        except Exception as exc:  # noqa: BLE001 — a broker failure is a null, not a crash
            return AgentTurnResult(ok=False, reason=f"broker_error:{type(exc).__name__}")
        finally:
            self._inflight.discard(task)

    def _settle(
        self, call: _Call, request: AgentTurnRequest, result: AgentTurnResult
    ) -> tuple[Any, Taint]:
        tokens = self._usage.add(result.usage)
        output, reason = _checked(result, call.step.get("schema"))
        model = result.model or request.model or ""
        if model:
            self._models.add(model)
        taint: Taint = () if reason else tuple(result.taint_out or ())
        record = _record(call, request, output, reason, result, self._svc.run.attempt_id, taint)
        self._journal.append(record)
        tally = self._tally(call.step_id)
        tally.add(not reason, reason)
        tally.add_refused(record["refused_tools"])
        self._mark_workspace(call, ok=not reason)
        self._emit_agent(
            EventKind.AGENT_COMPLETED,
            call,
            request,
            ok=not reason,
            reason=reason,
            tokens=tokens,
            model=model or None,
            refused_tools=record["refused_tools"],
            preview=_event_preview(output if not reason else record["text"]),
        )
        self._maybe_warn()
        return output, taint

    def _replay(
        self, call: _Call, request: AgentTurnRequest, record: dict[str, Any]
    ) -> tuple[Any, Taint]:
        """A journalled ok result at zero model cost: no broker call (D-8).
        The refusals it was answered under are named again (D-6)."""
        tally = self._tally(call.step_id)
        tally.add(True, replayed=True)
        self._mark_workspace(call, ok=True)
        refused = record.get("refused_tools")
        refused = list(refused) if isinstance(refused, list) else []
        tally.add_refused(refused)
        model = record.get("model") or request.model
        self._emit_agent(EventKind.AGENT_STARTED, call, request, replayed=True)
        self._emit_agent(
            EventKind.AGENT_COMPLETED,
            call,
            request,
            ok=True,
            model=model,
            replayed=True,
            refused_tools=refused,
            preview=_event_preview(record.get("output")),
        )
        return record.get("output"), _taint_from(record.get("taint"))

    def _request(self, call: _Call) -> AgentTurnRequest:
        """The rendered turn and its journal key (D-8): node path, item key,
        replica, rendered prompt, instructions, schema, model and tool set."""
        profile = profile_for(self._program, call.step)
        model = profile.get("model") or self._plan.default_model
        tools = tuple(profile.get("tools") or ())
        prompt = render(call.step["prompt"], call.scope, envelope=True)
        schema = call.step.get("schema")
        keyed: dict[str, Any] = {
            "node": call.node,
            "item": call.item_key,
            "replica": call.replica,
            "prompt": prompt,
            "instructions": profile["instructions"],
            "schema": schema,
            "model": model,
            "tools": sorted(tools),
        }
        if call.isolation != "none":
            keyed["isolation"] = call.isolation  # where its effects land (slice 5)
        key = canonical_hash(keyed)
        return AgentTurnRequest(
            call_key=key,
            label=_label(call),
            prompt=prompt,
            instructions=profile["instructions"],
            tools=tools,
            model=model,
            schema=schema,
            max_tool_rounds=int(profile["max_tool_rounds"]),
            taint_in=call.taint[:MAX_TAINT_IN],
            metadata=self._metadata(call),
        )

    def _metadata(self, call: _Call) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan": self._program.get("name"),
            "attempt": self._svc.run.attempt_id,
            "step": call.step_id,
            "phase": call.phase_id,
            "node": call.node,
            "item_index": call.item_index,
            "replica": call.replica,
            "stage": call.stage,
            # Slice 5: the broker runs the agent's tools in its workspace, the
            # project root unless the engine names an isolated one.
            "project_root": self._root,
            "isolation": call.isolation,
        }

    def _slots(self) -> asyncio.Semaphore:
        """The one semaphore every agent call of the run shares."""
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._concurrency())
        return self._sem

    def _concurrency(self) -> int:
        return int(self._program.get("concurrency", DEFAULT_CONCURRENCY))

    # ── budgets (D-7) ───────────────────────────────────────────────────────

    def _refusal(self) -> str | None:
        """Why the next agent may not start, or None."""
        if self._error is not None:
            return "engine_error"
        if self._cancel_reason is not None:
            halted = self._cancel_reason.startswith(("gate_", "merge_back_"))
            return "halted_by_gate" if halted else "cancelled"
        if self._durable_halt is not None:
            return DURABLE_FAILED
        kind = next((kind for kind, guard in _GUARDS if getattr(self, guard)()), None)
        if kind is None:
            return None
        if kind not in self._exhausted:
            self._exhausted.add(kind)
            text = f"budget exhausted ({kind}); the remaining agents are refused"
            self._message({"type": "budget_exhausted", "kind": kind, "text": text})
        return f"budget_exhausted:{kind}"

    def _tokens_out(self) -> bool:
        return self._tokens_remaining() == 0

    def _agents_out(self) -> bool:
        return self._agents_started >= self._max_agents()

    def _wallclock_out(self) -> bool:
        limit = self._budget.get("max_wallclock_sec")
        return bool(limit) and self._budget_elapsed() >= limit

    def _tool_calls_out(self) -> bool:
        limit = self._budget.get("max_tool_calls")
        return bool(limit) and self._usage.tool_calls >= limit

    def _max_agents(self) -> int:
        return int(self._budget.get("max_agents", DEFAULT_MAX_AGENTS))

    def _tokens_remaining(self) -> int | None:
        """The smaller of the meter's view and the plan ceiling minus reported usage."""
        values = [v for v in (self._meter_remaining(), self._own_remaining()) if v is not None]
        return min(values) if values else None

    def _meter_remaining(self) -> int | None:
        try:
            value = (self._svc.budget.remaining() or {}).get("max_tokens")
        except Exception:  # noqa: BLE001 — a broken meter must not crash the walk
            return None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _own_remaining(self) -> int | None:
        ceiling = self._budget.get("max_tokens")
        return max(0, int(ceiling) - self._usage.tokens) if ceiling else None

    def _spent_tokens(self) -> int:
        ceiling = int(self._budget.get("max_tokens") or 0)
        meter = self._meter_remaining()
        from_meter = ceiling - meter if meter is not None and ceiling else 0
        return max(self._usage.tokens, from_meter)

    def _maybe_warn(self) -> None:
        """BUDGET_WARNING once, when tokens spent pass 80% of max_tokens."""
        ceiling = self._budget.get("max_tokens")
        if self._warned or not ceiling:
            return
        spent = self._spent_tokens()
        if spent < BUDGET_WARN_FRACTION * ceiling:
            return
        self._warned = True
        payload = {
            "kind": "tokens",
            "spent_tokens": spent,
            "max_tokens": ceiling,
            "remaining": self._tokens_remaining(),
        }
        self._emit(EventKind.BUDGET_WARNING, _ENGINE_ACTOR, payload)

    def _remaining(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "max_tokens": self._tokens_remaining(),
            "max_agents": max(0, self._max_agents() - self._agents_started),
        }
        wallclock = self._budget.get("max_wallclock_sec")
        if wallclock:
            out["max_wallclock_sec"] = max(0.0, round(wallclock - self._budget_elapsed(), 1))
        tool_calls = self._budget.get("max_tool_calls")
        if tool_calls:
            out["max_tool_calls"] = max(0, tool_calls - self._usage.tool_calls)
        return out

    def _budget_elapsed(self) -> float:
        """The wall clock the ceiling counts: a gate's wait for a human is
        not the plan spending (slice 2)."""
        return max(0.0, self._elapsed() - self._gate_wait)

    def _elapsed(self) -> float:
        if self._t0 is None:
            return 0.0
        return (self._t1 if self._t1 is not None else time.monotonic()) - self._t0

    # ── taint ───────────────────────────────────────────────────────────────

    def _refs_taint(self, frame: _Frame, step_id: str, step: dict[str, Any]) -> Taint:
        """The taint of every step the prompt reads, and of every other
        agent-derived root it reads (a repeat's ``seen``)."""
        prompt = step.get("prompt") or ""
        groups = [frame.taint.get(ref, ()) for ref in sorted(template_refs(prompt))]
        groups += [frame.roots[root] for root in sorted(template_roots(prompt) & set(frame.roots))]
        return self._capped(step_id, *groups, limit=MAX_TAINT_IN)

    def _capped(self, step_id: str, *groups: Iterable[Any], limit: int = MAX_RUN_TAINT) -> Taint:
        """Merge taint under a cap; a cap that cuts excerpts is recorded.

        The caps bound what one agent imports and what result.json carries,
        but an excerpt past them is untrusted text the fence no longer sees
        (ADR-039, D-6). So the cut is never silent: a log line the first
        time a step is cut, and ``taint_truncated`` with per-step counts in
        result.json, so the lead knows that output is not fully covered.
        """
        taint, dropped = _merge_counted(*groups, limit=limit)
        if dropped:
            first = step_id not in self._taint_dropped
            self._taint_dropped[step_id] = self._taint_dropped.get(step_id, 0) + dropped
            if first:
                text = (
                    f"{step_id}: taint truncated: {dropped} excerpt(s) past the cap of {limit} "
                    "are not carried (ADR-039); treat this output as untrusted"
                )
                logger.warning("plan run %s: %s", self.run_id, text)
                self._message({"type": "log", "step": step_id, "text": text})
        return taint

    def _path_taint(self, frame: _Frame, path: Any) -> Taint:
        ref = _path_step(path)
        return frame.taint.get(ref, ()) if ref else ()

    # ── the end ─────────────────────────────────────────────────────────────

    def _finish(self) -> None:
        if self._terminal:
            return
        status = self._final_status()
        error = self._durable_halt or _failure_error(status, self._tallies.values())
        self._close(status, error)

    def _fail(self, error: str) -> None:
        if not self._terminal:
            self._close("failed", error)

    def _close(self, status: str, error: str | None) -> None:
        """result.json, the TeamResult and the one terminal event.

        Workspaces are removed first (slice 5), each kept only while a
        merge-back that names its step is pending; result.json says which.
        """
        self._t1 = time.monotonic() if self._t0 is not None else None
        self._ws_report = self._cleanup_workspaces()
        self._journal.close(status, self._close_summary(status, error))
        doc = self._result_doc(status, error)
        write_error = _try_write(self._dir / "result.json", doc)
        self._status = _RUN_STATE[status]
        self._result = self._team_result(status, doc, error)
        payload = {
            "status": status,
            "error": error,
            "reason": self._cancel_reason,
            "budget": doc["budget"],
            "result_path": str(self._dir / "result.json"),
            "durable": doc.get("durable"),
        }
        if write_error:
            payload["result_write_error"] = write_error
        self._emit(_TERMINAL[status], _ENGINE_ACTOR, payload)

    def _final_status(self) -> str:
        if self._cancel_reason is not None:
            return "cancelled"
        if self._durable_halt is not None:
            return "failed"
        ok = sum(t.ok for t in self._tallies.values())
        null = sum(t.null for t in self._tallies.values())
        if not null:
            return "success"
        return "partial" if ok else "failed"

    def _result_doc(self, status: str, error: str | None) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "run_id": self.run_id,
            "plan": self._program.get("name"),
            "plan_hash": self._plan.plan_hash,
            "attempt": self._svc.run.attempt_id,
            "status": status,
            "output": self._outputs(),
            "steps": {
                step["id"]: self._tally(step["id"]).as_dict()
                for _phase, step in iter_steps(self._program)
            },
            "budget": self._budget_doc(),
            "journal": JOURNAL_LABEL,
            # Slice 7: where the run's records are (UMRS, or this directory
            # only), any failure of that home, and so where it can resume.
            "durable": self._journal.report(),
            "checkpoint_scope": self._journal.checkpoint_scope,
            "taint": [_taint_dict(s) for s in self._run_taint()],
        }
        if self._gates:
            doc["gates"] = dict(self._gates)
        if self._ws_report:
            doc["workspaces"] = dict(self._ws_report)
        doc["taint_truncated"] = bool(self._taint_dropped)
        if self._taint_dropped:
            doc["taint_dropped"] = dict(self._taint_dropped)
        if error:
            doc["error"] = error
        if self._cancel_reason is not None:
            doc["cancel_reason"] = self._cancel_reason
        return doc

    def _outputs(self) -> dict[str, Any]:
        """The declared output map; with none, the last step's output by its id."""
        return _program_outputs(self._program, self._scope)

    def _budget_doc(self) -> dict[str, Any]:
        return {
            "spent_tokens": self._spent_tokens(),
            "remaining": self._remaining(),
            "agents_started": self._agents_started,
            "replayed": sum(t.replayed for t in self._tallies.values()),
            "llm_calls": self._usage.llm_calls,
            "tool_calls": self._usage.tool_calls,
        }

    def _run_taint(self) -> Taint:
        return self._capped(RUN_TAINT_KEY, *self._taint.values(), limit=MAX_RUN_TAINT)

    def _team_result(self, status: str, doc: dict[str, Any], error: str | None) -> TeamResult:
        summary = json.dumps(doc["output"], ensure_ascii=False, default=str)
        if len(summary) > MAX_SUMMARY_CHARS:
            summary = summary[:MAX_SUMMARY_CHARS] + "…"
        return TeamResult(
            status=status,
            summary=summary,
            metrics=self._metrics(),
            error=error,
            reproducibility={
                "seed": self._svc.run.seed,
                "engine_version": ENGINE_VERSION,
                "spec_hash": self._plan.plan_hash,
                "model_versions": sorted(self._models),
                "journal": JOURNAL_LABEL,
            },
        )

    def _metrics(self) -> RunMetrics:
        usage = self._usage
        return RunMetrics(
            wallclock_sec=round(self._elapsed(), 3),
            total_tokens=usage.tokens,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            tool_calls=usage.tool_calls,
            llm_calls=usage.llm_calls,
            cost_usd=None,
            agent_steps=self._agents_started,
            checkpoint_count=self._checkpoints,
            human_interventions=self._humans,
        )

    def _cleanup_workspaces(self) -> dict[str, Any]:
        """Remove the run's workspaces (worktree and branch, or the copy),
        except those a pending merge-back needs; never fails the close."""
        try:
            return self._ws.cleanup(self._pending_merges(), self._svc.run.attempt_id)
        except Exception as exc:  # noqa: BLE001 — cleanup must not lose the terminal event
            logger.warning("plan run %s: workspace cleanup failed: %s", self.run_id, exc)
            return {"(cleanup)": {"state": "error", "error": f"{type(exc).__name__}: {exc}"}}

    def _halt(self) -> None:
        for task in list(self._inflight):
            task.cancel()

    def _durable_failed(self, text: str) -> None:
        """The durable home failed (slice 7): named, never silent. A run that
        requires its durable record stops as a unit (fail closed); any other
        continues local-only."""
        logger.warning("plan run %s: %s", self.run_id, text)
        if self._first_seq is None:
            self._durable_notes.append(text)
        else:
            self._message({"type": "durable", "text": text})
        halt = self._journal.halt_reason
        if halt is not None and self._durable_halt is None:
            self._durable_halt = halt[:MAX_REASON_CHARS]
            self._stop_turns(self._durable_halt)

    def _close_summary(self, status: str, error: str | None) -> dict[str, Any]:
        tallies = list(self._tallies.values())
        summary = {
            "status": status,
            "error": (error or "")[:MAX_REASON_CHARS],
            "agents_ok": sum(t.ok for t in tallies),
            "agents_null": sum(t.null for t in tallies),
            "replayed": sum(t.replayed for t in tallies),
            "spent_tokens": self._spent_tokens(),
        }
        summary.update(_close_output(self._outputs()))
        return summary

    def _stop_turns(self, reason: str) -> None:
        """Stop every turn in flight, once through the broker, then the tasks.

        Cancelling an asyncio task cannot stop a turn running in a worker
        thread (``RoleTurnAgentBroker`` runs each in ``asyncio.to_thread``);
        only the broker's own cancel reaches it, so without that call a
        Ctrl-C left agents spending and held the CLI until they finished
        (D-3: a plan is cancelled as a unit). Best effort, per the SPI.
        """
        agents = self._svc.agents
        if agents is not None and not self._broker_stopped:
            self._broker_stopped = True
            with contextlib.suppress(Exception):
                agents.cancel(reason)
        self._halt()

    def _stopped_on_purpose(self) -> bool:
        """A turn was halted by a cancel, an engine error or a lost durable
        record (each names its reason first); otherwise the run task itself
        is being cancelled."""
        return (
            self._cancel_reason is not None
            or self._error is not None
            or self._durable_halt is not None
        )

    def _cancel_from_task(self) -> None:
        """The run task itself was cancelled: cancel the run as cancel() does."""
        self._task_cancelled = True
        if self._cancel_reason is None and self._error is None:
            self._cancel_reason = RUN_TASK_CANCELLED
        self._stop_turns(self._cancel_reason or self._error or RUN_TASK_CANCELLED)

    async def _drain(self) -> None:
        """Let the halted turns settle before the terminal event.

        Nothing follows the terminal event, so a turn that settled after it
        would lose its AGENT_COMPLETED (D-3). A gather that propagates a
        sibling's error or cancel returns while other agents are still in
        flight; their halted tasks finish here, and each owner settles in
        the same loop step its task finishes (it registered first).
        """
        pending = list(self._inflight)
        if pending:
            await asyncio.wait(pending, timeout=DRAIN_TIMEOUT_SEC)
        await asyncio.sleep(0)

    def _tally(self, step_id: str) -> _Tally:
        return self._tallies.setdefault(step_id, _Tally())

    # ── the EngineRun surface ───────────────────────────────────────────────

    async def checkpoint(self) -> CheckpointRef:
        """The journal is the checkpoint: every live call is already fsynced."""
        self._checkpoints += 1
        ref = CheckpointRef(
            run_id=self.run_id,
            attempt_id=self._svc.run.attempt_id,
            engine_name=ENGINE_NAME,
            engine_version=ENGINE_VERSION,
            state_blob_id=f"journal:{self.run_id}",
            event_offset=self._last_seq + 1,
            created_at=_utcnow(),
        )
        payload = {
            "state_blob_id": ref.state_blob_id,
            "journal": JOURNAL_LABEL,
            "scope": self._journal.checkpoint_scope,
        }
        self._emit(EventKind.CHECKPOINT_CREATED, _ENGINE_ACTOR, payload)
        return ref

    async def resume(self, ref: CheckpointRef) -> None:
        """Accept a same-engine checkpoint of this run.

        There is nothing to restore: a new attempt over the same run
        directory loads the journal when its walk starts and replays every
        ok record whose key still matches (D-8). A started attempt is
        never rewound.
        """
        if ref.engine_name != ENGINE_NAME or ref.engine_version != ENGINE_VERSION:
            raise IncompatibleCheckpoint(f"checkpoint from {ref.engine_name}@{ref.engine_version}")
        if ref.run_id != self.run_id:
            raise IncompatibleCheckpoint(f"checkpoint of run {ref.run_id!r}, not {self.run_id!r}")
        self._resumed_from = ref.attempt_id

    async def cancel(self, reason: str) -> None:
        """Idempotent. Turns in flight are stopped, pending agents become
        nulls with reason ``cancelled``, and the run ends RUN_CANCELLED."""
        if self._terminal or self._cancel_reason is not None:
            return
        self._cancel_reason = reason or "cancelled"
        self._stop_turns(self._cancel_reason)
        if self._task is None:
            self._finish()

    def state(self) -> str:
        return self._status

    def graph(self) -> ExecutionGraph:
        nodes: list[NodeState] = []
        edges: list[tuple[str, str]] = []
        for _phase, step in iter_steps(self._program):
            step_id = step["id"]
            tally = self._tallies.get(step_id)
            nodes.append(
                NodeState(
                    node_id=step_id,
                    agent=None if step.get("kind") == "reduce" else step.get("label") or step_id,
                    status=self._nodes.get(step_id, "pending"),
                    detail=f"ok={tally.ok} null={tally.null}" if tally else None,
                )
            )
            edges += [(dep, step_id) for dep in sorted(_step_deps(step))]
        return ExecutionGraph(nodes=tuple(nodes), edges=tuple(edges))

    async def result(self) -> TeamResult:
        if self._result is None:
            return TeamResult(status=self._status, summary="run not finished")
        return self._result

    async def metrics(self) -> RunMetrics:
        return self._metrics()


# ── helpers ─────────────────────────────────────────────────────────────────


def _replicas(call: _Call) -> list[_Call]:
    """Independent replica calls of a fan-out (``count``, or ``values`` bound to ``as``)."""
    fanout = call.step.get("fanout")
    if not isinstance(fanout, dict):
        return []
    if "count" in fanout:
        bindings: list[dict[str, Any]] = [{} for _ in range(int(fanout["count"]))]
    else:
        bindings = [{fanout["as"]: value} for value in fanout.get("values") or []]
    return [
        replace(call, replica=index, scope={**call.scope, **binding, "replica": index})
        for index, binding in enumerate(bindings)
    ]


def _label(call: _Call) -> str:
    """``<label>[<item>]#<replica>`` (D-3), sanitised, whitespace-folded and bounded.

    A label reaches approval cards and terminals, and a template may read
    agent-written data (``steps``, ``item``, ``prev``). So control and
    format characters (escape sequences, bidi overrides) are dropped and
    the brackets and middle dot that frame ``[agent <label> · plan <id>]``
    are neutralised: an upstream agent cannot close the card's prefix and
    write a harmless-looking description in front of the real one (D-6).
    """
    rendered = render(call.label, call.scope, envelope=False)
    text = " ".join(_sanitised(rendered).split())[:MAX_LABEL_CHARS]
    text = f"{call.head}{text or call.step_id}{call.tail}"
    if call.item_index is not None:
        text += f"[{call.item_index}]"
    if call.replica is not None:
        text += f"#{call.replica}"
    return text


def _sanitised(text: str) -> str:
    """``text`` without control or format characters (newlines and tabs fold
    to spaces), with the card-framing brackets neutralised."""
    kept = (
        " " if ch in "\n\r\t" else ch
        for ch in text
        if ch in "\n\r\t" or unicodedata.category(ch) not in ("Cc", "Cf")
    )
    return "".join(kept).translate(_LABEL_NEUTRAL)


def _refused_tools(text: str) -> list[str]:
    """The tools the broker's fork refused, from its note at the end of
    ``AgentTurnResult.text``. An agent that still answered is ok, so without
    this the refusal was named nowhere in the run's results (D-6)."""
    match = _REFUSED_NOTE_RE.search(text or "")
    if match is None:
        return []
    names = (name.strip() for name in match.group(1).split(","))
    return [name for name in names if _TOOL_NAME_RE.match(name)]


def _checked(result: AgentTurnResult, schema: dict[str, Any] | None) -> tuple[Any, str]:
    """``(output, "")`` or ``(None, reason)``. A schema'd output that does not
    validate is a named null: nothing is fabricated (D-5)."""
    if not result.ok:
        return None, result.reason or "agent_failed"
    output = result.output if result.output is not None else (result.text or None)
    if output is None:
        return None, "empty_output"
    errors = validate_output(output, schema) if schema is not None else []
    if errors:
        return None, f"schema_invalid:{errors[0]}"
    return output, ""


NULL_TEXT_LIMIT = 2000


def _event_preview(value: Any) -> str:
    """A bounded one-string view of an agent's answer for its event (slice 3).

    Text stays text; anything else is compact JSON. Cut with an explicit
    marker, so a surface never presents a cut answer as whole.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= MAX_EVENT_PREVIEW_CHARS:
        return text
    return text[:MAX_EVENT_PREVIEW_CHARS] + "…"


def _record(
    call: _Call,
    request: AgentTurnRequest,
    output: Any,
    reason: str,
    result: AgentTurnResult,
    attempt: str,
    taint: Taint,
) -> dict[str, Any]:
    return {
        "call_key": request.call_key,
        "label": request.label,
        "step": call.step_id,
        "phase": call.phase_id,
        "node": call.node,
        "item_index": call.item_index,
        "replica": call.replica,
        "ok": not reason,
        "output": output,
        "reason": reason,
        "usage": dict(result.usage or {}),
        "model": result.model or request.model or "",
        "attempt": attempt,
        "taint": [_taint_dict(s) for s in taint],
        "refused_tools": _refused_tools(result.text),
        # A null keeps what the agent actually said, bounded, so a failed call
        # can be diagnosed (a schema_invalid null was opaque, live 2026-10-09).
        # Never replayed: only ok records replay.
        "text": "" if not reason else (result.text or "")[:NULL_TEXT_LIMIT],
        # K3 (slice 2): what the agent's project-knowledge reads returned —
        # the query and each numbered passage's handle — so a result names
        # the passages it was grounded on, replayed or live.
        "retrieval": [dict(ref) for ref in (result.retrieval or ())[:MAX_RETRIEVAL_REFS]],
    }


def _merge_counted(*groups: Iterable[Any], limit: int) -> tuple[Taint, int]:
    """Distinct segments by (excerpt, class), first seen first, at most
    ``limit``; and how many distinct segments the cap cut (D-6: recorded)."""
    seen: set[tuple[str, str]] = set()
    out: list[TaintedSegment] = []
    dropped = 0
    for group in groups:
        for segment in group:
            key = (getattr(segment, "text_excerpt", ""), getattr(segment, "taint_class", ""))
            if key in seen:
                continue
            seen.add(key)
            if len(out) < limit:
                out.append(segment)
            else:
                dropped += 1
    return tuple(out), dropped


def _taint_dict(segment: Any) -> dict[str, Any]:
    return {
        "text_excerpt": getattr(segment, "text_excerpt", ""),
        "taint_class": getattr(segment, "taint_class", "generic"),
        "source_tool": getattr(segment, "source_tool", ""),
        "registered_at": getattr(segment, "registered_at", 0.0),
    }


def _taint_from(rows: Any) -> Taint:
    out: list[TaintedSegment] = []
    for row in rows if isinstance(rows, list) else []:
        try:
            out.append(
                TaintedSegment(
                    text_excerpt=str(row["text_excerpt"]),
                    taint_class=str(row.get("taint_class", "generic")),
                    source_tool=str(row.get("source_tool", "")),
                    registered_at=float(row.get("registered_at", 0.0)),
                )
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return tuple(out)


def _path_step(path: Any) -> str | None:
    """The step id a ``steps.<id>…`` path reads, or None."""
    if not isinstance(path, str):
        return None
    parts = path.strip().split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "steps" else None


def _step_deps(step: dict[str, Any]) -> set[str]:
    """The earlier steps one step reads (templates and paths, stages included)."""
    refs: set[str] = set()
    for part in [step, *(step.get("stages") or [])]:
        refs |= _part_deps(part)
    return refs - {""}


def _part_deps(part: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in ("prompt", "label", "log"):
        refs |= template_refs(part.get(key) or "")
    paths = [part.get(key) for key in ("over", "input", "items", "seed")]
    refs |= {str(source) for source in part.get("from") or ()}  # a merge-back's steps
    return refs | {_path_step(path) or "" for path in paths + list(part.get("inputs") or [])}


def _last_step_id(program: dict[str, Any]) -> str | None:
    last = None
    for _phase, step in iter_steps(program):
        last = step.get("id")
    return last


def _failure_error(status: str, tallies: Iterable[_Tally]) -> str | None:
    if status != "failed":
        return None
    reasons: list[str] = []
    for tally in tallies:
        reasons += [r for r in tally.reasons if r not in reasons]
    return f"no agent call succeeded ({', '.join(reasons[:5]) or 'no reason recorded'})"


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, doc: Any) -> None:
    """Atomic: a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _close_output(output: Any) -> dict[str, Any]:
    """The declared outputs for the durable close, when they fit (slice 8).

    A server-hosted run's client has no run directory: this is its result.
    Too large, they are named as left out, never cut mid-value.
    """
    text = json.dumps(output, ensure_ascii=False, default=str)
    if len(text.encode("utf-8")) > MAX_CLOSE_OUTPUT_BYTES:
        return {"output_truncated": True}
    return {"output": json.loads(text)}


def _try_write(path: Path, doc: Any) -> str | None:
    try:
        _write_json(path, doc)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


# ── slice 2 helpers ─────────────────────────────────────────────────────────


class _Loop:
    """A repeat's seen-set and what it collected (D-4, loop-until-dry)."""

    def __init__(self, step: dict[str, Any], scope: dict[str, Any]) -> None:
        self._key = step.get("key")
        self.seen: list[Any] = []
        self._keys: set[str] = set()
        self.collected: list[Any] = []
        self.iterations = 0
        self.dry = False
        self.stopped: str | None = None
        seed = resolve_path(scope, step["seed"]) if step.get("seed") else None
        for item in seed if isinstance(seed, list) else []:
            self._take(item)

    def _take(self, item: Any) -> bool:
        key = item_key(item, self._key)
        if key in self._keys:
            return False
        self._keys.add(key)
        self.seen.append(item)
        return True

    def absorb(self, found: Any) -> bool:
        """Take one iteration's list; False when the loop ends here."""
        if not isinstance(found, list):
            self.stopped = "collect_null" if found is None else "collect_not_a_list"
            return False
        new = [item for item in found if self._take(item)]
        self.collected += new
        self.dry = not new
        return bool(new)

    def output(self) -> dict[str, Any]:
        return {
            "items": list(self.collected),
            "iterations": self.iterations,
            "dry": self.dry,
            "stopped": self.stopped,
        }


def _reduce_source(step: dict[str, Any], scope: dict[str, Any]) -> Any:
    """A reduce's value: its ``input``, or its ``inputs`` as one list in order."""
    if "inputs" in step:
        return [resolve_path(scope, path) for path in step.get("inputs") or []]
    return resolve_path(scope, step["input"])


def _program_outputs(program: dict[str, Any], scope: dict[str, Any]) -> dict[str, Any]:
    """The declared output map; with none, the last step's output by its id."""
    declared = program.get("output") or {}
    if not declared:
        last = _last_step_id(program)
        declared = {last: f"steps.{last}.output"} if last else {}
    return {name: resolve_path(scope, path) for name, path in declared.items()}


def _raised(value: Any) -> bool:
    """A gate's ``when`` reads a value that raises it: anything but null,
    false, zero or an empty string, list or object."""
    if isinstance(value, (list, dict, str)):
        return bool(value)
    return value is not None and value is not False and value != 0


def _gate_text(text: str) -> str:
    """The question as a human reads it: no control or format characters
    (newlines kept), bounded."""
    kept = "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf")
    )
    if len(kept) > MAX_GATE_PROMPT_CHARS:
        kept = kept[:MAX_GATE_PROMPT_CHARS] + "…"
    return kept


def _gate_reason(node: str, decision: GateDecision) -> str:
    """``gate_rejected:<gate>`` for a human no; ``gate_refused:<gate>:<why>``
    when no human answered (no lane, a spent budget, a cancelled run)."""
    if decision.answered:
        return f"gate_rejected:{node}"[:MAX_REASON_CHARS]
    return f"gate_refused:{node}:{decision.reason or 'no_human'}"[:MAX_REASON_CHARS]


def _gate_record(
    request: GateRequest, phase_id: str, decision: GateDecision, attempt: str
) -> dict[str, Any]:
    """A human's answer, journalled under the gate's content key (D-8)."""
    return {
        "call_key": request.call_key,
        "kind": "gate",
        "label": f"gate {request.gate}",
        "step": request.metadata.get("step"),
        "phase": phase_id,
        "node": request.gate,
        "ok": bool(decision.approved),
        "answered": True,
        "class": request.trigger_class,
        "decided_by": decision.decided_by,
        "reason": decision.reason,
        "attempt": attempt,
    }


def _decision_from(record: dict[str, Any]) -> GateDecision:
    return GateDecision(
        approved=bool(record.get("ok")),
        answered=True,
        reason=str(record.get("reason") or ""),
        decided_by=str(record.get("decided_by") or ""),
    )


def _gate_note(
    node: str, step: dict[str, Any], decision: GateDecision, *, replayed: bool = False
) -> dict[str, Any]:
    return {
        "type": "gate",
        "gate": node,
        "raised": True,
        "class": step.get("class"),
        "approved": bool(decision.approved and decision.answered),
        "answered": decision.answered,
        "reason": decision.reason or None,
        "replayed": replayed,
    }


# ── slice 5 helpers ─────────────────────────────────────────────────────────


def _placed(call: _Call) -> _Call:
    """An isolated call's workspace key: its step's node (a map item's key is
    set already, shared by the item's stages), plus ``#<replica>`` for a
    fan-out replica, so parallel writers never share a workspace."""
    if call.isolation not in ISOLATED_MODES:
        return call
    key = call.ws_key or call.node
    if call.replica is not None:
        key = f"{key}#{call.replica}"
    return replace(call, ws_key=key)


def _merge_key(node: str) -> str:
    return f"merge_back:{node}"


def _merge_output(batch: MergeBatch, *, applied: bool, decided_by: str = "") -> dict[str, Any]:
    return {
        "applied": applied,
        "rejected": False,
        "files": batch.files(),
        "workspaces": [
            {
                "id": entry.workspace.id,
                "step": entry.workspace.step,
                "mode": entry.workspace.mode,
                "files": len(entry.changes),
                "diff": entry.diff_path,
            }
            for entry in batch.entries
        ],
        "skipped": list(batch.skipped),
        "decided_by": decided_by or None,
    }


def _merge_note(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "applied": bool(output.get("applied")),
        "rejected": bool(output.get("rejected")),
        "files": len(output.get("files") or []),
    }
