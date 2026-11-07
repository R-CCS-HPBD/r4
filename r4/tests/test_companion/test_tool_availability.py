"""Tool-availability gate tests (round-2 finding D, 2026-09-05).

The gate drops tools the session cannot serve before the model sees the
offered set; every removal must land in the ADR-037 record under
``filtered_out["unavailable"]`` and show up in the roll-up line.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from r4.companion.tool_availability import (
    UNAVAILABLE_REASON,
    filter_unavailable_tools,
    unavailable_reason,
)


def _tool(name: str) -> dict[str, Any]:
    return {"name": name, "description": f"{name} desc", "parameters": {"type": "object"}}


def _client(use_umrs_transport: bool, *, api_key: str | None = "platform-key") -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(
            use_umrs_transport=use_umrs_transport,
            umrs_server_host="127.0.0.1",
            umrs_control_port=5555,
            umrs_tunnel_mode=False,
            api_key=api_key,
            gateway_url="http://gw.example:8080",
        )
    )


@pytest.fixture(autouse=True)
def _fresh_probe_caches():
    from r4.companion.tool_availability import _CONNECT_PROBE_CACHE, _PLATFORM_LIVE_CACHE

    _CONNECT_PROBE_CACHE.clear()
    _PLATFORM_LIVE_CACHE.clear()
    yield
    _CONNECT_PROBE_CACHE.clear()
    _PLATFORM_LIVE_CACHE.clear()


@pytest.fixture(autouse=True)
def _no_search_backend(monkeypatch):
    monkeypatch.delenv("R4_WEB_SEARCH_URL", raising=False)


def test_unwired_callbacks_are_unavailable() -> None:
    # tool_search needs a session to wire its catalog callback; web_search a
    # callback or a configured SearXNG instance (R4_WEB_SEARCH_URL).
    assert unavailable_reason(_tool("tool_search"), None) is not None
    assert unavailable_reason(_tool("web_search"), None) is not None


def test_wired_callbacks_stay_available() -> None:
    from r4.agent import tools as agent_tools

    agent_tools.set_tool_search_callback(lambda query: [])
    agent_tools.set_web_search_callback(lambda query, n: [])
    try:
        assert unavailable_reason(_tool("tool_search"), None) is None
        assert unavailable_reason(_tool("web_search"), None) is None
    finally:
        agent_tools.set_tool_search_callback(None)  # type: ignore[arg-type]
        agent_tools.set_web_search_callback(None)  # type: ignore[arg-type]


def test_file_list_platform_always_unavailable() -> None:
    # DataClient.list_files unconditionally raises the retired-route error.
    assert unavailable_reason(_tool("file_list_platform"), _client(True)) is not None


def test_file_upload_gated_on_transport_config() -> None:
    # No ZMQ transport configured → executor can only hit the retired HTTP
    # route, so the tool must not be offered.
    assert unavailable_reason(_tool("file_upload_platform"), _client(False)) is not None
    assert unavailable_reason(_tool("file_upload_platform"), None) is not None


def test_file_upload_available_with_transport(monkeypatch) -> None:
    try:
        import r4.umrs_transport  # noqa: F401
    except ImportError:
        return  # pyzmq missing → gate would remove it anyway (allowed drift)
    monkeypatch.setattr("r4.companion.tool_availability._connect_probe", lambda host, port: True)
    assert unavailable_reason(_tool("file_upload_platform"), _client(True)) is None


def test_file_upload_filtered_when_probe_fails(monkeypatch) -> None:
    """Round-5 task 2: an endpoint that doesn't answer the pack probe drops
    the tool — the validator doesn't care which side was wrong last time."""
    monkeypatch.setattr("r4.companion.tool_availability._connect_probe", lambda host, port: False)
    reason = unavailable_reason(_tool("file_upload_platform"), _client(True))
    assert reason is not None
    assert "no ZMQ data transport" in reason


def test_connect_probe_caches_by_host_port(monkeypatch) -> None:
    """Per-process caching: a probe fires once per (host, port), not per scan."""
    import socket

    from r4.companion.tool_availability import _CONNECT_PROBE_CACHE, _connect_probe

    _CONNECT_PROBE_CACHE.clear()
    calls = {"n": 0}

    class _FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_create_connection(addr, timeout=None):
        calls["n"] += 1
        return _FakeSocket()

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    assert _connect_probe("10.0.0.1", 5555) is True
    assert _connect_probe("10.0.0.1", 5555) is True
    assert calls["n"] == 1

    # Negative results cache too
    calls["n"] = 0

    def fake_fails(addr, timeout=None):
        calls["n"] += 1
        raise OSError("refused")

    monkeypatch.setattr(socket, "create_connection", fake_fails)
    assert _connect_probe("10.0.0.2", 5555) is False
    assert _connect_probe("10.0.0.2", 5555) is False
    assert calls["n"] == 1


def test_filter_splits_and_reports_bucket() -> None:
    tools = [_tool(n) for n in ("file_read", "tool_search", "file_list_platform")]
    kept, bucket = filter_unavailable_tools(tools, _client(True))
    assert [t["name"] for t in kept] == ["file_read"]
    assert bucket is not None
    assert bucket["count"] == 2
    assert set(bucket["sample"]) == {"tool_search", "file_list_platform"}


