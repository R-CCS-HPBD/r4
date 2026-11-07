"""
R4 Data Catalog Client.

Agent-navigable lab upload metadata (Postgres ``catalog_objects`` + S3 blobs).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import R4Error

if TYPE_CHECKING:
    from .client import Client


class DataCatalogClient:
    """HTTP client for UMRS data catalog routes (``/gw/catalog/*``)."""

    def __init__(self, client: "Client") -> None:
        self._client = client

    def _catalog_url(self, path: str) -> str:
        base = (self._client.config.gateway_url or "").rstrip("/")
        prefix = self._client._api_prefix
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{base}{prefix}/catalog{suffix}"

    def search(
        self,
        *,
        query: str = "",
        limit: int = 20,
        modality: str | None = None,
        state: str | None = None,
        tag: str | None = None,
        prefix: str | None = None,
    ) -> dict[str, Any]:
        """Lexical grep over catalog metadata for the configured project."""
        params: dict[str, Any] = {"q": query, "limit": limit}
        if modality:
            params["modality"] = modality
        if state:
            params["state"] = state
        if tag:
            params["tag"] = tag
        if prefix:
            params["prefix"] = prefix
        return self._client.get(self._catalog_url("/search"), params=params)

    def list(
        self,
        project_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List catalog objects for a project (metadata only).

        Uses ``GET /catalog/search`` with a project prefix filter. Fetches
        ``ready`` and ``pending`` rows separately (API max 100 per call) and
        merges by ``s3_key``.
        """
        prefix = project_id if project_id.endswith("/") else f"{project_id}/"
        merged: dict[str, dict[str, Any]] = {}
        for state in ("ready", "pending"):
            try:
                data = self.search(
                    query="",
                    prefix=prefix,
                    state=state,
                    limit=min(limit, 100),
                )
            except R4Error:
                raise
            except Exception as exc:
                raise R4Error(f"Failed to list catalog objects: {exc}") from exc
            for item in data.get("results", []):
                if isinstance(item, dict):
                    key = str(item.get("s3_key", ""))
                    if key:
                        merged[key] = item
        return list(merged.values())

    def read(
        self,
        s3_key: str,
        *,
        offset: int = 0,
        length: int = 1_048_576,
        source: str = "auto",
    ) -> dict[str, Any]:
        """Byte-bounded read of one catalog object (``auto`` prefers extracted text)."""
        if source not in {"auto", "raw", "extracted"}:
            raise R4Error(f"source must be auto, raw, or extracted (got {source!r})")
        params: dict[str, Any] = {
            "s3_key": s3_key,
            "offset": offset,
            "length": length,
            "source": source,
        }
        return self._client.get(self._catalog_url("/read"), params=params)

    def stats(self) -> dict[str, Any]:
        """Aggregate catalog counts by state and modality for the configured project."""
        return self._client.get(self._catalog_url("/stats"))
