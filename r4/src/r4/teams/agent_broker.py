"""The governed agent broker for team plans (ADR-092 D-3, D-5, D-6, D-7).

The plan engine never holds a Client, a tool or a PermissionManager
(ADR-020 reverse control). It asks this broker for one agent turn at a
time, and the broker runs each request as one fresh companion turn through
the role-turn runtime (``role_agent.run_agent_turn``), so a plan agent and
a team role share one runtime.

What the broker adds around that turn, and why:

- **Its own permission manager** (D-6). Each turn runs on a fork of the
  lead's manager: its own taint slot (parallel agents raced on a shared
  one), an allowlist that only narrows, and approvals that name the agent.
  With no lead manager (the CLI) the fork's parent is a fail-closed
  ``AUTO_SAFE`` manager with no approval callback: safe tools run, every
  risky one is refused and the refusal is named in the result.
- **A fixed deny set** (D-6). ``TEAM_AGENT_DENY`` is never offered,
  whatever the profile's allowlist says; ``final_output`` always is.
- **Card grants** (MD-6). When the lead's human approved the run's card,
  every fork runs the card's SAFE tools without asking, for this run only;
  risky tools still follow the lead's mode per call.
- **Typed output** (D-5). A schema step's ``final_output`` is replaced by a
  copy whose ``result`` parameter is the schema. A text answer is parsed
  for one JSON value; an invalid one gets exactly one stateless repair call;
  after that it is a null with a named reason. Nothing is fabricated.
- **Budgets per model round** (D-1, D-7). Every round's tokens are charged
  to the run's meter (and to the lead's chat session when there is one).
  Before every round the guard refuses it once a run ceiling is spent:
  tokens, the wall clock, or the tool calls counted live across every turn
  of the run; or once the turn has used its profile's ``max_tool_rounds``.
  A tool that calls the model itself (``test_fix_loop``, ``search_agentic``)
  is built on a ``MeteredToolClient``: its calls are guarded and charged the
  same way, but are not rounds of the turn.
- **Taint follows the data** (D-6). Upstream excerpts are imported into the
  companion's registry after it is built (the companion attaches a new
  registry to its manager at construction); the turn's excerpts are
  exported with the result.
- **Project knowledge** (slice 2, K3). A profile listing ``knowledge_query``
  gets the companion's own tool (sized by the turn's reader, ADR-093 D-14)
  behind a project-scoped face: one ``query`` argument, no ``graphs=``, no
  ``project_id``; the run's project's graphs only. Its hits come back as
  numbered passages through the one pack renderer; each read is recorded
  with the result (``retrieval``, journalled by the engine); and the tool
  keeps its name, so its result registers ``rag_chunk`` taint (ADR-039).
- **Gates** (slice 2). ``ask_gate`` asks the lead's human through the
  never-auto approval path (``team_plan_gate``), labelled with the gate; with
  no approval lane (the CLI) it refuses by name. Nothing auto-approves it.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import os
import re
import threading
import time
import weakref
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from r4_contracts.team_engine.plan import (
    ENGINE_ONLY_TOOLS,
    GATE_TOOL,
    iter_agent_parts,
    profile_for,
    validate_output,
)
from r4_contracts.team_engine.services import (
    AgentTurnRequest,
    AgentTurnResult,
    GateDecision,
    GateRequest,
)
from r4_teamengine_spi.context import AgentBroker, BudgetMeter
from r4_teamengine_spi.errors import BudgetExhausted

from ..companion import tools as companion_tools
from ..companion.agent_workspace import AgentWorkspace, agent_workspace
from ..companion.cancellation import TurnCancellation, TurnCancelledError
from ..companion.permissions import TEAM_AGENT_DENY, PermissionManager, PermissionMode
from ..companion.session_models import BudgetLimitExceededError
from ..errors import ModelRouteRefusedError, R4Error
from ..teamup import role_agent
from .brokers import usage_tokens
from .project_knowledge import KNOWLEDGE_TOOL, project_knowledge_tool
from .tool_metering import MeteredToolClient

logger = logging.getLogger(__name__)

FINAL_OUTPUT = "final_output"
CALL_TYPE = "subagent"
# The companion's own default. A plan agent is a task-scoped worker whose
# answer is validated against a schema, so it runs cooler than a team role
# (role_agent passes 0.7 to every factory).
PLAN_AGENT_TEMPERATURE = 0.4
REPAIR_MAX_TOKENS = 4096
MAX_REPAIR_INPUT_CHARS = 8000
MAX_REASON_CHARS = 300

_NO_JSON = object()
_FENCED_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_SCHEMA_HINT = (
    "When you are done, call final_output with your answer as `result`. "
    "`result` must match that tool's parameter schema exactly."
)
_REPAIR_SYSTEM = (
    "You repair one JSON value so that it validates against a JSON schema. "
    "Keep the content; change only what the validation errors require. "
    "Reply with the corrected JSON value only: no prose, no code fence."
)
# One lock per lead session, shared by every broker that charges its hooks (D-7).
_SESSION_HOOK_LOCKS: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()
_SESSION_HOOK_LOCKS_GUARD = threading.Lock()

_FAIL_CLOSED_NOTE = "no approval lane: risky tools fail closed (ADR-092 D-6)"
_LADDER_NOTE = "refused by the permission ladder (ADR-092 D-6)"


class WorkspaceUnavailable(RuntimeError):
    """The workspace the engine named for a turn is not a directory (slice 5)."""


class RoundRefused(BudgetExhausted):
    """The next model round is refused: the named plan ceiling is spent (D-7).

    A ``BudgetExhausted``, so the null it becomes reads
    ``budget_exhausted:<kind>``, the same reason the engine gives an agent
    it refuses to start.
    """

    def __init__(self, kind: str) -> None:
        super().__init__(f"plan budget exhausted ({kind}); the next model round is refused")
        self.kind = kind


class _RunLimits:
    """The run-wide ceilings the round guard checks besides tokens (D-7).

    ``TokenBudgetMeter.remaining()`` reports only tokens, and the engine
    checks the wall clock and the summed tool calls only before it starts an
    agent (it sums a turn's tool calls only after the turn returns), so a
    turn in flight ran past both. The broker therefore keeps its own: the
    wall clock from the first turn it runs (a plan run starts on its first
    ``events()`` read, not at submit, so a clock started at construction
    could be spent before anything ran), and every turn's tool calls,
    counted under one lock as the companions announce them. A round's tool
    calls all run, so a turn in flight passes the tool-call ceiling by at
    most the batch of the round that reached it.
    """

    def __init__(self, budgets: Any, clock: Callable[[], float]) -> None:
        self.max_wallclock_sec = _positive(getattr(budgets, "max_wallclock_sec", None))
        self.max_tool_calls = _positive(getattr(budgets, "max_tool_calls", None))
        self._clock = clock
        self._lock = threading.Lock()
        self._t0: float | None = None
        self.tool_calls = 0

    def start(self) -> None:
        """Start the wall clock at the first turn; later turns keep it."""
        with self._lock:
            if self._t0 is None:
                self._t0 = self._clock()

    def add_tool_call(self) -> None:
        with self._lock:
            self.tool_calls += 1

    @contextlib.contextmanager
    def paused(self) -> Iterator[None]:
        """The wall clock stands still meanwhile (a gate waiting for a human)."""
        started = self._clock()
        try:
            yield
        finally:
            with self._lock:
                if self._t0 is not None:
                    self._t0 += self._clock() - started

    def spent(self) -> str | None:
        """``wallclock`` or ``tool_calls`` once that ceiling is reached, else None."""
        with self._lock:
            t0, calls = self._t0, self.tool_calls
        wallclock = self.max_wallclock_sec
        if wallclock and t0 is not None and self._clock() - t0 >= wallclock:
            return "wallclock"
        if self.max_tool_calls and calls >= self.max_tool_calls:
            return "tool_calls"
        return None


@dataclass
class _Turn:
    """One turn's own accounting. Turns run on parallel worker threads, so
    nothing here is shared between turns except ``limits``, the run's
    counters (locked there); the lock covers tool events a companion emits
    from its own parallel tool workers.

    ``max_rounds`` is the model rounds this turn may make (0: no cap),
    measured as ``llm_calls``: the rounds that were sent and charged, not
    the guard's calls (a send retried without streaming asks twice for one
    round) (D-1).
    """

    model: str = ""
    fork: Any = None
    fail_closed: bool = False
    cancel: TurnCancellation = field(default_factory=TurnCancellation)
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    tool_llm_calls: int = 0
    tool_calls: int = 0
    estimated: bool = False
    typed: list[Any] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    retrieval: list[dict[str, Any]] = field(default_factory=list)
    max_rounds: int = 0
    limits: _RunLimits | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    # ADR-092 slice 3: the run's live observer (``observer(kind, agent,
    # payload)``) and this turn's agent id, so a surface follows every round
    # and tool call of the agent (ADR-087). None: nobody watches.
    label: str = ""
    observer: Callable[[str, str, dict[str, Any]], None] | None = None
    last_tool: str = ""

    def may_start_round(self) -> bool:
        """False once the turn has made all the model rounds its cap allows."""
        with self.lock:
            return not self.max_rounds or self.llm_calls < self.max_rounds

    def add_round(self, tokens_in: int, tokens_out: int, payload: dict[str, Any]) -> None:
        with self.lock:
            self.input_tokens += tokens_in
            self.output_tokens += tokens_out
            self.llm_calls += 1
            self.estimated = self.estimated or bool(payload.get("estimated"))
            self.model = str(payload.get("model") or self.model)
            rounds, model = self.llm_calls, self.model
        self.notify(
            "round",
            {
                "round": rounds,
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "model": model or None,
            },
        )

    def notify(self, kind: str, payload: dict[str, Any]) -> None:
        """One live fact of this turn to the run's observer; never fails the turn."""
        if self.observer is None:
            return
        try:
            self.observer(kind, self.label, payload)
        except Exception:  # noqa: BLE001 — a watcher never breaks an agent
            logger.debug("plan observer failed on %s", kind, exc_info=True)

    def add_tool_llm(self, tokens_in: int, tokens_out: int, payload: dict[str, Any]) -> None:
        """A model call a tool made (``test_fix_loop``, ``search_agentic``):
        charged like a round, but not one of the turn's rounds (D-7)."""
        with self.lock:
            self.input_tokens += tokens_in
            self.output_tokens += tokens_out
            self.tool_llm_calls += 1
            self.estimated = self.estimated or bool(payload.get("estimated"))

    def on_activity(self, event: Any) -> None:
        """Count the agent's tool calls, for this turn and live for the run
        (D-7 ``max_tool_calls``); the terminal channel is not a capability,
        so it is not counted."""
        kind = getattr(event, "kind", "")
        if kind == "tool_result":
            self._notify_tool_result(event)
            return
        if kind != "tool_call":
            return
        with self.lock:
            self.last_tool = str(event.name or "")
        if event.name == FINAL_OUTPUT:
            return
        with self.lock:
            self.tool_calls += 1
        if self.limits is not None:
            self.limits.add_tool_call()
        self.notify("tool", {"phase": "call", "name": str(event.name or "")[:64]})

    def _notify_tool_result(self, event: Any) -> None:
        """The result of the tool this turn called last. The terminal channel
        (``final_output``) is the turn's answer, not a tool: it is not shown."""
        with self.lock:
            name = self.last_tool
        if name == FINAL_OUTPUT:
            return
        duration = getattr(event, "duration_ms", None)
        self.notify(
            "tool",
            {
                "phase": "result",
                "name": name[:64],
                "status": str(getattr(event, "status", "") or "")[:32],
                "duration_ms": duration if isinstance(duration, (int, float)) else None,
            },
        )

    def usage(self) -> dict[str, Any]:
        """The usage keys the plan engine sums (input, output, calls)."""
        with self.lock:
            return {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "llm_calls": self.llm_calls + self.tool_llm_calls,
                "tool_calls": self.tool_calls,
                "estimated": self.estimated,
            }

    def taint_out(self) -> tuple[Any, ...]:
        registry = getattr(self.fork, "taint_registry", None)
        return tuple(registry.export_segments()) if registry is not None else ()

    def refusal_note(self) -> str:
        if not self.refused:
            return ""
        why = _FAIL_CLOSED_NOTE if self.fail_closed else _LADDER_NOTE
        return f"[refused tools: {', '.join(self.refused)}; {why}]"


