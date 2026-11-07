"""Research session client + runtime threading (Milestone 6, ADR-025)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.client import Client
from r4.runtime import HybridRuntime, LoopSpec, UmrsRuntime
from r4.runtime.session_view import research_session_view


class _FakeHTTP:
    def __init__(self):
        self.calls = []

    def post(self, url, json_body=None, timeout=30, **kw):
        self.calls.append(("POST", url, json_body))
        if url.endswith("/research/sessions"):
            sid = (json_body or {}).get("session_id") or "sess-x1"
            return {
                "session": {
                    "session_id": sid,
                    "project_id": (json_body or {}).get("project_id"),
                    "title": "t",
                    "status": "active",
                    "loops": [],
                }
            }
        if "/loops" in url:
            return {"session_id": "sess-x1", "loop": {"loop_id": json_body.get("loop_id")}}
        return {}

    def get(self, url, params=None, timeout=30, **kw):
        self.calls.append(("GET", url, params))
        if url.endswith("/research/sessions"):
            return {
                "sessions": [
                    {
                        "session_id": "sess-x1",
                        "project_id": "proj-1",
                        "title": "filter",
                        "status": "active",
                        "loops": [{"loop_id": "loop-1"}],
                    }
                ],
                "count": 1,
            }
        return {"session": {"session_id": "sess-x1", "loops": []}}

    def patch(self, url, json_body=None, timeout=30, **kw):
        self.calls.append(("PATCH", url, json_body))
        return {"session": {"session_id": "sess-x1", "status": json_body.get("status")}}


def _client() -> Client:
    client = Client.__new__(Client)
    client._config = SimpleNamespace(gateway_url="https://gw.example", verify_ssl=True)
    client._http_core = _FakeHTTP()
    # bypass _api_prefix probing
    client._is_umrs_direct = False
    return client


def test_client_session_crud_and_attach():
    client = _client()
    out = client.create_research_session("proj-1", "goal text", title="filter")
    assert out["session"]["session_id"] == "sess-x1"
    method, url, body = client.http.calls[0]
    assert url.endswith("/research/sessions") and body["project_id"] == "proj-1"

    sessions = client.list_research_sessions("proj-1", status="active")
    assert sessions["count"] == 1
    method, url, params = client.http.calls[-1]
    assert params == {"project_id": "proj-1", "status": "active"}

    upd = client.update_research_session("sess-x1", status="paused")
    assert upd["session"]["status"] == "paused"

    attach = client.attach_loop_to_session("sess-x1", "loop-7", venue="local")
    assert attach["loop"]["loop_id"] == "loop-7"

    loops = client.list_session_loops("sess-x1")
    assert loops is not None


def test_umrs_runtime_threads_session_id():
    fake_client = MagicMock()
    fake_client.start_loop.return_value = {
        "loop_id": "loop-1",
        "status": "running",
        "message": "ok",
    }
    rt = UmrsRuntime(fake_client)
    state = rt.start_loop(LoopSpec("proj", "goal", session_id="sess-9"))
    fake_client.start_loop.assert_called_once_with(
        "proj", "goal", max_iterations=10, approved=False, session_id="sess-9"
    )
    assert state.loop_id == "loop-1"


def test_umrs_runtime_omits_session_kwarg_when_absent():
    """Payload compatibility: no session_id kwarg hits the wire when unset."""
    fake_client = MagicMock()
    fake_client.start_loop.return_value = {"loop_id": "loop-2", "status": "running"}
    rt = UmrsRuntime(fake_client)
    rt.start_loop(LoopSpec("proj", "goal"))
    fake_client.start_loop.assert_called_once_with(
        "proj", "goal", max_iterations=10, approved=False
    )


def test_umrs_runtime_threads_agentic_flag():
    """W9: LoopSpec.agentic → UMRS payload agentic=true."""
    fake_client = MagicMock()
    fake_client.start_loop.return_value = {"loop_id": "loop-3", "status": "running"}
    rt = UmrsRuntime(fake_client)
    rt.start_loop(LoopSpec("proj", "goal", agentic=True))
    fake_client.start_loop.assert_called_once_with(
        "proj", "goal", max_iterations=10, approved=False, agentic=True
    )


def test_hybrid_stamps_session_in_metadata(tmp_path):
    from r4.runtime.local import LocalRuntime

    local = LocalRuntime(tmp_path / "h.db")
    remote_client = MagicMock()
    remote_client.start_loop.return_value = {"loop_id": "loop-remote", "status": "running"}
    hybrid = HybridRuntime(local, UmrsRuntime(remote_client))
    state = hybrid.start_loop(LoopSpec("proj", "goal", session_id="sess-5"))
    assert state.metadata["session_id"] == "sess-5"
    assert state.metadata["venue"] == "local"


def test_session_view_shows_durable_sessions():
    client = _client()
    client._knowledge = MagicMock()
    client._knowledge.get_graph.return_value = {"nodes": [], "edges": []}
    client.start_loop = MagicMock(return_value={"loop_id": "loop-1"})
    client.list_loops = MagicMock(
        return_value={
            "loops": [
                {"loop_id": "loop-1", "status": "running", "metadata": {"session_id": "sess-x1"}}
            ]
        }
    )

    view = research_session_view(client, "proj-1", mode="umrs")

    assert view["sessions"]["available"] is True
    assert view["sessions"]["count"] == 1
    item = view["loops"]["items"][0]
    assert item["session_id"] == "sess-x1"


def test_session_view_sessions_offline_graceful():
    client = SimpleNamespace()
    client.knowledge = MagicMock()
    client.knowledge.get_graph.side_effect = RuntimeError("down")
    client.list_research_sessions = MagicMock(side_effect=RuntimeError("403 denied"))
    client.list_loops = MagicMock(return_value={"loops": []})

    view = research_session_view(client, "proj-1", mode="umrs")
    assert view["sessions"]["available"] is False
    assert "403 denied" in view["sessions"]["reason"]


class TestCliSessionsHandler:
    def test_list_sessions_json(self, monkeypatch, capsys):
        from argparse import Namespace

        from r4._cli.project_knowledge import project_sessions

        fake = MagicMock()
        fake.list_research_sessions.return_value = {
            "sessions": [
                {"session_id": "sess-1", "title": "MERMAID", "status": "active", "loops": []}
            ],
            "count": 1,
        }
        monkeypatch.setattr("r4.client.Client", lambda *a, **k: fake)

        args = Namespace(
            session_action="list",
            target="proj-x",
            status=None,
            json=True,
            project_id="proj-x",
        )
        project_sessions(args, MagicMock())
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["count"] == 1
        fake.list_research_sessions.assert_called_once()

    def test_show_session_payload(self, monkeypatch, capsys):
        from argparse import Namespace

        from r4._cli.project_knowledge import project_sessions

        fake = MagicMock()
        fake.get_research_session.return_value = {
            "session": {
                "session_id": "sess-1",
                "project_id": "proj-x",
                "title": "MERMAID",
                "status": "active",
                "authoritative_runtime": "umrs",
                "research_goal": "filter peaks",
                "loops": [{"loop_id": "loop-9", "venue": "umrs", "loop_role": "auto"}],
            }
        }
        monkeypatch.setattr("r4.client.Client", lambda *a, **k: fake)

        args = Namespace(session_action="show", target="sess-1", status=None, json=False)
        project_sessions(args, MagicMock())
        out = capsys.readouterr().out
        assert "Session sess-1" in out
        assert "MERMAID" in out
        assert "loop-9" in out
