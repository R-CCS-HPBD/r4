"""In-memory mock brokers + a reference stub engine (ADR-020 §11).

Thin stubs, not behavior re-models: enough surface for adapter unit tests
and the cross-engine conformance suite, nothing more.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from r4_contracts.team_engine.capabilities import (
    CheckpointScope,
    EngineCapabilities,
    EngineDescriptor,
    TrustTier,
)
from r4_contracts.team_engine.events import (
    TERMINAL_EVENT_KINDS,
    ActorRef,
    EventKind,
    TeamEvent,
)
from r4_contracts.team_engine.results import (
    ArtifactRef,
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
    ModelChunk,
    ModelRequest,
    ToolCallRequest,
    ToolCallResult,
    ToolDescriptor,
)
from r4_contracts.team_engine.spec import (
    TEAM_SPEC_VERSION,
    AgentSpec,
    BudgetSpec,
    TaskSpec,
    TeamSpec,
    Topology,
    WorkflowStyle,
)

from .context import (
    AgentBroker,
    ArtifactBroker,
    BudgetMeter,
    EngineContext,
    EventSink,
    MemoryBroker,
    ModelBroker,
    RunContext,
    RuntimeServices,
    ToolBroker,
    WorkerBroker,
)
from .engine import CompiledPlan, EngineRun, TeamEngine
from .errors import (
    BudgetExhausted,
    IncompatibleCheckpoint,
    SpecNotExpressible,
    ValidationIssue,
)
from .version import SPI_VERSION


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Mock brokers
# ---------------------------------------------------------------------------


class MemoryToolBroker(ToolBroker):
    def __init__(self, tools: list[ToolDescriptor] | None = None) -> None:
        self._tools = tools or []
        self.calls: list[ToolCallRequest] = []
        self.denied_agents: set[str] = set()

    def list_tools(self) -> list[ToolDescriptor]:
        return list(self._tools)

    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        self.calls.append(request)
        if request.agent in self.denied_agents:
            return ToolCallResult(call_id=request.call_id, ok=False, error="POLICY_DENIED: test")
        return ToolCallResult(call_id=request.call_id, ok=True, content={"echo": request.arguments})


class MemoryModelBroker(ModelBroker):
    def __init__(self, models: list[str] | None = None) -> None:
        self._models = models or ["mock-model"]
        self.requests: list[ModelRequest] = []
        # tokens attributed to the LAST streamed request, for tests that
        # need to assert broker-measured usage.
        self.tokens_out = 0

    def available_models(self) -> list[str]:
        return list(self._models)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.requests.append(request)
        text = f"stub-response[{len(self.requests)}]"
        self.tokens_out += len(text.split())
        yield ModelChunk(text=text, finish_reason="stop")


class MemoryMemoryBroker(MemoryBroker):
    def __init__(self) -> None:
        self.stored: list[tuple[str, dict[str, Any]]] = []

    async def recall(
        self,
        query: str,  # noqa: ARG002 — mock ignores the query
        *,
        scope: str = "project",  # noqa: ARG002 — mock ignores scope
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        return [c for _, c in self.stored][:limit]

    async def store(self, kind: str, content: dict[str, Any]) -> str:
        self.stored.append((kind, content))
        return f"mem-{len(self.stored)}"


class MemoryArtifactBroker(ArtifactBroker):
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}
        self.refs: dict[str, ArtifactRef] = {}

    async def put(self, name: str, data: bytes, media_type: str, *, producer: str) -> ArtifactRef:
        digest = hashlib.sha256(data).hexdigest()
        artifact_id = f"art-{len(self.refs) + 1}-{digest[:12]}"
        ref = ArtifactRef(
            artifact_id=artifact_id,
            name=name,
            media_type=media_type,
            producer=producer,
            content_hash=digest,
        )
        self._blobs[artifact_id] = data
        self.refs[artifact_id] = ref
        return ref

    async def get(self, artifact_id: str) -> bytes:
        return self._blobs[artifact_id]

    async def publish(self, ref: ArtifactRef) -> ArtifactRef:
        return ref


class MemoryEventSink(EventSink):
    """Assigns seq, persists in-memory, fans out to subscriber queues."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.events: list[TeamEvent] = []
        self._subscribers: list[asyncio.Queue[TeamEvent | None]] = []

    def subscribe(self) -> asyncio.Queue[TeamEvent | None]:
        queue: asyncio.Queue[TeamEvent | None] = asyncio.Queue()
        for ev in self.events:
            queue.put_nowait(ev)
        self._subscribers.append(queue)
        return queue

    def emit(self, kind: str, actor: tuple[str, str], payload: dict) -> TeamEvent:
        event = TeamEvent(
            seq=len(self.events),
            run_id=self.run_id,
            ts=_utcnow(),
            kind=EventKind(kind),
            actor=ActorRef(kind=actor[0], name=actor[1]),
            payload=payload,
        )
        self.events.append(event)
        for queue in self._subscribers:
            queue.put_nowait(event)
        return event


