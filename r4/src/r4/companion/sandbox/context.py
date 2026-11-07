"""One session's sandbox: the wrapper every agent-driven spawn goes through (ADR-101).

Spawn sites call :func:`wrap_spawn` right before ``Popen`` and use the
returned :class:`SpawnSpec` as is. Off: the arguments come back unchanged.
On: an argv list (``shell=True`` becomes ``["/bin/sh", "-c", command]``)
under the backend, with a scrubbed environment, ``TMPDIR`` pointing at the
per-session temp directory, ``R4_SANDBOXED`` set and, when the session's web
access allows anything, the proxy variables. A wrapper that cannot be built
while the setting is on raises :class:`SandboxError` (and only that); the
caller turns it into a tool error and never runs the command unconfined (D-3).

The writable root is the ADR-092 agent workspace, else the context's
``workspace_root``, else the directory the context was created in; a per-call
``cwd`` never widens the writes. A root at or above ``$HOME`` or a temp
directory is refused (``sandbox.refused``).

The egress proxy starts lazily on the first sandboxed spawn whose session
allows web access, after the availability check passed. With web ``none`` no
proxy runs and no proxy variable is set on either platform: the child's
network is simply closed (implementer choice, ADR-101 D-6). A process started
then stays offline if ``/web`` later opens; one started under
``allowlist``/``public`` follows ``/web`` live, ``none`` included.

Session directories (``<tempdir>/r4-sandbox-<uid>/s-XXXX``) record their
owner's pid; one whose owner is gone (a SIGKILLed launcher) is removed when
the next one is made. A removed session directory (tmp cleaners) is rebuilt.
A directory lives until :meth:`SandboxContext.close` or process exit, never
only as long as the context object: it is the bind-mounted ``/tmp`` of
sandboxed processes (detached shells, language servers) that outlive a
session switch (2026-10-30, review LC-3).

Which context a spawn reads (2026-10-30, review ESC-2/3/4): the one pinned
for the running thread or task (:func:`pin_sandbox`; a turn pins its
session's, a background subagent or team plan the one it started under, see
:func:`bind_sandbox`), else the process-global installed one, else an
ambient one built from the environment and the profile default.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import os
import shutil
import stat
import tempfile
import threading
import weakref
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeVar

from ..web_access import WebAccess, ambient_web_access
from .backends import Backend, backend_for_platform
from .policy import (
    PROXY_ENV_NAMES,
    SandboxPolicy,
    UserSandboxPolicy,
    deny_read_paths,
    real,
    realpaths,
    refused_root,
    scrub_env,
)
from .proxy import EgressProxy, ProxyEndpoint, proxy_env
from .setting import (
    REFUSED,
    SANDBOXED_ENV,
    SELF_TEST_FAILED,
    UNAVAILABLE,
    SandboxError,
    SandboxSetting,
    resolve_sandbox,
    safe_profile_default,
)

_WEB_OPEN = ("allowlist", "public")
_BACKEND_NAMES = ("bwrap", "seatbelt")
_SOCKET_PATH_MAX = 100
_OWNER_FILE = "owner"
_T = TypeVar("_T")


class _DirAnchor:
    """Lives as long as the module: session directories go at close or exit."""


_DIR_ANCHOR = _DirAnchor()


@dataclass(frozen=True)
class SpawnSpec:
    """What the spawn site passes to ``Popen``."""

    argv: list[str] | str
    shell: bool
    env: dict[str, str]
    sandboxed: bool
    backend: str
    #: True only when this process built the wrapper (``build_argv`` ran);
    #: an inherited pass-through is ``sandboxed`` but not ``wrapped``.
    wrapped: bool = False


@dataclass(frozen=True)
class SessionDirs:
    """The per-session directory: ``tmp`` (``/tmp`` inside), the proxy socket dir, an empty file."""

    base: str
    session: str
    tmp: str
    proxy: str
    empty_file: str


def _private_dir(path: str) -> str:
    """Create (or check) a 0700 directory owned by this user; refuse anything else."""
    with contextlib.suppress(FileExistsError):
        os.mkdir(path, 0o700)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise SandboxError(UNAVAILABLE, f"{path} is not a private directory of this user")
    if info.st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


def _owner_gone(session: str) -> bool:
    """True when the session's recorded owner process no longer exists."""
    try:
        pid = int(Path(session, _OWNER_FILE).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False  # no owner recorded (yet): leave it alone
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def reap_stale_sessions(base: str) -> list[str]:
    """Remove ``s-*`` directories under ``base`` whose owner process is gone."""
    try:
        with os.scandir(base) as entries:
            sessions = [e.path for e in entries if e.name.startswith("s-") and e.is_dir()]
    except OSError:
        return []
    stale = [path for path in sessions if _owner_gone(path)]
    for path in stale:
        shutil.rmtree(path, ignore_errors=True)
    return stale


def _make_session_dirs() -> SessionDirs:
    base = _private_dir(os.path.join(real(tempfile.gettempdir()), f"r4-sandbox-{os.getuid()}"))
    reap_stale_sessions(base)
    session = real(tempfile.mkdtemp(prefix="s-", dir=base))
    Path(session, _OWNER_FILE).write_text(str(os.getpid()), encoding="ascii")
    tmp = _private_dir(os.path.join(session, "tmp"))
    proxy = _private_dir(os.path.join(session, "proxy"))
    empty = os.path.join(session, "empty")
    Path(empty).touch(mode=0o444)
    return SessionDirs(base, session, tmp, proxy, empty)


def make_session_dirs() -> SessionDirs:
    """``<tempdir>/r4-sandbox-<uid>/s-XXXX/{tmp,proxy,empty,owner}``, all private."""
    try:
        return _make_session_dirs()
    except OSError as exc:
        raise SandboxError(
            UNAVAILABLE, f"cannot make the sandbox session directory: {exc}"
        ) from exc


def _as_argv(args: str | Sequence[str], shell: bool) -> list[str]:
    if isinstance(args, str):
        return ["/bin/sh", "-c", args] if shell else [args]
    # Popen(list, shell=True) runs args[0] as the script, the rest as $0, $1, ...
    return ["/bin/sh", "-c", *args] if shell else list(args)


def _getcwd() -> str:
    try:
        return os.getcwd()
    except OSError:
        return ""


def _teardown(proxy: EgressProxy | None, finalizer: weakref.finalize[..., Any] | None) -> None:
    if proxy is not None:
        with contextlib.suppress(Exception):
            proxy.stop()
    if finalizer is not None:
        finalizer()


class SandboxContext:
    """Per-session sandbox state: setting and web getters, dirs, proxy, backend.

    ``die_with_parent``: the wrapper is killed when the process that started
    it dies (the MCP launcher); the spawn sites leave it off.
    """

    def __init__(
        self,
        *,
        setting: Callable[[], SandboxSetting],
        web_access: Callable[[], WebAccess],
        workspace_root: str | Path | None = None,
        user_policy: UserSandboxPolicy | None = None,
        backend: Backend | None = None,
        write_paths: Sequence[str] = (),
        read_paths: Sequence[str] = (),
        network: str = "isolated",
        cwd_writable: bool = True,
        proxy_factory: Callable[..., EgressProxy] | None = None,
        die_with_parent: bool = False,
    ) -> None:
        self._setting = setting
        self._web = web_access
        self._workspace_root = str(workspace_root) if workspace_root else ""
        self._open_cwd = _getcwd() if cwd_writable else ""
        self._user = user_policy or UserSandboxPolicy()
        self._backend = backend
        self._write_paths = tuple(write_paths)
        self._read_paths = tuple(read_paths)
        self._network = network
        self._die_with_parent = die_with_parent
        self._proxy_factory = proxy_factory or EgressProxy
        self._lock = threading.RLock()
        self._dirs: SessionDirs | None = None
        self._finalizer: weakref.finalize[..., Any] | None = None
        self._proxy: EgressProxy | None = None
        self._failure = ""

    # ── parts ──

    @property
    def backend(self) -> Backend:
        if self._backend is None:
            self._backend = backend_for_platform()
        return self._backend

    @property
    def setting(self) -> SandboxSetting:
        return self._setting()

    def session_dirs(self) -> SessionDirs:
        """The session directory, made on first use and rebuilt when it was removed."""
        with self._lock:
            if self._dirs is not None and not os.path.isdir(self._dirs.tmp):
                _teardown(*self._detach_locked())
            if self._dirs is None:
                dirs = make_session_dirs()
                # Anchored to the module, not to self: sandboxed children
                # still running use this directory as /tmp (review LC-3).
                self._finalizer = weakref.finalize(
                    _DIR_ANCHOR, shutil.rmtree, dirs.session, ignore_errors=True
                )
                self._dirs = dirs
            return self._dirs

    def _detach_locked(self) -> tuple[EgressProxy | None, weakref.finalize[..., Any] | None]:
        proxy, finalizer = self._proxy, self._finalizer
        self._proxy = self._dirs = self._finalizer = None
        return proxy, finalizer

    def _primary_root(self) -> str:
        from ..agent_workspace import current_workspace

        workspace = current_workspace()
        if workspace is not None:
            return str(workspace.root)
        return self._workspace_root or self._open_cwd

    def write_roots(self) -> tuple[str, ...]:
        """The writable roots (real); a spawn's cwd never widens them."""
        primary = self._primary_root()
        return realpaths([primary, *self._user.extra_write_paths, *self._write_paths])

    def _vetted_roots(self, dirs: SessionDirs) -> tuple[str, ...]:
        roots = self.write_roots()
        home = real(Path.home())
        temps = realpaths(["/tmp", tempfile.gettempdir()])
        for root in roots:
            reason = refused_root(root, home=home, temp_dirs=temps, base=dirs.base)
            if reason:
                hint = "start r4 in a project directory or set an agent workspace"
                raise SandboxError(
                    REFUSED, f"{reason}; the sandbox will not make it writable ({hint})"
                )
        return roots

    def _web_open(self) -> bool:
        return self._network == "isolated" and self._web().mode in _WEB_OPEN

    def _endpoint(self, dirs: SessionDirs) -> ProxyEndpoint:
        with self._lock:
            if self._proxy is None:
                sock = os.path.join(dirs.proxy, "p.sock") if self.backend.name == "bwrap" else ""
                if len(sock) > _SOCKET_PATH_MAX:
                    raise SandboxError(UNAVAILABLE, f"proxy socket path too long: {sock}")
                self._proxy = self._proxy_factory(self._web, unix_path=sock)
            return self._start_proxy_locked()

    def _start_proxy_locked(self) -> ProxyEndpoint:
        proxy = self._proxy
        assert proxy is not None
        try:
            return proxy.start()
        except SandboxError:
            raise
        except (OSError, RuntimeError) as exc:
            self._proxy = None
            _teardown(proxy, None)
            raise SandboxError(
                UNAVAILABLE, f"the sandbox egress proxy did not start: {exc}"
            ) from exc

    def _with_proxy(
        self, policy: SandboxPolicy, dirs: SessionDirs
    ) -> tuple[SandboxPolicy, ProxyEndpoint]:
        endpoint = self._endpoint(dirs)
        return replace(policy, proxy_socket=endpoint.unix_path, proxy_port=endpoint.port), endpoint

    def policy_for(self) -> SandboxPolicy:
        """The effective policy of one spawn, before any proxy is attached.

        Raises :class:`SandboxError` (``sandbox.refused``) for a writable root
        at or above ``$HOME`` or a temp directory. A spawn's cwd never widens
        writes.
        """
        dirs = self.session_dirs()
        return SandboxPolicy(
            write_roots=(*self._vetted_roots(dirs), dirs.tmp),
            deny_read=deny_read_paths(self._user),
            read_paths=realpaths(self._read_paths),
            tmp_dir=dirs.tmp,
            session_dir=dirs.session,
            base_dir=dirs.base,
            empty_file=dirs.empty_file,
            network=self._network,
            die_with_parent=self._die_with_parent,
        )

    # ── availability ──

    def check_available(self, policy: SandboxPolicy | None = None) -> None:
        """Probe the backend and run the self-test (cached per policy); raise when unusable."""
        ok, reason = self.backend.probe()
        if not ok:
            self._failure = reason
            raise SandboxError(UNAVAILABLE, reason)
        from .selftest import run_self_test

        ok, reason = run_self_test(self, policy)
        if not ok:
            self._failure = reason
            raise SandboxError(SELF_TEST_FAILED, reason)
        self._failure = ""

    # ── the wrapper ──

    def wrap(
        self, args: str | list[str], *, shell: bool, cwd: str | None, env: dict[str, str]
    ) -> SpawnSpec:
        setting = self._setting()
        if not setting.enabled:
            return SpawnSpec(args, shell, env, sandboxed=False, backend="")
        if setting.source == "inherited":
            name = env.get(SANDBOXED_ENV) or os.environ.get(SANDBOXED_ENV, "")
            backend = name if name in _BACKEND_NAMES else ""
            return SpawnSpec(args, shell, env, sandboxed=True, backend=backend)
        policy = self.policy_for()
        endpoint: ProxyEndpoint | None = None
        self.check_available(policy)  # before the proxy starts: a refusal leaves none running
        if self._web_open():
            policy, endpoint = self._with_proxy(policy, self.session_dirs())
        argv = self.backend.build_argv(_as_argv(args, shell), policy, cwd)
        child_env = self.child_env(env, policy, endpoint)
        return SpawnSpec(
            argv, False, child_env, sandboxed=True, backend=self.backend.name, wrapped=True
        )

    def child_env(
        self, env: dict[str, str], policy: SandboxPolicy, endpoint: ProxyEndpoint | None
    ) -> dict[str, str]:
        """D-5 env: scrubbed, host proxy variables dropped, ours added when web is open."""
        out = scrub_env(env, keep=self._user.env_keep)
        for name in PROXY_ENV_NAMES:
            out.pop(name, None)
        out[SANDBOXED_ENV] = self.backend.name
        out["TMPDIR"] = policy.tmp_dir
        if endpoint is not None:
            out.update(proxy_env(endpoint))
        return out

    # ── reporting and teardown ──

    def _network_label(self, setting: SandboxSetting) -> str:
        if not setting.enabled or setting.source == "inherited":
            return "host (sandbox off)" if not setting.enabled else "inherited"
        web = self._web().label()
        return f"{'isolated' if self._network == 'isolated' else 'host'} (web: {web})"

    def _availability(self, setting: SandboxSetting) -> tuple[str, bool, str]:
        if setting.source in ("venue", "inherited"):
            return setting.source, setting.source == "inherited", ""
        ok, reason = self.backend.probe()
        if ok and self._failure:
            return self.backend.name, False, self._failure
        return self.backend.name, ok, reason

    def status(self) -> dict[str, Any]:
        """``{mode, source, backend, available, reason, tmp_dir, write_roots, network}``."""
        setting = self._setting()
        backend, available, reason = self._availability(setting)
        return {
            "mode": setting.mode,
            "source": setting.source,
            "backend": backend,
            "available": available,
            "reason": reason,
            "tmp_dir": self._dirs.tmp if self._dirs else "",
            "write_roots": list(self.write_roots()),
            "network": self._network_label(setting),
        }

    def close(self) -> None:
        """Stop the proxy and remove the per-session directory (idempotent)."""
        with self._lock:
            detached = self._detach_locked()
        _teardown(*detached)


# ── the process-global current context ─────────────────────────────────────

_CURRENT: SandboxContext | None = None
_AMBIENT: SandboxContext | None = None
_PINNED: contextvars.ContextVar[SandboxContext | None] = contextvars.ContextVar(
    "r4_sandbox_pinned", default=None
)


def set_current_sandbox(ctx: SandboxContext | None) -> None:
    """Install the session's context (process-global, like ``set_current_user_hooks``)."""
    global _CURRENT
    _CURRENT = ctx


def installed_sandbox() -> SandboxContext | None:
    """The process-global installed context, None when none is installed."""
    return _CURRENT


def pinned_sandbox() -> SandboxContext | None:
    """The context pinned for this thread or task, None when none is."""
    return _PINNED.get()


@contextlib.contextmanager
def pin_sandbox(ctx: SandboxContext) -> Iterator[SandboxContext]:
    """Spawns in this block (and threads or tasks that copy its context) read ``ctx``.

    A session switch then never reroutes them (review ESC-2/3/4).
    """
    token = _PINNED.set(ctx)
    try:
        yield ctx
    finally:
        _PINNED.reset(token)


def bind_sandbox(func: Callable[..., _T]) -> Callable[..., _T]:
    """Capture :func:`current_sandbox` now; ``func`` later runs with it pinned.

    For thread targets that outlive the turn that started them (a
    background subagent, a team plan run): ``threading.Thread`` does not
    copy context variables, and the process-global context changes when the
    user switches sessions.
    """
    ctx = current_sandbox()

    @functools.wraps(func)
    def run(*args: Any, **kwargs: Any) -> _T:
        with pin_sandbox(ctx):
            return func(*args, **kwargs)

    return run


def _ambient_setting() -> SandboxSetting:
    return resolve_sandbox(metadata={}, env=os.environ, profile_default=safe_profile_default())


def current_sandbox() -> SandboxContext:
    """The pinned context, else the installed one, else an ambient one
    (env + profile default, ambient web)."""
    global _AMBIENT
    pinned = _PINNED.get()
    if pinned is not None:
        return pinned
    if _CURRENT is not None:
        return _CURRENT
    if _AMBIENT is None:
        _AMBIENT = SandboxContext(setting=_ambient_setting, web_access=ambient_web_access)
    return _AMBIENT


def wrap_spawn(
    args: str | list[str], *, shell: bool, cwd: str | None, env: dict[str, str]
) -> SpawnSpec:
    """``current_sandbox().wrap(...)``: call right before ``Popen``."""
    return current_sandbox().wrap(args, shell=shell, cwd=cwd, env=env)
