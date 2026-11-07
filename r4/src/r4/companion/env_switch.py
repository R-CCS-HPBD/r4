"""One parser for the on/off environment switches of the TB 4.0 harness fixes.

Plan: ``docs/development/tb4-harness-efficiency-2026-10-24.md`` §3.1 (H20).
Every switch accepts the same words, so an ablation arm written ``=false``
turns a fix off exactly like ``=0`` (review 2026-10-24: three switches only
honoured ``0``). Any other value, or the variable unset, gives the default.
"""

from __future__ import annotations

import os

ON_VALUES = ("1", "true", "yes", "on")
OFF_VALUES = ("0", "false", "no", "off")


def env_flag(name: str) -> bool | None:
    """True / False for a recognised on / off word, ``None`` otherwise."""
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in ON_VALUES:
        return True
    if raw in OFF_VALUES:
        return False
    return None


def env_switch(name: str, default: bool) -> bool:
    """The switch's value: the recognised word when set, else *default*."""
    flag = env_flag(name)
    return default if flag is None else flag


AUTOPILOT_POLICY_ENV = "R4_AUTOPILOT_SERVER_POLICY"


def autopilot_server_policy() -> bool:
    """``R4_AUTOPILOT_SERVER_POLICY`` is on: an organisation narrows autopilot
    to its server allowlist (ADR-035; off by default, ADR-104 amendment 1)."""
    return env_switch(AUTOPILOT_POLICY_ENV, False)


def autonomous() -> bool:
    """``R4_AUTONOMOUS`` is on: an unattended run, nobody answers questions."""
    return env_flag("R4_AUTONOMOUS") is True
