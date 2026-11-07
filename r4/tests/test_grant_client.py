"""Client + CLI coverage for asset grants (ADR-041, WP-06)."""

from __future__ import annotations

from argparse import Namespace
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


@contextmanager
def _client_with_http():
    """Client whose http layer is mocked; calls run inside the patch."""
    from r4.client import Client

    mock_core = MagicMock()
    with patch.object(Client, "http", new_callable=lambda: property(lambda self: mock_core)):
        client = Client()
        mock_core.get = MagicMock(return_value={"grants": []})
        mock_core.post = MagicMock(return_value={"status": "ok"})
        yield client, mock_core


class TestGrantClient:
    def test_create_grant_payload_minimal(self):
        with _client_with_http() as (client, mock_core):
            client.create_grant("model", "mdl-1", "user", "alice")
        args, kwargs = mock_core.post.call_args
        assert args[0].endswith("/gw/grants")
        assert kwargs["json_body"] == {
            "asset_kind": "model",
            "asset_id": "mdl-1",
            "grantee_kind": "user",
            "grantee_id": "alice",
        }

    def test_create_grant_payload_with_pin_and_expiry(self):
        with _client_with_http() as (client, mock_core):
            client.create_grant(
                "knowledge_graph",
                "g-1",
                "project",
                "proj-x",
                version_pin="v3",
                expires_at="2027-01-01T00:00:00Z",
            )
        _, kwargs = mock_core.post.call_args
        assert kwargs["json_body"]["version_pin"] == "v3"
        assert kwargs["json_body"]["expires_at"] == "2027-01-01T00:00:00Z"

    def test_list_grants_params(self):
        with _client_with_http() as (client, mock_core):
            client.list_grants("model", "mdl-1")
        args, kwargs = mock_core.get.call_args
        assert args[0].endswith("/gw/grants")
        assert kwargs["params"] == {"asset_kind": "model", "asset_id": "mdl-1"}

    def test_list_my_grants_route(self):
        with _client_with_http() as (client, mock_core):
            client.list_my_grants()
        args, _ = mock_core.get.call_args
        assert args[0].endswith("/gw/grants/mine")

    def test_revoke_route_posts(self):
        with _client_with_http() as (client, mock_core):
            client.revoke_grant("grt-abc")
        args, kwargs = mock_core.post.call_args
        assert args[0].endswith("/gw/grants/grt-abc/revoke")
        assert "json_body" not in kwargs or kwargs.get("json_body") is None


class TestGrantCLI:
    def test_command_registered(self):
        import r4._cli.grants  # noqa: F401 — module import runs @command
        from r4._cli.registry import registry

        assert registry.get("grant").name == "grant"

    def test_grantee_xor_enforced(self):
        from r4._cli.grants import _grantee

        both = Namespace(grantee_user="alice", grantee_project="proj-x")
        with pytest.raises(SystemExit):
            _grantee(both)
        neither = Namespace(grantee_user="", grantee_project="")
        with pytest.raises(SystemExit):
            _grantee(neither)
        assert _grantee(Namespace(grantee_user="alice", grantee_project="")) == (
            "user",
            "alice",
        )
        assert _grantee(Namespace(grantee_user="", grantee_project="proj-x")) == (
            "project",
            "proj-x",
        )
