"""Which model a research loop's iterations use, and the line that says so.

Maintainer 2026-11-03: "are loop also using the service models of r4?" They
were not, and nothing said which model a loop used. Worse, an iteration
this process runs (a local agentic iteration) and a plan loop's runs took
the profile default (``config.model``) even after ``/model`` chose another,
so the chat and its loop ran different models without a word.

One order for every loop this client hands a model to:

1. the model the caller named (the ``research_loop_start`` ``model``);
2. the session's current model, the one the status bar shows (``/model``);
3. the profile default (``config.model``).

A loop the server drives picks its own model: the planned engine runs
workflows and knowledge with no chat model, and an agentic UMRS loop uses
the project's first READY chat endpoint (or the site's ``UMRS_AGENT_MODEL``).
The ``svc-*`` service tier (ADR-106) serves fast sub-agents and decisions,
not loops. :func:`loop_model_note` names whichever applies, for the start
reply of every surface.
"""

from __future__ import annotations

from typing import Any, Optional

SERVER_AGENT_MODEL = (
    "chosen by the server: the project's first ready chat endpoint on the site "
    "(or its UMRS_AGENT_MODEL), not your /model choice"
)
PLANNED_ENGINE = "none: the server's planned engine runs workflows and knowledge, no chat model"
LOCAL_PLACEHOLDER = (
    "none: the local runtime has no iteration agent here, so iterations are placeholders "
    "(ask in chat for an agentic loop, which uses your model)"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def client_default_model(client: Any) -> str:
    """``config.model`` of ``client`` ("" when it has none)."""
    for attr in ("config", "_config"):
        model = _text(getattr(getattr(client, attr, None), "model", None))
        if model:
            return model
    return ""


def resolve_loop_model(explicit: Optional[str], session_model: Optional[str], client: Any) -> str:
    """The caller's model, else the session's, else the profile default."""
    for value in (explicit, session_model):
        if _text(value):
            return _text(value)
    return client_default_model(client)


def _umrs_note(agentic: bool, plan: str, model: str) -> str:
    if plan:
        return f"{model or 'the site default'} for the plan's runs (a plan step may name its own)"
    return SERVER_AGENT_MODEL if agentic else PLANNED_ENGINE


def _local_note(plan: str, model: str, executor_bound: bool) -> str:
    if not executor_bound:
        return LOCAL_PLACEHOLDER
    if plan:
        return (
            f"{model or 'the profile default'} for the plan's runs (a plan step may name its own)"
        )
    return model or "the profile default"


def loop_model_note(
    location: str,
    *,
    agentic: bool,
    plan: str = "",
    model: str = "",
    executor_bound: bool = False,
) -> str:
    """The "model:" line of a loop start, by where its iterations run."""
    if location == "umrs":
        return _umrs_note(agentic, plan, model)
    if location == "local":
        return _local_note(plan, model, executor_bound)
    local = _local_note(plan, model, executor_bound)
    return f"by venue: local iterations use {local}; on UMRS, {_umrs_note(agentic, plan, model)}"
