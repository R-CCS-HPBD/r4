"""Team Engine runtime integration (ADR-020 phases 1-2).

r4.teams hosts the client-side TeamService: engine registry, selection
resolver, run session supervision with append-only event persistence, and
broker implementations over the r4 client stack. Adapters (starting with
the CORE native engine) implement r4_teamengine_spi.TeamEngine; external
adapters plug in via the ``r4.team_engines`` entry-point group.
"""

from __future__ import annotations

from .registry import EngineRegistry, RegisteredEngine
from .resolver import Resolution, resolve_engine
from .service import RunHandle, TeamService

__all__ = [
    "EngineRegistry",
    "RegisteredEngine",
    "Resolution",
    "resolve_engine",
    "RunHandle",
    "TeamService",
]
