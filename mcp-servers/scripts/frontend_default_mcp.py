#!/usr/bin/env python3
"""Bring up the default Hatch! MCP server set on the frontend host.

Launches the default packages from ``mcp-servers/hatch-registry/packages/``
via the stdlib Hatch runtime bridge and registers them (upsert) with every
local UMRS server so they appear in ``/gw/mcp/servers`` and are invocable
through ``/gw/mcp/invoke``.

Designed to run ON the frontend host (umrs-front) where UMRS servers run
with ``--network=host`` and can reach the loopback runtime URLs. Requires
``MCP_URL_ALLOWLIST=127.0.0.1/32`` in the UMRS environment (set by the
Ansible umrs-front profile).

Stdlib-only. Idempotent: healthy runtimes are reused, registration is
an upsert.

Usage::

    python3 mcp-servers/scripts/frontend_default_mcp.py \
        --repo-root <checkout> \
        --gateway-ports <port>,<port>,...

Registration goes through each cluster gateway (loopback) with
``Bearer $R4_GATEWAY_TOKEN``: UMRS accepts registry writes only with a
gateway-verified caller identity (2026-09-25 — direct POSTs to the UMRS
ports answer 401, and the launcher had been failing silently since July).

Exit codes: 0 all launched and registered; 1 a runtime failed to start;
3 runtimes up but registration incomplete (no token, or a gateway refused)
— the systemd unit treats 3 as success (SuccessExitStatus) so healthy
runtimes are not killed, and `systemctl --user status` shows status=3.

Environment:
    R4_GATEWAY_URL    Gateway base URL passed to tool runtimes
                      (default: http://127.0.0.1:50080)
    UMRS_MCP_REGISTER_TOKEN  Admin-role service token for registration only
                      (converge-minted on the host; never passed to runtimes)
    R4_GATEWAY_TOKEN  Token the tools use for their own Gateway calls (and the
                      registration fallback); owner-provisioned in
                      ~/.config/umrs/default-mcp.env
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_PACKAGES = {
    "platform-tools": 8171,
    "rag-search": 8172,
    "knowledge-tools": 8173,
    "rikyu-hpc": 8174,
    "rikyu-docs": 8175,
    "fugaku": 8176,
}

# Per-package default env for the spawned runtimes. Values from the calling
# environment always win (Ansible injects real paths/secrets via .env).
PACKAGE_DEFAULT_ENV: dict[str, dict[str, str]] = {
    "rikyu-hpc": {
        "RIKYU_HOST": "login.rikyu.r-ccs.riken.jp",
        "RIKYU_TOOL_DENY": "run_command_on_cluster",
    },
    "rikyu-docs": {"RIKYU_HOST": "login.rikyu.r-ccs.riken.jp"},
    "fugaku": {"FUGAKU_TOOL_DENY": "run_command,check_update"},
}

# Tools removed from the surface registered with UMRS (still callable
# directly against the runtime for debugging). Conservative defaults chosen
# by the maintainers (ADR-023 hardening contract).
PACKAGE_TOOL_DENY: dict[str, set[str]] = {
    "rikyu-hpc": {"run_command_on_cluster"},
    "fugaku": {"run_command", "check_update"},
}

STATE_DIR = Path.home() / ".local" / "state" / "r4-default-mcp"
# Admin-role service token used ONLY to register the platform-wide default set
# (non-admin registrations are bound to the caller's project). Minted on the
# host by the converge; stripped from every runtime's environment.
REGISTER_TOKEN_ENV = "UMRS_MCP_REGISTER_TOKEN"
STATE_FILE = STATE_DIR / "state.json"


def _http_json(
    url: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: int = 10,
    token: str = "",
) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _load_state() -> dict[str, Any]:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _runtime_healthy(port: int) -> bool:
    try:
        payload = _http_json(f"http://127.0.0.1:{port}/health", timeout=3)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict)


def _launch_runtime(
    repo_root: Path,
    package: str,
    port: int,
) -> int:
    """Launch a hatch runtime for a package; return the PID."""
    runtime_script = repo_root / "r4" / "src" / "r4" / "integrations" / "hatch_runtime.py"
    entry_point = repo_root / "mcp-servers" / "hatch-registry" / "packages" / package / "server.py"
    if not runtime_script.is_file():
        raise FileNotFoundError(f"hatch runtime not found: {runtime_script}")
    if not entry_point.is_file():
        raise FileNotFoundError(f"package entry point not found: {entry_point}")

    env = os.environ.copy()
    # The registration identity is admin-scoped: never hand it to tool code.
    env.pop(REGISTER_TOKEN_ENV, None)
    env.setdefault("R4_GATEWAY_URL", "http://127.0.0.1:50080")
    for key, value in PACKAGE_DEFAULT_ENV.get(package, {}).items():
        env.setdefault(key, value)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log_path = STATE_DIR / f"{package}.log"
    with open(log_path, "ab") as log_file:
        proc = subprocess.Popen(
            [
                sys.executable,
                str(runtime_script),
                "--entry-point",
                str(entry_point),
                "--package-name",
                package,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            env=env,
        )
    return proc.pid


def _wait_healthy(port: int, timeout_sec: int = 20) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if _runtime_healthy(port):
            return True
        time.sleep(0.5)
    return False


def _fetch_tools(port: int) -> list[dict[str, Any]]:
    try:
        payload = _http_json(f"http://127.0.0.1:{port}/tools", timeout=5)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        return [t for t in payload.get("tools", []) if isinstance(t, dict)]
    if isinstance(payload, list):
        return [t for t in payload if isinstance(t, dict)]
    return []


def _load_capabilities(repo_root: Path, package: str) -> dict[str, Any]:
    """Read the ``capabilities`` block from the package's hatch.json."""
    manifest = repo_root / "mcp-servers" / "hatch-registry" / "packages" / package / "hatch.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    caps = data.get("capabilities", {})
    return caps if isinstance(caps, dict) else {}


