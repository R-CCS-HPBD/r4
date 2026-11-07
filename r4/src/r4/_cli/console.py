"""``r4 console`` — launch Console v3 Research Operations TUI.

Launch order (``plan_console_launch``; docs/r4/TUI_REFERENCE.md §1):

1. ``R4_TUI_BIN`` — an explicit binary (a missing file falls to the dev source).
2. The Console v3 source of the checkout this r4 is imported from, run with
   bun, when its deps are installed (``make tui-deps``) and bun >= 1.3.14 is
   found. Every clone runs its own TUI; nothing is compiled, nothing goes stale.
3. ``<user_bin>/r4-tui`` — the release binary from ``r4 tui install``.
4. Dev-source fallback: ``packages/r4-tui-v3`` under the cwd, run with bun.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path

from .base import CommandHandler
from .registry import command
from .tui import BIN_PATH_ENV, resolve_tui_binary, user_bin_path

#: ``packages/r4-tui-v3`` package.json ``engines.bun``.
MIN_BUN = (1, 3, 14)
#: ``make tui-deps`` writes ``node_modules/.r4-deps-stamp`` after a successful
#: ``bun install --frozen-lockfile``: the bytes of package.json + bun.lock that
#: node_modules was installed from (Makefile ``tui-deps``; keep in sync).
DEPS_STAMP = ".r4-deps-stamp"


def checkout_package_dir() -> Path | None:
    """``packages/r4-tui-v3`` of the checkout this r4 is imported from, else None.

    r4/src/r4/_cli/console.py → parents[4] = repo root for the editable install;
    from site-packages parents[4] is ``<venv>/lib`` and never matches. The cwd
    is deliberately ignored: r4 from clone A run inside clone B keeps A's TUI.
    """
    parents = Path(__file__).resolve().parents
    if len(parents) < 5:
        return None
    pkg = parents[4] / "packages" / "r4-tui-v3"
    return pkg if (pkg / "package.json").is_file() else None


def _console_package_dir() -> Path | None:
    """This checkout's packages/r4-tui-v3, else one under the cwd (dev fallback)."""
    own = checkout_package_dir()
    if own is not None:
        return own
    for path in (Path.cwd() / "packages" / "r4-tui-v3", Path.cwd() / "r4-tui-v3"):
        if (path / "package.json").is_file():
            return path
    return None


