"""The Claude Code lane's Anthropic -> OpenAI bridge (same_model/anthropic_bridge.py).

B6 (harness repair plan, 2026-10-05): the upstream pump must always end the
relay. An upstream exception that was not an httpx error used to kill the pump
with nothing queued, and the client received pings forever. No network: the
upstream response is a stand-in with ``aiter_raw`` / ``aclose``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("starlette", reason="the bridge serves through starlette")
httpx = pytest.importorskip("httpx")

from benchmarks.harness.same_model import anthropic_bridge as bridge  # noqa: E402


class _Upstream:
    """Stand-in for an httpx streaming response: yields chunks, then raises or ends."""

    def __init__(self, chunks: list[bytes], error: BaseException | None = None) -> None:
        self.chunks, self.error, self.closed = chunks, error, False

    async def aiter_raw(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        self.closed = True


def _sse(obj: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


_TEXT = _sse({"model": "glm-5.3", "choices": [{"delta": {"content": "partial"}}]})


async def _drain(resp: _Upstream) -> list[tuple[str, Any]]:
    q: asyncio.Queue = asyncio.Queue()
    await asyncio.wait_for(bridge._pump(resp, q), timeout=2)  # type: ignore[arg-type]
    return [q.get_nowait() for _ in range(q.qsize())]


@pytest.mark.parametrize(
    "error, terminal",
    [
        (None, ("eof", None)),
        (httpx.ReadError("cut"), ("error", "ReadError")),
        (RuntimeError("decoder blew up"), ("error", "RuntimeError")),  # B6
        (ValueError("bad chunk"), ("error", "ValueError")),  # B6
    ],
)
def test_pump_always_queues_one_terminal_item(error: Exception | None, terminal: tuple) -> None:
    items = asyncio.run(_drain(_Upstream([_TEXT], error)))
    assert items == [("data", _TEXT), terminal]


def test_a_cancelled_pump_queues_nothing_more() -> None:
    async def scenario() -> list[tuple[str, Any]]:
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        task = asyncio.create_task(bridge._pump(_Upstream([_TEXT, _TEXT, _TEXT]), q))  # type: ignore[arg-type]
        await asyncio.sleep(0.01)  # the pump now waits on the full queue
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return [q.get_nowait() for _ in range(q.qsize())]

    assert asyncio.run(scenario()) == [("data", _TEXT)]


def test_relay_ends_with_an_error_event_on_a_non_httpx_upstream_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end through StreamRelay.events(): the stream closes, never pings forever."""
    monkeypatch.setattr(bridge, "PING_EVERY_S", 0.05)
    resp = _Upstream([_TEXT], RuntimeError("decoder blew up"))
    up = SimpleNamespace(target="glm-5.3", reasoning="carry")
    relay = bridge.StreamRelay(up, resp, 1, "case=t", 0.0, msg_id="msg_t")  # type: ignore[arg-type]

    async def collect() -> list[bytes]:
        return [event async for event in relay.events()]

    events = asyncio.run(asyncio.wait_for(collect(), timeout=2))
    text = b"".join(events).decode()
    assert text.startswith("event: message_start")
    assert '"text": "partial"' in text
    assert text.rstrip().splitlines()[-2] == "event: error"
    assert "upstream stream broke: RuntimeError" in text
    assert resp.closed
    assert "stream_error=RuntimeError" in capsys.readouterr().out
