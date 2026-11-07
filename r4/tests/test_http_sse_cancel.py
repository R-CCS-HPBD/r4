"""Pin for HTTP SSE cancellation threading (Esc-immediate chain, T5 topic-11).

`HTTPCore.iter_sse_chunks` accepts `cancel_check` + `register_abort` so an
active `TurnCancellation` token closes the response AND runs a check between
chunks. Otherwise Esc-stop waited for the next chunk landing (multi-taps or
hold-aspect behavior reported live by the user).
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import Mock

import httpx
import pytest

from r4._http import HTTPCore
from r4.companion.cancellation import TurnCancellation, TurnCancelledError


def _chunk_lines():
    return [
        'data: {"choices":[{"delta":{"content":"a-nice"}}]}',
        'data: {"choices":[{"delta":{}}]}',
        "data: [DONE]",
    ]


class _Resp:
    def __init__(self):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_lines(self):
        return iter(_chunk_lines())

    def close(self):
        self.closed = True


def _core_with_response(resp):
    core = HTTPCore(Mock(api_key=None, timeout=1, verify_ssl=False))
    core.stream = Mock(return_value=resp)
    return core


def test_iter_sse_chunks_runs_cancel_check_between_chunks():
    check_log: list[int] = []
    core = _core_with_response(_Resp())

    def check():
        check_log.append(1)

    chunks = list(
        core.iter_sse_chunks(
            "http://test/v1/chat/completions",
            cancel_check=check,
        )
    )
    assert len(chunks) == 2
    assert check_log  # ran at least once between chunks


def test_iter_sse_chunks_registers_abort_closer():
    resp = _Resp()
    core = _core_with_response(resp)
    token = TurnCancellation()
    it = iter(
        core.iter_sse_chunks(
            "http://test/v1/chat/completions",
            register_abort=token.register_close,
        )
    )
    next(it)
    token.request()
    assert resp.closed is True


class _ClosingStreamClient:
    """httpx.Client stand-in: one SSE chunk, then the read error a
    cancel-closed response raises."""

    def __init__(self, token: TurnCancellation):
        self.token = token

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    @contextmanager
    def stream(self, *_args, **_kwargs):
        token = self.token

        class _Live:
            status_code = 200
            headers: dict[str, str] = {}
            url = "http://test/v1/chat/completions"
            history: list = []

            def iter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"w0 "}}]}'
                token.request()  # Esc: the closer shuts the response …
                raise httpx.ReadError("[Errno 9] Bad file descriptor")  # … mid-read

            def close(self):
                pass

        yield _Live()


def test_cancel_closed_stream_raises_turn_cancel_not_request_failed():
    """Stabilization sweep 2026-10-21, chat#9: the read error of a response
    closed by Esc escaped as R4Error("Request failed"), so the session never
    recorded the turn as stopped (the model saw no assistant row at all)."""
    token = TurnCancellation()
    core = HTTPCore(Mock(api_key=None, timeout=1, verify_ssl=False, gateway_url=None))
    core._client = lambda _timeout=None: _ClosingStreamClient(token)
    stream = core.iter_sse_chunks(
        "http://test/v1/chat/completions",
        provider_request=True,
        cancel_check=token.check,
        register_abort=token.register_close,
    )
    assert next(stream)["choices"][0]["delta"]["content"] == "w0 "
    with pytest.raises(TurnCancelledError):
        next(stream)
