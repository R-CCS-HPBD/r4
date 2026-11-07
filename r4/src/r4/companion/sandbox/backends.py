"""Sandbox backends: bubblewrap (Linux) and Seatbelt (macOS) (ADR-101 D-3).

A backend turns a command and a :class:`SandboxPolicy` into the argv that runs
it confined. Neither backend starts a new session, and bwrap ties the child to
its parent's life (``--die-with-parent``) only when the policy asks for it
(the MCP launcher): the spawn sites' ``start_new_session=True`` + ``killpg``
must keep reaching every process the command starts, and ``sandbox-exec``
execs in place anyway.

bwrap mount order (argv order is mount order; a later bind takes its source
from the host, so it would undo an earlier mask below it): ``/`` read-only,
``/dev``, ``/proc``, the per-session temp directory at ``/tmp`` and at its
real path (other sessions' directories hidden behind a tmpfs), the writable
roots, the rename guards (each directory between a root and a protected or
denied path, bound onto itself so a rename gives EBUSY), the write-protected
paths (read-only binds; a missing ``hooks`` directory of a git dir pinned as
an empty read-only tmpfs), the read-deny masks (directories: tmpfs; files: an
empty regular file, never ``/dev/null``, whose ``nodev`` bind gives EACCES),
re-exposed read paths, then the host sockets (``/run/user/$UID``, D-Bus,
container engines, Kerberos, smartcards, libvirt, screen; with an isolated
network also the name-service sockets, a DNS channel out of the namespace).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from . import _netns_init
from .policy import SandboxPolicy, realpaths, rename_guards, within, write_protection
from .proxy import INSIDE_PROXY_PORT
from .setting import REFUSED, UNAVAILABLE, SandboxError

#: The in-namespace proxy bridge, run by path as ``python -I <file> ...`` (stdlib only).
NETNS_INIT = os.path.realpath(_netns_init.__file__)
SANDBOX_EXEC = "/usr/bin/sandbox-exec"

INSTALL_FIX = "install bubblewrap (sudo apt install bubblewrap, or sudo dnf install bubblewrap)"
UBUNTU_FIX = (
    "on Ubuntu 24.04 and later allow bubblewrap user namespaces: sudo cp "
    "/usr/share/apparmor/extra-profiles/bwrap-userns-restrict /etc/apparmor.d/ && "
    "sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict"
)
USERNS_FIX = "enable unprivileged user namespaces (sysctl user.max_user_namespaces > 0)"
CONTAINER_HINT = (
    "r4 seems to run inside a container, which usually blocks user namespaces; "
    "run r4 on the host or allow user namespaces in the container"
)
_APPARMOR_USERNS = "/proc/sys/kernel/apparmor_restrict_unprivileged_userns"
#: Host sockets masked inside (D-5): D-Bus, container engines, Kerberos (KCM,
#: gssproxy), smartcards, libvirt, screen; plus the runtime dir.
SOCKET_MASK_PATHS: tuple[str, ...] = (
    "/run/dbus",
    "/var/run/dbus",
    "/run/docker.sock",
    "/var/run/docker.sock",
    "/run/docker",
    "/run/podman",
    "/run/containerd",
    "/var/run/containerd",
    "/run/crio",
    "/run/.heim_org.h5l.kcm-socket",
    "/run/gssproxy.sock",
    "/run/gssproxy.default.sock",
    "/run/pcscd",
    "/run/libvirt",
    "/var/run/libvirt",
    "/run/screen",
    "/var/run/screen",
)
#: Name-service sockets, masked when the network is isolated: a lookup through
#: them leaves the namespace (a DNS channel). The egress proxy resolves outside.
RESOLVER_SOCKET_PATHS: tuple[str, ...] = (
    "/run/systemd/resolve",
    "/run/nscd",
    "/var/run/nscd",
    "/run/avahi-daemon",
    "/var/run/avahi-daemon",
)
_NAMESPACES = (
    "--unshare-user",
    "--unshare-ipc",
    "--unshare-pid",
    "--unshare-uts",
    "--unshare-cgroup-try",
)
_DEVICE_WRITES = (
    '(literal "/dev/null")',
    '(literal "/dev/zero")',
    '(literal "/dev/tty")',
    '(regex #"^/dev/ttys[0-9]+$")',
    '(literal "/dev/dtracehelper")',
)

#: Successes only: a failure (a timeout under load, a sysctl the user then
#: fixes) is probed again on the next check (2026-10-30, review LC-4).
_PROBE_CACHE: dict[str, tuple[bool, str]] = {}
_USERNS_FLAG_CACHE: dict[str, bool] = {}


def exc_reason(exc: BaseException) -> str:
    """One short line for an exception; never a timed-out command's whole argv."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return f"timed out after {exc.timeout:g}s"
    lines = str(exc).splitlines()
    return (lines[0] if lines else type(exc).__name__)[:200]


