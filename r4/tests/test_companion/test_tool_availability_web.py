"""H15 (TB 4.0 harness plan, 2026-10-24): a web tool the session's choice
refuses is not offered.

``web_fetch`` refused every call in every TB 4.0 trial (web access unset),
yet stayed in the offered schemas. In an unattended run (``R4_AUTONOMOUS=1``
or the terminal profile) the availability gate now reads the session's choice
each turn: ``web_fetch`` is offered with allowlist or public access,
``web_search`` with public access only (the guard's own rules). Interactive
sessions keep both (review 2026-10-24): the refusal names the user's choice and
``/web``, which a silently missing tool does not.
``R4_COMPANION_PROMPT_HYGIENE=0`` offers them again.
"""

from __future__ import annotations

import pytest

from r4.companion import tool_availability
from r4.companion.tool_availability import unavailable_reason
from r4.companion.web_access import WebAccess

from .offline_session import first_request

# No skill keyword in it: every builtin tool is offered (no skill filter).
_PLAIN_TASK = "Hello there."


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    for name in ("R4_COMPANION_PROMPT_HYGIENE", "R4_AGENT_PROFILE", "R4_WEB_ACCESS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("R4_AUTONOMOUS", "1")


def _tool(name: str, mode: str) -> dict:
    # The key the session sets (tool_availability.WEB_ACCESS_KEY).
    return {"name": name, "web_access": lambda: WebAccess(mode=mode)}


def test_session_getter_key():
    assert tool_availability.WEB_ACCESS_KEY == "web_access"


@pytest.mark.parametrize("mode", ["", "none"])
def test_web_tools_unavailable_while_access_is_off(mode):
    assert unavailable_reason(_tool("web_fetch", mode), None)
    assert unavailable_reason(_tool("web_search", mode), None)


def test_allowlist_offers_fetch_but_not_search():
    assert unavailable_reason(_tool("web_fetch", "allowlist"), None) is None
    assert unavailable_reason(_tool("web_search", "allowlist"), None)


def test_public_offers_fetch():
    assert unavailable_reason(_tool("web_fetch", "public"), None) is None


def test_a_tool_without_the_sessions_getter_is_not_judged():
    """Team agents and recipes build tools without the session's getter."""
    assert unavailable_reason({"name": "web_fetch"}, None) is None


def test_session_does_not_offer_web_fetch_while_access_is_off(monkeypatch, tmp_path):
    _, names, _ = first_request(monkeypatch, tmp_path, task=_PLAIN_TASK)
    assert "shell_exec" in names
    assert "web_fetch" not in names


def test_interactive_session_keeps_the_refusing_web_tools(monkeypatch, tmp_path):
    monkeypatch.delenv("R4_AUTONOMOUS")
    assert unavailable_reason(_tool("web_fetch", "none"), None) is None
    _, names, _ = first_request(monkeypatch, tmp_path, task=_PLAIN_TASK)
    assert "web_fetch" in names


def test_terminal_profile_counts_as_unattended(monkeypatch):
    monkeypatch.delenv("R4_AUTONOMOUS")
    monkeypatch.setenv("R4_AGENT_PROFILE", "terminal")
    assert unavailable_reason(_tool("web_fetch", "none"), None)


def test_session_offers_web_fetch_after_the_user_allows_the_web(monkeypatch, tmp_path):
    public = {"web_access": {"mode": "public", "domains": [], "source": "prompt"}}
    _, names, _ = first_request(monkeypatch, tmp_path, task=_PLAIN_TASK, metadata=public)
    assert "web_fetch" in names


def test_off_switch_offers_web_fetch_again(monkeypatch, tmp_path):
    monkeypatch.setenv("R4_COMPANION_PROMPT_HYGIENE", "0")
    _, names, _ = first_request(monkeypatch, tmp_path, task=_PLAIN_TASK)
    assert "web_fetch" in names
