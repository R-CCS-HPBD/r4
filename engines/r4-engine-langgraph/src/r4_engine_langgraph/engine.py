"""LangGraph adapter: descriptor, validation, IR->StateGraph compile.

Adapter rules (ADR-020 §10): compile() is deterministic and side-effect-
free; all outward effects go through RuntimeServices brokers; native
events are translated into canonical TeamEvents, never proxied.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from r4_contracts.team_engine.capabilities import (
    CheckpointScope,
    EngineCapabilities,
    EngineDescriptor,
    TrustTier,
)
from r4_contracts.team_engine.spec import TeamSpec, WorkflowStyle
from r4_teamengine_spi.context import EngineContext, RuntimeServices
from r4_teamengine_spi.engine import CompiledPlan, EngineRun, TeamEngine
from r4_teamengine_spi.errors import SpecNotExpressible, ValidationIssue
from r4_teamengine_spi.version import SPI_VERSION

if TYPE_CHECKING:
    from .run import LGRun

SUPPORTED_STYLES = (WorkflowStyle.DAG, WorkflowStyle.SEQUENTIAL)


class LangGraphPlan(CompiledPlan):
    def __init__(self, spec: TeamSpec, ctx: EngineContext) -> None:
        self.spec = spec
        self.ctx = ctx
        canonical = json.dumps(
            {
                "v": spec.spec_version,
                "name": spec.run_name,
                "agents": [a.name for a in spec.agents],
                "edges": [(e.source, e.target) for e in spec.edges],
                "style": spec.topology.style.value,
                "env": ctx.env_fingerprint,
            },
            sort_keys=True,
        )
        self._hash = hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def plan_hash(self) -> str:
        return self._hash


class LangGraphEngine(TeamEngine):
    NAME = "langgraph"
    VERSION = "0.1.0"

    def descriptor(self) -> EngineDescriptor:
        return EngineDescriptor(
            name=self.NAME,
            version=self.VERSION,
            spi_version=SPI_VERSION,
            trust_tier=TrustTier.VERIFIED,
            capabilities=EngineCapabilities(
                supports_graph_execution=True,
                supports_checkpointing=True,
                supports_streaming=True,
                supports_multi_model=True,
                checkpoint_scope=CheckpointScope.ENGINE_INTERNAL,
                workflow_styles=SUPPORTED_STYLES,
            ),
        )

    def validate(self, spec: TeamSpec) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not spec.agents:
            issues.append(ValidationIssue("agents", "at least one agent required"))
        if spec.topology.style not in SUPPORTED_STYLES:
            issues.append(
                ValidationIssue(
                    "topology.style",
                    f"supported: {[s.value for s in SUPPORTED_STYLES]}, "
                    f"got {spec.topology.style.value}",
                )
            )
        issues.extend(_edge_issues(spec))
        return issues

    def compile(self, spec: TeamSpec, ctx: EngineContext) -> LangGraphPlan:
        errors = [i for i in self.validate(spec) if i.severity == "error"]
        if errors:
            raise SpecNotExpressible("; ".join(i.message for i in errors))
        return LangGraphPlan(spec, ctx)

    async def start(self, plan: CompiledPlan, services: RuntimeServices) -> EngineRun:
        assert isinstance(plan, LangGraphPlan)
        from .run import LGRun

        run: LGRun = LGRun(plan, services)
        run.build_graph()
        return run


def _edge_issues(spec: TeamSpec) -> list[ValidationIssue]:
    """Edge validation: no conditions, no unknown endpoints, no cycles."""
    issues: list[ValidationIssue] = []
    names = {a.name for a in spec.agents}
    for edge in spec.edges:
        if edge.condition is not None:
            issues.append(
                ValidationIssue(
                    "edges",
                    f"conditional edge {edge.source}->{edge.target} unsupported in v1",
                )
            )
        for endpoint in (edge.source, edge.target):
            if endpoint not in names and endpoint not in ("$start", "$end"):
                issues.append(ValidationIssue("edges", f"unknown endpoint {endpoint!r}"))
    if _has_cycle(spec):
        issues.append(ValidationIssue("edges", "cycle detected; cycles need GRAPH style (v2)"))
    return issues


def _has_cycle(spec: TeamSpec) -> bool:
    """DFS three-color cycle detection over explicit edges."""
    adjacency = _adjacency(spec)
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        state[node] = 1
        for nxt in adjacency.get(node, ()):
            if state.get(nxt) == 1:
                return True
            if state.get(nxt) is None and visit(nxt):
                return True
        state[node] = 2
        return False

    return any(state.get(a.name) is None and visit(a.name) for a in spec.agents)


def _adjacency(spec: TeamSpec) -> dict[str, tuple[str, ...]]:
    """Effective edges: explicit spec edges, else sequential chain."""
    if spec.edges:
        adjacency: dict[str, list[str]] = {}
        for edge in spec.edges:
            source = spec.agents[0].name if edge.source == "$start" else edge.source
            if edge.target == "$end":
                continue
            adjacency.setdefault(source, []).append(edge.target)
        return {k: tuple(v) for k, v in adjacency.items()}
    names = [a.name for a in spec.agents]
    return {names[i]: (names[i + 1],) for i in range(len(names) - 1)}
