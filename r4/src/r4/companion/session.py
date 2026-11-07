"""Companion session orchestration and persistent conversation state."""

from __future__ import annotations

import functools
import logging
import os
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from r4.client import Client

    from .sandbox import SandboxContext, SandboxSetting

from . import agent_profile, tool_availability
from .ablation import memory_off
from .agent import CompanionEventType, ResearchCompanion
from .agent_mode import apply_mode, session_mode
from .cancellation import TurnCancellation, is_stopped_reply, response_for_cancelled_turn
from .context import ContextManager
from .endpoint_policy import (
    DEFAULT_ENDPOINT_BLOCK_THRESHOLD_SEC,
    DEFAULT_ENDPOINT_WARNING_THRESHOLD_SEC,
)
from .endpoint_state import format_active_endpoints_prompt
from .env_switch import autonomous, autopilot_server_policy
from .permissions import SAFE_TOOLS, PermissionManager, PermissionMode
from .plan_reply import UNCLEAR_REPLY_NOTICE, classify_plan_reply
from .session_flow import (
    evaluate_turn_endpoint_guard as evaluate_session_turn_endpoint_guard,
)
from .session_flow import (
    finish_turn as finalize_session_turn,
)
from .session_flow import (
    routed_message,
    send_turn,
)
from .session_flow import (
    run_agent_turn as run_session_agent_turn,
)
from .session_flow import (
    start_turn as begin_session_turn,
)
from .session_models import (
    SessionConflictError,
    SessionMessage,
    UsageRecord,
    UsageSummary,
    metadata_project_id,
)
from .session_render import export_markdown, format_duration
from .session_runtime import (
    get_harness,
    harness_post_turn,
    refresh_active_endpoints,
)
from .session_turns import active_tools as select_active_tools
from .session_turns import (
    assemble_extra_system,
    assemble_legacy_extra_system,
    assemble_turn_reminder,
    build_history,
    prepare_turn_payload,
    turn_reminder_with_guard,
)
from .session_usage import (
    guard_llm_call,
    load_usage_state_from_metadata,
    record_usage,
    sync_metadata_usage_state,
    usage_report,
)
from .skills import Skill, SkillRegistry
from .status_report import skills_listed_in_prompt
from .tool_evidence import ToolEvidence
from .tools import get_builtin_tools
from .turn_context import (
    SKILL_REFUSAL,
    join_parts,
    refusals_outside,
    split_fitted,
    stable_prefix_enabled,
)
from .web_access import WebAccess, session_web_access

logger = logging.getLogger(__name__)


def _now_marker(like: str = "") -> str:
    """Now as an offset-carrying ISO string (the snapshot LWW marker).

    A session keeps the offset it already carries (*like*), so a venue worker
    running in UTC stamps the owner's wall clock and every marker of one
    session shares an offset; otherwise this host's local offset. Never at or
    before *like*: a marker a skewed clock stamped ahead of this host stays
    outranked (+1 µs), so a bump always wins last-writer-wins over the copy it
    replaces.
    """
    try:
        previous = datetime.fromisoformat(like.replace("Z", "+00:00")) if like else None
    except ValueError:
        previous = None
    if previous is not None and previous.tzinfo is not None:
        now = datetime.now(previous.tzinfo)
    else:
        now = datetime.now().astimezone()
        # A naive marker was written as this host's local time.
        previous = previous.astimezone() if previous is not None else None
    if previous is not None and now <= previous:
        now = previous + timedelta(microseconds=1)
    return now.isoformat()


DB_PATH = Path(
    os.environ.get("R4_COMPANION_DB") or os.path.expanduser("~/.config/r4/companion/sessions.db")
)


# ADR-104 D-1: the plan card's accept verbs → (the mode the plan runs in,
# the confirmation the execution turn carries).
_PLAN_ACCEPT: dict[str, tuple[str, str]] = {
    "execute_auto": (
        "autopilot",
        "Accepted via plan picker: execute in autopilot (tools auto-approved).",
    ),
    "execute_ask": ("chat", "Accepted via plan picker: execute with manual tool approval."),
}


def _plan_read_only_tools(names: frozenset[str]) -> frozenset[str]:
    """Plan mode read set (ADR-037 amendment): the SAFE tool names minus
    manifest verbs that mutate server state (propose/readiness endpoints).
    Selection (mode_readonly) and execution (plan short-circuit) see
    exactly the same list — one classification, one layer wins."""
    exclude = frozenset({"knowledge_graph_propose"})
    return frozenset(name for name in names if name not in exclude)


_PLAN_MODE_ONLY_SYSTEM = (
    "Plan mode is active. You may read (files, search, docs, catalog, job status) "
    "to ground your plan — do not write, execute workflows, or mutate anything.\n"
    "Return a structured plan (steps, assumptions, risks, open questions). "
    "Do not present the work as completed and do not imply anything was executed.\n"
    "For open-ended or brainstorming requests, frame items as a proposed roadmap, "
    "not a final deliverable."
)
_PLAN_MODE_HEADER = "**Plan mode** — review only. No tools were run.\n\n"
_PLAN_MODE_FOOTER = (
    "\n\n---\n"
    "**Awaiting your approval** — use the plan picker (↑↓) to execute with "
    "auto-approved tools, manual approval, or keep planning."
)


_OUTSIDE_ROOT_ASK = "Ask the user before reading or writing paths outside this root."
_OUTSIDE_ROOT_TRUST = "Stay inside this root unless the task names another path."
_PLATFORM_SKILL_OFF = "they need the R4 platform, which this session cannot reach"


def _outside_root_rule() -> str:
    """H19: an unattended run (``R4_AUTONOMOUS=1``) has nobody to ask, so the
    line must not say "ask the user". Keyed on the run, not on
    ``PermissionMode.TRUST`` (review 2026-10-24): the TUI's autopilot and
    ``--allow-all-tools`` also run TRUST with the user present, and there
    this line is the last guard for paths outside the root."""
    if autonomous() and agent_profile.hygiene_enabled():
        return _OUTSIDE_ROOT_TRUST
    return _OUTSIDE_ROOT_ASK


def _skill_body_omission(client: Any, skill: Skill) -> str:
    """H19: a platform skill's body stays out while its tools are gated
    (no live platform); ``""`` loads it."""
    if not agent_profile.hygiene_enabled() or not tool_availability.is_platform_skill(
        skill.tool_names
    ):
        return ""
    return "" if tool_availability.platform_live(client) else _PLATFORM_SKILL_OFF


def _build_contract_orientation() -> str:
    """G16 startup ritual for the turn block while a build contract is active.

    Workspace state, defensive: any read error is silence, never a blocked turn.
    """
    from .build_contract import contract_orientation

    try:
        return contract_orientation(os.getcwd())
    except Exception:
        logger.debug("build-contract orientation skipped", exc_info=True)
        return ""


def _client_profile(client: Any) -> str | None:
    """The client's profile name, None for a client without one (test doubles)."""
    from .sandbox import profile_of

    return profile_of(getattr(client, "config", None))


