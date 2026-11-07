"""Vision inference client for r4.

The client talks only to Gateway routes. Local files are uploaded through
Gateway/UMRS first; presigned URLs remain internal to UMRS and BentoML.
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from ._http import HTTPCore, probe_api_prefix, raise_for_status, raise_if_web_login
from .config import Config, get_config


class VisionClient:
    """Gateway-backed vision inference helper."""

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

    def _url(self, path: str) -> str:
        """Absolute URL for *path* in the deployment's mode (FilesClient._url twin).

        Callers pass the edge shape (``/gw/…``); against a direct-UMRS base
        it maps to ``/umrs/…`` (2026-10-20, plan H-10.2).
        """
        base = (self._config.gateway_url or "").rstrip("/")
        path = path if path.startswith("/") else "/" + path
        if path.startswith("/gw/") and self._api_prefix == "/umrs":
            path = "/umrs/" + path[len("/gw/") :]
        return f"{base}{path}"

    def upload_file(
        self,
        path: str | Path,
        *,
        key: str | None = None,
        purpose: str = "vision",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Upload a local file through Gateway and return its storage URI."""
        import httpx

        file_path = Path(path)
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        headers = self._http.build_headers()
        headers.pop("Content-Type", None)
        data = {"purpose": purpose}
        if key:
            data["key"] = key

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
            from ._http import map_httpx_error

            raise map_httpx_error(exc, gateway_url=self._config.gateway_url) from exc

    def submit(
        self,
        image: str | Path,
        *,
        model: str | None = None,
        endpoint_id: str | None = None,
        adapter: str | None = None,
        inference_config: dict[str, Any] | None = None,
        key: str | None = None,
        output_bucket: str | None = None,
        output_artifacts: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Submit a vision inference task."""
        image_text = str(image)
        if not image_text.startswith("s3://"):
            image_path = Path(image_text)
            if image_path.exists():
                upload = self.upload_file(image_path, key=key, timeout=timeout)
                image_text = str(upload["uri"])

        payload: dict[str, Any] = {
            "image_uri": image_text,
            "inference_config": inference_config or {},
        }
        if model:
            payload["model"] = model
        if endpoint_id:
            payload["endpoint_id"] = endpoint_id
        if adapter:
            payload["adapter"] = adapter
        if output_bucket:
            payload["output_bucket"] = output_bucket
        if output_artifacts:
            payload["output_artifacts"] = output_artifacts
        return self._http.post(
            self._url("/gw/vision/infer/submit"),
            json_body=payload,
            timeout=timeout,
        )

    def status(
        self,
        task_id: str,
        *,
        model: str | None = None,
        endpoint_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        params = {"task_id": task_id}
        if model:
            params["model"] = model
        if endpoint_id:
            params["endpoint_id"] = endpoint_id
        return self._http.get(self._url("/gw/vision/infer/status"), params=params, timeout=timeout)

    def cancel(
        self,
        task_id: str,
        *,
        model: str | None = None,
        endpoint_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        params = {"task_id": task_id}
        if model:
            params["model"] = model
        if endpoint_id:
            params["endpoint_id"] = endpoint_id
        return self._http.post(
            self._url("/gw/vision/infer/cancel"),
            json_body=None,
            params=params,
            timeout=timeout,
        )

    def get(
        self,
        task_id: str,
        *,
        model: str | None = None,
        endpoint_id: str | None = None,
        output: str | Path | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | bytes:
        """Fetch a vision task result, returning JSON when possible or raw bytes."""
        params = {"task_id": task_id}
        if model:
            params["model"] = model
        if endpoint_id:
            params["endpoint_id"] = endpoint_id
        headers = self._http.build_headers()
        with self._http._client(timeout) as client:  # noqa: SLF001 - shared HTTP config
            response = client.get(self._url("/gw/vision/infer/get"), params=params, headers=headers)
        raise_for_status(response)
        raise_if_web_login(response)
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        data = response.content
        if output:
            Path(output).write_bytes(data)
            return {"output": str(output), "bytes": len(data), "content_type": content_type}
        try:
            return json.loads(response.text)
        except ValueError:
            return data
