"""R4 Artifact Client.

HTTP client for accessing the UMRS Artifact Registry via the Gateway.

Uses /gw/artifacts/* or /umrs/artifacts/* depending on deployment mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import Client


class ArtifactClient:
    """HTTP client for UMRS artifact registry routes (``/gw/artifacts/*``)."""

    def __init__(self, client: "Client") -> None:
        self._client = client

    def _artifact_url(self, path: str) -> str:
        base = (self._client.config.gateway_url or "").rstrip("/")
        prefix = self._client._api_prefix
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{base}{prefix}/artifacts{suffix}"

    def list(
        self,
        *,
        artifact_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        execution_id: str | None = None,
        name_prefix: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """List artifacts with optional filters.

        Args:
            artifact_type: Filter by artifact type (dataset, model, etc.).
            owner_id: Filter by owner.
            project_id: Filter by project.
            execution_id: Filter by execution.
            name_prefix: Filter by name prefix.
            limit: Max results.
            offset: Result offset.

        Returns:
            List of artifact dicts.

        Raises:
            R4Error: On HTTP error.
        """
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }
        if artifact_type:
            params["artifact_type"] = artifact_type
        if owner_id:
            params["owner_id"] = owner_id
        if project_id:
            params["project_id"] = project_id
        if execution_id:
            params["execution_id"] = execution_id
        if name_prefix:
            params["name_prefix"] = name_prefix

        return self._client.get(self._artifact_url(""), params=params)

    def get(self, artifact_id: str) -> dict:
        """Get an artifact by id.

        Args:
            artifact_id: UUID of the artifact.

        Returns:
            Artifact dict.

        Raises:
            R4Error: On HTTP error (404 → artifact not found).
        """
        return self._client.get(self._artifact_url(f"/{artifact_id}"))

    def lineage(
        self,
        artifact_id: str,
        depth: int = 3,
    ) -> list[dict]:
        """Get lineage edges for an artifact.

        Args:
            artifact_id: UUID of the artifact.
            depth: Max lineage depth (1–10).

        Returns:
            List of lineage edge dicts.

        Raises:
            R4Error: On HTTP error.
        """
        return self._client.get(
            self._artifact_url(f"/{artifact_id}/lineage"),
            params={"depth": depth},
        )

    def register(
        self,
        artifact_type: str,
        name: str,
        owner_id: str,
        uri: str,
        *,
        project_id: str | None = None,
        execution_id: str | None = None,
        workflow_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        """Register a new artifact in the registry.

        Args:
            artifact_type: Type (dataset, model, result, etc.).
            name: Artifact name.
            owner_id: Owner/user ID.
            uri: File URI (s3://... or file path).
            project_id: Optional project/namespace.
            execution_id: Producing execution.
            workflow_id: Producing workflow.
            metadata: Optional metadata dict.

        Returns:
            Created artifact dict.

        Raises:
            R4Error: On HTTP error.
        """
        payload: dict[str, Any] = {
            "artifact_type": artifact_type,
            "name": name,
            "owner_id": owner_id,
            "uri": uri,
        }
        if project_id:
            payload["project_id"] = project_id
        if execution_id:
            payload["execution_id"] = execution_id
        if workflow_id:
            payload["workflow_id"] = workflow_id
        if metadata:
            payload["metadata"] = metadata

        return self._client.post(self._artifact_url(""), json=payload)
