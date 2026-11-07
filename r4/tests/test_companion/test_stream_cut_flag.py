"""A stream that ends without a finish signal is flagged, never finalised
as a complete answer (stabilization sweep 2026-10-21, chat#11).

The provider sent three content deltas and then a clean EOF: no
finish_reason, no ``data: [DONE]``. The reply rendered and was stored like
any finished answer.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

from r4._http import HTTPCore
from r4.client import STREAM_CUT_FINISH, Client
from r4.companion.agent import STREAM_CUT_NOTE, ResearchCompanion


def _delta(text: str, finish: str | None = None) -> str:
    return 'data: {"choices":[{"delta":{"content":"%s"},"finish_reason":%s}]}' % (
        text,
        f'"{finish}"' if finish else "null",
    )


class _Resp:
    def __init__(self, lines: list[str]):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_lines(self):
        return iter(self.lines)


def _client(lines: list[str]) -> Client:
    core = HTTPCore(Mock(api_key=None, timeout=1, verify_ssl=False))
    core.stream = Mock(return_value=_Resp(lines))
    client = Client.__new__(Client)
    client._http_for_route = lambda _route: core
    client._record_stream_usage = lambda *a, **k: None
    return client


def _collect(client: Client) -> list[str]:
    return list(client._collect_sse_deltas("u", SimpleNamespace(kind="public"), {}, {}, []))


def test_clean_eof_without_finish_or_done_is_flagged() -> None:
    client = _client([_delta("partial-alpha "), _delta("partial-beta")])
    assert _collect(client) == ["partial-alpha ", "partial-beta"]
    assert client._last_stream_finish == STREAM_CUT_FINISH


def test_done_terminator_is_a_finished_stream() -> None:
    client = _client([_delta("all "), _delta("here"), "data: [DONE]"])
    _collect(client)
    assert client._last_stream_finish is None


def test_finish_reason_without_done_is_a_finished_stream() -> None:
    client = _client([_delta("all "), _delta("here", finish="stop")])
    _collect(client)
    assert client._last_stream_finish == "stop"


class _CutClient:
    def __init__(self):
        self._config = MagicMock(model="m", endpoint_url="")
        self._last_stream_finish = None

    def _stream_request_collect(self, payload: dict[str, Any], acc, **_kw):
        def gen():
            yield "partial-alpha partial-beta"
            self._last_stream_finish = STREAM_CUT_FINISH

        return gen()


def test_agent_marks_a_cut_reply_on_screen_and_in_the_round() -> None:
    agent = ResearchCompanion(_CutClient(), model="m", call_type="main")
    chunks: list[str] = []
    agent.on_event(
        lambda e: chunks.append(e.message) if e.event_type.value == "response_chunk" else None
    )
    payload = agent._stream_agentic_round([{"role": "user", "content": "q"}], None)
    content = payload["choices"][0]["message"]["content"]
    assert content == f"partial-alpha partial-beta\n\n{STREAM_CUT_NOTE}"
    assert "".join(chunks) == content
