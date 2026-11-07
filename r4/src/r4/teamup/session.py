"""
Teamup Session Manager (A-801)

Manages the lifecycle of multi-agent collaboration sessions: the HPC
optimisation preset (the ``hpc`` crew).

ADR-092 slice 4:

- Every other crew is a plan template (``r4.teams.crews``); a teamup session
  is the HPC preset, and its HPC scaffolding (SOTA tracker, hardware
  profile, the HPC ``CLAUDE.md``, the SOTA status in each role's context)
  is built for that preset only. A crew session persisted before slice 4
  (``crew_pack`` research, code, paper-writing ...) still loads and runs,
  without it (``TeamupSessionConfig.hpc_preset``).
- Role keys are read through ``AgentKey``, so any spelling a snapshot holds
  loads; a key outside the preset is skipped with a warning.
- The compute ``BudgetTracker`` is retired: nothing ever charged it, so its
  stop rule never fired and its "used" was always 0. Role rounds are capped
  by the lead chat session's caps (MD-7) and a spec run by its run's
  ``BudgetMeter``; compute by UMRS jobs (ADR-067). The SOTA
  ``target_performance`` stop rule is retired too: nothing records a SOTA
  measurement, so it never fired either.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Optional

from .comms import AgentCommunication
from .hardware import get_hardware_profile
from .role_agent import RoleActivityEvent, run_role_turn
from .roles import AgentKey, AgentRole, get_default_roles, get_role_spec
from .sota import SOTATracker

if TYPE_CHECKING:
    from r4.client import Client

logger = logging.getLogger(__name__)

# The HPC optimisation preset's crew name (``r4.teams.crews.HPC_CREW``).
HPC_CREW = "hpc"

# Session storage location
TEAMUP_SESSIONS_DIR = Path(
    os.environ.get("R4_TEAMUP_SESSIONS_DIR")
    or os.environ.get("R4_TEAMUP_SESSIONS_DIR")
    or os.path.expanduser("~/.config/r4/teamup_sessions")
)


class SessionStatus(Enum):
    """Status of a teamup session."""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStatus(Enum):
    """Status of an agent in a session."""

    IDLE = "idle"
    THINKING = "thinking"
    EXECUTING = "executing"
    WAITING = "waiting"
    ERROR = "error"


@dataclass
class AgentState:
    """State of an agent in a session."""

    role: AgentRole
    status: AgentStatus = AgentStatus.IDLE
    current_task: Optional[str] = None
    last_action_at: Optional[datetime] = None
    errors: list[str] = field(default_factory=list)
    messages_sent: int = 0
    messages_received: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "role": self.role.value,
            "status": self.status.value,
            "current_task": self.current_task,
            "last_action_at": self.last_action_at.isoformat() if self.last_action_at else None,
            "errors": self.errors,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentState:
        """Restore persisted activity without resetting counters or errors."""
        last_action = data.get("last_action_at")
        role = AgentKey(data["role"]).role
        if role is None:
            raise ValueError(f"agent key {data['role']!r} is not a role of the HPC preset")
        return cls(
            role=role,
            status=AgentStatus(data.get("status", "idle")),
            current_task=data.get("current_task"),
            last_action_at=datetime.fromisoformat(last_action) if last_action else None,
            errors=list(data.get("errors", [])),
            messages_sent=int(data.get("messages_sent", 0)),
            messages_received=int(data.get("messages_received", 0)),
        )


@dataclass
class TeamupSessionConfig:
    """Configuration for a teamup session."""

    name: str
    roles: list[AgentRole] = field(default_factory=get_default_roles)
    hardware_target: str = "generic"
    max_iterations: int = 50
    base_code_path: Optional[Path] = None
    optimization_goal: Optional[str] = None
    timeout_minutes: int = 120
    auto_approve: bool = False  # Auto-approve optimization attempts
    verbose: bool = False
    # "hpc" (or None) for the HPC preset; any other value is a crew session
    # persisted before ADR-092 slice 4, when crews were teamup sessions.
    crew_pack: Optional[str] = None
    # D-29 (suggestion 10): per-role model names (role.value -> model). Empty
    # entry = the broker default (honest absence, never invented).
    role_models: dict[str, str] = field(default_factory=dict)
    # D-29 / ADR-072: per-role system prompts (role.value -> prompt) that
    # replace the built-in HPC-optimisation role prompts — how a TeamSpec's
    # agent instructions (and the research crew) reach the teamup runtime.
    role_prompts: dict[str, str] = field(default_factory=dict)
    # Roles run concurrently per iteration when > 1 (bounded by role count).
    # Trade-off: parallel roles no longer see each other's same-iteration
    # changelog entries in their context. 1 = the sequential pipeline order.
    parallel_roles: int = 1
    # ADR-075 G3: who advances iterations. "manual" (user/agent runs them)
    # is the only honest value until the embedded autonomous driver lands
    # (U-03); "autonomous" is refused by name at every entry surface.
    driver: str = "manual"
    # 2026-10-01: owning project — lets the work pane scope sessions to the
    # active project. None for legacy/untagged sessions (honest absence).
    project_id: Optional[str] = None

    @property
    def hpc_preset(self) -> bool:
        """True for the HPC preset, the only kind created since ADR-092 slice 4.

        False only for a crew session persisted before slice 4: it keeps
        running, without the HPC scaffolding.
        """
        return (self.crew_pack or HPC_CREW) == HPC_CREW

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "roles": [r.value for r in self.roles],
            "hardware_target": self.hardware_target,
            "max_iterations": self.max_iterations,
            "base_code_path": str(self.base_code_path) if self.base_code_path else None,
            "optimization_goal": self.optimization_goal,
            "timeout_minutes": self.timeout_minutes,
            "auto_approve": self.auto_approve,
            "verbose": self.verbose,
            "crew_pack": self.crew_pack,
            "role_models": dict(self.role_models),
            "role_prompts": dict(self.role_prompts),
            "parallel_roles": self.parallel_roles,
            "driver": self.driver,
            "project_id": self.project_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeamupSessionConfig":
        """Create from dictionary.

        Role keys go through ``AgentKey`` (ADR-092 slice 4), so any spelling
        a snapshot holds loads. Keys of the retired ``compute_budget`` and
        ``target_performance`` knobs in an old snapshot are ignored.
        """
        roles = _preset_roles(data.get("roles", []), where=str(data.get("name")))
        base_path = Path(data["base_code_path"]) if data.get("base_code_path") else None
        return cls(
            name=data["name"],
            roles=roles or get_default_roles(),
            hardware_target=data.get("hardware_target", "generic"),
            max_iterations=data.get("max_iterations", 50),
            base_code_path=base_path,
            optimization_goal=data.get("optimization_goal"),
            timeout_minutes=data.get("timeout_minutes", 120),
            auto_approve=data.get("auto_approve", False),
            verbose=data.get("verbose", False),
            crew_pack=data.get("crew_pack"),
            role_models=_by_agent_key(data.get("role_models")),
            role_prompts=_by_agent_key(data.get("role_prompts")),
            parallel_roles=int(data.get("parallel_roles") or 1),
            driver=str(data.get("driver") or "manual"),
            project_id=data.get("project_id") or None,
        )


def _preset_roles(raw: Any, *, where: str) -> list[AgentRole]:
    """The HPC preset roles a snapshot names, in order, read through
    ``AgentKey``; a key outside the preset is skipped with a warning."""
    roles: list[AgentRole] = []
    for item in raw or []:
        role = AgentKey(item).role
        if role is None:
            logger.warning("teamup session %s: skipping role key %r (not HPC preset)", where, item)
        elif role not in roles:
            roles.append(role)
    return roles


def _by_agent_key(raw: Any) -> dict[str, str]:
    """A per-role map with its keys normalised through ``AgentKey``."""
    return {AgentKey(key).value: value for key, value in dict(raw or {}).items()}


@dataclass
class SessionSnapshot:
    """Snapshot of session state for persistence."""

    session_id: str
    config: TeamupSessionConfig
    status: SessionStatus
    agent_states: dict[str, AgentState]
    iteration: int
    started_at: datetime
    updated_at: datetime
    current_sota: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "config": self.config.to_dict(),
            "status": self.status.value,
            "agent_states": {k: v.to_dict() for k, v in self.agent_states.items()},
            "iteration": self.iteration,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "current_sota": self.current_sota,
        }


class TeamupSession:
    """
    Multi-agent teamup session manager (A-801).

    Manages the lifecycle of a multi-agent collaboration for HPC optimization
    (the HPC preset; ADR-092 slice 4).
    """

    _active_runs: ClassVar[dict[Path, TeamupSession]] = {}
    # Class-level role-turn factory fallback (2026-09-28): instance attr
    # wins, then this class attr, then role_agent._default_factory. Test
    # adapters monkeypatch THIS (reverted automatically) to install a
    # turn engine for freshly-loaded objects too.
    _subcompanion_factory: ClassVar[Callable | None] = None
    _active_runs_lock: ClassVar[threading.Lock] = threading.Lock()

    def run_in_background(self, target: Callable[[TeamupSession], None]) -> None:
        """Claim this local run once across UI/tool entry points, then start it."""
        key = self.session_dir.resolve()
        with self._active_runs_lock:
            if key in self._active_runs:
                raise ValueError(
                    f"teamup.conflict: iteration already running for {self.session_id} "
                    "(this is not an error — the team is alive; follow it in Work pane → Enter)"
                )
            if self.status not in (SessionStatus.CREATED, SessionStatus.RUNNING):
                raise ValueError(f"teamup.not_running: session is {self.status.value}")
            self._active_runs[key] = self
        try:
            if self.status == SessionStatus.CREATED:
                self.status = SessionStatus.RUNNING
                self._save_snapshot()
            threading.Thread(target=self._background_run, args=(target, key), daemon=True).start()
        except BaseException:
            self._release_run(key)
            raise

    def _background_run(self, target: Callable[[TeamupSession], None], key: Path) -> None:
        try:
            target(self)
        finally:
            self._release_run(key)

    def _release_run(self, key: Path) -> None:
        with self._active_runs_lock:
            if self._active_runs.get(key) is self:
                del self._active_runs[key]

    def __init__(
        self,
        config: TeamupSessionConfig,
        client: "Client",
        session_id: Optional[str] = None,
        session_dir: Optional[Path] = None,
    ):
        """
        Initialize a teamup session.

        Args:
            config: Session configuration
            client: R4 client for LLM access
            session_id: Existing session ID (for resume)
            session_dir: Directory for session data
        """
        self.config = config
        self.client = client
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.session_dir = session_dir or (TEAMUP_SESSIONS_DIR / self.session_id)
        self.status = SessionStatus.CREATED
        self.iteration = 0
        self.started_at = datetime.now()
        self.updated_at = datetime.now()

        # Initialize agent states
        self.agent_states: dict[str, AgentState] = {}
        for role in config.roles:
            self.agent_states[role.value] = AgentState(role=role)

        # Initialize communication
        self.comms = AgentCommunication(self.session_dir / "agent-shared")

        # HPC scaffolding, for the HPC preset only (ADR-092 slice 4): the
        # SOTA tracker and the hardware profile. A legacy crew session has
        # neither (None), and its roles' context carries no SOTA section.
        hpc = config.hpc_preset
        self.sota = SOTATracker(self.session_dir / "agent-shared") if hpc else None
        self.hardware = get_hardware_profile(config.hardware_target) if hpc else None

        # Runtime-only default model (B-family, 2026-09-27): set by the
        # caller (e.g. the bridge wires the host session's active model so
        # public-source users are not forced back to the profile default).
        # NEVER serialized — the honest config change is `role_models`.
        self._session_model: Optional[str] = None

        # Runtime-only role-turn engine (2026-09-28): a callable
        # factory (client, *, model, temperature, max_tool_rounds,
        # permission_manager) → a small companion; tests/adapters inject.
        self._subcompanion_factory: Optional[Callable] = None

        # Runtime-only PermissionManager shared from the HOST session (bridge
        # sets it at callback composition, CompanionSession passes it down):
        # roles inherit the parent's approval ladder — final_output/ask_user
        # unblocked, risky tools gated identically to the main agent
        # (2026-10-01; never serialized).
        self._permissions: Any = None

        # Runtime-only turn path of an SPI engine run (ADR-092 slice 9): the
        # native engine routes every role turn through its run's AgentBroker
        # (``(role, context, system_prompt, model) -> final text``), the one
        # governed turn path; None keeps the role-turn runtime. Never
        # serialized.
        self._agent_turn: Optional[Callable[..., str]] = None

        # Runtime-only usage hooks of the HOST chat session,
        # ``(record_usage, guard_llm_call)`` (ADR-092 MD-7, 2026-10-11): role
        # rounds charge that session's usage records and LLM-call caps, as a
        # team plan's agents do. None (the CLI) charges nothing; never
        # serialized.
        self._usage_hooks: Any = None
        # The HOST chat session's web-access accessor (ADR-099 D-9): role
        # turns' web tools follow the lead's choice. None: R4_WEB_ACCESS,
        # else they refuse. Runtime-only, never serialized.
        self._web_access: Any = None

        # Callbacks
        self._on_agent_message: Optional[Callable[[AgentRole, str], None]] = None
        self._on_iteration_complete: Optional[Callable[[int], None]] = None
        # Live per-agent lifecycle hook (TUI team visibility, 2026-09-27).
        # Fired with "thinking" at turn start, "idle" after a turn, "error"
        # on failure. Unset = inert; consumers never get invented states.
        self._on_agent_status: Optional[Callable[[AgentRole, str], None]] = None
        # Live per-agent WORK facts (tool calls, thinking, tool results) fired
        # by the role_agent turn engine (2026-09-28). Same honesty rule: only
        # real events, never invented.
        self._on_agent_activity: Optional[Callable[[AgentRole, RoleActivityEvent], None]] = None

        # Serializes hook dispatch — parallel role turns (parallel_roles > 1)
        # fire status/message/activity from concurrent worker threads.
        self._hooks_lock = threading.Lock()

    def _fire_hook(self, name: str, *args: Any) -> None:
        """Dispatch an optional session hook under the hooks lock (parallel-safe)."""
        hook = getattr(self, name, None)
        if hook is None:
            return
        with self._hooks_lock:
            hook(*args)

    def _record_agent_error(self, role: AgentRole, exc: Exception) -> None:
        """One honest error path for every turn loop (sequential or parallel)."""
        logger.error(f"Agent {role.value} failed: {exc}")
        self.agent_states[role.value].errors.append(str(exc))
        self.agent_states[role.value].status = AgentStatus.ERROR
        self._fire_hook("_on_agent_status", role, "error")

    def _parallel_workers(self) -> int:
        """Bounded fan-out width for this iteration (1 = sequential order)."""
        return max(1, min(int(self.config.parallel_roles or 1), len(self.config.roles)))

    def _ensure_session_dir(self) -> None:
        """Ensure session directory exists with proper structure."""
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Create agent-shared directory
        agent_shared = self.session_dir / "agent-shared"
        agent_shared.mkdir(exist_ok=True)

        # Create directories for each agent role
        for role in self.config.roles:
            role_dir = self.session_dir / f"agent-{role.value}"
            role_dir.mkdir(exist_ok=True)

        # Create the HPC preset's CLAUDE.md rules file (HPC scaffolding).
        claude_md = agent_shared / "CLAUDE.md"
        if self.config.hpc_preset and not claude_md.exists():
            self._create_claude_md(claude_md)

        # Create initial ChangeLog.md
        changelog = agent_shared / "ChangeLog.md"
        if not changelog.exists():
            self.comms.init_changelog()

        # Copy base code if provided
        if self.config.base_code_path:
            base_code_dir = self.session_dir / "base-code"
            if not base_code_dir.exists():
                src = self.config.base_code_path
                if src.is_dir():
                    shutil.copytree(src, base_code_dir)
                elif src.is_file():
                    base_code_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, base_code_dir / src.name)
                else:
                    logger.warning(f"base code path not found, skipping copy: {src}")

    def _create_claude_md(self, path: Path) -> None:
        """Create CLAUDE.md rules file."""
        content = f"""# Teamup Session Rules: {self.config.name}

