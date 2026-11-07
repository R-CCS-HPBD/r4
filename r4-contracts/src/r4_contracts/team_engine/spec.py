"""TeamSpec — engine-neutral intermediate representation for team runs.

Defined by ADR-020. Adapters compile this IR into their native execution
form (LLVM-style). Nothing in this module may reference any engine SDK or
any runtime package.

Version 2 (ADR-092): ``WorkflowStyle.PLAN`` and ``TeamSpec.plan``, a bound
and expanded team plan (``team_engine.plan``). Version-1 roster specs are
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

TEAM_SPEC_VERSION = 2


class WorkflowStyle(str, Enum):
    """Execution topology families engines may advertise."""

    SEQUENTIAL = "sequential"
    DAG = "dag"
    GRAPH = "graph"  # cyclic, conditional routing
    HIERARCHICAL = "hierarchical"  # manager/worker delegation
    DEBATE = "debate"
    SWARM = "swarm"
    FILE_BASED = "file_based"  # shared-workspace (VibeCodeHPC-style)
    PLAN = "plan"  # a declarative team plan (ADR-092)


@dataclass(frozen=True)
class AgentSpec:
    """Engine-neutral agent: role + instructions + allowed tools + model.

    An agent is declarative only; the adapter's compile() maps it to a
    native node/step definition.
    """

    name: str
    role: str
    instructions: str
    tool_refs: tuple[str, ...] = ()  # "mcp://<server>/<tool>" ids
    model_ref: str | None = None  # registry name; None = broker default
    max_iterations: int | None = None
    engine_hint: str | None = None  # sub-delegation preference (policy-checked)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EdgeSpec:
    """Directed edge between agents. "$start"/"$end" are reserved names."""

    source: str
    target: str
    condition: str | None = None  # simple expression language v1
    carry: tuple[str, ...] = ()  # state/artifact keys to pass


@dataclass(frozen=True)
class Topology:
    style: WorkflowStyle
    entry_agent: str | None = None
    max_parallel: int = 1


@dataclass(frozen=True)
class BudgetSpec:
    """Ceilings enforced by the runtime's BudgetMeter, not by engines."""

    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_wallclock_sec: int | None = None
    max_tool_calls: int | None = None


@dataclass(frozen=True)
class ResourceHints:
    """HPC-aware hints. Engines MAY ignore; r4 validates feasibility."""

    gpu_workers: int = 0
    cpu_workers: int = 0
    slurm_partition: str | None = None  # validated against site config
    distributed_ok: bool = False


@dataclass(frozen=True)
class TaskSpec:
    objective: str
    inputs: tuple[str, ...] = ()  # artifact ids / memory queries
    expected_outputs: tuple[str, ...] = ()  # named artifacts to produce
    human_checkpoints: tuple[str, ...] = ()  # points requiring approval


@dataclass(frozen=True)
class TeamSpec:
    """The engine-neutral IR. Versioned via TEAM_SPEC_VERSION."""

    spec_version: int
    run_name: str
    project: str
    task: TaskSpec
    agents: tuple[AgentSpec, ...]
    topology: Topology
    edges: tuple[EdgeSpec, ...] = ()
    budgets: BudgetSpec = BudgetSpec()
    policy_ref: str = "default"
    resources: ResourceHints = ResourceHints()
    context: dict[str, Any] = field(default_factory=dict)
    # ADR-092: the bound, expanded plan program when style is PLAN.
    plan: dict[str, Any] | None = None
