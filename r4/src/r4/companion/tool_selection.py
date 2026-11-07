"""Tool selection as an explicit, observable companion phase (ADR-037).

The turn loop enumerates the tool catalog (discovery) and decides what is
offered to the model for each step (selection), recording why the rest was
not offered. Passthrough is the default strategy: all tools are offered
and the record still ships, because the point is observability before
optimization.

Hard rules (ADR-037):
- Selection is NEVER the enforcement point. PermissionManager at execution
  and UMRS authz at the RPC deny; filters here are cost + UX only.
- No second LLM call for selection, at any strategy level.
- Every list in the record is capped (samples ≤ 5, selected ≤ 50 + "+N
  more") so the record itself stays cheap.
- No fabricated numbers: fields record only what the harness measures
  (today: registered catalog, user-denied hides, skill allowlist filter).
  Strategies that introduce new reasons extend the measured set, not the
  vocabulary.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

# Record budget: samples stay tiny, selected lists stay bounded. These
# constants are load-bearing — the design promise is "the record costs a
# few dozen tokens, never more".
MAX_SAMPLE = 5
MAX_SELECTED = 50

_STRATEGIES = frozenset({"passthrough", "family", "policy_filter", "ranked"})

# Family grouping (ADR-037, bench row 3): verb-cluster prefixes measured live
# (project 14, workflow 12, knowledge 7, file 6, research 6, mcp 6, endpoint
# 5, job 4 — plus smaller clusters measured in the same dump: hpc 2, lab 2,
# platform 2, teamup 3, web 2, shell 2, run/compile/test/demo/search lanes.
# Family names activate lexically when a family token appears in the user's
# message; core tools are always offered (the model's read/ask/final channel
# must never hide).
_FAMILY_PREFIXES: frozenset[str] = frozenset(
    {
        "project",
        "workflow",
        "knowledge",
        "file",
        "research",
        "mcp",
        "endpoint",
        "job",
        "model",
        "source",
        "session",
        "litellm",
        "rag",
        "vision",
        "hpc",
        "lab",
        "platform",
        "teamup",
        "web",
        "shell",
        "python",
        "run",
        "compile",
        "test",
        "demo",
        "detect",
        "start",
        "pool",
        "search",
        "apply",
    }
)

# Always-offered channel regardless of family activation.
_CORE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "final_output",
        "ask_user",
        "todo_write",
        "tool_search",
        "file_read",
        "grep_search",
        "glob_search",
        "search_docs",
        "list_dir",
        "detect_code_context",
    }
)


def classify_family(tool_name: str) -> str:
    """Prefix-extract the family: first ``_``-separated verb segment."""
    return tool_name.split("_", 1)[0]


def match_families_from_text(text: str, catalog: list[dict[str, Any]]) -> frozenset[str]:
    """Lexical family activation: a family is ON when its name appears in
    the user message (word-boundary, case-insensitive)."""
    import re as _re

    text_lower = (text or "").lower()
    present = {classify_family(str(tool.get("name", ""))) for tool in catalog}
    matched: set[str] = set()
    for family in _FAMILY_PREFIXES:
        if family not in present:
            continue
        if _re.search(rf"\b{family}\w*", text_lower):
            matched.add(family)
    return frozenset(matched)


def select_with_family(
    *,
    tools: list[dict[str, Any]],
    message_text: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """Strategy 'family': core + family-matched + utility floor.

    Measurement first: a real dump (108 builtins today) splits 72 familied
    vs 36 unfamilied. Filtering ONLY ever reaches tracked-family tools —
    utilities (apply_patch / shell_exec / hpc_* — capabilities that drive
    writes in normal mode) stay offered unconditionally: the family filter
    must not kill recall on the unfamilied third of the catalog (measured
    ``utility`` row, honest by construction).

    Returns (offered, filtered_out_rows, observed utility stats).
    """
    matched = match_families_from_text(message_text, tools)
    offered: list[dict[str, Any]] = []
    family_off: dict[str, list[str]] = {}
    utility_names: list[str] = []
    for tool in tools:
        name = str(tool.get("name", ""))
        family = classify_family(name)
        if family not in _FAMILY_PREFIXES:
            # utility floor: never filter what we could not measure
            offered.append(tool)
            utility_names.append(name)
        elif name in _CORE_TOOL_NAMES or family in matched:
            offered.append(tool)
        else:
            family_off.setdefault(family, []).append(name)
    filtered_out: dict[str, dict[str, Any]] = {}
    for family in sorted(family_off):
        names = family_off[family]
        filtered_out[f"family_off:{family}"] = {
            "count": len(names),
            "sample": names[:MAX_SAMPLE],
        }
    observed = {
        "count": len(utility_names),
        "sample": utility_names[:MAX_SAMPLE],
    }
    return offered, filtered_out, observed


@dataclass(frozen=True)
class SelectionContext:
    """Immutable per-turn selection inputs, computed once in ``send()``.

    Per model call inside the turn, ``step_record`` derives a fresh record
    (same inputs, measured around-record latency, per-step index).

    ``warnings`` is a free-text observed-fault list (duplicate registrations,
    shadowed names). ``would_filter`` is the SHADOW-mode bucket for filters
    that must not remove yet (ADR-037 amendment: capability_tier until the
    identity-derived grant path lands) — measured only, empty when no data
    path exists. ``skill_body_tokens`` is the measured context cost of the
    active skill bodies concatenated into the system prompt each turn.
    """

    available_names: tuple[str, ...]
    offered_names: tuple[str, ...]
    denied_names: tuple[str, ...]
    strategy: str
    skills_active: tuple[str, ...]
    schema_tokens_before: int
    schema_tokens_after: int
    warnings: tuple[str, ...] = ()
    would_filter: dict[str, dict[str, Any]] = field(default_factory=dict)
    skill_body_tokens: int = 0
    mode_readonly_names: tuple[str, ...] = ()
    extra_filtered_out: dict[str, dict[str, Any]] = field(default_factory=dict)
    utility_offered: dict[str, Any] | None = None

    def step_record(self, *, step_index: int, turn_id: str) -> dict[str, Any]:
        """One ToolSelectionRecord payload for one model call."""
        start = time.perf_counter()
        available = len(self.available_names)
        offered = len(self.offered_names)
        record: dict[str, Any] = {
            "type": "tool_selection",
            "turn_id": turn_id,
            "step_index": step_index,
            "available_tools": available,
            "discovered": _discovered_breakdown(self),
            "filtered_out": _filtered_out(self),
            "would_filter": dict(self.would_filter),
            "warnings": list(self.warnings),
            "candidate_tools": offered,
            "selected_tools": _cap_names(self.offered_names),
            "selected_skills": list(self.skills_active),
            "strategy": self.strategy,
            "ranking": None,
            "selection_latency_ms": 0,  # filled below
            "schema_tokens_estimate": {
                "before": self.schema_tokens_before,
                "after": self.schema_tokens_after,
                # measured context cost of skill bodies loaded this turn
                # (eager today — item 6 defers; kept as its own key so
                # schema before/after stays a pure tool-schema measure).
                "skill_bodies": self.skill_body_tokens,
            },
        }
        # Latency measured AROUND the record build only — the selection
        # decision for passthrough is the deny-prune already applied in
        # send(); timing it here keeps the number honest (it is the
        # phase's observable cost, not the prune's, which is pre-existing).
        record["selection_latency_ms"] = max(0, round((time.perf_counter() - start) * 1000))
        return record


def _discovered_breakdown(ctx: SelectionContext) -> dict[str, Any]:
    """What was enumerated, by what the harness actually tracks today.

    Source attribution beyond "session-registered catalog" does not exist
    yet (tools carry no _source field); inventing splits would be a
    fabricated number — the breakdown says so structurally.
    """
    breakdown: dict[str, Any] = {"session_registered": len(ctx.available_names)}
    if ctx.skills_active:
        breakdown["skills_active"] = list(ctx.skills_active)
    if ctx.utility_offered:
        # utility floor (measured): unfamilied tools stay offered in family
        # mode; the count is evidence the filter can't touch that slice.
        breakdown["utility_offered"] = dict(ctx.utility_offered)
    return breakdown


def _filtered_out(ctx: SelectionContext) -> dict[str, Any]:
    """Measured removals only. Passthrough still reports user-denied hides
    (those happened at the agent prune, not at 'selection' — recorded here
    as the `disabled` reason with user_denied provenance in the sample).
    Plan-mode offer-only removals appear as `mode_readonly` (execution's plan
    short-circuit allows exactly this read set — see ADR-037 amendment)."""
    out: dict[str, Any] = {}
    if ctx.denied_names:
        out["disabled"] = {
            "count": len(ctx.denied_names),
            "sample": [f"{name} (user_denied)" for name in ctx.denied_names[:MAX_SAMPLE]],
        }
    if ctx.mode_readonly_names:
        out["mode_readonly"] = {
            "count": len(ctx.mode_readonly_names),
            "sample": list(ctx.mode_readonly_names[:MAX_SAMPLE]),
        }
    for key, bucket in ctx.extra_filtered_out.items():
        if key not in out:
            out[key] = bucket
    return out


def _cap_names(names: tuple[str, ...]) -> list[str]:
    """Selected list with the honesty suffix the doc demands."""
    if len(names) <= MAX_SELECTED:
        return list(names)
    return [*names[:MAX_SELECTED], f"+{len(names) - MAX_SELECTED} more"]


def detect_duplicate_registrations(
    registrations: dict[str, list[str]],
) -> list[str]:
    """Pairwise collision detector for same-package double registrations.

    ``registrations`` maps server name → tool names it exposes. Two servers
    whose normalized names (strip the default-/hatch- prefixes) match AND
    whose tool sets match produce ONE warning naming both servers and the
    tool count. Measured comparison only — no equivalence guessing.
    """
    warnings: list[str] = []

    def _norm(name: str) -> str:
        for prefix in ("default-", "hatch-"):
            if name.startswith(prefix):
                return name[len(prefix) :]
        return name

    names = list(registrations)
    seen: set[tuple[str, str]] = set()
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            if _norm(left) != _norm(right):
                continue
            pair = (_norm(left), left) if left < right else (_norm(right), right)
            if pair in seen:
                continue
            left_tools = sorted(registrations[left])
            if left_tools != sorted(registrations[right]):
                continue
            seen.add(pair)
            warnings.append(
                f"{left} and {right} register the same "
                f"{len(left_tools)} tools (double registration)"
            )
    return warnings


def estimate_schema_tokens(tools: list[dict[str, Any]]) -> int:
    """Rough tool-schema context cost: serialized bytes / 4 (chars→tokens).

    The estimate is deliberately crude and labeled `estimate` everywhere —
    an exact tokenizer would couple the phase to model internals; the
    ratio between before/after is what decisions need.
    """
    try:
        return max(0, len(json.dumps(tools, default=str)) // 4)
    except (TypeError, ValueError):  # never let accounting crash a turn
        return 0


def _removal_attribution(
    tools: list[dict[str, Any]],
    offered: list[dict[str, Any]],
    offer_only_names: frozenset[str] | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split removals into user-denied vs plan-mode (offer-only) removals.

    With offer_only active, a name outside the offer set left via the plan
    short-circuit (mode_readonly); a name inside the set but absent left
    via the deny prune. Without offer_only everything absent is denied.
    """
    offered_names = {str(tool.get("name", "")) for tool in offered}
    denied = [
        str(tool.get("name", ""))
        for tool in tools
        if str(tool.get("name", "")) not in offered_names
    ]
    if offer_only_names is None:
        return tuple(denied), ()
    readonly = [name for name in denied if name not in offer_only_names]
    kept = [name for name in denied if name in offer_only_names]
    return tuple(kept), tuple(readonly)


