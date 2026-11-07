"""Web tools under a skill limit (2026-10-30).

Only the ``research`` skill listed ``web_fetch``/``web_search``, so in a
session with web access ``public`` the skill filter refused them on almost
every turn ("the active skills limit this turn's tools"). A web tool the
session's choice lets run is now outside the skill limit: ``web_fetch`` with
allowlist or public access, ``web_search`` with public access.
"""

from __future__ import annotations

import pytest

from r4.companion.session_turns import active_tools
from r4.companion.turn_context import SKILL_REFUSAL
from r4.companion.web_access import WebAccess

from .offline_session import first_request

# "fix" and "script" activate the code skill, whose tool list has no web tool.
_CODING_TASK = "Fix the bug in my script."


@pytest.fixture(autouse=True)
def _interactive(monkeypatch):
    for name in (
        "R4_AUTONOMOUS",
        "R4_AGENT_PROFILE",
        "R4_WEB_ACCESS",
        "R4_COMPANION_PROMPT_HYGIENE",
    ):
        monkeypatch.delenv(name, raising=False)
    # A search backend, so the gate offers web_search (nothing calls it).
    monkeypatch.setenv("R4_WEB_SEARCH_URL", "https://searx.example.org")


def _web_tools(mode: str) -> list[dict]:
    return [
        {"name": name, "web_access": lambda: WebAccess(mode=mode)}
        for name in ("web_fetch", "web_search")
    ]


@pytest.mark.parametrize(
    ("mode", "kept"),
    [
        ("public", ["web_fetch", "web_search"]),
        ("allowlist", ["web_fetch"]),
        ("none", []),
        ("", []),
    ],
)
def test_skill_filter_keeps_the_web_tools_the_choice_allows(mode, kept):
    tools = active_tools(tools=_web_tools(mode), active_tool_names={"file_read"})
    assert [tool["name"] for tool in tools] == kept


def test_a_tool_without_the_sessions_getter_follows_the_skill():
    assert active_tools(tools=[{"name": "web_fetch"}], active_tool_names={"file_read"}) == []


def _refusals(monkeypatch, tmp_path, metadata=None) -> dict:
    _, names, session = first_request(monkeypatch, tmp_path, task=_CODING_TASK, metadata=metadata)
    assert "code" in {skill.name for skill in session.skills.list_active()}
    assert {"web_fetch", "web_search"} <= set(names)
    return session._turn_refusals


def test_public_session_does_not_refuse_web_tools_under_a_skill(monkeypatch, tmp_path):
    public = {"web_access": {"mode": "public", "domains": [], "source": "prompt"}}
    refusals = _refusals(monkeypatch, tmp_path, public)
    assert "web_fetch" not in refusals
    assert "web_search" not in refusals


def test_session_without_web_access_keeps_the_skill_refusal(monkeypatch, tmp_path):
    refusals = _refusals(monkeypatch, tmp_path)
    assert refusals.get("web_fetch") == SKILL_REFUSAL
    assert refusals.get("web_search") == SKILL_REFUSAL
