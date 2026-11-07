"""Native R4 team engine (ADR-020 phase 1).

Core-tier engine: compiles TeamSpec into a TeamupSessionConfig and drives
the existing r4.teamup session machinery. ADR-092 slice 9 (native
convergence): every role turn is one ``AgentTurnRequest`` to the run's
``AgentBroker`` (``RuntimeServices.agents``), the one governed turn path the
plan engine uses too (a forked permission manager, the deny set, the ADR-062
gate, per-round budgets charged to the run's ``BudgetMeter``). The session's
own client refuses any direct model call; a host without an AgentBroker is
refused by name (``agent_broker_required``).

Agent mapping: agent names/roles matching pm/se/pg/cd map to native roles;
anything else runs under the default HPC role set. Teamup iteration
semantics stay intact — a sequential TeamSpec becomes iterations.

ADR-092 slice 4: the teamup runtime is the HPC preset (``AgentRole`` is
frozen to it); names are read through ``AgentKey``. Crews no longer reach
this engine: each is a plan template on the ``plan`` engine. A roster spec
still lowers here (D-2: native stays the default for roster specs).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import AsyncIterator
from typing import Any

from r4_contracts.team_engine.capabilities import (
    CheckpointScope,
    EngineCapabilities,
    EngineDescriptor,
    TrustTier,
)
from r4_contracts.team_engine.events import TERMINAL_EVENT_KINDS, EventKind, TeamEvent
from r4_contracts.team_engine.results import (
    CheckpointRef,
    ExecutionGraph,
    NodeState,
    RunMetrics,
    TeamResult,
)
from r4_contracts.team_engine.services import AgentTurnRequest, AgentTurnResult
from r4_contracts.team_engine.spec import TeamSpec, WorkflowStyle
from r4_teamengine_spi.context import EngineContext, RuntimeServices
from r4_teamengine_spi.engine import CompiledPlan, EngineRun, TeamEngine
from r4_teamengine_spi.errors import (
    BudgetExhausted,
    IncompatibleCheckpoint,
    SpecNotExpressible,
    ValidationIssue,
)
from r4_teamengine_spi.version import SPI_VERSION

from ..teamup.roles import AgentKey, AgentRole
from ..teamup.session import TeamupSession, TeamupSessionConfig
from .brokers import FileEventSink, utcnow


def _preset_role(token: Any) -> AgentRole | None:
    """The HPC preset role a spec's agent name or role names (any spelling)."""
    return AgentKey(token).role


