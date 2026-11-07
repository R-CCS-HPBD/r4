"""Pushed UMRS job events for the agent (ADR-105 D-5, slice S2).

UMRS writes one durable ``platform_notifications`` row per terminal job
transition (``job.succeeded | job.failed | job.cancelled | job.lost``) and
serves the caller's own rows after a cursor at ``GET /umrs/jobs/events``.
This module is the r4 side:

- :class:`JobWatch` takes its cursor *before* a submitting tool runs
  (``workflow_run``, ``hpc_submit``, ...), keeps the job and run ids the tool
  returned in an ephemeral watch set, and at each round boundary reads the
  events after its cursor — only while it watches something, at most every
  few seconds. A matching event becomes one bounded ``<job-notification>``
  block, shaped like the sub-agent notifications, so the model learns a run
  finished without spending turns on ``job_status`` polls.
- ``job_wait_any`` blocks on the same feed until one of the named jobs or
  runs ends (the replacement for busy polling; ``workflow_wait`` stays). A
  turn cancel ends the wait.

Only the session's top-level companion observes and drains (sub-agents share
the client; they must not take the parent's notices). Ownership
(CONSTITUTION §1.3): the watch set and the cursor are a session's ephemeral
view, never job state; terminal truth is always re-read from UMRS. A UMRS
without the route (older pin) answers an error: the watch backs off and the
agent falls back to polling, as before.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Tools whose results name durable jobs or workflow runs.
SUBMIT_TOOLS = frozenset(
    {"workflow_run", "workflow_trigger", "workflow_replay", "hpc_submit", "job_retry", "job_commit"}
)
WAIT_TOOL = "job_wait_any"
_ID_KEYS = ("job_id", "durable_job_id", "execution_id")
_JOB_ID_RE = re.compile(r"\bjob-[0-9a-f]{6,}\b")
_MIN_READ_INTERVAL_SEC = 5.0
_MAX_FAILURES = 3
_BACKOFF_SEC = 60.0
_PAGE = 50
_MAX_PAGES = 4
MAX_WAIT_SEC = 300
_WAIT_POLL_SEC = 3.0
_STATUS_RECHECK_POLLS = 5
_TERMINAL = frozenset({"succeeded", "completed", "all_complete", "failed", "cancelled", "lost"})

NOTE_FOOTER = (
    "This is a UMRS job update — not a user message. Fold it into the work "
    "that submitted the job (fetch results with workflow_results or "
    "job_status); do not answer it directly."
)


def _bare(handle: str) -> str:
    """``exec-9@rccs`` → ``exec-9`` (site-tagged handles from site routing)."""
    return handle.strip().split("@", 1)[0]


def _json_result(result: str) -> dict[str, Any]:
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def extract_ids(result: Any) -> set[str]:
    """Job and run ids a submitting tool's result names."""
    text = result if isinstance(result, str) else str(result or "")
    data = _json_result(text)
    ids = {_bare(str(data[k])) for k in _ID_KEYS if isinstance(data.get(k), str) and data[k]}
    return ids | set(_JOB_ID_RE.findall(text))


def already_terminal(result: Any) -> bool:
    """A submit result that already reports the end (``wait=True``)."""
    text = result if isinstance(result, str) else str(result or "")
    return str(_json_result(text).get("status") or "").lower() in _TERMINAL


