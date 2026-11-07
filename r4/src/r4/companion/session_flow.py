"""Send-flow helpers for companion sessions."""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable

from .cancellation import TurnCancelledError, response_for_cancelled_turn
from .endpoint_policy import evaluate_endpoint_guard as policy_evaluate_endpoint_guard
from .session_models import BudgetLimitExceededError, SessionMessage
from .session_turns import active_tools as select_active_tools

if TYPE_CHECKING:
    from .skills import SkillRegistry


def start_turn(
    *,
    messages: list[SessionMessage],
    user_message: str,
    skills: Any,
    tools: list[dict[str, Any]],
    updated_at_factory: Callable[[], str],
    logger: logging.Logger,
) -> tuple[str, int, list[dict[str, Any]]]:
    messages.append(SessionMessage(role="user", content=user_message))
    updated_at = updated_at_factory()
    current_turn_llm_calls = 0

    newly_activated = _activate_turn_skills(skills, user_message)
    if newly_activated:
        logger.info("Auto-activated skills: %s", newly_activated)

    active_tools = select_active_tools(
        tools=tools,
        active_tool_names=skills.active_tool_names(),
    )
    return updated_at, current_turn_llm_calls, active_tools


def _activate_turn_skills(skills: "SkillRegistry", user_message: str) -> list[str]:
    """Keyword activation for one turn. Under the terminal profile (H2) only
    its coding skills are active: others are dropped, keywords reach no more."""
    from . import agent_profile

    if not agent_profile.active():
        return skills.auto_activate(user_message)
    only = agent_profile.skill_allowlist()
    for skill in skills.list_active():
        if skill.name not in only:
            skills.deactivate(skill.name)
    return skills.auto_activate(user_message, only=only)


def evaluate_turn_endpoint_guard(
    *,
    user_message: str,
    active_tools: list[dict[str, Any]],
    refresh_active_endpoints: Callable[[], None],
    get_active_endpoints: Callable[[], list[dict[str, Any]]],
    warning_threshold_sec: int,
    block_threshold_sec: int,
    format_duration: Callable[[int], str],
) -> dict[str, str]:
    refresh_active_endpoints()
    return policy_evaluate_endpoint_guard(
        user_message=user_message,
        tools=active_tools,
        active_endpoints=get_active_endpoints(),
        warning_threshold_sec=warning_threshold_sec,
        block_threshold_sec=block_threshold_sec,
        format_duration=format_duration,
    )


#: ``agent.send``'s keyword for the un-augmented message (ADR-093 D-8):
#: intent heuristics (tool families, "did the user ask for JSON") read it,
#: never the context pack's document text (r4-harness-8).
INTENT_KWARG = "intent_message"


