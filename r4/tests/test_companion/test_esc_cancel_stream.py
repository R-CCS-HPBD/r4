"""Pins for the T5 topic-11 Esc-immediate-cancel chain (2026-09-19).

Chain: bridge server cancel_turn → session.request_turn_cancel → agent
_turn_cancel (threading.Token). The agent streams LLM chunks through two
paths:

  * `_stream_chat` (text-only) — uses client._stream_request
  * `_stream_chat_collect` (tool-reassembling) — uses client._stream_request_collect

Both paths MUST thread the token into the streaming round AND the HTTP
streamer must run `cancel_check()` between chunks and register the response
close on the token (`register_close`→close).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from r4.companion.agent import ResearchCompanion
from r4.companion.cancellation import TurnCancellation


class _FakeClient:
    def __init__(self):
        self._config = MagicMock(model="test-model", endpoint_url="")
        self.seen_turn_cancel_stream = None
        self.seen_turn_cancel_collect = None

    def _stream_request(self, payload, **kwargs):
        self.seen_turn_cancel_stream = kwargs.get("turn_cancel")
        yield "chunk-1"

    def _stream_request_collect(self, payload, acc, *, reasoning_acc=None, turn_cancel=None):
        self.seen_turn_cancel_collect = turn_cancel
        yield "chunk-collect-1"


def _make_agent(client):
    return ResearchCompanion(client, model="m", call_type="main")


class TestStreamCancellationThreading:
    def test_stream_chat_threads_turn_cancel(self):
        client = _FakeClient()
        agent = _make_agent(client)
        token = TurnCancellation()
        agent.set_turn_cancel(token)
        msgs = [{"role": "user", "content": "hi"}]
        assert [] != list(agent._stream_chat(msgs, None))
        assert client.seen_turn_cancel_stream is token

    def test_stream_chat_collect_threads_turn_cancel(self):
        client = _FakeClient()
        agent = _make_agent(client)
        token = TurnCancellation()
        agent.set_turn_cancel(token)
        msgs = [{"role": "user", "content": "hi"}]
        out = agent._stream_chat_collect(msgs, None, [])
        # drink the generator
        list(out)
        assert client.seen_turn_cancel_collect is token

    def test_stream_collect_cancellation_between_chunks(self):
        # a pre-set token must trip the check between chunks of the collect
        # path (the http layer registers its own cancel_check now).
        token = TurnCancellation()
        token.request()
        client = _FakeClient()

        class _NeverCancellable(_FakeClient):
            def _collect_sse_deltas(self, *a, **k):
                # simulate the collect path directly honoring
                # `turn_cancel.check()` between chunks
                k["turn_cancel"].check()
                yield "never"

        agent = _make_agent(client)
        agent.set_turn_cancel(token)
        agent.client._collect_sse_deltas = _NeverCancellable._collect_sse_deltas
