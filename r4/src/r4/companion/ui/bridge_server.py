"""JSON-lines bridge for the JavaScript companion TUI."""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import select
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, cast

from r4.errors import APIError, RateLimitError
from r4.tui.commands import SUBCOMMANDS, get_slash_registry
from r4.tui.dashboard import collect_backend_snapshot

from ...runtime.science_tracker import ScienceLoop
from ..agent import note_user_rejection
from ..agent_mode import session_mode
from ..agent_registry import AgentRegistry
from ..approval_answers import (
    ALWAYS,
    ONCE,
    REJECT,
    approval_options,
    approves,
    normalize_decision,
)
from ..cancellation import TurnCancellation, TurnCancelledError
from ..checkpoint import CheckpointManager
from ..citation_check import turn_citations
from ..coding import detect_code_context
from ..control_schema import (
    ApprovalRequest,
    BudgetState,
    ThinkingDelta,
    ThinkingDone,
    ToolCallEnd,
    ToolCallStart,
    ToolSelection,
)
from ..control_schema import to_dict as control_event_to_dict
from ..event_log import EventLog
from ..job_manager import JobManager
from ..knowledge_base import KnowledgeBase
from ..knowledge_write_gate import sanitize_acquisition
from ..model_resolution import ensure_model_ready, resolve_initial_model, session_model_info
from ..permission_rules import remove_project_rule, rule_rows
from ..permissions import NEVER_AUTO_APPROVE, approval_fence, approving_agent
from ..quota_notice import RATE_LIMIT_HINT
from ..replay import SessionReplay
from ..resource_monitor import ResourceMonitor
from ..session import CompanionSession
from ..session_models import SessionConflictError
from ..session_store import CompanionSessionStore, SessionCorruptError
from ..tool_summaries import args_summary_for, result_preview_for
from ..web_access import (
    REMOTE_START_UNCHOSEN,
    WebAccess,
    WebAccessError,
    make_web_access,
    remote_start_choice,
)
from ..workflow_policy import load_workflow_policy, policy_denied_error
from .citation_bridge import CitationBridgeMixin
from .command_bridge import command_specs, dispatch_shared_command
from .project_bridge import ProjectBridgeMixin
from .rpc_concurrency import (
    ReadPool,
    RpcClass,
    StateLock,
    rpc_class,
    serves_concurrently,
)
from .sandbox_bridge import (
    SandboxBridgeError,
    SandboxBridgeMixin,
    activate_sandbox,
    launch_sandbox,
    sandbox_state,
)
from .search_server_bridge import SearchServerBridgeMixin
from .session_bus_bridge import attach_bridge_session, detach_bridge_bus, start_bridge_bus
from .slash_manifest import surface_local_commands, v3_local_rows, v3_slash_names
from .team_plan_bridge import TeamPlanBridgeMixin
from .turn_save_bridge import TurnSaveBridgeMixin
from .web_access_bridge import WebAccessBridgeMixin, launch_web_access, take_env_preselection
from .worktree_bridge import WorktreeBridgeMixin

logger = logging.getLogger(__name__)

# The v3 slash rows (packages/r4-tui-v3/src/commands/catalog.manifest.json,
# read through slash_manifest; plan H-5, 2026-10-18, replaces the hand-kept
# V3_LOCAL_SLASH_OWNERS list). SURFACE_LOCAL_UI_COMMANDS: UI chrome each TUI
# owns (manifest companionRow "omit"), kept out of list_slash_commands.
# V3_SLASH_NAMES: every v3 row name and alias; a registry alias among them
# would resolve to the v3 row (and drop the typed arguments when that row
# runs locally), so list_slash_commands never exports it. kb, ckpt, resume
# and q are among them: v3 canonicalizes them (manifest "canon") before
# dispatch. V3_LOCAL_ROWS: rows v3 runs itself in every form; a registry
# alias carried on one would land on the local row too.
SURFACE_LOCAL_UI_COMMANDS = surface_local_commands()
V3_SLASH_NAMES = v3_slash_names()
V3_LOCAL_ROWS = v3_local_rows()

# Tool events surfaced to the JS UI as live notifications (others are noise).
_SURFACED_EVENTS = {
    "tool_call",
    "tool_result",
    "tool_blocked",
    "tool_selection",
    "tools_disabled",
    "error",
    "agent_spawn",
    "agent_result",
    "response_start",
    "response_chunk",
    # Auto-compaction and the overflow/budget trims rewrite earlier turns;
    # v3 shows a keyed "context compacted" notice for it (TUI audit r2 #20).
    "context_compact",
    # A round's closing text. Slices that streamed no chunks (an answer-mode
    # digest after an empty stream) arrive only here; v3 renders the
    # un-streamed remainder (TUI audit r2 #21). A round that streamed text
    # is not re-sent (_done_already_streamed).
    "response_done",
    # Live reasoning → canonical thinking_delta / thinking_done (the v3
    # Thought block; it was fed only on the ACP rail, TUI audit r2 #22).
    "reasoning_delta",
    "reasoning_done",
}
# THINKING events are noise ("Processing your message…") except these kinds:
# the pre-retrieval router's "Retrieved n passages (status)" (ADR-093 D-8),
# and its once-per-session "model scale unknown ..." notice (D-14, C22).
_SURFACED_THINKING_KINDS = frozenset({"context_pack", "model_scale"})

# Remote resume (ADR-099 D-8, RS-1 round 3 N4): when the journal did not
# answer, the attach-time settings push is skipped (the worker keeps the last
# settings sent), and the push itself waits less than a change push does, so
# a stalled gateway cannot run load_session past the TUI's 30 s RPC timeout.
_RESUME_JOURNAL_UNAVAILABLE = "remote journal unavailable; showing cached session"
_RESUME_ADOPTION_FAILED = "remote journal adoption failed; showing cached session"
_RESUME_REFRESH_FAILED = frozenset({_RESUME_JOURNAL_UNAVAILABLE, _RESUME_ADOPTION_FAILED})
_ATTACH_PUSH_TIMEOUT_SEC = 5.0


def _is_surfaced(event_type: Any, data: Any) -> bool:
    if event_type in _SURFACED_EVENTS:
        return True
    kind = data.get("kind") if isinstance(data, dict) else None
    return event_type == "thinking" and kind in _SURFACED_THINKING_KINDS


def _handled_before_forward(server: Any, event_type: Any, message: str) -> bool:
    """Events that never take the generic line: live reasoning (its own
    canonical envelope) and a round's already-streamed closing text."""
    if event_type in ("reasoning_delta", "reasoning_done"):
        server._emit_thinking(event_type, message)
        return True
    return _done_already_streamed(server, event_type, message)


def _done_already_streamed(server: Any, event_type: Any, message: str) -> bool:
    """True for a ``response_done`` that closes a round which streamed text.

    Its message is the post-processed answer (tool scaffold stripped,
    trimmed), not a suffix of the streamed chunks, so v3 would render the
    reply twice (TUI audit r2 #21 review). Only a slice that streamed no
    text (an answer-mode digest after an empty stream) is forwarded. The
    round flag lives on ``server`` (``_round_streamed``).
    """
    if event_type == "response_start":
        server._round_streamed = False
    elif event_type == "response_chunk" and message:
        server._round_streamed = True
    elif event_type == "response_done":
        return bool(getattr(server, "_round_streamed", False))
    return False


def _exported_aliases(catalog_names: set[str]) -> dict[str, list[str]]:
    """Registry aliases per canonical name, minus the ones that resolve to a
    v3 row (or ride a row v3 runs locally) and the ones that are already
    their own catalog row."""
    out: dict[str, list[str]] = {}
    for command in get_slash_registry().command_list():
        if command.name in V3_LOCAL_ROWS:
            continue
        kept = [
            alias
            for alias in command.aliases
            if alias not in V3_SLASH_NAMES and alias not in catalog_names
        ]
        if kept:
            out[command.name] = kept
    return out


def _catalog_entry(spec: Any, aliases: list[str] | None) -> dict[str, Any]:
    """One list_slash_commands row for a command spec."""
    help_text = spec.help
    takes_args = spec.name in SUBCOMMANDS or "[" in help_text or "..." in help_text
    entry: dict[str, Any] = {
        "name": spec.name,
        "help": help_text,
        "category": spec.category,
        "takesArgs": takes_args,
    }
    if spec.name in SUBCOMMANDS:
        entry["subcommands"] = [s.name for s in SUBCOMMANDS[spec.name]]
    if aliases:
        entry["aliases"] = aliases
    return entry


# JSONL bridge wire protocol version. Bump when request/response shapes change
# incompatibly. Clients (Bun TUI, Ink TUI) refuse to start on integer mismatch.
BRIDGE_PROTOCOL_VERSION = 1

MAX_RECENT_MESSAGES = 40
MAX_MESSAGE_CHARS = 6000


def _loop_not_found(loop_id: str, project_id: str) -> dict[str, Any]:
    """research_loop.status payload for a loop the caller cannot see."""
    return {
        "type": "research_loop_status",
        "project_id": project_id,
        "loop_id": loop_id,
        "found": False,
        "status": "not found",
        "message": "no such loop, or not yours (loops are owner-scoped)",
    }


class StdinMultiplexer:
    """Background stdin reader with per-traffic-class routed queues.

    ``approval_response`` and ``ask_user_response`` lines land on DEDICATED
    waiter queues, so the main loop's out-of-band poll can never swallow an
    answer that a blocked ``_read_approval``/``_read_ask_user`` is waiting
    for (starvation class: the busy 20 ms poll won the single-queue draw
    and the approval then waited forever). Cancel-turn and EOF markers are
    duplicated to every queue so a blocked waiter still resolves on cancel
    or shutdown. Stray answers (nobody waiting) simply park on their queue;
    the next waiter skips them by id mismatch.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._approval_queue: queue.Queue[str] = queue.Queue()
        self._ask_user_queue: queue.Queue[str] = queue.Queue()
        self._thread = threading.Thread(target=self._read_loop, daemon=True, name="bridge-stdin")
        self._started = False
        self._closed = threading.Event()

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def _route(self, line: str) -> None:
        if not line:  # EOF — wake every consumer.
            self._closed.set()
            self._queue.put(line)
            self._approval_queue.put(line)
            self._ask_user_queue.put(line)
            return
        message = _decode_json_message(line)
        if message is not None and "approval_response" in message:
            self._approval_queue.put(line)
            return
        if message is not None and "ask_user_response" in message:
            self._ask_user_queue.put(line)
            return
        self._queue.put(line)
        if _is_cancel_turn(message):
            # A blocked waiter must observe the cancel too (it returns its
            # own denial/cancelled marker; the main loop cancels the turn).
            self._approval_queue.put(line)
            self._ask_user_queue.put(line)

    def drop_stale_cancels(self) -> None:
        """Forget cancel_turn copies no blocked waiter consumed.

        A cancel pressed while no approval/ask_user waiter was blocked stays
        parked on the waiter queues; the NEXT turn's first approval wait then
        took it and silently cancelled that turn while its card said "gate
        approved" (stabilization sweep 2026-10-21, chat#0). Called when a
        turn starts: every cancel already queued belongs to an earlier turn
        (the main-queue copy is the authoritative one). Answers and the EOF
        marker stay.
        """
        for waiter_queue in (self._approval_queue, self._ask_user_queue):
            _drop_cancel_lines(waiter_queue)

    def _read_loop(self) -> None:
        while True:
            line = sys.stdin.readline()
            self._route(line)
            if not line:
                return

    @property
    def closed(self) -> bool:
        """True once stdin reached EOF (or the parent watchdog fired)."""
        return self._closed.is_set()

    @staticmethod
    def _take(q: "queue.Queue[str]", timeout: float | None) -> str | None:
        try:
            line = q.get() if timeout is None else q.get(timeout=timeout)
        except queue.Empty:
            return None
        if not line:
            # EOF is sticky: a mid-turn poll that takes the marker must not
            # hide it from the main loop, which then blocked in get() forever
            # (companion sweep 2026-10-21: an orphaned bridge per closed TUI).
            q.put(line)
            return None
        return line

    def get(self, timeout: float | None = None) -> str | None:
        """Main-traffic lines (RPC requests, cancel signals)."""
        return self._take(self._queue, timeout)

    def get_approval(self) -> str | None:
        """Blocking read of the next approval_response line (None on EOF)."""
        return self._take(self._approval_queue, None)

    def get_ask_user(self) -> str | None:
        """Blocking read of the next ask_user_response line (None on EOF)."""
        return self._take(self._ask_user_queue, None)


def _stdin_closed(mux: Any) -> bool:
    """True once the bridge's stdin reached EOF (False without a mux)."""
    return bool(getattr(mux, "closed", False))


def _drop_cancel_lines(waiter_queue: "queue.Queue[str]") -> None:
    """Remove queued cancel_turn lines, keeping every other line in order."""
    kept: list[str] = []
    while True:
        try:
            line = waiter_queue.get_nowait()
        except queue.Empty:
            break
        if line and _is_cancel_turn(_decode_json_message(line)):
            continue
        kept.append(line)
    for line in kept:
        waiter_queue.put(line)


@dataclass
class CollectingRenderer:
    """Renderer adapter that captures command output for the JS UI."""

    lines: list[dict[str, str]] = field(default_factory=list)
    cleared: bool = False
    # Prompts a command hands back as chat turns (/code edit, /agent chat,
    # /search, /retry): the TUI sends each as a normal streamed turn instead
    # of the command running a whole turn under its HTTP timeout.
    follow_up_messages: list[str] = field(default_factory=list)
    # Turn ids a command cut from the session (/retry drops the last turn
    # before resending it): the TUI removes those transcript rows too.
    dropped_turn_ids: list[str] = field(default_factory=list)

    def info(self, text: str) -> None:
        self.lines.append({"kind": "info", "text": str(text)})

    def system(self, text: str) -> None:
        self.lines.append({"kind": "system", "text": str(text)})

    def error(self, text: str, *, correlation_id: str | None = None) -> None:
        suffix = f"  (cid={correlation_id})" if correlation_id else ""
        self.lines.append({"kind": "error", "text": f"{text}{suffix}"})

    def clear(self) -> None:
        self.cleared = True
        self.lines.clear()


def _queue_remote_body(params: dict[str, Any]) -> dict[str, Any]:
    """F9 queue_remote params → POST body (has owner-attribution stamps).
    Raises ValueError for unknown action — never recasts silently.
    """
    action = (str(params.get("action") or "").strip() or "").lower()
    if action not in {"append", "pop", "clear"}:
        raise ValueError("session.queue_remote requires action=append|pop|clear")
    body: dict[str, Any] = {"action": action}
    content = str(params.get("content") or "").strip()
    if content:
        body["content"] = content
    client_id = str(params.get("client_id") or "").strip()
    if client_id:
        body["client_id"] = client_id
    return body


def _thread_host_usage_hooks(host: Any, team: Any) -> None:
    """ADR-092 MD-7: a team's role rounds charge the host chat session's usage
    records and LLM-call caps, as a team plan's agents do. Their web tools
    follow the host session's web-access choice (ADR-099 D-9)."""
    record = getattr(host, "_record_usage", None)
    guard = getattr(host, "_guard_llm_call", None)
    if callable(record) and callable(guard):
        team._usage_hooks = (record, guard)
    web_access = getattr(host, "web_access", None)
    if callable(web_access):
        team._web_access = web_access


