"""The headless worker of one server-hosted team plan run (ADR-092 slice 8).

``r4 venue worker --plan-run <run_id>`` runs inside the frontend-venue
sandbox UMRS starts for the run (ADR-046 A2: a per-session ``--internal``
network, the egress sidecar as the only way out, a lease token as the only
credential). It holds no user JWT and no provider key:

- model calls go through the sidecar to ``umrs-infer`` (the session's
  sealed sources, the ADR-062 class gate per request), as a remote chat
  turn's do;
- the run's record (slice 7's ``research_loop_job`` + ``execution_traces``)
  is read and written through the rail's gw prefix, which admits this
  session's own run only (``/traces/plan-runs/<run_id>``);
- a plan gate is raised as an intent in the UMRS approvals inbox (ADR-066)
  through the rail and only read back: the run's owner answers it from any
  client. The worker has no decide verb, so it never answers its own gate.

The run is the r4 plan engine, unchanged (D-3..D-8): ``TeamService`` rebuilds
it from its record (``host_recorded``), its agents run fail-closed (safe
tools only, no lead permission manager, as a CLI run's), its budget is the
plan's own with the wall clock lowered to the project's ADR-067 cap the
server recorded, and its record is required (a record that cannot be
written stops the run, fail closed). Heartbeats keep the lease warm, report
progress, and carry the owner's stop; when the run ends the worker reports
``done`` and its session is torn down.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import R4Error
from ..venue_rail import VenueRailClient, VenueRailError

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_LEASE_REFUSED = 3
EXIT_BOOTSTRAP_FAILED = 4
EXIT_ISOLATION_FAILED = 78

RECORDS_ROUTE = "/traces/plan-runs"  # under the rail's gw prefix
DONE_PHASE = "done"
DEFAULT_PROGRESS_SEC = 30.0
DEFAULT_GATE_POLL_SEC = 3.0
DEFAULT_GATE_TIMEOUT_SEC = 24 * 3600.0
_GOOD = frozenset({"success", "partial"})
_MAX_DETAIL = 400


class GateUnanswered(RuntimeError):
    """A gate nobody answered (the run was stopped, or the wait timed out):
    a refusal the engine records, never an answer."""


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def bare_model(model: Any) -> str:
    """``model@source`` → ``model``: the sidecar is the only route here;
    ``model@site`` is kept (the rail client routes it to the site source)."""
    from ..venue_rail import SITE_SOURCE

    text = str(model or "").strip()
    if text.rsplit("@", 1)[-1] == SITE_SOURCE:
        return text
    return text.split("@", 1)[0].strip()


class InboxGateLane:
    """The plan's gate lane: the UMRS approvals inbox, through the rail.

    ``manager`` is a permission manager asking every time and used only for
    the gate's never-auto approval (as the CLI's terminal lane is); its
    callback raises the intent, then waits for the owner's decision. A stop
    or a timeout raises ``GateUnanswered``: the gate is refused, unanswered.
    """

    def __init__(
        self,
        rail: Any,
        *,
        stopped: Callable[[], bool],
        heartbeat: Callable[..., Any],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_sec: float = DEFAULT_GATE_POLL_SEC,
        timeout_sec: float = DEFAULT_GATE_TIMEOUT_SEC,
    ) -> None:
        self._rail = rail
        self._stopped = stopped
        self._heartbeat = heartbeat
        self._clock = clock
        self._sleep = sleep
        self._poll = poll_sec
        self._timeout = timeout_sec
        self.asked: list[str] = []

    @property
    def manager(self) -> Any:
        from ..companion.permissions import PermissionManager, PermissionMode

        return PermissionManager(PermissionMode.ASK_ALWAYS, approval_callback=self.ask)

    def ask(self, _tool: str, _description: str, arguments: dict) -> bool:
        gate = str(arguments.get("gate") or "")
        record = self._rail.request_plan_gate(
            gate=gate,
            trigger_class=str(arguments.get("class") or ""),
            prompt=str(arguments.get("prompt") or ""),
        )
        approval_id = str(record.get("approval_id") or "")
        if not approval_id:
            raise GateUnanswered(f"gate {gate}: the inbox did not take the question")
        self.asked.append(approval_id)
        self._heartbeat(
            "awaiting_approval",
            detail=f"plan gate {gate}: approval {approval_id} waits for the owner",
        )
        return self._await(approval_id, gate)

    def _await(self, approval_id: str, gate: str) -> bool:
        deadline = self._clock() + self._timeout
        while not self._stopped():
            status = str(self._rail.get_plan_gate(approval_id).get("status") or "pending")
            if status != "pending":
                return status == "approved"
            if self._clock() >= deadline:
                raise GateUnanswered(f"gate {gate}: no answer in {int(self._timeout)} s")
            self._sleep(self._poll)
            self._heartbeat("awaiting_approval", detail=f"plan gate {gate}: waiting")
        raise GateUnanswered(f"gate {gate}: the run was stopped while it waited")


class PlanRunWorker:
    """One server-hosted plan run's worker; every collaborator is injectable.

    ``client_factory(model)`` builds the r4 client whose platform verbs ride
    the rail and whose model route is the sidecar; ``service_factory(client,
    records, gate_lane)`` builds the ``TeamService`` (tests pass scripted
    ones).
    """

    def __init__(
        self,
        *,
        run_id: str,
        rail: Any,
        client_factory: Callable[[str], Any],
        service_factory: Callable[..., Any] | None = None,
        work_root: Path = Path("/tmp/home"),
        progress_sec: float = DEFAULT_PROGRESS_SEC,
        gate_timeout_sec: float = DEFAULT_GATE_TIMEOUT_SEC,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.run_id = run_id
        self.rail = rail
        self._client_factory = client_factory
        self._service_factory = service_factory or _team_service
        self._root = Path(work_root)
        self._progress_sec = progress_sec
        self._gate_timeout = gate_timeout_sec
        self._sleep = sleep
        self._stop = False
        self._last_line = ""
        self.status = ""
        self.error = ""

    # ── rail ───────────────────────────────────────────────────────────

    def heartbeat(self, phase: str, *, detail: str = "") -> None:
        """Report, renew the lease, and read the owner's stop."""
        try:
            answer = self.rail.heartbeat(phase=phase, detail=detail[:_MAX_DETAIL])
        except VenueRailError as exc:
            logger.warning("plan worker heartbeat refused: %s", exc.reason)
            if exc.status == 401:
                self._stop = True  # the lease is gone: nothing more is accepted
            return
        if answer.get("stop_requested"):
            self._stop = True

    def stopped(self) -> bool:
        return self._stop

    # ── the run ────────────────────────────────────────────────────────

    def run(self) -> int:
        try:
            service, caps = self._bootstrap()
        except VenueRailError as exc:
            return self._exit(
                EXIT_LEASE_REFUSED if exc.status == 401 else EXIT_BOOTSTRAP_FAILED,
                f"rail refused the bootstrap ({exc.reason})",
            )
        except Exception as exc:  # noqa: BLE001 — every bootstrap failure is reported
            return self._exit(EXIT_BOOTSTRAP_FAILED, f"{type(exc).__name__}: {str(exc)[:200]}")
        self.heartbeat("running", detail=f"plan run {self.run_id}: starting")
        try:
            result = asyncio.run(self._drive(service, caps))
        except Exception as exc:  # noqa: BLE001 — a refused start is a failed run, named
            self.status, self.error = "failed", f"{type(exc).__name__}: {str(exc)[:300]}"
        else:
            self.status, self.error = str(result.status), str(result.error or "")
        detail = f"plan run {self.status}" + (f": {self.error}" if self.error else "")
        self.heartbeat(DONE_PHASE, detail=detail)
        return EXIT_OK if self.status in _GOOD else EXIT_RUN_FAILED

    def _exit(self, code: int, reason: str) -> int:
        print(f"plan worker: {reason}", file=sys.stderr)
        self.status, self.error = "failed", reason
        try:
            self.rail.heartbeat(phase="exited", detail=reason[:_MAX_DETAIL])
        except Exception:  # noqa: BLE001 — best effort: the owner reads why
            logger.debug("exit report skipped", exc_info=True)
        return code

    def _bootstrap(self) -> tuple[Any, dict[str, Any]]:
        """The run's record through the rail; the service that runs it."""
        from .durable_records import UmrsPlanRecords

        reader = UmrsPlanRecords(self._client_factory(""), route=RECORDS_ROUTE)
        view = reader.run(self.run_id)
        if view.get("hosting") != "venue":
            raise R4Error(f"run {self.run_id} is not hosted on the server venue")
        manifest = (view.get("definition") or {}).get("manifest") or {}
        client = self._client_factory(bare_model(manifest.get("default_model")))
        records = UmrsPlanRecords(client, route=RECORDS_ROUTE)
        lane = InboxGateLane(
            self.rail,
            stopped=self.stopped,
            heartbeat=self.heartbeat,
            sleep=self._sleep,
            timeout_sec=self._gate_timeout,
        )
        work = self._root / "work"
        work.mkdir(parents=True, exist_ok=True)
        service = self._service_factory(
            client,
            records=records,
            gate_lane=lane.manager,
            run_root=self._root / "team_runs",
            project_root=work,
        )
        return service, dict(view.get("caps") or {})

    async def _drive(self, service: Any, caps: dict[str, Any]) -> Any:
        cap = int(caps.get("max_wall_clock_sec") or 0) or None
        handle = await service.host_recorded(self.run_id, max_wallclock_sec=cap)
        watch = asyncio.create_task(self._watch(handle))
        try:
            async for event in handle.events():
                self._note(event)
            return await handle.result()
        finally:
            watch.cancel()

    async def _watch(self, handle: Any) -> None:
        """Progress heartbeats; the owner's stop cancels the run as a unit."""
        while True:
            await asyncio.sleep(self._progress_sec)
            await asyncio.to_thread(self.heartbeat, "running", detail=self._last_line)
            if self._stop:
                await handle.cancel("stopped on the server venue")
                return

    def _note(self, event: Any) -> None:
        from .._cli.team_plan import _event_line

        line = _event_line(event)
        if line:
            self._last_line = line
            logger.info("%s", line)


