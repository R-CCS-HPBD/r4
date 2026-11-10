"""Tests for the R4 editor agent protocol bridge."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from r4.acp import ERR_CANCELLED, ACPServer, create_default_server
from r4.agent.core import AgentCancelledError


@dataclass
class FakeEvent:
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"event_type": "message", "message": self.message}


class FakeSession:
    def __init__(self, session_id: str = "session-1", name: str = "Agent session") -> None:
        self.id = session_id
        self.name = name
        self.model: str | None = None
        self.messages: list[str] = []
        self.callback: Callable[[Any], None] | None = None
        self.save_count = 0
        self.deleted = False
        self.editor_tools: list[Any] = []
        self.editor_tool_call: Callable[[str, dict[str, Any]], Any] | None = None
        self.cancelled = False
        self.stream_callback: Callable[[str], None] | None = None

    def on_event(self, callback: Callable[[Any], None]) -> None:
        self.callback = callback

    def chat(self, message: str, auto_save: bool = True) -> str:
        self.messages.append(message)
        if self.callback:
            self.callback(FakeEvent(f"processing {message}"))
        if message == "use editor" and self.editor_tool_call:
            result = self.editor_tool_call("workspace.readFile", {"path": "README.md"})
            return f"editor: {result}"
        return f"answer: {message}"

    def save(self) -> None:
        self.save_count += 1

    def set_editor_tools(
        self,
        tools: list[Any],
        callback: Callable[[str, dict[str, Any]], Any],
    ) -> None:
        self.editor_tools = tools
        self.editor_tool_call = callback

    def cancel(self) -> None:
        self.cancelled = True

    def prepare_request(self) -> None:
        self.cancelled = False

    def is_cancelled(self) -> bool:
        return self.cancelled

    def set_stream_callback(self, callback: Callable[[str], None] | None) -> None:
        self.stream_callback = callback

    # C6: structured event callback
    def set_event_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        self.event_callback = callback

    def _notify_event(self, event: dict[str, Any]) -> None:
        """C6: fire the structured event callback."""
        if self.event_callback:
            try:
                self.event_callback(event)
            except Exception:
                pass

    def rename(self, name: str) -> None:
        self.name = name
        self.save_count += 1

    def set_model(self, model: str | None) -> None:
        self.model = model
        self.save_count += 1

    def delete(self) -> None:
        self.deleted = True


def invoke(server: ACPServer, method: str, params: dict[str, Any], request_id: int = 1):
    output: list[dict[str, Any]] = []
    server._write = output.append  # type: ignore[method-assign]
    server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
    )
    return output


def _tool_names(tools: list[Any]) -> list[str]:
    return [tool.name for tool in tools]


def _tool_state_statuses(messages: list[dict[str, Any]]) -> list[Any]:
    return [
        message["params"]["status"]
        for message in messages
        if message.get("method") == "session/toolState"
    ]


def _subset(mapping: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: mapping[key] for key in keys}


def test_initialize_advertises_versioned_session_protocol() -> None:
    server = create_default_server(client_factory=object)

    output = invoke(server, "initialize", {})

    result = output[-1]["result"]
    assert result["protocol_version"] == 1
    assert result["capabilities"]["sessions"] is True
    assert result["capabilities"]["session_events"] is True
    assert result["capabilities"]["tool_approval"] is True


def test_agent_cancellation_maps_to_json_rpc_cancellation() -> None:
    server = ACPServer()

    def cancelled(params: dict[str, Any]) -> dict[str, Any]:
        raise AgentCancelledError("cancelled")

    server.register("cancelled", cancelled)

    output = invoke(server, "cancelled", {})

    assert output[-1]["error"] == {"code": ERR_CANCELLED, "message": "Request cancelled"}


def test_chat_send_uses_and_reuses_agent_session() -> None:
    session = FakeSession()
    created: list[tuple[Any, str | None, str | None]] = []

    def factory(client: Any, name: str | None, model: str | None) -> FakeSession:
        created.append((client, name, model))
        return session

    client = object()
    server = create_default_server(client_factory=lambda: client, session_factory=factory)

    first = invoke(
        server,
        "chat/send",
        {"message": "first", "model": "r4-model", "request_id": "request-1"},
    )
    response = first[-1]["result"]
    assert response == {
        "status": "completed",
        "session_id": "session-1",
        "response": "answer: first",
    }
    assert first[0]["method"] == "session/event"
    assert first[0]["params"]["session_id"] == "session-1"

    second = invoke(
        server,
        "chat/send",
        {"session_id": "session-1", "message": "second", "request_id": "request-2"},
    )
    assert second[-1]["result"]["response"] == "answer: second"
    assert session.messages == ["first", "second"]
    assert session.save_count == 1
    assert created == [(client, None, "r4-model")]


def test_chat_send_emits_progress_keepalives(monkeypatch: Any) -> None:
    """Healthy long turns must stream session/progress so the editor keeps
    its request timer alive (regression for \"R4 ACP request timed out: chat/send\")."""
    import r4.acp as acp_module

    session = FakeSession()
    real_chat = session.chat

    def slow_chat(message: str, auto_save: bool = True) -> str:
        time.sleep(0.25)
        return real_chat(message, auto_save)

    session.chat = slow_chat  # type: ignore[method-assign]
    monkeypatch.setattr(acp_module, "_CHAT_HEARTBEAT_INTERVAL_SEC", 0.05)
    client = object()
    server = create_default_server(
        client_factory=lambda: client,
        session_factory=lambda *_args: session,
    )

    output = invoke(server, "chat/send", {"message": "hi", "request_id": "req-hb"})

    progress = [
        message
        for message in output
        if message.get("method") == "session/progress"
        and message.get("params", {}).get("request_id") == "req-hb"
    ]
    assert progress
    assert all(message["params"]["state"] == "running" for message in progress)
    assert output[-1]["result"]["status"] == "completed"


def _stream_methods(messages: list[dict[str, Any]]) -> list[str]:
    return [message.get("method") for message in messages]


def _struct_event_payload(messages: list[dict[str, Any]], method: str) -> dict[str, Any]:
    return next(m for m in messages if m.get("method") == method)["params"]


def _assert_tool_events_streamed_live(methods: list[str], output_len: int) -> None:
    """Live = both events precede the terminal response; exactly once = no
    end-of-turn duplicate replay of structured events."""
    assert methods.index("session/tool_call_start") < output_len - 1
    assert methods.index("session/tool_call_done") < output_len - 1
    assert methods.count("session/tool_call_start") == 1
    assert methods.count("session/tool_call_done") == 1


def _assert_tool_call_start_payload(payload: dict[str, Any]) -> None:
    assert payload["request_id"] == "r1"
    assert payload["session_id"] == "session-1"
    assert payload["name"] == "knowledge_ingest"
    # The event's "type" is consumed for the method name, not forwarded.
    assert "type" not in payload


def test_chat_send_streams_structured_events_live() -> None:
    """Structured tool events must go out DURING the turn, not buffered to the
    end — end-facing buffering left long tool loops showing only a spinner."""

    class EventfulSession(FakeSession):
        def chat(self, message: str, auto_save: bool = True) -> str:
            self._notify_event(
                {"type": "tool_call_start", "tool_call_id": "t1", "name": "knowledge_ingest"}
            )
            self._notify_event(
                {
                    "type": "tool_call_done",
                    "tool_call_id": "t1",
                    "name": "knowledge_ingest",
                    "status": "completed",
                }
            )
            return "done"

    session = EventfulSession()
    server = create_default_server(
        client_factory=object,
        session_factory=lambda *_args: session,
    )

    output = invoke(server, "chat/send", {"message": "go", "request_id": "r1"})

    assert output[-1]["result"]["status"] == "completed"
    _assert_tool_events_streamed_live(_stream_methods(output), len(output))
    _assert_tool_call_start_payload(_struct_event_payload(output, "session/tool_call_start"))


def test_projects_create_syncs_cached_client_config(tmp_path: Any, monkeypatch: Any) -> None:
    """A Studio/chat-created project must become resolvable for knowledge
    calls without waiting for an ACP-process restart (client config is
    snapshot once at construction)."""
    import r4.project as project_module
    from r4.project import ProjectManager

    manager = ProjectManager(config_dir=tmp_path / "cfg")
    monkeypatch.setattr(project_module, "_project_manager", manager)

    client = SimpleNamespace(config=SimpleNamespace(project_id=None))
    server = create_default_server(client_factory=lambda: client)

    output = invoke(server, "projects/create", {"project_id": "m1", "name": "MERMAIDS"})

    assert output[-1]["result"]["current"] == "m1"
    assert client.config.project_id == "m1"

    switch = invoke(server, "projects/switch", {"project_id": "m1"})
    assert switch[-1]["result"]["current"] == "m1"


def test_sessions_load_uses_existing_agent_session_storage() -> None:
    session = FakeSession(session_id="saved-1", name="Saved")
    loaded: list[str] = []

    def loader(client: Any, session_id: str) -> FakeSession:
        loaded.append(session_id)
        return session

    server = create_default_server(client_factory=object, session_loader=loader)

    output = invoke(server, "sessions/load", {"session_id": "saved-1"})

    assert output[-1]["result"] == {
        "session": {"id": "saved-1", "name": "Saved", "model": None, "mode": "chat", "messages": []}
    }
    assert loaded == ["saved-1"]


def test_sessions_rename_updates_and_returns_session() -> None:
    session = FakeSession(session_id="s-1", name="Session 2026")
    server = create_default_server(
        client_factory=object, session_loader=lambda client, sid: session
    )

    output = invoke(server, "sessions/rename", {"session_id": "s-1", "name": "  Protein run  "})

    assert output[-1]["result"]["session"]["name"] == "Protein run"
    assert session.name == "Protein run"


def test_sessions_set_model_binds_endpoint() -> None:
    session = FakeSession(session_id="s-1")
    server = create_default_server(
        client_factory=object, session_loader=lambda client, sid: session
    )

    output = invoke(server, "sessions/set_model", {"session_id": "s-1", "model": "qwen-ready"})

    assert output[-1]["result"]["session"]["model"] == "qwen-ready"
    assert session.model == "qwen-ready"


def test_sessions_delete_removes_session() -> None:
    session = FakeSession(session_id="s-1")
    server = create_default_server(
        client_factory=object, session_loader=lambda client, sid: session
    )

    output = invoke(server, "sessions/delete", {"session_id": "s-1"})

    assert output[-1]["result"] == {"deleted": "s-1"}
    assert session.deleted is True


def test_chat_send_rebinds_model_for_existing_session() -> None:
    session = FakeSession(session_id="s-1")
    server = create_default_server(
        client_factory=object, session_loader=lambda client, sid: session
    )

    invoke(
        server,
        "chat/send",
        {"session_id": "s-1", "message": "hi", "model": "qwen-ready", "request_id": "r-1"},
    )

    assert session.model == "qwen-ready"


def test_chat_send_rejects_empty_message() -> None:
    server = create_default_server(client_factory=object)

    output = invoke(server, "chat/send", {"message": "  ", "request_id": "request-1"})

    assert output[-1]["error"]["code"] == -32602
    assert output[-1]["error"]["message"] == "message must be a non-empty string"


def test_internal_errors_do_not_leak_exception_details() -> None:
    server = ACPServer()

    def fail(params: dict[str, Any]) -> None:
        raise RuntimeError("token=secret-value")

    server.register("fail", fail)
    output = invoke(server, "fail", {})

    assert output[-1]["error"] == {"code": -32603, "message": "Internal error"}
    assert "secret-value" not in json.dumps(output)


def test_internal_errors_forward_r4_error_message_and_hint() -> None:
    from r4.errors import R4Error

    server = ACPServer()

    def fail(params: dict[str, Any]) -> None:
        raise R4Error("No model selected for this session.", {"hint": "Run /models to pick one."})

    server.register("fail", fail)
    output = invoke(server, "fail", {})

    assert output[-1]["error"] == {
        "code": -32603,
        "message": "Internal error: No model selected for this session. Run /models to pick one.",
    }


def test_registered_editor_tool_is_invoked_by_agent_session() -> None:
    session = FakeSession()
    server = create_default_server(client_factory=object, session_factory=lambda *args: session)
    editor_calls: list[tuple[str, dict[str, Any]]] = []

    def request_client(method: str, params: dict[str, Any]) -> dict[str, str]:
        editor_calls.append((method, params))
        return {"content": "workspace data"}

    server.request_client = request_client  # type: ignore[method-assign]
    registration = invoke(
        server,
        "editor/tools/register",
        {"tools": [{"name": "workspace.readFile", "description": "Read workspace file"}]},
    )
    output = invoke(server, "chat/send", {"message": "use editor", "request_id": "request-editor"})

    assert registration[-1]["result"] == {"tools": ["workspace.readFile"]}
    assert _tool_names(session.editor_tools) == ["workspace.readFile"]
    assert output[-1]["result"]["response"] == "editor: {'content': 'workspace data'}"
    assert len(editor_calls) == 1
    method, params = editor_calls[0]
    assert isinstance(params["tool_call_id"], str)
    assert method == "editor/toolCall"
    assert _subset(params, ("session_id", "request_id", "name", "arguments")) == {
        "session_id": "session-1",
        "request_id": "request-editor",
        "name": "workspace.readFile",
        "arguments": {"path": "README.md"},
    }
    assert _tool_state_statuses(output) == ["pending"]


def test_non_object_params_are_invalid() -> None:
    server = create_default_server(client_factory=object)
    output: list[dict[str, Any]] = []
    server._write = output.append  # type: ignore[method-assign]

    server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "chat/send", "params": []})
    )

    assert output[-1]["error"]["code"] == -32602


