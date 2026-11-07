"""H13 (TB 4.0 harness plan 2026-10-24): estimated usage counts what the model wrote.

A round cut at the thinking budget sends no usage chunk, so R4 estimates it — from
the reply text alone. A cut round has no text, so 157 rounds of up to 16k tokens
of reasoning were logged as ``completion_tokens: 1`` (about 290k tokens missing
on one photonic trial). The estimate now counts reasoning and tool-call argument
characters too, on the streamed and the blocking path.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from r4.companion.agent import ResearchCompanion
from r4.stream_assembly import THINK_BUDGET_FINISH

_REASONING = "r" * 40_000


class _CutStreamClient:
    """Streams only reasoning, then stops at the thinking budget; no usage chunk."""

    def __init__(self, reasoning: str = "", args: str = "") -> None:
        self._config = SimpleNamespace(model="glm-5.3@rikyu")
        self._last_stream_usage = None
        self._last_stream_finish = THINK_BUDGET_FINISH
        self._reasoning = reasoning
        self._args = args

    def _stream_request_collect(self, payload, tool_calls_acc, *, reasoning_acc=None, **_kw):
        def chunks():
            if self._reasoning and reasoning_acc is not None:
                reasoning_acc.append(self._reasoning)
            if self._args:
                tool_calls_acc.append(
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "shell_exec", "arguments": self._args},
                    }
                )
            return
            yield ""  # pragma: no cover - makes this a generator

        return chunks()


def _agent(client: Any, calls: list) -> ResearchCompanion:
    return ResearchCompanion(
        client,
        model="glm-5.3@rikyu",
        usage_callback=lambda kind, usage: calls.append(usage),
    )


def _messages() -> list[dict[str, Any]]:
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]


def test_cut_streamed_round_estimate_counts_reasoning() -> None:
    calls: list[dict] = []
    agent = _agent(_CutStreamClient(reasoning=_REASONING), calls)
    agent._stream_agentic_round(_messages(), [{"type": "function"}])
    assert len(calls) == 1
    assert calls[0]["estimated"] is True
    assert calls[0]["completion_tokens"] >= 10_000


def test_streamed_round_estimate_counts_tool_call_arguments() -> None:
    calls: list[dict] = []
    args = '{"command": "' + "x" * 20_000 + '"}'
    agent = _agent(_CutStreamClient(args=args), calls)
    agent._stream_agentic_round(_messages(), [{"type": "function"}])
    assert calls[0]["completion_tokens"] >= 5_000


def test_blocking_round_estimate_counts_reasoning_and_arguments() -> None:
    calls: list[dict] = []
    agent = _agent(SimpleNamespace(_config=SimpleNamespace(model="glm-5.3@rikyu")), calls)
    raw = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        content="",
        model="glm-5.3@rikyu",
        raw_response={
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": _REASONING,
                        "tool_calls": [{"function": {"name": "f", "arguments": "y" * 7_000}}],
                    }
                }
            ]
        },
    )
    agent._record_chat_usage(raw, _messages())
    assert calls[0]["estimated"] is True
    assert calls[0]["completion_tokens"] >= 12_000


def test_prompt_estimate_counts_replayed_arguments_and_reasoning() -> None:
    calls: list[dict] = []
    agent = _agent(_CutStreamClient(), calls)
    history = _messages() + [
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "q" * 7_000,
            "tool_calls": [{"id": "a", "function": {"name": "f", "arguments": "z" * 7_000}}],
        },
        {"role": "tool", "tool_call_id": "a", "content": "ok"},
    ]
    agent._record_streaming_usage(history, "done")
    assert calls[0]["prompt_tokens"] >= 4_000


def test_measured_usage_still_wins() -> None:
    calls: list[dict] = []
    client = _CutStreamClient(reasoning=_REASONING)
    client._last_stream_usage = {"prompt_tokens": 10, "completion_tokens": 7, "total_tokens": 17}
    agent = _agent(client, calls)
    agent._record_streaming_usage(_messages(), "")
    assert calls[0]["estimated"] is False
    assert calls[0]["completion_tokens"] == 7
