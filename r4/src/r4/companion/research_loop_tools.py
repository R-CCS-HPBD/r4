"""Companion research-loop engine tools, routed through the Research Runtime API.

Chat-first entry to autonomous research loops on any surface (Companion
REPL, console TUI, VS Code webview). The Companion orchestrates; the runtime
executes. The resolved :class:`~r4.runtime.api.ResearchRuntime` decides the
iteration substrate:

- ``UmrsRuntime`` (default): authoritative UMRS substrate (durable,
  distributed, Slurm/K8s/Compose backends).
- ``LocalRuntime``: SQLite local runtime — dev/CI/offline. Iterations then
  become bounded agent turns via the local iteration executor, using the
  caller's own chat endpoint (fully offline-capable when reachable).

Runtime selection follows project metadata and ``R4_RUNTIME_MODE`` env;
tools surface the location in every reply (location is informational, not a
control knob — the same dossier of verbs works identically for either).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from .knowledge_tools import _project_id_for  # R6.5: single precedence chain

if TYPE_CHECKING:
    from r4.client import Client
    from r4.runtime.api import ResearchRuntime

logger = logging.getLogger(__name__)


def _tool(name: str, description: str, parameters: dict[str, Any], execute) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
        "execute": execute,
    }


def _structured_error(code: str, message: str, **details: Any) -> str:
    return json.dumps(
        {"type": "research_loop_error", "code": code, "message": message, "details": details},
        indent=2,
    )


def _runtime_for(client: "Client", project_id: str) -> "ResearchRuntime":
    """Resolve the ResearchRuntime for one project invocation."""
    from r4.runtime import get_runtime

    return get_runtime(project_id=project_id, client=client)


def _serialize_loop(state: Any) -> dict[str, Any]:
    payload = state.to_dict() if hasattr(state, "to_dict") else dict(state or {})
    out: dict[str, Any] = {
        "loop_id": payload.get("loop_id"),
        "status": payload.get("status"),
        "phase": payload.get("phase"),
        "research_goal": payload.get("research_goal"),
        "current_iteration": payload.get("current_iteration"),
        "max_iterations": payload.get("max_iterations"),
    }
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        # Parallel hypothesis tracks (ADR-066 amendment 2, D-34): present only
        # when the loop really runs tracked — never invented defaults.
        tracks = metadata.get("tracks")
        if isinstance(tracks, list) and tracks:
            out["tracks"] = tracks
        max_tracks = metadata.get("max_tracks")
        if isinstance(max_tracks, int) and max_tracks > 1:
            out["max_tracks"] = max_tracks
    return out


def _runtime_location(runtime: "ResearchRuntime") -> dict[str, Any]:
    """Location disclosure (location is informational, never control flow)."""
    caps = runtime.capabilities()
    return {
        "runtime": caps.location,
        "durable": caps.durable,
        "distributed": caps.distributed,
        "backends": list(caps.backends),
    }


def _local_executor(client: "Client", project_id: str, model: str = ""):
    """Per-iteration agent for the LocalRuntime (milestone 3).

    Every iteration becomes one bounded agent turn driven by the caller's chat
    endpoint (``Client.chat_completion`` — gateway or public source, no full
    session needed): one hypothesis grounded in the knowledge graph, its
    evidence ids, an evaluation, and a next step. If the chat call fails,
    the iteration degrades to a digest entry — loop stays durable.
    """
    from r4.runtime.agent_executor import LocalAgentIterationExecutor

    # The loop's model (runtime/loop_model.py): it used to be None, so the
    # iterations ran the profile default after /model chose another (2026-11-03).
    return LocalAgentIterationExecutor(client, project_id, model=model or None)


def _reply_started(
    state: Any, project_id: str, runtime: "ResearchRuntime", model_note: str = ""
) -> dict[str, Any]:
    reply = {
        "type": "research_loop_started",
        "project_id": project_id,
        **_serialize_loop(state),
        **_runtime_location(runtime),
        # Which model the iterations use, in words (runtime/loop_model.py).
        "model": model_note,
        "next": (
            "research_loop_status to watch; the Studio Knowledge & Loops panel "
            "shows iterations live."
        ),
    }
    metadata = getattr(state, "metadata", None) or {}
    if metadata.get("iteration_plan"):
        # ADR-092 slice 6: say what each iteration runs and who it asks.
        reply["iteration_plan"] = metadata["iteration_plan"]
        reply["mode"] = metadata.get("mode")
        reply["next"] += (
            " Each iteration runs the plan; in ask mode the loop parks at every plan run "
            "and every plan gate: research_loop_approvals lists them, and only the "
            "human's answer (research_loop_decide) lets them go on."
        )
    return reply


# ── ADR-092 slice 6: a plan as the iteration executor ────────────────────


def _iteration_wiring(
    client: "Client",
    target: str,
    runtime: "ResearchRuntime",
    plan: str,
    plan_args: Any,
    policy: dict[str, Any],
    model: str = "",
) -> tuple[Any, dict[str, Any]]:
    """``(executor, LoopSpec policy fields)`` of one start.

    Without a plan nothing changes. With one, a runtime that drives
    iterations in this process (local, or hybrid's local venue) gets the
    plan executor; the UMRS runtime gets none: UMRS runs each round as one
    venue-hosted plan run (slice 8b). The loop's mode is the caller's, else the project's persisted
    ask policy (D-33), else the local runtime's plan-loop default (ask); its
    wall clock is the tighter of the caller's and the project's ADR-067 cap.
    """
    from r4.runtime.hybrid import HybridRuntime
    from r4.runtime.local import LocalRuntime

    fields = {
        "mode": policy.get("mode") or "",
        "pause_triggers": tuple(policy.get("triggers") or ()),
    }
    if not plan:
        local = isinstance(runtime, LocalRuntime)
        return (_local_executor(client, target, model) if local else None), fields
    from r4.runtime.plan_executor import PlanIterationExecutor

    executor = None
    if isinstance(runtime, (LocalRuntime, HybridRuntime)):
        executor = PlanIterationExecutor(client, target, template=plan, args=plan_args or {})
    if not fields["mode"]:
        fields["mode"], fields["pause_triggers"] = _project_ask_policy(client, target)
    given = policy.get("max_wall_clock_hours")
    fields["max_wall_clock_hours"] = _tighter_cap(
        given, _project_wall_clock_hours(client, target, given)
    )
    return executor, fields


def _source_names(value: Any) -> list[str]:
    """Venue sources of a server-hosted plan loop (list or csv)."""
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in value or () if str(v).strip()]


def _policy_arguments(mode: Any, triggers: Any, hours: Any) -> dict[str, Any]:
    return {
        "mode": str(mode or "").strip(),
        "triggers": list(triggers or []),
        "max_wall_clock_hours": hours,
    }


def _project_ask_policy(client: "Client", target: str) -> tuple[str, tuple]:
    """The project's persisted ask policy (D-33), or ``("", ())`` when it
    has none or it cannot be read (the runtime's default then applies)."""
    try:
        project = (client.get_platform_project(target) or {}).get("project") or {}
    except Exception:  # noqa: BLE001 - a default, never a start blocker
        return "", ()
    mode = str(project.get("ask_mode") or "").strip()
    triggers = tuple(project.get("pause_triggers") or ()) if mode == "profile" else ()
    return mode, triggers


def _project_wall_clock_hours(client: "Client", target: str, given: Any = None) -> float:
    """The project's ADR-067 wall-clock cap in hours (0 = uncapped). An
    unreadable row (platform unreachable) is uncapped, logged, as the
    server reads it; so is a 404 for a project recorded only locally (it has
    no server row). A DENIED row (403, or 404 for a project expected on the
    platform: a foreign project, or one reachable only through an unsigned
    membership since H-1) runs under the caller's cap ``given`` when it is
    positive, and otherwise refuses the start instead of running uncapped
    (H-1, 2026-10-18)."""
    from r4.client import denial_status, project_expected_on_platform

    try:
        payload = (
            client.get(
                f"{getattr(client, '_api_prefix', '/gw')}/projects/{target}/budget", timeout=15
            )
            or {}
        )
        return float((payload.get("budget") or {}).get("max_wall_clock_hours") or 0.0)
    except Exception as exc:  # noqa: BLE001 - fail open, as ADR-067 does
        status = denial_status(exc)
        if status == 403 or (status == 404 and project_expected_on_platform(target)):
            return _denied_budget_cap(target, status, given, exc)
        logger.warning("project %s budget unreadable; the loop is uncapped: %s", target, exc)
        return 0.0


def _denied_budget_cap(target: str, status: int, given: Any, exc: Exception) -> float:
    """A denied budget row: 0 (the caller's positive cap then applies), or
    refuse the start when no caller cap would bound the loop."""
    from r4.errors import R4Error

    if _tighter_cap(given, 0.0) > 0:
        logger.warning(
            "project %s: the platform denied its budget (HTTP %s); the loop runs "
            "under the caller's max_wall_clock_hours only",
            target,
            status,
        )
        return 0.0
    raise R4Error(
        f"research loop refused: the platform denied project {target}'s budget "
        f"(HTTP {status}), so its wall-clock cap cannot be read, and no "
        "max_wall_clock_hours was given: the loop would run uncapped. Pass "
        "max_wall_clock_hours, or check the project id and your membership "
        "(`r4 project switch`)."
    ) from exc


def _tighter_cap(given: Any, project: float) -> float:
    caps = [float(c) for c in (given, project) if isinstance(c, (int, float)) and c > 0]
    return min(caps) if caps else 0.0


# ── Tool builders ────────────────────────────────────────────────────────


def _resolve_session_arg(client: "Client", target: str, session: str | None) -> str | None:
    """Interpret the optional session selector.

    Accepted forms: explicit session id, "latest" (most recent active session
    for the project), "none" (skip linkage), or unset (skip). Any session-store
    error under "latest" simply returns None (no durable store — linkage is
    additive, never a hard dependency).
    """
    if not session:
        return None
    value = session.strip()
    if not value or value == "none":
        return None
    if value != "latest":
        return value
    try:
        data = client.list_research_sessions(target, status="active")
    except Exception:  # noqa: BLE001 - selector convenience must never break start
        return None
    sessions = data.get("sessions") or []
    return sessions[0]["session_id"] if sessions else None


def _plan_web_fields(web_access: Any, plan: str) -> dict[str, Any]:
    """The session's web-access choice for a plan loop's server runs
    (ADR-099 D-2): ``plan_web_policy`` and its domains, {} when unchosen
    (the UMRS runtime then refuses by name: the agent never chooses it).
    A local session's ``default_off`` is not a choice (RS-1 round-2 fix W2-4)."""
    from .web_access import remote_start_choice

    if not plan or web_access is None:
        return {}
    chosen = remote_start_choice(web_access())
    if chosen is None:
        return {}
    return {"plan_web_policy": chosen.mode, "plan_web_allow_domains": tuple(chosen.domains)}


def _start_model(client: "Client", reader: Any, explicit: str | None) -> str:
    """The loop's model: the caller's, else the session's now, else the profile's."""
    from r4.runtime.loop_model import resolve_loop_model

    try:
        session_model = reader() if callable(reader) else reader
    except Exception:  # noqa: BLE001 - an accessor failure falls back to the profile
        session_model = None
    return resolve_loop_model(explicit, session_model, client)


def _start_model_note(runtime: Any, *, agentic: bool, plan: str, model: str, executor: Any) -> str:
    from r4.runtime.loop_model import loop_model_note

    location = runtime.capabilities().location
    return loop_model_note(
        location, agentic=agentic, plan=plan, model=model, executor_bound=executor is not None
    )


def _build_research_loop_start(
    client: "Client", web_access: Any = None, reader: Any = None
) -> dict[str, Any]:
    def _start(
        research_goal: str,
        project_id: str | None = None,
        max_iterations: int = 10,
        requires: dict | None = None,
        session: str | None = None,
        agentic: bool = False,
        workflow_name: str | None = None,
        plan: str | None = None,
        plan_args: dict | None = None,
        mode: str | None = None,
        pause_triggers: list | None = None,
        max_wall_clock_hours: float | None = None,
        sources: Any = None,
        model: str | None = None,
    ) -> str:
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected — pass project_id or set one in the client config.",
            )
        try:
            from r4.runtime import LoopSpec
            from r4.runtime.umrs import PlanIterationRefused

            runtime = _runtime_for(client, target)
            plan_name = (plan or "").strip()
            policy = _policy_arguments(mode, pause_triggers, max_wall_clock_hours)
            loop_model = _start_model(client, reader, model)
            executor, fields = _iteration_wiring(
                client, target, runtime, plan_name, plan_args, policy, loop_model
            )
            state = runtime.start_loop(
                LoopSpec(
                    project_id=target,
                    research_goal=research_goal,
                    max_iterations=max_iterations,
                    requires=requires or {},
                    session_id=_resolve_session_arg(client, target, session) or "",
                    agentic=agentic,
                    workflow_name=(workflow_name or "").strip(),
                    iteration_plan=plan_name,
                    plan_args=dict(plan_args or {}),
                    plan_sources=tuple(_source_names(sources)),
                    model=loop_model,
                    **fields,
                    **_plan_web_fields(web_access, plan_name),
                ),
                executor=executor,
            )
            note = _start_model_note(
                runtime, agentic=agentic, plan=plan_name, model=loop_model, executor=executor
            )
            return json.dumps(_reply_started(state, target, runtime, note), indent=2, default=str)
        except PlanIterationRefused as exc:
            return _structured_error(exc.code, str(exc), project_id=target, plan=exc.template)
        except Exception as exc:  # noqa: BLE001 - a structured reply is the contract
            return _structured_error("research_loop_start_failed", str(exc), project_id=target)

    return _tool(
        "research_loop_start",
        (
            "Start an autonomous research loop on a project — the engine step"
            " after knowledge build (knowledge_ingest). The resolved runtime"
            " (UMRS or local) runs iterations continuously; watch with"
            " research_loop_status / research_loop_list. workflow_name names the"
            " registered UMRS workflow each real-mode experiment runs (discover"
            " with workflow_list; with exactly one registered workflow UMRS uses"
            " it automatically, otherwise the start fails closed naming the"
            " candidates)."
        ),
        {
            "type": "object",
            "properties": {
                "research_goal": {
                    "type": "string",
                    "description": (
                        "Goal statement the loop iterates on "
                        "(e.g. 'optimize MOF-5 for CO2 capture')"
                    ),
                },
                "project_id": {
                    "type": "string",
                    "description": "Project to loop over (default: current)",
                },
                "max_iterations": {
                    "type": "integer",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Cap on iterations (defensive: prevents runaway loops)",
                },
                "requires": {
                    "type": "object",
                    "description": (
                        "Resource requirements hint for venue routing, e.g. "
                        '{"gpu": 1, "cores": 128, "cluster": true}. Used only '
                        "in hybrid mode: cluster-class needs run on UMRS, "
                        "everything else runs locally."
                    ),
                },
                "session": {
                    "type": "string",
                    "description": (
                        "Durable research session to attach this loop to (ADR-025). "
                        "Forms: 'sess-...' (explicit id), 'latest' (most recent "
                        "active session for the project), 'none' (no linkage). "
                        "Create one first with research_session_create when a "
                        "new investigation starts."
                    ),
                },
                "agentic": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Wave 9 (UMRS venue): drive iterations with the site LLM "
                        "agent executor (one bounded turn per iteration, "
                        "parity with the local runtime executor). No-op on the "
                        "local runtime (which is already agent-driven)."
                    ),
                },
                "workflow_name": {
                    "type": "string",
                    "description": (
                        "Registered UMRS workflow every real-mode experiment runs. "
                        "Discover with workflow_list; omit when exactly one workflow "
                        "is registered (UMRS uses it). Never invent a name."
                    ),
                },
                "plan": {
                    "type": "string",
                    "description": (
                        "ADR-092 slices 6, 8b: a team-plan template each iteration "
                        "runs (research-round): in this process on the local runtime, "
                        "or on the UMRS site as one sandboxed run per iteration (give "
                        "sources; a site without the frontend venue refuses by name). "
                        "The loop decides continue / publish / stop from the reviewers' "
                        "verdict; a picked design is only PROPOSED to the knowledge "
                        "graph, never established."
                    ),
                },
                "plan_args": {
                    "type": "object",
                    "description": (
                        "Template parameters besides the goal and the loop's own "
                        'context (e.g. {"judges": 3, "reviewer_model": "..."}).'
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "ask", "profile"],
                    "description": (
                        "ADR-066 ask policy of a plan loop: ask (park at every plan "
                        "run for the human), profile (park when pause_triggers names "
                        "cost), auto (run under the loop's caps). Default: the "
                        "project's ask policy, else ask. Plan gates always ask."
                    ),
                },
                "pause_triggers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Trigger classes for mode=profile (a plan run is 'cost').",
                },
                "max_wall_clock_hours": {
                    "type": "number",
                    "description": (
                        "ADR-067 wall-clock cap of a plan loop in hours; the project's "
                        "cap applies too (the tighter wins)."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Model for iterations this client drives (a local agentic loop, a "
                        "plan loop's runs). Default: the session's current model. A "
                        "server-driven loop picks its own; the reply's 'model' says which."
                    ),
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "A server-hosted plan loop (ADR-092 slice 8b): the venue sources "
                        "its runs' models come from: the user's pushed source(s) and/or "
                        "'site' (the site's own models). Required for a server plan loop."
                    ),
                },
            },
            "required": ["research_goal"],
        },
        _start,
    )


