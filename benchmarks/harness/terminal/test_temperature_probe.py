"""The temperature probe's system ablations strip the per-turn context.

Wave (80) moved ``<project_state>`` and the active skill bodies from the
system message into a ``<turn_context>`` block on the turn's user message;
an ablation that only rewrote the system message stripped nothing from a
capture made after it (review (80)). Older captures keep the old behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest

from benchmarks.harness.terminal.temperature_probe import _system_variant

_STATE = "<project_state>\nJOBS: 3 running\n</project_state>"
_SKILLS = "[Skill: hpc]\nHPC body.\n\n[Skill: code]\nCode body."
_TAIL = "[Tool limit] Only these tools run this turn: file_read."
_TURN = f"fix it\n\n<turn_context>\npreamble\n\n{_STATE}\n\n{_SKILLS}\n\n{_TAIL}\n</turn_context>"


def _body(system: str, user: str) -> dict[str, Any]:
    return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}


def _user(body: dict[str, Any]) -> str:
    return body["messages"][-1]["content"]


@pytest.mark.parametrize("mode", ["noproj", "lean"])
def test_project_state_is_stripped_from_the_turn_block(mode):
    out = _system_variant(_body("SYSTEM", _TURN), mode)
    assert "<project_state>" not in _user(out)
    assert _user(out).startswith("fix it\n\n<turn_context>")
    assert _user(out).endswith(f"{_TAIL}\n</turn_context>")


def test_lean_strips_the_skill_bodies_from_the_turn_block():
    user = _user(_system_variant(_body("SYSTEM", _TURN), "lean"))
    assert "HPC body." not in user and "Code body." not in user
    assert _TAIL in user


def test_noplatskills_strips_only_platform_skills_from_the_turn_block():
    user = _user(_system_variant(_body("SYSTEM", _TURN), "noplatskills"))
    assert "HPC body." not in user
    assert "Code body." in user and _TAIL in user


def test_core_drops_the_turn_block():
    out = _system_variant(_body("SYSTEM", _TURN), "core")
    assert _user(out) == "fix it"


def test_old_captures_still_strip_the_system_message():
    old = _system_variant(_body(f"BASE\n\n{_STATE}\n\n{_SKILLS}", "fix it"), "lean")
    assert old["messages"][0]["content"].strip() == "BASE"
    assert _user(old) == "fix it"
