"""Team-role turn engine (wave 2026-09-28 — per-agent live work visibility).

A team role is no longer one flat ``client.chat()``: each turn runs a small
companion (its own think → tools → respond loop) under the role's verbatim
prompt. Compat decisions recorded by the wave:

- **Inherited toolset — full parity (2026-10-01)**: when the session carries the
  host's PermissionManager and every approval/ask lane is request-routed, roles
  inherit the host's FULL builtin catalog and ride the SAME approval ladder as
  the original agent. When no manager is wired (tests/native adapters), the
  concurrency-safe subset applies and risky tools stay honestly unavailable.
  Amended by ADR-092 D-6 (2026-10-09): the catalog excludes TEAM_AGENT_DENY and
  each turn runs on its own fork of the host manager (own taint slot, labelled
  approvals, same mode and approvals).
  Amended by ADR-092 MD-7 (2026-10-11): each turn runs on the plan agents'
  per-round hooks (``_RoleTurnHooks``): the host chat session's usage records
  and LLM-call caps, and the ADR-062 preflight of the role's model; a tool's
  own model calls are charged the same way.
- **Activity is first-class**: every discrete fact a role agent does lands as
  a `RoleActivityEvent` {role, kind, name?, status?, preview, duration_ms?} and
  fires the session's optional `_on_agent_activity` hook (live in the TUI).
- **Prompt is what the model sees**: role prompt rides `extra_system` (crew
  pack/tailor contract), never paraphrased. The user message = the team's
  build context (budget/changelog/sota), as before.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, List

from ..companion.permissions import TEAM_AGENT_DENY
from ..companion.tools import _CONCURRENCY_SAFE_TOOLS

logger = logging.getLogger(__name__)

_ACTIVITY_CAP = 100
_PREVIEW_CLIP = 96
_ARGS_CLIP = 72


@dataclass(frozen=True)
class RoleActivityEvent:
    """One discrete fact from a role's turn, bounded fields only."""

    role: str
    kind: str  # thinking | tool_call | tool_result | response_start | final
    name: str = ""
    status: str = ""
    preview: str = ""
    duration_ms: int | None = None
    emitted_at: float = 0.0


def _clip(text: Any, cap: int = _PREVIEW_CLIP) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= cap else flat[: cap - 1] + "…"