class RoleTurnAgentBroker(AgentBroker):
    """Runs every plan agent as one governed role turn (ADR-092 D-3).

    ``usage_hooks`` is ``(record_usage(call_type, payload),
    guard_llm_call(call_type))`` from the lead's chat session, or None: when
    a plan is started from chat, its agents' rounds count against that
    session's usage records and LLM-call caps too (D-7). Both hooks run
    under one lock shared by every broker of that session (see
    ``session_hook_lock``), because the session's records are not
    thread-safe and two plans of one session can run at once.

    ``budgets`` is the run's ``BudgetSpec``: its wall clock and tool-call
    ceilings are checked before every round, as tokens are (D-7). ``clock``
    is the wall clock's time source (``time.monotonic``).

    ``run_grants`` (MD-6) are the SAFE tools the lead's approved card listed
    for this run: every agent's fork runs them without asking, for this run
    only; risky tools still ask per call. Empty when no human approved a
    card (the CLI, a test).

    ``observer`` (slice 3) is told each agent's model rounds and tool calls
    as they happen, ``observer(kind, agent_label, payload)`` with kind
    ``round`` or ``tool``, so a surface streams them keyed by agent id
    (ADR-087). It only watches: it never decides, and a failing observer
    never fails a turn.

    ``project_root`` (slice 5) is the run's project root. Every tool call of
    a turn runs in the agent's workspace (``agent_workspace``): the
    ``workspace`` the engine names in the request's metadata (an isolated
    agent's worktree or copy), else the project root. The file, shell and
    python tools resolve relative paths and their working directory there
    and refuse paths outside it. With no root at all (a test double) tools
    run uncontained, as before.
    """

    def __init__(
        self,
        client: Any,
        *,
        meter: BudgetMeter,
        run_id: str,
        permission_manager: Any = None,
        session_model: str | None = None,
        usage_hooks: tuple[Any, Any] | None = None,
        web_access: Callable[[], Any] | None = None,
        factory: Callable[..., Any] | None = None,
        budgets: Any = None,
        clock: Callable[[], float] | None = None,
        run_grants: Iterable[str] = (),
        project: str | None = None,
        gate_lane: Any = None,
        observer: Callable[[str, str, dict[str, Any]], None] | None = None,
        project_root: str | None = None,
    ) -> None:
        self._client = client
        self._observer = observer
        self._project_root = str(project_root) if project_root else None
        self._run_grants = frozenset(run_grants or ())
        self._project = project or None
        # Who answers a gate: the lead's manager (its approval callback is
        # the human), or a lane of its own (the CLI's terminal prompt); none
        # in a CLI run without a terminal, where every gate refuses.
        self._gate_lane = gate_lane if gate_lane is not None else permission_manager
        self._meter = meter
        self._run_id = str(run_id or "")
        self._session_model = session_model or None
        self._record_usage, self._host_guard = usage_hooks or (None, None)
        # ADR-099 D-9 (RS-1 fix W3): the agents' web tools follow the lead's
        # choice; without it, R4_WEB_ACCESS, else they refuse.
        self._web_access = web_access
        self._factory = factory
        self._hook_lock = session_hook_lock(usage_hooks)
        self._limits = _RunLimits(budgets, clock or time.monotonic)
        self._cancel = TurnCancellation()
        self._live: set[TurnCancellation] = set()
        self._live_lock = threading.Lock()
        self.cancel_reason: str | None = None
        self._parent, self.fail_closed = _parent_manager(permission_manager)

    # ── the AgentBroker surface ──────────────────────────────────────────────

    async def run_turn(self, request: AgentTurnRequest) -> AgentTurnResult:
        """One turn on a worker thread: the companion loop is synchronous."""
        return await asyncio.to_thread(self._run_sync, request)

    def cancel(self, reason: str) -> None:
        """Stop every turn in flight at its next cancel check, and every later one.

        A plan is cancelled as a unit (D-3): the run's own token refuses
        every later turn, and each live turn's token is requested, which also
        aborts its open stream. Each turn has its own token because every
        streamed round registers an abort closer on the token it is given;
        one run-wide token would keep every finished round's closer.
        """
        self.cancel_reason = reason or "cancelled"
        self._cancel.request()
        with self._live_lock:
            live = list(self._live)
        for token in live:
            token.request()

    async def ask_gate(self, request: GateRequest) -> GateDecision:
        """Ask the human through the never-auto approval path (slice 2).

        On a worker thread: the approval callback blocks until the human
        answers. The wall clock of the run's per-round guard does not run
        meanwhile (a human reads at human speed, D-7).
        """
        return await asyncio.to_thread(self._ask_gate_sync, request)

    def _ask_gate_sync(self, request: GateRequest) -> GateDecision:
        lane = self._gate_lane
        fork = getattr(lane, "fork_for_agent", None)
        if self._cancel.is_set():
            return GateDecision(approved=False, answered=False, reason="cancelled")
        if not callable(fork) or getattr(lane, "_approval_callback", None) is None:
            return GateDecision(approved=False, answered=False, reason="no_human")
        if getattr(getattr(lane, "mode", None), "value", "") == PermissionMode.DENY.value:
            return GateDecision(approved=False, answered=False, reason="permission_mode_deny")
        asker = fork(f"gate {request.gate}", allow=(GATE_TOOL,), run_id=self._run_id)
        arguments = {
            "gate": request.gate,
            "class": request.trigger_class,
            "prompt": request.prompt,
            "run_id": self._run_id,
        }
        with self._limits.paused():
            approved = bool(asker.check(GATE_TOOL, arguments))
        return GateDecision(approved=approved, answered=True, decided_by="human")

    def preflight_models(self, models: Iterable[str | None]) -> None:
        """Resolve every distinct model through the ADR-062 gate before any spend.

        D-6: a public route for a sensitive project is refused by name when
        the plan is submitted, not mid-run after other agents have spent.
        The gate's ``R4Error`` propagates. A client without the preflight (a
        test double) has no route to name; every real round still resolves
        through ``Client._resolve_gated_route``.
        """
        preflight = getattr(self._client, "preflight_model_route", None)
        if not callable(preflight):
            logger.debug("client has no model preflight; the ADR-062 gate runs per round")
            return
        for model in sorted({str(m) for m in models if m}):
            preflight(model)

    # ── one turn ────────────────────────────────────────────────────────────

    def _run_sync(self, req: AgentTurnRequest) -> AgentTurnResult:
        """The turn, with every failure mapped to a named null (D-5)."""
        self._limits.start()
        turn = self._new_turn(req)
        with self._live_lock:
            self._live.add(turn.cancel)
        try:
            # After registering: a cancel() between the two is still seen here.
            if self._cancel.is_set():
                return _failed(turn, "cancelled")
            return self._attempt(req, turn)
        except Exception as exc:  # noqa: BLE001 — a failed turn is a null, never a crash
            reason = self._failure_reason(exc, turn)
            if reason.startswith("error:"):
                # Unexpected: keep the traceback, a bare message was undiagnosable.
                logger.warning("plan agent %s failed: %s", req.label, reason, exc_info=exc)
            return _failed(turn, reason)
        finally:
            with self._live_lock:
                self._live.discard(turn.cancel)

    def _new_turn(self, req: AgentTurnRequest) -> _Turn:
        """The turn's accounting, naming the model it will run on.

        With no model named anywhere the companion runs on the client's
        configured default, so that name is recorded rather than "" (D-8:
        a journal record says which model answered, even for a turn that
        failed before its first round reported one).
        """
        return _Turn(
            model=self._turn_model(req) or "",
            fail_closed=self.fail_closed,
            max_rounds=_round_cap(req.max_tool_rounds),
            limits=self._limits,
            label=req.label,
            observer=self._observer,
        )

    def _turn_model(self, req: AgentTurnRequest) -> str | None:
        """The model a turn runs on: the agent's own, else the lead session's,
        else the client's configured default (the companion's own fallback,
        ``_effective_model``); None only for a client with no configured model."""
        return req.model or self._session_model or client_default_model(self._client)

    def _attempt(self, req: AgentTurnRequest, turn: _Turn) -> AgentTurnResult:
        turn.fork = self._fork(req, turn)
        final_text, _activities = role_agent.run_agent_turn(
            self._client,
            label=req.label,
            prompt=req.prompt,
            system=_system_for(req),
            tools=self._tools(req, turn),
            model=req.model or self._session_model,
            permission_manager=turn.fork,
            max_tool_rounds=req.max_tool_rounds,
            usage_callback=functools.partial(self._charge, turn),
            pre_llm_call=functools.partial(self._round_guard, turn),
            cancel=turn.cancel,
            on_activity=turn.on_activity,
            factory=self._companion_factory(turn.fork, req.taint_in),
        )
        turn.cancel.check()  # a stopped turn's partial text is never an answer
        text = str(final_text or "")
        if req.schema is None:
            return _plain_result(turn, text)
        return self._schema_result(req, turn, text)

    def _fork(self, req: AgentTurnRequest, turn: _Turn) -> Any:
        """The agent's own manager (D-6); the deny set never reaches its allowlist."""
        allow = sorted(set(req.tools) - TEAM_AGENT_DENY - ENGINE_ONLY_TOOLS)
        grants = {"grants": sorted(self._run_grants)} if self._run_grants else {}
        fork = self._parent.fork_for_agent(req.label, allow=allow, run_id=self._run_id, **grants)
        _record_refusals(fork, turn.refused)
        return fork

    def _tools(self, req: AgentTurnRequest, turn: _Turn) -> list[dict[str, Any]]:
        """The profile's tools that exist, minus the deny set, plus the
        terminal channel; a schema step gets the typed one (D-5, D-6).

        The catalog's reader is the model this turn runs on and records
        (:meth:`_turn_model`), so the agent's retrieval is sized for the
        model that reads it (ADR-093 D-14), even when no model is named
        anywhere and the companion runs on the client's default.
        """
        wanted = (set(req.tools) | {FINAL_OUTPUT}) - TEAM_AGENT_DENY - ENGINE_ONLY_TOOLS
        reader = self._turn_model(req)
        workspace = self._workspace(req)
        tools = [
            _in_workspace(self._scoped(t, turn), workspace)
            for t in self._catalog(turn, reader)
            if t.get("name") in wanted
        ]
        if req.schema is None:
            return tools
        typed = _typed_final_output(req.schema, turn.typed)
        return [t for t in tools if t.get("name") != FINAL_OUTPUT] + [typed]

    def _workspace(self, req: AgentTurnRequest) -> AgentWorkspace | None:
        """The agent's workspace (slice 5): the engine's ``workspace``, else
        the project root; None only with neither (a test double). A named
        workspace that is not a directory fails the turn: a tool call must
        never fall back to the process's own directory."""
        meta = req.metadata or {}
        root = meta.get("workspace") or self._project_root
        if not root:
            return None
        if not os.path.isdir(str(root)):
            raise WorkspaceUnavailable(f"workspace {root} is not a directory")
        project = meta.get("project_root") or self._project_root or root
        return AgentWorkspace.of(str(root), str(project), label=req.label)

    def _scoped(self, tool: dict[str, Any], turn: _Turn) -> dict[str, Any]:
        """``knowledge_query`` behind its project-scoped face (slice 2, K3);
        every other tool as the catalog built it."""
        if tool.get("name") != KNOWLEDGE_TOOL:
            return tool
        return project_knowledge_tool(
            tool, self._client, project=self._project, record=turn.retrieval.append
        )

    def _catalog(self, turn: _Turn, reader: str | None) -> list[dict[str, Any]]:
        """The host's builtin catalog for this agent's fork, built for its
        reader; a client that cannot build it runs the agent tool-free, as
        role turns do.

        Built on a :class:`MeteredToolClient`, so a tool that calls the model
        itself (``test_fix_loop``, ``search_agentic``) is guarded by the run's
        ceilings and the lead session's caps and charged to this turn, the
        run's meter and the session, as every round is (D-7).

        Built per turn, never cached: each turn has its own fork, and turns
        of one plan run on different models, so a catalog kept by fork alone
        would hand one agent another's reader (2026-10-11, c23-broker-reader:
        ``knowledge_query`` and ``search_docs`` size their cut by it, D-14,
        and ``search_agentic`` synthesises and plans on it).
        """
        client = MeteredToolClient(
            self._client,
            charge=functools.partial(self._charge_tool, turn),
            guard=functools.partial(self._tool_guard, turn),
        )
        try:
            return companion_tools.get_builtin_tools(
                client,
                session_model=reader,
                permission_manager=turn.fork,
                web_access=self._web_access,
            )
        except Exception as exc:  # noqa: BLE001 — an availability gap, not a crash
            logger.warning("plan agent tool discovery unavailable (%s); running tool-free", exc)
            return []

    def _companion_factory(self, fork: Any, taint_in: Iterable[Any]) -> Callable[..., Any]:
        """The role-turn factory, with plan-agent settings and upstream taint.

        The companion attaches a NEW registry to its manager when it is
        built, so upstream excerpts are imported right after construction,
        into the registry the companion's fence will consult (D-6).
        """
        base = self._factory or role_agent._default_factory
        segments = tuple(taint_in or ())

        def build(client: Any, **kwargs: Any) -> Any:
            kwargs["temperature"] = PLAN_AGENT_TEMPERATURE
            companion = base(client, **kwargs)
            if segments:
                fork.taint_registry.import_segments(segments)
            return companion

        return build

    # ── budgets (D-7) ───────────────────────────────────────────────────────

    def _charge(self, turn: _Turn, call_type: str, payload: dict[str, Any]) -> None:
        """One model round: the turn's usage, the run's meter, the lead's session.

        A round past the ceiling is still recorded (its tokens were spent);
        ``_round_guard`` refuses the next one.
        """
        tokens_in = usage_tokens(payload, "prompt_tokens")
        tokens_out = usage_tokens(payload, "completion_tokens")
        turn.add_round(tokens_in, tokens_out, payload)
        self._spend(call_type, payload, tokens_in + tokens_out)

    def _charge_tool(self, turn: _Turn, call_type: str, payload: dict[str, Any]) -> None:
        """A model call a tool made: the same meter and session, not a round."""
        tokens_in = usage_tokens(payload, "prompt_tokens")
        tokens_out = usage_tokens(payload, "completion_tokens")
        turn.add_tool_llm(tokens_in, tokens_out, payload)
        self._spend(call_type, payload, tokens_in + tokens_out)

    def _spend(self, call_type: str, payload: dict[str, Any], tokens: int) -> None:
        """Charge the run's meter, then the lead session's usage records."""
        try:
            self._meter.spend_tokens(tokens)
        except BudgetExhausted as exc:
            logger.info("plan %s: %s", self._run_id, exc)
        if self._record_usage is None:
            return
        try:
            with self._hook_lock:
                self._record_usage(call_type, payload)
        except Exception as exc:  # noqa: BLE001 — telemetry never fails a turn
            logger.warning("session usage record failed for a plan agent: %s", exc)

    def _round_guard(self, turn: _Turn, call_type: str) -> None:
        """A companion's ``pre_llm_call``: the run's ceilings, then the
        turn's round cap, then the lead session's LLM-call caps (D-1, D-7).

        The cap is enforced here because the companion treats
        ``max_tool_rounds`` as a soft checkpoint interval and stops only at
        its own hard roof (1000 rounds by default), so a profile's budget
        bounded nothing.
        """
        self._refuse_spent()
        if not turn.may_start_round():
            raise RoundRefused("tool_rounds")
        self._ask_host(call_type)

    def _tool_guard(self, turn: _Turn, call_type: str) -> None:
        """Before a model call a tool makes: the turn's cancel, the run's
        ceilings, the lead session's caps (D-7). Not a round, so the turn's
        round cap does not apply; a refusal raises inside the tool, which
        reports it as its own failure to the agent."""
        turn.cancel.check()
        self._guard(call_type)

    def _guard(self, call_type: str) -> None:
        """Before a broker-made call (the repair): the run's ceilings, then
        the lead session's LLM-call caps (D-7). Not a companion round, so
        the turn's round cap does not apply."""
        self._refuse_spent()
        self._ask_host(call_type)

    def _refuse_spent(self) -> None:
        """Refuse the round once a run ceiling is spent: tokens on the meter,
        then the wall clock and the live tool-call count (D-7)."""
        kind = _spent_ceiling(self._meter.remaining() or {}) or self._limits.spent()
        if kind is not None:
            raise RoundRefused(kind)

    def _ask_host(self, call_type: str) -> None:
        if self._host_guard is not None:
            with self._hook_lock:
                self._host_guard(call_type)

    def _failure_reason(self, exc: Exception, turn: _Turn) -> str:
        """The named reason of a failed turn (D-5: a null always says why).

        A cancelled turn is ``cancelled`` whatever its stream raised when the
        abort closed it, including in the moment before ``cancel`` reached
        this turn's own token.
        """
        cancelled = turn.cancel.is_set() or self._cancel.is_set()
        if cancelled or isinstance(exc, TurnCancelledError):
            return "cancelled"
        if isinstance(exc, BudgetExhausted):
            return f"budget_exhausted:{getattr(exc, 'kind', 'tokens')}"
        if isinstance(exc, BudgetLimitExceededError):
            return "budget_exhausted:session_llm_calls"
        if isinstance(exc, ModelRouteRefusedError):  # the ADR-062 route gate
            return f"model_refused:{_clip(str(exc))}"
        if isinstance(exc, WorkspaceUnavailable):
            return f"workspace_unavailable:{_clip(str(exc))}"
        if isinstance(exc, R4Error):
            return f"error:{type(exc).__name__}:{_clip(str(exc))}"
        return f"error:{type(exc).__name__}"

    # ── structured output (D-5) ─────────────────────────────────────────────

    def _schema_result(self, req: AgentTurnRequest, turn: _Turn, text: str) -> AgentTurnResult:
        """The typed result, else JSON in the text, else one repair, else a named null."""
        schema = req.schema or {}
        candidates = _candidates(turn.typed, text)
        output, errors = _first_valid(candidates, schema)
        repaired = False
        if errors:
            bad = _as_text(candidates[0]) if candidates else text
            output, errors = self._repair(req, turn, bad, errors)
            repaired = True
        if errors:
            return _failed(turn, f"schema_invalid:{_clip(errors[0])}", text)
        return _ok(turn, output, text, repaired=repaired)

    def _repair(
        self, req: AgentTurnRequest, turn: _Turn, bad: str, errors: list[str]
    ) -> tuple[Any, list[str]]:
        """Exactly one stateless call, charged and guarded like any round (D-5, D-7)."""
        self._guard(CALL_TYPE)
        prompt = _repair_prompt(req.schema or {}, errors, bad)
        model = req.model or self._session_model
        response = self._repair_call(prompt, model, turn, req.schema or {})
        content = str(getattr(response, "content", "") or "")
        self._charge(turn, CALL_TYPE, _reply_usage(response, prompt, content, model))
        value = _json_in(content)
        if value is _NO_JSON:
            return None, ["$: the repair reply holds no JSON value"]
        return value, validate_output(value, req.schema or {})

    def _repair_call(
        self, prompt: str, model: str | None, turn: _Turn, schema: dict[str, Any]
    ) -> Any:
        """The repair call, with the step's schema as ``response_format`` where
        the route honours it (ADR-092 slice 9: an optimisation only).

        Whatever comes back is validated by the caller exactly as a plain
        reply is (``validate_output``): the stdlib validator stays the
        authority. A route that does not honour it, or a call that fails
        with it (a provider refusing the field), runs plain, logged; a
        cancelled turn is never retried.
        """
        fmt = _response_format(self._client, model, schema)
        if fmt is not None:
            try:
                return self._repair_chat(prompt, model, turn, fmt)
            except Exception as exc:  # noqa: BLE001 — falls back to plain, named in the log
                turn.cancel.check()
                logger.info("response_format refused (%s); repairing plain", type(exc).__name__)
                self._guard(CALL_TYPE)
        return self._repair_chat(prompt, model, turn, None)

    def _repair_chat(
        self, prompt: str, model: str | None, turn: _Turn, fmt: dict[str, Any] | None
    ) -> Any:
        extra = {"response_format": fmt} if fmt is not None else {}
        return self._client.chat(
            message=prompt,
            system=_REPAIR_SYSTEM,
            model=model,
            temperature=0.0,
            max_tokens=REPAIR_MAX_TOKENS,
            _turn_cancel=turn.cancel,
            **extra,
        )


