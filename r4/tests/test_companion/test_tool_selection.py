"""Tool selection phase (ADR-037) — per-call record emission.

Scope: items 2+3 of the prompt doc (phase in the turn loop, control
contract). The passthrough-first policy is load-bearing: records must
exist even when nothing was filtered, and they must never claim
optimization they did not perform.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from r4.companion.agent import CompanionEventType, ResearchCompanion
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry
from r4.companion.tool_selection import (
    MAX_SELECTED,
    build_selection_context,
    estimate_schema_tokens,
    selection_summary_line,
)


def _chat_response(content, tool_calls=None, finish_reason="stop"):
    message: dict = {"content": content, "role": "assistant"}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    return MagicMock(
        content=content or "",
        model="test-model",
        usage=MagicMock(prompt_tokens=10, completion_tokens=2, total_tokens=12),
        raw_response={"choices": [{"message": message, "finish_reason": finish_reason}]},
    )


@pytest.fixture
def mock_client():
    client = MagicMock()
    client._config.model = "test-model"
    client._request.return_value = _chat_response("done")
    return client


def _agent(mock_client):
    return ResearchCompanion(
        mock_client,
        context_manager=ContextManager(client=mock_client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )


def _echo_tool(name: str = "list_dir"):
    return {
        "name": name,
        "description": "echo",
        "parameters": {"type": "object", "properties": {}},
        "execute": lambda **k: "ok",
    }


def _selection_events(agent):
    return [e for e in agent._captured if e.event_type is CompanionEventType.TOOL_SELECTION]


def _capture(agent):
    agent._captured = []
    agent.on_event(agent._captured.append)


class TestPerCallEmission:
    def test_record_emitted_once_per_model_call(self, mock_client):
        tool_call = _chat_response(
            None,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                }
            ],
        )
        mock_client._request.side_effect = [tool_call, _chat_response("final")]
        agent = _agent(mock_client)
        _capture(agent)
        agent.send("hi", tools=[_echo_tool()], stream=False)

        events = _selection_events(agent)
        assert len(events) == 2  # one per model call
        steps = [e.data["step_index"] for e in events]
        assert steps == [0, 1]
        turn_ids = {e.data["turn_id"] for e in events}
        assert len(turn_ids) == 1

    def test_passthrough_reports_full_catalog(self, mock_client):
        agent = _agent(mock_client)
        _capture(agent)
        tools = [_echo_tool("a"), _echo_tool("b"), _echo_tool("c")]
        agent.send("hi", tools=tools, stream=False)

        rec = _selection_events(agent)[0].data
        assert rec["strategy"] == "passthrough"
        assert rec["candidate_tools"] == rec["available_tools"] == 3
        assert rec["filtered_out"] == {}
        assert rec["schema_tokens_estimate"]["before"] == rec["schema_tokens_estimate"]["after"]
        assert "all 3 tools offered (no selection)" in _selection_events(agent)[0].message

    def test_denied_tool_recorded_as_disabled(self, mock_client):
        """A policy-denied tool leaves the offered set (opencode deny→hide);
        the record carries the removal with user_denied provenance."""
        tool_call = _chat_response(
            None,
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "shell_exec", "arguments": "{}"},
                }
            ],
        )
        risky = _echo_tool("shell_exec")
        mock_client._request.side_effect = [tool_call, _chat_response("that's done")]
        agent = ResearchCompanion(
            mock_client,
            context_manager=ContextManager(client=mock_client),
            skill_registry=SkillRegistry(),
            permission_manager=PermissionManager(mode=PermissionMode.AUTO_SAFE),
        )
        from r4.companion.permission_rules import PermissionRule, RuleSet

        # ADR-104 D-2: a tool-wide deny (a person's "no" no longer hides).
        agent._permissions.set_approval_callback(lambda n, d, a: True)
        agent._permissions.ruleset = RuleSet(
            [PermissionRule("deny", "shell_exec", "", "user", "shell_exec")]
        )
        agent.send("try it", tools=[risky], stream=False)
        assert "shell_exec" in agent._session_denied_tools

        _capture(agent)
        mock_client._request.side_effect = [_chat_response("ok")]
        agent.send("next", tools=[risky], stream=False)
        rec = _selection_events(agent)[0].data
        assert rec["available_tools"] == 1
        assert rec["candidate_tools"] == 0
        assert rec["filtered_out"]["disabled"]["count"] == 1
        assert rec["filtered_out"]["disabled"]["sample"] == ["shell_exec (user_denied)"]
        assert rec["schema_tokens_estimate"]["after"] <= rec["schema_tokens_estimate"]["before"]


class TestRecordShape:
    def test_selected_list_capped(self):
        tools = [{"name": f"t{i}"} for i in range(60)]
        ctx = build_selection_context(tools=tools, offered=tools)
        rec = ctx.step_record(step_index=0, turn_id="t1")
        assert len(rec["selected_tools"]) == MAX_SELECTED + 1
        assert rec["selected_tools"][-1] == f"+{60 - MAX_SELECTED} more"

    def test_sample_capped_and_latency_measured(self):
        offered = [{"name": "t0"}]
        tools = offered + [{"name": f"denied{i}"} for i in range(10)]
        ctx = build_selection_context(tools=tools, offered=offered)
        rec = ctx.step_record(step_index=3, turn_id="t9")
        assert len(rec["filtered_out"]["disabled"]["sample"]) <= 5
        assert rec["filtered_out"]["disabled"]["count"] == 10
        assert isinstance(rec["selection_latency_ms"], int)
        assert rec["selection_latency_ms"] >= 0
        assert rec["turn_id"] == "t9"
        assert rec["step_index"] == 3

    def test_unknown_strategy_rejected(self):
        with pytest.raises(ValueError, match="unknown selection strategy"):
            build_selection_context(tools=[], offered=[], strategy="quantum_pick")

    def test_schema_tokens_estimate_monotonic(self):
        tools = [{"name": "a", "description": "x" * 100}]
        before = estimate_schema_tokens(tools)
        after = estimate_schema_tokens([])
        assert before > 0
        assert after == 0

    def test_summary_line_honesty(self):
        """The message must restate the record — never 'optimized'."""
        honest = selection_summary_line(
            {"available_tools": 5, "candidate_tools": 5, "strategy": "passthrough"}
        )
        assert honest == "all 5 tools offered (no selection)"
        filtered = selection_summary_line(
            {"available_tools": 5, "candidate_tools": 2, "strategy": "policy_filter"}
        )
        assert filtered == "2 of 5 tools offered (policy_filter)"
        assert "optimiz" not in honest and "optimiz" not in filtered


class TestRecordAmendmentFields:
    def test_duplicate_registration_warning_detected(self):
        from r4.companion.tool_selection import detect_duplicate_registrations

        regs = {
            "default-rag-search": ["a", "b", "c"],
            "hatch-rag-search": ["c", "b", "a"],
            "platform-tools": ["x"],
        }
        warnings = detect_duplicate_registrations(regs)
        assert len(warnings) == 1
        assert "default-rag-search" in warnings[0]
        assert "hatch-rag-search" in warnings[0]
        assert "same 3 tools" in warnings[0]

    def test_no_warning_when_tool_sets_differ(self):
        from r4.companion.tool_selection import detect_duplicate_registrations

        regs = {"default-rag-search": ["a", "b"], "hatch-rag-search": ["a", "b", "c"]}
        assert detect_duplicate_registrations(regs) == []

    def test_context_carries_warnings_and_skill_tokens(self):
        ctx = build_selection_context(
            tools=[{"name": "a"}],
            offered=[{"name": "a"}],
            warnings=tuple(["x and y register the same 2 tools"]),
            skill_body_text="x" * 400,
        )
        rec = ctx.step_record(step_index=0, turn_id="t1")
        assert rec["warnings"] == ["x and y register the same 2 tools"]
        assert rec["schema_tokens_estimate"]["skill_bodies"] == 100
        assert rec["schema_tokens_estimate"]["before"] == rec["schema_tokens_estimate"]["after"]
        assert rec["would_filter"] == {}

    def test_would_filter_shadow_bucket_only_when_measured(self):
        ctx = build_selection_context(
            tools=[{"name": "a"}],
            offered=[{"name": "a"}],
            would_filter={"capability_tier": {"count": 3, "sample": ["t1"]}},
        )
        rec = ctx.step_record(step_index=0, turn_id="t1")
        assert rec["would_filter"]["capability_tier"]["count"] == 3


class TestControlContract:
    def test_record_round_trips_through_control_schema(self):
        from r4.companion.control_schema import ToolSelection, from_dict, to_dict

        ctx = build_selection_context(tools=[{"name": "a"}, {"name": "b"}], offered=[{"name": "a"}])
        rec = ctx.step_record(step_index=1, turn_id="t7")
        event = ToolSelection(
            session_id="s1",
            turn_id=rec["turn_id"],
            step_index=rec["step_index"],
            available_tools=rec["available_tools"],
            discovered=rec["discovered"],
            filtered_out=rec["filtered_out"],
            candidate_tools=rec["candidate_tools"],
            selected_tools=rec["selected_tools"],
            selected_skills=rec["selected_skills"],
            strategy=rec["strategy"],
            ranking=rec["ranking"],
            selection_latency_ms=rec["selection_latency_ms"],
            schema_tokens_estimate=rec["schema_tokens_estimate"],
        )
        wire = to_dict(event)
        assert wire["type"] == "tool_selection"
        decoded = from_dict(wire)
        assert isinstance(decoded, ToolSelection)
        assert decoded.candidate_tools == 1
        assert decoded.filtered_out["disabled"]["count"] == 1

    def test_bad_strategy_raises_at_schema_layer(self):
        from r4.companion.control_schema import ToolSelection

        with pytest.raises(ValueError, match="strategy"):
            ToolSelection(strategy="made_up")

    def test_candidate_over_available_raises(self):
        from r4.companion.control_schema import ToolSelection

        with pytest.raises(ValueError, match="candidate_tools"):
            ToolSelection(available_tools=2, candidate_tools=3)

    def test_amendment_fields_round_trip(self):
        from r4.companion.control_schema import ToolSelection, from_dict, to_dict

        event = ToolSelection(
            session_id="s",
            available_tools=5,
            candidate_tools=5,
            warnings=["default-x and hatch-x register the same 3 tools"],
            would_filter={"capability_tier": {"count": 2, "sample": ["a"]}},
            schema_tokens_estimate={"before": 10, "after": 10, "skill_bodies": 25},
        )
        decoded = from_dict(to_dict(event))
        assert decoded.warnings == ["default-x and hatch-x register the same 3 tools"]
        assert decoded.would_filter["capability_tier"]["count"] == 2
        assert decoded.schema_tokens_estimate["skill_bodies"] == 25

    def test_imaginary_schema_tokens_key_raises(self):
        from r4.companion.control_schema import ToolSelection

        with pytest.raises(ValueError, match="schema_tokens_estimate"):
            ToolSelection(schema_tokens_estimate={"before": 1, "after": 1, "imaginary": 2})


class TestBridgeCanonical:
    def test_bridge_emits_validated_envelope(self):
        """_emit_canonical_tool_selection attaches session_id and passes
        dataclass validation (bad input fails loudly, not silently)."""
        from types import SimpleNamespace

        from r4.companion.ui.bridge_server import CompanionBridgeServer

        lines: list[dict] = []
        bridge = SimpleNamespace(
            _session_id_str=lambda: "sess-1",
            _emit_line=lines.append,
        )
        bridge._tool_selection_event = lambda d: CompanionBridgeServer._tool_selection_event(
            bridge, d
        )
        bridge._coerce_selection_fields = lambda d: CompanionBridgeServer._coerce_selection_fields(
            d
        )
        data = {
            "turn_id": "t1",
            "step_index": 0,
            "available_tools": 3,
            "discovered": {"session_registered": 3},
            "filtered_out": {},
            "candidate_tools": 3,
            "selected_tools": ["a", "b", "c"],
            "selected_skills": [],
            "strategy": "passthrough",
            "ranking": None,
            "selection_latency_ms": 0,
            "schema_tokens_estimate": {"before": 100, "after": 100},
        }
        CompanionBridgeServer._emit_canonical_tool_selection(bridge, data)
        envelope = lines[-1]
        assert envelope["type"] == "tool_selection"
        assert envelope["payload"]["session_id"] == "sess-1"
        assert envelope["payload"]["available_tools"] == 3

    def test_bridge_rejects_corrupt_record(self):
        from types import SimpleNamespace

        import pytest as pt

        from r4.companion.ui.bridge_server import CompanionBridgeServer

        bridge = SimpleNamespace(_session_id_str=lambda: "s", _emit_line=lambda e: None)
        bridge._tool_selection_event = lambda d: CompanionBridgeServer._tool_selection_event(
            bridge, d
        )
        bridge._coerce_selection_fields = lambda d: CompanionBridgeServer._coerce_selection_fields(
            d
        )
        bad = {
            "turn_id": "t1",
            "step_index": 0,
            "available_tools": 1,
            "candidate_tools": 5,  # exceeds available -> validation failure
            "selected_tools": [],
            "selected_skills": [],
            "discovered": {},
            "filtered_out": {},
            "strategy": "passthrough",
            "ranking": None,
            "selection_latency_ms": 0,
            "schema_tokens_estimate": {},
        }
        with pt.raises(ValueError, match="candidate_tools"):
            CompanionBridgeServer._emit_canonical_tool_selection(bridge, bad)

    def test_tool_selection_forwarded_by_bridge(self):
        """_forward_event surfaces tool_selection: persisted + canonical
        envelope + legacy event line."""
        from r4.companion.agent import CompanionEvent, CompanionEventType
        from r4.companion.ui.bridge_server import CompanionBridgeServer

        bridge, lines, logged = _bridge_stub("sess-9")
        record = _sample_record(available=2, selected=["a", "b"])
        event = CompanionEvent(
            event_type=CompanionEventType.TOOL_SELECTION,
            message="all 2 tools offered (no selection)",
            data=record,
        )
        CompanionBridgeServer._forward_event(bridge, event)

        _assert_persisted_selection(logged)
        _assert_canonical_selection_envelope(lines, "sess-9")
        _assert_legacy_selection_line(lines)


def _sample_record(*, available: int, selected: list[str]) -> dict:
    return {
        "turn_id": "t1",
        "step_index": 0,
        "available_tools": available,
        "discovered": {"session_registered": available},
        "filtered_out": {},
        "candidate_tools": available,
        "selected_tools": selected,
        "selected_skills": [],
        "strategy": "passthrough",
        "ranking": None,
        "selection_latency_ms": 0,
        "schema_tokens_estimate": {"before": 10, "after": 10},
    }


def _bridge_stub(session_id: str):
    from types import SimpleNamespace

    from r4.companion.ui.bridge_server import CompanionBridgeServer

    lines: list[dict] = []
    logged: list[dict] = []
    bridge = SimpleNamespace(
        session=SimpleNamespace(session_id=session_id),
        _session_id_str=lambda: session_id,
        _emit_line=lines.append,
        _event_log=SimpleNamespace(append=lambda **kw: logged.append(kw)),
    )
    bridge._emit_canonical_tool_selection = lambda d: (
        CompanionBridgeServer._emit_canonical_tool_selection(bridge, d)
    )
    bridge._tool_selection_event = lambda d: CompanionBridgeServer._tool_selection_event(bridge, d)
    bridge._coerce_selection_fields = lambda d: CompanionBridgeServer._coerce_selection_fields(d)
    return bridge, lines, logged


def _assert_persisted_selection(logged: list[dict]) -> None:
    assert logged and logged[0]["event_type"] == "tool_selection"


def _assert_canonical_selection_envelope(lines: list[dict], session_id: str) -> None:
    canonical = [line for line in lines if line.get("type") == "tool_selection"]
    assert len(canonical) == 1
    assert canonical[0]["payload"]["session_id"] == session_id


def _assert_legacy_selection_line(lines: list[dict]) -> None:
    legacy = [line for line in lines if "event" in line]
    assert legacy and legacy[0]["event"]["type"] == "tool_selection"
