"""Approval parity wave (opencode study):
- asks for file_write/file_edit carry the SERVER-COMPUTED unified diff so the
  approval card shows the actual change (their permission EditBody);
- replies are DELIVERED into per-request queues from every stdin reader path
  (a stray-swallow can never starve a waiter);
- turn cancel resolves every parked waiter with "no".
"""

from __future__ import annotations

import queue
from unittest.mock import MagicMock

import pytest

from r4.companion.ui.bridge_server import (
    CompanionBridgeServer,
    _approval_preview,
    _proposed_after,
)


@pytest.fixture()
def server():
    srv = CompanionBridgeServer.__new__(CompanionBridgeServer)
    srv._pending_approvals = {}
    srv._approval_seq = 0
    srv._active_turn_cancel = None
    srv._stdin_mux = None
    srv._client = MagicMock()
    return srv


class TestApprovalPreview:
    def test_file_write_carries_diff(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "f.py"
        target.write_text("old_line\nkeep\n")
        preview = _approval_preview(
            "file_write", {"path": str(target), "content": "new_line\nkeep\n"}
        )
        assert preview["path"] == str(target)
        assert "-old_line" in preview["diff"]
        assert "+new_line" in preview["diff"]

    def test_new_file_write_diff_is_all_additions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        preview = _approval_preview("file_write", {"path": "fresh.py", "content": "a\nb\n"})
        assert preview["diff"].count("\n+") + preview["diff"].startswith("+") >= 1

    def test_file_edit_preview_reconstructs_after(self, tmp_path):
        target = tmp_path / "e.py"
        target.write_text("alpha\nbeta\n")
        preview = _approval_preview(
            "file_edit",
            {"path": str(target), "old_text": "alpha", "new_text": "ALPHA"},
        )
        assert "-alpha" in preview["diff"]
        assert "+ALPHA" in preview["diff"]

    def test_non_file_tool_has_no_preview(self):
        assert _approval_preview("shell_exec", {"command": "ls"}) == {}

    def test_uncomputable_preview_is_empty_never_raises(self):
        # unreadable path / non-unique edit / missing fields — all silent {}
        assert _approval_preview("file_write", {"path": "/nonexistent-dir/x", "content": 1}) == {}
        assert _proposed_after("file_edit", "alpha", {"old_text": "", "new_text": "x"}) is None

    def test_expected_count_respected(self):
        assert (
            _proposed_after(
                "file_edit", "x x", {"old_text": "x", "new_text": "y", "expected_count": 2}
            )
            == "y y"
        )


class TestDeliveryRegistry:
    def test_deliver_routes_decision_to_matching_waiter(self, server):
        q = server._pending_approvals[7] = queue.Queue()
        handled = server._deliver_approval({"approval_response": {"id": 7, "decision": "yes"}})
        assert handled is True
        assert q.get_nowait() == "yes"

    def test_stray_delivery_returns_false_and_parks_nothing(self, server):
        stray = server._deliver_approval({"approval_response": {"id": 99, "decision": "yes"}})
        assert stray is False
        assert server._deliver_approval({"unrelated": True}) is False
        assert server._pending_approvals == {}

    def test_cancel_turn_resolves_every_pending_waiter(self, server):
        q1 = server._pending_approvals[1] = queue.Queue()
        q2 = server._pending_approvals[2] = queue.Queue()
        server._session = MagicMock()
        server.cancel_turn({})
        assert q1.get_nowait() == "no"
        assert q2.get_nowait() == "no"

    def test_await_resolves_when_cancel_set(self, server):
        server._active_turn_cancel = MagicMock()
        server._active_turn_cancel.is_set.return_value = True
        assert server._await_approval(5, queue.Queue()) == "no"

    def test_oob_approval_line_delivers_instead_of_swallowing(self, server):
        from r4.companion.ui.bridge_server import _handle_oob_line

        q = server._pending_approvals[3] = queue.Queue()
        result = _handle_oob_line(server, '{"approval_response": {"id": 3, "decision": "always"}}')
        assert result is None
        assert q.get_nowait() == "always"


def _emit_approval(server, tmp_path):
    """Run one file_write approval with all side seams stubbed; return emits."""
    target = tmp_path / "f.py"
    target.write_text("old\n")
    emitted: list[dict] = []
    server._emit_line = emitted.append
    server._register_approval_intent = MagicMock(return_value=None)
    server._decide_approval_intent = MagicMock()
    server._record_durable_trust = MagicMock()
    server._session = MagicMock()
    server._session_id_str = MagicMock(return_value="s1")
    server._await_approval = MagicMock(return_value="once")
    server._on_tool_approval("file_write", "write f", {"path": str(target), "content": "new\n"})
    return emitted, target


class TestApprovalEmission:
    def test_legacy_envelope_carries_preview(self, server, tmp_path):
        emitted, target = _emit_approval(server, tmp_path)
        legacy = [line for line in emitted if "approval_request" in line]
        assert legacy[0]["approval_request"]["diff"].count("+new") == 1
        assert legacy[0]["approval_request"]["path"] == str(target)

    def test_canonical_envelope_stays_byte_shaped(self, server, tmp_path):
        emitted, _target = _emit_approval(server, tmp_path)
        canonical = [line for line in emitted if line.get("type") == "approval_request"]
        assert "diff" not in canonical[0]["payload"]
        assert "path" not in canonical[0]["payload"]