# ── helpers ─────────────────────────────────────────────────────────────────


RESPONSE_FORMAT_NAME = "plan_step_output"


def _response_format(client: Any, model: str | None, schema: dict[str, Any]) -> dict | None:
    """The OpenAI ``json_schema`` response format for ``schema``, or None when
    the client cannot say its route honours one (slice 9)."""
    honours = getattr(client, "honours_response_format", None)
    if not callable(honours) or not schema:
        return None
    try:
        if not honours(model, cancellable=True):
            return None
    except Exception as exc:  # noqa: BLE001 — unknown support is no support
        logger.debug("response_format support unknown: %s", exc)
        return None
    return {
        "type": "json_schema",
        "json_schema": {"name": RESPONSE_FORMAT_NAME, "schema": schema, "strict": False},
    }


def session_hook_lock(hooks: tuple[Any, Any] | None) -> Any:
    """The one lock every broker charging the same session's hooks takes (D-7).

    A plan started from chat gets a new service and broker per call, and a
    run outlives the call that started it, so two plans of one session can
    charge it at once; a lock per broker serialised neither against the
    other and the session's per-turn LLM-call count lost updates. The key
    is the object the hooks are bound to (the session; a bound method is a
    new object at every attribute read), else the hook itself; a key that
    cannot be weakly held gets a lock of its own.

    Still racing: the lead's own companion calls the same hooks on the chat
    thread without this lock, so a lead round can interleave with a plan
    round until the session locks inside its own hooks.
    """
    hook = next((h for h in hooks or () if h is not None), None)
    if hook is None:
        return threading.RLock()
    owner = getattr(hook, "__self__", hook)
    with _SESSION_HOOK_LOCKS_GUARD:
        try:
            return _SESSION_HOOK_LOCKS.setdefault(owner, threading.RLock())
        except TypeError:  # unhashable, or no weak reference: this broker alone
            return threading.RLock()


