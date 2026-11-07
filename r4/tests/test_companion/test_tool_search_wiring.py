"""G18 (2026-10-28): the tool_search meta-tool gets a real backend.

Searching ranks session-catalog tools against a natural-language query
(whole-word terms — the skills matching fix), returns argument schemas, and
opts the discovered tools out of the skill refusal set from the next turn.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import r4.agent.tools as agent_tools
from r4.companion.tool_search import register_session_tool_search, search_catalog

CATALOG: list[dict[str, Any]] = [
    {
        "name": "hpc_submit",
        "description": "Submit a Slurm or PBS job to a configured HPC site.",
        "parameters": {"type": "object", "properties": {"script": {"type": "string"}}},
    },
    {
        "name": "hpc_job_status",
        "description": "Poll the status of a submitted HPC job.",
        "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}},
    },
    {
        "name": "file_read",
        "description": "Read a file from the workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
    {
        "name": "profile_tool",
        "description": "Profile a run's execution timeline.",
        "parameters": {"type": "object", "properties": {}},
    },
]


@pytest.fixture(autouse=True)
def _restore_tool_search_callback():
    previous = agent_tools._tool_search_fn
    yield
    agent_tools._tool_search_fn = previous


def test_search_ranks_the_capability_first() -> None:
    hits = search_catalog(CATALOG, "submit a job to slurm")
    assert hits[0]["name"] == "hpc_submit"
    assert {hit["name"] for hit in hits[:2]} == {"hpc_submit", "hpc_job_status"}
    assert hits[0]["parameters"]["properties"]["script"]


def test_search_is_whole_word(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R4_TOOL_SEARCH_LIMIT", "10")
    hits = search_catalog(CATALOG, "file")
    assert {hit["name"] for hit in hits} == {"file_read"}


def test_search_empty_query_and_limit() -> None:
    assert search_catalog(CATALOG, "") == []
    assert search_catalog(CATALOG, "!!!") == []
    assert len(search_catalog(CATALOG, "job hpc status", limit=1)) == 1


def _session() -> SimpleNamespace:
    return SimpleNamespace(tools=list(CATALOG), tool_search_opt_ins=set())


def test_registration_records_opt_ins() -> None:
    session = _session()
    search = register_session_tool_search(session)
    first = search("submit slurm job")
    assert [hit["name"] for hit in first][:1] == ["hpc_submit"]
    assert all(hit["activated_for_next_turn"] for hit in first)
    assert "hpc_submit" in session.tool_search_opt_ins
    second = search("submit slurm job")
    assert not any(hit["activated_for_next_turn"] for hit in second)


def test_registered_callback_serves_the_meta_tool() -> None:
    register_session_tool_search(_session())
    assert agent_tools._tool_search_fn is not None
    result = agent_tools._handle_tool_search({"query": "poll job status"})
    assert "hpc_job_status" in result.output


def test_session_construction_wires_the_callback(monkeypatch, tmp_path) -> None:
    import r4.companion.model_resolution as model_resolution
    from r4.companion.permissions import PermissionMode
    from r4.companion.session import CompanionSession

    from .offline_session import OfflineClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("R4_HARNESS_ABLATE_MEMORY", "1")
    monkeypatch.setattr(model_resolution, "ensure_model_ready", lambda *_a, **_k: None)
    session = CompanionSession(
        session_id="offline",
        name="offline",
        client=OfflineClient(),
        model="test-model",
        permission_mode=PermissionMode.TRUST,
        endpoint_refresh_interval_sec=86_400,
    )
    assert agent_tools._tool_search_fn is not None
    assert session.tool_search_opt_ins == set()
    hits = agent_tools._tool_search_fn("poll the status of a submitted hpc job")
    assert hits[0]["name"] == "hpc_job_status"
    assert "hpc_job_status" in session.tool_search_opt_ins


def test_opted_tools_leave_the_skill_refusal_set(monkeypatch, tmp_path) -> None:
    from .offline_session import first_request

    task = "fix the bug in the training script and rerun the test suite"
    _, _, session = first_request(monkeypatch, tmp_path, task=task)
    refusals = session.agent._turn_refusals
    assert "mcp_register_server" in refusals  # outside this turn's skill set
    session.tool_search_opt_ins.add("mcp_register_server")
    session.send("another look, same task")
    refusals_after = session.agent._turn_refusals
    assert "mcp_register_server" not in refusals_after
