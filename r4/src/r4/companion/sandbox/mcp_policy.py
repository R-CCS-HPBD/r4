"""The MCP launcher's policy file and prefix (ADR-101 D-9).

The ``r4-mcp remote-proxy`` daemon is one per user and outlives sessions, and
``r4_mcp`` imports nothing from ``r4``. The bridge therefore writes a small
policy file (``{"enabled", "user_policy", "exempt", "digest"}``, built by
:func:`launcher_policy_document` for the MCP daemon, the hatch runtimes and
the tests alike) and hands the daemon a launcher prefix; the daemon starts
each stdio child as ``<prefix> --name <server> -- <command>``. The digest
lets the bridge see whether the running daemon was started with the
current policy.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..._atomic import atomic_write_text
from .policy import UserSandboxPolicy, user_sandbox_policy

LAUNCH_MODULE = "r4.companion.sandbox.launch"
#: ``python -c`` bootstrap: drop "" (the current directory) and every relative
#: entry from ``sys.path`` before importing anything, then run the launcher.
#: ``-m`` would put the current directory first, and the launcher runs
#: unconfined in the caller's (writable) directory. ``-I`` would also drop
#: ``PYTHONPATH`` and user site-packages, which some installs need; ``-P`` is
#: 3.11+ only.
LAUNCH_BOOTSTRAP = (
    "import sys; sys.path[:] = [p for p in sys.path if p.startswith('/')]; "
    f"import runpy; runpy.run_module({LAUNCH_MODULE!r}, run_name='__main__', alter_sys=True)"
)


def mcp_launcher_prefix(policy_file: str | Path) -> list[str]:
    """``[python, -c, LAUNCH_BOOTSTRAP, --policy, FILE]`` (no current-directory imports)."""
    return [sys.executable, "-c", LAUNCH_BOOTSTRAP, "--policy", str(policy_file)]


def mcp_policy_digest(enabled: bool, user_policy: UserSandboxPolicy) -> str:
    """Stable digest of what the launcher enforces."""
    body = json.dumps(
        {"enabled": bool(enabled), "user_policy": user_policy.to_mapping()}, sort_keys=True
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def launcher_policy_document(
    user_policy: UserSandboxPolicy, *, exempt: Sequence[str] = (), enabled: bool = True
) -> dict[str, Any]:
    """The one shape every launcher policy file has, with its digest."""
    names = sorted(exempt)
    body = {"policy": mcp_policy_digest(enabled, user_policy), "exempt": names}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    return {
        "enabled": bool(enabled),
        "user_policy": user_policy.to_mapping(),
        "exempt": names,
        "digest": digest,
    }


def write_policy_document(path: Path, document: dict[str, Any]) -> None:
    """Write a launcher policy file (0600, atomic)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(Path(path), json.dumps(document, indent=2), mode=0o600)


def write_mcp_policy(path: Path, *, enabled: bool, profile: str | None = None) -> str:
    """Write the launcher's policy file for ``profile``'s user policy; returns its digest."""
    document = launcher_policy_document(user_sandbox_policy(profile), enabled=enabled)
    write_policy_document(path, document)
    return str(document["digest"])