def _round_cap(max_tool_rounds: Any) -> int:
    """Model rounds one turn may make: its tool rounds plus the one that
    answers (D-1). A tool round is a model round that asks for tools; the
    round that reads the last results and answers is not one, so
    ``max_tool_rounds: 1`` still lets an agent use one tool's result. The
    broker cannot take tools away from a round, so an agent still asking
    for tools in its answering round has that batch run and its next round
    refused (``budget_exhausted:tool_rounds``)."""
    return max(1, int(max_tool_rounds or 1)) + 1


def _positive(value: Any) -> float | None:
    """A ceiling that is set (a positive number), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if value > 0 else None


def client_default_model(client: Any) -> str | None:
    """The model the client runs when a call names none (``config.model``).

    The companion falls back to it (``_effective_model``), so the preflight
    and the turn's record name it too (D-6, D-8); a client without a
    configured model (a test double) has none.
    """
    for attr in ("config", "_config"):
        value = getattr(getattr(client, attr, None), "model", None)
        if isinstance(value, str) and value:
            return value
    return None


def _parent_manager(host: Any) -> tuple[Any, bool]:
    """The manager every agent forks: the lead's, or a fail-closed one (D-6).

    Agents never share an unforked manager, so a host without forks is
    replaced by the fail-closed manager rather than passed through.
    """
    if host is not None and callable(getattr(host, "fork_for_agent", None)):
        return host, False
    if host is not None:
        logger.warning("permission manager without agent forks; plan agents fail closed")
    return PermissionManager(PermissionMode.AUTO_SAFE), True


def _record_refusals(fork: Any, refused: list[str]) -> None:
    """Name every tool the fork refuses (D-6: a refusal is named in the result).

    The companion reports a blocked tool only to the model; wrapping this
    turn's own fork is the one place the broker can see it.
    """
    gate = fork.check

    def check(tool_name: str, arguments: dict[str, Any]) -> bool:
        allowed = gate(tool_name, arguments)
        if not allowed and tool_name not in refused:
            refused.append(tool_name)
        return allowed

    fork.check = check


def _in_workspace(tool: dict[str, Any], workspace: AgentWorkspace | None) -> dict[str, Any]:
    """``tool`` whose every call runs in the agent's workspace (slice 5).

    Set around the call itself, not around the turn: the companion may run
    a concurrency-safe tool on a pool thread, which would not inherit a
    context variable set on the turn's thread.
    """
    if workspace is None:
        return tool
    inner = tool["execute"]

    @functools.wraps(inner)
    def execute(*args: Any, **kwargs: Any) -> Any:
        with agent_workspace(workspace):
            return inner(*args, **kwargs)

    return {**tool, "execute": execute}


def _typed_final_output(schema: dict[str, Any], holder: list[Any]) -> dict[str, Any]:
    """``final_output`` whose ``result`` IS the step schema (D-5).

    Same name, so the companion's terminal channel ends the turn unchanged;
    the object is kept as given, not re-parsed from (possibly truncated)
    tool-result text.
    """

    def execute(**kwargs: Any) -> str:
        if "result" not in kwargs:
            return str(kwargs.get("summary") or "")
        holder.append(kwargs["result"])
        return json.dumps(kwargs["result"], ensure_ascii=False)

    return {
        "name": FINAL_OUTPUT,
        "description": (
            "Finish the task. Pass your answer as `result`, which must match this "
            "parameter's schema; `summary` is optional."
        ),
        "parameters": {
            "type": "object",
            "properties": {"result": schema, "summary": {"type": "string"}},
            "required": ["result"],
        },
        "is_final": True,
        "execute": execute,
    }


def _system_for(req: AgentTurnRequest) -> str:
    """The profile's instructions verbatim, plus the typed channel's contract."""
    if req.schema is None:
        return req.instructions
    return f"{req.instructions}\n\n{_SCHEMA_HINT}".strip()


def _spent_ceiling(remaining: dict[str, Any]) -> str | None:
    """The first ceiling with nothing left (``max_tokens`` → ``tokens``)."""
    for key, value in remaining.items():
        if isinstance(value, int) and not isinstance(value, bool) and value <= 0:
            return key[4:] if key.startswith("max_") else key
    return None


def _candidates(typed: list[Any], text: str) -> list[Any]:
    """What may be the answer, best first: the typed result (and the JSON a
    model stringified into it), then the JSON value in the final text."""
    found: list[Any] = []
    if typed:
        found.append(typed[-1])
        if isinstance(typed[-1], str):
            found.append(_json_in(typed[-1]))
    found.append(_json_in(text))
    return [value for value in found if value is not _NO_JSON]


def _first_valid(candidates: list[Any], schema: dict[str, Any]) -> tuple[Any, list[str]]:
    """The first candidate that validates, else the first one's errors."""
    first_errors: list[str] | None = None
    for value in candidates:
        errors = validate_output(value, schema)
        if not errors:
            return value, []
        first_errors = errors if first_errors is None else first_errors
    return None, first_errors or ["$: the reply holds no JSON value"]


