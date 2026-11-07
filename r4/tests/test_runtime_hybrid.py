"""HybridRuntime routing tests: requires-based venue selection + dual-store verbs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.runtime import HybridRuntime, LocalRuntime, LoopSpec, UmrsRuntime, get_runtime


def _spec(goal: str = "goal", requires=None) -> LoopSpec:
    return LoopSpec(project_id="proj", research_goal=goal, requires=requires or {})


def _umrs_mock_client():
    client = MagicMock()
    client.start_loop.return_value = {
        "loop_id": "loop-remote",
        "status": "running",
        "message": "started remotely",
    }
    client.get_loop_status.return_value = {
        "loop_id": "loop-remote",
        "status": "paused",
        "phase": "observe",
    }
    client.list_loops.return_value = {"loops": [{"loop_id": "loop-remote", "status": "running"}]}
    client.list_loop_iterations.return_value = [
        {"iteration_id": "ri1", "iteration_number": 1, "status": "completed"}
    ]
    return client


@pytest.fixture
def hybrid(tmp_path):
    local = LocalRuntime(tmp_path / "local.db")
    umrs = UmrsRuntime(_umrs_mock_client())
    return HybridRuntime(local, umrs), umrs._client


class TestVenueRouting:
    def test_cluster_requires_routes_to_umrs(self, hybrid):
        hybrid_rt, remote = hybrid
        state = hybrid_rt.start_loop(_spec(requires={"gpu": 1}))
        assert state.loop_id == "loop-remote"
        remote.start_loop.assert_called_once_with("proj", "goal", max_iterations=10, approved=False)

    def test_plain_goal_lands_local(self, hybrid):
        hybrid_rt, remote = hybrid
        state = hybrid_rt.start_loop(_spec())
        assert state.loop_id.startswith("local-")
        remote.start_loop.assert_not_called()

    def test_requires_recorded_into_metadata(self, hybrid):
        """Returned descriptor carries the ask for BOTH venues — even when
        UMRS's own metadata omits it (view-layer stamp)."""
        state = hybrid_loop(hybrid)
        assert state.metadata["requires"] == {"gpu": 2}
        assert state.metadata["venue"] == "umrs"

    def test_falsy_requires_still_local(self, hybrid):
        hybrid_rt, remote = hybrid
        state = hybrid_rt.start_loop(_spec(requires={"gpu": 0, "cluster": False}))
        assert state.loop_id.startswith("local-")
        remote.start_loop.assert_not_called()

    def test_capabilities_merge(self, hybrid):
        hybrid_rt, _remote = hybrid
        caps = hybrid_rt.capabilities()
        assert caps.location == "hybrid"
        assert caps.durable and caps.distributed
        assert "local" in caps.backends


def hybrid_loop(hybrid):
    rt, _ = hybrid
    return rt.start_loop(_spec(requires={"gpu": 2}))


def _spec_state(tmp_path, requires=None):
    return LocalRuntime(tmp_path / "l.db").start_loop(
        LoopSpec(project_id="p", research_goal="x", requires=requires or {})
    )


class TestDualStoreVerbs:
    def test_get_loop_routes_by_prefix(self, hybrid):
        hybrid_rt, remote = hybrid
        remote.get_loop_status.return_value = {"loop_id": "loop-remote", "status": "paused"}
        state = hybrid_rt.get_loop("loop-remote")
        assert state.status == "paused"
        remote.get_loop_status.assert_called_with("loop-remote")

    def test_stop_routes_to_owning_store(self, hybrid, tmp_path):
        hybrid_rt, remote = hybrid
        local_state = hybrid_rt.start_loop(_spec())
        stopped_local = hybrid_rt.stop_loop(local_state.loop_id)
        assert stopped_local.status == "stopped"
        remote.stop_loop.assert_not_called()

    def test_list_loops_merges_stores(self, hybrid, tmp_path):
        hybrid_rt, remote = hybrid
        hybrid_rt.start_loop(_spec(goal="x"))
        hybrid_rt.start_loop(_spec(requires={"gpu": 1}, goal="y"))
        loops = hybrid_rt.list_loops()
        ids = {loop.loop_id for loop in loops}
        assert "loop-remote" in ids
        assert any(loop.startswith("local-") for loop in ids)

    def test_unknown_loop_raises_when_both_stores_miss(self, tmp_path):
        """Lenient hybrid: falls through to second store; only raises when
        a loop is absent from BOTH runtimes (error = primary store's)."""
        local = LocalRuntime(tmp_path / "u.db")
        remote_client = MagicMock()
        remote_client.get_loop_status.side_effect = LookupError("not in umrs")
        hybrid_rt = HybridRuntime(local, UmrsRuntime(remote_client))
        with pytest.raises(LookupError):
            hybrid_rt.get_loop("local-nonexistent")

    def test_requires_none_and_no_umrs(self, tmp_path):
        hybrid_rt = HybridRuntime(LocalRuntime(tmp_path / "s.db"), None)
        state = hybrid_rt.start_loop(_spec(requires={"gpu": 1}))
        assert state.loop_id.startswith("local-")
        caps = hybrid_rt.capabilities()
        assert caps.backends == ("local",)
        assert caps.distributed is False


class TestResolverHybrid:
    def test_hybrid_mode_builds_hybrid_runtime(self, tmp_path):
        runtime = get_runtime(
            mode="hybrid",
            client=SimpleNamespace(config=SimpleNamespace(project_id="p")),
            local_db_path=tmp_path / "h.db",
        )
        assert isinstance(runtime, HybridRuntime)

    def test_hybrid_without_client_gives_local_only(self, tmp_path):
        from r4.runtime.resolver import _hybrid_runtime

        runtime = _hybrid_runtime(tmp_path / "h.db", None)
        assert runtime.capabilities().distributed is False

    def test_spec_requires_field_round_trip(self):
        spec = LoopSpec(project_id="p", research_goal="g", requires={"gpu": 2})
        assert spec.requires == {"gpu": 2}