def cached_probe(key: str, run: Callable[[], tuple[bool, str]]) -> tuple[bool, str]:
    """``run()`` once per ``key`` while it succeeds; a failure is not remembered."""
    hit = _PROBE_CACHE.get(key)
    if hit is not None:
        return hit
    result = run()
    if result[0]:
        _PROBE_CACHE[key] = result
    return result


class Backend(Protocol):
    """What :class:`SandboxContext` needs from a backend."""

    name: str

    def probe(self) -> tuple[bool, str]: ...

    def build_argv(
        self, command: Sequence[str], policy: SandboxPolicy, cwd: str | None
    ) -> list[str]: ...


def extra_write_roots(backend: object) -> tuple[str, ...]:
    """Host directories a backend leaves writable beyond the policy's roots.

    Seatbelt keeps the per-user temp directory writable (ADR-101 D-5); the
    self-test must not pick its "this write fails" target there.
    """
    roots = getattr(backend, "extra_write_roots", None)
    return tuple(roots()) if callable(roots) else ()


def _in_container() -> bool:
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def bwrap_failure_reason(stderr: str) -> str:
    """Why bwrap could not start, with the fix command when one is known."""
    detail = " ".join(stderr.split())[:200] or "no output"
    fixes = []
    if _read_text(_APPARMOR_USERNS) == "1":
        fixes.append(UBUNTU_FIX)
    if _in_container():
        fixes.append(CONTAINER_HINT)
    fixes = fixes or [USERNS_FIX]
    return f"bubblewrap cannot create a sandbox here ({detail}); " + "; ".join(fixes)


# ── bubblewrap ──────────────────────────────────────────────────────────────


def _hidden(path: str, policy: SandboxPolicy) -> bool:
    """True for a path the /tmp replacement already hides (nothing to mask there)."""
    if not within(path, "/tmp"):
        return False
    binds = (*policy.write_roots, policy.tmp_dir, *policy.read_paths)
    return not any(bind and within(path, bind) for bind in binds)


def _tmp_args(policy: SandboxPolicy) -> list[str]:
    if not policy.tmp_dir:
        return ["--tmpfs", "/tmp"]
    args = ["--bind", policy.tmp_dir, "/tmp"]
    if policy.base_dir:
        args += ["--tmpfs", policy.base_dir]
    args += ["--bind", policy.tmp_dir, policy.tmp_dir]
    if policy.proxy_socket:
        proxy_dir = os.path.dirname(policy.proxy_socket)
        args += ["--ro-bind", proxy_dir, proxy_dir]
    return args


def _write_args(policy: SandboxPolicy) -> list[str]:
    args: list[str] = []
    for root in policy.write_roots:
        if os.path.exists(root) and root != policy.tmp_dir:
            args += ["--bind", root, root]
    return args


def _mask(path: str, empty_file: str) -> list[str]:
    if os.path.isdir(path):
        return ["--tmpfs", path]
    if not empty_file:
        raise SandboxError(UNAVAILABLE, f"no empty file to mask {path}")
    return ["--ro-bind", empty_file, path]


def mask_targets(paths: Sequence[str], policy: SandboxPolicy) -> tuple[str, ...]:
    """The paths that need a mask: existing, real, de-duplicated, not already hidden."""
    found = realpaths(paths)
    return tuple(p for p in found if os.path.lexists(p) and not _hidden(p, policy))