def test_loaded_session_returns_persisted_transcript() -> None:
    session = FakeSession()
    session.messages = ["restored question"]
    server = create_default_server(
        client_factory=object, session_loader=lambda client, session_id: session
    )

    output = invoke(server, "sessions/load", {"session_id": session.id})

    assert output[-1]["result"]["session"]["messages"] == [
        {"role": "user", "content": "restored question"}
    ]


def test_editor_response_is_routed_to_waiting_agent_request() -> None:
    server = ACPServer()
    output: list[dict[str, Any]] = []
    server._write = output.append  # type: ignore[method-assign]
    result: list[Any] = []
    worker = threading.Thread(
        target=lambda: result.append(server.request_client("editor/toolCall", {"name": "read"}))
    )

    worker.start()
    while not output:
        worker.join(0.001)
    request_id = output[0]["id"]
    server._handle_line(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"ok": True}}))
    worker.join(1)

    assert result == [{"ok": True}]
    assert not worker.is_alive()


def test_cancel_notification_discards_only_matching_request_result() -> None:
    server = ACPServer()
    output: list[dict[str, Any]] = []
    server._write = output.append  # type: ignore[method-assign]
    server.register("complete", lambda params: {"value": params["value"]})

    server._handle_line(
        json.dumps({"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": 3}})
    )
    server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "complete", "params": {"value": 1}})
    )
    server._handle_line(
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "complete", "params": {"value": 2}})
    )

    assert output[0]["error"]["code"] == -32800
    assert output[1]["result"] == {"value": 2}


