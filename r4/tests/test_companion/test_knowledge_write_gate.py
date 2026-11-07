"""Session-scoped knowledge-acquisition mode (write gate).

The user decides mid-session whether the chat may grow knowledge at all,
and into which pre-existing graph when it may extend-only. Guards fire
before any server call — knowledge_ingest / knowledge_graph_propose
return a structured refusal, never a silent write.
"""

from __future__ import annotations

import json

import pytest

from r4.companion.knowledge_graph_tools import _build_graph_propose
from r4.companion.knowledge_tools import _build_knowledge_ingest
from r4.companion.knowledge_write_gate import (
    acquisition_constraints,
    check_propose,
    describe_acquisition,
    resolve_ingest_target,
    sanitize_acquisition,
    set_acquisition,
)


class _FakeKnowledge:
    def __init__(self, graphs=None):
        self.write_constraints = {"mode": "open", "graphs": []}
        self.actor = None
        self.session_id = None
        self._graphs = graphs or []
        self.ingest_calls = []
        self.propose_calls = []

    def graphs(self, **_kwargs):
        return self._graphs

    def ingest_paths(self, target, paths, urls=None, proposal_id=None, graph_id=None):
        self.ingest_calls.append(
            {
                "target": target,
                "paths": paths,
                "urls": urls,
                "graph_id": graph_id,
                "proposal_id": proposal_id,
            }
        )
        return {"job_id": "j-1", "status": "queued"}

    def graph_propose(self, graph_id, **_kwargs):
        self.propose_calls.append(graph_id)
        return {"proposal_id": "p-1", "graph_id": graph_id, "status": "pending"}


class _FakeConfig:
    project_id = "proj-1"


class _FakeClient:
    def __init__(self, graphs=None):
        self.knowledge = _FakeKnowledge(graphs)
        self.config = _FakeConfig()


def _ingest_tool(client):
    return _build_knowledge_ingest(client)["execute"]


def _propose_tool(client):
    return _build_graph_propose(client)["execute"]


def _code(payload: str) -> str:
    return json.loads(payload).get("code", "")


# ── sanitize / set ─────────────────────────────────────────────────────


class TestSanitize:
    def test_defaults_open(self):
        assert sanitize_acquisition(None) == {"mode": "open", "graphs": []}
        assert sanitize_acquisition({})["mode"] == "open"

    def test_unknown_mode_falls_back_open(self):
        assert sanitize_acquisition({"mode": "weird"})["mode"] == "open"

    def test_pinned_without_graphs_is_open(self):
        assert sanitize_acquisition({"mode": "pinned", "graphs": []})["mode"] == "open"

    def test_set_acquisition_pinned_requires_graph(self):
        client = _FakeClient()
        with pytest.raises(ValueError, match="pinned mode needs"):
            set_acquisition(client, "pinned", [])

    def test_set_acquisition_stores_on_facade(self):
        client = _FakeClient()
        out = set_acquisition(client, "pinned", ["experiment-history"])
        assert out == {"mode": "pinned", "graphs": ["experiment-history"]}
        assert acquisition_constraints(client)["mode"] == "pinned"

    def test_describe(self):
        assert "disabled" in describe_acquisition({"mode": "disabled", "graphs": []})
        assert "experiment-history" in describe_acquisition(
            {"mode": "pinned", "graphs": ["experiment-history"]}
        )
        assert "open" in describe_acquisition({"mode": "open", "graphs": []})


# ── resolve_ingest_target ─────────────────────────────────────────────


