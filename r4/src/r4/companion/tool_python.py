"""The Python interpreter the companion's tools run user code with.

TB 4.0 harness-efficiency plan 2026-10-24, item H1: ``python_exec`` and the
coding helpers (``compile_code`` / ``run_code`` / ``test_code``) used
``sys.executable``. When R4 is installed in its own venv (pipx, ``uv tool``,
the terminal-bench ``/opt/r4bench/venv``), that is R4's interpreter, so the
user's packages (numpy, torch, PIL …) were "missing" and the model concluded
the environment lacked them. :func:`tool_python` returns the user's Python
instead:

1. ``R4_TOOL_PYTHON`` when set: ``sys`` means ``sys.executable`` (the old
   behaviour, and the off-switch); any other value is used as the path.
2. Outside a venv, or inside R4's venv when the user activated it
   (``VIRTUAL_ENV`` is R4's own prefix), ``sys.executable``.
3. Inside a venv the user did not activate, the first usable of: the
   user's active environment (``VIRTUAL_ENV``, then ``CONDA_PREFIX``), the
   workspace's ``.venv`` / ``venv``, then ``python3`` / ``python`` on
   ``PATH`` with R4's venv bin directory removed. The macOS Command Line
   Tools stub ``/usr/bin/python3`` is skipped when the tools are not
   installed (running it opens an install dialog). "Usable" means it runs
   and is Python 3.8 or newer, probed once per path (review 2026-10-24: a
   RHEL8 ``/usr/bin/python3`` is 3.6).
4. No such interpreter: ``sys.executable``.

With the local tool sandbox on (ADR-101) the probe runs through
``wrap_spawn`` like the tool itself: a workspace ``.venv/bin/python`` is a
file the sandboxed agent can write, so probing it on the host would run
agent-written code unconfined. Probe results are cached per path and
sandbox state; a sandbox refusal counts as unusable and is not cached.
"""

from __future__ import annotations

import contextlib
import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .sandbox import SandboxError, SpawnSpec, current_sandbox, wrap_spawn

TOOL_PYTHON_ENV = "R4_TOOL_PYTHON"
_MACOS_STUB = "/usr/bin/python3"
_CANDIDATES = ("python3", "python")
_MIN_VERSION = (3, 8)
_PROBE = "import sys; print(sys.version_info[:2] >= (3, 8))"


def _in_foreign_venv() -> bool:
    """True when R4 runs from a venv that the user did not activate."""
    if sys.prefix == sys.base_prefix:
        return False
    active = os.environ.get("VIRTUAL_ENV", "")
    return not (active and _same_path(active, sys.prefix))


def _same_path(a: str, b: str) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return a == b


def _venv_bin_dirs() -> set[Path]:
    """R4's own interpreter directories (venv ``bin`` / ``Scripts``)."""
    dirs = {Path(sys.prefix) / "bin", Path(sys.prefix) / "Scripts"}
    dirs.add(Path(sys.executable).parent)
    return {_resolved(d) for d in dirs}


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _search_path() -> str:
    """``PATH`` without R4's venv bin directories."""
    skip = _venv_bin_dirs()
    entries = os.environ.get("PATH", "").split(os.pathsep)
    kept = [e for e in entries if e and _resolved(Path(e)) not in skip]
    return os.pathsep.join(kept)


@functools.lru_cache(maxsize=1)
def _macos_clt_missing() -> bool:
    """True when ``xcode-select -p`` fails (the CLT python3 is only a stub)."""
    try:
        done = subprocess.run(
            ["xcode-select", "-p"],
            capture_output=True,
            timeout=5,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return done.returncode != 0


def _is_macos_stub(path: str) -> bool:
    if sys.platform != "darwin" or path != _MACOS_STUB:
        return False
    return _macos_clt_missing()


@functools.lru_cache(maxsize=32)
def _usable(path: str, sandboxed: bool = False) -> bool:
    """*path* runs and is Python 3.8 or newer (probed once per path and sandbox state).

    ``sandboxed`` only keys the cache; the probe is wrapped by the current
    sandbox. Raises :class:`SandboxError` (never cached) when it refuses.
    """
    spec = wrap_spawn([path, "-c", _PROBE], shell=False, cwd=None, env=dict(os.environ))
    return _run_probe(spec)


def _run_probe(spec: SpawnSpec) -> bool:
    """Run the probe in its own process group; a timeout kills the whole group."""
    try:
        proc = subprocess.Popen(
            spec.argv,
            shell=spec.shell,
            env=spec.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    try:
        out, _err = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return False
    return proc.returncode == 0 and out.strip() == "True"


def _kill_group(proc: subprocess.Popen[str]) -> None:
    from ..procgroup import signal_group

    signal_group(proc)
    with contextlib.suppress(Exception):
        proc.communicate(timeout=5)


def _probe_ok(path: str, sandboxed: bool) -> bool:
    try:
        return _usable(path, sandboxed)
    except SandboxError:
        return False


def _env_interpreter(prefix: str) -> str | None:
    """``<prefix>/bin/python`` (or ``Scripts/python.exe``) when it exists."""
    if not prefix or _same_path(prefix, sys.prefix):
        return None
    for relative in ("bin/python", "bin/python3", "Scripts/python.exe"):
        candidate = Path(prefix) / relative
        if candidate.is_file():
            return str(candidate)
    return None


def _user_env_pythons() -> list[str]:
    """The user's active environment, then the workspace's own venv."""
    prefixes = [os.environ.get("VIRTUAL_ENV", ""), os.environ.get("CONDA_PREFIX", "")]
    prefixes += [str(Path.cwd() / name) for name in (".venv", "venv")]
    found = (_env_interpreter(prefix) for prefix in prefixes)
    return [path for path in found if path]


def _path_pythons() -> list[str]:
    """``python3`` / ``python`` on ``PATH`` outside R4's venv (no macOS stub)."""
    search = _search_path()
    found = (shutil.which(name, path=search) for name in _CANDIDATES)
    return [path for path in found if path and not _is_macos_stub(path)]


def _system_python() -> str | None:
    """The first usable interpreter outside R4's venv, or None."""
    sandboxed = current_sandbox().setting.enabled
    for candidate in _user_env_pythons() + _path_pythons():
        if _probe_ok(candidate, sandboxed):
            return candidate
    return None


def tool_python() -> str:
    """The interpreter tools run user Python with (see the module docstring)."""
    override = os.environ.get(TOOL_PYTHON_ENV, "").strip()
    if override:
        return sys.executable if override.lower() == "sys" else override
    if not _in_foreign_venv():
        return sys.executable
    return _system_python() or sys.executable
