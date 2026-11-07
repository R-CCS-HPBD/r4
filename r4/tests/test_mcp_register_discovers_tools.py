"""`r4 mcp register` registers a server together with the tools it serves.

Defect 5 of defect-wave-2026-10-08: the documented bring-your-own-server recipe
registered servers with an empty tool list, and nothing ever discovered them,
so the agent saw a server it could not call. ``register_server`` promised
"tools … will be discovered" and did not. Discovery runs here, in the client
that registers, rather than in UMRS: server-side discovery would make UMRS dial
a caller-supplied URL (an SSRF review the defect deferred).
"""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import httpx
import pytest

from r4 import mcp as mcp_mod

_TOOLS = [{"name": "fetch", "description": "Fetch a URL"}, "not-a-tool"]


def _respond(payload, status=200):
    def get(url, headers=None, timeout=None):
        get.calls.append((url, headers))
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    get.calls = []
    return get


def _client():
    posted = {}

    def post(url, json_body=None, timeout=None):
        posted.update(url=url, body=json_body)
        return {"server_id": "srv-1"}

    parent = SimpleNamespace(
        http=SimpleNamespace(post=post), config=SimpleNamespace(gateway_url="http://gw")
    )
    client = mcp_mod.MCPClient.__new__(mcp_mod.MCPClient)
    client._parent = parent
    return client, posted


def test_discover_tools_reads_the_bridge_listing(monkeypatch):
    get = _respond({"tools": _TOOLS})
    monkeypatch.setattr(mcp_mod.httpx, "get", get)
    assert mcp_mod.discover_tools("http://localhost:8765/") == [_TOOLS[0]]
    assert get.calls[0][0] == "http://localhost:8765/tools"


def test_discover_tools_sends_the_credential_the_proxy_will_send(monkeypatch):
    get = _respond([_TOOLS[0]])
    monkeypatch.setattr(mcp_mod.httpx, "get", get)
    mcp_mod.discover_tools("http://h", {"token": "s3cret"})
    mcp_mod.discover_tools("http://h", {"token": "s3cret", "header": "X-Api-Key"})
    assert get.calls[0][1] == {"Authorization": "Bearer s3cret"}
    assert get.calls[1][1] == {"X-Api-Key": "s3cret"}


@pytest.mark.parametrize(
    "get",
    [
        _respond({"detail": "nope"}, status=404),
        _respond("not a listing"),
    ],
)
def test_a_server_that_lists_nothing_is_empty_not_an_error(monkeypatch, get):
    monkeypatch.setattr(mcp_mod.httpx, "get", get)
    assert mcp_mod.discover_tools("http://h") == []


def test_unreachable_or_non_http_server_is_empty(monkeypatch):
    def refuse(*_a, **_k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(mcp_mod.httpx, "get", refuse)
    assert mcp_mod.discover_tools("http://h") == []
    assert mcp_mod.discover_tools("stdio://npx fetch") == []


def test_register_without_tools_discovers_them(monkeypatch):
    monkeypatch.setattr(mcp_mod.httpx, "get", _respond({"tools": _TOOLS}))
    client, posted = _client()
    client.register_server(name="fetch", url="http://localhost:8765")
    assert posted["body"]["tools"] == [_TOOLS[0]]


def test_explicit_tools_are_not_rediscovered(monkeypatch):
    """Hatchling registers the tools it already read from the runtime."""
    get = _respond({"tools": _TOOLS})
    monkeypatch.setattr(mcp_mod.httpx, "get", get)
    client, posted = _client()
    client.register_server(name="h", url="http://h", tools=[])
    assert get.calls == [] and posted["body"]["tools"] == []


def test_cli_reports_the_tool_count_and_warns_at_zero(monkeypatch, capsys):
    from r4._cli.mcp import _cmd_mcp_register

    calls = {}
    client = SimpleNamespace(
        mcp=SimpleNamespace(register_server=lambda **kw: calls.update(kw) or {"server_id": "srv-1"})
    )
    args = Namespace(
        name="fetch",
        url="http://localhost:8765",
        description="",
        auth_type="bearer",
        auth_token="t",
        as_json=False,
    )
    monkeypatch.setattr(mcp_mod.httpx, "get", _respond({"tools": [_TOOLS[0]]}))
    _cmd_mcp_register(args, client)
    out = capsys.readouterr().out
    assert calls["tools"] == [_TOOLS[0]] and "Tools: 1 (fetch)" in out

    monkeypatch.setattr(mcp_mod.httpx, "get", _respond({"tools": []}))
    _cmd_mcp_register(args, client)
    out = capsys.readouterr().out
    assert "Tools: 0" in out and "cannot call it" in out
