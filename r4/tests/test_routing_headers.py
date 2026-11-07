"""Tests for MM-4-MA routing headers (X-R4-Task / X-R4-Agent)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from r4.client import Client, _build_routing_headers
from r4.config import Config


@pytest.fixture(autouse=True)
def isolate_xdg_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("R4_CONFIG", raising=False)


def _make_client() -> Client:
    return Client(
        config=Config(
            gateway_url="http://gw.test:8080",
            project_id="proj-x",
            api_key="sk-test",
            model="chat-3b",
        )
    )


class TestBuildRoutingHeaders:
    def test_none_when_neither_set(self):
        assert _build_routing_headers() is None
        assert _build_routing_headers(task=None, agent=None) is None
        assert _build_routing_headers(task="", agent="") is None

    def test_task_only(self):
        assert _build_routing_headers(task="code") == {"X-R4-Task": "code"}

    def test_agent_only(self):
        assert _build_routing_headers(agent="planner") == {"X-R4-Agent": "planner"}

    def test_both(self):
        assert _build_routing_headers(task="code", agent="coder") == {
            "X-R4-Task": "code",
            "X-R4-Agent": "coder",
        }

    def test_custom_task_label(self):
        # Free-form task hints flow through unchanged.
        assert _build_routing_headers(task="custom:vibe-coder") == {
            "X-R4-Task": "custom:vibe-coder"
        }


class TestClientChatRoutingHeaders:
    def test_chat_completion_emits_routing_headers(self):
        client = _make_client()
        with patch.object(
            client.http,
            "post",
            return_value={"choices": [{"message": {"content": "ok"}}]},
        ) as post:
            client.chat("hi", task="code", agent="coder")
            post.assert_called_once()
            kwargs = post.call_args.kwargs
            assert kwargs["headers"] == {"X-R4-Task": "code", "X-R4-Agent": "coder"}

    def test_chat_completion_no_headers_without_hints(self):
        client = _make_client()
        with patch.object(
            client.http,
            "post",
            return_value={"choices": [{"message": {"content": "ok"}}]},
        ) as post:
            client.chat("hi")
            post.assert_called_once()
            assert post.call_args.kwargs["headers"] is None

    def test_streaming_emits_routing_headers(self):
        client = _make_client()
        with patch.object(client.http, "iter_sse_content", return_value=iter([])) as it:
            list(client.chat_stream("hi", task="search"))
            it.assert_called_once()
            assert it.call_args.kwargs["headers"] == {"X-R4-Task": "search"}

    def test_stream_chat_chunks_emits_routing_headers(self):
        from r4.chat_types import ChatMessage

        client = _make_client()
        with patch.object(client.http, "iter_sse_chunks", return_value=iter([])) as it:
            list(
                client.stream_chat_chunks([ChatMessage.user("hi")], task="reason", agent="planner")
            )
            it.assert_called_once()
            assert it.call_args.kwargs["headers"] == {
                "X-R4-Task": "reason",
                "X-R4-Agent": "planner",
            }

    def test_chat_completion_passes_through_headers_with_tools(self):
        from r4.chat_types import ChatMessage

        client = _make_client()
        with patch.object(
            client.http,
            "post",
            return_value={"choices": [{"message": {"content": "ok"}}]},
        ) as post:
            client.chat_completion(
                [ChatMessage.user("hi")],
                tools=[{"type": "function", "function": {"name": "x"}}],
                task="code",
            )
            assert post.call_args.kwargs["headers"] == {"X-R4-Task": "code"}


class TestCLITaskFlag:
    def test_chat_subparser_registers_task_and_agent_flags(self):
        # Phase 2m refactor (2026-04-28): the chat subparser now lives in
        # ``r4/src/r4/_cli/chat.py``. Pin both flags + the help text
        # marker so they cannot regress silently.
        from pathlib import Path

        chat_src = (
            Path(__file__).resolve().parents[1] / "src" / "r4" / "_cli" / "chat.py"
        ).read_text()
        assert '"--task"' in chat_src
        assert '"--agent"' in chat_src
        assert "X-R4-Task" in chat_src
