"""Contract tests for the aggregate project/platform status surface.

Covers the status_report snapshot consumed by the TUI ``/status`` command
and the companion ``project_status`` tool: full section coverage, per-section
fail-open behavior, registry presence, and SAFE classification.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from r4.companion.status_report import build_project_status
from r4.companion.tools import get_builtin_tools


class _Jobs:
    def list(self, status=None, limit=20):
        assert status == "running"
        return [SimpleNamespace(job_id="j1", job_type="workflow_run", created_at="now")]


class _Workflows:
    def list_workflows(self):
        return [SimpleNamespace(workflow_name="wf-b"), SimpleNamespace(workflow_name="wf-a")]


class _MCP:
    def list_servers(self):
        return [{"name": "s1", "url": "http://x"}]

    def list_tools(self):
        return [SimpleNamespace(name="t1", server="s1", description="d", parameters={})]


class _Knowledge:
    def graphs(self, status=None):
        return [{"name": "g1", "kind": "project", "status": "active"}]

    def list_loops(self, project_id):
        return [SimpleNamespace(loop_id="l1", status="running")]


class _RAG:
    def list_documents(self, project_id=None):
        return [{"filename": "f.pdf", "document_id": "d1", "chunks": 3}]


class _FakeClient:
    config = object()
    jobs = _Jobs()
    workflows = _Workflows()
    mcp = _MCP()
    knowledge = _Knowledge()
    rag = _RAG()

    def health(self):
        return True

    def readiness(self):
        return {"ready": True, "checks": {"gateway": "ok"}}

    def list_endpoints(self):
        return [{"instance_id": "e1", "model": "m1", "tier": "user", "status": "ready"}]

    research_targets: list = []

    def list_research_sessions(self, target, status=None):
        assert target != "latest"
        self.research_targets.append(target)
        return [{"session_id": "sess-1", "status": "active"}]

    def get(self, path, params=None):
        assert path == "/gw/catalog/search"
        return {"results": [{"s3_key": "default/f.csv", "tags": ["t1"]}]}


class _BrokenEndpoints(_FakeClient):
    def list_endpoints(self):
        raise RuntimeError("boom")


@pytest.fixture
def _patch_models(monkeypatch):
    import r4.companion.status_report as sr

    class _MC:
        def list_models(self, limit=50):
            return [
                SimpleNamespace(name="m1", scope="system", model_type="llm", status="ready"),
                SimpleNamespace(name="m2", scope="user", model_type="llm", status="staging"),
            ]

    monkeypatch.setattr(sr, "_model_client", lambda client: _MC())


@pytest.mark.usefixtures("_patch_models")
class TestSnapshot:
    def test_all_sections_present(self):
        text = build_project_status(_FakeClient(), include_local=True)
        for header in (
            "## Source & Health",
            "## Endpoints",
            "## Model Registry",
            "## Registered Workflows",
            "## Running Jobs",
            "## MCP Servers",
            "## Knowledge Graphs",
            "## Research Loops & Sessions",
            "## RAG Documents",
            "## Data Catalog",
            "## Companion Skills",
        ):
            assert header in text, header

    def test_section_content(self):
        text = build_project_status(_FakeClient())
        assert "wf-a" in text and "wf-b" in text
        assert "g1" in text
        assert "sess-1" in text
        assert "default/f.csv" in text
        assert "tools available: 1" in text

    def test_bare_build_never_sends_latest_sentinel(self):
        client = _FakeClient()
        client.research_targets = []
        build_project_status(client)
        assert client.research_targets == [None]

    def test_configured_project_scopes_research_sessions(self):
        client = _FakeClient()
        client.research_targets = []
        client.config = SimpleNamespace(project_id="proj-a")
        build_project_status(client)
        assert client.research_targets == ["proj-a"]

    def test_explicit_project_wins_for_research_sessions(self):
        client = _FakeClient()
        client.research_targets = []
        client.config = SimpleNamespace(project_id="proj-a")
        build_project_status(client, project_id="proj-b")
        assert client.research_targets == ["proj-b"]

    def test_fail_open_endpoint_section(self):
        text = build_project_status(_BrokenEndpoints())
        assert "boom" in text
        assert "## Registered Workflows" in text
        assert "wf-a" in text

    def test_wrapped_list_payloads(self):
        class _WrappedClient(_FakeClient):
            knowledge = _Knowledge()

        monkey = _WrappedClient()
        monkey.knowledge = type(
            "K",
            (),
            {
                "graphs": lambda self, status=None: [],
                "list_loops": lambda self, pid: {"loops": [{"loop_name": "lp-1"}]},
            },
        )()
        monkey.list_research_sessions = lambda target, status=None: {
            "sessions": [{"session_name": "sess-9"}],
            "count": 1,
        }
        text = build_project_status(monkey)
        assert "loop lp-1" in text
        assert "session sess-9" in text

    def test_row_limiting(self):
        class _Many(_FakeClient):
            def list_endpoints(self):
                return [
                    {"instance_id": f"e{i}", "model": "m", "tier": "u", "status": "ready"}
                    for i in range(12)
                ]

        text = build_project_status(_Many())
        assert "+4 more" in text


@pytest.mark.usefixtures("_patch_models")
class TestToolSurface:
    def test_project_status_tool_registered_and_safe(self):
        from r4.companion.permissions import RISKY_TOOLS, SAFE_TOOLS

        tools = {t["name"]: t for t in get_builtin_tools(_FakeClient())}
        assert "project_status" in tools
        assert "project_status" in SAFE_TOOLS
        assert "project_status" not in RISKY_TOOLS

    def test_section_filter(self):
        tools = {t["name"]: t for t in get_builtin_tools(_FakeClient())}
        out = tools["project_status"]["execute"](section="workflows")
        assert "Registered Workflows" in out
        assert "## Endpoints" not in out

    def test_tui_overview_command_registered(self):
        from r4.tui.commands import get_slash_registry

        registry = get_slash_registry()
        names = [c.name for c in registry.command_list()]
        assert "overview" in names
        # Pre-existing behaviour: /status stays the dashboard alias.
        status_cmd = registry._commands.get("status")
        assert status_cmd is not None and status_cmd.name == "dashboard"
