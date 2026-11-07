"""Team Engine SPI contracts (ADR-020).

Engine-neutral wire types: TeamSpec IR, canonical events, broker service
DTOs, results/metrics, capability descriptors. Stdlib-only (ADR-005).
"""

from __future__ import annotations

from .capabilities import (
    CheckpointScope,
    Dependency,
    EngineCapabilities,
    EngineDescriptor,
    TrustTier,
)
from .events import (
    TEAM_EVENT_VERSION,
    TERMINAL_EVENT_KINDS,
    ActorRef,
    EventKind,
    TeamEvent,
)
from .results import (
    ArtifactRef,
    CheckpointRef,
    ExecutionGraph,
    NodeState,
    RunMetrics,
    TeamResult,
)
from .services import (
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
from .spec import (
    TEAM_SPEC_VERSION,
    AgentSpec,
    BudgetSpec,
    EdgeSpec,
    ResourceHints,
    TaskSpec,
    TeamSpec,
    Topology,
    WorkflowStyle,
)

__all__ = [
    "AgentTurnRequest",
    "AgentTurnResult",
    "GateDecision",
    "GateRequest",
    "TEAM_EVENT_VERSION",
    "TEAM_SPEC_VERSION",
    "TERMINAL_EVENT_KINDS",
    "ActorRef",
    "AgentSpec",
    "ArtifactRef",
    "BudgetSpec",
    "CheckpointRef",
    "CheckpointScope",
    "Dependency",
    "EdgeSpec",
    "EngineCapabilities",
    "EngineDescriptor",
    "EventKind",
    "ExecutionGraph",
    "ModelChunk",
    "ModelRequest",
    "NodeState",
    "ResourceHints",
    "RunMetrics",
    "TaskSpec",
    "TeamEvent",
    "TeamResult",
    "TeamSpec",
    "ToolCallRequest",
    "ToolCallResult",
    "ToolDescriptor",
    "Topology",
    "TrustTier",
    "WorkflowStyle",
]
