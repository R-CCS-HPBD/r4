"""Agent session management for multi-turn conversations.

Manages persistent conversation state, context, and memory
across multiple interactions with the SciWorkflowAgent.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast
from urllib.error import HTTPError

from .core import (
    R4_USAGE_GUIDE,
    AgentCancellation,
    AgentCancelledError,
    AgentConfig,
    AgentEvent,
    AgentEventType,
    SciWorkflowAgent,
)
from .egress import TrustSession
from .models import WorkflowDefinition, WorkflowExecution

# ── KNOWN_R4_TOOLS fallback (same intent as the extension's KNOWN_R4_TOOLS) ──
_KNOWN_R4_TOOLS_FALLBACK: list[dict[str, str]] = [
    {"name": "health", "description": "Check R4 platform health status"},
    {"name": "model_list", "description": "List available models in the registry"},
    {"name": "endpoint_list", "description": "List active inference endpoints"},
    {"name": "endpoint_up", "description": "Provision a new inference endpoint"},
    {"name": "endpoint_down", "description": "Deprovision an existing endpoint"},
    {"name": "job_list", "description": "List recent jobs and their status"},
    {"name": "workflow_list", "description": "List registered workflows"},
]


def _tool_failure_text(name: str, exc: Exception) -> str:
    """Format a tool failure so the model can recover instead of the loop dying."""
    return (
        f"❌ Tool `{name}` failed: {exc}\n\n"
        "*The tool execution errored, but the agent loop continues. "
        "The model can adjust arguments or try a different approach.*"
    )


def _normalize_mcp_content(content: Any) -> str:
    """Render an MCP tool result payload as plain text for the agent loop."""
    if isinstance(content, list):
        text_parts: list[str] = []
        for c in content:
            if isinstance(c, dict):
                text_parts.append(c.get("text", json.dumps(c)))
            elif hasattr(c, "text"):
                text_parts.append(str(c.text))
            else:
                text_parts.append(str(c))
        return "\n".join(text_parts)
    if isinstance(content, str):
        return content
    return json.dumps(content, default=str)


class _RawChatMessage:
    """Passthrough wrapper so raw message dicts survive ``Client.chat_completion``.

    ``ChatMessage.to_dict()`` only emits ``role``/``content``/``name``, which
    silently drops ``tool_calls`` and ``tool_call_id``.  The agentic loop needs
    those fields to round-trip tool results back to the model.
    """

    __slots__ = ("_payload",)

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    @property
    def role(self) -> str:
        return str(self._payload.get("role", "user"))

    @property
    def content(self) -> Any:
        return self._payload.get("content")

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


def _extract_tool_calls(raw_response: Any) -> list[dict[str, Any]]:
    """Pull tool calls out of an OpenAI-shaped completion payload.

    Normalizes to the flat ``{"id", "name", "arguments"}`` shape the agentic
    loop expects. Returns an empty list when the payload has no tool calls.
    """
    if not isinstance(raw_response, dict):
        return []

    choices = raw_response.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return []

    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        return []

    raw_calls = message.get("tool_calls") or []
    valid_calls = filter(lambda call: isinstance(call, dict), raw_calls)
    return [_normalize_tool_call(call) for call in valid_calls]


def _normalize_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    """Normalize one OpenAI tool-call entry to the agentic loop's flat shape."""
    function = call.get("function") or {}
    return {
        "id": call.get("id", ""),
        "name": function.get("name", ""),
        "arguments": function.get("arguments", "{}") or "{}",
    }


def _parse_llm_response(response: Any) -> tuple[str, list[dict[str, Any]]]:
    """Extract (content, tool_calls) from a dict- or object-shaped LLM reply."""
    if isinstance(response, dict):
        return response.get("content", "") or "", response.get("tool_calls", []) or []
    if hasattr(response, "content"):
        return (
            getattr(response, "content", "") or "",
            getattr(response, "tool_calls", []) or [],
        )
    return "", []