def _build_sandbox_context(session: Any) -> SandboxContext:
    """The session's sandbox (ADR-101): its setting and web access read per spawn,
    writes rooted at the session's workspace (the bridge chdirs into it first)."""
    from .sandbox import SandboxContext, safe_user_policy

    profile = _client_profile(getattr(session, "client", None))
    return SandboxContext(
        setting=session.sandbox_setting,
        web_access=session.web_access,
        workspace_root=os.getcwd(),
        user_policy=safe_user_policy(profile),
    )


def _install_first_sandbox(context: SandboxContext) -> None:
    """Install the process's first session context (spawns outside any turn).

    A later session built in the same process (a fork, an export, a pulled
    row, another ACP session) must not swap the installed context. A turn
    does not read it anyway: :meth:`CompanionSession.send` pins its own
    session's context (2026-10-30, review ESC-4).
    """
    from .sandbox import installed_sandbox, set_current_sandbox

    if installed_sandbox() is None:
        set_current_sandbox(context)


def _own_sandbox_pinned(method: Callable[..., str]) -> Callable[..., str]:
    """Run a turn with its session's sandbox pinned, unless one is already.

    ACP keeps many sessions in one process and the bridge switches the
    installed context; a turn must spawn under its own session's setting,
    web access and write root (review ESC-4). A turn nested in another
    run's pin (a team plan, a background subagent) keeps that pin.
    """

    @functools.wraps(method)
    def run(self: Any, *args: Any, **kwargs: Any) -> str:
        from .sandbox import pin_sandbox, pinned_sandbox

        if pinned_sandbox() is not None:
            return method(self, *args, **kwargs)
        with pin_sandbox(self.sandbox_context):
            return method(self, *args, **kwargs)

    return run


def _bind_client_session(client: Any, session_id: str) -> None:
    """D-52: stamp the turn's companion session on the (possibly shared)
    client — loop calls send it as x-r4-session-id, and knowledge mutations
    already read ``client.knowledge.session_id`` (never set before)."""
    try:
        client.companion_session_id = session_id
        knowledge = getattr(client, "knowledge", None)
        if knowledge is not None and hasattr(knowledge, "session_id"):
            knowledge.session_id = session_id
    except Exception as exc:  # binding never blocks a turn
        logger.debug("companion session bind skipped", exc_info=exc)


