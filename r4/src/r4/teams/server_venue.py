"""A team plan run on the server venue, from the client (ADR-092 slice 8).

``r4 team plan run --venue server`` and ``team_plan_run(venue="server")``
start a run UMRS hosts: the client validates and compiles the plan as for
a local run (``TeamService.hosted_definition``), and UMRS records it and
launches one frontend-venue worker that runs it headless
(``POST /gw/traces/plan-runs/<run_id>/venue``). The client keeps nothing:
it follows the run through its durable record (records, attempts, the
worker's state), resumes it (a fresh worker, attempt N+1), reads its result
(the outcome the worker reported) and cancels it, all by run id.

A gate the run raises is an intent in the UMRS approvals inbox (ADR-066);
the follower names it, and a person at a terminal may answer it here, as
the run's owner (the inbox's decide route, with the owner's own
credential). The worker never answers it.

When the site has no server venue, UMRS refuses by name
(``plan_iteration_needs_server_venue``) with the settings it lacks; that
refusal is raised here as ``ServerVenueRefused``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from ..errors import R4Error
from .durable_records import UmrsPlanRecords

REFUSAL_CODE = "plan_iteration_needs_server_venue"
GATE_LOOP_PREFIX = "plan-run:"
_RUNNING = "running"


class ServerVenueRefused(R4Error):
    """The server venue cannot host this run; ``code`` names why."""

    code = REFUSAL_CODE

    def __init__(self, message: str) -> None:
        super().__init__(message, {"code": REFUSAL_CODE})


def venue_model(model: str) -> str:
    """A model as the server venue serves it: bare, or ``model@site`` (the
    site's own models, slice 8b); any other ``@source`` is dropped (the
    run's venue source serves it)."""
    from ..venue_rail import SITE_SOURCE

    text = str(model or "").strip()
    if not text or text.rsplit("@", 1)[-1] == SITE_SOURCE:
        return text
    return text.split("@", 1)[0]


def _api(client: Any) -> UmrsPlanRecords:
    config = getattr(client, "config", None)
    if not getattr(config, "gateway_url", None) or not callable(
        getattr(getattr(client, "http", None), "request", None)
    ):
        raise ServerVenueRefused(
            f"{REFUSAL_CODE}: no UMRS source is configured (r4 source use <platform>); the "
            "server venue is a UMRS site's"
        )
    return UmrsPlanRecords(client)


def _venue_body(
    sources: list[str],
    default_source: str,
    web_policy: str,
    definition: dict[str, Any],
    web_allow_domains: Sequence[str] = (),
) -> dict[str, Any]:
    if not sources:
        raise ValueError(
            "the server venue needs a pushed source for its models: --source NAME[,NAME] "
            "(r4 source push <name> first)"
        )
    return {
        "definition": definition,
        "source_refs": list(sources),
        "default_source": default_source or "",
        "web_policy": web_policy or "",
        "web_allow_domains": list(web_allow_domains),
    }


def _start(api: UmrsPlanRecords, run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return api.start_on_venue(run_id, body)
    except R4Error as exc:
        text = str(getattr(exc, "response_body", "") or exc)
        if REFUSAL_CODE in text:
            raise ServerVenueRefused(_detail(text)) from exc
        raise


def _detail(text: str) -> str:
    """A typed error's ``detail`` text, else the text itself."""
    import json

    try:
        detail = json.loads(text).get("detail")
    except (ValueError, AttributeError):
        return text
    return str(detail) if detail else text


def start_server_run(
    client: Any,
    service: Any,
    spec: Any,
    *,
    sources: list[str],
    default_source: str = "",
    web_policy: str = "",
    web_allow_domains: Sequence[str] = (),
    run_id: str | None = None,
) -> dict[str, Any]:
    """Start ``spec`` as a server-hosted run; nothing runs here."""
    api = _api(client)
    run_id, definition = service.hosted_definition(spec, run_id=run_id)
    body = _venue_body(sources, default_source, web_policy, definition, web_allow_domains)
    return {"run_id": run_id, "baseline": 0, **_start(api, run_id, body)}


def resume_server_run(
    client: Any,
    run_id: str,
    *,
    sources: list[str],
    default_source: str = "",
    web_policy: str = "",
    web_allow_domains: Sequence[str] = (),
) -> dict[str, Any]:
    """A fresh worker for the run's next attempt, on its recorded definition."""
    api = _api(client)
    baseline = len(api.run(run_id).get("attempts") or [])
    body = _venue_body(sources, default_source, web_policy, {}, web_allow_domains)
    return {"run_id": run_id, "baseline": baseline, **_start(api, run_id, body)}


def cancel_server_run(client: Any, run_id: str) -> dict[str, Any]:
    """Stop the run's worker and cancel its record (UMRS does both)."""
    return _api(client).cancel(run_id)


def outcome(view: dict[str, Any], baseline: int = 0) -> dict[str, Any] | None:
    """The run's outcome once its attempt after ``baseline`` ended, else None.

    The worker reports the outcome when it closes its attempt; a cancelled
    record is cancelled; a worker that ended without closing one (it failed
    to start, or died) is a failed run, named.
    """
    if view.get("status") == "cancelled":
        return {"status": "cancelled", "error": "the run's record was cancelled"}
    last = _last_attempt(view, baseline)
    if last.get("plan_status"):
        return _reported(last)
    return _worker_ended(view.get("venue") or {}, last)


def _last_attempt(view: dict[str, Any], baseline: int) -> dict[str, Any]:
    """The last attempt after ``baseline`` ({} when there is none)."""
    attempts = [a for a in view.get("attempts") or () if isinstance(a, dict)][baseline:]
    return attempts[-1] if attempts else {}


def _reported(last: dict[str, Any]) -> dict[str, Any]:
    """The outcome a worker reported when it closed its attempt."""
    summary = last.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    return {
        "status": last["plan_status"],
        "error": summary.get("error") or last.get("exit_reason") or "",
        "summary": summary,
        "attempt": last.get("label"),
    }


def _worker_ended(venue: dict[str, Any], last: dict[str, Any]) -> dict[str, Any] | None:
    """A failed run when the worker's session ended with no attempt running."""
    if venue.get("state") not in ("failed", "stopped") or last.get("status") == _RUNNING:
        return None
    runtime = venue.get("runtime") or {}
    why = venue.get("reason") or runtime.get("detail") or venue["state"]
    return {"status": "failed", "error": f"worker_ended: {why}"}


def server_result(client: Any, run_id: str) -> dict[str, Any]:
    """The run's last finished attempt's outcome, or its running state."""
    view = _api(client).venue_status(run_id)
    return outcome(view) or {"status": _RUNNING, "venue": (view.get("venue") or {}).get("state")}


class Follower:
    """Follow one server-hosted run through its record until it ends.

    ``emit(line)`` gets one line per new agent record and per gate waiting;
    ``ask(question) -> bool | None`` (a terminal) answers a gate as the
    owner, None leaves it to any other client of the inbox.
    """

    def __init__(
        self,
        client: Any,
        run_id: str,
        *,
        emit: Callable[[str], None],
        ask: Callable[[str], bool | None] | None = None,
        baseline: int = 0,
        poll_sec: float = 3.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._api = _api(client)
        self.run_id = run_id
        self._emit = emit
        self._ask = ask
        self._baseline = baseline
        self._poll = poll_sec
        self._sleep = sleep
        self._clock = clock
        self._records = 0
        self._gates: set[str] = set()

    def follow(self, timeout_sec: float | None = None) -> dict[str, Any]:
        deadline = None if timeout_sec is None else self._clock() + timeout_sec
        while True:
            view = self._api.venue_status(self.run_id)
            self._new_records()
            self._waiting_gates()
            done = outcome(view, self._baseline)
            if done is not None:
                return done
            if deadline is not None and self._clock() >= deadline:
                return {"status": _RUNNING, "venue": (view.get("venue") or {}).get("state")}
            self._sleep(self._poll)

    def _new_records(self) -> None:
        rows = self._api.records(self.run_id)
        for row in rows[self._records :]:
            self._emit(_record_line(row))
        self._records = max(self._records, len(rows))

    def _waiting_gates(self) -> None:
        loop_id = f"{GATE_LOOP_PREFIX}{self.run_id}"
        try:
            payload = self._client.list_loop_approvals(loop_id=loop_id)
        except Exception:  # noqa: BLE001 — the inbox listing is a convenience
            return
        for item in _pending_rows(payload):
            approval_id = str(item.get("id") or item.get("approval_id") or "")
            if approval_id and approval_id not in self._gates:
                self._gates.add(approval_id)
                self._gate(approval_id, item.get("action") or {})

    def _gate(self, approval_id: str, action: dict[str, Any]) -> None:
        gate, klass = action.get("gate"), action.get("class")
        self._emit(
            f"[gate] {gate} ({klass}): approval {approval_id} waits in the UMRS inbox; "
            f"r4 project loop decide {approval_id} --decision once|no"
        )
        if self._ask is None:
            return
        answer = self._ask(f"[gate] {gate} ({klass}): {action.get('prompt') or ''}")
        if answer is not None:
            self._client.decide_loop_approval(approval_id, "once" if answer else "no")


def _pending_rows(payload: Any) -> list[dict[str, Any]]:
    pending = payload.get("pending", []) if isinstance(payload, dict) else payload
    return [row for row in pending or () if isinstance(row, dict)]


def _record_line(row: dict[str, Any]) -> str:
    import json

    try:
        record = json.loads(row.get("record") or "{}")
    except ValueError:
        return "[record] unreadable"
    render = _RECORD_LINES.get(record.get("kind"), _agent_record_line)
    return render(record)


def _gate_record_line(record: dict[str, Any]) -> str:
    verdict = "approved" if record.get("ok") else "rejected"
    return f"[gate] {record.get('node')}: {verdict} by {record.get('decided_by') or 'a human'}"


def _agent_record_line(record: dict[str, Any]) -> str:
    verdict = "ok" if record.get("ok") else f"null({record.get('reason') or 'unknown'})"
    return f"[agent] {record.get('label') or record.get('node')} {verdict}"


_RECORD_LINES = {
    "gate": _gate_record_line,
    "invalidate": lambda _record: "[durable] phases invalidated",
}


__all__ = [
    "REFUSAL_CODE",
    "Follower",
    "ServerVenueRefused",
    "cancel_server_run",
    "outcome",
    "resume_server_run",
    "venue_model",
    "server_result",
    "start_server_run",
]