def test_cancel_before_callback_registration_is_replayed() -> None:
    server = ACPServer()
    cancelled: list[str] = []
    server._handle_line(
        json.dumps({"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": 7}})
    )

    server.register_cancel_callback(7, lambda: cancelled.append("cancelled"))

    assert cancelled == ["cancelled"]


def test_parallel_editor_requests_receive_unique_correlated_ids() -> None:
    server = ACPServer()
    output: list[dict[str, Any]] = []
    server._write = output.append  # type: ignore[method-assign]
    results: dict[str, Any] = {}
    workers = [
        threading.Thread(
            target=lambda key=key: results.__setitem__(
                key, server.request_client("editor/toolCall", {"name": key})
            )
        )
        for key in ("first", "second")
    ]
    for worker in workers:
        worker.start()
    while len(output) < 2:
        workers[0].join(0.001)
    assert output[0]["id"] != output[1]["id"]
    for request in reversed(output):
        server._handle_line(
            json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": request["params"]["name"]})
        )
    for worker in workers:
        worker.join(1)

    assert results == {"first": "first", "second": "second"}


class ThinkingSession(FakeSession):
    """Session whose reply carries model reasoning markers."""

    def __init__(self, reply: str, stream: bool = True) -> None:
        super().__init__()
        self._reply = reply
        self._stream = stream

    def chat(self, message: str, auto_save: bool = True) -> str:
        self.messages.append(message)
        if self._stream and self.stream_callback:
            self.stream_callback(self._reply)
        return self._reply


def _chat_once(reply: str, stream: bool = True) -> list[dict[str, Any]]:
    session = ThinkingSession(reply, stream=stream)
    server = create_default_server(
        client_factory=lambda: object(),
        session_factory=lambda client, name, model: session,
    )
    return invoke(server, "chat/send", {"message": "hi", "request_id": "r1"})


def _notified(messages: list[dict[str, Any]], method: str) -> list[str]:
    return [
        message["params"].get("content") or message["params"].get("text")
        for message in messages
        if message.get("method") == method
    ]


def test_chat_send_completes_without_internal_error() -> None:
    """Regression: a NameError in thinking extraction turned every successful
    reply into a trailing '-32603 Internal error' after the text was shown."""
    messages = _chat_once("Hello there.")

    assert "error" not in messages[-1]
    assert messages[-1]["result"]["response"] == "Hello there."


def test_reasoning_is_separated_from_the_visible_answer() -> None:
    messages = _chat_once("<think>Weigh the options.</think>The answer is 42.")

    assert _notified(messages, "session/thinking") == ["Weigh the options."]
    assert _notified(messages, "session/text") == ["The answer is 42."]
    assert messages[-1]["result"]["response"] == "The answer is 42."


def test_reasoning_is_never_streamed_to_the_transcript() -> None:
    """Regression: streamed chunks were emitted verbatim before the thinking
    split ran, so the model's reasoning rendered as part of the answer."""
    messages = _chat_once("<think>internal deliberation</think>Visible.")

    assert all("internal deliberation" not in text for text in _notified(messages, "session/text"))


def test_backtick_wrapped_reasoning_markers_are_recognised() -> None:
    messages = _chat_once("`<think>`Reasoning.`</think>`Answer.")

    assert _notified(messages, "session/thinking") == ["Reasoning."]
    assert messages[-1]["result"]["response"] == "Answer."


def test_canonical_thinking_envelopes_flow_alongside_legacy() -> None:
    """T1 thinking notifications accompany legacy session/thinking."""
    messages = _chat_once("<think>cab weigh options</think>The answer is 42.")

    methods = [m.get("method") for m in messages]
    assert "session/thinking" in methods
    assert "session/thinking_done" in methods or "session/thinking_delta" in methods


def test_canonical_thinking_payload_shape() -> None:
    """Canonical thinking payload carries schema version + session id."""
    messages = _chat_once("<think>cab weigh options</think>The answer is 42.")

    canonical = next(
        m
        for m in messages
        if m.get("method") in {"session/thinking_delta", "session/thinking_done"}
    )
    payload = canonical["params"]["payload"]
    assert payload["version"] == 1
    assert payload["session_id"] == "session-1"


