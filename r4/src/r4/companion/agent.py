"""ResearchCompanion — the primary agent for the companion module.

Wraps the r4 Client for LLM calls, manages a researcher-persona agent
with tool-use agentic loop, event-driven callbacks for UI integration,
and sub-agent spawning for delegated tasks.
"""

from __future__ import annotations

import contextvars
import importlib
import json
import logging
import os
import random
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, cast

from r4.agent.large_response import handle_large_response
from r4.client import STREAM_CUT_FINISH
from r4.errors import APIError, RateLimitError
from r4.stream_assembly import (
    ARGS_BUDGET_FINISH,
    TEXT_BUDGET_FINISH,
    THINK_BUDGET_FINISH,
    RoundLimits,
)

from .cancellation import TurnCancelledError
from .env_switch import autonomous as _env_autonomous
from .env_switch import env_switch
from .quota_notice import QuotaRefusedError, raise_quota_notice, rpm_wait_seconds
from .turn_context import (
    COMPACTION_INSTRUCTION,
    PLAN_GATE_REFUSAL,
    PLAN_MODE_REFUSAL,
    callable_tools_note,
    join_parts,
    merge_refusals,
    project_state_block,
    refusal_text,
    refusals_outside,
    stable_prefix_enabled,
    with_turn_context,
)

if TYPE_CHECKING:
    from r4.client import Client

    from .cancellation import TurnCancellation
    from .context import ContextManager
    from .permissions import PermissionManager
    from .skills import SkillRegistry

from .taint import TaintRegistry

logger = logging.getLogger(__name__)

try:
    from .context import CHARS_PER_TOKEN
except Exception:
    CHARS_PER_TOKEN = 3.5

# Cap each tool result so multi-step searches cannot blow the context window.
COMPANION_TOOL_OUTPUT_TRUNCATE = 40_000  # W2 2026-10-05 (was 12_000)
# Per-block evaluation cadence for tool rounds within one turn. After every
# block the loop evaluates "did the last block yield distinct evidence?":
# progress → another block starts automatically; no progress → stop and render
# the checkpoint fallback. This is what makes the cap a soft-opportunity bound,
# not a shutter.
DEFAULT_SOFT_BLOCK_ROUNDS = 25
DEFAULT_TOTAL_TOOL_ROUNDS = 1000
# Back-compat alias for callers/tests pinned to the cap-era name.
DEFAULT_MAX_TOOL_ROUNDS = DEFAULT_SOFT_BLOCK_ROUNDS
# Last-N same-tool executions with failure markers always trip the breaker —
# an agent that keeps failing on the same tool class will not recover by
# guessing different flags.
TOOL_FAILURE_STREAK_TRIP = 4
TOOL_SSH_TARGET_STALL_WINDOW = 4
# Tool steps the loop guards and the round-exhausted digest look back over.
_RECENT_STEP_WINDOW = 8

# Model families whose tool-call emissions are semantically unreliable in this
# stack. kimi-k3 is handled by the open-pipe parser (_parse_open_pipe_tool_calls)
# since 2026-08-30; its entries were removed once that landed. Families that
# stay here lack a dialect parser — the session starts with tools pre-disabled
# rather than a mid-run shutdown.
KNOWN_BAD_TOOL_MODEL_FAMILIES: frozenset = frozenset(
    {
        "deepseek-chat-routed",
    }
)

# Sentinel for "skip this model-call round, keep iterating" from the
# answer-mode fast path / final-text branch helpers. A string sentinel
# would collide with real model text; an object never does.
_LOOP_CONTINUE = object()

# Output-budget exhaustion recovery (2026-09-30, terminal-bench biped/ks class):
# a reasoning model can spend its whole window on long turns and reply with
# finish_reason="length", content=None, and zero tool calls. Before this
# ladder the turn silently returned "" as a SUCCESS — the bench counted an
# empty string as a completed task. One answer-mode wrap-up nudge; on repeat,
# a named diagnostic (never silent).
# ADR-087 (2026-10-05): the nudge asks the model to CONTINUE — in an agentic
# task "write the final answer now, no tool calls" made it give up (TB 4.0:
# 4 of 17 failures ended after 3-11 calls). Several recoveries per turn
# (R4_COMPANION_LENGTH_RECOVERIES, default 4) before the named stop; the cut
# reasoning's tail is handed back so the model does not start over.
_OUTPUT_BUDGET_NUDGE = (
    "Your last reply hit the output limit while still reasoning, before any tool "
    "call or answer. Do not restart the analysis and do not stop: continue from "
    "where you were — make the next tool call, or give the final answer if the "
    "task is done — and keep your reasoning short."
)
# Tail of the cut reasoning carried into the continuation nudge. GLM-style chat
# templates drop reasoning before the last user message, so this tail is all the
# model keeps of a cut reply; with 2,000 chars a TB 4.0 trial restarted its
# analysis twice in a row (two ~80-min replies, 2026-10-05). R4_COMPANION_LENGTH_TAIL_CHARS.
_OUTPUT_BUDGET_TAIL_CHARS = 12_000
_OUTPUT_BUDGET_STOP = (
    "(Model exhausted its output budget {n} times without producing a tool call "
    "or a final answer (finish_reason=length). Narrow the ask, or retry with a "
    "model/backend offering a larger output budget.)"
)
# An empty reply (no text, no tool call) in tool mode gets a continuation
# nudge instead of disabling tools for the session (ADR-087).
_EMPTY_REPLY_NUDGE = (
    "Your last reply was empty — no text and no tool call. Continue the task: "
    "make the next tool call, or give the final answer if the task is done."
)
_EMPTY_REPLY_MAX_NUDGES = 2

# H17 (TB 4.0 re-run 2026-10-05, ks-solver-cpp): in tool mode the model ended a
# reply with "Let me isolate it with a random-matrix test:" and no tool call; R4
# took the announcement as the final answer and the task ended mid-debugging.
_UNFINISHED_INTENT_NUDGE = (
    "Your last reply announced a next step but made no tool call, so nothing ran. "
    "If the task is not finished, make that tool call now. If it is finished, "
    "give the final answer."
)
_UNFINISHED_INTENT_MAX_NUDGES = 3
_INTENT_OPENERS = re.compile(
    r"^(?:ok(?:ay)?[,.]?\s+|now[,]?\s+|next[,]?\s+|first[,]?\s+|so[,]?\s+)*"
    r"(?:let me|let's|i'll|i will|i am going to|i'm going to|i need to|we need to)\b",
    re.I,
)
_NOT_INTENT = re.compile(r"\blet me know\b|\bif you\b|\bwould you\b", re.I)


def _announces_unfinished_step(content: str) -> bool:
    """True when a text-only reply ends by announcing an action it did not take:
    the last line ends with a colon, or its last sentence opens with "let me" /
    "I'll" / "next I need to" and the like (never "let me know")."""
    lines = [line.strip() for line in (content or "").strip().splitlines() if line.strip()]
    if not lines:
        return False
    last = lines[-1].strip("`*_ ")
    if _NOT_INTENT.search(last):
        return False
    if last.endswith(":"):
        return True
    sentence = re.split(r"(?<=[.!?])\s+", last)[-1]
    if len(sentence.partition(":")[2].strip()) > 20:
        return False  # "I'll summarize: <the answer>" carries the answer itself
    return bool(_INTENT_OPENERS.match(sentence))


def _output_length_exhausted(response: dict[str, Any], content: str) -> bool:
    """True when the round died at the output budget with nothing to show."""
    choices = response.get("choices") or [{}]
    if not choices or not isinstance(choices[0], dict):
        return False
    if (choices[0].get("finish_reason") or "") != "length":
        return False
    return not (content or "").strip()


def _round_reasoning(round_message: dict[str, Any] | None) -> str | None:
    """Reasoning text of a round under either field name (ADR-087)."""
    message = round_message or {}
    return message.get("reasoning_content") or message.get("reasoning")


# Unattended runs (R4_AUTONOMOUS=1) keep recovering: the ladder's final stop
# always meant a failed task there (TB 4.0 photonic: 12/14 tests, ended on the
# fifth cut, 2026-10-06); the task's own time limit is the real bound.
_AUTONOMOUS_LENGTH_RECOVERIES = 12


def _length_recoveries() -> int:
    """Continuation nudges allowed per turn after output-limit cuts (ADR-087)."""
    default = _AUTONOMOUS_LENGTH_RECOVERIES if _autonomous() else 4
    raw = os.environ.get("R4_COMPANION_LENGTH_RECOVERIES", "").strip()
    try:
        return max(0, int(raw)) if raw else default
    except ValueError:
        return default


def _length_nudge_text(response: dict[str, Any]) -> str:
    """The continuation nudge, carrying the tail of the cut reasoning."""
    choices = response.get("choices") or [{}]
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") or {}
    reasoning = str(message.get("reasoning_content") or message.get("reasoning") or "")
    if not reasoning.strip():
        return _OUTPUT_BUDGET_NUDGE
    tail = reasoning[-_env_chars("R4_COMPANION_LENGTH_TAIL_CHARS", _OUTPUT_BUDGET_TAIL_CHARS) :]
    return f"{_OUTPUT_BUDGET_NUDGE}\n\nThe end of your cut-off reasoning was:\n…{tail}"


def _env_chars(name: str, default: int) -> int:
    """A non-negative char count from the environment (bad values → default)."""
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() else default


# Thinking budget (ADR-087 amendment, 2026-10-06). glm-5.3 at a design point of a
# hard task deliberates past 16k tokens in 24/24 samples whatever the request
# shape (system prompt, tool schemas, reasoning replay, text vs tool history),
# and terminus-2 on the same tasks has 17-26 min replies too: it is the model,
# and at ~25 tokens/s one reply costs 30-80 min. A continuation nudge after a
# cut keeps deliberating (6/6). Closing the deliberation instead — the same
# request plus an assistant prefill holding the reasoning so far, sent with
# ``continue_final_message`` — made the model act at once in 6/6 (115-479
# tokens): it ran the experiment it had been designing around. Budget in
# tokens (R4_COMPANION_THINK_BUDGET, 0 = off), counted as ~4 chars per token.
_THINK_BUDGET_TOKENS = 16_000
_THINK_BUDGET_CHARS_PER_TOKEN = 4
_THINK_CLOSE_LINE = (
    "\n\nI have deliberated long enough. I will act on the plan now with the next concrete step."
)


# Sampling defaults (H14, 2026-10-24): Z.ai recommends 1.0 / top_p 0.95 for
# glm-5.3; the public TB 4.0 rows do not state their settings. R4 keeps 0.4
# and sends no top_p unless R4_COMPANION_TEMPERATURE / R4_COMPANION_TOP_P say
# otherwise.
_DEFAULT_TEMPERATURE = 0.4


def _env_float(name: str, *, upper: float) -> float | None:
    """A float in [0, upper] from the environment (unset, bad or out of range → None)."""
    try:
        value = float(os.environ.get(name, "").strip())
    except ValueError:
        return None
    return value if 0 <= value <= upper else None


def _initial_temperature(explicit: float | None) -> float:
    """The constructor argument, else R4_COMPANION_TEMPERATURE, else 0.4."""
    if explicit is not None:
        return explicit
    configured = _env_float("R4_COMPANION_TEMPERATURE", upper=2.0)
    return _DEFAULT_TEMPERATURE if configured is None else configured


def _think_budget_chars() -> int:
    """Reasoning chars a round may spend before its deliberation is closed."""
    tokens = _env_chars("R4_COMPANION_THINK_BUDGET", _THINK_BUDGET_TOKENS)
    return tokens * _THINK_BUDGET_CHARS_PER_TOKEN


# Wall-clock cap on the same deliberation (D-5b, 2026-10-07): with 8 TB trials on
# rikyu each stream decoded ~14 tokens/s, so 16k tokens took 18-20 min and one
# step with a tool-call cut reached 29 min. Whichever limit comes first closes it.
_THINK_BUDGET_SECONDS = 600


def _think_budget_seconds() -> int:
    """Seconds a round may deliberate (from the request) before it is closed."""
    return _env_chars("R4_COMPANION_THINK_BUDGET_SEC", _THINK_BUDGET_SECONDS)


# Tool-call arguments budget (ADR-087 D-6, 2026-10-06): with the thinking budget
# in place, glm-5.3 moved its deliberation into tool calls — heredoc scripts of
# 44k-268k chars, 92-99% comment lines; the 268k one took 59 min. A round whose
# tool-call arguments pass R4_COMPANION_TOOL_ARGS_BUDGET chars, or a quarter of it
# while nearly every line is a comment, is stopped before the call runs and the
# model is asked for a runnable step (stream_assembly.planning_in_args). 0 = off.
_TOOL_ARGS_BUDGET_CHARS = 100_000
_ARGS_TAIL_CHARS = 4_000
_PLANNING_IN_ARGS_NUDGE = (
    "Your last tool call was stopped before it ran: its arguments grew past the size "
    "limit for one call. Such calls are usually planning written as code comments. "
    "Plan in your reasoning and keep it short, then make a tool call that runs one "
    "concrete step; write a large file in parts of a few hundred lines."
)


# Reply text without a tool call (ADR-087 D-7, 2026-10-06): the deliberation's
# third home after reasoning (D-5) and code comments (D-6). A TB 4.0 round wrote
# 131,072 tokens of plain text (89 min), and the cut text ended the task as its
# "final answer". In tool mode a cut text reply now goes back to the ladder with
# its tail kept, and unattended runs stop text past R4_COMPANION_TEXT_BUDGET
# chars before any tool call (default 64,000 when R4_AUTONOMOUS=1, else off).
_TEXT_BUDGET_CHARS = 64_000
_TEXT_TAIL_CHARS = 8_000
_TEXT_CUT_NUDGE = (
    "Your last reply was cut off as long reply text without any tool call; that is "
    "planning written as text, not a finished task. Make the tool call for the next "
    "concrete step now, and keep any explanation to a few sentences."
)


def _text_budget_chars() -> int:
    default = _TEXT_BUDGET_CHARS if _autonomous() else 0
    return _env_chars("R4_COMPANION_TEXT_BUDGET", default)


def _is_text_cut(response: dict[str, Any], content: str, tool_mode: bool) -> bool:
    """A tool-mode reply cut as text (output limit or text budget) — never an answer."""
    finish = _response_choice(response).get("finish_reason")
    return tool_mode and finish in ("length", TEXT_BUDGET_FINISH) and bool(content.strip())


def _clip_text_tail(text: str) -> str:
    if len(text) <= _TEXT_TAIL_CHARS:
        return text
    return f"[… {len(text) - _TEXT_TAIL_CHARS:,} chars omitted …]\n{text[-_TEXT_TAIL_CHARS:]}"


def _planning_cut(response: dict[str, Any]) -> dict[str, Any]:
    """An args-budget round without its unfinished tool calls, carrying the tail
    of what they wrote (``_args_tail``) for the continuation nudge."""
    choice = _response_choice(response)
    message = dict(choice.get("message") or {})
    calls = message.pop("tool_calls", None) or []
    args = "".join(str((c.get("function") or {}).get("arguments") or "") for c in calls)
    message["content"] = ""
    return {
        **response,
        "choices": [{**choice, "message": message, "finish_reason": ARGS_BUDGET_FINISH}],
        "_args_tail": args[-_ARGS_TAIL_CHARS:],
    }


def _planning_nudge_text(response: dict[str, Any]) -> str:
    tail = str(response.get("_args_tail") or "")
    tail = tail.replace("\\n", "\n").replace('\\"', '"').replace("\\t", "\t")
    if not tail:
        return _PLANNING_IN_ARGS_NUDGE
    return f"{_PLANNING_IN_ARGS_NUDGE}\n\nThe end of what you wrote:\n…{tail}"


def _streamed_round_payload(
    text: str, reasoning: str, tool_calls: list[dict[str, Any]], finish: Any
) -> dict[str, Any]:
    """A streamed round in the non-streaming response shape."""
    message: dict[str, Any] = {"content": text}
    if reasoning:
        # goose pattern: echo reasoning on the assistant message so the
        # model keeps self-consistency across tool-call chains
        # (required by deepseek-family providers).
        message["reasoning_content"] = reasoning
    calls = [call for call in tool_calls if not _is_placeholder_call(call)]
    if calls:
        message["tool_calls"] = calls
    finish = finish if isinstance(finish, str) else None
    return {"choices": [{"message": message, "finish_reason": finish}]}


def _is_placeholder_call(call: dict[str, Any]) -> bool:
    """An index slot no delta filled (a stream that skipped indexes: its
    first ``tool_calls`` delta said ``index: 5``). It has no id, no name and
    no arguments; each one used to ask its own approval for the tool ""."""
    function = call.get("function") or {}
    return not (call.get("id") or function.get("name") or function.get("arguments"))


def _call_arguments(call: dict[str, Any]) -> str:
    args = (call.get("function") or {}).get("arguments")
    return args if isinstance(args, str) else json.dumps(args or {}, default=str)


def _message_text(message: dict[str, Any]) -> str:
    """What a message carries to or from the model, for usage estimates: content,
    reasoning and tool-call arguments (H13, 2026-10-24). Content alone logged a
    round cut at the thinking budget — up to 16k tokens of reasoning — as one
    completion token."""
    parts = [str(message.get("content") or ""), str(_round_reasoning(message) or "")]
    parts.extend(_call_arguments(call) for call in message.get("tool_calls") or [])
    return "".join(parts)


def _messages_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(_message_text(m) for m in messages)


def _completion_text(raw: Any) -> str:
    """The reply of a blocking round (``client._request`` result) for the estimate."""
    message = dict(_response_choice(raw.raw_response or {}).get("message") or {})
    message["content"] = raw.content or message.get("content") or ""
    return _message_text(message)


def _budget_kwargs(limits: RoundLimits | None) -> dict[str, Any]:
    """Client kwargs for a bounded round; none at all when unbounded, so client
    doubles without the parameter keep working."""
    return {"limits": limits} if limits else {}