class CompanionBridgeServer(
    ProjectBridgeMixin,
    TeamPlanBridgeMixin,
    WebAccessBridgeMixin,
    SearchServerBridgeMixin,
    WorktreeBridgeMixin,
    SandboxBridgeMixin,
    CitationBridgeMixin,
    TurnSaveBridgeMixin,
):
    """Small request dispatcher used by the JavaScript terminal UI."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        store: CompanionSessionStore | None = None,
        session_id: str | None = None,
        model: str | None = None,
        skill: str | None = None,
        system: str | None = None,
        source_name: str | None = None,
        allow_all_tools: bool = False,
        interaction_mode: str | None = None,
        fs_root: str | None = None,
        web_access: str | None = None,
        sandbox: str | None = None,
    ) -> None:
        self._client = client
        self._team_client = None
        self._store = store or CompanionSessionStore()
        self._session: CompanionSession | None = None
        self._initial_session_id = session_id
        self._initial_model = model
        self._initial_skill = skill
        self._initial_system = system
        self._source_name = source_name
        self._allow_all_tools = allow_all_tools
        self._interaction_mode = (interaction_mode or "").strip().lower() or None
        self._fs_root = fs_root
        # ADR-099 D-2: --web-access / R4_LAUNCH_WEB_ACCESS (r4 --web) for
        # sessions that have no choice yet; the console asks when there is
        # none. An ambient R4_WEB_ACCESS only preselects that question.
        self._web_access_preset = launch_web_access(web_access)
        self._web_access_preselect = take_env_preselection()
        # ADR-101 D-2: --sandbox / R4_LAUNCH_SANDBOX (r4 --sandbox), taken out
        # of the environment so nothing the agent starts inherits it.
        self._sandbox_preset = launch_sandbox(sandbox)
        self.should_exit = False
        self._stdin_mux: StdinMultiplexer | None = None
        self._active_turn_cancel: TurnCancellation | None = None
        # Interactive tool approval / ask_user coordination (server -> client).
        self._approval_seq = 0
        self._ask_user_seq = 0
        # ADR-092 D-6: parallel agents ask at once; an unlocked += then read
        # handed two waiters the same request id.
        self._seq_lock = threading.Lock()
        # Pending approval waiters (request_id -> reply queue). Replies are
        # DELIVERED into these queues by every reader path (routed mux queue,
        # main-loop OOB stray branch) so a waiting approval can never starve.
        self._pending_approvals: dict[int, "queue.Queue[str]"] = {}
        self._subscribed_agent_id: int | None = None
        # The current stream round already showed text (response_done dedup).
        self._round_streamed = False
        # Persistent event log (same DB as the session store).
        raw_db_path = getattr(self._store, "db_path", None)
        # Only a real filesystem path is valid. Mocked stores pass
        # isinstance(x, os.PathLike) too (a MagicMock auto-creates
        # __fspath__), so the discriminator is the CONCRETE type real
        # stores actually hold (str or pathlib.Path) — anything else falls
        # back to in-memory SQLite, or str(mock.db_path) creates a junk
        # '<MagicMock ...>' database file in the working directory.
        db_path = raw_db_path if isinstance(raw_db_path, (str, Path)) else ":memory:"
        self._event_log = EventLog(str(db_path))
        # Background job manager (same DB as the session store).
        self._job_manager = JobManager(str(db_path))
        # HPC resource monitor (GPU/CPU/RAM/Slurm).
        self._resource_monitor = ResourceMonitor()
        # Checkpoint manager for crash recovery.
        self._checkpoint_mgr = CheckpointManager(str(db_path))
        # Session replay from event log.
        self._replay = SessionReplay(self._event_log)
        # Science loop state tracker.
        self._science_loop = ScienceLoop(str(db_path))
        # Knowledge base for patterns and lessons.
        self._knowledge_base = KnowledgeBase(str(db_path))
        # Guarded sqlite path for every later ad-hoc construction — with a
        # mock store this is ':memory:', and a ':memory:' TaskGraph PER
        # handler is an empty world each time (list-after-add has no
        # table), so the task handlers share ONE instance like the other
        # managers above. str(mock.db_path) NEVER becomes a junk file in
        # the working directory again (d360e6a2 covered the ctor sites;
        # the task-graph handlers leaked for another year... by name:
        # <MagicMock name='mock.db_path' id=...> strays).
        from ..task_graph import TaskGraph

        self._sqlite_db_path = str(db_path)
        self._task_graph = TaskGraph(self._sqlite_db_path)
        # (session id, todo-write integer id) → task-graph uuid (session
        # plan mirror; keyed per session so /new starts an empty plan).
        self._plan_task_ids: dict[tuple[str, int], str] = {}
        # Live agent registry for the TUI's agent panel.
        self._agent_registry = AgentRegistry()
        self._project_jobs: dict[str, Any] = {}
        self._output_lock = threading.Lock()
        # Plan H-8: reads answer from a small pool (during a turn too) under
        # the shared side of the state lock; mutations hold the exclusive side.
        self._state_lock = StateLock()
        self._read_pool = ReadPool()
        # The JSONL wire main() binds once: redirect_stdout elsewhere in the
        # process (tool output capture, CLI wrappers) must never swallow a reply.
        self._wire: Any = None
        # Async teamup iteration guard (B-family 2026-09-27): one run per
        # session, tracked so a second `teamup.run` refuses by name.
        # Bootstrap-in-flight flag (teamup.start, B-family — the tailor meta
        # call must never starve the single-flight RPC mailbox).
        self._teamup_bootstrapping = False

    def _rebuild_client_after_config_reload(self) -> None:
        """Drop the cached Client and propagate the fresh one into the
        companion session + agent (B-family badge bug, 2026-09-27:
        `project_switch` flipped the manager but the session kept the
        spawn-time client — snapshot/badge and chat stayed on the old
        project forever). Tool-layer clients (context/trust) follow the
        session on later waves; the switch's chat-journal flush covers the
        discontinuity (wave-intercepts clears chatMessages + sessions)."""
        self._client = None
        session = getattr(self, "_session", None)
        if session is None:
            return
        try:
            session.client = self.client
            agent = getattr(session, "agent", None)
            if agent is not None:
                agent.client = session.client
        except Exception:  # noqa: BLE001 — a rebuild hiccup must not veto the switch
            logger.debug("post-switch client propagation failed", exc_info=True)

    def list_slash_commands(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return companion slash catalog (shared ops only).

        Excludes Bun lifecycle subcommands (never in shared metadata) and
        surface-local UI chrome (clear/health/palette/quit) so each TUI owns
        those handlers locally. Registry aliases ride each entry; the typed
        alias reaches dispatch_slash_command unchanged and the registry
        resolves it.
        """
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        specs = command_specs()
        aliases = _exported_aliases({spec.name for spec in specs})
        for spec in specs:
            if spec.name in seen or spec.name in SURFACE_LOCAL_UI_COMMANDS:
                continue
            seen.add(spec.name)
            out.append(_catalog_entry(spec, aliases.get(spec.name)))
        return {"commands": out}

    @property
    def client(self) -> Any:
        if self._client is None:
            from ...client import Client

            self._client = Client()
        return self._client

    @property
    def session(self) -> CompanionSession:
        if self._session is None:
            self.initialize({})
        if self._session is None:  # pragma: no cover - initialize always sets it
            raise RuntimeError("companion session is not initialized")
        return self._session

    def _open_session(
        self,
        *,
        session_id: str | None,
        explicit_model: str | None,
        skill: str | None,
        system_prompt: str | None,
        venue: str | None = None,
    ) -> CompanionSession:
        if session_id:
            session = self._store.load(str(session_id), self.client)
            if session is None:
                raise ValueError(f"session not found: {session_id}")
            self._apply_resume_venue(session, venue)
        else:
            session = self._create_with_venue(explicit_model, venue)

        if explicit_model:
            session.agent.model = str(explicit_model)
        elif not getattr(session.agent, "model", None):
            resolved = resolve_initial_model(self.client, explicit=None, saved=None)
            if resolved:
                session.agent.model = resolved
        if skill:
            session.skills.activate(str(skill))
        if system_prompt:
            session.metadata["system_prompt_override"] = str(system_prompt)
        # ADR-108 W1c: the console's chip follows a worktree the agent enters.
        self._attach_worktree_events(session)
        return session

    @staticmethod
    def _apply_resume_venue(session: CompanionSession, venue: str | None) -> None:
        """First resume of a pre-venue session: record the declared venue.

        Never overwrites an already-recorded venue (migration-by-verb stays
        plan-explicit, not silent).
        """
        if venue and not session.metadata.get("venue"):
            from ..session_store import validate_venue

            session.metadata["venue"] = validate_venue(venue)

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create or load the active companion session.

        Additive venue parameter (plan 2026-09-09): the TUI picker declares
        ``venue: local | frontend | cluster:<name>`` at session start. On
        create it is recorded; on first resume of a pre-venue session it is
        filled in; an already-recorded venue is never silently re-written.
        """
        session = self._open_session(
            session_id=params.get("session_id") or self._initial_session_id,
            explicit_model=params.get("model") or self._initial_model,
            skill=params.get("skill") or self._initial_skill,
            system_prompt=params.get("system") or self._initial_system,
            venue=params.get("venue") or self._profile_default_venue(),
        )
        self._apply_initial_web_access(session, params.get("web_access"))
        self._apply_initial_sandbox(session)
        self._session = session
        self._configure_session(session)
        notice = self._attach_push(session, params)
        session = self._session  # a rejoin may have adopted the journal's copy
        # F12: register mirror poster/client so state-change hooks ride the
        # mirror immediately instead of waiting for the next save(). Test
        # harnesses may own a lighter store that lacks set_mirror_poster; it's
        # never compatible-with-harness-conditional to require it.
        setter = getattr(self._store, "set_mirror_poster", None)
        if callable(setter):
            setter(getattr(session.client, "post", None), session.client)
        snapshot = self.snapshot()
        # Workspace root is the launch cwd (--fs-root chdir ran first); the TUI
        # greets with it so users see the agent's operating directory at boot.
        snapshot["workspace_root"] = os.getcwd()

        # F19 audit fix (2026-09-21): background-threaded save failures will
        # currently die silently — record them against the last_sync lane so
        # sync_status reads them honestly (`mode: degraded`, never silent).
        def _safe_init_save() -> None:
            try:
                self._store.save(session)
            except Exception as exc:
                logger.exception("init save failed (silently lost before)")
                try:
                    self._last_sync = {"mode": "degraded", "error": str(exc)[:120]}
                except Exception:
                    pass

        threading.Thread(
            target=_safe_init_save,
            daemon=True,
            name="bridge-init-save",
        ).start()
        snapshot["bridge_protocol"] = BRIDGE_PROTOCOL_VERSION
        snapshot.update(_notice_field(notice))
        # Round-3 task 7: source identity for stale-process detection —
        # the TUI recomputes client-side and warns on mismatch.
        try:
            from r4.source_identity import current_source_identity

            snapshot["source"] = current_source_identity().to_dict()
        except Exception:  # pragma: no cover - degrade gracefully
            snapshot["source"] = None
        return snapshot

    def _profile_default_venue(self) -> str | None:
        """Profile-persisted default venue (`r4 config context --default-venue`).

        Convenience only (§8a-1): an explicit initialize ``venue`` param wins;
        missing/invalid persisted values fall back to the create-time default
        ('local') without failing the boot.
        """
        try:
            from ...config import ProfileConfigStore
            from ..session_store import validate_venue

            profile = str(getattr(getattr(self._client, "config", None), "profile", "default"))
            stored = ProfileConfigStore().load(profile).get("session_default_venue")
            if isinstance(stored, str) and stored:
                return validate_venue(stored)
        except Exception:  # noqa: BLE001 — venue default must never block boot
            logger.debug("default venue lookup failed; falling back to local")
        return None

    def _create_with_venue(self, explicit_model: str | None, venue: str | None) -> CompanionSession:
        """Create branch — venue kwarg only when declared (pre-venue store
        doubles stay signature-safe; store default is 'local')."""
        venue_kwargs: dict[str, str] = {"venue": venue} if venue else {}
        return self._store.create_session(
            self.client,
            model=str(explicit_model) if explicit_model else None,
            **venue_kwargs,
        )

    def _configure_session(self, session: CompanionSession) -> None:
        """Apply launch flags and wire approval/event plumbing to a session."""
        mode = self._interaction_mode
        if mode:
            session.metadata["interaction_mode"] = mode

        self._apply_permission_mode(session)
        reload_rules = getattr(session, "reload_permission_rules", None)
        if callable(reload_rules):  # ADR-104 D-3: this session's project
            reload_rules()
        self._apply_knowledge_acquisition(session)
        self._apply_workflow_arm(session)
        self._scope_session_changes(session)
        # ADR-101: the active session's sandbox is the one every spawn reads
        # (loading another session, a fork say, must not swap it).
        activate_sandbox(session)
        session.permissions.set_approval_callback(self._on_tool_approval)
        try:
            from r4.agent.tools import set_ask_user_callback, set_plan_mirror

            set_ask_user_callback(self._on_ask_user)
            set_plan_mirror(self._on_plan_items)
        except Exception:  # pragma: no cover - never block session init
            logger.exception("failed to wire ask_user callback")
        self._register_main_agent(session)
        self._subscribe_events(session)
        # ADR-114: the active session is the one on the local session bus.
        attach_bridge_session(self, session)

    def _apply_knowledge_acquisition(self, session: CompanionSession) -> None:
        """Mirror the session-scoped acquisition mode onto the shared client.

        The mode lives in session.metadata (survives resume of THIS session);
        tool guards read the shared client facade, so it is re-applied at
        every session configure (open default when never set).
        """
        from r4.companion.knowledge_write_gate import sanitize_acquisition

        constraints = sanitize_acquisition(session.metadata.get("knowledge_acquisition"))
        knowledge = getattr(session.client, "knowledge", None)
        if knowledge is not None:
            knowledge.write_constraints = dict(constraints)

    def _scope_session_changes(self, session: CompanionSession) -> None:
        """Start the file-change registry clean when another session takes
        over this bridge, so the Changes overlay and its revert never act on
        a previous session's edits (TUI audit #69). A reload of the same
        session keeps them."""
        from ..tools import reset_session_changes

        if getattr(self, "_changes_session_id", None) != session.session_id:
            reset_session_changes()
            self._changes_session_id = session.session_id

    def _apply_workflow_arm(self, session: CompanionSession) -> None:
        """Mirror the session's workflow arm onto the shared tool guards
        (ADR-075 G2; same facade pattern as the knowledge-write gate)."""
        from ..tools import set_workflow_armed_flag

        set_workflow_armed_flag(bool(session.metadata.get("workflow_armed")))

    def set_workflow_armed(self, params: dict[str, Any]) -> dict[str, Any]:
        """Session-scoped workflow arm (ADR-075 G2, user switchable).

        Params: ``armed`` (bool; absent = read current). Persisted in
        session.metadata (survives resume of THIS session); while armed the
        compute-submit guards refuse the non-UMRS lane by name.
        """
        session = self.session
        if "armed" not in params:
            return {
                "workflow_armed": bool(session.metadata.get("workflow_armed")),
                "state": self.snapshot(),
            }
        armed = bool(params.get("armed"))
        session.metadata["workflow_armed"] = armed
        self._apply_workflow_arm(session)
        self._store.save(session)
        self._push_venue_settings(session)
        return {"workflow_armed": armed, "state": self.snapshot()}

    def _apply_permission_mode(self, session: CompanionSession, *, launch: bool = True) -> None:
        """Apply the session's agent settings (ADR-099 D-8: one rule, local
        and remote — ``r4.companion.agent_settings``).

        The permission mode follows the session's one mode (ADR-104 D-1).
        Resumed sessions persist their mode in metadata; the launch flag
        (and ``--allow-all-tools``) wins only at launch (``launch=True``).
        """
        from ..agent_settings import apply_agent_settings

        apply_agent_settings(session, self._agent_settings(session, launch=launch))

    def _agent_settings(self, session: CompanionSession, *, launch: bool = False) -> dict[str, Any]:
        """The settings document this bridge runs ``session`` with."""
        from ..agent_settings import configured_ask_user_mode, effective_agent_settings

        return effective_agent_settings(
            session,
            launch_mode=(self._interaction_mode or "") if launch else "",
            # ``--allow-all-tools`` launches in autopilot; a later /mode wins.
            allow_all_tools=bool(self._allow_all_tools) and launch,
            ask_user_mode=configured_ask_user_mode(
                getattr(getattr(self, "_client", None), "config", None)
            ),
        )

    def _push_venue_settings(
        self, session: Any = None, *, force: bool = False, timeout: float | None = None
    ) -> str:
        """Send this session's agent settings to its remote worker (ADR-099
        D-8): before a remote start (``force``) and after every change while
        the session is remote. "" when sent or not remote, else the reason
        (best-effort: the worker keeps the same rule's defaults). No
        ``session`` means the live one."""
        from ...venue_settings import try_push_venue_agent_settings

        session = session if session is not None else getattr(self, "_session", None)
        if session is None or not (force or self._session_is_frontend()):
            return ""
        sid = str(getattr(session, "session_id", "") or "")
        reason = try_push_venue_agent_settings(
            self._client, sid, self._agent_settings(session), timeout=timeout
        )
        if reason:
            logger.warning("agent settings not sent to the remote session %s: %s", sid, reason)
        return reason

    def _register_main_agent(self, session: CompanionSession) -> None:
        """Inject the live agent registry so sub-agents appear in the TUI panel."""
        agent = getattr(session, "agent", None)
        if (
            agent is not None
            and self._agent_registry is not None
            and hasattr(agent, "agent_id")
            and hasattr(agent, "_agent_registry")
        ):
            agent._agent_registry = self._agent_registry
            self._agent_registry.register(
                agent_id=agent.agent_id,
                parent_id=None,
                name="main",
                task="",
            )

    def _subscribe_events(self, session: CompanionSession) -> None:
        agent = getattr(session, "agent", None)
        on_event = getattr(agent, "on_event", None)
        if not callable(on_event):
            return
        if self._subscribed_agent_id == id(agent):
            return
        on_event(self._forward_event)
        self._subscribed_agent_id = id(agent)

    # -- server -> client notifications -------------------------------------

    def _emit_line(self, payload: dict[str, Any]) -> None:
        """Write one JSON line to the client (a reply or a notification).

        One write per line under the output lock, so lines from the stdin
        thread, the read pool and the turn thread never interleave. The bound
        wire (main()) wins over ``sys.stdout``, which another thread may have
        redirected for a moment (plan H-8).
        """
        try:
            encoded = json.dumps(payload, default=str)
            with self._output_lock:
                out = self.__dict__.get("_wire") or sys.stdout
                out.write(encoded + "\n")
                out.flush()
        except Exception:  # pragma: no cover - never let UI plumbing crash a turn
            pass

    def _emit_cost(self) -> None:
        """C3: same session/cost payload contract as the ACP rail, emitted
        on the JSONL bridge as an event {"type": "cost"} at turn end."""
        from ..cost_event import cost_event_payload

        cursor = getattr(self, "_cost_cursor", 0)
        envelope, next_cursor = cost_event_payload(self.session, cursor)
        if envelope is None:
            return
        self._cost_cursor = next_cursor
        try:
            self._emit_line(envelope)
        except Exception:
            pass

    def _forward_event(self, event: Any) -> None:
        """Forward companion tool events to the JS UI as live notifications."""
        event_type = getattr(getattr(event, "event_type", None), "value", None)
        data = getattr(event, "data", None) or {}
        if not _is_surfaced(event_type, data):
            return
        if _handled_before_forward(self, event_type, str(getattr(event, "message", ""))):
            return
        agent_id = data.get("agent_id")
        parent_agent_id = data.get("parent_agent_id")
        # Persist to event log for replay/recovery/audit
        try:
            is_tool_event = event_type in {"tool_call", "tool_result"}
            self._event_log.append(
                session_id=self.session.session_id,
                event_type=event_type,
                message=str(getattr(event, "message", "")),
                data=data,
                agent_id=agent_id,
                parent_agent_id=parent_agent_id,
                tool_name=data.get("name") if is_tool_event else None,
                tool_status=data.get("status") if is_tool_event else None,
                tool_duration_ms=(data.get("duration_ms") if is_tool_event else None),
            )
        except Exception:  # pragma: no cover - never let logging crash a turn
            pass
        # Canonical T1 transparency lines alongside legacy {"event": {...}}
        # (same dual-shape window as approvals: consumers tolerate unknown
        # top-level keys; legacy line stays during the window).
        if event_type == "tool_selection":
            self._emit_canonical_tool_selection(data)
        else:
            self._emit_canonical_tool_event(event_type, data, str(getattr(event, "message", "")))
        # Emit to stdout for JS UI
        self._emit_line(
            {
                "event": {
                    "type": event_type,
                    "message": str(getattr(event, "message", "")),
                    "data": data,
                }
            }
        )

    def _emit_thinking(self, event_type: str, text: str) -> None:
        """Canonical thinking envelope for one reasoning delta or the block end.

        Deltas are appended client-side (true deltas, not snapshots); ``seq``
        orders them within the open block. Not written to the event log: one
        line per streamed token would swamp it.
        """
        session_id = self._session_id_str()
        if event_type == "reasoning_done":
            self.__dict__["_thinking_seq"] = 0
            self._emit_line(control_event_to_dict(ThinkingDone(session_id=session_id)))
            return
        seq = self.__dict__.get("_thinking_seq", 0)
        self.__dict__["_thinking_seq"] = seq + 1
        event = ThinkingDelta(session_id=session_id, seq=seq, text=text, partial=True)
        self._emit_line(control_event_to_dict(event))

    def _emit_canonical_tool_event(
        self, event_type: str, data: dict[str, Any], message: str
    ) -> None:
        """Emit canonical T1 tool-call envelopes for tool_call/tool_result.

        Same contract as the ACP rail (args_summary sanitized at emission,
        GAP-COMP-4). Legacy event line above stays for the deprecation window.
        """
        if event_type == "tool_call":
            self._emit_canonical_tool_start(data)
            return
        if event_type == "tool_result":
            self._emit_canonical_tool_end(data, message)
            return
        if event_type == "tool_blocked" and data.get("tool_call_id"):
            # A denied call ends too: its tool_call_start stayed open forever
            # (companion sweep 2026-10-21).
            self._emit_canonical_tool_end({**data, "status": "error"}, message)

    def _emit_canonical_tool_selection(self, data: dict[str, Any]) -> None:
        """Canonical ADR-037 envelope for the per-step selection record."""
        self._emit_line(control_event_to_dict(self._tool_selection_event(data)))

    def _tool_selection_event(self, data: dict[str, Any]) -> "ToolSelection":
        """Build the validated dataclass from the agent-side record.

        session_id is attached here (producer-side the agent has no session
        handle); field names map 1:1 from the record, validation happens in
        the dataclass __post_init__ (strategy enum, count invariants).
        """
        return ToolSelection(
            session_id=self._session_id_str(),
            **self._coerce_selection_fields(data),
        )

    # coercion table: (record key, coercer, default)
    _SELECTION_FIELD_TABLE = (
        ("step_index", int, 0),
        ("available_tools", int, 0),
        ("candidate_tools", int, 0),
        ("selection_latency_ms", int, 0),
        ("discovered", dict, lambda: {}),
        ("filtered_out", dict, lambda: {}),
        ("schema_tokens_estimate", dict, lambda: {}),
        ("selected_tools", list, lambda: []),
        ("selected_skills", list, lambda: []),
        ("warnings", list, lambda: []),
        ("would_filter", dict, lambda: {}),
    )

    @staticmethod
    def _coerce_selection_fields(data: dict[str, Any]) -> dict[str, Any]:
        """Typed record→dataclass coercion, table-driven to keep the
        emitter's branch count behavioral rather than per-field."""
        fields: dict[str, Any] = {}
        for name, coercer, default in CompanionBridgeServer._SELECTION_FIELD_TABLE:
            value = data.get(name)
            fallback = default() if callable(default) else default
            fields[name] = coercer(value) if value is not None else fallback
        fields["turn_id"] = str(data.get("turn_id") or "")
        fields["strategy"] = str(data.get("strategy") or "passthrough")
        fields["ranking"] = data.get("ranking")
        return fields

    def _emit_canonical_tool_start(self, data: dict[str, Any]) -> None:
        name = str(data.get("name") or "")
        risk_tier = "never_auto" if name in NEVER_AUTO_APPROVE else "risky"
        event = ToolCallStart(
            session_id=self._session_id_str(),
            tool_call_id=str(data.get("tool_call_id") or ""),
            name=name,
            args_summary=args_summary_for(name, _arguments_dict(data.get("arguments"))),
            risk_tier=risk_tier,
        )
        self._emit_line(control_event_to_dict(event))
        # Live usage update: the round's usage record lands BEFORE its tools
        # execute (agent records at stream end, then dispatches tool calls),
        # so tool-start is the earliest point carrying fresh context numbers —
        # a long-running tool shows the updated ctx chip before it runs, not
        # after. One emit per tool: start/end payloads are identical within a
        # round (no new usage record lands between them).
        self._emit_cost()

    def _emit_canonical_tool_end(self, data: dict[str, Any], message: str) -> None:
        name = str(data.get("name") or "")
        status = data.get("status") or "done"
        if status not in {"done", "error", "cancelled"}:
            status = "error" if status in {"failed", "fail"} else "done"
        event = ToolCallEnd(
            session_id=self._session_id_str(),
            tool_call_id=str(data.get("tool_call_id") or ""),
            name=name,
            status=status,
            duration_ms=data.get("duration_ms"),
            result_preview=result_preview_for(name, message),
        )
        self._emit_line(control_event_to_dict(event))

    @staticmethod
    def _read_current(path: str) -> str | None:
        try:
            p = Path(path).expanduser()
            return p.read_text(errors="replace") if p.is_file() else ""
        except OSError:
            return None

    def _on_tool_approval(self, tool_name: str, description: str, arguments: dict) -> bool:
        """Ask the console to approve a tool call; block until it replies.

        ADR-104 D-3: the console renders the ask and returns one of the
        offered answers; remembering an answer happens here, never there.
        """
        intent_id = self._register_approval_intent(tool_name, description, arguments)
        options = approval_options(tool_name, fenced=bool(approval_fence()))
        with self.__dict__.setdefault("_seq_lock", threading.Lock()):
            self._approval_seq += 1
            request_id = self._approval_seq
        self._emit_approval_request(request_id, tool_name, description, arguments, options)
        reply_q: "queue.Queue[str]" = queue.Queue()
        registry = self.__dict__.setdefault("_pending_approvals", {})
        registry[request_id] = reply_q
        try:
            decision = normalize_decision(self._await_approval(request_id, reply_q), options)
        finally:
            registry.pop(request_id, None)
        self._remember_decision(tool_name, decision)
        self._decide_approval_intent(intent_id, _INTENT_WORDS[decision])
        if not approves(decision) and not self._turn_cancelled():
            # A person answered "no" (not a turn cancel): the agent names it
            # as their rejection, not a policy block (chat#8).
            note_user_rejection(tool_name)
        return approves(decision)

    def _rules_project_id(self) -> str:
        """The project whose user rule file ``/settings rules`` manages."""
        project_of = getattr(getattr(self, "_session", None), "permission_project_id", None)
        return str(project_of() or "") if callable(project_of) else ""

    def _remember_decision(self, tool_name: str, decision: str) -> None:
        """Keep an "always" for this session (ADR-104 amendment 1: in memory
        only; it no longer writes a self-granted server trust row, which the
        autopilot check read back slowly and only when the write succeeded)."""
        if decision == ALWAYS:
            self.session.permissions.approve_for_session(tool_name)

    def _emit_approval_request(
        self,
        request_id: int,
        tool_name: str,
        description: str,
        arguments: dict[str, Any],
        options: list[str] | None = None,
    ) -> None:
        """The approval ask: canonical envelope + legacy line.

        ADR-092 slice 3 (MD-5): a never-auto tool is answered per call by a
        human; the console must not remember a yes for the session or a
        project rule for it. Fix 2026-10-30: neither may it for an ask a
        content fence raised on this call's arguments (``fenced``, with the
        fence's reason in ``fence_reason``), whatever the tool.
        """
        risk_tier = "never_auto" if tool_name in NEVER_AUTO_APPROVE else "risky"
        fence = approval_fence()
        # ADR-092 slice 2: the agent whose call this is ("" for the lead).
        agent = approving_agent()
        # opencode parity: the ask carries the SERVER-COMPUTED diff so the
        # approval card shows the actual change before the human answers
        # (their permission EditBody). Legacy-envelope-only fields, additive.
        preview = _approval_preview(tool_name, arguments or {})
        # Canonical T1 control-schema envelope (GAP-COMP-4) alongside the
        # legacy line. Dual-shape for one deprecation window: JSONL consumers
        # ignore unknown top-level keys, so old clients stay unaffected until
        # they adopt the canonical envelope.
        self._emit_line(
            control_event_to_dict(
                ApprovalRequest(
                    request_id=request_id,
                    tool=tool_name,
                    description=description,
                    arguments=dict(arguments or {}),
                    risk_tier=risk_tier,
                    session_id=self._session_id_str(),
                    agent=agent,
                    fenced=bool(fence),
                    fence_reason=fence,
                )
            )
        )
        self._emit_line(
            {
                "approval_request": {
                    "id": request_id,
                    "tool": tool_name,
                    "description": description,
                    "agent": agent,
                    "risk_tier": risk_tier,
                    "session_approval_allowed": risk_tier != "never_auto" and not fence,
                    "fenced": bool(fence),
                    "fence_reason": fence,
                    # ADR-104 D-3: the answers this ask offers (the console
                    # renders exactly these; it never answers by itself).
                    "options": options or approval_options(tool_name, fenced=bool(fence)),
                    **preview,
                }
            }
        )

    def _turn_cancelled(self) -> bool:
        cancel = getattr(self, "_active_turn_cancel", None)
        return cancel is not None and cancel.is_set()

    def _register_approval_intent(
        self, tool_name: str, description: str, arguments: dict
    ) -> str | None:
        """G-P6-2 extension (ADR-057 leg 3 client leg): every approval the
        UI asks is ALSO a durable intent row on the server, so a second
        client of the same session sees it even if this process dies.
        Best-effort — unreachable rows are never faked as pending."""
        try:
            from uuid import uuid4

            cfg = getattr(self._client, "config", None)
            gateway_url = getattr(cfg, "gateway_url", "") if cfg else ""
            session_id = self._session_id_str()
            if not gateway_url or not session_id:
                return None
            turn_id = str(getattr(self.session, "_current_turn_id", "") or "")
            intent_id = f"appr-{uuid4().hex[:12]}"
            body = {
                "turn_id": turn_id,
                "action": {
                    "tool": tool_name,
                    "description": description,
                    "arguments": dict(arguments or {}),
                },
                "approval_id": intent_id,
            }
            from ..session_store import journal_prefix

            prefix = journal_prefix(self._client)
            self._client.post(
                f"{prefix}/companion/sessions/{session_id}/approvals",
                json=body,
                timeout=10,
            )
            return intent_id
        except Exception:  # noqa: BLE001 — durable write is best-effort
            logger.debug("approval intent registration skipped", exc_info=True)
            return None

    def _decide_approval_intent(self, intent_id: str | None, decision: str) -> None:
        """Post the terminal decision onto the durable row (best-effort)."""
        if not intent_id:
            return
        try:
            cfg = getattr(self._client, "config", None)
            gateway_url = getattr(cfg, "gateway_url", "") if cfg else ""
            session_id = self._session_id_str()
            if not gateway_url or not session_id:
                return
            from ..session_store import journal_prefix

            prefix = journal_prefix(self._client)
            self._client.post(
                f"{prefix}/companion/sessions/{session_id}/approvals/{intent_id}/decision",
                json={"decision": decision},
                timeout=10,
            )
        except Exception:  # noqa: BLE001 — durable write is best-effort
            logger.debug("approval decision write skipped", exc_info=True)

    def _on_ask_user(self, question: str, options: list[str]) -> str:
        """Ask the JS UI a clarifying question; block until the user answers.

        The user's ``ask_user_mode=auto`` answers without asking, with the
        same text a remote session uses (ADR-099 D-8)."""
        from ..agent_mode import session_mode
        from ..agent_settings import ask_user_auto_answer, configured_ask_user_mode

        config = getattr(getattr(self, "_client", None), "config", None)
        automatic = session_mode(getattr(self, "_session", None)) == "autopilot"
        if automatic or configured_ask_user_mode(config) == "auto":
            return ask_user_auto_answer()
        with self.__dict__.setdefault("_seq_lock", threading.Lock()):
            self._ask_user_seq += 1
            request_id = self._ask_user_seq
        self._emit_line(
            {
                "ask_user_request": {
                    "id": request_id,
                    "question": question or "",
                    "options": [str(o) for o in (options or []) if str(o).strip()],
                }
            }
        )
        return self._read_ask_user(request_id)

    def _await_approval(self, request_id: int, reply_q: "queue.Queue[str]") -> str:
        """Wait for the decision of one pending approval.

        opencode parity: replies are DELIVERED into the pending queue by
        every stdin reader path instead of one blocked reader racing the
        main loop for lines (the starve class). Turn cancel resolves every
        pending queue with "no" so no waiter outlives its turn.
        """
        while True:
            try:
                return reply_q.get(timeout=0.05)
            except queue.Empty:
                pass
            self._drain_approval_lines()
            cancel = self._active_turn_cancel
            if (cancel is not None and cancel.is_set()) or _stdin_closed(self._stdin_mux):
                return "no"

    def _drain_approval_lines(self) -> None:
        """Deliver queued approval_response lines to their pending waiters."""
        mux = self._stdin_mux
        if mux is None:
            try:
                readable = bool(select.select([sys.stdin], [], [], 0)[0])
            except (OSError, ValueError):
                readable = False  # captured/no-fd stdin (tests, pipes w/o fd)
            if readable:
                line = sys.stdin.readline()
                if line:
                    self._deliver_stdin_line(line)
            return
        while True:
            line = mux._take(mux._approval_queue, 0)
            if line is None:
                return
            self._deliver_stdin_line(line)

    def _deliver_stdin_line(self, line: str) -> None:
        """Route one decoded stdin line to the matching pending approval."""
        message = _decode_json_message(line)
        if message is None:
            return
        if _is_cancel_turn(message):
            self.cancel_turn({})
            return
        self._deliver_approval(message)

    def _deliver_approval(self, message: dict[str, Any]) -> bool:
        """Deliver one decoded approval_response to its waiter (False=stray)."""
        response = message.get("approval_response")
        if not isinstance(response, dict):
            return False
        try:
            rid = int(response.get("id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        reply_q = self.__dict__.get("_pending_approvals", {}).get(rid)
        if reply_q is None:
            return False  # stray — parked on the routed queue, never blocks
        reply_q.put(str(response.get("decision") or "no").strip().lower())
        return True

    def _read_ask_user(self, request_id: int) -> str:
        """Wait for the matching ask_user answer on its OWN pending queue
        (approval-wave discipline, 2026-10-01): concurrent waiters (parallel
        role agents included) can never starve each other — every stdin
        reader path DELIVERS lines to the queue matching the request id."""
        if self._stdin_mux is None:
            return self._read_ask_user_stdin(request_id)
        reply_q: "queue.Queue[str]" = queue.Queue()
        registry = self._pending_ask_users()
        registry[request_id] = reply_q
        try:
            return self._await_ask_user(request_id, reply_q)
        finally:
            registry.pop(request_id, None)

    def _read_ask_user_stdin(self, request_id: int) -> str:
        """Legacy single-consumer stdin loop (no mux): only one waiter lives."""
        while True:
            line = sys.stdin.readline()
            answer = self._parse_ask_user_answer(line, request_id)
            if answer is not None:
                return answer

    def _pending_ask_users(self) -> "dict[int, queue.Queue[str]]":
        registry = self.__dict__.get("_pending_ask_users_dict")
        if registry is None:
            registry = {}
            self.__dict__["_pending_ask_users_dict"] = registry
        return registry

    def _await_ask_user(self, request_id: int, reply_q: "queue.Queue[str]") -> str:
        while True:
            try:
                return reply_q.get(timeout=0.05)
            except queue.Empty:
                pass
            self._drain_ask_user_lines(request_id)
            if _stdin_closed(self._stdin_mux):
                return "[ask_user cancelled: bridge closed]"
            cancel = self._active_turn_cancel
            if cancel is not None and cancel.is_set():
                return "[ask_user cancelled]"

    def _drain_ask_user_lines(self, request_id: int) -> None:
        """Deliver queued ask_user_response lines to their pending waiters."""
        mux = self._stdin_mux
        if mux is None:
            return
        while True:
            line = mux._take(mux._ask_user_queue, 0)
            if line is None:
                return
            if line == "":
                # EOF: every pending waiter gets the honest cancelled marker
                # (same contract as _parse_ask_user_answer's falsy line).
                for q in list(self._pending_ask_users().values()):
                    q.put("[ask_user cancelled: bridge closed]")
                return
            self._deliver_ask_user_line(line, request_id)

    def _deliver_ask_user_line(self, line: str, request_id: int = -1) -> bool:
        """Route one decoded ask_user_response to its waiter (False=stray)."""
        message = _decode_json_message(line)
        if message is None:
            return False
        response = message.get("ask_user_response")
        if not isinstance(response, dict):
            return False
        try:
            rid = int(response.get("id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        reply_q = self._pending_ask_users().get(rid)
        if reply_q is None:
            return False  # stray — parked, never blocks
        reply_q.put(str(response.get("answer") or "").strip() or "")
        return True

    def _parse_approval_decision(self, line: str | None, request_id: int) -> str | None:
        """Return a decision if *line* matches the approval request, else None."""
        if not line:
            return "no"  # EOF/empty input — deny by default
        message = _decode_json_message(line)
        if message is None or "ask_user_response" in message:
            return None  # Ignore noise / ask_user traffic while waiting
        response = message.get("approval_response")
        if _matching_response_id(response, request_id):
            return str(response.get("decision") or "no").strip().lower()
        if _is_cancel_turn(message):
            self.cancel_turn({})
            return "no"
        return None  # Ignore other traffic while waiting

    def _parse_ask_user_answer(self, line: str | None, request_id: int) -> str | None:
        """Return an answer if *line* matches the ask_user request, else None."""
        if not line:
            return "[ask_user cancelled: bridge closed]"
        message = _decode_json_message(line)
        if message is None or "approval_response" in message:
            return None  # Ignore noise / approval traffic while waiting
        response = message.get("ask_user_response")
        if _matching_response_id(response, request_id):
            answer = response.get("answer")
            return "" if answer is None else str(answer)
        if _is_cancel_turn(message):
            self.cancel_turn({})
            return "[ask_user cancelled]"
        return None

    def snapshot(self, *, max_messages: int = MAX_RECENT_MESSAGES) -> dict[str, Any]:
        """Return a bounded UI snapshot for the active session.

        Reads ONE session object throughout (plan H-8: get_state is a pool
        read, and a swap of the active session must not mix two sessions in
        one snapshot); lists are sliced copies, never live references.
        """
        session = self.session
        model_info = session_model_info(session)
        plan_awaiting, plan_pending = _plan_mode_view(session)
        identity_username, identity_credential = _session_identity_hint(session)
        return {
            "session": {
                "id": session.session_id,
                "name": session.name,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "model": model_info["model"],
                "profile_default": model_info["profile_default"],
                "model_provisioned": model_info["provisioned"],
                "needs_model_selection": model_info["needs_selection"],
                "permission_mode": session.permissions.mode.value,
                # ADR-035 badge honesty: server-enforced allowlist vs legacy
                # process-global TRUST. TUI renders trust(server)|trust(legacy).
                "trust_source": getattr(session.permissions, "trust_source", ""),
                "interaction_mode": str(session.metadata.get("interaction_mode") or "chat"),
                "plan_awaiting_confirmation": plan_awaiting,
                "plan_pending_request": plan_pending,
                # F9-lite audit fix: chat-queue content rides the snapshot
                # (TUI resume replay re-hydrates pendingChatQueue on-didi).
                # Server-lane dict rows give their content, never a repr
                # (TUI audit #71).
                "chat_queue": list(filter(None, self._queue_meta(session))),
                # T5 topic-24 gate visibility (never fabricated).
                "write_requires_plan": bool(session.metadata.get("write_requires_plan")),
                # Session-scoped knowledge-acquisition mode (mid-session
                # switchable; enforcement server-side in the tool guards).
                "workflow_armed": bool(session.metadata.get("workflow_armed")),
                "knowledge_acquisition": sanitize_acquisition(
                    session.metadata.get("knowledge_acquisition")
                ),
                # Wave 2026-09-21 (ADR-046 A2): venue + remote runtime projection
                # so every snapshot consumer (initialize/load/send/get_state)
                # knows whether turns execute in the sandbox.
                "venue": str(session.metadata.get("venue") or "local"),
                "venue_runtime": _venue_runtime_meta(session),
                # ADR-099 D-2: the user's web-access choice (mode "" = not
                # chosen yet: the console asks before the next turn).
                "web_access": WebAccess.from_metadata(session.metadata).to_metadata(),
                # ADR-101 D-1/D-12: the local tool sandbox and its notice line
                # ("remote" for a frontend session, no notice).
                "sandbox": sandbox_state(session),
            },
            "config": {
                "profile": getattr(session.client.config, "profile", "default"),
                "gateway_url": getattr(session.client.config, "gateway_url", ""),
                "project_id": getattr(session.client.config, "project_id", ""),
                "source_name": self._source_name or "",
                # display-only identity (never authoritative) for the TUI
                # session identity chip: account@project when a JWT decodes,
                # otherwise the credential state ("api-key" | "none") so the
                # chip renders "key-only(profile)" / "signed-out" (2026-10-07).
                "username": identity_username,
                "credential": identity_credential,
            },
            # F10 audit fix (2026-09-21): total_messages + message_window
            # surface in the resume path — chatMessages replays rows can't
            # silently silent-trim anymore (window never implied whole-session).
            "total_messages": len(session.messages),
            "message_window": max_messages,
            "messages": [_message_to_dict(message) for message in session.messages[-max_messages:]],
            "skills": self._skills_snapshot(session),
            "usage": session.usage_report(),
            "active_endpoints": list(getattr(session, "active_endpoints", [])),
            "progress": self._progress_snapshot(session),
        }

    def send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        text = str(params.get("text") or "").strip()
        if not text:
            raise ValueError("message text is required")
        self._end_plan_revision()
        if self._session_is_frontend():
            # ADR-046 A2: frontend sessions never execute locally — the turn
            # rides the owner-scope queue to the sandbox worker.
            return self._send_remote(text, params)
        self._ensure_session_current()
        ensure_model_ready(self.session)
        cancel = self._start_turn_cancel()

        self.session._on_turn_started_hook = self._on_turn_started
        turn_before = getattr(self.session, "_current_turn_id", None)
        try:
            response, cancelled = self._run_cancellable(
                lambda: self.session.send(text, stream=True), cancel
            )
        finally:
            self.session._turn_cancel = None
            self._active_turn_cancel = None
            self.session._on_turn_started_hook = None
        if cancelled:
            # F7 tail: persist the interrupted marker on session metadata so
            # replays/exports never read a bare "(Generation stopped.)" as text.
            self.session.metadata["interrupted"] = True
        self._emit_budget_state()
        self._emit_cost()
        self._save_after_turn()
        if not cancelled:
            self._maybe_emit_auto_title()
        return self._turn_result(response, cancelled, turn_before)

    def _save_after_turn(self) -> None:
        """The end-of-turn save. A conflict here comes after the model and the
        tools ran: it says so (stage ``after_turn``), so no client asks for
        the same message again and runs its tools twice (cycle 3 review)."""
        try:
            self._store.save(self.session)
        except SessionConflictError as exc:
            raise exc.at_stage("after_turn") from exc

    def _start_turn_cancel(self) -> TurnCancellation:
        """Open a turn's cancellation scope. Cancels queued for an earlier
        turn are dropped first, so they cannot cancel this one (chat#0)."""
        self._await_stopped_turn()
        mux = getattr(self, "_stdin_mux", None)
        if mux is not None:
            mux.drop_stale_cancels()
        cancel = TurnCancellation()
        self._active_turn_cancel = cancel
        self.session._turn_cancel = cancel
        return cancel

    def _await_stopped_turn(self) -> None:
        """Let a stopped turn's worker finish before the next turn starts.

        A stop answers the TUI at once, but the worker can still be unwinding
        (a provider that has not sent a byte yet lets go only at its read
        timeout). A second turn on the same agent then ran beside it: the old
        turn's "(Generation stopped.)" record landed after the new reply,
        carrying the new reply's text, and its cleanup cleared the new turn's
        cancel token (companion sweep 2026-10-21). Wait a bounded time, then
        refuse by name.
        """
        worker = self.__dict__.get("_turn_worker")
        if worker is None or not worker.is_alive():
            return
        worker.join(_STOPPED_TURN_GRACE_SEC)
        if worker.is_alive():
            raise RuntimeError(
                "the stopped turn is still finishing (its model request has not "
                "returned yet) — send again in a moment"
            )

    def _turn_result(self, response: str, cancelled: bool, turn_before: Any) -> dict[str, Any]:
        """send_message's result. ``turn_id`` names the turn this message
        opened, so the TUI can tag its row for a later session.truncate
        (TUI audit r2 #17); absent when no turn started."""
        result: dict[str, Any] = {"response": response, "state": self.snapshot()}
        turn_id = getattr(self.session, "_current_turn_id", None)
        if turn_id and turn_id != turn_before:
            result["turn_id"] = str(turn_id)
            citations = turn_citations(self.session, str(turn_id))
            if citations:
                result["citations"] = citations
        if cancelled:
            result["cancelled"] = True
        suggestion = _suggest_next_slash(response)
        if suggestion:
            result["suggested_next"] = suggestion
        return result

    def _ensure_session_current(self) -> None:
        """Refuse a turn on a copy another console has saved over (T1): it
        could not be saved afterwards. Stores without CAS have no check.

        Every local turn passes here: a sandbox swap held while a background
        team plan ran (ADR-101, :func:`activate_sandbox`) is retried first."""
        activate_sandbox(self.session)
        check = getattr(self._store, "ensure_current", None)
        if not callable(check):
            return
        try:
            check(self.session)
        except SessionConflictError as exc:
            raise exc.at_stage("before_turn") from exc

    def _begin_plan_revision(self) -> None:
        """keep_planning: the plan stays pending (the next message revises
        it), but the card and its a/m/e/n keys step aside so that message can
        be typed (TUI audit #70)."""
        plan_state = self.session.metadata.get("plan_mode_state")
        if isinstance(plan_state, dict) and plan_state.get("awaiting_confirmation"):
            plan_state["revising"] = True

    def _end_plan_revision(self) -> None:
        """A new turn answers the pending plan; its card returns after it."""
        plan_state = self.session.metadata.get("plan_mode_state")
        if isinstance(plan_state, dict):
            plan_state.pop("revising", None)

    def confirm_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        action = str(params.get("action") or "").strip().lower()
        if not action:
            raise ValueError("action is required")
        if action == "keep_planning":
            self._begin_plan_revision()
            return {"response": "", "state": self.snapshot()}
        self._end_plan_revision()
        self._ensure_session_current()
        ensure_model_ready(self.session)
        cancel = self._start_turn_cancel()
        try:
            response, cancelled = self._run_cancellable(
                lambda: self.session.confirm_plan(action, stream=True),
                cancel,
            )
        finally:
            self.session._turn_cancel = None
            self._active_turn_cancel = None
        self._emit_budget_state()
        self._emit_cost()
        self._save_after_turn()
        if not cancelled:
            self._maybe_emit_auto_title()
        result: dict[str, Any] = {"response": response, "state": self.snapshot()}
        if cancelled:
            result["cancelled"] = True
        return result

    def _maybe_emit_auto_title(self) -> None:
        """Background one-shot: name a still-default session from its first
        completed turn (console tabs show the topic, not the timestamp)."""
        if getattr(self, "_auto_title_started", False):
            return
        session = self._session
        if session is None:
            return
        from ..auto_title import is_default_name

        if not is_default_name(getattr(session, "name", "")):
            return
        self._auto_title_started = True
        threading.Thread(
            target=self._auto_title_worker, daemon=True, name="bridge-auto-title"
        ).start()

    def _auto_title_worker(self) -> None:
        """Ask the model for a title outside the turn path; publish when it lands."""
        from ..auto_title import generate_session_title, is_default_name

        try:
            session = self._session
            if session is None:
                return
            request_chat = getattr(getattr(session, "agent", None), "_chat", None)
            title = generate_session_title(session, request_chat)
            if not title:
                return
            # Apply-time re-check: a manual rename must win over a stale ask.
            if not is_default_name(getattr(session, "name", "")):
                return
            session.name = title
            self._store.save(session)
            self._emit_line(
                {
                    "session_title": {
                        "session_id": self._session_id_str(),
                        "name": title,
                        "auto": True,
                    }
                }
            )
        except Exception as exc:  # noqa: BLE001 — titles are best-effort
            logger.debug("auto title worker failed: %s", exc)

    def cancel_turn(self, _params: dict[str, Any]) -> dict[str, Any]:
        if self._active_turn_cancel is not None:
            self._active_turn_cancel.request()
            self.session.request_turn_cancel()
            self._emit_turn_stopped()
        # A cancelled turn must never leave an approval waiter parked.
        for reply_q in self.__dict__.get("_pending_approvals", {}).values():
            reply_q.put("no")
        return {"ok": True}

    def _emit_turn_stopped(self) -> None:
        self._emit_line({"turn_stopped": {"ok": True}})

    def _park_oob_request(self, line: str) -> None:
        """Park a mutating RPC that arrived mid-turn (2026-10-06; reads answer
        mid-turn since plan H-8); it runs, in arrival order, once the turn has
        replied: the stdin loop drains it via :meth:`_drain_oob_parked`."""
        self.__dict__.setdefault("_oob_parked", []).append(line)

    def _drain_oob_parked(self) -> None:
        """Answer every parked mid-turn RPC as the main loop would have, FIFO.

        Called by the stdin loop AFTER the turn's own reply is written (cycle
        3, L2: it ran inside the turn's ``finally``, so a ``send_message``
        sent mid-turn ran nested and replied before the first turn did). A
        parked turn may park more lines; they queue behind the rest. A line
        failing never stops the drain.
        """
        parked = self.__dict__.setdefault("_oob_parked", [])
        while parked and not getattr(self, "should_exit", False):
            line = parked.pop(0)
            try:
                response = _handle_bridge_line(self, line)
                if response is not None:
                    self._emit_line(response)
            except Exception:  # noqa: BLE001 — drain is best-effort
                logger.exception("parked mid-turn request failed to drain")

    def _session_id_str(self) -> str:
        return str(getattr(self.session, "session_id", "") or "")

    def _on_turn_started(self, turn_id: str) -> None:
        """F7 hooked-save: user row's commit lands mid-flight at this point —
        crash-window eliminated for the user message + turn_seq bump.

        Then name the turn to the TUI (``turn_opened``) so it tags the user
        row right away: a turn that is cancelled or fails afterwards is still
        in the history, and edit-and-resend / /retry cut it by this id
        (TUI audit r2 #17 review).

        A conflict here stops the turn before the model is asked
        (``before_turn``; CompanionSession._start_turn re-raises it).
        """
        try:
            self._store.save(self.session)
        except SessionConflictError as exc:
            raise exc.at_stage("before_turn") from exc
        if turn_id:
            self._emit_line(
                {"event": {"type": "turn_opened", "message": "", "data": {"turn_id": str(turn_id)}}}
            )

    def _usage_int(self, attr: str) -> int:
        usage = getattr(self.session, "usage_summary", None)
        return int(getattr(usage, attr, 0) or 0)

    def _last_prompt_tokens(self) -> int:
        records = getattr(self.session, "usage_records", None) or []
        if not records:
            return 0
        return int(getattr(records[-1], "prompt_tokens", 0) or 0)

    def _context_limit(self) -> int:
        context = getattr(self.session, "context", None)
        return int(getattr(context, "max_context_tokens", 0) or 0) or 120_000

    def _turn_llm_calls(self) -> int:
        # Assistant rounds for the completed turn — session telemetry, mirroring
        # the ACP rail bias (control_schema BudgetState semantics, GAP-COMP-4).
        return int(getattr(self.session, "_current_turn_llm_calls", 0) or 0)

    def _budget_state_event(self) -> BudgetState | None:
        """Compose the canonical budget read-model for this rail."""
        agent = getattr(self.session, "agent", None)
        if agent is None:
            return None
        return BudgetState(
            llm_calls_used=self._usage_int("llm_calls"),
            llm_calls_limit=getattr(self.session, "max_llm_calls_per_session", None),
            tool_rounds_used=self._turn_llm_calls(),
            tool_rounds_limit=int(getattr(agent, "max_tool_rounds", 0) or 1000),
            context_used_tokens=self._last_prompt_tokens(),
            context_limit_tokens=self._context_limit(),
            session_id=self._session_id_str(),
        )

    def _emit_budget_state(self) -> None:
        """Publish the budget read-model on the JSONL bridge (GAP-COMP-4/2).

        Emitted once per completed turn. The canonical envelope is a new
        top-level line — older JS clients simply ignore unknown keys.
        """
        event = self._budget_state_event()
        if event is None:
            return
        try:
            self._emit_line(control_event_to_dict(event))
        except Exception:
            pass

    def _run_cancellable(
        self,
        action: Callable[[], str],
        cancel: TurnCancellation,
    ) -> tuple[str, bool]:
        """Run *action* in a worker thread; return quickly once cancel is requested."""
        from ..cancellation import STOPPED_MESSAGE

        result: dict[str, Any] = {}
        error: dict[str, Exception] = {}

        def worker() -> None:
            try:
                result["value"] = action()
            except TurnCancelledError:
                result["cancelled"] = True
            except Exception as exc:  # noqa: BLE001 - surfaced via handle_request
                if cancel.is_set():
                    result["cancelled"] = True
                else:
                    error["exc"] = exc

        thread = threading.Thread(target=worker, daemon=True, name="companion-turn")
        self.__dict__["_turn_worker"] = thread
        thread.start()
        while thread.is_alive():
            outcome = self._poll_turn_once(thread, cancel)
            if outcome is not None:
                return outcome
        thread.join(timeout=0.05)
        if error.get("exc"):
            raise error["exc"]
        if result.get("cancelled") or cancel.is_set():
            return STOPPED_MESSAGE, True
        return str(result.get("value", "")), False

    def _poll_turn_once(
        self,
        thread: threading.Thread,
        cancel: TurnCancellation,
    ) -> tuple[str, bool] | None:
        """Check cancel and OOB stdin once during a blocked turn."""
        from ..cancellation import STOPPED_MESSAGE

        if cancel.is_set():
            self._emit_turn_stopped()
            thread.join(timeout=0.25)
            return STOPPED_MESSAGE, True
        mux = self._stdin_mux
        if mux is None:
            thread.join(timeout=0.02)
            return None
        if _stdin_closed(mux):
            # Nobody reads this turn any more (the TUI closed or died): stop
            # it, so the main loop sees the EOF and the bridge exits.
            self.cancel_turn({})
            return None
        line = mux.get(timeout=0.02)
        if line is None or _handle_oob_line(self, line) is None:
            return None
        if cancel.is_set():
            self._emit_turn_stopped()
            thread.join(timeout=0.25)
            return STOPPED_MESSAGE, True
        return None

    def dispatch_slash_command(self, params: dict[str, Any]) -> dict[str, Any]:
        command = str(params.get("command") or "").strip()
        if not command.startswith("/"):
            raise ValueError("slash command must start with '/'")
        renderer = CollectingRenderer()
        root_command = command.split(maxsplit=1)[0].lower()
        dashboard: dict[str, Any] | None = None
        cancelled = False
        if root_command in {"/dashboard", "/status", "/refresh"}:
            dashboard = self.backend_dashboard({})["dashboard"]
            renderer.system("Backend dashboard refreshed")
            handled = True
        elif root_command in {"/sessions", "/resume"}:
            handled = self._dispatch_sessions(command, renderer)
        else:
            handled, cancelled = self._run_shared_slash(command, renderer)
        self._store.save(self.session)
        result = {
            "handled": handled,
            "cleared": renderer.cleared,
            "output": renderer.lines,
            "state": self.snapshot(),
            "dashboard": dashboard,
            "follow_up_messages": list(renderer.follow_up_messages),
            "dropped_turn_ids": list(renderer.dropped_turn_ids),
        }
        if cancelled:
            result["cancelled"] = True
            result["follow_up_messages"] = []
        return result

    def _run_shared_slash(self, command: str, renderer: "CollectingRenderer") -> tuple[bool, bool]:
        """Run a registry slash command under the chat-turn pump → (handled, cancelled).

        It ran on the stdin thread, so a slow command (``/code test``,
        ``/workflow trigger --wait``) left /btw, cancel and polls unread until
        it ended (TUI audit 2026-10-17). Like a turn, it now answers deferred
        verbs and reads, honours cancel and parks mutations until it finishes.
        """
        outcome: dict[str, bool] = {}

        def run() -> str:
            outcome["handled"] = dispatch_shared_command(
                command,
                session=self.session,
                store=self._store,
                renderer=renderer,
            )
            return ""

        cancel = self._start_turn_cancel()
        try:
            _text, cancelled = self._run_cancellable(run, cancel)
        finally:
            self.session._turn_cancel = None
            self._active_turn_cancel = None
        if cancelled:
            renderer.system(f"{command.split(maxsplit=1)[0]} stopped")
            return True, True
        return outcome.get("handled", False), False

    def _dispatch_sessions(self, command: str, renderer: "CollectingRenderer") -> bool:
        """Handle ``/sessions [session_id]`` (``/resume`` is an alias)."""
        parts = command.split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            session_id = parts[1].strip()
            try:
                self.load_session({"session_id": session_id})
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                renderer.error(f"could not resume {session_id}: {exc}")
                return True
            renderer.system(f"resumed session {session_id}")
            return True
        sessions = self._store.list_sessions(limit=10)
        if not sessions:
            renderer.info("(no saved sessions found)")
            return True
        renderer.system("recent sessions:")
        for record in sessions:
            sid = str(record.get("id") or "")[:8]
            name = record.get("name") or "(unnamed)"
            model = record.get("model") or "?"
            renderer.info(f"  {sid}  {name}  model={model}")
        renderer.info("usage: /sessions <session_id>")
        return True

    def list_sessions(self, params: dict[str, Any]) -> dict[str, Any]:
        """Unified session listing (plan 2026-09-09, additive response fields).

        Local store rows carry venue/state; with the journal reachable the
        same response merges the server's snapshot journal — remote-only ids
        enter with sync='remote-only' — and each row gets a sync badge:
        local-only | remote-only | mirrored | degraded (mirror errors, §8a).
        ``remote_available:false`` + ``remote_reason`` answer the picker's
        frontend gate honestly. No new verbs: v1 stays additive-only.
        """
        limit = int(params.get("limit", 50))
        venue = str(params.get("venue") or "")
        state = str(params.get("state") or "")
        include_archived = bool(params.get("include_archived") or False)
        if venue:
            from ..session_store import validate_venue

            validate_venue(venue)
        rows = self._local_session_rows(limit, venue, state, include_archived)
        sync_probe = getattr(self._store, "sync_status", None)
        sync_mode = str(sync_probe().get("mode") or "") if callable(sync_probe) else ""
        response: dict[str, Any] = {
            "sessions": [dict(row, sync="local-only") for row in rows],
            "remote_available": False,
            "remote_reason": "",
            "sync_mode": sync_mode,
        }
        if params.get("no_remote"):
            return response
        return self._merge_remote_sessions(response, venue, state, include_archived)

    def _local_session_rows(
        self, limit: int, venue: str, state: str, include_archived: bool
    ) -> list[dict[str, Any]]:
        """Store listing — filter kwargs only when set so pre-venue store
        doubles stay signature-safe (they accept limit only)."""
        filter_kwargs: dict[str, Any] = {}
        if include_archived:
            filter_kwargs["include_archived"] = True
        if venue:
            filter_kwargs["venue"] = venue
        if state:
            filter_kwargs["state"] = state
        return self._store.list_sessions(limit=limit, **filter_kwargs)

    def _merge_remote_sessions(
        self,
        response: dict[str, Any],
        venue: str,
        state: str,
        include_archived: bool,
    ) -> dict[str, Any]:
        """Fold the server journal into the listing response (honest failure).

        Journal unreachable → remote_available=false + reason; local rows
        keep their 'local-only' badge — never emulated remote rows.
        """
        snapshots, error = self._fetch_session_journal(venue, state, include_archived)
        if error is not None:
            response["remote_reason"] = error
            return response
        response["remote_available"] = True
        remote_ids = {str(snap.get("session_id") or "") for snap in snapshots}
        _badge_mirrored_rows(response, remote_ids)
        local_ids = {row["id"] for row in response["sessions"]}
        for snap in snapshots:
            remote_row = _remote_only_row(snap, local_ids)
            if remote_row is not None:
                response["sessions"].append(remote_row)
        # F14 audit fix (2026-09-21): unified merge-order — remote-only rows
        # no longer sort last simply by the erasure rule. Sort by updated_at
        # DESC with missing-dates-sink-last (never fabricated dates).
        response["sessions"].sort(
            key=lambda row: str(row.get("updated_at") or ""),
            reverse=True,
        )
        return response

    def _fetch_session_journal(
        self, venue: str, state: str, include_archived: bool
    ) -> tuple[list[dict[str, Any]], str | None]:
        """GET the snapshot journal with filter params on the wire.

        Filters ride the server side (never client-emulated). Returns
        (snapshots, error); error non-empty means unavailable.
        """
        from ..session_store import journal_prefix

        prefix = journal_prefix(self._client)
        query = ["limit=200"]
        if venue:
            query.append(f"venue={venue}")
        if state:
            query.append(f"state={state}")
        if include_archived:
            query.append("include_archived=true")
        try:
            listing = self._client.get(f"{prefix}/companion/sessions?{'&'.join(query)}") or {}
        except Exception as exc:  # noqa: BLE001 — journal optional
            return [], str(exc)[:200]
        return list(listing.get("snapshots") or []), None

    # -- chat-queue rail (T5 topic-23 locality + F9 server-side cross-client) --

    def session_queue_remote(self, params: dict[str, Any]) -> dict[str, Any]:
        """Server-side queue verb — cross-client queue remote lane (F9 wave).
        client_id stamps are written by the caller, never recast.
        """
        """Server-side queue verb — cross-client queue remote lane (F9 wave).
        client_id stamps are written by the caller, never recast.
        """
        body = _queue_remote_body(params)
        session_id = self._session_id_str()
        if not session_id:
            raise ValueError("queue remote requires an active session id")
        from ..session_store import journal_prefix

        url = f"{journal_prefix(self._client)}/companion/sessions/{session_id}/queue"
        payload = self._client.post(url, json=body, timeout=10)
        return {
            "applied": payload.get("applied", False) if isinstance(payload, dict) else False,
            "queue": payload.get("queue", []) if isinstance(payload, dict) else [],
            "front": payload.get("front") if isinstance(payload, dict) else None,
        }

    # -- frontend venue runtime (wave 2026-09-21, ADR-046 A2) -----------------

    def _venue_url(self, session_id: str, tail: str = "") -> str:
        from ..session_store import journal_prefix

        return f"{journal_prefix(self._client)}/companion/sessions/{session_id}/venue{tail}"

    def _session_is_frontend(self) -> bool:
        session = getattr(self, "_session", None)
        metadata = getattr(session, "metadata", None) if session is not None else None
        return bool(isinstance(metadata, dict) and str(metadata.get("venue") or "") == "frontend")

    def _record_venue_runtime(
        self, session: Any, payload: dict[str, Any], *, save: bool = True
    ) -> dict[str, Any]:
        """Project the server's runtime row onto session metadata (references only)."""
        runtime = _venue_runtime_projection(payload)
        _project_runtime(session, runtime)
        if save:
            adopted = self._save_adopting_newer(session)
            if adopted is not session:
                _project_runtime(adopted, runtime)
                self._store.save(adopted)
        return runtime

    def _save_adopting_newer(self, session: Any) -> Any:
        """save(); when the journal refused it as older, adopt the journal row.

        The journal is newer when a remote turn finished while nobody watched
        it here (Esc, a closed TUI, an expired lease): server wins — the
        worker's copy is the newer one, and a restarted worker restores it
        anyway. Returns the live session (the reloaded one after an adoption).
        """
        self._store.save(session)
        sync = self._store.sync_status() if hasattr(self._store, "sync_status") else {}
        if not sync.get("journal_newer"):
            return session
        from ..session_store import journal_prefix

        sid = str(getattr(session, "session_id", "") or "")
        try:
            detail = _as_dict(
                self._client.get(
                    f"{journal_prefix(self._client)}/companion/sessions/{sid}", timeout=15
                )
            )
        except Exception:  # noqa: BLE001 — best effort: the caller reports degraded
            logger.debug("journal detail for adoption unavailable", exc_info=True)
            return session
        if sid != self._session_id_str() or not self._adopt_journal_row(sid, detail):
            return session
        return self._session

    def _mirror_for_venue(self, session: Any, access: WebAccess | None = None) -> Any:
        """Pin venue=frontend (and the start's web choice) and mirror
        synchronously (start refuses unmirrored rows).

        The choice is written into the copy that is mirrored, the adopted
        journal copy too, so the worker restores the choice the start sends
        (RS-1 fix W4: the server's newer copy had none).
        """
        _require_frontend_capable(session)
        _pin_remote_start(session, access)
        adopted = self._save_adopting_newer(session)
        if adopted is not session:
            _require_frontend_capable(adopted)
            _pin_remote_start(adopted, access)
            self._store.save(adopted)
        sync = self._store.sync_status() if hasattr(self._store, "sync_status") else {}
        if sync.get("mode") == "degraded":
            raise ValueError(f"venue.mirror_failed: {sync.get('error') or 'journal unreachable'}")
        return adopted

    def session_venue_start(self, params: dict[str, Any]) -> dict[str, Any]:
        """Provision the sandbox runtime for the active session (design F-2).

        Order matters: the session is mirrored synchronously first (the
        server refuses to start an unmirrored session), then the start call
        rides the owner's identity. 409 ``source_not_pushed`` surfaces as a
        typed ValueError the TUI turns into the push flow.
        """
        before = self.session
        sid = self._session_id_str()
        refs = _clean_strings(params.get("source_refs"))
        if not refs:
            raise ValueError("venue.bad_request: source_refs must name at least one pushed source")
        access = _start_web_access(before, params)
        if access is None:
            # RS-1 round 4 (web R4-2): refused here, before the mirror pins
            # the venue — a server still on the transition setting
            # (choice not required) gave the site default (public) to a
            # start nobody chose for (M-4, M-6).
            raise ValueError(REMOTE_START_UNCHOSEN)
        session = self._mirror_for_venue(before, access)
        body = _venue_start_body(session, refs, params)
        # ADR-099 D-8: the worker runs with this session's settings.
        settings_error = self._push_venue_settings(session, force=True)
        try:
            payload = self._client.post(self._venue_url(sid, "/start"), json=body, timeout=240)
        except APIError as exc:
            raise ValueError(_venue_error_text(exc)) from exc
        payload = _as_dict(payload)
        runtime = self._record_venue_runtime(session, payload)
        # adopted: the journal's newer copy replaced the local one (the TUI
        # re-renders the transcript from state.messages).
        return {
            "venue": payload,
            "runtime": runtime,
            "state": self.snapshot(),
            "adopted": self._session is not before,
            "settings_error": settings_error,
        }

    def session_venue_status(self, params: dict[str, Any]) -> dict[str, Any]:
        sid = str(params.get("session_id") or self._session_id_str())
        if not sid:
            raise ValueError("venue.bad_request: session id required")
        try:
            payload = self._client.get(self._venue_url(sid), timeout=15)
        except APIError as exc:
            raise ValueError(_venue_error_text(exc)) from exc
        payload = payload if isinstance(payload, dict) else {}
        if self._session is not None and sid == self._session_id_str():
            self._record_venue_runtime(self._session, payload, save=False)
        return {"venue": payload, "lease_remaining_sec": _lease_remaining_sec(payload)}

    def session_venue_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        sid = str(params.get("session_id") or self._session_id_str())
        if not sid:
            raise ValueError("venue.bad_request: session id required")
        try:
            payload = self._client.post(self._venue_url(sid, "/stop"), json={}, timeout=120)
        except APIError as exc:
            raise ValueError(_venue_error_text(exc)) from exc
        payload = payload if isinstance(payload, dict) else {}
        before = self._session
        if before is not None and sid == self._session_id_str():
            self._record_venue_runtime(before, payload)
        return {"venue": payload, "state": self.snapshot(), "adopted": self._session is not before}

    def session_venue_sync(self, params: dict[str, Any]) -> dict[str, Any]:
        """Adopt the journal row when the remote worker advanced it (server wins).

        One call answers the TUI's remote-turn watcher: runtime (phase,
        partial text, turn_seq) from the venue row + the snapshot detail;
        the local row is replaced whenever the server's copy moved.
        """
        from ..session_store import journal_prefix

        session = self.session
        sid = self._session_id_str()
        try:
            status = _as_dict(self._client.get(self._venue_url(sid), timeout=15))
            detail = _as_dict(
                self._client.get(
                    f"{journal_prefix(self._client)}/companion/sessions/{sid}", timeout=15
                )
            )
        except APIError as exc:
            raise ValueError(_venue_error_text(exc)) from exc
        adopted = False
        if _snapshot_moved(session, detail) or bool(params.get("force")):
            adopted = self._adopt_journal_row(sid, detail)
        runtime = self._record_venue_runtime(self._session, status, save=False)
        return {
            "adopted": adopted,
            "runtime": _as_dict(status.get("runtime")),
            "venue_state": runtime["state"],
            "lease_remaining_sec": _lease_remaining_sec(status),
            "turn_seq": int(self._session.metadata.get("turn_seq") or 0),
            "state": self.snapshot(),
        }

    def _adopt_journal_row(self, sid: str, detail: dict[str, Any]) -> bool:
        """Server-wins replace of the local row + reload of the live session."""
        adopt = getattr(self._store, "adopt_remote_detail", None)
        if not callable(adopt) or not adopt(sid, detail):
            return False
        reloaded = self._store.load(sid, self.client)
        if reloaded is None:
            return False
        self._session = reloaded
        self._configure_session(reloaded)
        return True

    def _send_remote(self, text: str, params: dict[str, Any]) -> dict[str, Any]:
        runtime = self.session.metadata.get("venue_runtime") or {}
        state = str(runtime.get("state") or "") if isinstance(runtime, dict) else ""
        if state not in ("running", "provisioning"):
            raise ValueError(
                f"venue.not_running: remote runtime is {state or 'not started'} — "
                "start it with /venue start, or fork the session to run locally"
            )
        result = self.session_queue_remote(
            {
                "action": "append",
                "content": text,
                "client_id": str(params.get("client_id") or "tui"),
            }
        )
        if not result.get("applied"):
            raise ValueError("venue.queue_failed: the server did not accept the queued turn")
        return {
            "response": "",
            "state": self.snapshot(),
            "remote": True,
            "queued": True,
            "queue": result.get("queue", []),
        }

    def session_venue_approvals(self, params: dict[str, Any]) -> dict[str, Any]:
        from ..session_store import journal_prefix

        sid = str(params.get("session_id") or self._session_id_str())
        try:
            payload = self._client.get(
                f"{journal_prefix(self._client)}/companion/sessions/{sid}/approvals",
                params={"status": "pending"},
                timeout=15,
            )
        except APIError as exc:
            raise ValueError(_venue_error_text(exc)) from exc
        rows = payload.get("approvals", []) if isinstance(payload, dict) else []
        return {"approvals": rows, "count": len(rows)}

    def session_venue_decide(self, params: dict[str, Any]) -> dict[str, Any]:
        from ..session_store import journal_prefix

        sid = str(params.get("session_id") or self._session_id_str())
        approval_id = str(params.get("approval_id") or "").strip()
        decision = str(params.get("decision") or "").strip().lower()
        if not approval_id or decision not in ("once", "always", "reject"):
            raise ValueError(
                "venue.bad_request: approval_id and decision (once|always|reject) required"
            )
        try:
            payload = self._client.post(
                f"{journal_prefix(self._client)}/companion/sessions/{sid}/approvals/{approval_id}/decision",
                json={"decision": decision},
                timeout=15,
            )
        except APIError as exc:
            raise ValueError(_venue_error_text(exc)) from exc
        approval = payload.get("approval") if isinstance(payload, dict) else None
        self._remember_remote_always(sid, decision, approval)
        return {"approval": approval}

    def _remember_remote_always(self, sid: str, decision: str, approval: Any) -> None:
        """An "always" decided for the remote worker also reaches this
        session's settings, so a restarted worker keeps it (ADR-099 D-8).
        Not for a fenced ask (fix 2026-10-30): an "always" on a tainted,
        credential or protected-path card approves that one call only."""
        if decision != "always" or sid != self._session_id_str():
            return
        action = approval.get("action") if isinstance(approval, dict) else None
        if not isinstance(action, dict) or action.get("fenced"):
            return
        tool = str(action.get("tool") or "")
        session = getattr(self, "_session", None)
        if not tool or session is None:
            return
        session.permissions.approve_for_session(tool)
        self._push_venue_settings(session)

    # -- remote ask_user questions (ADR-099 D-8) -----------------------------

    def _questions_url(self, session_id: str, tail: str = "") -> str:
        from ..session_store import journal_prefix

        return f"{journal_prefix(self.client)}/companion/sessions/{session_id}/questions{tail}"

    def session_venue_questions(self, params: dict[str, Any]) -> dict[str, Any]:
        """Pending questions the remote worker asked this session's owner."""
        sid = str(params.get("session_id") or self._session_id_str())
        if not sid:
            raise ValueError("venue.bad_request: session id required")
        try:
            payload = self.client.get(
                self._questions_url(sid), params={"status": "pending"}, timeout=15
            )
        except APIError as exc:
            raise ValueError(_venue_error_text(exc)) from exc
        rows = payload.get("questions", []) if isinstance(payload, dict) else []
        return {"questions": rows if isinstance(rows, list) else []}

    def session_venue_answer(self, params: dict[str, Any]) -> dict[str, Any]:
        """Answer one remote question (409 ``question_closed`` when it is not pending)."""
        sid = str(params.get("session_id") or self._session_id_str())
        question_id = str(params.get("question_id") or "").strip()
        answer = str(params.get("answer") or "")
        if not sid or not question_id or not answer.strip():
            raise ValueError("venue.bad_request: question_id and a non-empty answer required")
        try:
            payload = self.client.post(
                self._questions_url(sid, f"/{question_id}/answer"),
                json={"answer": answer},
                timeout=15,
            )
        except APIError as exc:
            raise ValueError(_venue_error_text(exc)) from exc
        return {"question": payload.get("question") if isinstance(payload, dict) else None}

    # -- sealed sources from the TUI (push/unpush without shelling out) --------

    def source_list_remote(self, _params: dict[str, Any]) -> dict[str, Any]:
        from ...source import get_source_store
        from ...user_sources import UserSourcesClient

        remote: dict[str, dict] | None
        try:
            remote = {
                str(r.get("name")): r
                for r in UserSourcesClient(self._client).list_mine(with_leases=True)
            }
        except Exception as exc:  # noqa: BLE001 — unreachable journal is a labeled state
            logger.debug("sealed remote unreachable: %s", exc)
            remote = None
        from ..._cli.source import _remote_badge

        rows = []
        for src in get_source_store().list():
            badge = _remote_badge(src, remote)
            rows.append(
                {
                    "name": src.name,
                    "kind": src.kind,
                    "url": src.url,
                    "provider": src.provider,
                    "has_api_key": bool(src.api_key),
                    "remote": badge,
                    "pushed": badge.startswith("pushed"),
                    "eligible": src.kind == "public" and bool(src.api_key),
                }
            )
        return {"rows": rows, "remote_available": remote is not None}

    def source_push(self, params: dict[str, Any]) -> dict[str, Any]:
        from ..._cli.source import SourceCommand
        from ...source import get_source_store
        from ...user_sources import UserSourcesClient

        name = str(params.get("name") or "").strip()
        if not name:
            raise ValueError("source.push_refused: source name required")
        src = get_source_store().get(name)
        refusal = SourceCommand._push_precondition_error(src, name)
        if refusal:
            raise ValueError(f"source.push_refused: {refusal}")
        provider_kind = _provider_kind_for(src)
        allow_paths = _clean_strings(params.get("allow_paths"))
        try:
            result = UserSourcesClient(self._client).push(
                name=src.name,
                provider_kind=provider_kind,
                base_url=src.url,
                api_key=src.api_key,
                allow_paths=allow_paths,
            )
        except APIError as exc:
            raise ValueError(f"source.push_failed: {exc}") from exc
        return {
            "ok": True,
            "name": src.name,
            "allow_host": result.get("allow_host"),
            "key_fingerprint": result.get("key_fingerprint"),
            "replaced": bool(result.get("replaced")),
        }

    def source_unpush(self, params: dict[str, Any]) -> dict[str, Any]:
        from ...user_sources import UserSourcesClient

        name = str(params.get("name") or "").strip()
        if not name:
            raise ValueError("source.unpush_refused: source name required")
        try:
            result = UserSourcesClient(self._client).unpush(name)
        except APIError as exc:
            raise ValueError(f"source.unpush_failed: {exc}") from exc
        revoked = int(result.get("leases_revoked", 0) or 0)
        return {"ok": True, "name": name, "leases_revoked": revoked}

    # -- write-requires-plan gate (T5 topic-24 opt-in session flag) ----------

    def set_write_requires_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        """Toggle the write_requires_plan session flag (on/off, never cast).

        Additive metadata only; the agent-side gate prunes write-family verbs
        the next turn unless plan approved rides along.
        """
        enabled_raw = params.get("enabled")
        enabled = bool(enabled_raw) if not isinstance(enabled_raw, bool) else enabled_raw
        self.session.metadata["write_requires_plan"] = enabled
        self._store.save(self.session)
        self._push_venue_settings(self.session)
        return {"write_requires_plan": enabled}

    def session_queue_append(self, params: dict[str, Any]) -> dict[str, Any]:
        """Append a queued user text to session.metadata["chat_queue"].

        Never fabricated-by-construction (only caller-supplied text lands).
        A NO-OP when empty content. Returns the current queue copy.
        """
        content = str(params.get("content") or "").strip()
        if not content:
            return {"ok": False, "queue": self._queue_meta()}
        queue = self._queue_meta()
        queue.append(content)
        self.session.metadata["chat_queue"] = queue
        self._store.save(self.session)
        return {"ok": True, "queue": list(queue)}

    def session_queue_pop(self, params: dict[str, Any]) -> dict[str, Any]:
        """Pop the head of session.metadata["chat_queue"].

        Returns {\"content\": str|None, \"remaining\": int}; the caller is the
        ONLY consumer per-intent (never shared banners).
        """
        _ = params
        queue = self._queue_meta()
        content = queue.pop(0) if queue else None
        self.session.metadata["chat_queue"] = queue
        self._store.save(self.session)
        return {"content": content, "remaining": len(queue)}

    def session_queue_clear(self, params: dict[str, Any]) -> dict[str, Any]:
        """Wipe the queue (interrupt semantics; never silent under it)."""
        _ = params
        count_before = len(self._queue_meta())
        self.session.metadata["chat_queue"] = []
        self._store.save(self.session)
        return {"ok": True, "cleared": count_before}

    def _queue_meta(self, session: Any = None) -> list[str]:
        """Read the metadata chat_queue; never fabricated when absent.

        Rows are plain strings on the local lane and ``{id, content, client_id,
        added_at}`` dicts on the server lane (F9) — a journal-pulled session
        keeps its queued turns instead of silently dropping the dict rows.
        """
        rows = (session if session is not None else self.session).metadata.get("chat_queue")
        if not isinstance(rows, list):
            return []
        out: list[str] = []
        for r in list(rows):
            if isinstance(r, str):
                out.append(r)
            elif isinstance(r, dict) and str(r.get("content") or "").strip():
                out.append(str(r["content"]))
        return out

    def session_pin(self, params: dict[str, Any]) -> dict[str, Any]:
        """C2: pin a session (rides the dedicated sessions.pinned_at column —
        survives live saves; the bridge lists column is pinned-first)."""
        session_id = str(params.get("session_id") or "")
        if not session_id:
            raise ValueError("session_id is required")
        if not self._store.set_pinned(session_id, True):
            raise ValueError(f"session not found: {session_id}")
        return {"session_id": session_id, "pinned": True}

    def session_fork(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fork a session row under a fresh id (G-P9-2 + F6 from_turn plumbing).

        from_turn (optional) callers fork at the prefix-through-N turn slice —
        matches the CLI-facing `r4 session fork --from-turn ...` contract; the
        named `fork.turn_not_found` error class surfaces translator-side.
        """
        import uuid as _uuid

        session_id = str(params.get("session_id") or "")
        if not session_id:
            raise ValueError("session_id is required")
        new_name = str(params.get("name") or "")
        from_turn = str(params.get("from_turn") or "")
        new_id = f"sess-{_uuid.uuid4().hex[:12]}"
        try:
            summary = self._store.fork(
                session_id,
                new_id,
                new_name=new_name,
                from_turn=from_turn,
            )
        except KeyError as exc:
            missing = exc.args[0] if exc.args else "turn id"
            raise ValueError(f"fork.turn_not_found: {missing}") from exc
        if summary is None:
            raise ValueError(f"session not found: {session_id}")
        return summary

    def session_unpin(self, params: dict[str, Any]) -> dict[str, Any]:
        """C2: unpin a session."""
        session_id = str(params.get("session_id") or "")
        if not session_id:
            raise ValueError("session_id is required")
        if not self._store.set_pinned(session_id, False):
            raise ValueError(f"session not found: {session_id}")
        return {"session_id": session_id, "pinned": False}

    def session_rename(self, params: dict[str, Any]) -> dict[str, Any]:
        """C2: store-level rename (live sessions re-derive on next save)."""
        session_id = str(params.get("session_id") or "")
        name = str(params.get("name") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        if not name:
            raise ValueError("name is required")
        if not self._store.rename_record(session_id, name):
            raise ValueError(f"session not found: {session_id}")
        return {"session_id": session_id, "name": name}

    def session_archive(self, params: dict[str, Any]) -> dict[str, Any]:
        """Archive a session — hidden from default list_sessions (filter rule
        lives in CompanionSessionStore.list_sessions include_archived=False)."""
        session_id = str(params.get("session_id") or "")
        if not session_id:
            raise ValueError("session_id is required")
        if not self._store.archive(session_id):
            raise ValueError(f"session not found: {session_id}")
        # Item 8 (durable approvals, session-bound): the server owns the
        # trust rows, so the cascade lives in ITS archive endpoint — the
        # bridge converges on archive (tombstone + session-scope revoke +
        # per-grant audit, one transaction) instead of enumerating grants.
        # Best-effort here: the 86400s wall-clock TTL stays as the
        # fail-safe bound when the server is unreachable.
        # NOTE: use self._client directly — the `self.session` property
        # auto-INITIALIZES a fresh CompanionSession (writing a new store
        # row), which would spawn a session as an archive side effect.
        archive_server = getattr(self._client, "companion_session_archive", None)
        if callable(archive_server):
            try:
                archive_server(session_id)
            except Exception:  # noqa: BLE001 — durable cascade is best-effort
                logger.debug(
                    "server-side session archive failed for %s (TTL bound applies)",
                    session_id,
                    exc_info=True,
                )
        return {"session_id": session_id, "archived": True}

    def session_compact(self, params: dict[str, Any]) -> dict[str, Any]:
        """C4: force-run compaction on the current session's history.

        User trigger (`/compact`), not threshold-triggered. See
        ``companion/session_ops.compact_history`` for the shared routine —
        rewrite + persist via store save here; ACP's ``sessions/compact``
        round-trips through ``session.save()`` on its own rail.
        """
        from ..session_ops import compact_history

        before = list(self.session.messages)
        result = compact_history(
            self.session,
            keep_recent=int(params.get("keep_recent", 6)),
        )
        if result.get("changed"):
            _restore_turn_ids(before, self.session.messages)
            self._store.save(self.session)
        return result

    def session_truncate(self, params: dict[str, Any]) -> dict[str, Any]:
        """Cut the history at the user turn a rewind replaces (TUI audit r2 #17).

        Rewind / edit-and-resend sliced only the TUI transcript; the companion
        kept every discarded turn in the model context and the saved session.
        The cut is keyed on the turn id (ADR-057) the TUI got back from
        send_message, never on a count: /compact and /retry change the
        session's turn count, not the transcript's. ``turn_id`` = tN drops
        tN's user message and everything after it. A turn that is no longer
        in the history (compacted away, retried) is ``rewind.desync`` and
        nothing is cut. A frontend session's context lives in its remote
        runtime, so the verb refuses there.
        """
        turn_id = str(params.get("turn_id") or "").strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        if self._session_is_frontend():
            raise ValueError(
                "venue.remote: rewind cannot cut a remote session's context — "
                "fork the session to run locally"
            )
        messages = self.session.messages
        cut = _user_turn_index(messages, turn_id)
        if cut is None:
            raise ValueError(
                f"rewind.desync: turn {turn_id} is not in the session history "
                "(compacted or retried) — nothing was cut"
            )
        removed = len(messages) - cut
        del messages[cut:]
        self._store.save(self.session)
        return {"ok": True, "removed": removed, "messages": cut, "state": self.snapshot()}

    def load_session(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("session_id") or "")
        if not session_id:
            raise ValueError("session_id is required")
        session = self._store.load(session_id, self.client)
        if session is None:
            # Remote-only rows (plan P5): resume IS the pull — fetch the row
            # from the journal, then load. Local rows always win; verbiage is
            # honest about the fetch (no silent two-step).
            pulled = self._pull_then_load(session_id)
            if pulled is None:
                raise ValueError(f"session not found locally or in the journal: {session_id}")
            session = pulled
        # RS-1 fix W6: a resumed session without a choice gets the launch
        # preset (r4 --web) as a new one does.
        self._apply_initial_web_access(session, None)
        self._apply_initial_sandbox(session)
        self._session = session
        self._configure_session(session)
        notice = self._refresh_then_push(session)
        snapshot = self.snapshot()
        if session.metadata.get("_pulled_from_journal"):
            session.metadata.pop("_pulled_from_journal", None)
            snapshot["notice"] = "pulled from server journal before resume"
        if notice:
            snapshot["notice"] = notice
        return snapshot

    def _attach_push(self, session: CompanionSession, params: dict[str, Any]) -> str:
        """M-6: attaching to a running remote session sends this client's
        settings (a no-op for a local session). RS-1 round 4 (A4-1): a
        rejoin by id (a restarted tab bridge) refreshes from the journal
        first, as load_session does — the cached copy may be stale."""
        if params.get("session_id") or self._initial_session_id:
            return self._refresh_then_push(session)
        self._push_venue_settings(session, timeout=_ATTACH_PUSH_TIMEOUT_SEC)
        return ""

    def _refresh_then_push(self, session: CompanionSession) -> str:
        """Resume or rejoin: refresh a remote session from the journal, then
        send this client's settings; returns the refresh notice.

        M-6 (ADR-099 D-8): a resumed remote session runs with THIS client's
        settings from its next turn — a pre-RS-1 start left rev 0 (the
        worker took the client for one that cannot answer questions), and
        a relaunch may change --yolo, ask_user_mode or the approval TTL.
        RS-1 round 3: the live session (adoption may have replaced
        ``session``, N1), and only when the journal answered (N4)."""
        notice = self._refresh_remote_resume(session)
        if notice not in _RESUME_REFRESH_FAILED:
            self._push_venue_settings(timeout=_ATTACH_PUSH_TIMEOUT_SEC)
        return notice

    def _refresh_remote_resume(self, session: CompanionSession) -> str:
        """Refresh a remote session's cached transcript without writing it back."""
        if session.metadata.get("venue") != "frontend":
            return ""
        from ..session_store import journal_prefix

        sid = session.session_id
        try:
            detail = _as_dict(
                self.client.get(
                    f"{journal_prefix(self.client)}/companion/sessions/{sid}", timeout=15
                )
            )
            if not _journal_payload(session, detail):
                return _RESUME_JOURNAL_UNAVAILABLE
            if _snapshot_moved(session, detail):
                if not self._adopt_journal_row(sid, detail):
                    return _RESUME_ADOPTION_FAILED
                return "refreshed from server journal before resume"
        except Exception:
            logger.warning("remote resume refresh failed for %s", sid, exc_info=True)
            return _RESUME_JOURNAL_UNAVAILABLE
        return ""

    def _pull_then_load(self, session_id: str) -> CompanionSession | None:
        """Pull-fallback for load_session failures (remote-only resume)."""
        pull_probe = getattr(self._store, "pull_session", None)
        if not callable(pull_probe):
            return None
        try:
            outcome = pull_probe(self._client, session_id)
        except Exception:  # noqa: BLE001 — journal optional
            logger.debug("journal pull probe failed for %s", session_id, exc_info=True)
            return None
        if outcome != "pulled":
            return None
        session = self._store.load(session_id, self.client)
        if session is not None:
            session.metadata["_pulled_from_journal"] = True
        return session

    def export_session(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = params.get("session_id")
        if session_id and str(session_id) != self._session_id_or_empty():
            try:
                session = self._store.load(str(session_id), self.client)
            except SessionCorruptError as exc:
                return self._export_raw_row(str(session_id), exc)
            if session is None:
                raise ValueError(f"session not found: {session_id}")
        else:
            session = self.session
        # The exported session's own model and project: the TUI stamped the
        # active tab's values onto any other session (TUI audit r2 #18).
        # There is no per-session cost on a loaded session, so none is sent.
        return {
            "markdown": session.export_markdown(),
            "model": session_model_info(session)["model"] or None,
            "project": CompanionSessionStore._project_id(session.metadata or {}) or None,
        }

    def _session_id_or_empty(self) -> str:
        """The live session's id without creating one (a corrupt row's
        initialize failed, so there may be none)."""
        session = getattr(self, "_session", None)
        return str(getattr(session, "session_id", "") or "") if session is not None else ""

    def _export_raw_row(self, session_id: str, exc: SessionCorruptError) -> dict[str, Any]:
        """The export of a row that does not load: its stored columns as-is (L3)."""
        raw = self._store.raw_row(session_id) or {}
        blocks = [f"# Session {session_id} (raw export)", "", f"> {exc}", ""]
        for column, value in raw.items():
            blocks += [f"## {column}", "", "```", str(value), "```", ""]
        return {"markdown": "\n".join(blocks), "model": None, "project": None}

    def list_models(self, _params: dict[str, Any]) -> dict[str, Any]:
        from r4.tui.model_options import (
            collect_bridge_model_options,
            list_extra_source_probe_problems,
        )

        session = self.session
        current = session_model_info(session)["model"] or ""
        return {
            "models": collect_bridge_model_options(session.client),
            "current": current,
            # Probe failures of registered sources, named — an empty list used
            # to fall out as a misleading "no provisioned models" console line
            # (2026-10-20 report); shares collect's probe pass/cache.
            "problems": list_extra_source_probe_problems(),
        }

    def list_up_models(self, _params: dict[str, Any]) -> dict[str, Any]:
        from r4.tui.commands import collect_registry_model_options

        session = self.session
        current = session_model_info(session)["model"] or ""
        return {
            "models": collect_registry_model_options(session.client),
            "session_model": current,
        }

    def list_stoppable_endpoints(self, _params: dict[str, Any]) -> dict[str, Any]:
        from r4.tui.commands import collect_stoppable_endpoint_options

        return {"endpoints": collect_stoppable_endpoint_options(self.session.client)}

    def set_model(self, params: dict[str, Any]) -> dict[str, Any]:
        from r4.tui.commands import persist_profile_default_model

        model = str(params.get("model") or "").strip()
        if not model:
            raise ValueError("model is required")
        self.session.agent.model = model
        if bool(params.get("persist_default")):
            persist_profile_default_model(self.session.client, model)
        self._store.save(self.session)
        return self.snapshot()

    def set_interaction_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        from r4.companion.ui.command_bridge import CompanionCommandApp
        from r4.tui.modes import Mode

        mode_param = params.get("mode")
        renderer = CollectingRenderer()
        app = CompanionCommandApp(
            session=self.session,
            store=self._store,
            renderer=renderer,
        )
        if mode_param:
            try:
                mode = Mode(str(mode_param).strip().lower())
            except ValueError as exc:
                raise ValueError(f"unknown mode: {mode_param} (chat|plan|autopilot)") from exc
        else:
            mode = app.mode.next()
        app.set_mode(mode)
        # One rule with the remote worker (ADR-099 D-8): leaving autopilot
        # returns to the bridge default, as a resume of this session would.
        self._apply_permission_mode(self.session, launch=False)
        self._store.save(self.session)
        self._push_venue_settings(self.session)
        return {"mode": mode.value, "output": renderer.lines, "state": self.snapshot()}

    def set_knowledge_acquisition(self, params: dict[str, Any]) -> dict[str, Any]:
        """Session-scoped knowledge-acquisition mode (user switchable mid-session).

        Params: ``mode`` (open|pinned|disabled — empty = read current) and
        ``graphs`` (pinned targets). Persisted in session.metadata so the
        mode survives a resume of THIS session only; enforced by the
        knowledge_ingest / knowledge_graph_propose tool guards.
        """
        from r4.companion.knowledge_write_gate import (
            acquisition_constraints,
            describe_acquisition,
            sanitize_acquisition,
            set_acquisition,
        )

        session = self.session
        mode = str(params.get("mode") or "").strip().lower()
        if mode:
            graphs = params.get("graphs") or []
            if isinstance(graphs, str):
                graphs = [graphs]
            constraints = set_acquisition(session.client, mode, graphs)
            session.metadata["knowledge_acquisition"] = dict(constraints)
            self._store.save(session)
        else:
            constraints = sanitize_acquisition(
                session.metadata.get("knowledge_acquisition")
                or acquisition_constraints(session.client)
            )
        return {
            "acquisition": constraints,
            "summary": describe_acquisition(constraints),
            "state": self.snapshot(),
        }

    def list_skills(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"skills": self._skills_snapshot()}

    def trust_list_rpc(self, params: dict[str, Any]) -> dict[str, Any]:
        """List the caller's unexpired server-side trust rows (owner-scope)."""
        _ = params
        try:
            payload = self.client.trust_list()
        except Exception as exc:
            # relay the transport family so the local TUI can report honestly
            # (only ever thrown by lane failures, never by ownership misses).
            raise ValueError(f"trust lane failure: {exc}") from exc
        # TrustListResponse is {rows, count} (TUI audit #73).
        rows = payload.get("rows") if isinstance(payload, dict) else payload
        rows = rows if isinstance(rows, list) else []
        count = payload.get("count") if isinstance(payload, dict) else None
        return {"rows": rows, "count": count if isinstance(count, int) else len(rows)}

    def trust_grant_rpc(self, params: dict[str, Any]) -> dict[str, Any]:
        """Persist a server-side trust row (W2 approval-remember flow).

        The consent happens in the approval overlay; this call only records
        the row so FUTURE turns/sessions auto-approve the same tool server-side.
        """
        tool_name = str(params.get("tool_name") or "").strip()
        scope = str(params.get("scope") or "user").strip() or "user"
        if not tool_name:
            raise ValueError("tool_name is required")
        result = self.client.trust_grant(tool_name, scope=scope)
        # Revocation diagnostics (ADR-035): the allowlist was mutated; clear
        # the local positive cache so a later stale answer never sticks.
        self.session.permissions.clear_trust_check_cache()
        return {"granted": result, "tool_name": tool_name, "scope": scope}

    def get_governance_overview(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Ownership + quotas + recent audit trace, tolerant of per-section
        failures (gateway offline, older UMRS) — sections degrade to errors
        so the UI stays useful.
        """
        sections: dict[str, Any] = {}
        for name, call in (
            ("ownership", self.client.governance_ownership),
            ("quotas", self.client.governance_quotas),
            ("audit", lambda: self.client.audit_trace(limit=30)),
        ):
            try:
                sections[name] = call()
            except Exception as exc:
                sections[name] = {"error": str(exc)}
        return sections

    def revert_session_change(self, params: dict[str, Any]) -> dict[str, Any]:
        """Restore one file to the session's recorded before state (W3).

        The TUI arms with `r` and fires with `R` — the explicit two-step
        key dialog on the client side is the approval guard.
        """
        from r4.companion.tools import revert_session_change as _revert

        path = str(params.get("path") or "").strip()
        if not path:
            raise ValueError("path is required")
        return _revert(path)

    def get_session_changes(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Cumulative per-session file edits (OpenCode-parity session diff).

        The companion write/edit tools record before→after per path; the TUI
        renders side-by-side diffs from this snapshot after each turn.
        """
        from r4.companion.tools import get_session_changes

        return {"changes": get_session_changes()}

    # -- session plan (structured todo mirror → TaskGraph) ------------------

    # todo_write status → TaskGraph status (agent enum ≠ task enum by design).
    _TODO_TO_TASK_STATUS = {
        "completed": "done",
        "in-progress": "active",
        "not-started": "pending",
    }

    def _on_plan_items(self, items: list[dict[str, Any]]) -> None:
        """Mirror the agent's todo_write list into the session TaskGraph.

        todo_write rewrites the WHOLE list each call, so upsert by the
        todo's integer id: first sight → add_task (depends_on = previous
        row, giving the plan its order), later sightings → status refresh.
        """
        tg = self._task_graph
        scope = self._plan_scope()
        previous_uuid: str | None = None
        for item in items:
            if not isinstance(item, dict):
                continue
            todo_id = int(item.get("id") or 0)
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            status = self._TODO_TO_TASK_STATUS.get(str(item.get("status")), "pending")
            uuid = self._plan_task_ids.get((scope, todo_id))
            if uuid is None or tg.get_task(uuid) is None:
                deps = [previous_uuid] if previous_uuid else None
                task = tg.add_task(title=title, depends_on=deps, session_id=scope)
                uuid = task.id
                self._plan_task_ids[(scope, todo_id)] = uuid
            tg.update_task_status(uuid, status)
            previous_uuid = uuid

    def get_session_plan(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Structured session plan (steps + dependency edges) for the TUI's
        plan graph. Steps are the task-graph rows mirrored from todo_write
        plus any tasks registered via the add_task RPC. Every bridge shares
        one sqlite file, so only this session's rows are returned (the plan
        of every session used to show in each tab; TUI audit r2 #27)."""
        tasks = self._task_graph.list_all_tasks(session_id=self._plan_scope())
        return {"steps": [task.to_dict() for task in tasks]}

    def _plan_scope(self) -> str:
        """The session a plan row belongs to ('' before a session exists)."""
        return str(getattr(getattr(self, "_session", None), "session_id", "") or "")

    def activate_skill(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "")
        active = bool(params.get("active", True))
        if not name:
            raise ValueError("skill name is required")
        if active:
            if not self.session.skills.activate(name):
                raise ValueError(f"unknown skill: {name}")
        else:
            self.session.skills.deactivate(name)
        self._store.save(self.session)
        return self.snapshot()

    # -- MCP inventory surfaces (ACP mcp/* parity) --------------------------

    def list_mcp_servers(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Registered MCP servers (/gw/mcp/servers) + declarative user rows.

        Registry rows are annotated ``source: "registry"`` / ``agent_visible:
        True``; user-config rows (``~/.config/r4/mcp-servers.json`` & friends)
        are appended with declared-state status and ``agent_visible: False``
        so panels can show the full vigilance view without pretending the
        agent sees local config entries.
        """
        from r4.companion.mcp_user_config import user_defined_mcp_rows

        try:
            raw = self.session.client.mcp.list_servers()
        except Exception:  # noqa: BLE001 — registry outage must not hide user rows
            raw = []
        servers: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for entry in raw:
                row = dict(entry) if isinstance(entry, dict) else {"name": str(entry)}
                row.setdefault("status", "unknown")
                row["source"] = "registry"
                row["agent_visible"] = True
                servers.append(row)
        servers.extend(user_defined_mcp_rows())
        return {"servers": _annotate_registered_user_rows(servers)}

    def set_mcp_server_enabled(self, params: dict[str, Any]) -> dict[str, Any]:
        """Flip a user-config MCP entry's ``enabled`` flag (atomic rewrite).

        ON also tries to make the server agent-visible right now: ensure the
        remote-proxy answers locally (spawned when absent) and register the
        name in UMRS. Every failure is named in ``exposure`` (registry outage,
        spawn failure) — never a fake success. Registry rows are read-only
        here (governed by `r4 mcp unregister`).
        """
        name = str(params.get("name") or "")
        enabled = bool(params.get("enabled", True))
        if not name:
            raise ValueError("name is required")
        from r4.tui.mcp_config import set_mcp_enabled

        ok = set_mcp_enabled(name, enabled)
        if ok and enabled:
            exposure = self._expose_local_mcp(name)
        else:
            exposure = None
        result = self.list_mcp_servers({})
        result["applied"] = ok
        if exposure is not None:
            result["exposure"] = exposure
        if not ok:
            result["reason"] = f"no user-config entry named {name!r}"
        return result

    def _expose_local_mcp(self, name: str) -> dict[str, Any]:
        """Proxy + registration for one name (defensive; never raises)."""
        try:
            from r4.companion.mcp_exposure import expose_local_mcp

            description = ""
            try:
                from r4.tui.mcp_config import load_mcp_configs

                match = [c for c in load_mcp_configs() if c.name == name]
                description = match[0].description if match else ""
            except Exception:  # noqa: BLE001 — description is cosmetic
                pass
            return expose_local_mcp(self.session.client, name, description)
        except Exception as exc:  # noqa: BLE001 — exposure must not kill the toggle
            return {
                "proxy": {"state": "failed", "reason": str(exc)},
                "registration": {"state": "blocked", "reason": "exposure error"},
            }

    def list_mcp_tools(self, params: dict[str, Any]) -> dict[str, Any]:
        """MCP tools, optionally filtered to one server."""
        server = params.get("server")
        if server is not None and not isinstance(server, str):
            raise ValueError("server must be a string")
        tools = self.session.client.mcp.list_tools(server=server)
        return {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "server": tool.server,
                    "input_schema": dict(tool.parameters),
                }
                for tool in tools
            ]
        }

    def mcp_server_health(self, params: dict[str, Any]) -> dict[str, Any]:
        """Trigger a health check for one MCP server."""
        server_id = str(params.get("server_id") or "")
        if not server_id:
            raise ValueError("server_id is required")
        return self.session.client.mcp.check_server_health(server_id)

    # -- knowledge graph content (ACP knowledge/graph_content parity) -------

    def knowledge_graph_content(self, params: dict[str, Any]) -> dict[str, Any]:
        """Bounded nodes+edges for one registered knowledge graph (0069)."""
        graph_id = str(params.get("graph_id") or "")
        if not graph_id:
            raise ValueError("graph_id is required")
        node_limit = params.get("node_limit", 200)
        edge_limit = params.get("edge_limit", 400)
        return self.session.client.knowledge.graph_content(
            graph_id, node_limit=node_limit, edge_limit=edge_limit
        )

    def workflow_describe(self, params: dict[str, Any]) -> dict[str, Any]:
        """Parameter schema + declared steps for one workflow (ACP workflows/describe)."""
        workflow_name = str(params.get("workflow_name") or "")
        if not workflow_name:
            raise ValueError("workflow_name is required")
        return self.session.client.workflows.describe(workflow_name, version=_version_param(params))

    def workflow_package(self, params: dict[str, Any]) -> dict[str, Any]:
        """Definition + registered code rows for the work-pane card (2026-10-01).

        Mirrors describe's owner scoping via the client; a backend without
        the route raises — the pane renders an honest unavailability note.
        """
        workflow_name = str(params.get("workflow_name") or "")
        if not workflow_name:
            raise ValueError("workflow_name is required")
        return self.session.client.workflows.definition_package(
            workflow_name, version=_version_param(params)
        )

    def workflow_export(self, params: dict[str, Any]) -> dict[str, Any]:
        """Save one visible workflow as a verified zip (2026-11-07).

        Folder and never-overwrite name: ``ui/workflow_export.py``; the
        download and checksum check: ``WorkflowClient.export``.
        """
        from .workflow_export import export_workflow

        return export_workflow(self.session.client.workflows, params)

    def permission_rules_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        """``/settings rules``: every G14 rule this session runs with, with its
        source (ADR-104 D-3); only the project's user rules are removable."""
        return {
            "project_id": self._rules_project_id(),
            "rules": rule_rows(self.session.permissions.ruleset),
        }

    def permission_rules_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        """Remove one ``user_project`` rule by its list index."""
        rows = rule_rows(self.session.permissions.ruleset)
        index = params.get("index")
        row = rows[index] if isinstance(index, int) and 0 <= index < len(rows) else None
        if row is None or not row["removable"]:
            raise ValueError(f"rule #{index} cannot be removed here (only this project's rules)")
        remove_project_rule(self._rules_project_id(), row["behavior"], row["rule"])
        ruleset = self.session.permissions.ruleset
        if ruleset is not None:
            ruleset.remove(row["rule"], row["behavior"], "user_project")
        return self.permission_rules_list({})

    def get_usage(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"usage": self.session.usage_report()}

    def get_event_log(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return persisted events for timeline display and replay."""
        session_id = str(params.get("session_id") or self.session.session_id)
        limit = int(params.get("limit", 100))
        events = self._event_log.tail(session_id, limit=limit)
        return {"events": events, "session_id": session_id}

    # -- background job management -----------------------------------------

    def list_jobs(self, params: dict[str, Any]) -> dict[str, Any]:
        """List background jobs for this session."""
        include_completed = bool(params.get("include_completed", False))
        jobs = self._job_manager.list_jobs(
            include_completed=include_completed,
            session_id=self.session.session_id,
        )
        return {"jobs": jobs}

    def get_job(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get status of a background job."""
        job_id = str(params.get("job_id") or "")
        if not job_id:
            raise ValueError("job_id is required")
        job = self._job_manager.get_job(job_id) or self._durable_job(job_id)
        if not job:
            raise ValueError(f"job not found: {job_id}")
        return {"job": job}

    def _durable_job(self, job_id: str) -> dict[str, Any] | None:
        """UMRS durable job + attempts when the local detached-agent table
        misses (the Work pane and /job open UMRS ids; TUI audit #67)."""
        try:
            job = self.session.client.jobs.get(job_id)
        except Exception:  # noqa: BLE001 — a miss on both lanes is "not found"
            logger.debug("durable job lookup failed for %s", job_id, exc_info=True)
            return None
        return {
            "id": job.job_id,
            "job_id": job.job_id,
            "state": job.status,
            "status": job.status,
            "job_type": job.job_type,
            "project_id": job.project_id,
            "attempts": [_attempt_row(attempt) for attempt in job.attempts],
        }

    def get_job_log(self, params: dict[str, Any]) -> dict[str, Any]:
        """Read the tail of a job's log file."""
        job_id = str(params.get("job_id") or "")
        if not job_id:
            raise ValueError("job_id is required")
        tail_lines = int(params.get("tail_lines", 50))
        log = self._job_manager.get_job_log(job_id, tail_lines=tail_lines)
        return {"log": log or "", "job_id": job_id}

    def stop_job(self, params: dict[str, Any]) -> dict[str, Any]:
        """Stop a running background job."""
        job_id = str(params.get("job_id") or "")
        if not job_id:
            raise ValueError("job_id is required")
        stopped = self._job_manager.stop_job(job_id)
        return {"stopped": stopped, "job_id": job_id}

    def workflow_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """List registered UMRS workflows for the active client profile."""
        _ = params
        workflows = self.session.client.workflows.list_workflows()
        return {
            "workflows": [_workflow_catalog_row(workflow) for workflow in workflows],
            "count": len(workflows),
        }

    # -- sessions rail (T5 topic-20 tail — owner-scope turn-level detail) --

    def session_turns(self, params: dict[str, Any]) -> dict[str, Any]:
        """Owner-scope turn payloads for one session id.

        turn_id threading is optional; never fabricates rows — matches
        the server's exact stored payload shape.
        """
        session_id = str(params.get("session_id") or "")
        if not session_id:
            raise ValueError("session_turns requires session_id")
        turn_id = str(params.get("turn_id") or "")
        payload = self.session.client.list_session_turns(
            session_id=session_id, turn_id=turn_id or None
        )
        # The companion turn index answers {entries, count}, or {entry} for
        # one turn (TUI audit #58/#66/#75/#86).
        payload = payload if isinstance(payload, dict) else {}
        entry = payload.get("entry")
        rows = [entry] if isinstance(entry, dict) else payload.get("entries") or []
        return {
            "rows": rows,
            "count": payload.get("count", len(rows)),
            "session_id": session_id,
            "turn_id": turn_id or None,
        }

    # -- admin rail (T5 topic-14 — owner-scope read-only operator stats) --

    def _admin_project(self, params: dict[str, Any], verb: str) -> str:
        """The session project for a caller-scoped report; another one is refused.

        UMRS scopes rag/stats and corpus-attrition-report to the verified
        caller project and ignores a project query, so a named other project
        would come back as this one's numbers under its label (TUI audit
        #56/#83/#94). With no session project UMRS answers for the token's
        project, so a named one is refused there too.
        """
        requested = str(params.get("project_id") or "").strip()
        identity = self._identity_project_id()
        if requested and not identity:
            raise ValueError(
                f"{verb}: no session project, so the report scope cannot be"
                f" confirmed; switch first: /project switch {requested}"
            )
        if requested and requested != identity:
            raise ValueError(
                f"{verb} reports the session project {identity} only;"
                f" switch first: /project switch {requested}"
            )
        return identity or ""

    def admin_rag_stats(self, params: dict[str, Any]) -> dict[str, Any]:
        """RAG corpus stats for the caller's project."""
        project_id = self._admin_project(params, "admin.rag_stats")
        payload = self.session.client.http.get(self._umrs_url("rag/stats"), timeout=15)
        return {
            "stats": payload if isinstance(payload, dict) else {},
            "project_id": project_id or None,
        }

    def admin_corpus_attrition(self, params: dict[str, Any]) -> dict[str, Any]:
        """Read-only corpus-attrition report for one project (caller-scoped)."""
        project_id = self._admin_project(params, "admin.corpus_attrition")
        if not project_id:
            raise ValueError("admin.corpus_attrition requires project_id")
        payload = self.session.client.http.post(
            self._umrs_url("knowledge/corpus-attrition-report"),
            params={"all_projects": "false"},
            timeout=20,
        )
        # The body wraps per-project reports: reports.projects.<pid>.
        reports = payload.get("reports") if isinstance(payload, dict) else None
        projects = reports.get("projects") if isinstance(reports, dict) else None
        projects = projects if isinstance(projects, dict) else {}
        report = projects.get(project_id)
        if not isinstance(report, dict):
            # never show another project's numbers under this label
            raise ValueError(f"corpus-attrition report has no entry for {project_id}")
        return {"report": report, "project_id": project_id}

    # -- rag rail (T5 topic-9 — semantic corpus query, caller-scoped) ---

    def rag_query(self, params: dict[str, Any]) -> dict[str, Any]:
        """RAG query across the caller's project attics (forwarded; row-shape
        verbatim; never fabricated when the lane declines)."""
        query = str(params.get("query") or "").strip()
        if not query:
            raise ValueError("rag.query requires query text")
        # RAGQueryRequest requires project_id and caps top_k at 20; default
        # the project like client.rag.query does (TUI audit #48/#80/#87).
        payload: dict[str, Any] = {"query": query, "project_id": self._research_project_id(params)}
        top_k = params.get("top_k")
        if isinstance(top_k, int) and top_k > 0:
            payload["top_k"] = min(top_k, 20)
        return self.session.client.http.post(
            self._umrs_url("rag/query"),
            json_body=payload,
            timeout=30,
        )

    # -- traces + provenance rails (T5 topic-8b — lineage reads) ---------

    def _umrs_url(self, suffix: str) -> str:
        """Rides the rails as do the r4 client prefix family (never invented)."""
        client = self.session.client
        return f"{client._config.gateway_url}{client._api_prefix}/{suffix}"

    def traces_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Single execution trace by id."""
        trace_id = str(params.get("trace_id") or "")
        if not trace_id:
            raise ValueError("traces.get requires trace_id")
        return self.session.client.http.get(self._umrs_url(f"traces/{trace_id}"), timeout=10)

    def traces_job(self, params: dict[str, Any]) -> dict[str, Any]:
        """All execution traces for one job (caller-scoped)."""
        job_id = str(params.get("job_id") or "")
        if not job_id:
            raise ValueError("traces.job requires job_id")
        payload = self.session.client.http.get(
            self._umrs_url(f"traces/job/{job_id}"),
            timeout=10,
        )
        return {
            "traces": payload.get("traces", []) if isinstance(payload, dict) else [],
            "count": payload.get("count") if isinstance(payload, dict) else None,
        }

    def traces_execution(self, params: dict[str, Any]) -> dict[str, Any]:
        """All execution traces for one workflow execution (caller-scoped)."""
        execution_id = str(params.get("execution_id") or "")
        if not execution_id:
            raise ValueError("traces.execution requires execution_id")
        payload = self.session.client.http.get(
            self._umrs_url(f"traces/execution/{execution_id}"),
            timeout=10,
        )
        return {
            "traces": payload.get("traces", []) if isinstance(payload, dict) else [],
            "count": payload.get("count") if isinstance(payload, dict) else None,
        }

    def provenance_job(self, params: dict[str, Any]) -> dict[str, Any]:
        """Provenance record for one job id."""
        job_id = str(params.get("job_id") or "")
        if not job_id:
            raise ValueError("provenance.job requires job_id")
        return self.session.client.http.get(
            self._umrs_url(f"provenance/{job_id}"),
            timeout=10,
        )

    def provenance_lineage(self, params: dict[str, Any]) -> dict[str, Any]:
        """Provenance lineage (version chain) for one job id."""
        job_id = str(params.get("job_id") or "")
        if not job_id:
            raise ValueError("provenance.lineage requires job_id")
        return self.session.client.http.get(
            self._umrs_url(f"provenance/{job_id}/lineage"),
            timeout=10,
        )

    # -- loops rail (T5 topic-8 — research loop list/status + lifecycle control) --

    def loops_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """List research loops (id, status, phase — caller-scoped)."""
        _ = params
        payload = self.session.client.list_loops()
        return {
            "loops": payload.get("loops", []) if isinstance(payload, dict) else [],
            "count": payload.get("count") if isinstance(payload, dict) else None,
        }

    def loops_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Status snapshot of one research loop (caller-scoped)."""
        loop_id = str(params.get("loop_id") or "")
        if not loop_id:
            raise ValueError("loops.status requires loop_id")
        return self.session.client.get_loop_status(loop_id)

    def loops_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        """Stop a running research loop (caller-auth server murder)."""
        return self._loop_control("stop", params)

    def loops_pause(self, params: dict[str, Any]) -> dict[str, Any]:
        """Pause a running research loop."""
        return self._loop_control("pause", params)

    def loops_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        """Resume a paused research loop."""
        return self._loop_control("resume", params)

    def _loop_control(self, verb: str, params: dict[str, Any]) -> dict[str, Any]:
        """loops.<verb> through the resolved runtime (TUI audit #43).

        Under ``umrs`` mode the client call (and its payload) is unchanged;
        under ``local``/``hybrid`` the runtime owns the loop, as for
        ``/research stop``, so a local loop is no longer missed.
        """
        loop_id = str(params.get("loop_id") or "")
        if not loop_id:
            raise ValueError(f"loops.{verb} requires loop_id")
        runtime = self._non_umrs_loop_runtime(params)
        if runtime is None:
            return getattr(self.session.client, f"{verb}_loop")(loop_id)
        from ..research_loop_tools import _runtime_location, _serialize_loop

        state = getattr(runtime, f"{verb}_loop")(loop_id)
        return {**_serialize_loop(state), **_runtime_location(runtime)}

    def _non_umrs_loop_runtime(self, params: dict[str, Any]) -> Any:
        """The local/hybrid ResearchRuntime, or None when UMRS is authoritative.

        An unknown mode token (e.g. ``R4_RUNTIME_MODE=umr``) keeps the UMRS
        client call these verbs always made, rather than failing every
        loop control for a UMRS user.
        """
        from ...runtime import get_runtime
        from ...runtime.resolver import RuntimeResolutionError, _project_metadata, resolve_mode

        client = getattr(self.session, "client", None)
        project_id = str(params.get("project_id") or "").strip() or getattr(
            getattr(client, "config", None), "project_id", None
        )
        try:
            mode = resolve_mode(None, _project_metadata(project_id))
        except RuntimeResolutionError as exc:
            logger.warning("loop control falls back to UMRS: %s", exc)
            return None
        if mode == "umrs":
            return None
        return get_runtime(project_id=project_id, client=client)

    # -- grants rail (T5 topic-7d — live grants received, read-only) -----

    def grants_mine(self, params: dict[str, Any]) -> dict[str, Any]:
        """Live grants received by the caller (user + verified project scope)."""
        _ = params
        payload = self.session.client.list_my_grants()
        return {
            "grants": payload.get("grants", []) if isinstance(payload, dict) else [],
            "count": payload.get("count") if isinstance(payload, dict) else None,
        }

    # -- files rail (TUI function sweep, 2026-09-27) ---------------------
    # The scientist's daily file verbs on the console: upload/download
    # through the gateway's project-namespace Files client.

    def files_upload(self, params: dict[str, Any]) -> dict[str, Any]:
        """Upload one local file into the project bucket (`/gw/files/upload`)."""
        from ...files import FilesClient

        path = str(params.get("path") or "").strip()
        if not path:
            raise ValueError("files.upload params.path is required")
        key = params.get("key")
        purpose = str(params.get("purpose") or "general")
        result = FilesClient().upload_file(
            path,
            key=str(key) if key else None,
            purpose=purpose,
        )
        return {
            "ok": True,
            "key": result.get("key"),
            "bucket": result.get("bucket"),
            "uri": result.get("uri"),
            "size": result.get("size"),
        }

    def files_download(self, params: dict[str, Any]) -> dict[str, Any]:
        """Download one object to a local destination (dest required)."""
        from ...files import FilesClient

        key = str(params.get("key") or "").strip()
        if not key:
            raise ValueError("files.download params.key is required")
        dest = str(params.get("dest") or "").strip()
        if not dest:
            raise ValueError("files.download params.dest is required (a local path to write)")
        result = FilesClient().download_file(key, dest=dest)
        return {"ok": True, "key": key, "dest": dest, "result": result}

    # -- explain rail (TUI function sweep, 2026-09-27) --------------------

    def explain(self, params: dict[str, Any]) -> dict[str, Any]:
        """Look up an error code: cause + remediation from the registry."""
        from ..._cli.explain import _EXPLANATIONS, _lookup_error_meta

        error_id = str(params.get("error_id") or "").strip()
        if not error_id:
            raise ValueError("explain params.error_id is required")
        entry = _EXPLANATIONS.get(error_id)
        if entry is None:
            return {
                "found": False,
                "error_id": error_id,
                "hint": "run `r4 explain --list` for every known error id",
            }
        cause, remediation = entry
        meta = _lookup_error_meta(error_id) or {}
        base = {
            "found": True,
            "error_id": error_id,
            "cause": cause,
            "remediation": remediation,
            "exit_code": meta.get("exit_code"),
            "retryable": bool(meta.get("retryable", False)),
        }
        return base

    def explain_match(self, params: dict[str, Any]) -> dict[str, Any]:
        """Find a known error id inside a free-text message (or none).

        Scans level-style tokens (`auth.failed`-shaped) in order and answers
        only with an id the registry really carries — never a guess.
        """
        import re

        from ..._cli.explain import _EXPLANATIONS

        message = str(params.get("message") or "")
        if not message:
            raise ValueError("explain.match params.message is required")
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]*\.[A-Za-z0-9_.]+", message)
        for token in tokens:
            cleaned = token.strip(".")
            if cleaned in _EXPLANATIONS:
                cause, remediation = _EXPLANATIONS[cleaned]
                return {
                    "found": True,
                    "error_id": cleaned,
                    "cause": cause,
                    "remediation": remediation,
                }
        return {"found": False}

    # -- sweep templates rail (TUI/rccs sweep batch 3, 2026-09-27) ---------

    def sweep_templates(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sweep template pack inventory (read-only)."""
        from ...sweep_templates import list_templates

        _ = params
        rows = [
            {
                "name": t.name,
                "description": t.description,
                "required_params": t.required_params(),
                "params": t.all_param_names(),
            }
            for t in list_templates()
        ]
        return {"templates": rows, "count": len(rows)}

    def sweep_preview(self, params: dict[str, Any]) -> dict[str, Any]:
        """Dry-render one template (in-memory; never writes).

        Required params without a value render as ``<name>`` placeholders, so
        every template previews; optional ``params`` {name: value} override
        them (TUI audit #59/#81).
        """
        from ...sweep_templates import load_template, render_plan

        name = str(params.get("name") or "").strip()
        if not name:
            raise ValueError("sweep.preview params.name is required")
        template = load_template(name)
        combo: dict[str, Any] = {p: f"<{p}>" for p in template.required_params()}
        overrides = params.get("params")
        if isinstance(overrides, dict):
            combo.update(overrides)
        plan = render_plan(template, [combo])
        job = plan[0]
        return {
            "name": template.name,
            "description": template.description,
            "required_params": template.required_params(),
            "job_name": job.job_name,
            "script": job.script,
        }

    # -- evidence rail (TUI/rccs sweep batch 3, 2026-09-27) ----------------

    def evidence_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Evidence rows (V-07 retraction/confidence plane), caller-scoped."""
        payload = self.session.client.list_evidence(
            status=str(params.get("status") or "") or None,
            limit=int(params.get("limit") or 50),
        )
        rows = payload.get("evidence", []) if isinstance(payload, dict) else []
        return {"evidence": rows, "count": len(rows)}

    # -- usage rail (T5 topic-7c — token-usage rows, caller-scoped) -------

    def usage_me(self, params: dict[str, Any]) -> dict[str, Any]:
        """Caller-attributed token usage rows (grouped by model by default)."""
        group_by = str(params.get("group_by") or "model")
        payload = self.session.client.http.get(
            self._umrs_url("usage/me"),
            params={"group_by": group_by},
            timeout=10,
        )
        return {
            "user_id": payload.get("user_id") if isinstance(payload, dict) else None,
            "group_by": group_by,
            "rows": payload.get("rows", []) if isinstance(payload, dict) else [],
        }

    # -- circles rail (ADR-086 — reads; amendment 2026-10-19 adds the roster
    # write verbs the console /circles rides) -----------------------------

    def circles_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Circles the caller owns or belongs to; a ``project_id`` param
        scopes the listing to one project (ADR-086 derivation)."""
        include_retired = bool(params.get("include_retired", False))
        project_id = str(params.get("project_id") or "")
        if project_id:
            payload = self.session.client.list_circles(project_id, include_retired=include_retired)
        else:
            payload = self.session.client.list_my_circles(include_retired=include_retired)
        return {
            "circles": payload.get("circles", []) if isinstance(payload, dict) else [],
            "count": payload.get("count") if isinstance(payload, dict) else None,
        }

    def circles_members(self, params: dict[str, Any]) -> dict[str, Any]:
        """Member rows of one circle (caller-visible surfaces only)."""
        project_id = str(params.get("project_id") or "")
        circle_id = str(params.get("circle_id") or "")
        if not project_id or not circle_id:
            raise ValueError("circles.members requires project_id and circle_id")
        payload = self.session.client.list_circle_members(project_id, circle_id)
        return {
            "members": payload.get("members", []) if isinstance(payload, dict) else [],
            "project_id": project_id,
            "circle_id": circle_id,
        }

    @staticmethod
    def _circle_ids(params: dict[str, Any], verb: str) -> tuple[str, str]:
        """The two ids every circles write verb needs (400-shape refusal)."""
        project_id = str(params.get("project_id") or "").strip()
        circle_id = str(params.get("circle_id") or "").strip()
        if not project_id or not circle_id:
            raise ValueError(f"{verb} requires project_id and circle_id")
        return project_id, circle_id

    def circles_create(self, params: dict[str, Any]) -> dict[str, Any]:
        """Ensure-shape circle creation — ``circle_id == caller`` is the
        personal cell (casual project entry); named circles need standing
        (server-side gate; the four-rung roster ladder is amended ADR-086)."""
        project_id, circle_id = self._circle_ids(params, "circles.create")
        payload = self.session.client.create_circle(
            project_id, circle_id, name=str(params.get("name") or "")
        )
        row = payload.get("circle") if isinstance(payload, dict) else None
        return {
            "circle": row if isinstance(row, dict) else {},
            "created": bool(payload.get("created")) if isinstance(payload, dict) else False,
        }

    def circles_member_add(self, params: dict[str, Any]) -> dict[str, Any]:
        """Roster add (owner / circle admin / project owner / platform admin)."""
        project_id, circle_id = self._circle_ids(params, "circles.member_add")
        user_id = str(params.get("user_id") or "").strip()
        if not user_id:
            raise ValueError("circles.member_add requires user_id")
        role = str(params.get("role") or "member").strip()
        payload = self.session.client.add_circle_member(project_id, circle_id, user_id, role=role)
        row = payload.get("circle") if isinstance(payload, dict) else None
        return {
            "circle": row if isinstance(row, dict) else {},
            "members": payload.get("members", []) if isinstance(payload, dict) else [],
        }

    def circles_member_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        """Roster removal — same ladder; the circle owner is never removed."""
        project_id, circle_id = self._circle_ids(params, "circles.member_remove")
        user_id = str(params.get("user_id") or "").strip()
        if not user_id:
            raise ValueError("circles.member_remove requires user_id")
        payload = self.session.client.remove_circle_member(project_id, circle_id, user_id)
        return {
            "members": payload.get("members", []) if isinstance(payload, dict) else [],
            "project_id": project_id,
            "circle_id": circle_id,
        }

    # -- quota rail (T5 topic-7a — owner-scoped gauge) --------------------

    def quota_units(self, params: dict[str, Any]) -> dict[str, Any]:
        """The caller's two resource poles, verified on the server's side.

        Never fabricated — anonymous callers get the server's canonical
        unauthorized error byte-identical (401 class).
        """
        _ = params
        payload = self.session.client.quota_units()
        return {
            "user_id": payload.get("user_id"),
            "cpu_process": payload.get("cpu_process"),
            "disk_bytes": payload.get("disk_bytes"),
        }

    # -- F4 asset index (read-model, snapshot+patch rails) ---------------

    def assets_snapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        """Read-model snapshot: GET /umrs/assets — the whole row set the caller
        owns (owner-scoped server-side; kinds filter optional).
        Params: kinds (csv, optional), project_id (optional).
        """
        kinds = str(params.get("kinds") or "")
        project_id = str(params.get("project_id") or "")
        payload = self.session.client.list_assets(kinds=kinds, project_id=project_id)
        return {
            "rows": payload.get("rows", []) if isinstance(payload, dict) else [],
            "snapshot_hash": payload.get("snapshot_hash") if isinstance(payload, dict) else None,
        }

    def assets_patch(self, params: dict[str, Any]) -> dict[str, Any]:
        """Patch lane: POST /umrs/assets/patch — caller-held base sha map;
        never fabricate rows (server answers only deltas since that base).
        Params: base (required dict), kinds (optional), project_id (optional).
        """
        base = params.get("base")
        if not isinstance(base, dict):
            raise ValueError("assets.patch requires base dict {shape_key: shape_hash}")
        kinds = str(params.get("kinds") or "")
        project_id = str(params.get("project_id") or "")
        payload = self.session.client.assets_patch(base=base, kinds=kinds, project_id=project_id)
        # CatalogPatchResponse names the deltas upserts/tombstones (TUI audit
        # #76); the TUI contract keeps rows/tombstoned.
        payload = payload if isinstance(payload, dict) else {}
        return {
            "rows": payload.get("upserts", payload.get("rows", [])),
            "tombstoned": payload.get("tombstones", payload.get("tombstoned", [])),
            "snapshot_hash": payload.get("snapshot_hash"),
        }

    def _workflow_policy_guard(self, operation: str) -> None:
        project_id = getattr(getattr(self.session.client, "config", None), "project_id", None)
        policy = load_workflow_policy(project_id=project_id)
        if not policy.allows(operation):
            denied = policy_denied_error(operation, policy)
            raise PermissionError(str(denied["message"]))

    @staticmethod
    def _serialize_workflow_status(status: Any) -> dict[str, Any]:
        return {
            "execution_id": status.execution_id,
            "workflow_name": status.workflow_name or getattr(status, "workflow_id", None),
            "status": status.status,
            "progress": getattr(status, "progress", None),
            "error": getattr(status, "error", None),
            **_run_pin(status),
        }

    @staticmethod
    def _serialize_workflow_result(result: Any) -> dict[str, Any]:
        return {
            "execution_id": result.execution_id,
            "workflow_name": result.workflow_name or getattr(result, "workflow_id", None),
            "status": result.status,
            "artifacts": getattr(result, "artifacts", None),
            "outputs": getattr(result, "outputs", None),
            "error": getattr(result, "error", None),
            **_run_pin(result),
        }

    def workflow_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get UMRS workflow execution status."""
        execution_id = str(params.get("execution_id") or "")
        if not execution_id:
            raise ValueError("execution_id is required")
        status = self.session.client.workflows.status(execution_id)
        return self._serialize_workflow_status(status)

    def workflow_run(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run a registered UMRS workflow."""
        self._workflow_policy_guard("run")
        workflow_name = str(params.get("workflow_name") or params.get("workflow_id") or "")
        if not workflow_name:
            raise ValueError("workflow_name is required")
        parameters = params.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise ValueError("parameters must be an object")
        input_files = params.get("input_files")
        if input_files is not None and not isinstance(input_files, list):
            raise ValueError("input_files must be an array")
        project_id = params.get("project_id")
        wait = bool(params.get("wait", False))
        result = self.session.client.workflows.trigger(
            workflow_name,
            parameters=parameters,
            input_files=input_files,
            project_id=project_id,
            wait=wait,
            version=_version_param(params),
        )
        return self._serialize_workflow_result(result)

    # ── teamup (multi-agent session manager, A-801) ─────────────────────────
    #
    # vernacular contract: same as workflows — rows reflect only what really
    # exists (never fabricated), names stay what the store says, and the
    # answer for unknown ids is a typed not_found, not a blank dict.

    def _agent_model_value(self) -> str | None:
        """The model the host agent's OWN turn would use (2026-09-29 fix).

        Mirrors ``ResearchCompanion._effective_model``: self.model, else the
        client config model. Raw ``agent.model`` alone was the 503 class: a
        None fell through to ``client.chat(model=None)`` → the gateway's
        default deployment (dead ``rivault-gpt-oss-static``) instead of the
        endpoint the chat actually runs on (e.g. public ``kimi-k3``).
        """
        agent = getattr(getattr(self, "_session", None), "agent", None)
        effective = getattr(agent, "_effective_model", None)
        if callable(effective):
            value = effective()
            return str(value) if value else None
        value = getattr(agent, "model", None)
        return str(value) if value else None

    def _teamup_client(self):
        # 2026-09-27 (B-family): the team must ride the chat session's client —
        # its active model and provider (public sources included). A fresh
        # Client() copies the raw config default (`rivault-gpt-oss-static`),
        # which hard-503s when the only live reach is a public provider.
        session_obj = getattr(self, "_session", None)
        session_client = getattr(session_obj, "client", None)
        if session_client is not None:
            return session_client
        if self._team_client is None:
            from r4.client import Client

            self._team_client = Client()
        return self._team_client

    def _teamup_session(self, session_id: str):
        from ...teamup.session import TeamupSession

        sid = session_id or self._teamup_latest_session_id()
        if not sid:
            raise ValueError("teamup session_id is required (none active yet)")
        try:
            return TeamupSession.load(sid, self._teamup_client())
        except (FileNotFoundError, KeyError) as exc:
            raise ValueError(f"teamup session not found: {sid}") from exc

    @staticmethod
    def _teamup_latest_session_id() -> str:
        from ...teamup.session import TeamupSession

        sessions = TeamupSession.list_sessions()
        if not sessions:
            return ""
        latest = max(sessions, key=lambda s: s.get("updated_at") or "")
        return str(latest.get("session_id") or "")

    def teamup_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """List every teamup session known to the local store."""
        _ = params
        from ...teamup.session import TeamupSession

        sessions = TeamupSession.list_sessions()
        return {"sessions": sessions, "count": len(sessions)}

    def teamup_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Status of one session (latest when `session_id` is omitted)."""
        session = self._teamup_session(str(params.get("session_id") or ""))
        return session.get_status()

    # Live team visibility (2026-09-27): a running iteration streams
    # out-of-band {"team_event": {...}} lines — the user follows each
    # agent's status and output as the turn runs, before the reply lands.

    _TEAM_EVENT_PREVIEW_CAP = 1200

    def _emit_team_event(
        self,
        session: Any,
        kind: str,
        *,
        role: Any = None,
        status: str | None = None,
        preview: str | None = None,
        continued: bool | None = None,
        errors: list[dict[str, Any]] | None = None,
        error_text: str | None = None,
    ) -> None:
        """One bounded team_event line; never invented fields."""
        from datetime import datetime, timezone

        payload: dict[str, Any] = {
            "session_id": session.session_id,
            "session_name": session.config.name,
            "kind": kind,
            "iteration": session.iteration,
            "max_iterations": session.config.max_iterations,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
        }
        if role is not None:
            payload["role"] = getattr(role, "value", str(role))
        if status is not None:
            payload["status"] = status
        if preview is not None:
            payload["preview"] = preview
        if continued is not None:
            payload["continued"] = continued
        if errors:
            payload["errors"] = errors
        if error_text is not None:
            payload["error_text"] = error_text
        self._emit_line({"team_event": payload})

    def _emit_team_activity(self, session: Any, role: Any, event: Any) -> None:
        """One bounded agent_activity team_event line (role work facts, 2026-09-28)."""
        from datetime import datetime, timezone

        self._emit_line(
            {
                "team_event": {
                    "session_id": session.session_id,
                    "session_name": session.config.name,
                    "kind": "agent_activity",
                    "iteration": session.iteration,
                    "role": getattr(role, "value", str(role)),
                    "activity_kind": str(getattr(event, "kind", "") or ""),
                    "name": str(getattr(event, "name", "") or "")[:64],
                    "status": str(getattr(event, "status", "") or "")[:32],
                    "preview": str(getattr(event, "preview", "") or "")[:200],
                    "duration_ms": getattr(event, "duration_ms", None),
                    "emitted_at": datetime.now(timezone.utc).isoformat(),
                }
            }
        )

    def _compose_teamup_live_callbacks(self, session: Any) -> None:
        """Wrap the session's callbacks so live events reach the UI too.

        The previous callback chain keeps running (the SPI native engine
        consumes them); the bridge only observes. Session objects are loaded
        fresh per RPC, so wrapping is idempotent here, but the previous
        chain is still restored on exit for repeated reuse of one object.
        """
        prev_status = session._on_agent_status
        prev_message = session._on_agent_message
        prev_iter = session._on_iteration_complete
        prev_activity = session._on_agent_activity
        session._teamup_live_prev = (prev_status, prev_message, prev_iter, prev_activity)

        # Roles inherit the host's approval ladder (2026-10-01): the team's
        # role companions receive the SAME PermissionManager, so final_output,
        # ask_user and gated tools behave like the original agent's.
        session_obj = getattr(self, "_session", None)
        host_permissions = (
            getattr(session_obj, "permissions", None) if session_obj is not None else None
        )
        if host_permissions is not None:
            session._permissions = host_permissions
        _thread_host_usage_hooks(session_obj, session)

        def _status(role: Any, value: str) -> None:
            if prev_status:
                prev_status(role, value)
            self._emit_team_event(session, "agent_status", role=role, status=value)

        def _message(role: Any, content: str) -> None:
            if prev_message:
                prev_message(role, content)
            text = str(content)
            cap = self._TEAM_EVENT_PREVIEW_CAP
            preview = text if len(text) <= cap else text[:cap] + "…"
            self._emit_team_event(session, "agent_message", role=role, preview=preview)

        def _iter_done(iteration: int) -> None:
            if prev_iter:
                prev_iter(iteration)
            self._emit_team_event(session, "iteration_complete")

        def _activity(role: Any, event: Any) -> None:
            if prev_activity:
                prev_activity(role, event)
            self._emit_team_activity(session, role, event)

        session._on_agent_status = _status
        session._on_agent_message = _message
        session._on_iteration_complete = _iter_done
        session._on_agent_activity = _activity

    def _restore_teamup_live_callbacks(self, session: Any) -> None:
        """Undo _compose_teamup_live_callbacks (no-op when never composed)."""
        prev = getattr(session, "_teamup_live_prev", None)
        if prev is None:
            return
        (
            session._on_agent_status,
            session._on_agent_message,
            session._on_iteration_complete,
            session._on_agent_activity,
        ) = prev
        session._teamup_live_prev = None

    @staticmethod
    def _agent_error_tails(session: Any) -> list[dict[str, str]]:
        """Per-role error tails on this session, verbatim (debugging reasons —
        a failed turn must never answer bland `continued` (B2b, 2026-09-27)."""
        tails: list[dict[str, str]] = []
        for role, state in session.agent_states.items():
            errors = getattr(state, "errors", None) or []
            if errors:
                tails.append({"role": str(role), "error": str(errors[-1])[:200]})
        return tails

    def _teamup_iteration_thread(self, session: Any) -> None:
        """Background iteration: events stream live; a run_finished line ends it."""
        import asyncio

        continued = False
        thrown: BaseException | None = None
        try:
            self._compose_teamup_live_callbacks(session)
            self._emit_team_event(session, "iteration_started")
            continued = asyncio.run(session.run_iteration())
        except BaseException as exc:  # noqa: BLE001 — reported on the wire, named.
            thrown = exc
        finally:
            self._restore_teamup_live_callbacks(session)
        errors = self._agent_error_tails(session)
        self._emit_team_event(
            session,
            "run_finished",
            status=session.status.value,
            continued=continued,
            errors=errors,
            error_text=str(thrown)[:400] if thrown else None,
        )

    def teamup_run(self, params: dict[str, Any]) -> dict[str, Any]:
        """START one iteration (async): the reply lands immediately and real
        events stream over team_event (completed shape — B-family 2026-09-27,
        killed the 30 s bridge-timeout class). A second run while busy is a
        named conflict, never a parallel iteration.
        """
        session = self._teamup_session(str(params.get("session_id") or ""))
        session._session_model = self._agent_model_value()
        expected_iteration = session.iteration + 1
        session.run_in_background(self._teamup_iteration_thread)
        return {
            "ok": True,
            "session_id": session.session_id,
            "started": True,
            "expected_iteration": expected_iteration,
            "status": session.status.value,
        }

    def teamup_spawn_agent_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Open one role agent as its own chat session (2026-10-01).

        One role → one persona'd chat session: name rides the team's goal
        slug, system_prompt_override seeds the role's verbatim prompt (+ the
        team context block), and the model mirrors the team's session model
        (never a different endpoint class than the team runs).
        """
        team = self._teamup_session(str(params.get("session_id") or ""))
        role = self._team_role_or_refusal(team, str(params.get("role") or ""))
        name, seed = self._spawn_seed(team, role)

        session_obj = self._store.create_session(self.session.client, name=name)
        session_obj.metadata["system_prompt_override"] = seed
        preferred_model = getattr(team, "_session_model", None) or getattr(
            self.session.agent, "model", None
        )
        if preferred_model:
            session_obj.agent.model = preferred_model
        self._store.save(session_obj)
        return {"session_id": session_obj.session_id, "title": name, "role": role.value}

    @staticmethod
    def _team_role_or_refusal(team: Any, role_value: str) -> Any:
        """Resolve the role by value on this team, with a NAMED refusal."""
        value = role_value.strip().lower()
        role = next((r for r in team.config.roles if r.value == value), None)
        if role is None:
            raise ValueError(
                f"teamup.spawn_agent_session: unknown role {value!r} in {team.config.name}"
            )
        return role

    @staticmethod
    def _spawn_seed(team: Any, role: Any) -> tuple[str, str]:
        """(title, system_prompt_override seed) for the role's chat session."""
        from ...teamup.roles import get_role_spec

        prompt = team.config.role_prompts.get(role.value) or get_role_spec(role).system_prompt
        name = f"{team.config.name}-{role.value}"[:60]
        goal = str(team.config.optimization_goal or team.config.name)
        seed = (
            f"## Team seat: {role.value.upper()} of {team.config.name}\n\n"
            f"Team goal: {goal}\n\n"
            f"{prompt}"
        )
        return name, seed

    def teamup_pause(self, params: dict[str, Any]) -> dict[str, Any]:
        """Pause a running session."""
        session = self._teamup_session(str(params.get("session_id") or ""))
        session.pause()
        return {
            "session_id": session.session_id,
            "status": session.status.value,
        }

    def teamup_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        """Resume a paused session."""
        session = self._teamup_session(str(params.get("session_id") or ""))
        session.resume()
        return {
            "session_id": session.session_id,
            "status": session.status.value,
        }

    def teamup_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        """Cancel the session."""
        session = self._teamup_session(str(params.get("session_id") or ""))
        session.cancel()
        return {
            "session_id": session.session_id,
            "status": session.status.value,
        }

    def teamup_logs(self, params: dict[str, Any]) -> dict[str, Any]:
        """Recent log entries for one session (latest when unspecified)."""
        session = self._teamup_session(str(params.get("session_id") or ""))
        limit = int(params.get("limit") or 10)
        # Entries flow verbatim from the session changelog — never synthesized.
        entries_text = session.get_logs(limit=limit)
        entries = [line for line in entries_text.splitlines() if line.strip()]
        return {
            "session_id": session.session_id,
            "entries": entries,
            "count": len(entries),
        }

    def teamup_report(self, params: dict[str, Any]) -> dict[str, Any]:
        """Full session report (latest when unspecified)."""
        session = self._teamup_session(str(params.get("session_id") or ""))
        import asyncio

        report = asyncio.run(session.generate_report())
        return {
            "session_id": session.session_id,
            "report": report,
        }

    def _identity_project_id(self) -> str | None:
        """Caller's identity project for session stamping (2026-10-01).

        None when the session/config carries none (bare constructions
        included) — never invented.
        """
        try:
            config = self.session.client.config
        except Exception:  # bare server / no client yet
            return None
        return str(getattr(config, "project_id", "") or "") or None

    @staticmethod
    def _teamup_parse_roles(roles_raw: str):
        """Split the roles_csv string into AgentRole entries (never silent coercion)."""
        from ...teamup.roles import AgentKey

        roles = []
        for raw in (token for token in roles_raw.split(",") if token.strip()):
            role = AgentKey(raw).role
            if role is None:
                raise ValueError(f"teamup create: unknown role {raw.strip()!r} (pm/se/pg/cd)")
            roles.append(role)
        return roles

    @staticmethod
    def _teamup_optional_kwargs(params: dict[str, Any]) -> dict[str, Any]:
        """Optional keyword arguments for TeamupSessionConfig — empty-key omission
        (never coaching `None` strings into the typed schema).
        """
        from pathlib import Path

        kwargs: dict[str, Any] = {}
        if params.get("code"):
            kwargs["base_code_path"] = Path(params["code"])
        return kwargs

    def _teamup_build_config(self, params: dict[str, Any]):
        """Aux-builder for teamup_create (extracted so the handler stays slim.

        Raises ValueError with named reasons; never recasts silently.
        """
        from ...teamup.session import TeamupSessionConfig

        name = str(params.get("name") or "").strip()
        if not name:
            raise ValueError("teamup create: name is required")
        config = TeamupSessionConfig(
            name=name,
            roles=self._teamup_parse_roles(str(params.get("roles") or "pm,se,pg")),
            hardware_target=str(params.get("hardware") or "local"),
            max_iterations=int(params.get("max_iterations") or 10),
            optimization_goal=str(params.get("goal") or ""),
            auto_approve=bool(params.get("auto_approve", False)),
            # 2026-10-01: stamp the caller's identity project so the work
            # pane can scope sessions; None stays untagged (honest absence).
            project_id=self._identity_project_id(),
            **self._teamup_optional_kwargs(params),
        )
        return config, list(config.roles)

    def teamup_hardware(self, params: dict[str, Any]) -> dict[str, Any]:
        """Hardware profiles available for a teamup session."""
        from r4.teamup.hardware import list_hardware_profiles

        profiles = list_hardware_profiles()
        return {"profiles": profiles, "count": len(profiles)}

    def teamup_engines(self, params: dict[str, Any]) -> dict[str, Any]:
        """List installed team engines per ADR-020 (engine SPI row verbatim)."""
        from r4.teams import TeamService

        service = TeamService(self._teamup_client())
        rows = []
        for engine in service.list_engines():
            caps = engine.descriptor.capabilities
            rows.append(
                {
                    "name": engine.descriptor.name,
                    "version": engine.descriptor.version,
                    "trust_tier": engine.descriptor.trust_tier.value,
                    "available": engine.available,
                    "note": engine.note,
                    "workflow_styles": [s.value for s in caps.workflow_styles],
                    "supports_streaming": caps.supports_streaming,
                    "supports_checkpointing": caps.supports_checkpointing,
                    "supports_graph_execution": caps.supports_graph_execution,
                    "supports_parallel_agents": caps.supports_parallel_agents,
                    "supports_hierarchical_agents": caps.supports_hierarchical_agents,
                    "supports_distributed_execution": caps.supports_distributed_execution,
                    "supports_multi_model": caps.supports_multi_model,
                    "supports_gpu_workers": caps.supports_gpu_workers,
                    "supports_human_in_loop": caps.supports_human_in_loop,
                }
            )
        return {"engines": rows, "count": len(rows)}

    def teamup_crews(self, params: dict[str, Any]) -> dict[str, Any]:
        """Every crew: ``hpc`` (the teamup preset) and the plan template each
        other crew is an alias for (ADR-092 slice 4), with its aliases."""
        from ...teams.crews import list_crews

        crews = list_crews()
        return {"crews": crews, "count": len(crews)}

    @staticmethod
    def _teamup_goal_and_crew(params: dict[str, Any]) -> tuple[str, str]:
        """(goal, canonical crew) validated for teamup.start, named refusals."""
        from ...teams.crews import crew_name

        goal = str(params.get("goal") or "").strip()
        if not goal:
            raise ValueError("teamup.start params.goal is required")
        try:
            return goal, crew_name(params.get("crew") or "research")
        except KeyError as exc:
            raise ValueError(f"teamup.start refuses {exc.args[0]}") from exc

    @staticmethod
    def _knob_int(params: dict[str, Any], key: str, *, default: int, low: int, high: int) -> int:
        """One clamped integer knob; bad/absent values fall to the default."""
        try:
            return max(low, min(high, int(params.get(key) or default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _knob_driver(params: dict[str, Any]) -> str:
        """ADR-075 G3: driver knob with a NAMED refusal — autonomous team
        sessions land with the embedded driver (U-03); until then the
        server-autonomous path is /loops, said out loud instead of faking."""
        driver = str(params.get("driver") or "manual").strip().lower()
        if driver == "manual":
            return "manual"
        raise ValueError(
            f"teamup.start refuses driver={driver!r}: autonomous team sessions land "
            "with the embedded driver (ADR-075 U-03); use /loops for server-autonomous runs today."
        )

    @staticmethod
    def _teamup_knobs(params: dict[str, Any]) -> dict[str, Any]:
        """Optional-bootstrap knobs for teamup.start."""
        models = params.get("models")
        return {
            "name": (str(params.get("name") or "").strip() or None),
            "max_iterations": CompanionBridgeServer._knob_int(
                params, "max_iterations", default=6, low=1, high=20
            ),
            "models": models if isinstance(models, dict) else None,
            "hardware_target": str(params.get("hardware_target") or "generic"),
            "code_path": (str(params.get("code_path") or "") or None),
            # Parallel role turns per iteration (2026-09-29); bridge clamps
            # 1..8, the session further clamps to the crew's role count.
            "parallel_roles": CompanionBridgeServer._knob_int(
                params, "parallel_roles", default=1, low=1, high=8
            ),
            "driver": CompanionBridgeServer._knob_driver(params),
        }

    def _teamup_start_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """The full teamup.start spec from goal_and_crew + knobs."""
        goal, crew = self._teamup_goal_and_crew(params)
        return {
            "goal": goal,
            "crew": crew,
            **self._teamup_knobs(params),
            "run": bool(params.get("run", True)),
            # 2026-10-01: stamp the caller's identity project for work-pane
            # scoping; None stays untagged (honest absence).
            "project_id": self._identity_project_id(),
        }

    def _teamup_start_guarded(self, session: Any) -> None:
        """Register the session id and fire one async iteration — or refuse."""
        session.run_in_background(self._teamup_iteration_thread)

    def _teamup_bootstrap_thread(
        self, *, spec: dict[str, Any], run: bool, model: str | None
    ) -> None:
        """Background bootstrap: tailor once, then(optionally) the guard +
        first async iteration. Forked from the RPC thread so the single-
        flight mailbox never waits for the tailor meta call (2026-09-27)."""
        from ...teams.crews import HPC_CREW
        from ...teams.service import TeamService
        from ...teamup.bootstrap import bootstrap_team

        try:
            if spec.get("crew") != HPC_CREW:
                self._crew_plan_thread(spec, run)
                return
            # ADR-092 slice 3 (ADR-072 one seam): the team is opened by
            # TeamService, never constructed here.
            service = TeamService(self.session.client, session_model=model)
            session, tailor = bootstrap_team(
                self.session.client, model=model, service=service, **spec
            )
            if run:
                self._teamup_start_guarded(session)
            self._emit_line(
                {
                    "team_event": {
                        "session_id": session.session_id,
                        "session_name": session.config.name,
                        "kind": "team_started",
                        "crew": session.config.crew_pack or spec.get("crew"),
                        "roles": [r.value for r in session.config.roles],
                        "goal": spec.get("goal"),
                        "tailor": {
                            "root": tailor.get("root"),
                            "tailored": tailor.get("tailored", []),
                            "kept_default": tailor.get("kept_default", []),
                            # Present only when the meta call failed — honest
                            # absence otherwise; surfaces name the degradation.
                            **({"degraded": tailor["degraded"]} if tailor.get("degraded") else {}),
                        },
                        "emitted_at": __import__("datetime")
                        .datetime.now(__import__("datetime").timezone.utc)
                        .isoformat(),
                    }
                }
            )
        except Exception as exc:  # noqa: BLE001 — surface on the wire, named.
            self._emit_line(
                {
                    "team_event": {
                        "kind": "team_start_error",
                        "goal": spec.get("goal"),
                        "error_text": str(exc)[:300],
                    }
                }
            )
        finally:
            self._teamup_bootstrapping = False

    def _crew_plan_thread(self, spec: dict[str, Any], run: bool) -> None:
        """A template crew (ADR-092 slice 4): its plan runs through this
        session's plan executor, which asks the human with the plan's card
        (MD-5) on this worker thread. Progress streams as plan ``team_event``
        lines; a refusal or an invalid plan lands as ``team_start_error``."""
        reply = self._plan_executor().start_crew(
            spec["crew"], spec["goal"], models=spec.get("models"), run=run
        )
        if reply.get("ok"):
            self._emit_line(
                {
                    "team_event": {
                        "kind": "crew_plan_started",
                        "goal": spec["goal"],
                        **{
                            k: reply.get(k)
                            for k in ("crew", "template", "run_id", "status", "started")
                        },
                    }
                }
            )
            return
        problems = "; ".join(str(p) for p in reply.get("problems") or [])
        error = str(reply.get("error") or problems or "the crew's plan did not start")
        self._emit_line(
            {
                "team_event": {
                    "kind": "team_start_error",
                    "goal": spec.get("goal"),
                    "crew": spec.get("crew"),
                    "error_text": error[:300],
                }
            }
        )

    def teamup_agent_start(self, params: dict[str, Any]) -> dict[str, Any]:
        """Async team start (B-family, armed-toggle seam): reply NOW, bootstrap
        in a daemon thread (`team_started`/`team_start_error` land when ready).
        """
        spec = self._teamup_start_params(params)
        run = bool(spec.pop("run"))
        model = self._agent_model_value()
        if self._teamup_bootstrapping:
            raise ValueError("teamup.conflict: a team bootstrap is in flight")
        self._teamup_bootstrapping = True
        threading.Thread(
            target=self._teamup_bootstrap_thread,
            kwargs={"spec": spec, "run": run, "model": model},
            daemon=True,
        ).start()
        return {
            "ok": True,
            "accepted": True,
            "goal": spec["goal"],
            "crew": spec["crew"],
        }

    def teamup_tailor(self, params: dict[str, Any]) -> dict[str, Any]:
        """Root-agent prompt tailoring (B3, 2026-09-27): one meta-call from the
        session's PM (or first role) generates every role's prompt from THIS
        project's situation; roles the answer skips keep their templates."""
        from ...teamup.prompt_tailor import tailor_role_prompts

        session = self._teamup_session(str(params.get("session_id") or ""))
        result = tailor_role_prompts(session, self.session.client, model=self._agent_model_value())
        return {
            "session_id": session.session_id,
            **result,
        }

    def teamup_create(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a teamup session (T5: name/roles/hardware/budget/max-iterations).
        Contract: a *created* session is persisted immediately (snapshot) so
        `/teamup status` et al have something real to answer; `start=True`
        starts it contemporaneously (default False × noise, opened only on request).
        """
        config, roles = self._teamup_build_config(params)
        from ...teams.service import TeamService

        # ADR-092 slice 3 (ADR-072 one seam): opened and persisted by
        # TeamService on the native engine, never constructed here.
        session = TeamService(self._teamup_client()).open_team_session(config)
        start = bool(params.get("start", False))
        if start:
            import asyncio

            asyncio.run(session.start())
        return {
            "session_id": session.session_id,
            "name": config.name,
            "status": session.status.value,
            "roles": [role.value for role in roles],
            "started": bool(start),
        }

    def workflow_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        """Cancel a UMRS workflow execution."""
        self._workflow_policy_guard("cancel")
        execution_id = str(params.get("execution_id") or "")
        if not execution_id:
            raise ValueError("execution_id is required")
        status = self.session.client.workflows.cancel(execution_id)
        payload = self._serialize_workflow_status(status)
        payload["ok"] = True
        return payload

    def workflow_results(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch UMRS workflow execution results and artifact keys."""
        execution_id = str(params.get("execution_id") or "")
        if not execution_id:
            raise ValueError("execution_id is required")
        result = self.session.client.workflows.get_results(execution_id)
        return self._serialize_workflow_result(result)

    def workflow_detail(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the raw execution record including per-step statuses.

        Exposes ``results.steps`` so graph UIs can render step-level nodes
        without scraping ``r4 workflow watch`` CLI output.
        """
        execution_id = str(params.get("execution_id") or "")
        if not execution_id:
            raise ValueError("execution_id is required")
        detail = self.session.client.workflows.get_execution_detail(execution_id)
        return {"execution": detail}

    def workflow_approve_gate(self, params: dict[str, Any]) -> dict[str, Any]:
        """Approve or reject a manual-gate step (W5) on a waiting execution."""
        self._workflow_policy_guard("run")
        execution_id = str(params.get("execution_id") or "")
        step_name = str(params.get("step_name") or params.get("step") or "")
        if not execution_id or not step_name:
            raise ValueError("execution_id and step_name are required")
        approved = bool(params.get("approved", True))
        approver = params.get("approver")
        comment = params.get("comment")
        status = self.session.client.workflows.approve_gate(
            execution_id,
            step_name,
            approved=approved,
            approver=str(approver) if approver else None,
            comment=str(comment) if comment else None,
        )
        payload = self._serialize_workflow_status(status)
        payload["ok"] = True
        return payload

    def _research_project_id(self, params: dict[str, Any]) -> str:
        """Project id from params, else the client's configured project."""
        project_id = str(params.get("project_id") or "").strip()
        if project_id:
            return project_id
        configured = getattr(getattr(self.session.client, "config", None), "project_id", None)
        if not configured:
            raise ValueError("project_id is required (no project configured on the client profile)")
        return str(configured)

    def research_loop_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """List live research loops for the project (projection over the runtime)."""
        from ..research_loop_tools import _runtime_for, _runtime_location, _serialize_loop

        project_id = self._research_project_id(params)
        runtime = _runtime_for(self.session.client, project_id)
        loops = [_serialize_loop(state) for state in runtime.list_loops()]
        active = [item for item in loops if item.get("status") in {"running", "paused", "pending"}]
        return {
            "type": "research_loop_list",
            "project_id": project_id,
            "loops": loops,
            "active_count": len(active),
            **_runtime_location(runtime),
        }

    def research_loop_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """One live research loop plus recent iterations."""
        from ..research_loop_tools import _runtime_for, _runtime_location, _serialize_loop

        loop_id = str(params.get("loop_id") or "")
        if not loop_id:
            raise ValueError("loop_id is required")
        project_id = self._research_project_id(params)
        runtime = _runtime_for(self.session.client, project_id)
        try:
            state = runtime.get_loop(loop_id)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise
            # Loop routes are owner-scoped (V-13): another user's loop is a
            # 404 by design — a card, not a traceback.
            return _loop_not_found(loop_id, project_id)
        iterations = runtime.list_iterations(loop_id, limit=5)
        return {
            "type": "research_loop_status",
            "project_id": project_id,
            **_serialize_loop(state),
            **_runtime_location(runtime),
            "recent_iterations": [it.to_dict() for it in iterations[:5]],
        }

    def research_session_view(self, params: dict[str, Any]) -> dict[str, Any]:
        """ADR-025 aggregate view: loops + knowledge digest + authority note."""
        from ...runtime.session_view import research_session_view as _view

        project_id = self._research_project_id(params)
        mode = params.get("mode")
        return _view(self.session.client, project_id, mode=mode)

    def research_knowledge_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Knowledge-graph status projection (read-only). Never mutates —
        reuses the session-view digest surface (client.knowledge.get_graph)."""
        from ...runtime.session_view import _knowledge_digest
        from ..research_loop_tools import _runtime_for, _runtime_location

        project_id = self._research_project_id(params)
        runtime = _runtime_for(self.session.client, project_id)
        digest = _knowledge_digest(self.session.client, project_id)
        return {
            "type": "research_knowledge_status",
            "project_id": project_id,
            **_runtime_location(runtime),
            **digest,
        }

    # -- checkpoint / replay -----------------------------------------------

    def save_checkpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        """Save a manual checkpoint for crash recovery."""
        state = params.get("state") or {}
        agent_id = str(params.get("agent_id") or "__supervisor__")
        cp_id = self._checkpoint_mgr.save_checkpoint(
            session_id=self.session.session_id,
            agent_id=agent_id,
            state=state,
            checkpoint_type="manual",
        )
        return {"checkpoint_id": cp_id}

    def list_checkpoints(self, _params: dict[str, Any]) -> dict[str, Any]:
        """List checkpoints for the active session."""
        checkpoints = self._checkpoint_mgr.list_checkpoints(self.session.session_id)
        return {"checkpoints": checkpoints}

    def load_latest_checkpoint(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Load the latest checkpoint for crash recovery."""
        state = self._checkpoint_mgr.load_latest_checkpoint(self.session.session_id)
        return {"state": state}

    def replay_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Replay events from the event log for timeline display."""
        session_id = str(params.get("session_id") or self.session.session_id)
        limit = int(params.get("limit", 1000))
        events = self._replay.replay_session(session_id, limit=limit)
        return {"events": events, "session_id": session_id}

    def session_side_question(self, params: dict[str, Any]) -> dict[str, Any]:
        """/btw: answer from the current conversation, no tools, not recorded."""
        from ..side_question import answer_side_question

        question = str(params.get("question") or "")
        return {
            "question": question.strip(),
            "answer": answer_side_question(self.session, question),
        }

    def summarize_session(self, params: dict[str, Any]) -> dict[str, Any]:
        """Produce a summary of a session from its events."""
        session_id = str(params.get("session_id") or self.session.session_id)
        summary = self._replay.summarize(session_id)
        return {"summary": summary, "session_id": session_id}

    def get_resources(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Get current HPC resource usage (GPU/CPU/RAM/Slurm)."""
        return {"resources": self._resource_monitor.get_resources()}

    def get_artifacts(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Return artifacts tracked by the ProgressTracker."""
        from ..harness.progress import ProgressTracker

        tracker = ProgressTracker()
        artifacts = list(tracker._state.artifacts)
        return {"artifacts": artifacts}

    # -- task graph ---------------------------------------------------------

    def add_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add a task to the task graph."""
        tg = self._task_graph
        task = tg.add_task(
            title=str(params.get("title") or ""),
            parent_id=params.get("parent_id"),
            depends_on=params.get("depends_on"),
            notes=str(params.get("notes") or ""),
            session_id=self._plan_scope(),
        )
        return {"task": task.to_dict()}

    def get_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get a task by ID."""
        tg = self._task_graph
        task = tg.get_task(str(params.get("task_id") or ""))
        if not task:
            raise ValueError(f"task not found: {params.get('task_id')}")
        return {"task": task.to_dict()}

    def list_tasks(self, _params: dict[str, Any]) -> dict[str, Any]:
        """List all tasks in the graph."""
        tg = self._task_graph
        tasks = [t.to_dict() for t in tg.list_all_tasks()]
        return {"tasks": tasks}

    def update_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Update task status or progress."""
        tg = self._task_graph
        task_id = str(params.get("task_id") or "")
        if "status" in params:
            tg.update_task_status(task_id, str(params["status"]))
        if "progress" in params:
            tg.update_task_progress(task_id, int(params["progress"]))
        task = tg.get_task(task_id)
        return {"task": task.to_dict() if task else None}

    def get_status(self, _params: dict[str, Any]) -> dict[str, Any]:
        try:
            readiness = self.client.readiness()
        except Exception as exc:
            readiness = {"ready": False, "error": str(exc)}
        return {"readiness": readiness, "state": self.snapshot()}

    def backend_dashboard(self, _params: dict[str, Any]) -> dict[str, Any]:
        session = self.session
        approval = "auto" if session_mode(session) == "autopilot" else "ask"
        model_info = session_model_info(session)
        dashboard = collect_backend_snapshot(
            session.client,
            model=model_info["model"] or model_info["profile_default"],
            mode=str(session.metadata.get("interaction_mode") or "chat"),
            approval=approval,
            secure=False,
            local_tools=[],
            skill_registry=session.skills,
            mcp_manager=None,
        )
        return {"dashboard": dashboard, "state": self.snapshot()}

    def get_code_context(self, params: dict[str, Any]) -> dict[str, Any]:
        path = str(params.get("path") or ".")
        return {"code_context": detect_code_context(path)}

    def get_welcome(self, _params: dict[str, Any]) -> dict[str, Any]:
        from r4.ui_brand import brand_banner_text

        try:
            readiness = self.client.readiness()
        except Exception as exc:
            readiness = {"ready": False, "error": str(exc)}
        return {
            "banner": brand_banner_text("companion"),
            "readiness": readiness,
        }

    def shutdown(self, _params: dict[str, Any]) -> dict[str, Any]:
        self._cancel_all_project_jobs()
        detach_bridge_bus(self)
        if self._session is not None:
            self._store.save(self._session)
        self.should_exit = True
        return {"ok": True}

    # -- agent tracking -----------------------------------------------------

    def list_active_agents(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Return a flat list of currently-active agents."""
        agents = self._agent_registry.list_active()
        return {"agents": agents, "active_count": len(agents)}

    def get_agent_tree(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Return the nested agent tree for TUI display."""
        return self._agent_registry.tree()

    def get_agent_status(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the status of a single agent by id."""
        agent_id = str(params.get("agent_id") or "")
        record = self._agent_registry.get(agent_id)
        if record is None:
            return {"agent": None}
        return {"agent": record.to_dict()}

    # -- science loop -------------------------------------------------------

    def get_science_loop(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Get current science loop state for TUI display."""
        session_id = self.session.session_id
        trajectory = self._science_loop.get_trajectory(session_id, limit=20)
        stats = self._science_loop.get_stats(session_id)
        return {
            "trajectory": trajectory,
            "stats": stats,
            "session_id": session_id,
        }

    def record_science_iteration(self, params: dict[str, Any]) -> dict[str, Any]:
        """Record a science loop iteration."""
        session_id = self.session.session_id
        iteration_id = self._science_loop.record_iteration(
            session_id=session_id,
            hypothesis=str(params.get("hypothesis", "")),
            experiment_id=str(params.get("experiment_id", "")),
            reward_total=float(params.get("reward_total", 0.0)),
            performance=float(params.get("performance", 0.0)),
            novelty=float(params.get("novelty", 0.0)),
            information_gain=float(params.get("information_gain", 0.0)),
            reproducibility=float(params.get("reproducibility", 0.0)),
            cost=float(params.get("cost", 0.0)),
            belief_entropy=float(params.get("belief_entropy", 0.0)),
        )
        return {"iteration_id": iteration_id}

    # -- knowledge base -----------------------------------------------------

    def get_knowledge(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Get knowledge base stats for TUI display."""
        session_id = self.session.session_id
        stats = self._knowledge_base.get_stats(session_id)
        patterns = self._knowledge_base.list_patterns(session_id, limit=20)
        lessons = self._knowledge_base.list_lessons(session_id, limit=20)
        return {
            "stats": stats,
            "patterns": patterns,
            "lessons": lessons,
            "session_id": session_id,
        }

    def add_pattern(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add a successful pattern to the knowledge base."""
        session_id = self.session.session_id
        pattern_id = self._knowledge_base.add_pattern(
            session_id=session_id,
            title=str(params.get("title", "")),
            description=str(params.get("description", "")),
            evidence=str(params.get("evidence", "")),
            confidence=float(params.get("confidence", 0.5)),
        )
        return {"pattern_id": pattern_id}

    def add_anti_pattern(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add an anti-pattern to the knowledge base."""
        session_id = self.session.session_id
        pattern_id = self._knowledge_base.add_anti_pattern(
            session_id=session_id,
            title=str(params.get("title", "")),
            description=str(params.get("description", "")),
            evidence=str(params.get("evidence", "")),
            confidence=float(params.get("confidence", 0.5)),
        )
        return {"pattern_id": pattern_id}

    def add_lesson(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add a distilled lesson to the knowledge base."""
        session_id = self.session.session_id
        lesson_id = self._knowledge_base.add_lesson(
            session_id=session_id,
            lesson=str(params.get("lesson", "")),
            context=str(params.get("context", "")),
            category=str(params.get("category", "general")),
        )
        return {"lesson_id": lesson_id}

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        request_id = request.get("id")
        method = str(request.get("method") or "")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return _error_response(request_id, "params must be an object")
        if method in _DEFERRED_METHODS:
            # Slow calls (a model round trip) answer from a worker thread so the
            # stdin loop keeps serving — the reply carries the same request id.
            threading.Thread(
                target=lambda: self._emit_line(self._handle_now(request_id, method, params)),
                daemon=True,
                name=f"bridge-{method}",
            ).start()
            return None
        if self._reads_concurrently(method):
            self._submit_read(request_id, method, params)
            return None
        return self._handle_serialized(request_id, method, params)

    # -- concurrency (plan H-8; classes in rpc_concurrency.py) ---------------

    def _rw(self) -> StateLock:
        """The state lock (lazy for servers built without __init__ in tests).

        (setdefault() returns Any — the explicit get/set keeps mypy happy.)
        """
        lock = self.__dict__.get("_state_lock")
        if lock is None:
            lock = self.__dict__["_state_lock"] = StateLock()
        return cast(StateLock, lock)

    def _reads_concurrently(self, method: str) -> bool:
        """A pool read needs the stdin loop (main or pump) and a live session:
        before ``initialize`` a read would create the session, a mutation.
        A read asked from inside a mutation runs inline, after it."""
        return (
            getattr(self, "_stdin_mux", None) is not None
            and getattr(self, "_session", None) is not None
            and serves_concurrently(method)
            and not self._rw().owned()
        )

    def _submit_read(self, request_id: Any, method: str, params: dict[str, Any]) -> None:
        """Admit a read in arrival order here; a pool thread answers it.

        The shared lock is taken on the calling (stdin) thread, so a mutation
        read after this line waits until this read has replied; the worker
        writes the reply before releasing it.
        """
        lock = self._rw()
        lock.acquire_read()

        def job() -> None:
            try:
                try:
                    reply = self._answer_read(request_id, method, params)
                except Exception as exc:  # noqa: BLE001 — the client always gets a reply
                    reply = _bridge_error_response(request_id, exc)
                self._emit_line(reply)
            finally:
                lock.release_read()

        try:
            pool = self.__dict__.setdefault("_read_pool", ReadPool())
            pool.submit(job)
        except BaseException:
            lock.release_read()
            raise

    def _answer_read(self, request_id: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Answer one read as a detached copy of the wire reply.

        A read may iterate a dict the turn thread is resizing; reads are
        idempotent, so a torn iteration ("changed size during iteration")
        is retried before it is reported.
        """
        reply: dict[str, Any] = {}
        for _attempt in range(_READ_ATTEMPTS):
            try:
                reply = _detached(self._handle_now(request_id, method, params))
            except RuntimeError as exc:
                reply = _bridge_error_response(request_id, exc)
            if not _torn_read(reply):
                return reply
        return reply

    def _handle_serialized(
        self, request_id: Any, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Answer on this thread: reads share the lock, mutations own it.

        A turn only waits for earlier reads (a barrier), then streams
        unlocked so reads sent during it are answered while it runs.
        """
        kind = rpc_class(method)
        lock = self._rw()
        if kind is RpcClass.READ:
            with lock.read():
                return self._handle_now(request_id, method, params)
        if kind is RpcClass.MUTATE:
            with lock.write():
                return self._handle_now(request_id, method, params)
        if kind is RpcClass.TURN:
            with lock.write():
                pass
        return self._handle_now(request_id, method, params)

    def _handle_now(self, request_id: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._dispatch(method, params)
            return {"id": request_id, "ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001 - protocol should return all bridge errors
            return _bridge_error_response(request_id, exc)

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        handler = _bridge_rpc_bindings(self).get(method)
        if handler is None:
            raise ValueError(f"unknown method: {method}")
        return handler(params)

    def _skills_snapshot(self, session: Any = None) -> list[dict[str, Any]]:
        skills = (session if session is not None else self.session).skills
        active_names = {skill.name for skill in skills.list_active()}
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "source": skill.source,
                "active": skill.name in active_names,
                "tools": list(skill.tool_names),
            }
            for skill in skills.list_all()
        ]

    def _progress_snapshot(self, session: Any = None) -> dict[str, Any]:
        harness = getattr(session if session is not None else self.session, "_harness", None)
        if harness is None:
            return {"orientation": None}
        try:
            return {"orientation": harness.progress.orientation()}
        except Exception:
            return {"orientation": None}


def _annotate_registered_user_rows(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark user-config rows whose name is also UMRS-registered as agent-visible.

    Without this a registered local server would display "not agent-visible"
    forever: the merge only DERIVES visibility (it never registers anything —
    registration stays an explicit `r4 mcp register` action).
    """
    registered = {s.get("name") for s in servers if s.get("source") == "registry"}
    for row in servers:
        if not str(row.get("source", "")).startswith("user-config"):
            continue
        if row.get("name") in registered:
            row["agent_visible"] = True
            row["status"] = "registered ✓ (agent-visible as registry entry)"
    return servers


def _bridge_rpc_bindings(server: "CompanionBridgeServer") -> dict[str, Any]:
    """RPC verb → bound handler table (WP-08: module scope for the P9 parity
    contract — the capability registry asserts every claimed bridge verb is
    actually served; see BRIDGE_RPC_NAMES below)."""
    return {
        "initialize": server.initialize,
        "get_state": lambda _params: server.snapshot(),
        "get_governance_overview": server.get_governance_overview,
        "get_session_changes": server.get_session_changes,
        "get_session_plan": server.get_session_plan,
        "trust_grant": server.trust_grant_rpc,
        "trust_list": server.trust_list_rpc,
        "revert_session_change": server.revert_session_change,
        "send_message": server.send_message,
        "confirm_plan": server.confirm_plan,
        "cancel_turn": server.cancel_turn,
        "dispatch_slash_command": server.dispatch_slash_command,
        "list_slash_commands": server.list_slash_commands,
        "project_list": server.project_list,
        "project_current": server.project_current,
        "project_create": server.project_create,
        "project_register": server.project_register,
        "project_delete": server.project_delete,
        "project_switch": server.project_switch,
        "project_import": server.project_import,
        "project_init": server.project_init,
        "project_verify": server.project_verify,
        "project_readiness": server.project_readiness,
        "project_plan": server.project_plan,
        "project_run": server.project_run,
        "project_cancel_job": server.project_cancel_job,
        "project_status": server.project_status,
        "project_findings": server.project_findings,
        "project_ingest": server.project_ingest,
        "project_session_create": server.project_session_create,
        "list_sessions": server.list_sessions,
        "list_models": server.list_models,
        "list_up_models": server.list_up_models,
        "list_stoppable_endpoints": server.list_stoppable_endpoints,
        "set_model": server.set_model,
        "set_interaction_mode": server.set_interaction_mode,
        "load_session": server.load_session,
        "export_session": server.export_session,
        "session.pin": server.session_pin,
        "session.queue_append": server.session_queue_append,
        "session.queue_pop": server.session_queue_pop,
        "session.queue_clear": server.session_queue_clear,
        "session.queue_remote": server.session_queue_remote,
        "session.venue_start": server.session_venue_start,
        "session.venue_status": server.session_venue_status,
        "session.venue_stop": server.session_venue_stop,
        "session.venue_sync": server.session_venue_sync,
        "session.venue_approvals": server.session_venue_approvals,
        "session.venue_decide": server.session_venue_decide,
        # ADR-099 D-8: remote ask_user questions answered from any client.
        "session.venue_questions": server.session_venue_questions,
        "session.venue_answer": server.session_venue_answer,
        # ADR-099 D-2: the session's web-access choice and the server caps.
        "session.web_access_get": server.session_web_access_get,
        "session.web_access_set": server.session_web_access_set,
        "search_server.status": server.search_server_status,
        "search_server.up": server.search_server_up,
        "search_server.down": server.search_server_down,
        "worktree.list": server.worktree_list,
        "worktree.enter": server.worktree_enter,
        "worktree.exit": server.worktree_exit,
        "worktree.mode": server.worktree_mode,
        "session.venue_policy": server.session_venue_policy,
        # ADR-101 D-12: the local tool sandbox.
        "session.sandbox_get": server.session_sandbox_get,
        "session.sandbox_set": server.session_sandbox_set,
        "source.list_remote": server.source_list_remote,
        "source.push": server.source_push,
        "source.unpush": server.source_unpush,
        "set_write_requires_plan": server.set_write_requires_plan,
        "session.unpin": server.session_unpin,
        "session.fork": server.session_fork,
        "session.rename": server.session_rename,
        "session.archive": server.session_archive,
        "session.compact": server.session_compact,
        "session.truncate": server.session_truncate,
        "session.side_question": server.session_side_question,
        "list_skills": server.list_skills,
        "activate_skill": server.activate_skill,
        "list_mcp_servers": server.list_mcp_servers,
        "set_mcp_server_enabled": server.set_mcp_server_enabled,
        "list_mcp_tools": server.list_mcp_tools,
        "mcp_server_health": server.mcp_server_health,
        "knowledge_graph_content": server.knowledge_graph_content,
        "workflow_describe": server.workflow_describe,
        "workflow.package": server.workflow_package,
        "workflow.export": server.workflow_export,
        "permission_rules.list": server.permission_rules_list,
        "permission_rules.remove": server.permission_rules_remove,
        "set_knowledge_acquisition": server.set_knowledge_acquisition,
        "set_workflow_armed": server.set_workflow_armed,
        "get_usage": server.get_usage,
        "get_event_log": server.get_event_log,
        "list_jobs": server.list_jobs,
        "get_job": server.get_job,
        "get_job_log": server.get_job_log,
        "stop_job": server.stop_job,
        "workflow.list": server.workflow_list,
        "assets.snapshot": server.assets_snapshot,
        "assets.patch": server.assets_patch,
        "quota.units": server.quota_units,
        "circles.list": server.circles_list,
        "circles.members": server.circles_members,
        "circles.create": server.circles_create,
        "circles.member_add": server.circles_member_add,
        "circles.member_remove": server.circles_member_remove,
        "files.upload": server.files_upload,
        "files.download": server.files_download,
        "explain": server.explain,
        "explain.match": server.explain_match,
        "sweep.templates": server.sweep_templates,
        "sweep.preview": server.sweep_preview,
        "evidence.list": server.evidence_list,
        # K1.4: a citation chip opens its passage (server-checked fetch).
        "citation.open": server.citation_open,
        # K1.6: /save keeps a turn, verbatim, in project knowledge.
        "knowledge.save": server.knowledge_save,
        "usage.me": server.usage_me,
        "grants.mine": server.grants_mine,
        "loops.list": server.loops_list,
        "loops.status": server.loops_status,
        "loops.stop": server.loops_stop,
        "loops.pause": server.loops_pause,
        "loops.resume": server.loops_resume,
        "traces.get": server.traces_get,
        "traces.job": server.traces_job,
        "traces.execution": server.traces_execution,
        "provenance.job": server.provenance_job,
        "provenance.lineage": server.provenance_lineage,
        "rag.query": server.rag_query,
        "session.turns": server.session_turns,
        "admin.rag_stats": server.admin_rag_stats,
        "admin.corpus_attrition": server.admin_corpus_attrition,
        "workflow.status": server.workflow_status,
        "workflow.run": server.workflow_run,
        "workflow.cancel": server.workflow_cancel,
        "workflow.results": server.workflow_results,
        "workflow.detail": server.workflow_detail,
        "workflow.approve_gate": server.workflow_approve_gate,
        "teamup.list": server.teamup_list,
        "teamup.status": server.teamup_status,
        "teamup.run": server.teamup_run,
        "teamup.start": server.teamup_agent_start,
        "teamup.pause": server.teamup_pause,
        "teamup.spawn_agent_session": server.teamup_spawn_agent_session,
        "teamup.resume": server.teamup_resume,
        "teamup.cancel": server.teamup_cancel,
        "teamup.create": server.teamup_create,
        "teamup.tailor": server.teamup_tailor,
        "teamup.crews": server.teamup_crews,
        "teamup.engines": server.teamup_engines,
        "teamup.logs": server.teamup_logs,
        "teamup.report": server.teamup_report,
        "teamup.hardware": server.teamup_hardware,
        # ADR-092 slice 3: team plans and every TeamService run.
        "team.plan_templates": server.team_plan_templates,
        "team.plan_run": server.team_plan_run,
        "team.plan_result": server.team_plan_result,
        "team.plan_cancel": server.team_plan_cancel,
        "team.runs": server.team_runs,
        "research_loop.list": server.research_loop_list,
        "research_loop.status": server.research_loop_status,
        "research_session.view": server.research_session_view,
        "research_knowledge.status": server.research_knowledge_status,
        "save_checkpoint": server.save_checkpoint,
        "list_checkpoints": server.list_checkpoints,
        "load_latest_checkpoint": server.load_latest_checkpoint,
        "replay_session": server.replay_session,
        "summarize_session": server.summarize_session,
        "get_artifacts": server.get_artifacts,
        "get_resources": server.get_resources,
        "get_science_loop": server.get_science_loop,
        "record_science_iteration": server.record_science_iteration,
        "get_knowledge": server.get_knowledge,
        "add_pattern": server.add_pattern,
        "add_anti_pattern": server.add_anti_pattern,
        "add_lesson": server.add_lesson,
        "add_task": server.add_task,
        "get_task": server.get_task,
        "list_tasks": server.list_tasks,
        "update_task": server.update_task,
        "get_status": server.get_status,
        "get_backend_dashboard": server.backend_dashboard,
        "get_code_context": server.get_code_context,
        "get_welcome": server.get_welcome,
        "shutdown": server.shutdown,
        "list_active_agents": server.list_active_agents,
        "get_agent_tree": server.get_agent_tree,
        "get_agent_status": server.get_agent_status,
    }


BRIDGE_RPC_NAMES: frozenset[str] = frozenset(
    {
        "activate_skill",
        "add_anti_pattern",
        "add_lesson",
        "add_pattern",
        "add_task",
        "assets.patch",
        "assets.snapshot",
        "cancel_turn",
        "confirm_plan",
        "dispatch_slash_command",
        "export_session",
        "get_agent_status",
        "get_agent_tree",
        "get_artifacts",
        "get_backend_dashboard",
        "get_code_context",
        "get_event_log",
        "get_job",
        "get_job_log",
        "get_knowledge",
        "get_resources",
        "get_governance_overview",
        "get_science_loop",
        "get_session_changes",
        "get_session_plan",
        "get_state",
        "get_status",
        "get_task",
        "get_usage",
        "get_welcome",
        "initialize",
        "knowledge_graph_content",
        "list_active_agents",
        "list_checkpoints",
        "list_jobs",
        "list_mcp_servers",
        "list_mcp_tools",
        "set_mcp_server_enabled",
        "list_models",
        "list_sessions",
        "list_skills",
        "list_slash_commands",
        "list_stoppable_endpoints",
        "list_tasks",
        "list_up_models",
        "load_latest_checkpoint",
        "load_session",
        "mcp_server_health",
        "project_cancel_job",
        "project_create",
        "project_current",
        "project_delete",
        "project_findings",
        "project_import",
        "project_ingest",
        "project_init",
        "project_list",
        "project_plan",
        "project_readiness",
        "project_register",
        "project_run",
        "project_session_create",
        "project_status",
        "project_switch",
        "project_verify",
        "quota.units",
        "circles.list",
        "circles.members",
        "circles.create",
        "circles.member_add",
        "circles.member_remove",
        "usage.me",
        "files.upload",
        "files.download",
        "explain",
        "explain.match",
        "sweep.templates",
        "sweep.preview",
        "evidence.list",
        "citation.open",
        "knowledge.save",
        "grants.mine",
        "loops.list",
        "loops.status",
        "loops.stop",
        "loops.pause",
        "loops.resume",
        "traces.get",
        "traces.job",
        "traces.execution",
        "provenance.job",
        "provenance.lineage",
        "rag.query",
        "admin.rag_stats",
        "admin.corpus_attrition",
        "record_science_iteration",
        "revert_session_change",
        "trust_grant",
        "trust_list",
        "replay_session",
        "research_knowledge.status",
        "research_loop.list",
        "research_loop.status",
        "research_session.view",
        "save_checkpoint",
        "send_message",
        "session.archive",
        "session.compact",
        "session.truncate",
        "session.side_question",
        "session.fork",
        "session.pin",
        "session.turns",
        "session.queue_append",
        "session.queue_pop",
        "session.queue_clear",
        "session.queue_remote",
        # Wave 2026-09-21 (ADR-046 A2): frontend-venue runtime + sealed sources.
        "session.venue_start",
        "session.venue_status",
        "session.venue_stop",
        "session.venue_sync",
        "session.venue_approvals",
        "session.venue_decide",
        "session.venue_questions",
        "session.venue_answer",
        "session.web_access_get",
        "session.web_access_set",
        "search_server.status",
        "search_server.up",
        "search_server.down",
        "worktree.list",
        "worktree.enter",
        "worktree.exit",
        "worktree.mode",
        "session.venue_policy",
        "session.sandbox_get",
        "session.sandbox_set",
        "source.list_remote",
        "source.push",
        "source.unpush",
        "set_write_requires_plan",
        "session.rename",
        "teamup.spawn_agent_session",
        "session.unpin",
        "set_interaction_mode",
        "set_knowledge_acquisition",
        "set_workflow_armed",
        "set_model",
        "permission_rules.list",
        "permission_rules.remove",
        "shutdown",
        "stop_job",
        "summarize_session",
        "update_task",
        "workflow.approve_gate",
        "workflow.cancel",
        "workflow.detail",
        "workflow.list",
        "workflow.export",
        "workflow.package",
        "workflow.results",
        "workflow.run",
        "workflow.status",
        "workflow_describe",
        "teamup.cancel",
        "teamup.create",
        "teamup.tailor",
        "teamup.crews",
        "teamup.engines",
        "teamup.hardware",
        "teamup.list",
        "teamup.logs",
        "teamup.pause",
        "teamup.report",
        "teamup.resume",
        "teamup.run",
        "teamup.start",
        "teamup.status",
        "team.plan_templates",
        "team.plan_run",
        "team.plan_result",
        "team.plan_cancel",
        "team.runs",
    }
)


def _venue_error_text(exc: "APIError") -> str:
    """``venue.<name>: <server message>`` — the TUI branches on the prefix.

    The typed code rides the ``x-platform-error-code`` header (r4-contracts
    taxonomy, ``companionvenue.*``); the message keeps the server's own
    explanation (e.g. the missing source names on ``source_not_pushed``).
    """
    code = str((getattr(exc, "details", None) or {}).get("code") or "")
    name = code.split(".", 1)[1] if code.startswith("companionvenue.") else ""
    if not name:
        status = (getattr(exc, "details", None) or {}).get("status_code")
        name = {404: "not_found", 409: "conflict", 503: "unavailable"}.get(status or 0, "error")
    message = str(exc)
    marker = " — "
    if marker in message:
        message = message.split(marker, 1)[1]
    return f"venue.{name}: {message}"


def _lease_remaining_sec(payload: dict[str, Any]) -> int | None:
    lease = payload.get("lease") if isinstance(payload.get("lease"), dict) else None
    expires = str((lease or {}).get("expires_at") or "")
    if not expires:
        return None
    try:
        from datetime import datetime, timezone

        stamp = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0, int((stamp - datetime.now(timezone.utc)).total_seconds()))
    except ValueError:
        return None


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _attempt_row(attempt: Any) -> dict[str, Any]:
    """One durable job attempt in the shape the jobgraph overlay reads."""
    return {
        "attempt_id": attempt.attempt_id,
        "attempt_no": attempt.attempt_no,
        "status": attempt.status,
        "backend": attempt.backend,
        "started_at": _iso(attempt.started_at),
        "ended_at": _iso(attempt.ended_at),
        "detail": attempt.exit_reason or attempt.status_detail,
    }


def _plan_mode_view(session: Any) -> tuple[bool, str]:
    """(awaiting_confirmation, pending_request) from session.metadata.plan_mode_state.

    A plan under revision (keep_planning) is still pending but not awaiting
    a card answer."""
    plan_state = session.metadata.get("plan_mode_state")
    if not isinstance(plan_state, dict):
        return False, ""
    awaiting = bool(plan_state.get("awaiting_confirmation")) and not plan_state.get("revising")
    return awaiting, str(plan_state.get("pending_request") or "")


_VENUE_STATE_TO_SESSION = {"running": "ongoing", "stopped": "stopped", "failed": "error"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str_field(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "")


def _clean_strings(values: Any) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()]


def _provider_kind_for(src: Any) -> str:
    if src.provider:
        return str(src.provider)
    return "openai_compat" if src.kind == "public" else str(src.kind)


def _venue_runtime_meta(session: Any) -> dict[str, Any] | None:
    """Snapshot projection of the recorded remote runtime (None when absent)."""
    raw = session.metadata.get("venue_runtime")
    return dict(raw) if isinstance(raw, dict) else None


def _session_identity_hint(session: Any) -> tuple[str, str]:
    """Display-only ``(username, credential)`` for the session identity chip.

    Same rule as ``r4 auth status``: claims are decoded WITHOUT verification
    for UX display (the server re-validates the JWT per request); the values
    never feed an access decision. ``credential`` is ``jwt`` when a decodable
    token is held (username = its ``sub``), ``api-key`` when credentials
    exist but no JWT decodes, ``none`` when nothing is held or the lookup
    itself fails — the chip renders each state explicitly (2026-10-07:
    ``default/{project}`` was too ambiguous to read as "no account").
    """
    try:
        from ...auth import AuthManager, decode_jwt_claims
    except ImportError:  # pragma: no cover - package layout invariant
        return "", "none"
    try:
        manager = AuthManager(profile=getattr(session.client.config, "profile", "default"))
        token_info = manager.token_store.load(manager.profile)
        raw_token = (
            getattr(token_info, "access_token", None)
            if token_info
            else manager.api_key_manager.get_api_key(manager.profile)
        )
        if not raw_token:
            return "", "none"
        claims = decode_jwt_claims(raw_token) or {}
        username = str(claims.get("sub") or claims.get("username") or "")
        return (username, "jwt") if username else ("", "api-key")
    except Exception:  # noqa: BLE001 - display sugar never breaks a snapshot
        return "", "none"


def _version_param(params: dict[str, Any]) -> str | None:
    """Optional ``version`` of a workflow verb (ADR-113 D-3); None = default."""
    value = str(params.get("version") or "").strip()
    return value or None


def _run_pin(run: Any) -> dict[str, str]:
    """The version and code digest a run pinned (ADR-113 D-4)."""
    return {
        "workflow_version": str(getattr(run, "workflow_version", "") or ""),
        "source_sha256": str(getattr(run, "source_sha256", "") or ""),
    }


def _workflow_catalog_row(workflow: Any) -> dict[str, Any]:
    """One ``workflow.list`` row; the ownership fields let the TUI label a
    row shared with the caller as shared (2026-10-23), not by the owner's scope."""
    return {
        "workflow_name": workflow.name or workflow.workflow_id,
        "name": workflow.name,
        # ADR-113 D-3: the default version and every registered one.
        "version": str(getattr(workflow, "version", "") or ""),
        "versions": list(getattr(workflow, "versions", None) or []),
        "source_sha256": str(getattr(workflow, "source_sha256", "") or ""),
        "description": workflow.description,
        "tags": list(workflow.tags),
        "scope": str(getattr(workflow, "scope", "") or ""),
        "owner": str(getattr(workflow, "owner", "") or ""),
        "shared": getattr(workflow, "shared", False) is True,
        "access": list(getattr(workflow, "access", None) or []),
    }


def _venue_runtime_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """References-only projection of a server venue row for session metadata."""
    lease = _as_dict(payload.get("lease"))
    remote = _as_dict(payload.get("runtime"))
    return {
        "state": _str_field(payload, "state"),
        "reason": _str_field(payload, "reason"),
        "lease_expires_at": lease.get("expires_at"),
        "source_names": list(payload.get("source_names") or []),
        "default_source": _str_field(payload, "default_source"),
        "web_policy": _str_field(payload, "web_policy"),
        "phase": _str_field(remote, "phase"),
    }


def _notice_field(notice: str) -> dict[str, str]:
    """``{"notice": notice}`` when there is one (a snapshot field)."""
    return {"notice": notice} if notice else {}


def _start_web_access(session: Any, params: dict[str, Any]) -> WebAccess | None:
    """The start's web choice: the ``web_policy`` param (the console's
    answer), else the session's own; None when there is neither (the server
    then refuses by name, ADR-099 D-2). A local ``default_off`` is no choice
    (RS-1 round-3 fix N2)."""
    if _str_field(params, "web_policy"):
        try:
            return make_web_access(
                _str_field(params, "web_policy"),
                _clean_strings(params.get("web_allow_domains")),
                source="prompt",
            )
        except WebAccessError as exc:
            raise ValueError(f"web_access.invalid: {exc}") from exc
    return remote_start_choice(WebAccess.from_metadata(session.metadata))


def _pin_remote_start(session: Any, access: WebAccess | None) -> None:
    session.metadata["venue"] = "frontend"
    if access is not None:
        session.metadata["web_access"] = access.to_metadata()


def _venue_start_body(session: Any, refs: list[str], params: dict[str, Any]) -> dict[str, Any]:
    """The start body: the web choice is the one written into the mirrored
    copy (:meth:`_mirror_for_venue`), so start and worker agree (W4). A
    local ``default_off`` is sent as no choice (RS-1 round-3 fix N2)."""
    config = _as_dict(session.metadata.get("config"))
    chosen = remote_start_choice(WebAccess.from_metadata(session.metadata)) or WebAccess("")
    return {
        "source_refs": refs,
        "default_source": _str_field(params, "default_source"),
        "web_policy": chosen.mode,
        "web_allow_domains": list(chosen.domains),
        "project": _str_field(config, "project_id"),
    }


def _journal_payload(session: Any, detail: dict[str, Any]) -> dict[str, Any] | None:
    """Parsed journal payload for THIS session, else None."""
    if str(detail.get("session_id") or "") != str(getattr(session, "session_id", "") or ""):
        return None
    try:
        payload = json.loads(detail.get("payload") or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _journal_message_count(payload: dict[str, Any]) -> int:
    messages = payload.get("messages")
    if isinstance(messages, str):
        try:
            messages = json.loads(messages)
        except json.JSONDecodeError:
            messages = []
    return len(messages) if isinstance(messages, list) else 0


def _require_frontend_capable(session: Any) -> None:
    """Only a local or frontend session may be pinned to the frontend venue."""
    current_venue = str(session.metadata.get("venue") or "")
    if current_venue not in ("", "frontend", "local"):
        raise ValueError(f"venue.bad_request: session venue is {current_venue}, not frontend")


def _turn_id_of(message: Any) -> Any:
    return (getattr(message, "metadata", None) or {}).get("turn_id")


def _user_turn_index(messages: list[Any], turn_id: str) -> int | None:
    """Index of turn ``turn_id``'s user message, None when it is not there."""
    return next(
        (
            index
            for index, message in enumerate(messages)
            if getattr(message, "role", None) == "user" and _turn_id_of(message) == turn_id
        ),
        None,
    )


def _restore_turn_ids(before: list[Any], after: list[Any]) -> None:
    """Re-stamp turn ids on the verbatim tail a /compact kept (TUI audit r2 #17).

    Compaction rebuilds the messages without metadata, so every kept turn
    lost the id session.truncate keys on. The kept tail is the old
    non-system tail unchanged; pair the two from the end and stop at the
    first difference (the summary).
    """
    old_tail = [m for m in before if m.role != "system" and m.content]
    new_tail = [m for m in after if m.role != "system"]
    for old, new in zip(reversed(old_tail), reversed(new_tail)):
        if (old.role, old.content) != (new.role, new.content):
            return
        turn_id = _turn_id_of(old)
        if turn_id:
            new.metadata["turn_id"] = turn_id


def _project_runtime(session: Any, runtime: dict[str, Any]) -> None:
    """Venue runtime row → session metadata (references + mapped state).

    One dict.update: the session.venue_status poll is a pool read (plan H-8),
    and a concurrent snapshot must never see the runtime without its state.
    """
    update: dict[str, Any] = {"venue_runtime": runtime}
    mapped_state = _VENUE_STATE_TO_SESSION.get(runtime["state"])
    if mapped_state:
        update["state"] = mapped_state
    session.metadata.update(update)


def _snapshot_moved(session: Any, detail: dict[str, Any]) -> bool:
    """True when the journal copy differs from the loaded session (server wins)."""
    payload = _journal_payload(session, detail)
    if payload is None:
        return False
    if _journal_message_count(payload) != len(getattr(session, "messages", []) or []):
        return True
    remote_updated = str(payload.get("updated_at") or "")
    local_updated = str(getattr(session, "updated_at", "") or "")
    return bool(remote_updated and local_updated and remote_updated != local_updated)


def _badge_mirrored_rows(response: dict[str, Any], remote_ids: set[str]) -> None:
    """§8a badges on local rows present in the journal (mutates response).

    mirrored normally; 'degraded' when the store's mirror sync_mode reports
    accumulated mirror errors — divergence stays visible, never concealed.
    """
    degraded = response["sync_mode"] == "degraded"
    for row in response["sessions"]:
        if row["id"] in remote_ids:
            row["sync"] = "degraded" if degraded else "mirrored"


def _remote_only_row(snap: dict[str, Any], local_ids: set[str]) -> dict[str, Any] | None:
    """Listing row for a journal snapshot absent locally; None when mirrored/junk."""
    sid = str(snap.get("session_id") or "")
    if not sid or sid in local_ids:
        return None
    return {
        "id": sid,
        "name": str(snap.get("name") or ""),
        "created_at": "",
        "updated_at": str(snap.get("updated_at") or ""),
        "model": "",
        "venue": str(snap.get("venue") or ""),
        "state": str(snap.get("state") or ""),
        "archived_at": str(snap.get("archived_at") or ""),
        "pinned": False,
        "sync": "remote-only",
    }


# RPCs answered from a worker thread (a model round trip must not block the
# stdin loop — e.g. /btw while a turn is running).
# ADR-092 slice 3: team.plan_run / team.plan_cancel wait for a human's
# answer to their card, which may take minutes.
_DEFERRED_METHODS = frozenset({"session.side_question", "team.plan_run", "team.plan_cancel"})


# How long a new turn waits for a stopped turn's worker to unwind.
_STOPPED_TURN_GRACE_SEC = 10.0

# A read retries a torn copy this many times before it reports the error.
_READ_ATTEMPTS = 3


def _detached(reply: dict[str, Any]) -> dict[str, Any]:
    """The reply exactly as the wire would carry it, copied off live state."""
    return json.loads(json.dumps(reply, default=str))


def _torn_read(reply: dict[str, Any]) -> bool:
    """True for a read that failed on a container resized under it."""
    error = reply.get("error") if reply.get("ok") is False else None
    if not isinstance(error, dict) or error.get("type") != "RuntimeError":
        return False
    return "changed size during iteration" in str(error.get("message") or "")


def _arguments_dict(arguments: Any) -> dict[str, Any] | None:
    """A tool call's arguments as a dict: the agent emits the model's JSON
    string, and args_summary_for read only dicts, so every summary on this
    rail was "" (companion sweep 2026-10-21)."""
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) else None
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _message_to_dict(message: Any) -> dict[str, Any]:
    content = str(getattr(message, "content", ""))
    if len(content) > MAX_MESSAGE_CHARS:
        content = f"{content[:MAX_MESSAGE_CHARS]}..."
    return {
        "role": str(getattr(message, "role", "system")),
        "content": content,
        "timestamp": str(getattr(message, "timestamp", "")),
        "metadata": dict(getattr(message, "metadata", {}) or {}),
    }


def _error_response(
    request_id: Any,
    message: str,
    *,
    error_type: str = "BridgeError",
    hint: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The wire shape of a failed request (docs/r4/TUI_REFERENCE.md §12).

    ``message`` and ``hint`` are prose for people; clients branch on
    ``data["code"]`` only. ``session_conflict`` carries ``session_id``,
    ``copy_id`` ("" when nothing unsaved was kept) and ``stage`` (one of
    ``CONFLICT_STAGES``: "before_turn" nothing ran, "after_turn" the turn ran
    and must not be sent again, "" any other save). ``session_corrupt``
    carries ``session_id`` and ``column``.
    """
    error: dict[str, Any] = {"type": error_type, "message": message}
    if hint:
        error["hint"] = str(hint)
    if data:
        error["data"] = dict(data)
    return {"id": request_id, "ok": False, "error": error}


_RATE_LIMIT_HINT = RATE_LIMIT_HINT  # one text: a quota notice keeps it (RS-1 fix Q5)


def _bridge_error_response(request_id: Any, exc: Exception) -> dict[str, Any]:
    """Format bridge errors with API body snippets and recovery hints.

    The console's own remedy wins when the error carries one (a 401's
    ``tui_hint`` names /connect, not the shell's ``r4 auth login`` pipe)."""
    message = str(exc)
    hint = getattr(exc, "tui_hint", None) or getattr(exc, "hint", None)
    if isinstance(exc, APIError):
        detail = _summarize_api_error_body(exc.response_body)
        # The HTTP layer already puts the server's reason in the message;
        # appending it again read "...: too long — {'message': 'too long'}".
        if detail and detail not in message:
            message = f"{message} — {detail}"
        if not hint:
            hint = _hint_for_api_error(exc)
    elif isinstance(exc, RateLimitError) and not hint:
        hint = _RATE_LIMIT_HINT
    # A typed store error names its case for clients (T1 conflict, L3 corrupt row).
    typed = (SessionConflictError, SessionCorruptError, SandboxBridgeError)
    data = exc.data if isinstance(exc, typed) else None
    return _error_response(
        request_id,
        message,
        error_type=type(exc).__name__,
        hint=hint,
        data=data,
    )


def _summarize_api_error_body(body: str | None, *, max_len: int = 240) -> str:
    """Extract a short human-readable detail from an API error body."""
    if not body:
        return ""
    text = body.strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:max_len]
    if isinstance(payload, dict):
        for key in ("detail", "message", "error", "error_message"):
            value = payload.get(key)
            if isinstance(value, dict) and value.get("message"):
                value = value["message"]  # OpenAI shape {"error": {"message": ...}}
            if value:
                text = str(value)
                break
        else:
            text = json.dumps(payload, default=str)
    else:
        text = str(payload)
    return text[:max_len]


def _hint_for_api_error(exc: APIError) -> str | None:
    """Suggest next steps for common chat/completions failures."""
    status = exc.status_code or 0
    body = (exc.response_body or "").lower()
    if status == 400:
        return _hint_for_400_error(body)
    if status == 404:
        return "Model or endpoint not found — run /endpoints or /models."
    if status == 429:
        return _RATE_LIMIT_HINT
    if status == 503 and "quota" in body:
        return "Quota cap reached — read /quota (verified gauge) before retrying."
    if status == 401:
        return "Auth stale — /health to verify connection then /login if needed."
    return None


# ---------------------------------------------------------------------------
# Slash-suggestion engine (T5 topic-10, 2026-09-19)
# Deterministic; no LLM calls. Fires ONLY at the named beneficial moments for
# the matching marker set; never chats twice in one turn, never recommends a
# slash the user already used in this session.
# ---------------------------------------------------------------------------

_SUGGESTION_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    # (rule_name, marker_set (all substrings required at any one moment), suggestion_text)
    (
        "quota-family",
        ("quota",),
        "/quota — read the owner-scoped gauge first",
    ),
    (
        "401-token-stale",
        ("401",),
        "/health — verify the gateway baseline, then /login if prompted",
    ),
    (
        "endpoint-missing",
        ("model not found",),
        "/endpoints — own coverage, then /models to switch",
    ),
    (
        "endpoint-missing",
        ("endpoint not found",),
        "/endpoints — own coverage, then /models to switch",
    ),
    (
        "job-failed",
        ("job failed",),
        "/job <id> — attempt timeline, then /traces <job-id>",
    ),
    (
        "job-404",
        ("job not found",),
        "/session <id> — resume intact, or /sessions to browse",
    ),
    (
        "loop-visible",
        ("research loop",),
        "/loops — lifecycle control (list/status/pause/resume/stop)",
    ),
    (
        "rate-limited",
        ("429",),
        "/quota — owner-scoped gauge before retry",
    ),
    (
        "context-window-full",
        ("context window",),
        "/compact — then /usage if you want the token evidence",
    ),
    (
        "corpus-questions",
        ("corpus",),
        "/rag <question> — semantic query over the project attics",
    ),
    (
        "corpus-questions",
        ("documents",),
        "/rag <question> — semantic query over the project attics",
    ),
    (
        "circles-or-grants",
        ("grant",),
        "/grants — received inventory; /circles for memberships",
    ),
    (
        "circles-or-grants",
        ("circle",),
        "/circles — owned + member inventory",
    ),
    (
        "provision-pending",
        ("provisioning",),
        "/quota — while the venue cooks; /endpoints for the pane view",
    ),
    (
        "provision-pending",
        ("provision",),
        "/quota — while the venue cooks; /endpoints for the pane view",
    ),
    (
        "approval-pending",
        ("approval",),
        "/job <id> — full context before approval firing",
    ),
)

_SUGGESTION_DEFAULT: str | None = None


def _suggest_next_slash(response_text: str) -> str | None:
    """Rule-grounded suggestion post-send_message completion.

    deterministic: marker sets → suggestion; zero-output on idle turns.
    Returns the suggestion line or None (never fabricated).
    """
    body = (response_text or "").lower()
    if not body:
        return None
    for _name, markers, suggestion in _SUGGESTION_RULES:
        if all(m in body for m in markers):
            return suggestion
    return _SUGGESTION_DEFAULT


def _hint_for_400_error(body: str) -> str:
    """Return a recovery hint for a 400-class chat/completions failure."""
    if "quota" in body or "disk_bytes_exceeded" in body or "cpu_process_exceeded" in body:
        return (
            "Quota cap — read /quota for the verified gauge first; only then "
            "/endpoints or /project list to trim your active surface."
        )
    if any(marker in body for marker in ("context", "token", "length", "max_model")):
        return (
            "Context window full — run /compact to summarize history; /usage shows "
            "token rows if you want to inspect the driver. /new for a fresh session."
        )
    if any(
        marker in body for marker in ("tool", "function", "auto tool choice", "tool-call-parser")
    ):
        return (
            "Model may not support tool calling — try /mode chat with another model "
            "or /clear to reset tool state."
        )
    return (
        "Chat request rejected (400). /health for gateway baseline, then /endpoints "
        "for your own lane, or /models to switch."
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="R4 companion JS TUI bridge")
    parser.add_argument("--session-id")
    parser.add_argument("--model")
    parser.add_argument("--skill")
    parser.add_argument("--system")
    parser.add_argument("--source")
    parser.add_argument("--mode")
    parser.add_argument("--fs-root", dest="fs_root")
    # ADR-099 D-2: r4 --web's MODE[:d1,d2] (R4_LAUNCH_WEB_ACCESS when absent).
    parser.add_argument("--web-access", dest="web_access")
    # ADR-101 D-2: r4 --sandbox (R4_LAUNCH_SANDBOX when absent).
    parser.add_argument("--sandbox", dest="sandbox", choices=("on", "off"), default=None)
    parser.add_argument(
        "--yolo",
        "--allow-all-tools",
        dest="allow_all_tools",
        action="store_true",
        default=False,
    )
    return parser.parse_args(argv)


# The durable intent row's words (ADR-057 leg 3) for each decision.
_INTENT_WORDS = {ONCE: "once", ALWAYS: "always", REJECT: "reject"}


def _is_stray_approval(request: object) -> bool:
    return isinstance(request, dict) and "approval_response" in request and "method" not in request


def _is_stray_ask_user(request: object) -> bool:
    return isinstance(request, dict) and "ask_user_response" in request and "method" not in request


def _is_cancel_turn(request: object) -> bool:
    return isinstance(request, dict) and request.get("cancel_turn") is True


def _decode_json_message(line: str) -> dict[str, Any] | None:
    """Decode one bridge wire line into a dict, or None for blank/noise lines."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        message = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return message if isinstance(message, dict) else None


def _matching_response_id(response: object, request_id: int) -> bool:
    """True when *response* is a dict carrying the awaited request id."""
    return isinstance(response, dict) and response.get("id") == request_id


_APPROVAL_DIFF_LINES = sys.maxsize  # uncapped: the approval card shows the whole diff


def _approval_preview(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Server-computed change preview for the approval card (opencode EditBody
    parity): file_write / file_edit asks carry the unified diff so the human
    approves the ACTUAL change, not a one-line description. Empty dict for
    other tools or when the diff is not computable. Never raises — the
    approval must survive an unreadable file."""
    try:
        if tool_name not in {"file_write", "file_edit"}:
            return {}
        path = str(arguments.get("path") or "")
        if not path:
            return {}
        from ..tools import _format_diff

        before = CompanionBridgeServer._read_current(path)
        if before is None:
            return {}
        after = _proposed_after(tool_name, before, arguments)
        if after is None or after == before:
            return {}
        # Every line of the change (the 30-line tool-result cap hid the rest
        # of a write the card approved; w3-py#1). The card scrolls it.
        diff = _format_diff(before, after, path, max_lines=_APPROVAL_DIFF_LINES)
        return {"path": path, "diff": diff} if diff else {}
    except Exception:  # noqa: BLE001 — preview is best-effort
        return {}


def _proposed_after(tool_name: str, before: str, arguments: dict[str, Any]) -> str | None:
    """Reconstruct the post-tool file content for the preview diff."""
    if tool_name == "file_write":
        content = arguments.get("content")
        return content if isinstance(content, str) else None
    return _proposed_edit_after(before, arguments)


def _proposed_edit_after(before: str, arguments: dict[str, Any]) -> str | None:
    """Apply a file_edit's old→new replacement when its count gate passes."""
    old_text = str(arguments.get("old_text") or "")
    new_text = arguments.get("new_text")
    if not old_text or not isinstance(new_text, str):
        return None
    if not _edit_count_ok(before, old_text, arguments):
        return None
    return before.replace(old_text, new_text)


def _edit_count_ok(before: str, old_text: str, arguments: dict[str, Any]) -> bool:
    """Mirror of _file_edit's occurrence gate (unique, or expected_count)."""
    count = before.count(old_text)
    if count == 0:
        return False
    expected = arguments.get("expected_count")
    if expected is None:
        return count == 1
    return count == int(expected)


def _handle_oob_line(server: CompanionBridgeServer, line: str) -> dict[str, Any] | None:
    """Handle out-of-band stdin traffic (approval, ask_user, cancel) during blocked turns."""
    line = line.strip()
    if not line:
        return None
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        return None
    # Never swallow an answer: deliver to a pending waiter first (the stray
    # branch used to drop these and the approval then waited forever).
    if _is_stray_approval(request):
        server._deliver_approval(request)
        return None
    if _is_stray_ask_user(request):
        return None
    if _is_cancel_turn(request):
        server.cancel_turn({})
        return {"handled": True}
    if isinstance(request, dict) and request.get("method") is not None:
        _route_mid_turn_rpc(server, request, line)
    return None


def _route_mid_turn_rpc(server: CompanionBridgeServer, request: dict[str, Any], line: str) -> None:
    """A genuine RPC arrived mid-turn (2026-10-06 fix: the pump used to
    DISCARD it — /btw went permanently silent).

    Plan H-8: deferred verbs answer on their own thread, reads from the read
    pool, control verbs at once; mutations park until the turn ends and then
    run in arrival order.
    """
    if not _answers_mid_turn(str(request.get("method"))):
        server._park_oob_request(line)
        return
    reply = server.handle_request(request)  # None: answered off-thread
    if reply is not None:
        server._emit_line(reply)


def _answers_mid_turn(method: str) -> bool:
    """True for a verb answered while a turn runs (else it parks)."""
    return (
        method in _DEFERRED_METHODS
        or rpc_class(method) is RpcClass.CONTROL
        or serves_concurrently(method)
    )


def _handle_bridge_line(
    server: CompanionBridgeServer,
    line: str,
) -> dict[str, Any] | None:
    """Parse one stdin line; return None when the line should be skipped."""
    try:
        request = json.loads(line)
    except json.JSONDecodeError as exc:
        return _error_response(None, f"invalid JSON: {exc}", error_type="JSONDecodeError")
    if _is_stray_approval(request):
        server._deliver_approval(request)
        return None
    if _is_stray_ask_user(request):
        return None
    if _is_cancel_turn(request):
        server.cancel_turn({})
        return None
    return server.handle_request(request)


def _start_parent_watchdog(mux: "StdinMultiplexer", *, interval: float = 2.0, misses: int = 2):
    """Parent-death watchdog (2026-10-05 orphan-bridge class).

    When the TUI is SIGKILLed (terminal window closed, ssh dropped), stdin
    EOF *usually* fires — but not when the console was re-parented with the
    pipes intact (session leaders, some spawn wrappers). Then the bridge
    just lives forever in `mux.get()`. Two consecutive polls with
    ``getppid() == 1`` (re-parented to init) wake the main loop with the
    same EOF-shaped marker stdin EOF sends, so every consumer resolves and
    the main loop exits through the normal clean path — last-out-finalize,
    never a daemon. Skipped when the process STARTS re-parented (containers
    / daemon context — a watchdog there would fire at boot).
    """
    import os
    import threading
    import time

    if os.getppid() == 1:
        return None

    def _watch() -> None:
        streak = 0
        while True:
            try:
                time.sleep(interval)
            except BaseException:  # noqa: BLE001 — mocks/atexit kill the sleep; daemon goes quiet
                return
            streak = streak + 1 if os.getppid() == 1 else 0
            if streak >= misses:
                mux._route("")
                return

    thread = threading.Thread(target=_watch, daemon=True, name="bridge-ppid-watchdog")
    thread.start()
    return thread


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.fs_root:
        try:
            os.chdir(os.path.expanduser(args.fs_root))
        except OSError as exc:
            print(f"warning: could not enter --fs-root {args.fs_root}: {exc}", file=sys.stderr)
    server = CompanionBridgeServer(
        session_id=args.session_id,
        model=args.model,
        skill=args.skill,
        system=args.system,
        source_name=args.source,
        allow_all_tools=args.allow_all_tools,
        interaction_mode=args.mode,
        fs_root=args.fs_root,
        web_access=args.web_access,
        sandbox=args.sandbox,
    )
    # Bind the JSONL wire before anything can redirect sys.stdout (plan H-8).
    server._wire = sys.stdout
    mux = StdinMultiplexer()
    mux.start()
    _start_parent_watchdog(mux)
    # H18: a bridge stopped by SIGTERM/SIGHUP stops the shell commands it
    # detached (opt-in R4_SHELL_DETACH_ON_TIMEOUT); atexit alone misses them.
    from r4.companion.shell_detach import install_signal_cleanup

    install_signal_cleanup()
    server._stdin_mux = mux
    # ADR-092 slice 3: every team plan run of this process streams as
    # run-keyed team_event lines (a plan the lead started in chat too).
    server._start_plan_event_stream()
    # ADR-114: presence on the local session bus and the inbox watcher.
    start_bridge_bus(server)
    _serve(server, mux)
    return 0


def _serve(server: CompanionBridgeServer, mux: StdinMultiplexer) -> None:
    """The stdin loop: one line at a time, until EOF or ``shutdown``.

    Mutations run here in arrival order; reads are admitted here and
    answered by the read pool (plan H-8), so their replies may come later.
    """
    while True:
        line = mux.get()
        if line is None:
            return
        response = _handle_bridge_line(server, line)
        if response is not None:
            server._emit_line(response)
        # Requests parked during that line's turn run now, after its reply (L2).
        server._drain_oob_parked()
        if server.should_exit:
            return


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    raise SystemExit(main())
