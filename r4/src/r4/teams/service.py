"""TeamService — client-side facade for team runs (ADR-020 §8).

Owns: registry, resolution, run directory layout, RuntimeServices wiring,
and RunHandle lifecycle. Server-side persistence (UMRS team_runs registry,
REST routes) lands in a later phase; runs persist locally under
``~/.config/r4/team_runs/<run_id>/`` as append-only JSONL + result files.

ADR-092 D-8: a plan run resumes as attempt N+1 in the same run directory.
``resolution.json`` records the attempt and what is needed to rebuild the
spec; the journal there is an attempt-local cache, not a job store.

ADR-092 slice 7 (MD-3, MD-4): when a UMRS source is configured, a plan
run's durable record is UMRS's (a client-hosted ``research_loop_job`` plus
``execution_traces``) and the run directory is its cache. The run's UMRS
attempt is opened before the engine starts; ``durable=True`` requires it
(refused by name when there is no UMRS source or it cannot be opened, and
the run stops if a record cannot be written), otherwise a failure is named
and the run is local-only. A resume on a machine without the run directory
rebuilds it from UMRS (``hydrate``) and replays the records kept there.

ADR-092 D-3, D-6: every run gets the governed ``RoleTurnAgentBroker``, and
a plan spec's models are preflighted through it before the engine starts,
so an ADR-062 refusal is named at submit time with nothing spent; that
includes the client's configured default when an agent names no model.
D-7: the broker is handed the spec's budgets, so the wall clock and tool
call ceilings are checked before every model round, not only before an
agent starts.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from r4_contracts.team_engine.results import CheckpointRef
from r4_contracts.team_engine.spec import TeamSpec, WorkflowStyle
from r4_teamengine_spi.context import AgentBroker, BudgetMeter, RunContext, RuntimeServices
from r4_teamengine_spi.engine import EngineRun

from .brokers import (
    ClientModelBroker,
    FileEventSink,
    NullWorkerBroker,
    PolicyToolBroker,
    RunArtifactBroker,
    TokenBudgetMeter,
    TranscriptMemoryBroker,
    _line_count,
    utcnow,
)
from .durable_records import (
    DurableRecordUnavailable,
    DurableRunRecords,
    umrs_records_for,
)
from .plan.workspace import PROJECT_ROOT_KEY
from .registry import EngineRegistry, RegisteredEngine
from .resolver import Resolution, resolve_engine

logger = logging.getLogger(__name__)

AUTO = "auto"  # TeamService(records=AUTO): UMRS when a source is configured

# Beside the rest of r4's state: $XDG_CONFIG_HOME/r4, else ~/.config/r4.
TEAM_RUNS_DIR = Path(
    os.environ.get("R4_TEAM_RUNS_DIR")
    or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "r4" / "team_runs"
)


@dataclass
class RunHandle:
    """Submitted run: engine run + supervision context."""

    run_id: str
    run_dir: Path
    engine_name: str
    resolution: Resolution
    engine_run: EngineRun
    services: RuntimeServices

    def events(self):
        return self.engine_run.events()

    async def cancel(self, reason: str = "user cancel") -> None:
        await self.engine_run.cancel(reason)

    async def result(self):
        return await self.engine_run.result()

    async def metrics(self):
        return await self.engine_run.metrics()

    def state(self) -> str:
        return self.engine_run.state()

    def graph(self):
        return self.engine_run.graph()


class TeamService:
    """Entry point for r4 SDK/CLI team runs.

    ``permission_manager``, ``session_model`` and ``usage_hooks`` are the
    lead's governance handles (ADR-092 D-6, D-7): the agent broker forks
    the manager per agent, defaults agents to the session's model, and
    charges every model round to the chat session's usage records. They
    are held here so the broker can be built per run; the CLI passes none
    and the broker then fails closed. ``run_grants`` are the SAFE tools a
    human-approved plan card granted this service's runs (ADR-092 MD-6);
    the chat tool builds one service per approved call.
    """

    def __init__(
        self,
        client: Any,
        *,
        registry: EngineRegistry | None = None,
        run_root: Path = TEAM_RUNS_DIR,
        policy: dict | None = None,
        permission_manager: Any = None,
        session_model: str | None = None,
        usage_hooks: Any = None,
        web_access: Any = None,
        run_grants: Any = (),
        gate_lane: Any = None,
        observer: Any = None,
        project_root: str | os.PathLike[str] | None = None,
        records: Any = AUTO,
    ) -> None:
        self._client = client
        # ADR-092 slice 7: the UMRS plan-record adapter (``UmrsPlanRecords``),
        # resolved from the client when AUTO; None keeps every run local-only.
        self._records = records
        self.registry = registry or EngineRegistry(policy)
        self._run_root = Path(run_root)
        self._policy = policy or {}
        self._permission_manager = permission_manager
        self._session_model = session_model
        self._usage_hooks = usage_hooks
        # ADR-099 D-9 (RS-1 fix W3): the lead session's web-access accessor;
        # its agents' web tools follow it (None: R4_WEB_ACCESS, else refuse).
        self._web_access = web_access
        self._run_grants = frozenset(run_grants or ())
        # ADR-092 slice 5: the project a plan's agents work in (their
        # workspace, or what an isolated workspace is made from and merged
        # back into). Read at submit, default the process's directory, and
        # recorded with the run so a resume works in the same tree.
        self._project_root = os.fspath(project_root) if project_root else None
        # Who answers a plan gate when there is no lead manager (the CLI's
        # terminal); with a lead manager, its approval callback does.
        self._gate_lane = gate_lane
        # ADR-092 slice 3: told every agent's rounds and tool calls live
        # (``observer(kind, agent, payload)``); a surface streams them.
        self._observer = observer

    def list_engines(self) -> list[RegisteredEngine]:
        return self.registry.list()

    # ── interactive roster teams (ADR-072 one seam; ADR-092 slice 3) ─────────

    def open_team_session(self, config: Any, *, chat_client: Any = None, running: bool = False):
        """Create and persist an interactive roster team on the native engine.

        The chat surfaces (the ``teamup_start`` tool, the bridge's
        ``teamup.start`` and ``teamup.create``, the teamup CLI) built the
        teamup runtime themselves, beside the SPI: the ADR-072 bypass. They
        now ask here. The engine comes from this service's registry, so its
        SPI version check and the site policy apply (a site that disables
        ``native`` refuses interactive teams by name), and the engine builds
        and persists the session. The session then runs iteration by
        iteration under its surface, as before (ADR-092 D-12 keeps the
        ``teamup.*`` RPCs and the persisted sessions).

        The lead's handles held on this service go to the session: its model
        (``session_model``), its permission manager, and its usage hooks
        (MD-7). ``chat_client`` is the client the roles chat through, by
        default this service's client. ``running`` marks the session started.
        """
        engine = self.registry.get(ROSTER_SESSION_ENGINE)
        opener = getattr(engine, "open_session", None)
        if not callable(opener):
            raise ValueError(
                f"team engine {ROSTER_SESSION_ENGINE!r} cannot open interactive team sessions"
            )
        client = chat_client if chat_client is not None else self._client
        session = opener(config, client, running=running)
        if self._session_model:
            session._session_model = self._session_model
        if self._permission_manager is not None:
            session._permissions = self._permission_manager
        if self._usage_hooks is not None:
            session._usage_hooks = self._usage_hooks
        if self._web_access is not None:
            session._web_access = self._web_access
        return session

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Every team run recorded on this machine, newest first (slice 3).

        Two kinds, one list (the Work pane's teams section): ``plan`` runs
        (and any other SPI run) from this service's run root, and ``team``
        sessions from the native engine's session store. A row says only
        what its record says: a run with neither a result nor a terminal
        event is ``unknown`` here; the process that started a live run
        overlays its progress. Read-only, and not a job store (MD-3, §6).
        """
        rows = [*self._spec_run_rows(), *_roster_rows()]
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return rows[: max(0, int(limit))]

    def _spec_run_rows(self) -> list[dict[str, Any]]:
        if not self._run_root.is_dir():
            return []
        rows = []
        for run_dir in self._run_root.iterdir():
            row = _spec_run_row(run_dir) if run_dir.is_dir() else None
            if row is not None:
                rows.append(row)
        return rows

    def resolve(self, spec: TeamSpec, hint: str | None = None, requested: str | None = None):
        return resolve_engine(self.registry, spec, requested=requested, hint=hint)

    async def submit(
        self,
        spec: TeamSpec,
        engine: str | None = None,
        *,
        hint: str | None = None,
        caller: str = "local-user",
        seed: int = 0,
        run_id: str | None = None,
        attempt: int = 1,
        durable: bool | None = None,
        invalidated: list[str] | None = None,
    ) -> RunHandle:
        """Resolve, validate, compile and start one attempt of a run.

        ``run_id`` reuses an existing run directory and ``attempt`` numbers
        the attempt inside it (ADR-092 D-8); a fresh run is attempt 1. A
        plan spec's models are preflighted before the engine starts; the
        ADR-062 refusal (``R4Error``) propagates (D-6).

        ``durable`` (slice 7): None writes the run's records through to UMRS
        when a UMRS source is configured; True requires it
        (``DurableRecordUnavailable`` before anything runs when it cannot);
        False keeps the run local-only. ``invalidated`` are the phases a
        resume's ``--from-phase`` dropped, written to UMRS as a marker.
        """
        spec = self._pin_default_model(spec)
        spec = self._pin_project_root(spec)
        resolution = self.resolve(spec, hint=hint, requested=engine)
        team_engine = self.registry.get(resolution.engine_name)
        run_id = run_id or _new_run_id(spec)
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        services = self._build_services(spec, run_id, run_dir, caller, seed, attempt)
        issues = team_engine.validate(spec)
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            raise ValueError(
                f"spec not expressible on {resolution.engine_name}: "
                + "; ".join(i.message for i in errors)
            )
        self._preflight_models(spec, services)
        from r4_teamengine_spi.context import EngineContext

        ctx = EngineContext(
            project=spec.project,
            available_tools=tuple(services.tools.list_tools()),
            available_models=tuple(services.models.available_models()),
            mcp_servers=(),
            policy_name=spec.policy_ref,
            user=caller,
            env_fingerprint="local",
        )
        plan = team_engine.compile(spec, ctx)
        manifest = _resolution_manifest(run_id, resolution, plan.plan_hash, spec, attempt)
        services.records = self._durable_records(spec, run_id, manifest, durable, invalidated)
        manifest["durable"] = _durable_note(services.records)
        engine_run = await team_engine.start(plan, services)
        _write_manifest(run_dir, manifest)
        return RunHandle(
            run_id=run_id,
            run_dir=run_dir,
            engine_name=resolution.engine_name,
            resolution=resolution,
            engine_run=engine_run,
            services=services,
        )

    async def resume(
        self, run_id: str, *, from_phase: str | None = None, durable: bool | None = None
    ) -> RunHandle:
        """Start attempt N+1 of a plan run in its own run directory.

        Rebuilds the spec from the run's ``plan.json`` and ``params.json``
        through the plan engine's own ``spec_from_plan``, so a resumed run
        is bound, expanded and validated exactly as a fresh one. The
        journal replays every unchanged ``ok`` call; ``from_phase`` first
        invalidates that phase and every later one (ADR-092 D-8). The new
        attempt is handed a checkpoint of the previous one, so its
        ``run_started`` event says which attempt it resumes.

        Slice 7: with no run directory here, the run is rebuilt from its
        durable record in UMRS (``hydrate``), so a resume works from any
        venue. ``durable`` None keeps the run's own choice.
        """
        run_dir = self._run_dir(run_id)
        if not (run_dir / "resolution.json").is_file():
            self.hydrate(run_id)
        manifest = _read_json(run_dir / "resolution.json")
        spec = _rebuild_plan_spec(run_dir, manifest)
        invalidated = None
        if from_phase:
            from .plan.journal import PlanJournal, phases_from

            invalidated = phases_from(spec.plan, from_phase)
            PlanJournal(run_dir).invalidate_from_phase(spec.plan, from_phase)
        previous = int(manifest.get("attempt") or 1)
        if durable is None and (manifest.get("durable") or {}).get("required"):
            durable = True
        handle = await self.submit(
            spec,
            engine=manifest.get("engine"),
            run_id=run_id,
            attempt=previous + 1,
            durable=durable,
            invalidated=invalidated,
        )
        await self._resume_from(handle, f"attempt-{previous}")
        return handle

    def hydrate(self, run_id: str) -> Path:
        """Rebuild a run's directory here from its durable record (slice 7).

        Writes ``plan.json``, ``params.json`` and ``resolution.json`` (the
        attempt is the last one UMRS recorded); the journal fills from UMRS
        when the next attempt loads it. The project root is this service's
        (its ``project_root``, else the process's directory): the recorded one
        names a tree on the machine that started the run. Raises
        FileNotFoundError when neither this machine nor UMRS has the run.
        """
        run_dir = self._run_dir(run_id)
        api = self._records_api()
        if api is None:
            raise FileNotFoundError(
                f"cannot resume: no team plan run {run_id!r} under {self._run_root}, and no "
                "UMRS source is configured to read its durable record from"
            )
        try:
            view = api.run(run_id)
        except Exception as exc:  # noqa: BLE001 — named as a missing run
            raise FileNotFoundError(
                f"cannot resume: no team plan run {run_id!r} under {self._run_root}, and UMRS "
                f"has no durable record of it ({type(exc).__name__}: {exc})"
            ) from exc
        files = _hydrated_files(view, os.path.realpath(self._project_root or os.getcwd()))
        run_dir.mkdir(parents=True, exist_ok=True)
        for name, doc in files.items():
            (run_dir / name).write_text(json.dumps(doc, indent=2))
        return run_dir

    # ── slice 8: the server venue ───────────────────────────────────────────

    def hosted_definition(self, spec: TeamSpec, *, run_id: str | None = None) -> tuple[str, dict]:
        """``(run_id, definition)`` a server-hosted run is started with (slice 8).

        Resolved, validated and compiled as ``submit`` does, nothing run: the
        definition is what UMRS records (the bound, expanded plan, its params
        and the resolution manifest) and what its venue worker rebuilds the
        spec from. No project root is pinned (the worker works in its own
        sandbox), and the model preflight is the server's: the venue's
        sources pass its ADR-062 gate when the worker starts.
        """
        if spec.topology.style is not WorkflowStyle.PLAN:
            raise ValueError("only a team plan runs on the server venue")
        from r4_teamengine_spi.context import EngineContext

        spec = _venue_models(self._pin_default_model(spec))
        resolution = self.resolve(spec)
        team_engine = self.registry.get(resolution.engine_name)
        errors = [i for i in team_engine.validate(spec) if i.severity == "error"]
        if errors:
            raise ValueError(
                f"spec not expressible on {resolution.engine_name}: "
                + "; ".join(i.message for i in errors)
            )
        ctx = EngineContext(
            project=spec.project,
            available_tools=(),
            available_models=(),
            mcp_servers=(),
            policy_name=spec.policy_ref,
            user="server-venue",
            env_fingerprint="venue",
        )
        compiled = team_engine.compile(spec, ctx)
        run_id = run_id or _new_run_id(spec)
        manifest = _resolution_manifest(run_id, resolution, compiled.plan_hash, spec)
        return run_id, _definition(spec, manifest)

    async def host_recorded(
        self, run_id: str, *, max_wallclock_sec: int | None = None
    ) -> RunHandle:
        """The server-venue worker's start (slice 8): the next attempt of a run
        UMRS records (attempt 1 when it has none), its record required.

        The run directory is rebuilt here from the record, in this service's
        project root; ``max_wallclock_sec`` (the project's ADR-067 cap the
        server recorded) lowers the plan's own wall clock, which the broker
        then enforces per round (D-7).
        """
        api = self._records_api()
        if api is None:
            raise DurableRecordUnavailable(
                f"durable_required: run {run_id} is hosted on the server venue and its "
                "worker has no UMRS route; nothing ran"
            )
        view = api.run(run_id)
        files = _hydrated_files(view, os.path.realpath(self._project_root or os.getcwd()))
        files["plan.json"] = _within_wallclock(files["plan.json"], max_wallclock_sec)
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        for name, doc in files.items():
            (run_dir / name).write_text(json.dumps(doc, indent=2))
        manifest = files["resolution.json"]
        previous = _labelled_attempts(view)
        handle = await self.submit(
            _rebuild_plan_spec(run_dir, manifest),
            engine=manifest.get("engine"),
            run_id=run_id,
            attempt=previous + 1,
            durable=True,
        )
        if previous:
            await self._resume_from(handle, f"attempt-{previous}")
        return handle

    def _records_api(self) -> Any:
        if self._records is AUTO:
            self._records = umrs_records_for(self._client)
        return self._records

    def _durable_records(
        self,
        spec: TeamSpec,
        run_id: str,
        manifest: dict[str, Any],
        durable: bool | None,
        invalidated: list[str] | None,
    ) -> DurableRunRecords | None:
        """The run's durable home, its attempt open, or None (local-only)."""
        if durable is False:
            return None
        api = self._records_api() if spec.topology.style is WorkflowStyle.PLAN else None
        if api is None:
            if durable:
                raise DurableRecordUnavailable(
                    f"durable_required: run {run_id} requires its durable record, and no "
                    "UMRS source is configured (r4 source use <platform>), or it is not a "
                    "plan run; nothing ran"
                )
            return None
        records = DurableRunRecords(api, run_id, required=bool(durable))
        records.open(int(manifest["attempt"]), _definition(spec, manifest))
        if invalidated:
            from .plan.journal import invalidation_marker

            records.invalidate(invalidation_marker(invalidated, f"attempt-{manifest['attempt']}"))
        return records

    async def _resume_from(self, handle: RunHandle, attempt_id: str) -> None:
        """Pass the started engine run a same-engine checkpoint of ``attempt_id``.

        The plan journal is the state (D-8), so the checkpoint only names
        the run, the engine and the attempt being resumed; an engine run
        without ``resume`` support is left as it is. An engine or version
        mismatch (``IncompatibleCheckpoint``) propagates before anything ran.
        """
        resume = getattr(handle.engine_run, "resume", None)
        if not callable(resume):
            return
        ref = CheckpointRef(
            run_id=handle.run_id,
            attempt_id=attempt_id,
            engine_name=handle.engine_name,
            engine_version=self.registry.get(handle.engine_name).descriptor().version,
            state_blob_id=f"journal:{handle.run_id}",
            event_offset=_line_count(handle.run_dir / "events.jsonl"),
            created_at=utcnow(),
        )
        try:
            await resume(ref)
        except NotImplementedError:
            logger.debug("engine %s cannot resume a checkpoint", handle.engine_name)

    def _run_dir(self, run_id: str) -> Path:
        """``<run_root>/<run_id>``, refusing ids that would leave the root.

        A resumed run id can come from the lead agent's tool call, so it is
        a single plain path component or nothing (no ``/``, ``..``).
        """
        if not run_id or run_id in (".", "..") or Path(run_id).name != run_id or "\\" in run_id:
            raise ValueError(f"invalid team run id {run_id!r}")
        return self._run_root / run_id

    def _build_services(
        self,
        spec: TeamSpec,
        run_id: str,
        run_dir: Path,
        caller: str,
        seed: int,
        attempt: int = 1,
    ) -> RuntimeServices:
        tools = _tools_for_policy(self._policy, spec.policy_ref)
        meter = TokenBudgetMeter(spec.budgets)
        return RuntimeServices(
            tools=PolicyToolBroker(tools),
            models=ClientModelBroker(self._client),
            memory=TranscriptMemoryBroker(run_dir),
            artifacts=RunArtifactBroker(run_dir),
            events=FileEventSink(run_id, run_dir),
            budget=meter,
            workers=NullWorkerBroker(),
            run=RunContext(
                run_id=run_id,
                attempt_id=f"attempt-{attempt}",
                project=spec.project,
                caller=caller,
                working_dir=str(run_dir),
                seed=seed or 7,
            ),
            agents=self._agent_broker(spec, run_id, meter),
        )

    def _agent_broker(self, spec: TeamSpec, run_id: str, meter: BudgetMeter) -> AgentBroker | None:
        """The run's governed agent broker (ADR-092 D-3).

        Built per run from the client, the run's meter, the spec's budgets
        and the lead's governance handles held on this service: agents fork
        the lead's permission manager (or fail closed without one), default
        to the session's model, charge the session's usage records, and have
        every round refused once the tokens, the wall clock or the run's
        tool calls are spent (D-6, D-7). Roster engines never call it.
        Imported here, not at module load, so the team CLI does not import
        the companion tool catalog to list engines.
        """
        from .agent_broker import RoleTurnAgentBroker

        return RoleTurnAgentBroker(
            self._client,
            meter=meter,
            run_id=run_id,
            permission_manager=self._permission_manager,
            session_model=self._session_model,
            usage_hooks=self._usage_hooks,
            web_access=self._web_access,
            budgets=getattr(spec, "budgets", None),
            run_grants=self._run_grants,
            project=getattr(spec, "project", None) or None,
            gate_lane=self._gate_lane,
            observer=self._observer,
            project_root=(getattr(spec, "context", None) or {}).get(PROJECT_ROOT_KEY) or None,
        )

    def _pin_project_root(self, spec: TeamSpec) -> TeamSpec:
        """A plan spec carries the real path of the project its agents work in
        (ADR-092 slice 5): this service's ``project_root``, else the process's
        directory. A spec that already names one (a resume) keeps it."""
        context = dict(spec.context or {})
        if spec.topology.style is not WorkflowStyle.PLAN or context.get(PROJECT_ROOT_KEY):
            return spec
        context[PROJECT_ROOT_KEY] = os.path.realpath(self._project_root or os.getcwd())
        return dataclasses.replace(spec, context=context)

    def _pin_default_model(self, spec: TeamSpec) -> TeamSpec:
        """A plan spec carries the model its unnamed agents will actually run.

        Journal keys hash each agent's model (D-8). An agent naming no model
        hashed ``None``, so a resume after the session's or the client's
        default model changed replayed the old model's results as current.
        The resolved default rides in ``spec.context`` (and so in
        resolution.json, which resume reads back).
        """
        context = dict(spec.context or {})
        if spec.topology.style is not WorkflowStyle.PLAN or context.get("default_model"):
            return spec
        from .agent_broker import client_default_model

        model = self._session_model or client_default_model(self._client)
        if not model:
            return spec
        context["default_model"] = model
        return dataclasses.replace(spec, context=context)

    def _preflight_models(self, spec: TeamSpec, services: RuntimeServices) -> None:
        """Every distinct model of a plan spec through the ADR-062 gate (D-6).

        Before compile and start, so a public route for a sensitive project
        is refused by name at submit and no agent has spent anything. An
        agent that names no model runs on the client's configured default
        (the CLI without ``--model``), so that default is preflighted too.
        """
        preflight = getattr(services.agents, "preflight_models", None)
        if spec.topology.style is not WorkflowStyle.PLAN or not callable(preflight):
            return
        from .agent_broker import client_default_model, plan_models

        preflight(
            plan_models(
                spec.plan or {},
                default_model=(spec.context or {}).get("default_model"),
                session_model=self._session_model,
                client_model=client_default_model(self._client),
            )
        )


