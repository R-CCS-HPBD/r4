"""TUI audit round 2 (2026-10-17), glue lane: cross-lane Python deferrals.

#20  auto-compaction announces "context compacted" only after it succeeded,
     with the kept message count.
#21  response_done reaches the TUI (it renders an un-streamed remainder);
     a round that streamed text does not send it again.
#17  the turn id reaches the TUI when the turn opens (turn_opened), so a
     cancelled or failed turn can still be rewound.
#27  get_session_plan returns only the asking session's plan rows (every
     bridge shares one sqlite file).
#48  deleting a tab's env-selected project keeps the global state.json
     selection of another project.
#18  export_session returns the exported session's model and project.
#54  reconcile_project sends the resolved project as X-Project-ID.
approval  r4 project approve judges the latest report (report_id; was 422)
     and then records the per-project readiness approval.
"""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.companion import session_turns
from r4.companion.agent import CompanionEventType
from r4.companion.task_graph import TaskGraph
from r4.companion.ui.bridge_server import CompanionBridgeServer
from r4.knowledge import KnowledgeClient
from r4.project import ProjectConfig, ProjectManager

# -- #20 ----------------------------------------------------------------------


class _Agent:
    def __init__(self) -> None:
        self.events: list[tuple[CompanionEventType, str, dict | None]] = []

    def _emit(self, event_type, message, data=None):
        self.events.append((event_type, message, data))


def _compact_ctx(compact):
    """A context manager stand-in; wave 80's turn compaction calls ``compact_turn``."""
    return SimpleNamespace(
        needs_compaction_for_payload=lambda **_kw: True,
        reuse_compaction=lambda history: history,
        compact_turn=lambda history, request=None: compact(history),
    )


def test_auto_compaction_announces_after_success_with_the_kept_count():
    agent = _Agent()
    history = [{"role": "user", "content": str(i)} for i in range(6)]
    out, error = session_turns.compact_turn_history(
        context_manager=_compact_ctx(lambda h: h[-2:]),
        agent=agent,
        user_message="q",
        extra_system="",
        history=history,
        active_tools=[],
    )
    assert error is None
    assert out == history[-2:]
    assert agent.events == [
        (CompanionEventType.CONTEXT_COMPACT, "Conversation compacted.", {"kept_messages": 2})
    ]


def test_failed_auto_compaction_stays_silent():
    from r4.companion.session_models import BudgetLimitExceededError

    def boom(_history):
        raise BudgetLimitExceededError("over budget")

    agent = _Agent()
    history = [{"role": "user", "content": "x"}]
    out, error = session_turns.compact_turn_history(
        context_manager=_compact_ctx(boom),
        agent=agent,
        user_message="q",
        extra_system="",
        history=history,
        active_tools=[],
    )
    assert out is history
    assert "Budget limit" in (error or "")
    assert agent.events == []


# -- #21 ----------------------------------------------------------------------


def _bare_server() -> CompanionBridgeServer:
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._session = SimpleNamespace(session_id="s-1", metadata={})
    server._event_log = MagicMock()
    server._emitted = []
    server._emit_line = server._emitted.append
    return server


def test_response_done_is_forwarded_to_the_tui():
    server = _bare_server()
    event = SimpleNamespace(
        event_type=CompanionEventType.RESPONSE_DONE, message="final answer", data=None
    )
    server._forward_event(event)
    assert server._emitted == [
        {"event": {"type": "response_done", "message": "final answer", "data": {}}}
    ]


def _forward(server, event_type: CompanionEventType, message: str) -> None:
    server._forward_event(SimpleNamespace(event_type=event_type, message=message, data=None))


def _emitted_types(server) -> list[str]:
    return [line["event"]["type"] for line in server._emitted if "event" in line]


def test_response_done_after_a_streamed_round_is_not_resent():
    # review: done carries the post-processed answer (tool scaffold stripped),
    # not a suffix of the stream; v3 rendered the reply twice.
    server = _bare_server()
    _forward(server, CompanionEventType.RESPONSE_START, "")
    _forward(server, CompanionEventType.RESPONSE_CHUNK, "Answer <tool_call>x</tool_call>")
    _forward(server, CompanionEventType.RESPONSE_DONE, "Answer")
    assert _emitted_types(server) == ["response_start", "response_chunk"]


