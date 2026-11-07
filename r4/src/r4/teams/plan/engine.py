"""The CORE ``plan`` team engine (ADR-092 D-2).

A team plan is data (D-1): phases of ``agent``/``map``/``reduce`` steps
with plan-local profiles, a budget and an output map. This engine is one
more compile target behind the ADR-020 seam. It declares only
``WorkflowStyle.PLAN``, so roster specs keep resolving to ``native`` and a
plan spec reaches this engine through the resolver's capability match.

Reverse control (ADR-020): the engine never holds a Client, a credential,
a tool or a PermissionManager. Every agent step is one governed turn asked
of ``RuntimeServices.agents`` (D-3). It imports only the standard library,
``r4_contracts`` and ``r4_teamengine_spi``, which an import-fence test
pins, so it can move to ``engines/`` as a file move.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from collections.abc import Callable
from typing import Any

from r4_contracts.team_engine.capabilities import (
    CheckpointScope,
    EngineCapabilities,
    EngineDescriptor,
    TrustTier,
)
from r4_contracts.team_engine.plan import (
    DEFAULT_PROFILE_TOOLS,
    DEFAULT_TOOL_ROUNDS,
    MAX_AGENTS_HARD,
    MAX_PLAN_BYTES,
    PlanProblem,
    canonical_hash,
    discard_warnings,
    independence_warnings,
    iter_agent_parts,
    prepare_plan,
    static_agent_bound,
    validate_plan,
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
from r4_teamengine_spi.context import EngineContext, RuntimeServices
from r4_teamengine_spi.engine import CompiledPlan, EngineRun, TeamEngine
from r4_teamengine_spi.errors import SpecNotExpressible, ValidationIssue
from r4_teamengine_spi.version import SPI_VERSION

from .run import DEFAULT_CONCURRENCY, ENGINE_NAME, ENGINE_VERSION, PlanRun

PROFILE_ROLE = "plan-profile"
# The plan budget fields spec_from_plan lowers into spec.budgets, which the
# run's BudgetMeter (and so the broker's per-round guard) enforces (D-7).
_LOWERED_BUDGET = ("max_tokens", "max_wallclock_sec", "max_tool_calls")
_UNBOUND = (
    "the plan is not bound: lower it with spec_from_plan, which binds its params "
    "and expands its patterns"
)


class PlanProgram(CompiledPlan):
    """A validated plan program, frozen at compile time.

    ``plan_hash`` is the canonical hash of the bound, expanded program: the
    same plan with the same arguments always hashes the same, which is
    what a resumed attempt's journal keys rest on (D-8).
    """

    def __init__(self, spec: TeamSpec, ctx: EngineContext) -> None:
        self.spec = spec
        self.program: dict[str, Any] = copy.deepcopy(spec.plan or {})
        self._hash = canonical_hash(self.program)
        self.agent_bound = static_agent_bound(self.program)
        self.default_model: str | None = (spec.context or {}).get("default_model") or None
        self.warnings = model_warnings(self.program, ctx, self.default_model) + [
            ValidationIssue(field="agents", message=text, severity="warning")
            for text in independence_warnings(self.program, self.default_model)
        ]
        # Slice 5: an isolated writer no merge_back names loses its changes.
        self.warnings += [
            ValidationIssue(field="isolation", message=text, severity="warning")
            for text in discard_warnings(self.program)
        ]

    @property
    def plan_hash(self) -> str:
        return self._hash


class PlanEngine(TeamEngine):
    """Runs team plans: barrier fan-outs and per-item pipelines (D-4)."""

    NAME = ENGINE_NAME
    VERSION = ENGINE_VERSION

    def descriptor(self) -> EngineDescriptor:
        # Slice 2: ``gate`` steps ask a human through the broker (ADR-066 classes).
        return EngineDescriptor(
            name=self.NAME,
            version=self.VERSION,
            spi_version=SPI_VERSION,
            trust_tier=TrustTier.CORE,
            capabilities=EngineCapabilities(
                supports_human_in_loop=True,
                supports_graph_execution=True,
                supports_checkpointing=True,
                supports_streaming=True,
                supports_parallel_agents=True,
                supports_multi_model=True,
                checkpoint_scope=CheckpointScope.ENGINE_INTERNAL,
                max_agents=MAX_AGENTS_HARD,
                workflow_styles=(WorkflowStyle.PLAN,),
            ),
        )

    def validate(self, spec: TeamSpec) -> list[ValidationIssue]:
        """Refuse anything but a v2 PLAN spec, then everything spec_from_plan refuses.

        ``r4 team launch --spec`` hands TeamService a spec that never went
        through spec_from_plan, and this is its only gate. So it holds the
        same line: the 64 KB limit on the bound program (D-4), every plan
        problem, a URL default model (ADR-068), and spec budgets that are
        the plan's own (D-7: the meter guarding every model round is built
        from ``spec.budgets``; without them a run had no per-round ceiling).
        """
        issues = _spec_issues(spec)
        if issues:
            return issues
        program = spec.plan or {}
        default_model = (spec.context or {}).get("default_model")
        capabilities = (spec.context or {}).get("tool_capabilities")
        problems = _url_model_problems(default_model) + _size_problems(program)
        problems += validate_plan(program, tool_capabilities=_capability_map(capabilities))
        issues = [ValidationIssue(field=p.path, message=str(p)) for p in problems]
        return issues + _budget_issues(spec, program)

    def compile(self, spec: TeamSpec, ctx: EngineContext) -> PlanProgram:
        errors = [i for i in self.validate(spec) if i.severity == "error"]
        if errors:
            raise SpecNotExpressible("; ".join(i.message for i in errors[:5]))
        return PlanProgram(spec, ctx)

    async def start(self, plan: CompiledPlan, services: RuntimeServices) -> EngineRun:
        if not isinstance(plan, PlanProgram):
            raise TypeError(f"the plan engine runs a PlanProgram, not {type(plan).__name__}")
        return PlanRun(plan, services)


def _budget_issues(spec: TeamSpec, program: dict[str, Any]) -> list[ValidationIssue]:
    """``spec.budgets`` must be the plan budget spec_from_plan lowered (D-7)."""
    raw = program.get("budget")
    budget: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return [
        ValidationIssue(
            f"budgets.{name}",
            f"spec budgets.{name} is {getattr(spec.budgets, name)!r}, not the plan's "
            f"budget.{name} ({budget.get(name)!r}): the run's meter would not hold the "
            "plan's ceiling; lower the plan with spec_from_plan",
        )
        for name in _LOWERED_BUDGET
        if getattr(spec.budgets, name) != budget.get(name)
    ]


def _spec_issues(spec: TeamSpec) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if spec.topology.style is not WorkflowStyle.PLAN:
        issues.append(
            ValidationIssue(
                "topology.style",
                f"the plan engine runs only plan specs, got {spec.topology.style.value}",
            )
        )
    if not isinstance(spec.plan, dict):
        issues.append(ValidationIssue("plan", "a plan spec carries its bound, expanded program"))
    elif not isinstance(spec.plan.get("_bound_params"), dict):
        # A raw plan (e.g. ``r4 team launch --spec``) would pass validate_plan
        # and then render every ``{{params.x}}`` as null (D-1: bound program).
        issues.append(ValidationIssue("plan._bound_params", _UNBOUND))
    if spec.spec_version < 2:
        issues.append(ValidationIssue("spec_version", "a plan spec is TeamSpec v2 or later"))
    return issues


# ── model warnings (compile-time, never errors) ─────────────────────────────


def model_warnings(
    program: dict[str, Any], ctx: EngineContext, default_model: str | None
) -> list[ValidationIssue]:
    """A warning per named model the host does not list.

    Not an error: the broker's ADR-062 gate decides at run time, and an
    empty listing (a failed ``list_models``) says nothing, so it warns
    about nothing.
    """
    available = set(ctx.available_models)
    if not available:
        return []
    named = _profile_models(program) + _step_models(program)
    named.append(("context.default_model", default_model))
    return [
        ValidationIssue(
            field=path,
            message=f"model {model!r} is not in this host's model list",
            severity="warning",
        )
        for path, model in named
        if isinstance(model, str) and model and model not in available
    ]


def _profile_models(program: dict[str, Any]) -> list[tuple[str, Any]]:
    profiles = program.get("agents") or {}
    return [
        (f"agents.{name}.model", p.get("model"))
        for name, p in profiles.items()
        if isinstance(p, dict)
    ]


def _step_models(program: dict[str, Any]) -> list[tuple[str, Any]]:
    """Every agent step's and stage's own model, a repeat's body and an
    inlined template's included (slice 2), named by its path."""
    return [
        (
            f"{part.name}{'' if part.stage is None else f'.stages[{part.stage}]'}.model",
            part.part.get("model"),
        )
        for part in iter_agent_parts(program)
    ]