class TestIngestTarget:
    def test_open_passes_through(self):
        client = _FakeClient()
        assert resolve_ingest_target(client, None) == (None, None)
        assert resolve_ingest_target(client, "g-1") == ("g-1", None)

    def test_disabled_refuses(self):
        client = _FakeClient()
        set_acquisition(client, "disabled")
        target, err = resolve_ingest_target(client, "g-1")
        assert target is None and "DISABLED" in err

    def test_pinned_allows_pinned_graph(self):
        client = _FakeClient()
        set_acquisition(client, "pinned", ["experiment-history"])
        target, err = resolve_ingest_target(client, "experiment-history")
        assert target == "experiment-history" and err is None

    def test_pinned_refuses_other_graph(self):
        client = _FakeClient()
        set_acquisition(client, "pinned", ["experiment-history"])
        target, err = resolve_ingest_target(client, "institutional")
        assert target is None and "experiment-history" in err

    def test_pinned_single_auto_routes_without_graph_id(self):
        client = _FakeClient()
        set_acquisition(client, "pinned", ["experiment-history"])
        target, err = resolve_ingest_target(client, None)
        assert target == "experiment-history" and err is None

    def test_pinned_multi_requires_explicit_choice(self):
        client = _FakeClient()
        set_acquisition(client, "pinned", ["g1", "g2"])
        target, err = resolve_ingest_target(client, None)
        assert target is None and "g1, g2" in err
        # explicit one of the two is fine
        target, err = resolve_ingest_target(client, "g2")
        assert target == "g2" and err is None

    def test_pinned_id_name_crosswalk(self):
        client = _FakeClient(
            graphs=[{"graph_id": "uuid-9", "name": "experiment-history", "status": "active"}]
        )
        set_acquisition(client, "pinned", ["experiment-history"])
        # agent passes the id, the user pinned the name — must match.
        target, err = resolve_ingest_target(client, "uuid-9")
        assert target == "uuid-9" and err is None

    def test_pinned_case_insensitive(self):
        client = _FakeClient()
        set_acquisition(client, "pinned", ["Experiment-History"])
        target, err = resolve_ingest_target(client, "experiment-history")
        assert target == "experiment-history" and err is None


# ── check_propose ──────────────────────────────────────────────────────


class TestProposeGate:
    def test_open_allows(self):
        client = _FakeClient()
        assert check_propose(client, "any-graph") is None

    def test_disabled_refuses(self):
        client = _FakeClient()
        set_acquisition(client, "disabled")
        err = check_propose(client, "g")
        assert "DISABLED" in err

    def test_pinned_refuses_other_graph(self):
        client = _FakeClient()
        set_acquisition(client, "pinned", ["experiment-history"])
        assert check_propose(client, "experiment-history") is None
        assert "experiment-history" in check_propose(client, "other")


# ── tool-level enforcement ─────────────────────────────────────────────


class TestToolEnforcement:
    def test_ingest_blocked_in_disabled_mode(self):
        client = _FakeClient()
        set_acquisition(client, "disabled")
        out = _ingest_tool(client)(urls=["https://example.org/paper"])
        assert _code(out) == "knowledge_acquisition_blocked"
        assert client.knowledge.ingest_calls == []

    def test_ingest_pinned_routes_into_the_pinned_graph(self):
        client = _FakeClient()
        set_acquisition(client, "pinned", ["experiment-history"])
        out = _ingest_tool(client)(urls=["https://example.org/paper"])
        assert _code(out) == ""
        assert client.knowledge.ingest_calls[0]["graph_id"] == "experiment-history"

    def test_ingest_pinned_wrong_explicit_graph_blocked(self):
        client = _FakeClient()
        set_acquisition(client, "pinned", ["experiment-history"])
        out = _ingest_tool(client)(urls=["https://example.org/p"], graph_id="institutional")
        assert _code(out) == "knowledge_acquisition_blocked"
        assert client.knowledge.ingest_calls == []

    def test_ingest_open_keeps_default_routing(self):
        client = _FakeClient()
        out = _ingest_tool(client)(urls=["https://example.org/paper"])
        assert _code(out) == ""
        assert client.knowledge.ingest_calls[0]["graph_id"] is None

    def test_propose_blocked_in_disabled_mode(self):
        client = _FakeClient()
        set_acquisition(client, "disabled")
        out = _propose_tool(client)(graph_id="g", reason="need more")
        assert _code(out) == "knowledge_acquisition_blocked"
        assert client.knowledge.propose_calls == []

    def test_propose_pinned_graph_still_works(self):
        client = _FakeClient()
        set_acquisition(client, "pinned", ["experiment-history"])
        out = _propose_tool(client)(graph_id="experiment-history", reason="need more")
        assert json.loads(out)["type"] == "knowledge_graph_proposal"
        assert client.knowledge.propose_calls == ["experiment-history"]