def _ro_binds(paths: Sequence[str]) -> list[str]:
    args: list[str] = []
    for path in realpaths(paths):
        if os.path.exists(path):
            args += ["--ro-bind", path, path]
    return args


def _self_binds(paths: Sequence[str]) -> list[str]:
    """Rename guards: each directory bound onto itself (still writable, but a mount point)."""
    return [arg for path in paths for arg in ("--bind", path, path)]


def _pinned_empty_dirs(paths: Sequence[str]) -> list[str]:
    """An empty read-only directory at each (missing) path."""
    return [arg for path in paths for arg in ("--tmpfs", path, "--remount-ro", path)]


def socket_mask_paths(network: str = "isolated") -> list[str]:
    """``/run/user/$UID``, ``$XDG_RUNTIME_DIR``, host sockets; resolvers when isolated."""
    runtime = [f"/run/user/{os.getuid()}", os.environ.get("XDG_RUNTIME_DIR", "")]
    resolvers = RESOLVER_SOCKET_PATHS if network == "isolated" else ()
    return [path for path in (*runtime, *SOCKET_MASK_PATHS, *resolvers) if path]


def _netns_bridge_binds(policy: SandboxPolicy) -> list[str]:
    script = [NETNS_INIT] if policy.proxy_socket and _hidden(NETNS_INIT, policy) else []
    return _ro_binds(script)


class _BwrapPlan:
    """The mount part of one bwrap argv, plus what it masked (for ``--chdir``)."""

    def __init__(self, policy: SandboxPolicy) -> None:
        self.policy = policy
        self.masked: list[str] = []

    def _masks(self, paths: Sequence[str]) -> list[str]:
        args: list[str] = []
        for path in mask_targets(paths, self.policy):
            if path not in self.masked:
                self.masked.append(path)
                args += _mask(path, self.policy.empty_file)
        return args

    def mounts(self) -> list[str]:
        policy = self.policy
        deny = mask_targets(policy.deny_read, policy)
        protected, missing = write_protection(policy.write_roots)
        guards = rename_guards(policy.write_roots, (*deny, *protected, *missing))
        args = ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
        args += _tmp_args(policy) + _write_args(policy) + _self_binds(guards)
        args += _ro_binds(protected) + _pinned_empty_dirs(missing)
        args += self._masks(deny)
        args += _ro_binds(policy.read_paths) + _netns_bridge_binds(policy)
        return args + self._masks(socket_mask_paths(policy.network))

    def chdir(self, cwd: str | None) -> str:
        """The directory inside; an explicit ``cwd`` not visible there is refused.

        ``/tmp`` is the sandbox's own ``/tmp``. Without ``cwd``: the current
        directory when visible, else the first writable root.
        """
        if cwd is None:
            return self._default_dir()
        candidate = os.path.realpath(cwd)
        if candidate == "/tmp":
            return candidate
        if not os.path.isdir(candidate) or not self._visible(candidate):
            raise SandboxError(
                REFUSED, f"the working directory {cwd} is not visible in the sandbox"
            )
        return candidate

    def _default_dir(self) -> str:
        try:
            here = os.path.realpath(os.getcwd())
        except OSError:
            here = ""
        if here and self._visible(here):
            return here
        roots = [root for root in self.policy.write_roots if os.path.isdir(root)]
        return roots[0] if roots else "/"

    def _visible(self, path: str) -> bool:
        if _hidden(path, self.policy):
            return False
        return not any(within(path, masked) for masked in self.masked)


