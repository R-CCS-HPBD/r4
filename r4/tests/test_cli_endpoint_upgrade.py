"""Regression: `r4 endpoint upgrade` CLI route parses and routes to client.

Q-13 Phase 1. A fresh machine without the upgrade subcmd would every new
model version deployed via `endpoint down` + `endpoint up` → 60 + 60
seconds of dead air between steps and a caller-visible 502 spike in the
middle. The upgrade subcmd introduces the flip-at-registry-instant
contract call sites depend on.

Zero ─ boundary: tests never hit the real gateway; the client is stubbed
and each test asserts on either (1) arg-parse or (2) dispatch.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest


def _make_cli_args(**overrides) -> argparse.Namespace:
    base = {
        "endpoint_command": "upgrade",
        "endpoint_id": "end-001",
        "model_path": "/models/new",
        "wait": False,
        "timeout": 900,
        "as_json": False,
        "source": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class TestUpgradeArgsParser:
    def test_upgrade_subcommand_registered(self) -> None:
        from r4._cli.endpoint import EndpointCommand

        parser = EndpointCommand().register(argparse.ArgumentParser().add_subparsers())
        args = parser.parse_args(["upgrade", "end-001", "--model-path", "/models/new"])
        assert args.endpoint_command == "upgrade"
        assert args.endpoint_id == "end-001"
        assert args.model_path == "/models/new"
        assert args.timeout == 900

    def test_requires_endpoint_id(self) -> None:
        from r4._cli.endpoint import EndpointCommand

        parser = EndpointCommand().register(argparse.ArgumentParser().add_subparsers())
        with pytest.raises(SystemExit):
            parser.parse_args(["upgrade", "--model-path", "/models/new"])

    def test_requires_model_path(self) -> None:
        from r4._cli.endpoint import EndpointCommand

        parser = EndpointCommand().register(argparse.ArgumentParser().add_subparsers())
        with pytest.raises(SystemExit):
            parser.parse_args(["upgrade", "end-001"])

    def test_wait_timeout_defaults(self) -> None:
        from r4._cli.endpoint import EndpointCommand

        parser = EndpointCommand().register(argparse.ArgumentParser().add_subparsers())
        args = parser.parse_args(["upgrade", "end-001", "--model-path", "/models/new"])
        assert args.wait is False
        assert args.timeout == 900


class TestUpgradeHandlerDispatch:
    def test_upgrade_calls_client_upgrade_endpoint(self) -> None:
        from r4._cli.endpoint import _cmd_endpoint_upgrade

        mock_client = MagicMock()
        mock_client.upgrade_endpoint.return_value = {
            "status": "provisioning",
            "old_model_path": "/models/old",
            "model_path": "/models/new",
        }
        args = _make_cli_args(endpoint_id="end-123", model_path="/models/new")
        rc = _cmd_endpoint_upgrade(args, mock_client)
        mock_client.upgrade_endpoint.assert_called_once()
        call_kwargs = mock_client.upgrade_endpoint.call_args[1]
        assert call_kwargs["endpoint_id"] == "end-123"
        assert call_kwargs["model_path"] == "/models/new"
        assert rc == 0

    def test_upgrade_json_output_suppresses_log_lines(self, capsys) -> None:
        from r4._cli.endpoint import _cmd_endpoint_upgrade

        mock_client = MagicMock()
        mock_client.upgrade_endpoint.return_value = {
            "status": "ready",
            "old_model_path": "/models/old",
            "model_path": "/models/new",
        }
        args = _make_cli_args(as_json=True)
        rc = _cmd_endpoint_upgrade(args, mock_client)
        out = capsys.readouterr().out
        import json as _json

        payload = _json.loads(out)
        assert payload["status"] == "ready"
        assert rc == 0

    def test_upgrade_error_contains_action_hint_for_404(self, capsys) -> None:
        from r4._cli.endpoint import _cmd_endpoint_upgrade

        mock_client = MagicMock()
        mock_client.upgrade_endpoint.side_effect = Exception("404 not found")
        args = _make_cli_args()
        with pytest.raises(SystemExit):
            _cmd_endpoint_upgrade(args, mock_client)

    def test_upgrade_failure_rc_nonzero_on_failed_status(self) -> None:
        from r4._cli.endpoint import _cmd_endpoint_upgrade

        mock_client = MagicMock()
        mock_client.upgrade_endpoint.return_value = {"status": "failed"}
        args = _make_cli_args()
        rc = _cmd_endpoint_upgrade(args, mock_client)
        assert rc == 1

    def test_upgrade_dispatch_in_registry(self) -> None:
        from r4._cli.endpoint import _endpoint_command_handlers

        assert "upgrade" in _endpoint_command_handlers()
