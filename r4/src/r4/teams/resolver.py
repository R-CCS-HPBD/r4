"""Engine selection resolver (ADR-020 §8).

Resolution chain (first match wins):
  1. explicit request (``engine=``)
  2. caller/agent hint (policy-checked)
  3. spec-driven capability match
  4. project default
  5. site default ("native")

Step 3 only acts when the default (4, else 5) is unavailable or cannot
express the spec: it then picks the first available builtin CORE engine,
by name, that lacks nothing the spec needs (ADR-092 D-2: a ``plan`` spec
reaches the ``plan`` engine while every roster spec keeps resolving to
native). Builtin means shipped with r4 (``EngineRegistry.builtin_names``):
a descriptor's trust tier is the plugin's own claim, so a plugin that
declares CORE is never picked by capability. When no builtin can, the
default is kept exactly as before and the engine's own ``validate``
refuses the spec by name at submit.

Every resolution records who asked, what was picked, and why — agent
autonomy is auditable, never silent.
"""

from __future__ import annotations

from dataclasses import dataclass

from r4_contracts.team_engine.capabilities import TrustTier
from r4_contracts.team_engine.spec import TeamSpec, WorkflowStyle
from r4_teamengine_spi.errors import UnknownEngineError

from ..teams.registry import EngineRegistry

SITE_DEFAULT_ENGINE = "native"

_STYLE_CAPABILITY: dict[WorkflowStyle, str] = {
    WorkflowStyle.GRAPH: "supports_graph_execution",
    WorkflowStyle.HIERARCHICAL: "supports_hierarchical_agents",
    WorkflowStyle.DAG: "supports_graph_execution",
}


@dataclass(frozen=True)
class Resolution:
    engine_name: str
    requested: str | None
    hint: str | None
    reason: str


def _capable_of(descriptor, spec: TeamSpec) -> list[str]:
    """Missing capabilities for this spec. Empty list = fully capable."""
    checks = (
        _style_checks,
        _parallel_checks,
        _resource_checks,
        _interaction_checks,
    )
    missing: list[str] = []
    for check in checks:
        missing.extend(check(descriptor.capabilities, spec))
    return missing


def _style_checks(caps, spec: TeamSpec) -> list[str]:
    missing: list[str] = []
    if spec.topology.style not in caps.workflow_styles:
        missing.append(f"style:{spec.topology.style.value}")
    flag = _STYLE_CAPABILITY.get(spec.topology.style)
    if flag and not getattr(caps, flag, False):
        missing.append(flag)
    return missing


def _parallel_checks(caps, spec: TeamSpec) -> list[str]:
    missing: list[str] = []
    if spec.topology.max_parallel > 1 and not caps.supports_parallel_agents:
        missing.append("supports_parallel_agents")
    if caps.max_agents is not None and len(spec.agents) > caps.max_agents:
        missing.append(f"max_agents:{caps.max_agents}")
    return missing


def _resource_checks(caps, spec: TeamSpec) -> list[str]:
    missing: list[str] = []
    if spec.resources.gpu_workers and not caps.supports_gpu_workers:
        missing.append("supports_gpu_workers")
    if spec.resources.distributed_ok and not caps.supports_distributed_execution:
        missing.append("supports_distributed_execution")
    return missing


def _interaction_checks(caps, spec: TeamSpec) -> list[str]:
    if spec.task.human_checkpoints and not caps.supports_human_in_loop:
        return ["supports_human_in_loop"]
    return []


def resolve_engine(
    registry: EngineRegistry,
    spec: TeamSpec,
    *,
    requested: str | None = None,
    hint: str | None = None,
    project_default: str | None = None,
) -> Resolution:
    """Select an engine. Raises UnknownEngineError for hard failures;
    soft fallbacks record the reason on the returned Resolution."""
    available = registry.descriptors()

    if requested:
        return _resolve_requested(available, spec, requested, hint)

    if hint and hint in available and not _capable_of(available[hint], spec):
        return Resolution(hint, requested, hint, "agent hint accepted")

    builtins = _builtin_names(registry)
    return _resolve_fallback(available, spec, requested, hint, project_default, builtins)


def _builtin_names(registry) -> frozenset[str]:
    """The engines shipped with r4 (ADR-092 D-2); none for a registry that
    cannot say, so step 3 then never routes by capability (fail closed)."""
    names = getattr(registry, "builtin_names", None)
    return frozenset(names()) if callable(names) else frozenset()


def _resolve_requested(available, spec: TeamSpec, requested: str, hint) -> Resolution:
    if requested not in available:
        raise UnknownEngineError(
            f"requested engine {requested!r} unavailable (installed: {sorted(available)})"
        )
    missing = _capable_of(available[requested], spec)
    reason = "explicit request"
    if missing:
        reason += f" (warning: missing {missing})"
    return Resolution(requested, requested, hint, reason)


def _resolve_fallback(
    available, spec: TeamSpec, requested, hint, project_default, builtins=frozenset()
) -> Resolution:
    note = f"hint {hint!r} incapable/disallowed, fell back; " if hint else ""
    default, reason = _default_engine(available, project_default)
    gap = _default_gap(available, spec, default, project_default)
    if gap:
        matched = _capability_match(available, spec, builtins)
        if matched:
            return Resolution(matched, requested, hint, f"{note}capability match ({gap})")
    if default is None:
        raise UnknownEngineError(f"no usable engine installed (installed: {sorted(available)})")
    return Resolution(default, requested, hint, note + reason)


def _default_engine(available, project_default) -> tuple[str | None, str]:
    """Steps 4-5 as before: the first installed of project, then site default."""
    for candidate, reason in (
        (project_default, "project default"),
        (SITE_DEFAULT_ENGINE, "site default"),
    ):
        if candidate and candidate in available:
            return candidate, reason
    return None, ""


def _default_gap(available, spec: TeamSpec, default, project_default) -> str:
    """Why the default cannot run this spec; empty when it can."""
    if default is None:
        return f"{project_default or SITE_DEFAULT_ENGINE} unavailable"
    missing = _capable_of(available[default], spec)
    return f"{default} lacks {', '.join(missing)}" if missing else ""


def _capability_match(available, spec: TeamSpec, builtins=frozenset()) -> str | None:
    """Step 3: the first available builtin CORE engine, by name, missing nothing.

    Builtin and CORE only: a capability gap must never route a spec to a
    plugin the site did not choose as its default (ADR-020 §7 trust tiers,
    ADR-092 D-2). The tier alone is not enough: it is what the plugin's own
    descriptor declares, and a site's ``trust_overrides`` never reaches it.
    """
    for name in sorted(set(available) & set(builtins)):
        descriptor = available[name]
        if descriptor.trust_tier is TrustTier.CORE and not _capable_of(descriptor, spec):
            return name
    return None
