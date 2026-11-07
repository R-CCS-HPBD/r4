"""Batch 7 tests: MEM-D2 (Routing Feedback), MEM-D3 (Adaptive Planning),
MEM-D4 (Reflective Memory Inspection).

Plain imports by ADR-053 (the spec-load machinery predates the jobs↔api
cycle fix; umrs resolves as a namespace package from the repo root).
"""

from __future__ import annotations

import unittest

from umrs.jobs.adaptive_planning import (
    AdaptivePlanningEngine,
)
from umrs.jobs.reflective_memory import (
    ReflectiveMemoryInspector,
)

# Now import
from umrs.jobs.routing_feedback import (
    RoutingFeedbackStore,
)
from umrs.jobs.workflow_outcome import (
    WorkflowOutcomeStore,
)

# r4-contracts DTOs (plain imports; r4-contracts already on the path)
from r4_contracts.provenance import (
    PlanningHeuristicRef,
    ReflectiveMemoryEntryRef,
    RoutingFeedbackRef,
)

# ╔═════════════════════════════════════════════════════════════╗
# ║ MEM-D2: Routing Feedback Store                             ║
# ╚═════════════════════════════════════════════════════════════╝


class TestRoutingFeedbackRecord(unittest.TestCase):
    """Test recording routing feedback."""

    def setUp(self):
        self.store = RoutingFeedbackStore()

    def test_record_basic(self):
        fb = self.store.record_feedback(
            job_id="job-001",
            step_name="generate",
            model_selected="gpt-4",
        )
        self.assertTrue(fb.feedback_id.startswith("rfb-"))
        self.assertEqual(fb.job_id, "job-001")
        self.assertEqual(fb.step_name, "generate")
        self.assertEqual(fb.model_selected, "gpt-4")

    def test_record_with_outcome(self):
        fb = self.store.record_feedback(
            job_id="job-002",
            step_name="summarize",
            model_selected="llama-70b",
            outcome="succeeded",
            user_accepted=True,
            latency_ms=450.0,
            tokens_used=1200,
            quality_score=0.9,
        )
        self.assertEqual(fb.outcome, "succeeded")
        self.assertTrue(fb.user_accepted)
        self.assertAlmostEqual(fb.latency_ms, 450.0)
        self.assertEqual(fb.tokens_used, 1200)

    def test_record_invalid_outcome_rejects(self):
        with self.assertRaises(ValueError):
            self.store.record_feedback(
                job_id="job-bad",
                step_name="step",
                model_selected="m",
                outcome="invalid_outcome",
            )

    def test_record_with_correction(self):
        fb = self.store.record_feedback(
            job_id="job-003",
            step_name="classify",
            model_selected="gpt-3.5",
            outcome="succeeded",
            user_accepted=False,
            user_correction="gpt-4",
        )
        self.assertFalse(fb.user_accepted)
        self.assertEqual(fb.user_correction, "gpt-4")


class TestRoutingFeedbackUpdate(unittest.TestCase):
    """Test updating user feedback on routing decisions."""

    def setUp(self):
        self.store = RoutingFeedbackStore()

    def test_update_acceptance(self):
        fb = self.store.record_feedback(
            job_id="j1",
            step_name="s1",
            model_selected="m1",
        )
        ok = self.store.update_user_feedback(fb.feedback_id, True)
        self.assertTrue(ok)
        updated = self.store.get_feedback(fb.feedback_id)
        self.assertTrue(updated.user_accepted)

    def test_update_with_correction(self):
        fb = self.store.record_feedback(
            job_id="j2",
            step_name="s2",
            model_selected="m2",
        )
        self.store.update_user_feedback(
            fb.feedback_id,
            False,
            user_correction="m3",
            quality_score=0.3,
        )
        updated = self.store.get_feedback(fb.feedback_id)
        self.assertFalse(updated.user_accepted)
        self.assertEqual(updated.user_correction, "m3")
        self.assertAlmostEqual(updated.quality_score, 0.3)

    def test_update_nonexistent_returns_false(self):
        ok = self.store.update_user_feedback("nonexistent", True)
        self.assertFalse(ok)


