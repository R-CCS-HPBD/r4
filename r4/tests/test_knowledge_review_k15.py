"""K1.5 minimal review (knowledge plan 2026-10-24).

- ``r4 knowledge review`` lists pending proposals and proposed rows, and
  ``establish`` promotes a proposed loop finding over the human-only
  establish route (no client called those routes before).
- The Companion's agent actor flag covers one mutation only: it was sticky,
  so after one agent ingest every later human mutation went out marked
  ``x-r4-actor: agent``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from r4._cli.knowledge import KnowledgeCommand
from r4._cli.knowledge_review import run_rows
from r4.cli import _build_parser
from r4.companion.knowledge_tools import build_knowledge_tools
from r4.knowledge import KnowledgeClient

PROJECT = "example-project-b"
GRAPH = "g-example-project-b"
FINDING = "art-finding-1"
LOOP = "loop-7"


class _FakeUmrs:
    """The UMRS knowledge routes the review reaches, over one project graph."""

    _api_prefix = "/gw"

    def __init__(self) -> None:
        self.status = {FINDING: "proposed", "loop:loop-7": "proposed", "e-prod": "proposed"}
        self.proposals = {"gp-1": "pending", "gp-2": "pending"}
        self.calls: list[tuple[str, str, dict | None]] = []

    # -- routing -------------------------------------------------------------

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(("GET", url, kwargs.get("headers")))
        path = url.removeprefix("/gw/knowledge/")
        if path == "graphs":
            return {
                "graphs": [
                    {"graph_id": GRAPH, "project_id": PROJECT},
                    {"graph_id": "g-foreign", "project_id": "other"},
                ]
            }
        if path == f"graphs/{GRAPH}/proposals":
            return {
                "proposals": [
                    {
                        "proposal_id": pid,
                        "target_graph_id": GRAPH,
                        "created_by": "agent",
                        "reason": f"extend {pid}",
                    }
                    for pid, state in self.proposals.items()
                    if state == "pending"
                ]
            }
        if path == f"graphs/{GRAPH}/proposed":
            return self._proposed()
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, json: Any = None, **kwargs: Any) -> Any:
        headers = kwargs.get("headers") or {}
        self.calls.append(("POST", url, headers))
        if headers.get("x-r4-actor") == "agent":
            raise PermissionError("agents propose, humans establish")
        path = url.removeprefix("/gw/knowledge/")
        if path.startswith(f"graphs/{GRAPH}/") and path.endswith("/establish"):
            row_id = path.split("/")[3]
            if row_id not in self.status:
                from r4.errors import APIError

                raise APIError(f"Node {row_id} not found", status_code=404)
            self.status[row_id] = "established"
            return {"status": "established", "established_by": "mika"}
        if path.startswith("proposals/") and path.endswith("/decision"):
            pid = path.split("/")[1]
            self.proposals[pid] = "approved" if json["approved"] else "declined"
            return {"proposal_id": pid, "status": self.proposals[pid]}
        raise AssertionError(f"unexpected POST {url}")

    def _proposed(self) -> dict[str, Any]:
        rows = {
            FINDING: {
                "kind": "node",
                "id": FINDING,
                "summary": "MOF-5 binds CO2",
                "origin": "loop",
                "run_id": LOOP,
                "graph_id": GRAPH,
            },
            "loop:loop-7": {
                "kind": "node",
                "id": "loop:loop-7",
                "summary": "loop:loop-7",
                "origin": "loop",
                "run_id": LOOP,
                "graph_id": GRAPH,
            },
            "e-prod": {
                "kind": "edge",
                "id": "e-prod",
                "source_id": FINDING,
                "target_id": "loop:loop-7",
                "summary": "produced_by",
                "origin": "",
                "run_id": "",
                "graph_id": GRAPH,
            },
        }
        live = [row for rid, row in rows.items() if self.status[rid] == "proposed"]
        return {
            "nodes": [r for r in live if r["kind"] == "node"],
            "edges": [r for r in live if r["kind"] == "edge"],
        }


@pytest.fixture
def umrs(monkeypatch) -> _FakeUmrs:
    server = _FakeUmrs()
    knowledge = KnowledgeClient(server)  # type: ignore[arg-type]
    monkeypatch.setattr("r4.client.Client", lambda: SimpleNamespace(knowledge=knowledge))
    return server


def _review(*argv: str) -> int:
    args = _build_parser().parse_args(["knowledge", "review", *argv])
    return KnowledgeCommand().handle(args)


# ── the CLI inbox ───────────────────────────────────────────────────────────


def _inbox_json(capsys) -> dict[str, Any]:
    capsys.readouterr()
    assert _review("--json", "-p", PROJECT) == 0
    return json.loads(capsys.readouterr().out)


def _establish_calls(umrs: _FakeUmrs) -> list[tuple[str, str, dict | None]]:
    return [c for c in umrs.calls if c[1].endswith("/establish")]


def test_a_loop_finding_appears_in_review(umrs, capsys):
    assert _review("-p", PROJECT, "--rows") == 0
    shown = [line.split()[:2] for line in capsys.readouterr().out.splitlines()]
    assert ["node", FINDING] in shown
    row = next(r for r in _inbox_json(capsys)["rows"] if r["id"] == FINDING)
    assert (row["origin"], row["run_id"], row["graph_id"]) == ("loop", LOOP, GRAPH)


def test_the_inbox_lists_proposals_of_the_projects_graphs_only(umrs, capsys):
    inbox = _inbox_json(capsys)
    assert [p["proposal_id"] for p in inbox["proposals"]] == ["gp-1", "gp-2"]
    assert {r["graph_id"] for r in inbox["rows"]} == {GRAPH}


def test_establish_promotes_the_loop_finding(umrs, capsys):
    assert _review("establish", FINDING, "-p", PROJECT) == 0
    assert f"✓ established node {FINDING}" in capsys.readouterr().out
    expected = ("POST", f"/gw/knowledge/graphs/{GRAPH}/nodes/{FINDING}/establish", {})
    assert _establish_calls(umrs) == [expected]
    assert FINDING not in {r["id"] for r in _inbox_json(capsys)["rows"]}


def test_establish_run_promotes_every_row_of_the_run(umrs, capsys):
    assert _review("-p", PROJECT, "establish", "--run", LOOP) == 0
    assert set(umrs.status.values()) == {"established"}
    urls = [c[1] for c in umrs.calls if c[0] == "POST"]
    assert urls[-1] == f"/gw/knowledge/graphs/{GRAPH}/edges/e-prod/establish"


def test_approve_and_reject_name_the_proposal(umrs, capsys):
    assert _review("approve", "gp-2", "-p", PROJECT) == 0
    assert _review("reject", "gp-1", "-p", PROJECT) == 0
    assert umrs.proposals == {"gp-1": "declined", "gp-2": "approved"}
    assert "✓ proposal gp-2: approved" in capsys.readouterr().out


def test_an_unknown_row_is_said_not_guessed(umrs, capsys):
    assert _review("establish", "art-none", "-p", PROJECT) == 1
    assert "No proposed row for art-none" in capsys.readouterr().err
    # The establish route is asked (node, then edge) and answers 404 for both.
    assert [c[1].rsplit("/", 3)[-3] for c in umrs.calls if c[0] == "POST"] == ["nodes", "edges"]


def test_establish_needs_a_row_or_a_run(umrs, capsys):
    assert _review("establish", "-p", PROJECT) == 2
    assert "--run" in capsys.readouterr().err


def test_run_rows_takes_the_runs_nodes_then_edges_leaving_them():
    rows = [
        {"kind": "edge", "id": "e1", "source_id": "n1", "run_id": ""},
        {"kind": "node", "id": "n1", "run_id": "r"},
        {"kind": "node", "id": "n2", "run_id": "other"},
        {"kind": "edge", "id": "e2", "source_id": "n2", "run_id": ""},
    ]
    assert [r["id"] for r in run_rows(rows, "r")] == ["n1", "e1"]


# ── the client ──────────────────────────────────────────────────────────────


class _Recorder:
    _api_prefix = "/gw"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return {"nodes": [], "edges": []}

    def post(self, url: str, json: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"method": "POST", "url": url, "json": json, **kwargs})
        return {"job_id": "j-1"}


def test_establish_is_the_persons_act_even_on_an_agent_marked_client():
    inner = _Recorder()
    knowledge = KnowledgeClient(inner)  # type: ignore[arg-type]
    knowledge.actor, knowledge.session_id = "agent", "s-1"
    knowledge.establish("g-1", "e-9", kind="edge")
    call = inner.calls[0]
    assert call["url"] == "/gw/knowledge/graphs/g-1/edges/e-9/establish"
    assert call["headers"] == {"x-r4-session-id": "s-1"}
    with pytest.raises(ValueError):
        knowledge.establish("g-1", "x", kind="claim")


def test_graph_proposed_reads_the_review_route():
    inner = _Recorder()
    KnowledgeClient(inner).graph_proposed("g-1", limit=50)  # type: ignore[arg-type]
    assert inner.calls[0]["url"] == "/gw/knowledge/graphs/g-1/proposed"
    assert inner.calls[0]["params"] == {"limit": 50}


# ── the agent actor flag ────────────────────────────────────────────────────


def _companion(knowledge: KnowledgeClient) -> dict[str, Any]:
    client = SimpleNamespace(config=SimpleNamespace(project_id="proj-1"), knowledge=knowledge)
    return {t["name"]: t for t in build_knowledge_tools(client)}


def test_one_agent_mutation_does_not_mark_the_next_human_one(monkeypatch):
    inner = _Recorder()
    knowledge = KnowledgeClient(inner)  # type: ignore[arg-type]
    seen: list[str | None] = []
    monkeypatch.setattr(
        knowledge, "ingest_paths", lambda *a, **k: seen.append(knowledge.actor) or {"job_id": "j"}
    )
    _companion(knowledge)["knowledge_ingest"]["execute"](urls="https://example.org/x")
    assert seen == ["agent"]
    assert knowledge.actor is None
    knowledge.graph_create("human-graph", project_id="proj-1")
    assert inner.calls[-1]["headers"] is None


def test_the_flag_is_reset_when_the_agent_mutation_fails(monkeypatch):
    knowledge = KnowledgeClient(_Recorder())  # type: ignore[arg-type]

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("server down")

    monkeypatch.setattr(knowledge, "ingest_paths", _boom)
    _companion(knowledge)["knowledge_ingest"]["execute"](urls="https://example.org/x")
    assert knowledge.actor is None


# ── review of K1 part A (2026-10-24) ─────────────────────────────────────────


class _BigUmrs(_FakeUmrs):
    """A graph whose newest 200 rows are 250 extracted entities of one upload,
    with the loop finding behind them; plus the caller's personal graph."""

    def __init__(self) -> None:
        super().__init__()
        self.paper = {f"node-sem-{i}-art-paper": "proposed" for i in range(250)}
        self.status.update(self.paper)
        self.proposed_params: list[dict[str, Any]] = []
        self.read_paths: list[str] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        path = url.removeprefix("/gw/knowledge/")
        self.read_paths.append(path)
        if path == "graphs" and "project_id" not in (kwargs.get("params") or {}):
            return {"graphs": [{"graph_id": "g-mine", "project_id": None, "kind": "personal"}]}
        if path == "graphs/g-mine/proposals":
            return {"proposals": []}
        if path == "graphs/g-mine/proposed":
            return {"nodes": [], "edges": [], "truncated": False}
        if path == f"graphs/{GRAPH}/proposed":
            return self._window(kwargs.get("params") or {})
        return super().get(url, **kwargs)

    def _window(self, params: dict[str, Any]) -> dict[str, Any]:
        self.proposed_params.append(dict(params))
        base = super()._proposed()
        paper = [
            {
                "kind": "node",
                "id": rid,
                "summary": "entity",
                "origin": "extracted",
                "run_id": "art-paper",
                "graph_id": GRAPH,
            }
            for rid, state in self.paper.items()
            if state == "proposed" and self.status[rid] == "proposed"
        ]
        nodes = paper + base["nodes"]
        run = params.get("run_id")
        if run:
            nodes = [n for n in nodes if n["run_id"] == run]
        limit = int(params.get("limit", 200))
        return {"nodes": nodes[:limit], "edges": base["edges"], "truncated": len(nodes) > limit}