def _default_toolkit(
    client: Any,
    *,
    full_parity: bool = False,
    reader: str | None = None,
    hooks: _RoleTurnHooks | None = None,
    web_access: Any = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Host builtins for the role turn.

    ``full_parity=True`` (the session has the host's PermissionManager and all
    approval/ask lanes are request-routed) → the host's FULL catalog. Otherwise
    the concurrency-safe subset. Returns (tools, notice): when the builtins
    cannot be built for this client (fixture-class or unwired), the role runs
    tool-free, honestly named — never a mid-turn error.

    ``reader`` is the model the turn runs on (``_role_reader``): the catalog
    is built for it, so ``knowledge_query`` and ``search_docs`` size their
    cut by the role's own model (ADR-093 D-14, 2026-10-11), and
    ``search_agentic`` plans and synthesises on it, as it does for the lead
    session and for plan agents.

    ``hooks`` (MD-7): with a lead session to charge, the catalog is built on
    a metered client, so a tool's own model calls (``test_fix_loop``,
    ``search_agentic``) are charged and capped like the role's rounds.

    ``web_access`` is the lead session's web-access accessor: the role's web
    tools follow the lead's choice (ADR-099 D-9; None: R4_WEB_ACCESS, else
    they refuse, RS-1 fix W3).
    """
    from ..companion.tools import get_builtin_tools

    if hooks is not None and hooks.charges_session:
        from ..teams.tool_metering import MeteredToolClient

        client = MeteredToolClient(client, charge=hooks.usage_callback, guard=hooks.tool_guard)
    try:
        builtins = get_builtin_tools(client, session_model=reader, web_access=web_access)
    except Exception as exc:  # noqa: BLE001 — availability notice, never a crash
        logger.warning("role tool discovery unavailable (%s) — this turn runs tool-free", exc)
        return [], f"tool discovery unavailable: {str(exc)[:160]}"
    # ADR-092 D-6 (MD-7): a role is never broader than the lead; it is never
    # offered a tool that starts teams, plans or loops, decides for the human,
    # changes platform trust or allocation, or asks the user. In BOTH
    # branches: the concurrency-safe subset holds deny-set tools too
    # (team_plan_templates, team_plan_result), and a role turn without a
    # host manager was offered them.
    offered = [t for t in builtins if t.get("name") not in TEAM_AGENT_DENY]
    if full_parity:
        return offered, None
    return [t for t in offered if t.get("name") in _CONCURRENCY_SAFE_TOOLS], None


class RoleTurnActivities:
    """Bounded capture of the sub-companion's events for one role turn."""

    def __init__(self, role_value: str, on_event: Any | None = None, cap: int = _ACTIVITY_CAP):
        self._role = role_value
        self._sink = on_event
        self._cap = cap
        self.entries: List[RoleActivityEvent] = []

    def _push(self, event: RoleActivityEvent) -> None:
        self.entries.append(event)
        if len(self.entries) > self._cap:
            self.entries = self.entries[1:]
        if self._sink is not None:
            try:
                self._sink(event)
            except Exception:  # never crash a turn on telemetry
                logger.debug("activity sink failed", exc_info=True)

    # Companion event consumer ------------------------------------------------
    def _record_thinking(self, event: Any, now: float) -> None:
        message = str(getattr(event, "message", "") or "")
        if message and not message.startswith("Processing"):
            self._push(
                RoleActivityEvent(
                    role=self._role, kind="thinking", preview=_clip(message), emitted_at=now
                )
            )

    def _record_tool_call(self, data: dict[str, Any], now: float) -> None:
        name = str(data.get("name") or "?")
        preview = _clip(data.get("arguments"), _ARGS_CLIP)
        self._push(
            RoleActivityEvent(
                role=self._role, kind="tool_call", name=name, preview=preview, emitted_at=now
            )
        )

    def _record_tool_result(self, data: dict[str, Any], now: float) -> None:
        status = str(data.get("status") or "")
        duration = data.get("duration_ms")
        self._push(
            RoleActivityEvent(
                role=self._role,
                kind="tool_result",
                status=status,
                preview=_clip(data.get("result_preview") or ""),
                duration_ms=duration if isinstance(duration, (int, float)) else None,
                emitted_at=now,
            )
        )

    def listener(self):
        def _listener(event: Any) -> None:
            from ..companion.agent import CompanionEventType

            event_type = str(getattr(getattr(event, "event_type", None), "value", "") or "")
            data = getattr(event, "data", None) or {}
            now = time.monotonic()
            if event_type == CompanionEventType.THINKING.value:
                self._record_thinking(event, now)
                return
            if event_type == CompanionEventType.TOOL_CALL.value:
                self._record_tool_call(data, now)
                return
            if event_type == CompanionEventType.TOOL_RESULT.value:
                self._record_tool_result(data, now)
                return
            if event_type == CompanionEventType.RESPONSE_START.value:
                self._push(
                    RoleActivityEvent(role=self._role, kind="response_start", emitted_at=now)
                )

        return _listener


def _default_factory(
    client: Any,
    *,
    model: str | None,
    temperature: float,
    max_tool_rounds: int,
    permission_manager: Any,
    usage_callback: Callable[[str, dict[str, Any]], None] | None = None,
    pre_llm_call: Callable[[str], None] | None = None,
) -> Any:
    """Default role companion factory (one mini companion per turn).

    ``usage_callback`` and ``pre_llm_call`` are the companion's own per-round
    hooks: the plan broker charges each round's measured tokens through the
    first and refuses the next round past a ceiling through the second
    (ADR-092 D-7).
    """
    from ..companion.agent import ResearchCompanion

    return ResearchCompanion(
        client,
        skill_registry=None,
        permission_manager=permission_manager,
        model=model,
        temperature=temperature,
        max_tool_rounds=max(1, int(max_tool_rounds)),
        usage_callback=usage_callback,
        pre_llm_call=pre_llm_call,
        call_type="subagent",
    )


def _session_factory(session: Any) -> Callable[..., Any]:
    """Instance ``_subcompanion_factory`` → class-level default → the agentic
    default factory. Never serialized; adapters/tests may inject."""
    return (
        getattr(session, "_subcompanion_factory", None)
        or getattr(type(session), "_subcompanion_factory", None)
        or _default_factory
    )


def _build_subcompanion(
    session: Any,
    role_prompt: str,
    *,
    max_tool_rounds: int,
    model: str | None = None,
) -> Any:
    """One mini companion for one turn of one role (see ``_session_factory``)."""
    return _session_factory(session)(
        session.client,
        model=model,
        temperature=0.7,
        max_tool_rounds=max_tool_rounds,
        permission_manager=getattr(session, "_permissions", None),
    )


def _new_companion(
    factory: Callable[..., Any],
    client: Any,
    *,
    model: str | None,
    max_tool_rounds: int,
    permission_manager: Any,
    usage_callback: Callable[[str, dict[str, Any]], None] | None,
    pre_llm_call: Callable[[str], None] | None,
) -> Any:
    """Call ``factory`` the way every factory understands; the per-round hooks
    are passed only when set, so injected legacy factories keep working."""
    hooks = {
        name: hook
        for name, hook in (("usage_callback", usage_callback), ("pre_llm_call", pre_llm_call))
        if hook is not None
    }
    return factory(
        client,
        model=model,
        temperature=0.7,
        max_tool_rounds=max_tool_rounds,
        permission_manager=permission_manager,
        **hooks,
    )


