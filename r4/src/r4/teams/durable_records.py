"""A team plan run's durable record in UMRS (ADR-092 slice 7, MD-4).

The durable record is UMRS's (CONSTITUTION §1.3, §6); the run directory is
its cache (MD-3). Over the traces family (``/gw/traces/plan-runs/…``, the
caller's own runs only), a run is one client-hosted ``research_loop_job``,
an r4 attempt is one UMRS attempt, and each portable journal record is one
``execution_traces`` row keyed by its D-8 call key. No job store, retry or
queue lives here: this module only moves records over HTTP.

- ``UmrsPlanRecords``: the thin HTTP adapter over the r4 ``Client``.
- ``DurableRunRecords``: the run's ``RecordBroker`` (the SPI seam the plan
  engine writes through; the engine never holds a client).
- ``umrs_records_for``: the adapter when a UMRS source is configured (an
  active platform source, or ``R4_GATEWAY_URL``), else None. With
  ``R4_PLAN_DURABLE=off`` runs stay local-only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any
from urllib.parse import quote

from r4_teamengine_spi.context import RecordBroker

logger = logging.getLogger(__name__)

TIMEOUT_SEC = 15.0
ENV_DURABLE = "R4_PLAN_DURABLE"


class DurableRecordUnavailable(RuntimeError):
    """A run that requires its durable record cannot have one: refused by name."""


class UmrsPlanRecords:
    """``…/traces/plan-runs`` over the client's HTTP (the gateway's auth).

    ``route`` is the path under the client's gateway URL; default
    ``{prefix}/traces/plan-runs`` follows the client's mode probe
    (``/gw`` behind the edge, ``/umrs`` direct — 2026-10-20, plan H-10.2);
    a server-venue worker passes ``/traces/plan-runs`` because its gateway
    URL is already its rail's gw prefix (slice 8).
    """

    def __init__(
        self, client: Any, *, timeout: float = TIMEOUT_SEC, route: str | None = None
    ) -> None:
        if route is None:
            prefix = getattr(client, "_api_prefix", "/gw")
            route = f"{prefix}/traces/plan-runs"
        self._client = client
        self._timeout = timeout
        self._route = route

    def open_attempt(
        self, run_id: str, *, attempt: int, definition: dict[str, Any], required: bool
    ) -> dict[str, Any]:
        body = {"attempt": int(attempt), "definition": definition, "required": bool(required)}
        return self._call("POST", self._url(run_id, "attempts"), body)

    def append(self, run_id: str, attempt_id: str, record_text: str) -> dict[str, Any]:
        body = {"attempt_id": attempt_id, "record": record_text}
        return self._call("POST", self._url(run_id, "records"), body)

    def run(self, run_id: str) -> dict[str, Any]:
        return self._call("GET", self._url(run_id))

    def records(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._call("GET", self._url(run_id, "records")).get("records")
        return [row for row in rows or () if isinstance(row, dict)]

    def finish(
        self, run_id: str, attempt_id: str, *, status: str, error: str, summary: dict[str, Any]
    ) -> dict[str, Any]:
        body = {"status": status, "error": error, "summary": summary}
        url = self._url(run_id, "attempts", attempt_id, "finish")
        return self._call("POST", url, body)

    # ── slice 8: the server venue (a user's client only) ─────────────────

    def start_on_venue(self, run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Start (with a definition) or resume (without) a server-hosted run."""
        return self._call("POST", self._url(run_id, "venue"), body)

    def venue_status(self, run_id: str) -> dict[str, Any]:
        return self._call("GET", self._url(run_id, "venue"))

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self._call("POST", self._url(run_id, "cancel"), {})

    def _url(self, run_id: str, *parts: str) -> str:
        base = str(self._client.config.gateway_url).rstrip("/")
        path = "/".join(quote(str(p), safe="") for p in (run_id, *parts))
        return f"{base}{self._route}/{path}"

    def _call(self, method: str, url: str, body: Any = None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"timeout": self._timeout}
        if body is not None:
            kwargs["json_body"] = body
        answer = self._client.http.request(method, url, **kwargs)
        return answer if isinstance(answer, dict) else {}