class TestRoutingFeedbackQuery(unittest.TestCase):
    """Test querying and filtering feedback."""

    def setUp(self):
        self.store = RoutingFeedbackStore()
        self.store.record_feedback(
            job_id="j1",
            step_name="s1",
            model_selected="gpt-4",
            outcome="succeeded",
            step_type="llm",
        )
        self.store.record_feedback(
            job_id="j2",
            step_name="s2",
            model_selected="gpt-4",
            outcome="failed",
            step_type="llm",
        )
        self.store.record_feedback(
            job_id="j3",
            step_name="s3",
            model_selected="llama-70b",
            outcome="succeeded",
            step_type="embedding",
        )

    def test_list_all(self):
        results = self.store.list_feedback()
        self.assertEqual(len(results), 3)

    def test_filter_by_model(self):
        results = self.store.list_feedback(model_name="gpt-4")
        self.assertEqual(len(results), 2)

    def test_filter_by_job(self):
        results = self.store.list_feedback(job_id="j1")
        self.assertEqual(len(results), 1)

    def test_filter_by_step_type(self):
        results = self.store.list_feedback(step_type="embedding")
        self.assertEqual(len(results), 1)

    def test_limit_applied(self):
        results = self.store.list_feedback(limit=2)
        self.assertEqual(len(results), 2)


class TestRoutingFeedbackSummary(unittest.TestCase):
    """Test model routing summary aggregation."""

    def setUp(self):
        self.store = RoutingFeedbackStore()
        # 3 runs with gpt-4
        self.store.record_feedback(
            job_id="j1",
            step_name="s1",
            model_selected="gpt-4",
            outcome="succeeded",
            user_accepted=True,
            latency_ms=100.0,
            quality_score=0.9,
        )
        self.store.record_feedback(
            job_id="j2",
            step_name="s2",
            model_selected="gpt-4",
            outcome="succeeded",
            user_accepted=True,
            latency_ms=200.0,
            quality_score=0.8,
        )
        self.store.record_feedback(
            job_id="j3",
            step_name="s3",
            model_selected="gpt-4",
            outcome="failed",
            user_accepted=False,
            user_correction="llama-70b",
            latency_ms=300.0,
        )

    def test_summary_counts(self):
        s = self.store.summarize_model("gpt-4")
        self.assertEqual(s.total_selections, 3)
        self.assertEqual(s.succeeded, 2)
        self.assertEqual(s.failed, 1)
        self.assertEqual(s.user_accepted, 2)

    def test_summary_correction_targets(self):
        s = self.store.summarize_model("gpt-4")
        self.assertEqual(s.user_corrected, 1)
        self.assertIn("llama-70b", s.correction_targets)

    def test_summary_averages(self):
        s = self.store.summarize_model("gpt-4")
        self.assertAlmostEqual(s.avg_latency_ms, 200.0)
        self.assertIsNotNone(s.avg_quality_score)

    def test_summary_acceptance_rate(self):
        s = self.store.summarize_model("gpt-4")
        # 2 accepted out of 3 with feedback
        self.assertGreater(s.acceptance_rate, 0.5)

    def test_summary_empty_model(self):
        s = self.store.summarize_model("nonexistent")
        self.assertEqual(s.total_selections, 0)


class TestRoutingFeedbackDelete(unittest.TestCase):
    """Test deletion of routing feedback."""

    def setUp(self):
        self.store = RoutingFeedbackStore()

    def test_delete_existing(self):
        fb = self.store.record_feedback(
            job_id="j",
            step_name="s",
            model_selected="m",
        )
        ok = self.store.delete_feedback(fb.feedback_id)
        self.assertTrue(ok)
        self.assertIsNone(self.store.get_feedback(fb.feedback_id))

    def test_delete_nonexistent(self):
        ok = self.store.delete_feedback("nonexistent")
        self.assertFalse(ok)