def build_selection_context(
    *,
    tools: list[dict[str, Any]],
    offered: list[dict[str, Any]],
    strategy: str = "passthrough",
    skills_active: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
    would_filter: dict[str, dict[str, Any]] | None = None,
    skill_body_text: str = "",
    offer_only_names: frozenset[str] | None = None,
    extra_filtered_out: dict[str, dict[str, Any]] | None = None,
    utility_offered: dict[str, Any] | None = None,
    user_denied_names: tuple[str, ...] | None = None,
) -> SelectionContext:
    """Compute the per-turn selection inputs.

    ``tools`` is the turn-level catalog handed to send(); ``offered`` is
    the post-prune set that actually reaches the model. ``user_denied_names``
    overrides the set-difference fallback: with the availability gate
    (round-3 task-1 fix) not every removal is a user denial — only names the
    permission layer actually hid must land in the `disabled` bucket.
    """
    if strategy not in _STRATEGIES:
        raise ValueError(f"unknown selection strategy: {strategy!r}")
    denied_names, mode_readonly_names = _removal_attribution(tools, offered, offer_only_names)
    if user_denied_names is not None:
        denied_names = tuple(user_denied_names)
    return SelectionContext(
        available_names=tuple(str(tool.get("name", "")) for tool in tools),
        offered_names=tuple(str(tool.get("name", "")) for tool in offered),
        denied_names=denied_names,
        strategy=strategy,
        skills_active=skills_active,
        schema_tokens_before=estimate_schema_tokens(tools),
        schema_tokens_after=estimate_schema_tokens(offered),
        warnings=warnings,
        would_filter=would_filter or {},
        skill_body_tokens=max(0, len(skill_body_text) // 4),
        mode_readonly_names=mode_readonly_names,
        extra_filtered_out=extra_filtered_out or {},
        utility_offered=utility_offered,
    )


def _unavailable_suffix(record: dict[str, Any]) -> str:
    """' · N unavailable filtered' when the availability gate removed tools
    (round-2 finding D); empty string otherwise."""
    unavailable = (record.get("filtered_out") or {}).get("unavailable") or {}
    count = int(unavailable.get("count") or 0)
    if not count:
        return ""
    return f" · {count} unavailable filtered"


def selection_summary_line(record: dict[str, Any]) -> str:
    """Human one-liner for the event message (honesty rule: the message
    can only restate what the record carries — no 'optimized' language)."""
    available = int(record.get("available_tools") or 0)
    candidate = int(record.get("candidate_tools") or 0)
    strategy = str(record.get("strategy") or "passthrough")
    if strategy == "passthrough" and candidate == available:
        line = f"all {available} tools offered (no selection)"
    else:
        line = f"{candidate} of {available} tools offered ({strategy})"
    return line + _unavailable_suffix(record)
