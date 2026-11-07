"""Benchmark-only harness ablation flags (harness-benchmarking plan Phase 1).

The ablation table ("full harness vs. each layer disabled") is an
acceptance criterion of docs/development/harness-benchmarking-plan.md.
Flags are OFF by default and enabled ONLY via environment variables set by
the bench runner, so production companion behavior never changes silently.

Layers (component names follow r4/companion/harness/__init__.py):
  steering  — SteeringLoop lessons injection + observe_turn learning
  guides    — FeedforwardGuides domain guides + workflow templates
  memory    — ContextManager.memory_as_context() injection (session scope)
  knowledge — the pre-retrieval router's pack in the user turn (ADR-093 D-8;
              companion/retrieval_router.py), even when R4_RAG_ROUTER is on

The environment probe snapshot and ProgressTracker orientation stay on in
every cell: they are session continuity, not feedforward steering.
"""

from __future__ import annotations

import os

ABLATE_STEERING = "R4_HARNESS_ABLATE_STEERING"
ABLATE_GUIDES = "R4_HARNESS_ABLATE_GUIDES"
ABLATE_MEMORY = "R4_HARNESS_ABLATE_MEMORY"
ABLATE_KNOWLEDGE = "R4_HARNESS_ABLATE_KNOWLEDGE"


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def steering_off() -> bool:
    return _flag(ABLATE_STEERING)


def guides_off() -> bool:
    return _flag(ABLATE_GUIDES)


def memory_off() -> bool:
    return _flag(ABLATE_MEMORY)


def knowledge_off() -> bool:
    return _flag(ABLATE_KNOWLEDGE)