class TestRoutingFeedbackSerialization(unittest.TestCase):
    """Test to_dict serialization."""

    def test_to_dict(self):
        store = RoutingFeedbackStore()
        fb = store.record_feedback(
            job_id="j1",
            step_name="s1",
            model_selected="gpt-4",
            outcome="succeeded",
        )
        d = fb.to_dict()
        self.assertEqual(d["job_id"], "j1")
        self.assertEqual(d["model_selected"], "gpt-4")
        self.assertIn("created_at", d)

    def test_summary_to_dict(self):
        store = RoutingFeedbackStore()
        store.record_feedback(
            job_id="j1",
            step_name="s1",
            model_selected="m",
            outcome="succeeded",
            user_accepted=True,
        )
        s = store.summarize_model("m")
        d = s.to_dict()
        self.assertEqual(d["model_name"], "m")
        self.assertIn("acceptance_rate", d)


# ╔═════════════════════════════════════════════════════════════╗
# ║ MEM-D3: Adaptive Planning Engine                           ║
# ╚═════════════════════════════════════════════════════════════╝


class TestAdaptivePlanningSuggest(unittest.TestCase):
    """Test recommendation generation."""

    def setUp(self):
        self.outcome_store = WorkflowOutcomeStore()
        self.routing_store = RoutingFeedbackStore()
        self.engine = AdaptivePlanningEngine(self.outcome_store, self.routing_store)

    def test_empty_outcomes_returns_empty(self):
        result = self.engine.suggest("nonexistent-wf")
        self.assertEqual(result.outcome_count, 0)
        self.assertEqual(len(result.recommendations), 0)

    def test_model_switch_recommendation(self):
        # Create outcomes with 2 models, one clearly better
        for i in range(5):
            self.outcome_store.record_outcome(
                job_id=f"j-a-{i}",
                workflow_name="test-wf",
                user_id="u1",
                project_id="p1",
                status="succeeded",
                model_used="good-model",
            )
        for i in range(5):
            self.outcome_store.record_outcome(
                job_id=f"j-b-{i}",
                workflow_name="test-wf",
                user_id="u1",
                project_id="p1",
                status="failed",
                model_used="bad-model",
                failure_category="llm_error",
            )
        result = self.engine.suggest("test-wf")
        self.assertEqual(result.outcome_count, 10)
        # Should have at least one recommendation
        self.assertTrue(len(result.recommendations) > 0)

    def test_failure_pattern_recommendation(self):
        # Repeated timeout failures
        for i in range(5):
            self.outcome_store.record_outcome(
                job_id=f"j-{i}",
                workflow_name="timeout-wf",
                user_id="u1",
                project_id="p1",
                status="failed",
                model_used="m1",
                failure_category="timeout",
            )
        result = self.engine.suggest("timeout-wf")
        retry_recs = [r for r in result.recommendations if r.category == "retry_config"]
        self.assertTrue(len(retry_recs) > 0)

    def test_resource_adjustment_recommendation(self):
        # High variance in durations
        for i, dur in enumerate([10, 10, 10, 100]):
            self.outcome_store.record_outcome(
                job_id=f"j-{i}",
                workflow_name="var-wf",
                user_id="u1",
                project_id="p1",
                status="succeeded",
                model_used="m1",
                execution_duration_sec=float(dur),
            )
        result = self.engine.suggest("var-wf")
        resource_recs = [r for r in result.recommendations if r.category == "resource_adjust"]
        self.assertTrue(len(resource_recs) > 0)

    def test_suggest_result_serialization(self):
        result = self.engine.suggest("empty-wf")
        d = result.to_dict()
        self.assertEqual(d["workflow_name"], "empty-wf")
        self.assertIn("recommendations", d)
        self.assertIn("generated_at", d)


