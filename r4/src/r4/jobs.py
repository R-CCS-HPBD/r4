"""
Job management client for R4 SDK.

Provides access to the UMRS job tracking API for monitoring
long-running operations like index building, RAG pipeline runs,
and endpoint provisioning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .errors import R4ConnectionError, R4Error

if TYPE_CHECKING:
    from .client import Client


@dataclass
class JobAttempt:
    """Information about a job attempt."""

    attempt_id: str
    job_id: str
    attempt_no: int
    backend: str
    backend_ref: Optional[str]
    status: str
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    exit_reason: Optional[str]
    log_uri: Optional[str]
    status_detail: Optional[str]
    metrics: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobAttempt":
        """Create from API response dict."""
        return cls(
            attempt_id=data.get("attempt_id", ""),
            job_id=data.get("job_id", ""),
            attempt_no=data.get("attempt_no", 0),
            backend=data.get("backend", ""),
            backend_ref=data.get("backend_ref"),
            status=data.get("status", ""),
            started_at=_parse_datetime(data.get("started_at")),
            ended_at=_parse_datetime(data.get("ended_at")),
            exit_reason=data.get("exit_reason"),
            log_uri=data.get("log_uri"),
            status_detail=data.get("status_detail"),
            metrics=data.get("metrics", {}),
            created_at=_parse_datetime(data.get("created_at")),
        )


@dataclass
class Job:
    """Information about a job."""

    job_id: str
    project_id: str
    user_id: str
    job_type: str
    spec: Dict[str, Any]
    status: str
    priority: int
    max_attempts: int
    restart_policy: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    active_attempt_id: Optional[str] = None
    commit_attempt_id: Optional[str] = None
    attempts: List[JobAttempt] = field(default_factory=list)
    # The site that claims the row (multi-HPC plan S3; "" = shared row,
    # None = a server that does not report it yet).
    venue: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Job":
        """Create from API response dict."""
        # F-004: UMRS may return ``"attempts": null`` for jobs without a
        # materialised attempt list; ``data.get("attempts", [])`` does NOT
        # fall back to ``[]`` when the key is present with a None value, so
        # we coalesce explicitly.
        raw_attempts = data.get("attempts") or []
        attempts = [JobAttempt.from_dict(a) for a in raw_attempts]
        return cls(
            job_id=data.get("job_id", ""),
            project_id=data.get("project_id", ""),
            user_id=data.get("user_id", ""),
            job_type=data.get("job_type", ""),
            spec=data.get("spec", {}),
            status=data.get("status", ""),
            priority=data.get("priority", 0),
            max_attempts=data.get("desired_max_attempts", data.get("max_attempts", 3)),
            restart_policy=data.get("restart_policy", "on_crash"),
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
            active_attempt_id=data.get("active_attempt_id"),
            commit_attempt_id=data.get("commit_attempt_id"),
            attempts=attempts,
            venue=data.get("venue"),
        )


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string."""
    if not value:
        return None
    try:
        # Handle ISO format with Z suffix
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


