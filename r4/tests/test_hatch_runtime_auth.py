"""Hatch runtime: reachable from a containerised UMRS, never open to the LAN.

A Hatch runtime used to listen on, and advertise, 127.0.0.1 only. With UMRS in
a container (the stand-alone deployment) that URL is the container itself:
registration succeeded and every tool call failed. Listen and advertise hosts
are now configurable; any non-loopback listen gets a per-runtime bearer token
that the runtime requires on every request and registers with UMRS.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.integrations import hatchling
from r4.integrations.hatch_runtime import HatchBridgeHandler, HatchBridgeHTTPServer


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("R4_HATCH_BIND_HOST", raising=False)
    monkeypatch.delenv("R4_HATCH_ADVERTISE_HOST", raising=False)


def test_defaults_are_unchanged_loopback(monkeypatch):
    assert hatchling._hatch_bind_host() == "127.0.0.1"
    assert hatchling._hatch_advertise_host("127.0.0.1") == "127.0.0.1"
    assert hatchling._is_loopback_host("127.0.0.1")


def test_wildcard_bind_advertises_explicit_host(monkeypatch):
    monkeypatch.setenv("R4_HATCH_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("R4_HATCH_ADVERTISE_HOST", "host.containers.internal")
    bind = hatchling._hatch_bind_host()
    assert bind == "0.0.0.0"
    assert hatchling._hatch_advertise_host(bind) == "host.containers.internal"
    assert not hatchling._is_loopback_host(bind)


def test_wildcard_bind_without_advertise_falls_back_to_loopback():
    assert hatchling._hatch_advertise_host("0.0.0.0") == "127.0.0.1"


class _FakeBridge:
    def list_tools(self):
        return [{"name": "echo", "description": "echo", "inputSchema": {"type": "object"}}]


def _serve(token):
    httpd = HatchBridgeHTTPServer(
        ("127.0.0.1", 0),
        HatchBridgeHandler,
        package_name="demo",
        bridge_server=_FakeBridge(),
        token=token,
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _get(url, token=None):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_runtime_requires_its_token_on_every_request():
    httpd, base = _serve("tok-123")
    try:
        assert _get(f"{base}/health") == 401
        assert _get(f"{base}/tools", token="wrong") == 401
        assert _get(f"{base}/health", token="tok-123") == 200
        assert _get(f"{base}/tools", token="tok-123") == 200
        post = urllib.request.Request(f"{base}/tools/call", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(post, timeout=5)
        assert exc.value.code == 401
    finally:
        httpd.shutdown()


def test_runtime_without_token_stays_open_on_loopback():
    httpd, base = _serve(None)
    try:
        assert _get(f"{base}/health") == 200
    finally:
        httpd.shutdown()


def _bridge(tmp_path):
    client = SimpleNamespace(mcp=MagicMock(), config=SimpleNamespace(verify_ssl=False))
    return hatchling.HatchlingBridge(client, packages_dir=tmp_path / "packages"), client


def test_registration_carries_the_token(tmp_path):
    bridge, client = _bridge(tmp_path)
    asyncio.run(bridge._register_with_umrs("demo", "http://h:1", [], token="tok-9"))
    kwargs = client.mcp.register_server.call_args.kwargs
    assert kwargs["auth_type"] == "api_key"
    assert kwargs["auth_config"] == {"token": "tok-9"}


def test_registration_without_token_is_unauthenticated(tmp_path):
    bridge, client = _bridge(tmp_path)
    asyncio.run(bridge._register_with_umrs("demo", "http://127.0.0.1:1", []))
    kwargs = client.mcp.register_server.call_args.kwargs
    assert kwargs["auth_type"] == "none"
    assert kwargs["auth_config"] is None


def test_runtime_state_with_token_is_private(tmp_path):
    bridge, _ = _bridge(tmp_path)
    bridge._save_runtime_state("demo", {"url": "http://h:1", "token": "tok"})
    path = bridge._runtime_state_path("demo")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text())["token"] == "tok"


def test_runtime_gets_the_connecting_clients_gateway():
    client = SimpleNamespace(
        config=SimpleNamespace(gateway_url="http://localhost:8080/", api_key="jwt-1")
    )
    assert hatchling._runtime_gateway_env(client) == {
        "R4_GATEWAY_URL": "http://localhost:8080",
        "R4_GATEWAY_TOKEN": "jwt-1",
    }


def test_runtime_gateway_env_empty_without_credentials():
    client = SimpleNamespace(config=SimpleNamespace(gateway_url="http://x", api_key=None))
    assert hatchling._runtime_gateway_env(client) == {}
