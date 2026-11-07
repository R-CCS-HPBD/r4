"""Run results, metrics, checkpoints, and graph projections (ADR-020)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str  # UMRS artifact id
    name: str
    media_type: str
    producer: str  # agent name
    content_hash: str  # sha256 — reproducibility anchor


@dataclass(frozen=True)
class RunMetrics:
    """Cross-engine comparable. Authoritative source is the runtime's
    broker metering, not engine self-report."""

    wallclock_sec: float
    total_tokens: int
    input_tokens: int
    output_tokens: int
    tool_calls: int
    llm_calls: int
    cost_usd: float | None
    agent_steps: int
    checkpoint_count: int
    human_interventions: int


@dataclass(frozen=True)
class TeamResult:
    status: str  # "success" | "partial" | "failed" | "cancelled"
    summary: str
    artifacts: tuple[ArtifactRef, ...] = ()
    metrics: RunMetrics | None = None
    error: str | None = None
    reproducibility: dict[str, Any] = field(default_factory=dict)
    # reproducibility keys: seed, engine_version, model_versions,
    # spec_hash, event_log_hash


@dataclass(frozen=True)
class CheckpointRef:
    """Opaque same-engine checkpoint. Cross-engine resume uses the event
    log (transcript replay), never this blob."""

    run_id: str
    attempt_id: str
    engine_name: str  # resumable only by same engine + version
    engine_version: str
    state_blob_id: str  # opaque blob id in the artifact store
    event_offset: int  # stream position at checkpoint time
    created_at: str  # ISO-8601 UTC


@dataclass(frozen=True)
class NodeState:
    node_id: str
    agent: str | None
    status: str  # pending | running | done | failed | skipped
    detail: str | None = None


@dataclass(frozen=True)
class ExecutionGraph:
    """Best-effort projection of the engine's internal execution graph.
    Engines without introspectable graphs emit a linear chain."""

    nodes: tuple[NodeState, ...]
    edges: tuple[tuple[str, str], ...] = ()