class JobClient:
    """
    Client for job management operations.

    Access via `client.jobs`:
        client = Client()
        jobs = client.jobs.list(project_id="my-project")

    Transport:
    - Uses HTTP Gateway by default (cloud/hybrid modes).
    - Uses direct ZMQ to UMRS in HPC-local mode.
    """

    def __init__(self, client: "Client"):
        """Initialize with parent client."""
        self._client = client

    def _route(self, path: str) -> str:
        """Translate a ``/gw/…`` path for direct-UMRS mode (WP-08).

        Job routes register on the UMRS sidecar under ``/umrs/jobs*``; in
        direct mode the ``/gw`` prefix 404s. Gateway mode is unchanged.
        """
        if path.startswith("/gw/"):
            # Duck-typed clients (test stubs without the probe) default to /gw.
            prefix = getattr(self._client, "_api_prefix", "/gw")
            return f"{prefix}{path[3:]}"
        return path

    def reproduce(self, job_id: str) -> Dict[str, Any]:
        """Executor-backed replay of one job with drift honesty (P7b).

        The job's spec is requeued so the durable runtime executes it again
        with the SAME inputs; the response carries the drift window between
        the recorded and current code versions.
        """
        return self._request("POST", f"/gw/jobs/{job_id}/reproduce")

    def attempt_credentials_mint(self, job_id: str, attempt_id: str) -> Dict[str, Any]:
        """Mint (idempotent) the attempt's credential record (WP-11 round 1)."""
        return self._request("POST", f"/gw/jobs/{job_id}/attempts/{attempt_id}/credentials")

    def attempt_credentials_sts(self, job_id: str, attempt_id: str) -> Dict[str, Any]:
        """Federate the attempt's record into per-attempt STS credentials.

        Returns {access_key, secret_key, session_token, expiration} — the
        server keeps no copy of the token (round-1 mint record is the audit
        trail); the caller prints it ONCE and keeps it local.
        """
        return self._request(
            "POST",
            f"/gw/jobs/{job_id}/attempts/{attempt_id}/credentials/sts",
        )

    def attempt_presign(
        self,
        job_id: str,
        attempt_id: str,
        key: str,
        *,
        method: str = "get",
        ttl_sec: int | None = None,
    ) -> Dict[str, Any]:
        """Attempt-scoped presign; server refuses keys outside the minted scope."""
        body: Dict[str, Any] = {"key": key, "method": method}
        if ttl_sec is not None:
            body["ttl_sec"] = ttl_sec
        return self._request("POST", f"/gw/jobs/{job_id}/attempts/{attempt_id}/presign", json=body)

    def _request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        turn_cancel: Any = None,
    ) -> Dict[str, Any]:
        """Make an HTTP request to the Gateway UMRS adapter."""
        url = f"{self._client.config.gateway_url}{self._route(path)}"
        return self._client.http.request(
            method,
            url,
            json_body=json,
            params=params,
            cancel_check=turn_cancel.check if turn_cancel is not None else None,
            register_abort=turn_cancel.register_close if turn_cancel is not None else None,
        )

    def list(
        self,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
        job_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Job]:
        """
        List jobs with optional filters.

        Args:
            project_id: Filter by project (uses default if not specified).
            status: Filter by status (planned, queued, running, completed, failed, cancelled).
            job_type: Filter by type (index_build, endpoint_provision, etc.).
            limit: Maximum results.
            offset: Pagination offset.

        Returns:
            List of Job objects.
        """
        project_id = project_id or self._client.config.project_id

        filters = {"limit": limit, "offset": offset}
        if project_id:
            filters["project_id"] = project_id
        if status:
            filters["status"] = status
        if job_type:
            filters["job_type"] = job_type

        return self._client.router.execute(
            "job_list",
            http_fn=lambda: self._list_via_http(filters),
            zmq_fn=lambda: self._list_via_zmq(filters),
        )

    def _list_via_http(self, filters: Dict[str, Any]) -> List[Job]:
        data = self._request("GET", "/gw/jobs", params=filters)
        return [Job.from_dict(j) for j in data.get("jobs", [])]

    def _list_via_zmq(self, filters: Dict[str, Any]) -> List[Job]:
        """List jobs via ZMQ transport."""
        if not self._client._ensure_transport_connected():
            raise R4ConnectionError("UMRS transport not available")

        transport = self._client.transport
        username = self._client._get_username()
        data = transport.request_job_list(username, filters)

        if data.get("status") == "error":
            raise R4Error(data.get("message", "Job list failed via ZMQ"))

        return [Job.from_dict(j) for j in data.get("jobs", [])]

    def events(
        self,
        *,
        after: int = 0,
        limit: int = 50,
        tail: bool = False,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The caller's terminal job events after ``after`` (ADR-105 D-5).

        ``{"events": [{id, kind, title, detail, created_at, project_id}],
        "cursor": <last id>}``, oldest first; ``tail=True`` returns no
        events and the current cursor (a new watcher starts at "now").
        """
        params: Dict[str, Any] = {"after": after, "limit": limit}
        if tail:
            params["tail"] = "true"
        if session_id:
            params["session_id"] = session_id
        return self._request("GET", "/gw/jobs/events", params=params)

    def get(self, job_id: str, *, _turn_cancel: Any = None) -> Job:
        """
        Get detailed information about a specific job.

        Args:
            job_id: The job ID.

        Returns:
            Job with all attempts.
        """
        data = self._request("GET", f"/gw/jobs/{job_id}", turn_cancel=_turn_cancel)
        return Job.from_dict(data)

    def cancel(self, job_id: str) -> Job:
        """
        Cancel a job.

        Args:
            job_id: The job ID to cancel.

        Returns:
            Updated job.
        """
        return self._client.router.execute(
            "job_cancel",
            http_fn=lambda: self._cancel_via_http(job_id),
            zmq_fn=lambda: self._cancel_via_zmq(job_id),
        )

    def _cancel_via_http(self, job_id: str) -> Job:
        self._request("POST", f"/gw/jobs/{job_id}/cancel")
        return self.get(job_id)

    def _cancel_via_zmq(self, job_id: str) -> Job:
        """Cancel a job via ZMQ transport."""
        if not self._client._ensure_transport_connected():
            raise R4ConnectionError("UMRS transport not available")

        transport = self._client.transport
        username = self._client._get_username()
        data = transport.request_job_cancel(username, {"job_id": job_id})

        if data.get("status") == "error":
            raise R4Error(data.get("message", "Job cancel failed via ZMQ"))

        return Job.from_dict(data) if "job_id" in data else self.get(job_id)

    def retry(self, job_id: str) -> Job:
        """
        Force retry a failed job.

        Args:
            job_id: The job ID to retry.

        Returns:
            Updated job.
        """
        self._request("POST", f"/gw/jobs/{job_id}/retry")
        return self.get(job_id)

    def create(
        self,
        job_type: str,
        spec: Dict[str, Any],
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
        priority: int = 0,
        max_attempts: int = 3,
        restart_policy: str = "on_crash",
        idempotency_key: Optional[str] = None,
        enqueue_immediately: bool = True,
        _turn_cancel: Any = None,
    ) -> Job:
        """
        Create a new job.

        Args:
            job_type: Type of job (index_build, endpoint_provision, etc.).
            spec: Job specification.
            project_id: Project ID (uses default if not specified).
            user_id: User ID (uses authenticated user if not specified).
            priority: Job priority (-100 to 100).
            max_attempts: Maximum retry attempts.
            restart_policy: Restart policy (on_crash, on_failure, never).
            idempotency_key: Idempotency key for deduplication.
            enqueue_immediately: Whether to immediately queue for scheduling.

        Returns:
            Created job.
        """
        project_id = project_id or self._client.config.project_id
        if not project_id:
            raise ValueError("project_id is required")

        payload = {
            "project_id": project_id,
            "user_id": user_id or "cli-user",
            "job_type": job_type,
            "spec": spec,
            "priority": priority,
            "max_attempts": max_attempts,
            "restart_policy": restart_policy,
            "enqueue_immediately": enqueue_immediately,
        }

        if idempotency_key:
            payload["idempotency_key"] = idempotency_key

        return self._client.router.execute(
            "job_submit",
            http_fn=lambda: self._create_via_http(payload, _turn_cancel),
            zmq_fn=lambda: self._create_via_zmq(payload),
        )

    def _create_via_http(self, payload: Dict[str, Any], turn_cancel: Any = None) -> Job:
        data = self._request("POST", "/gw/jobs", json=payload, turn_cancel=turn_cancel)
        return self.get(data["job_id"], _turn_cancel=turn_cancel)

    def _create_via_zmq(self, payload: Dict[str, Any]) -> Job:
        """Create a job via ZMQ transport."""
        if not self._client._ensure_transport_connected():
            raise R4ConnectionError("UMRS transport not available")

        transport = self._client.transport
        username = self._client._get_username()
        data = transport.request_job_submit(username, payload)

        if data.get("status") == "error":
            raise R4Error(data.get("message", "Job submit failed via ZMQ"))

        return Job.from_dict(data) if "job_id" in data else self.get(data.get("job_id", ""))