def test_response_done_of_a_streamless_slice_is_forwarded():
    server = _bare_server()
    _forward(server, CompanionEventType.RESPONSE_START, "")
    _forward(server, CompanionEventType.RESPONSE_CHUNK, "Working on it.")
    _forward(server, CompanionEventType.RESPONSE_START, "")
    _forward(server, CompanionEventType.RESPONSE_DONE, "Checkpoint after 3 tool rounds.")
    assert _emitted_types(server)[-1] == "response_done"
    assert server._emitted[-1]["event"]["message"] == "Checkpoint after 3 tool rounds."


# -- #17 review: the turn id reaches the TUI when the turn opens --------------


def test_turn_start_names_the_turn_to_the_tui():
    server = _bare_server()
    server._store = MagicMock()
    server._on_turn_started("t3")
    server._store.save.assert_called_once()
    assert server._emitted == [
        {"event": {"type": "turn_opened", "message": "", "data": {"turn_id": "t3"}}}
    ]


# -- #27 ----------------------------------------------------------------------


def _plan_server(tmp_path, session_id: str) -> CompanionBridgeServer:
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._task_graph = TaskGraph(str(tmp_path / "plan.db"))
    server._plan_task_ids = {}
    server._session = SimpleNamespace(session_id=session_id)
    return server


def test_session_plan_is_scoped_to_the_session(tmp_path):
    first = _plan_server(tmp_path, "sess-a")
    first._on_plan_items([{"id": 1, "title": "a step", "status": "not-started"}])
    first.add_task({"title": "a task"})
    second = _plan_server(tmp_path, "sess-b")
    second._on_plan_items([{"id": 1, "title": "b step", "status": "completed"}])

    assert [s["title"] for s in first.get_session_plan({})["steps"]] == ["a step", "a task"]
    assert [s["title"] for s in second.get_session_plan({})["steps"]] == ["b step"]


def test_new_session_on_the_same_bridge_starts_an_empty_plan(tmp_path):
    server = _plan_server(tmp_path, "sess-a")
    server._on_plan_items([{"id": 1, "title": "old", "status": "not-started"}])
    server._session = SimpleNamespace(session_id="sess-new")
    assert server.get_session_plan({})["steps"] == []
    server._on_plan_items([{"id": 1, "title": "new", "status": "completed"}])
    steps = server.get_session_plan({})["steps"]
    assert [(s["title"], s["status"]) for s in steps] == [("new", "done")]


def test_an_existing_task_db_gains_the_session_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, parent_id TEXT, title TEXT NOT NULL"
            " DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', progress INTEGER NOT NULL"
            " DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at"
            " TEXT, notes TEXT NOT NULL DEFAULT '', depends_on TEXT NOT NULL DEFAULT '[]',"
            " child_ids TEXT NOT NULL DEFAULT '[]')"
        )
    graph = TaskGraph(str(path))
    graph.add_task("scoped", session_id="s")
    assert [t.title for t in graph.list_all_tasks(session_id="s")] == ["scoped"]


# -- #48 ----------------------------------------------------------------------


def test_deleting_the_env_project_keeps_the_global_selection(tmp_path, monkeypatch):
    pm = ProjectManager(config_dir=tmp_path)
    pm.create(ProjectConfig(project_id="tab-proj"))
    pm.create(ProjectConfig(project_id="global-proj"))
    pm.set_current("global-proj")
    monkeypatch.setenv("R4_PROJECT_ID", "tab-proj")

    pm.delete("tab-proj")

    state = json.loads((tmp_path / "state.json").read_text())
    assert state.get("current_project") == "global-proj"


def test_deleting_the_globally_selected_project_clears_it(tmp_path, monkeypatch):
    pm = ProjectManager(config_dir=tmp_path)
    pm.create(ProjectConfig(project_id="gone"))
    pm.set_current("gone")
    monkeypatch.setenv("R4_PROJECT_ID", "other")

    pm.delete("gone")

    state = json.loads((tmp_path / "state.json").read_text())
    assert "current_project" not in state


# -- #18 ----------------------------------------------------------------------


