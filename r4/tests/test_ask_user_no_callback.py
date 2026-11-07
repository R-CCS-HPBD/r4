"""ask_user with no UI callback honours ``ask_user_mode`` (TB 4.0 plan, item H12).

The bridge and the venue worker already auto-answer when the user's
``ask_user_mode`` is ``auto`` (``R4_ASK_USER_MODE``); the unwired path (headless
runs such as the terminal-bench driver) returned a dead-end text instead.
"""

from __future__ import annotations

import pytest

from r4.agent import tools as agent_tools
from r4.companion.agent_settings import ASK_USER_AUTO_ANSWER


@pytest.fixture(autouse=True)
def _no_callback(monkeypatch):
    monkeypatch.setattr(agent_tools, "_ask_user_callback", None)
    monkeypatch.delenv("R4_ASK_USER_MODE", raising=False)


def _ask() -> str:
    return agent_tools._handle_ask_user({"question": "Which file?", "options": ["a", "b"]}).output


def test_auto_mode_answers_without_a_callback(monkeypatch):
    """Old code: '[user input not available — ask_user callback not wired]'."""
    monkeypatch.setenv("R4_ASK_USER_MODE", "auto")
    assert _ask() == ASK_USER_AUTO_ANSWER


def test_unset_mode_keeps_the_old_text():
    assert _ask() == "[user input not available — ask_user callback not wired]"


def test_ask_mode_keeps_the_old_text(monkeypatch):
    monkeypatch.setenv("R4_ASK_USER_MODE", "ask")
    assert _ask() == "[user input not available — ask_user callback not wired]"


def test_a_wired_callback_still_answers(monkeypatch):
    monkeypatch.setenv("R4_ASK_USER_MODE", "auto")
    monkeypatch.setattr(agent_tools, "_ask_user_callback", lambda q, opts: f"picked {opts[0]}")
    assert _ask() == "picked a"
