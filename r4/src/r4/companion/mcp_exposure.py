"""Bridge-side auto-exposure for user-config MCP servers (2026-10-01).

The /mcp pane toggle previously only flipped the ``enabled`` flag and then
told the user to go run two shell commands (start the remote-proxy, register
the server). This module does both from the toggle:

1. ``ensure_proxy_running`` — probe 127.0.0.1:<port> for an answering
   remote-proxy; when absent, spawn ``r4-mcp remote-proxy`` detached against
   the user's config (survives the bridge process; logs under
   ``~/.config/r4/logs/remote-proxy.log``, pid in ``remote-proxy.pid``).
2. ``register_for_agent`` — ``client.mcp.register_server`` with the public
   URL of the bridged name. The URL is resolved by the UMRS server, so it
   must be reachable from it: default ``http://<fqdn>:<port>/<name>`` (works
   inside the site LAN), overridable via ``R4_REMOTE_PROXY_PUBLIC_URL``
   (e.g. ``http://localhost:50800`` when the bridge runs on the UMRS host).

MCP sandbox (ADR-101 D-9, strictest wins, no registry): the daemon is one per
user and outlives sessions, so its stdio children follow a per-user desired
state, never one session's toggle or one profile's view. It is on when ANY
stored profile has ``sandbox_default: on`` or when a session called
:func:`require_mcp_sandbox` (a ratchet file ``mcp-sandbox.json`` in the r4
config directory, holding a snapshot of the user policy). The launcher policy
is the strictest combination of those profiles' policies and the snapshot
(extra writes and ``env_keep``: the common entries; extra denied reads: all),
so every session computes the same digest whatever its ``R4_PROFILE``. A
session turning its sandbox off does not weaken it; only
:func:`release_mcp_sandbox_default` (``/sandbox default off``, ``r4 config
sandbox off``) clears the ratchet, and MCP goes off once no profile default
is on either.

The policy also carries what the user-level ``mcp-servers.json`` (the file
the daemon serves) says per server: the names in a stdio entry's ``env`` are
kept through the launcher's credential scrub (the remote-proxy hands each
wrapped child only its own declared names), and the exempt servers
(``"sandbox": false``) are part of the digest, so an edit to either restarts
the daemon instead of leaving its report wrong. The daemon is started without
``$R4_MCP_CONFIG``: the launcher then reads the same file the daemon serves.

When on, the daemon is started with ``--stdio-wrap <launcher prefix>`` and
``--sandbox-digest``; a running daemon whose reported digest differs is
restarted, but only when it is identified as the r4-mcp remote-proxy this
module started (``GET /`` identity, the pid file and the process's command
line all agree; a daemon from before ADR-101, which reports no identity, is
recognised by its command line, owner and port). An unidentified listener on
the port is never killed: the result says so, and while the sandbox is
wanted no server is registered through it. Starting and stopping the daemon
is serialised by a lock file, and the pid file is written only once the new
daemon answers with its own pid.

Nothing here raises to the RPC layer: every failure is returned as a dict
with a ``reason`` so the pane notice can name the exact blocker (registry
outage, spawn failure, …) instead of pretending success.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from r4._atomic import atomic_write_text
from r4.config import _resolve_config_dir

if TYPE_CHECKING:
    from r4.companion.sandbox import UserSandboxPolicy

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 50800
#: What the r4-mcp remote-proxy names itself in ``GET /`` (``SERVICE_NAME`` there).
_SERVICE = "r4-mcp-remote-proxy"
_DAEMON_MODULE = "r4_mcp.runners.remote_proxy"
_RATCHET_FILE = "mcp-sandbox.json"
_POLICY_FILE = "mcp-sandbox-policy.json"
_HATCH_POLICY_FILE = "hatch-sandbox-policy.json"
_LOCK_FILE = "remote-proxy.lock"
#: A hatch runtime bound beyond loopback needs its bearer token inside the sandbox.
_HATCH_TOKEN_ENV = "R4_HATCH_RUNTIME_TOKEN"
_STOP_WAIT = 5.0
_START_WAIT = 8.0


def _port() -> int:
    try:
        return int(os.environ.get("R4_REMOTE_PROXY_PORT", "") or _DEFAULT_PORT)
    except ValueError:
        return _DEFAULT_PORT


def _proxy_base_url() -> str:
    """Public base URL the UMRS server uses to reach the proxy."""
    override = os.environ.get("R4_REMOTE_PROXY_PUBLIC_URL", "").strip()
    if override:
        return override.rstrip("/")
    import socket

    return f"http://{socket.getfqdn()}:{_port()}"


def _slice_url(name: str) -> str:
    return f"{_proxy_base_url()}/{name}"


def _http_probe_ok(url: str, timeout: float = 2.0) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:  # noqa: BLE001 — probe failure == not running
        return False


def _proxy_identity(timeout: float = 2.0) -> dict[str, Any]:
    """The daemon's ``GET /`` JSON; {} when absent, unreadable or not an object."""
    import urllib.request

    try:
        with urllib.request.urlopen(_proxy_health_url(), timeout=timeout) as resp:
            body = json.loads(resp.read() or b"{}")
    except Exception:  # noqa: BLE001 — an unknown answer identifies nothing
        return {}
    return body if isinstance(body, dict) else {}