def _response_choice(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") or [{}]
    return choices[0] if isinstance(choices[0], dict) else {}


def _with_finish(response: dict[str, Any], finish: str) -> dict[str, Any]:
    """A shallow copy of ``response`` whose first choice ends with ``finish``."""
    return {**response, "choices": [{**_response_choice(response), "finish_reason": finish}]}


def _merge_closed_reasoning(forced: dict[str, Any], reasoning: str) -> dict[str, Any]:
    """The forced reply, carrying the closed deliberation as its reasoning."""
    choice = _response_choice(forced)
    message = dict(choice.get("message") or {})
    own = str(_round_reasoning(message) or "").strip()
    message["reasoning_content"] = f"{reasoning}\n\n{own}" if own else reasoning
    message.pop("reasoning", None)
    return {**forced, "choices": [{**choice, "message": message}]}


@dataclass
class _RoundPostState:
    """Mutable loop state returned by ResearchCompanion._post_round_state."""

    force_text_response: bool
    should_continue: bool
    corrective_nudge_sent: bool
    last_progress_marker: str


_TOOL_FAILURE_MARKERS = (
    "timed out",
    "timeout",
    "connection refused",
    "command not found",
    "permission denied",
    "no route to host",
    "network is unreachable",
    "econnrefused",
    "non-zero exit status",
)


@dataclass
class _PreparedToolCall:
    """A tool call after serial prepare (args parse, pre-hooks, permissions)."""

    name: str
    args: dict[str, Any]
    tool_def: dict[str, Any] | None
    error_result: str | None
    # Provider-side call id (round-3 task 2): keys the per-session result
    # journal so the webview can fetch the full result on demand.
    tool_call_id: str = ""
    # ADR-108 W1c: the auto-worktree note that leads this call's result.
    note: str = ""


def _auto_worktree(tool_name: str) -> str:
    """Auto mode: enter the session's worktree before its first writer."""
    from .session_worktree import current_controller

    controller = current_controller()
    return controller.maybe_auto(tool_name) if controller is not None else ""


def _worktree_guard() -> Any:
    """The session worktree's write guard (a no-op outside one)."""
    import contextlib

    from .session_worktree import current_controller

    controller = current_controller()
    return controller.guard() if controller is not None else contextlib.nullcontext()


def _prepared_failure(
    name: str,
    args: dict[str, Any],
    tool_call_id: str,
    kind: str,
    detail: str,
) -> "_PreparedToolCall":
    """Compact prepared-call for the early-exit failure paths (complexity gate)."""
    return _PreparedToolCall(
        name=name,
        args=args,
        tool_def=None,
        error_result=_tool_failure_text(kind, name=name, detail=detail),
        tool_call_id=tool_call_id,
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class _LiveReasoning(list):
    """``reasoning_acc`` that also hands each reasoning delta to *emit*.

    The client only ever ``append``s to it (``client._collect_reasoning``), so
    reasoning reaches the TUI's Thought block as it streams instead of only
    being stored on the round (TUI audit r2 #22, 2026-10-17).
    """

    def __init__(self, emit: Callable[[str], None]) -> None:
        super().__init__()
        self._emit = emit

    def append(self, item: str) -> None:
        super().append(item)
        if item:
            self._emit(item)


class CompanionEventType(Enum):
    """Events emitted by the companion during a turn."""

    THINKING = "thinking"
    RESPONSE_START = "response_start"
    RESPONSE_CHUNK = "response_chunk"
    RESPONSE_DONE = "response_done"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_BLOCKED = "tool_blocked"
    TOOLS_DISABLED = "tools_disabled"
    AGENT_SPAWN = "agent_spawn"
    AGENT_RESULT = "agent_result"
    CONTEXT_COMPACT = "context_compact"
    # Live reasoning (reasoning_content deltas) and its end, per round; the
    # JSONL bridge turns them into canonical thinking_delta/thinking_done.
    REASONING_DELTA = "reasoning_delta"
    REASONING_DONE = "reasoning_done"
    TOOL_SELECTION = "tool_selection"
    ERROR = "error"


@dataclass
class CompanionEvent:
    """Single event payload."""

    event_type: CompanionEventType
    message: str
    data: dict[str, Any] | None = None
    timestamp: datetime = field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

COMPANION_SYSTEM_PROMPT = """\
You are the R4 Research Companion — a dedicated AI colleague for scientists \
and researchers working on the R4 Platform (RiVault).

Your role:
- Help researchers design, run, and analyze computational experiments
- Guide HPC workflow design (Slurm, Singularity, GPU allocation)
- Assist with data analysis, visualization, and interpretation
- Help navigate the r4 SDK, MCP tools, and RAG capabilities
- Provide domain-aware scientific guidance

Principles:
- Be concise and technically precise
- Prefer showing working code over lengthy explanations
- Always validate parameters before submitting HPC jobs
- Never download models inside inference containers — use `r4 model stage`
- Route all LLM traffic through the Gateway, never directly to vLLM
- Use presigned URLs for file access, not direct uploads

When using tools:
- Batch independent reads and commands as parallel tool calls in one response
- For destructive or costly operations, ask for confirmation
- When you need the user to decide or confirm (choosing between options,
  yes/no before acting), call ask_user — never leave the question only in
  chat prose, so the UI can render the answering card
- After failures, diagnose and suggest a fix
- A tool error (5xx, 403, unavailable) is one fact: note it and offer the
  remaining useful answer — do not re-emit an earlier summary
- final_output is the turn's terminal: call it exactly once when the work is
  done; its content IS the answer the user reads. After it, stop.
- Local file/shell tools (file_read, file_write, file_edit, list_dir,
  grep_search, shell_exec, python_exec) operate on the LOCAL filesystem;
  tools named *_platform (file_upload_platform, ...) operate on platform
  storage (S3/catalog) — use lab_catalog_search / lab_file_read for repo or
  lab files, not storage uploads.

PROTOCOL VIOLATION PREVENTION — false claims are a critical failure:
- You MUST cite the output of `git log -1 --format=%H` when claiming a commit
- You MUST show the diff when claiming a change (use `git diff` or the
  structured diff returned by file_edit/apply_patch)
- You MUST show test output when claiming tests pass (use the structured
  result from test_code or shell_exec)
- You MUST show lint output when claiming lint passes
- Failure to cite verifiable evidence is a protocol violation and will be
  treated as a false claim — the user will lose trust in all future claims
- If you cannot verify a claim, say "I cannot verify this" instead of
  claiming it is done
"""


# D-28 project-state block budget: hard cap on the context contribution.
_PROJECT_STATE_BUDGET = 4000


def _load_r4md_briefing() -> str | None:
    """Load ``R4.md`` per-repo agent briefing, if present.

    ``R4.md`` is a Markdown file at the repository root (or any parent
    directory) that gives the agent project-specific context: naming
    conventions, build/test commands, file layout, and "do not" rules.
    Inspired by claw-code's ``CLAW.md`` and goose's ``AGENTS.md`` patterns.
    """
    try:
        from r4.briefing import load_briefing

        return load_briefing()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tool schema helpers
# ---------------------------------------------------------------------------


def _tools_to_openai_schema(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert companion tool definitions to OpenAI function-calling schema."""
    schemas = []
    for t in sorted(tools, key=lambda tool: tool.get("name", "")):
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                },
            }
        )
    return schemas


def _tool_calling_retryable(exc: Exception) -> bool:
    """Return whether a tool-enabled request should be retried without tools.

    Only 400-level errors with backend tool-calling configuration markers
    trigger a retry-without-tools.  A 500 is a transient server error, not
    a signal that the model lacks tool capability — re-raise it so the
    caller can retry (or fail) with tools still enabled.
    """
    if not isinstance(exc, APIError):
        return False

    status_code = exc.status_code or 0
    response_body = (exc.response_body or "").lower()

    tool_error_markers = (
        "auto tool choice requires",
        "tool-call-parser",
        "enable-auto-tool-choice",
    )
    return status_code == 400 and any(marker in response_body for marker in tool_error_markers)


def _context_overflow_retryable(exc: Exception) -> bool:
    """Return whether a chat request failed because the prompt exceeded the window."""
    if not isinstance(exc, APIError):
        return False
    if (exc.status_code or 0) != 400:
        return False
    body = (exc.response_body or "").lower()
    return any(marker in body for marker in ("context", "token", "length", "max_model"))


def _leading_system_count(messages: list[dict[str, Any]]) -> int:
    """How many messages the leading system block holds."""
    for index, message in enumerate(messages):
        if message.get("role") != "system":
            return index
    return len(messages)


def _in_flight_start(messages: list[dict[str, Any]]) -> int:
    """Index of the turn's first tool round (a tool call or result), or the end.

    Stored history carries only user and assistant text, so the first tool
    message belongs to the turn in flight."""
    for index, message in enumerate(messages):
        if message.get("role") == "tool" or message.get("tool_calls"):
            return index
    return len(messages)


def _turn_user_index(messages: list[dict[str, Any]]) -> int | None:
    """The user message that started the turn in flight, or None."""
    for index in range(_in_flight_start(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            return index
    return None


def _trim_messages_on_overflow(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop older messages to recover from a context-length failure.

    The user message that started the turn stays (H9, 2026-10-24): in an
    unattended run it is the task itself, and the oldest-quarter drop removed
    it. In a multi-turn session it is the current request, not the session's
    first message. Only the leading system block stays at the front (the
    notice joins it); a later system message (a mid-turn nudge) keeps its
    place among the rest.
    """
    if len(messages) <= 3:
        return messages

    lead = _leading_system_count(messages)
    system, rest = messages[:lead], messages[lead:]
    keep = _turn_user_index(rest)
    others = [index for index in range(len(rest)) if index != keep]
    dropped = set(others[: max(2, len(others) // 4)])
    kept = [message for index, message in enumerate(rest) if index not in dropped]
    notice = {
        "role": "system",
        "content": "[Earlier messages were trimmed to fit the model context window.]",
    }
    return system + [notice] + kept


def _user_requested_structured_output(user_message: str) -> bool:
    """Return whether the user explicitly asked for a structured format."""
    lower = user_message.lower()
    patterns = (
        r"\bjson\b",
        r"\byaml\b",
        r"\bxml\b",
        r"\btoml\b",
        r"\bcsv\b",
        r"\bstructured\b",
        r"\bschema\b",
        r"\bobject\b",
        r"\bkey[- ]value\b",
    )
    return any(re.search(pattern, lower) for pattern in patterns)


def _parse_json_object(content: str) -> dict[str, Any] | None:
    """Return a non-empty JSON object parsed from content, if any."""
    if not (content.startswith("{") and content.endswith("}")):
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and parsed else None


def _coerce_positive_int(value: Any, default: int) -> int:
    """Return a positive integer from value, falling back to default."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _progress_marker(recent_tool_steps: list[dict[str, str]]) -> str:
    """Compact identity of the evidence gathered in the trailing tool window.

    Used at each soft-block boundary: a new marker means the last block found
    genuinely new evidence (keep working); same marker means destined drift.
    """
    if not recent_tool_steps:
        return ""
    keys = {
        f"{step.get('name', '')}:{(step.get('result') or '')[:48]}" for step in recent_tool_steps
    }
    return "||".join(sorted(keys))


def _normalize_tool_arguments(arguments: str) -> str:
    """Return a stable JSON string for tool arguments."""
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return (arguments or "").strip()
    if not isinstance(parsed, dict):
        return (arguments or "").strip()
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def _fingerprint_tool_result(result: str, *, max_chars: int = 400) -> str:
    """Create a compact signature for loop-progress detection."""
    normalized = " ".join((result or "").split())
    if len(normalized) > max_chars:
        normalized = normalized[:max_chars]
    return normalized


# Aliases that count as a stall target on any mention (the original lane). Any
# other host counts only when the step's result shows the ssh connection itself
# failing, so exploratory probing of a reachable host stays open.
_KNOWN_SSH_ALIASES = ("rccs-cloud", "ai4s", "umrs-front", "bdrt")
_SSH_CONNECT_FAILURE = re.compile(
    r"\bssh: |could not resolve hostname|connection closed by|connection reset by"
    r"|kex_exchange_identification|host key verification failed|permission denied \("
)


def _ssh_host(command: str) -> str:
    """Host of the first ``ssh`` invocation in *command* (``user@`` dropped, lowercased)."""
    import shlex

    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    names = [token.rsplit("/", 1)[-1] for token in tokens]
    if "ssh" not in names:
        return ""
    index = names.index("ssh") + 1
    while index < len(tokens) and tokens[index].startswith("-"):
        index += 2 if tokens[index] in _SSH_VALUE_FLAGS else 1
    if index >= len(tokens):
        return ""
    return tokens[index].rsplit("@", 1)[-1].lower()


def _ssh_step_targets(step: dict[str, str]) -> set[str] | None:
    """Stall targets of one step; None when the step is not an ssh shell call."""
    if step.get("name") != "shell_exec":
        return None
    command = (step.get("arguments") or "").lower()
    if "ssh" not in command:
        return None
    hits = _ssh_alias_hits(command)
    return hits if hits else _failed_ssh_host(step)


def _ssh_alias_hits(command: str) -> set[str]:
    """Built-in aliases mentioned anywhere in the (lowercased) command."""
    return {alias for alias in _KNOWN_SSH_ALIASES if alias in command}


def _failed_ssh_host(step: dict[str, str]) -> set[str]:
    """The step's ssh host when its result shows the connection failing."""
    if not _SSH_CONNECT_FAILURE.search((step.get("result") or "").lower()):
        return set()
    host = _ssh_host(_step_command(step))
    return {host} if host else set()


def _stalling_ssh_target(tail: list[dict[str, str]]) -> str:
    """The repeated ssh target when EVERY step is an ssh call to the same host.

    A known alias trips on any mention (unchanged); any other host trips only
    when every step in the tail failed to connect to it.
    """
    per_step = [_ssh_step_targets(step) for step in tail]
    if any(targets is None for targets in per_step):
        return ""
    merged: set[str] = set().union(*(t for t in per_step if t))
    if len(merged) != 1:
        return ""
    target = next(iter(merged))
    if target not in _KNOWN_SSH_ALIASES and not all(per_step):
        return ""
    return target


def _switch_reason_text(
    *,
    repetitive: bool,
    failures: bool,
    ssh_target: str,
    repeated_tool: str,
) -> str:
    """Human-readable break reason for the answer-mode switch."""
    if failures and not repetitive:
        return f"the last {TOOL_FAILURE_STREAK_TRIP} '{repeated_tool}' calls all returned errors"
    if ssh_target:
        return (
            f"the last {TOOL_SSH_TARGET_STALL_WINDOW} shell calls all chase the same "
            f"ssh target ({ssh_target}) with flag variations — the connection cause is "
            "not discoverable by more retries"
        )
    return f"repeated '{repeated_tool}' calls with no progress"


def _should_switch_to_answer_mode(recent_steps: list[dict[str, str]]) -> str | None:
    """Return the break reason when tool mode should stop — None otherwise."""
    reason = _stall_reason(recent_steps)
    if reason is None:
        return _same_call_reason(recent_steps)
    return reason


def _stall_reason(recent_steps: list[dict[str, str]]) -> str | None:
    """Exact-replay, failure-streak and ssh-stall guards."""
    repetitive = _is_repetitive_tool_loop(recent_steps)
    failures = _is_failure_streak_breaker(recent_steps)
    ssh_target = ""
    if not repetitive and not failures and len(recent_steps) >= TOOL_SSH_TARGET_STALL_WINDOW:
        ssh_target = _stalling_ssh_target(recent_steps[-TOOL_SSH_TARGET_STALL_WINDOW:])
    if not repetitive and not failures and not ssh_target:
        return None
    return _switch_reason_text(
        repetitive=repetitive,
        failures=failures,
        ssh_target=ssh_target,
        repeated_tool=recent_steps[-1]["name"] or "unknown",
    )


# Same-call loops (H5, TB 4.0 plan 2026-10-24): a timing benchmark run 181 times
# (biped ``ifD6J9q``, about 2 h) and two heredoc scripts alternating about 53
# times (layout ``HUWHQob``) passed the exact-replay guard because their results
# changed. The same (name, arguments) 6 times in a row, or an A/B pair over 6
# steps, trips the guard whatever the results. Polls (sleep/tail/watch/wait
# outside heredoc bodies), scheduler and monitoring probes (squeue, sacct,
# nvidia-smi, git status, curl to localhost …), read-only probes and
# status-like tools are exempt. Results are compared as they are: no digits
# are stripped anywhere. The guard runs in unattended runs (R4_AUTONOMOUS=1),
# where a trip is a bounded nudge; R4_COMPANION_SAME_CALL_GUARD=0 turns it
# off there and =1 opts an interactive session in (review 2026-10-24: an
# interactive trip switches to answer mode, which a user watching a job with
# squeue must not get).
_SAME_CALL_RUN = 6
_POLL_WORDS = re.compile(r"\b(?:sleep|tail|watch|wait)\b")
_MONITOR_COMMAND = re.compile(
    r"\b(?:squeue|sacct|sinfo|scontrol|pjstat|qstat|bjobs|nvidia-smi|rocm-smi"
    r"|git\s+(?:status|log))\b"
    r"|\b(?:curl|wget)\b[^|;&\n]*\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0)\b"
)
_HEREDOC_BODY = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)
_POLL_TOOL_NAMES = re.compile(
    r"status|wait|poll|watch|log|read|fetch|list|search|show|result|health|progress"
)


def _step_command(step: dict[str, str]) -> str:
    raw = step.get("arguments") or ""
    try:
        args = json.loads(raw)
    except ValueError:
        return raw
    return str(args.get("command") or "") if isinstance(args, dict) else raw


def _polling_step(step: dict[str, str]) -> bool:
    """A poll or a read-only probe: repeating it is waiting, not looping."""
    name = str(step.get("name") or "")
    if name != "shell_exec":
        return bool(_POLL_TOOL_NAMES.search(name))
    command = _HEREDOC_BODY.sub("", _step_command(step))
    if _POLL_WORDS.search(command) or _MONITOR_COMMAND.search(command):
        return True
    return _is_read_only_shell_command(command)


def _same_call_guard_on() -> bool:
    """On in unattended runs; the switch overrides either way."""
    return env_switch("R4_COMPANION_SAME_CALL_GUARD", _autonomous())


def _call_key(step: dict[str, str]) -> tuple[str, str]:
    return str(step.get("name") or ""), str(step.get("arguments") or "")


def _same_call_tail(recent_steps: list[dict[str, str]]) -> list[dict[str, str]] | None:
    """The last steps when the guard applies: on, enough of them, at most two
    distinct calls, and no poll among them."""
    if len(recent_steps) < _SAME_CALL_RUN or not _same_call_guard_on():
        return None
    tail = recent_steps[-_SAME_CALL_RUN:]
    if len({_call_key(step) for step in tail}) > 2 or any(_polling_step(s) for s in tail):
        return None
    return tail


def _same_call_reason(recent_steps: list[dict[str, str]]) -> str | None:
    """Reason text when the tail is one call repeated, or an A/B pair alternating."""
    tail = _same_call_tail(recent_steps)
    if tail is None:
        return None
    keys = [_call_key(step) for step in tail]
    if len(set(keys)) == 1:
        return (
            f"the same '{keys[0][0]}' call ran {_SAME_CALL_RUN} times in a row "
            "and only details of its output changed"
        )
    if all(key == keys[i % 2] for i, key in enumerate(keys)):
        return (
            f"two '{keys[0][0]}'/'{keys[1][0]}' calls alternated over the last "
            f"{_SAME_CALL_RUN} steps without settling anything"
        )
    return None


def _any_named_step(steps: list[dict[str, str]], name: str) -> bool:
    """True when any recent step used *name* (comprehension kept out of the
    loop body for the complexity gate)."""
    return any(str(step.get("name")) == name for step in steps)


_JOURNAL_MAX_ENTRIES = 32
_JOURNAL_MAX_ENTRY_CHARS = 4 * 1024 * 1024  # 4 MiB per entry — one big read


def _journal_put(
    journal: "OrderedDict[str, tuple[str, str]]",
    tool_call_id: str,
    name: str,
    content: str,
) -> None:
    """Insert a full tool result into the bounded FIFO journal."""
    if tool_call_id in journal:
        journal.pop(tool_call_id, None)
    while len(journal) >= _JOURNAL_MAX_ENTRIES:
        journal.popitem(last=False)
    journal[tool_call_id] = (name, content[:_JOURNAL_MAX_ENTRY_CHARS])


def _round_tool_calls(
    agent: "ResearchCompanion",
    response: Any,
    force_text_response: bool,
) -> list[dict[str, Any]]:
    """Tool calls for this round; force-text rounds drop tool-shaped artifacts.

    Extracted from _run_agentic_turn so the loop body stays under the
    complexity gate.
    """
    tool_calls = agent._extract_tool_calls(response)
    if force_text_response and tool_calls:
        return []
    return tool_calls


def _without_null_optionals(tool_def: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Drop an explicit null for an optional parameter: it means "absent".

    Small models fill every optional parameter with null. Passed through, the
    null replaced the tool's own default, and ``file_read`` failed on
    ``int(None)`` for every call (llama3.2:3b on the stand-alone stack,
    2026-10-09). A required parameter keeps its null, so the tool still
    refuses it by name.
    """
    required = set((tool_def.get("parameters") or {}).get("required") or ())
    return {key: value for key, value in args.items() if value is not None or key in required}


def _terminal_tool_answer(
    messages: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
) -> str | None:
    """The executed ``final_output`` result for this round, if any.

    ``final_output`` is the companion's terminal channel: once it fires, its
    content is the turn's answer — the loop must not invite another report.
    Tool results for the round are the last ``len(tool_calls)`` role=tool
    entries, in call order (see _record_tool_round).
    """
    names = [str(tc.get("function", {}).get("name", "")) for tc in tool_calls]
    if "final_output" not in names:
        return None
    idx = names.index("final_output")
    round_results = [m for m in messages if m.get("role") == "tool"][-len(tool_calls) :]
    if idx >= len(round_results):
        return None
    content = str(round_results[idx].get("content") or "").strip()
    return content or None


def _failure_streak_only(recent_steps: list[dict[str, str]]) -> bool:
    """Failure streak candidate for a corrective nudge (not generic repetition)."""
    return bool(
        _is_failure_streak_breaker(recent_steps) and not _is_repetitive_tool_loop(recent_steps)
    )


def _corrective_nudge_message(recent_steps: list[dict[str, str]]) -> dict[str, str]:
    """OpenHands get_action_error_nudge pattern: one corrective system message
    before the loop halts on an error streak. The model must change approach
    or expressly concede — not keep replaying the failing call.
    """
    repeated = ""
    for step in reversed(recent_steps):
        if step.get("name"):
            repeated = step["name"]
            break
    repeated_name = repeated or "unknown"
    return {
        "role": "system",
        "content": (
            f"Several recent attempts with the same tool ('{repeated_name}') "
            "failed in a row. Do NOT repeat the same call again. Choose one: "
            "(1) vary the arguments fundamentally (different command/flags/paths), "
            "(2) use a DIFFERENT tool to obtain the same evidence, or "
            "(3) concede plainly: summarize what was gathered, name the blocker, "
            "and ask the user what to change. Do not loop."
        ),
    }


def _checkpoint_system_message(rounds_so_far: int) -> dict[str, str]:
    """Checkpoint nudge injected at soft block boundaries (copilot-style:
    keep iterating; never stop at a block boundary the model did not close
    itself). The visible digest only renders at the hard roof.
    """
    return {
        "role": "system",
        "content": (
            f"Checkpoint: {rounds_so_far} tool rounds consumed in this turn and "
            "the task is NOT closed — you keep iterating. Before your next tool "
            "call, write 2-3 sentences to the user stating: (1) what you have "
            "established so far, (2) what you are trying next and why, (3) "
            "whether the current approach is still the right one or should "
            "change. Then CONTINUE without waiting for user input — do not "
            "stop, do not ask for permission to continue."
        ),
    }


def _wrap_up_system_prompt() -> dict[str, str]:
    """opencode MAX_STEPS prompt pattern: the budget-closing wrap-up demand."""
    return {
        "role": "system",
        "content": (
            "Tool budget for this turn is exhausted — no more tool calls are "
            "possible. Using ONLY the evidence already in this conversation, "
            "write the concise checkpoint: (1) what you found (bullets, cite "
            "the tool observations), (2) what is still blocked or unknown, "
            "(3) the single concrete next action the user can give to resume. "
            "Plain text, no tool calls."
        ),
    }


# Unattended runs (R4_AUTONOMOUS=1: benchmarks, batch drivers) have nobody to
# answer a checkpoint, so a stuck-loop guard first nudges the model to change
# approach instead of ending the turn (2026-10-06: TB 4.0 mp-checkpoint polled a
# plateaued search three times with identical output; answer mode then ended the
# task on the interactive "pick one of: continue with …" digest).
_AUTONOMOUS_GUARD_NUDGES = 3


def _reachable_sections(status: str) -> str:
    """Drop ``## Section`` blocks whose fetch failed (``unavailable: …``).

    Failure = absent (D-28): offline, every section was an "unavailable:
    Connection refused … check that your SSH tunnel is forwarding :8080" line,
    so each TB 4.0 task started with six gateway-debugging hints and no project
    facts (2026-10-06). Nothing reachable → no block at all.
    """
    head, *sections = re.split(r"(?m)^(?=## )", status)
    kept = [
        sec
        for sec in sections
        if not any(line.startswith("unavailable:") for line in sec.splitlines()[1:])
    ]
    return (head + "".join(kept)).strip()


def _autonomous() -> bool:
    return _env_autonomous()


def _autonomous_guard_nudge(reason: str) -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            f"No progress: {reason}. Do not repeat that call. Change approach now. "
            "If you were waiting on a running job, check whether it is still making "
            "progress; if it has stalled, stop it and continue with the best result so "
            "far. Then finish the task's deliverables and verify them. Nobody will "
            "answer questions in this session — decide and act."
        ),
    }


# Completion review (H4, TB 4.0 plan 2026-10-24): a reply without a tool call, or
# final_output, ended an unattended task with nothing checking the work.
# terminus-2 asks for task_complete twice and shows the terminal state;
# LangChain's pre-completion checklist moved deepagents-cli from 52.8 to 66.5 on
# TB 2.0 with the model held fixed. Unattended runs get ONE review per task;
# R4_COMPANION_COMPLETION_REVIEW=0 turns it off. R4_COMPANION_TIME_BUDGET_SEC,
# when set, is quoted as the task's time budget.
_REVIEW_INSTRUCTION_CHARS = 8_000
_COMPLETION_REVIEW = (
    "[completion review] Before this task ends, check the work once against the task.\n\n"
    "The task was:\n<task>\n{task}\n</task>\n\n"
    "- Go through each requirement of the task and check it against the actual result: "
    "read or run the files at the exact paths the task names, in the formats it names. "
    "Do not rely on what you remember writing.\n"
    "- Assume the result will be checked by programmatic tests, so follow the spec's "
    "paths, names and output formats exactly.\n"
    "- Change only what fails, then finish. If everything holds, give a short final answer.\n"
    "{elapsed}"
)


def _completion_review_on() -> bool:
    return _autonomous() and env_switch("R4_COMPANION_COMPLETION_REVIEW", True)


def _elapsed_note(elapsed_seconds: float) -> str:
    used = f"Time used so far: {elapsed_seconds / 60:.0f} min"
    budget = _env_chars("R4_COMPANION_TIME_BUDGET_SEC", 0)
    return f"{used} (of a {budget / 60:.0f} min budget)." if budget else f"{used}."


def _completion_review_message(task: str, elapsed_seconds: float) -> dict[str, str]:
    text = task.strip()
    if len(text) > _REVIEW_INSTRUCTION_CHARS:
        omitted = len(text) - _REVIEW_INSTRUCTION_CHARS
        text = (
            f"{text[:_REVIEW_INSTRUCTION_CHARS]}\n[… {omitted:,} more chars: see the first message]"
        )
    content = _COMPLETION_REVIEW.format(task=text, elapsed=_elapsed_note(elapsed_seconds))
    return {"role": "user", "content": content}


def _answer_mode_system_nudge(reason: str) -> dict[str, str]:
    """System message appended when the loop flips into answer mode."""
    return {
        "role": "system",
        "content": (
            f"The last few tool calls hit a wall ({reason}). "
            "Do not call tools again in this turn. "
            "State plainly what blocked progress, summarize the evidence "
            "already gathered, and tell the user concretely what to fix "
            "before retrying (env variables, ssh config, permissions). "
            "Do not guess further."
        ),
    }


_READ_ONLY_SHELL_VERBS = {
    "hostname",
    "ls",
    "cat",
    "head",
    "tail",
    "echo",
    "pwd",
    "which",
    "whoami",
    "uname",
    "date",
    "df",
    "du",
    "wc",
    "find",
    "type",
}
_SHELL_MUTATION_MARKERS = (
    ">>",
    "| tee",
    "| bash",
    "| sh",
    "| zsh",
    "| sed -i",
    "| rm ",
    "| mv ",
    "| cp ",
    "| dd",
    "export ",
    "source ",
    ". ",
    "| tee -a",
)


def _read_only_verb_of(tokens: list[str]) -> bool:
    """Read-only when the leading verb belongs to the safe set."""
    if not tokens:
        return False
    verb = tokens[0].split("/")[-1].lower()
    return verb in _READ_ONLY_SHELL_VERBS


# ssh flags that consume a following value (common probe shapes).
_SSH_VALUE_FLAGS = {"-o", "-F", "-i", "-L", "-R", "-D", "-p", "-l", "-b", "-c", "-m"}


def _ssh_inner_command(command: str) -> str:
    """Extract the remote command from an ssh invocation (shlex-safe).

    Skips flag tokens (including value-taking ones like ``-o ConnectTimeout=8``),
    treats the first remaining bare token as the target host, and returns the
    remainder as the inner command. Empty on anything ambiguous.
    """
    import shlex

    try:
        tokens = shlex.split(command)
    except ValueError:
        return ""
    if not tokens or tokens[0].lower() != "ssh":
        return ""
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):
            index += 2 if token in _SSH_VALUE_FLAGS else 1
            continue
        index += 1
        break
    if index >= len(tokens):
        return ""
    return " ".join(tokens[index:])


def _is_read_only_shell_command(command: str) -> bool:
    """Conservative read-only shell-command classifier (parallel batch lane).

    Rules: whole command contains no mutation markers (redirection, in-place
    edits, destructive verbs, environment mutations), and the leading verb of
    the command (or of the trailing ``ssh`` inner command when remote) is a
    read-only one. Wrongly bundling a mutation is far worse than serializing a
    probe.
    """
    text = (command or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in _SHELL_MUTATION_MARKERS):
        return False
    if ">" in text:
        return False
    tokens = text.split()
    if not tokens:
        return False
    if tokens[0].lower() == "ssh":
        inner = _ssh_inner_command(command)
        return _read_only_verb_of(inner.split()) if inner else False
    return _read_only_verb_of(tokens)


def _exact_replay_loop(tail: list[dict[str, str]]) -> bool:
    """Every step in the tail has identical (name, args, result)."""
    first = tail[0]
    return all(
        step.get("name") == first.get("name")
        and step.get("arguments") == first.get("arguments")
        and step.get("result") == first.get("result")
        for step in tail[1:]
    )


def _failure_drift_loop(tail: list[dict[str, str]]) -> bool:
    """Same (name, result) with args drifted, and the result is a failure.

    The marker gate is what keeps legitimate stateless exploration (list_dir
    across different paths returning the same shape) from tripping a false
    positive — spotted by test_send_supports_deeper_progressive_tool_sequences.
    """
    first = tail[0]
    result = (first.get("result") or "").lower()
    if not any(marker in result for marker in _TOOL_FAILURE_MARKERS):
        return False
    return all(
        step.get("name") == first.get("name") and step.get("result") == first.get("result")
        for step in tail[1:]
    )


def _is_repetitive_tool_loop(
    recent_steps: list[dict[str, str]],
    *,
    window: int = 3,
) -> bool:
    """Return whether the latest tool executions indicate no progress."""
    if len(recent_steps) < window:
        return False
    tail = recent_steps[-window:]
    if not tail[0].get("name"):
        return False
    return _exact_replay_loop(tail) or _failure_drift_loop(tail)


def _is_failure_streak_breaker(
    recent_steps: list[dict[str, str]],
    *,
    window: int = TOOL_FAILURE_STREAK_TRIP,
) -> bool:
    """Last-window same-tool executions ARE all failures — stop guessing."""
    if len(recent_steps) < window:
        return False
    tail = recent_steps[-window:]
    first_name = tail[0].get("name")
    if not first_name:
        return False
    if not all(step.get("name") == first_name for step in tail):
        return False
    return all(
        any(marker in (step.get("result") or "").lower() for marker in _TOOL_FAILURE_MARKERS)
        for step in tail
    )


# Alternate (non-OpenAI) tool-call formats. Some models (e.g. Kimi-K2, or a
# Claude-style endpoint provisioned with the wrong tool parser) emit tool calls
# as XML inside ``message.content`` instead of structured ``tool_calls``. We
# parse those into the same shape the agentic loop already understands.
_OPEN_PIPE_OPEN = "<|open|>"
_OPEN_PIPE_CLOSE = "<|close|>"
_OPEN_PIPE_SEP = "<|sep|>"
_OPEN_PIPE_CALL_RE = re.compile(r'<\|open\|>call\s+tool="([^"]+)"(?:[^"<\|>]*index="([^"]+)")?')


def _coerce_open_pipe_value(raw: str, arg_type: str) -> Any:
    """Coerce a dialect argument value by its declared type (best-effort)."""
    value = raw.strip()
    if arg_type in ("number", "integer"):
        try:
            return int(value)
        except ValueError:
            return value
    if arg_type == "float":
        try:
            return float(value)
        except ValueError:
            return value
    if arg_type in ("object", "array"):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    if arg_type == "boolean":
        return value.lower() in ("true", "1", "yes")
    return value


def _parse_open_pipe_call_block(block: str, ordinal: int) -> dict[str, Any] | None:
    r"""Parse one ``<|open|>call…<\|close\|>call`` block into OpenAI tool_call shape."""
    match = _OPEN_PIPE_CALL_RE.search(block)
    if not match:
        return None
    name = match.group(1)
    call_id = match.group(2) or ordinal
    args: dict[str, Any] = {}
    for arg_match in re.finditer(
        r'<\|open\|>argument\s+key="([^"]+)"\s*(?:type="([^"]+)")?\s*'
        r"(?:<\|sep\|>)?(.*?)(?=<\|close\|>argument)",
        block,
        re.DOTALL,
    ):
        arg_name, arg_type, raw = arg_match.groups()
        args[arg_name] = _coerce_open_pipe_value(raw, arg_type or "string")
    return {
        "id": f"openpipe-{call_id}",
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def _squash_open_pipe_escapes(content: str) -> str:
    r"""Canonicalize marker escapes (kimi@openrouter emits literal backslashes).

    ``<\|open\|>`` (backslash-pipe literal) becomes ``<|open|>`` so downstream
    parsers see one shape regardless of which renderer produced the text.
    """
    return content.replace("<\\|", "<|").replace("\\|>", "|>").replace("\\_", "_")


def _parse_open_pipe_tool_calls(content: str) -> list[dict[str, Any]]:
    r"""Parse ``<|open|>call…`` dialect tool calls (kimi@openrouter family.

    The dialect wraps calls of the form::

        <|open|>tools<\|sep|><|open|>call tool="X" index="1"<\|sep|>
        <|open|>argument key="k" type="t"<\|sep|>value<\|close\|>argument<\|sep|>
        <|close\|>call<\|sep|><|close\|>tools<\|sep|>

    Backslash-escaped markers (kimi's actual bytes) are canonicalized first.
    Structural-only: ``call tool="…"`` is the hard anchor; blocks without a
    parseable header are skipped (text fallbacks carry the detail already).
    """
    normalized = _squash_open_pipe_escapes(content)
    if not _looks_like_open_pipe_dialect(normalized):
        return []
    calls: list[dict[str, Any]] = []
    for idx, block in enumerate(re.split(r"<\|close\|>call", normalized)):
        if not block.strip():
            continue
        parsed = _parse_open_pipe_call_block(block.strip(), idx)
        if parsed is not None:
            calls.append(parsed)
    return calls


def _looks_like_open_pipe_dialect(content: str) -> bool:
    """Dialect family emitted by e.g. kimi-k3@openrouter: ``<|open|>…<|close|>``.

    The model attaches its call as markup-quoted tokens instead of structured
    ``tool_calls``. Distinguish plain mentions of the markers (rare) from an
    actual attempt: require both the open/close markers AND a 'call' or 'tools'
    burst token in the same body.
    """
    return (
        _OPEN_PIPE_OPEN in content
        and _OPEN_PIPE_CLOSE in content
        and ("<|open|>call" in content or "<|open|>tools" in content)
    )


_XML_INVOKE_RE = re.compile(r"<invoke\s+name=\"([^\"]+)\"\s*>(.*?)</invoke>", re.DOTALL)
_XML_PARAM_RE = re.compile(r"<parameter\s+name=\"([^\"]+)\"\s*>(.*?)</parameter>", re.DOTALL)
_XML_TOOL_CALL_JSON_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_TAGGED_TOOL_SECTION_RE = re.compile(
    r"<tool_calls_section_begin>(.*?)<tool_calls_section_end>",
    re.DOTALL | re.IGNORECASE,
)
_TAGGED_TOOL_CALL_RE = re.compile(
    r"<tool_call_begin>\s*([^\s<]+)\s*"
    r"<tool_call_argument_begin>(.*?)<tool_call_argument_end>\s*"
    r"<tool_call_end>",
    re.DOTALL | re.IGNORECASE,
)


def _coerce_xml_param(raw: str) -> Any:
    """Best-effort coercion of an XML parameter value to a JSON scalar."""
    text = raw.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


def _parse_xml_json_tool_calls(content: str, *, start_index: int = 0) -> list[dict[str, Any]]:
    """Parse ``<tool_call>{json}</tool_call>`` blocks into OpenAI tool_calls."""
    calls: list[dict[str, Any]] = []
    for index, match in enumerate(_XML_TOOL_CALL_JSON_RE.finditer(content)):
        try:
            obj = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        name = obj.get("name") or obj.get("tool")
        if not name:
            continue
        arguments = obj.get("arguments")
        if arguments is None:
            arguments = obj.get("parameters", {})
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        calls.append(
            {
                "id": f"toolcall_{start_index + index}",
                "type": "function",
                "function": {"name": str(name), "arguments": arguments},
            }
        )
    return calls


def _parse_xml_tool_calls(content: str) -> list[dict[str, Any]]:
    """Parse Claude/Kimi-style XML tool calls from message content.

    Supports both ``<invoke name="...">/<parameter ...>`` blocks and
    ``<tool_call>{json}</tool_call>`` blocks, normalizing each to the OpenAI
    ``tool_calls`` shape so the agentic loop can execute them.
    """
    calls: list[dict[str, Any]] = []
    for index, match in enumerate(_XML_INVOKE_RE.finditer(content)):
        name = match.group(1).strip()
        args: dict[str, Any] = {}
        for param in _XML_PARAM_RE.finditer(match.group(2)):
            args[param.group(1).strip()] = _coerce_xml_param(param.group(2))
        calls.append(
            {
                "id": f"xmlcall_{index}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        )
    if calls:
        return calls

    return _parse_xml_json_tool_calls(content)


def _normalize_tagged_tool_name(name: str) -> str:
    raw = name.strip()
    for prefix in ("functions_", "function_"):
        if raw.startswith(prefix):
            return raw[len(prefix) :]
    return raw


def _tagged_calls_from_body(body: str, start_index: int) -> list[dict[str, Any]]:
    """One section's tagged calls, ids sequenced from a global index."""
    calls: list[dict[str, Any]] = []
    for index, match in enumerate(_TAGGED_TOOL_CALL_RE.finditer(body), start=start_index):
        name = _normalize_tagged_tool_name(match.group(1))
        arguments_raw = match.group(2).strip()
        arguments = arguments_raw or "{}"
        if not name:
            continue
        calls.append(
            {
                "id": f"taggedcall_{index}",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return calls


def _tagged_sections(content: str) -> list[Any]:
    """Section regex matches, with a whole-content fallback when the
    scaffold markers exist but the section regex missed them."""
    sections = list(_TAGGED_TOOL_SECTION_RE.finditer(content))
    if not sections and "<tool_call_begin>" in content and "<tool_call_end>" in content:
        return [re.match(r"(?s)(.*)", content)] if content else []
    return sections


def _parse_tagged_tool_calls(content: str) -> list[dict[str, Any]]:
    """Parse `<tool_call_begin>...` scaffolding into OpenAI-style tool calls."""
    calls: list[dict[str, Any]] = []
    for section in _tagged_sections(content):
        if section is None:
            continue
        body = section.group(1) if section.lastindex else content
        calls.extend(_tagged_calls_from_body(body, len(calls)))
    return calls


def _strip_tagged_tool_scaffold(content: str) -> str:
    """Remove tagged tool-call scaffolding from assistant-visible content."""
    stripped = _TAGGED_TOOL_SECTION_RE.sub("", content)
    stripped = re.sub(r"</?tool_[^>\s]+[^>]*>", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(
        r"<tool_call_begin>.*?<tool_call_end>",
        "",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if _looks_like_open_pipe_dialect(stripped):
        # Whole message is dialect-seeded tool-call text: drop it so the user
        # never sees raw call markup when detection couldn't flip tool mode.
        return ""
    return stripped.strip()


def _looks_like_tool_mode_artifact(content: str) -> bool:
    """Heuristic for bogus JSON/text artifacts emitted in tool mode.

    Some weaker models accept the ``tools`` payload but, instead of using
    actual OpenAI ``tool_calls``, reply with empty content or a fabricated JSON
    object such as ``{"response": ...}`` or ``{"user": {...}}``. Those are
    not useful assistant replies and should trigger a retry without tools.
    """
    stripped = content.strip()
    if "<tool_calls_section_begin>" in stripped and "<tool_call_begin>" in stripped:
        return True
    if _looks_like_open_pipe_dialect(stripped):
        return True
    if not stripped:
        return True
    parsed = _parse_json_object(stripped)
    if not parsed:
        return False

    suspicious_keys = {
        "assistant",
        "data",
        "message",
        "reply",
        "response",
        "result",
        "sessionId",
        "session_id",
        "status",
        "text",
        "user",
    }
    if suspicious_keys & set(parsed):
        return True

    if len(parsed) == 1:
        value = next(iter(parsed.values()))
        return isinstance(value, (str, dict, list))
    return False


def _iter_json_char_states(text: str):
    """Yield (idx, ch, in_string) while tracking JSON string/escape state."""
    in_string = False
    escaped = False
    for idx, ch in enumerate(text):
        yield idx, ch, in_string
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True


_JSON_CONTROL_ESCAPES = {"\n": "\\n", "\t": "\\t", "\r": "\\r"}


def _json_control_escape(ch: str) -> str | None:
    """JSON escape form of one control char, or None when not a control char."""
    if ord(ch) >= 0x20:
        return None
    known = _JSON_CONTROL_ESCAPES.get(ch)
    if known is not None:
        return known
    return f"\\u{ord(ch):04x}"


def _escape_json_control_chars(raw: str) -> str:
    """Escape control chars (<U+0020) inside double-quoted JSON strings.

    Pattern: OpenHands sanitize_json_control_chars — models frequently emit raw
    newlines/tabs inside string values, which strict JSON rejects. State-machine
    version preserves them as escapes instead of dropping the call.
    """
    out: list[str] = []
    for _idx, ch, in_string in _iter_json_char_states(raw):
        escape = _json_control_escape(ch) if in_string else None
        out.append(escape if escape is not None else ch)
    return "".join(out)


def _destringify_glm_values(obj: Any) -> Any:
    """GLM quirk: list/dict arguments arrive as JSON-stringified strings.

    Pattern: OpenHands fix_malformed_tool_arguments. Converts value strings
    that themselves parse fully as JSON containers into the container.
    """
    if isinstance(obj, str):
        text = obj.strip()
        if text.startswith(("{", "[")):
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                return obj
            if isinstance(parsed, (dict, list)):
                return parsed
        return obj
    if isinstance(obj, list):
        return [_destringify_glm_values(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _destringify_glm_values(v) for k, v in obj.items()}
    return obj


def _truncate_trailing_garbage(raw: str) -> str | None:
    """Cut text after the balanced closing brace of the first JSON object.

    Models sometimes append commentary after the payload; keep the balanced
    prefix (pattern: OpenHands trailing-garbage truncation).
    """
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for idx, ch, in_string in _iter_json_char_states(raw[start:]):
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[: start + idx + 1]
    return None


def _repair_tool_arguments(arguments: Any) -> dict[str, Any] | None:
    """Repair-before-reject argument parsing chain (pattern: OpenHands
    agent/utils.py — handle GLM/MiniMax-class malformation before failing).

    Accepts the already-parsed dict from dialect extraction (open-pipe path),
    strict JSON strings, strings with control chars, GLM stringified
    containers, or JSON followed by commentary. Returns the argument dict, or
    ``None`` when nothing recovers a dict.
    """
    if isinstance(arguments, dict):
        return _destringify_glm_values(dict(arguments))
    if not isinstance(arguments, str):
        return None

    text = arguments.strip()
    if not text:
        return {}

    return _parse_arguments_dict(_argument_parse_candidates(text))


def _argument_parse_candidates(text: str) -> list[str]:
    """Ordered parse candidates: raw, sanitized, balanced truncation."""
    candidates = [text]
    sanitized = _escape_json_control_chars(text)
    if sanitized != text:
        candidates.append(sanitized)
    truncated = _truncate_trailing_garbage(sanitized)
    if truncated and truncated not in candidates:
        candidates.append(truncated)
    return candidates


def _parse_arguments_dict(candidates: list[str]) -> dict[str, Any] | None:
    """First candidate parsing to a dict, destringified."""
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return _destringify_glm_values(parsed)
    return None


# Transient provider error classification (patterns: goose retry.rs
# RateLimit/5xx/Network-only; OpenHands transient exception whitelist;
# opencode retry.ts marker table). Overflow and tool-rejection paths are
# handled separately in _chat and must NOT classify as transient.
_TRANSIENT_ERROR_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "rate limit",
    "rate-limit",
    "overloaded",
    "service unavailable",
    "temporarily unavailable",
    "upstream error",
    "gateway timeout",
    "try again later",
    # 2026-09-30 (terminal-bench sweep): slow shared endpoints (glm-5.3@rikyu
    # on multi-hundred-k prompts) surface as R4TimeoutError — plain text
    # "Request timed out: the read operation timed out." matched NOTHING and
    # one slow burst killed the whole turn on the first attempt.
    "timed out",
    "read operation timed out",
)

_HARD_ERROR_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "too many tokens",
    "tool_call_parser",
    "does not support tool",
    "does not support function",
    "not be openai-compatible",
    "invalid tool",
    # ADR-099 D-5/D-6: platform quota refusals no retry clears today. The
    # proxy's 429 reaches r4 as its message text, so the phrases match too.
    "quota.tokens_per_day_exceeded",
    "quota.concurrent_sessions_exceeded",
    "daily token limit reached",
    "concurrent session limit reached",
)

# The turn's answer when the model returned nothing, even without tools.
EMPTY_REPLY_NOTE = "(the model returned an empty reply — retry, or switch models with /models)"

# Appended to a streamed reply the provider stopped sending mid-way (chat#11).
STREAM_CUT_NOTE = (
    "(reply may be incomplete — the model provider ended the stream without a "
    "finish signal; retry, or ask to continue)"
)

_TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


def _retry_delay_hint(exc: Exception) -> float | None:
    """Provider-supplied retry hint (Retry-After / retry_delay), else None."""
    for attr in ("retry_after", "retry_delay"):
        hint = getattr(exc, attr, None)
        if isinstance(hint, (int, float)) and not isinstance(hint, bool) and hint > 0:
            return float(hint)
    return None


def _status_is_transient(exc: Exception) -> bool:
    """Status-code attribute carries a transient code.

    A 429 arrives as RateLimitError, which has no ``status_code``: a streamed
    429 was never retried although 429 is transient here (companion sweep
    2026-10-21).
    """
    if isinstance(exc, RateLimitError):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return isinstance(status, int) and status in _TRANSIENT_STATUS_CODES


def _transient_retry_delay(exc: Exception, hint: float, attempt: int) -> float:
    """Seconds before the next attempt: a request-rate refusal waits its full
    Retry-After (at most 60 s, never jittered below it, RS-1 fix Q5); any
    other transient error backs off exponentially, jittered, capped at 20 s."""
    rpm_wait = rpm_wait_seconds(exc)
    if rpm_wait is not None:
        return rpm_wait
    delay = min(max(1.0, hint or 0.0) * (2.0 ** (attempt - 1)), 20.0)
    return delay * random.uniform(0.8, 1.2)


def _is_transient_provider_error(exc: Exception) -> float | None:
    """Retry delay hint (seconds) for a transient provider error, else None.

    Returns provider-supplied hints (Retry-After / retry_delay) when present.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(marker in text for marker in _HARD_ERROR_MARKERS):
        return None
    hint = _retry_delay_hint(exc)
    if hint is not None:
        return hint
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return 0.0
    import httpx

    if isinstance(exc, httpx.TransportError):
        return 0.0
    if _status_is_transient(exc):
        return 1.0
    if any(marker in text for marker in _TRANSIENT_ERROR_MARKERS):
        return 0.5
    return None


# The approval prompt answered by a person on THIS thread (the permission
# check calls the console's approval callback synchronously), so the gate can
# say "rejected by you" instead of "blocked by policy" (stabilization sweep
# 2026-10-21, chat#8).
_approval_outcome = threading.local()


def note_user_rejection(tool_name: str) -> None:
    """Record that the person at the approval prompt rejected ``tool_name``."""
    _approval_outcome.rejected = tool_name


def _take_user_rejection(tool_name: str) -> bool:
    """True (once) when the last denial on this thread was a person's "no"."""
    rejected = getattr(_approval_outcome, "rejected", None)
    _approval_outcome.rejected = None
    return rejected == tool_name


def _tool_failure_text(kind: str, *, name: str, detail: str) -> str:
    """Model-facing tool failure text (pattern: goose DECLINED_RESPONSE /
    opencode InvalidArgumentsError — the message is written FOR the model so it
    can decide the next step, not for the log).

    Every call-site failure variant feeds back as a ``role: tool`` result
    carrying a ``[tool_contract]`` instruction so the model does not loop the
    same failed call.
    """
    instructions = {
        "unknown_tool": (
            "No such tool is available in this session. Do NOT retry this name. "
            "Use only tools from the provided tool definitions, or answer in "
            "plain text."
        ),
        "invalid_arguments": (
            "Arguments failed to parse. Fix them against the tool schema and "
            "retry ONCE with valid JSON; if the same call fails again, switch "
            "approach instead of retrying."
        ),
        "user_rejected": (
            "The user rejected this call at the approval prompt. Do NOT retry "
            "the same call. Explain what you intended, then ask the user how to "
            "proceed or continue with the information already gathered."
        ),
        "permission_denied": (
            "The session policy denied this action. Do NOT retry the same call. "
            "Explain what you intended, then either ask the user for an "
            "alternative or continue with the information already gathered."
        ),
        "blocked_hook": (
            "A session hook blocked this action. Do NOT retry the same call. "
            "Work around it with different arguments/tools, or ask the user."
        ),
        "execution_error": (
            "Tool execution failed. Do not loop on the exact same call — vary "
            "the arguments once if the error suggests a fix, otherwise "
            "continue from the information you already have."
        ),
        "no_result": (
            "Do not blindly retry this exact call — explain the interruption and switch approach."
        ),
        "refused_this_turn": (
            "The current mode does not allow this tool. Do NOT retry it this turn; "
            "use a tool the mode allows, or answer in plain text."
        ),
    }
    instruction = instructions.get(kind, instructions["execution_error"])
    return f"{detail}\n[tool_contract:{kind} for '{name}' case] {instruction}"


def _claimed_tool_ids(msg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """tool_call ids claimed by one assistant message."""
    out: dict[str, dict[str, Any]] = {}
    if msg.get("role") != "assistant":
        return out
    for tc in msg.get("tool_calls") or []:
        call_id = (tc or {}).get("id")
        if isinstance(call_id, str) and call_id:
            out.setdefault(call_id, tc)
    return out


def _answered_tool_id(msg: dict[str, Any]) -> str | None:
    """tool_call_id answered by one tool message, or None."""
    if msg.get("role") != "tool":
        return None
    call_id = str(msg.get("tool_call_id") or "")
    return call_id or None


def _collect_tool_pairing_index(
    messages: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Index claimed tool_call ids + answered ids over the history."""
    claimed: dict[str, dict[str, Any]] = {}
    answered: set[str] = set()
    for msg in messages:
        claimed.update(_claimed_tool_ids(msg))
        call_id = _answered_tool_id(msg)
        if call_id:
            answered.add(call_id)
    return claimed, answered


def _synthetic_no_result(tc: dict[str, Any]) -> dict[str, Any] | None:
    """Synthetic error result for a call that never completed."""
    call_id = tc.get("id")
    if not isinstance(call_id, str) or not call_id:
        return None
    name = str(((tc.get("function") or {}).get("name")) or "unknown")
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": (
            f"error: tool '{name}' produced no result (turn interrupted or "
            "history trimmed). Do not blindly retry this exact call — explain "
            "the interruption and switch approach."
        ),
    }


def _close_assistant_call_pends(
    repaired: list[dict[str, Any]],
    msg: dict[str, Any],
    *,
    answered: set[str],
    orphaned: set[str],
    emitted: set[str],
) -> int:
    """Append synthetic results for one assistant message's unanswered calls."""
    count = 0
    for tc in msg.get("tool_calls") or []:
        call_id = tc.get("id")
        if not isinstance(call_id, str) or not call_id:
            continue
        emitted.add(call_id)
        if call_id in answered and call_id not in orphaned:
            continue
        synthetic = _synthetic_no_result(tc)
        if synthetic is None:
            continue
        repaired.append(synthetic)
        count += 1
    return count


def _keep_orphaned_tool_result(
    repaired: list[dict[str, Any]],
    msg: dict[str, Any],
    *,
    emitted: set[str],
    claimed: dict[str, dict[str, Any]],
    orphaned: set[str],
) -> int:
    """Keep claimed results that follow their call; drop the rest."""
    call_id = str(msg.get("tool_call_id") or "")
    if call_id and call_id in emitted:
        repaired.append(msg)
        return 0
    # A claimed-but-not-yet-emitted id means the result precedes its call;
    # after the drop the call looks unanswered (needs its own synthetic).
    if call_id in claimed:
        orphaned.add(call_id)
    return 1


_TOOL_RESULT_TAIL_KEEP = 1024
_TOOL_RESULT_HEAD_KEEP = 1024


def _last_round_tool_indices(messages: list[dict[str, Any]], tool_indices: list[int]) -> set[int]:
    """Tool messages answering the LAST assistant message that made tool calls."""
    last_call = max(
        (i for i, m in enumerate(messages) if m.get("role") == "assistant" and m.get("tool_calls")),
        default=-1,
    )
    return {i for i in tool_indices if i > last_call} or {tool_indices[-1]}


def _within_budget(
    messages: list[dict[str, Any]], tool_indices: list[int], budget_chars: int | None
) -> bool:
    if budget_chars is None:
        return False
    return sum(len(messages[i].get("content") or "") for i in tool_indices) <= budget_chars


def _trimmed_tool_message(m: dict[str, Any]) -> dict[str, Any]:
    content = m.get("content") or ""
    if len(content) <= _TOOL_RESULT_HEAD_KEEP + _TOOL_RESULT_TAIL_KEEP:
        return m
    head = content[:_TOOL_RESULT_HEAD_KEEP]
    tail = content[-_TOOL_RESULT_TAIL_KEEP:]
    omitted = len(content) - len(head) - len(tail)
    call_id = str(m.get("tool_call_id") or "")
    fetch = (
        f' — call tool_result_fetch(tool_call_id="{call_id}") to read it in full' if call_id else ""
    )
    stamped = f"{head}\n[… ~{omitted} chars elided to keep the context small{fetch} …]\n{tail}"
    return {**m, "content": stamped}


def _bound_tool_result_history(
    messages: list[dict[str, Any]], budget_chars: int | None = None
) -> list[dict[str, Any]]:
    """Trim oversized ``role: tool`` result bodies stored in turn history.

    W4 (2026-10-05, plan row H5): the WHOLE last round of tool results stays in
    full (previously only the single last message, so k-1 results of a
    parallel round were cut before the model read them). Older results are
    trimmed to head + tail only once the tool history exceeds ``budget_chars``
    (``None`` = always trim older results, the pre-W4 behaviour). A trimmed
    result names ``tool_result_fetch`` so the model can re-read it.
    """
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_indices) < 2 or _within_budget(messages, tool_indices, budget_chars):
        return messages
    protected = _last_round_tool_indices(messages, tool_indices)
    return [
        _trimmed_tool_message(m) if m.get("role") == "tool" and i not in protected else m
        for i, m in enumerate(messages)
    ]


# Tool-call ARGUMENTS replayed in history (2026-10-06, TB 4.0 photonic): the model
# writes whole files through shell heredocs and file_write, and every later request
# replayed them verbatim — 390k of a 510k-char (134k-token) prompt, one call alone
# 150k chars. Rounds before the latest keep each long argument string's head
# and tail; the written file is on disk. R4_COMPANION_ARG_MAX_CHARS (0 = keep all).
_ARG_KEEP_ROUNDS = 1
_ARG_MAX_CHARS = 2_000


def _clip_arg_value(value: Any, limit: int) -> Any:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    head, tail = value[: limit * 3 // 5], value[-(limit // 5) :]
    omitted = len(value) - len(head) - len(tail)
    marker = f"[… {omitted:,} chars of this earlier call omitted; its effect is on disk …]"
    return f"{head}\n{marker}\n{tail}"


def _clip_call_arguments(call: dict[str, Any], limit: int) -> dict[str, Any]:
    """A tool call whose long JSON string arguments are clipped (still valid JSON)."""
    fn = call.get("function") or {}
    raw = fn.get("arguments")
    if not isinstance(raw, str) or len(raw) <= limit:
        return call
    try:
        args = json.loads(raw)
    except ValueError:
        return call
    if not isinstance(args, dict):
        return call
    clipped = {k: _clip_arg_value(v, limit) for k, v in args.items()}
    return {**call, "function": {**fn, "arguments": json.dumps(clipped, ensure_ascii=False)}}


def _bound_tool_call_args(
    messages: list[dict[str, Any]], keep_rounds: int = _ARG_KEEP_ROUNDS, limit: int = _ARG_MAX_CHARS
) -> list[dict[str, Any]]:
    """Clip long tool-call arguments of assistant rounds older than ``keep_rounds``."""
    rounds = [
        i for i, m in enumerate(messages) if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    old = set(rounds[: max(0, len(rounds) - keep_rounds)])
    if limit <= 0 or not old:
        return messages
    return [
        {**m, "tool_calls": [_clip_call_arguments(c, limit) for c in m["tool_calls"]]}
        if i in old
        else m
        for i, m in enumerate(messages)
    ]


# Old rounds (2026-10-07, TB 4.0 layout): the per-result trim keeps a 2k head and
# tail for EVERY earlier round, so a 229-round task carried 362k chars of results
# and 176k of arguments — prompts of ~150k tokens on every request, which slows the
# trial and every other stream sharing the endpoint. Rounds older than the latest
# R4_COMPANION_HISTORY_FULL_ROUNDS (40; 0 = off) keep R4_COMPANION_OLD_ROUND_CHARS
# (400) of each result and argument string; the boundary moves in blocks of 10
# rounds so the server's prefix cache stays valid between them.
_HISTORY_FULL_ROUNDS = 40
_OLD_ROUND_CHARS = 400
_OLD_ROUND_BLOCK = 10


def _old_round_ids(messages: list[dict[str, Any]], keep: int) -> tuple[set[int], set[str]]:
    """Indices of assistant rounds older than the latest ``keep`` (moved in
    blocks) and the tool-call ids they made."""
    rounds = [
        i for i, m in enumerate(messages) if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    cut = (max(0, len(rounds) - keep) // _OLD_ROUND_BLOCK) * _OLD_ROUND_BLOCK
    old = set(rounds[:cut])
    ids = {str(c.get("id")) for i in old for c in messages[i]["tool_calls"]}
    return old, ids


def _shrunk_result(m: dict[str, Any], limit: int) -> dict[str, Any]:
    content = str(m.get("content") or "")
    if len(content) <= limit:
        return m
    call_id = str(m.get("tool_call_id") or "")
    note = f"[… {len(content) - limit:,} chars of an older result omitted"
    if call_id:
        note += f' — tool_result_fetch(tool_call_id="{call_id}") re-reads it'
    return {**m, "content": f"{content[:limit]}\n{note} …]"}


def _fast_role_model(client: Any) -> str | None:
    """``svc-fast@<platform source>`` while the platform serves the role."""
    from r4.service_roles import resolve_role_model

    try:
        return resolve_role_model(getattr(client, "config", None), "fast")
    except Exception:  # noqa: BLE001 - a role lookup never blocks a sub-agent
        return None


def _compact_old_rounds(
    messages: list[dict[str, Any]], keep: int = _HISTORY_FULL_ROUNDS, limit: int = _OLD_ROUND_CHARS
) -> list[dict[str, Any]]:
    """Shrink results and argument strings of rounds older than the latest ``keep``."""
    if keep <= 0:
        return messages
    old, ids = _old_round_ids(messages, keep)
    if not old:
        return messages
    out: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if i in old:
            m = {**m, "tool_calls": [_clip_call_arguments(c, limit) for c in m["tool_calls"]]}
        elif m.get("role") == "tool" and str(m.get("tool_call_id")) in ids:
            m = _shrunk_result(m, limit)
        out.append(m)
    return out


_REASONING_PLACEHOLDER = "(earlier reasoning omitted to keep the context small)"
TOOL_RESULT_FETCH = "tool_result_fetch"


def _reasoning_keep() -> int:
    """Assistant rounds whose reasoning is replayed in full (W4; -1 = all)."""
    raw = os.environ.get("R4_COMPANION_REASONING_KEEP", "").strip()
    try:
        return int(raw) if raw else 2
    except ValueError:
        return 2


# Kept rounds replay at most this much of their reasoning (the end, nearest the
# tool call): a 116k-token reasoning replayed in full grew the next TB 4.0 prompt
# from 15k to 131k tokens (react-lead-form, 2026-10-05). 0 = no cap.
_REASONING_REPLAY_MAX_CHARS = 24_000


def _clip_reasoning(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return f"[… {len(text) - limit:,} chars of earlier reasoning omitted …]\n{text[-limit:]}"


def _bound_reasoning_history(
    messages: list[dict[str, Any]], keep: int, max_chars: int = 0
) -> list[dict[str, Any]]:
    """Replay reasoning for the latest ``keep`` assistant rounds only (plan row H4),
    each clipped to its last ``max_chars`` characters (0 = whole; ``keep`` -1 = all).

    Older rounds keep their content and tool calls; their reasoning becomes a
    short placeholder (the field stays present for providers that expect it on
    tool-call messages). TB 4.0 measured prompts of 120k-630k tokens with full
    replay.
    """
    carrying = [
        i
        for i, m in enumerate(messages)
        if m.get("role") == "assistant" and m.get("reasoning_content")
    ]
    cut = max(0, len(carrying) - keep) if keep >= 0 else 0
    old, kept = set(carrying[:cut]), set(carrying[cut:])
    return [_replayed(m, i in old, i in kept, max_chars) for i, m in enumerate(messages)]


def _replayed(
    message: dict[str, Any], is_old: bool, is_kept: bool, max_chars: int
) -> dict[str, Any]:
    if is_old:
        return {**message, "reasoning_content": _REASONING_PLACEHOLDER}
    if not is_kept:
        return message
    clipped = _clip_reasoning(str(message["reasoning_content"]), max_chars)
    return (
        message
        if clipped == message["reasoning_content"]
        else {**message, "reasoning_content": clipped}
    )


def _repair_tool_pairing(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Close unpaired tool-call/result chains (pattern: opencode failUnsettledTools,
    OpenHands _emit_orphaned_action_errors, goose fix_conversation).

    Providers reject message histories containing either:

    1. an assistant message carrying ``tool_calls`` whose ``tool_call_id`` never
       received a ``role: tool`` result (turn cancelled mid-round, history
       trimmed between pair),
    2. a ``role: tool`` result no assistant message claims (trimmed history,
       or a result that precedes its call).

    Repairs (order-preserving): dangling assistant calls get a synthetic error
    result inserted immediately after the assistant message; orphan/out-of-order
    tool results are dropped. Returns a NEW list plus the repair count.
    """
    claimed, answered = _collect_tool_pairing_index(messages)
    repaired: list[dict[str, Any]] = []
    emitted: set[str] = set()
    orphaned: set[str] = set()
    repairs = 0
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            repaired.append(msg)
            repairs += _close_assistant_call_pends(
                repaired, msg, answered=answered, orphaned=orphaned, emitted=emitted
            )
        elif role == "tool":
            repairs += _keep_orphaned_tool_result(
                repaired, msg, emitted=emitted, claimed=claimed, orphaned=orphaned
            )
        else:
            repaired.append(msg)
    return repaired, repairs


# Mid-turn system nudges (H8, 2026-10-24): every system message used to be merged
# into system message 0 when the round was fitted, so a checkpoint nudge ("write
# 2-3 sentences to the user …") stayed in the system prompt for the rest of the
# task. With the switch on, only the leading system block is merged; a later
# system message stays in place as a user message with this prefix (GLM chat
# templates expect the system message first). A note next to another user
# message is merged into it, so templates that require strict user/assistant
# alternation never see two user messages in a row. On by default only in
# unattended runs (R4_AUTONOMOUS=1); R4_COMPANION_NUDGES_AS_USER=0 turns it off
# there and =1 opts an interactive session in (review 2026-10-24: the served
# template's handling is checked in M1 before it can become a product default).
_HARNESS_NOTE_PREFIX = "[harness note] "


def _nudges_as_user() -> bool:
    return env_switch("R4_COMPANION_NUDGES_AS_USER", _autonomous())


def _harness_note(message: dict[str, Any]) -> dict[str, Any]:
    return {"role": "user", "content": _HARNESS_NOTE_PREFIX + str(message.get("content") or "")}


def _is_harness_note(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return isinstance(content, str) and content.startswith(_HARNESS_NOTE_PREFIX)


def _mergeable_users(previous: dict[str, Any], message: dict[str, Any]) -> bool:
    """Two adjacent plain-text user messages, at least one of them a harness note."""
    pair = (previous, message)
    if any(m.get("role") != "user" or not isinstance(m.get("content"), str) for m in pair):
        return False
    return _is_harness_note(previous) or _is_harness_note(message)


def _append_merging_notes(out: list[dict[str, Any]], message: dict[str, Any]) -> None:
    if out and _mergeable_users(out[-1], message):
        out[-1] = {**out[-1], "content": f"{out[-1]['content']}\n\n{message['content']}"}
        return
    out.append(message)


def _system_nudges_as_user(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The leading system block as it is; each later system message as a user note."""
    if not _nudges_as_user():
        return messages
    out: list[dict[str, Any]] = []
    leading = True
    for message in messages:
        is_system = message.get("role") == "system"
        leading = leading and is_system
        _append_merging_notes(out, _harness_note(message) if is_system and not leading else message)
    return out


def _split_system_prefix(
    messages: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Partition system content parts from the rest (_fit_agent_messages)."""
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "system":
            content = message.get("content")
            if content:
                system_parts.append(str(content))
        else:
            rest.append(message)
    return system_parts, rest


def _pop_trailing_user(
    rest: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Split a trailing user message (if any) from the history."""
    if rest and rest[-1].get("role") == "user":
        return str(rest[-1].get("content") or ""), rest[:-1]
    return "", rest


def _rebuild_fitted_messages(
    system_parts: list[str],
    history: list[dict[str, Any]],
    user_message: str,
) -> list[dict[str, Any]]:
    """Rebuild the message list after the budget filter (_fit_agent_messages)."""
    rebuild: list[dict[str, Any]] = []
    if system_parts:
        rebuild.append({"role": "system", "content": "\n\n".join(system_parts)})
    rebuild.extend(history)
    if user_message:
        rebuild.append({"role": "user", "content": user_message})
    return rebuild


# The terminal profile (H2/H11, TB 4.0 plan 2026-10-24) is an ablation arm, set
# only by the bench driver (R4_AGENT_PROFILE=terminal). Another lane owns
# ``agent_profile``; it is read defensively so either can land first.
_PROFILE_MODULE = f"{__package__}.agent_profile"
_PROFILE_TOOL_HISTORY_TOKENS = 24_000
_DEFAULT_TOOL_HISTORY_TOKENS = 64_000
_PROFILE_ARG_KEEP_ROUNDS = 3
_PROFILE_ARG_MAX_CHARS = 600


def _profile_call(name: str) -> Any:
    """``agent_profile.<name>()``, or None when the module or the function is missing."""
    try:
        module = importlib.import_module(_PROFILE_MODULE)
        function = getattr(module, name, None)
        return function() if callable(function) else None
    except Exception:  # noqa: BLE001 - a broken profile never breaks a turn
        logger.debug("agent_profile.%s unavailable", name, exc_info=True)
        return None


def _terminal_profile() -> bool:
    """True only under R4_AGENT_PROFILE=terminal (never for the default or R4_AUTONOMOUS)."""
    active = _profile_call("active")
    if active is None:
        return os.environ.get("R4_AGENT_PROFILE", "").strip().lower() == "terminal"
    return bool(active)


def _base_system_prompt(terminal: bool) -> str:
    """The profile's core prompt under the terminal profile, else the companion prompt."""
    core = _profile_call("core_prompt") if terminal else None
    return core if isinstance(core, str) and core.strip() else COMPANION_SYSTEM_PROMPT


def _arg_bounds() -> tuple[int, int]:
    """(rounds whose tool-call arguments stay whole, clip size for older ones)."""
    if _terminal_profile():
        return _PROFILE_ARG_KEEP_ROUNDS, _PROFILE_ARG_MAX_CHARS
    return _ARG_KEEP_ROUNDS, _ARG_MAX_CHARS


def _profile_allowlisted(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Tools the terminal profile offers, plus the ADR-037 bucket of the rest."""
    allow = _profile_call("tool_allowlist") if _terminal_profile() else None
    if not allow:
        return tools, None
    kept = [tool for tool in tools if tool.get("name") in allow]
    removed = [str(tool.get("name") or "?") for tool in tools if tool.get("name") not in allow]
    return kept, ({"count": len(removed), "sample": removed[:5]} if removed else None)


def _duplicate_registration_warnings(skills: "SkillRegistry | None") -> tuple[str, ...]:
    """Measured duplicate-registration warnings for the selection record.

    MCP-server skills carry their server's tool set (`mcp:`-named, source
    "mcp"); the detector compares server tools sets pairwise (after
    stripping the default-/hatch- prefixes) and emits one human-readable
    warning per colliding pair. Failures degrade to an empty tuple.
    """
    if skills is None:
        return ()
    from .tool_selection import detect_duplicate_registrations

    try:
        registrations: dict[str, list[str]] = {}
        for skill in skills.list_all():
            if getattr(skill, "source", "") != "mcp":
                continue
            name = str(getattr(skill, "name", ""))
            if name.startswith("mcp:"):
                name = name[4:]
            registrations[name] = sorted(str(n) for n in (getattr(skill, "tool_names", None) or []))
        return tuple(detect_duplicate_registrations(registrations))
    except Exception:  # accounting never crashes a turn
        return ()


# ---------------------------------------------------------------------------
# ResearchCompanion
# ---------------------------------------------------------------------------


class ResearchCompanion:
    """Main companion agent — agentic loop with tool use.

    The companion runs a loop:
    1. Assemble context (system prompt + harness guides + history)
    2. LLM call (with tool schemas)
    3. If tool call → validate via sensors → execute → append result → goto 2
    4. If text response → emit events → done

    All state is managed externally by ``CompanionSession``; the companion
    itself is stateless between ``send()`` calls.
    """

    # Published limits ``sync_session_context_budget`` hands over (W4); a
    # sub-agent on the same model inherits them (``_new_subagent``).
    model_context_window: int | None = None
    model_output_limit: int | None = None
    _served_window_memo: tuple[str, int | None] | None = None

    def __init__(
        self,
        client: "Client",
        *,
        context_manager: "ContextManager | None" = None,
        skill_registry: "SkillRegistry | None" = None,
        permission_manager: "PermissionManager | None" = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tool_rounds: int | None = None,
        usage_callback: Callable[[str, dict[str, Any]], None] | None = None,
        pre_llm_call: Callable[[str], None] | None = None,
        call_type: str = "turn",
    ):
        self.client = client
        self.model = model
        # H14 (2026-10-24): R4_COMPANION_TEMPERATURE / R4_COMPANION_TOP_P set the
        # sampling (read once); an explicit ``temperature`` argument still wins.
        self.temperature = _initial_temperature(temperature)
        self.top_p = _env_float("R4_COMPANION_TOP_P", upper=1.0)
        configured_rounds = (
            max_tool_rounds
            if max_tool_rounds is not None
            else os.environ.get("R4_COMPANION_MAX_TOOL_ROUNDS")
        )
        self.max_tool_rounds = _coerce_positive_int(
            configured_rounds,
            default=DEFAULT_SOFT_BLOCK_ROUNDS,
        )
        self.max_total_tool_rounds = _coerce_positive_int(
            os.environ.get("R4_COMPANION_TOTAL_TOOL_ROUNDS"),
            default=DEFAULT_TOTAL_TOOL_ROUNDS,
        )
        self._usage_callback = usage_callback
        self._pre_llm_call = pre_llm_call
        self._call_type = call_type
        self._tool_calling_disabled = self._model_family_disabled()
        # Tools the session policy denied this session — pruned from the
        # advertised schemas on later turns (opencode deny removes the tool
        # from the advertised set entirely).
        self._session_denied_tools: set[str] = set()
        # T5 topic-24 plan gate: the session rewires both per turn
        # (session._wire_plan_gate); off until it does.
        self._plan_gate_required = False
        self._plan_gate_approved = False
        if self._tool_calling_disabled:
            _config = getattr(self.client, "_config", None)
            logger.info(
                "Model %s is in the known-unreliable tool family — companion "
                "tools start disabled for this session (no mid-run shutdown).",
                (self.model or getattr(_config, "model", "")),
            )

        # Lazy-init collaborators (set by session or externally)
        self._context: "ContextManager | None" = context_manager
        self._skills: "SkillRegistry | None" = skill_registry
        self._permissions: "PermissionManager | None" = permission_manager
        # ADR-039/WP-19: every companion session ALWAYS gets a registry —
        # the taint fence must never silently disable itself when unwired.
        self._taint_registry: "TaintRegistry" = TaintRegistry()
        if self._permissions is not None:
            self._permissions.set_taint_registry(self._taint_registry)

        # Output-budget ladder and empty-reply nudges: counted per agentic turn.
        # G19a: one free output-token escalation per turn before nudges burn.
        self._output_token_override: int | None = None
        self._output_escalated = False
        self._model_fell_back = False
        self._length_budget_nudge_sent = False
        self._length_budget_nudges = 0
        self._empty_reply_nudges = 0
        self._intent_nudges = 0
        self._guard_nudges = 0
        self._completion_reviewed = False
        self._turn_started = time.monotonic()
        # Backend refused a forced close (no continue_final_message): the
        # thinking budget is off for the rest of the session.
        self._think_close_unsupported = False

        # Event callbacks
        self._listeners: list[Callable[[CompanionEvent], None]] = []
        # Guards listener dispatch — parallel tool workers also emit events
        # and TUI listeners are not thread-safe on their own.
        self._emit_lock = threading.Lock()
        # Started tool calls without their end event yet, id -> name (cycle
        # 3, L1: a failed call's tool_call_start was never closed).
        self._open_tool_calls: dict[str, str] = {}
        self._turn_cancel: "TurnCancellation | None" = None
        # The reply text this turn streamed to the screen, one entry per
        # round (see :meth:`partial_reply`).
        self._turn_rounds: list[str] = []
        # Bounded FIFO of full tool results for on-demand fetch (task 2).
        self._tool_result_journal = OrderedDict()
        # G9: background subagents; completions drain into the parent loop at
        # round boundaries as <subagent-notification> blocks.
        from .async_agents import AsyncAgentPool

        self._async_pool = AsyncAgentPool()
        # G15: the current turn's wire set, stashed per send for the
        # evaluator tool subset (`build_contract_review`).
        self._last_wire_tools: list[dict[str, Any]] | None = None

    # -- public API ---------------------------------------------------------

    def _with_tool_result_fetch(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Offer ``tool_result_fetch`` beside any tool set (W4, plan row H5).

        Trimmed history results name it; it re-reads a full result from this
        agent's bounded journal. Read-only, so it is concurrency-safe.
        """
        if not tools or any(t.get("name") == TOOL_RESULT_FETCH for t in tools):
            return tools
        return [*tools, self._tool_result_fetch_tool()]

    def _tool_result_fetch_tool(self) -> dict[str, Any]:
        def execute(tool_call_id: str = "", offset: int = 0, limit: int = 32_000) -> str:
            record = self.tool_result_record(str(tool_call_id))
            if record is None:
                return (
                    f"Error: no stored result for tool_call_id {tool_call_id!r} "
                    "(evicted or unknown)."
                )
            name, text = record
            start = max(0, int(offset))
            chunk = text[start : start + max(1, int(limit))]
            more = len(text) - start - len(chunk)
            note = (
                f"\n[{more} more chars — call again with offset={start + len(chunk)}]"
                if more > 0
                else ""
            )
            return (
                f"[{name} result, chars {start}-{start + len(chunk)} of {len(text)}]\n{chunk}{note}"
            )

        return {
            "name": TOOL_RESULT_FETCH,
            "description": (
                "Re-read the full text of an earlier tool result that was shortened in the "
                "conversation history. Pass the tool_call_id named in the elision note; use "
                "offset/limit to page through long results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_call_id": {"type": "string"},
                    "offset": {"type": "integer", "description": "start char, default 0"},
                    "limit": {"type": "integer", "description": "max chars, default 32000"},
                },
                "required": ["tool_call_id"],
            },
            "execute": execute,
            "concurrency_safe": True,
        }

    def tool_result_record(self, tool_call_id: str) -> tuple[str, str] | None:
        """(name, full_text) for one executed call — None when evicted/absent."""
        return self._tool_result_journal.get(tool_call_id)

    def _record_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        """Append to the bounded FIFO journal (insert ordered, capped)."""
        if not tool_call_id:
            return
        _journal_put(self._tool_result_journal, tool_call_id, name, content)

    def on_event(self, callback: Callable[[CompanionEvent], None]) -> None:
        """Register an event listener."""
        self._listeners.append(callback)

    def off_event(self, callback: Callable[[CompanionEvent], None]) -> None:
        """Remove a previously registered event listener."""
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def set_turn_cancel(self, token: Any | None) -> None:
        """Attach cooperative cancellation for the active turn."""
        self._turn_cancel = token

    def _check_turn_cancel(self) -> None:
        if self._turn_cancel is not None:
            self._turn_cancel.check()

    def reset_tool_state(self) -> None:
        """Re-enable tool calling for the session.

        ``_tool_calling_disabled`` is sticky once a model rejects tool mode or
        returns a bogus artifact; ``/clear`` and ``/new`` call this so a fresh
        conversation can use tools again.
        """
        self._tool_calling_disabled = False

    def send(
        self,
        user_message: str,
        *,
        history: list[dict[str, Any]] | None = None,
        extra_system: str = "",
        tools: list[dict[str, Any]] | None = None,
        offer_only_names: frozenset[str] | None = None,
        stream: bool = False,
        intent_message: str | None = None,
        reminder: str = "",
        tool_refusals: Mapping[str, str] | None = None,
    ) -> str:
        """Run one full turn of the agentic loop.

        Args:
            user_message: The researcher's message (with the retrieval
                router on, the context pack with the question last).
            history: Prior messages ``[{"role": ..., "content": ...}, ...]``.
            extra_system: Additional system prompt fragment (from harness).
            tools: Tool definitions available this turn.
            stream: When ``True``, stream response chunks from the LLM,
                emitting ``RESPONSE_START`` / ``RESPONSE_CHUNK`` / ``RESPONSE_DONE``
                events as the stream progresses. The full concatenated text is
                returned just like the non-streaming path.
            intent_message: The message before the harness augmented it
                (ADR-093 D-8). The intent heuristics (family tool selection,
                the "user asked for structured output" safeguard) read it,
                never the passages; the model still reads ``user_message``.
            reminder: Per-turn volatile context (wave 80): it rides the
                ``<turn_context>`` block of this turn's user message, never
                the system message, so the request prefix stays cacheable.
            tool_refusals: ``{tool name: reason}`` — tools that stay in the
                tools array but whose calls are refused this turn.

        Returns:
            The assistant's final text response.
        """
        self._turn_rounds = []
        self._emit(CompanionEventType.THINKING, "Processing your message…")

        intent = intent_message or user_message  # r4-harness-8
        # Wave 80: the tools array is the session's stable set; plan mode,
        # write_requires_plan and skill limits refuse calls at execution
        # instead of reshaping the array (which broke vLLM prefix caching).
        wire_tools, extra_filtered_out, utility_offered = self._wire_tools(tools or [], intent)
        wire_tools, permitted = self._apply_turn_refusals(
            wire_tools, offer_only_names, tool_refusals
        )
        # The harness's own meta tool rides beside the selected set (W4); it is
        # not part of the ADR-037 selection record, but it runs, so the
        # [Tool limit] note names it (review (80)).
        wire_tools = self._with_tool_result_fetch(wire_tools)
        wire_tools = self._with_async_agent_tools(wire_tools)
        self._last_wire_tools = wire_tools
        note = callable_tools_note(
            [str(t.get("name", "")) for t in wire_tools], self._turn_refusals
        )
        messages = self._build_messages(
            user_message, history, extra_system, join_parts(reminder, note)
        )
        self._selection_ctx = self._build_selection_context(
            tools,
            permitted,
            offer_only_names=offer_only_names,
            extra_filtered_out=extra_filtered_out,
            utility_offered=utility_offered,
        )
        tool_schemas = _tools_to_openai_schema(wire_tools) if wire_tools else None

        if stream and not tool_schemas:
            return self._run_streaming_response(messages, tool_schemas)

        return self._run_agentic_turn(
            messages=messages,
            tool_schemas=tool_schemas,
            user_message=intent,  # heuristics only: ``messages`` carry the pack
            tools=wire_tools,
            stream=stream,
        )

    def _wire_tools(
        self, tools: list[dict[str, Any]], intent: str
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any] | None]:
        """The tools array a request carries: session-level filters only (denied
        tools, the opt-in family strategy, availability, the profile allowlist)."""
        wire = self._prune_denied_tools(tools)
        wire, extra_filtered_out, utility_offered = self._apply_family_strategy(
            wire, user_message=intent
        )
        wire, extra_filtered_out = self._offered_tools(wire, extra_filtered_out, None)
        return wire, extra_filtered_out, utility_offered

    def _apply_turn_refusals(
        self,
        wire_tools: list[dict[str, Any]],
        offer_only_names: frozenset[str] | None,
        tool_refusals: Mapping[str, str] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Set this turn's refusals; returns (tools array, permitted tools).
        The pre-wave-80 layout drops refused tools from the array instead."""
        self._turn_refusals = self._turn_tool_refusals(wire_tools, offer_only_names, tool_refusals)
        permitted = [t for t in wire_tools if t.get("name") not in self._turn_refusals]
        if not stable_prefix_enabled():
            self._turn_refusals = {}
            return permitted, permitted
        return wire_tools, permitted

    def _turn_tool_refusals(
        self,
        wire_tools: list[dict[str, Any]],
        offer_only_names: frozenset[str] | None,
        tool_refusals: Mapping[str, str] | None,
    ) -> dict[str, str]:
        """Tools in the array whose calls this turn refuses, with the reason."""
        names = [str(tool.get("name", "")) for tool in wire_tools]
        return merge_refusals(
            refusals_outside(names, offer_only_names, PLAN_MODE_REFUSAL),
            # T5 topic-24: write_requires_plan opt-in gate — write-family verbs
            # refused as long as the plan lane has no active approved state.
            refusals_outside(names, self._plan_gate_permitted(), PLAN_GATE_REFUSAL),
            tool_refusals,
        )

    def _offered_tools(
        self,
        allowed_tools: list[dict[str, Any]],
        extra_filtered_out: dict[str, dict[str, Any]],
        offer_only_names: frozenset[str] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """The availability gate, then the plan-mode read-only offer."""
        # Availability gate (round-2 finding D): drop tools the session cannot
        # serve (unwired callbacks, retired backing routes) BEFORE the offered
        # set reaches the model — harness preconditions, not selection.
        from .tool_availability import UNAVAILABLE_REASON, filter_unavailable_tools

        allowed_tools, unavailable_out = filter_unavailable_tools(
            allowed_tools, getattr(self, "client", None)
        )
        if unavailable_out:
            extra_filtered_out = {**extra_filtered_out, UNAVAILABLE_REASON: unavailable_out}
        allowed_tools, profile_out = _profile_allowlisted(allowed_tools)
        if profile_out:
            extra_filtered_out = {**extra_filtered_out, "profile": profile_out}
        if offer_only_names is not None:
            # Plan-mode read-only offer (ADR-037 amendment): the model reads
            # to plan; the removal is recorded as mode_readonly, not hidden.
            allowed_tools = [tool for tool in allowed_tools if tool.get("name") in offer_only_names]
        return allowed_tools, extra_filtered_out

    def _apply_family_strategy(
        self,
        tools: list[dict[str, Any]],
        *,
        user_message: str,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any] | None]:
        """ADR-037 family grouping flag (default off). Returns the catalog
        filtered to core + lexically-matched verb families + the utility
        floor (measured) plus the family_off rows; passthrough returns the
        tools untouched with ({}, None)."""
        from .tool_selection import select_with_family

        strategy = (os.environ.get("R4_TOOL_SELECTION_STRATEGY") or "passthrough").strip()
        if strategy != "family":
            return tools, {}, None
        return select_with_family(tools=tools, message_text=user_message)

    def _build_selection_context(
        self,
        tools: list[dict[str, Any]] | None,
        allowed_tools: list[dict[str, Any]],
        *,
        offer_only_names: frozenset[str] | None = None,
        extra_filtered_out: dict[str, dict[str, Any]] | None = None,
        utility_offered: dict[str, Any] | None = None,
    ) -> Any:
        """Per-turn ToolSelectionContext (ADR-037 passthrough-first).

        Strategy resolution order: explicit env flag > default passthrough.
        No strategy is invented here — policy_filter / ranked land in later
        items behind their flags and this builder refuses unknown values.
        """
        from .tool_selection import build_selection_context

        strategy = (os.environ.get("R4_TOOL_SELECTION_STRATEGY") or "passthrough").strip()
        skills_active: tuple[str, ...] = ()
        if self._skills is not None:
            try:
                skills_active = tuple(sorted(skill.name for skill in self._skills.list_active()))
            except Exception:  # never let accounting crash a turn
                skills_active = ()
        self._turn_seq = getattr(self, "_turn_seq", 0) + 1
        skill_body_text = ""
        if self._skills is not None:
            try:
                skill_body_text = self._skills.active_system_prompt()
            except Exception:  # accounting never crashes a turn
                skill_body_text = ""
        return build_selection_context(
            tools=tools or [],
            offered=allowed_tools,
            strategy=strategy,
            skills_active=skills_active,
            warnings=_duplicate_registration_warnings(self._skills),
            skill_body_text=skill_body_text,
            offer_only_names=offer_only_names,
            extra_filtered_out=extra_filtered_out,
            utility_offered=utility_offered,
            user_denied_names=self._user_denied_removed(tools, allowed_tools),
        )

    def _user_denied_removed(
        self,
        tools: list[dict[str, Any]] | None,
        allowed_tools: list[dict[str, Any]],
    ) -> tuple[str, ...]:
        """Names removed by the permission layer only (never the availability
        gate / family strategy / plan-mode prune). Round-3 task-1: the set
        fallback mislabeled every removal as 'user_denied'."""
        if not self._session_denied_tools:
            return ()
        offered_names = {str(t.get("name", "")) for t in allowed_tools}
        removed = {str(t.get("name", "")) for t in (tools or [])} - offered_names
        return tuple(sorted(removed & self._session_denied_tools))

    def _emit_tool_selection(self, step_index: int) -> None:
        """Emit one ToolSelectionRecord for this model call.

        Skipped for force-text (answer) calls — no selection step executes
        there by construction (tools are mechanically absent, not filtered).
        """
        ctx = getattr(self, "_selection_ctx", None)
        if ctx is None:
            return
        from .tool_selection import selection_summary_line

        record = ctx.step_record(step_index=step_index, turn_id=f"t{self._turn_seq}")
        self._emit(
            CompanionEventType.TOOL_SELECTION,
            selection_summary_line(record),
            data=record,
        )

    def _plan_gate_permitted(self) -> frozenset[str] | None:
        """T5 topic-24 plan-gate: the read-only names while write-family verbs
        wait for plan_approved=True; None when the gate is off or satisfied.
        Session-established attrs inject the current values per-turn.
        """
        if not self._plan_gate_required or self._plan_gate_approved:
            return None
        from .permissions import SAFE_TOOLS

        return frozenset(SAFE_TOOLS)

    def _prune_denied_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """opencode deny→hide: a tool the policy denies whatever the arguments
        leaves the advertised set while that deny holds (ADR-104 D-2); it
        comes back when the deny is gone (a rule removed, DENY left)."""
        if not self._session_denied_tools:
            return tools
        hidden = {name for name in self._session_denied_tools if self._denies_whole_tool(name)}
        self._session_denied_tools = hidden
        if not hidden:
            return tools
        return [tool for tool in tools if tool.get("name") not in hidden]

    def _run_streaming_response(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]] | None,
    ) -> str:
        """Stream response chunks from the LLM, emitting events as they arrive.

        Returns the full concatenated text.
        """
        self._emit(CompanionEventType.RESPONSE_START, "")
        collected_content = ""
        for chunk_text in self._stream_chat(messages, tool_schemas):
            collected_content += chunk_text
            self._emit(CompanionEventType.RESPONSE_CHUNK, chunk_text)
        if self._usage_callback or self._context is not None:
            self._record_streaming_usage(messages, collected_content, tools=tool_schemas)
        self._emit(CompanionEventType.RESPONSE_DONE, collected_content)
        return collected_content

    def _agentic_round_response(
        self,
        messages: list[dict[str, Any]],
        active_tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Streamed-first agentic round (blocking fallback when the stream is empty).

        A round stopped at the thinking budget is closed (``_close_deliberation``)
        and returned as not-streamed, so its answer text is emitted afresh; one
        stopped at the tool-arguments budget loses its unfinished calls
        (``_planning_cut``) and the ladder asks for a runnable step.
        """
        streamed: dict[str, Any] | None = None
        limits = self._round_limits()
        if stream and active_tools:
            streamed = self._stream_agentic_round(messages, active_tools, limits=limits)
        if streamed is not None:
            response = streamed
        else:
            response = self._chat(messages, active_tools, limits=limits)
        if _response_choice(response).get("finish_reason") == THINK_BUDGET_FINISH:
            streamed, response = None, self._close_deliberation(messages, active_tools, response)
        if _response_choice(response).get("finish_reason") == ARGS_BUDGET_FINISH:
            response = _planning_cut(response)
        return streamed, response

    def _round_limits(self) -> RoundLimits:
        """This round's stream limits in chars (0 = unbounded)."""
        think = 0 if self._think_close_unsupported else _think_budget_chars()
        args = _env_chars("R4_COMPANION_TOOL_ARGS_BUDGET", _TOOL_ARGS_BUDGET_CHARS)
        return RoundLimits(
            think_chars=think,
            args_chars=args,
            text_chars=_text_budget_chars(),
            think_seconds=0 if self._think_close_unsupported else _think_budget_seconds(),
        )

    def _close_deliberation(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """Close a deliberation stopped at the thinking budget so the model acts.

        Re-sends the round with an assistant prefill holding the reasoning so
        far (``continue_final_message``: the chat template closes the thinking
        block and the model continues with its answer or tool call). A backend
        that refuses the prefill, or deliberates again, turns the thinking
        budget off for the session and the round becomes an ordinary
        output-limit cut, so the continuation ladder carries on.
        """
        reasoning = str(_round_reasoning(_response_choice(response).get("message")) or "")
        reasoning = reasoning.rstrip()
        self._emit(
            CompanionEventType.THINKING,
            f"Thinking budget reached (~{len(reasoning) // _THINK_BUDGET_CHARS_PER_TOKEN:,} "
            "tokens of reasoning, no action yet) — closing the deliberation so the "
            "model acts on its plan.",
        )
        filtered = self._clean_chat_messages(messages)
        payload = self._chat_payload(filtered, tools)
        prefill = {
            "role": "assistant",
            "content": "",
            "reasoning_content": reasoning + _THINK_CLOSE_LINE,
        }
        payload["messages"] = [*payload["messages"], prefill]
        payload.update(continue_final_message=True, add_generation_prompt=False)
        try:
            raw = self._request_with_transient_retry(payload, limits=self._round_limits())
        except (TurnCancelledError, QuotaRefusedError):
            raise  # a quota refusal ends the turn: the ladder would only re-send
        except Exception as exc:  # noqa: BLE001 - any refusal falls back to the ladder
            return self._think_close_refused(response, exc)
        self._record_chat_usage(raw, filtered, tools=payload.get("tools"))
        forced = raw.raw_response or {"choices": [{"message": {"content": raw.content}}]}
        if _response_choice(forced).get("finish_reason") == THINK_BUDGET_FINISH:
            return self._think_close_refused(response, "the model kept deliberating")
        return _merge_closed_reasoning(forced, reasoning)

    def _think_close_refused(self, response: dict[str, Any], why: Any) -> dict[str, Any]:
        """Thinking budget off for the session; the round counts as a cut."""
        self._think_close_unsupported = True
        logger.warning("Forced close of a long deliberation failed (%s); budget off", why)
        self._emit(
            CompanionEventType.THINKING,
            "The model backend did not accept the forced close of a long deliberation; "
            "the thinking budget is off for this session.",
        )
        return _with_finish(response, "length")

    def _record_tool_round(
        self,
        messages: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        recent_tool_steps: list[dict[str, str]],
        *,
        round_message: dict[str, Any] | None = None,
    ) -> None:
        """Execute the tool calls, append their results, and track repeat markers.

        Round history shape is the OpenAI-canonical one (pattern: opencode /
        goose / OpenHands): ONE assistant message carrying the round's thinking
        text and ALL its tool calls, followed by one ``role: tool`` result per
        call. The previous shape (``content: None`` + one assistant message
        per call) threw the model's reasoning breadcrumbs away — long tool
        chains then presented as 'no responses' in the UI and lost useful
        context on every follow-up request.
        """
        content = (round_message or {}).get("content")
        content = _strip_tagged_tool_scaffold(content) if content else None
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": content or None,
            "tool_calls": tool_calls,
        }
        reasoning = _round_reasoning(round_message)
        if reasoning:
            # deepseek-family providers require the reasoning echo back on the
            # assistant message to stay self-consistent in tool chains
            assistant_message["reasoning_content"] = reasoning
        messages.append(assistant_message)

        tool_results = self._execute_tool_calls(tool_calls, tools or [])
        for tc, result in zip(tool_calls, tool_results):
            self._check_turn_cancel()
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            recent_tool_steps.append(
                {
                    "name": str(tc.get("function", {}).get("name", "")),
                    "arguments": _normalize_tool_arguments(
                        str(tc.get("function", {}).get("arguments", "{}"))
                    ),
                    "result": _fingerprint_tool_result(result),
                }
            )
        # In place (H7, 2026-10-24): rebinding the name left the caller's list
        # growing, and only each round's first call reached it.
        del recent_tool_steps[:-_RECENT_STEP_WINDOW]

    def _model_family_disabled(self) -> bool:
        """True when the selected model belongs to a known-unreliable tool family."""
        config = getattr(self.client, "_config", None)
        model = str(self.model or getattr(config, "model", "") or "").lower()
        return any(model.startswith(prefix) for prefix in KNOWN_BAD_TOOL_MODEL_FAMILIES)

    def _run_agentic_turn(
        self,
        *,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]] | None,
        user_message: str,
        tools: list[dict[str, Any]],
        stream: bool = False,
    ) -> str:
        force_text_response = False
        recent_tool_steps: list[dict[str, str]] = []
        rounds_so_far = 0
        last_progress_marker = ""
        corrective_nudge_sent = False
        # Output-budget ladder: N continuation nudges per turn, then a named stop.
        self._output_token_override = None
        self._output_escalated = False
        self._model_fell_back = False
        self._length_budget_nudge_sent = False
        self._length_budget_nudges = 0
        self._empty_reply_nudges = 0
        self._intent_nudges = 0
        self._guard_nudges = 0
        self._completion_reviewed = False
        self._turn_started = time.monotonic()

        # Agentic loop — soft-block evaluation cadence. After every
        # max_tool_rounds block, _progress_marker decides whether to CONTINUE
        # (new evidence) or STOP (same evidence across a whole block). Only a
        # bounded hard roof stops a genuinely productive run.
        while rounds_so_far < self.max_total_tool_rounds:
            messages = self._drain_async_notifications(messages)
            messages, active_tools = self._round_preparation(
                messages,
                tool_schemas=tool_schemas,
                force_text_response=force_text_response,
                rounds_so_far=rounds_so_far,
            )

            fast_path = self._answer_mode_fast_path(
                messages,
                stream=stream,
                active_tools=active_tools,
                user_message=user_message,
                rounds_so_far=rounds_so_far,
                recent_tool_steps=recent_tool_steps,
            )
            if fast_path is _LOOP_CONTINUE:
                continue
            if isinstance(fast_path, str):
                return fast_path

            streamed, response = self._agentic_round_response(messages, active_tools, stream)
            tool_calls = _round_tool_calls(self, response, force_text_response)
            final_decision = self._try_final_text(
                tool_calls,
                response,
                user_message=user_message,
                active_tools=active_tools,
                rounds_so_far=rounds_so_far,
                recent_tool_steps=recent_tool_steps,
                message_stream=(stream, streamed, messages),
            )
            if final_decision is _LOOP_CONTINUE:
                continue
            if isinstance(final_decision, str):
                return final_decision

            rounds_so_far += 1
            post = self._post_round_state(
                messages,
                response,
                tool_calls,
                tools=tools,
                recent_tool_steps=recent_tool_steps,
                force_text_response=force_text_response,
                corrective_nudge_sent=corrective_nudge_sent,
                last_progress_marker=last_progress_marker,
                rounds_so_far=rounds_so_far,
            )
            # final_output is the turn's terminal answer (goose-style is_final
            # was decorative here — the loop kept asking the model for more,
            # which produced the triple-closing: report, "closing out" note,
            # second report). Its content IS the response: emit it as the
            # closing paragraph and end the turn. Renders in the terminal
            # assistant bubble (streams there as the final round), never
            # only inside the 300-char tool-card preview (findings B + C).
            terminal = self._terminal_answer(
                messages, tool_calls, user_message, post.force_text_response
            )
            if terminal is not None:
                return terminal

            force_text_response = post.force_text_response
            corrective_nudge_sent = post.corrective_nudge_sent
            last_progress_marker = post.last_progress_marker
            if post.should_continue:
                continue

        # Hard roof reached (or answer-mode flip exhausted the budget): one
        # no-tools wrap-up round lets the MODEL write the checkpoint from the
        # evidence (opencode MAX_STEPS pattern); the deterministic digest
        # remains the safety net when the wrap-up round fails or mis-shapes.
        answer = self._wrap_up_summary(messages, recent_tool_steps, rounds_so_far)
        # Emit as a full stream slice (START + CHUNK + DONE), not a bare
        # RESPONSE_DONE: chat clients that render chunks (TUI bridge) would
        # otherwise never show the checkpoint — a silent stop exactly where
        # the user needs the blocker named (2026-10-06 report).
        self._emit_streamed_text(answer, messages=messages)
        return answer

    def _drain_async_notifications(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """G9: completed background agents land as one user-role block per round.

        Appending only on actual completions keeps the request prefix intact
        while nothing finished (cache-safe no-op).
        """
        notes = self._async_pool.drain() + self._drain_job_notes()
        if not notes:
            return messages
        messages.append({"role": "user", "content": "\n\n".join(notes)})
        return messages

    def _session_job_watch(self) -> Any:
        """The session's JobWatch — top-level companions only: sub-agents
        share the client and must not take the parent's notices."""
        if getattr(self, "_call_type", "turn") != "turn":
            return None
        from .job_watch import job_watch_for

        return job_watch_for(self.client)

    def _drain_job_notes(self) -> list[str]:
        """ADR-105 D-5: UMRS jobs this session submitted that ended, as
        ``<job-notification>`` blocks (same round-boundary seam as G9)."""
        try:
            watch = self._session_job_watch()
            return list(watch.drain()) if watch is not None else []
        except Exception as exc:  # a broken feed never breaks a round
            logger.debug("job watch drain failed: %s", exc)
            return []

    def _prime_job_watch(self, tool_name: str) -> None:
        """Before a submit: take the event cursor (a fast job's end is then
        never behind it). Before job_wait_any: hand it the turn's cancel."""
        from .job_watch import SUBMIT_TOOLS, WAIT_TOOL

        if tool_name not in SUBMIT_TOOLS and tool_name != WAIT_TOOL:
            return
        try:
            watch = self._session_job_watch()
            if watch is None:
                return
            watch.cancel = self._turn_cancel
            if tool_name in SUBMIT_TOOLS:
                watch.prime()
        except Exception as exc:  # priming never blocks the tool call
            logger.debug("job watch prime failed: %s", exc)

    def _observe_submission(self, tool_name: str, result: str) -> None:
        from .job_watch import SUBMIT_TOOLS
        from .platform_brief import INVALIDATING_TOOLS, invalidate

        if tool_name in INVALIDATING_TOOLS:  # ADR-105 D-8: next send re-reads
            invalidate(self.client)
        if tool_name not in SUBMIT_TOOLS:
            return
        try:
            watch = self._session_job_watch()
            if watch is not None:
                watch.observe(tool_name, result)
        except Exception as exc:  # observation never fails a tool call
            logger.debug("job watch observe failed: %s", exc)

    def _round_preparation(
        self,
        messages: list[dict[str, Any]],
        *,
        tool_schemas: list[dict[str, Any]] | None,
        force_text_response: bool,
        rounds_so_far: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Per-round preamble: cancel check, pre-call hook, selection
        record (ADR-037), message fitting.

        ADR-037: the record is emitted even when the offered set pruned to
        zero — "0 of N offered" IS the selection outcome. Answer-mode
        (force-text) calls skip: selection phase absent by construction.
        """
        self._check_turn_cancel()
        if self._pre_llm_call:
            self._pre_llm_call(self._call_type)
        active_tools = None if force_text_response else tool_schemas
        if not force_text_response:
            self._emit_tool_selection(rounds_so_far)
        return self._fit_agent_messages(messages, active_tools), active_tools

    def _answer_mode_fast_path(
        self,
        messages: list[dict[str, Any]],
        *,
        stream: bool,
        active_tools: list[dict[str, Any]] | None,
        user_message: str,
        rounds_so_far: int,
        recent_tool_steps: list[dict[str, str]],
    ) -> Any:
        """Stream + answer-mode early exit.

        Returns _LOOP_CONTINUE (skip the round), the answer string (turn
        done), or None (no early exit applies — proceed to the model call).
        """
        if not (stream and not active_tools):
            return None
        answer = self._answer_mode_stream_answer(
            messages,
            active_tools,
            user_message=user_message,
            rounds_so_far=rounds_so_far,
            recent_tool_steps=recent_tool_steps,
        )
        if answer is None:
            return _LOOP_CONTINUE
        self._emit(CompanionEventType.RESPONSE_DONE, answer)
        return answer

    def _output_budget_ladder(
        self,
        response: dict[str, Any],
        *,
        tool_call_count: int,
        messages: list[dict[str, Any]],
        tool_mode: bool = False,
    ) -> Any:
        """Output-budget death ladder: no-op → one wrap-up nudge → named stop.

        None (did not fire) | _LOOP_CONTINUE (nudge queued; keep the round
        loop turning) | _OUTPUT_BUDGET_STOP string (second death, never "").
        The one-shot flag is instance state, reset per agentic turn.
        """
        if tool_call_count > 0:
            return None
        if _response_choice(response).get("finish_reason") == ARGS_BUDGET_FINISH:
            return self._ladder_step(
                messages,
                _planning_nudge_text(response),
                "Tool call stopped before it ran: planning was being written into code",
            )
        content = self._extract_content(response)
        if _is_text_cut(response, content, tool_mode):
            messages.append({"role": "assistant", "content": _clip_text_tail(content)})
            return self._ladder_step(
                messages, _TEXT_CUT_NUDGE, "Reply text cut off without a tool call"
            )
        if not _output_length_exhausted(response, content):
            return None
        escalated = self._escalate_output_budget()
        if escalated is not None:
            return escalated
        return self._ladder_step(
            messages, _length_nudge_text(response), "Output limit reached mid-reasoning"
        )

    def _escalate_output_budget(self) -> Any:
        """G19a: retry the same round once with a raised output-token cap.

        Before the ladder burns a continuation nudge on ``finish_reason:
        "length"``, raise the cap (``max(2x, +8k headroom)``, clamped by the
        harness ceiling ``R4_COMPANION_OUTPUT_ESCALATION_MAX``, default
        131,072) and resend — the prefix-cache makes an identical retry
        cheap, and a round that only needed more room completes without
        spending one of its bounded nudges. Published/persisted limits are
        endpoints' *defaults*, often conservative; the hard guard stays the
        context-window room clamp in ``_output_token_limit``. Skipped under
        an ``R4_MAX_OUTPUT_TOKENS`` pin (user intent stays authoritative) or
        when no cap was sent. ``R4_COMPANION_OUTPUT_ESCALATION=0`` restores
        the pre-G19a ladder.
        """
        if self._output_escalated or os.environ.get("R4_MAX_OUTPUT_TOKENS", "").strip():
            return None
        if os.environ.get("R4_COMPANION_OUTPUT_ESCALATION", "1").strip() == "0":
            return None
        model = self._effective_model() or ""
        current = self._requested_output_tokens(model)
        if not current:
            return None
        ceiling = _coerce_positive_int(
            os.environ.get("R4_COMPANION_OUTPUT_ESCALATION_MAX"),
            default=131_072,
        )
        target = min(max(current * 2, current + 8_192), ceiling)
        self._output_token_override = target
        self._output_escalated = True
        self._emit(
            CompanionEventType.THINKING,
            f"Output limit reached — raising the cap {current:,}→{target:,} and retrying "
            "the round (no continuation spent).",
        )
        return _LOOP_CONTINUE

    def _ladder_step(self, messages: list[dict[str, Any]], nudge: str, what: str) -> Any:
        """One continuation on the output-budget ladder, or its named stop."""
        limit = _length_recoveries()
        if self._length_budget_nudges >= limit:
            stop = _OUTPUT_BUDGET_STOP.format(n=self._length_budget_nudges + 1)
            # Full stream slice (not bare DONE) so chunk-renderers show the
            # named stop instead of ending the turn silently (2026-10-06).
            self._emit_streamed_text(stop, messages=messages)
            return stop
        self._length_budget_nudges += 1
        self._length_budget_nudge_sent = True
        messages.append({"role": "user", "content": nudge})
        self._emit(
            CompanionEventType.THINKING,
            f"{what} — continuing ({self._length_budget_nudges}/{limit}).",
        )
        return _LOOP_CONTINUE

    def _try_final_text(
        self,
        tool_calls: list[Any],
        response: Any,
        *,
        user_message: str,
        active_tools: list[dict[str, Any]] | None,
        rounds_so_far: int,
        recent_tool_steps: list[dict[str, str]],
        message_stream: tuple[bool, Any, list[dict[str, Any]]],
    ) -> Any:
        """Final-text branch: decision → _LOOP_CONTINUE | answer | None."""
        if tool_calls:
            return None
        budget_probe = self._output_budget_ladder(
            response,
            tool_call_count=0,
            messages=message_stream[2],
            tool_mode=bool(active_tools),
        )
        if budget_probe is not None:
            return budget_probe
        content = self._extract_content(response)
        if self._nudged_text_round(content, active_tools, message_stream[2]):
            return _LOOP_CONTINUE
        decision = self._final_text_decision(
            user_message,
            content,
            tools=active_tools,
            rounds_so_far=rounds_so_far,
            recent_tool_steps=recent_tool_steps,
        )
        if decision is None or self._review_before_final(
            decision, content, active_tools, message_stream[2], user_message
        ):
            return _LOOP_CONTINUE
        stream, streamed, messages = message_stream
        if stream and streamed is None:
            self._emit_streamed_text(decision, messages=messages)
        else:
            # Streamed rounds already emitted their content chunked —
            # just close the stream; do not re-emit the full text.
            self._emit(CompanionEventType.RESPONSE_DONE, decision)
        return decision

    def _nudged_text_round(
        self,
        content: str,
        active_tools: list[dict[str, Any]] | None,
        messages: list[dict[str, Any]],
    ) -> bool:
        """An empty reply or an announced-but-not-taken step got its nudge."""
        if self._nudge_empty_reply(content, active_tools, messages):
            return True
        return self._nudge_unfinished_intent(content, active_tools, messages)

    def _take_completion_review(self) -> bool:
        """True once per task in an unattended run (H4); the review is then spent.

        Sub-agents and role agents (call type ``subagent``) report to a parent
        whose own task gets the review."""
        if getattr(self, "_completion_reviewed", False) or not _completion_review_on():
            return False
        if getattr(self, "_call_type", "turn") != "turn":
            return False
        self._completion_reviewed = True
        return True

    def _queue_completion_review(self, messages: list[dict[str, Any]], task: str) -> None:
        elapsed = time.monotonic() - getattr(self, "_turn_started", time.monotonic())
        messages.append(_completion_review_message(task, elapsed))
        self._emit(
            CompanionEventType.THINKING,
            "Unattended run — one completion review against the task before it ends.",
        )

    def _review_before_final(
        self,
        decision: str,
        content: str,
        active_tools: list[dict[str, Any]] | None,
        messages: list[dict[str, Any]],
        task: str,
    ) -> bool:
        """Queue the completion review instead of ending on a final text reply.

        Only the model's own reply in tool mode (not a digest or the empty-reply
        note); the reply stays in history so the model reviews what it claimed.
        """
        if decision != content or not content.strip() or not active_tools:
            return False
        if self._tool_calling_disabled or not self._take_completion_review():
            return False
        messages.append({"role": "assistant", "content": content})
        self._queue_completion_review(messages, task)
        return True

    def _terminal_answer(
        self,
        messages: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        task: str,
        force_text_response: bool,
    ) -> str | None:
        """The executed ``final_output`` answer that ends the turn — or None: no
        final_output this round, or the completion review was queued instead (H4)."""
        terminal = _terminal_tool_answer(messages, tool_calls)
        if terminal is None:
            return None
        if not force_text_response and self._take_completion_review():
            self._queue_completion_review(messages, task)
            return None
        self._emit_streamed_text(terminal, messages=messages)
        return terminal

    def _nudge_empty_reply(
        self,
        content: str | None,
        active_tools: list[dict[str, Any]] | None,
        messages: list[dict[str, Any]],
    ) -> bool:
        """Empty reply in tool mode → continuation nudge (bounded) instead of
        the artifact path, which would disable tools for the whole session."""
        if (content or "").strip() or not active_tools or self._tool_calling_disabled:
            return False
        limit = _EMPTY_REPLY_MAX_NUDGES * (2 if _autonomous() else 1)
        if self._empty_reply_nudges >= limit:
            return False
        self._empty_reply_nudges += 1
        messages.append({"role": "user", "content": _EMPTY_REPLY_NUDGE})
        self._emit(CompanionEventType.THINKING, "Empty reply — asking the model to continue.")
        return True

    def _nudge_unfinished_intent(
        self,
        content: str | None,
        active_tools: list[dict[str, Any]] | None,
        messages: list[dict[str, Any]],
    ) -> bool:
        """Tool-mode reply that announces a step but calls no tool → keep the
        announcement in history and nudge (bounded, H17) instead of ending."""
        if not active_tools or self._tool_calling_disabled:
            return False
        if self._intent_nudges >= _UNFINISHED_INTENT_MAX_NUDGES:
            return False
        if not _announces_unfinished_step(content or ""):
            return False
        self._intent_nudges += 1
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": _UNFINISHED_INTENT_NUDGE})
        self._emit(
            CompanionEventType.THINKING,
            "Announced a step without a tool call — asking the model to act.",
        )
        return True

    def _post_round_state(
        self,
        messages: list[dict[str, Any]],
        response: Any,
        tool_calls: list[Any],
        *,
        tools: list[dict[str, Any]],
        recent_tool_steps: list[dict[str, str]],
        force_text_response: bool,
        corrective_nudge_sent: bool,
        last_progress_marker: str,
        rounds_so_far: int,
    ) -> "_RoundPostState":
        """After a tool-call round: record, mode control, block boundary.

        Collected so the agentic loop body stays flat (complexity gate);
        the pieces each keep their own narrow context.
        """
        round_message = (response.get("choices") or [{}])[0].get("message", {})
        self._record_tool_round(
            messages,
            tool_calls,
            tools or [],
            recent_tool_steps,
            round_message=round_message,
        )
        force_text_response, should_continue, corrective_nudge_sent = (
            self._post_tool_round_mode_control(
                messages,
                recent_tool_steps,
                force_text_response=force_text_response,
                corrective_nudge_sent=corrective_nudge_sent,
            )
        )
        marker = last_progress_marker
        if not should_continue:
            boundary_marker = self._block_boundary_control(
                messages,
                recent_tool_steps,
                rounds_so_far=rounds_so_far,
                last_progress_marker=last_progress_marker,
                force_text_response=force_text_response,
            )
            if boundary_marker is not None:
                marker = boundary_marker
        return _RoundPostState(
            force_text_response=force_text_response,
            should_continue=should_continue,
            corrective_nudge_sent=corrective_nudge_sent,
            last_progress_marker=marker,
        )

    def _post_tool_round_mode_control(
        self,
        messages: list[dict[str, Any]],
        recent_tool_steps: list[dict[str, str]],
        *,
        force_text_response: bool,
        corrective_nudge_sent: bool,
    ) -> tuple[bool, bool, bool]:
        """Corrective-nudge then answer-mode switch (OpenHands nudge pattern).

        Returns (new force_text_response, should_continue_loop, nudge_sent).
        """
        reason = _should_switch_to_answer_mode(recent_tool_steps)
        if self._autonomous_guard_nudge(messages, reason, force_text_response):
            return force_text_response, True, corrective_nudge_sent
        if (
            not force_text_response
            and reason is not None
            and not corrective_nudge_sent
            and _failure_streak_only(recent_tool_steps)
        ):
            # OpenHands nudge-then-stop: one corrective message before
            # halting — the model changes approach or expressly concedes.
            # Round-2 finding C: the nudge fires even when final_output
            # already executed this turn — a terminal answer exists, so
            # inviting "change approach" only manufactures a second report.
            if _any_named_step(recent_tool_steps, "final_output"):
                return True, False, True
            self._emit(
                CompanionEventType.THINKING,
                "Same call keeps failing — injecting one corrective nudge before answer mode.",
            )
            messages.append(_corrective_nudge_message(recent_tool_steps))
            return force_text_response, True, True
        if not force_text_response and reason is not None:
            self._emit(
                CompanionEventType.THINKING,
                f"Detected {reason}; switching to answer mode.",
            )
            messages.append(_answer_mode_system_nudge(reason))
            return True, False, corrective_nudge_sent
        return force_text_response, False, corrective_nudge_sent

    def _autonomous_guard_nudge(
        self, messages: list[dict[str, Any]], reason: str | None, force_text_response: bool
    ) -> bool:
        """Unattended run: a tripped guard nudges (bounded) instead of answer mode."""
        if reason is None or force_text_response or not _autonomous():
            return False
        if self._guard_nudges >= _AUTONOMOUS_GUARD_NUDGES:
            return False
        self._guard_nudges += 1
        self._emit(
            CompanionEventType.THINKING,
            f"Detected {reason}; unattended run — asking the model to change approach "
            f"({self._guard_nudges}/{_AUTONOMOUS_GUARD_NUDGES}).",
        )
        messages.append(_autonomous_guard_nudge(reason))
        return True

    def _block_boundary_control(
        self,
        messages: list[dict[str, Any]],
        recent_tool_steps: list[dict[str, str]],
        *,
        rounds_so_far: int,
        last_progress_marker: str,
        force_text_response: bool,
    ) -> str | None:
        """Soft-block boundary control — never stops the turn.

        Emits the progress notice and returns the updated marker when new
        evidence arrived; otherwise injects a checkpoint nudge and returns the
        unchanged marker. Only the hard roof and the exact-replay / drift /
        ssh-stall guards stop a turn now (copilot/opencode parity).
        """
        if rounds_so_far % self.max_tool_rounds != 0 or force_text_response:
            return None
        marker = _progress_marker(recent_tool_steps)
        if marker and marker != last_progress_marker:
            self._emit(
                CompanionEventType.THINKING,
                f"new evidence found at round {rounds_so_far} — "
                f"continuing ({self.max_tool_rounds}-round blocks).",
            )
            return marker
        self._emit(
            CompanionEventType.THINKING,
            f"checkpoint at {rounds_so_far} tool rounds — continuing (no stop on heuristic pause).",
        )
        messages.append(_checkpoint_system_message(rounds_so_far))
        return last_progress_marker

    def _wrap_up_summary(
        self,
        messages: list[dict[str, Any]],
        recent_tool_steps: list[dict[str, str]],
        rounds_so_far: int = 0,
    ) -> str:
        """opencode MAX_STEPS wrap-up: one no-tools round of model-authored
        checkpointing; deterministic digest fallback."""
        wrap_messages = list(messages) + [_wrap_up_system_prompt()]
        try:
            response = self._chat(wrap_messages, None)
            text = (self._extract_content(response) or "").strip()
        except TurnCancelledError:
            raise
        except Exception:
            logger.debug("wrap-up round failed; emitting deterministic digest")
            text = ""
        if text:
            self._emit(
                CompanionEventType.THINKING,
                "Tool budget reached — model wrap-up summary.",
            )
            return text
        return self._round_exhausted_fallback(recent_tool_steps, rounds_so_far)

    def _final_text_decision(
        self,
        user_message: str,
        content: str,
        *,
        tools: list[dict[str, Any]] | None,
        rounds_so_far: int,
        recent_tool_steps: list[dict[str, str]],
    ) -> str | None:
        """Decide the terminal text for a round without tool calls.

        Returns the answer/digest to return, or ``None`` to retry the round
        (structural artifact while tools were active). No-tools rounds
        (``tools is None`` — answer mode) never flip the session-wide tool
        switch: "retrying without tools" would falsify the session state.
        """
        if self._should_retry_without_tools(
            user_message=user_message,
            content=content,
            tools=tools,
        ):
            if rounds_so_far and not content:
                # Retry-without-tools also produced nothing after real tool
                # work — degrade to the evidence digest (fail-safe) instead
                # of returning '' to the user.
                return self._round_exhausted_fallback(recent_tool_steps, rounds_so_far)
            return None
        if not content and rounds_so_far and tools is None:
            # Answer-mode round produced only dialect scaffold: degrade to the
            # evidence digest instead of rendering raw markup.
            return self._round_exhausted_fallback(recent_tool_steps, rounds_so_far)
        # An empty reply (after the retry without tools) said nothing at all:
        # an empty bubble and an empty stored answer (companion sweep
        # 2026-10-21). Say so.
        return content if content.strip() else EMPTY_REPLY_NOTE

    def _answer_mode_stream_answer(
        self,
        messages: list[dict[str, Any]],
        active_tools: list[dict[str, Any]] | None,
        *,
        user_message: str,
        rounds_so_far: int,
        recent_tool_steps: list[dict[str, str]],
    ) -> str | None:
        """Answer-mode streamed round: final text/digest, or ``None`` to retry."""
        content = self._stream_text_response(messages, active_tools)
        return self._final_text_decision(
            user_message,
            _strip_tagged_tool_scaffold(content),
            tools=active_tools,
            rounds_so_far=rounds_so_far,
            recent_tool_steps=recent_tool_steps,
        )

    def _round_exhausted_fallback(
        self,
        recent_tool_steps: list[dict[str, str]],
        rounds_consumed: int = 0,
    ) -> str:
        """Degraded-mode checkpoint (evidence digest + resume pointers).

        The digest only appears at the hard roof or after identical-replay /
        drift / ssh-stall guard actions — never at a soft block boundary.
        """
        seen: set[str] = set()
        unique_steps: list[dict[str, str]] = []
        for step in recent_tool_steps:
            key = f"{step.get('name')}:{(step.get('result') or '')[:80]}"
            if key in seen:
                continue
            seen.add(key)
            unique_steps.append(step)

        header_rounds = rounds_consumed or len(seen)
        lines = [
            f"Checkpoint after {header_rounds} tool rounds "
            "— the turn ended here, but the thread keeps this digest. Evidence so far:",
        ]
        for step in unique_steps[:8]:
            name = step.get("name", "?")
            result = (step.get("result") or "").strip().replace("\n", " ")
            lines.append(f"  • {name} → {result[:140] or '(no output)'}")
        lines.extend(
            [
                "",
                "To continue without re-guessing, narrow the scope or pick one of:",
                "  1. `continue with <next action>` — e.g. "
                "'continue with the workflow draft from the evidence above'",
                "  2. a concrete pointer (path to `init.md`, the API key, or the ssh config fix)",
                "  3. `summary` — same digest, no new tooling",
                "",
                "The thread keeps this digest, so a follow-up turn resumes from here — it does not "
                "restart from zero.",
            ]
        )
        return "\n".join(lines)

    def _stream_text_response(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> str:
        """Stream a text-only model response, emitting RESPONSE_* events."""
        self._emit(CompanionEventType.RESPONSE_START, "")
        collected_content = ""
        for chunk_text in self._stream_chat(messages, tools):
            self._check_turn_cancel()
            collected_content += chunk_text
            self._emit(CompanionEventType.RESPONSE_CHUNK, chunk_text)
        self._record_streaming_usage(messages, collected_content, tools=tools)
        return collected_content

    def _emit_streamed_text(
        self,
        content: str,
        *,
        messages: list[dict[str, Any]],
    ) -> None:
        """Emit stream events for a blocking response that already has full text.

        No usage is recorded here: the blocking round that produced the text was
        accounted by ``_chat`` already (2026-10-05: re-recording it added one
        phantom, estimated "streaming" call per turn — TB driver metrics showed
        4 model calls for 3 real requests, and the phantom counted against the
        per-turn call budget)."""
        del messages  # kept for the call sites; the text needs no prompt here
        self._emit(CompanionEventType.RESPONSE_START, "")
        if content:
            self._emit(CompanionEventType.RESPONSE_CHUNK, content)
        self._emit(CompanionEventType.RESPONSE_DONE, content)

    def _stream_agentic_round(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        limits: RoundLimits | None = None,
    ) -> dict[str, Any] | None:
        """Stream one agentic round when the caller asked for streaming.

        Content chunks emit live so the caller sees thinking/answer text
        DURING reasoning, and tool-call deltas are reconstructed per index —
        a round mixing preamble text with tool calls still executes its
        tools (a text-only stream silently dropped them, ending the turn
        right after the preamble). Returns ``None`` when the route cannot
        surface tool-call deltas (provider-mediated streams) or the round
        streamed nothing at all; the caller then retries the round with a
        blocking call. A round that streamed only reasoning (cut at the output
        limit, stopped at the thinking budget, or an empty answer) is returned
        with its ``finish_reason`` — re-sending it blocking repeated the whole
        deliberation (up to 131k tokens, 2026-10-06).
        """
        self._emit(CompanionEventType.RESPONSE_START, "")
        reasoning_acc = _LiveReasoning(
            lambda text: self._emit(CompanionEventType.REASONING_DELTA, text)
        )
        try:
            return self._run_agentic_stream(messages, tools, limits, reasoning_acc)
        finally:
            if reasoning_acc:
                self._emit(CompanionEventType.REASONING_DONE, "")

    def _run_agentic_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        limits: RoundLimits | None,
        reasoning_acc: list[str],
    ) -> dict[str, Any] | None:
        """The body of :meth:`_stream_agentic_round` (its caller closes the
        live reasoning block whatever happens)."""
        collected: list[str] = []
        tool_calls_acc: list[dict[str, Any]] = []
        stream = self._stream_chat_collect(
            messages,
            tools,
            tool_calls_acc,
            reasoning_acc=reasoning_acc,
            limits=limits,
        )
        if stream is None:
            logger.debug("route cannot stream tool-call deltas; falling back to blocking call")
            return None
        if not self._drain_round_stream(stream, collected, tool_calls_acc, reasoning_acc):
            return None
        text = "".join(collected)
        reasoning = "".join(reasoning_acc).strip()
        if not text.strip() and not tool_calls_acc and not reasoning:
            logger.debug("streamed agentic round produced nothing; falling back to blocking call")
            return None
        # Account this round's tokens (measured when the server sent a usage
        # chunk, estimated otherwise) — streamed agentic rounds previously
        # recorded NO usage, so tool-heavy turns under-counted context and
        # the session-cost tracker never saw the stream at all.
        self._record_streaming_usage(
            messages,
            _message_text(
                {"content": text, "reasoning_content": reasoning, "tool_calls": tool_calls_acc}
            ),
            tools=tools,
        )
        finish = getattr(self.client, "_last_stream_finish", None)
        if finish == STREAM_CUT_FINISH:
            text, finish = self._flag_cut_stream(text), None
        payload = _streamed_round_payload(text, reasoning, tool_calls_acc, finish)
        # Preserve the XML scaffold fallback for models without structured
        # tool calling (e.g. Kimi-K2) — parse tags from streamed content.
        parsed = self._extract_tool_calls(payload)
        if parsed and not tool_calls_acc:
            payload["choices"][0]["message"]["tool_calls"] = parsed
        return payload

    def _flag_cut_stream(self, text: str) -> str:
        """The provider ended the stream with neither a finish_reason nor
        ``[DONE]``: say the reply may be cut, on screen AND in the stored
        reply, instead of finalising it as a complete answer (stabilization
        sweep 2026-10-21, chat#11)."""
        logger.warning("Stream ended without a finish signal; reply may be incomplete")
        note = ("\n\n" if text.strip() else "") + STREAM_CUT_NOTE
        self._emit(CompanionEventType.RESPONSE_CHUNK, note)
        return text + note

    def _drain_round_stream(
        self,
        stream: Iterator[str],
        collected: list[str],
        tool_calls_acc: list[dict[str, Any]],
        reasoning_acc: list[str],
    ) -> bool:
        """Emit a round's streamed chunks into ``collected``.

        False when the provider answered the stream request with a transient
        status (429/5xx) before anything arrived: the caller then runs the
        round blocking, which retries with backoff. A streamed 5xx used to
        surface after ONE request while the TUI said "already retried as
        transient" (stabilization sweep 2026-10-21, chat#10). Timeouts and
        mid-stream drops are not re-sent here (a stalled provider would
        otherwise be waited on once per attempt).
        """
        try:
            for chunk in stream:
                self._check_turn_cancel()
                collected.append(chunk)
                self._emit(CompanionEventType.RESPONSE_CHUNK, chunk)
        except TurnCancelledError:
            raise
        except Exception as exc:
            if collected or tool_calls_acc or reasoning_acc or not _status_is_transient(exc):
                raise
            self._before_blocking_retry(exc)
            logger.warning("Provider refused the stream (%s); retrying the round blocking", exc)
            return False
        return True

    def _before_blocking_retry(self, exc: Exception) -> None:
        """A stream refused before anything arrived is re-sent blocking, except
        a refusal no retry clears: a daily-token or session quota ends the turn
        with its notice instead of costing a second request; a request-rate
        refusal waits its Retry-After first (RS-1 fix Q5)."""
        if _is_transient_provider_error(exc) is None:
            raise_quota_notice(exc)
            raise exc
        rpm_wait = rpm_wait_seconds(exc)
        if rpm_wait is not None:
            self._cancelable_sleep(rpm_wait)

    def _build_stream_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Build the shared streaming request payload (same shape as _chat)."""
        paired, repairs = _repair_tool_pairing(messages)
        if repairs:
            logger.warning(
                "Repaired %d unpaired tool-call/result entries before stream request",
                repairs,
            )
        filtered = [
            m for m in paired if m.get("content") is not None or m.get("tool_calls") is not None
        ]
        payload: dict[str, Any] = {
            "model": self._effective_model(),
            "messages": self._bounded_history(filtered),
            **self._sampling(),
            "stream": True,
            # Ask OpenAI-compatible servers (vLLM, LiteLLM, Moonshot/Kimi)
            # for the final usage chunk so streamed rounds record MEASURED
            # tokens (cost/ctx chip) instead of estimates. Servers that
            # don't support it simply omit the chunk → estimate fallback.
            "stream_options": {"include_usage": True},
        }
        max_tokens = self._output_token_limit(payload["messages"])
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools and not self._tool_calling_disabled:
            payload["tools"] = tools
        return payload

    def _stream_chat_collect(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_calls_acc: list[dict[str, Any]],
        *,
        reasoning_acc: list[str] | None = None,
        limits: RoundLimits | None = None,
    ) -> Iterator[str] | None:
        """Stream one round while reconstructing tool-call deltas.

        Uses ``Client._stream_request_collect`` when the client provides it.
        Returns ``None`` when the route cannot surface tool-call deltas; the
        caller then falls back to a blocking round so tool calls are never
        lost. Clients without the collecting API stream text only.
        """
        collect = getattr(self.client, "_stream_request_collect", None)
        if callable(collect):
            return collect(
                self._build_stream_payload(messages, tools),
                tool_calls_acc,
                reasoning_acc=reasoning_acc,
                turn_cancel=self._turn_cancel,
                **_budget_kwargs(limits),
            )
        return self.client._stream_request(
            self._build_stream_payload(messages, tools),
            turn_cancel=self._turn_cancel,
        )

    def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> Iterator[str]:
        """Make a streaming LLM call, yielding text chunks.

        Builds the request payload the same way as ``_chat`` but with
        ``stream: True`` and calls ``Client._stream_request`` directly so the
        mock client in tests can intercept the call via ``_stream_request``.
        A platform quota refusal ends the turn with its notice (ADR-099).
        """
        try:
            yield from self.client._stream_request(
                self._build_stream_payload(messages, tools),
                turn_cancel=self._turn_cancel,
            )
        except TurnCancelledError:
            raise
        except Exception as exc:
            raise_quota_notice(exc)
            raise

    def _measured_stream_usage_payload(self, real: dict[str, Any]) -> dict[str, Any] | None:
        """Build a measured usage payload from the stream's usage chunk.

        Returns ``None`` when the chunk carries no usable counts (mock
        clients, zero-usage edge) so the caller keeps the estimate path.
        """
        prompt_tokens = self._normalize_token_count(real.get("prompt_tokens", 0))
        completion_tokens = self._normalize_token_count(real.get("completion_tokens", 0))
        total_tokens = self._normalize_token_count(real.get("total_tokens", 0))
        if total_tokens <= 0:
            total_tokens = prompt_tokens + completion_tokens
        if prompt_tokens + completion_tokens <= 0:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "estimated": False,
            "model": str(real.get("model") or "") or self._effective_model(),
            "streaming": True,
        }

    def _record_streaming_usage(
        self,
        messages: list[dict[str, Any]],
        collected_content: str,
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        # Prefer the stream's MEASURED usage (captured by the client from the
        # final SSE chunk when the server honours stream_options.include_usage).
        # Consume-on-read keeps the value paired with exactly this round; a
        # server that sent no usage chunk leaves None → estimate fallback.
        real = getattr(self.client, "_last_stream_usage", None)
        if real is not None:
            self.client._last_stream_usage = None
            payload = self._measured_stream_usage_payload(real)
            if payload is not None:
                # G10: re-anchor the context estimator (independent of the
                # cost-accounting callback below).
                self._observe_prompt_usage(messages, payload["prompt_tokens"], tools=tools)
                if self._usage_callback:
                    self._usage_callback(self._call_type, payload)
                return
        if not self._usage_callback:
            return
        prompt_tokens = self._estimate_tokens(_messages_text(messages))
        completion_tokens = self._estimate_tokens(collected_content)
        self._usage_callback(
            self._call_type,
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "estimated": True,
                "model": self._effective_model(),
                "streaming": True,
            },
        )

    def _observe_prompt_usage(
        self,
        messages: list[dict[str, Any]],
        prompt_tokens: int,
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        """G10: forward one measured prompt count to the context estimator.

        Never raises: a misbehaving observer must not break a turn. Sessions
        without a ContextManager (or one without the method) simply skip the
        calibration sample.
        """
        context = self._context
        observe = getattr(context, "observe_prompt_usage", None)
        if prompt_tokens <= 0 or not callable(observe):
            return
        try:
            observe(messages=messages, tools=tools, prompt_tokens=prompt_tokens)
        except Exception:
            logger.debug("prompt-usage observation skipped", exc_info=True)

    def send_streaming(
        self,
        user_message: str,
        *,
        history: list[dict[str, Any]] | None = None,
        extra_system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ):
        """Streaming variant of ``send`` — yields response chunks.

        Yields ``CompanionEvent`` objects. The final event has type
        ``RESPONSE_DONE``.
        """
        events: list[CompanionEvent] = []

        def _capture(event: CompanionEvent) -> None:
            events.append(event)

        self.on_event(_capture)
        try:
            self.send(
                user_message,
                history=history,
                extra_system=extra_system,
                tools=tools,
                stream=True,
            )
        finally:
            self.off_event(_capture)
        yield from events

    def _with_async_agent_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """G9: background-agent verbs beside any tool set (same lane as the
        tool_result_fetch meta tool — instance-bound, always available).

        Tool-less sends stay tool-less (fetch-tool precedent): a bare chat
        round must not gain schemas — they would push it off the plain
        streaming path into agentic dialect parsing (2026-10-29, found by the
        streaming pin suite)."""
        if not tools or any(t.get("name") == "async_agent_spawn" for t in tools):
            return tools
        metas = [
            self._async_agent_spawn_tool_dict(),
            self._async_agent_status_tool_dict(),
            self._async_agent_message_tool_dict(),
        ]
        # The sprint reviewer drives the whole contract lane; the terminal
        # profile's tool set is a fixed benchmark allowlist, so it stays out.
        if not _terminal_profile():
            metas.append(self._contract_review_tool_dict())
        return [*tools, *metas]

    def _contract_review_tool_dict(self) -> dict[str, Any]:
        return {
            "name": "build_contract_review",
            "description": (
                "Run an independent QA review of the current build contract sprint: a "
                "fresh-context evaluator agent runs the checks itself and returns a JSON "
                "verdict with hard-threshold findings. Call BEFORE marking features passing, "
                "or when a sprint feels done. Fail findings tell you exactly what to fix."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "focus": {
                        "type": "string",
                        "description": "What to grade (default: the whole active contract)",
                    }
                },
            },
            "execute": lambda focus="": self.run_sprint_review(focus),
            "concurrency_safe": False,
        }

    def run_sprint_review(self, focus: str = "") -> str:
        """G15: fresh-context evaluator over the active build contract."""
        import json as _json

        from .build_contract import contract_payload
        from .sprint_review import (
            evaluator_tools,
            parse_verdict,
            record_review,
            review_task,
        )

        root = os.getcwd()
        payload = contract_payload(root)
        if payload is None:
            return _json.dumps(
                {"error": "no build contract found; start one with build_contract_start"}
            )
        tools = evaluator_tools(self._last_wire_tools or [])
        task = review_task(payload["data"], focus)
        started_status = payload["status"]
        if not started_status.get("active"):
            return _json.dumps({"error": "contract is complete; nothing to review"})
        result = self.spawn_subagent(
            task,
            system="You are a skeptical QA evaluator. Grade the work, never the intent.",
            tools=tools,
        )
        verdict = parse_verdict(result)
        record_review(root, verdict, focus)
        verdict["next"] = started_status.get("next")
        if verdict.get("verdict") == "fail":
            verdict["note"] = (
                "Sprint failed review — fix the findings, then re-run build_contract_review "
                "before marking anything passing."
            )
        return _json.dumps(verdict, indent=2)

    def _async_agent_spawn_tool_dict(self) -> dict[str, Any]:
        return {
            "name": "async_agent_spawn",
            "description": (
                "Start a background sub-agent on a self-contained subtask and KEEP WORKING — "
                "its result arrives in a later round as a <subagent-notification>. Use for "
                "parallel read-heavy work (exploration, searches, reviews); pass everything "
                "the agent needs in the task text (it starts with a clean context)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Self-contained subtask description",
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "Optional model override for this agent (cheaper/stronger legs)"
                        ),
                    },
                },
                "required": ["task"],
            },
            "execute": self._async_agent_spawn_exec,
            "concurrency_safe": True,
        }

    def _async_agent_spawn_exec(self, task: str, model: str | None = None) -> str:
        """The async_agent_spawn verb (named so the line budget holds)."""
        return self.spawn_subagent_async(task, model=model or None)

    def _async_agent_status_tool_dict(self) -> dict[str, Any]:
        return {
            "name": "async_agent_status",
            "description": (
                "List this turn's background agents: id, task, status (running/completed/"
                "failed/cancelled), duration, and a result head for completed ones."
            ),
            "parameters": {"type": "object", "properties": {}},
            "execute": self._async_agent_status_tool,
            "concurrency_safe": True,
        }

    def _async_agent_message_tool_dict(self) -> dict[str, Any]:
        return {
            "name": "async_agent_message",
            "description": (
                "Send a follow-up message to a finished background agent (async_agent_status "
                "lists ids). The agent resumes with its accumulated context; its reply "
                "arrives as a later <subagent-notification>."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Background agent id"},
                    "message": {"type": "string", "description": "The follow-up instruction"},
                },
                "required": ["agent_id", "message"],
            },
            "execute": lambda agent_id, message: self.message_subagent(agent_id, message),
            "concurrency_safe": True,
        }

    def _async_agent_status_tool(self) -> str:
        import json as _json

        records = self._async_pool.status()
        return _json.dumps(records or {"background_agents": "none"}, indent=2)

    def _emit_agent_ended_early(self, record: Any, started: float) -> None:
        self._emit(
            CompanionEventType.AGENT_RESULT,
            f"Background sub-agent {record.agent_id} ended early.",
            data={
                "agent_id": record.agent_id,
                "status": "error",
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )

    def message_subagent(self, agent_id: str, message: str) -> str:
        """G17b: send a follow-up into a finished agent's accumulated context.

        The same agent id resumes (the child and its reconstructed history
        were retained at completion); running agents reject with a clear
        error (queue-while-running is a later wave).
        """
        import json as _json

        record = self._async_pool.get(agent_id)
        if record is None:
            return _json.dumps({"error": f"unknown background agent {agent_id!r}"})
        if record.status == "running":
            return _json.dumps({"error": f"{agent_id} is still running; wait for its notification"})

        def _follow(r: Any) -> str:
            child = r.child or self._new_subagent()
            if self._turn_cancel is not None:
                child.set_turn_cancel(self._turn_cancel)
            started = r.started_at
            try:
                result = child.send(
                    message,
                    history=list(r.history),
                    extra_system=r.system_prompt or "",
                )
            except Exception:
                self._emit_agent_ended_early(r, started)
                raise
            self._emit(
                CompanionEventType.AGENT_RESULT,
                result[:200],
                data={
                    "agent_id": r.agent_id,
                    "status": "completed",
                    "follow_up": True,
                    "result_preview": result[:1200],
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            r.child = child
            r.history.extend(
                [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": result},
                ]
            )
            return result

        resumed = self._async_pool.resume(agent_id, _follow)
        if isinstance(resumed, dict):
            return _json.dumps(resumed)
        self._emit(
            CompanionEventType.AGENT_SPAWN,
            f"Follow-up to background agent {agent_id}: {message[:80]}…",
            data={"agent_id": agent_id, "task": message, "follow_up": True},
        )
        return (
            f"Follow-up sent to background agent {agent_id}; its reply arrives as a "
            "<subagent-notification> in a later round."
        )

    def spawn_subagent_async(
        self,
        task: str,
        *,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> str:
        """G9: start a background sub-agent; results drain into later rounds.

        The child shares this companion's client and cancellation token, runs
        with half the round budget (same shape as ``spawn_subagent``), and its
        completion becomes a ``<subagent-notification>`` user-role block at the
        parent's next round boundary. ``model`` (G17a) overrides the child's
        model for cheaper or stronger legs.
        """
        system_prompt = system or "You are a sub-agent. Be concise. Return only results."

        def _runner(record: Any) -> str:
            sub = self._new_subagent(model)
            if self._turn_cancel is not None:
                sub.set_turn_cancel(self._turn_cancel)
            started = record.started_at
            try:
                result = sub.send(task, extra_system=system_prompt, tools=tools)
            except Exception:
                self._emit_agent_ended_early(record, started)
                raise
            self._emit(
                CompanionEventType.AGENT_RESULT,
                result[:200],
                data={
                    "agent_id": record.agent_id,
                    "status": "completed",
                    "result_preview": result[:1200],
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            # G17b: the child and its conversation survive for follow-ups.
            record.child = sub
            record.system_prompt = system_prompt
            record.history.extend(
                [
                    {"role": "user", "content": task},
                    {"role": "assistant", "content": result},
                ]
            )
            return result

        spawned = self._async_pool.spawn(task, _runner)
        if isinstance(spawned, dict):
            import json as _json

            return _json.dumps(spawned)
        label = self._subagent_label(model)
        self._emit(
            CompanionEventType.AGENT_SPAWN,
            f"Background delegation to {label}: {task[:80]}…",
            data={"agent_id": spawned.agent_id, "task": task, "async": True, "model": label},
        )
        return (
            f"Background agent {spawned.agent_id} started on the task. Its result will "
            "arrive as a <subagent-notification> in a later round — keep working; do not "
            "wait idle for it."
        )

    # -- sub-agent ----------------------------------------------------------

    def spawn_subagent(
        self,
        task: str,
        *,
        history: list[dict[str, Any]] | None = None,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        """Spawn a focused sub-agent for a delegated task.

        The sub-agent runs in the same process, uses the same Client, but
        gets a clean context window with only the task description.

        The spawn/result events carry the delegation contract
        (agent_id, task, and the verbatim system prompt the sub-agent ran
        under) so UI surfaces can show the user what the sub-agent was
        asked to do (team visibility, 2026-09-27).

        Returns:
            The sub-agent's final response.
        """
        import time
        import uuid

        agent_id = uuid.uuid4().hex[:12]
        system_prompt = system or "You are a sub-agent. Be concise. Return only results."
        label = self._subagent_label()
        self._emit(
            CompanionEventType.AGENT_SPAWN,
            f"Delegating to {label}: {task[:80]}…",
            data={
                "model": label,
                "agent_id": agent_id,
                "parent_agent_id": getattr(self, "agent_id", None),
                "task": task,
                "system_prompt": system_prompt,
            },
        )

        sub = self._new_subagent()
        started = time.monotonic()
        try:
            result = sub.send(
                task,
                history=history,
                extra_system=system_prompt,
                tools=tools,
            )
        except Exception as exc:
            self._emit(
                CompanionEventType.AGENT_RESULT,
                f"Sub-agent failed: {exc}",
                data={
                    "agent_id": agent_id,
                    "parent_agent_id": getattr(self, "agent_id", None),
                    "status": "error",
                    "error": str(exc),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            raise
        self._emit(
            CompanionEventType.AGENT_RESULT,
            result[:200],
            data={
                "agent_id": agent_id,
                "parent_agent_id": getattr(self, "agent_id", None),
                "status": "completed",
                "result_preview": result[:1200],
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return result

    # -- internals ----------------------------------------------------------

    def _build_messages(
        self,
        user_message: str,
        history: list[dict[str, Any]] | None,
        extra_system: str,
        reminder: str = "",
    ) -> list[dict[str, Any]]:
        """Assemble the message list for the LLM call.

        Wave 80 (vLLM prefix caching): the system message carries only what
        is stable for the session (``_system_message``); the per-turn state —
        the ``<project_state>`` block and the caller's ``reminder`` — rides
        the ``<turn_context>`` block of this turn's user message. Under the
        terminal profile (H2 ablation) the base prompt is the profile's core
        prompt and the ``<project_state>`` block is left out.
        """
        terminal = _terminal_profile()
        # D-28 project-state block: the active project's status snapshot
        # reaches the model as context (fit_payload still governs the
        # budget; the block is byte-bounded and absent when the project
        # surfaces are unreachable — never fabricated).
        state = project_state_block("" if terminal else self._load_project_state())
        if stable_prefix_enabled():
            system = self._system_message(extra_system, terminal)
            user_message = with_turn_context(user_message, join_parts(state, reminder))
        else:  # the pre-wave-80 layout: everything in the system message
            system = self._system_message(join_parts(extra_system, reminder), terminal, state)
        return [system, *(history or []), {"role": "user", "content": user_message}]

    def _system_message(
        self, extra_system: str, terminal: bool | None = None, project_state: str = ""
    ) -> dict[str, str]:
        """The session-stable system message: base prompt, the caller's stable
        extra block, the R4.md briefing and the HPC sites (wave 80).
        ``project_state`` is set only by the pre-wave-80 layout."""
        if terminal is None:
            terminal = _terminal_profile()
        # Inject R4.md per-repo agent briefing if present.
        # R4.md is a Markdown file at the repository root that gives the
        # agent project-specific context: conventions, build commands,
        # file layout, and "do not" rules.  Inspired by claw-code's CLAW.md
        # and goose's AGENTS.md patterns.
        briefing = _load_r4md_briefing()
        # Multi-HPC plan S2: the user's HPC sites, fetched once per session
        # and kept for a TTL (never per turn), only when there are >= 2.
        hpc_sites = self._load_hpc_sites()
        content = join_parts(
            _base_system_prompt(terminal),
            extra_system,
            f"<repo_briefing>\n{briefing}\n</repo_briefing>" if briefing else "",
            project_state,
            f"<hpc_sites>\n{hpc_sites}\n</hpc_sites>" if hpc_sites else "",
        )
        return {"role": "system", "content": content}

    def compaction_request(
        self,
        messages: list[dict[str, Any]],
        *,
        extra_system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Wave 80: the compaction call shares the turn's prefix — the same
        system message and tools array, the history as the turns sent it —
        and appends the summary instruction (``tool_choice: none``)."""
        request = [
            self._system_message(extra_system),
            *messages,
            {"role": "user", "content": COMPACTION_INSTRUCTION},
        ]
        wire_tools = self._with_async_agent_tools(
            self._with_tool_result_fetch(self._wire_tools(tools or [], "")[0])
        )
        schemas = _tools_to_openai_schema(wire_tools) if wire_tools else None
        filtered = self._clean_chat_messages(self._fit_agent_messages(request, schemas))
        payload = self._chat_payload(filtered, schemas)
        if payload.get("tools"):
            payload["tool_choice"] = "none"
        return self._request_with_transient_retry(payload)

    def _load_hpc_sites(self) -> str:
        """The ``<hpc_sites>`` text from this agent's session cache."""
        cache = getattr(self, "_hpc_sites_cache", None)
        if cache is None:
            from ..sites import SitesPromptCache

            cache = self._hpc_sites_cache = SitesPromptCache()
        return cache.text(getattr(self.client, "config", None))

    def _load_project_state(self) -> str:
        """D-28: bounded project status for the context assembly.

        One ``build_project_status`` snapshot (the same surface the
        ``project_status`` tool serves), truncated to the block budget;
        empty on any failure — the model never sees an invented project.
        ADR-105 D-8: UMRS renders it in one cached read when it can; the
        client builder below is the fallback for an older UMRS.
        """
        from .platform_brief import load_brief

        brief = load_brief(self.client)
        if brief is not None:
            return brief[:_PROJECT_STATE_BUDGET]
        try:
            from .status_report import build_project_status

            text = build_project_status(self.client, include_local=True)
        except Exception:  # noqa: BLE001 — context block is best-effort
            return ""
        return _reachable_sections(text)[:_PROJECT_STATE_BUDGET]

    def _should_retry_without_tools(
        self,
        *,
        user_message: str,
        content: str,
        tools: list[dict[str, Any]] | None,
    ) -> bool:
        """Disable tools and retry when tool mode yields bogus structured text."""
        if not tools or self._tool_calling_disabled:
            return False
        if _user_requested_structured_output(user_message):
            return False
        if not _looks_like_tool_mode_artifact(content):
            return False

        self._tool_calling_disabled = True
        model_name = self._effective_model()
        logger.warning(
            "Model %s returned structured text in tool mode; retrying without tools",
            model_name,
        )
        what = "an empty reply" if not content.strip() else "structured text"
        self._emit(
            CompanionEventType.TOOLS_DISABLED,
            f"Model {model_name} produced {what} instead of a normal reply; "
            "companion tools are disabled for this session (/clear re-enables them).",
        )
        return True

    def _chat(  # noqa: C901 — reason=pre-existing complexity debt; tagged with the loop-guard wave owner=agent-debug added=2026-09-05
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        allow_context_retry: bool = True,
        limits: RoundLimits | None = None,
    ) -> dict[str, Any]:
        """Make a non-streaming LLM call and return the raw response dict.

        Uses raw message dicts (not ChatMessage) because tool-call messages
        contain fields like ``tool_call_id`` and ``tool_calls`` that
        ChatMessage doesn't support.
        """
        filtered = self._clean_chat_messages(messages)
        payload = self._chat_payload(filtered, tools)
        raw = self._chat_call_with_recovery(payload, filtered, tools, allow_context_retry, limits)
        if isinstance(raw, dict):
            # Context-overflow recursion already finished the round (usage
            # accounted inside); the value IS the final response dict.
            return raw
        self._record_chat_usage(raw, filtered, tools=payload.get("tools"))
        return raw.raw_response or {"choices": [{"message": {"content": raw.content}}]}

    def _sampling(self) -> dict[str, float]:
        """Sampling fields of a request: temperature, and top_p only when set (H14)."""
        sampling = {"temperature": self.temperature}
        top_p = getattr(self, "top_p", None)
        if top_p is not None:
            sampling["top_p"] = top_p
        return sampling

    def _clean_chat_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Pair repairs + empty-content filtering (extracted for the gate)."""
        paired, repairs = _repair_tool_pairing(messages)
        if repairs:
            logger.warning(
                "Repaired %d unpaired tool-call/result entries before request",
                repairs,
            )
        return [
            m for m in paired if m.get("content") is not None or m.get("tool_calls") is not None
        ]

    def _chat_payload(
        self, filtered: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        """Request body for one ``_chat`` round (extracted for the gate)."""
        payload: dict[str, Any] = {
            "model": self._effective_model(),
            # Topic-20 tail: same binding rule as the streaming path — old tool
            # payloads carry target-only bounds, newest round untouched.
            "messages": self._bounded_history(filtered),
            **self._sampling(),
            "stream": False,
        }
        max_tokens = self._output_token_limit(payload["messages"])
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools and not self._tool_calling_disabled:
            payload["tools"] = tools
        return payload

    def _chat_call_with_recovery(
        self,
        payload: dict[str, Any],
        filtered: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        allow_context_retry: bool,
        limits: RoundLimits | None = None,
    ) -> Any:
        """Request plus recovery ladder; cancel re-raise stays at the caller edge."""
        try:
            return self._request_with_transient_retry(payload, limits=limits)
        except TurnCancelledError:
            raise
        except Exception as exc:
            return self._recover_chat_after_error(
                exc, payload, filtered, tools, allow_context_retry
            )

    def _recover_chat_after_error(
        self,
        exc: Exception,
        payload: dict[str, Any],
        filtered: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        allow_context_retry: bool,
    ) -> Any:
        """Recovery ladder for a failed ``_chat`` round (extracted from _chat
        for the complexity gate): cancel guard → overflow trim retry →
        tools-strip retry; otherwise re-raise."""
        if self._turn_cancel is not None and self._turn_cancel.is_set():
            raise TurnCancelledError() from exc
        if allow_context_retry and _context_overflow_retryable(exc):
            trimmed = _trim_messages_on_overflow(filtered)
            if trimmed != filtered:
                self._emit(
                    CompanionEventType.CONTEXT_COMPACT,
                    "Context full — trimming older messages and retrying…",
                )
                return self._chat(trimmed, tools, allow_context_retry=False)
        if "tools" not in payload or not _tool_calling_retryable(exc):
            raise exc
        self._tool_calling_disabled = True
        model_name = payload["model"]
        logger.warning(
            "Model %s rejected tool-enabled companion request; retrying without tools: %s",
            model_name,
            exc,
        )
        self._emit(
            CompanionEventType.TOOLS_DISABLED,
            f"Model {model_name} does not support tool calling; "
            f"companion tools are disabled for this session.",
        )
        retry_payload = dict(payload)
        retry_payload.pop("tools", None)
        return self._request_with_transient_retry(retry_payload)

    def _record_chat_usage(
        self,
        raw: Any,
        filtered: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        """Per-call token accounting (extracted from _chat for the gate)."""
        usage = getattr(raw, "usage", None)  # fakes and stream-closed rounds may carry none
        prompt_tokens = self._normalize_token_count(getattr(usage, "prompt_tokens", 0))
        completion_tokens = self._normalize_token_count(getattr(usage, "completion_tokens", 0))
        total_tokens = self._normalize_token_count(getattr(usage, "total_tokens", 0))
        if total_tokens > 0:
            # G10: provider-measured counts re-anchor the context estimator
            # even when no usage_callback is wired for cost accounting.
            self._observe_prompt_usage(filtered, prompt_tokens, tools=tools)
        if not self._usage_callback:
            return
        estimated = total_tokens <= 0
        if estimated:
            prompt_tokens = self._estimate_tokens(_messages_text(filtered))
            completion_tokens = self._estimate_tokens(_completion_text(raw))
            total_tokens = prompt_tokens + completion_tokens
        self._usage_callback(
            self._call_type,
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "estimated": estimated,
                "model": raw.model or self._effective_model(),
            },
        )

    def _transient_retry_attempts(self) -> int:
        """Total attempts per provider request; R4_COMPANION_TRANSIENT_RETRIES env."""
        return _coerce_positive_int(
            os.environ.get("R4_COMPANION_TRANSIENT_RETRIES"),
            default=3,
        )

    def _cancelable_sleep(self, seconds: float) -> None:
        """Sleep in 50 ms slices so turn cancellation stays responsive."""
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            self._check_turn_cancel()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))

    def _retry_on_or_raise(
        self,
        payload: dict[str, Any],
        exc: Exception,
        hint: float | None,
        limits: "RoundLimits | None",
    ) -> Any:
        """G19b: a transient error's last shot — one flip to the env-pinned
        fallback model; otherwise the quota notice names the failure."""
        if hint is not None and self._try_model_fallback():
            self._emit(
                CompanionEventType.THINKING,
                f"Provider keeps failing — switching model to {self.model} and retrying.",
            )
            return self.client._request(
                payload, turn_cancel=self._turn_cancel, **_budget_kwargs(limits)
            )
        raise_quota_notice(exc)  # ADR-099: the turn's error is the notice
        raise exc

    def _try_model_fallback(self) -> bool:
        """G19b: one mid-loop flip to ``R4_COMPANION_FALLBACK_MODEL`` per turn.

        Opt-in (unset env = no fallback, today's behavior exactly): routing
        policy belongs to the operator, not to guesses from endpoint states.
        """
        if self._model_fell_back:
            return False
        candidate = os.environ.get("R4_COMPANION_FALLBACK_MODEL", "").strip()
        if not candidate or candidate == (self.model or ""):
            return False
        logger.warning("Mid-loop model fallback: %s → %s", self.model, candidate)
        self.model = candidate
        self._model_fell_back = True
        return True

    def _request_with_transient_retry(
        self, payload: dict[str, Any], *, limits: RoundLimits | None = None
    ) -> Any:
        """One provider request with jittered backoff for transient errors.

        Patterns: goose retry.rs (cap 3, 0.8-1.2x jitter, honor retry_after),
        OpenHands tenacity whitelist. Non-transient exceptions pass through
        unchanged so the existing overflow/tool-reject recovery sees them.
        """
        attempts = self._transient_retry_attempts()
        for attempt in range(1, attempts + 1):
            try:
                return self.client._request(
                    payload, turn_cancel=self._turn_cancel, **_budget_kwargs(limits)
                )
            except TurnCancelledError:
                raise
            except Exception as exc:
                if self._turn_cancel is not None and self._turn_cancel.is_set():
                    raise TurnCancelledError() from exc
                hint = _is_transient_provider_error(exc)
                if hint is None or attempt >= attempts:
                    return self._retry_on_or_raise(payload, exc, hint, limits)
                delay = _transient_retry_delay(exc, hint, attempt)
                logger.warning(
                    "Transient provider error (attempt %d/%d): %s — retrying in %.1fs",
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
                self._emit(
                    CompanionEventType.THINKING,
                    f"Provider hiccup ({type(exc).__name__}) — retry "
                    f"{attempt + 1}/{attempts} in {delay:.1f}s…",
                )
                self._cancelable_sleep(delay)
        return None  # unreachable — loop always returns or raises

    def _fit_agent_messages(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Keep in-flight agent/tool messages inside the context budget."""
        messages = _system_nudges_as_user(messages)
        ctx = self._context
        if ctx is None or not messages:
            return messages

        system_parts, rest = _split_system_prefix(messages)
        user_message, history = _pop_trailing_user(rest)

        _, history, _, budget = ctx.fit_payload(
            system_prompt="\n\n".join(system_parts) or " ",
            extra_system="",
            history=history,
            user_message=user_message or " ",
            tools=tools,
        )
        rebuilt = _rebuild_fitted_messages(system_parts, history, user_message)
        if budget.hard_limit_exceeded:
            self._emit(
                CompanionEventType.CONTEXT_COMPACT,
                "Context budget tight — older tool output was trimmed.",
            )
        # Trimming can split assistant(tool_calls)+tool-result pairs — providers
        # reject the result; repair before handing back.
        rebuilt, _ = _repair_tool_pairing(rebuilt)
        return rebuilt

    def _extract_tool_calls(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tool calls from the LLM response.

        Prefers OpenAI-style ``message.tool_calls``; falls back to parsing
        XML tool-call syntax embedded in ``message.content`` for models that
        do not emit structured tool calls (e.g. Kimi-K2).
        """
        choices = response.get("choices", [])
        if not choices:
            return []
        message = choices[0].get("message", {})
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            return tool_calls
        content = message.get("content") or ""
        tagged_calls = _parse_tagged_tool_calls(content)
        if tagged_calls:
            return tagged_calls
        open_pipe_calls = _parse_open_pipe_tool_calls(content)
        if open_pipe_calls:
            return open_pipe_calls
        if "<invoke" in content or "<tool_call" in content:
            return _parse_xml_tool_calls(content)
        return []

    def _extract_content(self, response: dict[str, Any]) -> str:
        """Extract text content from the LLM response."""
        choices = response.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "") or ""
        return _strip_tagged_tool_scaffold(content)

    def _invoke_tool_executor(
        self,
        name: str,
        tool_def: dict[str, Any],
        args: dict[str, Any],
        tool_call_id: str = "",
    ) -> str:
        executor = tool_def.get("execute")
        if not executor:
            return _tool_failure_text(
                "unknown_tool",
                name=name,
                detail=f"Error: tool '{name}' has no executor",
            )
        try:
            result = executor(**_without_null_optionals(tool_def, args))
            result_str = json.dumps(result) if not isinstance(result, str) else result
            result_str = handle_large_response(
                result_str,
                tool_name=name,
                truncate_at=COMPANION_TOOL_OUTPUT_TRUNCATE,
                summarize_at=COMPANION_TOOL_OUTPUT_TRUNCATE * 3,
            )
            self._emit(
                CompanionEventType.TOOL_RESULT,
                result_str[:200],
                data={
                    "name": name,
                    "result": result_str[:2000],
                    "tool_call_id": tool_call_id,
                },
            )
            return result_str
        except Exception as exc:
            error_msg = _tool_failure_text(
                "execution_error",
                name=name,
                detail=f"Error executing {name}: {exc}",
            )
            self._emit(CompanionEventType.ERROR, error_msg)
            return error_msg

    def _invoke_tool_executor_cancellable(
        self,
        name: str,
        tool_def: dict[str, Any],
        args: dict[str, Any],
        tool_call_id: str = "",
    ) -> str:
        """Run a tool executor but return promptly when the turn is cancelled."""
        if self._turn_cancel is None:
            return self._invoke_tool_executor(name, tool_def, args, tool_call_id)

        with ThreadPoolExecutor(max_workers=1) as pool:
            # The tool runs in the turn's context (review 2026-10-24: a ContextVar
            # such as status_report's skills marker did not reach the worker).
            future = pool.submit(
                contextvars.copy_context().run,
                self._invoke_tool_executor,
                name,
                tool_def,
                args,
                tool_call_id,
            )
            while not future.done():
                self._check_turn_cancel()
                try:
                    return future.result(timeout=0.02)
                except FuturesTimeoutError:
                    continue
            return future.result()

    def _tool_parallel_max_workers(self) -> int:
        """Max concurrent tool workers; override with R4_COMPANION_TOOL_CONCURRENCY."""
        return _coerce_positive_int(
            os.environ.get("R4_COMPANION_TOOL_CONCURRENCY"),
            default=8,
        )

    def _parallel_eligible(self, item: "_PreparedToolCall") -> bool:
        """Whether one prepared call can join a parallel batch this round.

        Eligible: (a) tools flagged ``concurrency_safe`` on their definition,
        or (b) ``shell_exec`` whose command classifies read-only.
        """
        if item.tool_def is None:
            return False
        if item.tool_def.get("concurrency_safe"):
            return True
        name = item.tool_def.get("name", "")
        if name == "shell_exec":
            return _is_read_only_shell_command(str(item.args.get("command", "")))
        return False

    def _execute_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
    ) -> list[str]:
        """Execute one round of tool calls, preserving result order.

        Permission checks and pre-hooks run serially in the main thread so
        interactive prompts stay ordered. Consecutive calls whose tool is
        flagged ``concurrency_safe`` run in a bounded thread pool (openclaude
        ``isConcurrencySafe`` model); everything else executes serially in
        source order. Results line up with ``tool_calls`` by index.

        Every call this round started ends: a call cut short by a cancel or
        an error gets its end event before the exception leaves (L1).
        """
        try:
            return self._run_tool_round(tool_calls, available_tools)
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, TurnCancelledError) else "error"
            self._end_open_tool_calls(status, f"Tool call stopped: {type(exc).__name__}")
            raise

    def _run_tool_round(
        self,
        tool_calls: list[dict[str, Any]],
        available_tools: list[dict[str, Any]],
    ) -> list[str]:
        prepared = [self._prepare_tool_call(tc, available_tools) for tc in tool_calls]
        results: list[str] = [""] * len(prepared)
        index = 0
        count = len(prepared)
        while index < count:
            item = prepared[index]
            if not self._parallel_eligible(item):
                results[index] = self._finish_tool_call(item)
                index += 1
                continue
            batch_end = index + 1
            while batch_end < count and self._parallel_eligible(prepared[batch_end]):
                batch_end += 1
            batch = prepared[index:batch_end]
            if len(batch) == 1:
                results[index] = self._finish_tool_call(item)
            else:
                workers = min(self._tool_parallel_max_workers(), len(batch))
                runs = [(contextvars.copy_context(), item) for item in batch]
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    for offset, result in enumerate(pool.map(self._finish_in_context, runs)):
                        results[index + offset] = result
            index = batch_end
        return results

    def _finish_in_context(self, run: tuple[contextvars.Context, "_PreparedToolCall"]) -> str:
        """One parallel call, in a copy of the submitting turn's context."""
        context, item = run
        return context.run(self._finish_tool_call, item)

    def _execute_tool_call(
        self,
        tool_call: dict[str, Any],
        available_tools: list[dict[str, Any]],
    ) -> str:
        """Execute a single tool call, returning the result string."""
        return self._finish_tool_call(self._prepare_tool_call(tool_call, available_tools))

    def _prepare_tool_call(
        self,
        tool_call: dict[str, Any],
        available_tools: list[dict[str, Any]],
    ) -> "_PreparedToolCall":
        """Validate, hook, and permission-gate a tool call before execution.

        Runs serially in the main thread so prompts and pre-hook argument
        mutation stay deterministic. Returns a prepared call; on any early
        exit, ``error_result`` holds the final result string instead.
        """
        func = tool_call.get("function", {})
        name = func.get("name", "unknown")
        args_str = func.get("arguments", "{}")
        call_id = str(tool_call.get("id") or "")
        self._emit(
            CompanionEventType.TOOL_CALL,
            f"Calling tool: {name}",
            data={"name": name, "arguments": args_str, "tool_call_id": call_id},
        )
        self._check_turn_cancel()
        args = _repair_tool_arguments(args_str)
        if args is None:
            return _prepared_failure(
                name,
                {},
                call_id,
                "invalid_arguments",
                f"Error: invalid JSON arguments for {name}",
            )
        # A name this turn does not offer (made up, hidden after a denial, or
        # empty) fails before the permission gate: its approval card ran
        # nothing on "yes", and "always" granted the made-up name for the
        # session (companion sweep 2026-10-21).
        tool_def = next((tool for tool in available_tools if tool["name"] == name), None)
        if not tool_def:
            return _prepared_failure(
                name, args, call_id, "unknown_tool", f"Error: unknown tool '{name}'"
            )
        # Wave 80: a tool the array carries but this turn's mode refuses
        # (plan mode, write_requires_plan, a skill limit) fails here, before
        # any hook or approval card.
        refusal = getattr(self, "_turn_refusals", {}).get(name)
        if refusal:
            return _prepared_failure(
                name, args, call_id, "refused_this_turn", refusal_text(name, refusal)
            )

        # ── Pre-tool hooks (PreToolUse) ────────────────────────────────────
        pre_outcome = self._run_pre_tool_hooks(name, args)
        if isinstance(pre_outcome, _PreparedToolCall):
            return pre_outcome
        args = pre_outcome

        # ── Permission check ───────────────────────────────────────────���─
        denied = self._permission_gate(name, args, call_id=call_id)
        if denied is not None:
            return denied
        # ADR-108 W1c: in auto mode an approved writer first moves the
        # session into its worktree; the note leads that tool's result.
        return _PreparedToolCall(
            name=name,
            args=args,
            tool_def=tool_def,
            error_result=None,
            tool_call_id=call_id,
            note=_auto_worktree(name),
        )

    def _run_pre_tool_hooks(
        self, name: str, args: dict[str, Any]
    ) -> "_PreparedToolCall | dict[str, Any]":
        """PreToolUse hooks; returns a blocking PreparedToolCall or the
        (possibly MODIFY-mutated) args dict."""
        import r4.agent.hooks as tool_hooks

        hook_registry = tool_hooks._default_hook_registry()
        pre_result = hook_registry.run_pre_hooks(name, args)
        if pre_result.action == tool_hooks.HookAction.BLOCK:
            self._emit(
                CompanionEventType.TOOL_BLOCKED,
                f"Tool {name} blocked: {pre_result.reason}",
            )
            return _PreparedToolCall(
                name=name,
                args=args,
                tool_def=None,
                error_result=_tool_failure_text(
                    "blocked_hook",
                    name=name,
                    detail=f"Tool '{name}' was blocked by a pre-hook: {pre_result.reason}",
                ),
            )
        if pre_result.action == tool_hooks.HookAction.MODIFY and pre_result.modified_args:
            return pre_result.modified_args
        return args

    def _permission_gate(
        self, name: str, args: dict[str, Any], *, call_id: str = ""
    ) -> "_PreparedToolCall | None":
        """Return the blocked PreparedToolCall when policy denies the call.

        Fail-closed (credential-fence series, 2026-09-05): a companion whose
        PermissionManager was never wired cannot run tools at all — the old
        `if not self._permissions or check(...)` shape silently passed every
        tool through (fail-open). Wire the manager or the gate blocks.
        """
        if self._permissions is not None:
            self._permissions.set_taint_registry(self._taint_registry)
        if self._permissions is None:
            logger.error(
                "PermissionManager is not wired on this companion; denying tool %s "
                "(fail-closed — wire set_permissions or pass permission_manager)",
                name,
            )
            self._emit(
                CompanionEventType.TOOL_BLOCKED,
                f"Tool {name} blocked: no permission policy is active on this session",
            )
            return _PreparedToolCall(
                name=name,
                args=args,
                tool_def=None,
                error_result=_tool_failure_text(
                    "permission_unwired",
                    name=name,
                    detail=(
                        f"Tool {name} was blocked: the companion has no "
                        "PermissionManager wired (fail-closed)."
                    ),
                ),
            )
        _take_user_rejection(name)  # a stale mark never colours this check
        allowed = self._permissions.check(name, args)
        self._emit_permission_notes()
        if allowed:
            return None
        return self._denied_call(name, args, by_user=_take_user_rejection(name), call_id=call_id)

    def _emit_permission_notes(self) -> None:
        """Put in the transcript what autopilot let through or backed up for
        this call (ADR-104 amendment 2): a person is not asked, but sees it."""
        from .permissions import take_permission_notes

        for note in take_permission_notes():
            self._emit(CompanionEventType.THINKING, f"[autopilot] {note}")

    def _denied_call(
        self, name: str, args: dict[str, Any], *, by_user: bool, call_id: str = ""
    ) -> "_PreparedToolCall":
        """The blocked call: a person's "no" is named as theirs, never as a
        policy block (chat#8). The event names the call, so a console can
        close that call's row (it carried no data; companion sweep 2026-10-21)."""
        blocked = f"Tool {name} rejected by you" if by_user else f"Tool {name} blocked by policy"
        self._emit(
            CompanionEventType.TOOL_BLOCKED,
            blocked,
            data={"name": name, "tool_call_id": call_id},
        )
        # ADR-104 D-2: only a deny that holds whatever the arguments hides the
        # tool; a person's "no" or a fenced/argument-specific deny does not.
        if not by_user and self._denies_whole_tool(name) and name not in self._session_denied_tools:
            self._session_denied_tools.add(name)
            self._emit(
                CompanionEventType.THINKING,
                f"Tool {name} hidden for the rest of this session (permission denied once).",
            )
        kind, detail = (
            ("user_rejected", f"The user rejected the {name} call.")
            if by_user
            else ("permission_denied", f"Tool {name} was blocked by the permission policy.")
        )
        return _PreparedToolCall(
            name=name,
            args=args,
            tool_def=None,
            error_result=_tool_failure_text(kind, name=name, detail=detail),
        )

    def _denies_whole_tool(self, name: str) -> bool:
        """True when the permission policy denies every call of ``name``."""
        denies = getattr(self._permissions, "denies_tool", None)
        return bool(denies(name)) if callable(denies) else False

    def _finish_tool_call(self, prepared: "_PreparedToolCall") -> str:
        """Journal the terminal result, then return it (wrapper for the
        bounded result journal lookup used by the expand action)."""
        self._prime_job_watch(prepared.name)
        result = self._finish_tool_call_impl(prepared)
        # The call's arguments classify an exec result (a download is web
        # content, ADR-039); a dynamic mcp.<server>.<tool> name classifies by prefix.
        self._taint_registry.register(prepared.name, result, prepared.args)
        self._observe_submission(prepared.name, result)
        self._record_tool_result(prepared.tool_call_id, prepared.name, result)
        self._end_tool_call(prepared.tool_call_id, "error", result)
        return result

    def _end_tool_call(self, call_id: str, status: str, reason: str) -> None:
        """Close a started call that no result/block event closed (L1): an
        unknown tool, unparseable arguments, a hook block, an executor error.
        The end carries the failure reason; a call already ended is left."""
        with self._emit_lock:
            name = self._open_calls().get(call_id) if call_id else None
        if name is None:
            return
        self._emit(
            CompanionEventType.TOOL_RESULT,
            reason.split("\n", 1)[0][:200],
            data={"name": name, "tool_call_id": call_id, "status": status, "result": reason[:2000]},
        )

    def _end_open_tool_calls(self, status: str, reason: str) -> None:
        with self._emit_lock:
            open_ids = list(self._open_calls())
        for call_id in open_ids:
            self._end_tool_call(call_id, status, reason)

    def _finish_tool_call_impl(self, prepared: "_PreparedToolCall") -> str:
        """Execute a prepared tool call and apply post-hooks.

        Safe to call from worker threads for stateless read-only tools
        (HTTP clients are per-request; executor output handling is pure).
        """
        import r4.agent.hooks as tool_hooks

        if prepared.error_result is not None:
            return prepared.error_result
        if prepared.tool_def is None:
            return _tool_failure_text(
                "unknown_tool",
                name=prepared.name,
                detail=f"Error: unknown tool '{prepared.name}'",
            )

        # ADR-108 D-2: in a session worktree, writes stay inside it.
        with _worktree_guard():
            raw_output = self._invoke_tool_executor_cancellable(
                prepared.name, prepared.tool_def, prepared.args, prepared.tool_call_id
            )
        if prepared.note:
            raw_output = f"{prepared.note}\n{raw_output}"

        # ── Post-tool hooks (PostToolUse) ────────────────────────────────
        hook_registry = tool_hooks._default_hook_registry()
        post_result = hook_registry.run_post_hooks(prepared.name, prepared.args, raw_output)
        if post_result.suppress:
            self._emit(
                CompanionEventType.TOOL_RESULT,
                f"Tool {prepared.name} output suppressed: {post_result.reason}",
                data={"name": prepared.name, "suppressed": True},
            )
            return f"[output suppressed by post-hook: {post_result.reason}]"
        if post_result.modified_output is not None:
            return str(post_result.modified_output)

        return raw_output

    def _effective_model(self) -> str | None:
        """self.model, else the client config's model — configless clients tolerated."""
        if self.model:
            return self.model
        config = getattr(self.client, "_config", None)
        value = getattr(config, "model", None)
        return str(value) if value else None

    def _emit(
        self,
        event_type: CompanionEventType,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Emit an event to all listeners (thread-safe for parallel tools)."""
        event = CompanionEvent(event_type=event_type, message=message, data=data)
        with self._emit_lock:
            self._note_reply_text(event_type, message)
            self._track_tool_call(event_type, data)
            for cb in self._listeners:
                try:
                    cb(event)
                except Exception:
                    logger.debug("Event listener error", exc_info=True)

    def _track_tool_call(self, event_type: CompanionEventType, data: dict[str, Any] | None) -> None:
        """Open a call at its start event, close it at its end (caller holds the lock)."""
        call_id = str((data or {}).get("tool_call_id") or "")
        if not call_id:
            return
        open_calls = self._open_calls()
        if event_type is CompanionEventType.TOOL_CALL:
            open_calls[call_id] = str((data or {}).get("name") or "")
        elif event_type in (CompanionEventType.TOOL_RESULT, CompanionEventType.TOOL_BLOCKED):
            open_calls.pop(call_id, None)

    def _open_calls(self) -> dict[str, str]:
        """The open-call map (companions built without __init__ get one lazily)."""
        found = self.__dict__.get("_open_tool_calls")
        if found is None:
            found = self.__dict__["_open_tool_calls"] = {}
        return cast("dict[str, str]", found)

    def _note_reply_text(self, event_type: CompanionEventType, message: str) -> None:
        """Keep what the screen shows of this turn's reply: each round's
        streamed chunks (a round starts at ``RESPONSE_START``)."""
        if event_type is CompanionEventType.RESPONSE_START:
            self._turn_rounds.append("")
        elif event_type is CompanionEventType.RESPONSE_CHUNK and message:
            if not self._turn_rounds:
                self._turn_rounds.append("")
            self._turn_rounds[-1] += message

    def partial_reply(self) -> str:
        """The reply text this turn streamed so far, rounds joined as the TUI
        shows them. A stopped turn stores it before "(Generation stopped.)",
        so the model reads what the user read (stabilization sweep
        2026-10-21, w3-py#2: it got the marker alone)."""
        return "\n\n".join(text.strip() for text in self._turn_rounds if text.strip())

    def _subagent_model(self, model: str | None = None) -> str | None:
        """The model a sub-agent runs on: ``model``, else the platform's live
        ``fast`` role, else this companion's model (ADR-106 D-10)."""
        return model or _fast_role_model(self.client) or self.model

    def _subagent_label(self, model: str | None = None) -> str:
        """The sub-agent's model as the user sees it (role aliases name the
        backing model)."""
        from r4.service_roles import safe_model_label

        return safe_model_label(self._subagent_model(model) or "")

    def _new_subagent(self, model: str | None = None) -> "ResearchCompanion":
        """A sub-agent companion on ``model``, else the platform's live
        ``fast`` role (ADR-106 D-10: implicit, even when chat is on a public
        source), else this one's model. On the same model it inherits the
        limits the session synced to this companion (the served window,
        2026-10-30), which nothing else hands it."""
        sub = ResearchCompanion(
            self.client,
            model=self._subagent_model(model),
            temperature=self.temperature,
            max_tool_rounds=self.max_tool_rounds // 2,
            usage_callback=self._usage_callback,
            pre_llm_call=self._pre_llm_call,
            call_type="subagent",
        )
        if not model or model == self.model:
            sub.model_context_window = self.model_context_window
            sub.model_output_limit = self.model_output_limit
        return sub

    def _served_window(self, model: str) -> int | None:
        """The window an Ollama server serves ``model`` with, when the chat
        route goes straight to one; asked once per companion and model, so a
        sub-agent or team role that no session synced still never budgets
        from the trained window (2026-10-30)."""
        kept = self._served_window_memo
        if kept is not None and kept[0] == model:
            return kept[1]
        from .ollama_window import ollama_context_window

        window = ollama_context_window(self.client, model) if model else None
        self._served_window_memo = (model, window)
        return window

    def _context_window(self) -> int | None:
        """Model window in tokens: ``R4_MODEL_CONTEXT_WINDOW`` → published →
        served (Ollama) → name table."""
        from r4.model_context import known_context_length

        raw = os.environ.get("R4_MODEL_CONTEXT_WINDOW", "").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        published = getattr(self, "model_context_window", None)
        model = self._effective_model() or ""
        return published or self._served_window(model) or known_context_length(model)

    def _tool_history_budget_chars(self) -> int:
        """Tool-history size before older results are trimmed (W4): the smaller of
        25% of the window and 64k tokens (24k under the terminal profile, H11), in chars."""
        window = self._context_window() or 131_072
        cap = _PROFILE_TOOL_HISTORY_TOKENS if _terminal_profile() else _DEFAULT_TOOL_HISTORY_TOKENS
        return int(min(window * 0.25, cap) * CHARS_PER_TOKEN)

    def _bounded_history(self, filtered: list[dict[str, Any]]) -> list[dict[str, Any]]:
        bounded = _system_nudges_as_user(filtered)
        bounded = _bound_tool_result_history(bounded, self._tool_history_budget_chars())
        keep_rounds, arg_limit = _arg_bounds()
        bounded = _bound_tool_call_args(
            bounded, keep_rounds, limit=_env_chars("R4_COMPANION_ARG_MAX_CHARS", arg_limit)
        )
        bounded = _compact_old_rounds(
            bounded,
            _env_chars("R4_COMPANION_HISTORY_FULL_ROUNDS", _HISTORY_FULL_ROUNDS),
            _env_chars("R4_COMPANION_OLD_ROUND_CHARS", _OLD_ROUND_CHARS),
        )
        return _bound_reasoning_history(
            bounded,
            _reasoning_keep(),
            _env_chars("R4_COMPANION_REASONING_MAX_CHARS", _REASONING_REPLAY_MAX_CHARS),
        )

    def _requested_output_tokens(self, model: str) -> int | None:
        """``R4_MAX_OUTPUT_TOKENS`` (0 = never send) → published limit → family default."""
        from r4.model_context import default_max_output_tokens

        raw = os.environ.get("R4_MAX_OUTPUT_TOKENS", "").strip()
        if not raw:
            # G19a: an escalated override wins over the family default; the env
            # pin above stays authoritative (no escalation under a user pin).
            return (
                self._output_token_override
                or getattr(self, "model_output_limit", None)
                or default_max_output_tokens(model)
            )
        try:
            wanted = int(raw)
        except ValueError:
            return None
        return wanted if wanted > 0 else None

    def _output_token_limit(self, messages: list[dict[str, Any]]) -> int | None:
        """ADR-087 output limit for one request; ``None`` = leave the server default.

        Clamped to the context window left after the prompt (published
        ``model_context_window`` or the name table); below 4,096 tokens of
        room nothing is sent.
        """
        model = self._effective_model() or ""
        wanted = self._requested_output_tokens(model)
        if not wanted:
            return None
        window = self._context_window()
        if not window:
            return int(wanted)
        prompt = self._estimate_tokens(json.dumps(messages, ensure_ascii=False, default=str))
        room = int(window) - prompt - 1_024
        return min(int(wanted), room) if room >= 4_096 else None

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, int(len(text) / CHARS_PER_TOKEN))

    @staticmethod
    def _normalize_token_count(value: Any) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) else 0