def _attach_cancel(companion: Any, cancel: Any | None) -> None:
    setter = getattr(companion, "set_turn_cancel", None)
    if cancel is not None and callable(setter):
        setter(cancel)


def _send_with_fallback(
    companion: Any, prompt: str, *, system: str, tools: list[dict[str, Any]], stream: bool
) -> str | None:
    """One ``send``; a stream-unsupported fixture retries once without stream."""
    for attempt_stream in (stream, False):
        try:
            return companion.send(prompt, extra_system=system, tools=tools, stream=attempt_stream)
        except AttributeError as exc:
            # Only the stream-unsupported fixture class may legitimately
            # fall back; any other AttributeError is a real crash.
            if "_stream_request" not in str(exc) or not attempt_stream:
                raise
    return None


def run_agent_turn(
    client: Any,
    *,
    label: str,
    prompt: str,
    system: str,
    tools: list[dict[str, Any]],
    model: str | None,
    permission_manager: Any,
    max_tool_rounds: int = 8,
    usage_callback: Callable[[str, dict[str, Any]], None] | None = None,
    pre_llm_call: Callable[[str], None] | None = None,
    cancel: Any | None = None,
    on_activity: Callable[[RoleActivityEvent], None] | None = None,
    factory: Callable[..., Any] | None = None,
    stream: bool = True,
) -> tuple[str | None, list[RoleActivityEvent]]:
    """Run one agent turn as one fresh companion (ADR-092 D-3).

    The one runtime for every agent turn: a team role (``run_role_turn``)
    and a plan agent (the plan broker) differ only in what they pass. The
    caller decides the tools and the permission manager (an agent's own
    fork, D-6); ``system`` rides as the companion's extra-system. Returns
    (final text, activity entries); the ``final`` activity is pushed even
    when the turn raises.
    """
    activities = RoleTurnActivities(label, on_event=on_activity)
    companion = _new_companion(
        factory or _default_factory,
        client,
        model=model,
        max_tool_rounds=max_tool_rounds,
        permission_manager=permission_manager,
        usage_callback=usage_callback,
        pre_llm_call=pre_llm_call,
    )
    companion.on_event(activities.listener())
    _attach_cancel(companion, cancel)
    final_text = None
    try:
        final_text = _send_with_fallback(
            companion, prompt, system=system, tools=tools, stream=stream
        )
    finally:
        activities._push(
            RoleActivityEvent(
                role=label, kind="final", preview=_clip(""), emitted_at=time.monotonic()
            )
        )
    return final_text, list(activities.entries)


def _role_permissions(host: Any, label: str) -> Any:
    """The role's own fork of the host manager (ADR-092 D-6, MD-7), so
    parallel roles never share a taint slot and every approval names its
    role. A duck-typed manager without forks is passed through unchanged."""
    fork = getattr(host, "fork_for_agent", None)
    if host is None or not callable(fork):
        return host
    return fork(label)


class _RoleTurnHooks:
    """A role turn's per-round hooks: the plan agents' shape (ADR-092 D-7, MD-7).

    Role turns skipped both hooks, so their rounds reached neither the lead
    session's usage records nor its LLM-call caps, and the ADR-062 route was
    first checked inside the round. Now, as for a plan agent:

    - ``usage_callback`` charges every measured round to the lead session's
      ``record_usage`` hook (when the team was started from a chat session);
    - ``pre_llm_call`` resolves the turn's model through the client's ADR-062
      gate before the turn's first round (``preflight_model_route``), then
      asks the session's LLM-call caps (``guard_llm_call``) before every
      round; a refusal raises and ends the turn before anything is sent.

    Both session hooks run under the one lock every plan broker of that
    session takes (``teams.agent_broker.session_hook_lock``): parallel roles
    and plan agents charge one session's counters from worker threads.
    """

    def __init__(self, client: Any, model: str | None, usage_hooks: Any) -> None:
        # Lazy: teams.agent_broker imports this module.
        from ..teams.agent_broker import session_hook_lock

        self._record, self._guard = usage_hooks or (None, None)
        self._lock = session_hook_lock(usage_hooks)
        self._preflight = getattr(client, "preflight_model_route", None)
        self._model = model
        self._preflighted = False

    @property
    def charges_session(self) -> bool:
        """True when a lead session's usage records or caps are wired."""
        return self._record is not None or self._guard is not None

    @property
    def active(self) -> bool:
        """False when there is nothing to charge, cap or gate (a fixture client
        with no session): the turn then runs exactly as before."""
        return callable(self._preflight) or self.charges_session

    def round_hooks(self) -> dict[str, Any]:
        """The companion's hook keywords, empty when inactive (legacy factories)."""
        if not self.active:
            return {}
        return {"usage_callback": self.usage_callback, "pre_llm_call": self.pre_llm_call}

    def tool_guard(self, call_type: str) -> None:
        """Before a tool's own model call: the session's caps (not a round,
        and the call names its own model, which the client gates)."""
        if self._guard is not None:
            with self._lock:
                self._guard(call_type)

    def usage_callback(self, call_type: str, payload: dict[str, Any]) -> None:
        if self._record is None:
            return
        try:
            with self._lock:
                self._record(call_type, payload)
        except Exception as exc:  # noqa: BLE001 — telemetry never fails a role turn
            logger.warning("session usage record failed for a team role: %s", exc)

    def pre_llm_call(self, call_type: str) -> None:
        if not self._preflighted and callable(self._preflight):
            self._preflight(self._model)  # ModelRouteRefusedError propagates (ADR-062)
            self._preflighted = True
        if self._guard is not None:
            with self._lock:
                self._guard(call_type)


