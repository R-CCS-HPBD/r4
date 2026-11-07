"""r4-teamengine-spi — pluggable multi-agent orchestration SPI (ADR-020).

Engines import this package plus `r4-contracts` — never r4/umrs/gateway
internals. R4's runtime (`r4.teams`) implements the brokers; adapter
packages implement TeamEngine.
"""

from __future__ import annotations

__version__ = "0.2.0rc4"

from .context import (
    AgentBroker,
    ArtifactBroker,
    BudgetMeter,
    EngineContext,
    EventSink,
    MemoryBroker,
    ModelBroker,
    RecordBroker,
    RunContext,
    RuntimeServices,
    ToolBroker,
    WorkerBroker,
)
from .engine import CompiledPlan, EngineRun, TeamEngine
from .errors import (
    BudgetExhausted,
    EngineCrashed,
    IncompatibleCheckpoint,
    IncompatibleSpiVersion,
    PluginTrustError,
    SpecNotExpressible,
    TeamEngineError,
    UnknownEngineError,
    ValidationIssue,
)
from .loader import (
    ENTRY_POINT_GROUP,
    apply_site_policy,
    check_spi_compat,
    discover_descriptors,
    discover_entry_points,
    load,
)
from .version import SPI_VERSION, is_compatible

__all__ = [
    "AgentBroker",
    "ENTRY_POINT_GROUP",
    "SPI_VERSION",
    "ArtifactBroker",
    "BudgetExhausted",
    "BudgetMeter",
    "CompiledPlan",
    "EngineContext",
    "EngineCrashed",
    "EngineRun",
    "EventSink",
    "IncompatibleCheckpoint",
    "IncompatibleSpiVersion",
    "MemoryBroker",
    "ModelBroker",
    "PluginTrustError",
    "RecordBroker",
    "RunContext",
    "RuntimeServices",
    "SpecNotExpressible",
    "TeamEngine",
    "TeamEngineError",
    "ToolBroker",
    "UnknownEngineError",
    "ValidationIssue",
    "WorkerBroker",
    "apply_site_policy",
    "check_spi_compat",
    "discover_descriptors",
    "discover_entry_points",
    "is_compatible",
    "load",
]