def _detail(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail")
    return detail if isinstance(detail, dict) else {}


def event_ids(event: dict[str, Any]) -> set[str]:
    detail = _detail(event)
    return {str(detail[k]) for k in ("job_id", "execution_id") if detail.get(k)}


def render_note(event: dict[str, Any]) -> str:
    """One bounded ``<job-notification>`` block."""
    detail = _detail(event)
    lines = ["<job-notification>"]
    for tag, key in (
        ("job-id", "job_id"),
        ("execution-id", "execution_id"),
        ("job-type", "job_type"),
        ("status", "status"),
        ("exit-reason", "exit_reason"),
    ):
        if detail.get(key):
            lines.append(f"  <{tag}>{str(detail[key])[:200]}</{tag}>")
    summary = str(event.get("title") or event.get("kind") or "")[:300]
    lines.append(f"  <summary>{summary}</summary>")
    lines.append("</job-notification>")
    return "\n".join(lines) + "\n" + NOTE_FOOTER


def _synthetic_end(handle: str, status: str) -> dict[str, Any]:
    key = "job_id" if _JOB_ID_RE.fullmatch(handle) else "execution_id"
    return {
        "kind": f"job.{status}",
        "title": f"{handle} already {status}",
        "detail": {key: handle, "status": status},
    }


class JobWatch:
    """A session's ephemeral watch over the jobs it submitted."""

    def __init__(
        self,
        client: Any,
        *,
        clock: Callable[[], float] = time.monotonic,
        min_interval: float = _MIN_READ_INTERVAL_SEC,
    ) -> None:
        self._client = client
        self._clock = clock
        self._min_interval = min_interval
        self._lock = threading.Lock()  # watch set, pending, delivered
        self._poll_lock = threading.Lock()  # one feed read at a time
        self._watched: set[str] = set()
        self._pending: list[dict[str, Any]] = []
        self._delivered: set[str] = set()
        self._cursor: int | None = None
        self._last_read = float("-inf")
        self._failures = 0
        self._quiet_until = float("-inf")
        self.cancel: Any = None  # the running turn's TurnCancellation

    # -- observation --------------------------------------------------------

    def prime(self) -> None:
        """Take the cursor before a submitting tool runs (a fast job's end
        is then after the cursor, never skipped)."""
        with self._poll_lock:
            self._ensure_cursor()

    def observe(self, tool_name: str, result: Any) -> None:
        """Watch the ids a submitting tool returned."""
        if tool_name not in SUBMIT_TOOLS or already_terminal(result):
            return
        ids = extract_ids(result) - self._delivered
        if not ids:
            return
        with self._lock:
            self._watched |= ids
        with self._poll_lock:
            started_late = self._cursor is None
            self._ensure_cursor()
        if started_late:  # the cursor came after the submit: ask once
            self._queue(self._already_ended(ids))

    def watching(self) -> set[str]:
        with self._lock:
            return set(self._watched)

    # -- the feed -----------------------------------------------------------

    def _ensure_cursor(self) -> None:
        if self._cursor is not None:
            return
        page = self._read(tail=True)
        if page is not None:
            self._cursor = int(page.get("cursor") or 0)

    def _quiet(self) -> bool:
        return self._clock() < self._quiet_until

    def _read(self, *, after: int = 0, tail: bool = False) -> dict[str, Any] | None:
        if self._quiet():
            return None
        try:
            page = self._client.jobs.events(after=after, limit=_PAGE, tail=tail)
        except Exception as exc:  # older UMRS, transport error: back off
            self._failures += 1
            if self._failures >= _MAX_FAILURES:
                self._quiet_until = self._clock() + _BACKOFF_SEC
                self._failures = 0
            logger.debug("job events read failed: %s", exc)
            return None
        self._failures = 0
        return page if isinstance(page, dict) else None

    def _session_id(self) -> str:
        return str(getattr(self._client, "companion_session_id", "") or "")

    def _matches(self, event: dict[str, Any]) -> bool:
        ids = event_ids(event)
        if ids & self._delivered:
            return False
        if ids & self._watched:
            return True
        sid = self._session_id()
        return bool(sid) and _detail(event).get("session_id") == sid

    def _poll(self) -> None:
        """Read the pages after the cursor; queue the matching ends."""
        with self._poll_lock:
            self._ensure_cursor()
            for _ in range(_MAX_PAGES):
                if self._cursor is None or not self._read_page():
                    break

    def _read_page(self) -> bool:
        """One page; True when it was full (more may follow)."""
        page = self._read(after=self._cursor or 0)
        if page is None:
            return False
        events = [e for e in page.get("events") or [] if isinstance(e, dict)]
        self._cursor = int(page.get("cursor") or self._cursor or 0)
        self._absorb(events)
        return len(events) >= _PAGE

    def _absorb(self, events: list[dict[str, Any]]) -> None:
        with self._lock:
            self._queue_locked([e for e in events if self._matches(e)])

    def _queue(self, events: list[dict[str, Any]]) -> None:
        with self._lock:
            self._queue_locked(events)

    def _queue_locked(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            ids = event_ids(event)
            if ids & self._delivered:
                continue
            self._watched -= ids
            self._delivered |= ids
            self._pending.append(event)

    def _take_pending(self, wanted: set[str] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if wanted is None:
                taken, self._pending = self._pending, []
                return taken
            taken = [e for e in self._pending if event_ids(e) & wanted]
            self._pending = [e for e in self._pending if e not in taken]
            return taken

    def _handle_status(self, handle: str) -> str:
        """The current status of a job or run ('' when unknown)."""
        try:
            if _JOB_ID_RE.fullmatch(handle):
                status = getattr(self._client.jobs.get(handle), "status", "")
            else:
                status = getattr(self._client.workflows.status(handle), "status", "")
        except Exception:  # unknown id, other site, transport error
            return ""
        return str(getattr(status, "value", status) or "").lower()

    def _already_ended(self, handles: set[str]) -> list[dict[str, Any]]:
        """Handles that ended where the feed cannot show it (one read each)."""
        ended = []
        for handle in sorted(handles):
            status = self._handle_status(handle)
            if status in _TERMINAL:
                ended.append(_synthetic_end(handle, status))
        return ended

    # -- the two consumers --------------------------------------------------

    def drain(self) -> list[str]:
        """``<job-notification>`` blocks for jobs that ended (round boundary)."""
        due = self._clock() - self._last_read >= self._min_interval
        if due and self.watching():
            self._last_read = self._clock()
            self._poll()
        return [render_note(e) for e in self._take_pending()]

    def _cancelled(self) -> bool:
        return bool(self.cancel is not None and self.cancel.is_set())

    def _pause(self, sleep: Callable[[float], None] | None) -> None:
        event = getattr(self.cancel, "_event", None)
        if sleep is None and event is not None:
            event.wait(_WAIT_POLL_SEC)  # a cancel wakes the wait at once
        else:
            (sleep or time.sleep)(_WAIT_POLL_SEC)

    def wait_any(
        self,
        handles: set[str],
        timeout: float,
        *,
        sleep: Callable[[float], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Block until a job or run in ``handles`` ends; [] on timeout or cancel."""
        wanted = {_bare(h) for h in handles}
        with self._lock:
            self._watched |= wanted - self._delivered
        deadline = self._clock() + max(0.0, min(timeout, MAX_WAIT_SEC))
        polls = 0
        while True:
            if polls % _STATUS_RECHECK_POLLS == 0:  # ends the feed cannot show
                self._queue(self._already_ended(wanted & self.watching()))
            self._poll()
            found = self._take_pending(wanted)
            if found or self._clock() >= deadline or self._cancelled():
                return found
            self._pause(sleep)
            polls += 1

    def available(self) -> bool:
        """False while the feed is backing off (an older UMRS, an outage)."""
        return not self._quiet()


def job_watch_for(client: Any) -> JobWatch | None:
    """The client's JobWatch (one per session client), created on first use."""
    if client is None:
        return None
    watch = getattr(client, "_r4_job_watch", None)
    if not isinstance(watch, JobWatch):  # also a mock client's auto-attribute
        watch = JobWatch(client)
        try:
            client._r4_job_watch = watch
        except AttributeError:  # a client that refuses attributes: no watch
            return None
    return watch


def _wait_tool_result(
    watch: JobWatch, found: list[dict[str, Any]], handles: set[str], timeout: float
) -> str:
    if found:
        return json.dumps({"status": "ended", "events": found}, default=str)
    if watch._cancelled():
        return json.dumps({"status": "cancelled", "message": "the turn was cancelled"})
    if not watch.available():
        return json.dumps(
            {
                "status": "events_unavailable",
                "message": "this UMRS serves no job events; poll job_status / "
                "workflow_status instead",
            }
        )
    return json.dumps(
        {
            "status": "timeout",
            "handles": sorted(handles),
            "message": f"none of the jobs ended within {timeout:.0f}s; you will be "
            "notified at a later round when one does",
        }
    )


def build_job_watch_tools(client: Any) -> list[dict[str, Any]]:
    """``job_wait_any`` over the client's JobWatch."""

    def _job_wait_any(handles: Any = None, timeout_sec: int = 120) -> str:
        names = {str(h) for h in (handles or []) if str(h).strip()}
        if not names:
            return json.dumps(
                {"status": "error", "message": "handles: name at least one job or run id"}
            )
        watch = job_watch_for(client)
        if watch is None:
            return json.dumps({"status": "error", "message": "no platform client"})
        timeout = float(timeout_sec or 0)
        return _wait_tool_result(watch, watch.wait_any(names, timeout), names, timeout)

    return [
        {
            "name": WAIT_TOOL,
            "description": (
                "Wait until ANY of the given UMRS jobs or workflow runs ends "
                "(succeeded, failed, cancelled or lost), instead of polling "
                "job_status / workflow_status. handles: job ids (job-…) or "
                f"workflow execution ids (an id@site handle is accepted). "
                f"timeout_sec <= {MAX_WAIT_SEC}. Jobs you submitted in this session "
                "are also announced automatically as <job-notification> blocks "
                "when they end."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "handles": {"type": "array", "items": {"type": "string"}},
                    "timeout_sec": {"type": "integer", "default": 120, "maximum": MAX_WAIT_SEC},
                },
                "required": ["handles"],
            },
            "execute": _job_wait_any,
        }
    ]
