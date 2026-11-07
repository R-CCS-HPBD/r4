"""Companion research-loop tool tests: structured results + policy mapping."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.research_loop_tools import build_research_loop_tools


def _client_with_loops():
    client = SimpleNamespace(config=SimpleNamespace(project_id="proj-1"))
    client.start_loop = MagicMock(
        return_value={
            "loop_id": "loop-9",
            "status": "running",
            "phase": "observe",
            "research_goal": "goal",
            "current_iteration": 0,
            "max_iterations": 10,
        }
    )
    client.list_loops = MagicMock(
        return_value={
            "loops": [
                {"loop_id": "loop-9", "status": "running", "current_iteration": 0},
                {"loop_id": "loop-7", "status": "completed", "current_iteration": 10},
            ]
        }
    )
    client.get_loop_status = MagicMock(
        return_value={
            "loop_id": "loop-9",
            "status": "running",
            "phase": "observe",
            "research_goal": "goal",
            "current_iteration": 2,
            "max_iterations": 10,
            "iterations": [{"iteration": 1, "phase": "observe", "status": "completed"}],
        }
    )
    client.list_loop_iterations = MagicMock(
        return_value=[
            {
                "iteration_id": "i1",
                "iteration_number": 1,
                "phase": "observe",
                "status": "completed",
            }
        ]
    )
    client.stop_loop = MagicMock(return_value={"stopped": "loop-9"})
    client.list_loop_approvals = MagicMock(
        return_value={
            "pending": [
                {
                    "id": "appr-1",
                    "loop_id": "loop-9",
                    "iteration": 2,
                    "user_id": "alice",
                    "action": {"kind": "workflow_trigger", "workflow_name": "wf-x"},
                    "status": "pending",
                }
            ],
            "durability": "postgres",
        }
    )
    client.decide_loop_approval = MagicMock(
        return_value={
            "approval_id": "appr-1",
            "status": "approved",
            "loop_id": "loop-9",
            "durability": "postgres",
        }
    )
    client.knowledge = MagicMock()
    return client


def _client_with_knowledge():
    """Kept for name stability across tests; now backed by the loop API directly."""
    client = _client_with_loops()
    client.knowledge = MagicMock()
    return client


def _tool_map():
    return {tool["name"]: tool for tool in build_research_loop_tools(_client_with_knowledge())}


def test_registry_exposes_research_loop_crud():
    tools = _tool_map()
    assert {
        "research_loop_start",
        "research_loop_list",
        "research_loop_status",
        "research_loop_stop",
        "research_loop_approvals",
        "research_loop_decide",
        "research_session_create",
        "research_session_view",
    } == set(tools)


def test_start_loop_returns_structured_state_with_handoff_next():
    tool = _tool_map()["research_loop_start"]
    payload = json.loads(tool["execute"](research_goal="goal"))

    assert payload["type"] == "research_loop_started"
    assert payload["loop_id"] == "loop-9"
    assert payload["project_id"] == "proj-1"
    assert payload["runtime"] == "umrs"
    assert payload["durable"] is True
    assert "Workflows" in payload["next"] or "Knowledge" in payload["next"]


def test_start_loop_forwards_workflow_name_to_the_runtime():
    client = _client_with_loops()
    tools = {t["name"]: t for t in build_research_loop_tools(client)}
    tools["research_loop_start"]["execute"](research_goal="goal", workflow_name="demo08_yield_eval")
    assert client.start_loop.call_args.kwargs["workflow_name"] == "demo08_yield_eval"
    schema = tools["research_loop_start"]["parameters"]["properties"]
    assert "workflow_name" in schema and "Never invent" in schema["workflow_name"]["description"]


def test_start_loop_without_project_fails_structured(monkeypatch):
    # _project_id_for falls back to the global project registry when the
    # client config has no project — pin that fallback too, or a host/dev
    # "current project" (or one leaked by a prior suite, e.g. test_cli's
    # profile picture) silently supplies one and the premise dissolves.
    monkeypatch.setattr(
        "r4.project.get_project_manager",
        lambda: SimpleNamespace(get_current=lambda: None),
    )
    client = SimpleNamespace(
        config=SimpleNamespace(project_id=None),
        start_loop=MagicMock(),
        knowledge=MagicMock(),
    )
    tools = {t["name"]: t for t in build_research_loop_tools(client)}

    payload = json.loads(tools["research_loop_start"]["execute"](research_goal="goal"))

    assert payload["code"] == "project_missing"
    client.start_loop.assert_not_called()


def test_list_loops_marks_active_count_and_handoff():
    tool = _tool_map()["research_loop_list"]
    payload = json.loads(tool["execute"]())

    assert payload["type"] == "research_loop_list"
    assert payload["active_count"] == 1
    assert len(payload["loops"]) == 2


def test_status_includes_recent_iterations():
    tool = _tool_map()["research_loop_status"]
    payload = json.loads(tool["execute"](loop_id="loop-9"))

    assert payload["type"] == "research_loop_status"
    assert payload["recent_iterations"][0]["phase"] == "observe"


def test_approvals_tool_lists_durable_inbox_and_hands_off_to_human():
    tool = _tool_map()["research_loop_approvals"]
    payload = json.loads(tool["execute"](loop_id="loop-9"))

    assert payload["type"] == "research_loop_approvals"
    assert payload["pending"][0]["id"] == "appr-1"
    assert payload["runtime"] == "umrs"
    assert "research_loop_decide" in payload["next"]


def test_decide_tool_records_decision_and_rejects_bad_vocabulary():
    tools = _tool_map()
    payload = json.loads(
        tools["research_loop_decide"]["execute"](approval_id="appr-1", decision="once")
    )
    assert payload["type"] == "research_loop_decision"
    assert payload["status"] == "approved"
    assert payload["loop_id"] == "loop-9"

    bad = json.loads(
        tools["research_loop_decide"]["execute"](approval_id="appr-1", decision="maybe")
    )
    assert bad["code"] == "invalid_decision"


def test_decide_is_never_auto_approved_and_approvals_is_safe():
    from r4.companion.permissions import NEVER_AUTO_APPROVE, RISKY_TOOLS, SAFE_TOOLS

    assert "research_loop_decide" in NEVER_AUTO_APPROVE
    assert "research_loop_decide" in RISKY_TOOLS
    assert "research_loop_approvals" in SAFE_TOOLS


def test_stop_loop_returns_stopped_state():
    tool = _tool_map()["research_loop_stop"]
    payload = json.loads(tool["execute"](loop_id="loop-9"))

    assert payload["type"] == "research_loop_stopped"
    assert payload["loop_id"] == "loop-9"
    assert payload["status"] == "stopped"


def _wait_for_local_loop(runtime, loop_id, timeout=5.0):
    """Poll until the loop reaches a terminal state; return final state."""
    deadline = time.time() + timeout
    state = runtime.get_loop(loop_id)
    while state.status not in {"completed", "stopped", "error"} and time.time() < deadline:
        time.sleep(0.02)
        state = runtime.get_loop(loop_id)
    return state


def test_local_mode_runs_a_real_local_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("R4_RUNTIME_MODE", "local")
    monkeypatch.setenv("R4_RUNTIME_DB", str(tmp_path / "loops.db"))

    client = _client_with_loops()
    client.knowledge.get_graph = MagicMock(
        return_value={
            "nodes": [{"id": "n1", "type": "hypothesis", "name": "GOAL stuff"}],
            "edges": [],
        }
    )
    tools = {t["name"]: t for t in build_research_loop_tools(client)}

    started = json.loads(tools["research_loop_start"]["execute"](research_goal="GOAL stuff"))
    assert started["runtime"] == "local"
    assert started["loop_id"].startswith("local-")

    from r4.runtime import LocalRuntime

    state = _wait_for_local_loop(LocalRuntime(tmp_path / "loops.db"), started["loop_id"])
    assert state.status == "completed"

    listed = json.loads(tools["research_loop_list"]["execute"]())
    assert listed["runtime"] == "local"
    assert any(loop["loop_id"] == started["loop_id"] for loop in listed["loops"])

    status = json.loads(tools["research_loop_status"]["execute"](loop_id=started["loop_id"]))
    assert status["recent_iterations"][0]["summary"]["graph_nodes"] == 1


def test_permissions_classification():
    pm = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
    assert pm.is_safe("research_loop_list") is True
    assert pm.is_safe("research_loop_status") is True
    assert pm.is_safe("research_loop_start") is False
    assert pm.is_safe("research_loop_stop") is False


def test_tools_registered_under_companion_builtin_tools(monkeypatch):
    client = _client_with_knowledge()
    from r4.companion.tools import get_builtin_tools

    tools = get_builtin_tools(client)
    names = {t["name"] for t in tools}
    for expected in {
        "research_loop_start",
        "research_loop_list",
        "research_loop_status",
        "research_loop_stop",
    }:
        assert expected in names


def test_status_surfaces_parallel_tracks_from_metadata():
    """ADR-066 amendment 2 (D-34): tracks ride the status wire when the
    loop really runs tracked — never invented for plain loops."""
    from r4.runtime.api import LoopState

    state = LoopState(
        loop_id="loop-9",
        project_id="p",
        research_goal="g",
        status="running",
        phase="experiment_execution",
        metadata={
            "max_tracks": 2,
            "tracks": [
                {
                    "track_id": "track-1",
                    "hypothesis": "defect class A",
                    "turns": 3,
                    "status": "active",
                },
                {
                    "track_id": "track-2",
                    "hypothesis": "defect class B",
                    "turns": 1,
                    "status": "rejected",
                },
            ],
        },
    )
    from r4.companion.research_loop_tools import _serialize_loop

    out = _serialize_loop(state)
    assert out["max_tracks"] == 2
    assert out["tracks"][1]["status"] == "rejected"
    assert out["tracks"][0]["hypothesis"] == "defect class A"


def test_serialize_loop_without_tracks_keeps_clean_shape():
    from r4.companion.research_loop_tools import _serialize_loop
    from r4.runtime.api import LoopState

    out = _serialize_loop(
        LoopState(loop_id="l", project_id="p", research_goal="g", status="running")
    )
    assert "tracks" not in out
    assert "max_tracks" not in out
