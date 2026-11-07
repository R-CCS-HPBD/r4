"""Pins for G-P6-2 client-level durable approval intents (chat_turn_approvals
client leg, ADR-057 leg 3 follow-up).

Every approval the bridge asks is FIRST a durable intent row on the server,
and the answer lands as a decision on the same row — so a second client of
the same session id sees the as-of-now pending state (never a transient
in-process pending dict from the first client).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from r4.companion.ui.bridge_server import CompanionBridgeServer


def _server():
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._approval_seq = 0
    server._ask_user_seq = 0
    server._approval_waiters = {}
    server._outbound_dispatcher = None
    server._outbox = []
    server._out_drain_started = False
    server._attached = False
    server._session = None
    server._backend_running = False
    server._interaction_mode = None
    server._stdin_open = False
    server._client = MagicMock()
    server._client.config = MagicMock(gateway_url="https://gw.test/rccs")
    server._client._api_prefix = "/umrs"
    server._client.post = MagicMock()
    server._client.get = MagicMock(return_value={})
    server._pending_approval = None
    return server


def _preset_session(server) -> None:
    session = MagicMock()
    session.session_id = "sess-X"
    session.metadata = {"turn_seq": 1}
    session._current_turn_id = "t1"
    server._session = session


class TestDurableApprovalIntent:
    def test_intent_registration_posts_row(self):
        server = _server()
        _preset_session(server)
        session_id = server._session_id_str()
        intent_id = server._register_approval_intent("shell_exec", "run: ls", {"cmd": "ls -la"})
        assert intent_id is not None
        args, kwargs = server._client.post.call_args_list[0]
        assert args[0] == f"/umrs/companion/sessions/{session_id}/approvals"
        body = kwargs["json"]
        assert body["turn_id"] == "t1"
        assert body["action"]["tool"] == "shell_exec"
        assert body["action"]["description"] == "run: ls"
        assert body["approval_id"] == intent_id

    def test_missing_session_or_gateway_returns_none(self):
        server = _server()
        server._session = None
        assert server._register_approval_intent("shell_exec", "run", {}) is None
        server._client.post.assert_not_called()

    def test_transport_failure_returns_none_not_raises(self):
        server = _server()
        _preset_session(server)
        server._client.post.side_effect = RuntimeError("boom")
        assert server._register_approval_intent("shell_exec", "run", {}) is None

    def test_decision_writes_backend_row(self):
        server = _server()
        _preset_session(server)
        server._decide_approval_intent("appr-a1", "once")
        args, kwargs = server._client.post.call_args_list[0]
        assert args[0].endswith("/approvals/appr-a1/decision")
        assert kwargs["json"] == {"decision": "once"}

    def test_no_intent_never_posts_decision(self):
        server = _server()
        _preset_session(server)
        server._decide_approval_intent(None, "once")
        server._client.post.assert_not_called()

    def test_approval_flow_end_to_end_intent_then_decision(self):
        server = _server()
        _preset_session(server)
        # drive the blocking wait through a stub (pending-queue seam)
        server._await_approval = MagicMock(return_value="yes")
        verdict = server._on_tool_approval("shell_exec", "run ls", {})
        assert verdict is True
        posted = [c.args[0] for c in server._client.post.call_args_list]
        assert any(p.endswith("/approvals") for p in posted)
        assert any(p.endswith("/decision") for p in posted)