def _build_research_session_create(client: "Client") -> dict[str, Any]:
    def _create(
        research_goal: str,
        project_id: str | None = None,
        title: str | None = None,
        authoritative_runtime: str | None = None,
    ) -> str:
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected — pass project_id or set one in the client config.",
            )
        create_fn = getattr(client, "create_research_session", None)
        if create_fn is None:
            return _structured_error(
                "sessions_unavailable",
                (
                    "Research sessions need a UMRS-backed gateway (ADR-025). "
                    "This client has no sessions API — check the configured source."
                ),
                project_id=target,
            )
        try:
            kwargs: dict[str, Any] = {"title": title or ""}
            if authoritative_runtime:
                kwargs["authoritative_runtime"] = authoritative_runtime
            data = create_fn(target, research_goal, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return _structured_error("research_session_create_failed", str(exc), project_id=target)
        session = (data or {}).get("session") or {}
        return json.dumps(
            {
                "type": "research_session_created",
                "project_id": target,
                "session_id": session.get("session_id"),
                "title": session.get("title"),
                "status": session.get("status"),
                "goal": research_goal,
                "next": (
                    "research_loop_start with session='sess-...' (or 'latest') "
                    "to run this investigation continuously; research_session_view "
                    "to check its loops and knowledge."
                ),
            },
            indent=2,
            default=str,
        )

    return _tool(
        "research_session_create",
        (
            "Open a durable research investigation session (ADR-025) for a "
            "project. Use when a NEW line of inquiry starts: the session is the "
            "stable thread loops get attached to (session='sess-...'/'latest' in "
            "research_loop_start). Composing several loops under one session "
            "keeps everything resumable across clients and restarts."
        ),
        {
            "type": "object",
            "properties": {
                "research_goal": {
                    "type": "string",
                    "description": "Investigation intent (title is derived from it when unset)",
                },
                "project_id": {
                    "type": "string",
                    "description": "Owning project (default: current)",
                },
                "title": {
                    "type": "string",
                    "description": "Human label (optional; derived from goal when unset)",
                },
                "authoritative_runtime": {
                    "type": "string",
                    "enum": ["umrs", "local", "hybrid"],
                    "description": ("Runtime ownership hint on the session record (default: umrs)"),
                },
            },
            "required": ["research_goal"],
        },
        _create,
    )


def _list_payload(runtime: "ResearchRuntime", target: str) -> dict[str, Any]:
    payload = [_serialize_loop(state) for state in runtime.list_loops()]
    active = [item for item in payload if item.get("status") in {"running", "paused", "pending"}]
    return {
        "type": "research_loop_list",
        "project_id": target,
        "loops": payload,
        "active_count": len(active),
        **_runtime_location(runtime),
        "next": ("research_loop_status for detail on one; research_loop_stop to stop a live loop."),
    }


def _build_research_loop_list(client: "Client") -> dict[str, Any]:
    def _list(project_id: str | None = None) -> str:
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected — pass project_id or set one in the client config.",
            )
        try:
            return json.dumps(
                _list_payload(_runtime_for(client, target), target), indent=2, default=str
            )
        except Exception as exc:  # noqa: BLE001
            return _structured_error("research_loop_list_failed", str(exc), project_id=target)

    return _tool(
        "research_loop_list",
        "List research loops on the current (or given) project with status and iteration counts.",
        {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project to list (default: current)",
                },
            },
        },
        _list,
    )