def _find_bun() -> str | None:
    which = shutil.which("bun") or shutil.which("bun.exe")
    if which:
        return which
    home = Path.home()
    for candidate in (
        home / ".bun" / "bin" / "bun",
        home / ".bun" / "bin" / "bun.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _bun_version(bun: str) -> tuple[int, int, int] | None:
    try:
        out = subprocess.run(
            [bun, "--version"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", out.strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def checkout_blocker(pkg: Path) -> str | None:
    """Why this checkout's Console v3 source cannot run; None when it can."""
    if not (pkg / "node_modules" / "@opentui" / "solid" / "package.json").is_file():
        return "its node_modules is not installed"
    bun = _find_bun()
    if bun is None:
        return "bun was not found"
    version = _bun_version(bun)
    if version is None or version < MIN_BUN:
        shown = ".".join(map(str, version)) if version else "unknown"
        return f"bun {shown} is older than 1.3.14"
    return None


def deps_state(pkg: Path) -> str:
    """``ok`` | ``unstamped`` | ``stale``: node_modules vs package.json + bun.lock."""
    try:
        stamp = (pkg / "node_modules" / DEPS_STAMP).read_bytes()
    except OSError:
        return "unstamped"
    try:
        want = (pkg / "package.json").read_bytes() + (pkg / "bun.lock").read_bytes()
    except OSError:
        return "stale"
    return "ok" if stamp == want else "stale"


def plan_console_launch() -> tuple[str, Path | None, str]:
    """What ``r4 console`` runs: ``(kind, path, reason)``; kind is binary | source."""
    if os.environ.get(BIN_PATH_ENV):
        binary = resolve_tui_binary()
        if binary is not None:
            return "binary", binary, BIN_PATH_ENV
        return "source", _console_package_dir(), f"{BIN_PATH_ENV} file missing"
    own = checkout_package_dir()
    if own is not None and checkout_blocker(own) is None:
        return "source", own, "checkout"
    binary = resolve_tui_binary()
    if binary is not None:
        return "binary", binary, "release binary"
    return "source", _console_package_dir(), "dev-source fallback"


def _checkout_rev(pkg: Path) -> str:
    """Last commit touching the TUI package (``<sha> <date>``); '' when unknown."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(pkg), "log", "-1", "--format=%h %cs", "--", "."],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


_DEPS_NOTES = {
    "unstamped": "has no install stamp",
    "stale": "was installed from an older package.json/bun.lock",
}


def _source_notes(pkg: Path) -> list[str]:
    rev = _checkout_rev(pkg)
    notes = [f"Console v3 from this checkout: {pkg}" + (f" (last TUI commit {rev})" if rev else "")]
    state = deps_state(pkg)
    if state in _DEPS_NOTES:
        notes.append(f"{pkg}/node_modules {_DEPS_NOTES[state]} — run `make tui-deps`")
    if user_bin_path().is_file():
        notes.append(
            f"release binary {user_bin_path()} is not used in a checkout; "
            f"run it with {BIN_PATH_ENV}={user_bin_path()} r4 console"
        )
    return notes


def _is_checkout_venv(pkg: Path) -> bool:
    """True when this interpreter is the checkout's own ./.venv (a developer install)."""
    try:
        return Path(sys.prefix).resolve() == (pkg.parents[1] / ".venv").resolve()
    except (OSError, IndexError):
        return False


def _binary_notes(binary: Path) -> list[str]:
    own = checkout_package_dir()
    if own is None or not _is_checkout_venv(own):
        return []  # wheel installs and install-r4.sh (~/.r4/venv) stay quiet
    why = checkout_blocker(own) or "unknown"
    return [
        f"running the release binary {binary}, not this checkout's console ({why})"
        " — run `make tui-deps`"
    ]


def launch_notes(target: Path | None, reason: str) -> list[str]:
    """One-line stderr notes naming which console runs and why."""
    if target is None:
        return []
    if reason == "checkout":
        return _source_notes(target)
    if reason == "release binary":
        return _binary_notes(target)
    return []


@command
class ConsoleCommand(CommandHandler):
    """``r4 console`` — Research Operations Console (v3)."""

    name = "console"
    help = "Launch Console v3 (Research Workspace / Mission Control)"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        p.add_argument(
            "--demo",
            action="store_true",
            help="Fixture data + DEMO MODE banner (same as --offline)",
        )
        p.add_argument(
            "--offline",
            action="store_true",
            help="Use offline demo fixtures (R4_TUI_BACKEND=offline)",
        )
        p.add_argument(
            "--backend",
            choices=("cli", "offline"),
            default=None,
            help="Backend mode (default: cli / live)",
        )
        p.add_argument(
            "--fresh",
            action="store_true",
            help="Start fresh — skip the tab-layout auto-resume (A1)",
        )
        return p

    def handle(self, args: Namespace) -> int:
        return cmd_console(args)


def console_available() -> bool:
    """True when Console v3 can be launched (compiled binary or bun + source)."""
    if resolve_tui_binary() is not None:
        return True
    return _console_package_dir() is not None and _find_bun() is not None


def _tui_argv(head: list[str], *, demo: bool, fresh: bool) -> list[str]:
    """Launch argv for both TUI lanes (binary + dev source)."""
    argv = list(head)
    if demo:
        argv.append("--demo")
    if fresh:
        argv.append("--fresh")
    return argv


def _launch_binary(
    binary: Path, *, demo: bool, fresh: bool = False, backend: str, env: dict
) -> int:
    """Launch the compiled single-file TUI binary in the user's CWD.

    Never spawn from the package dir: its bunfig.toml preload references
    modules that do not exist inside a compiled binary.
    """
    argv = _tui_argv([str(binary)], demo=demo, fresh=fresh)
    print(f"Starting Console v3 ({backend}) via {binary} …", file=sys.stderr)
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(argv, env=env)
        return proc.wait()
    except KeyboardInterrupt:
        # Ctrl+C already signals the child (same process group); do not dump
        # a subprocess traceback on top of the user's intentional exit.
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except BaseException:  # second Ctrl+C during graceful wait must not leak
                proc.kill()
        return 130
    except OSError as exc:
        print(f"Failed to launch Console v3: {exc}", file=sys.stderr)
        return 1


def _launch_dev_source(*, demo: bool, fresh: bool = False, backend: str, env: dict) -> int:
    """Fallback: run the TUI from a repo checkout via bun."""
    pkg = _console_package_dir()
    if pkg is None:
        print(
            "Console v3 not found. Install the binary with:\n"
            "  r4 tui install\n"
            "or from an R4 source checkout: make tui-deps\n"
            "  (or cd packages/r4-tui-v3 && bun install)",
            file=sys.stderr,
        )
        return 1

    bun = _find_bun()
    if not bun:
        print(
            "No TUI binary installed and bun not found.\n"
            "  Easiest: r4 tui install\n"
            f"  Dev:     install bun (https://bun.sh), then cd {pkg} && bun install",
            file=sys.stderr,
        )
        return 1

    bun_dir = str(Path(bun).parent)
    env["PATH"] = bun_dir + os.pathsep + env.get("PATH", "")

    # NOTE: bunfig.toml has NO root preload (it would crash the compiled
    # binary when launched with this package as cwd). Dev source therefore
    # passes the Solid preload explicitly.
    argv = _tui_argv(
        [bun, "--preload", "@opentui/solid/preload", "src/cli.tsx"],
        demo=demo,
        fresh=fresh,
    )

    print(f"Starting Console v3 ({backend}) from {pkg} …", file=sys.stderr)
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(argv, cwd=str(pkg), env=env)
        return proc.wait()
    except KeyboardInterrupt:
        # Same quiet-exit contract as _launch_binary: no traceback spam.
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except BaseException:  # second Ctrl+C during graceful wait must not leak
                proc.kill()
        return 130
    except OSError as exc:
        print(f"Failed to launch Console v3: {exc}", file=sys.stderr)
        return 1


def cmd_console(args: Namespace) -> int:
    demo = bool(getattr(args, "demo", False) or getattr(args, "offline", False))
    fresh = bool(getattr(args, "fresh", False))
    backend = "offline" if demo else (getattr(args, "backend", None) or "cli")
    env = os.environ.copy()
    env["R4_TUI_BACKEND"] = backend
    env["R4_TUI_PYTHON"] = sys.executable
    # Dev-source spawn chdirs into packages/r4-tui-v3, so the TUI's
    # process.cwd() fallback would resolve the workspace to the package dir.
    # Pin the launch pwd as workspace root unless the user overrode it.
    env.setdefault("R4_TUI_WORKSPACE", os.getcwd())
    if demo:
        env["R4_TUI_DEMO"] = "1"
    # `r4 --source b console`: the console's bridge builds its config from
    # this environment, so it must name the source too (2026-10-15). An
    # explicit R4_SOURCE applies even when R4_GATEWAY_URL is exported.
    if getattr(args, "source", None):
        env["R4_SOURCE"] = args.source

    kind, target, reason = plan_console_launch()
    for note in launch_notes(target, reason):
        print(f"r4: {note}", file=sys.stderr)
    if kind == "binary" and target is not None:
        return _launch_binary(target, demo=demo, fresh=fresh, backend=backend, env=env)
    return _launch_dev_source(demo=demo, fresh=fresh, backend=backend, env=env)
