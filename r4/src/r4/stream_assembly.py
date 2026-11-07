"""Assemble an OpenAI chat-completion stream into the non-streaming response shape.

ADR-087 (harness model-call contract): blocking chat calls are sent upstream as
streams so long reasoning turns keep bytes flowing past proxy idle cuts (rikyu's
gateway and nginx ``/gw/llm/`` answer 502 after 600 s without a byte; the UMRS LLM
proxy read timeout was 120 s). Callers still get one response dict with the same
fields a non-streaming call returns: ``id``, ``model``, ``created``,
``choices[0].message`` (``content``, ``reasoning_content``, ``tool_calls``),
``choices[0].finish_reason`` and ``usage``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

# Thinking budget (ADR-087 amendment, 2026-10-06): a stream stopped by the
# client because the model deliberated past the budget without starting an
# answer or a tool call carries this finish_reason; the agent then closes the
# deliberation with an assistant prefill so the model acts on its plan.
THINK_BUDGET_FINISH = "think_budget"


# Tool-call arguments budget (ADR-087 D-6, 2026-10-06): glm-5.3 moved its
# deliberation into tool calls — heredoc scripts of 44k-268k chars, 92-99% comment
# lines ("let me carefully plan…"), one taking 59 min — which the thinking budget
# cannot see. A round whose tool-call arguments pass the budget, or a quarter of it
# while nearly every line is a comment, is stopped with this finish_reason.
ARGS_BUDGET_FINISH = "args_budget"
# Reply-text budget (ADR-087 D-7, 2026-10-06): the next displacement — 131,072
# tokens of plain reply text with no tool call (89 min), which also ended a TB 4.0
# task as its "final answer". Text past the budget before any tool call stops it.
TEXT_BUDGET_FINISH = "text_budget"
_PLANNING_COMMENT_SHARE = 0.8
_PLANNING_MIN_LINES = 100


@dataclass(frozen=True)
class RoundLimits:
    """Per-round stream limits (0 = off): reasoning before any answer or tool call,
    in chars (``think_chars``, D-5) and in wall-clock seconds from the request
    (``think_seconds``, D-5b), tool-call arguments (``args_chars``, D-6) and
    reply text before any tool call (``text_chars``, D-7)."""

    think_chars: int = 0
    args_chars: int = 0
    text_chars: int = 0
    think_seconds: float = 0

    def __bool__(self) -> bool:
        return max(self.think_chars, self.args_chars, self.text_chars, self.think_seconds) > 0


def past_think_budget(reasoning_chars: int, acted: bool, budget_chars: int) -> bool:
    """True when the reasoning outgrew the budget before any content/tool call."""
    return budget_chars > 0 and not acted and reasoning_chars > budget_chars


def planning_in_args(args: str, budget_chars: int) -> bool:
    """True when tool-call arguments (raw JSON text) outgrew the budget, or a quarter
    of it while nearly every line is a comment: planning written into code."""
    if budget_chars <= 0 or len(args) <= budget_chars // 4:
        return False
    if len(args) > budget_chars:
        return True
    lines = args.split("\\n")  # newlines inside the JSON string are escaped
    if len(lines) < _PLANNING_MIN_LINES:
        return False
    comments = sum(1 for line in lines if line.lstrip().startswith(("#", "//")))
    return comments >= _PLANNING_COMMENT_SHARE * len(lines)


class RoundGuard:
    """A streamed round's reasoning and tool-call argument sizes against its
    ``RoundLimits``; ``stop_reason`` names the limit that should end the stream."""

    _CHECK_EVERY = 4096  # the argument scan runs once per 4k chars of growth

    def __init__(self, limits: RoundLimits | None = None) -> None:
        self.limits = limits or RoundLimits()
        self.thought = 0
        self.args = 0
        self.text = 0
        self.acted = False
        self.called = False
        self._next_args_check = 0
        self._started = time.monotonic()

    def add(
        self, *, reasoning: int = 0, args: int = 0, text: int = 0, called: bool = False
    ) -> None:
        self.thought += reasoning
        self.args += args
        self.text += text
        self.called = self.called or called
        self.acted = self.acted or self.called or self.text > 0

    def _over_think(self) -> bool:
        """Deliberating with no answer or tool call yet, past the char budget or
        the wall-clock one (D-5b: at ~14 tokens/s, 16k tokens took 18-20 min)."""
        if self.acted or self.thought <= 0:
            return False
        if past_think_budget(self.thought, self.acted, self.limits.think_chars):
            return True
        seconds = self.limits.think_seconds
        return seconds > 0 and time.monotonic() - self._started > seconds

    def stop_reason(self, args_text: Callable[[], str]) -> str | None:
        if self._over_think():
            return THINK_BUDGET_FINISH
        if not self.called and 0 < self.limits.text_chars < self.text:
            return TEXT_BUDGET_FINISH
        budget = self.limits.args_chars
        if budget <= 0 or self.args <= budget // 4 or self.args < self._next_args_check:
            return None
        self._next_args_check = self.args + self._CHECK_EVERY
        return ARGS_BUDGET_FINISH if planning_in_args(args_text(), budget) else None


class StreamError(Exception):
    """An ``error`` object arrived inside the stream."""

    def __init__(self, error: Any) -> None:
        message = error.get("message") if isinstance(error, dict) else str(error)
        super().__init__(str(message or error))
        self.error = error


class ChatStreamAssembler:
    """Feed SSE ``data:`` payloads (or parsed chunks); ``assemble()`` returns the response."""

    def __init__(self, limits: RoundLimits | None = None) -> None:
        self.meta: dict[str, Any] = {}
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None
        self.usage: dict[str, Any] | None = None
        self.chunks = 0
        self.guard = RoundGuard(limits)

    def feed_line(self, line: str) -> bool:
        """Feed one raw SSE line. Returns False on ``[DONE]``."""
        if not line.startswith("data:"):
            return True
        data = line[5:].strip()
        if data == "[DONE]":
            return False
        if not data:
            return True
        try:
            chunk = json.loads(data)
        except ValueError:
            return True
        self.feed(chunk)
        return True

    def feed(self, chunk: dict[str, Any]) -> None:
        if chunk.get("error") is not None:
            raise StreamError(chunk["error"])
        self.chunks += 1
        self._feed_meta(chunk)
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            self.usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            self._feed_delta(choice.get("delta") or {})
            self.finish_reason = choice.get("finish_reason") or self.finish_reason

    def _feed_meta(self, chunk: dict[str, Any]) -> None:
        for key in ("id", "created", "model", "object", "system_fingerprint"):
            if chunk.get(key) is not None:
                self.meta.setdefault(key, chunk[key])

    def _feed_delta(self, delta: dict[str, Any]) -> None:
        if delta.get("content"):
            self.content.append(str(delta["content"]))
            self.guard.add(text=len(str(delta["content"])))
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            self.reasoning.append(str(reasoning))
            self.guard.add(reasoning=len(str(reasoning)))
        for call in delta.get("tool_calls") or []:
            self._feed_tool_call(call)

    def _feed_tool_call(self, call: dict[str, Any]) -> None:
        slot = self.tool_calls.setdefault(
            int(call.get("index") or 0),
            {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
        )
        slot["id"] = call.get("id") or slot["id"]
        slot["type"] = call.get("type") or slot["type"]
        fn = call.get("function") or {}
        slot["function"]["name"] += fn.get("name") or ""
        slot["function"]["arguments"] += fn.get("arguments") or ""
        self.guard.add(args=len(fn.get("arguments") or ""), called=True)

    def stop_reason(self) -> str | None:
        """The round limit that should end the stream here, if any."""
        return self.guard.stop_reason(
            lambda: "".join(c["function"]["arguments"] for c in self.tool_calls.values())
        )

    def assemble(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self.content) or None}
        reasoning = "".join(self.reasoning)
        if reasoning:
            message["reasoning_content"] = reasoning
        if self.tool_calls:
            message["tool_calls"] = [self.tool_calls[i] for i in sorted(self.tool_calls)]
        response: dict[str, Any] = {
            "id": self.meta.get("id"),
            "object": "chat.completion",
            "created": self.meta.get("created"),
            "model": self.meta.get("model"),
            "choices": [{"index": 0, "message": message, "finish_reason": self.finish_reason}],
        }
        if self.usage is not None:
            response["usage"] = self.usage
        return response