def _build_research_loop_status(client: "Client") -> dict[str, Any]:
    def _status(loop_id: str, project_id: str | None = None) -> str:
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected — pass project_id or set one in the client config.",
            )
        try:
            runtime = _runtime_for(client, target)
            state = runtime.get_loop(loop_id)
            iterations = runtime.list_iterations(loop_id, limit=50)
            return json.dumps(
                {
                    "type": "research_loop_status",
                    "project_id": target,
                    **_serialize_loop(state),
                    **_runtime_location(runtime),
                    "recent_iterations": [it.to_dict() for it in iterations[:5]],
                    "next": (
                        "research_loop_stop when done; "
                        "the Knowledge & Loops panel shows the graph while it runs."
                    ),
                },
                indent=2,
                default=str,
            )
        except Exception as exc:  # noqa: BLE001
            return _structured_error(
                "research_loop_status_failed", str(exc), project_id=target, loop_id=loop_id
            )

    return _tool(
        "research_loop_status",
        "Fetch the current status + recent iterations of one research loop.",
        {
            "type": "object",
            "properties": {
                "loop_id": {
                    "type": "string",
                    "description": "Loop id from research_loop_start / list",
                },
                "project_id": {
                    "type": "string",
                    "description": "Project owning the loop (default: current)",
                },
            },
            "required": ["loop_id"],
        },
        _status,
    )


