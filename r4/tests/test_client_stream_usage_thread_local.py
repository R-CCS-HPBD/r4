"""Pins for per-thread stream measurements and the model preflight (ADR-092 D-6, D-7).

Parallel plan agents share one ``Client``. The last stream's measured usage
(and finish reason) used to be one attribute: agent B's stream overwrote agent
A's before A consumed it, so A charged B's tokens and B's were lost.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from r4.client import Client
from r4.errors import R4Error


def _usage_chunks(prompt_tokens: int) -> list[dict[str, Any]]:
    return [
        {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 1}},
    ]


def _bare_client() -> Client:
    client = Client.__new__(Client)
    by_model = {"model-a": _usage_chunks(111), "model-b": _usage_chunks(222)}
    http = SimpleNamespace(
        iter_sse_chunks=lambda _url, json_body, **_k: iter(by_model[json_body["model"]])
    )
    client._http_for_route = lambda _route: http
    client._record_stream_usage = lambda *a, **k: None
    return client


def _collect(client: Client, model: str) -> None:
    route = SimpleNamespace(kind="platform")
    list(client._collect_sse_deltas("u", route, {"model": model}, {}, []))


def test_stream_usage_stash_is_per_thread() -> None:
    """A finishes its stream, B finishes its own, then A reads: A's usage."""
    client = _bare_client()
    a_done = threading.Event()
    b_done = threading.Event()
    seen: dict[str, Any] = {}

    def _agent_a() -> None:
        _collect(client, "model-a")
        a_done.set()
        b_done.wait(5)
        seen["a"] = client._last_stream_usage

    def _agent_b() -> None:
        a_done.wait(5)
        _collect(client, "model-b")
        seen["b"] = client._last_stream_usage
        b_done.set()

    threads = [threading.Thread(target=_agent_a), threading.Thread(target=_agent_b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert seen["a"]["prompt_tokens"] == 111 and seen["a"]["model"] == "model-a"
    assert seen["b"]["prompt_tokens"] == 222 and seen["b"]["model"] == "model-b"


def test_consume_on_read_in_one_thread_leaves_another_threads_stash() -> None:
    client = _bare_client()
    _collect(client, "model-a")
    other: dict[str, Any] = {}

    def _other_agent() -> None:
        other["before"] = client._last_stream_usage
        client._last_stream_usage = None  # the companion consumes on read

    thread = threading.Thread(target=_other_agent)
    thread.start()
    thread.join(5)

    assert other["before"] is None  # nothing streamed on that thread
    assert client._last_stream_usage["prompt_tokens"] == 111


def test_finish_reason_is_per_thread() -> None:
    client = _bare_client()
    client._last_stream_finish = "length"
    other: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: other.update(finish=client._last_stream_finish))
    thread.start()
    thread.join(5)
    assert other["finish"] is None
    assert client._last_stream_finish == "length"


# --- preflight_model_route (ADR-062 at submit time) ---------------------------------


def _config(**over: Any) -> SimpleNamespace:
    cfg = SimpleNamespace(
        model="test-model",
        gateway_url="https://gw.example",
        api_key="jwt",
        endpoint_url=None,
        endpoint_provider=None,
        model_routes={},
    )
    for key, value in over.items():
        setattr(cfg, key, value)
    return cfg


def test_preflight_model_route_propagates_the_gate_refusal(monkeypatch) -> None:
    monkeypatch.setattr(Client, "_project_data_class", lambda self: "sensitive")
    client = Client(config=_config(endpoint_url="https://api.public.example/v1"))
    with pytest.raises(R4Error) as exc:
        client.preflight_model_route("gpt-x")
    assert "ADR-062" in str(exc.value) and "api.public.example" in str(exc.value)


def test_preflight_model_route_passes_allowed_routes(monkeypatch) -> None:
    monkeypatch.setattr(Client, "_project_data_class", lambda self: "sensitive")
    Client(config=_config()).preflight_model_route("test-model")  # platform route
    monkeypatch.setattr(Client, "_project_data_class", lambda self: "non_sensitive")
    public = Client(config=_config(endpoint_url="https://api.public.example/v1"))
    assert public.preflight_model_route(None) is None  # the configured default