_UNSAFE_RUN_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
# The engine whose runtime holds interactive roster teams (``teamup``).
ROSTER_SESSION_ENGINE = "native"
# A run's terminal event, when it ended without a result.json (a native run).
_TERMINAL_EVENT_STATUS = {
    "run_completed": "completed",
    "run_failed": "failed",
    "run_cancelled": "cancelled",
}
_EVENT_TAIL_BYTES = 8192


def _spec_run_row(run_dir: Path) -> dict[str, Any] | None:
    """One SPI run's Work-pane row from its own records, or None (not a run)."""
    manifest = _json_or_none(run_dir / "resolution.json")
    if not isinstance(manifest, dict):
        return None
    doc = _json_or_none(run_dir / "result.json")
    doc = doc if isinstance(doc, dict) else {}
    engine = str(manifest.get("engine") or "")
    return {
        "kind": "plan" if engine == "plan" else "spec",
        "run_id": str(manifest.get("run_id") or run_dir.name),
        "name": _run_name(manifest, doc, run_dir),
        "engine": engine,
        "status": _run_status(doc, run_dir),
        "project_id": manifest.get("project") or None,
        "attempt": _attempt_label(manifest, doc),
        **_usage_columns(doc),
        "error": doc.get("error"),
        "updated_at": _mtime_iso(run_dir / ("result.json" if doc else "resolution.json")),
    }


