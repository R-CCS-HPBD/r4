"""Milestone 3: LocalAgentIterationExecutor — agentic iterations on LocalRuntime."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.chat_types import ChatResponse
from r4.runtime.agent_executor import (
    LocalAgentIterationExecutor,
    _normalize_iteration_requires,
)

GOAL = "Develop a digital filter for MERMAID seismic peak extraction"


def _client_with_graph_and_chat(chat_payload: dict | Exception):
    graph = {
        "nodes": [
            {"id": "n1", "type": "paper", "name": "MERMAID seismic hydrophone catalog"},
            {"id": "n2", "type": "hypothesis", "name": "1 Hz P-wave filtering improves picks"},
        ],
        "edges": [{"from": "n2", "to": "n1", "label": "derives_from"}],
    }
    client = SimpleNamespace(
        config=SimpleNamespace(project_id="mermaid-phase"),
        knowledge=MagicMock(get_graph=MagicMock(return_value=graph)),
    )
    if isinstance(chat_payload, Exception):
        client.chat_completion = MagicMock(side_effect=chat_payload)
    else:
        client.chat_completion = MagicMock(
            return_value=ChatResponse(content=json.dumps(chat_payload), model="m")
        )
    return client, graph


def test_agentic_iteration_records_hypothesis_and_digest():
    client, _graph = _client_with_graph_and_chat(
        {
            "hypothesis": "A 0.5–2 Hz zero-phase bandpass isolates P-wave onsets",
            "evidence_ids": ["n1", "n2"],
            "evaluation": "Catalog supports the 1 Hz P model; next verify noise PSD.",
            "next_step": "pull two example SAC segments",
        }
    )
    executor = LocalAgentIterationExecutor(client, "mermaid-phase", model="m")

    summary = executor("loop-x", 1, GOAL)

    assert summary["llm"] is True
    assert summary["phase"] == "agent_iteration"
    assert summary["hypothesis"].startswith("A 0.5–2 Hz")
    assert summary["evidence_ids"] == ["n1", "n2"]
    assert summary["next_step"] == "pull two example SAC segments"
    assert summary["graph_nodes"] == 2
    assert summary["goal_matches"] >= 1


def test_memory_accumulates_across_iterations_and_prevents_repeats():
    client, _graph = _client_with_graph_and_chat(
        {"hypothesis": "h2", "evidence_ids": [], "evaluation": "e", "next_step": "n"}
    )
    executor = LocalAgentIterationExecutor(client, "mermaid-phase")

    executor("loop-x", 1, GOAL)
    executor("loop-x", 2, GOAL)

    assert [h["iteration"] for h in executor.hypotheses] == [1, 2]
    messages = client.chat_completion.call_args_list[1].args[0]
    assert "do NOT repeat" in messages[1].content


def test_fallback_when_chat_fails_records_digest():
    client, _graph = _client_with_graph_and_chat(ConnectionError("endpoint down"))
    executor = LocalAgentIterationExecutor(client, "mermaid-phase")

    summary = executor("loop-x", 1, GOAL)

    assert summary["llm"] is False
    assert summary["graph_nodes"] == 2
    assert "endpoint down" in summary["note"]
    assert executor.hypotheses == []


def test_fallback_when_reply_is_not_json():
    client, _graph = _client_with_graph_and_chat(ConnectionError())
    client.chat_completion = MagicMock(return_value=SimpleNamespace(content="no json here"))
    executor = LocalAgentIterationExecutor(client, "mermaid-phase")

    summary = executor("loop-x", 1, GOAL)

    assert summary["llm"] is False
    assert summary["graph_edges"] == 1


def _wait_for_local_completion(tmp_path, loop_id, timeout=5.0):
    """Poll the local runtime store until the loop settles; return final state."""
    from r4.runtime import LocalRuntime

    runtime = LocalRuntime(tmp_path / "loops.db")
    deadline = time.time() + timeout
    state = runtime.get_loop(loop_id)
    while state.status not in {"completed", "stopped", "error"} and time.time() < deadline:
        time.sleep(0.02)
        state = runtime.get_loop(loop_id)
    return state


def test_loop_completes_end_to_end_through_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("R4_RUNTIME_MODE", "local")
    monkeypatch.setenv("R4_RUNTIME_DB", str(tmp_path / "loops.db"))

    client, _graph = _client_with_graph_and_chat(
        {"hypothesis": "phone-borne infrasound can be suppressed", "evaluation": "ok"}
    )
    from r4.companion.research_loop_tools import build_research_loop_tools

    tools = {t["name"]: t for t in build_research_loop_tools(client)}
    started = json.loads(
        tools["research_loop_start"]["execute"](research_goal=GOAL, max_iterations=2)
    )
    assert started["runtime"] == "local"

    state = _wait_for_local_completion(tmp_path, started["loop_id"])
    assert state.status == "completed"

    from r4.runtime import LocalRuntime

    iterations = LocalRuntime(tmp_path / "loops.db").list_iterations(started["loop_id"])
    assert len(iterations) == 2
    assert all(it.summary.get("llm") is True for it in iterations)
    assert iterations[0].summary.get("hypothesis")


def test_iteration_requires_kept_while_llm_true():
    client, _ = _client_with_graph_and_chat(
        {
            "hypothesis": "Apply matched-filter bank over 0.3-3 Hz",
            "evidence_ids": ["n1"],
            "evaluation": "good",
            "next_step": "compute wavelet PSD on a GPU node",
            "requires": {"gpu": 1, "cores": 32, "cluster": True},
        }
    )
    executor = LocalAgentIterationExecutor(client, "mermaid-phase", model="m")
    summary = executor("loop-x", 2, GOAL)

    assert summary["llm"] is True
    assert summary["requires"] == {"gpu": 1, "cores": 32, "cluster": True}
    assert executor.hypotheses[-1]["requires"]["gpu"] == 1


def test_iteration_requires_dropped_when_falsy_or_absent():
    client, _ = _client_with_graph_and_chat(
        {
            "hypothesis": "prefilter PSD before matched bank",
            "evidence_ids": ["n2"],
            "evaluation": "light compute",
            "next_step": "run locally",
            "requires": {"gpu": 0, "cluster": False},
        }
    )
    executor = LocalAgentIterationExecutor(client, "mermaid-phase", model="m")
    summary = executor("loop-x", 1, GOAL)

    assert "requires" not in summary
    assert "requires" not in executor.hypotheses[-1]


def test_normalize_iteration_requires_rules():
    assert _normalize_iteration_requires(None) == {}
    assert _normalize_iteration_requires("gpu") == {}
    assert _normalize_iteration_requires({"gpu": 1, "notes": "x"}) == {"gpu": 1}
    assert _normalize_iteration_requires({"gpu": 0, "cluster": False}) == {}
    assert _normalize_iteration_requires({"cores": 0, "slurm": True}) == {"slurm": True}