# ── a plan → a TeamSpec v2 ──────────────────────────────────────────────────


def spec_from_plan(
    raw_plan: Any,
    args: dict[str, Any] | None,
    *,
    project: str,
    run_name: str | None = None,
    max_tokens: int | None = None,
    concurrency: int | None = None,
    default_model: str | None = None,
    tool_capabilities: dict[str, Any] | None = None,
    templates: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[TeamSpec | None, list[PlanProblem]]:
    """Bind, expand and validate ``raw_plan``; lower it to a TeamSpec v2.

    ``templates`` loads a built-in template for a nested ``plan`` step
    (slice 2); r4's callers pass ``r4.teams.plan_sources.load_template``,
    which resolves its instruction references. Without one a nested plan
    is refused by name.

    ``tool_capabilities`` maps an MCP tool to the capabilities its manifest
    declares (D-6): an MCP tool it does not declare read-only is a writer,
    refused in a map or a fan-out. The entries for the MCP tools the plan's
    profiles list ride in ``spec.context``, so the engine's own ``validate``
    and a resume judge the plan by the same declarations.

    ``max_tokens`` and ``concurrency`` override the plan's own values
    before validation, so an override is held to the same limits (D-4,
    D-7). ``default_model`` is the model of every agent whose profile names
    none; it is a catalog name, never a URL (ADR-068).

    A program this engine wrote (a run's ``plan.json``, which carries
    ``_bound_params``) is already bound and expanded, so it is only
    re-validated: re-expanding would refuse its pattern-minted ``__`` ids.
    That is how a resumed attempt rebuilds the same program, and so the
    same plan hash and journal keys (D-8).
    """
    problems = _url_model_problems(default_model)
    if problems:
        return None, problems
    capabilities = _capability_map(tool_capabilities)
    program, problems = _prepare(
        _with_overrides(raw_plan, max_tokens, concurrency), args, capabilities, templates
    )
    if problems:
        return None, problems
    spec = _lower(program, project, run_name, default_model)
    listed = _listed_capabilities(program, capabilities)
    if listed:
        spec = dataclasses.replace(spec, context={**spec.context, "tool_capabilities": listed})
    return spec, []


def _capability_map(raw: Any) -> dict[str, list[str]]:
    """``{tool: [capability, …]}`` from whatever was recorded; junk dropped."""
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): sorted({str(flag) for flag in flags if isinstance(flag, str)})
        for name, flags in raw.items()
        if isinstance(flags, (list, tuple, set, frozenset))
    }