def test_export_session_returns_the_exported_sessions_model_and_project():
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    other = SimpleNamespace(
        session_id="other",
        metadata={"config": {"project_id": "OLD_PROJ"}},
        agent=SimpleNamespace(model="old-model"),
        client=MagicMock(),
        export_markdown=lambda: "# body",
    )
    server._session = SimpleNamespace(session_id="live")
    server._client = MagicMock()
    server._store = SimpleNamespace(load=lambda _sid, _client: other)
    out = server.export_session({"session_id": "other"})
    assert out["markdown"] == "# body"
    assert out["model"] == "old-model"
    assert out["project"] == "OLD_PROJ"


# -- #54 ----------------------------------------------------------------------


def test_reconcile_project_sends_the_resolved_project_header():
    client = MagicMock()
    client._api_prefix = "/gw"
    client.get.return_value = {"counts": {}}
    KnowledgeClient(client).reconcile_project("TAB_PROJ")
    assert client.get.call_args.kwargs["headers"]["X-Project-ID"] == "TAB_PROJ"


# -- approval -----------------------------------------------------------------


def test_approve_posts_the_latest_report_id():
    client = MagicMock()
    client._api_prefix = "/gw"
    client.get.return_value = {"reports": [{"report_id": "rep-new"}, {"report_id": "rep-old"}]}
    client.post.return_value = {"approved": True, "report_id": "rep-new"}
    KnowledgeClient(client).approve("P1", force=True)
    url = client.post.call_args.args[0]
    assert url.endswith("/knowledge/approve")
    assert client.post.call_args.kwargs["json"] == {"report_id": "rep-new", "force": True}


def test_approve_without_a_report_posts_nothing():
    client = MagicMock()
    client._api_prefix = "/gw"
    client.get.return_value = {"reports": []}
    out = KnowledgeClient(client).approve("P1")
    assert out["approved"] is False
    assert "r4 project verify" in out["reasons"][0]
    client.post.assert_not_called()


class _FakeKnowledge:
    def __init__(self, gate: dict) -> None:
        self.gate = gate
        self.calls: list[tuple] = []

    def approve(self, project_id, force=False):
        self.calls.append(("approve", project_id, force))
        return dict(self.gate)

    def readiness_approve(self, project_id, reason=""):
        self.calls.append(("readiness_approve", project_id, reason))
        return {"approval_id": "ap-1", "expires_at": "2026-10-24T00:00:00Z"}


def _run_approve(monkeypatch, capsys, gate: dict, **args):
    from r4 import client as client_module
    from r4._cli import project_knowledge

    knowledge = _FakeKnowledge(gate)
    monkeypatch.setattr(client_module, "Client", lambda: SimpleNamespace(knowledge=knowledge))
    monkeypatch.setattr(project_knowledge, "_resolve_project_id", lambda _a, _pm: "P1")
    project_knowledge.project_approve(Namespace(**args), MagicMock())
    return knowledge, capsys.readouterr()


def test_project_approve_records_the_readiness_approval(monkeypatch, capsys):
    knowledge, out = _run_approve(
        monkeypatch, capsys, {"approved": True}, force=False, json=True, reason="reviewed"
    )
    assert knowledge.calls == [
        ("approve", "P1", False),
        ("readiness_approve", "P1", "reviewed"),
    ]
    assert json.loads(out.out)["readiness_approval"]["approval_id"] == "ap-1"


def test_project_approve_denied_records_nothing(monkeypatch, capsys):
    knowledge, out = _run_approve(
        monkeypatch,
        capsys,
        {"approved": False, "reason": "confidence 0.40 below threshold 0.8"},
        force=False,
        json=False,
        reason="",
    )
    assert knowledge.calls == [("approve", "P1", False)]
    assert "confidence 0.40" in out.err


def test_project_approve_force_names_the_override(monkeypatch, capsys):
    knowledge, out = _run_approve(
        monkeypatch, capsys, {"approved": True}, force=True, json=False, reason=""
    )
    assert knowledge.calls[-1] == ("readiness_approve", "P1", "force override")
    assert "ap-1" in out.out


def test_project_approve_parser_takes_a_reason():
    from argparse import ArgumentParser

    from r4._cli.project import ProjectCommand

    parser = ArgumentParser()
    ProjectCommand().register(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["project", "approve", "P1", "--reason", "reviewed"])
    assert args.reason == "reviewed"


@pytest.fixture(autouse=True)
def _no_r4_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
