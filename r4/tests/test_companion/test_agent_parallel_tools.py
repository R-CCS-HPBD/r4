"""Tests for parallel tool-call execution in ResearchCompanion.

Covers the concurrency_safe batch scheduler in ``_execute_tool_calls``:
safe tools run in a thread pool, unsafe tools stay serial, results keep
source order with matching tool_call_ids, and serial-only concerns
(permissions, arg parse errors) behave the same inside a batch.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from r4.companion.agent import ResearchCompanion
from r4.companion.context import ContextManager
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.skills import SkillRegistry


def _chat_response(content: str | None, tool_calls: list[dict] | None = None):
    message = {"content": content, "role": "assistant"}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return MagicMock(
        content=content or "",
        model="test-model",
        usage=MagicMock(prompt_tokens=10, completion_tokens=4, total_tokens=14),
        raw_response={
            "choices": [{"message": message, "finish_reason": "stop"}],
        },
    )


def _tool_call(call_id: str, name: str, arguments: str) -> dict:
    return {
        "id": call_id,
        "function": {"name": name, "arguments": arguments},
    }


def _build_agent(mock_client) -> ResearchCompanion:
    return ResearchCompanion(
        mock_client,
        context_manager=ContextManager(client=mock_client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
        model="test-model",
    )


def _make_tool(name: str, execute, *, concurrency_safe: bool) -> dict:
    return {
        "name": name,
        "description": name,
        "parameters": {
            "type": "object",
            "properties": {"tag": {"type": "string"}},
        },
        "execute": execute,
        "concurrency_safe": concurrency_safe,
    }


@pytest.fixture
def mock_client():
    client = MagicMock()
    client._config.model = "test-model"
    return client


def _tool_messages(history: list[dict]) -> list[dict]:
    return [m for m in history if m.get("role") == "tool"]


def _request_tool_messages(mock_client, request_index: int = -1) -> list[dict]:
    """Tool messages as actually sent to the LLM in a follow-up request."""
    payload = mock_client._request.call_args_list[request_index].args[0]
    return [m for m in payload["messages"] if m.get("role") == "tool"]


def test_safe_tools_run_concurrently(mock_client):
    """Three safe tools are in flight at once: each waits for the other two.

    A barrier, not wall-clock time: the old ``elapsed < 2 * sleep`` bound
    failed on a loaded self-hosted runner (main CI at 2e537b11, 2026-10-22).
    Run serially, the first tool's wait times out and none of them finish.
    """
    barrier = threading.Barrier(3, timeout=10)
    finished: list[str] = []

    def _meet_tool(tag: str = "") -> str:
        barrier.wait()
        finished.append(tag)
        return f"met:{tag}"

    tools = [_make_tool(f"safe_{i}", _meet_tool, concurrency_safe=True) for i in range(3)]
    round_calls = [_tool_call(f"call_{i}", f"safe_{i}", f'{{"tag": "{i}"}}') for i in range(3)]
    mock_client._request.side_effect = [
        _chat_response(None, round_calls),
        _chat_response("done."),
    ]
    agent = _build_agent(mock_client)

    response = agent.send("run the batch", tools=tools)

    assert response == "done."
    assert sorted(finished) == ["0", "1", "2"], "the three safe tools did not run concurrently"


def test_results_keep_source_order_under_parallel(mock_client):
    """Slow-first batch still yields tool results in `tool_calls` order."""
    lock = threading.Lock()
    finished: list[str] = []

    def _make_exec(call_id: str, delay: float):
        def _exec(tag: str = "") -> str:
            time.sleep(delay)
            with lock:
                finished.append(call_id)
            return f"result-{call_id}"

        return _exec

    tools = [
        _make_tool("slow_a", _make_exec("a", 0.30), concurrency_safe=True),
        _make_tool("mid_b", _make_exec("b", 0.10), concurrency_safe=True),
        _make_tool("fast_c", _make_exec("c", 0.02), concurrency_safe=True),
    ]
    round_calls = [
        _tool_call("call_a", "slow_a", "{}"),
        _tool_call("call_b", "mid_b", "{}"),
        _tool_call("call_c", "fast_c", "{}"),
    ]
    mock_client._request.side_effect = [
        _chat_response(None, round_calls),
        _chat_response("done."),
    ]
    agent = _build_agent(mock_client)

    history: list[dict] = []
    # Sanity: out-of-order finish actually happens without order enforcement.
    agent.send("batch", history=history, tools=tools)
    assert finished == ["c", "b", "a"] or finished == ["b", "c", "a"]

    tool_msgs = _request_tool_messages(mock_client)
    assert [m["tool_call_id"] for m in tool_msgs] == ["call_a", "call_b", "call_c"]
    assert [m["content"] for m in tool_msgs] == ["result-a", "result-b", "result-c"]


def test_unsafe_tool_partitions_batches_serially(mock_client):
    """Unsafe middle call runs alone — after the first batch, before the second."""
    lock = threading.Lock()
    timeline: dict[str, tuple[float, float]] = {}

    def _rec(name: str, delay: float):
        def _exec(tag: str = "") -> str:
            start = time.monotonic()
            time.sleep(delay)
            end = time.monotonic()
            with lock:
                timeline[name] = (start, end)
            return name

        return _exec

    tools = [
        _make_tool("safe_a", _rec("safe_a", 0.20), concurrency_safe=True),
        _make_tool("safe_b", _rec("safe_b", 0.20), concurrency_safe=True),
        _make_tool("unsafe_x", _rec("unsafe_x", 0.10), concurrency_safe=False),
        _make_tool("safe_c", _rec("safe_c", 0.20), concurrency_safe=True),
        _make_tool("safe_d", _rec("safe_d", 0.20), concurrency_safe=True),
    ]
    round_calls = [
        _tool_call("1", "safe_a", "{}"),
        _tool_call("2", "safe_b", "{}"),
        _tool_call("3", "unsafe_x", "{}"),
        _tool_call("4", "safe_c", "{}"),
        _tool_call("5", "safe_d", "{}"),
    ]
    mock_client._request.side_effect = [
        _chat_response(None, round_calls),
        _chat_response("done."),
    ]
    agent = _build_agent(mock_client)
    agent.send("batch", tools=tools)

    a_start, a_end = timeline["safe_a"]
    b_start, b_end = timeline["safe_b"]
    x_start, x_end = timeline["unsafe_x"]
    c_start, c_end = timeline["safe_c"]
    d_start, d_end = timeline["safe_d"]

    # First batch overlapped.
    assert b_start < a_end and a_start < b_end
    # Unsafe call started only after the whole first batch finished.
    assert x_start >= max(a_end, b_end) - 0.01
    # Second batch started only after the unsafe call finished…
    assert c_start >= x_end - 0.01
    assert d_start >= x_end - 0.01
    # …and ran concurrently.
    assert d_start < c_end and c_start < d_end


def test_permission_denial_inside_parallel_batch(mock_client):
    """A policy-denied tool returns its block message; siblings still execute."""
    executed: list[str] = []
    lock = threading.Lock()

    def _exec(tool_name: str):
        def _run(tag: str = "") -> str:
            with lock:
                executed.append(tool_name)
            return f"ran-{tool_name}"

        return _run

    tools = [
        _make_tool("safe_ok", _exec("safe_ok"), concurrency_safe=True),
        _make_tool("safe_deny", _exec("safe_deny"), concurrency_safe=True),
    ]
    round_calls = [
        _tool_call("1", "safe_ok", "{}"),
        _tool_call("2", "safe_deny", "{}"),
    ]
    mock_client._request.side_effect = [
        _chat_response(None, round_calls),
        _chat_response("done."),
    ]
    agent = _build_agent(mock_client)

    perms = MagicMock()
    perms.check.side_effect = lambda name, args: name != "safe_deny"
    agent._permissions = perms

    history: list[dict] = []
    response = agent.send("batch", history=history, tools=tools)

    assert response == "done."
    assert executed == ["safe_ok"]
    tool_msgs = _request_tool_messages(mock_client)
    assert [m["tool_call_id"] for m in tool_msgs] == ["1", "2"]
    assert tool_msgs[0]["content"] == "ran-safe_ok"
    assert "blocked by the permission policy" in tool_msgs[1]["content"]


def test_bad_arguments_inside_parallel_batch(mock_client):
    """Invalid JSON args yield an error result in-place; the batch continues."""

    def _exec(tag: str = "") -> str:
        return "fine"

    tools = [_make_tool("safe_ok", _exec, concurrency_safe=True)]
    round_calls = [
        _tool_call("1", "safe_ok", "{not-json"),
        _tool_call("2", "safe_ok", "{}"),
    ]
    mock_client._request.side_effect = [
        _chat_response(None, round_calls),
        _chat_response("done."),
    ]
    agent = _build_agent(mock_client)

    agent.send("batch", tools=tools)

    tool_msgs = _request_tool_messages(mock_client)
    assert [m["tool_call_id"] for m in tool_msgs] == ["1", "2"]
    assert "invalid JSON arguments" in tool_msgs[0]["content"]
    assert tool_msgs[1]["content"] == "fine"


def test_listener_dispatch_thread_safety(mock_client):
    """Events from parallel workers all land in the listener without error."""
    events: list = []
    lock = threading.Lock()

    def _exec(tag: str = "") -> str:
        time.sleep(0.02)
        return "ok"

    tools = [_make_tool(f"safe_{i}", _exec, concurrency_safe=True) for i in range(6)]
    round_calls = [_tool_call(str(i), f"safe_{i}", "{}") for i in range(6)]
    mock_client._request.side_effect = [
        _chat_response(None, round_calls),
        _chat_response("done."),
    ]
    agent = _build_agent(mock_client)

    def _listener(event) -> None:
        with lock:
            events.append(event)

    agent.on_event(_listener)

    agent.send("batch", tools=tools)

    results = [e for e in events if e.event_type.value == "tool_result"]
    assert len(results) == 6
