"""Lightweight HTTP client for A2L (Artifact & Agent Layer) API.

Cross-package communication via HTTP only — r4 never imports a2l directly.
See: CONSTITUTION.md §mutation-safety.

Provides:
- ``A2LClient`` — typed client for /a2l/artifacts/derive and /a2l/agent-executions
- Graceful degradation when A2L is unreachable (returns None, logs warning)

Usage:
    from r4.a2l_client import A2LClient
    from r4 import Client

    r4_client = Client()
    a2l = A2LClient(r4_client)

    # Derive an artifact (agent write path)
    art = a2l.derive(
        parents=["art_abc123"],
        operation="segmentation",
        params={"model": "sam3"},
        type="segmentation",
        uri="s3://bucket/output.mask",
        user_id="alice",
        input_data_hash="sha256:def456...",
    )

    # Create agent execution record
    exec_id = a2l.create_execution(
        agent_name="research-orchestrator",
        goal="Analyze protein folding",
        model="qwen2.5-7b",
        user_id="alice",
    )

    # Update execution status
    a2l.update_execution(exec_id, status="completed", outputs={"result": "..."})
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from ._http import HTTPCore

if TYPE_CHECKING:
    from .client import Client

logger = logging.getLogger(__name__)

# A2L ledger path in each mode (2026-10-18, cleanup C-2(v)). Behind the edge
# the client calls ``/gw/a2l/*``, which nginx rewrites to UMRS's a2l-ledger
# router ``/umrs/a2l/ledger/*`` (gateway/nginx/conf.d/server.conf, location
# /gw/a2l/); in direct-UMRS mode the client calls that router itself. The
# mode prefix comes from ``Client._api_prefix``.
_DIRECT_PREFIX = "/umrs"
_DIRECT_LEDGER_SUFFIX = "/ledger"


def a2l_base_url(client: "Client") -> str:
    """The A2L base URL in *client*'s mode (:attr:`A2LClient._base_url` shared).

    ``…/gw/a2l`` behind the edge, ``…/umrs/a2l/ledger`` direct. Used by the
    A2L CLI families (``r4 agent-exec`` / ``r4 artifact``) so the split lives
    exactly once (2026-10-20, plan H-10.2).
    """
    prefix = str(getattr(client, "_api_prefix", "") or "") or "/gw"
    ledger = _DIRECT_LEDGER_SUFFIX if prefix == _DIRECT_PREFIX else ""
    return f"{str(client.config.gateway_url).rstrip('/')}{prefix}/a2l{ledger}"


# A2L artifact types for hypothesis-driven research
_A2L_HYPOTHESIS_TYPE = "hypothesis"
_A2L_EVIDENCE_TYPE = "evidence"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Response models (subset of A2L models, defined here to avoid import)
# ---------------------------------------------------------------------------


@dataclass
class ArtifactRef:
    """Lightweight artifact reference from A2L derive response."""

    id: str
    type: str
    uri: str
    status: str
    user_id: str
    created_at: Optional[datetime] = None
    parents: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtifactRef":
        created = data.get("created_at")
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                created = None
        return cls(
            id=data.get("id", ""),
            type=data.get("type", ""),
            uri=data.get("uri", ""),
            status=data.get("status", "pending"),
            user_id=data.get("user_id", ""),
            created_at=created,
            parents=data.get("parents", []),
            metadata=data.get("metadata", {}),
        )


@dataclass
class ExecutionRef:
    """Lightweight agent execution reference."""

    execution_id: str
    agent_name: str
    status: str
    goal: str
    user_id: str
    project_id: Optional[str] = None
    model: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    outputs: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionRef":
        def _parse_dt(val: Any) -> Optional[datetime]:
            if isinstance(val, str):
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            return None

        return cls(
            execution_id=data.get("execution_id", ""),
            agent_name=data.get("agent_name", ""),
            status=data.get("status", "pending"),
            goal=data.get("goal", ""),
            user_id=data.get("user_id", ""),
            project_id=data.get("project_id"),
            model=data.get("model"),
            created_at=_parse_dt(data.get("created_at")),
            completed_at=_parse_dt(data.get("completed_at")),
            outputs=data.get("outputs", {}),
            trace=data.get("trace", []),
        )


# ---------------------------------------------------------------------------
# A2L Client
# ---------------------------------------------------------------------------


class A2LClient:
    """HTTP client for A2L Artifact & Agent Layer.

    Uses the same Gateway HTTP transport as other R4 sub-clients.
    Gracefully degrades if A2L is unreachable — logs warnings, returns None.
    """

    def __init__(self, client: "Client"):
        """Initialize with an R4 Client.

        Args:
            client: R4 Client for shared HTTP transport and config.
        """
        self._client = client

    @property
    def _http(self) -> HTTPCore:
        """Reuse the platform HTTP core from the parent client."""
        return self._client.platform_http

    @property
    def _base_url(self) -> str:
        """``…/gw/a2l`` behind the edge, ``…/umrs/a2l/ledger`` direct."""
        return a2l_base_url(self._client)

    def _post(self, path: str, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        """POST to A2L, returning parsed JSON or None on failure."""
        url = f"{self._base_url}{path}"
        try:
            return self._http.request("POST", url, json_body=body)
        except Exception as e:
            logger.warning("A2L POST %s failed: %s", path, e)
            return None

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        """GET from A2L, returning parsed JSON or None on failure."""
        url = f"{self._base_url}{path}"
        try:
            return self._http.request("GET", url, params=params)
        except Exception as e:
            logger.warning("A2L GET %s failed: %s", path, e)
            return None

    def _patch(self, path: str, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        """PATCH to A2L, returning parsed JSON or None on failure."""
        url = f"{self._base_url}{path}"
        try:
            return self._http.request("PATCH", url, json_body=body)
        except Exception as e:
            logger.warning("A2L PATCH %s failed: %s", path, e)
            return None

    # ── Artifact Derive ────────────────────────────────────────────────

    def derive(
        self,
        parents: list[str],
        operation: str,
        params: dict[str, Any],
        type: str,
        uri: str,
        user_id: str,
        input_data_hash: str,
        project_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        type_schema: Optional[dict[str, Any]] = None,
        artifact_class: Optional[str] = None,
    ) -> Optional[ArtifactRef]:
        """Derive a new artifact with guaranteed lineage.

        This is the primary agent write path. Uses POST /a2l/artifacts/derive.

        Args:
            parents: Parent artifact IDs (must exist in A2L).
            operation: Operation name (e.g., "segmentation", "llm_chat").
            params: Operation parameters for deterministic ID.
            type: Output artifact type.
            uri: S3 URI for the output artifact.
            user_id: Owner user ID.
            input_data_hash: SHA-256 of the input data.
            project_id: Optional project scope.
            metadata: Arbitrary metadata.
            type_schema: Type-specific schema for compatibility checks.
            artifact_class: Coarse artifact class.

        Returns:
            ArtifactRef on success, None on failure.
        """
        body: dict[str, Any] = {
            "parents": parents,
            "operation": operation,
            "params": params,
            "type": type,
            "uri": uri,
            "user_id": user_id,
            "input_data_hash": input_data_hash,
        }
        if project_id:
            body["project_id"] = project_id
        if metadata:
            body["metadata"] = metadata
        if type_schema:
            body["type_schema"] = type_schema
        if artifact_class:
            body["artifact_class"] = artifact_class

        data = self._post("/artifacts/derive", body)
        if data is None:
            return None
        return ArtifactRef.from_dict(data)

    def list_artifacts(
        self,
        type: Optional[str] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> Optional[list[ArtifactRef]]:
        """List artifacts with optional filters. GET /a2l/artifacts."""
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if type:
            params["type"] = type
        if user_id:
            params["user_id"] = user_id
        if project_id:
            params["project_id"] = project_id
        if status:
            params["status"] = status

        data = self._get("/artifacts", params)
        if data is None:
            return None
        return [ArtifactRef.from_dict(item) for item in data.get("items", [])]

    def get_artifact(self, artifact_id: str) -> Optional[ArtifactRef]:
        """Get a single artifact by ID. GET /a2l/artifacts/{id}."""
        data = self._get(f"/artifacts/{artifact_id}")
        if data is None:
            return None
        return ArtifactRef.from_dict(data)

    # ── Agent Executions ───────────────────────────────────────────────

    def create_execution(
        self,
        agent_name: str,
        goal: str,
        user_id: str,
        model: Optional[str] = None,
        project_id: Optional[str] = None,
        prompt_hash: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """Create an agent execution record. POST /a2l/agent-executions.

        Returns execution_id on success, None on failure.
        """
        if prompt_hash is None:
            prompt_hash = hashlib.sha256(goal.encode()).hexdigest()[:16]

        body: dict[str, Any] = {
            "agent_name": agent_name,
            "goal": goal,
            "user_id": user_id,
            "prompt_hash": prompt_hash,
            "status": "running",
        }
        if model:
            body["model"] = model
        if project_id:
            body["project_id"] = project_id
        if params:
            body["params"] = params or {}
        if metadata:
            body["metadata"] = metadata

        data = self._post("/agent-executions", body)
        if data is None:
            return None
        return data.get("execution_id")

    def get_execution(self, execution_id: str) -> Optional[ExecutionRef]:
        """Get an agent execution by ID. GET /a2l/agent-executions/{id}."""
        data = self._get(f"/agent-executions/{execution_id}")
        if data is None:
            return None
        return ExecutionRef.from_dict(data)

    def update_execution(
        self,
        execution_id: str,
        status: str,
        outputs: Optional[dict[str, Any]] = None,
        trace: Optional[list[dict[str, Any]]] = None,
        error: Optional[str] = None,
    ) -> bool:
        """Update an agent execution. PATCH /a2l/agent-executions/{id}.

        Returns True on success.
        """
        body: dict[str, Any] = {"status": status}
        if outputs is not None:
            body["outputs"] = outputs
        if trace is not None:
            body["trace"] = trace
        if error is not None:
            body["error"] = error

        data = self._patch(f"/agent-executions/{execution_id}", body)
        return data is not None

    def list_executions(
        self,
        agent_name: Optional[str] = None,
        status: Optional[str] = None,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> Optional[list[ExecutionRef]]:
        """List agent executions with filters. GET /a2l/agent-executions."""
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if agent_name:
            params["agent_name"] = agent_name
        if status:
            params["status"] = status
        if user_id:
            params["user_id"] = user_id
        if project_id:
            params["project_id"] = project_id

        data = self._get("/agent-executions", params)
        if data is None:
            return None
        return [ExecutionRef.from_dict(item) for item in data.get("items", [])]

    # ── Convenience ────────────────────────────────────────────────────

    @staticmethod
    def hash_content(content: str) -> str:
        """Compute SHA-256 hash for input_data_hash fields."""
        return hashlib.sha256(content.encode()).hexdigest()