def test_multibyte_reasoning_and_answer_survive_the_split() -> None:
    messages = _chat_once("<think>日本語で答える。</think>こんにちは！")

    assert _notified(messages, "session/thinking") == ["日本語で答える。"]
    assert messages[-1]["result"]["response"] == "こんにちは！"


def test_unterminated_reasoning_block_keeps_markup_and_reaches_card() -> None:
    """Partial output is never dropped: the visible answer keeps raw markup,
    and the still-open block arrives as a thinking card (partial=False)."""
    messages = _chat_once("<think>never closed")

    thinking = [
        message["params"] for message in messages if message.get("method") == "session/thinking"
    ]
    assert thinking == [
        {
            "session_id": "session-1",
            "request_id": "r1",
            "content": "never closed",
            "partial": False,
        }
    ]
    assert messages[-1]["result"]["response"] == "<think>never closed"


class MultiChunkThinkingSession(FakeSession):
    """Stream several chunks, including a long reasoning block and a tag split."""

    CHUNKS = (
        "Answer intro. ",
        "<think>We need a filter. First, note whale songs span ",
        "8-15 Hz while MERMAIDs record seismic P-waves near 1 Hz. ",
        "A zero-phase bandpass isolates them without ringing [*] </think>",
        "Visible answer part one. ",
        "<thi",
        "nk>second line " + "of long reasoning " * 30,
        " tail</think> done.",
    )

    def chat(self, message: str, auto_save: bool = True) -> str:
        self.messages.append(message)
        for chunk in self.CHUNKS:
            self.stream_callback(chunk)
        return "".join(self.CHUNKS)


def _thinking_note_params(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        message["params"] for message in messages if message.get("method") == "session/thinking"
    ]


def _partial_thinking_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [n for n in notes if n.get("partial") is True]


def _final_thinking_contents(notes: list[dict[str, Any]]) -> list[str]:
    return [n["content"] for n in notes if n.get("partial") is False]


def test_reasoning_blocks_stream_live_during_the_turn() -> None:
    """Completed blocks are emitted the moment they close (mid-turn), and the
    open block produces one partial snapshot once it exceeds the threshold."""
    session = MultiChunkThinkingSession()
    server = create_default_server(
        client_factory=lambda: object(),
        session_factory=lambda client, name, model: session,
    )

    messages = invoke(server, "chat/send", {"message": "go", "request_id": "r1"})

    notes = _thinking_note_params(messages)
    partials = _partial_thinking_notes(notes)

    assert _final_thinking_contents(notes) == [
        (
            "We need a filter. First, note whale songs span 8-15 Hz while "
            "MERMAIDs record seismic P-waves near 1 Hz. A zero-phase bandpass "
            "isolates them without ringing [*]"
        ),
        ("second line " + "of long reasoning " * 30 + " tail"),
    ]
    # Exactly one partial snapshot, emitted for the long block while open.
    assert len(partials) == 1
    assert partials[0]["content"].startswith("second line")
    # Split-tag chunk carry kept the <thi/nk> boundary intact (no extra notes).
    assert len(notes) == 3
    # Final visible answer is free of reasoning markup.
    assert _notified(messages, "session/text") == ["Answer intro. Visible answer part one.  done."]


def test_streamed_blocks_are_not_replayed_at_completion() -> None:
    """Streamed thinking must not appear twice (live + replay-at-end)."""
    session = MultiChunkThinkingSession()
    server = create_default_server(
        client_factory=lambda: object(),
        session_factory=lambda client, name, model: session,
    )

    messages = invoke(server, "chat/send", {"message": "go", "request_id": "r1"})

    finals = _final_thinking_contents(_thinking_note_params(messages))
    assert len([n for n in finals if n.startswith("We need a filter")]) == 1


def test_answer_is_emitted_once_when_the_session_does_not_stream() -> None:
    messages = _chat_once("<think>quiet</think>Done.", stream=False)

    assert _notified(messages, "session/text") == ["Done."]


class _FakeCompanionPermissions:
    def __init__(self) -> None:
        self.callback: Callable[[str, str, dict[str, Any]], bool] | None = None
        self.session_approvals: list[str] = []

    def set_approval_callback(self, cb: Callable[[str, str, dict[str, Any]], bool]) -> None:
        self.callback = cb

    def approve_for_session(self, tool_name: str) -> None:
        self.session_approvals.append(tool_name)


class FakeCompanionSession(FakeSession):
    def __init__(self, session_id: str = "session-1", name: str = "Agent session") -> None:
        super().__init__(session_id=session_id, name=name)
        permissions = _FakeCompanionPermissions()
        self.companion = SimpleNamespace(permissions=permissions)


def _companion_approval_runtime(
    decision: str,
) -> tuple[Any, FakeCompanionSession, list[tuple[str, dict[str, Any]]]]:
    session = FakeCompanionSession()
    server = create_default_server(client_factory=object, session_factory=lambda *args: session)
    calls: list[tuple[str, dict[str, Any]]] = []

    def request_client(
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = 30,
    ) -> dict[str, str]:
        calls.append((method, params))
        return {"decision": decision}

    server.request_client = request_client  # type: ignore[method-assign]
    runtime = server._runtime  # type: ignore[attr-defined]
    runtime.remember(session)
    runtime.active_sessions[session.id] = "request-approval"
    return runtime, session, calls


def test_companion_tool_approval_allows_once() -> None:
    runtime, session, calls = _companion_approval_runtime("once")
    assert session.companion.permissions.callback is not None
    allowed = session.companion.permissions.callback("shell_exec", "Run command", {"command": "ls"})
    assert allowed is True
    assert calls[0][0] == "session/toolApproval"
    assert calls[0][1]["tool"] == "shell_exec"
    assert calls[0][1]["request_id"] == "request-approval"


def test_companion_tool_approval_denies_on_no() -> None:
    _runtime, session, _calls = _companion_approval_runtime("no")
    allowed = session.companion.permissions.callback("shell_exec", "Run command", {})
    assert allowed is False


def test_companion_tool_approval_always_approves_for_session() -> None:
    _runtime, session, _calls = _companion_approval_runtime("always")
    allowed = session.companion.permissions.callback("workflow_run", "Run workflow", {})
    assert allowed is True
    assert session.companion.permissions.session_approvals == ["workflow_run"]


# ── ask_user wiring ─────────────────────────────────────────────────────────