class DurableRunRecords(RecordBroker):
    """One run's durable home: its open attempt, its records, its close.

    ``open`` happens before the engine starts (TeamService). A run that does
    not require its record keeps a failed open as ``unavailable``: every
    later call raises it, so the journal names it and the run goes
    local-only. A run that requires it is refused before anything runs.
    """

    def __init__(self, api: Any, run_id: str, *, required: bool = False) -> None:
        self._api = api
        self._run_id = run_id
        self.required = bool(required)
        self._job_id: str | None = None
        self._attempt_id: str | None = None
        self._unavailable: str | None = None
        self.skipped = 0  # records whose text did not match their hash

    def open(self, attempt: int, definition: dict[str, Any]) -> None:
        try:
            opened = self._api.open_attempt(
                self._run_id, attempt=attempt, definition=definition, required=self.required
            )
            self._job_id = str(opened["job_id"])
            self._attempt_id = str(opened["attempt_id"])
        except Exception as exc:  # noqa: BLE001 — named below, or refused when required
            self._refuse("open", exc)

    def invalidate(self, marker: dict[str, Any]) -> None:
        """Write a ``--from-phase`` marker (the local file was rewritten)."""
        try:
            self.append(marker)
        except Exception as exc:  # noqa: BLE001
            self._refuse("invalidate", exc)

    def load(self) -> list[dict[str, Any]]:
        self._check()
        records = []
        for row in self._api.records(self._run_id):
            record = _record_of(row)
            if record is None:
                self.skipped += 1
                continue
            records.append(record)
        return records

    def append(self, record: dict[str, Any]) -> None:
        self._check()
        text = json.dumps(record, ensure_ascii=False, default=str)
        self._api.append(self._run_id, str(self._attempt_id), text)

    def close(self, status: str, summary: dict[str, Any]) -> None:
        self._check()
        error = str(summary.get("error") or "")
        self._api.finish(
            self._run_id, str(self._attempt_id), status=status, error=error, summary=summary
        )

    def describe(self) -> dict[str, Any]:
        described: dict[str, Any] = {"run_id": self._run_id}
        if self._job_id:
            described.update(job_id=self._job_id, umrs_attempt_id=self._attempt_id)
        if self.skipped:
            described["skipped_records"] = self.skipped
        return described

    @property
    def job_id(self) -> str | None:
        return self._job_id

    def _check(self) -> None:
        if self._unavailable is not None:
            raise RuntimeError(self._unavailable)
        if self._attempt_id is None:
            raise RuntimeError("no durable attempt is open")

    def _refuse(self, what: str, exc: BaseException) -> None:
        reason = f"{what}: {type(exc).__name__}: {exc}"[:300]
        if self.required:
            raise DurableRecordUnavailable(
                f"durable_required: run {self._run_id} cannot keep its durable record in "
                f"UMRS ({reason}); nothing ran"
            ) from exc
        logger.warning("plan run %s: durable record %s", self._run_id, reason)
        self._unavailable = reason


def _record_of(row: dict[str, Any]) -> dict[str, Any] | None:
    """The record a row carries, when its text matches its sha256."""
    text = row.get("record")
    if not isinstance(text, str):
        return None
    digest = row.get("outputs_hash")
    if digest and hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
        return None
    try:
        record = json.loads(text)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def umrs_records_for(client: Any) -> UmrsPlanRecords | None:
    """The UMRS adapter when this client is configured for a UMRS source."""
    if os.environ.get(ENV_DURABLE, "").strip().lower() in ("off", "0", "false", "local"):
        return None
    http = getattr(client, "http", None)
    config = getattr(client, "config", None)
    if not callable(getattr(http, "request", None)) or not getattr(config, "gateway_url", None):
        return None
    if not _umrs_source_configured():
        return None
    return UmrsPlanRecords(client)


def _umrs_source_configured() -> bool:
    if os.environ.get("R4_GATEWAY_URL"):
        return True
    try:
        from ..source import resolve_active_source

        source = resolve_active_source()
    except Exception:  # noqa: BLE001 — an unreadable source store is no source
        return False
    return source is not None and getattr(source, "kind", "") == "platform"


__all__ = [
    "DurableRecordUnavailable",
    "DurableRunRecords",
    "UmrsPlanRecords",
    "umrs_records_for",
]
