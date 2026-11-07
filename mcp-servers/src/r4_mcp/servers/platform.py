"""
Platform MCP Server (WS-5; ADR-105 D-2 since S5).

Exposes the R4 Platform Gateway HTTP API as MCP tools, enabling
external AI agents to operate the platform programmatically.

Two families of tools:

- **Generated** (``generated_platform``): one tool per ``mcp-eligible`` row
  of the UMRS agent manifest (``contracts/umrs-http/v1/agent-tools.json``,
  mirrored into ``r4_mcp/contracts/``): ``job_list``, ``job_status``,
  ``job_cancel``, ``job_retry``, ``job_events``, ``workflow_list``,
  ``workflow_describe``, ``workflow_plan``, ``workflow_run``,
  ``workflow_status``, ``workflow_results``, ``workflow_cancel``,
  ``rag_query``, ``rag_list_documents``. Their names, parameters and
  descriptions follow the UMRS API; nothing here is hand-written.
- **Hand-written residual: no manifest row yet.** ``endpoint_*``,
  ``model_*``, ``platform_health`` (untyped or outside S1's allowlist),
  ``platform_guide`` (a local playbook, no HTTP), ``workflow_upload``
  (multipart archive from a local path), ``workflow_wait`` (client-side
  polling over ``workflow_status``) and ``workflow_trigger`` (alias that
  delegates to the generated ``workflow_run``). A residual tool moves to
  the generated family when its operation joins the allowlist.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx
from r4_contracts.agent_tool_playbook import (
    PLATFORM_GUIDE_TOOL_DESCRIPTION,
    PLATFORM_GUIDE_TOPICS,
    platform_guide,
)
from r4_contracts.workflows import WORKFLOW_TOOL_HELP

from ..base import MCPServer, Tool, ToolResult
from ..workflow_policy import load_policy
from .generated_platform import generated_tools

logger = logging.getLogger(__name__)


def _response_body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"detail": resp.text}


class PlatformServer(MCPServer):
    """MCP server that wraps the R4 Platform Gateway API."""

    name = "platform"
    version = "1.0.0"

    def __init__(
        self,
        gateway_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout_seconds: int = 30,
        transport: str = "stdio",
        http_transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self._gateway_url = gateway_url.rstrip("/")
        self._api_key = (
            api_key or os.environ.get("R4_GATEWAY_TOKEN") or os.environ.get("R4_API_KEY")
        )
        self._timeout = timeout_seconds
        # Test seam (httpx.MockTransport); None = the real network.
        self._http_transport = http_transport
        super().__init__(transport=transport)
        self._register_generated_tools()

    def _register_generated_tools(self) -> None:
        """ADR-105 D-2: the manifest-generated family (see module docstring)."""
        tools, self.skipped_operations = generated_tools(self)
        for tool in tools:
            self.register_tool(tool)

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._http_transport)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Send a request to the Gateway and return the parsed response."""
        url = f"{self._gateway_url}{path}"
        # Filter None params
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        async with self._client() as client:
            resp = await client.request(
                method,
                url,
                headers=self._build_headers(),
                params=params or None,
                json=json_body,
            )
            return {"status": resp.status_code, "body": _response_body(resp)}

    def _format(self, result: dict[str, Any]) -> ToolResult:
        """Format a Gateway response as a ToolResult."""
        status = result["status"]
        body = result["body"]
        text = json.dumps(body, indent=2, default=str)
        if status >= 400:
            return ToolResult.error(f"HTTP {status}: {text}")
        return ToolResult.success(text)

    def _workflow_policy_guard(self, operation: str) -> ToolResult | None:
        policy = load_policy()
        if policy.get(operation):
            return None
        return ToolResult.error(
            json.dumps(
                {
                    "error": "policy_denied",
                    "code": "workflow_operation_disabled",
                    "operation": operation,
                    "message": (
                        f"Workflow operation '{operation}' is disabled by the MCP "
                        "platform-server local policy (evaluated client-side; no "
                        "request reached the gateway/UMRS — this is not a "
                        "server-side deny)."
                    ),
                    "surface": "mcp_platform_server.local_policy",
                    "remediation": (
                        "Set R4_WORKFLOW_POLICY_FILE to a JSON enabling this "
                        f'operation (e.g. {{"defaults": {{"{operation}": true}}}}) '
                        "in the shell that launches the agent. The gateway-side "
                        "ADR-027 role gate (workflow_author claim or admin) "
                        "applies after this local guard is satisfied."
                    ),
                },
                indent=2,
            )
        )

    # ── Endpoints (no manifest row yet) ──────────────────────────────

    @Tool(
        name="endpoint_list",
        description="List active inference endpoints.",
        parameters={
            "model": {
                "type": "string",
                "description": "Filter by model name",
                "required": False,
            },
            "include_stopped": {
                "type": "boolean",
                "description": "Include non-ready endpoints",
                "required": False,
                "default": False,
            },
        },
    )
    async def endpoint_list(
        self,
        model: Optional[str] = None,
        include_stopped: bool = False,
    ) -> ToolResult:
        result = await self._request(
            "GET",
            "/gw/endpoints",
            params={"model": model, "include_stopped": include_stopped},
        )
        return self._format(result)

    @Tool(
        name="endpoint_status",
        description="Get status of a specific endpoint by job ID.",
        parameters={
            "job_id": {
                "type": "string",
                "description": "Job ID of the endpoint",
            },
        },
    )
    async def endpoint_status(self, job_id: str) -> ToolResult:
        result = await self._request("GET", f"/gw/endpoints/{job_id}")
        return self._format(result)

    @Tool(
        name="endpoint_provision",
        description="Provision a new inference endpoint for a model.",
        parameters={
            "model": {
                "type": "string",
                "description": "Model name to provision (e.g. 'Qwen/Qwen2.5-7B-Instruct')",
            },
            "gpu_count": {
                "type": "integer",
                "description": "Number of GPUs (default: 1)",
                "required": False,
                "default": 1,
            },
            "ttl_sec": {
                "type": "integer",
                "description": "Time-to-live in seconds (default: 3600)",
                "required": False,
            },
        },
    )
    async def endpoint_provision(
        self,
        model: str,
        gpu_count: int = 1,
        ttl_sec: Optional[int] = None,
    ) -> ToolResult:
        payload: dict[str, Any] = {"model": model, "gpu_count": gpu_count}
        if ttl_sec is not None:
            payload["ttl_sec"] = ttl_sec
        result = await self._request("POST", "/gw/endpoints/provision", json_body=payload)
        return self._format(result)

    @Tool(
        name="endpoint_stop",
        description="Deprovision (stop) an inference endpoint.",
        parameters={
            "job_id": {
                "type": "string",
                "description": "Job ID of the endpoint to stop",
            },
            "force": {
                "type": "boolean",
                "description": "Force cleanup even if in use",
                "required": False,
                "default": False,
            },
        },
    )
    async def endpoint_stop(self, job_id: str, force: bool = False) -> ToolResult:
        result = await self._request(
            "DELETE",
            f"/gw/endpoints/{job_id}",
            params={"force": force},
        )
        return self._format(result)

    # ── Workflows (no manifest row yet) ──────────────────────────────
    # job_*, workflow_list/describe/plan/run/status/results/cancel and rag_*
    # are generated from the manifest (ADR-105 D-2, S5).

    @Tool(
        name="workflow_trigger",
        description=WORKFLOW_TOOL_HELP["run"],
        parameters={
            "workflow_name": {
                "type": "string",
                "description": "Name of the workflow to trigger",
            },
            "parameters": {
                "type": "object",
                "description": "Workflow parameters as key-value pairs",
                "required": False,
            },
            "input_files": {
                "type": "array",
                "description": "List of input file paths",
                "required": False,
            },
        },
    )
    async def workflow_trigger(
        self,
        workflow_name: str,
        parameters: Optional[dict[str, Any]] = None,
        input_files: Optional[list[str]] = None,
    ) -> ToolResult:
        """Alias of the generated ``workflow_run`` (same guard, same call)."""
        arguments: dict[str, Any] = {"workflow_name": workflow_name, "parameters": parameters or {}}
        if input_files is not None:
            arguments["input_files"] = input_files
        return await self._tools["workflow_run"].handler(**arguments)

    @Tool(
        name="workflow_wait",
        description=WORKFLOW_TOOL_HELP["wait"],
        parameters={
            "execution_id": {"type": "string"},
            "timeout_sec": {"type": "integer", "required": False},
        },
    )
    async def workflow_wait(
        self,
        execution_id: str,
        timeout_sec: int = 300,
    ) -> ToolResult:
        denied = self._workflow_policy_guard("wait")
        if denied:
            return denied
        import asyncio

        deadline = timeout_sec
        while deadline > 0:
            result = await self._request("GET", f"/gw/workflows/executions/{execution_id}")
            if result["status"] >= 400:
                return self._format(result)
            body = result["body"]
            execution = body.get("execution", {}) if isinstance(body, dict) else {}
            status = str(execution.get("status", ""))
            if status in {"succeeded", "failed", "cancelled"}:
                return self._format(result)
            await asyncio.sleep(1.0)
            deadline -= 1
        return ToolResult.error(f"Timed out waiting for execution {execution_id}")

    @Tool(
        name="workflow_upload",
        description=WORKFLOW_TOOL_HELP["upload"],
        parameters={
            "workflow_name": {"type": "string"},
            "archive_path": {"type": "string"},
        },
    )
    async def workflow_upload(self, workflow_name: str, archive_path: str) -> ToolResult:
        denied = self._workflow_policy_guard("upload")
        if denied:
            return denied
        from pathlib import Path

        path = Path(archive_path).expanduser()
        if not path.is_file():
            return ToolResult.error(f"archive not found: {archive_path}")

        url = f"{self._gateway_url}/gw/workflows/upload"
        headers = self._build_headers()
        async with self._client() as client:
            with path.open("rb") as archive:
                response = await client.post(
                    url,
                    headers=headers,
                    data={"workflow_name": workflow_name},
                    files={
                        "file": (
                            f"{workflow_name}.zip",
                            archive,
                            "application/zip",
                        )
                    },
                )
        text = json.dumps(_response_body(response), indent=2, default=str)
        if response.status_code >= 400:
            return ToolResult.error(f"HTTP {response.status_code}: {text}")
        return ToolResult.success(text)

    # ── Agent discovery ──────────────────────────────────────────────

    @Tool(
        name="platform_guide",
        description=PLATFORM_GUIDE_TOOL_DESCRIPTION,
        parameters={
            "topic": {
                "type": "string",
                "description": "Playbook section to return",
                "enum": list(PLATFORM_GUIDE_TOPICS),
                "required": False,
            },
        },
    )
    async def platform_guide_tool(self, topic: str = "all") -> ToolResult:
        try:
            return ToolResult.success(platform_guide(topic))
        except ValueError as exc:
            return ToolResult.error(str(exc))

    # ── Models (no manifest row yet) ─────────────────────────────────

    @Tool(
        name="model_list",
        description="List models visible to the current user.",
        parameters={
            "scope": {
                "type": "string",
                "description": "Filter by scope (system, project, user)",
                "required": False,
                "enum": ["system", "project", "user"],
            },
        },
    )
    async def model_list(self, scope: Optional[str] = None) -> ToolResult:
        result = await self._request(
            "GET",
            "/gw/models",
            params={"scope": scope},
        )
        return self._format(result)

    @Tool(
        name="model_get",
        description="Get detailed information about a specific model.",
        parameters={
            "model_name": {
                "type": "string",
                "description": "Model name (e.g. 'Qwen/Qwen2.5-7B-Instruct')",
            },
        },
    )
    async def model_get(self, model_name: str) -> ToolResult:
        result = await self._request("GET", f"/gw/models/{model_name}")
        return self._format(result)

    # ── Health (no manifest row yet) ─────────────────────────────────

    @Tool(
        name="platform_health",
        description="Check platform health including Gateway and UMRS status.",
    )
    async def platform_health(self) -> ToolResult:
        result = await self._request("GET", "/gw/endpoints/health")
        return self._format(result)