## Session ID: {self.session_id}
## Created: {self.started_at.isoformat()}

## Active Agents
{chr(10).join(f"- **{r.value.upper()}**: {get_role_spec(r).name}" for r in self.config.roles)}

## Hardware Target
- **Type**: {self.config.hardware_target}
- **Profile**: {self.hardware.name if self.hardware else "generic"}

## Optimization Goal
{self.config.optimization_goal or "General performance optimization"}

## Communication Protocol

1. **ChangeLog.md**: Primary communication channel
   - Each agent writes to their designated section
   - Read other agents' sections for updates
   - Include timestamps and action descriptions

2. **SOTA Files**: Performance tracking
   - `sota_local.txt`: Agent's local best
   - `sota_family.txt`: Technique family best
   - `sota_hardware.txt`: Hardware target best
   - `sota_project.txt`: Overall project best

3. **Directory Structure**
   - `agent-shared/`: Shared communication files
   - `agent-pm/`: PM working directory
   - `agent-se/`: SE working directory
   - `agent-pg/`: PG working directory
   - `base-code/`: Original code (read-only)
   - `optimizations/`: Optimization variants

## Constraints
- Auto-approve: {"Yes" if self.config.auto_approve else "No (PM approval required)"}
- Max iterations: {self.config.max_iterations}
- Timeout: {self.config.timeout_minutes} minutes
"""
        path.write_text(content)

    async def start(self) -> None:
        """Start the teamup session."""
        logger.info(f"Starting teamup session: {self.session_id}")

        self._ensure_session_dir()
        self.status = SessionStatus.RUNNING
        self.started_at = datetime.now()
        self._save_snapshot()

        # Log initial message
        await self.comms.add_entry(
            agent="SYSTEM",
            action="SESSION_START",
            description=f"Teamup session started: {self.config.name}",
            details={
                "session_id": self.session_id,
                "roles": [r.value for r in self.config.roles],
                "hardware": self.config.hardware_target,
            },
        )

    def _terminal_guard(self) -> bool:
        """True when this iteration should finish the session early.

        The one stop rule is the iteration cap. The compute-budget and
        SOTA-target rules are retired (ADR-092 slice 4): nothing charged the
        budget or recorded a SOTA measurement, so neither ever fired.
        """
        if self.iteration >= self.config.max_iterations:
            logger.info("Max iterations reached")
            self.status = SessionStatus.COMPLETED
            return True
        return False

    async def _run_agent_turns(self) -> None:
        """Run role turns — config order (default) or bounded-parallel."""
        if self._parallel_workers() <= 1:
            for role in self.config.roles:
                if self.status != SessionStatus.RUNNING:
                    break
                await self._guarded_agent_turn(role)
            return
        semaphore = asyncio.Semaphore(self._parallel_workers())

        async def _turn(role: AgentRole) -> None:
            async with semaphore:
                await self._guarded_agent_turn(role)

        await asyncio.gather(*[_turn(role) for role in self.config.roles])

    async def _guarded_agent_turn(self, role: AgentRole) -> None:
        """One status-checked, error-contained async turn."""
        if self.status != SessionStatus.RUNNING:
            return
        try:
            await self._run_agent_turn(role)
        except Exception as e:
            self._record_agent_error(role, e)

    async def run_iteration(self) -> bool:
        """
        Run one iteration of the multi-agent loop.

        Returns:
            True if session should continue, False if complete/failed
        """
        if self.status != SessionStatus.RUNNING:
            return False
        if self._terminal_guard():
            self.updated_at = datetime.now()
            self._save_snapshot()
            return False

        self.iteration += 1
        logger.info(f"Running iteration {self.iteration}")

        await self._run_agent_turns()

        finished = self.status != SessionStatus.RUNNING or self._terminal_guard()
        self.updated_at = datetime.now()
        self._save_snapshot()

        self._fire_hook("_on_iteration_complete", self.iteration)

        return not finished

    async def _run_agent_turn(self, role: AgentRole) -> None:
        """Run a single agent's turn as a small agent companion (2026-09-28)."""
        state = self.agent_states[role.value]
        spec = get_role_spec(role)

        state.status = AgentStatus.THINKING
        state.last_action_at = datetime.now()
        self._fire_hook("_on_agent_status", role, "thinking")

        context = await self._build_agent_context(role)
        prompt = self.config.role_prompts.get(role.value) or spec.system_prompt

        final_text = await asyncio.to_thread(
            self._role_turn,
            role,
            context,
            prompt,
            self.config.role_models.get(role.value) or self._session_model,
        )

        state.status = AgentStatus.EXECUTING
        state.messages_received += 1

        # Execute actions from the final answer (changelog is the contract).
        await self._execute_agent_response(role, final_text)

        state.status = AgentStatus.IDLE
        state.messages_sent += 1

        self._fire_hook("_on_agent_status", role, "idle")
        # Full content; consumers bound their own previews (the SPI
        # adapter caps at 4000, the bridge caps at 1200). The ChangeLog
        # holds 500; truncating here hid work from every live surface.
        self._fire_hook("_on_agent_message", role, final_text)

    async def _build_agent_context(self, role: AgentRole) -> str:
        """Build context message for an agent (the sync builder's text)."""
        return self._build_agent_context_sync(role)

    async def _execute_agent_response(self, role: AgentRole, response: str) -> None:
        """Execute an agent's response and log to changelog."""
        # Log the agent's response to changelog
        await self.comms.add_entry(
            agent=role.value.upper(),
            action="RESPONSE",
            description=response[:1200] + ("..." if len(response) > 1200 else ""),
        )

    def pause(self) -> None:
        """Pause the session."""
        if self.status == SessionStatus.RUNNING:
            self.status = SessionStatus.PAUSED
            self._save_snapshot()

    def resume(self) -> None:
        """Resume a paused session."""
        if self.status == SessionStatus.PAUSED:
            self.status = SessionStatus.RUNNING
            self._save_snapshot()

    def cancel(self) -> None:
        """Cancel the session."""
        self.status = SessionStatus.CANCELLED
        self._save_snapshot()

    def _save_snapshot(self) -> None:
        """Save session snapshot to disk."""
        snapshot = SessionSnapshot(
            session_id=self.session_id,
            config=self.config,
            status=self.status,
            agent_states=self.agent_states,
            iteration=self.iteration,
            started_at=self.started_at,
            updated_at=self.updated_at,
            current_sota=self._project_best().value if self._project_best() else None,
        )

        snapshot_path = self.session_dir / "session.json"
        with open(snapshot_path, "w") as f:
            json.dump(snapshot.to_dict(), f, indent=2)

    def _resolved_role_prompt(self, role: AgentRole) -> tuple[str, str]:
        """The verbatim prompt one role runs under + its source.

        Returns (prompt, source) where source is "override" when
        ``config.role_prompts`` carries the role (ADR-072 / SPI teams) and
        "default" for the built-in ``ROLE_SPECS`` prompt. Never paraphrased.
        """
        override = self.config.role_prompts.get(role.value)
        if override:
            return override, "override"
        return get_role_spec(role).system_prompt, "default"

    def get_status(self) -> dict[str, Any]:
        """Get current session status."""
        agents: dict[str, Any] = {}
        for key, state in self.agent_states.items():
            row = state.to_dict()
            prompt, prompt_source = self._resolved_role_prompt(state.role)
            row["prompt"] = prompt
            row["prompt_source"] = prompt_source
            row["model"] = self.config.role_models.get(state.role.value) or None
            agents[key] = row
        return {
            "session_id": self.session_id,
            "name": self.config.name,
            "crew_pack": self.config.crew_pack or None,
            "status": self.status.value,
            "iteration": self.iteration,
            "max_iterations": self.config.max_iterations,
            "agents": agents,
            "current_sota": self._project_best().to_dict() if self._project_best() else None,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def get_logs(self, agent: Optional[str] = None, limit: int = 20) -> str:
        """Get recent log entries."""
        return self.comms.get_recent_entries(agent=agent, limit=limit)

    def _project_best(self) -> Any:
        """The HPC preset's project-level SOTA entry, or None (no tracker on
        a legacy crew session, no measurement recorded)."""
        return self.sota.get_project_best() if self.sota is not None else None

    async def generate_report(self) -> str:
        """Generate final report (A-812).

        ADR-092 slice 4 fix: it called ``SOTATracker.get_best_score``, which
        never existed, so every session with an optimisation goal crashed
        ``r4 teamup report`` and ``teamup.report``. Performance now reads the
        tracker's own summary, for the HPC preset only.
        """
        return f"""# Teamup Session Report: {self.config.name}

## Summary
- **Session ID**: {self.session_id}
- **Status**: {self.status.value}
- **Iterations**: {self.iteration} / {self.config.max_iterations}
- **Duration**: {(self.updated_at - self.started_at).total_seconds() / 60:.1f} minutes

## Agent Activity
{self._format_agent_summary()}
{self._performance_report()}
## Recommendations
{self._recommendations()}
"""

    def _performance_report(self) -> str:
        """The HPC preset's performance sections (empty for a legacy crew)."""
        if self.sota is None:
            return ""
        best = self._project_best()
        return f"""
## Performance
- **Best Achieved**: {best.format() if best else "No measurements"}
- **Improvement**: {self._improvement_text()}

## Optimization History
{self.sota.get_history_report()}
"""

    def _improvement_text(self) -> str:
        """Best against the first measurement, from the tracker's own summary."""
        summary = self.sota.get_improvement_summary() if self.sota is not None else {}
        if not summary.get("has_data"):
            return "No measurements"
        return f"{summary['improvement_from_first']:+.1f}% over the first measurement"

    def _format_agent_summary(self) -> str:
        """Format agent activity summary."""
        lines = []
        for role_str, state in self.agent_states.items():
            lines.append(
                f"- **{role_str.upper()}**: {state.messages_sent} actions, "
                f"status: {state.status.value}"
            )
        return "\n".join(lines)

    def _recommendations(self) -> str:
        """What the session's own record supports: the goal, the measured
        improvement (HPC preset) and the most active role. Never invented."""
        if not self.config.optimization_goal:
            return "No optimization goal set."
        lines = [f"Goal: {self.config.optimization_goal}"]
        if self.sota is not None:
            lines.append(f"Improvement: {self._improvement_text()}")
        active = max(self.agent_states.values(), key=lambda a: a.messages_sent, default=None)
        if active:
            lines.append(f"Most active: {active.role.value} ({active.messages_sent} actions)")
        return "\n".join(lines)

    @classmethod
    def get_current(cls) -> Optional[str]:
        """Return the most recently updated session ID, or None when no sessions exist."""
        sessions = cls.list_sessions()
        if not sessions:
            return None
        return sessions[0]["session_id"]

    @classmethod
    def load(cls, session_id: str, client: "Client") -> "TeamupSession":
        """Load an existing session from disk."""
        session_dir = TEAMUP_SESSIONS_DIR / session_id
        with cls._active_runs_lock:
            active = cls._active_runs.get(session_dir.resolve())
            if active is not None:
                return active
        snapshot_path = session_dir / "session.json"

        if not snapshot_path.exists():
            raise ValueError(f"Session not found: {session_id}")

        with open(snapshot_path) as f:
            data = json.load(f)

        config = TeamupSessionConfig.from_dict(data["config"])
        session = cls(
            config=config,
            client=client,
            session_id=data["session_id"],
            session_dir=session_dir,
        )
        session.status = SessionStatus(data["status"])
        session.iteration = data["iteration"]
        session.started_at = datetime.fromisoformat(data["started_at"])
        session.updated_at = datetime.fromisoformat(data["updated_at"])
        for raw_key, state in data.get("agent_states", {}).items():
            key = AgentKey(raw_key)  # ADR-092 slice 4: any persisted spelling
            if key.value in session.agent_states:
                session.agent_states[key.value] = AgentState.from_dict({**state, "role": key})

        return session

    @classmethod
    def list_sessions(cls) -> list[dict[str, Any]]:
        """List all available sessions."""
        sessions = []

        if not TEAMUP_SESSIONS_DIR.exists():
            return sessions

        for session_dir in TEAMUP_SESSIONS_DIR.iterdir():
            if not session_dir.is_dir():
                continue

            snapshot_path = session_dir / "session.json"
            if snapshot_path.exists():
                try:
                    with open(snapshot_path) as f:
                        data = json.load(f)
                    config = data["config"]
                    sessions.append(
                        {
                            "session_id": data["session_id"],
                            "name": config["name"],
                            "status": data["status"],
                            "iteration": data["iteration"],
                            "updated_at": data["updated_at"],
                            # 2026-10-01: project scope for the work pane;
                            # None on legacy snapshots (honest absence).
                            "project_id": config.get("project_id"),
                            # ADR-092 slice 3: the Work pane's agents and
                            # cost columns, read from the same snapshot.
                            "roles": list(config.get("roles") or []),
                            "max_iterations": config.get("max_iterations"),
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to load session {session_dir.name}: {e}")

        return sorted(sessions, key=lambda x: x["updated_at"], reverse=True)

    @classmethod
    def create(
        cls,
        config: TeamupSessionConfig,
        client: "Client",
    ) -> "TeamupSession":
        """
        Create a new teamup session (convenience factory method).

        Args:
            config: Session configuration
            client: R4 client for LLM access

        Returns:
            New TeamupSession instance
        """
        session = cls(config=config, client=client)
        session._ensure_session_dir()
        session.status = SessionStatus.RUNNING
        session._save_snapshot()
        return session

    def run_iteratively(self):
        """
        Synchronous iterator for CLI usage (A-806).

        Yields:
            Tuple of (iteration_number, agent_outputs_dict)
        """
        if self.status != SessionStatus.RUNNING:
            self.status = SessionStatus.RUNNING

        while not self._terminal_guard():
            self.iteration += 1
            agent_outputs = self._run_turns_sync()
            yield self.iteration, agent_outputs

            self.updated_at = datetime.now()
            self._save_snapshot()

    def _run_turns_sync(self) -> dict[AgentRole, str]:
        """Run one sync turn per configured role (bounded-parallel when > 1)."""
        workers = self._parallel_workers()
        if workers <= 1:
            agent_outputs: dict[AgentRole, str] = {}
            for role in self.config.roles:
                if self.status != SessionStatus.RUNNING:
                    break
                agent_outputs[role] = self._guarded_turn_sync(role)
            return agent_outputs
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="teamup-turn") as pool:
            results = [pool.submit(self._guarded_turn_sync, role) for role in self.config.roles]
            return {role: future.result() for role, future in zip(self.config.roles, results)}

    def _guarded_turn_sync(self, role: AgentRole) -> str:
        """One status-checked, error-contained sync turn."""
        if self.status != SessionStatus.RUNNING:
            return ""
        try:
            return self._run_agent_turn_sync(role)
        except Exception as e:
            self._record_agent_error(role, e)
            return f"[ERROR: {e}]"

    def _run_agent_turn_sync(self, role: AgentRole) -> str:
        """Run a single agent's turn synchronously (small agent companion)."""
        state = self.agent_states[role.value]
        spec = get_role_spec(role)

        state.status = AgentStatus.THINKING
        state.last_action_at = datetime.now()
        self._fire_hook("_on_agent_status", role, "thinking")

        # Build context for this agent
        context = self._build_agent_context_sync(role)
        prompt = self.config.role_prompts.get(role.value) or spec.system_prompt

        final_text = self._role_turn(
            role,
            context,
            prompt,
            self.config.role_models.get(role.value) or self._session_model,
        )

        state.status = AgentStatus.EXECUTING
        state.messages_received += 1

        # Log the agent's response
        self.comms.add_entry_sync(
            agent=role.value.upper(),
            action="RESPONSE",
            description=final_text[:1200] + ("..." if len(final_text) > 1200 else ""),
        )

        state.status = AgentStatus.IDLE
        state.messages_sent += 1

        self._fire_hook("_on_agent_status", role, "idle")
        # Full content (same contract as the async turn): consumers
        # bound their own previews.
        self._fire_hook("_on_agent_message", role, final_text)

        return final_text

    def _role_turn(
        self, role: AgentRole, context: str, system_prompt: str, model: Optional[str]
    ) -> str:
        """One role turn: the run's AgentBroker when an engine wired one
        (ADR-092 slice 9), else the role-turn runtime. Both are the one turn
        runtime (``role_agent.run_agent_turn``); there is no other."""
        if self._agent_turn is not None:
            return self._agent_turn(role, context, system_prompt, model)
        final_text, _activities = run_role_turn(
            self, role, context, system_prompt=system_prompt, model=model
        )
        return final_text

    def _build_agent_context_sync(self, role: AgentRole) -> str:
        """Build context message for an agent.

        The SOTA status is HPC scaffolding: the HPC preset only (ADR-092
        slice 4). The retired compute budget is no longer listed.
        """
        changelog_content = self.comms.get_recent_entries(limit=10)
        sota = (
            f"\n### SOTA Status\n{self.sota.get_status_report()}\n" if self.sota is not None else ""
        )
        consider = (
            "Consider the recent activity and current SOTA."
            if self.sota is not None
            else "Consider the recent activity."
        )
        return f"""## Current Session State

**Iteration**: {self.iteration}
**Session**: {self.config.name}
**Goal**: {self.config.optimization_goal or self.config.name}

### Recent Activity
{changelog_content}
{sota}
### Your Turn
What is your next action as {role.value.upper()}?
{consider} Provide your action and reasoning.
"""

    def get_summary(self) -> str:
        """Get a summary of the session (sync version for CLI)."""
        sota_best = self._project_best()
        duration = (self.updated_at - self.started_at).total_seconds() / 60

        return f"""# Teamup Session Summary: {self.config.name}

**Session ID**: {self.session_id}
**Status**: {self.status.value}
**Iterations**: {self.iteration} / {self.config.max_iterations}
**Duration**: {duration:.1f} minutes

## Agent Activity
{self._format_agent_summary()}

## Best Performance
{sota_best.value if sota_best else "No measurements recorded"}
"""