class TestAdaptivePlanningHeuristics(unittest.TestCase):
    """Test heuristic CRUD operations."""

    def setUp(self):
        self.outcome_store = WorkflowOutcomeStore()
        self.engine = AdaptivePlanningEngine(self.outcome_store)

    def test_store_heuristic(self):
        h = self.engine.store_heuristic(
            workflow_name="test-wf",
            rule_type="model_preference",
            condition="step_type == 'llm'",
            action="prefer gpt-4",
            confidence=0.85,
            sample_count=20,
        )
        self.assertTrue(h.heuristic_id.startswith("heur-"))
        self.assertEqual(h.workflow_name, "test-wf")
        self.assertEqual(h.rule_type, "model_preference")

    def test_invalid_rule_type_rejects(self):
        with self.assertRaises(ValueError):
            self.engine.store_heuristic(
                workflow_name="w",
                rule_type="bad_type",
                condition="c",
                action="a",
            )

    def test_list_heuristics(self):
        self.engine.store_heuristic("wf-a", "model_preference", "c1", "a1", 0.9)
        self.engine.store_heuristic("wf-b", "failure_avoidance", "c2", "a2", 0.7)
        self.engine.store_heuristic("wf-a", "param_default", "c3", "a3", 0.5)
        all_h = self.engine.list_heuristics()
        self.assertEqual(len(all_h), 3)
        filtered = self.engine.list_heuristics(workflow_name="wf-a")
        self.assertEqual(len(filtered), 2)

    def test_deactivate_heuristic(self):
        h = self.engine.store_heuristic("w", "model_preference", "c", "a")
        ok = self.engine.deactivate_heuristic(h.heuristic_id)
        self.assertTrue(ok)
        deactivated = self.engine.get_heuristic(h.heuristic_id)
        self.assertFalse(deactivated.is_active)

    def test_deactivate_nonexistent(self):
        ok = self.engine.deactivate_heuristic("nonexistent")
        self.assertFalse(ok)

    def test_delete_heuristic(self):
        h = self.engine.store_heuristic("w", "model_preference", "c", "a")
        ok = self.engine.delete_heuristic(h.heuristic_id)
        self.assertTrue(ok)
        self.assertIsNone(self.engine.get_heuristic(h.heuristic_id))

    def test_delete_nonexistent(self):
        ok = self.engine.delete_heuristic("nonexistent")
        self.assertFalse(ok)

    def test_heuristic_serialization(self):
        h = self.engine.store_heuristic("w", "model_preference", "c", "a", 0.8, 10)
        d = h.to_dict()
        self.assertEqual(d["rule_type"], "model_preference")
        self.assertAlmostEqual(d["confidence"], 0.8)
        self.assertIn("created_at", d)


class TestAdaptivePlanningSuggestWithHeuristics(unittest.TestCase):
    """Test that stored heuristics are referenced in suggestions."""

    def setUp(self):
        self.outcome_store = WorkflowOutcomeStore()
        self.engine = AdaptivePlanningEngine(self.outcome_store)

    def test_heuristics_appear_in_suggest(self):
        h = self.engine.store_heuristic(
            "test-wf",
            "model_preference",
            "always",
            "prefer-gpt4",
        )
        # Need outcomes for suggest to run
        self.outcome_store.record_outcome(
            job_id="j1",
            workflow_name="test-wf",
            user_id="u",
            project_id="p",
            status="succeeded",
            model_used="gpt-4",
        )
        result = self.engine.suggest("test-wf")
        self.assertIn(h.heuristic_id, result.heuristics_used)

    def test_deactivated_heuristic_excluded(self):
        h = self.engine.store_heuristic(
            "test-wf",
            "model_preference",
            "always",
            "prefer-gpt4",
        )
        self.engine.deactivate_heuristic(h.heuristic_id)
        self.outcome_store.record_outcome(
            job_id="j1",
            workflow_name="test-wf",
            user_id="u",
            project_id="p",
            status="succeeded",
            model_used="gpt-4",
        )
        result = self.engine.suggest("test-wf")
        self.assertNotIn(h.heuristic_id, result.heuristics_used)


