"""Regression tests for #212: ACP inventory payloads must surface fetch
failures as explicit error fields — silent [] looked identical to a healthy
empty system. (#213's tests went with ``ContextManager.query_rag``, which had
no caller; ADR-093 C11.)"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from r4.acp import _models_payload, _tools_payload


class _BoomClient:
    def list_models(self) -> list[Any]:
        raise RuntimeError("gateway offline")

    @property
    def mcp(self) -> Any:
        return SimpleNamespace(list_tools=lambda: (_ for _ in ()).throw(RuntimeError("mcp down")))


def _server(client: Any) -> Any:
    runtime = SimpleNamespace(client=lambda: client, editor_tools=[])
    return SimpleNamespace(_runtime=runtime)


def test_models_payload_surfaces_error_instead_of_silent_empty() -> None:
    payload = _models_payload(_server(_BoomClient()), {})
    assert payload["models"] == []
    assert "gateway offline" in payload["error"]


def test_models_payload_no_error_key_on_success() -> None:
    client = SimpleNamespace(list_models=lambda: [{"id": "m1", "name": "m1", "status": "ready"}])
    payload = _models_payload(_server(client), {})
    assert payload["models"][0]["id"] == "m1"
    assert "error" not in payload


def test_tools_payload_surfaces_error_instead_of_silent_empty() -> None:
    payload = _tools_payload(_server(_BoomClient()), {})
    assert payload["tools"] == []
    assert "mcp down" in payload["error"]


def test_runtime_unavailable_surfaces_error() -> None:
    assert _models_payload(SimpleNamespace(_runtime=None), {}) == {
        "models": [],
        "error": "runtime unavailable",
    }
    assert _tools_payload(SimpleNamespace(_runtime=None), {}) == {
        "tools": [],
        "error": "runtime unavailable",
    }