def _ask_user_runtime(
    answer: str,
) -> tuple[Any, FakeSession, list[tuple[str, dict[str, Any]]]]:
    session = FakeSession()
    server = create_default_server(client_factory=object, session_factory=lambda *args: session)
    calls: list[tuple[str, dict[str, Any]]] = []

    def request_client(
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = 30,
    ) -> dict[str, str]:
        calls.append((method, params))
        return {"answer": answer}

    server.request_client = request_client  # type: ignore[method-assign]
    runtime = server._runtime  # type: ignore[attr-defined]
    runtime.remember(session)
    runtime.active_sessions[session.id] = "request-ask"
    runtime._ask_user_threads[threading.get_ident()] = session.id
    return runtime, session, calls


def test_ask_user_routes_to_editor_and_returns_answer() -> None:
    from r4.agent import tools

    runtime, session, calls = _ask_user_runtime("use /mnt/fixture")
    result = runtime._on_ask_user("Which folder?", ["a", "b"])
    assert result == "use /mnt/fixture"
    assert calls[0][0] == "session/askUser"
    assert calls[0][1]["session_id"] == session.id
    assert calls[0][1]["request_id"] == "request-ask"
    assert calls[0][1]["question"] == "Which folder?"
    assert calls[0][1]["options"] == ["a", "b"]
    # Global callback must be wired by the runtime (previously only the TUI
    # bridge registered it → sentinel text leaked into the webview chat).
    assert tools._handle_ask_user({"question": "q", "options": []}).output == "use /mnt/fixture"


def test_ask_user_autopilot_does_not_call_the_editor() -> None:
    from r4.companion.agent_settings import ASK_USER_AUTO_ANSWER

    runtime, session, calls = _ask_user_runtime("unused")
    session.interaction_mode = "autopilot"
    assert runtime._on_ask_user("Which folder?", ["a", "b"]) == ASK_USER_AUTO_ANSWER
    assert calls == []


def test_ask_user_without_active_chat_returns_unavailable() -> None:
    runtime, _session, _calls = _ask_user_runtime("ignored")
    runtime._ask_user_threads.clear()
    assert "unavailable" in runtime._on_ask_user("q?", [])


def test_ask_user_editor_error_returns_cancelled() -> None:
    session = FakeSession()
    server = create_default_server(client_factory=object, session_factory=lambda *args: session)
    server.request_client = None  # type: ignore[assignment]
    runtime = server._runtime  # type: ignore[attr-defined]
    runtime.active_sessions[session.id] = "request-ask"
    runtime._ask_user_threads[threading.get_ident()] = session.id
    assert runtime._on_ask_user("q?", []) == "[ask_user cancelled]"


def test_ask_user_thread_mapping_cleared_on_end_chat() -> None:
    runtime, session, _calls = _ask_user_runtime("x")
    runtime.end_chat(session.id)
    assert threading.get_ident() not in runtime._ask_user_threads
    assert session.id not in runtime.active_sessions


# ── Studio platform methods (projects + workflows) ──────────────────────────


def _fake_workflow_client() -> Any:
    def _status(execution_id: str, status: str = "running") -> SimpleNamespace:
        return SimpleNamespace(
            execution_id=execution_id,
            workflow_name="demo-wf",
            workflow_id="demo-wf",
            status=status,
            progress=0.5,
            error=None,
        )

    workflows = SimpleNamespace(
        list_workflows=lambda tags=None: [
            SimpleNamespace(name="demo-wf", workflow_id="demo-wf", description="Demo", tags=["lab"])
        ],
        trigger=lambda name, **kwargs: SimpleNamespace(
            execution_id="exec-1",
            workflow_name=name,
            workflow_id=name,
            status="running",
            artifacts=None,
            outputs=None,
            error=None,
        ),
        status=lambda execution_id: _status(execution_id),
        get_execution_detail=lambda execution_id: {
            "execution_id": execution_id,
            "results": {"steps": [{"name": "ingest", "status": "done"}]},
        },
        get_results=lambda execution_id: SimpleNamespace(
            execution_id=execution_id,
            workflow_name="demo-wf",
            workflow_id="demo-wf",
            status="completed",
            artifacts=["s3://bucket/out"],
            outputs={"ok": True},
            error=None,
        ),
        cancel=lambda execution_id: _status(execution_id, "cancelled"),
        approve_gate=lambda execution_id, step_name, **kwargs: _status(
            execution_id, "waiting_gate"
        ),
    )
    return SimpleNamespace(
        workflows=workflows,
        **{"config.gateway_url": "http://gw.test"} if False else {},
    )


def _studio_server(client: Any) -> ACPServer:
    return create_default_server(
        client_factory=lambda: client,
        session_factory=lambda c, name, model: FakeSession(),
        session_loader=lambda c, sid: FakeSession(session_id=sid),
    )


def test_workflows_list_serializes_definitions() -> None:
    server = _studio_server(_fake_workflow_client())
    output = invoke(server, "workflows/list", {})
    result = output[-1]["result"]
    assert result["count"] == 1
    assert result["workflows"][0]["workflow_name"] == "demo-wf"
    assert result["workflows"][0]["tags"] == ["lab"]


def test_workflows_trigger_status_detail_and_results() -> None:
    server = _studio_server(_fake_workflow_client())

    started = invoke(server, "workflows/trigger", {"workflow_name": "demo-wf"})
    assert started[-1]["result"]["execution_id"] == "exec-1"
    assert started[-1]["result"]["status"] == "running"

    status = invoke(server, "workflows/status", {"execution_id": "exec-1"})
    assert status[-1]["result"]["status"] == "running"
    assert status[-1]["result"]["progress"] == 0.5

    detail = invoke(server, "workflows/detail", {"execution_id": "exec-1"})
    steps = detail[-1]["result"]["execution"]["results"]["steps"]
    assert steps[0]["name"] == "ingest"

    results = invoke(server, "workflows/results", {"execution_id": "exec-1"})
    assert results[-1]["result"]["outputs"] == {"ok": True}


def test_workflows_cancel_and_gate_actions() -> None:
    server = _studio_server(_fake_workflow_client())

    cancelled = invoke(server, "workflows/cancel", {"execution_id": "exec-1"})
    assert cancelled[-1]["result"]["ok"] is True
    assert cancelled[-1]["result"]["status"] == "cancelled"

    gate = invoke(
        server,
        "workflows/approve_gate",
        {"execution_id": "exec-1", "step_name": "review", "approved": False},
    )
    assert gate[-1]["result"]["status"] == "waiting_gate"