def _listed_capabilities(
    program: dict[str, Any], capabilities: dict[str, list[str]]
) -> dict[str, list[str]]:
    """The declarations of the tools the plan's profiles list, only."""
    listed: set[str] = set()
    for profile in (program.get("agents") or {}).values():
        tools = profile.get("tools") if isinstance(profile, dict) else None
        listed.update(t for t in tools or () if isinstance(t, str))
    return {name: flags for name, flags in sorted(capabilities.items()) if name in listed}


def _url_model_problems(default_model: Any) -> list[PlanProblem]:
    if isinstance(default_model, str) and "://" in default_model:
        message = "models are named, never addressed by URL (ADR-068)"
        return [PlanProblem("default_model", "url_model", message)]
    return []


def _size_problems(program: dict[str, Any]) -> list[PlanProblem]:
    """The 64 KB limit on a bound program, measured as a run writes plan.json (D-4)."""
    size = len(json.dumps(program, ensure_ascii=False).encode("utf-8"))
    if size > MAX_PLAN_BYTES:
        return [PlanProblem("$", "too_large", f"{size} bytes; the limit is {MAX_PLAN_BYTES}")]
    return []


def _prepare(
    raw: Any,
    args: dict[str, Any] | None,
    capabilities: dict[str, list[str]],
    templates: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[PlanProblem]]:
    if isinstance(raw, dict) and isinstance(raw.get("_bound_params"), dict):
        return _reprepare(raw, args or {}, capabilities)
    return prepare_plan(raw, args, tool_capabilities=capabilities, templates=templates)


def _reprepare(
    program: dict[str, Any], args: dict[str, Any], capabilities: dict[str, list[str]]
) -> tuple[dict[str, Any], list[PlanProblem]]:
    """Re-validate a written program; it keeps the params it was bound with."""
    if args and args != program["_bound_params"]:
        message = "a written program resumes with the params it was bound with (params.json)"
        return {}, [PlanProblem("args", "rebind", message)]
    problems = _size_problems(program)
    if problems:
        return {}, problems
    return program, validate_plan(program, tool_capabilities=capabilities)


def _with_overrides(raw_plan: Any, max_tokens: int | None, concurrency: int | None) -> Any:
    """The overrides, and the ``concurrency`` default written in once.

    The run's manifest records the effective concurrency and a resume
    passes it back as an override. A program without the key would gain
    it on the first resume and hash differently from attempt 1 (D-8: the
    same plan, the same hash), so it carries its default from the start,
    before the size limit is measured.
    """
    if not isinstance(raw_plan, dict):
        return raw_plan
    raw = copy.deepcopy(raw_plan)
    if max_tokens is not None:
        budget = raw.get("budget")
        raw["budget"] = (
            {**budget, "max_tokens": max_tokens}
            if isinstance(budget, dict)
            else {"max_tokens": max_tokens}
        )
    if concurrency is not None:
        raw["concurrency"] = concurrency
    raw.setdefault("concurrency", DEFAULT_CONCURRENCY)
    return raw


