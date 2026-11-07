"""Canonical companion control-event schema (wave companion-any-client, T1).

Shared, versioned serde for the companion's control plane across rails —
ACP session events, the JSONL bridge, and the future `/gw/companion/*`
surface. One event shape everywhere; each rail encodes/decodes through
these types instead of inventing its own envelope (GAP-COMP-4).

Behavior scope: pure schema — zero runtime wiring. Consumers migrate
rail-by-rail in T2 (budget banner on the existing rails) and T3 (r4-web +
gateway endpoint), never all rails at once (mutation-safety).

Tolerance policy (future-proofing):
- Unknown *payload keys* are ignored on decode (forward compat).
- Unknown *event types* are skipped with a warning (returns None).
  Persistence changed the trade: replayed session logs written by newer
  binaries must not kill older companions attaching to them — "additive"
  is only additive if unknown types are tollerable. Typos still fail loudly
  at the PRODUCER side (to_dict refuses non-control events).
- Payload ``version`` higher than this module rejects (shape changed;
  skipping a future VERSION of a known event would silently misread data).
- Literal-typed fields are validated in ``__post_init__``.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

logger = logging.getLogger(__name__)

CONTROL_SCHEMA_VERSION = 1

_PERMISSION_MODES = frozenset({"ask_always", "auto_safe", "deny", "trust"})
_DECISIONS = frozenset({"once", "always", "no"})
_RISK_TIERS = frozenset({"risky", "never_auto"})
_MODE_SOURCES = frozenset({"user", "autopilot", "acp", "policy"})


def _require(value: str, allowed: frozenset, name: str) -> None:
    if value not in allowed:
        raise ValueError(f"{name}: {value!r} not in {sorted(allowed)}")


@dataclass(frozen=True)
class ApprovalRequest:
    """A tool call awaiting explicit human approval (GAP-COMP-4 unify)."""

    EVENT: ClassVar[str] = "approval_request"

    version: int = CONTROL_SCHEMA_VERSION
    request_id: int = 0
    tool: str = ""
    description: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    risk_tier: str = "risky"  # risky | never_auto
    session_id: str = ""
    # ADR-092 slice 2: which agent asked — a plan agent's or team role's label
    # (``permissions.approving_agent``); "" for the lead's own call. Additive:
    # an older reader ignores it.
    agent: str = ""
    # Fix 2026-10-30 (approval fence): this ask came from a content fence
    # (ADR-039 taint, credential/identity, a G14 ask rule, a protected path)
    # on THIS call's arguments, ``fence_reason`` names it. A client answers
    # it per call: never from an earlier yes, a project rule or an "always".
    # Additive: an older reader ignores both.
    fenced: bool = False
    fence_reason: str = ""

    def __post_init__(self) -> None:
        _require(self.risk_tier, _RISK_TIERS, "risk_tier")


@dataclass(frozen=True)
class ApprovalDecision:
    """The user's answer to an ApprovalRequest."""

    EVENT: ClassVar[str] = "approval_decision"

    version: int = CONTROL_SCHEMA_VERSION
    request_id: int = 0
    decision: str = "once"  # once | always | no

    def __post_init__(self) -> None:
        _require(self.decision, _DECISIONS, "decision")

    @property
    def approved(self) -> bool:
        """True when the call may proceed (once or always)."""
        return self.decision != "no"

    @property
    def applies_to_session(self) -> bool:
        """True when the decision pins a blanket per-session allow."""
        return self.decision == "always"


@dataclass(frozen=True)
class ModeChange:
    """Permission-mode transition (user toggle, autopilot flip, ACP map)."""

    EVENT: ClassVar[str] = "mode_change"

    version: int = CONTROL_SCHEMA_VERSION
    mode: str = "ask_always"  # ask_always | auto_safe | deny | trust
    source: str = "user"  # user | autopilot | acp | policy
    session_id: str = ""

    def __post_init__(self) -> None:
        _require(self.mode, _PERMISSION_MODES, "mode")
        _require(self.source, _MODE_SOURCES, "source")


@dataclass(frozen=True)
class SessionCost:
    """C3 per-turn token/cost/context read-model (chip payload).

    Dedicated control event (like BudgetState) — additive; older clients
    that don't know "cost" simply skip the line.
    """

    EVENT: ClassVar[str] = "cost"

    version: int = CONTROL_SCHEMA_VERSION
    session_id: str = ""
    model: str = ""
    turn_tokens: int = 0
    total_tokens: int = 0
    cost_usd_turn: float = 0.0
    cost_usd_total: float = 0.0
    price_known: bool = False
    context_used_tokens: int = 0
    context_limit_tokens: int = 0


