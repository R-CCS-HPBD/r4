"""r4 editor JSON-RPC rail (module name kept as ``acp``; see ADR-042).

A CUSTOM JSON-RPC transport that drives r4 from editors (the VS Code
extension is the reference consumer). Despite the module name and the
historical header, this is NOT the Agent Client Protocol standard: none of
``initialize``, ``authenticate``, ``session/new``, ``session/load``,
``session/prompt``, ``session/cancel``, ``session/update``,
``fs/*`` or ``terminal/*`` are implemented — editors that expect standard
ACP cannot attach. Standard conformance, when wanted, is an adapter in
front of ``_AgentSessionRuntime`` (ADR-042 §2), not this rail.

Truly shared with ACP only the framing: JSON-RPC 2.0, newline-delimited,
over stdin/stdout of a subprocess.

Protocol messages:
  → Request:  {"jsonrpc": "2.0", "id": N, "method": "...", "params": {...}}
  ← Response: {"jsonrpc": "2.0", "id": N, "result": {...}}
  ← Notify:   {"jsonrpc": "2.0", "method": "...", "params": {...}}
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, Optional, Protocol

from .agent.core import AgentCancelledError
from .errors import R4Error

logger = logging.getLogger(__name__)


# ── Protocol types ──────────────────────────────────────────────────────────


@dataclass
class ACPRequest:
    """An incoming JSON-RPC request."""

    id: int | str
    method: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ACPResponse:
    """An outgoing JSON-RPC response."""

    id: int | str
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"jsonrpc": "2.0", "id": self.id}
        if self.error is not None:
            d["error"] = self.error
        else:
            d["result"] = self.result
        return d


@dataclass
class ACPNotification:
    """An outgoing JSON-RPC notification (no id, no response expected)."""

    method: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "method": self.method, "params": self.params}


# ── Error codes (JSON-RPC + ACP extensions) ─────────────────────────────────

ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
ERR_CANCELLED = -32800


# ── Thinking blocks ─────────────────────────────────────────────────────────

# Models emit reasoning as <think>...</think>. Some wrap the tags in backticks,
# so both forms are accepted. DOTALL because reasoning spans lines.
_THINK_BLOCK_RE = re.compile(r"`?<think>`?(.*?)`?</think>`?", re.DOTALL)


def _split_thinking(text: str) -> tuple[list[str], str]:
    """Separate reasoning blocks from the visible answer.

    Returns (thinking_blocks, visible_text). An unterminated <think> with no
    closing tag is left untouched so partial output is never silently dropped.
    """
    blocks = [block.strip() for block in _THINK_BLOCK_RE.findall(text) if block.strip()]
    visible = _THINK_BLOCK_RE.sub("", text).strip()
    return blocks, visible


_THINK_OPEN_RE = re.compile(r"`?<think>`?")
_THINK_CLOSE_RE = re.compile(r"`?</think>`?")
# Longest tag span (`` `</think>` ``); longer tails keep a carry so a tag
# split across two stream chunks still matches once the rest arrives.
_TAG_SPAN = 11
_TAG_PREFIX_RE = re.compile(r"`?<(?:/?t(?:h(?:i(?:n(?:k)?)?)?)?)?$")


class _LiveThinkingTracker:
    """Emit session/thinking notifications while a turn is still streaming.

    Long reasoning phases previously produced a silent wait: the chat handler
    buffered the whole turn and only forwarded think blocks at completion.
    The tracker emits each completed block immediately, and emits partial
    snapshots (``partial: true``) of the currently-open block whenever it
    grows by ``_PARTIAL_MIN_CHARS`` — the webview shows those expanded and
    auto-collapses them once the block closes.
    """

    _PARTIAL_MIN_CHARS = 240

    def __init__(self, runtime, session: "AgentSessionProtocol", request_token: str) -> None:
        self._runtime = runtime
        self._session_id = session.id
        self._request_token = request_token
        self._state = "outside"
        self._open_content = ""
        self._last_partial_len = 0
        self._carry = ""
        self._seq = 0  # per-turn monotonic index for canonical thinking_delta

    def push(self, chunk: str) -> None:
        if chunk:
            self._step(self._carry + chunk)

    def _step(self, chunk: str) -> None:
        if self._state == "outside":
            self._enter_if_tag(chunk)
        else:
            self._grow(chunk)

    def _hold_split_tag_tail(self, chunk: str) -> tuple[str, str]:
        """Split off a trailing partial tag prefix for the next push."""
        tail = chunk[-_TAG_SPAN:]
        match = _TAG_PREFIX_RE.search(tail)
        if match is None:
            return chunk, ""
        return chunk[: len(chunk) - len(match.group())], match.group()

    def _enter_if_tag(self, chunk: str) -> None:
        match = _THINK_OPEN_RE.search(chunk)
        if match is None:
            self._carry = self._hold_split_tag_tail(chunk)[1]
            return  # answer text only — handled by the existing collection path
        self._carry = ""
        self._state = "inside"
        self._open_content = ""
        self._last_partial_len = 0
        self._step(chunk[match.end() :])

    def _grow(self, chunk: str) -> None:
        match = _THINK_CLOSE_RE.search(chunk)
        if match is None:
            safe, tail = self._hold_split_tag_tail(chunk)
            self._carry = tail
            self._open_content += safe
            self._maybe_emit_partial()
            return
        self._carry = ""
        self._open_content += chunk[: match.start()]
        content = self._open_content.strip()
        if content:
            self._emit(content, partial=False)
        self._state = "outside"
        self._step(chunk[match.end() :])

    def _maybe_emit_partial(self) -> None:
        if len(self._open_content) - self._last_partial_len < self._PARTIAL_MIN_CHARS:
            return
        content = self._open_content.strip()
        if not content:
            return
        self._emit(content, partial=True)
        self._last_partial_len = len(self._open_content)

    def finish(self) -> None:
        """Flush any unterminated block so no reasoning is ever dropped."""
        if self._carry:
            self._step(self._carry)
            self._carry = ""
        if self._state == "inside" and self._open_content.strip():
            self._emit(self._open_content.strip(), partial=False)
        self._state = "outside"
        self._open_content = ""

    def _emit(self, content: str, *, partial: bool) -> None:
        # Canonical first: clients that decode it suppress the legacy event
        # (same chunk arrives twice otherwise).
        self._emit_canonical(content, partial=partial)
        self._runtime.server.notify(
            "session/thinking",
            {
                "session_id": self._session_id,
                "request_id": self._request_token,
                "content": content,
                "partial": partial,
            },
        )

    def _emit_canonical(self, content: str, *, partial: bool) -> None:
        # Canonical T1 transparency envelope alongside legacy session/thinking
        # (think-markup split once, here in python; payload only, consumed by
        # the same client-side decode as all other canonical envelopes).
        method, packed = _canonical_thinking(self._session_id, content, partial, self._seq)
        if packed is None:
            return
        self._seq += 1
        self._runtime.server.notify(
            method,
            {
                "session_id": self._session_id,
                "request_id": self._request_token,
                "payload": packed,
            },
        )


def _canonical_thinking(
    session_id: str, content: str, partial: bool, seq: int
) -> tuple[str, Dict[str, Any] | None]:
    """Build the canonical envelope pair for one thinking chunk."""
    from .companion.control_schema import ThinkingDelta, ThinkingDone
    from .companion.control_schema import to_dict as control_event_to_dict

    if partial:
        event = ThinkingDelta(session_id=session_id, seq=seq, text=content, partial=True)
        return "session/thinking_delta", control_event_to_dict(event)["payload"]
    done = ThinkingDone(session_id=session_id, summary_hint="")
    return "session/thinking_done", control_event_to_dict(done)["payload"]


# ── Handler type ────────────────────────────────────────────────────────────

MethodHandler = Callable[[Dict[str, Any]], Any]
TEXT_CHUNK_SIZE = 512


def _internal_error_message(exc: Exception) -> str:
    """User-facing text for a -32603 reply.

    R4Error's message and hint are CLI-facing curated strings, so forwarding
    them makes webview errors actionable (e.g. "No model selected"). Any
    other exception type is scrubbed because its text may embed secrets or
    tokens constructed at runtime.
    """
    base = "Internal error"
    if not isinstance(exc, R4Error):
        return base
    text = (exc.message or "").strip()
    hint = exc.hint
    if isinstance(hint, str) and hint.strip():
        text = f"{text} {hint.strip()}" if text else hint.strip()
    return f"{base}: {text}" if text else base


class AgentSessionProtocol(Protocol):
    """Agent session surface required by the editor protocol."""

    id: str
    name: str

    def chat(self, message: str, auto_save: bool = True) -> str: ...

    def on_event(self, callback: Callable[[Any], None]) -> None: ...

    def set_editor_tools(
        self, tools: list[Any], callback: Callable[[str, Dict[str, Any]], Any]
    ) -> None: ...

    def cancel(self) -> None: ...

    def prepare_request(self) -> None: ...

    def is_cancelled(self) -> bool: ...

    def set_stream_callback(self, callback: Callable[[str], None] | None) -> None: ...


SessionFactory = Callable[[Any, str | None, str | None], AgentSessionProtocol]
SessionLoader = Callable[[Any, str], AgentSessionProtocol]
SessionLister = Callable[[], list[Dict[str, Any]]]


# ── ACP Server ──────────────────────────────────────────────────────────────


class ACPServer:
    """JSON-RPC ACP server over stdin/stdout.

    Register methods, then call ``serve()`` to enter the main loop.
    """

    def __init__(self) -> None:
        self._methods: Dict[str, MethodHandler] = {}
        self._running = False
        self._next_client_request_id = -1
        self._async_methods: set[str] = set()
        self._cancelled_requests: set[int | str] = set()
        self._pending_client_requests: Dict[int, tuple[threading.Event, Dict[str, Any]]] = {}
        self._cancel_callbacks: Dict[int | str, Callable[[], None]] = {}
        self._request_context = threading.local()
        self._state_lock = threading.RLock()
        self._write_lock = threading.Lock()

    def method(self, name: str) -> Callable[[MethodHandler], MethodHandler]:
        """Decorator to register a method handler."""

        def decorator(fn: MethodHandler) -> MethodHandler:
            self._methods[name] = fn
            return fn

        return decorator

    def register(self, name: str, handler: MethodHandler) -> None:
        """Register a method handler."""
        self._methods[name] = handler

    def register_async(self, name: str, handler: MethodHandler) -> None:
        """Register a handler that runs off the stdin reader thread."""
        self._methods[name] = handler
        self._async_methods.add(name)

    @property
    def current_request_id(self) -> int | str | None:
        return getattr(self._request_context, "request_id", None)

    def register_cancel_callback(self, request_id: int | str, callback: Callable[[], None]) -> None:
        with self._state_lock:
            self._cancel_callbacks[request_id] = callback
            already_cancelled = request_id in self._cancelled_requests
        if already_cancelled:
            callback()

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a notification to the client."""
        n = ACPNotification(method=method, params=params or {})
        self._write(n.to_dict())

    def request_client(self, method: str, params: Dict[str, Any], *, timeout: float = 30) -> Any:
        """Send a request to the editor and wait for its response."""
        completed = threading.Event()
        response: Dict[str, Any] = {}
        with self._state_lock:
            request_id = self._next_client_request_id
            self._next_client_request_id -= 1
            self._pending_client_requests[request_id] = (completed, response)
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        if not completed.wait(timeout=timeout):
            with self._state_lock:
                self._pending_client_requests.pop(request_id, None)
            request_token = params.get("request_id")
            if isinstance(request_token, str):
                self.notify("editor/toolCancel", {"request_id": request_token})
            raise RuntimeError("Editor request timed out")
        if response.get("error") is not None:
            raise RuntimeError("Editor request denied")
        return response.get("result")

    def serve(self) -> None:
        """Enter the main serve loop (blocking, reads from stdin)."""
        self._running = True
        while self._running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break  # EOF
                line = line.strip()
                if not line:
                    continue
                self._handle_line(line)
            except KeyboardInterrupt:
                break
            except Exception as exc:
                logger.error("ACP server error: %s", exc)

    def stop(self) -> None:
        """Stop the serve loop."""
        self._running = False

    def _handle_line(self, line: str) -> None:
        """Parse and dispatch a single JSON-RPC message."""
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            self._write_error(0, ERR_PARSE, "Parse error")
            return
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            req_id = msg.get("id", 0) if isinstance(msg, dict) else 0
            self._write_error(req_id, ERR_INVALID_REQUEST, "Invalid request")
            return
        if "method" not in msg and self._resolve_client_response(msg):
            return

        self._route_message(msg)

    def _route_message(self, msg: Dict[str, Any]) -> None:
        method = msg.get("method", "")
        params = msg.get("params", {})
        req_id = msg.get("id")
        if not isinstance(method, str) or not isinstance(params, dict):
            self._write_error(req_id or 0, ERR_INVALID_PARAMS, "method and params must be valid")
            return
        if req_id is None:
            self._route_notification(method, params)
            return
        if self._running and method in self._async_methods:
            threading.Thread(
                target=self._dispatch_request,
                args=(req_id, method, params),
                daemon=True,
            ).start()
        else:
            self._dispatch_request(req_id, method, params)

    def _route_notification(self, method: str, params: Dict[str, Any]) -> None:
        if method == "$/cancelRequest":
            cancelled_id = params.get("id")
            if isinstance(cancelled_id, (int, str)):
                with self._state_lock:
                    self._cancelled_requests.add(cancelled_id)
                    callback = self._cancel_callbacks.get(cancelled_id)
                if callback:
                    callback()
            return
        self._dispatch_notification(method, params)

    def _resolve_client_response(self, message: Dict[str, Any]) -> bool:
        request_id = message.get("id")
        if not isinstance(request_id, int):
            return False
        with self._state_lock:
            pending = self._pending_client_requests.pop(request_id, None)
        if pending is None:
            return False
        completed, response = pending
        response.update(message)
        completed.set()
        return True

    def _dispatch_notification(self, method: str, params: Dict[str, Any]) -> None:
        handler = self._methods.get(method)
        if handler is None:
            return
        try:
            handler(params)
        except Exception as exc:
            logger.warning("Notification handler error for %s: %s", method, exc)

    def _dispatch_request(self, req_id: int | str, method: str, params: Dict[str, Any]) -> None:
        handler = self._methods.get(method)
        if handler is None:
            self._write_error(req_id, ERR_METHOD_NOT_FOUND, f"Unknown method: {method}")
            return

        try:
            self._request_context.request_id = req_id
            result = handler(params)
            with self._state_lock:
                cancelled = req_id in self._cancelled_requests
            if cancelled:
                self._write_error(req_id, ERR_CANCELLED, "Request cancelled")
            else:
                self._write(ACPResponse(id=req_id, result=result).to_dict())
        except AgentCancelledError:
            self._write_error(req_id, ERR_CANCELLED, "Request cancelled")
        except TypeError as exc:
            self._write_error(req_id, ERR_INVALID_PARAMS, str(exc))
        except Exception as exc:
            logger.exception("ACP request failed for method %s", method)
            self._write_error(req_id, ERR_INTERNAL, _internal_error_message(exc))
        finally:
            with self._state_lock:
                self._cancelled_requests.discard(req_id)
                self._cancel_callbacks.pop(req_id, None)
            self._request_context.request_id = None

    def _write_error(self, req_id: int | str, code: int, message: str) -> None:
        self._write(ACPResponse(id=req_id, error={"code": code, "message": message}).to_dict())

    def _write(self, data: Dict[str, Any]) -> None:
        """Write a JSON message to stdout."""
        with self._write_lock:
            sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()