def _team_service(client: Any, **kw: Any) -> Any:
    from .service import TeamService

    return TeamService(client, **kw)


def main(run_id: str) -> int:
    """``r4 venue worker --plan-run <run_id>`` (inside the sandbox only)."""
    from ..venue import read_venue_token
    from ..venue_rail import build_rail_platform_client
    from ..venue_worker import WorkerEnv, isolation_selftest

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        env = WorkerEnv.from_environ()
    except R4Error as exc:
        print(f"plan worker: {exc}", file=sys.stderr)
        return EXIT_BOOTSTRAP_FAILED
    if not env.skip_selftest:
        reason = isolation_selftest()
        if reason:
            print(f"venue_isolation_failed: {reason}", file=sys.stderr)
            return EXIT_ISOLATION_FAILED
    token = read_venue_token()
    if not token:
        print("plan worker: session token file unreadable", file=sys.stderr)
        return EXIT_BOOTSTRAP_FAILED
    rail = VenueRailClient(env.rail_url, env.session_id, token)

    def client_for(model: str) -> Any:
        return build_rail_platform_client(
            rail_url=env.rail_url,
            session_id=env.session_id,
            token=token,
            endpoint_url=env.endpoint_url,
            model=model,
        )

    worker = PlanRunWorker(
        run_id=run_id,
        rail=rail,
        client_factory=client_for,
        progress_sec=_float_env("R4_PLAN_WORKER_PROGRESS_SEC", DEFAULT_PROGRESS_SEC),
        gate_timeout_sec=_float_env("R4_PLAN_GATE_TIMEOUT_SEC", DEFAULT_GATE_TIMEOUT_SEC),
    )
    try:
        return worker.run()
    finally:
        rail.close()


__all__ = [
    "GateUnanswered",
    "InboxGateLane",
    "PlanRunWorker",
    "bare_model",
    "main",
]
