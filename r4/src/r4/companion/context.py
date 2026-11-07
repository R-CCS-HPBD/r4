"""Context management — token tracking, compaction, memory, and RAG.

The ContextManager is the central authority for what enters the LLM's
context window on each turn. It implements:
- Message history with estimated token tracking
- Compaction (summarise old messages when nearing the window limit)
- Saved memory (ADR-111: one private file per fact, ``memory_store``; the
  turn carries a capped index, ``memory_read`` shows a fact's body)
- RAG integration (auto-query when relevant)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .cancellation import TurnCancelledError
from .memory_store import (
    DESCRIPTION_MAX,
    MemoryFact,
    MemoryStore,
    slugify,
    store_for,
)

if TYPE_CHECKING:
    from r4.client import Client

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = (
    "Summarise the following conversation for an AI assistant that needs "
    "to continue helping the user. Preserve: key decisions, file paths, "
    "job IDs, error messages, and next steps. Be concise.\n\n"
)

# Rough chars-per-token estimate (conservative for English + code)
CHARS_PER_TOKEN = 3.5
DEFAULT_MAX_TOKENS = 120_000  # Conservative limit for most models
DEFAULT_RESPONSE_RESERVE_TOKENS = 4_096

# G10 (2026-10-28): measured-usage calibration of the chars-per-token ratio.
# Every provider reply that carries usage.prompt_tokens re-anchors the ratio
# the estimators below divide by, so JSON-dense agentic loops (tool calls and
# schemas tokenize near 2-2.5 chars/token, not 3.5) stop under-counting the
# payload that fires — or just misses — the compaction threshold.
_RATIO_SAMPLE_MIN = 0.5  # chars/token sanity band; outside it the sample is noise
_RATIO_SAMPLE_MAX = 16.0
_RATIO_FLOOR = 1.5  # calibrated ratio never leaves [floor, ceiling]
_RATIO_CEILING = 8.0
_RATIO_SMOOTHING = 0.4  # EMA weight of each new measured sample


def _env_chars(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return max(512, int(raw)) if raw else default
    except ValueError:
        logger.warning("%s=%r is not an int", name, raw)
        return default


def _initial_chars_per_token() -> float:
    """The starting ratio: ``R4_COMPANION_CHARS_PER_TOKEN`` pin, else the default."""
    raw = os.environ.get("R4_COMPANION_CHARS_PER_TOKEN", "").strip()
    if not raw:
        return CHARS_PER_TOKEN
    try:
        return float(raw)
    except ValueError:
        logger.warning("R4_COMPANION_CHARS_PER_TOKEN=%r is not a float", raw)
        return CHARS_PER_TOKEN


def _summary_message(summary: str) -> dict[str, Any]:
    """The message a compaction summary replaces the older history with."""
    return {"role": "system", "content": f"[Conversation summary]\n{summary}"}


def _standalone_summary_request(
    client: Any, summary_prompt: str
) -> Callable[[list[dict[str, Any]]], Any]:
    """The pre-wave-80 summary call: its own system prompt and a text digest."""
    return lambda _messages: client.chat(
        summary_prompt,
        system="You are a conversation summariser. Output a concise summary.",
        temperature=0.2,
    )


@dataclass
class BudgetBreakdown:
    """Estimated token usage for a fully assembled outbound prompt."""

    system_tokens: int
    extra_system_tokens: int
    history_tokens: int
    user_tokens: int
    tool_tokens: int
    reserve_tokens: int
    available_history_tokens: int
    total_prompt_tokens: int
    total_with_reserve_tokens: int
    exceeded: bool
    hard_limit_exceeded: bool


class ContextManager:
    """Manages the context budget for each LLM turn."""

    def __init__(
        self,
        client: "Client | None" = None,
        max_context_tokens: int = DEFAULT_MAX_TOKENS,
        compaction_threshold: float = 0.80,
        response_reserve_tokens: int = DEFAULT_RESPONSE_RESERVE_TOKENS,
        usage_callback: Callable[[str, dict[str, Any]], None] | None = None,
        pre_llm_call: Callable[[str], None] | None = None,
    ):
        self.client = client
        self.max_context_tokens = max_context_tokens
        # Env overlay (T5 topic-20 tail): latency-calibrated sessions may want
        # an earlier fold (0.60-0.70) so context strip fires before tools pile
        # in; default keeps the library-pinned 0.80 policy shape unchanged.
        env_threshold = os.environ.get("R4_COMPANION_COMPACTION_THRESHOLD")
        if env_threshold:
            try:
                candidate = float(env_threshold)
                if 0.0 < candidate < 1.0:
                    compaction_threshold = candidate
            except ValueError:
                logger.warning("R4_COMPANION_COMPACTION_THRESHOLD=%r is not a float", env_threshold)
        self.compaction_threshold = compaction_threshold
        self.response_reserve_tokens = response_reserve_tokens
        self._usage_callback = usage_callback
        self._pre_llm_call = pre_llm_call

        # Saved memory (ADR-111) lives in files read per call; the session
        # sets ``workspace`` (default: cwd) for the project-less scope key.
        self.workspace: Path | None = None

        # Session-level accumulated context (reset per session)
        self._session_context: dict[str, str] = {}

        # Wave 80: the last turn compaction — the history messages its summary
        # replaced and the summary message — reused while that history holds.
        self._sticky_compaction: tuple[list[dict[str, Any]], dict[str, Any]] | None = None

        # G10: measured-usage calibration (pinned ratios skip calibration).
        self._chars_per_token = _initial_chars_per_token()
        self._ratio_pinned = bool(os.environ.get("R4_COMPANION_CHARS_PER_TOKEN", "").strip())
        # G13: user hook commands fired before an LLM compaction summary
        # (``companion.user_hooks.UserHooks``; None when unwired).
        self.user_hooks: Any = None
        # G12: consecutive LLM-summary failures; the breaker stops burning
        # calls on a dying summariser and falls to the deterministic digest.
        self._compaction_failures = 0

    # -- G12 pressure levels + compaction breaker ---------------------------

    def pressure_level(self, messages: list[dict[str, Any]]) -> int:
        """0 normal | 1 warning | 2 at/above compaction threshold | 3 blocking.

        Levels are window ratios (0.65 / threshold / 0.95); status surfaces
        and the TUI read this without re-deriving the estimate (G12).
        """
        used = self.estimate_messages_tokens(messages)
        ratio = used / max(1, self.max_context_tokens)
        if ratio >= 0.95:
            return 3
        if ratio >= self.compaction_threshold:
            return 2
        return 1 if ratio >= 0.65 else 0

    def _compaction_breaker_limit(self) -> int:
        raw = os.environ.get("R4_COMPANION_COMPACTION_BREAKER", "").strip()
        try:
            return max(1, int(raw)) if raw else 3
        except ValueError:
            logger.warning("R4_COMPANION_COMPACTION_BREAKER=%r is not an int", raw)
            return 3

    def _compaction_breaker_tripped(self) -> bool:
        if self._compaction_failures < self._compaction_breaker_limit():
            return False
        logger.warning(
            "Compaction breaker: %d consecutive summary failures — using the "
            "deterministic digest until the next success",
            self._compaction_failures,
        )
        return True

    # -- G11 microcompact ----------------------------------------------------

    def microcompact(
        self, messages: list[dict[str, Any]], *, keep_recent: int = 6
    ) -> tuple[list[dict[str, Any]], int]:
        """Bound stale oversized message bodies below the LLM-compaction layer.

        Older (pre ``keep_recent``) messages whose content exceeds the limit
        are head-capped with a marker; already-small messages pass through.
        Deterministic, so repeating it turn after turn yields the same list
        (prefix-cache friendly, unlike a fresh LLM summary). Returns
        ``(messages, chars_removed)``; ``(messages, 0)`` when nothing
        qualified or ``R4_COMPANION_MICROCOMPACT=0``.
        """
        if os.environ.get("R4_COMPANION_MICROCOMPACT", "1").strip() in ("0", "false", "off"):
            return messages, 0
        limit = _env_chars("R4_COMPANION_MICROCOMPACT_CHARS", 4_000)
        head = max(1, limit // 3)
        bounded: list[dict[str, Any]] = []
        removed_total = 0
        cutoff = max(0, len(messages) - keep_recent)
        for index, message in enumerate(messages):
            bounded_message, removed = self._bound_one(message, index < cutoff, head)
            bounded.append(bounded_message)
            removed_total += removed
        return bounded, removed_total

    @staticmethod
    def _bound_one(message: dict[str, Any], stale: bool, head: int) -> tuple[dict[str, Any], int]:
        content = message.get("content")
        if not stale or not isinstance(content, str) or len(content) <= head * 3:
            return message, 0
        removed = len(content) - head
        marker = (
            f"\n\n[microcompact: {removed:,} chars of this earlier "
            f"{message.get('role', '?')} message elided to ease context pressure]"
        )
        return {**message, "content": content[:head].rstrip() + marker}, removed

    def _fire_pre_compact_hooks(self, messages: list[dict[str, Any]]) -> None:
        """G13 PreCompact: the older messages about to be summarized (archive lane)."""
        hooks = self.user_hooks
        if hooks is None or not hooks.any("PreCompact"):
            return
        payload = {
            "older_message_count": len(messages),
            "older_messages_text": self._build_summary_input(messages),
        }
        try:
            hooks.run("PreCompact", payload)
        except Exception:
            logger.warning("PreCompact hook dispatch failed — continuing compaction", exc_info=True)

    # -- token estimation ---------------------------------------------------

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count from character length (measured-calibrated, G10)."""
        return max(1, int(len(text) / self._chars_per_token))

    def observe_prompt_usage(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        prompt_tokens: int,
    ) -> None:
        """Re-anchor the chars-per-token ratio on one measured provider count.

        ``prompt_tokens`` is the provider's count for exactly ``messages`` +
        ``tools``; the implied ratio folds into the running estimate (EMA).
        Out-of-band samples and pinned ratios are ignored.
        """
        if self._ratio_pinned or prompt_tokens <= 0:
            return
        counted = self._counted_chars(messages, tools)
        if counted <= 0:
            return
        sample = counted / prompt_tokens
        if not _RATIO_SAMPLE_MIN <= sample <= _RATIO_SAMPLE_MAX:
            return
        updated = (1 - _RATIO_SMOOTHING) * self._chars_per_token + _RATIO_SMOOTHING * sample
        updated = min(_RATIO_CEILING, max(_RATIO_FLOOR, updated))
        if abs(updated - self._chars_per_token) / self._chars_per_token > 0.05:
            logger.debug(
                "chars-per-token calibrated %.2f -> %.2f (measured sample %.2f)",
                self._chars_per_token,
                updated,
                sample,
            )
        self._chars_per_token = updated

    def _counted_chars(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> int:
        """The character mass the ratio is measured against (messages + schemas)."""
        total = 0
        for m in messages:
            content = m.get("content") or ""
            if not isinstance(content, str):
                content = json.dumps(content, sort_keys=True, default=str)
            total += len(content) + 4
            if m.get("tool_calls"):
                total += len(json.dumps(m["tool_calls"], sort_keys=True, default=str))
            if m.get("reasoning_content"):
                total += len(str(m["reasoning_content"]))
        if tools:
            total += len(json.dumps(tools, sort_keys=True, default=str))
        return total

    def estimate_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Estimate total tokens for a message list.

        Counts the full wire shape — content, ``tool_calls`` arguments, and
        ``reasoning_content`` echoes. Counting content alone under-estimates
        tool-heavy agentic rounds by orders of magnitude (one file_edit call
        carries KBs of JSON arguments; reasoning echoes add thousands more),
        which left budget fitting and compaction decisions blind until the
        provider rejected the request with a context-overflow error.
        """
        total = 0
        for m in messages:
            content = m.get("content") or ""
            if not isinstance(content, str):
                # Multimodal part lists — estimate from the serialized form.
                content = json.dumps(content, sort_keys=True)
            total += self.estimate_tokens(content) + 4  # role + separators
            tool_calls = m.get("tool_calls")
            if tool_calls:
                total += self.estimate_tokens(json.dumps(tool_calls, sort_keys=True))
            reasoning = m.get("reasoning_content")
            if reasoning:
                total += self.estimate_tokens(str(reasoning))
        return total

    def estimate_tool_tokens(self, tools: list[dict[str, Any]] | None) -> int:
        """Estimate token overhead for tool schemas."""
        if not tools:
            return 0

        serializable_tools = []
        for tool in tools:
            serializable_tools.append(
                {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {}),
                }
            )
        return self.estimate_tokens(json.dumps(serializable_tools, sort_keys=True))

    def estimate_payload_budget(
        self,
        *,
        system_prompt: str,
        extra_system: str,
        history: list[dict[str, Any]],
        user_message: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> BudgetBreakdown:
        """Estimate token usage for the fully assembled outbound payload."""
        system_tokens = self.estimate_tokens(system_prompt)
        extra_system_tokens = self.estimate_tokens(extra_system) if extra_system else 0
        history_tokens = self.estimate_messages_tokens(history)
        user_tokens = self.estimate_tokens(user_message)
        tool_tokens = self.estimate_tool_tokens(tools)
        total_prompt_tokens = (
            system_tokens + extra_system_tokens + history_tokens + user_tokens + tool_tokens
        )
        total_with_reserve_tokens = total_prompt_tokens + self.response_reserve_tokens
        available_history_tokens = max(
            0,
            self.max_context_tokens
            - self.response_reserve_tokens
            - system_tokens
            - extra_system_tokens
            - user_tokens
            - tool_tokens,
        )
        threshold_limit = int(self.max_context_tokens * self.compaction_threshold)

        return BudgetBreakdown(
            system_tokens=system_tokens,
            extra_system_tokens=extra_system_tokens,
            history_tokens=history_tokens,
            user_tokens=user_tokens,
            tool_tokens=tool_tokens,
            reserve_tokens=self.response_reserve_tokens,
            available_history_tokens=available_history_tokens,
            total_prompt_tokens=total_prompt_tokens,
            total_with_reserve_tokens=total_with_reserve_tokens,
            exceeded=total_with_reserve_tokens >= threshold_limit,
            hard_limit_exceeded=total_with_reserve_tokens > self.max_context_tokens,
        )

    def fit_payload(
        self,
        *,
        system_prompt: str,
        extra_system: str,
        history: list[dict[str, Any]],
        user_message: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], BudgetBreakdown]:
        """Trim oversized prompt components to fit within the hard context budget."""
        fitted_tools = list(tools or [])
        breakdown = self.estimate_payload_budget(
            system_prompt=system_prompt,
            extra_system=extra_system,
            history=history,
            user_message=user_message,
            tools=fitted_tools,
        )
        if not breakdown.hard_limit_exceeded:
            return extra_system, history, fitted_tools, breakdown

        fixed_without_extra = (
            breakdown.system_tokens
            + breakdown.user_tokens
            + breakdown.tool_tokens
            + breakdown.reserve_tokens
        )
        max_extra_tokens = max(0, self.max_context_tokens - fixed_without_extra)
        if breakdown.extra_system_tokens > max_extra_tokens:
            extra_system = self._truncate_text_to_tokens(extra_system, max_extra_tokens)

        breakdown = self.estimate_payload_budget(
            system_prompt=system_prompt,
            extra_system=extra_system,
            history=history,
            user_message=user_message,
            tools=fitted_tools,
        )
        if breakdown.hard_limit_exceeded and fitted_tools:
            max_tool_tokens = max(
                0,
                self.max_context_tokens
                - breakdown.system_tokens
                - breakdown.extra_system_tokens
                - breakdown.history_tokens
                - breakdown.user_tokens
                - breakdown.reserve_tokens,
            )
            fitted_tools = self._trim_tools_to_budget(fitted_tools, max_tool_tokens)
            breakdown = self.estimate_payload_budget(
                system_prompt=system_prompt,
                extra_system=extra_system,
                history=history,
                user_message=user_message,
                tools=fitted_tools,
            )
        if breakdown.history_tokens > breakdown.available_history_tokens:
            history = self._trim_history_to_budget(history, breakdown.available_history_tokens)
            breakdown = self.estimate_payload_budget(
                system_prompt=system_prompt,
                extra_system=extra_system,
                history=history,
                user_message=user_message,
                tools=fitted_tools,
            )

        return extra_system, history, fitted_tools, breakdown

    # -- compaction ----------------------------------------------------------

    def needs_compaction(self, messages: list[dict[str, Any]]) -> bool:
        """True if the messages are approaching the context limit."""
        used = self.estimate_messages_tokens(messages)
        return used >= int(self.max_context_tokens * self.compaction_threshold)

    def needs_compaction_for_payload(
        self,
        *,
        system_prompt: str,
        extra_system: str,
        history: list[dict[str, Any]],
        user_message: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> bool:
        """True if the fully assembled payload is approaching the context limit."""
        return self.estimate_payload_budget(
            system_prompt=system_prompt,
            extra_system=extra_system,
            history=history,
            user_message=user_message,
            tools=tools,
        ).exceeded

    def compact(
        self,
        messages: list[dict[str, Any]],
        keep_recent: int = 6,
    ) -> list[dict[str, Any]]:
        """Compact older messages into a summary, keeping recent ones.

        Uses the LLM to summarise the older portion of the conversation,
        then returns ``[system, summary_msg, ...recent_msgs]``.
        """
        if len(messages) <= keep_recent + 1:
            return messages  # nothing to compact

        system_msgs = [m for m in messages if m["role"] == "system"]
        non_system = [m for m in messages if m["role"] != "system"]

        old_messages = non_system[:-keep_recent]
        recent_messages = non_system[-keep_recent:]

        summary_msg = _summary_message(self._summarise(old_messages))

        return system_msgs + [summary_msg] + recent_messages

    def reuse_compaction(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``history`` with the last turn compaction applied while it holds.

        Wave 80: re-summarising every turn produced a new summary (and a new
        prompt prefix) each time; the same summary keeps the prefix — and the
        server's prefix cache — valid until the next compaction.
        """
        if self._sticky_compaction is None:
            return history
        covered, summary_msg = self._sticky_compaction
        if not covered or history[: len(covered)] != covered:
            return history
        return [summary_msg, *history[len(covered) :]]

    def compact_turn(
        self,
        history: list[dict[str, Any]],
        *,
        request: Callable[[list[dict[str, Any]]], Any] | None = None,
        keep_recent: int = 6,
    ) -> list[dict[str, Any]]:
        """Compact a turn's history: the older part (with any earlier summary)
        becomes one summary message, the latest ``keep_recent`` stay.

        ``request`` sends the summary call on the turn's own prefix (wave 80;
        the agent's ``compaction_request``); without it the stand-alone
        summariser runs.
        """
        reused = self.reuse_compaction(history)
        if len(reused) <= keep_recent + 1:
            return reused
        older = reused[:-keep_recent]
        if self._compaction_breaker_tripped():
            summary = None
        else:
            summary = self._llm_summary(older, request=request)
        if summary is None:  # a fallback digest is never reused (review (80))
            summary_msg = _summary_message(self._summarise_truncated_fallback(older))
        else:
            summary_msg = _summary_message(summary)
            self._sticky_compaction = (list(history[:-keep_recent]), summary_msg)
        return [summary_msg, *reused[-keep_recent:]]

    @staticmethod
    def _summarise_truncated_fallback(messages: list[dict[str, Any]]) -> str:
        """Last-resort compaction input when the LLM summariser is unavailable."""
        texts = [f"{m['role']}: {(m.get('content') or '')[:200]}" for m in messages[-10:]]
        return "Previous conversation:\n" + "\n".join(texts)

    def _build_summary_input(self, messages: list[dict[str, Any]]) -> str:
        """Budget-bounded compaction input text from message history."""
        lines: list[str] = []
        max_chars = max(4_000, int(self.max_context_tokens * CHARS_PER_TOKEN * 0.35))
        used = 0
        for message in messages:
            snippet = f"{message['role']}: {(message.get('content') or '')[:500]}"
            if used and used + len(snippet) > max_chars:
                lines.append("…[older messages omitted from compaction input]")
                break
            lines.append(snippet)
            used += len(snippet) + 1
        return "\n".join(lines)

    def _record_compaction_usage(self, response: Any, summary_prompt: str) -> None:
        """Record token usage for the compaction call (estimating when zero)."""
        prompt_tokens = self._normalize_token_count(getattr(response.usage, "prompt_tokens", 0))
        completion_tokens = self._normalize_token_count(
            getattr(response.usage, "completion_tokens", 0)
        )
        total_tokens = self._normalize_token_count(getattr(response.usage, "total_tokens", 0))
        estimated = total_tokens <= 0
        if estimated:
            prompt_tokens = self.estimate_tokens(summary_prompt)
            completion_tokens = self.estimate_tokens(response.content)
            total_tokens = prompt_tokens + completion_tokens
        self._usage_callback(
            "compaction",
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "estimated": estimated,
                "model": getattr(response, "model", ""),
            },
        )

    def _summarise(
        self,
        messages: list[dict[str, Any]],
        *,
        request: Callable[[list[dict[str, Any]]], Any] | None = None,
    ) -> str:
        """Ask the LLM to summarise a set of messages (the truncated digest
        when it cannot)."""
        summary = self._llm_summary(messages, request=request)
        return self._summarise_truncated_fallback(messages) if summary is None else summary

    def _llm_summary(
        self,
        messages: list[dict[str, Any]],
        *,
        request: Callable[[list[dict[str, Any]]], Any] | None = None,
    ) -> str | None:
        """The LLM's summary of ``messages``, or None when no LLM is reachable
        or the call fails. A stopped turn raises ``TurnCancelledError``.

        With ``request`` (wave 80) the messages go as they are, on the turn's
        prefix; otherwise a stand-alone prompt carries a bounded text digest.
        """
        if request is None and not self.client:
            return None
        self._fire_pre_compact_hooks(messages)
        request, summary_prompt = self._summary_call(messages, request)
        if self._pre_llm_call:
            self._pre_llm_call("compaction")
        try:
            response = request(messages)
            if self._usage_callback:
                self._record_compaction_usage(response, summary_prompt)
            content: str | None = response.content
            self._compaction_failures = 0
            return content
        except TurnCancelledError:
            raise
        except Exception as e:
            self._compaction_failures += 1  # G12 breaker counts this streak
            logger.warning("Compaction LLM call failed: %s", e)
            return None

    def _summary_call(
        self,
        messages: list[dict[str, Any]],
        request: Callable[[list[dict[str, Any]]], Any] | None,
    ) -> tuple[Callable[[list[dict[str, Any]]], Any], str]:
        """The summary call and the prompt text its usage estimate counts."""
        if request is not None:
            return request, "\n".join(str(m.get("content") or "") for m in messages)
        summary_prompt = SUMMARY_PROMPT + self._build_summary_input(messages)
        return _standalone_summary_request(self.client, summary_prompt), summary_prompt

    # -- saved memory (ADR-111) ---------------------------------------------

    def memory_store(self) -> MemoryStore:
        """The user scope plus this session's project scope, resolved now:
        a project switch reaches the next call."""
        return store_for(self.client, workspace=self.workspace)

    def remember(
        self, key: str, value: str, source: str = "user", *, scope: str = "project"
    ) -> MemoryFact:
        """Save ``value`` under ``key`` (replacing it): the Jupyter magic's
        call. Raises ``MemoryStoreError`` for a bad name or a secret-shaped text."""
        fact = MemoryFact(
            name=slugify(key),
            type="project",
            description=" ".join(f"{key}: {value}".split())[:DESCRIPTION_MAX],
            body=value,
            scope=scope,
            source=source,
        )
        return self.memory_store().write(fact, replace_existing=True)

    def forget(self, key: str) -> bool:
        """Delete ``key`` from the project scope, else the user scope."""
        store = self.memory_store()
        name = slugify(key)
        return store.forget(name, "project") or store.forget(name, "user")

    def get_memory(self, key: str) -> str | None:
        """The body saved under ``key`` (project scope first)."""
        fact = self.memory_store().find(slugify(key))
        return fact.body if fact else None

    def list_memories(self) -> list[MemoryFact]:
        """Every saved fact, user scope first."""
        return self.memory_store().facts()

    def memory_as_context(self) -> str:
        """The per-turn memory block: a capped index, never the bodies."""
        try:
            return self.memory_store().index_prompt()
        except (OSError, ValueError) as exc:
            logger.warning("Saved memory unreadable: %s", exc)
            return ""

    # -- session context -----------------------------------------------------

    def set_session_context(self, key: str, value: str) -> None:
        """Store a session-scoped context fact (not persisted)."""
        self._session_context[key] = value

    def session_context_as_prompt(self) -> str:
        """Format session context as a system prompt fragment."""
        if not self._session_context:
            return ""
        lines = ["[Session context]"]
        for k, v in sorted(self._session_context.items()):
            lines.append(f"- {k}: {v}")
        return "\n".join(lines)

    def clear_session_context(self) -> None:
        """Reset session context."""
        self._session_context.clear()

    @staticmethod
    def _normalize_token_count(value: Any) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def _trim_history_to_budget(
        self,
        history: list[dict[str, Any]],
        max_history_tokens: int,
    ) -> list[dict[str, Any]]:
        """Keep the newest messages that fit inside the history budget."""
        if max_history_tokens <= 0:
            return []

        kept: list[dict[str, Any]] = []
        used = 0
        for message in reversed(history):
            message_tokens = self.estimate_messages_tokens([message])
            if kept and used + message_tokens > max_history_tokens:
                break
            if not kept and message_tokens > max_history_tokens:
                continue
            kept.append(message)
            used += message_tokens
        kept.reverse()
        return kept

    def _trim_tools_to_budget(
        self,
        tools: list[dict[str, Any]],
        max_tool_tokens: int,
    ) -> list[dict[str, Any]]:
        """Keep the earliest tools that fit inside the remaining tool budget."""
        if max_tool_tokens <= 0:
            return []

        kept: list[dict[str, Any]] = []
        used = 0
        for tool in tools:
            tool_tokens = self.estimate_tool_tokens([tool])
            if tool_tokens > max_tool_tokens:
                continue
            if used + tool_tokens > max_tool_tokens:
                continue
            kept.append(tool)
            used += tool_tokens
        return kept

    def _truncate_text_to_tokens(self, text: str, max_tokens: int) -> str:
        """Truncate text to an approximate token budget and mark truncation."""
        if not text or max_tokens <= 0:
            return ""

        if self.estimate_tokens(text) <= max_tokens:
            return text

        max_chars = max(0, int(max_tokens * self._chars_per_token))
        if max_chars <= 0:
            return ""

        truncated = text[:max_chars].rstrip()
        suffix = "\n\n[truncated to fit context budget]"
        while truncated and self.estimate_tokens(truncated + suffix) > max_tokens:
            truncated = truncated[:-32].rstrip()
        return (truncated + suffix) if truncated else ""
