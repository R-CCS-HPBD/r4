"""The ssh-stall guard works for any ssh host, not only the built-in aliases.

Public-release review (2026-11-07): the guard only fired for a fixed list of
site aliases, so a user chasing an unreachable host of their own retried
forever. Any host now trips the guard when every step in the window failed to
connect to it; a reachable host being probed stays open, and the built-in
aliases keep their old behaviour.
"""

from __future__ import annotations

import json

import pytest

from r4.companion.agent import (
    _KNOWN_SSH_ALIASES,
    TOOL_SSH_TARGET_STALL_WINDOW,
    _should_switch_to_answer_mode,
    _ssh_host,
)

_UNRESOLVED = "ssh: Could not resolve hostname mycluster: Name or service not known"


@pytest.fixture(autouse=True)
def _interactive(monkeypatch):
    monkeypatch.delenv("R4_AUTONOMOUS", raising=False)
    monkeypatch.delenv("R4_COMPANION_SAME_CALL_GUARD", raising=False)


def _shell(command: str, result: str) -> dict[str, str]:
    arguments = json.dumps({"command": command}, sort_keys=True, separators=(",", ":"))
    return {"name": "shell_exec", "arguments": arguments, "result": result}


_VARIANTS = (
    "ssh mycluster hostname",
    "ssh -o ConnectTimeout=8 mycluster hostname",
    "ssh -v -p 2222 alice@mycluster 'uname -a'",
    "timeout 20 /usr/bin/ssh -F ~/.ssh/config mycluster true",
)


def test_unreachable_neutral_host_trips_the_ssh_stall() -> None:
    steps = [_shell(cmd, _UNRESOLVED) for cmd in _VARIANTS]
    assert len(steps) == TOOL_SSH_TARGET_STALL_WINDOW
    reason = _should_switch_to_answer_mode(steps)
    assert reason is not None
    assert "ssh target (mycluster)" in reason


def test_reachable_neutral_host_stays_open() -> None:
    steps = [_shell(cmd, f"node{i}") for i, cmd in enumerate(_VARIANTS)]
    assert _should_switch_to_answer_mode(steps) is None


def test_one_connected_step_keeps_a_neutral_host_open() -> None:
    steps = [_shell(cmd, _UNRESOLVED) for cmd in _VARIANTS[:-1]]
    steps.append(_shell(_VARIANTS[-1], "ok"))
    assert _should_switch_to_answer_mode(steps) is None


def test_two_failing_hosts_do_not_trip() -> None:
    steps = [_shell(cmd, _UNRESOLVED) for cmd in _VARIANTS[:-1]]
    steps.append(_shell("ssh otherhost true", "ssh: connect to host otherhost port 22"))
    assert _should_switch_to_answer_mode(steps) is None


def test_built_in_alias_keeps_its_old_behaviour() -> None:
    alias = _KNOWN_SSH_ALIASES[0]
    steps = [_shell(f"ssh {flag} {alias} hostname", "") for flag in ("-v", "-vv", "-4", "-6")]
    reason = _should_switch_to_answer_mode(steps)
    assert reason is not None
    assert f"ssh target ({alias})" in reason


@pytest.mark.parametrize(
    ("command", "host"),
    [
        ("ssh mycluster ls", "mycluster"),
        ("ssh -o ConnectTimeout=8 -p 22 Alice@MyCluster ls", "mycluster"),
        ("timeout 5 /usr/bin/ssh -l bob mycluster", "mycluster"),
        ("ssh -v", ""),
        ("echo hello", ""),
    ],
)
def test_ssh_host_parsing(command: str, host: str) -> None:
    assert _ssh_host(command) == host