def _accepts_keyword(func: Any, name: str) -> bool:
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == name or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def _send_kwargs(
    agent: Any,
    user_message: str,
    offer_only_names: frozenset[str] | None,
    intent_message: str | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if offer_only_names is not None:
        kwargs["offer_only_names"] = offer_only_names
    augmented = intent_message is not None and intent_message != user_message
    if augmented and _accepts_keyword(agent.send, INTENT_KWARG):
        kwargs[INTENT_KWARG] = intent_message
    return kwargs


def _turn_context_kwargs(
    agent: Any,
    extra_system: str,
    reminder: str,
    tool_refusals: dict[str, str] | None,
) -> dict[str, Any]:
    """Wave 80: the turn reminder and tool refusals for an agent that takes
    them; an agent that does not gets the reminder in its extra system block."""
    kwargs: dict[str, Any] = {"extra_system": extra_system}
    if tool_refusals and _accepts_keyword(agent.send, "tool_refusals"):
        kwargs["tool_refusals"] = tool_refusals
    if not reminder:
        return kwargs
    if _accepts_keyword(agent.send, "reminder"):
        kwargs["reminder"] = reminder
    else:
        kwargs["extra_system"] = "\n\n".join(part for part in (extra_system, reminder) if part)
    return kwargs


def _partial_reply(agent: Any) -> str:
    """What the stopped turn streamed to the screen ("" when the agent
    cannot say): the stored reply keeps it before the stop marker."""
    reply = getattr(agent, "partial_reply", None)
    text = reply() if callable(reply) else ""
    return text if isinstance(text, str) else ""


def run_agent_turn(
    *,
    agent: Any,
    user_message: str,
    history: list[dict[str, Any]],
    extra_system: str,
    active_tools: list[dict[str, Any]],
    offer_only_names: frozenset[str] | None = None,
    stream: bool = False,
    intent_message: str | None = None,
    reminder: str = "",
    tool_refusals: dict[str, str] | None = None,
) -> str:
    """One agent turn. ``user_message`` is what the model reads (with the
    router on, the context pack); ``intent_message`` is the message before
    augmentation, passed on as ``agent.send(intent_message=...)`` when the
    pack changed it and the agent takes it. ``reminder`` is the turn's
    volatile context and ``tool_refusals`` the tools this turn refuses
    (wave 80: both keep the request prefix stable)."""
    try:
        kwargs = _send_kwargs(agent, user_message, offer_only_names, intent_message)
        kwargs.update(_turn_context_kwargs(agent, extra_system, reminder, tool_refusals))
        return agent.send(
            user_message,
            history=history,
            tools=active_tools,
            stream=stream,
            **kwargs,
        )
    except TurnCancelledError:
        return response_for_cancelled_turn(partial=_partial_reply(agent))
    except BudgetLimitExceededError as exc:
        return f"Budget limit reached: {exc}"


def finish_turn(
    *,
    messages: list[SessionMessage],
    user_message: str,
    response: str,
    post_turn: Callable[[str, str], None],
    sync_metadata: Callable[[], None],
) -> str:
    messages.append(SessionMessage(role="assistant", content=response))
    post_turn(user_message, response)
    sync_metadata()
    return response


def routed_message(route: Callable[[], str]) -> str | None:
    """The message the model reads (``route()``), or ``None`` when the user
    stopped the turn during pre-retrieval: the turn then ends as stopped,
    never as "Retrieval unavailable" (review r4-harness-10, 2026-10-10)."""
    try:
        return route()
    except TurnCancelledError:
        return None


def send_turn(
    *,
    user_message: str,
    start_turn: Callable[[str], list[dict[str, Any]]],
    evaluate_turn_endpoint_guard: Callable[[str, list[dict[str, Any]]], dict[str, str]],
    prepare_turn_payload: Callable[
        ..., tuple[tuple[str, list[dict[str, Any]], list[dict[str, Any]]] | None, str | None]
    ],
    run_agent_turn: Callable[..., str],
    finish_turn: Callable[[str, str], str],
    stream: bool = False,
    route_turn: Callable[[str], str] | None = None,
    check_turn: Callable[[str, str, bool], str] | None = None,
) -> str:
    """One turn. ``route_turn`` (ADR-093 D-8) returns the message the model
    reads — the raw message, or the server's context pack with the question
    last — before payload preparation, so ``fit_payload`` counts it; the
    session keeps only the raw message. ``check_turn`` (D-9) is the
    deterministic citation check on the answer."""
    active_tools = start_turn(user_message)
    endpoint_guard = evaluate_turn_endpoint_guard(user_message, active_tools)
    if endpoint_guard["state"] == "blocked":
        return finish_turn(user_message, endpoint_guard["message"])

    model_message = (
        routed_message(lambda: route_turn(user_message)) if route_turn is not None else user_message
    )
    if model_message is None:
        return finish_turn(user_message, response_for_cancelled_turn())
    payload, error_response = prepare_turn_payload(
        user_message=model_message,
        endpoint_guard=endpoint_guard,
        active_tools=active_tools,
    )
    if error_response is not None:
        return finish_turn(user_message, error_response)

    extra_system, history, active_tools = payload
    response = run_agent_turn(
        user_message=model_message,
        history=history,
        extra_system=extra_system,
        active_tools=active_tools,
        stream=stream,
        intent_message=user_message,
    )
    if check_turn is not None:
        response = check_turn(user_message, response, stream)
    return finish_turn(user_message, response)
