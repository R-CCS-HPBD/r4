"""Broker request/response DTOs (ADR-020 reverse-control services).

These are the only shapes that cross the engine/runtime boundary for
outward effects. Engines receive broker implementations that speak these
types; engines never hold credentials or open connections themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..taint import TaintedSegment


@dataclass(frozen=True)
class ToolDescriptor:
    """MCP tool manifest as surfaced to engines."""

    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    required_capabilities: tuple[str, ...] = ()  # r4_mcp ToolCapability flags


@dataclass(frozen=True)
class ToolCallRequest:
    server: str
    tool: str
    arguments: dict[str, Any]
    agent: str  # requesting agent — policy evaluated per-agent
    call_id: str


@dataclass(frozen=True)
class ToolCallResult:
    call_id: str
    ok: bool
    content: Any = None
    error: str | None = None


@dataclass(frozen=True)
class ModelRequest:
    """OpenAI-compatible request routed by the platform ModelBroker."""

    messages: tuple[dict[str, Any], ...]
    model: str | None = None  # None = broker routes
    tools: tuple[ToolDescriptor, ...] = ()
    temperature: float | None = None
    max_tokens: int | None = None
    seed: int | None = None  # reproducibility anchor


@dataclass(frozen=True)
class ModelChunk:
    text: str
    finish_reason: str | None = None
    tool_calls: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AgentTurnRequest:
    """One task-scoped agent turn asked of the AgentBroker (ADR-092 D-3).

    The engine supplies only data: the rendered prompt, the profile's
    instructions, the tool allowlist (the broker narrows it further), a
    catalog model name and an optional output schema. ``call_key`` is the
    journal key; ``label`` names the agent in events and approvals.
    """

    call_key: str
    label: str
    prompt: str
    instructions: str = ""
    tools: tuple[str, ...] = ()
    model: str | None = None
    schema: dict[str, Any] | None = None
    max_tool_rounds: int = 8
    taint_in: tuple[TaintedSegment, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTurnResult:
    """The outcome of one agent turn. ``ok`` False means ``output`` is None
    and ``reason`` says why; nothing is fabricated (ADR-092 D-5)."""

    ok: bool
    output: Any = None
    text: str = ""
    reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    taint_out: tuple[TaintedSegment, ...] = ()
    repaired: bool = False
    # ADR-092 slice 2 (K3): one entry per project-knowledge read the turn
    # made — the query, the project and the numbered passages' handles —
    # journalled with the call so a result names what it was grounded on.
    retrieval: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class GateRequest:
    """A plan ``gate`` step asking a human (ADR-092 slice 2, ADR-066 classes).

    ``trigger_class`` is the ADR-066 class (``cost``, ``competing_hypotheses``,
    ``physical_action``, ``direction_change``); ``prompt`` is the rendered
    question, agent-written parts inside untrusted envelopes. A gate is never
    auto-approved: a broker with no human to ask refuses it.
    """

    call_key: str
    gate: str  # the gate's node path in the plan
    trigger_class: str
    prompt: str
    run_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GateDecision:
    """A gate's outcome. ``answered`` is True only when a human answered:
    a refusal for want of a human (``reason`` says why) is never an answer,
    so it is not journalled as one and a resume asks again."""

    approved: bool
    answered: bool = False
    reason: str = ""
    decided_by: str = ""
