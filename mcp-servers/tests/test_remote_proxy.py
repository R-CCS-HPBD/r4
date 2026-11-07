"""Pins for the remote-MCP → UMRS-dialect bridge (runners/remote_proxy.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from r4_mcp.runners import remote_proxy

# ---------------------------------------------------------------------------
# Fake upstream: streamable-HTTP MCP server with sessions + SSE replies
# ---------------------------------------------------------------------------


class _FakeUpstream(BaseHTTPRequestHandler):
    init_count = 0
    session = "s-1"
    fail_next_call_with_404 = False

    def log_message(self, *_args):  # silence
        return

    def _sse_reply(self, payload, session=None):
        envelope = json.dumps({"jsonrpc": "2.0", "id": 2, "result": payload})
        body = f"event: message\ndata: {envelope}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        if session:
            self.send_header("MCP-Session-Id", session)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_message(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _session_ok(self):
        return self.headers.get("MCP-Session-Id") == type(self).session

    def do_POST(self):  # noqa: N802
        message = self._read_message()
        method = message.get("method")
        if method == "initialize":
            type(self).init_count += 1
            type(self).session = f"s-{type(self).init_count}"
            init_result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "serverInfo": {"name": "fake", "version": "0"},
            }
            self._sse_reply(init_result, session=type(self).session)
        elif method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif method == "tools/list":
            if not self._session_ok():
                self.send_response(404)
                self.end_headers()
                return
            tool = {"name": "fake_tool", "description": "echo", "inputSchema": {"type": "object"}}
            self._sse_reply({"tools": [tool]})
        elif method == "tools/call":
            if not self._session_ok() or type(self).fail_next_call_with_404:
                type(self).fail_next_call_with_404 = False
                self.send_response(404)
                self.end_headers()
                return
            params = message.get("params", {})
            content = [{"type": "text", "text": json.dumps(params)}]
            self._sse_reply({"content": content, "isError": False})
        else:
            self.send_response(400)
            self.end_headers()


@pytest.fixture()
def upstream():
    _FakeUpstream.init_count = 0
    _FakeUpstream.session = "s-1"
    _FakeUpstream.fail_next_call_with_404 = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    server.shutdown()


@pytest.fixture()
def proxy(upstream):
    spec = remote_proxy.UpstreamSpec(name="fake", url=upstream, timeout=5.0)
    state = remote_proxy._ProxyState([spec], tools_ttl=0.0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), remote_proxy._make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def _post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_load_specs_http_only_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("RP_TEST_TOKEN", "abc123")
    config = tmp_path / "mcp-servers.json"
    config.write_text(
        """
        {
          // comment line must not break URLs below
          "mcpServers": {
            "context7": {"type": "http", "url": "https://mcp.context7.com/mcp"},
            "local-stdio": {"type": "stdio", "command": "npx", "args": ["x"]},
            "authed": {
              "type": "http",
              "url": "https://example.test/mcp",
              "headers": {"Authorization": "Bearer ${RP_TEST_TOKEN}", "X-Drop": "${RP_UNSET_VAR}"}
            }
          }
        }
        """
    )
    specs = {s.name: s for s in remote_proxy.load_upstream_specs(str(config), timeout=7.0)}
    assert set(specs) == {"context7", "authed", "local-stdio"}
    assert specs["authed"].headers == {"Authorization": "Bearer abc123"}
    assert specs["authed"].timeout == 7.0
    assert specs["context7"].kind == "http"
    assert specs["local-stdio"].kind == "stdio"
    assert specs["local-stdio"].command == ["npx", "x"]


def test_strip_jsonc_preserves_https_urls():
    text = '{"url": "https://example.test/mcp"} // trailing comment'
    parsed = json.loads(remote_proxy._strip_jsonc(text))
    assert parsed["url"] == "https://example.test/mcp"


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


def test_sse_payloads_and_pick():
    first = 'event: message\ndata: {"jsonrpc": "2.0", "id": 1}\n\n'
    second = 'event: message\ndata: {"jsonrpc": "2.0", "id": 2, "result": {"ok": true}}\n\n'
    body = first + second
    picked = remote_proxy._pick_jsonrpc_response(body, "text/event-stream")
    assert picked["result"] == {"ok": True}
    plain = remote_proxy._pick_jsonrpc_response('{"result": {"x": 1}}', "application/json")
    assert plain["result"] == {"x": 1}


# ---------------------------------------------------------------------------
# Dialect surface
# ---------------------------------------------------------------------------


def test_root_lists_upstreams(proxy):
    status, body = _get(f"{proxy}/")
    assert status == 200
    assert body["upstreams"] == ["fake"]


def test_get_tools_aggregates_after_handshake(proxy, upstream):
    status, body = _get(f"{proxy}/fake/tools")
    assert status == 200
    assert [t["name"] for t in body["tools"]] == ["fake_tool"]
    assert _FakeUpstream.init_count == 1  # one initialize handshake


def test_tool_call_jsonrpc_envelope_roundtrip(proxy):
    envelope = {
        "jsonrpc": "2.0",
        "id": 42,
        "method": "tools/call",
        "params": {"name": "fake_tool", "arguments": {"q": "hello"}},
    }
    status, body = _post(f"{proxy}/fake/tools/call", envelope)
    assert status == 200
    assert body["id"] == 42
    assert body["result"]["isError"] is False
    echoed = json.loads(body["result"]["content"][0]["text"])
    assert echoed == {"name": "fake_tool", "arguments": {"q": "hello"}}


def test_tool_call_refresh_once_on_session_expiry(proxy):
    _FakeUpstream.fail_next_call_with_404 = True
    envelope = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "fake_tool", "arguments": {}},
    }
    status, body = _post(f"{proxy}/fake/tools/call", envelope)
    assert status == 200
    assert body["result"]["isError"] is False
    assert _FakeUpstream.init_count >= 2  # re-handshook after 404


def test_tool_call_bad_arguments_is_error_result(proxy):
    envelope = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "fake_tool", "arguments": "not-an-object"},
    }
    status, body = _post(f"{proxy}/fake/tools/call", envelope)
    assert status == 200
    assert body["result"]["isError"] is True


def test_unknown_upstream_404(proxy):
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(f"{proxy}/nope/tools")
    assert err.value.code == 404


# ---------------------------------------------------------------------------
# Stdio (local provider) upstreams
# ---------------------------------------------------------------------------

_FAKE_STDIO = str(Path(__file__).parent / "fixtures" / "fake_stdio_mcp.py")


def _stdio_spec(name="fake-stdio"):
    return remote_proxy.UpstreamSpec(
        name=name,
        command=[sys.executable, _FAKE_STDIO],
        env={},
        timeout=10.0,
    )


@pytest.fixture()
def stdio_proxy():
    state = remote_proxy._ProxyState([_stdio_spec()], tools_ttl=0.0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), remote_proxy._make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", state
    server.shutdown()
    state.close()


def test_load_specs_stdio_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("RP_CHILD_ENV", "child-value")
    config = tmp_path / "mcp-servers.json"
    config.write_text(
        """
        {"mcpServers": {
          "playwright": {"type": "stdio", "command": "npx",
                         "args": ["-y", "@playwright/mcp"],
                         "env": {"CHILD": "${RP_CHILD_ENV}"}},
          "bad-stdio": {"type": "stdio", "command": "", "args": []}
        }}
        """
    )
    specs = {s.name: s for s in remote_proxy.load_upstream_specs(str(config), timeout=3.0)}
    assert set(specs) == {"playwright"}
    assert specs["playwright"].kind == "stdio"
    assert specs["playwright"].command == ["npx", "-y", "@playwright/mcp"]
    assert specs["playwright"].env == {"CHILD": "child-value"}


def test_stdio_tools_end_to_end(stdio_proxy):
    base, _state = stdio_proxy
    status, body = _get(f"{base}/fake-stdio/tools")
    assert status == 200
    assert [t["name"] for t in body["tools"]] == ["stdio_echo"]


def test_stdio_tool_call_echo(stdio_proxy):
    base, _state = stdio_proxy
    envelope = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "stdio_echo", "arguments": {"q": "local"}},
    }
    status, body = _post(f"{base}/fake-stdio/tools/call", envelope)
    assert status == 200
    assert body["id"] == 9
    assert body["result"]["isError"] is False
    echoed = json.loads(body["result"]["content"][0]["text"])
    assert echoed["arguments"] == {"q": "local"}


def test_stdio_respawns_after_child_death(stdio_proxy):
    base, state = stdio_proxy
    client = state.clients["fake-stdio"]
    first = client.list_tools()
    assert [t["name"] for t in first] == ["stdio_echo"]
    proc = client._proc
    assert proc is not None
    proc.kill()
    proc.wait()
    second = client.list_tools()
    assert [t["name"] for t in second] == ["stdio_echo"]
    assert client._proc is not proc  # a fresh child was spawned


def test_load_specs_skips_disabled_entries(tmp_path):
    config = tmp_path / "mcp-servers.json"
    config.write_text(
        '{"mcpServers": {'
        ' "on": {"type": "http", "url": "https://on.test/mcp"},'
        ' "off": {"type": "http", "url": "https://off.test/mcp", "enabled": false}}}'
    )
    specs = {s.name for s in remote_proxy.load_upstream_specs(str(config), timeout=3.0)}
    assert specs == {"on"}


def test_stdio_health_route_reports_kind(stdio_proxy):
    base, _state = stdio_proxy
    status, body = _get(f"{base}/fake-stdio/health")
    assert status == 200
    assert body["kind"] == "stdio"
    assert "fake_stdio_mcp.py" in body["upstream"]


# ---------------------------------------------------------------------------
# Sandbox wrap (ADR-101 D-9): --stdio-wrap / --sandbox-digest
# ---------------------------------------------------------------------------

# A stand-in launcher: records its argv and cwd, then execs what follows "--".
_SHIM = """\
import json, os, sys
with open(sys.argv[1], "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[2:], "cwd": os.getcwd()}) + "\\n")
rest = sys.argv[sys.argv.index("--") + 1:]
os.execv(rest[0], rest)
"""


def _shim_wrap(tmp_path):
    shim = tmp_path / "shim.py"
    shim.write_text(_SHIM)
    record = tmp_path / "record.jsonl"
    return [sys.executable, str(shim), str(record)], record


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_load_specs_sandbox_false_is_exempt(tmp_path):
    config = tmp_path / "mcp-servers.json"
    config.write_text(
        '{"mcpServers": {'
        ' "free": {"type": "stdio", "command": "srv", "sandbox": false},'
        ' "boxed": {"type": "stdio", "command": "srv", "sandbox": {"web": "none"}},'
        ' "plain": {"type": "stdio", "command": "srv"}}}'
    )
    specs = {s.name: s for s in remote_proxy.load_upstream_specs(str(config), timeout=3.0)}
    assert specs["free"].sandbox_exempt is True
    assert specs["boxed"].sandbox_exempt is False
    assert specs["plain"].sandbox_exempt is False


def _tool_names(client):
    return [t["name"] for t in client.list_tools()]


def _spawn_kill_respawn(client):
    """List tools, kill the child, list again (respawn); the two children."""
    assert _tool_names(client) == ["stdio_echo"]
    first = client._proc
    first.kill()
    first.wait()
    assert _tool_names(client) == ["stdio_echo"]
    return first, client._proc


def test_stdio_child_is_wrapped_on_spawn_and_respawn(tmp_path):
    wrap, record = _shim_wrap(tmp_path)
    client = remote_proxy.StdioUpstreamClient(_stdio_spec(), wrap=wrap)
    try:
        first, second = _spawn_kill_respawn(client)
    finally:
        client.close()
    assert second is not first
    expected = {"argv": ["--name", "fake-stdio", "--", sys.executable, _FAKE_STDIO], "cwd": "/"}
    assert _records(record) == [expected, expected]  # the respawn went through the wrapper too


def test_exempt_stdio_child_is_not_wrapped(tmp_path):
    wrap, record = _shim_wrap(tmp_path)
    spec = _stdio_spec()
    spec.sandbox_exempt = True
    client = remote_proxy.StdioUpstreamClient(spec, wrap=wrap)
    try:
        assert client.sandboxed is False
        assert client.argv() == [sys.executable, _FAKE_STDIO]
        assert [t["name"] for t in client.list_tools()] == ["stdio_echo"]
    finally:
        client.close()
    assert not record.exists()


def test_unwrapped_spawn_is_unchanged(monkeypatch):
    seen = {}

    class _Stop(Exception):
        pass

    def fake_popen(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        raise _Stop

    monkeypatch.setattr(remote_proxy.subprocess, "Popen", fake_popen)
    client = remote_proxy.StdioUpstreamClient(_stdio_spec())
    with pytest.raises(_Stop):
        client._spawn()
    assert seen["argv"] == [sys.executable, _FAKE_STDIO]
    assert seen["cwd"] is None


@pytest.fixture()
def wrapped_proxy(tmp_path):
    wrap, _record = _shim_wrap(tmp_path)
    free = _stdio_spec("free")
    free.sandbox_exempt = True
    state = remote_proxy._ProxyState(
        [_stdio_spec(), free], tools_ttl=0.0, wrap=wrap, sandbox_digest="d1g3st"
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), remote_proxy._make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    state.close()


def test_root_reports_identity_and_sandbox_digest(wrapped_proxy):
    status, body = _get(f"{wrapped_proxy}/")
    assert status == 200
    assert body["service"] == remote_proxy.SERVICE_NAME == "r4-mcp-remote-proxy"
    assert body["pid"] == os.getpid()
    assert body["sandbox_digest"] == "d1g3st"
    assert body["servers"]["fake-stdio"] == {"kind": "stdio", "sandboxed": True, "exempt": False}
    assert body["servers"]["free"] == {"kind": "stdio", "sandboxed": False, "exempt": True}


def test_health_reports_sandboxed_and_digest(wrapped_proxy):
    _status, body = _get(f"{wrapped_proxy}/fake-stdio/health")
    assert (body["sandboxed"], body["sandbox_digest"]) == (True, "d1g3st")
    _status, body = _get(f"{wrapped_proxy}/free/health")
    assert body["sandboxed"] is False


def test_root_without_sandbox_reports_empty_digest(proxy):
    _status, body = _get(f"{proxy}/")
    assert body["sandbox_digest"] == ""
    assert body["servers"]["fake"] == {"kind": "http", "sandboxed": False, "exempt": False}


def test_parse_stdio_wrap_fails_closed():
    assert remote_proxy.parse_stdio_wrap("") == []
    assert remote_proxy.parse_stdio_wrap('["py", "-c", "x"]') == ["py", "-c", "x"]
    for bad in ("not json", "[]", '"py"', '["py", 3]', '["py", ""]'):
        with pytest.raises(SystemExit):
            remote_proxy.parse_stdio_wrap(bad)


def test_main_passes_wrap_and_digest_to_serve(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(remote_proxy, "serve", lambda *a, **k: seen.update(k))
    config = tmp_path / "c.json"
    config.write_text('{"mcpServers": {"s": {"type": "stdio", "command": "srv"}}}')
    argv = ["--config", str(config), "--stdio-wrap", '["py", "-c", "x"]']
    remote_proxy.main([*argv, "--sandbox-digest", "abc"])
    assert seen["wrap"] == ["py", "-c", "x"] and seen["sandbox_digest"] == "abc"
    with pytest.raises(SystemExit):
        remote_proxy.main(["--config", str(config), "--stdio-wrap", "{bad"])


def test_remote_proxy_imports_nothing_from_r4():
    import r4_mcp

    src = str(Path(r4_mcp.__file__).resolve().parents[1])
    code = (
        "import sys; import r4_mcp.runners.remote_proxy; "
        "print(sorted(m for m in sys.modules if m == 'r4' or m.startswith('r4.')))"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = src
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def _env_specs():
    github = _stdio_spec("github")
    github.env = {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"}
    brave = _stdio_spec("brave")
    brave.env = {"BRAVE_API_KEY": "bk"}
    return [github, brave]


def test_wrapped_child_env_drops_names_other_entries_declare(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "daemon-env")
    monkeypatch.setenv("UNRELATED", "u")
    state = remote_proxy._ProxyState(_env_specs(), wrap=["launcher"])
    env = state.clients["github"].child_env()
    assert env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_x" and env["UNRELATED"] == "u"
    assert "BRAVE_API_KEY" not in env
    assert state.clients["brave"].child_env()["BRAVE_API_KEY"] == "bk"  # its own value wins


def test_unwrapped_child_env_is_unchanged(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "daemon-env")
    state = remote_proxy._ProxyState(_env_specs())
    env = state.clients["github"].child_env()
    assert env["BRAVE_API_KEY"] == "daemon-env" and env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_x"


@pytest.mark.parametrize(
    "host, local",
    [
        ("127.0.0.1", True),
        ("127.8.0.1", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("10.0.0.5", False),
        ("::ffff:10.0.0.5", False),
        ("", False),
    ],
)
def test_is_loopback(host, local):
    assert remote_proxy.is_loopback(host) is local


def test_identity_and_health_hide_sandbox_state_from_the_lan(tmp_path):
    wrap, _record = _shim_wrap(tmp_path)
    state = remote_proxy._ProxyState([_stdio_spec()], wrap=wrap, sandbox_digest="d1g3st")
    assert state.identity(local=False) == {
        "status": "ok",
        "service": "r4-mcp-remote-proxy",
        "upstreams": ["fake-stdio"],
    }
    handler = object.__new__(remote_proxy._make_handler(state))
    handler.client_address = ("10.0.0.5", 40000)
    health = handler._health(state.clients["fake-stdio"])
    assert "sandboxed" not in health and "sandbox_digest" not in health
    handler.client_address = ("127.0.0.1", 40000)
    assert handler._health(state.clients["fake-stdio"])["sandbox_digest"] == "d1g3st"
