"""ADR-101 D-9: local MCP servers and hatch runtimes under the sandbox.

Covers the r4 side outside ``mcp_exposure`` (see ``test_mcp_exposure.py``):
the hatch runtime prefix, the per-entry ``sandbox`` key and where it is
honoured, the /mcp row status, and a real bubblewrap round trip through the
remote-proxy's stdio spawn path.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import r4
from r4.companion import mcp_exposure
from r4.companion.mcp_user_config import user_defined_mcp_rows
from r4.companion.sandbox import (
    mcp_launcher_prefix,
    remember_sandbox_default,
    scrub_env,
    write_mcp_policy,
)
from r4.companion.sandbox.policy import UserSandboxPolicy
from r4.integrations import hatchling
from r4.tui.mcp_config import load_mcp_configs

# ── hatch runtimes ──────────────────────────────────────────────────────────


class _Proc:
    pid = 31337
    returncode = None


def _launch(monkeypatch, tmp_path) -> dict:
    """Run ``_launch_runtime`` with Popen and the health probes faked; the Popen call."""
    seen: dict = {}

    def fake_popen(command, **kwargs):
        seen.update(command=list(command), **kwargs)
        return _Proc()

    async def _noop(*_a, **_k):
        return []

    monkeypatch.setattr(hatchling.subprocess, "Popen", fake_popen)
    config = SimpleNamespace(gateway_url="http://gw.example", api_key="gw-key", verify_ssl=False)
    bridge = hatchling.HatchlingBridge(SimpleNamespace(config=config), packages_dir=tmp_path / "pk")
    monkeypatch.setattr(bridge, "_wait_for_runtime_health", _noop)
    monkeypatch.setattr(bridge, "_fetch_runtime_tools", _noop)
    entry = tmp_path / "pk" / "demo" / "server.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("")
    seen["result"] = asyncio.run(bridge._launch_runtime("demo", entry))
    seen["package_dir"] = tmp_path / "pk" / "demo"
    return seen


def test_hatch_runtime_unwrapped_when_mcp_sandbox_off(monkeypatch, tmp_path):
    seen = _launch(monkeypatch, tmp_path)
    assert seen["command"][:2] == [
        sys.executable,
        str(Path(hatchling.__file__).with_name("hatch_runtime.py")),
    ]
    assert seen["cwd"] is None
    assert seen["env"]["R4_GATEWAY_URL"] == "http://gw.example"


def test_hatch_runtime_prefixed_with_launcher_when_on(monkeypatch, tmp_path):
    remember_sandbox_default("on")
    seen = _launch(monkeypatch, tmp_path)
    policy = mcp_exposure._resolve_config_dir() / "hatch-sandbox-policy.json"
    prefix = [*mcp_launcher_prefix(policy), "--network", "host"]
    prefix += ["--read-extra", str(seen["package_dir"]), "--"]
    command = seen["command"]
    assert command[: len(prefix)] == prefix
    assert command[len(prefix) :][1].endswith("hatch_runtime.py")
    assert seen["cwd"] == "/"
    assert (seen["env"]["R4_GATEWAY_URL"], seen["env"]["R4_GATEWAY_TOKEN"]) == (
        "http://gw.example",
        "gw-key",
    )


def test_hatch_runtime_state_records_whether_it_was_sandboxed(monkeypatch, tmp_path):
    from r4.integrations.hatchling import unsandboxed_hatch_runtimes

    assert _launch(monkeypatch, tmp_path / "off")["result"]["sandboxed"] is False
    remember_sandbox_default("on")
    assert _launch(monkeypatch, tmp_path / "on")["result"]["sandboxed"] is True
    assert unsandboxed_hatch_runtimes(tmp_path / "nowhere") == []


def test_hatch_policy_keeps_the_runtime_token_through_the_scrub(monkeypatch):
    path = mcp_exposure.write_hatch_policy()
    document = json.loads(path.read_text())
    assert document["enabled"] is True
    keep = UserSandboxPolicy.from_mapping(document["user_policy"]).env_keep
    env = {"R4_HATCH_RUNTIME_TOKEN": "t", "OPENAI_API_KEY": "k", "R4_GATEWAY_TOKEN": "g"}
    assert scrub_env(env, keep=keep) == {"R4_HATCH_RUNTIME_TOKEN": "t", "R4_GATEWAY_TOKEN": "g"}
    assert oct(path.stat().st_mode & 0o777) == "0o600"


# ── the per-entry ``sandbox`` key ──────────────────────────────────────────


def _write(path: Path, servers: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}))
    return path


def _user_file() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"]) / "r4" / "mcp-servers.json"


def test_project_level_sandbox_false_is_ignored(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("R4_MCP_CONFIG", raising=False)
    entry = {"type": "stdio", "command": "srv", "sandbox": False}
    _write(_user_file(), {"mine": entry, "shadowed": entry})
    _write(tmp_path / ".r4" / "mcp.json", {"repo": entry, "shadowed": entry})
    by_name = {cfg.name: cfg for cfg in load_mcp_configs()}
    assert by_name["mine"].sandbox_exempt is True
    assert by_name["repo"].sandbox is True  # a cloned repo cannot exempt a server
    assert by_name["shadowed"].sandbox_exempt is False  # nor un-sandbox the user's entry


def test_env_config_sandbox_false_and_settings_are_honoured(monkeypatch, tmp_path):
    settings = {"web": "allowlist", "domains": ["a.org"], "write": ["/w"], "junk": 1}
    env_file = _write(
        tmp_path / "env.json",
        {
            "free": {"type": "stdio", "command": "srv", "sandbox": False},
            "boxed": {"type": "stdio", "command": "srv", "sandbox": settings},
        },
    )
    monkeypatch.setenv("R4_MCP_CONFIG", str(env_file))
    monkeypatch.chdir(tmp_path)
    by_name = {cfg.name: cfg for cfg in load_mcp_configs()}
    assert by_name["free"].sandbox_exempt is True
    assert by_name["boxed"].sandbox == {"web": "allowlist", "domains": ["a.org"], "write": ["/w"]}


# ── /mcp row status ─────────────────────────────────────────────────────────


def _rows(monkeypatch, tmp_path, identity=None) -> dict:
    """Row statuses; ``identity`` is the running daemon's ``GET /`` (None: no daemon)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("R4_MCP_CONFIG", raising=False)
    monkeypatch.setattr(mcp_exposure, "_http_probe_ok", lambda *a, **k: identity is not None)
    monkeypatch.setattr(mcp_exposure, "_proxy_identity", lambda *a, **k: identity or {})
    _write(
        _user_file(),
        {
            "fetch": {"type": "stdio", "command": "uvx"},
            "free": {"type": "stdio", "command": "uvx", "sandbox": False},
            "ctx": {"type": "http", "url": "https://ctx.example/mcp"},
        },
    )
    return {row["name"]: row["status"] for row in user_defined_mcp_rows()}