class MemoryBudgetMeter(BudgetMeter):
    def __init__(self, budgets: BudgetSpec | None = None) -> None:
        self._budgets = budgets or BudgetSpec()
        self.tokens_spent = 0

    def spend_tokens(self, n: int) -> None:
        self.tokens_spent += n
        if self._budgets.max_tokens is not None and self.tokens_spent > self._budgets.max_tokens:
            raise BudgetExhausted(
                f"token budget {self._budgets.max_tokens} exceeded ({self.tokens_spent})"
            )

    def remaining(self) -> dict[str, Any]:
        if self._budgets.max_tokens is None:
            return {"max_tokens": None}
        return {"max_tokens": max(0, self._budgets.max_tokens - self.tokens_spent)}


class MemoryWorkerBroker(WorkerBroker):
    def __init__(self) -> None:
        self.requested: list[dict] = []
        self.released: list[str] = []

    async def request_worker(self, profile: dict) -> str:
        self.requested.append(profile)
        return f"worker-{len(self.requested)}"

    async def release_worker(self, handle: str) -> None:
        self.released.append(handle)


class MemoryAgentBroker(AgentBroker):
    """Scripted agent turns for engine tests (ADR-092).

    ``script`` maps a label prefix to a value or a callable(request) → value;
    the longest matching prefix wins. A value of ``None`` (or a raised
    exception) is a failed turn. ``gates`` maps a label to an asyncio.Event
    the turn waits on, so a test can hold one agent while others proceed.
    Records ``calls`` and the most turns ever in flight (``max_in_flight``).
    ``usage`` tokens per turn are charged to ``meter`` when one is given.

    ``gate_answers`` maps a gate path prefix to the human's answer (True
    approves, False rejects); a gate it does not answer is refused for want
    of a human, as the SPI default refuses (ADR-092 slice 2). Every gate
    asked is recorded in ``gate_calls``.
    """

    def __init__(
        self,
        script: dict[str, Any] | None = None,
        *,
        gates: dict[str, asyncio.Event] | None = None,
        meter: BudgetMeter | None = None,
        usage: int = 100,
        gate_answers: dict[str, bool] | None = None,
    ) -> None:
        self.script = dict(script or {})
        self.gate_answers = dict(gate_answers or {})
        self.gate_calls: list[GateRequest] = []
        self.gates = dict(gates or {})
        self.meter = meter
        self.usage = usage
        self.calls: list[AgentTurnRequest] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.cancelled: str | None = None

    async def run_turn(self, request: AgentTurnRequest) -> AgentTurnResult:
        self.calls.append(request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            gate = self.gates.get(request.label)
            if gate is not None:
                await gate.wait()
            await asyncio.sleep(0)
            return self._result(request)
        finally:
            self.in_flight -= 1

    def _result(self, request: AgentTurnRequest) -> AgentTurnResult:
        usage = {"input_tokens": self.usage, "output_tokens": 0, "llm_calls": 1, "tool_calls": 0}
        if self.meter is not None:
            with contextlib.suppress(BudgetExhausted):
                self.meter.spend_tokens(self.usage)
        keys = [k for k in self.script if request.label.startswith(k)]
        if not keys:
            return AgentTurnResult(ok=False, reason="unscripted", usage=usage)
        entry = self.script[max(keys, key=len)]
        try:
            value = entry(request) if callable(entry) else entry
        except Exception as exc:  # noqa: BLE001 — a scripted failure
            return AgentTurnResult(ok=False, reason=f"error:{type(exc).__name__}", usage=usage)
        if value is None:
            return AgentTurnResult(ok=False, reason="scripted_null", usage=usage)
        return AgentTurnResult(
            ok=True, output=value, text=json.dumps(value), usage=usage, model=request.model or ""
        )

    def cancel(self, reason: str) -> None:
        self.cancelled = reason

    async def ask_gate(self, request: GateRequest) -> GateDecision:
        self.gate_calls.append(request)
        await asyncio.sleep(0)
        keys = [k for k in self.gate_answers if request.gate.startswith(k)]
        if not keys:
            return GateDecision(approved=False, answered=False, reason="no_human")
        approved = bool(self.gate_answers[max(keys, key=len)])
        return GateDecision(approved=approved, answered=True, decided_by="test-human")


def make_services(
    *,
    run_id: str = "run-test-1",
    attempt_id: str = "attempt-1",
    project: str = "test-project",
    caller: str = "test-user",
    seed: int = 7,
    budgets: BudgetSpec | None = None,
    tools: list[ToolDescriptor] | None = None,
    models: list[str] | None = None,
    agents: AgentBroker | None = None,
) -> RuntimeServices:
    """Wire a full set of in-memory brokers for one run."""
    return RuntimeServices(
        tools=MemoryToolBroker(tools),
        models=MemoryModelBroker(models),
        memory=MemoryMemoryBroker(),
        artifacts=MemoryArtifactBroker(),
        events=MemoryEventSink(run_id),
        budget=MemoryBudgetMeter(budgets),
        workers=MemoryWorkerBroker(),
        run=RunContext(
            run_id=run_id,
            attempt_id=attempt_id,
            project=project,
            caller=caller,
            working_dir="/tmp/r4-stub-run",
            seed=seed,
        ),
        agents=agents,
    )


def make_engine_context(*, project: str = "test-project", user: str = "test-user") -> EngineContext:
    return EngineContext(
        project=project,
        available_tools=(),
        available_models=("mock-model",),
        mcp_servers=(),
        policy_name="default",
        user=user,
        env_fingerprint="test-env",
    )


def make_minimal_spec(*, agents: int = 2) -> TeamSpec:
    return TeamSpec(
        spec_version=TEAM_SPEC_VERSION,
        run_name="minimal",
        project="test-project",
        task=TaskSpec(objective="do the test thing"),
        agents=tuple(
            AgentSpec(name=f"agent-{i}", role="worker", instructions=f"step {i}")
            for i in range(agents)
        ),
        topology=Topology(style=WorkflowStyle.SEQUENTIAL),
    )


# ---------------------------------------------------------------------------
# Reference stub engine (drives the conformance suite)
# ---------------------------------------------------------------------------


class StubPlan(CompiledPlan):
    def __init__(self, spec: TeamSpec, ctx: EngineContext) -> None:
        self.spec = spec
        self.ctx = ctx
        canonical = json.dumps(
            {
                "v": spec.spec_version,
                "name": spec.run_name,
                "agents": [a.name for a in spec.agents],
                "style": spec.topology.style.value,
                "env": ctx.env_fingerprint,
            },
            sort_keys=True,
        )
        self._hash = hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def plan_hash(self) -> str:
        return self._hash


class StubEngine(TeamEngine):
    """Trivial sequential executor. Ships the nine capability flags
    honestly: streaming + checkpointing only."""

    NAME = "stub"
    VERSION = "0.1.0"

    def descriptor(self) -> EngineDescriptor:
        return EngineDescriptor(
            name=self.NAME,
            version=self.VERSION,
            spi_version=SPI_VERSION,
            trust_tier=TrustTier.CORE,
            capabilities=EngineCapabilities(
                supports_checkpointing=True,
                supports_streaming=True,
                checkpoint_scope=CheckpointScope.ENGINE_INTERNAL,
                workflow_styles=(WorkflowStyle.SEQUENTIAL,),
            ),
        )

    def validate(self, spec: TeamSpec) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not spec.agents:
            issues.append(ValidationIssue("agents", "at least one agent required"))
        names = {a.name for a in spec.agents}
        dupes = len(names) != len(spec.agents)
        if dupes:
            issues.append(ValidationIssue("agents", "duplicate agent names"))
        if spec.topology.style is not WorkflowStyle.SEQUENTIAL:
            issues.append(
                ValidationIssue(
                    "topology.style",
                    f"stub engine only supports sequential, got {spec.topology.style}",
                )
            )
        return issues

    def compile(self, spec: TeamSpec, ctx: EngineContext) -> StubPlan:
        errors = [i for i in self.validate(spec) if i.severity == "error"]
        if errors:
            raise SpecNotExpressible("; ".join(i.message for i in errors))
        return StubPlan(spec, ctx)

    async def start(self, plan: CompiledPlan, services: RuntimeServices) -> EngineRun:
        assert isinstance(plan, StubPlan)
        return StubRun(plan, services)


class StubRun(EngineRun):
    def __init__(self, plan: StubPlan, services: RuntimeServices) -> None:
        self._plan = plan
        self._svc = services
        self._cancelled = False
        self._status = "pending"
        self._result: TeamResult | None = None
        self._llm_calls = 0
        self._task: asyncio.Task[None] | None = None

    @property
    def run_id(self) -> str:
        return self._svc.run.run_id

    async def _execute(self) -> None:
        self._svc.events.emit(
            EventKind.RUN_STARTED.value,
            ("engine", StubEngine.NAME),
            {"plan_hash": self._plan.plan_hash},
        )
        self._status = "running"
        try:
            for agent in self._plan.spec.agents:
                if self._cancelled:
                    self._status = "cancelled"
                    self._svc.events.emit(
                        EventKind.RUN_CANCELLED.value, ("engine", StubEngine.NAME), {}
                    )
                    return
                await self._run_agent(agent)
            artifacts = [await self._produce_summary_artifact()]
            self._finalize_success(artifacts)
        except BudgetExhausted as exc:
            self._status = "failed"
            self._result = TeamResult(status="failed", summary=str(exc), error=str(exc))
            self._svc.events.emit(
                EventKind.RUN_FAILED.value,
                ("engine", StubEngine.NAME),
                {"error": str(exc)},
            )

    async def _run_agent(self, agent: AgentSpec) -> None:
        events = self._svc.events
        events.emit(EventKind.AGENT_STARTED.value, ("agent", agent.name), {})
        request = ModelRequest(
            messages=(
                {"role": "system", "content": agent.instructions},
                {"role": "user", "content": self._plan.spec.task.objective},
            ),
            model=agent.model_ref,
            seed=self._svc.run.seed,
        )
        self._llm_calls += 1
        async for chunk in self._svc.models.stream(request):
            self._svc.budget.spend_tokens(len(chunk.text.split()))
            events.emit(
                EventKind.AGENT_MESSAGE.value,
                ("agent", agent.name),
                {"delta": chunk.text},
            )
        events.emit(EventKind.AGENT_COMPLETED.value, ("agent", agent.name), {})

    async def _produce_summary_artifact(self) -> ArtifactRef:
        ref = await self._svc.artifacts.put(
            "summary.md",
            b"# stub summary\n",
            "text/markdown",
            producer=StubEngine.NAME,
        )
        published = await self._svc.artifacts.publish(ref)
        self._svc.events.emit(
            EventKind.ARTIFACT_PRODUCED.value,
            ("engine", StubEngine.NAME),
            {"artifact_id": ref.artifact_id},
        )
        return published

    def _finalize_success(self, artifacts: list[ArtifactRef]) -> None:
        self._status = "done"
        broker_tokens = getattr(self._svc.models, "tokens_out", 0)
        metrics = RunMetrics(
            wallclock_sec=0.0,
            total_tokens=broker_tokens,
            input_tokens=0,
            output_tokens=broker_tokens,
            tool_calls=0,
            llm_calls=self._llm_calls,
            cost_usd=None,
            agent_steps=len(self._plan.spec.agents),
            checkpoint_count=0,
            human_interventions=0,
        )
        self._result = TeamResult(
            status="success",
            summary="stub run finished",
            artifacts=tuple(artifacts),
            metrics=metrics,
            reproducibility={
                "seed": self._svc.run.seed,
                "engine_version": StubEngine.VERSION,
                "spec_hash": self._plan.plan_hash,
            },
        )
        self._svc.events.emit(EventKind.RUN_COMPLETED.value, ("engine", StubEngine.NAME), {})

    async def events(self) -> AsyncIterator[TeamEvent]:  # type: ignore[override]
        sink = self._svc.events
        if not isinstance(sink, MemoryEventSink):
            raise TypeError("StubRun requires MemoryEventSink")
        queue = sink.subscribe()
        if self._task is None:
            self._status = "running"
            self._task = asyncio.create_task(self._execute())
        while True:
            event = await queue.get()
            if event is None:
                return
            yield event
            if event.kind in TERMINAL_EVENT_KINDS:
                return

    async def checkpoint(self) -> CheckpointRef:
        sink = self._svc.events
        offset = len(sink.events) if isinstance(sink, MemoryEventSink) else 0
        self._svc.events.emit(EventKind.CHECKPOINT_CREATED.value, ("engine", StubEngine.NAME), {})
        return CheckpointRef(
            run_id=self.run_id,
            attempt_id=self._svc.run.attempt_id,
            engine_name=StubEngine.NAME,
            engine_version=StubEngine.VERSION,
            state_blob_id=f"stub-state-{self.run_id}",
            event_offset=offset,
            created_at=_utcnow(),
        )

    async def resume(self, ref: CheckpointRef) -> None:
        if ref.engine_name != StubEngine.NAME or ref.engine_version != StubEngine.VERSION:
            raise IncompatibleCheckpoint(f"checkpoint from {ref.engine_name}@{ref.engine_version}")
        self._status = "running"

    async def cancel(self, reason: str) -> None:  # noqa: ARG002 — reason unrecorded in stub
        self._cancelled = True
        if self._status in ("pending",):
            self._status = "cancelled"

    def state(self) -> str:
        return self._status

    def graph(self) -> ExecutionGraph:
        return ExecutionGraph(
            nodes=tuple(
                NodeState(
                    node_id=f"n{i}",
                    agent=a.name,
                    status="done" if self._status == "done" else "pending",
                )
                for i, a in enumerate(self._plan.spec.agents)
            ),
            edges=tuple((f"n{i}", f"n{i + 1}") for i in range(len(self._plan.spec.agents) - 1)),
        )

    async def result(self) -> TeamResult:
        if self._result is None:
            return TeamResult(
                status=self._status,
                summary="run not finished",
                error=None if self._status == "done" else "incomplete",
            )
        return self._result

    async def metrics(self) -> RunMetrics:
        result = await self.result()
        return result.metrics or RunMetrics(
            wallclock_sec=0.0,
            total_tokens=0,
            input_tokens=0,
            output_tokens=0,
            tool_calls=0,
            llm_calls=0,
            cost_usd=None,
            agent_steps=0,
            checkpoint_count=0,
            human_interventions=0,
        )
