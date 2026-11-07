"""Adapter registry — fail closed on unknown harness names."""

from __future__ import annotations

from benchmarks.harness.adapters.aider import AiderAdapter
from benchmarks.harness.adapters.base import (
    AdapterResult,
    AdapterUnavailable,
    HarnessAdapter,
)
from benchmarks.harness.adapters.claude_code import ClaudeCodeAdapter
from benchmarks.harness.adapters.copilot import CopilotAdapter
from benchmarks.harness.adapters.echo import EchoAdapter
from benchmarks.harness.adapters.opencode import OpenCodeAdapter
from benchmarks.harness.adapters.r4_companion import R4CompanionAdapter

ADAPTER_REGISTRY: dict[str, type[HarnessAdapter]] = {
    cls.name: cls
    for cls in (
        R4CompanionAdapter,
        ClaudeCodeAdapter,
        OpenCodeAdapter,
        AiderAdapter,
        CopilotAdapter,
        EchoAdapter,
    )
}


def load_adapters(
    names: list[str],
    *,
    model: str | None = None,
    timeout_sec: int = 900,
) -> list[HarnessAdapter]:
    """Instantiate adapters; unknown names raise — fail closed so a typo
    never silently drops a baseline from a comparison run."""
    adapters: list[HarnessAdapter] = []
    for name in names:
        cls = ADAPTER_REGISTRY.get(name)
        if cls is None:
            raise KeyError(f"unknown harness {name!r} (known: {sorted(ADAPTER_REGISTRY)})")
        adapters.append(cls(model=model, timeout_sec=timeout_sec))
    return adapters


__all__ = [
    "ADAPTER_REGISTRY",
    "AdapterResult",
    "AdapterUnavailable",
    "HarnessAdapter",
    "load_adapters",
]
