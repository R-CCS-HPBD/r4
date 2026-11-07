"""Turn assembly and payload preparation helpers for companion sessions."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from .agent import COMPANION_SYSTEM_PROMPT, CompanionEventType
from .cancellation import TurnCancelledError, response_for_cancelled_turn
from .session_models import BudgetLimitExceededError, SessionMessage
from .turn_context import join_parts, stable_prefix_enabled


def _spine(platform_guidance: bool) -> list[str]:
    """The always-on research spine and platform_guide pointer (none under
    the terminal profile, H2). Full playbook via the platform_guide tool."""
    from r4_contracts.agent_tool_playbook import (
        PLATFORM_GUIDE_POINTER,
        SPINE_DISAMBIGUATION,
    )

    return [SPINE_DISAMBIGUATION.strip(), PLATFORM_GUIDE_POINTER] if platform_guidance else []


def assemble_extra_system(
    *,
    skills_discovery_prompt: str = "",
    platform_guidance: bool = True,
) -> str:
    """The session-stable extra system block (wave 80): what stays fixed
    across turns, so the system message is a cacheable prefix.

    ``platform_guidance=False`` (the terminal profile, H2) leaves out the
    always-on research spine and the platform_guide pointer.
    """
    return join_parts(*_spine(platform_guidance), skills_discovery_prompt)


def assemble_legacy_extra_system(
    *,
    skills_prompt: str,
    skills_discovery_prompt: str,
    turn_parts: str,
    platform_guidance: bool = True,
) -> str:
    """The pre-wave-80 extra system block (stable and per-turn parts mixed,
    in their old order) for ``stable_prefix_enabled() == False``."""
    spine = _spine(platform_guidance)
    return join_parts(*spine, skills_prompt, skills_discovery_prompt, turn_parts)


def assemble_turn_reminder(
    *,
    skills_prompt: str,
    memory_context: str,
    session_context: str,
    harness: Any,
    messages: list[SessionMessage],
    active_endpoints_prompt: str,
    logger: logging.Logger,
) -> str:
    """The per-turn volatile block, in a fixed order; empty parts are skipped.

    Wave 80: these parts change between turns (skill activation, memory and
    session facts, harness guides, endpoint state), so they ride the turn's
    ``<turn_context>`` block instead of the system message.
    """
    return join_parts(
        skills_prompt,
        memory_context,
        session_context,
        _harness_guides(harness, messages, logger),
        active_endpoints_prompt,
    )


def _harness_guides(harness: Any, messages: list[SessionMessage], logger: logging.Logger) -> str:
    if not harness:
        return ""
    try:
        return harness.feedforward(messages) or ""
    except Exception as exc:
        logger.debug("Harness feedforward error: %s", exc)
        return ""


def turn_reminder_with_guard(*, reminder: str, endpoint_guard: dict[str, str]) -> str:
    """The turn reminder plus the endpoint guard's warning, when it has one."""
    return join_parts(reminder, endpoint_guard.get("prompt", ""))


def build_history(messages: list[SessionMessage]) -> list[dict[str, Any]]:
    return [{"role": message.role, "content": message.content} for message in messages[:-1]]


def active_tools(
    *,
    tools: list[dict[str, Any]],
    active_tool_names: set[str],
) -> list[dict[str, Any]]:
    """Filter builtins to the active skill allowlist, always keeping meta tools.

    When *active_tool_names* is empty, every builtin is exposed (no skill
    filter). Otherwise the model sees the union of skill tools, the always-on
    meta set (ask / todo / search / final_output), the workspace tools (shell,
    python, file read/write/edit, search; W3 2026-10-05), any tool the user opted
    into session-wide (``always_on: True`` — e.g. ``/decision-model on``) and
    the web tools the session's web access lets run (2026-10-30).
    """
    from .skills import ALWAYS_ON_TOOL_NAMES, WORKSPACE_TOOL_NAMES
    from .tool_availability import web_tool_enabled

    ordered_tools = sorted(tools, key=lambda tool: tool.get("name", ""))
    if not active_tool_names:
        return ordered_tools
    allowed = set(active_tool_names) | ALWAYS_ON_TOOL_NAMES | WORKSPACE_TOOL_NAMES
    return [
        tool
        for tool in ordered_tools
        if tool["name"] in allowed or tool.get("always_on") or web_tool_enabled(tool)
    ]