def test_row_status_says_pending_while_the_running_daemon_is_unsandboxed(monkeypatch, tmp_path):
    remember_sandbox_default("on")
    status = _rows(monkeypatch, tmp_path, identity={"status": "ok", "upstreams": ["fetch"]})
    assert status["fetch"] == (
        "declared stdio (child on demand) · sandbox pending restart · not agent-visible"
    )
    assert "exempt from sandbox" in status["free"]


def test_row_status_sandboxed_when_the_daemon_runs_the_current_digest(monkeypatch, tmp_path):
    remember_sandbox_default("on")
    _rows(monkeypatch, tmp_path)  # writes the user file
    digest = mcp_exposure.mcp_policy_document(_user_file())["digest"]
    status = _rows(monkeypatch, tmp_path, identity={"sandbox_digest": digest})
    assert "· sandboxed ·" in status["fetch"]


def test_project_entry_shadowing_an_exempt_user_entry_still_shows_exempt(monkeypatch, tmp_path):
    """The daemon serves the user file, so the user's exemption is what runs."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("R4_MCP_CONFIG", raising=False)
    monkeypatch.setattr(mcp_exposure, "_http_probe_ok", lambda *a, **k: False)
    remember_sandbox_default("on")
    _write(_user_file(), {"s": {"type": "stdio", "command": "x", "sandbox": False}})
    _write(tmp_path / ".r4" / "mcp.json", {"s": {"type": "stdio", "command": "y"}})
    (row,) = user_defined_mcp_rows()
    assert "exempt from sandbox" in row["status"] and "· sandboxed" not in row["status"]


def test_row_status_unchanged_while_mcp_sandbox_off(monkeypatch, tmp_path):
    status = _rows(monkeypatch, tmp_path)
    assert status["fetch"] == "declared stdio (child on demand) · not agent-visible"
    assert status["free"] == (
        "declared stdio (child on demand) · exempt from sandbox · not agent-visible"
    )
    assert status["ctx"] == "declared (not probed) · not agent-visible"


def test_row_status_names_sandboxed_and_exempt(monkeypatch, tmp_path):
    remember_sandbox_default("on")
    status = _rows(monkeypatch, tmp_path)
    assert status["fetch"] == "declared stdio (child on demand) · sandboxed · not agent-visible"
    assert "exempt from sandbox" in status["free"] and "· sandboxed" not in status["free"]
    assert status["ctx"] == "declared (not probed) · not agent-visible"


# ── real bubblewrap through the remote-proxy spawn path ────────────────────


def _bwrap_works() -> bool:
    exe = shutil.which("bwrap")
    if not exe or not sys.platform.startswith("linux"):
        return False
    argv = [exe, "--ro-bind", "/", "/", "--unshare-user", "--unshare-net", "true"]
    try:
        return subprocess.run(argv, capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


needs_bwrap = pytest.mark.skipif(
    not _bwrap_works(), reason="bubblewrap with user namespaces unavailable"
)

# An MCP-like stdio child: NDJSON JSON-RPC; tools/list reports what it can read.
_CHILD = """\
import json, os, sys
home = os.path.expanduser("~")
def can(path):
    try:
        with open(path) as fh:
            return bool(fh.read())
    except OSError:
        return False
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    if msg["method"] == "tools/list":
        report = {"ssh": can(home + "/.ssh/id_x"), "visible": can(home + "/visible.txt"),
                  "sandboxed": os.environ.get("R4_SANDBOXED", "")}
        result = {"tools": [{"name": "probe", "description": json.dumps(report)}]}
    elif msg["method"] == "tools/call":
        result = {"content": [{"type": "text", "text": json.dumps(msg["params"])}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
"""


@pytest.fixture
def home_outside_tmp(monkeypatch):
    """A scratch HOME outside /tmp: under /tmp the sandbox's own /tmp would hide it anyway."""
    if not os.access("/var/tmp", os.W_OK):
        pytest.skip("/var/tmp not writable")
    base = Path(tempfile.mkdtemp(prefix="r4-sbx-mcp-", dir="/var/tmp"))
    home = base / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_x").write_text("PRIVATE-KEY")
    (home / "visible.txt").write_text("hello")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "xdg"))
    yield home
    shutil.rmtree(base, ignore_errors=True)


