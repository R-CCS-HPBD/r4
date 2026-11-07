"""The window an Ollama server serves, not the name table's (2026-10-30).

R4 budgeted llama3.2:3b at its trained 131,072 tokens while the stand-alone
Ollama serves 32,768 (``OLLAMA_CONTEXT_LENGTH``), so compaction could start
only after Ollama had already truncated the prompt. No network: every Ollama
reply is an ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import r4.companion.ollama_window as ow
from r4.companion.context_budget import (
    effective_context_budget,
    endpoint_model_limits,
    resolve_model_context_limit,
    sync_session_context_budget,
)
from r4.tui.commands import _cmd_context

MODEL = "llama3.2:3b"


def _client(url: str = "http://localhost:11434/v1", provider: str | None = "ollama", **extra):
    config = SimpleNamespace(
        endpoint_url=url,
        endpoint_provider=provider,
        endpoint_api_key=None,
        model_routes={},
        model=MODEL,
        **extra,
    )

    def no_listing(include_stopped: bool = False):
        raise RuntimeError("a public source has no platform endpoints")

    entries = [{"id": MODEL, "object": "model", "owned_by": "library"}]
    return SimpleNamespace(config=config, list_endpoints=no_listing, model_entries=lambda: entries)


def _ollama(monkeypatch, *, ps=None, show=None) -> list[str]:
    """Serve ``/api/tags``, ``/api/ps`` and ``/api/show``; returns the paths asked for."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": MODEL}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": ps or []})
        if request.url.path == "/api/show":
            assert json.loads(request.content)["model"] == MODEL
            return httpx.Response(200, json=show or {})
        return httpx.Response(404, json={})

    monkeypatch.setattr(ow, "_TRANSPORT", httpx.MockTransport(handle))
    return seen


def _trained(window: int, parameters: str = "") -> dict:
    info = {"general.architecture": "llama", "llama.context_length": window}
    return {"model_info": info, "parameters": parameters}


@pytest.fixture(autouse=True)
def _no_pin(monkeypatch):
    monkeypatch.delenv("R4_MODEL_CONTEXT_WINDOW", raising=False)


def test_loaded_model_budgets_from_the_served_window(monkeypatch) -> None:
    """The reported case: old code budgeted 131,072 (the name table)."""
    _ollama(monkeypatch, ps=[{"name": MODEL, "context_length": 32_768}], show=_trained(131_072))
    client = _client()
    assert resolve_model_context_limit(client, MODEL) == effective_context_budget(32_768)
    assert endpoint_model_limits(client, MODEL)["context_window"] == 32_768


def test_unloaded_model_falls_back_to_the_stack_default(monkeypatch) -> None:
    _ollama(monkeypatch, show=_trained(131_072))
    assert endpoint_model_limits(_client(), MODEL)["context_window"] == ow.OLLAMA_SERVED_DEFAULT


def test_num_ctx_parameter_is_the_served_window(monkeypatch) -> None:
    _ollama(monkeypatch, show=_trained(131_072, "stop <|eot_id|>\nnum_ctx 8192"))
    assert endpoint_model_limits(_client(), MODEL)["context_window"] == 8_192


def test_trained_window_caps_the_served_one(monkeypatch) -> None:
    _ollama(monkeypatch, ps=[{"name": MODEL, "context_length": 32_768}], show=_trained(8_192))
    assert endpoint_model_limits(_client(), MODEL)["context_window"] == 8_192


def test_unreachable_ollama_still_never_uses_the_trained_window(monkeypatch) -> None:
    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(ow, "_TRANSPORT", httpx.MockTransport(down))
    assert endpoint_model_limits(_client(), MODEL)["context_window"] == 32_768


def test_port_11434_marks_an_ollama_route_without_a_provider(monkeypatch) -> None:
    _ollama(monkeypatch, ps=[{"name": MODEL, "context_length": 16_384}])
    client = _client("http://llm.example:11434", provider=None)
    assert endpoint_model_limits(client, MODEL)["context_window"] == 16_384


def test_other_public_routes_are_not_probed(monkeypatch) -> None:
    seen = _ollama(monkeypatch)
    client = _client("http://vllm.example:8000/v1", provider=None)
    assert endpoint_model_limits(client, MODEL)["context_window"] is None
    assert seen == []


def test_window_is_remembered(monkeypatch) -> None:
    seen = _ollama(monkeypatch, ps=[{"name": MODEL, "context_length": 32_768}])
    client = _client()
    endpoint_model_limits(client, MODEL)
    endpoint_model_limits(client, MODEL)
    assert seen.count("/api/ps") == 1


def test_env_pin_wins_over_the_server(monkeypatch) -> None:
    seen = _ollama(monkeypatch, ps=[{"name": MODEL, "context_length": 32_768}])
    monkeypatch.setenv("R4_MODEL_CONTEXT_WINDOW", "16384")
    assert resolve_model_context_limit(_client(), MODEL) == effective_context_budget(16_384)
    assert seen == []


