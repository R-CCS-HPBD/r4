"""Client + CLI coverage for circles + quota (ADR-086, ADR-051)."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch


@contextmanager
def _client_with_http():
    from r4.client import Client

    mock_core = MagicMock()
    with patch.object(Client, "http", new_callable=lambda: property(lambda self: mock_core)):
        client = Client()
        mock_core.get = MagicMock(return_value={"circles": []})
        mock_core.post = MagicMock(return_value={"status": "ok"})
        yield client, mock_core


class TestCircleClient:
    def test_create_circle_payload_and_route(self):
        with _client_with_http() as (client, mock_core):
            client.create_circle("proj-a", "team-x", name="Team X")
        args, kwargs = mock_core.post.call_args
        assert args[0].endswith("/gw/circles")
        assert kwargs["json_body"] == {
            "project_id": "proj-a",
            "circle_id": "team-x",
            "name": "Team X",
        }

    def test_create_circle_for_user_admin_override(self):
        with _client_with_http() as (client, mock_core):
            client.create_circle("proj-a", "team-x", for_user="bob")
        _, kwargs = mock_core.post.call_args
        assert kwargs["json_body"]["for_user"] == "bob"

    def test_mine_uses_mine_route_and_flag(self):
        with _client_with_http() as (client, mock_core):
            client.list_my_circles(include_retired=True)
        args, kwargs = mock_core.get.call_args
        assert args[0].endswith("/gw/circles/mine")
        assert kwargs["params"] == {"include_retired": "true"}

    def test_list_circles_scopes_to_project(self):
        with _client_with_http() as (client, mock_core):
            client.list_circles("proj-a")
        args, _ = mock_core.get.call_args
        assert args[0].endswith("/gw/circles/proj-a")

    def test_retire_posts_patch_with_action(self):
        with _client_with_http() as (client, mock_core):
            client.retire_circle("proj-a", "team-x")
        args, kwargs = mock_core.patch.call_args
        assert args[0].endswith("/gw/circles/proj-a/team-x")
        assert kwargs["json_body"] == {"action": "retire"}

    def test_member_add_and_remove_routes(self):
        with _client_with_http() as (client, mock_core):
            client.add_circle_member("proj-a", "team-x", "alice", role="admin")
            client.remove_circle_member("proj-a", "team-x", "alice")
        assert mock_core.post.call_args[0][0].endswith("/gw/circles/proj-a/team-x/members")
        assert mock_core.post.call_args[1]["json_body"] == {
            "user_id": "alice",
            "role": "admin",
        }
        assert mock_core.delete.call_args[0][0].endswith("/gw/circles/proj-a/team-x/members/alice")


class TestQuotaClient:
    def test_quota_units_route(self):
        with _client_with_http() as (client, mock_core):
            client.quota_units()
        args, _kwargs = mock_core.get.call_args
        assert args[0].endswith("/gw/quota/units")


class TestCircleCLI:
    def test_command_registered(self):
        # two coexisting modules carried distinct names on purpose:
        # groups = CommandGroup framework infra (pre-existing, tracked),
        # circle = the ADR-086 handler verb.
        import r4._cli.circle as circle_mod  # noqa: F401 — @command registers on import
        from r4._cli import groups as groups_module
        from r4._cli import registry as cli_registry

        assert hasattr(groups_module, "CommandGroup")
        assert cli_registry.get("circle") is not None

    def test_quota_command_registered(self):
        import r4._cli.quota as _quota_mod  # noqa: F401 — @command registers on import
        from r4._cli import registry as cli_registry

        assert cli_registry.get("quota") is not None

    def test_circle_handler_dispatch_shape(self):
        import inspect

        import r4._cli.circle as mod

        handles = {
            "enter",
            "create",
            "mine",
            "list",
            "show",
            "retire",
            "members",
            "member-add",
            "member-remove",
        }
        src = inspect.getsource(mod.cmd_circle)
        for verb in handles:
            assert f'"{verb}"' in src
