"""ACP adapter: CompanionSession → AgentSessionProtocol."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Callable

from r4.agent.core import AgentCancelledError

from .agent import CompanionEvent, CompanionEventType
from .agent_mode import apply_mode, session_mode
from .cancellation import TurnCancellation, TurnCancelledError
from .context import DEFAULT_MAX_TOKENS
from .control_schema import BudgetState, ThinkingDelta, ThinkingDone, ToolCallEnd, ToolCallStart
from .control_schema import to_dict as control_event_to_dict
from .permissions import NEVER_AUTO_APPROVE
from .session import CompanionSession
from .session_store import CompanionSessionStore
from .tool_summaries import (
    args_summary_for,
    result_envelope_cap,
    result_preview_full,
)

_AUTO_NAME_RE = re.compile(r"^(Session|R4 Agent)\b")
_TITLE_MAX_LEN = 48

logger = logging.getLogger(__name__)


def _end_status(raw: Any) -> str:
    """Map the agent-side status onto the control-schema end vocabulary."""
    status = raw or "done"
    if status in {"done", "error", "cancelled"}:
        return status
    return "error" if status in {"failed", "fail"} else "done"


def _result_exceeds_envelope(result_field: Any, source_text: str) -> bool:
    """True when the full result is bigger than the wire preview carries —
    the expand action then fetches it from the session journal."""
    return len(source_text) > result_envelope_cap() or len(str(result_field or "")) >= 2000


def _derive_title(text: str) -> str:
    """Copilot-style session title from the first user message."""
    clean = " ".join((text or "").split()).strip()
    if not clean:
        return "New chat"
    return clean if len(clean) <= _TITLE_MAX_LEN else clean[:_TITLE_MAX_LEN].rstrip() + "…"


# Companion event types that already flow through dedicated ACP channels
# (streaming chunks, thinking, structured tool events, errors). Forwarding
# them as generic ``session/event`` payloads too would double-render in
# clients (VS Code webview showed each reply 3x). Mirrors the
# ``_SURFACED_EVENTS`` filter in ``companion/ui/bridge_server.py``.
_NOISY_SESSION_EVENT_TYPES = frozenset(
    {
        CompanionEventType.THINKING.value,
        CompanionEventType.RESPONSE_START.value,
        CompanionEventType.RESPONSE_CHUNK.value,
        CompanionEventType.RESPONSE_DONE.value,
        CompanionEventType.TOOL_CALL.value,
        CompanionEventType.TOOL_RESULT.value,
        CompanionEventType.ERROR.value,
        # Live reasoning (a model's reasoning_content stream) goes out as the
        # canonical thinking envelope (_reasoning_envelope). As generic
        # session/event payloads, one per token, the agent view printed every
        # token as its own "event:" row (maintainer report 2026-11-01).
        CompanionEventType.REASONING_DELTA.value,
        CompanionEventType.REASONING_DONE.value,
    }
)

# Events that mark a boundary between streamed text rounds: any text chunk
# arriving after one of these belongs to a NEW model round (narration before
# a tool call vs. after its result). Without an explicit break, consecutive
# rounds render glued together ("...fits the existing patterns.Now let me
# look...") in the single assistant bubble the ACP chat path builds.
# ERROR is defence-in-depth: today every tool failure also emits TOOL_CALL
# first (agent.py _prepare_tool_call), so the boundary already fires; a
# future failed-tool emitter that skips TOOL_CALL would silently re-glue
# without it. Idempotent — the separator is consumed by the first chunk.
_ROUND_BOUNDARY_EVENT_TYPES = frozenset(
    {
        CompanionEventType.TOOL_CALL.value,
        CompanionEventType.TOOL_RESULT.value,
        CompanionEventType.TOOL_BLOCKED.value,
        CompanionEventType.ERROR.value,
        CompanionEventType.RESPONSE_DONE.value,
    }
)


class _AcpForwardEvent:
    """AgentEvent-shaped payload for ACP session/event notifications."""

    def __init__(self, event: CompanionEvent) -> None:
        self.event_type = event.event_type
        self.message = event.message
        self.data = event.data
        self.timestamp = event.timestamp

    def to_dict(self) -> dict[str, Any]:
        event_type = getattr(self.event_type, "value", self.event_type)
        return {
            "event_type": event_type,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp.isoformat()
            if isinstance(self.timestamp, datetime)
            else str(self.timestamp),
        }


class CompanionAcpSession:
    """Wrap a CompanionSession so ACP can drive it through AgentSessionProtocol."""

    def __init__(
        self,
        session: CompanionSession,
        *,
        store: CompanionSessionStore | None = None,
    ) -> None:
        self._session = session
        self._store = store or CompanionSessionStore()
        self._client = getattr(session, "client", None)
        self._on_event: Callable[[Any], None] | None = None
        self._stream_callback: Callable[[str], None] | None = None
        self._event_callback: Callable[[dict[str, Any]], None] | None = None
        self._cancel: TurnCancellation | None = None
        self._editor_tools: list[Any] = []
        self._editor_tool_call: Callable[[str, dict[str, Any]], Any] | None = None
        self._agent_listener_attached = False
        # Round-boundary tracking for the streamed-text separator (see
        # _ROUND_BOUNDARY_EVENT_TYPES). Read via getattr() so instances
        # built with object.__new__ (test harness) keep working.
        self._stream_round_boundary = False
        self._stream_emitted_text = False
        self._install_shell_chunk_relay()
        self._attach_agent_listener()
        self._open_sandbox()

    def _open_sandbox(self) -> None:
        """ADR-101: an ACP session that opens with the sandbox on (its own
        choice, ``R4_SANDBOX``, the profile default) moves local MCP servers
        under it, like the console does. Its turns pin its own context
        (``CompanionSession.send``), never the first session's (review ESC-4)."""
        from .sandbox import profile_of
        from .ui.sandbox_bridge import open_session_sandbox

        try:
            profile = profile_of(getattr(self._client, "config", None))
            error = open_session_sandbox(self._session, None, profile)
        except Exception:  # noqa: BLE001 — never block opening a session
            logger.warning("ACP session sandbox not settled", exc_info=True)
            return
        if error:
            logger.warning("ACP session sandbox: %s", error)

    def _install_shell_chunk_relay(self) -> None:
        """Route per-line shell chunks straight to the webview as ACP frames.

        The sink lives in ``r4.companion.tools`` (module scope); relaying here
        avoids touching ``agent.py``, which carries pre-existing complexity
        debt that the CI complexity gate would surface.
        """
        from .streaming_shell import (
            SHELL_OUTPUT_EVENT_TYPE,
            SHELL_STREAM_END_EVENT_TYPE,
        )
        from .tools import set_shell_chunk_sink, set_shell_status_sink

        def relay(stream_id: str, chunk: str) -> None:
            try:
                event = CompanionEvent(
                    event_type=SHELL_OUTPUT_EVENT_TYPE,
                    message=chunk,
                    data={"stream_id": stream_id},
                )
                self._handle_companion_event(event)
            except Exception:
                pass

        def relay_status(stream_id: str, status: str) -> None:
            try:
                event = CompanionEvent(
                    event_type=SHELL_STREAM_END_EVENT_TYPE,
                    message="",
                    data={"stream_id": stream_id, "status": status},
                )
                self._handle_companion_event(event)
            except Exception:
                pass

        set_shell_chunk_sink(relay)
        set_shell_status_sink(relay_status)

    @property
    def id(self) -> str:
        return self._session.session_id

    @property
    def name(self) -> str:
        return self._session.name

    @name.setter
    def name(self, value: str) -> None:
        self._session.name = value

    @property
    def model(self) -> str | None:
        return getattr(self._session.agent, "model", None) or None

    @property
    def messages(self) -> list[Any]:
        return self._session.messages

    @property
    def companion(self) -> CompanionSession:
        return self._session

    def on_event(self, callback: Callable[[Any], None]) -> None:
        self._on_event = callback
        self._attach_agent_listener()

    def set_acp_context(
        self,
        *,
        capabilities: dict[str, Any],
        editor_tool_names: list[str],
        gateway_url: str | None,
        umrs_host: str | None,
    ) -> None:
        """Handshake context for the toolset log (round-3 task-1): which
        capabilities the editor advertised, which editor tools are wrapped,
        and the platform identity of this session."""
        self._acp_capabilities = dict(capabilities)
        self._acp_editor_tool_names = list(editor_tool_names)
        self._acp_gateway_url = gateway_url
        self._acp_umrs_host = umrs_host

    def _log_toolset_once(self, data: dict[str, Any]) -> None:
        """Log the offered set + filtered_out buckets + client identity for
        this session exactly once, at the first model call."""
        if getattr(self, "_toolset_logged", False):
            return
        self._toolset_logged = True
        logger.info(
            "acp toolset session=%s offered=%s filtered_out=%s editor_tools=%s "
            "capabilities=%s gateway=%s umrs_host=%s",
            self._session_id(),
            data.get("selected_tools"),
            data.get("filtered_out"),
            getattr(self, "_acp_editor_tool_names", []),
            sorted(getattr(self, "_acp_capabilities", {}).keys()),
            getattr(self, "_acp_gateway_url", None),
            getattr(self, "_acp_umrs_host", None),
        )

    def set_stream_callback(self, callback: Callable[[str], None] | None) -> None:
        self._stream_callback = callback

    def set_event_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        self._event_callback = callback

    def set_editor_tools(
        self,
        tools: list[Any],
        callback: Callable[[str, dict[str, Any]], Any],
    ) -> None:
        self._editor_tools = list(tools or [])
        self._editor_tool_call = callback
        self._inject_editor_tools()

    def prepare_request(self) -> None:
        self._cancel = TurnCancellation()
        self._session._turn_cancel = self._cancel

    def cancel(self) -> None:
        if self._cancel is not None:
            self._cancel.request()
        self._session.request_turn_cancel()

    def is_cancelled(self) -> bool:
        if self._cancel is not None:
            return self._cancel.is_set()
        token = getattr(self._session, "_turn_cancel", None)
        return bool(token is not None and token.is_set())

    def chat(self, message: str, auto_save: bool = True) -> str:
        if self._session._turn_cancel is None:
            self.prepare_request()
        # Fresh boundary state per turn: the first streamed round must not
        # get a leading separator.
        self._stream_round_boundary = False
        self._stream_emitted_text = False
        try:
            response = self._session.send(message, stream=True)
            self._emit_budget_state()
            self._maybe_auto_rename(message)
            if auto_save:
                self.save()
            return response
        except TurnCancelledError as exc:
            raise AgentCancelledError("Request cancelled") from exc
        finally:
            # Keep cancellation flag until the next prepare_request, matching AgentSession.
            pass

    def _maybe_auto_rename(self, message: str) -> None:
        """Rename placeholder sessions after the first turn (Copilot-style).

        Only fires when the current name looks machine-generated ("Session …",
        "R4 Agent …") — a user-chosen name always wins. Rename persists via
        ``_store.save``, so the sessions/list poll right after picks it up.
        """
        current = (getattr(self._session, "name", "") or "").strip()
        if not _AUTO_NAME_RE.match(current):
            return
        message_count = len(getattr(self._session, "messages", ()) or [])
        if message_count > 2:
            # Chat already had a couple turns — don't stomp on a manual save.
            return
        text = " ".join((message or "").split()).strip()
        new_name = _derive_title(text)
        if new_name and new_name != current:
            self._session.name = new_name[:120]

    def save(self) -> None:
        self._store.save(self._session)

    def delete(self) -> None:
        self._store.delete(self._session.session_id)
        # ADR-035 item-8: same convergence as the bridge/CLI — deleting an
        # ACP session triggers the server archive cascade (tombstone +
        # session-scope trust revoke + per-grant audit, one transaction).
        # Best-effort; the 86400s TTL is the crash bound.
        archive_server = getattr(self._client, "companion_session_archive", None)
        if callable(archive_server):
            try:
                archive_server(self._session.session_id)
            except Exception:  # noqa: BLE001
                logging.debug(
                    "server-side session archive failed for %s (TTL bound applies)",
                    self._session.session_id,
                    exc_info=True,
                )

    def rename(self, name: str) -> None:
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("Session name cannot be empty")
        self._session.name = cleaned[:120]
        self.save()

    def set_model(self, model: str | None) -> None:
        normalized = model.strip() if isinstance(model, str) and model.strip() else None
        self._session.agent.model = normalized
        self.save()

    def set_mode(self, mode: str | None) -> None:
        """Set the session's one mode (ADR-104 D-1): ``chat`` (alias ``ask``,
        or none), ``plan``, ``autopilot`` (alias ``agent``/``auto``). The
        permission mode follows it, as on every other surface."""
        apply_mode(self._session, mode or "chat")
        self.save()

    @property
    def interaction_mode(self) -> str:
        """Current normalized interaction mode for the webview mode control."""
        return session_mode(self._session)

    def _attach_agent_listener(self) -> None:
        agent = getattr(self._session, "agent", None)
        on_event = getattr(agent, "on_event", None)
        if not callable(on_event) or self._agent_listener_attached:
            return
        on_event(self._handle_companion_event)
        self._agent_listener_attached = True

    def _budget_state_event(self) -> BudgetState | None:
        """Compose the session budget read-model (wave T2 canonical event)."""
        session = self._session
        agent = getattr(session, "agent", None)
        if agent is None:
            return None
        return BudgetState(
            llm_calls_used=self._usage_int("llm_calls"),
            llm_calls_limit=getattr(session, "max_llm_calls_per_session", None),
            tool_rounds_used=self._turn_llm_calls(),
            tool_rounds_limit=int(getattr(agent, "max_tool_rounds", 0) or 1000),
            context_used_tokens=self._last_prompt_tokens(),
            context_limit_tokens=self._context_limit(),
            session_id=str(getattr(session, "session_id", "") or ""),
        )

    def _turn_llm_calls(self) -> int:
        # Assistant rounds for the completed turn — session telemetry, no
        # intrusion into the agentic loop (keeps agent.py out of this change).
        return int(getattr(self._session, "_current_turn_llm_calls", 0) or 0)

    def _usage_int(self, attr: str) -> int:
        usage = getattr(self._session, "usage_summary", None)
        return int(getattr(usage, attr, 0) or 0)

    def _last_prompt_tokens(self) -> int:
        records = getattr(self._session, "usage_records", None) or []
        if not records:
            return 0
        return int(getattr(records[-1], "prompt_tokens", 0) or 0)

    def _context_limit(self) -> int:
        context = getattr(self._session, "context", None)
        return int(getattr(context, "max_context_tokens", 0) or 0) or DEFAULT_MAX_TOKENS

    def _emit_budget_state(self) -> None:
        """Publish the budget read-model on the ACP structured-event channel.

        Envelope = control_schema {type: "budget_state", payload: {...}} —
        the same shape the JSONL bridge and a future /gw/companion/* surface
        will serve (GAP-COMP-4, T1 schema).
        """
        callback = getattr(self, "_event_callback", None)
        if callback is None:
            return
        event = self._budget_state_event()
        if event is None:
            return
        try:
            callback(control_event_to_dict(event))
        except Exception:
            pass

    def _handle_companion_event(self, event: CompanionEvent) -> None:
        event_type = getattr(event.event_type, "value", event.event_type)
        data = event.data or {}
        if event_type in _ROUND_BOUNDARY_EVENT_TYPES:
            self._stream_round_boundary = True
        if event_type == CompanionEventType.TOOL_SELECTION.value:
            self._log_toolset_once(data)
        if event_type == CompanionEventType.TOOL_CALL.value:
            # Track the open call so a following tool-executor ERROR (which
            # carries no name) can still complete the right card.
            self._last_tool_name = data.get("name")
        self._forward_stream_chunk(event_type, event.message)
        self._forward_structured(event_type, event.message, data)
        self._forward_agent_event(event)
        if event_type == CompanionEventType.RESPONSE_DONE.value:
            self._emit_cost()

    def _emit_cost(self) -> None:
        """C3: publish the per-turn token/cost/context chip as session/cost.

        Contract mirrors session/budget_state: dedicated method, additive
        payload, surfaces that predate it ignore the event entirely.
        """
        if self._event_callback is None:
            return
        from .cost_event import cost_event_payload

        cursor = getattr(self, "_cost_cursor", 0)
        envelope, next_cursor = cost_event_payload(self._session, cursor)
        if envelope is None:
            return
        self._cost_cursor = next_cursor
        try:
            self._event_callback(envelope)
        except Exception:
            pass

    def _forward_stream_chunk(self, event_type: str, message: str) -> None:
        if event_type != CompanionEventType.RESPONSE_CHUNK.value:
            return
        if not self._stream_callback or not message:
            return
        # Text after a tool round opens a new paragraph: insert the break
        # on the first chunk past the boundary (and only when a previous
        # round already streamed text). Accumulators downstream
        # (acp.py stream_chunks -> the final session/text) inherit the
        # separator, so live bubbles and the completed reply agree.
        if getattr(self, "_stream_round_boundary", False):
            self._stream_round_boundary = False
            if getattr(self, "_stream_emitted_text", False):
                message = "\n\n" + message
        self._stream_emitted_text = True
        try:
            self._stream_callback(message)
        except Exception:
            pass

    def _forward_structured(self, event_type: str, message: str, data: dict[str, Any]) -> None:
        if self._event_callback is None:
            return
        structured = self._structured_event(event_type, message, data)
        if structured is None:
            return
        try:
            self._event_callback(structured)
        except Exception:
            pass

    def _session_id(self) -> str:
        return str(getattr(self._session, "session_id", "") or "")

    def _tool_call_start_envelope(self, data: dict[str, Any]) -> dict[str, Any]:
        name = str(data.get("name") or "")
        risk_tier = "never_auto" if name in NEVER_AUTO_APPROVE else "risky"
        event = ToolCallStart(
            session_id=self._session_id(),
            tool_call_id=str(data.get("tool_call_id") or ""),
            name=name,
            args_summary=args_summary_for(name, data.get("arguments")),
            risk_tier=risk_tier,
        )
        return control_event_to_dict(event)

    # Preview source: ``data["result"]`` (up to 2,000 chars from the executor)
    # when present, else the event message. ``result_truncated`` marks "a
    # fuller result exists in the session journal" — either the executor
    # itself truncated at 2,000 chars or the envelope cap kicked in; the
    # expand action then fetches it via ``sessions/tool_result``.
    def _tool_call_end_envelope(self, data: dict[str, Any], message: str) -> dict[str, Any]:
        name = str(data.get("name") or "")
        status = _end_status(data.get("status"))
        source_text = str(data.get("result") or message or "")
        preview = result_preview_full(name, source_text)
        event = ToolCallEnd(
            session_id=self._session_id(),
            tool_call_id=str(data.get("tool_call_id") or ""),
            name=name,
            status=status,
            duration_ms=data.get("duration_ms"),
            result_preview=preview,
            # A fuller result exists in the session journal — expand fetches it.
            result_truncated=_result_exceeds_envelope(data.get("result"), source_text),
        )
        return control_event_to_dict(event)

    def _forward_agent_event(self, event: CompanionEvent) -> None:
        if self._on_event is None:
            return
        event_type = getattr(event.event_type, "value", event.event_type)
        # Skip event types already delivered via the dedicated stream /
        # thinking / structured-tool / error channels; forwarding them as
        # generic session/event payloads double-renders in clients.
        if event_type in _NOISY_SESSION_EVENT_TYPES:
            return
        try:
            self._on_event(_AcpForwardEvent(event))
        except Exception:
            pass

    def _reasoning_envelope(self, event_type: str, message: str) -> dict[str, Any] | None:
        """Live reasoning as the canonical thinking envelope, the shape the
        JSONL bridge sends too (``_emit_thinking``): deltas carry a sequence
        number within the open block, the block end resets it."""
        if event_type == CompanionEventType.REASONING_DONE.value:
            self._reasoning_seq = 0
            done = ThinkingDone(session_id=self._session_id(), summary_hint="")
            return control_event_to_dict(done)
        if event_type != CompanionEventType.REASONING_DELTA.value or not message:
            return None
        seq = getattr(self, "_reasoning_seq", 0)
        self._reasoning_seq = seq + 1
        return control_event_to_dict(
            ThinkingDelta(session_id=self._session_id(), seq=seq, text=message, partial=True)
        )

    def _structured_event(
        self, event_type: str, message: str, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        reasoning = self._reasoning_envelope(event_type, message)
        if reasoning is not None:
            return reasoning
        if event_type == CompanionEventType.TOOL_CALL.value:
            return self._tool_call_start_envelope(data)
        if event_type == CompanionEventType.TOOL_RESULT.value:
            return self._tool_call_end_envelope(data, message)
        if event_type == CompanionEventType.THINKING.value and message:
            return {"type": "thinking_chunk", "content": message}
        if event_type == CompanionEventType.ERROR.value:
            # Tool-executor failures (5xx/403 from the platform) arrive here
            # (agent.py _invoke_tool_executor) — complete the open card with
            # the error text: without this, failed tools only show up via the
            # agent's own narration (round-2 transcript evidence).
            name = data.get("name") or getattr(self, "_last_tool_name", None)
            if name:
                end_data = dict(data)
                end_data["name"] = name
                end_data.setdefault("status", "error")
                return self._tool_call_end_envelope(end_data, message)
            return {"type": "error", "message": message}
        return None

    def _inject_editor_tools(self) -> None:
        """Expose editor-governed tools as Companion tool definitions."""
        callback = self._editor_tool_call
        if callback is None:
            return

        existing = [
            tool
            for tool in self._session.tools
            if not (isinstance(tool, dict) and tool.get("_acp_editor_tool"))
        ]
        injected = [
            definition
            for tool in self._editor_tools
            if (definition := self._editor_tool_definition(tool, callback)) is not None
        ]
        self._session.tools = existing + injected

    @staticmethod
    def _editor_attr(tool: Any, key: str) -> Any:
        value = getattr(tool, key, None)
        if value is not None:
            return value
        if isinstance(tool, dict):
            return tool.get(key)
        return None

    @classmethod
    def _editor_tool_definition(
        cls,
        tool: Any,
        callback: Callable[[str, dict[str, Any]], Any],
    ) -> dict[str, Any] | None:
        name = cls._editor_attr(tool, "name")
        if not name:
            return None
        description = cls._editor_attr(tool, "description") or f"Editor tool: {name}"
        tool_name = str(name)

        def execute(**kwargs: Any) -> str:
            result = callback(tool_name, dict(kwargs))
            return "" if result is None else str(result)

        return {
            "name": tool_name,
            "description": str(description),
            "parameters": {"type": "object", "properties": {}},
            "execute": execute,
            "_acp_editor_tool": True,
        }


def create_companion_acp_session(
    client: Any,
    name: str | None = None,
    model: str | None = None,
    *,
    store: CompanionSessionStore | None = None,
) -> CompanionAcpSession:
    session_store = store or CompanionSessionStore()
    session = session_store.create_session(client, name=name, model=model)
    return CompanionAcpSession(session, store=session_store)


def load_companion_acp_session(
    client: Any,
    session_id: str,
    *,
    store: CompanionSessionStore | None = None,
) -> CompanionAcpSession:
    session_store = store or CompanionSessionStore()
    session = session_store.load(session_id, client)
    if session is None:
        raise FileNotFoundError(f"Companion session not found: {session_id}")
    return CompanionAcpSession(session, store=session_store)


def list_companion_acp_sessions(
    *,
    store: CompanionSessionStore | None = None,
) -> list[dict[str, Any]]:
    session_store = store or CompanionSessionStore()
    return session_store.list_sessions()


def set_companion_session_pinned(
    session_id: str,
    pinned: bool,
    *,
    store: CompanionSessionStore | None = None,
) -> dict[str, Any]:
    """Store-direct pin/unpin for ACP sessions/pin|unpin (C2).

    Works for sessions not currently loaded anywhere (the pin flag lives on
    the sessions row, never inside metadata JSON or the live object).
    """
    session_store = store or CompanionSessionStore()
    updated = session_store.set_pinned(session_id, pinned)
    if not updated:
        raise ValueError(f"unknown session: {session_id}")
    return {"session_id": session_id, "pinned": pinned}
