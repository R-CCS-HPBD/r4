"""TeamEngine / CompiledPlan / EngineRun interfaces (ADR-020).

One adapter class per engine. Engine instances are stateless factories of
runs — all mutable state lives on EngineRun, never on the engine object.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from r4_contracts.team_engine.capabilities import EngineCapabilities, EngineDescriptor
from r4_contracts.team_engine.events import TeamEvent
from r4_contracts.team_engine.results import (
    CheckpointRef,
    ExecutionGraph,
    RunMetrics,
    TeamResult,
)
from r4_contracts.team_engine.spec import TeamSpec

from .context import EngineContext, RuntimeServices
from .errors import ValidationIssue


class TeamEngine(ABC):
    """Pluggable multi-agent orchestration backend."""

    @abstractmethod
    def descriptor(self) -> EngineDescriptor: ...

    def capabilities(self) -> EngineCapabilities:
        """Convenience accessor over the descriptor."""
        return self.descriptor().capabilities

    @abstractmethod
    def validate(self, spec: TeamSpec) -> list[ValidationIssue]:
        """Static check: can this engine express this spec? Fail fast."""
        ...

    @abstractmethod
    def compile(self, spec: TeamSpec, ctx: EngineContext) -> CompiledPlan:
        """IR -> native graph. THE adapter core. Deterministic given
        (spec, ctx); no side effects, no network."""
        ...

    @abstractmethod
    async def start(self, plan: CompiledPlan, services: RuntimeServices) -> EngineRun:
        """Allocate the run. Work begins on first events() iteration so
        submission stays cheap and cancellable-before-launch."""
        ...


class CompiledPlan(ABC):
    """Opaque native compiled artifact. Runtime never inspects it."""

    @property
    @abstractmethod
    def plan_hash(self) -> str:
        """Content hash — reproducibility anchor and cache key."""
        ...


class EngineRun(ABC):
    """One execution attempt of a durable team run (ADR-002 pattern)."""

    @property
    @abstractmethod
    def run_id(self) -> str: ...

    @abstractmethod
    def events(self) -> AsyncIterator[TeamEvent]:
        """Canonical event stream. MUST yield until a terminal event
        (run_completed | run_failed | run_cancelled). Re-iterable after
        resume: replays from the last acked offset."""
        ...

    @abstractmethod
    async def checkpoint(self) -> CheckpointRef: ...

    @abstractmethod
    async def resume(self, ref: CheckpointRef) -> None:
        """Restore from same-engine checkpoint. Raises
        IncompatibleCheckpoint on engine/version mismatch."""
        ...

    @abstractmethod
    async def cancel(self, reason: str) -> None:
        """Safe to call from any task; idempotent."""
        ...

    @abstractmethod
    def state(self) -> str:
        """pending|running|awaiting_human|checkpointing|done|failed|cancelled"""
        ...

    @abstractmethod
    def graph(self) -> ExecutionGraph: ...

    @abstractmethod
    async def result(self) -> TeamResult: ...

    @abstractmethod
    async def metrics(self) -> RunMetrics: ...
