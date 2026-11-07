"""Owner-scoped loop routes (V-13) answer another user's loop with a 404;
the TUI bridge renders that as a not-found card, not a traceback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.companion import research_loop_tools
from r4.companion.ui.bridge_server import CompanionBridgeServer


class _Http404(Exception):
    status_code = 404


class _Boom(Exception):
    status_code = 500


def _server(monkeypatch, exc):
    class _Runtime:
        def get_loop(self, _loop_id):
            raise exc

    monkeypatch.setattr(research_loop_tools, "_runtime_for", lambda *_a: _Runtime())
    return SimpleNamespace(
        session=SimpleNamespace(client=object()),
        _research_project_id=lambda _params: "proj-1",
    )


def test_another_users_loop_is_a_not_found_payload(monkeypatch):
    fake = _server(monkeypatch, _Http404("Loop x not found"))
    payload = CompanionBridgeServer.research_loop_status(fake, {"loop_id": "loop-x"})
    assert payload["found"] is False
    assert payload["loop_id"] == "loop-x"
    assert "owner-scoped" in payload["message"]


def test_other_errors_still_raise(monkeypatch):
    fake = _server(monkeypatch, _Boom("down"))
    with pytest.raises(_Boom):
        CompanionBridgeServer.research_loop_status(fake, {"loop_id": "loop-x"})
