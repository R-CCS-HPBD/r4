"""R4 client for sealed user-credential routes (R1-6).

Routes (owner-scoped, verified identity):
    POST   /umrs/user-sources/sealed          push
    GET    /umrs/user-sources/sealed          list-mine
    DELETE /umrs/user-sources/sealed/{name}   unpush
    GET    /umrs/user-sources/audit           audit-mine

The api_key crosses the wire exactly once on push over TLS (design F-1).
Nothing in this client logs or retains it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .errors import R4Error

if TYPE_CHECKING:
    from .client import Client


class UserSourcesClient:
    """HTTP client for /umrs/user-sources/* (sealed credentials)."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def _url(self, path: str) -> str:
        base = (self._client.config.gateway_url or "").rstrip("/")
        prefix = self._client._api_prefix
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{base}{prefix}/user-sources{suffix}"

    def push(
        self,
        *,
        name: str,
        provider_kind: str,
        base_url: str,
        api_key: str,
        allow_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "provider_kind": provider_kind,
            "base_url": base_url,
            "api_key": api_key,
            "allow_paths": list(allow_paths or []),
        }
        return self._client.post(self._url("/sealed"), json=payload)

    def list_mine(self, *, with_leases: bool = False) -> list[dict[str, Any]]:
        params = {"with_leases": "true"} if with_leases else {}
        result = self._client.get(self._url("/sealed"), params=params)
        if not isinstance(result, list):
            raise R4Error("sealed sources list did not return an array")
        return result

    def unpush(self, name: str) -> dict[str, Any]:
        return self._client.delete(self._url(f"/sealed/{name}"))

    def audit(self, *, source_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if source_id:
            params["source_id"] = source_id
        result = self._client.get(self._url("/audit"), params=params)
        if not isinstance(result, list):
            raise R4Error("sealed sources audit did not return an array")
        return result