def _json_in(text: str) -> Any:
    """One JSON value in a reply: the whole text, a fenced block, or the
    outermost object (precedent: ``runtime/agent_executor.py``)."""
    for candidate in _json_texts(text):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return _NO_JSON


def _json_texts(text: str) -> Iterator[str]:
    stripped = (text or "").strip()
    if not stripped:
        return
    yield stripped
    for match in _FENCED_RE.finditer(stripped):
        yield match.group(1).strip()
    match = _OBJECT_RE.search(stripped)
    if match is not None:
        yield match.group()


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _repair_prompt(schema: dict[str, Any], errors: list[str], bad: str) -> str:
    """Schema, errors and the bad value; the value is agent output, so it is
    delimited and marked untrusted (D-4 envelope)."""
    return (
        "JSON schema:\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n\n"
        "Validation errors:\n"
        + "\n".join(f"- {error}" for error in errors[:10])
        + '\n\n<step_output source="repair" untrusted="true">\n'
        f"{bad[:MAX_REPAIR_INPUT_CHARS]}\n"
        "</step_output>"
    )


def _reply_usage(response: Any, prompt: str, content: str, model: str | None) -> dict[str, Any]:
    """The repair call's usage in the companion's payload shape; estimated
    (about four characters a token) when the route reports none."""
    usage = getattr(response, "usage", None)
    tokens_in = usage_tokens(usage, "prompt_tokens")
    tokens_out = usage_tokens(usage, "completion_tokens")
    estimated = tokens_in + tokens_out == 0
    if estimated:
        tokens_in, tokens_out = len(prompt) // 4 + 1, len(content) // 4 + 1
    return {
        "prompt_tokens": tokens_in,
        "completion_tokens": tokens_out,
        "total_tokens": tokens_in + tokens_out,
        "estimated": estimated,
        "model": str(getattr(response, "model", "") or model or ""),
    }


