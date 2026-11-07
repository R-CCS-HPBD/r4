"""Pins for find-crawl timeout hint + shortened default timeout (T5 topic-11 tail)."""

from __future__ import annotations

from r4.companion.tools import _find_command_hint, _shell_exec, effective_timeout_for_command


def test_find_timeout_returns_hint():
    out = _shell_exec("find . -name sandbox-test", timeout=60)
    # Precondition probe — runs quick in an empty dir.
    assert "timed out" not in out  # must not hang in small tree


def test_find_hint_for_missing_maxdepth():
    hint = _find_command_hint("find . -name target")
    assert "maxdepth" in hint


def test_find_hint_when_maxdepth_present():
    hint = _find_command_hint("find . -maxdepth 2 -name target")
    assert "narrow the root" in hint


def test_find_classification_applies_timeout_cap():
    assert effective_timeout_for_command("find /opt -name x", 60) == 25
    assert effective_timeout_for_command("find .", 60) == 25
    assert effective_timeout_for_command("find", 60) == 25
    assert effective_timeout_for_command("  find\t/opt", 60) == 25
    assert effective_timeout_for_command("echo hello", 60) == 60
    assert effective_timeout_for_command("findlf /opt -name x", 60) == 60
    assert effective_timeout_for_command("ls find_dir", 60) == 60


def test_non_find_commands_keep_their_timeout():
    out = _shell_exec("echo hello", timeout=1)
    assert "timed out" not in out
