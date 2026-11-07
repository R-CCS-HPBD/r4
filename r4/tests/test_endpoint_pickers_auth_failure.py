"""Signed out, /endpoint up and /endpoint down name the gateway failure.

Stabilization 2026-10-21 (slash-a#0): the registry and stoppable-endpoint
collectors swallowed every exception and returned [], so the bridge reported
an empty registry ("No registry models found — try /endpoint up <model>")
or "No active endpoints to stop" when the call had failed authentication.
They now raise; the bridge reports the message and its hint.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from r4.errors import AuthenticationError
from r4.tui.commands import (
    _cmd_up,
    collect_registry_model_options,
    collect_stoppable_endpoint_options,
)


def _auth_error() -> AuthenticationError:
    return AuthenticationError("Failed to list endpoints: Authentication failed")


def test_stoppable_endpoints_raise_the_gateway_failure() -> None:
    client = SimpleNamespace(list_endpoints=MagicMock(side_effect=_auth_error()))

    with pytest.raises(AuthenticationError, match="Authentication failed"):
        collect_stoppable_endpoint_options(client)


def test_registry_models_raise_the_registry_failure() -> None:
    client = SimpleNamespace(list_endpoints=MagicMock(return_value={"endpoints": []}))

    with patch("r4.models.ModelClient") as model_client:
        model_client.return_value.list_models.side_effect = _auth_error()
        with pytest.raises(AuthenticationError):
            collect_registry_model_options(client)


def test_python_tui_up_renders_the_failure_instead_of_usage() -> None:
    renderer = MagicMock()
    app = SimpleNamespace(
        renderer=renderer,
        session=SimpleNamespace(model=None),
        client=SimpleNamespace(
            list_endpoints=MagicMock(return_value={"endpoints": []}),
            provision_endpoint=MagicMock(),
        ),
    )

    with patch("r4.tui.commands._registry_model_options", side_effect=_auth_error()):
        _cmd_up(app, [])

    app.client.provision_endpoint.assert_not_called()
    errors = [call.args[0] for call in renderer.error.call_args_list]
    assert any("could not list registry models" in line for line in errors)