@dataclass(frozen=True)
class BudgetState:
    """Per-session budget counters (GAP-COMP-2 read-model payload).

    ``None`` limits mean unbounded; counters are always ints.
    """

    EVENT: ClassVar[str] = "budget_state"

    version: int = CONTROL_SCHEMA_VERSION
    llm_calls_used: int = 0
    llm_calls_limit: Optional[int] = None
    tool_rounds_used: int = 0
    tool_rounds_limit: int = 1000
    context_used_tokens: int = 0
    context_limit_tokens: int = 120_000
    session_id: str = ""


@dataclass(frozen=True)
class SessionState:
    """Point-in-time session read-model (mode, actors, active extensions)."""

    EVENT: ClassVar[str] = "session_state"

    version: int = CONTROL_SCHEMA_VERSION
    session_id: str = ""
    project_id: str = ""
    mode: str = "ask_always"
    graphs: List[str] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    actor: str = "human"


# ── T3-ui agent-work transparency events (plan slices S1) ────────────────────


_TOOL_END_STATUSES = frozenset({"done", "error", "cancelled"})


@dataclass(frozen=True)
class ThinkingDelta:
    """A live chunk of model reasoning (from <think> markup inside streams).

    ``seq`` is the monotonic index within the current turn; partials render
    in order and are closed by ``thinking_done`` or by partial=False here.
    """

    EVENT: ClassVar[str] = "thinking_delta"

    version: int = CONTROL_SCHEMA_VERSION
    session_id: str = ""
    seq: int = 0
    text: str = ""
    partial: bool = True


@dataclass(frozen=True)
class ThinkingDone:
    """Terminal marker for the thinking stream of a turn (block closed)."""

    EVENT: ClassVar[str] = "thinking_done"

    version: int = CONTROL_SCHEMA_VERSION
    session_id: str = ""
    summary_hint: str = ""


@dataclass(frozen=True)
class ToolCallStart:
    """One governed tool invocation started.

    ``args_summary`` is the sanitized per-category summary built at emission
    (allowlist + token redaction) — NEVER the raw arguments. The rails omit
    emission for tools without a safe summary builder rather than leaking raw
    arguments.
    """

    EVENT: ClassVar[str] = "tool_call_start"

    version: int = CONTROL_SCHEMA_VERSION
    session_id: str = ""
    tool_call_id: str = ""
    name: str = ""
    args_summary: str = ""
    risk_tier: str = "risky"  # risky | never_auto

    def __post_init__(self) -> None:
        _require(self.risk_tier, _RISK_TIERS, "risk_tier")


@dataclass(frozen=True)
class ToolCallEnd:
    """Completion of a governed tool invocation (status + timing + preview).

    ``result_preview`` is sanitized and length-capped at emission, matching
    the args_summary hygiene on the start event.
    """

    EVENT: ClassVar[str] = "tool_call_end"

    version: int = CONTROL_SCHEMA_VERSION
    session_id: str = ""
    tool_call_id: str = ""
    name: str = ""
    status: str = "done"  # done | error | cancelled
    duration_ms: Optional[int] = None
    result_preview: str = ""
    # True when the wire preview was bounded (16 KiB) and the full result
    # lives in the session's result journal — fetched via sessions/tool_result.
    result_truncated: bool = False

    def __post_init__(self) -> None:
        _require(self.status, _TOOL_END_STATUSES, "status")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError(f"duration_ms must be >= 0, got {self.duration_ms}")


@dataclass(frozen=True)
class TurnProgress:
    """Heartbeat counters mid-turn (drives the live budget/progress UI).

    ``round_index`` is the 0-based agentic round counter; the other counters
    are cumulative per turn so the UI can render "round 3 · 7 tool calls".
    """

    EVENT: ClassVar[str] = "turn_progress"

    version: int = CONTROL_SCHEMA_VERSION
    session_id: str = ""
    round_index: int = 0
    tool_calls_so_far: int = 0
    llm_calls_so_far: int = 0


_SELECTION_STRATEGIES = frozenset({"passthrough", "policy_filter", "ranked"})


