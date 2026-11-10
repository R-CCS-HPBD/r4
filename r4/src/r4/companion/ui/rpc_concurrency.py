"""Bridge RPC concurrency: which verbs read, which mutate (plan H-8, 2026-10-18).

The companion bridge reads JSON lines on one stdin thread. Until H-8 an agent
turn (or a slash command) ran under a pump that parked every RPC except the
three deferred ones until the turn ended, so a poll sent mid-turn was answered
long after the TUI's request timeout, and v3 pollers checked ``turnInFlight``
to stay quiet. Now every served verb has one class here:

``READ``
    Changes no bridge, session or remote state. Served from a small pool of
    daemon threads, during a turn too. A read is admitted (it takes the shared
    side of :class:`StateLock`) on the stdin thread in arrival order, so a
    mutation that arrives later waits for every read that arrived before it.
``MUTATE``
    Changes state. Runs on the stdin thread, one at a time, in arrival order,
    holding the exclusive side of the lock: no read observes it half-applied.
    During a turn it is parked and runs, in order, once the turn has replied.
``TURN``
    Runs an agent turn or a slash command under the pump. Serialized and
    ordered like ``MUTATE`` (it first waits for earlier reads to finish), but
    its body streams: reads sent during it see the turn in progress, as the
    TUI already does through the event stream. A turn sent during another
    turn queues FIFO: it starts after the running turn's reply, so replies
    and event streams never interleave (cycle 3, L2: it ran nested in the
    first turn and answered before it). v3 also queues client-side.
``CONTROL``
    Answered at once on the stdin thread, during a turn too (``cancel_turn``,
    ``search_server.up``, which only hands the start to its own thread).

The capability registry (``r4.capabilities``) keeps its canonical,
cross-transport ``mutating`` flag; this table extends the classification to
every verb ``_bridge_rpc_bindings`` serves, and
``r4/tests/test_bridge_concurrency.py`` fails when a served verb has no class
or when a capability declared mutating is classed ``READ`` here.
"""

from __future__ import annotations

import logging
import queue
import threading
from contextlib import contextmanager
from enum import Enum
from typing import Callable, Iterator

logger = logging.getLogger(__name__)


class RpcClass(str, Enum):
    READ = "read"
    MUTATE = "mutate"
    TURN = "turn"
    CONTROL = "control"


TURN_RPCS: frozenset[str] = frozenset({"send_message", "confirm_plan", "dispatch_slash_command"})

# search_server.up (2026-11-01) only sets a flag and starts its own thread,
# so the console's offer after a first-submit /web answer is not parked
# behind the turn that answer releases.
CONTROL_RPCS: frozenset[str] = frozenset({"cancel_turn", "search_server.up"})