def _absolute_pythonpath() -> str:
    """The launcher drops relative ``sys.path`` entries: hand it absolute ones."""
    import r4_mcp

    parts = [str(Path(r4.__file__).resolve().parents[1])]
    parts.append(str(Path(r4_mcp.__file__).resolve().parents[1]))
    parts += [
        str(Path(p).resolve()) for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p
    ]
    return os.pathsep.join(dict.fromkeys(parts))


@needs_bwrap
def test_stdio_child_through_remote_proxy_runs_sandboxed(home_outside_tmp, monkeypatch, tmp_path):
    remote_proxy = pytest.importorskip("r4_mcp.runners.remote_proxy")
    monkeypatch.setenv("PYTHONPATH", _absolute_pythonpath())
    monkeypatch.delenv("R4_SANDBOXED", raising=False)
    policy = Path(os.environ["XDG_CONFIG_HOME"]) / "r4" / "mcp-sandbox-policy.json"
    write_mcp_policy(policy, enabled=True)
    spec = remote_proxy.UpstreamSpec(
        name="probe", command=[sys.executable, "-c", _CHILD], timeout=60.0
    )
    client = remote_proxy.StdioUpstreamClient(spec, wrap=mcp_launcher_prefix(policy))
    try:
        (tool,) = client.list_tools()
        echoed = client.call_tool("probe", {"q": "ping"})
    finally:
        client.close()
    report = json.loads(tool["description"])
    assert report == {"ssh": False, "visible": True, "sandboxed": "bwrap"}
    assert json.loads(echoed["content"][0]["text"]) == {"name": "probe", "arguments": {"q": "ping"}}


# A stdio child whose tools/list reports the credential variables it was given.
_ENV_CHILD = """\
import json, os, sys
names = ("GITHUB_PERSONAL_ACCESS_TOKEN", "BRAVE_API_KEY", "OPENAI_API_KEY", "PLAIN")
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    seen = {name: os.environ.get(name, "<missing>") for name in names}
    result = {"tools": [{"name": "env", "description": json.dumps(seen)}]}
    if msg["method"] != "tools/list":
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
"""


