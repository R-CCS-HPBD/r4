"""Pins for the teamup_conclusions read tool (2026-09-29).

The authoritative answer to "what did the team conclude" lives in the team's
own ChangeLog — RAG/search index documents, not team iteration outcomes.
Also pins the persisted RESPONSE bound (1200, aligned with the live rail).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import r4.companion.tools as tools_mod
import r4.teamup.session as session_module
from r4.teamup.comms import AgentCommunication
from r4.teamup.session import TeamupSession, TeamupSessionConfig


def _find_tool(name: str, client: SimpleNamespace) -> dict:
    tools = tools_mod._teamup_tools(client, session_model="m")
    match = [t for t in tools if t["name"] == name]
    assert match, f"tool {name} not registered"
    return match[0]


def _session_with_work(tmp_path, monkeypatch) -> TeamupSession:
    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)
    session = TeamupSession.create(TeamupSessionConfig(name="conclusion-team"), SimpleNamespace())
    comms = AgentCommunication(session.session_dir / "agent-shared")
    comms.add_entry_sync(
        agent="PM", action="RESPONSE", description="Plan: sweep three ridge variants."
    )
    comms.add_entry_sync(
        agent="PM", action="NOTE", description="intermediate scratch — never a conclusion"
    )
    comms.add_entry_sync(
        agent="PG", action="RESPONSE", description="Implemented variant B; harness green."
    )
    return session


def _answers_of(parsed: dict, role: str) -> list[str]:
    by_role = {c["role"]: c for c in parsed["conclusions"]}
    return [a["answer"] for a in by_role[role]["answers"]]


def _assert_notes_are_not_conclusions(pm_answers: list[str]) -> None:
    assert any("sweep three ridge variants" in a for a in pm_answers)
    assert not any("intermediate scratch" in a for a in pm_answers)  # NOTE ≠ conclusion


def test_conclusions_returns_each_roles_persisted_answers(tmp_path, monkeypatch) -> None:
    session = _session_with_work(tmp_path, monkeypatch)
    tool = _find_tool("teamup_conclusions", SimpleNamespace())

    parsed = json.loads(tool["execute"](session_id=session.session_id))

    assert parsed["name"] == "conclusion-team"
    _assert_notes_are_not_conclusions(_answers_of(parsed, "pm"))
    assert any("harness green" in answer for answer in _answers_of(parsed, "pg"))
    assert "no RAG needed" in parsed["note"]


def test_conclusions_names_unknown_sessions_honestly(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(session_module, "TEAMUP_SESSIONS_DIR", tmp_path)
    tool = _find_tool("teamup_conclusions", SimpleNamespace())
    result = tool["execute"](session_id="no-such-session")
    assert "Error loading conclusions" in result


def test_description_marks_it_authoritative_over_rag(tmp_path, monkeypatch) -> None:
    tool = _find_tool("teamup_conclusions", SimpleNamespace())
    description = tool["description"].lower()
    assert "authoritative" in description
    assert "rag" in description  # explicitly steers the agent away from RAG here


@pytest.mark.parametrize("final_len", [2500])
def test_response_persistence_bound_matches_the_live_rail(
    tmp_path, monkeypatch, final_len: int
) -> None:
    """ChangeLog RESPONSE keeps 1200 chars + honesty marker, like the live rail."""
    from role_fixtures import chat_once_factory

    session = _session_with_work(tmp_path, monkeypatch)
    final_text = "z" * final_len

    def chat(**_kwargs):
        return SimpleNamespace(content=final_text)

    client = SimpleNamespace(chat=chat)
    session._subcompanion_factory = chat_once_factory(client)
    session._run_turns_sync()

    responses = [
        e
        for e in session.comms.get_agent_entries("PM")
        if e.action == "RESPONSE" and e.description.startswith("zzz")
    ]
    assert responses, "the long final answer must persist as a RESPONSE entry"
    description = responses[-1].description
    assert len(description) == 1203  # 1200 + "..."
    assert description.endswith("...")