def _build_research_loop_stop(client: "Client") -> dict[str, Any]:
    def _stop(loop_id: str, project_id: str | None = None) -> str:
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected — pass project_id or set one in the client config.",
            )
        try:
            runtime = _runtime_for(client, target)
            state = runtime.stop_loop(loop_id)
            return json.dumps(
                {
                    "type": "research_loop_stopped",
                    "project_id": target,
                    "loop_id": loop_id,
                    **{
                        "status": state.status,
                        **_runtime_location(runtime),
                    },
                },
                indent=2,
                default=str,
            )
        except Exception as exc:  # noqa: BLE001
            return _structured_error(
                "research_loop_stop_failed", str(exc), project_id=target, loop_id=loop_id
            )

    return _tool(
        "research_loop_stop",
        "Stop a running research loop cleanly. Idempotent when the loop already ended.",
        {
            "type": "object",
            "properties": {
                "loop_id": {"type": "string"},
                "project_id": {
                    "type": "string",
                    "description": "Project owning the loop (default: current)",
                },
            },
            "required": ["loop_id"],
        },
        _stop,
    )


def _build_research_session_view(client: "Client") -> dict[str, Any]:
    def _view(project_id: str | None = None) -> str:
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected — pass project_id or set one in the client config.",
            )
        try:
            from r4.runtime.session_view import research_session_view

            return json.dumps(research_session_view(client, target), indent=2, default=str)
        except Exception as exc:  # noqa: BLE001
            return _structured_error("research_session_view_failed", str(exc), project_id=target)

    return _tool(
        "research_session_view",
        "Aggregate a project's live research session: loops, knowledge digest, "
        "and where the authoritative state lives (read-only).",
        {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project (default: current)"},
            },
        },
        _view,
    )