def test_workflows_require_parameters() -> None:
    server = _studio_server(_fake_workflow_client())
    missing_execution = invoke(server, "workflows/status", {}, request_id=9)
    assert missing_execution[-1]["error"]
    missing_step = invoke(server, "workflows/approve_gate", {"execution_id": "x"}, request_id=10)
    assert missing_step[-1]["error"]


def test_projects_list_switch_create_roundtrip(tmp_path, monkeypatch) -> None:
    from r4 import project as project_module

    pm = project_module.ProjectManager(config_dir=tmp_path)
    monkeypatch.setattr(project_module, "get_project_manager", lambda: pm)
    server = _studio_server(object())

    created = invoke(server, "projects/create", {"project_id": "p2", "name": "Second"})
    assert created[-1]["result"]["current"] == "p2"
    assert [p["project_id"] for p in created[-1]["result"]["projects"]] == ["p2"]

    listed = invoke(server, "projects/list", {})
    assert listed[-1]["result"]["projects"][0]["active"] is True

    missing = invoke(server, "projects/switch", {"project_id": "nope"}, request_id=5)
    assert missing[-1]["error"]


def test_initialize_advertises_projects_and_workflows() -> None:
    server = create_default_server(client_factory=object)
    output = invoke(server, "initialize", {})
    caps = output[-1]["result"]["capabilities"]
    assert caps["projects"] is True
    assert caps["workflows"] is True


class _RenameFakeMessage:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class _RenameFakeCompanionSession:
    def __init__(self):
        self.id = "s1"
        self.name = "R4 Agent 2026-08-05 11:40"
        self.model = None
        self.interaction_mode = "chat"
        self.messages = []
        self._turn_cancel = None

    def _ensure_initialized(self):
        self.messages = []

    def add_user(self, text):
        self.messages.append(_RenameFakeMessage("user", text))

    def add_assistant(self, text):
        self.messages.append(_RenameFakeMessage("assistant", text))

    def request_turn_cancel(self):
        pass

    def send(self, message, stream=False):
        return "ok"


class _RenameFakeStore:
    def __init__(self):
        self.saved = 0

    def save(self, session):
        self.saved += 1


def _rename_session_entry(session):
    return (
        session._session.name
        if hasattr(session, "_session")
        else getattr(session, "_CompanionAcpSession__session", None).name
    )


def test_chat_auto_renames_placeholder_sessions() -> None:
    """First turn flips "R4 Agent ..." to a title from the user message."""
    from r4.companion.acp_session import CompanionAcpSession

    session = CompanionAcpSession.__new__(CompanionAcpSession)
    object.__setattr__(session, "_session", _RenameFakeCompanionSession())
    object.__setattr__(session, "_store", _RenameFakeStore())

    session.chat("Optimize MOF-5 for CO2 capture with first-principles Monte Carlo")

    name = _rename_session_entry(session)
    assert "R4 Agent" not in name
    assert "Optimize MOF-5" in name
    # Title is trimmed to a sane side-tab length
    assert len(name) <= 120


def test_mode_autopilot_grants_trust_without_bankrupting_fallback() -> None:
    """autopilot → TRUST; switching back to chat → AUTO_SAFE (ADR-104 D-1)."""
    from r4.companion.acp_session import CompanionAcpSession
    from r4.companion.permissions import PermissionMode

    class FakePerm:
        def __init__(self):
            from r4.companion.permissions import PermissionMode as PM2

            self.mode = PM2.ASK_ALWAYS

        def set_mode(self, m):
            self.mode = m

    class FakeSessionWithMeta:
        def __init__(self):
            self.id = "s1"
            self.name = "R4 Agent"
            self.model = None
            self._metadata = {}
            self.permissions = FakePerm()

        @property
        def metadata(self):
            return self._metadata

    store = type("S", (), {"save": lambda *a, **k: None})()
    session = CompanionAcpSession.__new__(CompanionAcpSession)
    object.__setattr__(session, "_session", FakeSessionWithMeta())
    object.__setattr__(session, "_store", store)

    session.set_mode("auto")
    assert session._session.permissions.mode is PermissionMode.TRUST
    assert "auto_approve_tools" not in session._session.metadata
    assert session.interaction_mode == "autopilot"

    session.set_mode("agent")  # same normalization alias path (auto==agent==autopilot)
    assert session._session.permissions.mode is PermissionMode.TRUST

    session.set_mode("chat")
    assert session._session.permissions.mode is PermissionMode.AUTO_SAFE
    assert session.interaction_mode == "chat"


def _loop_fake_client():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    return SimpleNamespace(
        config=SimpleNamespace(project_id="proj-1"),
        start_loop=MagicMock(
            return_value={"loop_id": "loop-9", "status": "running", "message": "ok"}
        ),
        list_loops=MagicMock(
            return_value={
                "loops": [{"loop_id": "loop-9", "status": "running", "phase": "planning"}]
            }
        ),
        get_loop_status=MagicMock(
            return_value={
                "loop_id": "loop-9",
                "project_id": "proj-1",
                "research_goal": "g",
                "status": "running",
                "phase": "planning",
            }
        ),
        list_loop_iterations=MagicMock(return_value=[]),
        stop_loop=MagicMock(return_value={"stopped": "loop-9"}),
    )


def test_acp_research_loops_routes_through_runtime() -> None:
    fake_client = _loop_fake_client()
    server = create_default_server(client_factory=lambda: fake_client)

    output = invoke(server, "research/loops", {})

    payload = output[-1]["result"]
    assert payload["project_id"] == "proj-1"
    assert payload["loops"][0]["loop_id"] == "loop-9"
    assert payload["detail"]["loop_id"] == "loop-9"
    assert payload["runtime"] == "umrs"
    assert payload["durable"] is True


def test_acp_research_loop_start_and_stop_use_runtime() -> None:
    fake_client = _loop_fake_client()
    server = create_default_server(client_factory=lambda: fake_client)

    started = invoke(server, "research/loop_start", {"research_goal": "g", "max_iterations": 5})
    stopped = invoke(server, "research/loop_stop", {"loop_id": "loop-9"})

    start_payload = started[-1]["result"]
    assert start_payload["loop"]["loop_id"] == "loop-9"
    assert start_payload["runtime"] == "umrs"
    fake_client.start_loop.assert_called_once_with("proj-1", "g", max_iterations=5, approved=False)
    stop_payload = stopped[-1]["result"]
    assert stop_payload["status"] == "stopped"
    assert stop_payload["runtime"] == "umrs"


