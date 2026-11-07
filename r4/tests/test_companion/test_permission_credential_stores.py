"""Credential-path fence: r4's own stores, symlinks and recursive searches.

Cycle-2 security lane (2026-10-21). In ``auto_safe`` the fence asked before
``file_read ~/.ssh/id_ed25519`` but auto-approved reads that return the same
class of secret: r4's own ``sources.json`` / ``tokens.json`` / ``config.json``
/ ``curve-session.json``, the ``s3cfg_<user>`` r4 login writes under
``~/.config/umrs``, a repo symlink pointing at a private key, and
``grep_search`` over ``~/.ssh`` or ``~/.config``. A prompt injection in any
tool result could have the agent read them without a human in the loop.
Repro: $C2/security/perm2/repro_cred_fence.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from r4.companion.permissions import PermissionManager, PermissionMode


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    for rel in (".ssh/id_ed25519", ".config/umrs/s3cfg_alice", "proj/README.md"):
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text("FAKE\n")
    (home / "proj/notes.txt").symlink_to(home / ".ssh/id_ed25519")
    monkeypatch.setenv("HOME", str(home))
    return home


def _asked(mode: PermissionMode, tool: str, args: dict) -> bool:
    asked: list[str] = []
    manager = PermissionManager(mode=mode, approval_callback=lambda t, d, a: bool(asked.append(t)))
    manager.check(tool, args)
    return bool(asked)


@pytest.mark.parametrize(
    "tool,args",
    [
        ("file_read", {"path": "~/.config/r4/sources.json"}),
        ("file_read", {"path": "~/.config/r4/tokens.json"}),
        ("file_read", {"path": "~/.config/r4/config.json"}),
        ("file_read", {"path": "~/.config/r4/curve-session-0123abcd.json"}),
        ("file_read", {"path": "~/.config/umrs/s3cfg_alice"}),
        ("file_read", {"path": "~/.git-credentials"}),
        ("file_read", {"path": "proj/notes.txt"}),
        ("grep_search", {"pattern": ".", "path": "~/.ssh"}),
        ("grep_search", {"pattern": "secret_key", "path": "~/.config"}),
        ("grep_search", {"pattern": "api_key", "path": "~"}),
        ("glob_search", {"pattern": ".ssh/*", "path": "~"}),
    ],
)
def test_credential_reads_ask_in_auto_safe(
    home: Path, monkeypatch: pytest.MonkeyPatch, tool: str, args: dict
) -> None:
    monkeypatch.chdir(home)
    assert _asked(PermissionMode.AUTO_SAFE, tool, args)


def test_reading_r4_stores_in_a_shell_asks_even_in_trust(home: Path) -> None:
    assert _asked(PermissionMode.TRUST, "shell_exec", {"command": "cat ~/.config/r4/sources.json"})


@pytest.mark.parametrize(
    "tool,args",
    [
        ("file_read", {"path": "proj/README.md"}),
        ("grep_search", {"pattern": "TODO", "path": "proj"}),
        ("glob_search", {"pattern": "**/*.py", "path": "proj"}),
        ("list_dir", {"path": "proj"}),
    ],
)
def test_ordinary_project_reads_stay_auto(
    home: Path, monkeypatch: pytest.MonkeyPatch, tool: str, args: dict
) -> None:
    monkeypatch.chdir(home)
    assert not _asked(PermissionMode.AUTO_SAFE, tool, args)
