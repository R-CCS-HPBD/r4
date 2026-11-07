"""ADR-087 harness model-call contract (W1, 2026-10-05).

Blocking chat calls are streamed upstream and assembled; R4 sends an explicit
output limit for reasoning models; an empty reply in tool mode is nudged
instead of disabling tools for the session.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from r4.client import Client
from r4.companion.agent import _EMPTY_REPLY_NUDGE, ResearchCompanion
from r4.errors import APIError
from r4.model_context import REASONING_OUTPUT_TOKENS, default_max_output_tokens
from r4.stream_assembly import ChatStreamAssembler, StreamError


def _sse(chunk: dict) -> str:
    return "data: " + json.dumps(chunk)


def _tool_delta(index: int, function: dict, call_id: str | None = None) -> dict:
    call: dict = {"index": index, "function": function}
    if call_id:
        call["id"] = call_id
    return {"choices": [{"delta": {"tool_calls": [call]}}]}


_LINES = [
    _sse(
        {
            "id": "c1",
            "created": 1,
            "model": "glm-5.3",
            "choices": [{"delta": {"reasoning_content": "think "}}],
        }
    ),
    _sse({"choices": [{"delta": {"reasoning": "more"}}]}),
    _sse({"choices": [{"delta": {"content": "Hi"}}]}),
    _sse(_tool_delta(0, {"name": "shell_exec", "arguments": '{"comm'}, "t0")),
    _sse(_tool_delta(0, {"arguments": 'and": "ls"}'})),
    _sse(_tool_delta(1, {"name": "file_read", "arguments": "{}"}, "t1")),
    _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    _sse(
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}}
    ),
    "data: [DONE]",
]


def _assembled() -> dict:
    asm = ChatStreamAssembler()
    for line in _LINES:
        if not asm.feed_line(line):
            break
    return asm.assemble()


class TestStreamAssembly:
    def test_message_fields(self) -> None:
        out = _assembled()
        msg = out["choices"][0]["message"]
        assert (out["id"], out["model"]) == ("c1", "glm-5.3")
        assert (msg["content"], msg["reasoning_content"]) == ("Hi", "think more")
        assert out["choices"][0]["finish_reason"] == "tool_calls"
        assert out["usage"]["total_tokens"] == 12

    def test_split_tool_calls(self) -> None:
        calls = _assembled()["choices"][0]["message"]["tool_calls"]
        assert [c["id"] for c in calls] == ["t0", "t1"]
        assert json.loads(calls[0]["function"]["arguments"]) == {"command": "ls"}

    def test_error_event_raises(self) -> None:
        with pytest.raises(StreamError, match="overloaded"):
            ChatStreamAssembler().feed_line('data: {"error": {"message": "overloaded"}}')


class _FakeResponse:
    def __init__(self, lines=None, content_type="text/event-stream", body=b""):
        self.headers = {"content-type": content_type}
        self._lines = lines or []
        self._body = body

    def iter_lines(self):
        yield from self._lines

    def read(self):
        return self._body


class _FakeHTTP:
    def __init__(self, response=None, stream_exc=None, post_result=None):
        self.response, self.stream_exc, self.post_result = response, stream_exc, post_result
        self.streamed, self.posted = [], []

    @contextmanager
    def stream(self, method, url, *, json_body=None, **_kw):
        self.streamed.append(json_body)
        if self.stream_exc is not None:
            raise self.stream_exc
        yield self.response

    def post(self, url, json_body=None, **_kw):
        self.posted.append(json_body)
        return self.post_result


_CANCEL = SimpleNamespace(check=lambda: None, register_close=lambda _close: None)


def _client_with(http) -> tuple[Client, SimpleNamespace]:
    client = Client.__new__(Client)
    client._http_for_route = lambda _route: http
    client._headers_for_route = lambda _route, extra_headers=None: {}
    return client, SimpleNamespace(base_url="http://x", kind="public")


class TestStreamedBlockingCall:
    def test_blocking_call_is_streamed_and_assembled(self, monkeypatch) -> None:
        monkeypatch.delenv("R4_STREAM_BLOCKING", raising=False)
        http = _FakeHTTP(response=_FakeResponse(_LINES))
        client, route = _client_with(http)
        data = client._request_via_http(route, {"model": "glm-5.3", "messages": []}, None, _CANCEL)
        assert data["choices"][0]["message"]["content"] == "Hi"
        assert (
            http.streamed[0]["stream"] is True
            and http.streamed[0]["stream_options"]["include_usage"]
        )
        assert http.posted == []

    def test_json_reply_to_stream_is_parsed(self) -> None:
        body = json.dumps({"choices": [{"message": {"content": "plain"}}]}).encode()
        http = _FakeHTTP(response=_FakeResponse(content_type="application/json", body=body))
        client, route = _client_with(http)
        data = client._request_via_http(route, {"model": "m", "messages": []}, None, _CANCEL)
        assert data["choices"][0]["message"]["content"] == "plain"

    def test_http_400_falls_back_to_plain_post(self) -> None:
        http = _FakeHTTP(
            stream_exc=APIError("bad", status_code=400),
            post_result={"choices": [{"message": {"content": "posted"}}]},
        )
        client, route = _client_with(http)
        data = client._request_via_http(route, {"model": "m", "messages": []}, None, _CANCEL)
        assert data["choices"][0]["message"]["content"] == "posted"
        assert http.posted and "stream" not in http.posted[0]

    def test_other_errors_propagate(self) -> None:
        http = _FakeHTTP(stream_exc=APIError("upstream", status_code=502))
        client, route = _client_with(http)
        with pytest.raises(APIError):
            client._request_via_http(route, {"model": "m", "messages": []}, None, _CANCEL)

    def test_calls_without_a_turn_token_are_unchanged(self) -> None:
        http = _FakeHTTP(post_result={"choices": [{"message": {"content": "posted"}}]})
        client, route = _client_with(http)
        client._request_via_http(route, {"model": "m", "messages": []}, None, None)
        assert http.streamed == [] and http.posted

    def test_switch_off_uses_plain_post(self, monkeypatch) -> None:
        monkeypatch.setenv("R4_STREAM_BLOCKING", "0")
        http = _FakeHTTP(post_result={"choices": [{"message": {"content": "posted"}}]})
        client, route = _client_with(http)
        client._request_via_http(route, {"model": "m", "messages": []}, None, _CANCEL)
        assert http.streamed == [] and http.posted


def _agent(model="glm-5.3@rikyu") -> ResearchCompanion:
    return ResearchCompanion(SimpleNamespace(_config=SimpleNamespace(model=model)), model=model)


class TestOutputLimit:
    def test_reasoning_family_default(self, monkeypatch) -> None:
        monkeypatch.delenv("R4_MAX_OUTPUT_TOKENS", raising=False)
        assert default_max_output_tokens("glm-5.3") == REASONING_OUTPUT_TOKENS
        assert default_max_output_tokens("llama-3.1-8b") is None
        payload = _agent()._chat_payload([{"role": "user", "content": "hi"}], None)
        assert payload["max_tokens"] == REASONING_OUTPUT_TOKENS

    def test_unknown_model_leaves_server_default(self, monkeypatch) -> None:
        monkeypatch.delenv("R4_MAX_OUTPUT_TOKENS", raising=False)
        payload = _agent("my-local-model")._chat_payload([{"role": "user", "content": "hi"}], None)
        assert "max_tokens" not in payload

    def test_env_override_and_zero(self, monkeypatch) -> None:
        monkeypatch.setenv("R4_MAX_OUTPUT_TOKENS", "8000")
        assert (
            _agent()._chat_payload([{"role": "user", "content": "hi"}], None)["max_tokens"] == 8000
        )
        monkeypatch.setenv("R4_MAX_OUTPUT_TOKENS", "0")
        assert "max_tokens" not in _agent()._chat_payload([{"role": "user", "content": "hi"}], None)

    def test_clamped_to_window_left(self, monkeypatch) -> None:
        monkeypatch.delenv("R4_MAX_OUTPUT_TOKENS", raising=False)
        agent = _agent()
        agent.model_context_window = 140_000
        big = [{"role": "user", "content": "x" * 70_000}]  # ~20k tokens at 3.5 chars/token
        limit = agent._output_token_limit(big)
        assert limit is not None and limit < REASONING_OUTPUT_TOKENS
        agent.model_context_window = 10_000
        assert agent._output_token_limit(big) is None

    def test_published_limit_wins_over_family_default(self, monkeypatch) -> None:
        monkeypatch.delenv("R4_MAX_OUTPUT_TOKENS", raising=False)
        agent = _agent()
        agent.model_output_limit = 50_000
        assert agent._output_token_limit([{"role": "user", "content": "hi"}]) == 50_000


class TestEmptyReplyInToolMode:
    def _run(self, responses):
        agent = _agent()
        agent._fit_agent_messages = lambda messages, tools=None: messages
        agent._emit_tool_selection = lambda *_a, **_k: None
        queue = list(responses)
        observed: list[list[dict]] = []

        def fake_round(messages, active_tools, stream):
            observed.append(list(messages))
            return None, queue.pop(0)

        agent._agentic_round_response = fake_round
        result = agent._run_agentic_turn(
            messages=[{"role": "user", "content": "work"}],
            tool_schemas=[{"type": "function", "function": {"name": "noop"}}],
            user_message="work",
            tools=[{"name": "noop"}],
        )
        return agent, result, observed

    def test_empty_reply_is_nudged_and_tools_stay_on(self) -> None:
        empty = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
        final = {"choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]}
        agent, result, observed = self._run([empty, final])
        assert result == "answer"
        assert observed[1][-1] == {"role": "user", "content": _EMPTY_REPLY_NUDGE}
        assert agent._tool_calling_disabled is False