# ── Multi-graph: registry + constraints (0069) ─────────────────────────────


def _graph_fake_client() -> Any:
    query_calls: list[dict] = []
    knowledge = SimpleNamespace(
        get_graph=lambda project_id: {"nodes": [], "edges": []},
        graphs=lambda **kwargs: [
            {
                "graph_id": "g-a",
                "name": "project-a",
                "kind": "project",
                "project_id": "proj-a",
                "status": "active",
                "description": "",
            },
            {
                "graph_id": "g-b",
                "name": "experiment-history",
                "kind": "experiment",
                "project_id": None,
                "status": "active",
                "description": "Experiments",
            },
        ],
        query=lambda query, **kwargs: (
            query_calls.append({"query": query, **kwargs})
            or {"query": {"results": [], "total_results": 0}}
        ),
        graph_constraints={},
    )
    return SimpleNamespace(
        config=SimpleNamespace(project_id="proj-a"),
        knowledge=knowledge,
        _query_calls=query_calls,
    )


def test_knowledge_graphs_lists_registry() -> None:
    server = _studio_server(_graph_fake_client())
    output = invoke(server, "knowledge/graphs", {"project_id": "proj-a"})
    result = output[-1]["result"]
    assert result["total"] == 2
    assert result["graphs"][1]["name"] == "experiment-history"


def test_knowledge_graph_constraints_roundtrip() -> None:
    client = _graph_fake_client()
    server = _studio_server(client)

    output = invoke(server, "knowledge/graph_constraints", {})
    # 2026-08-23: discovery defaults OFF — nothing auto-mounts as a satellite
    # until the session selects graphs (webview topic-ranked wave).
    assert output[-1]["result"]["constraints"] == {"include": [], "exclude": [], "discovery": False}

    invoke(
        server,
        "knowledge/graph_constraints",
        {"include": ["g-b"], "exclude": ["g-institutional"], "discovery": False},
    )
    echo = invoke(server, "knowledge/graph_constraints", {})
    assert echo[-1]["result"]["constraints"] == {
        "include": ["g-b"],
        "exclude": ["g-institutional"],
        "discovery": False,
    }
    assert client.knowledge.graph_constraints["include"] == ["g-b"]


def test_knowledge_query_applies_session_constraints() -> None:
    client = _graph_fake_client()
    server = _studio_server(client)

    invoke(
        server,
        "knowledge/graph_constraints",
        {"include": ["g-b"], "exclude": ["g-x"], "discovery": True},
    )
    invoke(server, "knowledge/query", {"query": "fwi experiments"})
    call = client._query_calls[-1]
    assert call["graphs"] == ["g-b"]
    assert call["exclude_graphs"] == ["g-x"]

    # Explicit per-call params still win over the session constraints
    invoke(server, "knowledge/query", {"query": "q2", "graphs": ["g-a"], "exclude_graphs": []})
    last = client._query_calls[-1]
    assert last["graphs"] == ["g-a"]
    # explicit empty clears the session exclusion for this call (None = none)
    assert last["exclude_graphs"] is None


def test_knowledge_import_forces_human_actor() -> None:
    """Studio ingest button = human authorization (must clear leaked agent)."""
    knowledge = SimpleNamespace(
        ingest=lambda pid, files: {"actor_seen": knowledge.actor, "ingested": len(files)},
        external_search=lambda pid, q: {"hits": 0},
        actor="agent",  # leaked from a prior companion turn
        session_id="s-1",
        graph_constraints={},
    )
    client = SimpleNamespace(
        config=SimpleNamespace(project_id="proj-a"),
        knowledge=knowledge,
    )
    server = _studio_server(client)

    output = invoke(server, "knowledge/import", {"files": ["/data/x.json"], "sources": []})
    result = output[-1]["result"]
    assert result["ingest"]["actor_seen"] is None  # human actor during the call
    assert knowledge.actor is None
    assert knowledge.session_id is None


# ── System tab surfaces: skills / commands / MCP / graph content ──────────────


def test_skills_list_bare_fallback_builtin_only() -> None:
    """No session yet → builtin skills listed, none loaded, session_bound=False."""
    server = _studio_server(object())
    output = invoke(server, "skills/list", {})
    result = output[-1]["result"]
    assert result["session_bound"] is False
    names = {skill["name"] for skill in result["skills"]}
    assert {"mcp_tools", "platform_ops", "research"} <= names
    assert all(skill["active"] is False for skill in result["skills"])
    assert any(
        skill["name"] == "mcp_tools" and "mcp_list_servers" in skill["tools"]
        for skill in result["skills"]
    )


def test_skills_set_active_requires_companion_session() -> None:
    server = _studio_server(object())
    output = invoke(
        server,
        "skills/set_active",
        {"session_id": "nope", "name": "mcp_tools", "active": True},
        request_id=7,
    )
    # FakeSession loader has no companion → curated error (and loader sees id)
    assert output[-1]["error"]


def test_commands_list_returns_catalog() -> None:
    server = _studio_server(object())
    output = invoke(server, "commands/list", {})
    commands = output[-1]["result"]["commands"]
    assert commands, "command catalog must not be empty"
    by_name = {cmd["name"]: cmd for cmd in commands}
    assert "help" in by_name
    assert by_name["help"]["category"] == "help"
    assert all({"name", "help", "category"} <= set(cmd) for cmd in commands)


def _mcp_fake_client() -> Any:
    from r4.mcp import MCPTool

    return SimpleNamespace(
        config=SimpleNamespace(project_id="proj-a"),
        mcp=SimpleNamespace(
            list_servers=lambda: [
                {
                    "name": "filesystem",
                    "server_id": "srv-1",
                    "status": "healthy",
                    "url": "http://fs:8080",
                    "description": "Local files",
                }
            ],
            list_tools=lambda server=None: [
                MCPTool(
                    name="read_file",
                    description="Read a file",
                    parameters={"type": "object"},
                    server="filesystem",
                )
            ],
            check_server_health=lambda server_id: {
                "server_id": server_id,
                "status": "healthy",
            },
        ),
    )


