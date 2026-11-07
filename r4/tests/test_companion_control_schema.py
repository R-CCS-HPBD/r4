"""T1 canonical control-event schema tests (wave companion-any-client)."""

from __future__ import annotations

import pytest

from r4.companion import control_schema as cs


class TestRoundTrips:
    def test_approval_request_round_trip(self):
        event = cs.ApprovalRequest(
            request_id=7,
            tool="knowledge_graph_decide",
            description="approve graph proposal gp-1",
            arguments={"proposal_id": "gp-1", "approved": True},
            risk_tier="never_auto",
            session_id="s-1",
        )
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded == event

    def test_approval_decision_round_trip(self):
        event = cs.ApprovalDecision(request_id=7, decision="always")
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded == event
        assert decoded.approved is True
        assert decoded.applies_to_session is True

    def test_approval_decision_no(self):
        event = cs.ApprovalDecision(request_id=1, decision="no")
        assert event.approved is False
        assert event.applies_to_session is False

    def test_mode_change_round_trip(self):
        event = cs.ModeChange(mode="trust", source="autopilot", session_id="s-9")
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded == event

    def test_budget_state_round_trip(self):
        event = cs.BudgetState(
            llm_calls_used=11,
            llm_calls_limit=None,
            tool_rounds_used=37,
            tool_rounds_limit=1000,
            context_used_tokens=88_000,
            context_limit_tokens=120_000,
            session_id="s-2",
        )
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded == event
        assert decoded.llm_calls_limit is None

    def test_session_state_round_trip(self):
        event = cs.SessionState(
            session_id="s-3",
            project_id="default",
            mode="auto_safe",
            graphs=["kg-a"],
            skills=["mcp:umrs"],
            actor="agent",
        )
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded == event
        assert decoded.graphs == ["kg-a"]


class TestTolerance:
    def test_unknown_payload_keys_ignored(self):
        data = cs.to_dict(cs.ModeChange(mode="trust", source="user"))
        data["payload"]["future_field"] = {"nested": True}
        decoded = cs.from_dict(data)
        assert decoded.mode == "trust"
        assert not hasattr(decoded, "future_field")

    def test_unknown_event_type_skips_with_warning(self, caplog):
        """Persisted logs mix binary generations: unknown types skip, not
        raise. Typos stay loud at the producer (to_dict refuses)."""
        import logging

        with caplog.at_level(logging.WARNING, logger="r4.companion.control_schema"):
            result = cs.from_dict({"type": "hello_v2", "payload": {"version": 1}})
        assert result is None
        assert any("unknown control event type" in rec.message for rec in caplog.records)

    def test_unknown_event_type_mid_stream_skips_silently_safe(self, caplog):
        """A newer-binary event inside a persisted log does not kill the rest."""
        import logging

        with caplog.at_level(logging.WARNING, logger="r4.companion.control_schema"):
            skipped = cs.from_dict({"type": "teleport_request", "payload": {}})
        assert skipped is None
        assert any("teleport_request" in rec.message for rec in caplog.records)

    def test_future_version_raises(self):
        data = cs.to_dict(cs.ApprovalDecision(request_id=1, decision="once"))
        data["payload"]["version"] = cs.CONTROL_SCHEMA_VERSION + 1
        with pytest.raises(ValueError, match="unsupported schema version"):
            cs.from_dict(data)

    def test_bad_decision_literal(self):
        with pytest.raises(ValueError, match="decision"):
            cs.ApprovalDecision(request_id=1, decision="maybe")

    def test_bad_mode_literal(self):
        with pytest.raises(ValueError, match="mode"):
            cs.ModeChange(mode="lax", source="user")

    def test_bad_risk_tier(self):
        with pytest.raises(ValueError, match="risk_tier"):
            cs.ApprovalRequest(tool="x", risk_tier="harmless")

    def test_bad_source(self):
        with pytest.raises(ValueError, match="source"):
            cs.ModeChange(mode="trust", source="human_override")

    def test_to_dict_rejects_foreign_object(self):
        class _NotAnEvent:
            pass

        with pytest.raises(ValueError, match="not a control event"):
            cs.to_dict(_NotAnEvent())

    def test_envelope_shape(self):
        data = cs.to_dict(cs.ApprovalDecision(request_id=3, decision="once"))
        assert set(data.keys()) == {"type", "payload"}
        assert data["type"] == "approval_decision"
        assert data["payload"]["request_id"] == 3


class TestTransparencyEvents:
    """S1 round-trips + validation for the 5 agent-work transparency types."""

    def test_thinking_delta_round_trip(self):
        event = cs.ThinkingDelta(session_id="s-1", seq=3, text="partial text", partial=True)
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded == event
        assert decoded.seq == 3
        assert decoded.partial is True

    def test_thinking_done_round_trip(self):
        event = cs.ThinkingDone(session_id="s-1", summary_hint="3-step plan")
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded == event

    def test_tool_call_start_sanitized_summary_required(self):
        event = cs.ToolCallStart(
            session_id="s-1",
            tool_call_id="tc-9",
            name="lab_file_read",
            args_summary="data/catalog/x.csv",
            risk_tier="risky",
        )
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded == event
        assert decoded.risk_tier == "risky"

    def test_tool_call_start_never_auto_risk_tier(self):
        event = cs.ToolCallStart(
            session_id="s-1", name="knowledge_graph_decide", risk_tier="never_auto"
        )
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded.risk_tier == "never_auto"

    def test_tool_call_end_with_duration(self):
        event = cs.ToolCallEnd(
            session_id="s-1",
            tool_call_id="tc-9",
            name="lab_file_read",
            status="done",
            duration_ms=412,
            result_preview="first 120 chars",
        )
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded == event
        assert decoded.duration_ms == 412

    def test_tool_call_end_error_status(self):
        event = cs.ToolCallEnd(session_id="s-1", name="shell_exec", status="error")
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded.status == "error"

    def test_tool_call_end_cancelled_status_allowed(self):
        event = cs.ToolCallEnd(session_id="s-1", name="shell_exec", status="cancelled")
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded.status == "cancelled"

    def test_tool_call_end_bad_status_raises(self):
        with pytest.raises(ValueError, match="status"):
            cs.ToolCallEnd(session_id="s-1", name="x", status="boom")

    def test_tool_call_end_negative_duration_raises(self):
        with pytest.raises(ValueError, match="duration_ms"):
            cs.ToolCallEnd(session_id="s-1", name="x", status="done", duration_ms=-5)

    def test_turn_progress_round_trip(self):
        event = cs.TurnProgress(
            session_id="s-4", round_index=2, tool_calls_so_far=7, llm_calls_so_far=3
        )
        decoded = cs.from_dict(cs.to_dict(event))
        assert decoded == event
