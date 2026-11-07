"""Tests for the Research Runtime API (contract, UMRS adapter, local runtime, resolver)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.runtime import (
    HybridRuntime,
    LocalRuntime,
    LoopSpec,
    LoopState,
    RuntimeResolutionError,
    UmrsRuntime,
    get_runtime,
    resolve_mode,
)

SPEC = LoopSpec(project_id="proj-1", research_goal="test goal", max_iterations=3)


def _wait_terminal(runtime: LocalRuntime, loop_id: str, timeout: float = 5.0) -> LoopState:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = runtime.get_loop(loop_id)
        if state.status in {"stopped", "completed", "error"}:
            return state
        time.sleep(0.01)
    raise AssertionError(f"loop {loop_id} did not reach terminal state")


class TestLoopStateMapping:
    def test_from_mapping_reads_umrs_payload(self):
        payload = {
            "loop_id": "loop-1",
            "project_id": "p",
            "research_goal": "g",
            "status": "running",
            "phase": "experiment_execution",
            "current_iteration": 4,
            "max_iterations": 10,
            "total_findings": 2,
            "success_rate": 0.5,
        }
        state = LoopState.from_mapping(payload)
        assert state.loop_id == "loop-1"
        assert state.total_findings == 2
        assert state.success_rate == 0.5
        assert state.to_dict()["loop_id"] == "loop-1"

    def test_from_mapping_tolerates_sparse_payload(self):
        state = LoopState.from_mapping({"loop_id": "loop-2", "status": "stopped"})
        assert state.project_id == ""
        assert state.max_iterations == 10


class TestLocalRuntime:
    def test_parallel_tracks_refused_by_name(self):
        from dataclasses import replace

        with pytest.raises(ValueError, match="UMRS engine only"):
            LocalRuntime().start_loop(replace(SPEC, max_tracks=2))

    def test_capabilities(self, tmp_path):
        caps = LocalRuntime(tmp_path / "rt.db").capabilities()
        assert caps.location == "local"
        assert caps.durable is True
        assert caps.distributed is False
        assert caps.backends == ()

    def _assert_completed(self, runtime, loop_id):
        final = _wait_terminal(runtime, loop_id)
        assert final.status == "completed"
        assert final.phase == "completed"
        assert final.current_iteration == 3
        iterations = runtime.list_iterations(loop_id)
        assert [it.iteration_number for it in iterations] == [3, 2, 1]  # DESC order
        assert all(it.status == "completed" for it in iterations)

    def test_full_lifecycle_completes(self, tmp_path):
        runtime = LocalRuntime(tmp_path / "rt.db")
        state = runtime.start_loop(SPEC)
        assert state.status == "running"
        assert state.loop_id.startswith("local-")
        self._assert_completed(runtime, state.loop_id)

    def test_executor_receives_loop_context(self, tmp_path):
        seen = []

        def executor(loop_id, number, goal):
            seen.append((loop_id, number, goal))
            return {"artifact": number}

        runtime = LocalRuntime(tmp_path / "rt.db")
        state = runtime.start_loop(SPEC, executor=executor)
        _wait_terminal(runtime, state.loop_id)
        assert [n for _, n, _ in seen] == [1, 2, 3]
        assert all(g == "test goal" for _, _, g in seen)
        summary = runtime.list_iterations(state.loop_id, limit=1)[0].summary
        assert summary["artifact"] == 3

    def test_pause_resume(self, tmp_path):
        gate = threading.Event()

        def executor(loop_id, number, goal):
            gate.wait(timeout=2.0)
            return {}

        runtime = LocalRuntime(tmp_path / "rt.db")
        state = runtime.start_loop(SPEC, executor=executor)
        paused = runtime.pause_loop(state.loop_id)
        assert paused.status == "paused"
        resumed = runtime.resume_loop(state.loop_id)
        assert resumed.status == "running"
        gate.set()
        assert _wait_terminal(runtime, state.loop_id).status == "completed"

    def test_stop_is_terminal_and_idempotent(self, tmp_path):
        blocker = threading.Event()

        def executor(loop_id, number, goal):
            blocker.wait(timeout=2.0)
            return {}

        runtime = LocalRuntime(tmp_path / "rt.db")
        state = runtime.start_loop(SPEC, executor=executor)
        stopped = runtime.stop_loop(state.loop_id)
        assert stopped.status == "stopped"
        blocker.set()
        assert _wait_terminal(runtime, state.loop_id).status == "stopped"
        assert runtime.stop_loop(state.loop_id).status == "stopped"  # idempotent

    def test_state_survives_reopen(self, tmp_path):
        db = tmp_path / "rt.db"
        LocalRuntime(db).start_loop(SPEC)
        reopened = LocalRuntime(db)
        loops = reopened.list_loops("proj-1")
        assert len(loops) == 1
        assert loops[0].research_goal == "test goal"

    def test_start_validates_input(self, tmp_path):
        runtime = LocalRuntime(tmp_path / "rt.db")
        with pytest.raises(ValueError):
            runtime.start_loop(LoopSpec(project_id="p", research_goal="  "))
        with pytest.raises(ValueError):
            runtime.start_loop(LoopSpec(project_id="p", research_goal="g", max_iterations=0))

    def test_local_runtime_inbox_is_empty_and_names_umrs_for_unknown_ids(self, tmp_path):
        # ADR-092 slice 6 changed this pin: a local loop that runs a plan
        # parks on its own record's approvals list, so the runtime has an
        # inbox. A plain loop never parks, and an id it does not hold is a
        # LookupError that points at UMRS (server-hosted loops' inbox).
        runtime = LocalRuntime(tmp_path / "rt.db")
        assert runtime.capabilities().approvals is True
        assert runtime.list_approvals() == []
        with pytest.raises(LookupError, match="UMRS"):
            runtime.decide_approval("appr-1", "once")

    def test_unknown_loop_raises(self, tmp_path):
        with pytest.raises(LookupError):
            LocalRuntime(tmp_path / "rt.db").get_loop("local-nope")


class TestUmrsRuntime:
    def _client(self):
        client = MagicMock()
        client.start_loop.return_value = {
            "loop_id": "loop-1",
            "status": "running",
            "message": "started",
        }
        client.get_loop_status.return_value = {
            "loop_id": "loop-1",
            "project_id": "proj-1",
            "research_goal": "test goal",
            "status": "running",
            "phase": "planning",
            "current_iteration": 1,
            "max_iterations": 3,
            "total_findings": 5,
        }
        client.list_loops.return_value = {"loops": [{"loop_id": "loop-1", "status": "running"}]}
        client.list_loop_iterations.return_value = [
            {"iteration_id": "i1", "iteration_number": 1, "status": "completed"}
        ]
        return client

    def test_adapter_maps_client_calls(self):
        client = self._client()
        runtime = UmrsRuntime(client)
        state = runtime.start_loop(SPEC)
        client.start_loop.assert_called_once_with(
            "proj-1", "test goal", max_iterations=3, approved=False
        )
        assert state.loop_id == "loop-1"
        assert state.research_goal == "test goal"  # backfilled from spec

        fetched = runtime.get_loop("loop-1")
        assert fetched.total_findings == 5
        assert runtime.list_loops()[0].loop_id == "loop-1"
        assert runtime.list_iterations("loop-1")[0].iteration_number == 1

        runtime.stop_loop("loop-1")
        client.stop_loop.assert_called_once_with("loop-1")

    def test_rejects_inprocess_executor(self):
        runtime = UmrsRuntime(self._client())
        with pytest.raises(ValueError, match="executor"):
            runtime.start_loop(SPEC, executor=lambda *a: {})

    def test_start_state_carries_resolved_workflow_in_metadata(self):
        client = self._client()
        client.start_loop.return_value = {
            "loop_id": "loop-1",
            "workflow_name": "wf-a",
            "status": "running",
        }
        state = UmrsRuntime(client).start_loop(SPEC)
        assert state.metadata["workflow_name"] == "wf-a"
        assert state.max_iterations == SPEC.max_iterations

    def test_workflow_name_forwarded_only_when_set(self):
        from dataclasses import replace

        client = self._client()
        runtime = UmrsRuntime(client)
        runtime.start_loop(SPEC)
        assert "workflow_name" not in client.start_loop.call_args.kwargs
        runtime.start_loop(replace(SPEC, workflow_name="demo08_yield_eval"))
        assert client.start_loop.call_args.kwargs["workflow_name"] == "demo08_yield_eval"

    def test_max_tracks_forwarded_only_above_one(self):
        from dataclasses import replace

        client = self._client()
        runtime = UmrsRuntime(client)
        runtime.start_loop(SPEC)
        assert "max_tracks" not in client.start_loop.call_args.kwargs
        runtime.start_loop(replace(SPEC, max_tracks=2))
        assert client.start_loop.call_args.kwargs["max_tracks"] == 2

    def test_approval_inbox_maps_to_client(self):
        client = self._client()
        client.list_loop_approvals.return_value = {
            "pending": [{"id": "appr-1", "loop_id": "loop-1"}],
            "durability": "postgres",
        }
        client.decide_loop_approval.return_value = {"approval_id": "appr-1", "status": "approved"}
        runtime = UmrsRuntime(client)
        assert runtime.capabilities().approvals is True
        assert runtime.list_approvals("loop-1") == [{"id": "appr-1", "loop_id": "loop-1"}]
        client.list_loop_approvals.assert_called_once_with(loop_id="loop-1")
        assert runtime.decide_approval("appr-1", "once")["status"] == "approved"
        client.decide_loop_approval.assert_called_once_with("appr-1", "once")

    def test_capabilities(self):
        caps = UmrsRuntime(self._client()).capabilities()
        assert caps.location == "umrs"
        assert caps.durable and caps.distributed


class TestResolver:
    def test_mode_precedence(self, monkeypatch, tmp_path):
        monkeypatch.delenv("R4_RUNTIME_MODE", raising=False)
        assert resolve_mode() == "umrs"
        assert resolve_mode(project_metadata={"runtime": {"mode": "local"}}) == "local"
        monkeypatch.setenv("R4_RUNTIME_MODE", "hybrid")
        assert resolve_mode() == "hybrid"
        assert resolve_mode(explicit="local") == "local"  # explicit wins

    def test_invalid_mode_rejected(self):
        with pytest.raises(RuntimeResolutionError):
            resolve_mode(explicit="slurm")

    def test_local_runtime_needs_no_client(self, tmp_path):
        runtime = get_runtime(mode="local", local_db_path=tmp_path / "rt.db")
        assert isinstance(runtime, LocalRuntime)

    def test_umrs_mode_wraps_client(self):
        runtime = get_runtime(mode="umrs", client=SimpleNamespace())
        assert isinstance(runtime, UmrsRuntime)

    def test_umrs_mode_requires_client(self):
        with pytest.raises(RuntimeResolutionError):
            get_runtime(mode="umrs")

    def test_hybrid_falls_back_to_local_only_without_client(self, tmp_path):
        """Milestone 4 evolution: hybrid always builds a HybridRuntime;
        without a client it becomes local-only inside (no dual-authority)."""
        runtime = get_runtime(
            mode="hybrid",
            client_factory=MagicMock(side_effect=RuntimeError("no gateway")),
            local_db_path=tmp_path / "rt.db",
        )
        assert isinstance(runtime, HybridRuntime)
        assert runtime.capabilities().distributed is False
        assert runtime.capabilities().backends == ("local",)
