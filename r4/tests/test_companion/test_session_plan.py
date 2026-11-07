"""Session plan mirror — todo_write items land in the session TaskGraph and
the bridge serves them as a structured plan (steps + dependency edges) for
the TUI's plan graph view.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from r4.agent import tools as agent_tools
from r4.companion.task_graph import TaskGraph
from r4.companion.ui.bridge_server import CompanionBridgeServer


@pytest.fixture()
def server():
    srv = CompanionBridgeServer.__new__(CompanionBridgeServer)
    with tempfile.TemporaryDirectory() as tmp:
        srv._task_graph = TaskGraph(str(Path(tmp) / "plan.db"))
        srv._plan_task_ids = {}
        yield srv


def teardown_function() -> None:
    # The mirror is process-global; leaving a bound server leaking into
    # other suites would poison their todo_write calls.
    agent_tools.set_plan_mirror(None)


def _items(*rows: tuple[int, str, str]) -> list[dict]:
    return [{"id": i, "title": t, "status": s} for i, t, s in rows]


def test_todo_write_mirrors_into_task_graph(server):
    agent_tools.set_plan_mirror(server._on_plan_items)
    result = agent_tools._handle_todo_write(
        {
            "items": _items(
                (1, "Survey papers", "completed"),
                (2, "Patch code", "in-progress"),
                (3, "Run tests", "not-started"),
            )
        }
    )
    assert "Todo list updated" in result.output
    steps = server.get_session_plan({})["steps"]
    assert [s["title"] for s in steps] == ["Survey papers", "Patch code", "Run tests"]
    assert [s["status"] for s in steps] == ["done", "active", "pending"]


def test_plan_steps_form_a_dependency_chain_in_order(server):
    server._on_plan_items(
        _items(
            (1, "first", "not-started"),
            (2, "second", "not-started"),
            (3, "third", "not-started"),
        )
    )
    steps = server.get_session_plan({})["steps"]
    assert steps[0]["depends_on"] == []
    assert steps[1]["depends_on"] == [steps[0]["id"]]
    assert steps[2]["depends_on"] == [steps[1]["id"]]


def test_full_rewrite_updates_status_without_duplicating_steps(server):
    server._on_plan_items(_items((1, "only step", "not-started")))
    server._on_plan_items(_items((1, "only step", "completed")))
    steps = server.get_session_plan({})["steps"]
    assert len(steps) == 1
    assert steps[0]["status"] == "done"
    assert steps[0]["progress"] == 100


def test_mirror_never_breaks_the_tool_result(server):
    def _boom(_items):
        raise RuntimeError("mirror exploded")

    agent_tools.set_plan_mirror(_boom)
    result = agent_tools._handle_todo_write({"items": _items((1, "x", "completed"))})
    assert "Todo list updated" in result.output


def test_malformed_items_are_skipped(server):
    server._on_plan_items([{"id": 1, "title": "", "status": "completed"}, "junk", 5])
    assert server.get_session_plan({})["steps"] == []


def test_rpc_registered_and_allowlisted(server):
    from r4.companion.ui import bridge_server as bs

    assert "get_session_plan" in bs.BRIDGE_RPC_NAMES
    bindings = bs._bridge_rpc_bindings(server)
    assert bindings["get_session_plan"]({}) == {"steps": []}
