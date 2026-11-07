"""Docker-backed E2E coverage for mounted custom MCP servers."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

httpx = pytest.importorskip("httpx")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.e2e.yaml"
DEFAULT_SERVICE_URL = "http://localhost:8085"

# Infrastructure guard (2026-09-14). These tests bring the docker-compose.e2e
# stack up and poll it. Having podman/docker on PATH is NOT enough — the stack
# also has to be built and running, and without it the fixtures error out (5
# errors measured on a host that has podman). Opt-in only, which is also how
# the compose-e2e CI job is gated; the presence check stays as a second, more
# specific failure message for someone who opted in without a runtime.
if os.environ.get("R4_E2E_COMPOSE", "") not in ("1", "true", "yes"):
    pytest.skip(
        "compose-backed E2E: set R4_E2E_COMPOSE=1 (and bring up docker-compose.e2e.yaml) to run it",
        allow_module_level=True,
    )
if not (shutil.which("podman") or shutil.which("docker")):
    pytest.skip(
        "R4_E2E_COMPOSE is set but neither podman nor docker is on PATH",
        allow_module_level=True,
    )


def _command_available(command: list[str]) -> bool:
    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _container_runtime() -> str | None:
    requested = os.getenv("R4_CONTAINER_RUNTIME") or os.getenv("CONTAINER_RUNTIME")
    if requested:
        return requested if _command_available([requested, "info"]) else None

    for runtime in ("podman", "docker"):
        if _command_available([runtime, "info"]):
            return runtime
    return None


def _compose_cmd(runtime: str) -> list[str] | None:
    override = os.getenv("R4_COMPOSE_CMD") or os.getenv("COMPOSE_CMD")
    if override:
        command = shlex.split(override)
        return command if _command_available([*command, "version"]) else None

    candidates = (
        [["podman-compose"], ["podman", "compose"]]
        if runtime == "podman"
        else [["docker", "compose"], ["docker-compose"]]
    )
    for command in candidates:
        if _command_available([*command, "version"]):
            return command
    return None


@pytest.fixture(scope="module")
def custom_mcp_service() -> str:
    service_url = os.getenv("CUSTOM_MCP_E2E_URL", DEFAULT_SERVICE_URL)
    if os.getenv("CUSTOM_MCP_E2E_URL"):
        _wait_for_service(service_url)
        yield service_url
        return

    runtime = _container_runtime()
    if runtime is None:
        pytest.skip("No healthy Podman or Docker runtime is available for custom MCP E2E tests")
    compose_cmd = _compose_cmd(runtime)
    if compose_cmd is None:
        pytest.skip(f"No compose frontend is available for {runtime}")

    subprocess.run(
        [*compose_cmd, "-f", str(COMPOSE_FILE), "up", "-d", "--build", "custom-mcp"],
        cwd=PROJECT_ROOT,
        check=True,
    )
    try:
        _wait_for_service(service_url)
        yield service_url
    finally:
        subprocess.run(
            [*compose_cmd, "-f", str(COMPOSE_FILE), "rm", "-sf", "custom-mcp"],
            cwd=PROJECT_ROOT,
            check=False,
        )


def _wait_for_service(service_url: str) -> None:
    deadline = time.monotonic() + 90
    last_error = "service did not respond"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{service_url}/health", timeout=2)
            if response.status_code == 200 and response.json().get("status") == "ok":
                return
            last_error = response.text
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(1)
    pytest.fail(f"custom MCP service did not become healthy: {last_error}")


def _rpc(service_url: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = httpx.post(
        f"{service_url}/message?session_id=e2e",
        json={"jsonrpc": "2.0", "id": method, "method": method, "params": params or {}},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert "result" in payload
    return payload["result"]


def _call_tool(
    service_url: str, name: str, arguments: dict[str, Any] | None = None, **meta
) -> dict[str, Any]:
    params: dict[str, Any] = {"name": name, "arguments": arguments or {}}
    if meta:
        params["_meta"] = meta
    return _rpc(service_url, "tools/call", params)


def _text(result: dict[str, Any]) -> str:
    return result["content"][0]["text"]


def _assert_sandbox_network_env(env: dict[str, Any]) -> None:
    """Assert the proxy/egress/sandbox-policy env vars seen inside the container."""
    assert env["http_proxy"] == "http://gateway:8080"
    assert env["egress_required"] == "1"
    assert env["egress_domains"] == "api.example.com"
    assert env["sandbox_filesystem"] == "read_only"
    assert env["sandbox_network"] == "egress_guard"


def _assert_sandbox_limit_env(env: dict[str, Any]) -> None:
    """Assert the resource-limit and offline env vars seen inside the container."""
    assert env["max_memory_mb"] == "512"
    assert env["max_runtime_sec"] == "2"
    assert env["transformers_offline"] == "1"
    assert env["hf_hub_offline"] == "1"


@pytest.mark.integration
@pytest.mark.e2e
def test_custom_mcp_container_lists_declared_tools(custom_mcp_service: str):
    result = _rpc(custom_mcp_service, "tools/list")

    tool_names = {tool["name"] for tool in result["tools"]}
    assert tool_names == {
        "echo_text",
        "fetch_json",
        "chain_payload",
        "sleep_forever",
        "write_mount",
    }


@pytest.mark.integration
@pytest.mark.e2e
def test_custom_mcp_container_invokes_tool_with_sandbox_env(custom_mcp_service: str):
    result = _call_tool(custom_mcp_service, "echo_text", {"text": "hello"})
    payload = json.loads(_text(result))

    assert result["isError"] is False
    assert payload["text"] == "hello"
    _assert_sandbox_network_env(payload["env"])
    _assert_sandbox_limit_env(payload["env"])


@pytest.mark.integration
@pytest.mark.e2e
def test_custom_mcp_container_denies_undeclared_and_unauthorized_tools(custom_mcp_service: str):
    undeclared = _call_tool(custom_mcp_service, "delete_all")
    unauthorized = _call_tool(custom_mcp_service, "fetch_json", {"url": "https://api.example.com"})

    assert undeclared["isError"] is True
    assert "not declared" in _text(undeclared)
    assert unauthorized["isError"] is True
    assert "Capability denied" in _text(unauthorized)


@pytest.mark.integration
@pytest.mark.e2e
def test_custom_mcp_container_blocks_chain_scanner_output(custom_mcp_service: str):
    result = _call_tool(
        custom_mcp_service,
        "chain_payload",
        chain_id="e2e-chain",
        target_tool="run_command",
    )

    assert result["isError"] is True
    assert "ChainScanner" in _text(result)


@pytest.mark.integration
@pytest.mark.e2e
def test_custom_mcp_container_enforces_timeout_and_read_only_mount(custom_mcp_service: str):
    timed_out = _call_tool(custom_mcp_service, "sleep_forever")
    write_result = _call_tool(custom_mcp_service, "write_mount")
    write_payload = json.loads(_text(write_result))

    assert timed_out["isError"] is True
    assert "timed out after 2s" in _text(timed_out)
    assert write_result["isError"] is False
    assert write_payload["write_ok"] is False
