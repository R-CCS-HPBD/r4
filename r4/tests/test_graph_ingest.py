"""Named-graph ingestion: client graph_ingest + CLI + companion pass-through."""

from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

from r4.knowledge import KnowledgeClient


class _FakeInnerClient:
    _api_prefix = "/gw"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.next: Any = {"job_id": "job-1", "status": "queued"}

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.next

    def post(self, url: str, json: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"method": "POST", "url": url, "json": json, **kwargs})
        return self.next

    def patch(self, url: str, json: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"method": "PATCH", "url": url, "json": json, **kwargs})
        return self.next


class TestClientGraphIngest:
    def test_builds_specs_and_carries_graph(self, tmp_path):
        exp = tmp_path / "exp.json"
        exp.write_text('{"run": 1}')
        inner = _FakeInnerClient()
        knowledge = KnowledgeClient(inner)
        knowledge.graph_ingest(
            "experiment-history",
            project_id="proj-a",
            files=[str(exp)],
            urls=["https://example.org/paper"],
            proposal_id="gp-1",
        )
        call = inner.calls[0]
        assert call["url"] == "/gw/knowledge/ingest/ingest"
        body = call["json"]
        assert {k: body[k] for k in ("graph_id", "proposal_id")} == {
            "graph_id": "experiment-history",
            "proposal_id": "gp-1",
        }
        # 2026-10-25: no allow_domains from the URLs' hosts; the site
        # allowlist checks every hop (a redirect to another host is allowed
        # when the site lists it). Asserted in test_knowledge_allowlist_cli.
        assert "allow_domains" not in body
        # content travels, never the path
        assert body["sources"] == [
            {"kind": "inline_text", "uri": "exp.json", "content": '{"run": 1}'},
            {"kind": "http_url", "uri": "https://example.org/paper"},
        ]

    def test_no_sources_raises(self):
        knowledge = KnowledgeClient(_FakeInnerClient())
        with pytest.raises(ValueError, match="nothing to ingest"):
            knowledge.graph_ingest("g-any", project_id="proj-a")


class TestCliGraphIngest:
    def test_handler_queues_and_prints(self, monkeypatch, capsys):
        from r4._cli.knowledge import KnowledgeCommand

        class _Knowledge(KnowledgeClient):
            pass

        seen = {}

        class _FakeClient:
            class knowledge:
                @staticmethod
                def graph_ingest(graph, **kwargs):
                    seen["graph"] = graph
                    seen.update(kwargs)
                    return {"job_id": "j-9", "status": "queued"}

        monkeypatch.setattr("r4.client.Client", lambda: _FakeClient())
        rc = KnowledgeCommand().handle(
            argparse.Namespace(
                knowledge_command="graph-ingest",
                graph="experiment-history",
                files=["/data/x.json"],
                urls=[],
                project_id="proj-a",
                proposal_id=None,
                as_json=False,
            )
        )
        assert rc == 0
        assert seen["graph"] == "experiment-history"
        assert seen["project_id"] == "proj-a"
        assert "j-9" in capsys.readouterr().out

    def test_handler_requires_sources(self, capsys):
        from r4._cli.knowledge import KnowledgeCommand

        rc = KnowledgeCommand().handle(
            argparse.Namespace(
                knowledge_command="graph-ingest",
                graph="g",
                files=[],
                urls=[],
                project_id="proj-a",
                proposal_id=None,
                as_json=False,
            )
        )
        assert rc == 1
        assert "Nothing to ingest" in capsys.readouterr().err


class TestCompanionIngestGraphTarget:
    def test_knowledge_ingest_carries_graph_and_proposal(self):
        from types import SimpleNamespace

        from r4.companion.knowledge_tools import _build_knowledge_ingest

        calls: list[dict[str, Any]] = []

        class _Knowledge:
            actor = None
            session_id = None
            graph_constraints: dict = {}

            def ingest_paths(self, project_id, paths, **kwargs):
                calls.append({"project_id": project_id, "paths": paths, **kwargs})
                return {"job_id": "j-1"}

        client = SimpleNamespace(
            knowledge=_Knowledge(),
            config=SimpleNamespace(project_id="proj-a"),
        )
        tool = _build_knowledge_ingest(client)
        out = json.loads(
            tool["execute"](
                files=["/data/x.json"],
                proposal_id="gp-1",
                graph_id="experiment-history",
            )
        )
        assert out["target_graph"] == "experiment-history"
        assert calls[0]["graph_id"] == "experiment-history"
        assert calls[0]["proposal_id"] == "gp-1"
