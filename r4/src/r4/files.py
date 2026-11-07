"""Gateway file transfer client for r4.

Phase 2l boundary: clients never receive presigned S3 URLs. User files
(clip uploads, workflow inputs) go through ``POST /gw/files/upload`` and
download through the streaming ``GET /gw/files/download/{key}`` route.
This client wraps both so downstream commands (``r4 files``) and Python
callers share one implementation with the audit/auth/rate-limit layer.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from ._http import HTTPCore, probe_api_prefix, raise_for_status, raise_if_web_login
from .config import Config, get_config


class FilesClient:
    """Gateway-backed file upload/download helper (project-namespace aware)."""

    def __init__(self, config: Config | None = None):
        self._config = config or get_config()
        self._http = HTTPCore(self._config)
        self._prefix: str | None = None

    @property
    def _api_prefix(self) -> str:
        """'/gw' behind the edge, '/umrs' for a direct UMRS base URL."""
        if self._prefix is None:
            self._prefix = probe_api_prefix(self._config)
        return self._prefix

    # ------------------------------------------------------------------
    def _url(self, path: str) -> str:
        """Absolute URL for *path* in the deployment's mode.

        Callers pass the edge shape (``/gw/…``); against a direct-UMRS base
        it maps to ``/umrs/…`` (2026-10-20, plan H-10.2).
        """
        base = (self._config.gateway_url or "").rstrip("/")
        path = path if path.startswith("/") else "/" + path
        if path.startswith("/gw/") and self._api_prefix == "/umrs":
            path = "/umrs/" + path[len("/gw/") :]
        return f"{base}{path}"

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------
    def upload_file(
        self,
        path: str | Path,
        *,
        key: str | None = None,
        purpose: str = "general",
        bucket: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Upload one local file into the project bucket — or, with
        ``bucket``, at ``key`` (default: the file name) in a bucket you own
        (ADR-079; see :meth:`create_bucket`). The body streams; nothing is
        held in memory.

        Returns the gateway payload: ``key``, ``bucket``, ``uri``, ``size``.
        ``key`` is server-prefixed with the active project id — callers pass
        the path *under* the project namespace (e.g. ``clips/sample.mp4``).
        """
        import httpx

        from ._http import map_httpx_error

        file_path = Path(path)
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        headers = self._http.build_headers()
        headers.pop("Content-Type", None)
        data = {"purpose": purpose}
        if key:
            data["key"] = key
        if bucket:
            data["bucket"] = bucket
            timeout = timeout or 7200.0

        try:
            with self._http._client(timeout) as client:  # noqa: SLF001 - shared HTTP config
                with file_path.open("rb") as handle:
                    response = client.post(
                        self._url("/gw/files/upload"),
                        files={"file": (file_path.name, handle, content_type)},
                        data=data,
                        headers=headers,
                    )
                raise_for_status(response)
                raise_if_web_login(response)
                return response.json()
        except httpx.RequestError as exc:
            raise map_httpx_error(exc, gateway_url=self._config.gateway_url) from exc

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------
    def download_file(
        self,
        key: str,
        *,
        dest: str | Path | None = None,
        bucket: str | None = None,
        timeout: float | None = None,
    ) -> bytes | None:
        """Stream one object back through the return lane.

        ``bucket`` selects the S3 bucket (e.g. ``r4-data-output``); default is
        the upload bucket from server config. If ``dest`` is given, bytes are
        written there and ``None`` is returned; otherwise bytes are returned.
        """
        import httpx

        from ._http import map_httpx_error

        url = self._url(f"/gw/files/download/{key}")
        params = {"bucket": bucket} if bucket else None
        headers = self._http.build_headers()
        # JSON listed too: an expired credential then gets the gateway's JSON
        # 401 instead of a 302 to the HTML login page saved as the file.
        headers["Accept"] = "application/octet-stream, application/json"

        try:
            with self._http._client(timeout or 600.0) as client:  # noqa: SLF001
                if dest is None:
                    response = client.get(url, params=params, headers=headers)
                    raise_for_status(response)
                    raise_if_web_login(response)
                    return response.content
                # Stream to disk: result files can be larger than memory.
                target = Path(dest)
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_name(target.name + ".part")
                with client.stream("GET", url, params=params, headers=headers) as response:
                    if response.is_error:
                        response.read()
                    raise_for_status(response)
                    raise_if_web_login(response)
                    with partial.open("wb") as handle:
                        for chunk in response.iter_bytes(chunk_size=8 * 1024 * 1024):
                            handle.write(chunk)
                partial.replace(target)
                return None
        except httpx.RequestError as exc:
            raise map_httpx_error(exc, gateway_url=self._config.gateway_url) from exc

    # ------------------------------------------------------------------
    # Owned buckets (ADR-079): the server performs every S3 call.
    # ------------------------------------------------------------------
    def _json(self, method: str, path: str, *, params=None, body=None, timeout=None) -> Any:
        import httpx

        from ._http import map_httpx_error

        headers = self._http.build_headers()
        headers["Accept"] = "application/json"
        try:
            with self._http._client(timeout or 60.0) as client:  # noqa: SLF001
                response = client.request(
                    method, self._url(path), params=params, json=body, headers=headers
                )
                raise_for_status(response)
                raise_if_web_login(response)
                return response.json()
        except httpx.RequestError as exc:
            raise map_httpx_error(exc, gateway_url=self._config.gateway_url) from exc

    def create_bucket(self, project: str, alias: str) -> dict[str, Any]:
        """Create (or re-confirm) your ``<user>.<project>.<alias>`` bucket and
        its ``-output`` pair (``POST /gw/buckets``)."""
        return self._json("POST", "/gw/buckets", body={"project": project, "alias": alias})

    def list_buckets(self) -> list[dict[str, Any]]:
        """Your personal and provisioned buckets (``GET /gw/buckets``)."""
        return list(self._json("GET", "/gw/buckets").get("buckets") or [])

    def list_objects(self, bucket: str, prefix: str = "") -> list[dict[str, Any]]:
        """Every object under *prefix* in a bucket you own (all pages)."""
        objects: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            params = {"bucket": bucket, "prefix": prefix}
            if token:
                params["token"] = token
            page = self._json("GET", "/gw/files/list", params=params)
            objects.extend(page.get("objects") or [])
            token = page.get("next_token")
            if not token:
                return objects
