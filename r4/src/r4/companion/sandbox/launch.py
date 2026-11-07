"""Supervisor that runs one MCP stdio server or hatch runtime sandboxed (ADR-101 D-9).

``python -m r4.companion.sandbox.launch --policy FILE [--name SERVER]
[--network isolated|host] [--web none|public|allowlist] [--domains a,b]
[--write PATH ...] [--read-extra PATH ...] -- CMD ARGS...``

- Policy file ``{"enabled": bool, "user_policy": {...}}``; disabled: exec the
  command unchanged.
- With ``--name``, the server's entry in the user-level ``mcp-servers.json``
  (or ``$R4_MCP_CONFIG``, never a project file) may carry ``"sandbox": {"web",
  "domains", "write"}``; explicit flags win. ``"sandbox": false`` exempts it.
- This process stays unsandboxed: it serves the child's egress proxy with a
  FIXED web access (default ``none``) and runs the child under the backend
  with stdio inherited (no pipes), forwards SIGTERM/SIGINT/SIGHUP and exits
  with the child's return code. The wrapper dies with it (bwrap
  ``--die-with-parent``), so a SIGKILL of this process (the daemon's
  ``kill()``) leaves no server behind.
- Started through :func:`mcp_policy.mcp_launcher_prefix`, which drops the
  current directory from ``sys.path`` before anything is imported: this
  process runs unconfined, usually in a writable workspace.
- ``--network host`` (hatch runtimes, which accept loopback calls): no
  network namespace and no proxy; filesystem and env confinement only.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..web_access import WebAccess, WebAccessError, make_web_access
from .context import SandboxContext, SpawnSpec
from .policy import UserSandboxPolicy
from .setting import SANDBOXED_ENV, SandboxError, SandboxSetting

EXIT_REFUSED = 78
_FORWARDED = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="r4-sandbox-launch", description=__doc__.splitlines()[0])
    parser.add_argument("--policy", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--network", choices=("isolated", "host"), default="isolated")
    parser.add_argument("--web", choices=("none", "public", "allowlist"), default="")
    parser.add_argument("--domains", default="")
    parser.add_argument("--write", action="append", default=[])
    parser.add_argument("--read-extra", action="append", default=[], dest="read_extra")
    return parser


def parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    """Options before ``--``, the command after it."""
    items = list(argv)
    if "--" not in items:
        raise SystemExit("r4-sandbox-launch: missing '-- CMD ARGS...'")
    split = items.index("--")
    args = _parser().parse_args(items[:split])
    command = items[split + 1 :]
    if not command:
        raise SystemExit("r4-sandbox-launch: empty command")
    return args, command


def read_policy(path: str) -> dict[str, Any]:
    """The policy file; unreadable means enabled with no user additions (fail closed)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"enabled": True}
    return data if isinstance(data, dict) else {"enabled": True}


def _entry_files() -> list[Path]:
    from ...tui.mcp_config import _user_config_path

    files = [_user_config_path()]
    override = os.environ.get("R4_MCP_CONFIG", "")
    if override:
        files.append(Path(override))
    return [path for path in files if path.is_file()]


def server_entry(name: str) -> dict[str, Any]:
    """The user-level entry of ``name`` (``$R4_MCP_CONFIG`` overrides); {} when none."""
    from ...tui.mcp_config import _load_json_file

    found: dict[str, Any] = {}
    for path in _entry_files() if name else []:
        servers = _load_json_file(path).get("mcpServers")
        entry = servers.get(name) if isinstance(servers, dict) else None
        if isinstance(entry, dict):
            found = entry
    return found


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part) for part in value] if isinstance(value, (list, tuple)) else []


def fixed_web_access(args: argparse.Namespace, entry: Mapping[str, Any]) -> WebAccess:
    """Flags win over the entry; default ``none``; a bad allowlist falls back to ``none``."""
    mode = args.web or str(entry.get("web") or "none")
    domains = _list(args.domains) or _list(entry.get("domains"))
    try:
        return make_web_access(mode, domains, source="flag")
    except WebAccessError as exc:
        print(f"r4 sandbox: {exc}; web access for this server is off", file=sys.stderr)
        return make_web_access("none", source="flag")


def _launch_setting() -> SandboxSetting:
    source = "inherited" if os.environ.get(SANDBOXED_ENV) else "profile"
    return SandboxSetting("on", "", source)


def build_context(
    args: argparse.Namespace, entry: Mapping[str, Any], user_policy: UserSandboxPolicy
) -> SandboxContext:
    web = fixed_web_access(args, entry)
    writes = args.write or _list(entry.get("write"))
    return SandboxContext(
        setting=_launch_setting,
        web_access=lambda: web,
        user_policy=user_policy,
        write_paths=writes,
        read_paths=args.read_extra,
        network=args.network,
        cwd_writable=False,
        die_with_parent=True,
    )


def _exit_code(returncode: int) -> int:
    return 128 - returncode if returncode < 0 else returncode


def supervise(spec: SpawnSpec) -> int:
    """Run the wrapped child with inherited stdio; forward signals; its exit code."""
    child = subprocess.Popen(spec.argv, env=spec.env)  # stdio inherited: no pipes

    def forward(signum: int, _frame: object) -> None:
        if child.poll() is None:
            child.send_signal(signum)

    for signum in _FORWARDED:
        signal.signal(signum, forward)
    return _exit_code(child.wait())


def _exempt(policy: Mapping[str, Any], entry: Mapping[str, Any]) -> bool:
    return not policy.get("enabled") or entry.get("sandbox") is False


def main(argv: Sequence[str] | None = None) -> int:
    args, command = parse_args(sys.argv[1:] if argv is None else argv)
    policy = read_policy(args.policy)
    entry = server_entry(args.name)
    if _exempt(policy, entry):
        os.execvp(command[0], command)
    sandbox = entry.get("sandbox")
    context = build_context(
        args,
        sandbox if isinstance(sandbox, dict) else {},
        UserSandboxPolicy.from_mapping(policy.get("user_policy")),
    )
    try:
        spec = context.wrap(command, shell=False, cwd=None, env=dict(os.environ))
        return supervise(spec)
    except SandboxError as exc:
        print(f"r4 sandbox: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    finally:
        context.close()


if __name__ == "__main__":
    sys.exit(main())
