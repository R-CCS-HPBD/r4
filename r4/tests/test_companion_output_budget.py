"""Output-budget exhaustion ladder + read-timeout transient markers (2026-09-30).

Terminal-bench sweep findings: (1) finish_reason=length with null content and
zero tool calls silently returned "" as a successful turn; (2) a slow shared
endpoint's R4TimeoutError matched no transient marker and killed turns on the
first attempt. These pins freeze the fixes.
"""

from __future__ import annotations

from types import SimpleNamespace

from r4.companion.agent import (
    _OUTPUT_BUDGET_NUDGE,
    _OUTPUT_BUDGET_STOP,
    ResearchCompanion,
    _is_transient_provider_error,
    _output_length_exhausted,
)
from r4.companion.skills import SkillRegistry
from r4.errors import R4TimeoutError


def _length_response(content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message, "finish_reason": "length"}]}


class TestOutputLengthExhausted:
    def test_length_with_null_content_flags(self) -> None:
        assert _output_length_exhausted(_length_response(), "")

    def test_length_with_text_is_a_normal_answer(self) -> None:
        assert not _output_length_exhausted(
            _length_response(content="partial but real"), "partial but real"
        )

    def test_stop_finish_reason_never_flags(self) -> None:
        response = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
        assert not _output_length_exhausted(response, "")

    def test_missing_choices_never_flags(self) -> None:
        assert not _output_length_exhausted({"choices": []}, "")
        assert not _output_length_exhausted({}, "")


def _agent_with_rich_client():
    client = SimpleNamespace(_config=SimpleNamespace(model="test-model"))
    agent = ResearchCompanion(client, model="test-model")
    # The ladder test never reaches the network: fake the round executor.
    agent._fit_agent_messages = lambda messages, tools=None: messages
    agent._emit_tool_selection = lambda *_a, **_k: None
    return agent


class TestOutputBudgetLadder:
    def _run(self, responses):
        agent = _agent_with_rich_client()
        queue = list(responses)
        observed_messages: list[list[dict]] = []

        def fake_round(messages, active_tools, stream):
            observed_messages.append(list(messages))
            return None, queue.pop(0)

        agent._agentic_round_response = fake_round
        result = agent._run_agentic_turn(
            messages=[{"role": "user", "content": "work"}],
            tool_schemas=[{"type": "function", "function": {"name": "noop"}}],
            user_message="work",
            tools=[{"name": "noop"}],
        )
        return result, observed_messages

    def test_length_round_nudges_then_recovers(self) -> None:
        final = {"choices": [{"message": {"content": "done, briefly"}, "finish_reason": "stop"}]}
        result, observed = self._run([_length_response(), final])
        assert result == "done, briefly"
        # The nudge landed as a user message before round 2.
        assert observed[1][-1] == {"role": "user", "content": _OUTPUT_BUDGET_NUDGE}

    def test_repeated_exhaustion_continues_then_named_stop(self) -> None:
        # ADR-087: up to 4 continuation nudges per turn, then the named stop.
        result, observed = self._run([_length_response() for _ in range(5)])
        assert result == _OUTPUT_BUDGET_STOP.format(n=5)
        assert all(o[-1] == {"role": "user", "content": _OUTPUT_BUDGET_NUDGE} for o in observed[1:])
        assert "finish_reason=length" in result
        assert result != ""

    def test_two_exhaustions_then_recovery_is_not_a_stop(self) -> None:
        final = {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
        result, _ = self._run([_length_response(), _length_response(), final])
        assert result == "done"

    def test_recovery_budget_is_configurable(self, monkeypatch) -> None:
        monkeypatch.setenv("R4_COMPANION_LENGTH_RECOVERIES", "1")
        result, _ = self._run([_length_response(), _length_response()])
        assert result == _OUTPUT_BUDGET_STOP.format(n=2)

    @staticmethod
    def _cut(reasoning: str) -> dict:
        message = {"role": "assistant", "content": None, "reasoning_content": reasoning}
        return {"choices": [{"message": message, "finish_reason": "length"}]}

    def test_nudge_hands_back_the_cut_reasoning_tail(self, monkeypatch) -> None:
        """12,000 chars by default (2026-10-05: 2,000 made a TB 4.0 trial restart
        its analysis); R4_COMPANION_LENGTH_TAIL_CHARS overrides."""
        monkeypatch.delenv("R4_COMPANION_LENGTH_TAIL_CHARS", raising=False)
        final = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        _, observed = self._run([self._cut("x" * 20_000 + "TAIL"), final])
        nudge = observed[1][-1]["content"]
        assert nudge.startswith(_OUTPUT_BUDGET_NUDGE)
        assert nudge.endswith("TAIL")
        assert 12_000 <= len(nudge) - len(_OUTPUT_BUDGET_NUDGE) < 12_200
        monkeypatch.setenv("R4_COMPANION_LENGTH_TAIL_CHARS", "500")
        _, observed = self._run([self._cut("x" * 20_000 + "TAIL"), final])
        assert len(observed[1][-1]["content"]) < len(_OUTPUT_BUDGET_NUDGE) + 700

    def test_nudge_asks_to_continue_not_to_stop(self) -> None:
        assert "no tool calls" not in _OUTPUT_BUDGET_NUDGE
        assert "next tool call" in _OUTPUT_BUDGET_NUDGE


class TestTimeoutTransientMarkers:
    def test_r4_timeout_error_retries(self) -> None:
        exc = R4TimeoutError("Request timed out: The read operation timed out.")
        assert _is_transient_provider_error(exc) is not None

    def test_hard_tool_markers_still_win(self) -> None:
        exc = RuntimeError("model does not support tool calling even after timed out marker")
        assert _is_transient_provider_error(exc) is None


class TestCodeSkillTaskKeywords:
    def test_tb_instruction_styles_activate_code(self) -> None:
        """Terminal-bench instructions name files/outputs, not 'code' — the
        keyword row must cover the write-the-file task shape."""
        registry = SkillRegistry()
        registry._load_builtin_skills()
        code = registry.get("code")
        assert code is not None
        photonic = (
            "Route the photonic waveguides described in /app/layout_spec.json and "
            "write the waypoint result to /app/routing_result_1.json."
        )
        biped = "Implement a PyDrake biped trajectory generator at /app/submission/solve.py"
        assert "file" in code.keywords
        assert code.matches(photonic)
        assert code.matches(biped)

    def test_plain_research_question_does_not_false_fire(self) -> None:
        registry = SkillRegistry()
        registry._load_builtin_skills()
        code = registry.get("code")
        assert not code.matches("Explain Newton's second law in prose.")