def _run_name(manifest: dict[str, Any], doc: dict[str, Any], run_dir: Path) -> str:
    return str(manifest.get("run_name") or doc.get("plan") or run_dir.name)


def _attempt_label(manifest: dict[str, Any], doc: dict[str, Any]) -> str:
    return str(doc.get("attempt") or f"attempt-{manifest.get('attempt') or 1}")


def _run_status(doc: dict[str, Any], run_dir: Path) -> str:
    """The result's status, else the terminal event's, else ``unknown``."""
    return str(doc.get("status") or _terminal_status(run_dir) or "unknown")


def _usage_columns(doc: dict[str, Any]) -> dict[str, Any]:
    """The ``agents`` and ``cost`` columns of a finished run's result."""
    budget = doc.get("budget")
    budget = budget if isinstance(budget, dict) else {}
    ok, null = _agent_counts(doc.get("steps"))
    return {
        "agents": {"started": budget.get("agents_started"), "ok": ok, "null": null},
        "cost": {"tokens": budget.get("spent_tokens"), "llm_calls": budget.get("llm_calls")},
    }


def _roster_rows() -> list[dict[str, Any]]:
    """The native engine's interactive team sessions as Work-pane rows."""
    from .native import list_team_sessions

    return [
        {
            "kind": "team",
            "run_id": row.get("session_id"),
            "name": row.get("name"),
            "engine": ROSTER_SESSION_ENGINE,
            "status": row.get("status"),
            "project_id": row.get("project_id"),
            "iteration": row.get("iteration"),
            "max_iterations": row.get("max_iterations"),
            # No cost: a team session records none since its compute budget
            # was retired (ADR-092 slice 4); its rounds charge the lead chat.
            "agents": {"roles": list(row.get("roles") or [])},
            "updated_at": row.get("updated_at"),
        }
        for row in list_team_sessions()
    ]