APPROVAL_DECISIONS = ("once", "always", "no")


def _build_research_loop_approvals(client: "Client") -> dict[str, Any]:
    def _approvals(loop_id: str | None = None, project_id: str | None = None) -> str:
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected — pass project_id or set one in the client config.",
            )
        try:
            runtime = _runtime_for(client, target)
            pending = runtime.list_approvals(loop_id)
            return json.dumps(
                {
                    "type": "research_loop_approvals",
                    "project_id": target,
                    "pending": pending,
                    **_runtime_location(runtime),
                    "next": (
                        "Show these to the human; record THEIR answer with "
                        "research_loop_decide (once | always | no). Never decide alone."
                    ),
                },
                indent=2,
                default=str,
            )
        except Exception as exc:  # noqa: BLE001
            return _structured_error(
                "research_loop_approvals_failed", str(exc), project_id=target, loop_id=loop_id
            )

    return _tool(
        "research_loop_approvals",
        "List pending tool-grain approvals of research loops: server-hosted "
        "loops' durable inbox (ADR-036; answerable from any client of the loop "
        "owner) and, on the local runtime, a plan loop's parked plan runs and "
        "plan gates (ADR-092 slice 6). Only the human answers them.",
        {
            "type": "object",
            "properties": {
                "loop_id": {"type": "string", "description": "Narrow to one loop (optional)"},
                "project_id": {"type": "string", "description": "Project (default: current)"},
            },
        },
        _approvals,
    )