def _proxy_health_url() -> str:
    return f"http://127.0.0.1:{_port()}/"


def _log_path() -> Path:
    config_dir = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    log_dir = Path(config_dir) / "r4" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "remote-proxy.log"


def _pid_path() -> Path:
    return _log_path().with_name("remote-proxy.pid")


# ── desired MCP sandbox state (ADR-101 D-9) ────────────────────────────────


@dataclass(frozen=True)
class McpSandbox:
    """The daemon flags of one desired state; ``digest == ""`` means off."""

    digest: str = ""
    wrap: tuple[str, ...] = ()

    def flags(self) -> list[str]:
        if not self.digest:
            return []
        return ["--stdio-wrap", json.dumps(list(self.wrap)), "--sandbox-digest", self.digest]


def _ratchet_path() -> Path:
    return _resolve_config_dir() / _RATCHET_FILE


def _policy_path() -> Path:
    return _resolve_config_dir() / _POLICY_FILE


def _ratchet() -> dict[str, Any] | None:
    """The ratchet document while a session's ``require`` stands; else None."""
    try:
        data = json.loads(_ratchet_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("required") is True else None


def _on_profiles() -> list[str]:
    """Every stored profile whose ``sandbox_default`` is on (any one turns MCP on)."""
    from r4.companion.sandbox import profile_sandbox_default
    from r4.config import ProfileConfigStore

    try:
        names = list(ProfileConfigStore().list_profiles())
        return [name for name in names if profile_sandbox_default(name) == "on"]
    except Exception:  # noqa: BLE001 — an unreadable profile store is no default
        return []


def mcp_sandbox_desired() -> bool:
    """Strictest wins: some profile default is on, or a session required it."""
    return _ratchet() is not None or bool(_on_profiles())


def _common(policies: list[UserSandboxPolicy], attr: str) -> tuple[str, ...]:
    first, rest = getattr(policies[0], attr), policies[1:]
    return tuple(item for item in first if all(item in getattr(p, attr) for p in rest))


def strictest_policy(policies: list[UserSandboxPolicy]) -> UserSandboxPolicy:
    """Common extra writes and ``env_keep``; every extra denied read."""
    from r4.companion.sandbox import UserSandboxPolicy

    if not policies:
        return UserSandboxPolicy()
    deny = dict.fromkeys(path for p in policies for path in p.extra_deny_read_paths)
    return UserSandboxPolicy(
        _common(policies, "extra_write_paths"), tuple(deny), _common(policies, "env_keep")
    )


def per_user_policy() -> UserSandboxPolicy:
    """The MCP/hatch user policy: the ratchet's snapshot and every on-profile's, strictest."""
    from r4.companion.sandbox import UserSandboxPolicy, user_sandbox_policy

    policies = [user_sandbox_policy(name) for name in _on_profiles()]
    ratchet = _ratchet()
    if ratchet is not None:
        policies.insert(0, UserSandboxPolicy.from_mapping(ratchet.get("user_policy")))
    return strictest_policy(policies)


def _mcp_entries(config: Path) -> dict[str, dict[str, Any]]:
    """The enabled stdio entries of the file the daemon serves."""
    from r4.tui.mcp_config import _load_json_file

    servers = _load_json_file(config).get("mcpServers") if config.is_file() else None
    if not isinstance(servers, dict):
        return {}
    return {
        str(name): entry
        for name, entry in servers.items()
        if isinstance(entry, dict)
        and entry.get("type") == "stdio"
        and entry.get("enabled", True) is not False
    }


def _exempt_of(entries: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(name for name, entry in entries.items() if entry.get("sandbox") is False)


def exempt_servers(config: Path | None = None) -> list[str]:
    """Stdio servers the user-level file exempts (``"sandbox": false``)."""
    return _exempt_of(_mcp_entries(config or _user_config_file()))


def _declared_env(entries: dict[str, dict[str, Any]], exempt: list[str]) -> list[str]:
    names = {
        str(key)
        for name, entry in entries.items()
        if name not in exempt and isinstance(entry.get("env"), dict)
        for key in entry["env"]
    }
    return sorted(names)


def mcp_policy_document(config: Path) -> dict[str, Any]:
    """The launcher policy for the daemon serving ``config``, with its digest."""
    from r4.companion.sandbox.mcp_policy import launcher_policy_document

    entries = _mcp_entries(config)
    exempt = _exempt_of(entries)
    policy = per_user_policy()
    keep = dict.fromkeys((*policy.env_keep, *_declared_env(entries, exempt)))
    return launcher_policy_document(replace(policy, env_keep=tuple(keep)), exempt=exempt)


def desired_mcp_sandbox(config: Path | None = None) -> McpSandbox:
    """The flags to start the daemon with; writes the launcher policy file when on.

    Raises ``OSError`` when the policy file cannot be written (never starts
    a daemon that claims a sandbox it cannot configure).
    """
    if not mcp_sandbox_desired():
        return McpSandbox()
    from r4.companion.sandbox import mcp_launcher_prefix
    from r4.companion.sandbox.mcp_policy import write_policy_document

    document = mcp_policy_document(config or _user_config_file())
    path = _policy_path()
    write_policy_document(path, document)
    return McpSandbox(document["digest"], tuple(mcp_launcher_prefix(path)))


def write_hatch_policy() -> Path:
    """The hatch runtimes' launcher policy: the per-user one, plus the runtime token kept.

    Without it the env scrub (``*TOKEN*``) would drop
    ``R4_HATCH_RUNTIME_TOKEN`` and a runtime bound beyond loopback would
    serve its tools without a bearer check.
    """
    from r4.companion.sandbox.mcp_policy import launcher_policy_document, write_policy_document

    user = per_user_policy()
    keep = tuple(dict.fromkeys((*user.env_keep, _HATCH_TOKEN_ENV)))
    path = _resolve_config_dir() / _HATCH_POLICY_FILE
    write_policy_document(path, launcher_policy_document(replace(user, env_keep=keep)))
    return path


def hatch_runtime_prefix(package_dir: Path) -> list[str]:
    """Launcher prefix for a hatch runtime (host network, package dir readable); [] when off."""
    if not mcp_sandbox_desired():
        return []
    from r4.companion.sandbox import mcp_launcher_prefix

    prefix = mcp_launcher_prefix(write_hatch_policy())
    return [*prefix, "--network", "host", "--read-extra", str(package_dir), "--"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_mcp_sandbox(profile: str | None = None) -> list[str]:
    """A session turned its sandbox on: MCP sandboxing stays on from now on.

    Snapshots ``profile``'s user policy (the active one by default) into the
    ratchet, combined strictest with an earlier snapshot. Returns the stdio
    servers of a running daemon that was restarted to pick the sandbox up
    ([] when no daemon runs, it already ran sandboxed, or it could not be
    restarted; the reason is logged and :func:`mcp_sandbox_status` shows the
    daemon out of sync). Hatch runtimes started before stay unconfined until
    reconnected; the status lists them. Raises ``OSError`` when the state
    file cannot be written.
    """
    from r4.companion.sandbox import UserSandboxPolicy, user_sandbox_policy

    policies = [user_sandbox_policy(profile)]
    earlier = _ratchet()
    if earlier is not None:
        policies.insert(0, UserSandboxPolicy.from_mapping(earlier.get("user_policy")))
    snapshot = strictest_policy(policies).to_mapping()
    document = {"required": True, "set_at": _now(), "user_policy": snapshot}
    atomic_write_text(_ratchet_path(), json.dumps(document), mode=0o600)
    return _restart_if_running()


def release_mcp_sandbox_default() -> list[str]:
    """``/sandbox default off``: the ratchet cleared, after the caller wrote the default.

    The caller (the bridge, ``r4 config sandbox off``) writes the profile's
    ``sandbox_default: off`` first; this no longer writes it a second time,
    for a profile it would have to guess (2026-10-30, review Q-3). MCP
    sandboxing goes off only when no stored profile has its default on
    (strictest wins). Restarts the daemon when the digest changes.
    """
    try:
        _ratchet_path().unlink()
    except FileNotFoundError:
        pass
    return _restart_if_running()


def _restart_if_running() -> list[str]:
    if not _http_probe_ok(_proxy_health_url()):
        return []
    result = ensure_proxy_running()
    if result.get("reason"):
        logger.warning("MCP sandbox: %s", result["reason"])
    return list(result.get("restarted") or [])


def _servers(identity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    servers = identity.get("servers")
    if not isinstance(servers, dict):
        return {}
    return {str(name): info for name, info in servers.items() if isinstance(info, dict)}


def _desired_digest(desired: bool) -> str:
    """The digest a daemon started now would report, without writing anything."""
    if not desired:
        return ""
    try:
        return str(mcp_policy_document(_user_config_file())["digest"])
    except Exception:  # noqa: BLE001 — status must not raise
        return ""


def _unsandboxed_hatch_runtimes() -> list[str]:
    try:
        from r4.integrations.hatchling import unsandboxed_hatch_runtimes

        return unsandboxed_hatch_runtimes()
    except Exception:  # noqa: BLE001 — status must not raise
        return []


def mcp_sandbox_status() -> dict[str, Any]:
    """``{desired, running_digest, sandboxed_servers, exempt_servers}`` (+ ``in_sync``).

    ``running_digest`` is None when no daemon answers and "" for a daemon
    that runs without a sandbox (or predates the digest). The server lists
    are the daemon's own report (a startup snapshot), so they are empty when
    none runs. ``unsandboxed_hatch_runtimes`` names live hatch runtimes
    started without the launcher (before the MCP sandbox went on).
    """
    desired = mcp_sandbox_desired()
    running = _http_probe_ok(_proxy_health_url())
    identity = _proxy_identity() if running else {}
    running_digest = str(identity.get("sandbox_digest") or "") if running else None
    digest = _desired_digest(desired)
    return {
        "desired": desired,
        "desired_digest": digest,
        "running_digest": running_digest,
        **_server_lists(identity),
        "in_sync": running_digest is None or running_digest == digest,
        "unsandboxed_hatch_runtimes": _unsandboxed_hatch_runtimes() if desired else [],
    }


def _server_lists(identity: dict[str, Any]) -> dict[str, list[str]]:
    servers = _servers(identity)
    return {
        "sandboxed_servers": sorted(n for n, info in servers.items() if info.get("sandboxed")),
        "exempt_servers": sorted(n for n, info in servers.items() if info.get("exempt")),
    }


def mcp_sandbox_labels() -> dict[str, Any]:
    """What the /mcp rows may claim: ``{state, exempt}``.

    ``state`` is ``"off"``, ``"sandboxed"`` (wanted, and the running daemon,
    if any, runs the current digest) or ``"pending"`` (wanted, but the
    running daemon does not run it yet). ``exempt`` lists the servers the
    file the daemon serves exempts.
    """
    if not mcp_sandbox_desired():
        return {"state": "off", "exempt": exempt_servers()}
    status = mcp_sandbox_status()
    state = "sandboxed" if status["in_sync"] and status["desired_digest"] else "pending"
    return {"state": state, "exempt": exempt_servers()}


# ── the daemon ──────────────────────────────────────────────────────────────


def _open_lock() -> Any:
    try:
        import fcntl

        handle = _pid_path().with_name(_LOCK_FILE).open("a")
    except (ImportError, OSError):
        return None
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
    except OSError:
        handle.close()
        return None
    return handle


@contextmanager
def _daemon_lock() -> Iterator[None]:
    """One session at a time starts or stops the daemon (``flock``; none where unsupported)."""
    handle = _open_lock()
    try:
        yield
    finally:
        if handle is not None:
            handle.close()  # closing the descriptor releases the lock


def ensure_proxy_running(config_path: Path | None = None) -> dict[str, Any]:
    """Ensure a remote-proxy answers on the local port, under the desired MCP sandbox."""
    port = _port()
    config = config_path or _user_config_file()
    with _daemon_lock():
        try:
            sandbox = desired_mcp_sandbox(config)
        except OSError as exc:
            return {"state": "failed", "reason": f"cannot write the MCP sandbox policy: {exc}"}
        if _http_probe_ok(_proxy_health_url()):
            return _reconcile_running(port, sandbox, config)
        return _start_daemon(port, sandbox, config)


def _daemon_env() -> dict[str, str]:
    """The daemon serves the user-level file; the launcher must read that one too."""
    env = dict(os.environ)
    env.pop("R4_MCP_CONFIG", None)
    return env


def _start_daemon(port: int, sandbox: McpSandbox, config: Path) -> dict[str, Any]:
    if not config.is_file():
        return {"state": "failed", "reason": f"no mcp config at {config}"}
    command = _spawn_command(config, port, sandbox)
    log = _log_path()
    try:
        with log.open("ab") as fh:
            proc = subprocess.Popen(  # noqa: S603 — r4-mcp from the active env, config is theirs
                command,
                stdout=fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                env=_daemon_env(),
            )
    except OSError as exc:
        return {"state": "failed", "reason": f"spawn failed: {exc}", "command": command}
    return _await_daemon(proc, port, sandbox, config)


def _await_daemon(proc: Any, port: int, sandbox: McpSandbox, config: Path) -> dict[str, Any]:
    """Record the pid only once the port answers with it; another answer is reconciled."""
    log = str(_log_path())
    deadline = time.monotonic() + _START_WAIT
    while time.monotonic() < deadline:
        identity = _proxy_identity(timeout=1.0)
        if identity and _as_pid(identity.get("pid")) == proc.pid:
            _write_pid_file(proc.pid, port, config)
            return {"state": "started", "port": port, "log": log}
        if identity and _legacy_start(identity, proc, sandbox):
            return {"state": "started", "port": port, "log": log}
        if identity and proc.poll() is not None:  # another daemon holds the port
            return _another_daemon(identity, port, sandbox)
        time.sleep(0.3)
    return {"state": "failed", "reason": "proxy did not answer within 8s", "log": log}


def _legacy_start(identity: dict[str, Any], proc: Any, sandbox: McpSandbox) -> bool:
    """An r4-mcp from before ADR-101 we just started (no ``pid`` in ``GET /``).

    Accepted only with the sandbox off (nothing to confirm) and while our
    process still runs; no pid file is written, as the answer may not be
    ours (review OFF-2).
    """
    if sandbox.digest or "pid" in identity or proc.poll() is not None:
        return False
    return "service" not in identity


def _another_daemon(identity: dict[str, Any], port: int, sandbox: McpSandbox) -> dict[str, Any]:
    """Ours lost the port: report the winner as it is, never restart it from here."""
    if str(identity.get("sandbox_digest") or "") == sandbox.digest:
        return {"state": "running", "port": port}
    reason = f"another process took port {port} first; its sandbox state was not changed"
    return _stale(port, sandbox, reason)


def _stale(port: int, sandbox: McpSandbox, reason: str) -> dict[str, Any]:
    return {
        "state": "running",
        "port": port,
        "sandbox": "stale",
        "want_sandbox": bool(sandbox.digest),
        "reason": reason,
    }


def _reconcile_running(port: int, sandbox: McpSandbox, config: Path) -> dict[str, Any]:
    identity = _proxy_identity()
    if str(identity.get("sandbox_digest") or "") == sandbox.digest:
        return {"state": "running", "port": port}
    pid = _owned_daemon_pid(identity, port)
    if not pid:
        reason = (
            f"the process on port {port} is not an r4-mcp remote-proxy this r4 started, "
            "so it was not restarted; its MCP servers keep their old sandbox state "
            f"(stop it and toggle the server again in /mcp; want sandbox {sandbox.digest or 'off'})"
        )
        return _stale(port, sandbox, reason)
    if not _stop_daemon(pid):
        reason = f"the r4-mcp remote-proxy (pid {pid}) did not stop; its sandbox state is stale"
        return _stale(port, sandbox, reason)
    result = _start_daemon(port, sandbox, config)
    if result.get("state") == "started":
        result["restarted"] = _stdio_names(identity)
    return result


def _stdio_names(identity: dict[str, Any]) -> list[str]:
    return sorted(n for n, info in _servers(identity).items() if info.get("kind") == "stdio")


def _write_pid_file(pid: int, port: int, config: Path) -> None:
    document = {"pid": pid, "port": port, "config": str(config)}
    try:
        atomic_write_text(_pid_path(), json.dumps(document), mode=0o600)
    except OSError as exc:  # the daemon runs; it just cannot be restarted later
        logger.warning("cannot record the remote-proxy pid: %s", exc)


def _read_pid_file() -> dict[str, Any]:
    try:
        data = json.loads(_pid_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_pid(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _command_line(pid: int) -> str:
    """The process's command line ("" when unknown): /proc, else ``ps``."""
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
    except OSError:
        pass
    try:
        out = subprocess.run(  # noqa: S603 — fixed argv, numeric pid
            ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def _owned_daemon_pid(identity: dict[str, Any], port: int) -> int:
    """The daemon's pid when identity, pid file and command line agree; else 0."""
    if identity.get("service") != _SERVICE:
        return _legacy_daemon_pid(identity, port)
    pid = _as_pid(identity.get("pid"))
    if pid <= 1 or pid == os.getpid() or _as_pid(_read_pid_file().get("pid")) != pid:
        return 0
    return pid if _DAEMON_MODULE in _command_line(pid) else 0


def _proc_pids() -> list[int]:
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:  # no procfs (macOS): a legacy daemon stays unidentified
        return []
    return [int(entry.name) for entry in entries if entry.name.isdigit()]


def _proc_argv(pid: int) -> list[str]:
    """``pid``'s argv when the process is ours (same uid); [] otherwise."""
    proc = Path(f"/proc/{pid}")
    try:
        if proc.stat().st_uid != os.getuid():
            return []
        return proc.joinpath("cmdline").read_bytes().decode(errors="replace").split("\0")
    except OSError:
        return []


def _is_legacy_daemon(argv: list[str], port: int) -> bool:
    """``python -m r4_mcp.runners.remote_proxy ... --port <port>``, without a wrap."""
    if _DAEMON_MODULE not in argv or "--stdio-wrap" in argv or "--port" not in argv:
        return False
    at = argv.index("--port")
    return at + 1 < len(argv) and argv[at + 1] == str(port)


def _legacy_daemon_pid(identity: dict[str, Any], port: int) -> int:
    """A pre-ADR-101 daemon this module started (``{status, upstreams}``, no identity).

    Found under /proc: owned by this user, our module, this port. Exactly
    one match or nothing.
    """
    if "service" in identity or not isinstance(identity.get("upstreams"), list):
        return 0
    found = [
        pid
        for pid in _proc_pids()
        if pid > 1 and pid != os.getpid() and _is_legacy_daemon(_proc_argv(pid), port)
    ]
    return found[0] if len(found) == 1 else 0


def _signal_daemon(pid: int, signum: int) -> bool:
    """Signal the daemon's process group (its stdio children with it); False on failure."""
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signum)
        else:
            os.kill(pid, signum)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return True


def _port_freed(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _http_probe_ok(_proxy_health_url(), timeout=0.5):
            return True
        time.sleep(0.1)
    return False


def _stop_daemon(pid: int) -> bool:
    """SIGTERM, then SIGKILL; True once the port no longer answers."""
    if not _signal_daemon(pid, signal.SIGTERM):
        return False
    if _port_freed(_STOP_WAIT):
        return True
    return _signal_daemon(pid, signal.SIGKILL) and _port_freed(_STOP_WAIT)


def _user_config_file() -> Path:
    from r4.tui.mcp_config import _user_config_path

    return _user_config_path()


def _spawn_command(config: Path, port: int, sandbox: McpSandbox | None = None) -> list[str]:
    return [
        sys.executable,
        "-m",
        _DAEMON_MODULE,
        "--config",
        str(config),
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        *(sandbox.flags() if sandbox else []),
    ]


def register_for_agent(client: Any, name: str, description: str = "") -> dict[str, Any]:
    """Register one bridged name in the UMRS registry (explicit, per toggle)."""
    try:
        result = client.mcp.register_server(
            name=name,
            url=_slice_url(name),
            description=description or f"local MCP {name} via r4-mcp remote-proxy",
        )
    except Exception as exc:  # noqa: BLE001 — surface the exact blocker
        return {"state": "blocked", "reason": str(exc), "url": _slice_url(name)}
    return {"state": "registered", "server_id": (result or {}).get("server_id")}


def _exposure_blocker(proxy: dict[str, Any]) -> str:
    """Why a server must not be registered through this proxy ("" when it may)."""
    if proxy.get("state") == "failed":
        return "no proxy"
    if proxy.get("sandbox") == "stale" and proxy.get("want_sandbox"):
        reason = proxy.get("reason", "")
        return f"the MCP sandbox is on but the running remote-proxy is not under it: {reason}"
    return ""


def expose_local_mcp(client: Any, name: str, description: str = "") -> dict[str, Any]:
    """Proxy ensured + name registered; every failure mode is named.

    Fails closed: while the MCP sandbox is wanted, a proxy that could not be
    brought under it is never handed to the agent.
    """
    proxy = ensure_proxy_running()
    blocker = _exposure_blocker(proxy)
    if blocker:
        return {"proxy": proxy, "registration": {"state": "blocked", "reason": blocker}}
    registration = register_for_agent(client, name, description)
    return {"proxy": proxy, "registration": registration}
