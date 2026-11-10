"""Team plan RPCs and the plan ``team_event`` stream for the console bridge.

ADR-092 slice 3 ("surfaces"). The console reaches team plans through the
same executor the lead agent's chat tools use (``team_plan_tools``), so the
two surfaces cannot drift:

- ``team.plan_templates`` (safe): the built-in templates and their params.
- ``team.plan_run`` (never-auto classification): validated first, so an
  invalid plan spends nothing. Chat/plan asks with the plan's card; autopilot
  auto-answers locally without creating a human-card grant (ADR-104 A3).
  The reply lands as soon as the run has
  started (``wait_sec`` is 0 here); progress streams as ``team_event``.
  ``goal`` binds free text to the template's first required string
  parameter (``/teamup on plan:<template>``).
- ``team.plan_result`` (safe): live progress, or the bounded result.
- ``team.plan_cancel``: the human's own cancel from the console (Work pane
  ``c``), so no approval card (maintainer, 2026-10-11); the engine's own
  cancel. The agent-called ``team_plan_cancel`` tool keeps its risky rule.
- ``team.runs`` (safe): every TeamService run, plan runs and interactive
  teams, with status, agents and cost, live progress overlaid.

``team.plan_run`` and ``team.plan_cancel`` answer from a worker thread (the
bridge's deferred methods): the human may take minutes over a card, and a
cancel waits for the run to settle; the stdin loop keeps serving meanwhile.

Every plan event of this process (a run the lead started in chat included)
is written as one ``{"team_event": {...}}`` line keyed by ``run_id`` and the
agent id (see ``team_plan_events`` for the kinds). Session-keyed teamup
events are unchanged.
"""

from __future__ import annotations

import json
import weakref
from typing import Any

from .. import team_plan_events as plan_events
from .. import team_plan_tools
from ..permissions import approving_agent

# Arguments the console may give team.plan_run besides the tool's own.
_RUN_EXTRA = frozenset({"goal"})
_NOT_APPROVED = "{tool} was not approved; nothing ran and nothing was spent"
_NO_EXECUTOR = (
    "team plans run in the local chat session; this session has no team plan tools "
    "(a remote-venue session runs on its venue)"
)
MAX_RUN_ROWS = 50
_AGENT_CANCEL = (
    "team.plan_cancel is the console user's own verb; an agent cancels a plan with the "
    "team_plan_cancel tool, which asks"
)