def _load_contract(repo_root: Path, package: str) -> dict:
    """Read the ``contract`` block (S8 schema pinning) from hatch.json."""
    manifest = repo_root / "mcp-servers" / "hatch-registry" / "packages" / package / "hatch.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    contract = data.get("contract", {})
    return contract if isinstance(contract, dict) else {}


def _register(
    gateway_port: int,
    package: str,
    runtime_port: int,
    tools: list,
    repo_root: Path,
    token: str,
) -> bool:
    """Upsert-register a runtime with one cluster's UMRS, via its gateway."""
    body = {
        "name": f"default-{package}",
        "url": f"http://127.0.0.1:{runtime_port}",
        "transport": "http",
        "description": f"Default platform MCP package: {package}",
        "auth_type": "none",
        "metadata": {
            "source": "default-set",
            "package": package,
            "capabilities": _load_capabilities(repo_root, package),
            "contract": _load_contract(repo_root, package),
        },
        "tools": tools,
    }
    try:
        _http_json(
            f"http://127.0.0.1:{gateway_port}/gw/mcp/servers",
            method="POST",
            body=body,
            timeout=10,
            token=token,
        )
        return True
    except (urllib.error.URLError, OSError) as exc:
        print(f"  ! register {package} via gateway :{gateway_port} failed: {exc}")
        return False


def _ensure_runtime(repo_root: Path, package: str, port: int, state: dict) -> bool:
    """Ensure a package runtime is running; update state. Returns health."""
    if _runtime_healthy(port):
        pid = state.get(package, {}).get("pid", "unknown")
        print(f"  = {package}: already running (pid={pid}, port={port})")
        return True
    pid = _launch_runtime(repo_root, package, port)
    if not _wait_healthy(port):
        print(f"  ! {package}: runtime failed to become healthy on port {port}")
        return False
    state[package] = {"pid": pid, "port": port}
    print(f"  + {package}: launched (pid={pid}, port={port})")
    return True


def _process_package(
    repo_root: Path, package: str, gateway_ports: list[int], state: dict, token: str
) -> tuple[bool, bool]:
    """Bring up one package and register it: (runtime healthy, fully registered)."""
    port = DEFAULT_PACKAGES.get(package)
    if port is None:
        print(f"  ! unknown default package: {package}")
        return False, False
    print(f"[{package}]")
    if not _ensure_runtime(repo_root, package, port, state):
        return False, False
    if not token:
        print(
            f"  ! not registered: neither {REGISTER_TOKEN_ENV} nor R4_GATEWAY_TOKEN is set "
            "(~/.config/umrs/default-mcp.env)"
        )
        return True, False
    deny = PACKAGE_TOOL_DENY.get(package, set())
    tools = [t for t in _fetch_tools(port) if t.get("name") not in deny]
    registered = 0
    for gateway_port in gateway_ports:
        if _register(gateway_port, package, port, tools, repo_root, token):
            registered += 1
            print(f"  ✓ registered via gateway :{gateway_port} ({len(tools)} tools)")
    return True, registered == len(gateway_ports)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="ai4s-platform checkout root (default: derived from script path)",
    )
    parser.add_argument(
        "--gateway-ports",
        default="50080,50082,50084,50086",
        help="Comma-separated loopback gateway ports (one per cluster UMRS) to register through",
    )
    parser.add_argument(
        "--umrs-ports",
        default="",
        help="Deprecated (direct UMRS registration answers 401); ignored",
    )
    parser.add_argument(
        "--packages",
        default=",".join(DEFAULT_PACKAGES),
        help="Comma-separated package names from the default set",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    gateway_ports = [int(p) for p in args.gateway_ports.split(",") if p.strip()]
    packages = [p.strip() for p in args.packages.split(",") if p.strip()]
    token = (
        os.environ.get(REGISTER_TOKEN_ENV, "").strip()
        or os.environ.get("R4_GATEWAY_TOKEN", "").strip()
    )

    state = _load_state()
    results = [_process_package(repo_root, p, gateway_ports, state, token) for p in packages]
    _save_state(state)
    return _exit_code(results)


def _exit_code(results: list[tuple[bool, bool]]) -> int:
    """1 = a runtime failed; 3 = runtimes up, registration incomplete; 0 = all good."""
    if not all(healthy for healthy, _ in results):
        return 1
    if not all(registered for _, registered in results):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