class BwrapBackend:
    """Linux: bubblewrap with unprivileged user namespaces."""

    name = "bwrap"

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("bwrap") or ""

    def probe(self) -> tuple[bool, str]:
        """Can bwrap make a user + network namespace here? Cached per executable."""
        if not self.executable:
            hint = f"; {CONTAINER_HINT}" if _in_container() else ""
            return False, f"bubblewrap (bwrap) is not installed; {INSTALL_FIX}{hint}"
        return cached_probe(self.executable, self._probe_once)

    def _probe_once(self) -> tuple[bool, str]:
        argv = [self.executable, "--ro-bind", "/", "/", "--unshare-user", "--unshare-net", "true"]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, bwrap_failure_reason(exc_reason(exc))
        return (True, "") if proc.returncode == 0 else (False, bwrap_failure_reason(proc.stderr))

    def supports_disable_userns(self) -> bool:
        """``--disable-userns`` exists from bwrap 0.8; probed once from ``--help``."""
        if self.executable not in _USERNS_FLAG_CACHE:
            try:
                out = subprocess.run(
                    [self.executable, "--help"], capture_output=True, text=True, timeout=10
                )
            except (OSError, subprocess.TimeoutExpired):
                return False  # not remembered: the next spawn asks again
            _USERNS_FLAG_CACHE[self.executable] = "--disable-userns" in out.stdout + out.stderr
        return _USERNS_FLAG_CACHE[self.executable]

    def namespace_args(self, policy: SandboxPolicy) -> list[str]:
        args = list(_NAMESPACES)
        if policy.network == "isolated":
            args.append("--unshare-net")
        args += ["--cap-drop", "ALL"]
        if self.supports_disable_userns():
            args.append("--disable-userns")
        if policy.die_with_parent:
            args.append("--die-with-parent")
        return args

    def build_argv(
        self, command: Sequence[str], policy: SandboxPolicy, cwd: str | None
    ) -> list[str]:
        if not self.executable:
            raise SandboxError(UNAVAILABLE, self.probe()[1])
        plan = _BwrapPlan(policy)
        mounts = plan.mounts()
        inner = list(command)
        if policy.network == "isolated" and policy.proxy_socket:
            port = str(INSIDE_PROXY_PORT)
            inner = [sys.executable, "-I", NETNS_INIT, policy.proxy_socket, port, "--", *inner]
        chdir = ["--chdir", plan.chdir(cwd)]
        return [self.executable, *mounts, *self.namespace_args(policy), *chdir, "--", *inner]


# ── Seatbelt ────────────────────────────────────────────────────────────────


def darwin_path(path: str) -> str:
    """Seatbelt matches real paths: ``/tmp``, ``/var``, ``/etc`` live under ``/private``."""
    for prefix in ("/tmp", "/var", "/etc"):
        if path == prefix or path.startswith(prefix + "/"):
            return "/private" + path
    return path


def darwin_user_temp() -> str:
    """The per-user temp directory (``/private/var/folders/.../T``)."""
    try:
        value = os.confstr("CS_DARWIN_USER_TEMP_DIR") or ""
    except (ValueError, OSError):
        value = ""
    return darwin_path(os.path.realpath(value or tempfile.gettempdir()))