def _agent_counts(steps: Any) -> tuple[int, int]:
    """(ok, null) agents summed over a result's steps."""
    if not isinstance(steps, dict):
        return 0, 0
    tallies = [tally for tally in steps.values() if isinstance(tally, dict)]
    return (
        sum(int(tally.get("ok") or 0) for tally in tallies),
        sum(int(tally.get("null") or 0) for tally in tallies),
    )


def _terminal_status(run_dir: Path) -> str | None:
    """The status of the run's terminal event, from the tail of its log."""
    try:
        with (run_dir / "events.jsonl").open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - _EVENT_TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        try:
            kind = json.loads(line).get("kind")
        except (ValueError, AttributeError):
            continue
        if kind in _TERMINAL_EVENT_STATUS:
            return _TERMINAL_EVENT_STATUS[kind]
    return None


def _json_or_none(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _mtime_iso(path: Path) -> str | None:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _new_run_id(spec: TeamSpec) -> str:
    """``<run name>-<8 hex>`` as one safe path component.

    A plan's name is free text (ADR-092 D-1), and the run id names the run
    directory, so ``_run_dir`` refused a valid plan named ``"a/b"`` at
    submit. Every character outside ``[A-Za-z0-9_-]`` becomes ``-``, as the
    chat tool's own run ids are built.
    """
    stem = _UNSAFE_RUN_ID_CHARS.sub("-", spec.run_name or "").strip("-")[:24] or "run"
    return f"{stem}-{uuid.uuid4().hex[:8]}"


def _resolution_manifest(
    run_id: str,
    resolution: Resolution,
    plan_hash: str,
    spec: TeamSpec,
    attempt: int = 1,
) -> dict[str, Any]:
    """Rewritten per attempt. ``project``, ``run_name``, ``max_tokens``,
    ``concurrency`` and ``default_model`` are what ``TeamService.resume``
    passes back to ``spec_from_plan`` to rebuild the spec unchanged."""
    return {
        "run_id": run_id,
        "engine": resolution.engine_name,
        "requested": resolution.requested,
        "hint": resolution.hint,
        "reason": resolution.reason,
        "plan_hash": plan_hash,
        "attempt": attempt,
        "project": spec.project,
        "run_name": spec.run_name,
        "max_tokens": spec.budgets.max_tokens,
        "concurrency": spec.topology.max_parallel,
        "default_model": (spec.context or {}).get("default_model"),
        # ADR-092 D-6: the MCP tools' declared capabilities this run
        # was validated with, so a resume judges it the same way.
        "tool_capabilities": (spec.context or {}).get("tool_capabilities") or {},
        # ADR-092 slice 5: the project the run's workspaces are made
        # from and merged back into, so a resume works in that tree.
        PROJECT_ROOT_KEY: (spec.context or {}).get(PROJECT_ROOT_KEY),
    }


def _write_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    (run_dir / "resolution.json").write_text(json.dumps(manifest, indent=2))


def _definition(spec: TeamSpec, manifest: dict[str, Any]) -> dict[str, Any]:
    """What UMRS keeps to rebuild the run elsewhere (slice 7): the plan and its
    params as ``plan.json`` and ``params.json`` hold them, and the manifest."""
    program = dict(spec.plan or {})
    return {
        "plan": program,
        "params": dict(program.get("_bound_params") or {}),
        "manifest": {key: value for key, value in manifest.items() if key != "attempt"},
    }


def _durable_note(records: DurableRunRecords | None) -> dict[str, Any]:
    """The run's durable choice, kept so a resume keeps it (resolution.json)."""
    if records is None:
        return {"home": "local", "required": False}
    return {"home": "umrs", "required": records.required, "job_id": records.job_id}


def _last_attempt(view: dict[str, Any]) -> int:
    """The last r4 attempt UMRS recorded for the run (1 when it names none)."""
    attempts = [a for a in view.get("attempts") or () if isinstance(a, dict)]
    labels = [a["label"] for a in attempts if isinstance(a.get("label"), int)]
    return max(labels, default=1)


def _venue_models(spec: TeamSpec) -> TeamSpec:
    """A server-hosted run's models (slice 8): bare catalog names, served by
    the venue session's default source through ``umrs-infer``, or
    ``model@site``: the site's own registry rows (slice 8b).

    The pinned default drops any other ``@source`` (the source is the
    session's, chosen at start). An agent that names another
    ``model@source`` is refused by name: silently serving it from another
    source would run a model the plan did not name.
    """
    from ..venue_rail import SITE_SOURCE
    from .agent_broker import plan_models

    named = [m for m in plan_models(spec.plan or {}) if _names_other_source(m, SITE_SOURCE)]
    if named:
        raise ValueError(
            "server venue: agent models are served by the run's default venue source, or "
            f"by the site's own models as model@{SITE_SOURCE}; these name another source: "
            f"{', '.join(named)}. Name the bare model and pass the source with --source"
        )
    context = dict(spec.context or {})
    default = context.get("default_model")
    if isinstance(default, str) and _names_other_source(default, SITE_SOURCE):
        context["default_model"] = default.split("@", 1)[0]
        return dataclasses.replace(spec, context=context)
    return spec


def _names_other_source(model: str, site: str) -> bool:
    """``model@<source>`` naming a source other than the site's own."""
    return "@" in model and model.rsplit("@", 1)[-1] != site


def _labelled_attempts(view: dict[str, Any]) -> int:
    """The last r4 attempt UMRS recorded for the run, 0 when none (slice 8)."""
    attempts = [a for a in view.get("attempts") or () if isinstance(a, dict)]
    return max((a["label"] for a in attempts if isinstance(a.get("label"), int)), default=0)


def _within_wallclock(plan: Any, cap_sec: int | None) -> Any:
    """``budget.max_wallclock_sec`` = min(its own, the cap); unchanged uncapped.

    An invalid own value is left for the validator to name (as the loop's
    ``plan_within_loop_budget`` does).
    """
    if not cap_sec or not isinstance(plan, dict):
        return plan
    budget = dict(plan.get("budget") or {})
    own = budget.get("max_wallclock_sec")
    if own is None:
        budget["max_wallclock_sec"] = int(cap_sec)
    elif isinstance(own, int) and not isinstance(own, bool):
        budget["max_wallclock_sec"] = min(own, int(cap_sec))
    return {**plan, "budget": budget}


def _hydrated_files(view: dict[str, Any], project_root: str) -> dict[str, Any]:
    """``plan.json``, ``params.json`` and ``resolution.json`` from a UMRS run view."""
    definition = view.get("definition") or {}
    plan, manifest = definition.get("plan"), definition.get("manifest")
    if not isinstance(plan, dict) or not isinstance(manifest, dict):
        raise FileNotFoundError(
            f"cannot resume: UMRS's record of run {view.get('run_id')!r} has no plan definition"
        )
    manifest = {
        **manifest,
        "attempt": _last_attempt(view),
        PROJECT_ROOT_KEY: project_root,
        "durable": {"home": "umrs", "required": bool(view.get("durable_required"))},
        "hydrated_from": {"job_id": view.get("job_id")},
    }
    return {
        "plan.json": plan,
        "params.json": definition.get("params") or {},
        "resolution.json": manifest,
    }


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"cannot resume: {path.name} missing in {path.parent}")
    return json.loads(path.read_text())