class CompanionSession:
    """A live conversation session with the companion.

    Orchestrates: agent loop, context management, harness, skills, and
    permission checks on every turn.
    """

    def __init__(
        self,
        session_id: str,
        name: str,
        client: "Client",
        *,
        model: str | None = None,
        permission_mode: PermissionMode = PermissionMode.ASK_ALWAYS,
        endpoint_refresh_interval_sec: int = 30,
        **budget_options: Any,
    ):
        max_llm_calls_per_turn = self._consume_budget_option(
            budget_options,
            "max_llm_calls_per_turn",
        )
        max_llm_calls_per_session = self._consume_budget_option(
            budget_options,
            "max_llm_calls_per_session",
        )
        if budget_options:
            unexpected = ", ".join(sorted(budget_options))
            raise TypeError(f"Unexpected session options: {unexpected}")

        self.session_id = session_id
        self.name = name
        self.client = client
        # Local wall time WITH its offset: the journal orders snapshots by
        # update_marker::timestamptz, which reads a naive value as UTC — a
        # JST workstation's naive 18:41 then outranked a UTC worker's 09:50
        # and every remote-session save was silently refused (2026-09-25).
        self.created_at = _now_marker()
        self.updated_at = self.created_at

        # Messages
        self.messages: list[SessionMessage] = []

        self._init_collaborators(client, permission_mode)
        self._init_budget_state(
            max_llm_calls_per_turn=max_llm_calls_per_turn,
            max_llm_calls_per_session=max_llm_calls_per_session,
            endpoint_refresh_interval_sec=endpoint_refresh_interval_sec,
        )
        self.agent = self._build_agent(client, model)
        # ADR-093 D-9 (r4-harness-6): the tools this turn called. A bare [n]
        # after a search_docs call may be that tool's numbering, so the
        # citation check reads only [S#] labels on such a turn.
        self._turn_tool_names: list[str] = []
        on_event = getattr(self.agent, "on_event", None)
        if callable(on_event):
            on_event(self._note_turn_tool)
        # W6 (D-52): JSONL transcript when R4_TRANSCRIPT_PATH / _DIR is set.
        from .transcript import attach_transcript

        self._transcript = attach_transcript(self)

        # After the agent: tools bind the agent's EFFECTIVE model (self.model
        # else client config) into LLM-backed tool closures — raw self.model
        # alone fell back to the gateway default deployment when unset
        # (2026-09-29 rivault/kimi-k3 503 class). The accessor itself, not its
        # value: the team plan tools read it per call, so a /model switch
        # reaches the next plan's agents, preflight and approval card; tools
        # that bind a model at build time resolve it once (ADR-092 D-6, D-9).
        self.tools = get_builtin_tools(
            client,
            session_model=self.agent._effective_model,
            permission_manager=self.permissions,
            # ADR-092 D-7: a team plan's agents charge this session's usage
            # records and LLM-call caps.
            usage_hooks=(self._record_usage, self._guard_llm_call),
            # ADR-092 slice 3: a plan started in a turn is cancelled with it.
            turn_cancel=self._current_turn_cancel,
            # ADR-099 D-9: web_fetch / web_search follow the user's web-access
            # choice, read at each call (a /web change reaches the next one).
            web_access=self.web_access,
            # K1.4: the knowledge tools label their passages in this
            # session's evidence ledger, so their citations are shown.
            evidence=ToolEvidence(self),
        )
        # ADR-111 D-3: saved-memory tools belong to the lead session only;
        # get_builtin_tools also builds team roles, plan agents and recipes.
        from .memory_tools import build_memory_tools

        self.tools.extend(build_memory_tools(self))
        # H15: the availability gate reads the session's choice per turn and
        # does not offer a web tool that would only refuse.
        for tool in self.tools:
            if tool.get("name") in ("web_fetch", "web_search"):
                tool[tool_availability.WEB_ACCESS_KEY] = self.web_access
        self._append_skill_activation_tool()
        # G18 (2026-10-28): tool_search gets a real backend — the session's
        # full catalog — and every hit opts the discovered tool out of the
        # skill refusal set from the next turn (stable array intact).
        self._tool_search_opt_ins: set[str] = set()
        from .tool_search import register_session_tool_search

        register_session_tool_search(self)
        # G13 (2026-10-29): user/project shell hooks. The project file is
        # opt-in (R4_COMPANION_PROJECT_HOOKS=1) — checked-in hooks are code
        # execution from repo content. Tool dispatchers read the current
        # session's hooks through the module global.
        from .user_hooks import load_user_hooks, register_dispatchers, set_current_user_hooks

        self._user_hooks = load_user_hooks(os.getcwd())
        self.context.user_hooks = self._user_hooks
        register_dispatchers()
        set_current_user_hooks(self._user_hooks)
        self._user_hook_inject = ""
        # ADR-101: the local tool sandbox. Every agent-driven spawn reads the
        # current session's context (process-global, like the user hooks);
        # the setting resolves from metadata, R4_SANDBOX and the profile
        # default at each spawn, so /sandbox reaches the next one.
        from .sandbox import safe_profile_default

        self._sandbox_profile_default = safe_profile_default(_client_profile(client))
        self._sandbox_context = _build_sandbox_context(self)
        _install_first_sandbox(self._sandbox_context)
        # G14 (2026-10-29) / ADR-104 D-3: persistent permission rules — the
        # user's files loosen or tighten, the checked-in file only tightens.
        self.metadata: dict[str, Any] = {}
        self.reload_permission_rules()
        # ADR-108 D-2 (W1c): the session's worktree — /worktree, the agent's
        # worktree_enter/exit tools and auto mode all drive this one controller.
        from .session_worktree import WorktreeController, set_current_controller

        self.worktree = WorktreeController(
            owner=f"session:{session_id}",
            rebind=self.rebind_workspace,
            session_key=str(session_id or ""),
        )
        set_current_controller(self.worktree)

        # Harness (lazy import to avoid circular deps)
        self._harness = None
        self._turn_cancel: TurnCancellation | None = None
        self._model_ready_thread: threading.Thread | None = None
        self._model_ready_error: Exception | None = None

        self._load_usage_state_from_metadata()

        # Store bookkeeping (cycle 3, T1): the row revision this copy was
        # loaded at or last saved as (None: never stored), the digest of the
        # messages the store holds for it, and its conflict copy's id. The
        # copy is a session like any other: its revision and digest as this
        # copy last wrote it guard the next write (it may have been resumed).
        self._store_revision: int | None = None
        self._store_digest = ""
        self._conflict_copy_id = ""
        self._conflict_copy_revision: int | None = None
        self._conflict_copy_digest = ""

    @staticmethod
    def _consume_budget_option(
        budget_options: dict[str, Any],
        name: str,
    ) -> int | None:
        value = budget_options.pop(name, None)
        if value is None:
            return None
        return int(value)

    def _init_collaborators(
        self,
        client: "Client",
        permission_mode: PermissionMode,
    ) -> None:
        self.context = ContextManager(
            client=client,
            usage_callback=self._record_usage,
            pre_llm_call=self._guard_llm_call,
        )
        self.skills = SkillRegistry()
        self.skills.load_user_skills()
        self.skills.apply_defaults(platform_live=tool_availability.platform_live(client))
        self.skills.set_body_filter(lambda skill: _skill_body_omission(client, skill))
        # bug.memo C6 (2026-04-25): unify skill discovery with the MCP
        # server registry so registering an MCP server (via `r4 mcp
        # register` or hatch install) automatically yields a `mcp:<name>`
        # skill the companion can activate.
        try:
            self.skills.load_mcp_skills(client)
        except Exception as e:  # pragma: no cover - best-effort
            logger.debug("load_mcp_skills failed: %s", e)
        self.permissions = PermissionManager(mode=permission_mode)
        self._client_ref = client
        self._wire_trust_checker(client, permission_mode)
        # ADR-076 decision gate (escalate-only); asks the decision model only
        # while /decision-model is on — the check reads the config per call.
        self._turn_user_message = ""
        self.permissions.set_escalation_check(self._decision_tool_gate)
        # Tool defs are wired after the agent exists (session_model thread,
        # B-family 2026-09-27 — tool-internal LLM calls use the chat's model).

    def _append_skill_activation_tool(self) -> None:
        """ADR-037 progressive disclosure loop: the model calls this to pull
        a discovered skill's body. Bodies land from the NEXT turn (extra
        system is rebuilt per send) — the tool is the catalog's escape
        hatch, and the catalog is what funds the model's ability to ask."""
        skills = self.skills

        def activate_skill(skill_name: str) -> str:
            name = str(skill_name or "").strip()
            if not name:
                return "Error: skill_name is required"
            if skills.get(name) is None:
                available = ", ".join(skill.name for skill in skills.list_all())
                return f"Error: unknown skill '{name}'. Discovery catalog: {available}"
            if skills.activate(name):
                return (
                    f"Skill '{name}' activated — full instructions apply from "
                    "the next turn (current turn in flight keeps the old set)."
                )
            return f"Error: skill '{name}' could not be activated"

        self.tools.append(
            {
                "name": "activate_skill",
                "description": (
                    "Load a registered skill's full body from the discovery "
                    "catalog (name — one-line description). Use when the task "
                    "needs a skill the current turn's system prompt does not "
                    "yet carry. Bodies apply from the next turn."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "Skill name exactly as listed in the discovery catalog.",
                        }
                    },
                    "required": ["skill_name"],
                },
                "execute": activate_skill,
            }
        )

    def _wire_trust_checker(self, client: "Client", mode: PermissionMode) -> None:
        """Attach the organisation's autopilot allowlist in TRUST mode (ADR-035).

        Opt-in since ADR-104 amendment 1 (2026-11-03): only when
        ``R4_AUTOPILOT_SERVER_POLICY`` is on and the client exposes
        ``trust_check``. Off (the default), autopilot runs whatever the safety
        floor lets through. Lookup failures fail closed inside
        PermissionManager; wiring failures here are silent by design.
        """
        if not isinstance(mode, PermissionMode) or mode is not PermissionMode.TRUST:
            return
        trust_check = getattr(client, "trust_check", None)
        if not callable(trust_check) or not autopilot_server_policy():
            return
        # The policy lives at project scope (falling back to system on the
        # server); approvals no longer write per-session rows.
        project = metadata_project_id(getattr(self, "metadata", None) or {})
        scope = f"project:{project}" if project else ""
        try:
            self.permissions.set_trust_checker(
                lambda tool_name: bool(trust_check(tool_name, scope=scope).get("allowed"))
            )
        except Exception as e:  # pragma: no cover - best-effort
            logger.debug("trust checker wiring skipped: %s", e)

    def _init_budget_state(
        self,
        *,
        max_llm_calls_per_turn: int | None,
        max_llm_calls_per_session: int | None,
        endpoint_refresh_interval_sec: int,
    ) -> None:
        self.max_llm_calls_per_turn = max_llm_calls_per_turn
        self.max_llm_calls_per_session = max_llm_calls_per_session
        self.endpoint_refresh_interval_sec = endpoint_refresh_interval_sec
        self.endpoint_warning_threshold_sec = int(
            os.environ.get("R4_COMPANION_ENDPOINT_WARNING_THRESHOLD_SEC")
            or str(DEFAULT_ENDPOINT_WARNING_THRESHOLD_SEC)
        )
        self.endpoint_block_threshold_sec = int(
            os.environ.get("R4_COMPANION_ENDPOINT_BLOCK_THRESHOLD_SEC")
            or str(DEFAULT_ENDPOINT_BLOCK_THRESHOLD_SEC)
        )
        self.usage_summary = UsageSummary()
        self.usage_records: list[UsageRecord] = []
        self.active_endpoints: list[dict[str, Any]] = []
        self._current_turn_llm_calls = 0
        self._last_endpoint_refresh_at: str | None = None

    def _build_agent(
        self,
        client: "Client",
        model: str | None,
    ) -> ResearchCompanion:
        return ResearchCompanion(
            client,
            model=model,
            context_manager=self.context,
            skill_registry=self.skills,
            permission_manager=self.permissions,
            usage_callback=self._record_usage,
            pre_llm_call=self._guard_llm_call,
        )

    @property
    def tool_search_opt_ins(self) -> set[str]:
        """G18: tool names discovered via ``tool_search`` this session — they
        leave the skill refusal set on later turns (never the stable array)."""
        return self._tool_search_opt_ins

    @property
    def harness(self):
        """Lazy-init the research harness."""
        self._harness = get_harness(
            current_harness=self._harness,
            client=self.client,
            context_manager=self.context,
            skill_registry=self.skills,
            logger=logger,
        )
        if self._harness is not None:
            # Harness-side decisions (the lesson check) log beside the session's.
            self._harness.decision_log = self.metadata.setdefault("decision_log", [])
            # ADR-111: ``/memory off`` keeps lessons out of this session too.
            self._harness.memory_enabled = self.memory_enabled
        return self._harness

    def memory_enabled(self) -> bool:
        """False after ``/memory off`` (ADR-111): saved memory and lessons
        are neither read nor written in this session."""
        from .memory_commands import SESSION_OFF_KEY

        return not self.metadata.get(SESSION_OFF_KEY)

    def sync_untrusted_mark(self) -> None:
        """ADR-111 D-4: the "untrusted content entered" mark survives a save
        and a resume (metadata ``untrusted_seen``), so memory writes keep
        asking in a resumed session; the store calls it on save and load."""
        registry = getattr(self.agent, "_taint_registry", None) or self.permissions.taint_registry
        if self.metadata.get("untrusted_seen"):
            registry.ever_tainted = True
        elif getattr(registry, "ever_tainted", False):
            self.metadata["untrusted_seen"] = True

    def _wire_plan_gate(self) -> None:
        """T5 topic-24: plan-gate wiring — session metadata → agent attrs at
        turn start (never silently recast: exact-truth values).
        """
        self.agent._plan_gate_required = bool(self.metadata.get("write_requires_plan"))
        plan_state = self.metadata.get("plan_mode_state")
        self.agent._plan_gate_approved = bool(
            isinstance(plan_state, dict) and plan_state.get("approved")
        )

    @_own_sandbox_pinned
    def send(self, user_message: str, *, stream: bool = False) -> str:
        """Send a message and get the agent's response.

        This is the main entry point that orchestrates:
        1. Auto-activate skills from message content
        2. Run harness feedforward (probe, guides, progress orientation)
        3. Build context (memory + skills + harness)
        4. Check compaction
        5. Run the agent loop
        6. Post-turn harness updates (progress, steering)
        7. Save state
        """
        from .model_resolution import ensure_model_ready

        if getattr(self, "_transcript", None) is not None:
            self._transcript.write("turn_start", user_message)
        self._wire_plan_gate()

        # Start ensure_model_ready in background thread.
        # This overlaps the list_endpoints HTTP call with context assembly
        # (skills activation, harness feedforward, memory injection) below.
        self._model_ready_error = None

        def _check_model() -> None:
            try:
                ensure_model_ready(self)
            except Exception as exc:
                self._model_ready_error = exc

        ready_thread = threading.Thread(target=_check_model, daemon=True, name="model-ready")
        self._model_ready_thread = ready_thread
        ready_thread.start()

        interaction_mode = self._interaction_mode()
        plan_state = self._plan_mode_state()

        if interaction_mode != "plan" and plan_state.get("awaiting_confirmation"):
            self._clear_plan_mode_state()

        if interaction_mode == "plan":
            if plan_state.get("awaiting_confirmation"):
                return self._answer_pending_plan(user_message, plan_state, stream=stream)

            response = self._send_plan_only_turn(user_message, stream=stream)
            self._set_plan_mode_state(awaiting_confirmation=True, pending_request=user_message)
            return response

        return self._send_with_flow(user_message, stream=stream)

    def confirm_plan(self, action: str, *, stream: bool = False) -> str:
        """Execute or continue a plan awaiting confirmation.

        ADR-104 D-1: accepting a plan changes the mode, visibly (Claude's
        ExitPlanMode): ``execute_auto`` runs it in autopilot, ``execute_ask``
        in chat (each risky call asks)."""
        if self._interaction_mode() != "plan":
            raise ValueError("plan confirmation is only available in plan mode")
        plan_state = self._plan_mode_state()
        if not plan_state.get("awaiting_confirmation"):
            raise ValueError("no plan awaiting confirmation")

        normalized = str(action or "").strip().lower()
        if normalized == "keep_planning":
            return ""
        accepted = _PLAN_ACCEPT.get(normalized)
        if accepted is None:
            raise ValueError(
                f"unknown plan action: {action} (execute_auto|execute_ask|keep_planning)"
            )
        mode, confirmation = accepted
        return self._execute_approved_plan(
            pending_request=str(plan_state.get("pending_request") or "").strip(),
            confirmation=confirmation,
            stream=stream,
            mode=mode,
        )

    def _execute_approved_plan(
        self, *, pending_request: str, confirmation: str, stream: bool = False, mode: str = "chat"
    ) -> str:
        """Switch to ``mode`` and run an approved plan; a stopped run goes
        back to plan mode with the plan still awaiting confirmation."""
        apply_mode(self, mode, keep_plan=True)
        # T5 topic-24: approval marker — the write_requires_plan gate frees
        # write-family verbs exactly when the plan opens (TURN-started-flow).
        self.metadata["plan_mode_state"] = {
            **(
                self.metadata.get("plan_mode_state")
                if isinstance(self.metadata.get("plan_mode_state"), dict)
                else {}
            ),
            "approved": True,
        }
        # ADR-093 D-8 (r4-harness-4): the execution turn is a harness-built
        # task, not the user's question: it is not routed, so the model is
        # never told to answer "in at most five sentences" instead of acting.
        response = self._send_with_flow(
            self._build_plan_execution_message(
                pending_request=pending_request,
                confirmation=confirmation,
            ),
            stream=stream,
            route=False,
        )
        if is_stopped_reply(response):
            apply_mode(self, "plan", keep_plan=True)
            self._set_plan_mode_state(
                awaiting_confirmation=True,
                pending_request=pending_request,
            )
        else:
            self._clear_plan_mode_state()
        return response

    def _send_with_flow(
        self, user_message: str, *, stream: bool = False, route: bool = True
    ) -> str:
        """Run the standard session send-flow with tools and endpoint guards.
        ``route=False`` skips pre-retrieval (a harness-built task turn)."""
        self._await_model_ready()
        cancel = self._turn_cancel or TurnCancellation()
        owned_cancel = self._turn_cancel is None
        if owned_cancel:
            self._turn_cancel = cancel
        self.agent.set_turn_cancel(cancel)
        try:
            return send_turn(
                user_message=user_message,
                start_turn=self._start_turn,
                evaluate_turn_endpoint_guard=self._evaluate_turn_endpoint_guard,
                prepare_turn_payload=self._prepare_turn_payload,
                run_agent_turn=self._run_agent_turn,
                finish_turn=self._finish_turn,
                stream=stream,
                route_turn=self._route_turn if route else None,
                check_turn=self._check_turn,
            )
        finally:
            self.agent.set_turn_cancel(None)
            if owned_cancel:
                self._turn_cancel = None

    def _route_turn(
        self, user_message: str, question: str | None = None, *, task: bool = False
    ) -> str:
        """ADR-093 D-8: the message the model reads this turn — the server's
        context pack before the question when the router (R4_RAG_ROUTER,
        default off) retrieves; the raw message otherwise. ``task`` frames
        the pack for a plan (no answer instruction or five-sentence recap)."""
        from .retrieval_router import route_session_turn

        return route_session_turn(self, user_message, question=question, task=task)

    def _note_turn_tool(self, event: Any) -> None:
        """Agent listener: record each tool this turn calls (by name)."""
        if getattr(event, "event_type", None) is CompanionEventType.TOOL_CALL:
            data = getattr(event, "data", None) or {}
            self._turn_tool_names.append(str(data.get("name") or ""))

    def _check_turn(self, user_message: str, response: str, stream: bool = False) -> str:
        """ADR-093 D-9: the deterministic citation check (R4_RAG_VERIFY)."""
        from .citation_check import check_session_turn

        return check_session_turn(self, user_message, response, stream=stream)

    def _await_model_ready(self) -> None:
        """Join the background ensure_model_ready thread if running."""
        thread = self._model_ready_thread
        if thread is None:
            return
        self._model_ready_thread = None
        thread.join()
        if self._model_ready_error is not None:
            err = self._model_ready_error
            self._model_ready_error = None
            raise err

    def _plan_tool_subset(self) -> tuple[list[dict[str, Any]], frozenset[str]]:
        """Plan-mode read set (ADR-037 amendment): SAFE minus mutating verbs."""
        read_names = _plan_read_only_tools(SAFE_TOOLS)
        subset = [t for t in self.tools if t.get("name") in read_names]
        return subset, read_names

    def _send_plan_only_turn(
        self,
        user_message: str,
        *,
        agent_message: str | None = None,
        stream: bool = False,
    ) -> str:
        """Plan turn: the model may READ to ground the plan (ADR-037)."""
        self._await_model_ready()
        cancel = self._turn_cancel or TurnCancellation()
        owned_cancel = self._turn_cancel is None
        if owned_cancel:
            self._turn_cancel = cancel
        self.agent.set_turn_cancel(cancel)
        effective_message = agent_message if agent_message is not None else user_message
        try:
            return self._run_plan_only_turn(user_message, effective_message, stream)
        finally:
            self.agent.set_turn_cancel(None)
            if owned_cancel:
                self._turn_cancel = None

    def _run_plan_only_turn(self, user_message: str, effective_message: str, stream: bool) -> str:
        """The plan turn proper. Wave 80: the tools array stays the session's
        stable set — the read-only limit refuses other calls at execution —
        and the plan-mode instruction rides the turn reminder."""
        active_tools = self._start_turn(user_message)
        _, read_names = self._plan_tool_subset()
        # ADR-093 D-8: route before payload preparation, so the payload
        # fit counts what the model reads (the session keeps the raw message).
        # A plan turn is a task: the pack carries no answer frame.
        model_message = routed_message(
            lambda: self._route_turn(user_message, effective_message, task=True)
        )
        if model_message is None:  # stopped during pre-retrieval
            stopped = self._format_plan_mode_response(response_for_cancelled_turn())
            return self._finish_turn(user_message, stopped)
        payload, error_response = self._prepare_turn_payload(
            user_message=model_message,
            endpoint_guard={"state": "ok", "message": ""},
            active_tools=active_tools,
        )
        if error_response is not None:
            return self._finish_turn(user_message, error_response)
        extra_system, history, tools = payload
        response = self._run_agent_turn(
            user_message=model_message,
            history=history,
            extra_system=extra_system,
            # The pre-wave-80 layout offered the whole catalog to the read filter.
            active_tools=tools if stable_prefix_enabled() else self.tools,
            offer_only_names=read_names,
            stream=stream,
            intent_message=effective_message,
            reminder=join_parts(self._turn_reminder, _PLAN_MODE_ONLY_SYSTEM),
        )
        response = self._check_turn(user_message, response, stream)
        return self._finish_turn(user_message, self._format_plan_mode_response(response))

    def _send_plan_mode_notice(self, user_message: str, notice: str) -> str:
        """Record a plan-mode system notice without calling the model."""
        cancel = self._turn_cancel or TurnCancellation()
        owned_cancel = self._turn_cancel is None
        if owned_cancel:
            self._turn_cancel = cancel
        self.agent.set_turn_cancel(cancel)
        try:
            self._start_turn(user_message)
            return self._finish_turn(user_message, notice)
        finally:
            self.agent.set_turn_cancel(None)
            if owned_cancel:
                self._turn_cancel = None

    def _interaction_mode(self) -> str:
        return session_mode(self)

    def permission_project_id(self) -> str:
        """The project whose user rule file binds this session (ADR-104 D-3):
        the session's own project, else the client profile's, else
        ``default`` (the console's name for "no project")."""
        project = metadata_project_id(self.metadata)
        config = getattr(getattr(self, "client", None), "config", None)
        return project or str(getattr(config, "project_id", "") or "") or "default"

    def rebind_workspace(self) -> None:
        """Re-root what was bound to the old working directory (ADR-108 D-2:
        the bridge chdir'd into a worktree, or back). The prompt's workspace
        root is read each turn; hooks, permission rules and the sandbox's
        write root were read at session start."""
        from .user_hooks import load_user_hooks, set_current_user_hooks

        self._user_hooks = load_user_hooks(os.getcwd())
        self.context.user_hooks = self._user_hooks
        set_current_user_hooks(self._user_hooks)
        self._sandbox_context = _build_sandbox_context(self)
        _install_first_sandbox(self._sandbox_context)
        self.reload_permission_rules()

    def reload_permission_rules(self) -> None:
        """Load the G14 rule files for this session's workspace and project
        (the bridge has chdir'd into the workspace before a session exists)."""
        from .permission_rules import load_permission_rules

        self.permissions.ruleset = load_permission_rules(
            os.getcwd(), project_id=self.permission_project_id()
        )

    def _plan_mode_state(self) -> dict[str, Any]:
        raw = self.metadata.get("plan_mode_state")
        if isinstance(raw, dict):
            return raw
        return {}

    def _set_plan_mode_state(self, *, awaiting_confirmation: bool, pending_request: str) -> None:
        self.metadata["plan_mode_state"] = {
            "awaiting_confirmation": awaiting_confirmation,
            "pending_request": pending_request,
        }
        self._sync_metadata_usage_state()

    def _clear_plan_mode_state(self) -> None:
        self.metadata.pop("plan_mode_state", None)
        self._sync_metadata_usage_state()

    def _answer_pending_plan(self, user_message: str, plan_state: dict, *, stream: bool) -> str:
        """A reply to a plan awaiting confirmation (plan_reply.classify_plan_reply):
        only an unmistakable yes runs it; a short no cancels it; hesitation
        asks again with the plan still pending; anything that says more
        revises the plan and asks again."""
        pending_request = str(plan_state.get("pending_request") or "").strip()
        verdict = classify_plan_reply(user_message)
        if verdict in ("revise", "unclear"):
            # ADR-076: the decision model may turn a reply the rules cannot
            # place into a one-word question — never into a run or a cancel.
            notice = self._plan_reply_intent_notice(pending_request, user_message)
            if notice:
                return self._send_plan_mode_notice(user_message, notice)
        if verdict == "execute":
            return self._execute_approved_plan(
                pending_request=pending_request,
                confirmation=user_message,
                stream=stream,
            )
        if verdict == "cancel":
            self._clear_plan_mode_state()
            return self._send_plan_mode_notice(
                user_message,
                "Plan cancelled. Send a new request when you're ready to plan again.",
            )
        if verdict == "unclear":
            return self._send_plan_mode_notice(user_message, UNCLEAR_REPLY_NOTICE)
        revision_prompt = self._build_plan_revision_message(
            pending_request=pending_request,
            user_message=user_message,
        )
        response = self._send_plan_only_turn(
            user_message,
            agent_message=revision_prompt,
            stream=stream,
        )
        self._set_plan_mode_state(awaiting_confirmation=True, pending_request=pending_request)
        return response

    def _decision_tool_gate(self, tool_name: str, arguments: dict) -> str | None:
        """Permission escalation hook: a reason to ask before an auto-approved risky call."""
        from r4.decisions.harness import tool_call_escalation

        log = self.metadata.setdefault("decision_log", [])
        last = log[-1] if log else None
        # W7 (2026-10-05): the turn id scopes the verdict cache to this turn.
        reason = tool_call_escalation(
            self.client,
            tool_name,
            arguments,
            self._turn_user_message,
            log=log,
            turn=getattr(self, "_current_turn_id", None) or None,
        )
        # Each new gate entry (asked / cached / error / first skip) also lands in
        # the transcript, so a decision-model lane can be watched live (2026-10-06).
        if log and log[-1] is not last and getattr(self, "_transcript", None) is not None:
            self._transcript.write("decision", tool_name, log[-1])
        return reason

    def _plan_reply_intent_notice(self, pending_request: str, user_message: str) -> str | None:
        """A confident decision-model reading of the reply, as the question to ask."""
        from r4.decisions.harness import intent_notice, plan_reply_hint

        log = self.metadata.setdefault("decision_log", [])
        return intent_notice(plan_reply_hint(self.client, pending_request, user_message, log=log))

    @staticmethod
    def _is_plan_confirmation(user_message: str) -> bool:
        return classify_plan_reply(user_message) == "execute"

    @staticmethod
    def _is_plan_cancellation(user_message: str) -> bool:
        return classify_plan_reply(user_message) == "cancel"

    @staticmethod
    def _format_plan_mode_response(response: str) -> str:
        body = (response or "").strip()
        if not body:
            return f"{_PLAN_MODE_HEADER.strip()}{_PLAN_MODE_FOOTER}"
        if body.startswith("**Plan mode**"):
            if "**Awaiting your approval**" in body:
                return body
            return f"{body}{_PLAN_MODE_FOOTER}"
        return f"{_PLAN_MODE_HEADER}{body}{_PLAN_MODE_FOOTER}"

    @staticmethod
    def _build_plan_revision_message(*, pending_request: str, user_message: str) -> str:
        base_request = pending_request.strip() or "the previously proposed task"
        return (
            f"{user_message.strip()}\n\n"
            "(Revise your plan for the original request below. Still do not execute tools.)\n\n"
            f"Original request:\n{base_request}"
        )

    @staticmethod
    def _build_plan_execution_message(*, pending_request: str, confirmation: str) -> str:
        base_request = pending_request.strip() or "the previously approved task"
        return (
            "Execution approved. Implement the previously proposed plan now.\n\n"
            f"Original request:\n{base_request}\n\n"
            f"User confirmation:\n{confirmation.strip()}"
        )

    def request_turn_cancel(self) -> None:
        """Stop the in-flight turn, if any."""
        if self._turn_cancel is not None:
            self._turn_cancel.request()

    def web_access(self) -> WebAccess:
        """The user's web-access choice for this session (ADR-099 D-2), else
        ``R4_WEB_ACCESS``; ``mode == ""`` (none) the web tools treat as off."""
        return session_web_access(getattr(self, "metadata", None) or {}, os.environ)

    def sandbox_setting(self) -> SandboxSetting:
        """The session's sandbox setting (ADR-101 D-2, D-11): a venue worker
        or an already sandboxed process first, then the session's own choice
        (the launch flag is stored there when the session opens),
        ``R4_SANDBOX``, the profile default, else off."""
        from .sandbox import resolve_sandbox

        return resolve_sandbox(
            metadata=getattr(self, "metadata", None) or {},
            env=os.environ,
            profile_default=getattr(self, "_sandbox_profile_default", ""),
        )

    def set_sandbox(self, setting: SandboxSetting) -> None:
        """Store a choice as this session's own; it applies from the next spawn."""
        self.metadata["sandbox"] = setting.to_metadata()

    @property
    def sandbox_context(self) -> SandboxContext:
        """The session's :class:`SandboxContext` (made on first use for a
        session built without ``__init__``)."""
        context = getattr(self, "_sandbox_context", None)
        if context is None:
            context = self._sandbox_context = _build_sandbox_context(self)
        return context

    def _current_turn_cancel(self) -> TurnCancellation | None:
        """The running turn's cancellation token, None between turns (the
        team plan tools read it when a plan starts, ADR-092 slice 3)."""
        return getattr(self, "_turn_cancel", None)

    def _start_turn(self, user_message: str) -> list[dict[str, Any]]:
        self._current_turn_id = self._next_turn_id()
        self._turn_user_message = user_message
        self._turn_tool_names = []
        self._user_hook_inject = self._run_prompt_hooks(user_message)
        _bind_client_session(self.client, self.session_id)
        # F7 tail: fresh turns wipe the interrupted marker from the last carry-over
        # (the stay-once class: match the ambient session-layer rule).
        if self.metadata.get("interrupted"):
            self.metadata.pop("interrupted", None)
        (
            self.updated_at,
            self._current_turn_llm_calls,
            active_tools,
        ) = begin_session_turn(
            messages=self.messages,
            user_message=user_message,
            skills=self.skills,
            tools=self.tools,
            updated_at_factory=lambda: _now_marker(self.updated_at),
            logger=logger,
        )
        if self.messages:
            self.messages[-1].metadata["turn_id"] = self._current_turn_id
        # F7 audit fix (2026-09-21): expose a per-turn-start hook so durability
        # owners can persist past this point on crash (previously this row
        # only landed at the post-turn `_store.save` — window uncovered).
        try:
            hook = getattr(self, "_on_turn_started_hook", None)
            if hook is not None:
                hook(self._current_turn_id)
        except SessionConflictError:
            # Another console saved this conversation: running the turn would
            # only produce a reply no save can keep (T1).
            raise
        except Exception as exc:
            logger.debug("on-turn-started hook skipped (never blocks a turn)", exc_info=exc)
        return active_tools

    def _evaluate_turn_endpoint_guard(
        self,
        user_message: str,
        active_tools: list[dict[str, Any]],
    ) -> dict[str, str]:
        return evaluate_session_turn_endpoint_guard(
            user_message=user_message,
            active_tools=active_tools,
            refresh_active_endpoints=self._refresh_active_endpoints,
            get_active_endpoints=lambda: self.active_endpoints,
            warning_threshold_sec=self.endpoint_warning_threshold_sec,
            block_threshold_sec=self.endpoint_block_threshold_sec,
            format_duration=self._format_duration,
        )

    def _prepare_turn_payload(
        self,
        *,
        user_message: str,
        endpoint_guard: dict[str, str],
        active_tools: list[dict[str, Any]],
    ) -> tuple[tuple[str, list[dict[str, Any]], list[dict[str, Any]]] | None, str | None]:
        """Wave 80: ``active_tools`` (the skill-limited set) becomes refusals on
        the session's stable tools array, and the volatile context becomes the
        turn reminder (``self._turn_reminder``) the agent appends to the turn's
        user message. With ``stable_prefix_enabled()`` off, the old layout."""
        skills_prompt, turn_parts = self._turn_parts()
        if not stable_prefix_enabled():
            self._turn_refusals, self._turn_reminder = {}, ""
            legacy = self._legacy_extra_system(skills_prompt, turn_parts)
            return self._fit_turn(
                user_message,
                turn_reminder_with_guard(reminder=legacy, endpoint_guard=endpoint_guard),
                "",
                active_tools,
            )
        stable_tools = select_active_tools(tools=self.tools, active_tool_names=set())
        stable_names = [str(tool.get("name", "")) for tool in stable_tools]
        # G18: tools discovered via tool_search leave the skill refusal set.
        permitted = frozenset(str(tool.get("name", "")) for tool in active_tools)
        permitted = permitted | self._tool_search_opt_ins
        self._turn_refusals = refusals_outside(stable_names, permitted, SKILL_REFUSAL)
        reminder = turn_reminder_with_guard(
            reminder=join_parts(skills_prompt, turn_parts), endpoint_guard=endpoint_guard
        )
        return self._fit_turn(user_message, self._assemble_extra_system(), reminder, stable_tools)

    def _fit_turn(
        self,
        user_message: str,
        stable_system: str,
        reminder: str,
        tools: list[dict[str, Any]],
    ) -> tuple[tuple[str, list[dict[str, Any]], list[dict[str, Any]]] | None, str | None]:
        """Compaction and budget fit; the fitted reminder lands in
        ``self._turn_reminder`` and the payload carries the stable block."""
        payload, error = prepare_turn_payload(
            context_manager=self.context,
            agent=self.agent,
            user_message=user_message,
            base_extra_system=stable_system,
            history=self._build_history(),
            active_tools=tools,
            reminder=reminder,
        )
        if payload is None:
            return None, error
        fitted, history, fitted_tools = payload
        extra_system, self._turn_reminder = split_fitted(fitted, stable_system)
        return (extra_system, history, fitted_tools), None

    def _run_agent_turn(
        self,
        *,
        user_message: str,
        history: list[dict[str, Any]],
        extra_system: str,
        active_tools: list[dict[str, Any]],
        offer_only_names: frozenset[str] | None = None,
        stream: bool = False,
        intent_message: str | None = None,
        reminder: str | None = None,
    ) -> str:
        # H19: the catalog in the prompt already lists the skills (the
        # terminal profile lists none): the project-state block skips them.
        listed = agent_profile.hygiene_enabled() or agent_profile.active()
        with skills_listed_in_prompt() if listed else nullcontext():
            return run_session_agent_turn(
                agent=self.agent,
                user_message=user_message,
                history=history,
                extra_system=extra_system,
                active_tools=active_tools,
                offer_only_names=offer_only_names,
                stream=stream,
                intent_message=intent_message,
                reminder=getattr(self, "_turn_reminder", "") if reminder is None else reminder,
                tool_refusals=getattr(self, "_turn_refusals", None),
            )

    def _finish_turn(self, user_message: str, response: str) -> str:
        if getattr(self, "_transcript", None) is not None:
            self._transcript.write("turn_end", response)
        # The reply carries a strictly newer marker than the turn-start save: a
        # copy taken at turn start (a watcher adoption, the owner's stale row)
        # must never tie with the finished turn — the journal applies ties.
        self.updated_at = _now_marker(self.updated_at)
        result = finalize_session_turn(
            messages=self.messages,
            user_message=user_message,
            response=response,
            post_turn=self._harness_post_turn,
            sync_metadata=self._sync_metadata_usage_state,
        )
        if self.messages:
            self.messages[-1].metadata["turn_id"] = getattr(
                self, "_current_turn_id", self.messages[-1].metadata.get("turn_id", "")
            )
        return result

    def _next_turn_id(self) -> str:
        """ADR-057 D-1: assign one retainable id per session turn at commit.

        Per-session monotonic by the committed-write count (the sequence
        itself lives in `metadata["turn_seq"]` — restored rows never remap
        older ids on resume; ids are never invented for unwritten messages)."""
        seq = int((self.metadata or {}).get("turn_seq") or 0) + 1
        self.metadata["turn_seq"] = seq
        return f"t{seq}"

    @staticmethod
    def _workspace_root_prompt() -> str:
        """Declare the workspace root at the top of the system prompt.

        The bridge process chdirs into --fs-root before creating the
        session, so cwd IS the workspace on every rail (TUI, REPL, tests).
        Without an explicit declaration the agent discovers the root by
        searching — e.g. scanning the user's home directory for keywords.
        The outside-root rule depends on the run (H19: unattended asks nobody).
        """
        root = os.path.realpath(os.getcwd())
        return (
            "## Workspace root\n"
            f"root: {root}\n"
            "Every relative path in this session resolves against this directory. "
            "This directory IS the project workspace — never scan the home "
            "directory or other locations to discover the project. " + _outside_root_rule()
        )

    def _assemble_extra_system(self) -> str:
        """The session-stable extra system block (wave 80: no per-turn state)."""
        # H2: the terminal profile carries no catalog and no research spine.
        lean = agent_profile.active()
        return self._system_block(
            assemble_extra_system(
                skills_discovery_prompt="" if lean else self.skills.discovery_catalog_prompt(),
                platform_guidance=not lean,
            )
        )

    def _legacy_extra_system(self, skills_prompt: str, turn_parts: str) -> str:
        """The pre-wave-80 extra system block (``stable_prefix_enabled()`` off)."""
        lean = agent_profile.active()
        return self._system_block(
            assemble_legacy_extra_system(
                skills_prompt=skills_prompt,
                skills_discovery_prompt="" if lean else self.skills.discovery_catalog_prompt(),
                turn_parts=turn_parts,
                platform_guidance=not lean,
            )
        )

    def _system_block(self, body: str) -> str:
        """The workspace declaration, the session's prompt override, then ``body``."""
        override = self.metadata.get("system_prompt_override")
        if override:
            body = "\n\n".join(part for part in [str(override), body] if part)
        # Workspace declaration first — the agent must know its project root
        # before any other guidance (prevents home-dir discovery scans).
        root = self._workspace_root_prompt()
        return "\n\n".join(part for part in [root, body] if part)

    def _turn_parts(self) -> tuple[str, str]:
        """The per-turn volatile context (wave 80) as (active skill bodies, the
        rest: memory, session facts, harness guides, endpoint state)."""
        turn_parts = assemble_turn_reminder(
            skills_prompt="",
            # Benchmark ablation: R4_HARNESS_ABLATE_MEMORY drops the injection
            memory_context=(
                self.context.memory_as_context()
                if self.memory_enabled() and not memory_off()
                else ""
            ),
            session_context=self.context.session_context_as_prompt(),
            harness=self.harness,
            messages=self.messages,
            active_endpoints_prompt=self._active_endpoints_as_prompt(),
            logger=logger,
        )
        turn_parts = join_parts(turn_parts, self._user_hook_inject, _build_contract_orientation())
        return self.skills.active_system_prompt(), turn_parts

    def _run_prompt_hooks(self, user_message: str) -> str:
        """G13 UserPromptSubmit: context text the session's hooks inject."""
        hooks = self._user_hooks
        if not hooks.any("UserPromptSubmit"):
            return ""
        try:
            return hooks.run("UserPromptSubmit", {"user_message": user_message}).inject
        except Exception:
            logger.warning("UserPromptSubmit hooks failed — continuing turn", exc_info=True)
            return ""

    def _build_history(self) -> list[dict[str, Any]]:
        """Convert stored messages to dicts for the agent."""
        return build_history(self.messages)

    def _harness_post_turn(self, user_message: str, response: str) -> None:
        """Run harness post-turn hooks."""
        harness_post_turn(
            harness=self.harness,
            user_message=user_message,
            response=response,
            messages=self.messages,
            logger=logger,
        )

    def _usage_lock(self) -> threading.RLock:
        """One lock for this session's usage counters (ADR-092 D-7).

        Plan agents charge the session from worker threads while the lead's
        own rounds charge it from the chat thread: both read-modify-write the
        same counters. Created on first use, so sessions built without
        ``__init__`` (tests) still work.
        """
        return self.__dict__.setdefault("_usage_rlock", threading.RLock())

    def _record_usage(self, call_type: str, payload: dict[str, Any]) -> None:
        """Record a single LLM usage event for this session."""
        with self._usage_lock():
            if getattr(self, "_transcript", None) is not None:
                self._transcript.model_round(call_type, payload)
            self._current_turn_llm_calls = record_usage(
                usage_records=self.usage_records,
                usage_summary=self.usage_summary,
                current_turn_llm_calls=self._current_turn_llm_calls,
                call_type=call_type,
                payload=payload,
            )

    def _guard_llm_call(self, call_type: str) -> None:
        """Enforce configured LLM call caps before a model invocation."""
        with self._usage_lock():
            guard_llm_call(
                current_turn_llm_calls=self._current_turn_llm_calls,
                max_llm_calls_per_turn=self.max_llm_calls_per_turn,
                session_llm_calls=self.usage_summary.llm_calls,
                max_llm_calls_per_session=self.max_llm_calls_per_session,
            )

    def _refresh_active_endpoints(self, force: bool = False) -> None:
        """Refresh endpoint state using the public R4 endpoint APIs."""
        self.active_endpoints, self._last_endpoint_refresh_at = refresh_active_endpoints(
            client=self.client,
            active_endpoints=self.active_endpoints,
            last_refreshed_at=self._last_endpoint_refresh_at,
            refresh_interval_sec=self.endpoint_refresh_interval_sec,
            warning_threshold_sec=self.endpoint_warning_threshold_sec,
            block_threshold_sec=self.endpoint_block_threshold_sec,
            force=force,
            logger=logger,
        )

    def _active_endpoints_as_prompt(self) -> str:
        """Format active endpoint state as a prompt fragment."""
        return format_active_endpoints_prompt(
            self.active_endpoints,
            format_duration=self._format_duration,
        )

    def _sync_metadata_usage_state(self) -> None:
        """Persist budget/accounting state into session metadata."""
        sync_metadata_usage_state(
            metadata=self.metadata,
            usage_summary=self.usage_summary,
            usage_records=self.usage_records,
            active_endpoints=self.active_endpoints,
            active_endpoints_updated_at=self._last_endpoint_refresh_at,
        )

    def _load_usage_state_from_metadata(self) -> None:
        """Restore budget/accounting state from session metadata."""
        (
            self.usage_summary,
            self.usage_records,
            self.active_endpoints,
            self._last_endpoint_refresh_at,
        ) = load_usage_state_from_metadata(self.metadata)

    @staticmethod
    def _format_duration(seconds: int) -> str:
        return format_duration(seconds)

    def usage_report(self) -> dict[str, Any]:
        """Return the current aggregate usage report."""
        return usage_report(
            usage_summary=self.usage_summary,
            active_endpoints=self.active_endpoints,
            warning_threshold_sec=self.endpoint_warning_threshold_sec,
            block_threshold_sec=self.endpoint_block_threshold_sec,
        )

    def export_markdown(self) -> str:
        """Export conversation as markdown."""
        return export_markdown(
            name=self.name,
            session_id=self.session_id,
            created_at=self.created_at,
            messages=self.messages,
        )


def __getattr__(name: str) -> Any:
    if name == "CompanionSessionStore":
        from .session_store import CompanionSessionStore

        return CompanionSessionStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