class TeamPlanBridgeMixin:
    """Mixin expecting ``session``, ``_emit_line`` and ``_teamup_client`` on the host."""

    # ── the stream ──────────────────────────────────────────────────────────

    def _start_plan_event_stream(self) -> None:
        """Write every plan event of this process as a ``team_event`` line.

        Held through a weak reference, so a discarded server never pins
        itself in the process-wide listener list (and stops listening).
        """
        if getattr(self, "_plan_listener", None) is not None:
            return
        ref = weakref.ref(self)

        def _listener(event: dict[str, Any]) -> None:
            server = ref()
            if server is None:
                plan_events.remove_run_listener(_listener)
                return
            server._emit_line({"team_event": event})  # type: ignore[attr-defined]

        self._plan_listener = _listener
        plan_events.add_run_listener(_listener)

    def _stop_plan_event_stream(self) -> None:
        listener = getattr(self, "_plan_listener", None)
        if listener is not None:
            plan_events.remove_run_listener(listener)
            self._plan_listener = None

    # ── RPCs ────────────────────────────────────────────────────────────────

    def team_plan_templates(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return json.loads(self._plan_executor().templates())

    def team_plan_run(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate, resolve through the local permission ladder, then start."""
        executor = self._plan_executor()
        arguments, problem = _run_arguments(params)
        if problem is not None:
            return {"ok": False, "problems": [problem], "note": team_plan_tools.INVALID_NOTE}
        call = executor.prepare(arguments)
        if call.problems or call.spec is None:
            return {
                "ok": False,
                "problems": list(call.problems),
                "note": team_plan_tools.INVALID_NOTE,
            }
        if not self._plan_permitted("team_plan_run", arguments):
            return _rejected("team_plan_run")
        return json.loads(executor.run(**arguments))

    def team_plan_result(self, params: dict[str, Any]) -> dict[str, Any]:
        run_id = str(params.get("run_id") or "")
        path = str(params.get("path") or "") or None
        return json.loads(self._plan_executor().result(run_id, path))

    def team_plan_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        """A cancel the human asked for in the console: a human action, no card.

        Maintainer decision (2026-10-11): the Work pane's ``c`` is itself the
        human's decision, so it raises no approval card. Only the agent-called
        ``team_plan_cancel`` tool keeps its risky-tool rule. Agents cannot
        reach this path: it is an RPC of the console's JSONL protocol, no tool
        calls it (``test_team_plan_surfaces`` scans for one), and it refuses
        when called inside an agent's approval context.
        """
        if approving_agent():
            return {"ok": False, "status": "rejected", "error": _AGENT_CANCEL}
        executor = self._plan_executor()
        run_id = str(params.get("run_id") or "")
        return json.loads(executor.cancel_by_user(run_id, params.get("reason")))

    def team_runs(self, params: dict[str, Any]) -> dict[str, Any]:
        """Every TeamService run, newest first, live progress overlaid (Work pane)."""
        from ...teams.service import TeamService

        executor = team_plan_tools.executor_of(getattr(self.session, "tools", None))  # type: ignore[attr-defined]
        root = executor.run_root() if executor is not None else None
        service_kwargs = {"run_root": root} if root is not None else {}
        service = TeamService(self._teamup_client(), **service_kwargs)  # type: ignore[attr-defined]
        limit = _limit(params.get("limit"))
        rows = _overlay_live(service.list_runs(limit=limit), team_plan_tools.live_runs())
        return {"runs": rows, "count": len(rows)}

    # ── helpers ─────────────────────────────────────────────────────────────

    def _plan_executor(self) -> Any:
        tools = getattr(self.session, "tools", None)  # type: ignore[attr-defined]
        executor = team_plan_tools.executor_of(tools)
        if executor is None:
            raise ValueError(_NO_EXECUTOR)
        return executor

    def _plan_permitted(self, tool: str, arguments: dict[str, Any]) -> bool:
        """The session manager's decision, exactly as for the chat tool."""
        permissions = getattr(self.session, "permissions", None)  # type: ignore[attr-defined]
        check = getattr(permissions, "check", None)
        return bool(check(tool, arguments)) if callable(check) else False


def _run_arguments(params: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """The tool's arguments from the RPC's params, or a named problem.

    ``wait_sec`` is always 0: the reply lands once the run has started, and
    the run streams. ``goal`` is bound to the template's free-text param.
    """
    unknown = sorted(set(params) - team_plan_tools.RUN_ARGUMENTS - _RUN_EXTRA)
    if unknown:
        return {}, f"team.plan_run does not take {', '.join(unknown)}"
    arguments = {
        key: params[key]
        for key in team_plan_tools.RUN_ARGUMENTS
        if key != "wait_sec" and params.get(key) is not None
    }
    goal = str(params.get("goal") or "").strip()
    if goal:
        if not arguments.get("template"):
            return {}, "team.plan_run: a goal needs a template (plan:<template>)"
        args, problem = team_plan_tools.args_with_goal(
            arguments["template"], arguments.get("args"), goal
        )
        if problem is not None:
            return {}, problem
        arguments["args"] = args
    arguments["wait_sec"] = 0
    return arguments, None


def _rejected(tool: str) -> dict[str, Any]:
    return {"ok": False, "status": "rejected", "error": _NOT_APPROVED.format(tool=tool)}


def _limit(value: Any) -> int:
    try:
        return max(1, min(MAX_RUN_ROWS, int(value or MAX_RUN_ROWS)))
    except (TypeError, ValueError):
        return MAX_RUN_ROWS


def _overlay_live(rows: list[dict[str, Any]], live: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A run going in this process reads ``running`` with its live counts,
    whatever its last attempt's record said; a live run with no row yet (the
    engine has not written its manifest) is listed first."""
    by_id = {row["run_id"]: row for row in live}
    out = []
    for row in rows:
        progress = by_id.pop(row.get("run_id"), None)
        out.append(_with_progress(row, progress) if progress else row)
    fresh = [_with_progress({"kind": "plan", "run_id": rid}, p) for rid, p in by_id.items()]
    return fresh + out


def _with_progress(row: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "name": row.get("name") or progress.get("plan"),
        "status": "running",
        "phase": progress.get("phase"),
        "agents": {"ok": progress.get("agents_ok"), "null": progress.get("agents_null")},
        "cost": {"tokens": progress.get("tokens")},
        "live": True,
    }