# ── Default ACP server with r4 methods ──────────────────────────────────────

# Static ACP capability set from the initialize handshake (task-1 logging).
_ACP_CAPABILITIES: Dict[str, Any] = {
    "chat": True,
    "sessions": True,
    "session_events": True,
    "tools": True,
    "tool_approval": True,
    "ask_user": True,
    "models": True,
    "skills": True,
    "recipes": True,
    "projects": True,
    "workflows": True,
}


def _legacy_session_factory(
    client: Any, name: str | None, model: str | None
) -> AgentSessionProtocol:
    """Legacy SciWorkflow AgentSession factory (kept for opt-in / tests)."""
    from .agent import AgentConfig, AgentSession

    config = AgentConfig(model=model) if model else None
    return AgentSession.create(client, name=name, config=config)


def _legacy_session_loader(client: Any, session_id: str) -> AgentSessionProtocol:
    from .agent import AgentSession

    return AgentSession.load(client, session_id)


def _legacy_session_lister() -> list[Dict[str, Any]]:
    from .agent import AgentSession

    return AgentSession.list_sessions()


def _default_session_factory(
    client: Any, name: str | None, model: str | None
) -> AgentSessionProtocol:
    from .companion.acp_session import create_companion_acp_session

    return create_companion_acp_session(client, name=name, model=model)


def _default_session_loader(client: Any, session_id: str) -> AgentSessionProtocol:
    from .companion.acp_session import load_companion_acp_session

    return load_companion_acp_session(client, session_id)


def _default_session_lister() -> list[Dict[str, Any]]:
    from .companion.acp_session import list_companion_acp_sessions

    return list_companion_acp_sessions()