class _Params:
    """``-D KEY=VALUE`` parameters; paths never appear in the profile text itself."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def add(self, prefix: str, paths: Sequence[str]) -> list[str]:
        keys = []
        for index, path in enumerate(p for p in paths if p):
            key = f"{prefix}_{index}"
            self.values[key] = darwin_path(path)
            keys.append(key)
        return keys


def _filters(keys: Sequence[str]) -> str:
    return " ".join(f'(subpath (param "{key}"))' for key in keys)


def _rule(action: str, keys: Sequence[str], extra: Sequence[str] = ()) -> list[str]:
    body = " ".join(filter(None, (_filters(keys), *extra)))
    return [f"({action} {body})"] if body else []


def _network_rules(policy: SandboxPolicy) -> list[str]:
    if policy.network != "isolated":
        return []
    rules = ["(deny network*)", '(allow network-bind network-inbound (local ip "localhost:*"))']
    if policy.proxy_port:
        rules.append(f'(allow network-outbound (remote ip "localhost:{int(policy.proxy_port)}"))')
    return rules


def _literal_rule(action: str, keys: Sequence[str]) -> list[str]:
    body = " ".join(f'(literal (param "{key}"))' for key in keys)
    return [f"({action} {body})"] if body else []


def seatbelt_profile(
    policy: SandboxPolicy,
    *,
    protected: Sequence[str] = (),
    user_temp: str = "",
    guards: Sequence[str] = (),
) -> tuple[str, dict[str, str]]:
    """The SBPL profile text and its ``-D`` parameters (pure; testable anywhere).

    Allow-default with targeted denies, last match wins: writes only to the
    roots, the session temp dir, the user temp dir and the tty/null devices;
    other sessions' directories closed; protected paths read-only; the rename
    guards' directory nodes unwritable (no rename, writes inside still go);
    the deny list unreadable (and unwritable); the network closed except
    loopback listening and the proxy port; unix-socket connects denied with it.
    """
    params = _Params()
    writes = params.add("WRITE", [*policy.write_roots, policy.tmp_dir, user_temp])
    lines = ["(version 1)", "(allow default)", "(deny file-write*)"]
    lines += _rule("allow file-write*", writes, _DEVICE_WRITES)
    if policy.base_dir and policy.tmp_dir:
        lines += _rule("deny file-read* file-write*", params.add("BASE", [policy.base_dir]))
        lines += _rule("allow file-read* file-write*", params.add("TMP", [policy.tmp_dir]))
    lines += _rule("deny file-write*", params.add("PROTECT", protected))
    lines += _literal_rule("deny file-write*", params.add("GUARD", guards))
    lines += _rule("deny file-read* file-write*", params.add("DENY", policy.deny_read))
    lines += _rule("allow file-read*", params.add("READ", policy.read_paths))
    lines += [
        '(deny mach-lookup (global-name "com.apple.SecurityServer")'
        ' (global-name "com.apple.securityd.xpc"))'
    ]
    lines += _network_rules(policy)
    return "\n".join(lines) + "\n", params.values


class SeatbeltBackend:
    """macOS: ``/usr/bin/sandbox-exec`` with an allow-default SBPL profile."""

    name = "seatbelt"

    def __init__(self, executable: str = SANDBOX_EXEC) -> None:
        self.executable = executable

    def probe(self) -> tuple[bool, str]:
        return cached_probe(self.executable, self._probe_once)

    def extra_write_roots(self) -> tuple[str, ...]:
        """The per-user temp directory the profile leaves writable."""
        return (darwin_user_temp(),)

    def _probe_once(self) -> tuple[bool, str]:
        if not os.path.exists(self.executable):
            return False, f"{self.executable} is missing; this macOS cannot run the sandbox"
        argv = [self.executable, "-p", "(version 1)(allow default)", "/usr/bin/true"]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"sandbox-exec did not run: {exc_reason(exc)}"
        if proc.returncode != 0:
            return False, f"sandbox-exec failed: {' '.join(proc.stderr.split())[:200]}"
        return True, ""

    def build_argv(
        self, command: Sequence[str], policy: SandboxPolicy, cwd: str | None
    ) -> list[str]:
        existing, missing = write_protection(policy.write_roots)
        deny = [path for path in realpaths(policy.deny_read) if os.path.lexists(path)]
        guards = rename_guards(policy.write_roots, (*deny, *existing, *missing))
        profile, params = seatbelt_profile(
            policy,
            protected=(*existing, *missing),
            user_temp=darwin_user_temp(),
            guards=guards,
        )
        defines = [arg for key, value in params.items() for arg in ("-D", f"{key}={value}")]
        return [self.executable, "-p", profile, *defines, *command]


class UnavailableBackend:
    """No backend on this platform: every sandboxed spawn is refused."""

    name = ""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def probe(self) -> tuple[bool, str]:
        return False, self.reason

    def build_argv(
        self, command: Sequence[str], policy: SandboxPolicy, cwd: str | None
    ) -> list[str]:
        raise SandboxError(UNAVAILABLE, self.reason)


def backend_for_platform(platform: str | None = None) -> Backend:
    """bwrap on Linux, Seatbelt on macOS, otherwise an unavailable backend."""
    name = platform or sys.platform
    if name.startswith("linux"):
        return BwrapBackend()
    if name == "darwin":
        return SeatbeltBackend()
    return UnavailableBackend(f"no sandbox backend for {name} (Linux: bwrap; macOS: sandbox-exec)")