def _capture_usage(response: Any, prior: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep the latest non-empty usage payload seen across agentic iterations."""
    usage = getattr(response, "usage", None)
    if usage:
        return usage
    if isinstance(response, dict) and response.get("usage"):
        return response["usage"]
    return prior


if TYPE_CHECKING:
    from r4.client import Client

logger = logging.getLogger(__name__)


def _session_path(session_id: str) -> Path:
    """Return the storage path for a canonical UUID session identifier."""
    try:
        canonical_id = str(uuid.UUID(session_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Invalid session ID") from exc
    if canonical_id != session_id:
        raise ValueError("Invalid session ID")
    return SESSIONS_DIR / f"{canonical_id}.json"


# Session storage location
SESSIONS_DIR = Path(
    os.environ.get("R4_AGENT_SESSIONS_DIR")
    or os.environ.get("R4_AGENT_SESSIONS_DIR")
    or os.path.expanduser("~/.config/r4/agent_sessions")
)


# Matches auto-generated session names so we can replace them with a title
# derived from the first user message (Copilot-style).
_AUTO_NAME_RE = re.compile(r"^(Session|R4 Agent)\b")
_TITLE_MAX_LEN = 48


def _is_auto_name(name: str) -> bool:
    """Return True if the name was machine-generated (datetime placeholder)."""
    return bool(_AUTO_NAME_RE.match((name or "").strip()))


# The TUI relays `!cmd` output as a user turn starting with this line
# (packages/r4-tui-v3/src/lib/local-shell.ts); it is not the user's question.
_SHELL_RELAY_RE = re.compile(r"^I ran `!.*` in the TUI's local shell — ")


def _is_shell_relay(message: str) -> bool:
    """Return True for a TUI `!cmd` output relay (machine text, not a prompt)."""
    return bool(_SHELL_RELAY_RE.match(message or ""))


def _should_auto_title(state: SessionState, message: str) -> bool:
    """Name a placeholder-named session after its first REAL user prompt.

    A `!cmd` relay sent first used to become the chat's title (2026-10-21
    stabilization, composer#10); the next real prompt names it instead.
    """
    if not _is_auto_name(state.name) or _is_shell_relay(message):
        return False
    return not any(m.role == "user" and not _is_shell_relay(m.content) for m in state.messages)


def _derive_title(message: str) -> str:
    """Derive a short, single-line chat title from the first user message."""
    text = " ".join((message or "").split())
    if not text:
        return "New chat"
    if len(text) > _TITLE_MAX_LEN:
        text = text[:_TITLE_MAX_LEN].rstrip() + "\u2026"
    return text


# =============================================================================
# Session State
# =============================================================================


@dataclass
class SessionMessage:
    """A message in the session conversation."""

    role: str  # user, assistant, system
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionMessage":
        """Create from dictionary."""
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat())),
            metadata=data.get("metadata", {}),
        )


@dataclass
class SessionState:
    """Persistent state for an agent session."""

    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    messages: list[SessionMessage] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)  # Workflow IDs
    context: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for persistence."""
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "messages": [m.to_dict() for m in self.messages],
            "workflows": self.workflows,
            "context": self.context,
            "config": self.config,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionState":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            messages=[SessionMessage.from_dict(m) for m in data.get("messages", [])],
            workflows=data.get("workflows", []),
            context=data.get("context", {}),
            config=data.get("config", {}),
            metadata=data.get("metadata", {}),
        )


# =============================================================================
# Agent Session
# =============================================================================