def _role_reader(client: Any, model: str | None) -> str | None:
    """The model a role turn runs on, which reads what its tools retrieve.

    ``model`` is the role's own, else the team's (the session picks it:
    ``role_models``, else the lead session's model); with neither, the
    companion falls back to the client's configured model, so that model
    reads. Mirrors ``teams.agent_broker.RoleTurnAgentBroker._turn_model``.

    Sizing never costs the turn: a client whose config cannot be read gives
    no reader, and the toolkit's own guard then decides (a client that cannot
    build tools runs the role tool-free, as before C23).
    """
    if model:
        return model
    # Lazy: teams.agent_broker imports this module.
    from ..teams.agent_broker import client_default_model

    try:
        return client_default_model(client)
    except Exception:  # noqa: BLE001 - an unreadable config only loses the sizing
        return None


def run_role_turn(
    session: Any,
    role: Any,
    context: str,
    *,
    system_prompt: str,
    model: str | None,
    max_tool_rounds: int = 8,
    stream: bool = True,
) -> tuple[str, list[RoleActivityEvent]]:
    """Run one role turn as a small agent companion (via ``run_agent_turn``).

    Returns (final text, activity entries). The role's own prompt rides as the
    extra-system (the companion compiles it into its system message); tools
    are the host's catalog minus the team-agent deny set under full parity,
    else the concurrency-safe set. The catalog is built for the model the
    turn runs on (``_role_reader``), so the role's retrieval is sized for
    it (ADR-093 D-14). Activity facts are emitted as they happen through
    ``session._on_agent_activity`` (when wired) and returned.
    """

    state = session.agent_states.get(role.value)
    host = getattr(session, "_permissions", None)
    # ADR-092 MD-7: the plan agents' per-round hooks (the lead session's usage
    # records and caps, the ADR-062 preflight of the role's model).
    hooks = _RoleTurnHooks(session.client, model, getattr(session, "_usage_hooks", None))
    # Role toolkit: full host parity when the session's PermissionManager is
    # wired (2026-10-01); otherwise the concurrency-safe subset.
    tools, _notice = _default_toolkit(
        session.client,
        full_parity=host is not None,
        reader=_role_reader(session.client, model),
        hooks=hooks,
        web_access=getattr(session, "_web_access", None),
    )
    return run_agent_turn(
        session.client,
        label=role.value,
        prompt=context,
        system=system_prompt,
        tools=tools,
        model=model,
        permission_manager=_role_permissions(host, role.value),
        max_tool_rounds=max_tool_rounds,
        on_activity=lambda event: _publish(session, role, event, state),
        factory=_session_factory(session),
        stream=stream,
        **hooks.round_hooks(),
    )


def _publish(session: Any, role: Any, event: RoleActivityEvent, state: Any | None = None) -> None:
    """Fire the session's optional `_on_agent_activity` hook (if wired) and
    keep `AgentState.current_task` honest per turn."""
    if state is not None:
        if event.kind == "tool_call":
            state.current_task = _clip(event.name, 48)
        elif event.kind == "final":
            state.current_task = None
    fire = getattr(session, "_fire_hook", None)
    if callable(fire):  # TeamupSession: lock-serialized (parallel-safe)
        fire("_on_agent_activity", role, event)
        return
    hook = getattr(session, "_on_agent_activity", None)
    if callable(hook):  # duck-typed fixtures
        hook(role, event)