# Verbs that only read: local snapshots, local stores (sqlite with a
# connection per call), local files, and remote GETs or query-only POSTs.
READ_RPCS: frozenset[str] = frozenset(
    {
        # session snapshot and local stores
        "get_state",
        "get_usage",
        "get_event_log",
        "inspect_subagent",
        "get_session_changes",
        "get_session_plan",
        "get_task",
        "list_tasks",
        "list_checkpoints",
        "load_latest_checkpoint",  # returns the stored state; restores nothing
        "replay_session",
        "summarize_session",  # summarizes the event log, no model call
        "get_artifacts",
        "get_resources",
        "get_science_loop",
        "get_knowledge",
        "list_active_agents",
        "get_agent_tree",
        "get_agent_status",
        "list_sessions",
        "export_session",
        "list_skills",
        "list_slash_commands",
        "get_code_context",
        "explain",
        "explain.match",
        "sweep.templates",
        "sweep.preview",  # renders in memory, never writes
        "session.side_question",  # deferred: answers on its own thread
        # project inventory
        "project_list",
        "project_current",
        "project_status",
        "project_findings",
        # remote reads
        "get_status",
        "get_backend_dashboard",
        "get_welcome",
        "get_governance_overview",
        "trust_list",
        "permission_rules.list",
        "list_models",
        "list_up_models",
        "list_stoppable_endpoints",
        "list_mcp_servers",
        "list_mcp_tools",
        "mcp_server_health",
        "knowledge_graph_content",
        "workflow_describe",
        "workflow.package",
        "workflow.list",
        "workflow.status",
        "workflow.results",
        "workflow.detail",
        # A remote GET that streams for up to 600 s into a fresh local zip
        # (``<name>-N.zip``, never an existing file). It changes no bridge,
        # session or remote state, so it is a read: as a MUTATE it held the
        # exclusive lock on the stdin thread and froze the console for the
        # whole download (review fix 2026-11-07).
        "workflow.export",
        "list_jobs",
        "get_job",
        "get_job_log",
        "assets.snapshot",
        "quota.units",
        "circles.list",
        "circles.members",
        "evidence.list",
        "citation.open",  # K1.4: fetches one passage, records nothing
        "usage.me",
        "grants.mine",
        "loops.list",
        "loops.status",
        "traces.get",
        "traces.job",
        "traces.execution",
        "provenance.job",
        "provenance.lineage",
        "rag.query",  # query-only POST
        "admin.rag_stats",
        "admin.corpus_attrition",  # report-only POST
        "session.turns",
        # The status poll refreshes the session's cached venue_runtime/state
        # projection of the remote row in one dict.update (_project_runtime):
        # idempotent, never saved, never half-visible.
        "session.venue_status",
        "session.venue_approvals",
        "session.venue_questions",
        # ADR-099 D-2: the session's web-access choice; the server's caps (GET).
        "session.web_access_get",
        "session.venue_policy",
        # ADR-101: the sandbox status (the backend probe is cached per process).
        "session.sandbox_get",
        # ADR-102: the local SearXNG's state (one /healthz probe, 2 s at most).
        "search_server.status",
        # ADR-108: the registered worktrees (local git reads).
        "worktree.list",
        "source.list_remote",
        "research_loop.list",
        "research_loop.status",
        "research_session.view",
        "research_knowledge.status",
        "teamup.list",
        "teamup.status",
        "teamup.logs",
        "teamup.report",
        "teamup.crews",
        "teamup.engines",
        "teamup.hardware",
        "team.plan_templates",
        "team.plan_result",
        "team.runs",
    }
)

# Reads that still run on the stdin thread (parked during a turn): their
# handlers swap process-global state. project_status / project_findings wrap a
# print()-based CLI handler in contextlib.redirect_stdout, which replaces
# sys.stdout for the whole process; two overlapping swaps restore in the wrong
# order and leave sys.stdout pointing at a dead buffer.
SERIAL_READS: frozenset[str] = frozenset({"project_status", "project_findings"})

MUTATING_RPCS: frozenset[str] = frozenset(
    {
        "initialize",
        "shutdown",
        # session state
        "load_session",
        "set_model",
        "set_interaction_mode",
        "permission_rules.remove",
        "set_knowledge_acquisition",
        "set_workflow_armed",
        "set_write_requires_plan",
        "activate_skill",
        "revert_session_change",
        "session.pin",
        "session.unpin",
        "session.fork",
        "session.rename",
        "session.archive",
        "session.compact",
        "session.truncate",
        "session.queue_append",
        "session.queue_pop",
        "session.queue_clear",
        "session.queue_remote",
        "session.venue_start",
        "session.venue_stop",
        "session.venue_sync",  # adopts the journal row (replaces the session)
        "session.venue_decide",
        "session.venue_answer",
        "session.web_access_set",
        "session.sandbox_set",  # probes, stores, saves, may restart MCP
        "search_server.down",  # removes the local SearXNG container
        "worktree.enter",  # chdirs the bridge; re-roots the session
        "worktree.exit",  # chdirs back; may remove the worktree
        "worktree.mode",  # sets the session's off|auto
        # local stores
        "save_checkpoint",
        "record_science_iteration",
        "add_pattern",
        "add_anti_pattern",
        "add_lesson",
        "add_task",
        "update_task",
        "stop_job",
        "set_mcp_server_enabled",
        # projects (selection, inventory, CLI jobs this process tracks)
        "project_create",
        "project_register",  # POST /gw/projects + the local registry
        "project_delete",
        "project_switch",
        "project_import",
        "project_init",
        "project_verify",
        "project_readiness",
        "project_plan",
        "project_run",
        "project_cancel_job",
        "project_ingest",
        "project_session_create",
        # remote writes
        "trust_grant",
        "source.push",
        "source.unpush",
        "assets.patch",
        "files.upload",
        "files.download",  # writes a local file
        # circles roster writes (ADR-086 amendment 2026-10-19)
        "circles.create",
        "circles.member_add",
        "circles.member_remove",
        "knowledge.save",  # K1.6: a /save ingests one turn into project knowledge
        "loops.stop",
        "loops.pause",
        "loops.resume",
        "workflow.run",
        "workflow.cancel",
        "workflow.approve_gate",
        "teamup.run",
        "teamup.start",
        "teamup.pause",
        "teamup.resume",
        "teamup.cancel",
        "teamup.create",
        "teamup.tailor",
        "teamup.spawn_agent_session",
        "team.plan_run",  # deferred: waits for the human on its own thread
        "team.plan_cancel",  # deferred
    }
)