def _rebuild_plan_spec(run_dir: Path, manifest: dict[str, Any]) -> TeamSpec:
    """The plan spec of a recorded run, bound and validated afresh."""
    from r4_contracts.team_engine.plan import PlanInvalid

    from .plan.engine import spec_from_plan

    params_path = run_dir / "params.json"
    params = _read_json(params_path) if params_path.exists() else {}
    spec, problems = spec_from_plan(
        _read_json(run_dir / "plan.json"),
        params,
        project=manifest.get("project") or "",
        run_name=manifest.get("run_name"),
        max_tokens=manifest.get("max_tokens"),
        concurrency=manifest.get("concurrency"),
        default_model=manifest.get("default_model"),
        tool_capabilities=manifest.get("tool_capabilities"),
    )
    if problems:
        raise PlanInvalid(problems)
    root = manifest.get(PROJECT_ROOT_KEY)
    if root:
        spec = dataclasses.replace(spec, context={**spec.context, PROJECT_ROOT_KEY: root})
    return spec


def _tools_for_policy(policy: dict, policy_ref: str) -> list:
    """Resolve the run's allowlisted tool descriptors from site policy.

    Until MCP session wiring lands (phase 2b), default policy exposes no
    tools — fail closed per agent-safety rules.
    """
    return list(policy.get("policies", {}).get(policy_ref, {}).get("tools", ()))
