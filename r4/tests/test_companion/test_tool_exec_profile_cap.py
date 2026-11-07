"""Exec output cap under the terminal-bench profile (TB 4.0 plan, item H11, shell part).

Only with ``R4_AGENT_PROFILE=terminal`` is shell/python output capped at
``agent_profile.SHELL_OUTPUT_CAP`` (16,000 chars when that module or constant
is absent), split 10,000 head + 6,000 tail. Product defaults, interactive or
``R4_AUTONOMOUS=1``, keep 32,000 = 20,000 + 8,000 and the same descriptions.
"""

from __future__ import annotations

import sys
import types

import pytest

import r4.companion as companion_pkg
from r4.companion import tools
from r4.companion.tools import PYTHON_EXEC_DESCRIPTION, SHELL_EXEC_DESCRIPTION, _cap_exec_output

_MODULE = "r4.companion.agent_profile"


@pytest.fixture
def no_profile_module(monkeypatch):
    """Make ``from . import agent_profile`` fail, as before lane L3 lands."""
    monkeypatch.setitem(sys.modules, _MODULE, None)
    monkeypatch.delattr(companion_pkg, "agent_profile", raising=False)


@pytest.fixture
def fake_profile_module(monkeypatch):
    fake = types.ModuleType(_MODULE)
    fake.SHELL_OUTPUT_CAP = 12_000
    monkeypatch.setitem(sys.modules, _MODULE, fake)
    monkeypatch.setattr(companion_pkg, "agent_profile", fake, raising=False)
    return fake


def _split(capped: str) -> tuple[str, str]:
    head, tail = capped.split("\n…[truncated ", 1)
    return head, tail.split("]…\n", 1)[1]


def test_terminal_profile_caps_30k_at_16k(monkeypatch, no_profile_module):
    """Old code: 30,000 chars passed uncut (cap 32,000)."""
    monkeypatch.setenv("R4_AGENT_PROFILE", "terminal")
    text = "h" * 10_000 + "m" * 14_000 + "t" * 6_000
    head, tail = _split(_cap_exec_output(text))
    assert (head, tail) == ("h" * 10_000, "t" * 6_000)
    assert "…[truncated 14000 chars]…" in _cap_exec_output(text)


def test_terminal_profile_reads_agent_profile_cap(monkeypatch, fake_profile_module):
    monkeypatch.setenv("R4_AGENT_PROFILE", "terminal")
    assert tools.exec_output_limits() == (12_000, 7_500, 4_500)


def test_bad_profile_cap_falls_back(monkeypatch, fake_profile_module):
    monkeypatch.setenv("R4_AGENT_PROFILE", "terminal")
    fake_profile_module.SHELL_OUTPUT_CAP = "big"
    assert tools.exec_output_limits()[0] == tools.TERMINAL_PROFILE_OUTPUT_CAP_CHARS


@pytest.mark.parametrize("autonomous", ["", "1"])
def test_product_defaults_keep_the_32k_cap(monkeypatch, fake_profile_module, autonomous):
    monkeypatch.delenv("R4_AGENT_PROFILE", raising=False)
    monkeypatch.setenv("R4_AUTONOMOUS", autonomous)
    assert tools.exec_output_limits() == (32_000, 20_000, 8_000)
    text = "x" * 30_000
    assert _cap_exec_output(text) == text


@pytest.mark.parametrize(("autonomous", "detach"), [("", ""), ("1", "0")])
def test_product_default_descriptions_are_byte_identical(monkeypatch, autonomous, detach):
    monkeypatch.delenv("R4_AGENT_PROFILE", raising=False)
    monkeypatch.setenv("R4_AUTONOMOUS", autonomous)
    monkeypatch.setenv("R4_SHELL_DETACH_ON_TIMEOUT", detach)
    assert tools.shell_exec_description() == SHELL_EXEC_DESCRIPTION
    assert tools.python_exec_description() == PYTHON_EXEC_DESCRIPTION
    assert "detached" not in SHELL_EXEC_DESCRIPTION
    assert "the whole process group is killed" in SHELL_EXEC_DESCRIPTION


def test_detach_on_describes_the_detach_not_the_kill(monkeypatch):
    """Review 2026-10-24: under detach the description said the group is killed
    and named a detach for users who never get one."""
    monkeypatch.delenv("R4_AGENT_PROFILE", raising=False)
    monkeypatch.setenv("R4_AUTONOMOUS", "1")
    monkeypatch.delenv("R4_SHELL_DETACH_ON_TIMEOUT", raising=False)
    text = tools.shell_exec_description()
    assert "the command is detached, not killed" in text
    assert "process group is killed" not in text
    assert "process group is killed" in tools.python_exec_description()


def test_terminal_profile_descriptions_quote_its_cap(monkeypatch, no_profile_module):
    monkeypatch.setenv("R4_AGENT_PROFILE", "terminal")
    assert "Output over 16,000 chars is cut to the first 10,000 + last 6,000" in (
        tools.shell_exec_description()
    )
    assert "16,000" in tools.python_exec_description()


def test_terminal_profile_shell_exec_is_capped_end_to_end(monkeypatch, no_profile_module):
    tools.set_shell_chunk_sink(None)
    monkeypatch.setenv("R4_AGENT_PROFILE", "terminal")
    out = tools._shell_exec("head -c 20000 /dev/zero | tr '\\0' z", timeout=30)
    assert "…[truncated 4000 chars]…" in out
