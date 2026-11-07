"""Engine registry (ADR-020 §7).

Builtin CORE engines (``native``, and ``plan`` per ADR-092 D-2) plus
external adapters discovered through the ``r4.team_engines`` entry-point
group. Site-project policy can disable engines or pin trust tiers; a
broken adapter surfaces as unavailable, never as a crash.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from r4_contracts.team_engine.capabilities import EngineDescriptor
from r4_teamengine_spi.errors import PluginTrustError, UnknownEngineError
from r4_teamengine_spi.loader import (
    apply_site_policy,
    check_spi_compat,
    discover_entry_points,
)

logger = logging.getLogger(__name__)

# Builtins imported on first registry use rather than with this module, so
# a broken or half-installed optional engine is skipped with a warning
# instead of breaking every team surface (ADR-092 D-2: ``plan``).
_OPTIONAL_BUILTINS: dict[str, str] = {"plan": "r4.teams.plan.engine:PlanEngine"}
# r4's own pyproject declares its builtins on the entry-point group too; an
# entry point naming a builtin's own target is that builtin, not a plugin
# shadowing it (only a foreign target with a builtin's name shadows one).
_BUILTIN_TARGETS: dict[str, str] = {
    "native": "r4.teams.native:NativeEngine",
    **_OPTIONAL_BUILTINS,
}


@dataclass(frozen=True)
class RegisteredEngine:
    descriptor: EngineDescriptor
    factory: str  # "builtin" | entry-point name
    available: bool
    note: str = ""


class EngineRegistry:
    """Discover and hold engine factories. Instantiated per TeamService."""

    def __init__(self, site_policy: dict | None = None) -> None:
        self._policy = site_policy or {}
        self._builtin = self._load_builtin()
        self._entry_points = {
            name: ep
            for name, ep in discover_entry_points().items()
            if not _is_own_builtin(name, ep, self._builtin)
        }

    @staticmethod
    def _load_builtin() -> dict[str, type]:
        from .native import NativeEngine

        builtin: dict[str, type] = {NativeEngine.NAME: NativeEngine}
        for name, target in _OPTIONAL_BUILTINS.items():
            engine_cls = _import_optional(name, target)
            if engine_cls is not None:
                builtin[name] = engine_cls
        return builtin

    def names(self) -> list[str]:
        return sorted(set(self._builtin) | set(self._entry_points))

    def _instantiate(self, name: str):
        if name in self._entry_points:
            cls = self._entry_points[name].load()()
        elif name in self._builtin:
            cls = self._builtin[name]()
        else:
            raise UnknownEngineError(f"engine {name!r} not installed")
        check_spi_compat(cls.descriptor())
        apply_site_policy(cls.descriptor(), self._policy)
        return cls

    def get(self, name: str):
        """Instantiate a trust-gated engine by name."""
        return self._instantiate(name)

    def list(self) -> list[RegisteredEngine]:
        """Descriptors of everything installed, with availability notes."""
        out: list[RegisteredEngine] = []
        for name in sorted(set(self._builtin) | set(self._entry_points)):
            try:
                engine = self._instantiate(name)
            except (PluginTrustError, UnknownEngineError, Exception) as exc:  # noqa: BLE001
                out.append(
                    RegisteredEngine(
                        descriptor=_placeholder_descriptor(name),
                        factory=self._factory_label(name),
                        available=False,
                        note=str(exc) or "unavailable",
                    )
                )
                continue
            out.append(
                RegisteredEngine(
                    descriptor=engine.descriptor(),
                    factory=self._factory_label(name),
                    available=True,
                )
            )
        return out

    def descriptors(self) -> dict[str, EngineDescriptor]:
        return {r.descriptor.name: r.descriptor for r in self.list() if r.available}

    def builtin_names(self) -> frozenset[str]:
        """The engines shipped with r4 that no entry point shadows (ADR-092 D-2).

        The resolver's capability match picks only these: a descriptor's
        trust tier is the engine's own claim. ``_instantiate`` prefers an
        entry point over a builtin of the same name, so a plugin named
        ``plan`` is the plugin, not the builtin.
        """
        return frozenset(set(self._builtin) - set(self._entry_points))

    def _factory_label(self, name: str) -> str:
        """``builtin``, or the entry point that ``_instantiate`` loads first."""
        return name if name in self._entry_points else "builtin"


def _is_own_builtin(name: str, entry_point, builtin: dict[str, type]) -> bool:
    target = str(getattr(entry_point, "value", "") or "").strip()
    return name in builtin and target == _BUILTIN_TARGETS.get(name)


def _import_optional(name: str, target: str) -> type | None:
    """``module:attr`` of an optional builtin, or None with a logged warning.

    Native stays a hard import (it is the site default); an optional
    builtin that fails to import is left out of the registry, the same
    "engine unavailable, never a crash" rule entry points follow.
    """
    module_name, _, attr = target.partition(":")
    try:
        return getattr(importlib.import_module(module_name), attr)
    except Exception as exc:  # noqa: BLE001 — a broken builtin must not break discovery
        logger.warning("team engine %r skipped: %s unavailable (%s)", name, target, exc)
        return None


def _placeholder_descriptor(name: str) -> EngineDescriptor:
    from r4_contracts.team_engine.capabilities import (
        EngineCapabilities,
        EngineDescriptor,
        TrustTier,
    )

    return EngineDescriptor(
        name=name,
        version="?",
        spi_version="?",
        trust_tier=TrustTier.UNTRUSTED,
        capabilities=EngineCapabilities(),
    )