def _build_classes() -> dict[str, RpcClass]:
    classes: dict[str, RpcClass] = {}
    for names, cls in (
        (READ_RPCS, RpcClass.READ),
        (MUTATING_RPCS, RpcClass.MUTATE),
        (TURN_RPCS, RpcClass.TURN),
        (CONTROL_RPCS, RpcClass.CONTROL),
    ):
        for name in names:
            if name in classes:
                raise ValueError(f"bridge RPC {name!r} has two concurrency classes")
            classes[name] = cls
    return classes


RPC_CLASSES: dict[str, RpcClass] = _build_classes()


def rpc_class(method: str) -> RpcClass:
    """The verb's class; an unknown verb is treated as a mutation (fail safe)."""
    return RPC_CLASSES.get(method, RpcClass.MUTATE)


def serves_concurrently(method: str) -> bool:
    """True for a read the pool may answer, during a turn too."""
    return rpc_class(method) is RpcClass.READ and method not in SERIAL_READS


class StateLock:
    """Readers-writer lock over the bridge's session and state.

    Many reads, or one mutation. Ownerless on the read side: the stdin thread
    admits a read and the pool worker that answers it releases it. The write
    side is re-entrant for its owner, and the owner may also take a read
    (a mutation that answers a read inline never waits on itself).
    """

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer: int | None = None
        self._depth = 0

    def owned(self) -> bool:
        """True when the calling thread holds the write side."""
        return self._writer == threading.get_ident()

    def acquire_read(self) -> None:
        me = threading.get_ident()
        with self._cond:
            while self._writer is not None and self._writer != me:
                self._cond.wait()
            self._readers += 1

    def release_read(self) -> None:
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self) -> None:
        me = threading.get_ident()
        with self._cond:
            if self._writer == me:
                self._depth += 1
                return
            while self._writer is not None or self._readers:
                self._cond.wait()
            self._writer, self._depth = me, 1

    def release_write(self) -> None:
        with self._cond:
            self._depth -= 1
            if self._depth == 0:
                self._writer = None
                self._cond.notify_all()

    @contextmanager
    def read(self) -> Iterator[None]:
        self.acquire_read()
        try:
            yield
        finally:
            self.release_read()

    @contextmanager
    def write(self) -> Iterator[None]:
        self.acquire_write()
        try:
            yield
        finally:
            self.release_write()


class ReadPool:
    """A few daemon threads answering read RPCs.

    Daemon threads, not a ThreadPoolExecutor: an executor's workers are
    joined at interpreter exit, so one read stuck in an HTTP timeout would
    hold the bridge process after shutdown or stdin EOF.
    """

    def __init__(self, workers: int = 8) -> None:
        self._jobs: queue.Queue[Callable[[], None]] = queue.Queue()
        self._size = workers
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def submit(self, job: Callable[[], None]) -> None:
        self._ensure_started()
        self._jobs.put(job)

    def _ensure_started(self) -> None:
        with self._lock:
            while len(self._threads) < self._size:
                name = f"bridge-read-{len(self._threads)}"
                thread = threading.Thread(target=self._work, daemon=True, name=name)
                thread.start()
                self._threads.append(thread)

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception:  # noqa: BLE001 — a job reports its own errors
                logger.exception("bridge read job failed")
