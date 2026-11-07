"""TUI audit round 2 (2026-10-17), python lane: bridge + session store.

#20  context_compact events reach the TUI (were filtered by _SURFACED_EVENTS).
#60  a fork of a frontend session runs locally (venue/runtime were copied).
#17  session.truncate cuts the companion history a rewind discards, keyed
     on the turn id send_message returns (not a count: /compact and /retry
     change the session's turn count); /compact keeps the kept tail's ids
     and /retry reports the turn ids it dropped.
followup  slash commands that start an agent turn hand the prompt back as
     result["follow_up_messages"] instead of running the turn inline.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.companion.agent import CompanionEventType
from r4.companion.session_models import SessionMessage
from r4.companion.session_store import CompanionSessionStore
from r4.companion.ui import command_bridge
from r4.companion.ui.bridge_server import (
    BRIDGE_RPC_NAMES,
    CollectingRenderer,
    CompanionBridgeServer,
    _bridge_rpc_bindings,
)
from r4.companion.ui.command_bridge import CompanionCommandApp

# -- #20: context_compact is surfaced ---------------------------------------


def _bare_server() -> CompanionBridgeServer:
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._session = SimpleNamespace(session_id="s-1", metadata={})
    server._event_log = MagicMock()
    server._emitted = []
    server._emit_line = server._emitted.append
    return server


def test_context_compact_event_is_forwarded_to_the_tui():
    server = _bare_server()
    event = SimpleNamespace(
        event_type=CompanionEventType.CONTEXT_COMPACT,
        message="Compacting conversation…",
        data=None,
    )
    server._forward_event(event)
    lines = [line for line in server._emitted if "event" in line]
    assert lines == [
        {"event": {"type": "context_compact", "message": "Compacting conversation…", "data": {}}}
    ]


def test_unsurfaced_events_stay_filtered():
    server = _bare_server()
    event = SimpleNamespace(
        event_type=CompanionEventType.THINKING, message="Processing your message…", data={}
    )
    server._forward_event(event)
    assert server._emitted == []


# -- #60: fork of a frontend session is local --------------------------------


def _seed_frontend_session(store: CompanionSessionStore, session_id: str) -> None:
    session = MagicMock()
    session.session_id = session_id
    session.name = "remote"
    session.created_at = "2026-10-17T10:00:00+00:00"
    session.updated_at = "2026-10-17T10:00:00+00:00"
    session.client = MagicMock()
    session.client.config = MagicMock(profile="default", gateway_url="", project_id="")
    session.messages = [MagicMock(role="user", content="hello", timestamp="t", metadata={})]
    session.skills._active = set()
    session.context._memory = {}
    session.metadata = {"config": {}}
    session.agent.model = "m"
    session._sync_metadata_usage_state = lambda: None
    store.save(session)
    remote = {
        "config": {},
        "venue": "frontend",
        "venue_runtime": {"state": "stopped"},
        "state": "stopped",
        "chat_queue": ["queued for the remote lane"],
        "permission_override": "trust",
    }
    with store._conn() as conn:
        conn.execute(
            "UPDATE sessions SET metadata = ? WHERE id = ?", (json.dumps(remote), session_id)
        )


def _row_metadata(store: CompanionSessionStore, session_id: str) -> dict:
    with store._conn() as conn:
        row = conn.execute("SELECT metadata FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return json.loads(row[0])


def test_fork_of_a_frontend_session_runs_locally(tmp_path):
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    _seed_frontend_session(store, "sess-remote")
    assert store.fork("sess-remote", "sess-local") is not None
    fork = _row_metadata(store, "sess-local")
    assert fork["venue"] == "local"
    for key in ("venue_runtime", "chat_queue", "state"):
        assert key not in fork
    assert fork["forked_from"] == "sess-remote"
    assert fork["permission_override"] == "trust"  # unrelated metadata survives
    parent = _row_metadata(store, "sess-remote")
    assert parent["venue"] == "frontend" and parent["venue_runtime"] == {"state": "stopped"}


def test_forked_frontend_session_is_not_routed_remote(tmp_path):
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    _seed_frontend_session(store, "sess-remote")
    store.fork("sess-remote", "sess-local")
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._session = SimpleNamespace(metadata=_row_metadata(store, "sess-local"))
    assert server._session_is_frontend() is False


# -- #17: session.truncate ---------------------------------------------------


def _msg(role: str, content: str, turn: str = "") -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content, metadata={"turn_id": turn} if turn else {})


def _turn_messages() -> list[SimpleNamespace]:
    return [
        _msg("user", "u1", "t1"),
        _msg("assistant", "a1", "t1"),
        _msg("user", "u2", "t2"),
        _msg("tool", "t2"),
        _msg("assistant", "a2", "t2"),
        _msg("user", "u3", "t3"),
        _msg("assistant", "a3", "t3"),
    ]


def _truncate_server(metadata: dict | None = None) -> CompanionBridgeServer:
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._session = SimpleNamespace(
        session_id="s-1", metadata=metadata or {}, messages=_turn_messages()
    )
    server._store = MagicMock()
    server.snapshot = lambda: {"messages": "snap"}
    return server


def test_session_truncate_is_a_bridge_verb():
    bindings = _bridge_rpc_bindings(CompanionBridgeServer.__new__(CompanionBridgeServer))
    assert "session.truncate" in BRIDGE_RPC_NAMES and "session.truncate" in bindings


def test_session_truncate_drops_the_rewound_turn_and_saves():
    server = _truncate_server()
    out = server.session_truncate({"turn_id": "t2"})
    assert [m.content for m in server._session.messages] == ["u1", "a1"]
    assert out["ok"] is True and out["removed"] == 5 and out["messages"] == 2
    server._store.save.assert_called_once_with(server._session)


def test_session_truncate_at_the_first_turn_empties_history():
    server = _truncate_server()
    server.session_truncate({"turn_id": "t1"})
    assert server._session.messages == []


def test_session_truncate_cuts_by_turn_id_not_by_position():
    """After /compact or /retry the session holds fewer user turns than the
    transcript; a head count would cut the wrong turn or nothing at all."""
    server = _truncate_server()
    server._session.messages = [
        _msg("system", "[Conversation summary]\nolder turns"),
        _msg("user", "u7", "t7"),
        _msg("assistant", "a7", "t7"),
        _msg("user", "u8", "t8"),
        _msg("assistant", "a8", "t8"),
    ]
    server.session_truncate({"turn_id": "t8"})
    assert [m.content for m in server._session.messages][-2:] == ["u7", "a7"]


@pytest.mark.parametrize("turn_id", ["t9", "t0"])
def test_session_truncate_of_a_missing_turn_is_a_desync_error(turn_id):
    server = _truncate_server()
    with pytest.raises(ValueError, match="rewind.desync"):
        server.session_truncate({"turn_id": turn_id})
    assert len(server._session.messages) == 7
    server._store.save.assert_not_called()


def test_session_truncate_needs_the_user_row_of_the_turn():
    """A turn whose user message was summarised away cannot be cut cleanly."""
    server = _truncate_server()
    del server._session.messages[2]  # u2 gone, a2 (t2) kept
    with pytest.raises(ValueError, match="rewind.desync"):
        server.session_truncate({"turn_id": "t2"})


@pytest.mark.parametrize("params", [{}, {"turn_id": ""}, {"keep_user_turns": 1}])
def test_session_truncate_requires_a_turn_id(params):
    server = _truncate_server()
    with pytest.raises(ValueError, match="turn_id is required"):
        server.session_truncate(params)
    assert len(server._session.messages) == 7


def test_session_truncate_refuses_a_frontend_session():
    server = _truncate_server({"venue": "frontend"})
    with pytest.raises(ValueError, match="venue.remote"):
        server.session_truncate({"turn_id": "t2"})
    assert len(server._session.messages) == 7
    server._store.save.assert_not_called()


def test_send_message_result_names_the_turn_it_opened():
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._session = SimpleNamespace(_current_turn_id="t4")
    server.snapshot = lambda: {}
    assert server._turn_result("ok", False, "t3")["turn_id"] == "t4"
    assert "turn_id" not in server._turn_result("ok", False, "t4")


def test_compact_keeps_turn_ids_on_the_kept_tail(monkeypatch):
    from r4.companion import session_ops

    session = SimpleNamespace(
        messages=[
            SessionMessage(role="system", content="sys"),
            *[
                SessionMessage(role=r, content=f"{r[0]}{n}", metadata={"turn_id": f"t{n}"})
                for n in range(1, 6)
                for r in ("user", "assistant")
            ],
        ],
        agent=None,
    )
    summary = {"role": "system", "content": "[Conversation summary]\nolder"}

    def compact(payload, keep_recent):
        return [payload[0], summary, *payload[-keep_recent:]]

    session.context = SimpleNamespace(compact=compact)
    monkeypatch.setattr(session_ops, "_emit_compact_mark", lambda *_args: None)
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._session = session
    server._store = MagicMock()
    server.snapshot = lambda: {}
    out = server.session_compact({"keep_recent": 4})
    assert out["changed"] is True
    kept = [(m.content, m.metadata.get("turn_id")) for m in session.messages]
    assert kept[-4:] == [("u4", "t4"), ("a4", "t4"), ("u5", "t5"), ("a5", "t5")]
    assert kept[1] == (summary["content"], None)
    server.session_truncate({"turn_id": "t5"})
    assert [m.content for m in session.messages][-2:] == ["u4", "a4"]


# -- /retry: the dropped turn reaches the TUI ---------------------------------


def _retry_session() -> MagicMock:
    session = MagicMock()
    session.metadata = {}
    session.messages = [
        SessionMessage(role="user", content="u1", metadata={"turn_id": "t1"}),
        SessionMessage(role="assistant", content="a1", metadata={"turn_id": "t1"}),
        SessionMessage(role="user", content="u2", metadata={"turn_id": "t2"}),
        SessionMessage(role="assistant", content="", metadata={}),
        SessionMessage(role="tool", content="tool out", metadata={}),
        SessionMessage(role="assistant", content="a2", metadata={"turn_id": "t2"}),
    ]
    return session


def test_drop_last_turn_cuts_the_whole_turn_including_tool_rows():
    dropped: list[list[str]] = []
    adapter = command_bridge.CompanionSessionAdapter(_retry_session(), on_drop=dropped.append)
    turn = adapter.drop_last_turn()
    assert turn.user == "u2"
    assert [m.content for m in adapter.messages] == ["u1", "a1"]
    assert dropped == [["t2"]]


def test_retry_reports_dropped_turn_ids_and_queues_the_resend(monkeypatch):
    server, _ = _dispatch_server(monkeypatch)
    server._session = session = _retry_session()
    session.send.side_effect = AssertionError("slash command ran a turn inline")
    out = server.dispatch_slash_command({"command": "/retry"})
    assert out["dropped_turn_ids"] == ["t2"]
    assert out["follow_up_messages"] == ["u2"]
    assert [m.content for m in session.messages] == ["u1", "a1"]


# -- followup: agent-turn slash commands queue their prompt -------------------


def _companion_session() -> MagicMock:
    session = MagicMock()
    session.metadata = {}
    session.messages = []
    session.send.side_effect = AssertionError("slash command ran a turn inline")
    return session


def test_send_user_message_queues_on_a_collecting_renderer(monkeypatch):
    monkeypatch.setattr(command_bridge, "_make_mcp_manager", lambda client: MagicMock())
    renderer = CollectingRenderer()
    app = CompanionCommandApp(session=_companion_session(), store=MagicMock(), renderer=renderer)
    app.send_user_message("do the thing")
    assert renderer.follow_up_messages == ["do the thing"]
    assert renderer.lines == []


def _dispatch_server(monkeypatch) -> tuple[CompanionBridgeServer, MagicMock]:
    monkeypatch.setattr(command_bridge, "_make_mcp_manager", lambda client: MagicMock())
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    session = _companion_session()
    server._session = session
    server._store = MagicMock()
    server._stdin_mux = None
    server._active_turn_cancel = None
    server._oob_parked = []
    server.snapshot = lambda: {}
    return server, session


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/search quantum dots", "Search for: quantum dots."),
        ("/agent chat explain the run", "explain the run"),
        ("/code review src/x.py", "Target: src/x.py"),
    ],
)
def test_dispatch_slash_command_returns_follow_up_messages(monkeypatch, command, expected):
    server, session = _dispatch_server(monkeypatch)
    out = server.dispatch_slash_command({"command": command})
    assert out["handled"] is True
    follow_ups = out["follow_up_messages"]
    assert len(follow_ups) == 1 and expected in follow_ups[0]
    session.send.assert_not_called()


def test_dispatch_slash_command_without_a_turn_has_no_follow_ups(monkeypatch):
    server, _session = _dispatch_server(monkeypatch)
    out = server.dispatch_slash_command({"command": "/search"})
    assert out["follow_up_messages"] == []
    assert out["dropped_turn_ids"] == []