def compact_turn_history(
    *,
    context_manager: Any,
    agent: Any,
    user_message: str,
    extra_system: str,
    history: list[dict[str, Any]],
    active_tools: list[dict[str, Any]],
    summary_system: str = "",
) -> tuple[list[dict[str, Any]], str | None]:
    """Compact the history when the payload nears the window (wave 80: the
    last summary is reused while the history it covers is unchanged, and a
    new one is requested on the turn's own prefix, whose stable system block
    is ``summary_system``)."""
    stable = stable_prefix_enabled()
    reused = context_manager.reuse_compaction(history) if stable else history
    if not context_manager.needs_compaction_for_payload(
        system_prompt=COMPANION_SYSTEM_PROMPT,
        extra_system=extra_system,
        history=reused,
        user_message=user_message,
        tools=active_tools,
    ):
        return reused, None

    payload = {
        "system_prompt": COMPANION_SYSTEM_PROMPT,
        "extra_system": extra_system,
        "user_message": user_message,
        "tools": active_tools,
    }
    # G11 (2026-10-29): bounded stale bodies can skip the summary entirely.
    micro = _microcompact_or_none(context_manager, reused, payload=payload, agent=agent)
    if micro is not None:
        return micro
    # The full-summary path below keeps the original history: the LLM
    # summary should capture the large bodies, not their microcompact stubs.

    # G20 (bench lane): hard reset instead of the LLM summary, when enabled.
    reset = _maybe_context_reset(reused, agent)
    if reset is not None:
        return reset

    try:
        compacted = (
            context_manager.compact_turn(
                history,
                request=_prefix_summary_request(agent, summary_system, active_tools),
            )
            if stable
            else context_manager.compact(history)
        )
    except BudgetLimitExceededError as exc:
        return history, f"Budget limit reached before compaction completed: {exc}"
    except TurnCancelledError:
        # A Stop during the summary call ends the turn as stopped, like a
        # Stop during pre-retrieval (review (80); r4-harness-10).
        return history, response_for_cancelled_turn()
    # Announce only a compaction that happened: the TUI shows "context
    # compacted" for this event, so a failed one must stay silent (TUI audit
    # r2 #20).
    agent._emit(
        CompanionEventType.CONTEXT_COMPACT,
        "Conversation compacted.",
        {"kept_messages": len(compacted)},
    )
    return compacted, None


def _microcompact_or_none(
    context_manager: Any,
    reused: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    agent: Any,
) -> tuple[list[dict[str, Any]], None] | None:
    """G11: bounded stale bodies + still-fits ⇒ skip the LLM summary.

    Deterministic and free; attribute-guarded for duck-typed test doubles.
    """
    microcompact = getattr(context_manager, "microcompact", None)
    if not callable(microcompact):
        return None
    bounded, removed = microcompact(reused)
    if not removed:
        return None
    if context_manager.needs_compaction_for_payload(history=bounded, **payload):
        return None
    agent._emit(
        CompanionEventType.CONTEXT_COMPACT,
        "Early oversized history bounded (microcompact; summary skipped).",
        {"chars_removed": removed, "kept_messages": len(bounded)},
    )
    return bounded, None


def _maybe_context_reset(
    reused: list[dict[str, Any]], agent: Any
) -> tuple[list[dict[str, Any]], None] | None:
    """G20 bench lane: keep the recent window with a marker, drop the rest,
    no LLM call. Compaction-vs-reset ablation (Anthropic's context-anxiety
    finding); enabled only via ``R4_COMPANION_CONTEXT_RESET``."""
    if os.environ.get("R4_COMPANION_CONTEXT_RESET", "").strip() not in ("1", "true", "on"):
        return None
    dropped = max(0, len(reused) - 6)
    marker = {
        "role": "system",
        "content": (
            f"[context reset — G20 bench lane] Earlier turns dropped "
            f"({dropped} messages); continue from the recent context "
            "and the running notes (progress file / contract / memory)."
        ),
    }
    agent._emit(
        CompanionEventType.CONTEXT_COMPACT,
        "Context reset (bench lane).",
        {"kept_messages": min(6, len(reused)), "dropped": dropped},
    )
    return [marker, *reused[-6:]], None


def _prefix_summary_request(
    agent: Any, extra_system: str, active_tools: list[dict[str, Any]]
) -> Callable[[list[dict[str, Any]]], Any] | None:
    """The agent's prefix-sharing compaction call, or None (the context
    manager's stand-alone summariser) for an agent without one."""
    request = getattr(agent, "compaction_request", None)
    if not callable(request):
        return None
    return lambda messages: request(messages, extra_system=extra_system, tools=active_tools)


def fit_turn_payload(
    *,
    context_manager: Any,
    user_message: str,
    extra_system: str,
    history: list[dict[str, Any]],
    active_tools: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], str | None]:
    extra_system, history, active_tools, budget = context_manager.fit_payload(
        system_prompt=COMPANION_SYSTEM_PROMPT,
        extra_system=extra_system,
        history=history,
        user_message=user_message,
        tools=active_tools,
    )
    if budget.hard_limit_exceeded:
        return (
            extra_system,
            history,
            active_tools,
            "Context budget exceeded even after trimming prompt context. "
            "Please shorten the request or reduce active context.",
        )
    return extra_system, history, active_tools, None


def prepare_turn_payload(
    *,
    context_manager: Any,
    agent: Any,
    user_message: str,
    base_extra_system: str,
    history: list[dict[str, Any]],
    active_tools: list[dict[str, Any]],
    reminder: str = "",
) -> tuple[tuple[str, list[dict[str, Any]], list[dict[str, Any]]] | None, str | None]:
    """Compaction and budget fit for one turn. ``base_extra_system`` is the
    stable system block; ``reminder`` (wave 80, the turn's volatile context)
    is budgeted after it, so a tight fit trims the reminder first. The
    payload's extra system text is the fitted ``join_parts(base, reminder)``
    (``turn_context.split_fitted`` separates them)."""
    counted_extra = join_parts(base_extra_system, reminder)
    history, compaction_error = compact_turn_history(
        context_manager=context_manager,
        agent=agent,
        user_message=user_message,
        extra_system=counted_extra,
        history=history,
        active_tools=active_tools,
        summary_system=base_extra_system,
    )
    if compaction_error is not None:
        return None, compaction_error

    extra_system, history, active_tools, fit_error = fit_turn_payload(
        context_manager=context_manager,
        user_message=user_message,
        extra_system=counted_extra,
        history=history,
        active_tools=active_tools,
    )
    if fit_error is not None:
        return None, fit_error
    return (extra_system, history, active_tools), None