def _require_string(params: Dict[str, Any], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{name} must be a non-empty string")
    return value.strip()


class _AgentSessionRuntime:
    def __init__(
        self,
        server: ACPServer,
        client_factory: Callable[[], Any] | None,
        session_factory: SessionFactory,
        session_loader: SessionLoader,
    ) -> None:
        self.server = server
        self.client_factory = client_factory
        self.session_factory = session_factory
        self.session_loader = session_loader
        self.sessions: Dict[str, AgentSessionProtocol] = {}
        self.editor_tools: list[Any] = []
        self.active_sessions: Dict[str, str] = {}
        self._active_sessions_lock = threading.Lock()
        # Chat-thread -> session id routing for the process-global ask_user
        # callback (begin_chat/end_chat maintain it on the chat thread).
        self._ask_user_threads: Dict[int, str] = {}
        self._client: Any = None
        try:
            from .agent.tools import set_ask_user_callback

            set_ask_user_callback(self._on_ask_user)
        except Exception:  # pragma: no cover - never block server init
            logger.exception("failed to wire ask_user callback")

    def client(self) -> Any:
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory()
        from .client import Client

        return Client()

    def create(self, name: str | None, model: str | None) -> AgentSessionProtocol:
        session = self.remember(self.session_factory(self.client(), name, model))
        self._attach_acp_context(session)
        save = getattr(session, "save", None)
        if callable(save):
            save()
        return session

    def _attach_acp_context(self, session: AgentSessionProtocol) -> None:
        """Hand the session the ACP handshake context for toolset logging
        (round-3 task-1). Duck-typed: only CompanionAcpSession consumes it."""
        setter = getattr(session, "set_acp_context", None)
        if not callable(setter):
            return
        config = getattr(self.client(), "config", None)
        setter(
            capabilities=_ACP_CAPABILITIES,
            editor_tool_names=[tool.name for tool in self.editor_tools],
            gateway_url=getattr(config, "gateway_url", None),
            umrs_host=getattr(config, "umrs_server_host", None),
        )

    def get(self, session_id: str) -> AgentSessionProtocol:
        session = self.sessions.get(session_id)
        if session is None:
            session = self.remember(self.session_loader(self.client(), session_id))
        return session

    def rename(self, session: AgentSessionProtocol, name: str) -> None:
        renamer = getattr(session, "rename", None)
        if callable(renamer):
            renamer(name)

    def set_model(self, session: AgentSessionProtocol, model: str | None) -> None:
        setter = getattr(session, "set_model", None)
        if callable(setter):
            setter(model)

    def set_mode(self, session: AgentSessionProtocol, mode: str | None) -> None:
        """Set the interaction mode (ask/plan/autopilot) when supported."""
        setter = getattr(session, "set_mode", None)
        if callable(setter):
            setter(mode)

    def delete(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is None:
            session = self.session_loader(self.client(), session_id)
        with self._active_sessions_lock:
            self.active_sessions.pop(session_id, None)
        remover = getattr(session, "delete", None)
        if callable(remover):
            remover()

    def remember(self, session: AgentSessionProtocol) -> AgentSessionProtocol:
        self.sessions[session.id] = session
        session.on_event(lambda event: self._forward_event(session.id, event))
        self._attach_editor_tools(session)
        companion = getattr(session, "companion", None)
        if companion is not None:
            companion.permissions.set_approval_callback(
                lambda tool_name, description, arguments, sid=session.id: (
                    self._on_companion_tool_approval(sid, tool_name, description, arguments)
                )
            )
        return session

    def configure_editor_tools(self, tools: list[Dict[str, Any]]) -> None:
        self.editor_tools = [
            SimpleNamespace(name=tool["name"], description=tool["description"]) for tool in tools
        ]
        for session in self.sessions.values():
            self._attach_editor_tools(session)

    def _attach_editor_tools(self, session: AgentSessionProtocol) -> None:
        setter = getattr(session, "set_editor_tools", None)
        if callable(setter):
            setter(
                self.editor_tools, lambda name, args: self._call_editor_tool(session.id, name, args)
            )

    def _call_editor_tool(self, session_id: str, name: str, arguments: Dict[str, Any]) -> Any:
        request_token = self.active_sessions.get(session_id)
        if request_token is None:
            raise RuntimeError("Editor tool request has no active session")
        self._notify_tool_state(session_id, request_token, name, "pending")
        tool_call_id = str(uuid.uuid4())
        try:
            result = self.server.request_client(
                "editor/toolCall",
                {
                    "session_id": session_id,
                    "request_id": request_token,
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "arguments": arguments,
                },
            )
        except Exception:
            raise
        return result

    def _on_companion_tool_approval(
        self,
        session_id: str,
        tool_name: str,
        description: str,
        arguments: Dict[str, Any],
    ) -> bool:
        """Ask the editor to approve a risky Companion tool call; block until it replies."""
        request_token = self.active_sessions.get(session_id)
        if request_token is None:
            return False
        approval_id = str(uuid.uuid4())
        from .companion.approval_answers import approval_options
        from .companion.permissions import approval_fence, approving_agent

        fence = approval_fence()
        # ADR-104 D-3: the editor offers these answers (once / always / reject).
        options = approval_options(tool_name, fenced=bool(fence))
        try:
            result = self.server.request_client(
                "session/toolApproval",
                {
                    "session_id": session_id,
                    "request_id": request_token,
                    "approval_id": approval_id,
                    "tool": tool_name,
                    "description": description,
                    "arguments": arguments,
                    # ADR-092 slice 2: which agent asked ("" for the lead).
                    "agent": approving_agent(),
                    # Fix 2026-10-30: a fenced ask is answered per call (an
                    # "always" on it grants nothing: approve_for_session).
                    "fenced": bool(fence),
                    "fence_reason": fence,
                    "options": options,
                },
                timeout=600,
            )
        except Exception:
            logger.debug("Companion tool approval request failed", exc_info=True)
            return False
        if not isinstance(result, dict):
            return False
        return self._apply_tool_decision(session_id, tool_name, result.get("decision"), options)

    def _apply_tool_decision(
        self, session_id: str, tool_name: str, raw: Any, options: list[str]
    ) -> bool:
        """One answer, read as every surface reads it (ADR-104 D-3)."""
        from .companion.agent import note_user_rejection
        from .companion.approval_answers import ALWAYS, approves, normalize_decision

        decision = normalize_decision(raw, options)
        if decision == ALWAYS:
            companion = getattr(self.sessions.get(session_id), "companion", None)
            if companion is not None:
                companion.permissions.approve_for_session(tool_name)
        if not approves(decision):
            # A person's "no", named as theirs, never as a policy block (chat#8).
            note_user_rejection(tool_name)
        return approves(decision)

    def _notify_tool_state(
        self, session_id: str, request_token: str, name: str, status: str
    ) -> None:
        self.server.notify(
            "session/toolState",
            {
                "session_id": session_id,
                "request_id": request_token,
                "tool": name,
                "status": status,
            },
        )

    def _on_ask_user(self, question: str, options: list[str]) -> str:
        """Route an agent ``ask_user`` call to the editor; block for the answer.

        The process-global ask_user callback carries no session context, so
        the session is resolved from the chat thread that invoked the tool
        (registered in begin_chat, cleared in end_chat).
        """
        session_id, request_token = self._ask_user_context()
        if request_token is None or session_id is None:
            return "[ask_user unavailable — no active interactive chat]"
        session = self.sessions.get(session_id)
        if getattr(session, "interaction_mode", "") == "autopilot":
            from .companion.agent_settings import ask_user_auto_answer

            return ask_user_auto_answer()
        return self._request_user_answer(session_id, request_token, question, options)

    def _ask_user_context(self) -> tuple[str | None, str | None]:
        """Current chat thread's (session_id, request_token), if any."""
        session_id = self._ask_user_threads.get(threading.get_ident())
        if session_id is None:
            return None, None
        return session_id, self.active_sessions.get(session_id)

    def _request_user_answer(
        self, session_id: str, request_token: str, question: str, options: list[str]
    ) -> str:
        try:
            result = self.server.request_client(
                "session/askUser",
                {
                    "session_id": session_id,
                    "request_id": request_token,
                    "question": question or "",
                    "options": [str(o) for o in (options or []) if str(o).strip()],
                },
                timeout=600,
            )
        except Exception:
            logger.debug("Companion ask_user request failed", exc_info=True)
            return "[ask_user cancelled]"
        if not isinstance(result, dict):
            return "[ask_user cancelled]"
        return str(result.get("answer") or "")

    def begin_chat(self, session: AgentSessionProtocol, request_token: str) -> bool:
        with self._active_sessions_lock:
            if session.id in self.active_sessions:
                return False
            session.prepare_request()
            self.active_sessions[session.id] = request_token
            self._ask_user_threads[threading.get_ident()] = session.id
            request_id = self.server.current_request_id
            if request_id is not None:
                self.server.register_cancel_callback(
                    request_id, lambda: self._cancel_session(session, request_token)
                )
            return True

    def _cancel_session(self, session: AgentSessionProtocol, request_token: str) -> None:
        session.cancel()
        self.server.notify("editor/toolCancel", {"request_id": request_token})

    def end_chat(self, session_id: str) -> None:
        with self._active_sessions_lock:
            self.active_sessions.pop(session_id, None)
            ident = threading.get_ident()
            if self._ask_user_threads.get(ident) == session_id:
                self._ask_user_threads.pop(ident, None)

    def _forward_event(self, session_id: str, event: Any) -> None:
        request_token = self.active_sessions.get(session_id)
        if request_token is None:
            return
        session = self.sessions.get(session_id)
        if session is None or session.is_cancelled():
            return
        payload = event.to_dict() if hasattr(event, "to_dict") else {"message": str(event)}
        self.server.notify(
            "session/event",
            {"session_id": session_id, "request_id": request_token, "event": payload},
        )


def _register_lifecycle_methods(server: ACPServer) -> None:
    @server.method("initialize")
    def initialize(params: Dict[str, Any]) -> Dict[str, Any]:
        from .source_identity import current_source_identity

        return {
            "name": "r4",
            "version": "0.2.0",
            "protocol_version": 1,
            "capabilities": dict(_ACP_CAPABILITIES),
            # Round-3 task 7: source identity for stale-child detection —
            # the extension recomputes on its side and warns on mismatch.
            "source": current_source_identity().to_dict(),
        }

    @server.method("shutdown")
    def shutdown(params: Dict[str, Any]) -> None:
        server.stop()


def _optional_string(params: Dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _sessions_create(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    name = _optional_string(params, "name")
    model = _optional_string(params, "model")
    return _session_result(runtime.create(name, model))


def _sessions_rename(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    session = runtime.get(_require_string(params, "session_id"))
    runtime.rename(session, _require_string(params, "name"))
    return _session_result(session)


def _sessions_set_model(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    session = runtime.get(_require_string(params, "session_id"))
    model = _optional_string(params, "model")
    runtime.set_model(session, model)
    return _session_result(session)


def _sessions_set_mode(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    session = runtime.get(_require_string(params, "session_id"))
    mode = _optional_string(params, "mode")
    runtime.set_mode(session, mode)
    return _session_result(session)


def _register_session_methods(
    server: ACPServer, runtime: _AgentSessionRuntime, session_lister: SessionLister
) -> None:
    @server.method("sessions/create")
    def sessions_create(params: Dict[str, Any]) -> Dict[str, Any]:
        return _sessions_create(runtime, params)

    @server.method("sessions/list")
    def sessions_list(params: Dict[str, Any]) -> Dict[str, Any]:
        return {"sessions": session_lister()}

    @server.method("sessions/load")
    def sessions_load(params: Dict[str, Any]) -> Dict[str, Any]:
        return _session_result(runtime.get(_require_string(params, "session_id")))

    @server.method("shell/cancel")
    def shell_cancel(params: Dict[str, Any]) -> Dict[str, Any]:
        """W5b Cancel-chip endpoint: kill a live shell stream by id.

        The webview Cancel chip posts stream_id here; we SIGKILL the
        process group registered under that id and return the new state.
        Maps to the same pgid kill that `shell_stream_cancel` (LLM tool)
        triggers, but without involving the model.
        """
        from r4.companion.streaming_shell import cancel_stream

        stream_id = _require_string(params, "stream_id")
        killed = cancel_stream(stream_id)
        return {
            "stream_id": stream_id,
            "status": "cancelled" if killed else "not-found",
        }

    _register_session_pin_compact(server)
    _register_session_compact_methods(server, runtime)
    _register_session_set_commands(server, runtime)


def _register_session_pin_compact(server: ACPServer) -> None:
    """Register C2 pin/unpin + C4 compact verbs (split out of the main
    _register_session_methods to stay under the complexity budget).
    Runtime-independent: pins hit the store directly, compact locates the
    live session via the runtime inside _sessions_compact."""

    @server.method("sessions/pin")
    def sessions_pin(params: Dict[str, Any]) -> Dict[str, Any]:
        from .companion.acp_session import set_companion_session_pinned

        return set_companion_session_pinned(_require_string(params, "session_id"), True)

    @server.method("sessions/unpin")
    def sessions_unpin(params: Dict[str, Any]) -> Dict[str, Any]:
        from .companion.acp_session import set_companion_session_pinned

        return set_companion_session_pinned(_require_string(params, "session_id"), False)


def _register_session_compact_methods(server: ACPServer, runtime: _AgentSessionRuntime) -> None:
    """C4: /compact through the live companion session (separate from pin verbs).

    Same semantics as the bridge verb (session.compact) — user trigger, no
    needs_compaction threshold check.
    """

    @server.method("sessions/compact")
    def sessions_compact(params: Dict[str, Any]) -> Dict[str, Any]:
        from .companion.session_ops import compact_history

        session = runtime.get(_require_string(params, "session_id"))
        core = getattr(session, "_session", session)
        if not hasattr(core, "messages") or not hasattr(core, "context"):
            raise TypeError("sessions/compact requires an active companion session")
        result = compact_history(core, keep_recent=int(params.get("keep_recent", 6)))
        if result.get("changed"):
            save = getattr(session, "save", None)
            if callable(save):
                save()
        return result


def _register_session_set_commands(server: ACPServer, runtime: _AgentSessionRuntime) -> None:
    @server.method("sessions/rename")
    def sessions_rename(params: Dict[str, Any]) -> Dict[str, Any]:
        return _sessions_rename(runtime, params)

    @server.method("sessions/set_model")
    def sessions_set_model(params: Dict[str, Any]) -> Dict[str, Any]:
        return _sessions_set_model(runtime, params)

    @server.method("sessions/set_mode")
    def sessions_set_mode(params: Dict[str, Any]) -> Dict[str, Any]:
        return _sessions_set_mode(runtime, params)

    @server.method("sessions/delete")
    def sessions_delete(params: Dict[str, Any]) -> Dict[str, Any]:
        session_id = _require_string(params, "session_id")
        runtime.delete(session_id)
        return {"deleted": session_id}

    @server.method("sessions/tool_result")
    def sessions_tool_result(params: Dict[str, Any]) -> Dict[str, Any]:
        """Round-3 task 2: fetch one tool call's FULL result text from the
        session's bounded journal by tool_call_id (expand action)."""
        session = runtime.get(_require_string(params, "session_id"))
        call_id = _require_string(params, "tool_call_id")
        companion = getattr(session, "companion", None)
        agent = getattr(companion, "agent", None)
        record = getattr(agent, "tool_result_record", None)
        entry = record(call_id) if callable(record) else None
        if entry is None:
            return {"tool_call_id": call_id, "found": False}
        name, content = entry
        return {"tool_call_id": call_id, "found": True, "name": name, "content": content}

    @server.method("shell/cancel")
    def shell_cancel(params: Dict[str, Any]) -> Dict[str, Any]:
        """W5b Cancel-chip endpoint: kill a live shell stream by id.

        The webview Cancel chip posts stream_id here; we SIGKILL the
        process group registered under that id and return the new state.
        Maps to the same pgid kill that `shell_stream_cancel` (LLM tool)
        triggers, but without involving the model.
        """
        from r4.companion.streaming_shell import cancel_stream

        stream_id = _require_string(params, "stream_id")
        killed = cancel_stream(stream_id)
        return {
            "stream_id": stream_id,
            "status": "cancelled" if killed else "not-found",
        }


def _session_result(session: AgentSessionProtocol) -> Dict[str, Any]:
    messages = []
    for message in getattr(session, "messages", []):
        if isinstance(message, str):
            messages.append({"role": "user", "content": message})
        else:
            messages.append(
                {
                    "role": getattr(message, "role", "assistant"),
                    "content": getattr(message, "content", ""),
                }
            )
    return {
        "session": {
            "id": session.id,
            "name": session.name,
            "model": getattr(session, "model", None),
            "mode": getattr(session, "interaction_mode", None) or "chat",
            "messages": messages,
        }
    }


def _resolve_chat_session(
    runtime: _AgentSessionRuntime, params: Dict[str, Any]
) -> AgentSessionProtocol:
    session_id = params.get("session_id")
    if session_id is None:
        return runtime.create(params.get("name"), params.get("model"))
    session = runtime.get(_require_string(params, "session_id"))
    model = params.get("model")
    if isinstance(model, str) and model.strip():
        runtime.set_model(session, model)
    return session


def _notify_chat_event(
    server: ACPServer,
    session_id: str,
    request_token: str,
    evt: Dict[str, Any],
) -> None:
    """Forward one structured agent event live (tool calls, usage) — C6.

    Previously these were buffered until the turn completed, which left the
    webview showing only a spinner during long tool loops (read as "keeps
    thinking, no return"). Live forwarding renders each tool card as it
    happens. ``evt`` is copied first because ``type`` is popped for the
    notification method name.
    """

    payload = dict(evt)
    evt_type = str(payload.pop("type", "event"))
    server.notify(
        f"session/{evt_type}",
        {"session_id": session_id, "request_id": request_token, **payload},
    )


def _forward_chat_notifications(
    runtime: _AgentSessionRuntime,
    session: AgentSessionProtocol,
    request_token: str,
    think_blocks: list[str],
    response: str,
) -> None:
    # Replay thinking blocks only when nothing was streamed live (non-
    # streaming models); live blocks already went out via the tracker.
    for block in think_blocks:
        runtime.server.notify(
            "session/thinking",
            {"session_id": session.id, "request_id": request_token, "content": block},
        )
    # Finally forward the accumulated assistant text
    runtime.server.notify(
        "session/text",
        {"session_id": session.id, "request_id": request_token, "text": response},
    )


_CHAT_HEARTBEAT_INTERVAL_SEC = 15.0


def _chat_heartbeat(
    server: ACPServer,
    session_id: str,
    request_token: str,
    stop: threading.Event,
) -> None:
    """Emit ``session/progress`` keepalives while a chat turn runs.

    Long knowledge/research turns can exceed the editor's per-request timeout
    even while healthy (tool rounds produce little dedicated live traffic).
    The editor resets its request timer on any notification carrying a
    matching ``request_id``; without keepalives, productive turns were killed
    mid-run ("R4 ACP request timed out: chat/send").
    """

    while not stop.wait(_CHAT_HEARTBEAT_INTERVAL_SEC):
        server.notify(
            "session/progress",
            {"session_id": session_id, "request_id": request_token, "state": "running"},
        )


def _register_chat_method(server: ACPServer, runtime: _AgentSessionRuntime) -> None:
    def chat_send(params: Dict[str, Any]) -> Dict[str, Any]:
        message = _require_string(params, "message")
        request_token = _require_string(params, "request_id")
        session = _resolve_chat_session(runtime, params)
        if not runtime.begin_chat(session, request_token):
            raise ValueError("Session already has an active request")
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=_chat_heartbeat,
            args=(server, session.id, request_token, heartbeat_stop),
            daemon=True,
        )
        heartbeat.start()
        try:
            stream_chunks: list[str] = []

            tracker = _LiveThinkingTracker(runtime, session, request_token)

            def stream_text(text: str) -> None:
                stream_chunks.append(text)
                tracker.push(text)

            def event_handler(evt: Dict[str, Any]) -> None:
                # C6: forward structured notifications (tool call events,
                # usage) live so the webview shows per-tool progress as the
                # agentic loop runs instead of a silent spinner.
                _notify_chat_event(server, session.id, request_token, evt)

            session.set_stream_callback(stream_text)
            session.set_event_callback(event_handler)
            response = session.chat(message)
            tracker.finish()

            accumulated = "".join(stream_chunks)
            source = accumulated if accumulated.strip() else (response or "")
            think_blocks, response = _split_thinking(source)
            if accumulated.strip():
                # Blocks were already streamed live by the tracker — do not
                # replay them (would duplicate the transcript's thinking cards).
                think_blocks = []
            _forward_chat_notifications(
                runtime,
                session,
                request_token,
                think_blocks,
                response,
            )

            return {
                "status": "completed",
                "session_id": session.id,
                "response": response,
            }
        finally:
            heartbeat_stop.set()
            session.set_stream_callback(None)
            runtime.end_chat(session.id)

    server.register_async("chat/send", chat_send)


def _normalize_model_entry(model: Any) -> Dict[str, Any]:
    if isinstance(model, dict):
        return {
            "id": model.get("id") or model.get("model_id") or model.get("name", ""),
            "name": model.get("name", ""),
            "status": model.get("status", ""),
        }
    return {"id": str(model), "name": str(model), "status": ""}


def _models_payload(server: ACPServer, params: Dict[str, Any]) -> Dict[str, Any]:
    del params
    runtime = getattr(server, "_runtime", None)
    if runtime is None:
        return {"models": [], "error": "runtime unavailable"}
    error: str | None = None
    try:
        models = runtime.client().list_models()
    except Exception as exc:  # noqa: BLE001 - surfaced as payload, not silent [] (#212)
        models = []
        error = str(exc)
    payload = {"models": [_normalize_model_entry(model) for model in models]}
    if error:
        payload["error"] = error
    return payload


def _mcp_tool_entry(tool: Any) -> Dict[str, Any] | None:
    name = getattr(tool, "name", "")
    if not name:
        return None
    return {
        "name": name,
        "description": getattr(tool, "description", ""),
        "inputSchema": getattr(tool, "inputSchema", {}),
    }


def _editor_tool_entry(tool: Any) -> Dict[str, Any]:
    return {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", ""),
        "inputSchema": getattr(
            tool,
            "input_schema",
            {"type": "object", "properties": {}},
        ),
    }


def _tools_payload(server: ACPServer, params: Dict[str, Any]) -> Dict[str, Any]:
    del params
    runtime = getattr(server, "_runtime", None)
    if runtime is None:
        return {"tools": [], "error": "runtime unavailable"}
    error: str | None = None
    try:
        mcp_tools = runtime.client().mcp.list_tools()
    except Exception as exc:  # noqa: BLE001 - surfaced as payload, not silent [] (#212)
        mcp_tools = []
        error = str(exc)
    result: list[Dict[str, Any]] = []
    for tool in mcp_tools:
        entry = _mcp_tool_entry(tool)
        if entry is not None:
            result.append(entry)
    for editor_tool in getattr(runtime, "editor_tools", []):
        result.append(_editor_tool_entry(editor_tool))
    payload = {"tools": result}
    if error:
        payload["error"] = error
    return payload


def _health_payload(server: ACPServer, params: Dict[str, Any]) -> Dict[str, Any]:
    del params
    try:
        ok = getattr(server, "_runtime", None) and server.client().health()
    except Exception:
        ok = False
    return {"status": "ok" if ok else "error"}


def _register_inventory_methods(server: ACPServer) -> None:
    """Expose real models/tools/health via the R4 client.

    These replace the empty stubs so the webview can populate a model
    picker and tool inspector (C5).
    """

    server.register("models/list", lambda params: _models_payload(server, params))
    server.register("tools/list", lambda params: _tools_payload(server, params))
    server.register("health/check", lambda params: _health_payload(server, params))


def _register_editor_methods(server: ACPServer, runtime: _AgentSessionRuntime) -> None:
    @server.method("editor/tools/register")
    def editor_tools_register(params: Dict[str, Any]) -> Dict[str, Any]:
        raw_tools = params.get("tools")
        if not isinstance(raw_tools, list):
            raise TypeError("tools must be a list")
        tools: list[Dict[str, Any]] = []
        for raw_tool in raw_tools:
            if not isinstance(raw_tool, dict):
                raise TypeError("tool must be an object")
            name = _require_string(raw_tool, "name")
            desc = _require_string(raw_tool, "description")
            # C3: preserve the JSON Schema input schema so the LLM can
            # produce valid arguments (previously stripped).
            schema = raw_tool.get("input_schema")
            if schema is None:
                schema = {"type": "object", "properties": {}}
            tools.append(
                {
                    "name": name,
                    "description": desc,
                    "input_schema": schema,
                }
            )
        runtime.configure_editor_tools(tools)
        return {"tools": [tool["name"] for tool in tools]}


# ── Projects + workflow platform methods (webview Studio surface) ───────────
#
# These mirror the companion JSONL bridge RPCs (project_list, workflow.*) so
# ACP clients — notably the VS Code agent webview — can render and drive
# research projects and UMRS workflows without reading r4 internals.


def _serialize_workflow_definition(workflow: Any) -> Dict[str, Any]:
    return {
        "workflow_name": workflow.name or getattr(workflow, "workflow_id", None),
        "name": getattr(workflow, "name", None),
        "description": getattr(workflow, "description", None),
        "tags": list(getattr(workflow, "tags", []) or []),
        "scope": getattr(workflow, "scope", "") or "",
        "owner": getattr(workflow, "owner", "") or "",
        "shared": getattr(workflow, "shared", False) is True,
        "access": list(getattr(workflow, "access", None) or []),
    }


def _serialize_workflow_status(status: Any) -> Dict[str, Any]:
    return {
        "execution_id": status.execution_id,
        "workflow_name": status.workflow_name or getattr(status, "workflow_id", None),
        "status": status.status,
        "progress": getattr(status, "progress", None),
        "error": getattr(status, "error", None),
    }


def _serialize_workflow_result(result: Any) -> Dict[str, Any]:
    return {
        "execution_id": result.execution_id,
        "workflow_name": result.workflow_name or getattr(result, "workflow_id", None),
        "status": result.status,
        "artifacts": getattr(result, "artifacts", None),
        "outputs": getattr(result, "outputs", None),
        "error": getattr(result, "error", None),
    }


def _require_execution_id(params: Dict[str, Any]) -> str:
    execution_id = str(params.get("execution_id") or "")
    if not execution_id:
        raise ValueError("execution_id is required")
    return execution_id


def _snapshot_projects() -> Dict[str, Any]:
    from r4.project import get_project_manager

    pm = get_project_manager()
    current = pm.get_current()
    return {
        "projects": [
            {
                "project_id": p.project_id,
                "name": p.name,
                "active": p.project_id == current,
                "gateway_url": p.gateway_url,
                "model": p.model,
            }
            for p in pm.list()
        ],
        "current": current,
    }


def _projects_list(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    del runtime, params
    return _snapshot_projects()


def _sync_client_project(runtime: _AgentSessionRuntime, project_id: str) -> None:
    """Point the cached client's project_id at the newly current project.

    The runtime client is built once with config loaded at construction; a
    later project switch would leave knowledge calls resolving the stale (or
    empty) project. Best-effort — a frozen config simply stays stale.
    """

    try:
        config = getattr(runtime.client(), "config", None)
        if config is not None:
            config.project_id = project_id
    except Exception:  # noqa: BLE001
        logger.debug("Client config project sync skipped", exc_info=True)


def _projects_switch(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    from r4.project import get_project_manager

    project_id = _require_string(params, "project_id")
    pm = get_project_manager()
    if not pm.get(project_id):
        raise ValueError(f"Project not found: {project_id}")
    pm.set_current(project_id)
    _sync_client_project(runtime, project_id)
    return _snapshot_projects()


def _projects_create(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    from r4.project import ProjectConfig, get_project_manager

    project_id = _require_string(params, "project_id")
    name = _optional_string(params, "name")
    gateway = _optional_string(params, "gateway")
    model = _optional_string(params, "model")
    pm = get_project_manager()
    existing = pm.get(project_id)
    metadata = dict(existing.metadata) if existing else {}
    project = ProjectConfig(
        project_id=project_id,
        name=name or (existing.name if existing else None),
        gateway_url=gateway or (existing.gateway_url if existing else None),
        model=model or (existing.model if existing else None),
        metadata=metadata,
    )
    pm.create(project)
    pm.set_current(project_id)
    _sync_client_project(runtime, project_id)
    return _snapshot_projects()


def _workflows_list(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    del params
    workflows = runtime.client().workflows.list_workflows()
    return {
        "workflows": [_serialize_workflow_definition(wf) for wf in workflows],
        "count": len(workflows),
    }


def _resolve_project_id(client: Any, params: Dict[str, Any]) -> str:
    """Same priority as CLI: explicit param → client config default."""
    explicit = _optional_string(params, "project_id")
    if explicit is not None:
        return explicit
    config = getattr(client, "config", None)
    project_id = getattr(config, "project_id", None)
    if isinstance(project_id, str) and project_id.strip():
        return project_id
    raise ValueError("project_id is required (no project configured)")


def _knowledge_graph(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Project knowledge graph (nodes + typed edges) for webview visualization."""
    client = runtime.client()
    project_id = _resolve_project_id(client, params)
    return client.knowledge.get_graph(project_id)


def _knowledge_query(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Direct ask-the-knowledge-graph query (no LLM turn)."""
    client = runtime.client()
    project_id = _resolve_project_id(client, params)
    query = _require_string(params, "query")
    limit = params.get("limit", 20)
    scope = _optional_string(params, "scope") or "all"
    # Session graph constraints (Studio selector) fill gaps; explicit
    # per-call params always win — an explicitly empty list clears the
    # session constraint for this call.
    constraints = getattr(client.knowledge, "graph_constraints", {}) or {}
    if "graphs" in params:
        graphs = params.get("graphs") or None
    else:
        graphs = constraints.get("include") or None
    if "exclude_graphs" in params:
        exclude = params.get("exclude_graphs") or None
    else:
        exclude = constraints.get("exclude") or None
    return client.knowledge.query(
        query,
        project_id=project_id,
        scope=scope,
        graphs=graphs,
        exclude_graphs=exclude,
        limit=limit,
    )


def _knowledge_graphs(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """List registered knowledge graphs for the Studio selector (0069)."""
    client = runtime.client()
    project_id = _optional_string(params, "project_id")
    query = _optional_string(params, "q")
    graphs = client.knowledge.graphs(project_id=project_id, status="active", query=query)
    return {"graphs": graphs, "total": len(graphs)}


def _catalog_search(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Science catalogue rows for the Studio card (read-only passthrough).

    Empty query → all rows for the project (ready+pending merged via
    DataCatalogClient.list); non-empty query → lexical grep scoped to the
    project's s3_key prefix (bare ``/catalog/search`` 422s without filters,
    so the project prefix is always attached).
    """
    client = runtime.client()
    project_id = _resolve_project_id(client, params)
    query = (_optional_string(params, "q") or "").strip()
    raw_limit = params.get("limit")
    limit = raw_limit if isinstance(raw_limit, int) and 1 <= raw_limit <= 100 else 50
    if query:
        prefix = project_id if project_id.endswith("/") else f"{project_id}/"
        result = client.data_catalog.search(query=query, prefix=prefix, limit=limit)
        rows = list(result.get("results", []))
    else:
        rows = client.data_catalog.list(project_id, limit=limit)
    return {"project_id": project_id, "query": query, "results": rows, "total": len(rows)}


def _knowledge_graph_constraints(
    runtime: _AgentSessionRuntime, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Get/set session knowledge-graph selection constraints.

    Set when any of include/exclude/discovery present; always returns the
    current constraints so the webview can boot with the persisted state.
    """
    client = runtime.client()
    if any(key in params for key in ("include", "exclude", "discovery")):
        client.knowledge.graph_constraints = {
            "include": list(params.get("include") or []),
            "exclude": list(params.get("exclude") or []),
            # Default OFF (2026-08-23): nothing auto-mounts as a satellite
            # until the session selects graphs — the topic-ranked discovery
            # lane is opt-in. Behavior note attached to the webview wave.
            "discovery": bool(params.get("discovery", False)),
        }
    current = getattr(client.knowledge, "graph_constraints", {}) or {}
    return {
        "constraints": {
            "include": list(current.get("include") or []),
            "exclude": list(current.get("exclude") or []),
            "discovery": bool(current.get("discovery", False)),
        }
    }


def _knowledge_import(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Feed data into knowledge: paths and/or external sources.

    Human-initiated path (Studio ingest button): explicitly force the human
    actor — a user click IS the authorization. This must override any
    agent-actor marking a prior chat turn left on the shared client.
    """
    files = _require_list_param(params, "files", "paths")
    sources = _require_list_param(params, "sources", "query strings")
    client = runtime.client()
    client.knowledge.actor = None
    client.knowledge.session_id = None
    project_id = _resolve_project_id(client, params)
    result: Dict[str, Any] = {"project_id": project_id}
    if files:
        result["ingest"] = client.knowledge.ingest(project_id, files=files)
    if sources:
        result["external_search"] = _external_search_probes(client, project_id, sources)
    if not files and not sources:
        raise ValueError("Nothing to ingest — pass files[] and/or sources[]")
    return result


def _require_list_param(params: Dict[str, Any], key: str, item_kind: str) -> list | None:
    """Validate list-shaped ACP params used by knowledge/import."""
    value = params.get(key)
    if value is not None and not isinstance(value, list):
        raise ValueError(f"{key} must be an array of {item_kind}")
    return value


def _external_search_probes(client: Any, project_id: str, sources: list) -> Dict[str, Any]:
    """Run bounded external searches for user-supplied query strings."""
    queries = [str(q).strip() for q in sources if str(q).strip()]
    if not queries:
        return {"queries": [], "results": []}
    try:
        probes = [client.knowledge.external_search(project_id, q) for q in queries[:5]]
    except Exception as exc:  # noqa: BLE001 — the files' ingest stands; say why
        from .companion.knowledge_tools import external_search_skipped

        return external_search_skipped(queries, exc)
    return {"queries": queries, "results": probes}


def _knowledge_import_status(
    runtime: _AgentSessionRuntime, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Import progress for the pending-knowledge strip."""
    client = runtime.client()
    project_id = _resolve_project_id(client, params)
    return client.knowledge.ingest_status(project_id)


def _knowledge_verification(
    runtime: _AgentSessionRuntime, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Latest verification state for a project (console knowledge card parity)."""
    client = runtime.client()
    project_id = _resolve_project_id(client, params)
    return client.knowledge.verify_status(project_id)


def _knowledge_reconcile(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """ADR-031 plane reconciliation state for a project (§12 preflight parity).

    Delegates to the server-side checker (``/gw/knowledge/reconcile``) —
    never re-derived locally; errors surface read-side like verification.
    """
    client = runtime.client()
    project_id = _resolve_project_id(client, params)
    sample = params.get("sample") if isinstance(params.get("sample"), int) else 50
    return client.knowledge.reconcile_project(project_id, sample=sample)


def _research_runtime_for(runtime: _AgentSessionRuntime, project_id: str):
    """Resolve the ResearchRuntime executing loops for one project."""
    from .runtime import get_runtime

    return get_runtime(project_id=project_id, client=runtime.client())


def _runtime_capabilities_payload(runtime) -> Dict[str, Any]:
    caps = runtime.capabilities()
    return {
        "runtime": caps.location,
        "durable": caps.durable,
        "distributed": caps.distributed,
        "backends": list(caps.backends),
    }


def _research_loops(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Research loop list + latest loop detail (Research DAG parity)."""
    client = runtime.client()
    project_id = _resolve_project_id(client, params)
    rr = _research_runtime_for(runtime, project_id)
    loops = [state.to_dict() for state in rr.list_loops()]
    payload: Dict[str, Any] = {
        "project_id": project_id,
        "loops": loops,
        **_runtime_capabilities_payload(rr),
    }
    loop_id = _optional_string(params, "loop_id")
    if loop_id is None and loops:
        loop_id = loops[-1].get("loop_id")
    if loop_id:
        try:
            payload["detail"] = rr.get_loop(loop_id).to_dict()
        except Exception as exc:  # noqa: BLE001 - older gateway without loop detail
            payload["detail"] = None
            payload["detail_error"] = str(exc)  # #212: failure must not look like "no detail"
    return payload


@dataclass(frozen=True)
class _LoopStartOptions:
    """Validated kind selectors from one research/loop_start call."""

    research_goal: str
    max_iterations: int
    agentic: bool
    requires: Dict[str, Any]
    runtime_mode: str | None
    metadata: Dict[str, Any]


_VALID_RUNTIME_MODES = ("umrs", "local", "hybrid")


def _dict_param(params: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Optional object param — {} when absent, error when misshaped."""
    value = params.get(key)
    if value is not None and not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return dict(value or {})


def _loop_start_options(params: Dict[str, Any]) -> _LoopStartOptions:
    max_iterations = params.get("max_iterations", 10)
    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise ValueError("max_iterations must be a positive integer")
    runtime_mode = _optional_string(params, "runtime_mode") or None
    if runtime_mode is not None and runtime_mode not in _VALID_RUNTIME_MODES:
        raise ValueError("runtime_mode must be one of umrs|local|hybrid")
    return _LoopStartOptions(
        research_goal=_require_string(params, "research_goal"),
        max_iterations=max_iterations,
        agentic=bool(params.get("agentic", False)),
        requires=_dict_param(params, "requires"),
        runtime_mode=runtime_mode,
        metadata=_dict_param(params, "metadata"),
    )


def _research_loop_start(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Start a research loop via the resolved runtime (chat and Studio entries).

    Kind selectors (all optional): ``runtime_mode`` picks the venue
    (umrs|local|hybrid; default = resolver rules), ``agentic`` drives
    iterations with the site LLM agent executor, and ``requires`` (gpu,
    cores, cluster, …) routes cluster-class work to UMRS in hybrid mode.
    """
    options = _loop_start_options(params)
    client = runtime.client()
    project_id = _resolve_project_id(client, params)
    from .runtime import LoopSpec, get_runtime

    rr = get_runtime(mode=options.runtime_mode, project_id=project_id, client=client)
    state = rr.start_loop(
        LoopSpec(
            project_id=project_id,
            research_goal=options.research_goal,
            max_iterations=options.max_iterations,
            metadata=options.metadata,
            requires=options.requires,
            agentic=options.agentic,
        )
    )
    return {
        "loop": state.to_dict(),
        "project_id": project_id,
        "agentic": options.agentic,
        "requires": options.requires,
        "runtime_mode": options.runtime_mode,
        **_runtime_capabilities_payload(rr),
    }


def _research_loop_stop(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Stop a running research loop (idempotent)."""
    client = runtime.client()
    project_id = _resolve_project_id(client, params)
    loop_id = _require_string(params, "loop_id")
    rr = _research_runtime_for(runtime, project_id)
    state = rr.stop_loop(loop_id)
    return {
        "stopped": loop_id,
        "status": state.status,
        "project_id": project_id,
        **_runtime_capabilities_payload(rr),
    }


def _research_session_view(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate the live research session (loops + knowledge digest, read-only)."""
    client = runtime.client()
    project_id = _resolve_project_id(client, params)
    from .runtime.session_view import research_session_view

    return research_session_view(client, project_id)


# ── Skills / commands / MCP inventory surfaces (webview System tab) ─────────


def _resolve_skills_session(
    runtime: _AgentSessionRuntime, params: Dict[str, Any]
) -> AgentSessionProtocol | None:
    """Pick the companion session whose skill registry answers this call.

    Explicit ``session_id`` wins; otherwise the first live companion session
    (registries converge — builtin/user/project scans look at the same dirs).
    Returns None when no session exists yet (caller shows builtin fallback).
    """
    session_id = _optional_string(params, "session_id")
    if session_id:
        session = runtime.get(session_id)
        if getattr(session, "companion", None) is None:
            raise ValueError("skills are only available on companion sessions")
        return session
    for session in runtime.sessions.values():
        if getattr(session, "companion", None) is not None:
            return session
    return None


def _serialize_skills(
    registry: Any, *, active_names: set[str] | None = None
) -> list[Dict[str, Any]]:
    active = (
        active_names
        if active_names is not None
        else {skill.name for skill in registry.list_active()}
    )
    return [
        {
            "name": skill.name,
            "description": skill.description,
            "source": skill.source,
            "active": skill.name in active,
            "tools": list(skill.tool_names),
            "keywords": list(skill.keywords),
        }
        for skill in registry.list_all()
    ]


def _skills_list(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Skills known to the companion, with loaded (active) state per skill."""
    session = _resolve_skills_session(runtime, params)
    if session is None:
        from .companion.skills import SkillRegistry

        registry = SkillRegistry()
        try:
            registry.load_user_skills()
        except Exception:  # noqa: BLE001 - user skill dirs are best-effort
            logger.debug("bare skill registry: user skill scan failed", exc_info=True)
        return {
            "skills": _serialize_skills(registry, active_names=set()),
            "session_bound": False,
        }
    return {
        "skills": _serialize_skills(session.companion.skills),
        "session_bound": True,
        "session_id": session.id,
    }


def _skills_set_active(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Load/unload one skill (toggle). Requires a bound companion session."""
    session_id = _require_string(params, "session_id")
    name = _require_string(params, "name")
    activate_flag = bool(params.get("active", True))
    session = runtime.get(session_id)
    companion = getattr(session, "companion", None)
    if companion is None:
        raise ValueError("skills are only available on companion sessions")
    if activate_flag:
        if not companion.skills.activate(name):
            raise ValueError(f"unknown skill: {name}")
    else:
        companion.skills.deactivate(name)
    return {
        "skills": _serialize_skills(companion.skills),
        "session_bound": True,
        "session_id": session.id,
    }


def _commands_list(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Slash-command catalog for the webview help surface (companion parity)."""
    del runtime, params
    from .companion.ui.command_bridge import command_specs

    return {
        "commands": [
            {"name": spec.name, "help": spec.help, "category": spec.category}
            for spec in command_specs()
        ]
    }


def _mcp_servers(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Registered MCP servers (gateway /gw/mcp/servers)."""
    del params
    return {"servers": runtime.client().mcp.list_servers()}


def _mcp_tools(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """MCP tools, optionally filtered to one server."""
    server = _optional_string(params, "server")
    tools = runtime.client().mcp.list_tools(server=server)
    return {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "server": tool.server,
                "input_schema": dict(tool.parameters),
            }
            for tool in tools
        ]
    }


def _mcp_server_health(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Trigger a health check for one MCP server."""
    server_id = _require_string(params, "server_id")
    return runtime.client().mcp.check_server_health(server_id)


def _knowledge_graph_content(
    runtime: _AgentSessionRuntime, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Bounded nodes+edges for one registered knowledge graph (0069)."""
    graph_id = _require_string(params, "graph_id")
    node_limit = params.get("node_limit", 200)
    edge_limit = params.get("edge_limit", 400)
    return runtime.client().knowledge.graph_content(
        graph_id, node_limit=node_limit, edge_limit=edge_limit
    )


def _workflows_trigger(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    workflow_name = str(params.get("workflow_name") or params.get("workflow_id") or "")
    if not workflow_name:
        raise ValueError("workflow_name is required")
    parameters = params.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object")
    input_files = params.get("input_files")
    if input_files is not None and not isinstance(input_files, list):
        raise ValueError("input_files must be an array")
    project_id = _optional_string(params, "project_id")
    wait = bool(params.get("wait", False))
    result = runtime.client().workflows.trigger(
        workflow_name,
        parameters=parameters,
        input_files=input_files,
        project_id=project_id,
        wait=wait,
    )
    return _serialize_workflow_result(result)


def _workflows_describe(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Parameter schema + step metadata for the extension's run form."""
    workflow_name = str(params.get("workflow_name") or "")
    if not workflow_name:
        raise ValueError("workflow_name is required")
    return runtime.client().workflows.describe(workflow_name)


def _workflow_http_errors_curated(fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Re-raise APIError as R4Error with the HTTP status in the message.

    The webview watchlist (#210) decides "execution vanished vs transient
    fetch failure" from curated ACP error text — include "HTTP 404" /
    "not found" so real deletions are distinguishable from 5xx/timeouts.
    """
    from .errors import APIError

    try:
        return fn()
    except APIError as exc:
        code = exc.status_code or 0
        reason = "not found" if code == 404 else "request failed"
        raise R4Error(f"HTTP {code}: {reason}", exc.details) from exc


def _workflows_status(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    return _workflow_http_errors_curated(
        lambda: _serialize_workflow_status(
            runtime.client().workflows.status(_require_execution_id(params))
        )
    )


def _workflows_detail(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    """Raw execution record incl. results.steps step-level statuses."""
    return _workflow_http_errors_curated(
        lambda: {
            "execution": runtime.client().workflows.get_execution_detail(
                _require_execution_id(params)
            )
        }
    )


def _workflows_results(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    result = runtime.client().workflows.get_results(_require_execution_id(params))
    return _serialize_workflow_result(result)


def _workflows_cancel(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    status = runtime.client().workflows.cancel(_require_execution_id(params))
    payload = _serialize_workflow_status(status)
    payload["ok"] = True
    return payload


def _workflows_approve_gate(
    runtime: _AgentSessionRuntime, params: Dict[str, Any]
) -> Dict[str, Any]:
    """Approve/reject a manual-gate step (W5) on a waiting execution."""
    execution_id = _require_execution_id(params)
    step_name = str(params.get("step_name") or params.get("step") or "")
    if not step_name:
        raise ValueError("step_name is required")
    approved = bool(params.get("approved", True))
    approver = _optional_string(params, "approver")
    comment = _optional_string(params, "comment")
    status = runtime.client().workflows.approve_gate(
        execution_id,
        step_name,
        approved=approved,
        approver=approver,
        comment=comment,
    )
    payload = _serialize_workflow_status(status)
    payload["ok"] = True
    return payload


# ── Jobs + endpoints rails (WP-08, G-P9-4 — was CLI/tree-views only) ──────


def _serialize_job(job: Any) -> Dict[str, Any]:
    """Compact job projection for ACP consumers (matches bridge row shape)."""
    return {
        "job_id": getattr(job, "job_id", None),
        "job_type": getattr(job, "job_type", None),
        "status": getattr(job, "status", None),
        "project_id": getattr(job, "project_id", None),
        "created_at": str(getattr(job, "created_at", "") or ""),
    }


def _jobs_list(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    project_id = _resolve_project_id(runtime.client(), params)
    status = _optional_string(params, "status")
    job_type = _optional_string(params, "job_type") or _optional_string(params, "kind")
    limit = int(params.get("limit", 50))
    jobs = runtime.client().jobs.list(
        project_id=project_id, status=status, job_type=job_type, limit=limit
    )
    return {"jobs": [_serialize_job(j) for j in jobs], "count": len(jobs)}


def _jobs_status(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(params.get("job_id") or "")
    if not job_id:
        raise ValueError("job_id is required")
    job = runtime.client().jobs.get(job_id)
    return _serialize_job(job)


def _jobs_cancel(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(params.get("job_id") or "")
    if not job_id:
        raise ValueError("job_id is required")
    job = runtime.client().jobs.cancel(job_id)
    payload = _serialize_job(job)
    payload["ok"] = True
    return payload


def _endpoints_list(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    include_stopped = bool(params.get("include_stopped", False))
    data = runtime.client().list_endpoints(include_stopped=include_stopped)
    return {"endpoints": data.get("endpoints", []), "total": data.get("total", 0)}


def _endpoints_stop(runtime: _AgentSessionRuntime, params: Dict[str, Any]) -> Dict[str, Any]:
    instance_id = str(params.get("instance_id") or params.get("job_id") or "")
    if not instance_id:
        raise ValueError("instance_id is required")
    force = bool(params.get("force", False))
    data = runtime.client().stop_endpoint(instance_id, force=force)
    return {"ok": True, "instance_id": instance_id, "result": data}


# The platform-verb registry (name → handler). Extracted to module scope in
# WP-08 so the capability parity contract can inspect the served set without
# instantiating a session (r4/tests/test_capability_parity.py).
ACP_PLATFORM_METHODS: Dict[str, Callable[[Any, Dict[str, Any]], Dict[str, Any]]] = {
    "projects/list": _projects_list,
    "projects/switch": _projects_switch,
    "projects/create": _projects_create,
    "workflows/list": _workflows_list,
    "workflows/trigger": _workflows_trigger,
    "workflows/describe": _workflows_describe,
    "workflows/status": _workflows_status,
    "workflows/detail": _workflows_detail,
    "workflows/results": _workflows_results,
    "knowledge/graph": _knowledge_graph,
    "knowledge/graphs": _knowledge_graphs,
    "catalog/search": _catalog_search,
    "knowledge/graph_constraints": _knowledge_graph_constraints,
    "knowledge/query": _knowledge_query,
    "knowledge/import": _knowledge_import,
    "knowledge/import_status": _knowledge_import_status,
    "knowledge/verification": _knowledge_verification,
    "knowledge/reconcile": _knowledge_reconcile,
    "research/loops": _research_loops,
    "research/loop_start": _research_loop_start,
    "research/loop_stop": _research_loop_stop,
    "research/session": _research_session_view,
    "workflows/cancel": _workflows_cancel,
    "workflows/approve_gate": _workflows_approve_gate,
    "skills/list": _skills_list,
    "skills/set_active": _skills_set_active,
    "commands/list": _commands_list,
    "mcp/servers": _mcp_servers,
    "mcp/tools": _mcp_tools,
    "mcp/server_health": _mcp_server_health,
    "knowledge/graph_content": _knowledge_graph_content,
    "jobs/list": _jobs_list,
    "jobs/status": _jobs_status,
    "jobs/cancel": _jobs_cancel,
    "endpoints/list": _endpoints_list,
    "endpoints/stop": _endpoints_stop,
}


def _register_platform_methods(server: ACPServer, runtime: _AgentSessionRuntime) -> None:
    """Projects + UMRS workflow/jobs/endpoints management (P9 parity)."""
    for name, handler in ACP_PLATFORM_METHODS.items():
        server.register_async(
            name,
            lambda params, _handler=handler: _handler(runtime, params),
        )


def create_default_server(
    *,
    client_factory: Callable[[], Any] | None = None,
    session_factory: SessionFactory = _default_session_factory,
    session_loader: SessionLoader = _default_session_loader,
    session_lister: SessionLister = _default_session_lister,
) -> ACPServer:
    """Create an ACP server backed by Companion sessions (AgentSessionProtocol)."""
    server = ACPServer()
    runtime = _AgentSessionRuntime(server, client_factory, session_factory, session_loader)
    # Attach runtime so the inventory methods can reach the client (C5).
    server._runtime = runtime  # type: ignore[attr-defined]
    _register_lifecycle_methods(server)
    _register_session_methods(server, runtime, session_lister)
    _register_session_pin_compact(server)
    _register_session_compact_methods(server, runtime)
    _register_session_set_commands(server, runtime)
    _register_chat_method(server, runtime)
    _register_inventory_methods(server)
    _register_platform_methods(server, runtime)
    _register_editor_methods(server, runtime)
    return server