def _build_research_loop_decide(client: "Client") -> dict[str, Any]:
    def _decide(approval_id: str, decision: str, project_id: str | None = None) -> str:
        if decision not in APPROVAL_DECISIONS:
            return _structured_error(
                "invalid_decision",
                f"decision must be one of {list(APPROVAL_DECISIONS)}",
                approval_id=approval_id,
            )
        target = _project_id_for(client, project_id)
        if not target:
            return _structured_error(
                "project_missing",
                "No project selected — pass project_id or set one in the client config.",
            )
        try:
            runtime = _runtime_for(client, target)
            result = runtime.decide_approval(approval_id, decision)
            return json.dumps(
                {
                    "type": "research_loop_decision",
                    "project_id": target,
                    "approval_id": approval_id,
                    "decision": decision,
                    **result,
                    **_runtime_location(runtime),
                },
                indent=2,
                default=str,
            )
        except Exception as exc:  # noqa: BLE001
            return _structured_error(
                "research_loop_decide_failed",
                str(exc),
                project_id=target,
                approval_id=approval_id,
            )

    return _tool(
        "research_loop_decide",
        "Record the human's decision on one pending loop approval "
        "(once | always | no). This is the human's call — never auto-approved.",
        {
            "type": "object",
            "properties": {
                "approval_id": {
                    "type": "string",
                    "description": "Approval id from research_loop_approvals",
                },
                "decision": {"type": "string", "enum": list(APPROVAL_DECISIONS)},
                "project_id": {"type": "string", "description": "Project (default: current)"},
            },
            "required": ["approval_id", "decision"],
        },
        _decide,
    )


def build_research_loop_tools(
    client: "Client", web_access: Any = None, reader: Any = None
) -> list[dict[str, Any]]:
    """Return research-loop engine tools (all surfaces reach them via get_builtin_tools).

    ``web_access`` is the session's web-access accessor: a server plan loop's
    runs get the user's choice (ADR-099 D-2). ``reader`` is the session's
    model accessor: a loop's iterations default to the session's model."""
    return [
        _build_research_loop_start(client, web_access, reader),
        _build_research_loop_list(client),
        _build_research_loop_status(client),
        _build_research_loop_stop(client),
        _build_research_loop_approvals(client),
        _build_research_loop_decide(client),
        _build_research_session_create(client),
        _build_research_session_view(client),
    ]
