"""Pins for the T5 bridge teamup handlers (create/list/status/lifecycle/etc)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from r4.companion.ui.bridge_server import CompanionBridgeServer


class TestTeamupBridgeHandlers:
    @pytest.fixture
    def server(self, tmp_path):
        srv = CompanionBridgeServer.__new__(CompanionBridgeServer)
        srv._client = MagicMock()
        srv._client.config = MagicMock(profile="default", gateway_url="", project_id="")
        srv._team_client = None
        return srv

    def test_teamup_create_validates_name_only(self, server, tmp_path):
        import os

        os.environ.setdefault("UMRS_SKIP_BALANCE_ROWS", "1")
        with pytest.raises((ValueError, TypeError)) as exc:
            server.teamup_create({})
        assert "name is required" in str(exc.value)

    def test_teamup_create_validates_role_names(self, server):
        with pytest.raises(ValueError, match="unknown role"):
            server.teamup_create({"name": "gone", "roles": "nope"})

    def test_teamup_create_marks_snapshot_and_start_state(self, server, tmp_path, monkeypatch):
        monkeypatch.setattr(
            __import__("r4.teamup.session", fromlist=["TeamupSession"]).TeamupSession,
            "__init__",
            lambda self, config, client: (
                setattr(self, "_config", config)
                or setattr(self, "_client", client)
                or setattr(self, "session_id", "sess-0001")
                or setattr(self, "status", MagicMock(value="created"))
            ),
        )
        monkeypatch.setattr(
            __import__("r4.teamup.session", fromlist=["TeamupSession"]).TeamupSession,
            "_ensure_session_dir",
            lambda self: None,
        )
        monkeypatch.setattr(
            __import__("r4.teamup.session", fromlist=["TeamupSession"]).TeamupSession,
            "_save_snapshot",
            lambda self: None,
        )
        result = server.teamup_create({"name": "batch-1", "roles": "pm,se"})
        assert result["session_id"] == "sess-0001"
        assert result["status"] == "created"
        assert result["roles"] == ["pm", "se"]
        assert result["started"] is False

    def test_teamup_list_returns_verbatim_sessions(self, server, monkeypatch):
        monkeypatch.setattr(
            __import__("r4.teamup.session", fromlist=["TeamupSession"]).TeamupSession,
            "list_sessions",
            classmethod(
                lambda cls: [
                    {"session_id": "sess-a", "name": "a"},
                    {"session_id": "sess-b", "name": "b"},
                ]
            ),
        )
        result = server.teamup_list({})
        assert result["count"] == 2
        assert result["sessions"][0]["session_id"] == "sess-a"

    def test_teamup_status_uses_latest_when_unspecified(self, server, monkeypatch):
        monkeypatch.setattr(
            "r4.companion.ui.bridge_server.CompanionBridgeServer._teamup_latest_session_id",
            staticmethod(lambda: "sess-0009"),
        )
        fake_session = MagicMock()
        fake_session.get_status.return_value = {"session_id": "sess-0009", "status": "paused"}
        monkeypatch.setattr(
            "r4.companion.ui.bridge_server.CompanionBridgeServer._teamup_session",
            lambda self, sid: fake_session,
        )
        result = server.teamup_status({})
        assert result["session_id"] == "sess-0009"
        assert result["status"] == "paused"
