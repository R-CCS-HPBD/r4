"""UMRS-backed ResearchRuntime — the authoritative production executor.

Thin adapter over :class:`r4.client.Client`'s loop control surface
(``/gw/loops`` → ``/umrs/loops``). Owns no state of its own; UMRS Postgres
is the authoritative store. The Companion and CLIs should depend on the
:class:`ResearchRuntime` API, not on this adapter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .api import (
    IterationState,
    LoopSpec,
    LoopState,
    RuntimeCapabilities,
)

if TYPE_CHECKING:
    from ..client import Client


class PlanIterationRefused(ValueError):
    """A plan loop UMRS cannot run: refused by name
    (``plan_iteration_needs_server_venue``).

    ADR-092 slice 8b: a server-hosted loop runs each iteration as one
    venue-hosted plan run, where the site has its frontend venue; UMRS
    refuses otherwise, naming the settings it lacks, and that refusal is
    raised here with UMRS's own words (``detail``). A loop whose plan
    cannot be sent (no venue source) is refused before anything is sent.
    """

    code = "plan_iteration_needs_server_venue"

    def __init__(self, template: str, detail: str = "") -> None:
        super().__init__(
            detail
            or (
                f"{self.code}: the server could not run the plan {template!r} as its "
                "loop's iteration. Run the loop on the local runtime "
                "(R4_RUNTIME_MODE=local), start it without a plan, or ask the site to "
                "enable its frontend venue (UMRS_VENUE_ENABLED and the UMRS_VENUE_* "
                "settings)."
            )
        )
        self.template = template


class PlanWebChoiceRequired(PlanIterationRefused):
    """A server plan loop with no web-access choice (ADR-099 D-2, M-4).

    The loop's runs are remote sandboxes: the user chooses their web access,
    nothing chooses it silently. Raised before anything is sent when no
    choice is known here, and for UMRS's typed refusal
    (``sessionpolicy.web_choice_required``).
    """

    code = "sessionpolicy.web_choice_required"

    def __init__(self, template: str, detail: str = "") -> None:
        if detail and self.code not in detail:
            detail = f"{self.code}: {detail}"
        super().__init__(
            template,
            detail
            or (
                f"{self.code}: choose the agent's web access for the plan loop's runs: "
                "/web in the console, r4 --web off|allowlist|public, or "
                "R4_WEB_ACCESS=off|allowlist:<domains>|public"
            ),
        )


class UmrsRuntime:
    """ResearchRuntime over the UMRS durable, distributed substrate."""

    def __init__(self, client: "Client") -> None:
        self._client = client

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            location="umrs",
            durable=True,
            distributed=True,
            backends=("slurm", "k8s", "compose"),
            approvals=True,
        )

    def start_loop(self, spec: LoopSpec, executor=None) -> LoopState:
        # UMRS runs its own server-side engine; an in-process executor is
        # meaningless here and rejected rather than silently ignored.
        if executor is not None:
            raise ValueError("UmrsRuntime does not accept an in-process executor")
        kwargs: dict[str, Any] = {}
        if spec.iteration_plan:
            kwargs["plan"] = plan_loop_fields(self._client, spec)
        if spec.session_id:
            kwargs["session_id"] = spec.session_id
        if spec.agentic:
            kwargs["agentic"] = True
        if spec.workflow_name:
            kwargs["workflow_name"] = spec.workflow_name
        if spec.max_tracks > 1:
            kwargs["max_tracks"] = spec.max_tracks
        try:
            payload = self._client.start_loop(
                spec.project_id,
                spec.research_goal,
                max_iterations=spec.max_iterations,
                approved=spec.approved,
                **kwargs,
            )
        except Exception as exc:
            _raise_plan_refusal(spec, exc)
            raise
        return LoopState.from_mapping(_ensure_goal(payload, spec))

    def get_loop(self, loop_id: str) -> LoopState:
        return LoopState.from_mapping(self._client.get_loop_status(loop_id))

    def list_loops(self, project_id: Optional[str] = None) -> List[LoopState]:
        del project_id  # UMRS scopes loops server-side; listing crosses projects intentionally
        payload = self._client.list_loops()
        raw = payload.get("loops", []) if isinstance(payload, dict) else payload
        return [LoopState.from_mapping(item) for item in raw]

    def list_iterations(self, loop_id: str, limit: int = 50) -> List[IterationState]:
        payload = self._client.list_loop_iterations(loop_id)
        raw = payload.get("iterations", []) if isinstance(payload, dict) else payload
        states = [IterationState.from_mapping(item) for item in raw]
        return states[-limit:]

    def stop_loop(self, loop_id: str) -> LoopState:
        state = self.get_loop(loop_id)
        self._client.stop_loop(loop_id)
        state.status = "stopped"
        return state

    def pause_loop(self, loop_id: str) -> LoopState:
        self._client.pause_loop(loop_id)
        state = self.get_loop(loop_id)
        state.status = "paused"
        return state

    def resume_loop(self, loop_id: str) -> LoopState:
        self._client.resume_loop(loop_id)
        state = self.get_loop(loop_id)
        state.status = "running"
        return state

    # ── durable approval inbox (ADR-036 (a)) ───────────────────────────────

    def list_approvals(self, loop_id: Optional[str] = None) -> List[Dict[str, Any]]:
        payload = self._client.list_loop_approvals(loop_id=loop_id)
        raw = payload.get("pending", []) if isinstance(payload, dict) else payload
        return [dict(item) for item in raw]

    def decide_approval(self, approval_id: str, decision: str) -> Dict[str, Any]:
        return dict(self._client.decide_loop_approval(approval_id, decision))


def plan_loop_fields(client: Any, spec: LoopSpec) -> Dict[str, Any]:
    """What UMRS needs to run ``spec.iteration_plan`` as each iteration
    (ADR-092 slice 8b): the template resolved here (r4 owns the templates),
    the caller's args, the venue sources, the default model and the mode
    (a plan loop with none asks). Checked with the shared loop table."""
    from r4_contracts.loop_plan import caller_args, loop_template

    from ..teams.plan_sources import load_template
    from ..teams.server_venue import venue_model

    shape = loop_template(spec.iteration_plan)
    args = caller_args(shape, spec.plan_args)
    if not spec.plan_sources:
        raise PlanIterationRefused(
            spec.iteration_plan,
            f"{PlanIterationRefused.code}: a server-hosted plan loop needs a venue source "
            "for its runs' models: sources=[<pushed source>] or ['site'] (the site's "
            "own models)",
        )
    from ..teams.agent_broker import client_default_model

    mode, domains = plan_web_choice(spec)
    return {
        "iteration_plan": shape.name,
        "iteration_plan_doc": load_template(shape.name),
        "plan_args": args,
        "plan_sources": list(spec.plan_sources),
        # The session's model when the start named it (spec.model), else
        # the profile default as before (2026-11-03).
        "plan_default_model": venue_model(spec.model or client_default_model(client) or ""),
        "plan_web_policy": mode,
        "plan_web_allow_domains": domains,
        "mode": spec.mode or "ask",
        "pause_triggers": list(spec.pause_triggers),
    }


def plan_web_choice(spec: LoopSpec, env: Optional[Dict[str, str]] = None) -> tuple[str, list[str]]:
    """``(web_policy, domains)`` for a server plan loop's runs (ADR-099 D-2).

    The spec's (the session's choice, set by the caller), else
    ``R4_WEB_ACCESS`` (``r4 --web``'s form); else :class:`PlanWebChoiceRequired`.
    """
    import os

    from ..companion.web_access import WebAccessError, make_web_access, web_access_from_env

    try:
        chosen = (
            make_web_access(
                spec.plan_web_policy, list(spec.plan_web_allow_domains), source="session"
            )
            if spec.plan_web_policy
            else web_access_from_env(os.environ if env is None else env)
        )
    except WebAccessError as exc:
        raise PlanWebChoiceRequired(
            spec.iteration_plan, f"{PlanWebChoiceRequired.code}: {exc}"
        ) from exc
    if chosen is None:
        raise PlanWebChoiceRequired(spec.iteration_plan)
    return chosen.mode, list(chosen.domains)


def _raise_plan_refusal(spec: LoopSpec, exc: Exception) -> None:
    """UMRS's named refusal of a plan loop, raised as ``PlanIterationRefused``."""
    if not spec.iteration_plan:
        return
    body = str(getattr(exc, "response_body", "") or exc)
    refusal = _refusal_class(body)
    if refusal is None:
        return
    try:
        import json

        detail: Any = json.loads(body).get("detail") or body
    except (ValueError, AttributeError):
        detail = body
    raise refusal(spec.iteration_plan, _detail_text(detail)) from exc


def _refusal_class(body: str) -> type[PlanIterationRefused] | None:
    """The typed refusal UMRS's error body names, else None."""
    if PlanWebChoiceRequired.code in body or "web_choice_required" in body:
        return PlanWebChoiceRequired
    if PlanIterationRefused.code in body or "plan_invalid" in body:
        return PlanIterationRefused
    return None


def _detail_text(detail: Any) -> str:
    """A typed error's ``detail`` (a dict with ``message``) or plain text."""
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("code") or detail)
    return str(detail)


def _ensure_goal(payload: Dict, spec: LoopSpec) -> Dict:
    """UMRS start response lacks project/goal fields the CLI prints; backfill.

    The resolved ``workflow_name`` (G-P1-3) rides along in ``metadata`` so
    every surface can show which registered workflow the loop will run.
    """
    out = dict(payload) if isinstance(payload, dict) else {"message": str(payload)}
    out.setdefault("project_id", spec.project_id)
    out.setdefault("research_goal", spec.research_goal)
    out.setdefault("max_iterations", spec.max_iterations)
    if out.get("workflow_name"):
        meta = dict(out.get("metadata") or {})
        meta.setdefault("workflow_name", out["workflow_name"])
        out["metadata"] = meta
    return out
