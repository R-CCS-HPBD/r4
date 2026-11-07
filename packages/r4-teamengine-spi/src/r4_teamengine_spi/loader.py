"""Plugin discovery and trust-gated loading (ADR-020 §7).

Layered discovery:
  1. ``r4.team_engines`` entry points (in-proc trusted engines)
  2. site policy overlay: enable/disable + trust-tier overrides
  3. sidecar engines (declared in site config, phase 4 — see sidecar/)
  4. MCP bridge adapter (optional package, not implemented here)

Engines are never imported statically by the runtime. A broken adapter
surfaces as "engine unavailable" and cannot break R4.
"""

from __future__ import annotations

import importlib.metadata
from typing import Any

from r4_contracts.team_engine.capabilities import EngineDescriptor, TrustTier

from .engine import TeamEngine
from .errors import IncompatibleSpiVersion, PluginTrustError, UnknownEngineError
from .version import is_compatible

ENTRY_POINT_GROUP = "r4.team_engines"


def discover_entry_points() -> dict[str, importlib.metadata.EntryPoint]:
    """Map engine name -> entry point, without importing anything."""
    return {ep.name: ep for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)}


def discover_descriptors() -> dict[str, EngineDescriptor]:
    """Instantiate each plugin and collect descriptors. Incompatible
    plugins are skipped (they surface as "engine unavailable", not a crash)."""
    found: dict[str, EngineDescriptor] = {}
    for name, ep in discover_entry_points().items():
        try:
            engine_cls = ep.load()
            desc = engine_cls().descriptor()
        except Exception:  # noqa: BLE001 — a broken plugin must not break discovery
            continue
        if not is_compatible(desc.spi_version):
            continue
        found[desc.name or name] = desc
    return found


def check_spi_compat(desc: EngineDescriptor) -> None:
    """Raise IncompatibleSpiVersion if the descriptor targets another SPI."""
    if not is_compatible(desc.spi_version):
        raise IncompatibleSpiVersion(
            f"engine {desc.name!r} built against SPI {desc.spi_version}, "
            "outside the supported window"
        )


def apply_site_policy(desc: EngineDescriptor, policy: dict[str, Any] | None) -> TrustTier:
    """Resolve the effective trust tier after site policy overlay.

    policy shape::

        {"enabled": {"langgraph": True, "mada": False},
         "trust_overrides": {"experimental": "untrusted"}}

    Fail closed: unknown shape or explicit disable raises PluginTrustError.
    """
    policy = policy or {}
    enabled = policy.get("enabled", {})
    overrides = policy.get("trust_overrides", {})
    tier = TrustTier(overrides.get(desc.name, desc.trust_tier.value))
    if tier is TrustTier.UNTRUSTED:
        raise PluginTrustError(f"engine {desc.name!r} is untrusted: must run as sidecar (phase 4)")
    if not enabled.get(desc.name, True):
        raise PluginTrustError(f"engine {desc.name!r} disabled by site policy")
    return tier


def load(name: str, *, site_policy: dict[str, Any] | None = None) -> TeamEngine:
    """Instantiate a trust-gated in-proc engine by name."""
    eps = discover_entry_points()
    ep = eps.get(name)
    if ep is None:
        raise UnknownEngineError(
            f"no installed adapter provides engine {name!r} (installed: {sorted(eps)})"
        )
    engine_cls = ep.load()
    engine: TeamEngine = engine_cls()
    desc = engine.descriptor()
    check_spi_compat(desc)
    apply_site_policy(desc, site_policy)
    return engine