# ╔═════════════════════════════════════════════════════════════╗
# ║ MEM-D4: Reflective Memory Inspector                       ║
# ╚═════════════════════════════════════════════════════════════╝


class TestReflectiveMemoryInspect(unittest.TestCase):
    """Test unified memory inspection."""

    def setUp(self):
        self.outcome_store = WorkflowOutcomeStore()
        self.routing_store = RoutingFeedbackStore()
        self.engine = AdaptivePlanningEngine(self.outcome_store, self.routing_store)
        self.inspector = ReflectiveMemoryInspector(
            outcome_store=self.outcome_store,
            routing_store=self.routing_store,
            planning_engine=self.engine,
        )
        # Seed data
        self.outcome_store.record_outcome(
            job_id="j1",
            workflow_name="wf-a",
            user_id="u1",
            project_id="p1",
            status="succeeded",
            model_used="gpt-4",
        )
        self.routing_store.record_feedback(
            job_id="j1",
            step_name="s1",
            model_selected="gpt-4",
            outcome="succeeded",
            user_accepted=True,
        )
        self.engine.store_heuristic(
            "wf-a",
            "model_preference",
            "always",
            "prefer-gpt4",
            0.9,
        )

    def test_inspect_all(self):
        snap = self.inspector.inspect()
        self.assertEqual(snap.total_outcomes, 1)
        self.assertEqual(snap.total_routing_feedback, 1)
        self.assertEqual(snap.total_heuristics, 1)
        self.assertEqual(snap.active_heuristics, 1)
        self.assertEqual(len(snap.entries), 3)

    def test_inspect_filter_by_tier_outcome(self):
        snap = self.inspector.inspect(tier="outcome")
        self.assertEqual(len(snap.entries), 1)
        self.assertEqual(snap.entries[0].tier, "outcome")

    def test_inspect_filter_by_tier_routing(self):
        snap = self.inspector.inspect(tier="routing")
        self.assertEqual(len(snap.entries), 1)
        self.assertEqual(snap.entries[0].tier, "routing")

    def test_inspect_filter_by_tier_heuristic(self):
        snap = self.inspector.inspect(tier="heuristic")
        self.assertEqual(len(snap.entries), 1)
        self.assertEqual(snap.entries[0].tier, "heuristic")

    def test_inspect_filter_by_workflow(self):
        snap = self.inspector.inspect(workflow_name="wf-a")
        # outcome + heuristic match (routing has no wf filter)
        self.assertTrue(len(snap.entries) >= 2)

    def test_inspect_filter_by_model(self):
        snap = self.inspector.inspect(model_name="gpt-4")
        # outcome + routing match
        self.assertTrue(len(snap.entries) >= 2)

    def test_inspect_empty(self):
        empty = ReflectiveMemoryInspector()
        snap = empty.inspect()
        self.assertEqual(len(snap.entries), 0)

    def test_snapshot_serialization(self):
        snap = self.inspector.inspect()
        d = snap.to_dict()
        self.assertIn("entries", d)
        self.assertIn("total_outcomes", d)
        self.assertIn("generated_at", d)
        for e in d["entries"]:
            self.assertIn("entry_id", e)
            self.assertIn("tier", e)
            self.assertIn("summary", e)