class AgentSession:
    """Manages a persistent agent session for multi-turn conversations.

    Features:
    - Persistent conversation history
    - Workflow tracking
    - Context accumulation
    - Trust session management for egress
    - Save/load session state

    Example:
        from r4 import Client
        from r4.agent import AgentSession

        client = Client()

        # Create new session
        session = AgentSession.create(client, name="My Research")

        # Or resume existing session
        session = AgentSession.load(client, session_id="...")

        # Chat with context
        response = session.chat("Analyze the simulation data")
        response = session.chat("Now compare with the reference")

        # Save for later
        session.save()
    """

    def __init__(
        self,
        client: "Client",
        state: SessionState,
        config: AgentConfig | None = None,
    ):
        """Initialize session.

        Args:
            client: R4 Client
            state: Session state
            config: Agent configuration (overrides state config)
        """
        self.client = client
        self.state = state

        # Merge config
        if config:
            self.config = config
        elif state.config:
            self.config = AgentConfig(**state.config)
        else:
            self.config = AgentConfig()

        # Initialize agent
        self._agent = SciWorkflowAgent(client, self.config)
        self._trust_session: TrustSession | None = None
        self._on_event: Callable[[AgentEvent], None] | None = None
        self._cancellation = AgentCancellation()
        self._stream_callback: Callable[[str], None] | None = None
        # C6: structured event callback (tool_call_start, tool_call_done,
        # thinking_chunk, usage, assistant_delta).  The ACP server's
        # chat_send routes these to dedicated webview notifications.
        self._event_callback: Callable[[dict[str, Any]], None] | None = None
        self._active_stream: Any = None
        self._agent.set_cancellation(self._cancellation)
        self._turn_usage: dict[str, Any] | None = None

    @classmethod
    def create(
        cls,
        client: "Client",
        name: str | None = None,
        config: AgentConfig | None = None,
    ) -> "AgentSession":
        """Create a new session.

        Args:
            client: R4 Client
            name: Session name
            config: Agent configuration

        Returns:
            New AgentSession
        """
        session_id = str(uuid.uuid4())
        now = datetime.now()

        state = SessionState(
            id=session_id,
            name=name or f"Session {now.strftime('%Y-%m-%d %H:%M')}",
            created_at=now,
            updated_at=now,
            config=config.to_dict() if config else {},
        )

        return cls(client, state, config)

    @classmethod
    def load(cls, client: "Client", session_id: str) -> "AgentSession":
        """Load an existing session.

        Args:
            client: R4 Client
            session_id: Session ID to load

        Returns:
            Loaded AgentSession

        Raises:
            FileNotFoundError: If session doesn't exist
        """
        session_path = _session_path(session_id)

        if not session_path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")

        with open(session_path) as f:
            data = json.load(f)

        state = SessionState.from_dict(data)
        if state.id != session_id:
            raise ValueError("Stored session ID does not match requested session")
        return cls(client, state)

    @classmethod
    def list_sessions(cls) -> list[dict[str, Any]]:
        """List all saved sessions.

        Returns:
            List of session summaries
        """
        sessions = []

        if not SESSIONS_DIR.exists():
            return sessions

        for path in SESSIONS_DIR.glob("*.json"):
            try:
                with open(path) as f:
                    data = json.load(f)
                sessions.append(
                    {
                        "id": data["id"],
                        "name": data["name"],
                        "created_at": data["created_at"],
                        "updated_at": data["updated_at"],
                        "model": (data.get("config") or {}).get("model"),
                        "message_count": len(data.get("messages", [])),
                        "workflow_count": len(data.get("workflows", [])),
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to load session {path}: {e}")

        # Sort by updated_at
        sessions.sort(key=lambda s: s["updated_at"], reverse=True)
        return sessions

    def save(self) -> None:
        """Save session state to disk."""
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        session_path = _session_path(self.state.id)

        self.state.updated_at = datetime.now()

        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", dir=SESSIONS_DIR, prefix=f".{self.state.id}.", suffix=".tmp", delete=False
            ) as temporary_file:
                temporary_path = temporary_file.name
                json.dump(self.state.to_dict(), temporary_file, indent=2)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, session_path)
            directory_fd = os.open(SESSIONS_DIR, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_path is not None and os.path.exists(temporary_path):
                os.unlink(temporary_path)

        logger.debug(f"Saved session to {session_path}")

    def delete(self) -> None:
        """Delete this session."""
        session_path = _session_path(self.state.id)
        if session_path.exists():
            session_path.unlink()
            logger.info(f"Deleted session {self.state.id}")

    def on_event(self, callback: Callable[[AgentEvent], None]) -> None:
        """Register event callback."""
        self._on_event = callback
        self._agent.on_event(callback)

    def set_editor_tools(
        self,
        tools: list[Any],
        callback: Callable[[str, dict[str, Any]], Any],
    ) -> None:
        """Attach editor-governed tools to this session."""
        self._agent.set_editor_tools(tools, callback)

    def cancel(self) -> None:
        self._cancellation.request()
        close = getattr(self._active_stream, "close", None)
        if callable(close):
            try:
                cast(Callable[[], None], close)()
            except (RuntimeError, ValueError):
                pass

    def prepare_request(self) -> None:
        self._cancellation.clear()

    def is_cancelled(self) -> bool:
        return self._cancellation.is_set()

    def set_stream_callback(self, callback: Callable[[str], None] | None) -> None:
        self._stream_callback = callback

    def set_event_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        """C6: structured event callback (tool calls, thinking, usage)."""
        self._event_callback = callback

    def _notify_event(self, event: dict[str, Any]) -> None:
        """Fire the structured event callback if registered."""
        if self._event_callback:
            try:
                self._event_callback(event)
            except Exception:
                pass  # never let callback errors kill the turn

    def _emit(self, event_type: AgentEventType, message: str, data: dict | None = None) -> None:
        """Emit an event."""
        if self._on_event:
            self._on_event(AgentEvent(event_type, message, data))

    # =========================================================================
    # Conversation
    # =========================================================================

    def chat(self, message: str, auto_save: bool = True) -> str:
        """Send a message and get a response.

        Args:
            message: User message
            auto_save: Save session after response

        Returns:
            Assistant response
        """
        message_count = len(self.state.messages)
        workflow_count = len(self.state.workflows)
        # Copilot-style auto-title: name the session after its first real user
        # prompt while it still carries a machine-generated placeholder name.
        if _should_auto_title(self.state, message):
            self.state.name = _derive_title(message)
        self.state.messages.append(SessionMessage(role="user", content=message))

        try:
            response = self._agentic_response(message)

            if self._cancellation.is_set():
                raise AgentCancelledError("Agent request cancelled")
            self.state.messages.append(SessionMessage(role="assistant", content=response))

            if auto_save:
                self.save()
        except AgentCancelledError:
            del self.state.messages[message_count:]
            del self.state.workflows[workflow_count:]
            raise

        return response

    # ── Agentic loop ─────────────────────────────────────────────────────

    def _agentic_response(self, user_message: str) -> str:
        """Run the agentic loop: LLM → tool_calls → execute → feedback → repeat.

        This is the Surface B equivalent of `AgentEngine.runTurn()` in the
        VS Code extension (Surface A).  It preserves server-side properties:
        disk-persisted sessions, memory, trust-session egress control.
        """
        # ── Tool list (MCP + editor + KNOWN_R4 fallback) ────────────
        tool_schemas = self._build_tool_schemas()

        # ── Working message history (last 40 messages for context) ────
        context = self._build_context()
        system_msg = (
            "You are a helpful AI assistant for scientists using the R4 Platform.\n"
            "You help with research questions, data analysis, workflow planning,\n"
            "and general scientific inquiries.\n\n"
            f"{R4_USAGE_GUIDE}\n\n"
            f"Session context:\n{context}"
        )
        messages = self._agentic_messages(system_msg, user_message)

        max_iterations = self.config.max_iterations  # default 10
        max_tool_calls_per_step = self.config.max_tool_calls_per_step  # default 5
        chunks: list[str] = []
        seen_calls: dict[str, str] = {}  # hash(name, args) → result text (B9 dedup)
        last_tool_calls: list[dict[str, Any]] = []
        has_tool_calls_this_round = False
        final_usage: dict[str, Any] | None = None  # C6: capture usage from last LLM call

        for _iteration in range(max_iterations):
            if self._cancellation.is_set():
                break

            response, tool_schemas, error_msg = self._agentic_llm_turn(messages, tool_schemas)
            if error_msg is not None:
                return self._finish_failed_turn(chunks, error_msg)

            final_usage = _capture_usage(response, final_usage)

            content, tool_calls = _parse_llm_response(response)

            if tool_calls:
                has_tool_calls_this_round = True
                last_tool_calls = tool_calls
                self._execute_agentic_tool_calls(
                    tool_calls, max_tool_calls_per_step, seen_calls, messages
                )
                # Continue loop to let model process tool results
                continue

            # No tool calls — stream content.
            # Thinking markers are deliberately preserved here: both consumers
            # (the ACP server and the CLI's _format_thinking) parse them to
            # separate reasoning from the answer. Stripping at this layer
            # destroys that structure and leaks reasoning into the reply.
            if content:
                chunks.append(content)
                if self._stream_callback:
                    self._stream_callback(content)

            # Model exhausted — stop
            break

        # If we only got tool results without final content, synthesize
        if not chunks and has_tool_calls_this_round:
            summary = f"Completed {len(last_tool_calls)} tool call(s)."
            chunks.append(summary)

        self._notify_usage(final_usage)

        return "".join(chunks)

    def _agentic_messages(self, system_msg: str, user_message: str) -> list[dict[str, Any]]:
        """Assemble the agentic turn's working history (system + recent + user)."""
        recent = self.state.messages[-40:]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_msg}]
        for m in recent:
            messages.append({"role": m.role, "content": m.content})
        messages.append({"role": "user", "content": user_message})
        return messages

    def _finish_failed_turn(self, chunks: list[str], error_msg: str) -> str:
        """Render a terminal agentic failure as visible assistant text."""
        chunks.append(error_msg)
        if self._stream_callback:
            self._stream_callback(error_msg)
        return "".join(chunks)

    def _notify_usage(self, final_usage: dict[str, Any] | None) -> None:
        """Emit the C6 usage event so the webview can render token counts."""
        if not final_usage:
            return
        self._notify_event(
            {
                "type": "usage",
                "prompt_tokens": int(final_usage.get("prompt_tokens", 0)),
                "completion_tokens": int(final_usage.get("completion_tokens", 0)),
                "total_tokens": int(final_usage.get("total_tokens", 0)),
            }
        )

    def _agentic_llm_turn(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]] | None,
    ) -> tuple[Any, list[dict[str, Any]] | None, str | None]:
        """One LLM call inside the agentic loop, with tool-fallback handling.

        Returns ``(response, tool_schemas, error_msg)``:

        - On success: ``(response, tool_schemas, None)`` — ``tool_schemas`` is
          ``None`` when a fallback stripped tools for the remainder of the turn.
        - On terminal failure: ``(None, schemas, error_msg)`` — the caller
          renders *error_msg* and ends the turn.
        - HTTPErrors that are not tool rejections propagate (unchanged behavior).
        """
        try:
            response = self._call_llm(
                messages=messages,
                tool_schemas=tool_schemas if tool_schemas else None,
                _turn_cancel=self._cancellation,
            )
            return response, tool_schemas, None
        except HTTPError as exc:
            # B10 tool-rejection fallback: 400 mentioning tool/function → retry without tools
            if self._is_tool_rejection(exc) and tool_schemas:
                logger.info("Retrying without tools after %s", exc)
                try:
                    response = self._call_llm(
                        messages=messages,
                        tool_schemas=None,
                        _turn_cancel=self._cancellation,
                    )
                    return response, None, None
                except Exception:
                    raise
            raise
        except Exception as exc:
            # B10b: Any other failure (TypeError, JSONDecodeError, network, etc.)
            # falls back to a no-tools call so the session does NOT freeze.
            if tool_schemas:
                logger.warning("LLM call failed (%s), retrying without tools", type(exc).__name__)
                try:
                    response = self._call_llm(
                        messages=messages,
                        tool_schemas=None,
                        _turn_cancel=self._cancellation,
                    )
                    return response, None, None
                except Exception:
                    pass  # fall through to terminal error
            return None, tool_schemas, f"Failed to get a response: {exc}"

    def _execute_agentic_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        max_per_step: int,
        seen_calls: dict[str, str],
        messages: list[dict[str, Any]],
    ) -> None:
        """Execute one batch of model tool calls, appending results to *messages*.

        Includes B9 dedup (identical name+args replays the prior result) and
        C6 structured events for the webview tool cards.
        """
        for tc in tool_calls[:max_per_step]:
            if self._cancellation.is_set():
                break
            name = tc.get("name", "")
            args_str = tc.get("arguments", "{}") or "{}"
            parsed_args = json.loads(args_str) if args_str.strip() else {}

            # B9 dedup: hash(name, args)
            hash_key = self._dedup_key(name, args_str)
            prior_result = seen_calls.get(hash_key)
            if prior_result is not None:
                self._append_tool_exchange(messages, tc, prior_result)
                continue

            # C6: emit structured events for the webview tool card
            self._notify_event(
                {
                    "type": "tool_call_start",
                    "name": name,
                    "arguments": parsed_args,
                    "tool_call_id": tc.get("id", ""),
                    "status": "pending",
                }
            )

            import time

            t0 = time.monotonic()
            result_text = self._execute_tool(name, parsed_args)
            duration_ms = round((time.monotonic() - t0) * 1000)

            self._notify_event(
                {
                    "type": "tool_call_done",
                    "name": name,
                    "tool_call_id": tc.get("id", ""),
                    "status": "completed",
                    "result_preview": result_text[:500] if result_text else "",
                    "duration_ms": duration_ms,
                }
            )

            # B9 store result (if short enough)
            if result_text and len(result_text) < 5000:
                seen_calls[hash_key] = result_text

            self._append_tool_exchange(messages, tc, result_text)

    @staticmethod
    def _append_tool_exchange(
        messages: list[dict[str, Any]],
        tc: dict[str, Any],
        result_text: str,
    ) -> None:
        """Append the assistant tool-call + tool result pair to the history."""
        messages.append({"role": "assistant", "content": None, "tool_calls": [tc]})
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tc.get("id", str(uuid.uuid4())),
                "content": result_text,
            }
        )

    def _build_tool_schemas(self) -> list[dict[str, Any]] | None:
        """Build OpenAI-style tool schemas from MCP + editor + known tools.

        Returns ``None`` when the resolved provider doesn't support structured
        tool calling (e.g. local Ollama models that hallucinate tool-call JSON
        as text) — the turn then runs conversationally.
        """
        schemas = self._mcp_tool_schemas() + self._editor_tool_schemas()

        # KNOWN_R4_TOOLS fallback (no duplicates by name)
        existing_names = {s["function"]["name"] for s in schemas}
        for kt in _KNOWN_R4_TOOLS_FALLBACK:
            if kt["name"] not in existing_names:
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": kt["name"],
                            "description": kt.get("description", ""),
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                )

        if schemas and not self._provider_supports_tools():
            return None
        return schemas if schemas else None

    @staticmethod
    def _schema_entry(name: str, desc: str, schema: dict[str, Any] | None) -> dict[str, Any]:
        """One OpenAI function-tool schema entry."""
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": desc or "",
                "parameters": schema or {"type": "object", "properties": {}},
            },
        }

    def _mcp_tool_schemas(self) -> list[dict[str, Any]]:
        """Collect tool schemas advertised by connected MCP servers.

        Any failure (unavailable MCP client, mock client without an iterable
        tool list) degrades to "no MCP schemas" — the session must not fail.
        """
        try:
            return [
                self._schema_entry(
                    getattr(t, "name", ""),
                    getattr(t, "description", ""),
                    getattr(t, "inputSchema", None),
                )
                for t in self.client.mcp.list_tools()
            ]
        except Exception:
            return []

    def _editor_tool_schemas(self) -> list[dict[str, Any]]:
        """Collect schemas for editor-governed tools (workspace.*, terminal.*)."""
        planner = getattr(self._agent, "planner", None)
        if planner is None:
            return []
        return [
            self._schema_entry(
                getattr(et, "name", ""),
                getattr(et, "description", ""),
                getattr(et, "input_schema", None),
            )
            for et in getattr(planner, "_editor_tools", [])
        ]

    def _provider_supports_tools(self) -> bool:
        """Check whether the resolved provider supports structured tool calling.

        Looks up the provider for the session's model (which may include a
        ``@source`` suffix) in the client's provider registry and checks its
        ``model_capabilities().supports_tool_calling`` flag. Returns True
        when the provider is unknown (legacy HTTP path, gateway-managed
        models that do support tools) so we don't break existing behavior.
        """
        from ..model_routing import parse_model_ref

        model = self.config.model or ""
        ref = parse_model_ref(model)
        source_name = ref.source
        if source_name is None:
            return True  # default route — assume gateway-managed, supports tools
        try:
            provider = self.client.provider_registry.get(source_name)
            if provider is None:
                return True
            return provider.model_capabilities(ref.model).supports_tool_calling
        except Exception:
            return True

    def _call_llm(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]] | None,
        _turn_cancel: Any | None = None,
    ) -> dict[str, Any]:
        """Call the LLM with optional tools. Returns dict with content + tool_calls."""
        # `Client.chat_completion()` calls `m.to_dict()` on every message and
        # `ChatMessage.to_dict()` drops `tool_calls` / `tool_call_id`, which the
        # agentic loop needs.  Wrap raw dicts in a passthrough adapter instead.
        wire_messages = [_RawChatMessage(m) for m in messages]

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "stream": False,
            "_turn_cancel": _turn_cancel,
        }
        if tool_schemas:
            kwargs["tools"] = tool_schemas
            kwargs["tool_choice"] = "auto"

        response = self.client.chat_completion(wire_messages, **kwargs)

        if isinstance(response, dict):
            return response

        content = getattr(response, "content", "") or ""
        tool_calls = getattr(response, "tool_calls", None)

        # ChatResponse has no `tool_calls` field — recover them from the raw
        # OpenAI-shaped payload so the agentic loop can act on them.
        if tool_calls is None:
            tool_calls = _extract_tool_calls(getattr(response, "raw_response", None))

        return {"content": content, "tool_calls": tool_calls or []}

    def _execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool (MCP or editor-governed). Returns result text."""
        editor_result = self._execute_editor_tool(name, arguments)
        if editor_result is not None:
            return editor_result
        return self._execute_mcp_tool(name, arguments)

    def _execute_editor_tool(self, name: str, arguments: dict[str, Any]) -> str | None:
        """Run an editor-governed tool when one matches *name*; None when absent."""
        planner = getattr(self._agent, "planner", None)
        if planner is None:
            return None
        for et in getattr(planner, "_editor_tools", []):
            if getattr(et, "name", "") != name:
                continue
            try:
                cb = getattr(et, "call", None)
                if callable(cb):
                    result = cb(arguments)
                    return json.dumps(result, default=str)
            except Exception as exc:
                return _tool_failure_text(name, exc)
        return None

    def _execute_mcp_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Run an MCP tool and normalize its content payload to text."""
        try:
            servers = getattr(self.client.mcp, "server_names", []) or []
            server_name = servers[0] if servers else "default"
            result = self.client.mcp.call_tool(name, arguments, server_name)
            return _normalize_mcp_content(getattr(result, "content", []) or [])
        except Exception as exc:
            return _tool_failure_text(name, exc)

    @staticmethod
    def _dedup_key(name: str, args_str: str) -> str:
        """Simple deterministic hash for deduplication."""
        raw = f"{name}||{args_str}"
        h = 0
        for ch in raw:
            h = ((h << 5) - h + ord(ch)) & 0x7FFFFFFF
        return f"h_{h:08x}"

    @staticmethod
    def _is_tool_rejection(exc: HTTPError) -> bool:
        """Check if a 400 error is a tool-rejection (vs other 400s)."""
        if exc.response is None:
            return False
        body = exc.response.text or ""
        return any(kw in body.lower() for kw in ["tool", "function", "tool_choice", "tools"])

    def _build_context(self) -> str:
        """Build context from session history."""
        # Get recent messages (last 10)
        recent = self.state.messages[-10:]
        context_parts = []

        for msg in recent:
            context_parts.append(f"{msg.role}: {msg.content}")

        # Add any accumulated context
        if self.state.context:
            context_parts.append(f"\nSession context: {json.dumps(self.state.context)}")

        return "\n".join(context_parts)

    def _conversational_response(self, message: str, context: str) -> str:
        """Generate a conversational response."""
        messages = [
            {
                "role": "system",
                "content": f"""You are a helpful AI assistant for scientists using the R4 Platform.
You help with research questions, data analysis, workflow planning,
and general scientific inquiries.

{R4_USAGE_GUIDE}

Session context:
{context}

Provide helpful, accurate responses. If the user asks about capabilities,
explain that you can help with:
- Creating and running workflows
- Analyzing data
- Querying documents (RAG)
- Using various research tools (MCP)
- Submitting HPC jobs""",
            },
            {"role": "user", "content": message},
        ]

        chunks = []
        self._active_stream = self.client.chat_stream(
            messages=messages,
            model=self.config.model,
            _turn_cancel=self._cancellation,
        )
        try:
            for chunk in self._active_stream:
                if self._cancellation.is_set():
                    raise AgentCancelledError("Agent request cancelled")
                chunks.append(chunk)
                if self._stream_callback:
                    self._stream_callback(chunk)
        finally:
            self._active_stream = None
        return "".join(chunks) or "I apologize, I couldn't generate a response."

    def _format_workflow_result(self, result: WorkflowExecution) -> str:
        """Format workflow execution result for display."""
        lines = [f"✓ Completed: {result.workflow.name}"]

        for step_id, step_result in result.step_results.items():
            step = next((s for s in result.workflow.steps if s.id == step_id), None)
            desc = step.description if step else step_id

            if step_result.status.value == "completed":
                lines.append(f"  ✓ {desc}")
                if step_result.output:
                    # Truncate long outputs
                    output_str = str(step_result.output)
                    if len(output_str) > 200:
                        output_str = output_str[:200] + "..."
                    lines.append(f"    → {output_str}")
            else:
                lines.append(f"  ✗ {desc}: {step_result.error}")

        return "\n".join(lines)

    # =========================================================================
    # Workflow Management
    # =========================================================================

    def run_workflow(
        self,
        objective: str,
        inputs: dict[str, Any] | None = None,
        auto_save: bool = True,
    ) -> WorkflowExecution:
        """Run a workflow with the given objective.

        Args:
            objective: Research objective
            inputs: Initial inputs
            auto_save: Save session after

        Returns:
            WorkflowExecution result
        """
        self.state.messages.append(
            SessionMessage(
                role="user",
                content=f"[Workflow Request] {objective}",
                metadata={"type": "workflow_request"},
            )
        )

        context = self._build_context()
        result = self._agent.run(objective, inputs=inputs, context=context)

        self.state.workflows.append(result.id)
        self.state.messages.append(
            SessionMessage(
                role="assistant",
                content=self._format_workflow_result(result),
                metadata={"type": "workflow_result", "workflow_id": result.id},
            )
        )

        if auto_save:
            self.save()

        return result

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
        """Get a workflow definition by ID."""
        # For now, only return current workflow
        current = self._agent.get_current_workflow()
        if current and current.id == workflow_id:
            return current
        return None

    # =========================================================================
    # Trust Session (Egress Batching)
    # =========================================================================

    def start_trust_session(self, duration_minutes: int = 5) -> TrustSession:
        """Start a trust session for batched egress approvals.

        During a trust session, certain egress patterns are pre-approved
        to reduce user interaction friction.

        Args:
            duration_minutes: Duration of trust session

        Returns:
            TrustSession object
        """
        self._trust_session = self._agent.egress_guard.create_trust_session(
            duration_minutes=duration_minutes
        )
        return self._trust_session

    def end_trust_session(self) -> None:
        """End the current trust session."""
        if self._trust_session:
            self._trust_session.revoke()
            self._trust_session = None

    # =========================================================================
    # Context Management
    # =========================================================================

    def add_context(self, key: str, value: Any) -> None:
        """Add context that persists across messages.

        Args:
            key: Context key
            value: Context value
        """
        self.state.context[key] = value

    def get_context(self, key: str) -> Any | None:
        """Get context value."""
        return self.state.context.get(key)

    def clear_context(self) -> None:
        """Clear all context."""
        self.state.context = {}

    def set_project(self, project_id: str) -> None:
        """Set the current project for RAG and jobs.

        Args:
            project_id: Project identifier
        """
        self.state.context["project_id"] = project_id

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def id(self) -> str:
        """Session ID."""
        return self.state.id

    @property
    def name(self) -> str:
        """Session name."""
        return self.state.name

    @property
    def messages(self) -> list[SessionMessage]:
        """Session messages."""
        return self.state.messages

    @property
    def model(self) -> str | None:
        """Model bound to this session, if any."""
        return self.config.model

    def rename(self, name: str) -> None:
        """Rename the session and persist the change."""
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("Session name cannot be empty")
        self.state.name = cleaned[:120]
        self.save()

    def set_model(self, model: str | None) -> None:
        """Bind the session to a model/endpoint and persist the change.

        Mutates the shared ``AgentConfig`` so the underlying agent picks up the
        new model immediately, allowing the endpoint to change mid-session.
        """
        normalized = model.strip() if isinstance(model, str) and model.strip() else None
        self.config.model = normalized
        self.state.config = self.config.to_dict()
        self.save()

    @property
    def agent(self) -> SciWorkflowAgent:
        """Underlying agent."""
        return self._agent

    def __repr__(self) -> str:
        return (
            f"<AgentSession id={self.state.id} name={self.state.name!r} "
            f"messages={len(self.state.messages)}>"
        )