@needs_bwrap
def test_wrapped_child_keeps_only_its_own_declared_credentials(home_outside_tmp, monkeypatch):
    """The entry's ``env`` survives the launcher's scrub; other entries' names do not leak."""
    remote_proxy = pytest.importorskip("r4_mcp.runners.remote_proxy")
    monkeypatch.setenv("PYTHONPATH", _absolute_pythonpath())
    monkeypatch.delenv("R4_SANDBOXED", raising=False)
    monkeypatch.setenv("BRAVE_API_KEY", "from-the-daemon-env")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-key")
    remember_sandbox_default("on")
    child = {"type": "stdio", "command": sys.executable, "args": ["-c", _ENV_CHILD]}
    config = _write(
        _user_file(),
        {
            "github": {**child, "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x", "PLAIN": "p"}},
            "brave": {**child, "env": {"BRAVE_API_KEY": "bk"}},
        },
    )
    sandbox = mcp_exposure.desired_mcp_sandbox(config)
    specs = remote_proxy.load_upstream_specs(str(config), timeout=60.0)
    state = remote_proxy._ProxyState(specs, wrap=sandbox.wrap, sandbox_digest=sandbox.digest)
    try:
        github = json.loads(state.clients["github"].list_tools()[0]["description"])
        brave = json.loads(state.clients["brave"].list_tools()[0]["description"])
    finally:
        state.close()
    assert github == {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x",
        "BRAVE_API_KEY": "<missing>",  # brave's name, not github's
        "OPENAI_API_KEY": "<missing>",
        "PLAIN": "p",
    }
    assert (brave["BRAVE_API_KEY"], brave["GITHUB_PERSONAL_ACCESS_TOKEN"]) == ("bk", "<missing>")


# A hatch package whose one tool reports what the runtime process can see.
_HATCH_SERVER = """\
import os

def _can(path):
    try:
        with open(path) as fh:
            return bool(fh.read())
    except OSError:
        return False

class _Server:
    def list_tools(self):
        home = os.path.expanduser("~")
        report = f"ssh={_can(home + '/.ssh/id_x')} sbx={os.environ.get('R4_SANDBOXED', '')}"
        report += f" gw={os.environ.get('R4_GATEWAY_TOKEN', '')}"
        return [{"name": "probe", "description": report, "inputSchema": {"type": "object"}}]

    def invoke(self, name, arguments):
        return {"ok": True}

def create_server():
    return _Server()
"""


@needs_bwrap
def test_hatch_runtime_runs_sandboxed_on_host_network(home_outside_tmp, monkeypatch):
    """The package dir sits in the denied r4 config dir; --read-extra makes it readable."""
    import signal

    monkeypatch.setenv("PYTHONPATH", _absolute_pythonpath())
    monkeypatch.delenv("R4_SANDBOXED", raising=False)
    monkeypatch.delenv("R4_HATCH_BIND_HOST", raising=False)
    remember_sandbox_default("on")
    packages = Path(os.environ["XDG_CONFIG_HOME"]) / "r4" / "hatch_packages"
    entry = packages / "probe" / "server.py"
    entry.parent.mkdir(parents=True)
    entry.write_text(_HATCH_SERVER)
    config = SimpleNamespace(gateway_url="http://gw.example", api_key="gw-key", verify_ssl=False)
    bridge = hatchling.HatchlingBridge(SimpleNamespace(config=config), packages_dir=packages)
    runtime = asyncio.run(bridge._launch_runtime("probe", entry))
    try:
        (tool,) = runtime["tools"]
    finally:
        os.killpg(runtime["pid"], signal.SIGTERM)
    assert tool["description"] == "ssh=False sbx=bwrap gw=gw-key"


def test_no_test_reaches_the_host_mcp_daemon() -> None:
    # Review Q-1: the contract case session.sandbox_set[on] probed the real
    # 127.0.0.1:50800 and could SIGTERM a developer's own (older) daemon.
    from conftest import DEAD_PROXY_URL

    assert mcp_exposure._proxy_health_url() == DEAD_PROXY_URL
    assert mcp_exposure._proc_pids() == []
    assert mcp_exposure._http_probe_ok(mcp_exposure._proxy_health_url(), timeout=0.5) is False
