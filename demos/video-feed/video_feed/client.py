"""Minimal stdlib HTTPS client for the r4 Gateway (upload + workflow trigger).

Runs on low-dependency hosts (cluster login nodes with python3.9, field laptops) — uses
urllib only. Auth = bearer token from environment or explicit argument.

Client invariants honored:
- uploads go through ``POST {gateway}/gw/files/upload`` (Phase 2l)
- workflow runs go through ``POST {gateway}/gw/workflows/{name}/trigger``
- presigned S3 URLs are never returned to external callers (platform keeps
  them internal; this client never expects them)
"""

from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT = 120


class GatewayError(RuntimeError):
    """Gateway returned a non-2xx result or an unreachable endpoint."""


@dataclass
class GatewayClient:
    gateway_url: str
    token: str
    project_id: str = "default"
    timeout: int = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls, gateway_url: str | None = None, project_id: str = "default") -> GatewayClient:
        url = (gateway_url or os.environ.get("R4_GATEWAY_URL", "")).rstrip("/")
        token = os.environ.get("R4_API_KEY") or os.environ.get("R4_TOKEN", "")
        if not url:
            raise GatewayError("gateway url missing (pass --gateway or set R4_GATEWAY_URL)")
        if not token:
            raise GatewayError("api token missing (set R4_API_KEY)")
        return cls(gateway_url=url, token=token, project_id=project_id)

    # ------------------------------------------------------------------
    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.token}", "X-Project-Id": self.project_id}
        if extra:
            h.update(extra)
        return h

    def _request(
        self, method: str, path: str, body: bytes | None, headers: dict[str, str]
    ) -> dict[str, Any]:
        req = urllib.request.Request(
            url=f"{self.gateway_url}{path}",
            data=body,
            headers=self._headers(headers),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise GatewayError(f"{method} {path} → {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise GatewayError(f"{method} {path} unreachable: {e.reason}") from e
        if ctype.startswith("application/json") and raw:
            return json.loads(raw)
        return {"status": "ok", "bytes": len(raw)}

    # ------------------------------------------------------------------
    @staticmethod
    def _multipart(
        fields: dict[str, str],
        filename: str,
        content: bytes,
        content_type: str,
    ) -> tuple[bytes, str]:
        boundary = f"vf-{uuid.uuid4().hex}"
        parts: list[bytes] = []
        for name, value in fields.items():
            parts += [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        parts += [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"

    # ------------------------------------------------------------------
    def upload_file(
        self,
        path: str | Path,
        *,
        key: str | None = None,
        purpose: str = "video-feed",
        chunk_size: int = 0,  # noqa: ARG002 - reserved for streaming (see docstring)
    ) -> dict[str, Any]:
        """Upload one clip through the gateway into the project bucket.

        ``chunk_size`` reserved for future streaming (gateway buffers now).
        Returns UMRS payload (key, uri, size, ...).
        """
        p = Path(path)
        fields = {"purpose": purpose}
        if key:
            fields["key"] = key
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        body, ctype_header = self._multipart(fields, p.name, p.read_bytes(), ctype)
        return self._request(
            "POST",
            "/gw/files/upload",
            body,
            {
                "Content-Type": ctype_header,
                "Accept": "application/json",
            },
        )

    def trigger_workflow(
        self,
        name: str,
        *,
        parameters: dict[str, Any] | None = None,
        input_files: list[str] | None = None,
        wait: bool = False,
    ) -> dict[str, Any]:
        """Trigger a registered workflow."""
        payload: dict[str, Any] = {
            "parameters": parameters or {},
            "input_files": input_files or [],
            "project_id": self.project_id,
            "wait": wait,
        }
        return self._request(
            "POST",
            f"/gw/workflows/{name}/trigger",
            json.dumps(payload).encode(),
            {"Content-Type": "application/json", "Accept": "application/json"},
        )

    def upload_workflow(self, zip_path: str | Path, name: str | None = None) -> dict[str, Any]:
        """Register a workflow ZIP bundle on the platform.
        Bundle shape (arbitrary dir with workflow.json at root): see r4-workflow
        runtime. The server-side name is usually taken from workflow.json
        unless ``name`` is provided here.
        """
        z = Path(zip_path)
        fields: dict[str, str] = {}
        if name:
            fields["workflow_name"] = name
        body, ctype_header = self._multipart(fields, z.name, z.read_bytes(), "application/zip")
        return self._request(
            "POST",
            "/gw/workflows/upload",
            body,
            {"Content-Type": ctype_header, "Accept": "application/json"},
        )

    def list_workflows(self, name_filter: str | None = None) -> dict[str, Any]:
        q = f"?name={name_filter}" if name_filter else ""
        return self._request(
            "GET",
            f"/gw/workflows{q}",
            None,
            {"Accept": "application/json"},
        )

    def execution_status(self, execution_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/gw/workflows/executions/{execution_id}",
            None,
            {"Accept": "application/json"},
        )

    def execution_results(self, execution_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/gw/workflows/executions/{execution_id}/results",
            None,
            {"Accept": "application/json"},
        )

    def cancel(self, execution_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/gw/workflows/executions/{execution_id}/cancel",
            b"{}",
            {"Content-Type": "application/json", "Accept": "application/json"},
        )

    def wait_execution(
        self, execution_id: str, *, poll_s: float = 5.0, timeout_s: float = 3600.0
    ) -> Iterator[dict[str, Any]]:
        """Yield status payloads until terminal or timeout."""
        deadline = time.monotonic() + timeout_s
        while True:
            st = self.execution_status(execution_id)
            state = str(st.get("state") or st.get("status", "")).lower()
            yield st
            if state in {"succeeded", "success", "completed", "failed", "error", "cancelled"}:
                return
            if time.monotonic() > deadline:
                raise GatewayError(f"wait timeout for {execution_id}")
            time.sleep(poll_s)

    def download_file(
        self,
        key: str,
        dest: str | Path | None = None,
        bucket: str | None = None,
    ) -> bytes | None:
        """Download a project object through the return lane (UMRS streaming).

        Path-form endpoint ``/gw/files/download/<key>``; never exposes presigned
        URLs (Phase 2l). ``bucket`` selects the S3 bucket (e.g. the workflow
        output bucket); default is the upload bucket from server config.
        If ``dest`` is given, writes bytes to that path. Otherwise returns
        the bytes.
        """
        url = f"{self.gateway_url}/gw/files/download/{key}"
        if bucket:
            url += "?" + urllib.parse.urlencode({"bucket": bucket})
        req = urllib.request.Request(
            url=url,
            headers=self._headers({"Accept": "application/octet-stream"}),
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400] if e.readable() else ""
            raise GatewayError(f"download {key} → {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise GatewayError(f"download {key} unreachable: {e.reason}") from e
        if dest is not None:
            Path(dest).write_bytes(data)
            return None
        return data
