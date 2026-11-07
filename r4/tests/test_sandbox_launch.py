"""ADR-101 D-9: the MCP / hatch launcher (``python -m r4.companion.sandbox.launch``)."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import r4
from r4.companion.sandbox import (
    UserSandboxPolicy,
    mcp_launcher_prefix,
    mcp_policy_digest,
    write_mcp_policy,
)
from r4.companion.sandbox.launch import fixed_web_access, parse_args, server_entry
from r4.companion.sandbox.mcp_policy import LAUNCH_BOOTSTRAP


def _bwrap_works() -> bool:
    exe = shutil.which("bwrap")
    if not exe or not sys.platform.startswith("linux"):
        return False
    argv = [exe, "--ro-bind", "/", "/", "--unshare-user", "--unshare-net", "true"]
    try:
        return subprocess.run(argv, capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_BWRAP_OK = _bwrap_works()
needs_bwrap = pytest.mark.skipif(
    not _BWRAP_OK, reason="bubblewrap with user namespaces unavailable"
)


def _env() -> dict[str, str]:
    """The child finds this checkout's r4 whatever its cwd."""
    src = str(Path(r4.__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [src, env.get("PYTHONPATH", "")]))
    env.pop("R4_SANDBOXED", None)
    return env


def _mcp_config(tmp_path: Path, monkeypatch, servers: dict) -> None:
    (tmp_path / "r4").mkdir(exist_ok=True)
    (tmp_path / "r4" / "mcp-servers.json").write_text(json.dumps({"mcpServers": servers}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("R4_MCP_CONFIG", raising=False)


def test_the_test_policy_file_has_the_shape_production_writes(tmp_path, monkeypatch):
    """Review Q-4: one builder for the daemon's, the hatch runtimes' and this file."""
    from r4.companion import mcp_exposure
    from r4.companion.sandbox.mcp_policy import launcher_policy_document

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "r4" / "mcp-sandbox-policy.json"
    digest = write_mcp_policy(path, enabled=True)
    doc = json.loads(path.read_text())
    assert doc == launcher_policy_document(UserSandboxPolicy())
    daemon_doc = mcp_exposure.mcp_policy_document(tmp_path / "absent.json")
    assert set(daemon_doc) == set(doc) and daemon_doc["digest"] == digest
    assert digest != launcher_policy_document(UserSandboxPolicy(), enabled=False)["digest"]


def test_prefix_and_policy_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "r4" / "mcp-sandbox-policy.json"
    digest = write_mcp_policy(path, enabled=True)
    doc = json.loads(path.read_text())
    assert doc["enabled"] is True and doc["digest"] == digest
    assert mcp_policy_digest(True, UserSandboxPolicy())  # the policy part of it
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    prefix = mcp_launcher_prefix(path)
    assert prefix == [sys.executable, "-c", LAUNCH_BOOTSTRAP, "--policy", str(path)]
    assert "r4.companion.sandbox.launch" in LAUNCH_BOOTSTRAP and "-m" not in prefix


def test_launcher_never_imports_from_its_current_directory(tmp_path, monkeypatch):
    """SEC-4: the launcher runs unconfined in a writable workspace; planted modules stay dead."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    policy = tmp_path / "policy.json"
    write_mcp_policy(policy, enabled=False)
    planted = tmp_path / "planted"
    (planted / "r4").mkdir(parents=True)
    marker = tmp_path / "PLANT-RAN"
    payload = f"open({str(marker)!r}, 'a').write(__name__ + '\\n')\n"
    for name in ("runpy.py", "argparse.py", "json.py", "r4/__init__.py"):
        (planted / name).write_text(payload)
    argv = [*mcp_launcher_prefix(policy), "--", "sh", "-c", "echo ran"]
    result = subprocess.run(
        argv, cwd=planted, capture_output=True, text=True, timeout=60, env=_env()
    )
    assert result.stdout.strip() == "ran", result.stderr
    assert not marker.exists()


def test_parse_args_splits_at_double_dash():
    args, command = parse_args(
        ["--policy", "p", "--name", "s", "--write", "/a", "--", "srv", "--x"]
    )
    assert (args.policy, args.name, args.write, command) == ("p", "s", ["/a"], ["srv", "--x"])
    with pytest.raises(SystemExit):
        parse_args(["--policy", "p"])


def test_entry_settings_and_flags_win(tmp_path, monkeypatch):
    entry = {"command": "x", "sandbox": {"web": "allowlist", "domains": ["a.org"], "write": ["/w"]}}
    _mcp_config(tmp_path, monkeypatch, {"srv": entry})
    found = server_entry("srv")
    assert found["sandbox"]["web"] == "allowlist" and server_entry("missing") == {}
    args, _ = parse_args(["--policy", "p", "--", "x"])
    access = fixed_web_access(args, found["sandbox"])
    assert (access.mode, access.domains) == ("allowlist", ("a.org",))
    args, _ = parse_args(["--policy", "p", "--web", "none", "--", "x"])
    assert fixed_web_access(args, found["sandbox"]).mode == "none"


def test_disabled_policy_execs_the_command_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    policy = tmp_path / "policy.json"
    write_mcp_policy(policy, enabled=False)
    argv = [*mcp_launcher_prefix(policy), "--", "sh", "-c", 'echo "S=${R4_SANDBOXED:-none}"']
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=_env())
    assert result.stdout.strip() == "S=none"


@needs_bwrap
def test_exempt_server_runs_unconfined(tmp_path, monkeypatch):
    _mcp_config(tmp_path, monkeypatch, {"free": {"command": "sh", "sandbox": False}})
    policy = tmp_path / "policy.json"
    write_mcp_policy(policy, enabled=True)
    argv = [
        *mcp_launcher_prefix(policy),
        "--name",
        "free",
        "--",
        "sh",
        "-c",
        'echo "S=${R4_SANDBOXED:-none}"',
    ]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=_env())
    assert result.stdout.strip() == "S=none"


@needs_bwrap
def test_launch_passes_stdio_through_under_the_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    policy = tmp_path / "policy.json"
    write_mcp_policy(policy, enabled=True)
    script = 'read line; echo "echo:$line S=$R4_SANDBOXED"'
    argv = [*mcp_launcher_prefix(policy), "--name", "srv", "--", "sh", "-c", script]
    result = subprocess.run(
        argv, input="ping\n", capture_output=True, text=True, timeout=60, env=_env()
    )
    assert result.stdout.strip() == "echo:ping S=bwrap", result.stderr


def _procs_with(token: str) -> list[int]:
    """Pids whose command line carries ``token`` (scans /proc)."""
    found = []
    for entry in Path("/proc").iterdir():
        try:
            if entry.name.isdigit() and token in (entry / "cmdline").read_text(errors="replace"):
                found.append(int(entry.name))
        except OSError:
            continue
    return found


@needs_bwrap
def test_sigkill_of_the_launcher_takes_the_sandboxed_server_with_it(tmp_path, monkeypatch):
    """COR-4: the MCP daemon's kill() is a SIGKILL of the launcher pid only."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    policy = tmp_path / "policy.json"
    write_mcp_policy(policy, enabled=True)
    token = f"{os.getpid() % 1000}.{time.monotonic_ns() % 100000}"
    argv = [*mcp_launcher_prefix(policy), "--", "sh", "-c", f"echo up; exec sleep 6{token}"]
    env = dict(_env(), TMPDIR=str(tmp_path))  # the orphaned session dir stays in tmp_path
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, env=env)
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "up"
        assert _procs_with(f"sleep\x006{token}")
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=5)
        deadline = time.monotonic() + 5
        while _procs_with(f"sleep\x006{token}") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _procs_with(f"sleep\x006{token}") == []
    finally:
        for pid in _procs_with(f"sleep\x006{token}"):
            os.kill(pid, signal.SIGKILL)


@needs_bwrap
def test_launch_host_network_keeps_fs_confinement(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "r4").mkdir()
    (tmp_path / "r4" / "secret.txt").write_text("SECRET")
    policy = tmp_path / "policy.json"
    write_mcp_policy(policy, enabled=True)
    script = f"cat {tmp_path}/r4/secret.txt; ls /proc/net >/dev/null && echo ran"
    argv = [*mcp_launcher_prefix(policy), "--network", "host", "--", "sh", "-c", script]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=_env())
    assert "SECRET" not in result.stdout and "ran" in result.stdout