def _session(client) -> SimpleNamespace:
    agent = SimpleNamespace(model=MODEL)
    context = SimpleNamespace(max_context_tokens=0, compaction_threshold=0.8)
    return SimpleNamespace(client=client, agent=agent, context=context)


def test_sync_hands_the_served_window_to_agent_and_context(monkeypatch) -> None:
    _ollama(monkeypatch, ps=[{"name": MODEL, "context_length": 32_768}], show=_trained(131_072))
    monkeypatch.setattr(
        "r4.companion.model_resolution.session_model_info", lambda _s: {"model": MODEL}
    )
    session = _session(_client())
    sync_session_context_budget(session)
    assert session.agent.model_context_window == 32_768
    assert session.context.model_context_window == 32_768
    assert session.context.max_context_tokens == effective_context_budget(32_768)


def test_context_command_shows_the_effective_window() -> None:
    lines: list[str] = []
    record = lines.append
    renderer = SimpleNamespace(system=record, info=record)
    turns_session = SimpleNamespace(
        total_prompt_tokens=10, total_completion_tokens=5, turns=[], messages=[]
    )
    context = SimpleNamespace(
        max_context_tokens=effective_context_budget(32_768),
        model_context_window=32_768,
        context_window_basis="endpoint",
        compaction_threshold=0.8,
    )
    app = SimpleNamespace(session=turns_session, renderer=renderer, context_manager=context)
    _cmd_context(app, [])
    assert "  context window:   32,768 tokens (endpoint)" in lines
    assert "  context budget:   25,395 tokens, compacts at 80%" in lines


def _not_ollama(monkeypatch) -> list[str]:
    """A LiteLLM-style server on :11434: every Ollama path is a 404."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(404, json={"error": "not found"})

    monkeypatch.setattr(ow, "_TRANSPORT", httpx.MockTransport(handle))
    return seen


def test_port_11434_server_that_is_not_ollama_keeps_the_name_table(monkeypatch) -> None:
    """Review finding 1: the R-CCS LiteLLM proxy listens on :11434 too; old code
    capped its models at the 32,768 fallback."""
    seen = _not_ollama(monkeypatch)
    client = _client("http://llm.ai.r-ccs.riken.jp:11434", provider=None)
    assert endpoint_model_limits(client, MODEL)["context_window"] is None
    assert resolve_model_context_limit(client, MODEL) == effective_context_budget(131_072)
    endpoint_model_limits(client, MODEL)
    assert seen == ["/api/tags"]  # never asked /api/show or /api/ps; the answer is kept


def _synced(monkeypatch, client) -> SimpleNamespace:
    monkeypatch.setattr(
        "r4.companion.model_resolution.session_model_info", lambda _s: {"model": MODEL}
    )
    session = _session(client)
    sync_session_context_budget(session)
    return session


def test_fallback_window_is_labelled_a_default_not_the_endpoint(monkeypatch) -> None:
    _ollama(monkeypatch, show=_trained(131_072))
    session = _synced(monkeypatch, _client())
    assert session.context.model_context_window == ow.OLLAMA_SERVED_DEFAULT
    assert session.context.context_window_basis == ow.DEFAULT_BASIS


def test_reported_window_is_labelled_the_endpoint(monkeypatch) -> None:
    _ollama(monkeypatch, ps=[{"name": MODEL, "context_length": 16_384}])
    session = _synced(monkeypatch, _client())
    assert session.context.context_window_basis == "endpoint"


def test_probe_honours_verify_ssl(monkeypatch) -> None:
    """Review finding 3: old code always verified, so a self-signed https
    Ollama with verify_ssl off fell back to 32,768 every time."""
    _ollama(monkeypatch, ps=[{"name": MODEL, "context_length": 16_384}])
    verifies: list[object] = []
    real_client = httpx.Client

    def spy(*args, **kwargs):
        verifies.append(kwargs.get("verify", True))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(ow.httpx, "Client", spy)
    client = _client("https://ollama.example:11434", verify_ssl=False)
    assert endpoint_model_limits(client, MODEL)["context_window"] == 16_384
    assert verifies and all(v is False for v in verifies)


def _companion(client):
    from r4.companion.agent import ResearchCompanion

    return ResearchCompanion(client, model=MODEL)


def test_unsynced_companion_budgets_from_the_served_window(monkeypatch) -> None:
    """Review finding 2: a team role or sub-agent no session synced used the
    name table's 131,072 against an Ollama server serving 32,768."""
    _ollama(monkeypatch, ps=[{"name": MODEL, "context_length": 32_768}])
    assert _companion(_client())._context_window() == 32_768


def test_sub_agent_inherits_the_parent_synced_limits(monkeypatch) -> None:
    _not_ollama(monkeypatch)
    parent = _companion(_client("http://vllm.example:8000/v1", provider=None))
    parent.model_context_window, parent.model_output_limit = 32_768, 8_192
    sub = parent._new_subagent()
    assert sub._context_window() == 32_768
    assert sub.model_output_limit == 8_192
    assert getattr(parent._new_subagent("other-model"), "model_context_window", None) is None
