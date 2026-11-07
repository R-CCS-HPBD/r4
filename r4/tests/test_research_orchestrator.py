"""Tests for the autonomous research orchestrator and A2L client.

Covers:
- ResearchBudget.is_exhausted() boundary conditions
- ResearchSession / IterationRecord model behavior
- A2LClient HTTP behavior with a mocked transport (success + graceful degradation)
- ResearchOrchestrator construction and lazy A2L wiring
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.a2l_client import A2LClient, ArtifactRef, ExecutionRef
from r4.agent import (
    OrchestratorStatus,
    ResearchBudget,
    ResearchOrchestrator,
    ResearchSession,
)
from r4.agent.models import WorkflowDefinition
from r4.agent.research_orchestrator import IterationRecord

# ── ResearchBudget ──────────────────────────────────────────────────────────


def test_budget_not_exhausted_when_under_all_limits():
    budget = ResearchBudget()
    exhausted, reason = budget.is_exhausted(
        iterations=1, elapsed=10.0, total_tokens=100, consecutive_failures=0
    )
    assert exhausted is False
    assert reason == ""


def test_budget_exhausted_on_max_iterations():
    budget = ResearchBudget(max_iterations=3)
    exhausted, reason = budget.is_exhausted(
        iterations=3, elapsed=0.0, total_tokens=0, consecutive_failures=0
    )
    assert exhausted is True
    assert "iterations" in reason.lower()


def test_budget_exhausted_on_wall_clock():
    budget = ResearchBudget(max_wall_clock_seconds=60.0)
    exhausted, reason = budget.is_exhausted(
        iterations=0, elapsed=60.0, total_tokens=0, consecutive_failures=0
    )
    assert exhausted is True
    assert "wall clock" in reason.lower()


def test_budget_exhausted_on_token_cost():
    budget = ResearchBudget(max_cost_tokens=1000)
    exhausted, reason = budget.is_exhausted(
        iterations=0, elapsed=0.0, total_tokens=1000, consecutive_failures=0
    )
    assert exhausted is True
    assert "token" in reason.lower()


def test_budget_exhausted_on_consecutive_failures():
    budget = ResearchBudget(max_consecutive_failures=3)
    exhausted, reason = budget.is_exhausted(
        iterations=0, elapsed=0.0, total_tokens=0, consecutive_failures=3
    )
    assert exhausted is True
    assert "failures" in reason.lower()


def test_budget_iterations_boundary_just_below():
    budget = ResearchBudget(max_iterations=5)
    exhausted, _ = budget.is_exhausted(
        iterations=4, elapsed=0.0, total_tokens=0, consecutive_failures=0
    )
    assert exhausted is False


# ── ResearchSession / IterationRecord ───────────────────────────────────────


def test_session_defaults():
    session = ResearchSession(goal="test goal")
    assert session.goal == "test goal"
    assert session.status == OrchestratorStatus.IDLE
    assert session.iteration_count == 0
    assert session.last_observation == ""
    assert session.session_id  # auto-generated uuid


def test_session_iteration_count_and_last_observation():
    session = ResearchSession(goal="g")
    plan = WorkflowDefinition(name="p1", description="")
    session.iterations.append(IterationRecord(iteration=1, plan=plan, observation="first obs"))
    session.iterations.append(IterationRecord(iteration=2, plan=plan, observation="second obs"))
    assert session.iteration_count == 2
    assert session.last_observation == "second obs"


def test_iteration_record_defaults():
    plan = WorkflowDefinition(name="p", description="")
    rec = IterationRecord(iteration=1, plan=plan)
    assert rec.execution is None
    assert rec.observation == ""
    assert rec.replan_decision == ""
    assert rec.artifacts_created == []
    assert rec.cost_tokens == 0


# ── A2LClient (mocked HTTP transport) ───────────────────────────────────────


def _make_a2l_client(http_core: MagicMock) -> A2LClient:
    """Build an A2LClient whose parent Client exposes a mocked platform_http."""
    fake_config = SimpleNamespace(gateway_url="http://gw.test:8000")
    fake_client = SimpleNamespace(platform_http=http_core, config=fake_config, _api_prefix="/gw")
    return A2LClient(fake_client)  # type: ignore[arg-type]


def test_a2l_derive_success():
    http = MagicMock()
    http.request.return_value = {
        "id": "art_123",
        "type": "segmentation",
        "uri": "s3://b/out.mask",
        "status": "completed",
        "user_id": "alice",
        "parents": ["art_parent"],
    }
    a2l = _make_a2l_client(http)

    ref = a2l.derive(
        parents=["art_parent"],
        operation="segmentation",
        params={"model": "sam3"},
        type="segmentation",
        uri="s3://b/out.mask",
        user_id="alice",
        input_data_hash="sha256:deadbeef",
    )

    assert isinstance(ref, ArtifactRef)
    assert ref.id == "art_123"
    assert ref.parents == ["art_parent"]
    # Verify the POST went to the derive endpoint (via Gateway /gw/a2l proxy)
    method, url = http.request.call_args.args[0], http.request.call_args.args[1]
    assert method == "POST"
    assert url == "http://gw.test:8000/gw/a2l/artifacts/derive"


def test_a2l_derive_graceful_degradation_on_error():
    http = MagicMock()
    http.request.side_effect = RuntimeError("connection refused")
    a2l = _make_a2l_client(http)

    ref = a2l.derive(
        parents=[],
        operation="op",
        params={},
        type="t",
        uri="s3://b/x",
        user_id="alice",
        input_data_hash="h",
    )
    assert ref is None


def test_a2l_create_execution_returns_id():
    http = MagicMock()
    http.request.return_value = {"execution_id": "exec_abc"}
    a2l = _make_a2l_client(http)

    exec_id = a2l.create_execution(
        agent_name="research-orchestrator",
        goal="Analyze folding",
        user_id="alice",
        model="qwen",
    )
    assert exec_id == "exec_abc"


def test_a2l_create_execution_none_on_failure():
    http = MagicMock()
    http.request.return_value = None
    a2l = _make_a2l_client(http)

    exec_id = a2l.create_execution(agent_name="a", goal="g", user_id="u")
    assert exec_id is None


def test_a2l_update_execution_true_on_success():
    http = MagicMock()
    http.request.return_value = {"ok": True}
    a2l = _make_a2l_client(http)

    ok = a2l.update_execution("exec_abc", status="completed", outputs={"r": 1})
    assert ok is True


def test_a2l_update_execution_false_on_failure():
    http = MagicMock()
    http.request.return_value = None
    a2l = _make_a2l_client(http)

    ok = a2l.update_execution("exec_abc", status="failed", error="boom")
    assert ok is False


def test_a2l_get_execution_parses_trace():
    http = MagicMock()
    http.request.return_value = {
        "execution_id": "exec_abc",
        "agent_name": "research-orchestrator",
        "status": "running",
        "goal": "g",
        "user_id": "alice",
        "trace": [{"iteration": 1, "plan_name": "p1"}],
    }
    a2l = _make_a2l_client(http)

    ref = a2l.get_execution("exec_abc")
    assert isinstance(ref, ExecutionRef)
    assert ref.execution_id == "exec_abc"
    assert ref.trace[0]["plan_name"] == "p1"


def test_a2l_list_artifacts_maps_items():
    http = MagicMock()
    http.request.return_value = {
        "items": [
            {"id": "a1", "type": "t", "uri": "u1", "status": "completed", "user_id": "x"},
            {"id": "a2", "type": "t", "uri": "u2", "status": "pending", "user_id": "x"},
        ]
    }
    a2l = _make_a2l_client(http)

    refs = a2l.list_artifacts(type="t", user_id="x")
    assert refs is not None
    assert [r.id for r in refs] == ["a1", "a2"]


def test_a2l_hash_content_is_sha256():
    import hashlib

    expected = hashlib.sha256(b"hello").hexdigest()
    assert A2LClient.hash_content("hello") == expected


# ── ResearchOrchestrator construction ───────────────────────────────────────


def test_orchestrator_lazy_a2l_property():
    client = MagicMock()
    orch = ResearchOrchestrator(client)
    # Lazy A2L client builds on first access
    a2l_first = orch.a2l
    a2l_second = orch.a2l
    assert isinstance(a2l_first, A2LClient)
    assert a2l_first is a2l_second  # cached


def test_orchestrator_accepts_injected_a2l():
    client = MagicMock()
    injected = MagicMock(spec=A2LClient)
    orch = ResearchOrchestrator(client, a2l=injected)
    assert orch.a2l is injected


def test_orchestrator_uses_autonomous_mode_by_default():
    from r4.agent.core import AgentMode

    client = MagicMock()
    orch = ResearchOrchestrator(client)
    assert orch._config.mode == AgentMode.AUTONOMOUS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