@dataclass(frozen=True)
class ToolSelection:
    """Per-step tool-selection record (ADR-037).

    One record per model call, emitted from the agentic loop. The record
    is the ONLY place a UI may source selection claims from (honesty
    rule); when strategy is passthrough the UI says so verbatim.

    ``discovered`` / ``filtered_out`` are free-form mappings because the
    reason vocabulary grows with strategies (mode_readonly,
    capability_tier, …); the record's list caps live in the producer
    (tool_selection.MAX_SAMPLE / MAX_SELECTED).
    """

    EVENT: ClassVar[str] = "tool_selection"

    version: int = CONTROL_SCHEMA_VERSION
    session_id: str = ""
    turn_id: str = ""
    step_index: int = 0
    available_tools: int = 0
    discovered: Dict[str, Any] = field(default_factory=dict)
    filtered_out: Dict[str, Any] = field(default_factory=dict)
    candidate_tools: int = 0
    selected_tools: List[str] = field(default_factory=list)
    selected_skills: List[str] = field(default_factory=list)
    strategy: str = "passthrough"
    ranking: Optional[Dict[str, Any]] = None
    selection_latency_ms: int = 0
    # {"before": int, "after": int, "skill_bodies": int} — doc schema
    # (ADR-037); estimate by contract, exact tokenizers deliberately not
    # wired in. skill_bodies measures eager skill-body prompt cost this turn.
    schema_tokens_estimate: Dict[str, int] = field(default_factory=dict)
    # ADR-037 amendment: catalog-integrity warnings (double registrations,
    # shadowed names) — free text, honest shape; empty list when clean.
    warnings: List[str] = field(default_factory=list)
    # ADR-037 amendment (Finding A, shadow mode): per-reason count+sample of
    # what the identity's capability tier WOULD have removed — measured only,
    # {} when no manifest data path exists (never a fabricated size).
    would_filter: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require(self.strategy, _SELECTION_STRATEGIES, "strategy")
        if self.step_index < 0:
            raise ValueError(f"step_index must be >= 0, got {self.step_index}")
        if self.available_tools < 0 or self.candidate_tools < 0:
            raise ValueError("tool counts must be >= 0")
        if self.candidate_tools > self.available_tools:
            raise ValueError(
                f"candidate_tools ({self.candidate_tools}) > "
                f"available_tools ({self.available_tools})"
            )
        for key in self.schema_tokens_estimate:
            if key not in {"before", "after", "skill_bodies"}:
                raise ValueError(f"schema_tokens_estimate: unknown key {key!r}")


_EVENT_TYPES = {
    cls.EVENT: cls
    for cls in (
        ApprovalRequest,
        ApprovalDecision,
        ModeChange,
        BudgetState,
        SessionCost,
        SessionState,
        ThinkingDelta,
        ThinkingDone,
        ToolCallStart,
        ToolCallEnd,
        ToolSelection,
        TurnProgress,
    )
}


def to_dict(event: object) -> Dict[str, Any]:
    """Serialize a control event to the wire envelope ``{type, payload}``."""
    event_type = getattr(type(event), "EVENT", "") or getattr(event, "EVENT", "")
    if event_type not in _EVENT_TYPES:
        raise ValueError(f"not a control event: {type(event).__name__}")
    return {"type": event_type, "payload": dataclasses.asdict(event)}  # type: ignore[arg-type]


def from_dict(data: Dict[str, Any]) -> object | None:
    """Deserialize a wire envelope.

    Unknown event types return None after a warning (they are events from
    a newer binary — persisted session logs mix generations). Future
    payload versions of KNOWN types still raise, and malformed payloads of
    known types raise: both are shape mismatches, not generation drift.
    """
    event_type = str(data.get("type", ""))
    cls = _EVENT_TYPES.get(event_type)
    if cls is None:
        logger.warning("skipping unknown control event type (newer binary?): %r", event_type)
        return None
    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError(f"payload for {event_type!r} must be an object")
    version = payload.get("version", 0)
    if isinstance(version, int) and version > CONTROL_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema version {version} for {event_type!r} "
            f"(max {CONTROL_SCHEMA_VERSION})"
        )
    field_names = {f.name for f in dataclasses.fields(cls)}
    kwargs = {k: v for k, v in payload.items() if k in field_names}
    return cls(**kwargs)