class TestReflectiveMemoryDelete(unittest.TestCase):
    """Test deletion through inspector."""

    def setUp(self):
        self.outcome_store = WorkflowOutcomeStore()
        self.routing_store = RoutingFeedbackStore()
        self.engine = AdaptivePlanningEngine(self.outcome_store, self.routing_store)
        self.inspector = ReflectiveMemoryInspector(
            outcome_store=self.outcome_store,
            routing_store=self.routing_store,
            planning_engine=self.engine,
        )

    def test_delete_routing_entry(self):
        fb = self.routing_store.record_feedback(
            job_id="j1",
            step_name="s1",
            model_selected="m1",
        )
        ok = self.inspector.delete_entry(fb.feedback_id, "routing")
        self.assertTrue(ok)
        self.assertIsNone(self.routing_store.get_feedback(fb.feedback_id))

    def test_delete_heuristic_entry(self):
        h = self.engine.store_heuristic("w", "model_preference", "c", "a")
        ok = self.inspector.delete_entry(h.heuristic_id, "heuristic")
        self.assertTrue(ok)
        self.assertIsNone(self.engine.get_heuristic(h.heuristic_id))

    def test_delete_outcome_unsupported(self):
        o = self.outcome_store.record_outcome(
            job_id="j1",
            workflow_name="w",
            user_id="u",
            project_id="p",
            status="succeeded",
        )
        ok = self.inspector.delete_entry(o.outcome_id, "outcome")
        self.assertFalse(ok)

    def test_delete_invalid_tier(self):
        ok = self.inspector.delete_entry("x", "invalid")
        self.assertFalse(ok)


class TestReflectiveMemoryReset(unittest.TestCase):
    """Test tier reset operations."""

    def setUp(self):
        self.outcome_store = WorkflowOutcomeStore()
        self.engine = AdaptivePlanningEngine(self.outcome_store)
        self.inspector = ReflectiveMemoryInspector(
            planning_engine=self.engine,
        )

    def test_reset_heuristic_tier(self):
        self.engine.store_heuristic("w1", "model_preference", "c", "a")
        self.engine.store_heuristic("w2", "failure_avoidance", "c", "a")
        count = self.inspector.reset_tier("heuristic")
        self.assertEqual(count, 2)
        # All should be deactivated
        for h in self.engine.list_heuristics():
            self.assertFalse(h.is_active)

    def test_reset_empty_tier(self):
        count = self.inspector.reset_tier("heuristic")
        self.assertEqual(count, 0)


# ╔═════════════════════════════════════════════════════════════╗
# ║ r4-contracts DTOs                                          ║
# ╚═════════════════════════════════════════════════════════════╝


class TestRoutingFeedbackRefDTO(unittest.TestCase):
    """Test RoutingFeedbackRef DTO."""

    def test_to_dict(self):
        ref = RoutingFeedbackRef(
            feedback_id="rfb-001",
            job_id="j1",
            step_name="s1",
            model_selected="gpt-4",
            outcome="succeeded",
            user_accepted=True,
        )
        d = ref.to_dict()
        self.assertEqual(d["feedback_id"], "rfb-001")
        self.assertEqual(d["model_selected"], "gpt-4")
        self.assertTrue(d["user_accepted"])


class TestPlanningHeuristicRefDTO(unittest.TestCase):
    """Test PlanningHeuristicRef DTO."""

    def test_to_dict(self):
        ref = PlanningHeuristicRef(
            heuristic_id="heur-001",
            workflow_name="test-wf",
            rule_type="model_preference",
            condition="step==llm",
            action="prefer gpt-4",
            confidence=0.85,
        )
        d = ref.to_dict()
        self.assertEqual(d["heuristic_id"], "heur-001")
        self.assertAlmostEqual(d["confidence"], 0.85)


class TestReflectiveMemoryEntryRefDTO(unittest.TestCase):
    """Test ReflectiveMemoryEntryRef DTO."""

    def test_to_dict(self):
        ref = ReflectiveMemoryEntryRef(
            entry_id="e-001",
            tier="routing",
            source_store="RoutingFeedbackStore",
            summary="[succeeded] gpt-4 → accepted",
        )
        d = ref.to_dict()
        self.assertEqual(d["entry_id"], "e-001")
        self.assertEqual(d["tier"], "routing")


if __name__ == "__main__":
    unittest.main()
