"""Shared graph-constraints: store round-trip, toggle cycle, explorer picker."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

from r4.graph_constraints_store import (
    empty_constraints,
    graph_state,
    load_constraints,
    save_constraints,
    toggle_graph,
)


class TestStore:
    def test_missing_file_yields_neutral_defaults(self, tmp_path):
        loaded = load_constraints(tmp_path / "nope.json")
        assert loaded == empty_constraints()

    def test_round_trip(self, tmp_path):
        path = tmp_path / "knowledge_graph_constraints.json"
        save_constraints(
            {"include": ["experiment-history"], "exclude": ["institutional"], "discovery": False},
            path,
        )
        loaded = load_constraints(path)
        assert loaded["include"] == ["experiment-history"]
        assert loaded["exclude"] == ["institutional"]
        assert loaded["discovery"] is False

    def test_invalid_json_is_neutral(self, tmp_path):
        path = tmp_path / "knowledge_graph_constraints.json"
        path.write_text("{not json")
        assert load_constraints(path)["discovery"] is True

    def test_toggle_cycle(self):
        state = toggle_graph("experiment-history", empty_constraints())
        assert graph_state("experiment-history", state) == "include"
        state = toggle_graph("experiment-history", state)
        assert graph_state("experiment-history", state) == "exclude"
        state = toggle_graph("experiment-history", state)
        assert graph_state("experiment-history", state) == "neutral"


# ── Explorer picker (providers mirror the CLI wiring) ───────────────────────


class _FakeKnowledge:
    def graphs(self, **kwargs):
        return [
            {
                "graph_id": "g-a",
                "name": "project-a",
                "kind": "project",
                "project_id": "proj-a",
                "status": "active",
            },
            {
                "graph_id": "g-e",
                "name": "experiment-history",
                "kind": "experiment",
                "project_id": None,
                "status": "active",
            },
        ]

    def graph_info(self, graph_id):
        return {
            "graph_id": graph_id,
            "name": "experiment-history",
            "kind": "experiment",
            "status": "active",
            "project_id": None,
            "description": "Experiments",
        }

    def status(self, project_id):
        return {"artifacts": []}

    def findings(self, project_id):
        return []

    def memory(self, project_id):
        return {"memories": []}

    def list_plans(self, project_id):
        return []

    def readiness(self, project_id):
        return {"readiness_level": "ready"}

    def export(self, project_id):
        return None


def _explore_app(saved: dict, persist: list):
    """Explorer wired like _handle_explore, with fake knowledge + spies."""
    from r4._cli.knowledge import _graph_detail, _graph_rows
    from r4.graph_constraints_store import toggle_graph as _cycle
    from r4.knowledge_explorer.app import ExploreApp

    knowledge = _FakeKnowledge()
    constraints = dict(saved)

    def project_data() -> dict:
        return {
            "artifacts": [],
            "findings": [],
            "memory": [],
            "plans": [],
            "readiness_level": "ready",
            "graphs": knowledge.graphs(),
        }

    def documents(section):
        if section == "graphs":
            return _graph_rows(knowledge.graphs(), constraints)
        return []

    def detail(section, doc_id):
        if section == "graphs":
            return _graph_detail(knowledge, doc_id, constraints)
        return {}

    def toggle(gid: str):
        nonlocal constraints
        graph = next(
            (g for g in knowledge.graphs() if g["graph_id"] == gid), {"graph_id": gid, "name": gid}
        )
        constraints = _cycle(graph["name"], constraints)
        persist.append(dict(constraints))
        return constraints

    def toggle_discovery():
        nonlocal constraints
        constraints = {**constraints, "discovery": not constraints["discovery"]}
        persist.append(dict(constraints))
        return constraints

    return ExploreApp(
        project_id="proj-a",
        project_data_provider=project_data,
        documents_provider=documents,
        detail_provider=detail,
        toggle_graph_provider=toggle,
        toggle_discovery_provider=toggle_discovery,
    )


class TestExplorerGraphPicker:
    async def test_graph_rows_show_state_markers(self):
        from textual.widgets import ListView

        app = _explore_app(
            {"include": ["experiment-history"], "exclude": ["project-a"], "discovery": True},
            [],
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._load_documents("graphs")
            await pilot.pause()
            docs = app.query_one("#docs", ListView)
            titles = [str(c.children[0].render()) for c in docs.children]
            # discovery pseudo-row first
            assert "auto-discovery: on" in titles[0]
            assert "+" in titles[2]  # experiment-history included
            assert "−" in titles[1]  # project-a excluded

    async def test_toggle_graph_cycles_and_persists(self):
        from textual.widgets import ListView

        persisted: list = []
        app = _explore_app(empty_constraints(), persisted)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._load_documents("graphs")
            await pilot.pause()
            docs = app.query_one("#docs", ListView)

            async def _press_on_first_graph() -> None:
                # reload resets docs.index to 0 (discovery pseudo-row)
                docs.index = 1
                app.action_toggle_graph()
                await pilot.pause()

            await _press_on_first_graph()
            assert persisted[-1]["include"] == ["project-a"]
            await _press_on_first_graph()
            assert persisted[-1]["exclude"] == ["project-a"]
            await _press_on_first_graph()
            assert persisted[-1]["include"] == []
            assert persisted[-1]["exclude"] == []

    async def test_toggle_discovery_flips_flag(self):
        persisted: list = []
        app = _explore_app(empty_constraints(), persisted)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_toggle_discovery()
            await pilot.pause()
            assert persisted[-1]["discovery"] is False


class TestFallbacks:
    def test_companion_merge_uses_persisted_store(self, tmp_path, monkeypatch):
        from r4.companion.knowledge_graph_tools import _merged_constraints

        save_constraints(
            {"include": ["experiment-history"], "exclude": ["institutional"], "discovery": False},
            tmp_path / "knowledge_graph_constraints.json",
        )
        monkeypatch.setenv("R4_STATE_DIR", str(tmp_path))
        client = SimpleNamespace(knowledge=SimpleNamespace(graph_constraints={}))
        include, exclude, only, discovery = _merged_constraints(client, None, None, None, None)
        assert include == ["experiment-history"]
        assert exclude == ["institutional"]
        assert only == []
        assert discovery is False

    def test_cli_query_falls_back_to_store(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("R4_STATE_DIR", str(tmp_path))
        save_constraints(
            {"include": ["experiment-history"], "exclude": [], "discovery": True},
            tmp_path / "knowledge_graph_constraints.json",
        )

        seen = {}

        class _FakeKnowledge:
            def query(self, query, **kwargs):
                seen.update(kwargs)
                return {"query": {"results": [], "selected_graphs": [], "excluded_graphs": []}}

        class _FakeClient:
            knowledge = _FakeKnowledge()

        monkeypatch.setattr("r4.client.Client", lambda: _FakeClient())
        from r4._cli.knowledge import KnowledgeCommand

        # Facade unified search: 'query' became 'search' on 2026-09-13 when
        # the WP-15 fold took 'query' for the corpus rag flow.
        rc = KnowledgeCommand().handle(
            argparse.Namespace(
                knowledge_command="search",
                query="fwi?",
                project_id="proj-a",
                scope="all",
                graphs=None,
                exclude_graphs=None,
                limit=5,
                as_json=False,
            )
        )
        assert rc == 0
        assert seen["graphs"] == ["experiment-history"]