def test_filter_noop_when_all_live(monkeypatch) -> None:
    monkeypatch.setattr("r4.companion.tool_availability._connect_probe", lambda host, port: True)
    tools = [_tool("search_docs"), _tool("job_list")]
    kept, bucket = filter_unavailable_tools(tools, _client(True))
    assert kept == tools
    assert bucket is None


def test_selection_record_and_rollup_disclose_unavailable() -> None:
    """send() must remove unservable tools from the offered set and record
    them — the roll-up line appends '· N unavailable filtered'."""
    from r4.companion.agent import ResearchCompanion
    from r4.companion.context import ContextManager
    from r4.companion.permissions import PermissionManager, PermissionMode
    from r4.companion.skills import SkillRegistry
    from r4.companion.tool_selection import selection_summary_line

    mock_client = MagicMock()
    mock_client._config.model = "test-model"
    mock_client._stream_request_collect.side_effect = lambda payload, acc, **kw: iter(
        ["answer without tools"]
    )

    agent = ResearchCompanion(
        mock_client,
        context_manager=ContextManager(client=mock_client),
        skill_registry=SkillRegistry(),
        permission_manager=PermissionManager(mode=PermissionMode.TRUST),
    )
    offered = [
        _tool("file_read"),
        _tool("tool_search"),
        _tool("web_search"),
        _tool("file_list_platform"),
    ]
    response = agent.send("hi", tools=offered, stream=True)
    assert response == "answer without tools"

    ctx = agent._selection_ctx
    assert "tool_search" not in ctx.offered_names
    assert "web_search" not in ctx.offered_names
    assert "file_list_platform" not in ctx.offered_names
    assert "file_read" in ctx.offered_names

    record = ctx.step_record(step_index=0, turn_id="t1")
    bucket = record["filtered_out"][UNAVAILABLE_REASON]
    assert bucket["count"] == 3

    line = selection_summary_line(record)
    assert "1 of 4 tools offered" in line
    assert "3 unavailable filtered" in line


def test_rollup_line_clean_when_nothing_filtered() -> None:
    from r4.companion.tool_selection import selection_summary_line

    record = {
        "available_tools": 65,
        "candidate_tools": 65,
        "strategy": "passthrough",
        "filtered_out": {},
    }
    assert selection_summary_line(record) == "all 65 tools offered (no selection)"


def test_rag_upload_maps_5xx_to_typed_backend_unavailable() -> None:
    """F.2: a bare 500 from /gw/rag/upload must surface as a typed, agent-
    readable error — embedding backend unavailable, nothing queued."""
    from r4.companion.tools import _rag_tool_error_message
    from r4.errors import APIError

    message = _rag_tool_error_message(
        "uploading document", APIError("API error: 500", status_code=500)
    )
    assert message.startswith("[embedding_backend_unavailable]")
    assert "NOT queued" in message
    assert "500" in message

    # Non-5xx stays a plain error (e.g. 401 auth problem).
    plain = _rag_tool_error_message(
        "uploading document", APIError("API error: 401", status_code=401)
    )
    assert "embedding_backend_unavailable" not in plain
    assert "401" in plain


class TestPlatformGate:
    """W3 (2026-10-05): platform-backed tools need credentials + a reachable gateway."""

    def test_no_credentials_drops_platform_tools_keeps_local(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "r4.companion.tool_availability._connect_probe", lambda host, port: True
        )
        client = _client(False, api_key=None)
        for name in (
            "platform_health",
            "endpoint_list",
            "job_list",
            "list_models",
            "pool_up",
            "rag_query",
            "search_docs",
            "lab_file_read",
            "model_list_registry",
            "model_stage",
            "workflow_run",
            "workflow_list",
            "workflow_approve_gate",
        ):
            assert "no live platform" in (unavailable_reason(_tool(name), client) or ""), name
        for name in (
            "source_list",
            "shell_exec",
            "file_read",
            "platform_guide",
            "teamup_list",
            "project_list",
        ):
            assert unavailable_reason(_tool(name), client) is None, name

    def test_unreachable_gateway_drops_platform_tools(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "r4.companion.tool_availability._connect_probe", lambda host, port: False
        )
        assert "no live platform" in unavailable_reason(_tool("endpoint_status"), _client(False))

    def test_liveness_is_rechecked_after_ttl(self, monkeypatch) -> None:
        import r4.companion.tool_availability as ta

        answers = iter([False, True])
        monkeypatch.setattr(ta, "_connect_probe", lambda host, port: next(answers))
        client = _client(False)
        assert ta.platform_live(client) is False
        assert ta.platform_live(client) is False  # cached within the TTL
        monkeypatch.setattr(ta, "_PLATFORM_LIVE_TTL_SEC", 0.0)
        assert ta.platform_live(client) is True

    def test_explicit_flag_marks_a_platform_tool(self) -> None:
        from r4.companion.tool_availability import is_platform_tool

        assert is_platform_tool({"name": "anything", "requires_platform": True})
        assert not is_platform_tool({"name": "file_read"})

    def test_platform_down_hides_knowledge_build_from_context(self) -> None:
        """ADR-108 W1: it writes through client.knowledge, so it was offered
        and failed only at call time while the platform was down."""
        from r4.companion.tool_availability import is_platform_tool

        assert is_platform_tool({"name": "knowledge_build_from_context"})
        # A local runtime runs loops with no platform; the local registry too.
        assert not is_platform_tool({"name": "research_loop_start"})
        assert not is_platform_tool({"name": "project_ensure"})