class _NoDirectModelCalls:
    """The session client of a native SPI run: role turns go through the
    run's AgentBroker (slice 9), so any direct model call is a bug, refused."""

    config = None

    def chat(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(
            "native engine: a direct model call bypassed the run's AgentBroker "
            "(ADR-092 slice 9: every role turn is an AgentTurnRequest)"
        )


class _BrokerTurns:
    """``(role, context, system_prompt, model) -> text`` over the AgentBroker.

    One request per role turn; a turn the broker could not complete (a
    budget, a refused model, a cancel) raises with its reason, which the
    session records as that role's error. Usage is summed for the metrics.
    """

    def __init__(self, agents: Any, run_id: str) -> None:
        self._agents = agents
        self._run_id = run_id
        self._lock = threading.Lock()
        self._seq = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.llm_calls = 0
        self.turns = 0

    def __call__(self, role: Any, context: str, system_prompt: str, model: str | None) -> str:
        label = str(getattr(role, "value", role))
        with self._lock:
            self._seq += 1
            seq = self._seq
        request = AgentTurnRequest(
            call_key=f"native:{self._run_id}:{label}:{seq}",
            label=label,
            prompt=context,
            instructions=system_prompt,
            tools=(),
            model=model,
            metadata={"engine": "native", "role": label},
        )
        result = _run_coroutine(self._agents.run_turn(request))
        self._count(result)
        if not result.ok:
            raise RuntimeError(f"agent turn failed: {result.reason or 'unknown'}")
        return result.text

    def _count(self, result: AgentTurnResult) -> None:
        usage = result.usage or {}
        with self._lock:
            self.turns += 1
            self.input_tokens += int(usage.get("input_tokens") or 0)
            self.output_tokens += int(usage.get("output_tokens") or 0)
            self.llm_calls += int(usage.get("llm_calls") or 0)


def _run_coroutine(coro: Any) -> Any:
    """Run ``coro`` to its result from sync code, on this thread when it has
    no running loop, else on a fresh one (never nested in a running loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="native-turn") as pool:
        return pool.submit(asyncio.run, coro).result()


class NativePlan(CompiledPlan):
    """TeamSpec lowered into a TeamupSessionConfig."""

    def __init__(self, spec: TeamSpec, ctx: EngineContext) -> None:
        self.spec = spec
        self.ctx = ctx
        self.config = spec_to_session_config(spec)
        canonical = json.dumps(self.config.to_dict(), sort_keys=True, default=str)
        self._hash = hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def plan_hash(self) -> str:
        return self._hash


def spec_to_session_config(spec: TeamSpec) -> TeamupSessionConfig:
    """IR -> teamup config. Deterministic; no side effects."""
    roles = _resolve_roles(spec)
    budgets = spec.budgets
    return TeamupSessionConfig(
        name=spec.run_name,
        roles=roles,
        max_iterations=_max_iterations(spec),
        optimization_goal=spec.task.objective,
        timeout_minutes=(
            max(1, budgets.max_wallclock_sec // 60) if budgets.max_wallclock_sec else 120
        ),
        auto_approve=not spec.task.human_checkpoints,
        role_models=_role_models(spec),
        role_prompts=_role_prompts(spec),
        # 2026-10-01: TeamSpec.project IS the owning project — stamps the
        # session for work-pane scoping; "" → None stays honest.
        project_id=spec.project or None,
    )


def _role_models(spec: TeamSpec) -> dict[str, str]:
    """D-29 (suggestion 10): per-role models from the spec's model_refs.

    An agent's model_ref lands on its resolved role; agents without a
    model_ref (or with an unresolvable role name) contribute nothing —
    that role runs the broker default, never an invented model.
    """
    models: dict[str, str] = {}
    for agent in spec.agents:
        ref = (agent.model_ref or "").strip()
        if not ref:
            continue
        for token in (agent.role, agent.name):
            role = _preset_role(token)
            if role and role.value not in models:
                models[role.value] = ref
                break
    return models


def _role_prompts(spec: TeamSpec) -> dict[str, str]:
    """D-29 / ADR-072: each agent's instructions become its role's system
    prompt (first agent per role wins); no instructions → the built-in one."""
    prompts: dict[str, str] = {}
    for agent in spec.agents:
        text = (agent.instructions or "").strip()
        role = _agent_role(agent)
        if text and role and role.value not in prompts:
            prompts[role.value] = text
    return prompts


def _agent_role(agent: Any) -> AgentRole | None:
    for token in (agent.role, agent.name):
        role = _preset_role(token)
        if role:
            return role
    return None


def _resolve_roles(spec: TeamSpec) -> list[AgentRole]:
    roles: list[AgentRole] = []
    seen: set[AgentRole] = set()
    for agent in spec.agents:
        for token in (agent.name, agent.role):
            role = _preset_role(token)
            if role and role not in seen:
                roles.append(role)
                seen.add(role)
                break
    if roles:
        if AgentRole.PM not in seen:
            roles.insert(0, AgentRole.PM)
        return roles
    return [AgentRole.PM, AgentRole.SE, AgentRole.PG]


def _max_iterations(spec: TeamSpec) -> int:
    for agent in spec.agents:
        if agent.max_iterations:
            return agent.max_iterations
    return 10 if spec.topology.style is WorkflowStyle.SEQUENTIAL else 50


class NativeEngine(TeamEngine):
    NAME = "native"
    VERSION = "0.1.0"

    def descriptor(self) -> EngineDescriptor:
        return EngineDescriptor(
            name=self.NAME,
            version=self.VERSION,
            spi_version=SPI_VERSION,
            trust_tier=TrustTier.CORE,
            capabilities=EngineCapabilities(
                supports_human_in_loop=True,
                supports_checkpointing=True,
                supports_streaming=True,
                checkpoint_scope=CheckpointScope.ENGINE_INTERNAL,
                workflow_styles=(WorkflowStyle.FILE_BASED, WorkflowStyle.SEQUENTIAL),
            ),
        )

    def validate(self, spec: TeamSpec) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not spec.agents:
            issues.append(ValidationIssue("agents", "at least one agent required"))
        if spec.topology.style not in self.capabilities().workflow_styles:
            supported = sorted(s.value for s in self.capabilities().workflow_styles)
            issues.append(
                ValidationIssue(
                    "topology.style",
                    f"native engine supports {supported}, got {spec.topology.style.value}",
                )
            )
        if spec.resources.gpu_workers or spec.resources.distributed_ok:
            issues.append(
                ValidationIssue(
                    "resources", "native engine is single-process; no distributed workers"
                )
            )
        return issues

    def compile(self, spec: TeamSpec, ctx: EngineContext) -> NativePlan:
        errors = [i for i in self.validate(spec) if i.severity == "error"]
        if errors:
            raise SpecNotExpressible("; ".join(i.message for i in errors))
        return NativePlan(spec, ctx)

    async def start(self, plan: CompiledPlan, services: RuntimeServices) -> EngineRun:
        assert isinstance(plan, NativePlan)
        return NativeRun(plan, services)

    def open_session(
        self, config: TeamupSessionConfig, client: Any, *, running: bool = False
    ) -> TeamupSession:
        """An interactive roster team on this engine's runtime, persisted at once.

        Reached only through ``TeamService.open_team_session`` (ADR-072: one
        team seam; ADR-092 slice 3). The surface that asked runs it one
        iteration at a time, so the team is a session, not an EngineRun:
        the chat drives it (ADR-075 D-2 keeps one driver of record).
        ``running`` marks it started, as ``TeamupSession.create`` did.
        """
        session = TeamupSession(config, client)
        session._ensure_session_dir()
        if running:
            from ..teamup.session import SessionStatus

            session.status = SessionStatus.RUNNING
        session._save_snapshot()
        return session


def list_team_sessions() -> list[dict[str, Any]]:
    """The interactive roster teams in this engine's session store, newest first."""
    return TeamupSession.list_sessions()


class NativeRun(EngineRun):
    """Drives a TeamupSession; teamup callbacks become canonical events."""

    def __init__(self, plan: NativePlan, services: RuntimeServices) -> None:
        self._plan = plan
        self._svc = services
        self._status = "pending"
        self._session: TeamupSession | None = None
        self._task: asyncio.Task | None = None
        self._result: TeamResult | None = None
        self._iterations = 0
        self._checkpoints = 0
        self._seen_agents: set[str] = set()
        self._turns: _BrokerTurns | None = None

    @property
    def run_id(self) -> str:
        return self._svc.run.run_id

    def _new_session(self) -> TeamupSession:
        session = TeamupSession(
            self._plan.config,
            _NoDirectModelCalls(),  # type: ignore[arg-type]
            session_id=self.run_id,
            session_dir=services_run_dir(self._svc),
        )
        # ADR-092 slice 9: every role turn through the run's AgentBroker.
        self._turns = _BrokerTurns(self._svc.agents, self.run_id)
        session._agent_turn = self._turns
        session._on_agent_message = self._on_agent_message
        session._on_iteration_complete = self._on_iteration_complete
        return session

    def _on_agent_message(self, role: AgentRole, message: str) -> None:
        if role.value not in self._seen_agents:
            self._seen_agents.add(role.value)
            self._svc.events.emit(EventKind.AGENT_STARTED.value, ("agent", role.value), {})
        self._svc.events.emit(
            EventKind.AGENT_MESSAGE.value,
            ("agent", role.value),
            {"delta": message[:4000]},
        )

    def _on_iteration_complete(self, iteration: int) -> None:
        self._iterations = iteration
        self._svc.events.emit(
            EventKind.AGENT_COMPLETED.value,
            ("engine", NativeEngine.NAME),
            {"iteration": iteration},
        )

    async def _execute(self) -> None:
        events = self._svc.events
        events.emit(
            EventKind.RUN_STARTED.value,
            ("engine", NativeEngine.NAME),
            {"plan_hash": self._plan.plan_hash},
        )
        self._status = "running"
        try:
            await self._drive_session()
        except BudgetExhausted as exc:
            self._fail("budget", str(exc))
        except asyncio.CancelledError:
            self._session_cancel()
        except Exception as exc:  # noqa: BLE001 — engine boundary must not leak crashes
            self._fail("engine_error", str(exc))

    async def _drive_session(self) -> None:
        if self._svc.agents is None:
            self._fail(
                "agent_broker_required",
                "the native engine runs every role turn through the host's AgentBroker "
                "(ADR-092 slice 9); this host provides none",
            )
            return
        session = self._new_session()
        self._session = session
        await session.start()
        while session.status.value == "running" and self._status == "running":
            progressed = await session.run_iteration()
            self._check_budget()
            if not progressed:
                break
        if self._status == "running":
            self._complete()

    def _check_budget(self) -> None:
        """The broker charged every round to the run's BudgetMeter (D-7); a
        spent meter ends the run between iterations."""
        left = (self._svc.budget.remaining() or {}).get("max_tokens")
        if left is not None and left <= 0:
            raise BudgetExhausted("the run's token budget is spent")

    def _complete(self) -> None:
        failures = _role_failures(self._session)
        if failures and not _any_turn_completed(self._session):
            # Live 2026-10-09: every role failed and the run reported success.
            self._fail("roles_failed", f"no role completed a turn: {_describe(failures)}")
            return
        self._status = "done"
        summary = self._session.get_summary() if self._session else ""
        self._result = TeamResult(
            status="partial" if failures else "success",
            summary=summary,
            error=f"roles_failed: {_describe(failures)}" if failures else None,
            metrics=self._mk_metrics(),
            reproducibility={
                "engine_version": NativeEngine.VERSION,
                "spec_hash": self._plan.plan_hash,
                "seed": self._svc.run.seed,
            },
        )
        self._svc.events.emit(EventKind.RUN_COMPLETED.value, ("engine", NativeEngine.NAME), {})

    def _session_cancel(self) -> None:
        self._status = "cancelled"
        if self._session:
            self._session.cancel()
        self._result = TeamResult(status="cancelled", summary="cancelled by caller")
        self._svc.events.emit(EventKind.RUN_CANCELLED.value, ("engine", NativeEngine.NAME), {})

    def _fail(self, code: str, message: str) -> None:
        self._status = "failed"
        self._result = TeamResult(status="failed", summary=message, error=f"{code}: {message}")
        self._svc.events.emit(
            EventKind.RUN_FAILED.value, ("engine", NativeEngine.NAME), {"error": message}
        )

    def _mk_metrics(self) -> RunMetrics:
        # The AgentBroker's per-turn usage is authoritative (slice 9).
        turns = self._turns or _BrokerTurns(None, self.run_id)
        return RunMetrics(
            wallclock_sec=0.0,
            total_tokens=turns.input_tokens + turns.output_tokens,
            input_tokens=turns.input_tokens,
            output_tokens=turns.output_tokens,
            tool_calls=0,
            llm_calls=turns.llm_calls,
            cost_usd=None,
            agent_steps=turns.turns,
            checkpoint_count=self._checkpoints,
            human_interventions=0,
        )

    async def events(self) -> AsyncIterator[TeamEvent]:  # type: ignore[override]
        sink = self._svc.events
        if not isinstance(sink, FileEventSink):
            raise TypeError("NativeRun requires FileEventSink")
        queue = sink.subscribe()
        if self._task is None:
            self._task = asyncio.create_task(self._execute())
        while True:
            event = await queue.get()
            yield event
            if event.kind in TERMINAL_EVENT_KINDS:
                return

    async def checkpoint(self) -> CheckpointRef:
        if self._session is None:
            raise RuntimeError("run not started")
        self._session._save_snapshot()
        self._checkpoints += 1
        offset = len(getattr(self._svc.events, "events", []))
        ref = CheckpointRef(
            run_id=self.run_id,
            attempt_id=self._svc.run.attempt_id,
            engine_name=NativeEngine.NAME,
            engine_version=NativeEngine.VERSION,
            state_blob_id=f"teamup-snapshot-{self.run_id}",
            event_offset=offset,
            created_at=utcnow(),
        )
        self._svc.events.emit(EventKind.CHECKPOINT_CREATED.value, ("engine", NativeEngine.NAME), {})
        return ref

    async def resume(self, ref: CheckpointRef) -> None:
        if ref.engine_name != NativeEngine.NAME or ref.engine_version != NativeEngine.VERSION:
            raise IncompatibleCheckpoint(ref.engine_name)
        self._status = "running"

    async def cancel(self, reason: str) -> None:
        if self._status in ("done", "failed", "cancelled"):
            return
        self._session_cancel()

    def state(self) -> str:
        return self._status

    def graph(self) -> ExecutionGraph:
        roles = self._plan.config.roles
        return ExecutionGraph(
            nodes=tuple(
                NodeState(
                    node_id=f"role-{r.value}",
                    agent=r.value,
                    status="done" if self._status == "done" else "pending",
                )
                for r in roles
            ),
            edges=tuple(
                (f"role-{roles[i].value}", f"role-{roles[i + 1].value}")
                for i in range(len(roles) - 1)
            ),
        )

    async def result(self) -> TeamResult:
        if self._result is None:
            return TeamResult(status=self._status, summary="run not finished")
        return self._result

    async def metrics(self) -> RunMetrics:
        return (await self.result()).metrics or self._mk_metrics()


def services_run_dir(services: RuntimeServices) -> Any:
    from pathlib import Path

    working = getattr(services.run, "working_dir", None)
    return Path(working) if working else None


def _role_failures(session: Any) -> dict[str, str]:
    """Each role that failed a turn, with its last error."""
    states = getattr(session, "agent_states", None) or {}
    return {name: state.errors[-1] for name, state in states.items() if state.errors}


def _any_turn_completed(session: Any) -> bool:
    states = getattr(session, "agent_states", None) or {}
    return any(state.messages_sent for state in states.values())


def _describe(failures: dict[str, str]) -> str:
    return "; ".join(f"{role}: {error}" for role, error in failures.items())
