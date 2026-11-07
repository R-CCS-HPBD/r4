"""ADR-108 W1a (2026-11-03): client fixes ahead of the work-level waves.

- D-9: a loop's model calls are gated on the LOOP's project's data class.
  The gate asked the console's current project, so a loop on a sensitive
  project started from another one could reach a public endpoint.
- D-8: ``search_docs`` and the local loop's retrieve honour the user's graph
  constraints, as ``knowledge_query`` does (they passed no ``graphs=``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from test_search_docs_tool import _client as _search_client
from test_search_docs_tool import _passage, _result, _search_docs

from r4 import client as client_module
from r4.client import gate_project
from r4.graph_scope import retrieve_graph_args
from r4.runtime.agent_executor import LocalAgentIterationExecutor

# ── D-9: the gate asks the loop's project ───────────────────────────────


def test_gate_project_names_the_project_inside_and_restores_outside() -> None:
    assert client_module._GATE_PROJECT.get() == ""
    with gate_project("sensitive-proj"):
        assert client_module._GATE_PROJECT.get() == "sensitive-proj"
        with gate_project("inner"):
            assert client_module._GATE_PROJECT.get() == "inner"
        assert client_module._GATE_PROJECT.get() == "sensitive-proj"
    assert client_module._GATE_PROJECT.get() == ""


def test_the_gate_asks_the_named_project_before_the_current_one(monkeypatch) -> None:
    from r4 import project as project_module

    monkeypatch.setattr(
        project_module,
        "get_project_manager",
        lambda: SimpleNamespace(get_current=lambda: "console-proj"),
    )
    gate = SimpleNamespace(_config=SimpleNamespace(bound_source=None, project_id=None))
    assert client_module.Client._gate_project_id(gate) == "console-proj"
    with gate_project("loop-proj"):
        assert client_module.Client._gate_project_id(gate) == "loop-proj"


class _ChatClient:
    """Records the gate project each model call is made under."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(model="m")
        self.seen: list[str] = []

    def chat_completion(self, messages: Any, **kwargs: Any) -> Any:
        self.seen.append(client_module._GATE_PROJECT.get())
        return SimpleNamespace(content='{"hypothesis": "h", "next_step": "n"}')


def test_a_loop_iteration_is_gated_on_the_loops_project() -> None:
    """Fails on the old code: the call ran with no gate project, so the gate
    asked the console's current project."""
    chat = _ChatClient()
    executor = LocalAgentIterationExecutor(chat, "sensitive-proj", model="m")
    executor._call_llm("goal", {"text": "", "citations": {}, "basis": "graph_digest"}, 1)
    assert chat.seen == ["sensitive-proj"]
    assert client_module._GATE_PROJECT.get() == ""


# ── D-8: retrieval honours the user's graph constraints ─────────────────


def _with_constraints(**constraints: Any) -> SimpleNamespace:
    return SimpleNamespace(knowledge=SimpleNamespace(graph_constraints=constraints))


@pytest.mark.parametrize(
    ("constraints", "expected"),
    [
        ({}, {}),
        ({"exclude": ["g-old"]}, {"exclude_graphs": ["g-old"]}),
        ({"include": ["g-a"], "discovery": True}, {}),  # a preference, not a filter
        ({"include": ["g-a"], "discovery": False}, {"graphs": ["g-a"]}),  # pinned
        (
            {"include": ["g-a", " "], "exclude": ["g-b"], "discovery": False},
            {"exclude_graphs": ["g-b"], "graphs": ["g-a"]},
        ),
    ],
)
def test_constraints_map_onto_retrieve(constraints, expected) -> None:
    assert retrieve_graph_args(_with_constraints(**constraints)) == expected


def test_no_constraints_anywhere_sends_nothing(monkeypatch) -> None:
    import r4.graph_constraints_store as store

    monkeypatch.setattr(store, "load_constraints", lambda path=None: {})
    assert retrieve_graph_args(SimpleNamespace()) == {}


def test_search_docs_passes_the_pinned_and_excluded_graphs() -> None:
    client = _search_client(_result([_passage(1)]))
    client.knowledge = SimpleNamespace(
        graph_constraints={"include": ["g-a"], "exclude": ["g-b"], "discovery": False}
    )
    _search_docs(client, "q")
    call = client.rag.calls[0]
    assert call["graphs"] == ["g-a"] and call["exclude_graphs"] == ["g-b"]


def test_the_loop_retrieve_passes_the_excluded_graphs() -> None:
    calls: list[dict[str, Any]] = []

    def retrieve(query: str, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return _result([_passage(1)])

    loop_client = SimpleNamespace(
        config=SimpleNamespace(model="m"),
        rag=SimpleNamespace(retrieve=retrieve),
        knowledge=SimpleNamespace(graph_constraints={"exclude": ["g-b"]}),
    )
    LocalAgentIterationExecutor(loop_client, "proj", model="m")._retrieve("goal")
    assert calls[0]["exclude_graphs"] == ["g-b"] and "graphs" not in calls[0]
