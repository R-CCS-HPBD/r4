"""RuntimeServices and broker interfaces (ADR-020 reverse control).

Engines receive exactly one RuntimeServices object per run. Every outward
effect (LLM call, tool call, memory, artifact, event, worker) flows back
through these brokers into R4, where policy, budgets, and audit are
enforced. Engines never hold credentials or open connections.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from r4_contracts.team_engine.events import TeamEvent
from r4_contracts.team_engine.results import ArtifactRef
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


class ToolBroker(ABC):
    """Governed MCP access. Policy-checked, budgeted, audited per call."""

    @abstractmethod
    def list_tools(self) -> list[ToolDescriptor]: ...

    @abstractmethod
    async def execute(self, request: ToolCallRequest) -> ToolCallResult:
        """Denial returns ToolCallResult(ok=False, error="POLICY_DENIED: ...")."""
        ...


class ModelBroker(ABC):
    """Streaming-first model access through the platform route."""

    @abstractmethod
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]: ...

    @abstractmethod
    def available_models(self) -> list[str]: ...


class MemoryBroker(ABC):
    @abstractmethod
    async def recall(
        self, query: str, *, scope: str = "project", limit: int = 8
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def store(self, kind: str, content: dict[str, Any]) -> str: ...


class ArtifactBroker(ABC):
    @abstractmethod
    async def put(
        self, name: str, data: bytes, media_type: str, *, producer: str
    ) -> ArtifactRef: ...

    @abstractmethod
    async def get(self, artifact_id: str) -> bytes: ...

    @abstractmethod
    async def publish(self, ref: ArtifactRef) -> ArtifactRef:
        """Promote attempt-local artifact to run-visible (commit pointer)."""
        ...


class EventSink(ABC):
    """Engines push event drafts; the sink assigns seq, persists, fans out.

    ``emit`` returns the fully-sequenced TeamEvent for the caller's own
    bookkeeping. The sink is the single writer of sequence numbers.
    """

    @abstractmethod
    def emit(self, kind: str, actor: tuple[str, str], payload: dict) -> TeamEvent: ...


class BudgetMeter(ABC):
    @abstractmethod
    def spend_tokens(self, n: int) -> None:
        """Raises BudgetExhausted when the ceiling is exceeded."""
        ...

    @abstractmethod
    def remaining(self) -> dict[str, Any]: ...


class AgentBroker(ABC):
    """One task-scoped agent turn per request (ADR-092 D-3).

    The runtime runs the turn as a governed companion turn: its own forked
    permission manager, the tool allowlist narrowed by a fixed deny set,
    the ADR-062 route gate, and token metering per model round into the
    run's BudgetMeter. Engines see only the request and the result.
    """

    @abstractmethod
    async def run_turn(self, request: AgentTurnRequest) -> AgentTurnResult: ...

    def cancel(self, reason: str) -> None:  # noqa: B027 — optional hook, default no-op
        """Stop turns in flight (best effort)."""

    async def ask_gate(self, request: GateRequest) -> GateDecision:  # noqa: ARG002 — default refuses
        """Ask a human to pass a plan ``gate`` (ADR-092 slice 2, ADR-066).

        Optional, so existing brokers are unchanged and SPI_VERSION stays
        0.1. The default has no human to ask, so it refuses: a gate is never
        auto-approved, and a broker that cannot reach a human fails closed.
        """
        return GateDecision(approved=False, answered=False, reason="no_human")


class RecordBroker(ABC):
    """The durable home of a run's records (ADR-092 slice 7, MD-4).

    Optional, as ``agents`` is, so roster engines are unchanged and
    SPI_VERSION stays 0.1. A plan engine journals every agent call locally
    (an attempt-local cache, D-8, MD-3) and, when the run has a durable
    home, through this broker too; a resume elsewhere reads the records
    back. The broker only moves records: a failed call raises, and the
    engine decides (name it and go local-only, or stop the run when the run
    requires its durable record). Engines never see where the home is.
    """

    #: The run requires its durable record: a failure stops it (fail closed).
    required: bool = False

    @abstractmethod
    def load(self) -> list[dict[str, Any]]:
        """This run's records, in write order. Raises when unreadable."""
        ...

    @abstractmethod
    def append(self, record: dict[str, Any]) -> None:
        """Write one record durably before returning. Raises on failure."""
        ...

    def close(self, status: str, summary: dict[str, Any]) -> None:  # noqa: B027 — optional hook
        """The attempt ended with ``status`` (best effort; may raise)."""

    def describe(self) -> dict[str, Any]:
        """Where the records live, for the run's result (never a credential)."""
        return {}


class WorkerBroker(ABC):
    """The ONLY path to distributed workers. Backed by UMRS durable jobs;
    engines never see Slurm or SSH material."""

    @abstractmethod
    async def request_worker(self, profile: dict) -> str:
        """Returns an opaque worker handle."""
        ...

    @abstractmethod
    async def release_worker(self, handle: str) -> None: ...


@dataclass(frozen=True)
class RunContext:
    run_id: str
    attempt_id: str
    project: str
    caller: str  # authenticated user id
    working_dir: str  # attempt-local scratch
    seed: int


@dataclass(frozen=True)
class EngineContext:
    """Everything compile() may consult — static, snapshot-at-submit."""

    project: str
    available_tools: tuple[ToolDescriptor, ...]
    available_models: tuple[str, ...]
    mcp_servers: tuple[str, ...]
    policy_name: str
    user: str
    env_fingerprint: str  # site + dependency digest


@dataclass
class RuntimeServices:
    """The bootstrap object handed to EngineRun. Governance lives behind
    every broker implementation — engines are untrusted orchestration."""

    tools: ToolBroker
    models: ModelBroker
    memory: MemoryBroker
    artifacts: ArtifactBroker
    events: EventSink
    budget: BudgetMeter
    workers: WorkerBroker
    run: RunContext
    # ADR-092: optional, so roster engines are unchanged and SPI_VERSION stays 0.1.
    agents: AgentBroker | None = None
    # ADR-092 slice 7: the run's durable record home (UMRS), when it has one.
    records: RecordBroker | None = None