def test_mcp_servers_tools_health_roundtrip() -> None:
    server = _studio_server(_mcp_fake_client())

    servers = invoke(server, "mcp/servers", {})
    assert servers[-1]["result"]["servers"][0]["name"] == "filesystem"
    assert servers[-1]["result"]["servers"][0]["status"] == "healthy"

    tools = invoke(server, "mcp/tools", {"server": "filesystem"})
    tool = tools[-1]["result"]["tools"][0]
    assert tool["name"] == "read_file"
    assert tool["server"] == "filesystem"
    assert tool["input_schema"] == {"type": "object"}

    health = invoke(server, "mcp/server_health", {"server_id": "srv-1"})
    assert health[-1]["result"]["status"] == "healthy"

    missing = invoke(server, "mcp/server_health", {}, request_id=11)
    assert missing[-1]["error"]


def test_knowledge_graph_content_passthrough() -> None:
    calls: list[dict] = []
    knowledge = SimpleNamespace(
        graph_content=lambda graph_id, **kwargs: (
            calls.append({"graph_id": graph_id, **kwargs})
            or {
                "graph_id": graph_id,
                "name": "experiment-history",
                "nodes": [{"id": "n1", "type": "finding", "name": "f1"}],
                "edges": [],
                "truncated": False,
            }
        )
    )
    client = SimpleNamespace(config=SimpleNamespace(project_id="proj-a"), knowledge=knowledge)
    server = _studio_server(client)

    output = invoke(server, "knowledge/graph_content", {"graph_id": "g-abc"})
    result = output[-1]["result"]
    assert result["graph_id"] == "g-abc"
    assert result["nodes"][0]["type"] == "finding"
    assert calls[0]["node_limit"] == 200

    missing = invoke(server, "knowledge/graph_content", {}, request_id=12)
    assert missing[-1]["error"]


def test_acp_loop_start_agentic_and_requires_echo() -> None:
    fake_client = _loop_fake_client()
    server = create_default_server(client_factory=lambda: fake_client)

    started = invoke(
        server,
        "research/loop_start",
        {
            "research_goal": "optimize mof",
            "max_iterations": 5,
            "agentic": True,
            "requires": {"gpu": 1, "cluster": True},
        },
    )
    payload = started[-1]["result"]
    assert payload["agentic"] is True
    assert payload["requires"] == {"gpu": 1, "cluster": True}
    assert payload["runtime"] == "umrs"
    # UMRS substrate forwards the agentic flag to the client call
    _, kwargs = fake_client.start_loop.call_args
    assert kwargs["agentic"] is True


def test_acp_loop_start_rejects_bad_runtime_mode_and_requires() -> None:
    fake_client = _loop_fake_client()
    server = create_default_server(client_factory=lambda: fake_client)

    bad_mode = invoke(
        server,
        "research/loop_start",
        {"research_goal": "g", "runtime_mode": "quantum"},
        request_id=13,
    )
    assert bad_mode[-1]["error"]

    bad_requires = invoke(
        server,
        "research/loop_start",
        {"research_goal": "g", "requires": ["gpu"]},
        request_id=14,
    )
    assert bad_requires[-1]["error"]


def test_knowledge_reconcile_delegates_and_passes_sample() -> None:
    """knowledge/reconcile is a passthrough to the ADR-031 checker via the client."""
    called = {}

    def _reconcile(pid, sample=50):
        called.update({"project": pid, "sample": sample})
        return {"project_id": pid, "counts": {"COMPLETE": 1}, "assets": []}

    knowledge = SimpleNamespace(
        reconcile_project=_reconcile,
        graph_constraints={},
    )
    client = SimpleNamespace(
        config=SimpleNamespace(project_id="proj-a"),
        knowledge=knowledge,
    )
    server = _studio_server(client)

    output = invoke(server, "knowledge/reconcile", {"sample": 7})
    result = output[-1]["result"]
    assert called["project"] == "proj-a"
    assert called["sample"] == 7
    assert result["project_id"] == "proj-a"
    assert result["counts"]["COMPLETE"] == 1


def test_sessions_pinned_flag_round_trips_store(tmp_path) -> None:
    """C2: the pinned_at column round-trips via the store-direct helper."""
    from unittest.mock import MagicMock

    from r4.companion.acp_session import set_companion_session_pinned
    from r4.companion.session_store import CompanionSessionStore

    store = CompanionSessionStore(db_path=tmp_path / "acp-c2.db")
    record = store.create_session(
        MagicMock(
            config=SimpleNamespace(
                profile="default", gateway_url="http://g", project_id="p", model="m"
            )
        ),
        name="Pin Target",
    )

    assert set_companion_session_pinned(record.session_id, True, store=store)["pinned"] is True
    assert store.list_sessions()[0]["pinned"] is True

    assert set_companion_session_pinned(record.session_id, False, store=store)["pinned"] is False
    assert store.list_sessions()[0]["pinned"] is False

    with pytest.raises(ValueError, match="unknown session"):
        set_companion_session_pinned("ghost", True, store=store)


def test_sessions_compact_through_acp() -> None:
    """C4: sessions/compact runs the shared routine via the live session."""
    core = SimpleNamespace(
        messages=[SimpleNamespace(role="user", content=f"u{i}") for i in range(10)],
        context=SimpleNamespace(
            compact=lambda payload, keep_recent=6: (
                [{"role": "system", "content": "[Conversation summary]\nshort"}]
                + [{"role": "user", "content": "keep"}]
            )
        ),
        agent=None,
    )
    session = FakeSession(session_id="s-1", name="C4 session")
    session._session = core  # liveness duck-punch: same shape as CompanionAcpSession

    index = {"s-1": session}

    def loader(client, session_id):
        return index[session_id]

    server = create_default_server(client_factory=object, session_loader=loader)

    output = invoke(server, "sessions/compact", {"session_id": "s-1", "keep_recent": 3})

    result = output[-1]["result"]
    assert result["changed"] is True
    assert result["keep_recent"] == 3
    assert any(m for m in core.messages if m.content == "[Conversation summary]\nshort")
    assert session.save_count == 1


def test_companion_tool_approval_names_the_agent_that_asked() -> None:
    """ADR-092 slice 2: ``session/toolApproval`` says which agent asked."""
    from r4.companion.permissions import PermissionManager, PermissionMode

    _runtime, session, calls = _companion_approval_runtime("once")
    host = PermissionManager(
        PermissionMode.ASK_ALWAYS, approval_callback=session.companion.permissions.callback
    )
    assert host.fork_for_agent("skeptic[1]#0", run_id="r-1").check("shell_exec", {"command": "ls"})
    assert host.check("shell_exec", {"command": "pwd"})
    assert [params["agent"] for _method, params in calls] == ["skeptic[1]#0", ""]
