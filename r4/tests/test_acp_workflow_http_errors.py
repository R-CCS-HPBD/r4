"""Regression tests for #210: ACP workflow status/detail errors must carry
the HTTP status in the message so the webview watchlist can tell a true
404-not-found ("drop from watchlist") apart from transient 5xx/timeout
failures ("keep watching the running execution")."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from r4.acp import _workflow_http_errors_curated
from r4.errors import APIError


class _BoomWorkflows:
    def __init__(self, status_code: int) -> None:
        self._status_code = status_code

    def status(self, execution_id: str) -> Any:
        raise APIError(f"API error: {self._status_code}", status_code=self._status_code)

    def get_execution_detail(self, execution_id: str) -> dict[str, Any]:
        raise APIError(f"API error: {self._status_code}", status_code=self._status_code)


def _runtime_with_404() -> Any:
    client = SimpleNamespace(workflows=_BoomWorkflows(404))
    return SimpleNamespace(_client=client, client=lambda: client)


def test_404_becomes_r4error_with_http_prefix() -> None:
    from r4.errors import R4Error

    runtime = _runtime_with_404()
    with pytest.raises(R4Error) as excinfo:
        _workflow_http_errors_curated(
            lambda: runtime.client().workflows.get_execution_detail("exec-gone")
        )
    message = str(excinfo.value.message)
    assert "HTTP 404" in message
    assert "not found" in message.lower()


def test_5xx_is_not_marked_not_found() -> None:
    from r4.errors import R4Error

    with pytest.raises(R4Error) as excinfo:
        _workflow_http_errors_curated(lambda: _BoomWorkflows(503).status("exec-x"))
    message = str(excinfo.value.message).lower()
    assert "503" in message
    assert "not found" not in message


def test_success_path_untouched() -> None:
    payload = {"execution": {"id": "e1"}}
    assert _workflow_http_errors_curated(lambda: payload) == payload