def _plain_result(turn: _Turn, text: str) -> AgentTurnResult:
    """A no-schema step's answer is the final text; an empty one is a named null."""
    if text.strip():
        return _ok(turn, text, text)
    if turn.refused:
        return _failed(turn, f"tool_refused:{','.join(turn.refused)}")
    return _failed(turn, "empty_output")


def _ok(turn: _Turn, output: Any, text: str, *, repaired: bool = False) -> AgentTurnResult:
    return AgentTurnResult(
        ok=True,
        output=output,
        text=_with_note(text, turn.refusal_note()),
        usage=turn.usage(),
        model=turn.model,
        taint_out=turn.taint_out(),
        repaired=repaired,
        retrieval=tuple(turn.retrieval),
    )


def _failed(turn: _Turn, reason: str, text: str = "") -> AgentTurnResult:
    return AgentTurnResult(
        ok=False,
        reason=reason,
        text=_with_note(text, turn.refusal_note()),
        usage=turn.usage(),
        model=turn.model,
        taint_out=turn.taint_out(),
        retrieval=tuple(turn.retrieval),
    )


def _with_note(text: str, note: str) -> str:
    if not note:
        return text
    return f"{text}\n\n{note}" if text else note


def _clip(text: str, cap: int = MAX_REASON_CHARS) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= cap else flat[: cap - 1] + "…"


# ── the models a plan runs on ───────────────────────────────────────────────


def plan_models(
    program: dict[str, Any],
    *,
    default_model: str | None = None,
    session_model: str | None = None,
    client_model: str | None = None,
) -> list[str]:
    """Every distinct model a bound plan's agents will run on (D-6 preflight).

    The same fallback the run applies: the step's model, else its profile's,
    else the plan's default, else the lead session's model, else the
    client's configured default (``client_model``). Without that last link
    a plan naming no model preflighted nothing, and a public default route
    for a sensitive project was refused mid-run after other agents spent.
    """
    fallback = default_model or session_model or client_model
    found: set[str] = set()
    # Every agent part: a repeat's body and an inlined template's steps too
    # (slice 2), or a nested agent's model would spend unpreflighted.
    for part in iter_agent_parts(program):
        model = profile_for(program, part.part).get("model") or fallback
        if model:
            found.add(str(model))
    return sorted(found)


__all__ = [
    "RoleTurnAgentBroker",
    "RoundRefused",
    "client_default_model",
    "plan_models",
    "session_hook_lock",
]
