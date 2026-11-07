"""The fail-closed sandbox self-test (ADR-101 D-8, the ADR-046 P4 analogue).

Runs a small probe INSIDE the real backend with the policy about to be used:

- a write outside the writable roots must not reach the host;
- a sentinel file created in the r4 config directory (on the deny list) must
  be unreadable;
- the network must be isolated (Linux: no interface but ``lo`` in the
  namespace; macOS: a loopback connect to a closed port is denied by the
  profile instead of refused by the kernel, which also holds offline).

Results are cached per process by ``(backend, policy digest)``.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .backends import exc_reason, extra_write_roots
from .policy import SandboxPolicy, scrub_env, within
from .setting import SANDBOXED_ENV

if TYPE_CHECKING:
    from .backends import Backend
    from .context import SandboxContext

_CACHE: dict[tuple[str, str], tuple[bool, str]] = {}
_TIMEOUT = 30

#: The probe, run as ``python -I -c PROBE <write target> <sentinel> <net mode>``.
PROBE = r"""
import errno, json, socket, sys
target, sentinel, net = sys.argv[1:4]
res = {}
try:
    with open(target, "w") as fh:
        fh.write("x")
    res["write"] = "written"
except OSError:
    res["write"] = "blocked"
try:
    with open(sentinel) as fh:
        res["read"] = "read:" + fh.read(64)
except OSError:
    res["read"] = "blocked"
if net == "linux":
    names = sorted(n for _, n in socket.if_nameindex())
    res["net"] = "isolated" if names in (["lo"], []) else "open:" + ",".join(names)
elif net == "darwin":
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect(("127.0.0.1", 9))
        res["net"] = "open"
    except OSError as exc:
        res["net"] = "isolated" if exc.errno in (errno.EPERM, errno.EACCES) else "open:%s" % exc
    finally:
        s.close()
else:
    res["net"] = "skipped"
print(json.dumps(res))
"""


def clear_cache() -> None:
    """Forget cached results (tests; a backend that changed under a long process)."""
    _CACHE.clear()


def _net_mode(backend: Backend, policy: SandboxPolicy) -> str:
    if policy.network != "isolated":
        return "skip"
    return "darwin" if backend.name == "seatbelt" else "linux"


def _make_sentinel(token: str) -> str:
    """A sentinel in the r4 config dir (deny list); "" when it cannot be written."""
    from ...config import _resolve_config_dir

    directory = _resolve_config_dir()
    path = directory / f".sandbox-selftest-{token}"
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        return ""
    return str(path)


def _write_target(policy: SandboxPolicy, token: str, extra: tuple[str, ...] = ()) -> str:
    """A host path outside every writable root, in a directory that exists.

    ``extra`` are the directories the backend itself leaves writable (macOS:
    the per-user temp directory, where a test's or a user's HOME may live).
    """
    open_roots = (*policy.write_roots, policy.tmp_dir, *extra)
    for candidate in (Path.home(), Path(sys.prefix), Path(__file__).parent):
        path = os.path.realpath(candidate)
        if os.path.isdir(path) and not any(root and within(path, root) for root in open_roots):
            return os.path.join(path, f".r4-sandbox-probe-{token}")
    return ""


def _probe_env(backend: Backend, policy: SandboxPolicy) -> dict[str, str]:
    env = scrub_env(os.environ)
    env[SANDBOXED_ENV] = backend.name
    env["TMPDIR"] = policy.tmp_dir or env.get("TMPDIR", "")
    return env


def _parse(proc: subprocess.CompletedProcess[str]) -> dict[str, Any] | None:
    lines = proc.stdout.strip().splitlines()
    try:
        data = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _failures(result: dict[str, Any], target: str) -> list[str]:
    failures = []
    if target and os.path.lexists(target):
        failures.append("a write outside the writable roots reached the host")
    if str(result.get("read", "")).startswith("read:"):
        failures.append("a file in the r4 config directory was readable")
    if str(result.get("net", "")).startswith("open"):
        failures.append(f"the network is not isolated ({result.get('net')})")
    return failures


def _verdict(proc: subprocess.CompletedProcess[str], target: str) -> tuple[bool, str]:
    result = _parse(proc)
    if result is None:
        tail = " ".join(proc.stderr.split())[-300:]
        return False, f"the self-test probe did not report (rc {proc.returncode}): {tail}"
    failures = _failures(result, target)
    return (not failures), "; ".join(failures)


def _unprovable(target: str, sentinel: str) -> str:
    """Why the probe could not prove the policy ("" when it can): never a silent skip."""
    if not target:
        return "no probe location outside the writable roots (are they above $HOME?)"
    if not sentinel:
        return "the sentinel in the r4 config directory could not be written"
    return ""


def _run_probe(backend: Backend, policy: SandboxPolicy) -> tuple[bool, str]:
    token = secrets.token_hex(8)
    sentinel = _make_sentinel(token)
    target = _write_target(policy, token, extra_write_roots(backend))
    probe = [sys.executable, "-I", "-c", PROBE, target, sentinel]
    try:
        reason = _unprovable(target, sentinel)
        if reason:
            return False, f"the self-test cannot run: {reason}"
        argv = backend.build_argv([*probe, _net_mode(backend, policy)], policy, None)
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            env=_probe_env(backend, policy),
            stdin=subprocess.DEVNULL,
        )
        return _verdict(proc, target)
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        return False, f"the self-test probe did not run: {exc_reason(exc)}"
    finally:
        for path in (sentinel, target):
            with contextlib.suppress(OSError):
                if path:
                    os.unlink(path)


def self_test_backend(backend: Backend, policy: SandboxPolicy) -> tuple[bool, str]:
    """Run (or recall) the self-test of ``backend`` under ``policy``.

    Only a pass is remembered: a transient failure (a timeout under load, a
    fork that failed) is retried by the next spawn (2026-10-30, review LC-4).
    """
    key = (backend.name, policy.digest())
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    result = _run_probe(backend, policy)
    if result[0]:
        _CACHE[key] = result
    return result


def run_self_test(context: SandboxContext, policy: SandboxPolicy | None = None) -> tuple[bool, str]:
    """``(ok, reason)`` for the context's backend under ``policy`` (default: its own)."""
    if policy is None:
        try:
            policy = context.policy_for()
        except RuntimeError as exc:  # SandboxError: a refused root is a failed self-test
            return False, str(exc)
    return self_test_backend(context.backend, policy)