@pytest.fixture
def big(monkeypatch) -> _BigUmrs:
    server = _BigUmrs()
    knowledge = KnowledgeClient(server)  # type: ignore[arg-type]
    monkeypatch.setattr("r4.client.Client", lambda: SimpleNamespace(knowledge=knowledge))
    return server


def test_the_inbox_says_when_a_graph_has_more_rows_than_shown(big, capsys):
    """It printed "Proposed rows (200)" with no hint that more existed."""
    assert _review("-p", PROJECT) == 0
    assert f"Graph {GRAPH} has more proposed rows than shown" in capsys.readouterr().out


def test_the_inbox_groups_rows_by_run_with_their_origin(big, capsys):
    """200 lines of ``origin=- run=art-…`` buried the loop finding."""
    assert _review("-p", PROJECT) == 0
    out = capsys.readouterr().out
    assert "run art-paper  200 row(s)  origin=extracted" in out
    assert len(out.splitlines()) < 12


def test_establish_reaches_a_row_past_the_window(big, capsys):
    assert _review("establish", FINDING, "-p", PROJECT) == 0
    assert big.status[FINDING] == "established"


def test_establish_run_reaches_every_row_of_a_large_run(big, capsys):
    assert _review("-p", PROJECT, "establish", "--run", "art-paper") == 0
    assert set(big.paper) <= {rid for rid, s in big.status.items() if s == "established"}
    assert big.status[FINDING] == "proposed"
    assert {p.get("run_id") for p in big.proposed_params} == {"art-paper"}


def test_the_inbox_reads_the_callers_personal_graphs(big, capsys):
    """Personal graphs are stored with no project: the project listing never
    returned them, so their proposed rows were invisible without ``-g``."""
    assert _review("-p", PROJECT) == 0
    assert "graphs/g-mine/proposed" in big.read_paths


def test_approve_and_reject_need_no_current_project(umrs, capsys, monkeypatch):
    monkeypatch.setattr("r4._cli.knowledge._current_project_id", lambda: None)
    assert _review("approve", "gp-2") == 0
    assert umrs.proposals["gp-2"] == "approved"


def test_the_help_reads_for_people_not_developers(capsys):
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["knowledge", "review", "--help"])
    out = capsys.readouterr().out
    for note in ("D-35", "K1.5", "Subtree", "``", "REVIEW_GRAPH"):
        assert note not in out
    assert "--json" in out and "Print the result as JSON" in out
