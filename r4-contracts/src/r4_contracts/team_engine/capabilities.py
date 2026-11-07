"""Engine self-description: capabilities, trust tiers, manifest (ADR-020)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .spec import WorkflowStyle


class TrustTier(str, Enum):
    """Install/execute trust classification for engine plugins."""

    CORE = "core"  # shipped with r4, CI-gated (native)
    VERIFIED = "verified"  # signed + reviewed, optional install
    COMMUNITY = "community"  # unsigned, in-proc allowed with warning
    UNTRUSTED = "untrusted"  # sidecar-only, sandboxed brokers


class CheckpointScope(str, Enum):
    NONE = "none"
    ENGINE_INTERNAL = "engine_internal"  # opaque blob, same-engine resume
    PORTABLE = "portable"  # event-log replay transferable


@dataclass(frozen=True)
class Dependency:
    package: str
    constraint: str  # PEP 440 specifier
    optional: bool = False


@dataclass(frozen=True)
class EngineCapabilities:
    """Queryable feature matrix (``engine.capabilities()``)."""

    supports_human_in_loop: bool = False
    supports_graph_execution: bool = False
    supports_hierarchical_agents: bool = False
    supports_distributed_execution: bool = False
    supports_checkpointing: bool = False
    supports_streaming: bool = False
    supports_parallel_agents: bool = False
    supports_multi_model: bool = False
    supports_gpu_workers: bool = False
    checkpoint_scope: CheckpointScope = CheckpointScope.NONE
    max_agents: int | None = None
    workflow_styles: tuple[WorkflowStyle, ...] = ()
    supported_models: tuple[str, ...] = ("any",)  # registry names or "any"


@dataclass(frozen=True)
class EngineDescriptor:
    """Self-declared manifest returned by ``TeamEngine.descriptor()``."""

    name: str  # "native" | "langgraph" | ...
    version: str  # adapter package version
    spi_version: str  # SPI semver this adapter was built against
    trust_tier: TrustTier
    capabilities: EngineCapabilities
    dependencies: tuple[Dependency, ...] = ()