def _lower(
    program: dict[str, Any], project: str, run_name: str | None, default_model: str | None
) -> TeamSpec:
    budget = program.get("budget") or {}
    return TeamSpec(
        spec_version=TEAM_SPEC_VERSION,
        run_name=run_name or program["name"],
        project=project,
        task=TaskSpec(
            objective=program["description"],
            expected_outputs=tuple(program.get("output") or {}),
        ),
        agents=tuple(
            _agent_spec(name, profile, default_model)
            for name, profile in (program.get("agents") or {}).items()
        ),
        topology=Topology(
            style=WorkflowStyle.PLAN,
            max_parallel=int(program.get("concurrency", DEFAULT_CONCURRENCY)),
        ),
        budgets=BudgetSpec(
            max_tokens=budget.get("max_tokens"),
            max_wallclock_sec=budget.get("max_wallclock_sec"),
            max_tool_calls=budget.get("max_tool_calls"),
        ),
        context={"default_model": default_model} if default_model else {},
        plan=program,
    )


def _agent_spec(name: str, profile: dict[str, Any], default_model: str | None) -> AgentSpec:
    """A profile as an engine-neutral AgentSpec: what an approval card and
    the resolver read. The plan itself stays the source of truth."""
    rounds = profile.get("max_tool_rounds", DEFAULT_TOOL_ROUNDS)
    return AgentSpec(
        name=name,
        role=PROFILE_ROLE,
        instructions=profile.get("instructions", ""),
        model_ref=profile.get("model") or default_model,
        max_iterations=rounds,
        metadata={
            "tools": list(profile.get("tools", DEFAULT_PROFILE_TOOLS)),
            "max_tool_rounds": rounds,
        },
    )


# ── a roster spec → its degenerate plan (D-3, SPI conformance) ─────────────

ROSTER_STYLES = frozenset({WorkflowStyle.SEQUENTIAL})


def plan_from_roster(spec: TeamSpec, *, max_tokens: int = 100_000) -> dict[str, Any]:
    """A sequential roster spec as the degenerate plan (ADR-092 D-3).

    One phase; one agent step per roster agent, in roster order; each agent
    its own profile (its instructions, its model, its iteration budget as
    tool rounds, the default read tools), its prompt the team's objective
    plus every earlier agent's answer inside the untrusted envelope. This
    is what "a crew is the degenerate plan" means, and how the SPI
    conformance suite runs the plan engine over a roster spec. It does not
    make the engine accept roster specs: it still declares only
    ``WorkflowStyle.PLAN`` (D-2), and slice 4 compiles crews into templates.
    Raises ``ValueError`` for a roster this lowering cannot express.
    """
    if spec.topology.style not in ROSTER_STYLES or not spec.agents:
        raise ValueError("a degenerate plan lowers a non-empty sequential roster")
    names = [_roster_step_id(index, agent.name) for index, agent in enumerate(spec.agents)]
    steps = []
    for index, agent in enumerate(spec.agents):
        earlier = "".join(f"\n\n{n}: {{{{steps.{n}.output}}}}" for n in names[:index])
        steps.append(
            {
                "id": names[index],
                "kind": "agent",
                "use": names[index],
                "label": agent.name,
                "prompt": f"{spec.task.objective}{earlier}",
            }
        )
    return {
        "plan_version": 1,
        "name": spec.run_name,
        "description": spec.task.objective,
        "agents": {name: _roster_profile(agent) for name, agent in zip(names, spec.agents)},
        "budget": {"max_tokens": spec.budgets.max_tokens or max_tokens},
        "phases": [{"id": "roster", "title": "Roster", "steps": steps}],
    }


def _roster_step_id(index: int, name: str) -> str:
    """``a<index>_<name>`` folded to a plan id ([a-z][a-z0-9_]*, no ``__``)."""
    folded = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")
    while "__" in folded:
        folded = folded.replace("__", "_")
    return f"a{index}_{folded}"[:48].rstrip("_")


def _roster_profile(agent: AgentSpec) -> dict[str, Any]:
    profile: dict[str, Any] = {"instructions": agent.instructions or ""}
    if agent.model_ref:
        profile["model"] = agent.model_ref
    if agent.max_iterations:
        profile["max_tool_rounds"] = max(1, min(int(agent.max_iterations), MAX_ROSTER_ROUNDS))
    return profile


MAX_ROSTER_ROUNDS = DEFAULT_TOOL_ROUNDS


def spec_from_roster(spec: TeamSpec, **kwargs: Any) -> TeamSpec:
    """The roster spec lowered to its degenerate plan, as a PLAN spec."""
    lowered, problems = spec_from_plan(
        plan_from_roster(spec), {}, project=spec.project, run_name=spec.run_name, **kwargs
    )
    if problems or lowered is None:
        raise ValueError("; ".join(str(p) for p in problems[:5]))
    return lowered
