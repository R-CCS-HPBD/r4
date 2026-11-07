"""Bridge standard remote MCP servers into the UMRS registry dialect.

UMRS's tool proxy (``umrs/src/umrs/api/mcp.py``) speaks a fixed per-server
dialect::

    GET  {url}/tools        -> {"tools": [{name, description, inputSchema}, ...]}
    POST {url}/tools/call   <- JSON-RPC {"method": "tools/call", ...}
                            -> JSON-RPC {"result": {"content": [...], "isError": bool}}

Standard remote MCP servers (context7, deepwiki, microsoft-docs, huggingface, …)
instead expose a single streamable-HTTP endpoint that answers with
``text/event-stream`` framing and optional ``MCP-Session-Id`` headers, so they
cannot be registered directly. This runner bridges N upstreams behind one
local port, one path prefix per upstream::

    r4-mcp remote-proxy --config ~/.config/r4/mcp-servers.json --port 50800
    r4 mcp register context7 --url http://<host>:50800/context7

Config: reuses the Copilot-style ``mcpServers`` file: ``type: "http"``
entries are bridged over streamable HTTP, ``type: "stdio"`` entries run as a
child process of this proxy (spawned on demand, respawned on death). Header
and env values support ``${VAR}`` expansion; a value whose variable is unset
is dropped (never sent as a literal placeholder).

Sandbox (ADR-101 D-9): ``--stdio-wrap '<json argv list>'`` makes every stdio
child (and every respawn) start as ``<wrap> --name <server> -- <command>``
with ``cwd="/"``; an entry with ``"sandbox": false`` is exempt and starts
unwrapped. ``--sandbox-digest`` is reported, unchanged, in ``GET /`` and each
server's health (to loopback callers only), so the client that started the
daemon can tell which policy it runs under. A wrapped child gets this
process's environment minus the names other entries declare in their
``env``, plus its own. This package imports nothing from ``r4``: the wrapper
is an opaque argv prefix.

Placement note: the registered URL is resolved by the UMRS server, not by the
client — run this proxy on the UMRS host (or somewhere it can reach).
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

logger = logging.getLogger("r4_mcp.remote_proxy")

_PROTOCOL_VERSION = "2025-06-18"
#: ``GET /`` names the service so a client can tell this daemon from another listener.
SERVICE_NAME = "r4-mcp-remote-proxy"
_DEFAULT_TOOLS_TTL = 300.0


# =============================================================================
# Config
# =============================================================================


@dataclass
class UpstreamSpec:
    """One upstream MCP server behind a path prefix."""

    name: str
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    #: ``"sandbox": false`` in the entry: never started under ``--stdio-wrap``.
    sandbox_exempt: bool = False

    @property
    def kind(self) -> str:  # noqa: D102
        return "stdio" if self.command else "http"


def _expand_env(value: str) -> str | None:
    """Expand ``${VAR}``; return None when any variable is unset (drop header)."""
    missing = False

    def _repl(match: re.Match[str]) -> str:
        nonlocal missing
        env = os.environ.get(match.group(1))
        if env is None:
            missing = True
            return ""
        return env

    expanded = re.sub(r"\$\{(\w+)\}", _repl, value)
    return None if missing else expanded


def _expand_headers(raw: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in raw.items():
        expanded = _expand_env(str(value))
        if expanded is None:
            logger.warning("header %s dropped: referenced env var is unset", key)
            continue
        headers[key] = expanded
    return headers


# String literals come first so ``//`` inside a quoted value (a URL) is never
# treated as a comment.
_JSONC_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*')


def _strip_jsonc(text: str) -> str:
    """Remove ``//`` line comments without touching quoted strings."""
    parts: list[str] = []
    last = 0
    for match in _JSONC_TOKEN.finditer(text):
        parts.append(text[last : match.start()])
        if match.group(0).startswith('"'):
            parts.append(match.group(0))
        last = match.end()
    parts.append(text[last:])
    return "".join(parts)


def _expand_env_map(raw: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in raw.items():
        expanded = _expand_env(str(value))
        if expanded is None:
            logger.warning("env %s dropped: referenced variable is unset", key)
            continue
        out[key] = expanded
    return out


def _spec_from_entry(name: str, entry: dict[str, Any], timeout: float) -> UpstreamSpec | None:
    """Build one spec from an mcpServers entry; None when unusable or disabled."""
    if entry.get("enabled", True) is False:
        logger.info("skipping %s: disabled in config", name)
        return None
    if entry.get("type", "http") == "stdio":
        command = [entry.get("command", ""), *(entry.get("args") or [])]
        if not command[0]:
            logger.warning("skipping %s: stdio entry without command", name)
            return None
        return UpstreamSpec(
            name=name,
            command=command,
            env=_expand_env_map(entry.get("env") or {}),
            timeout=timeout,
            sandbox_exempt=entry.get("sandbox") is False,
        )
    url = entry.get("url", "")
    if not url:
        logger.warning("skipping %s: http entry without url", name)
        return None
    return UpstreamSpec(
        name=name,
        url=url,
        headers=_expand_headers(entry.get("headers") or {}),
        timeout=timeout,
    )


def load_upstream_specs(config_path: str, timeout: float) -> list[UpstreamSpec]:
    """Load http + stdio upstreams from an mcpServers-style config file."""
    with open(config_path, encoding="utf-8") as fh:
        data = json.loads(_strip_jsonc(fh.read()))
    entries = data.get("mcpServers") or data.get("upstreams") or {}
    specs: list[UpstreamSpec] = []
    for name, entry in entries.items():
        spec = _spec_from_entry(name, entry, timeout)
        if spec is not None:
            specs.append(spec)
    return specs


# =============================================================================
# Upstream streamable-HTTP MCP client
# =============================================================================


def _sse_json_payloads(body: str) -> list[dict[str, Any]]:
    """Extract JSON-RPC payloads from an SSE body (``data: {…}`` lines)."""
    payloads: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


def _pick_jsonrpc_response(body: str, content_type: str) -> dict[str, Any]:
    """Pick the JSON-RPC response object out of a JSON or SSE reply."""
    if "text/event-stream" in content_type:
        payloads = _sse_json_payloads(body)
        for payload in reversed(payloads):
            if "result" in payload or "error" in payload:
                return payload
        raise ValueError("no JSON-RPC response in SSE stream")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("non-object JSON-RPC response")
    return parsed


class UpstreamClient:
    """Minimal streamable-HTTP MCP client with session-id handling."""

    def __init__(self, spec: UpstreamSpec) -> None:
        self.spec = spec
        self._client = httpx.Client(timeout=spec.timeout)
        self._session_id: str | None = None
        self._initialized = False

    # -- wire ----------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **self.spec.headers,
        }
        if self._session_id:
            headers["MCP-Session-Id"] = self._session_id
            headers["MCP-Protocol-Version"] = _PROTOCOL_VERSION
        return headers

    def _post(self, message: dict[str, Any]) -> tuple[int, dict[str, Any], httpx.Headers]:
        resp = self._client.post(self.spec.url, json=message, headers=self._headers())
        content_type = resp.headers.get("content-type", "")
        if resp.status_code == 202 or not resp.content:
            return resp.status_code, {}, resp.headers
        try:
            payload = _pick_jsonrpc_response(resp.text, content_type)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"upstream {self.spec.name} reply not parseable "
                f"(status={resp.status_code}, content-type={content_type})"
            ) from exc
        return resp.status_code, payload, resp.headers

    def ensure_session(self, force: bool = False) -> None:
        """Run the initialize handshake once; ``force`` re-handshakes."""
        if self._initialized and not force:
            return
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "r4-mcp-remote-proxy", "version": "0.2.0"},
            },
        }
        status, payload, headers = self._post(message)
        if status >= 400 or "error" in payload:
            raise RuntimeError(f"initialize failed on {self.spec.name}: status={status}")
        session = headers.get("mcp-session-id")
        self._session_id = session or None
        self._initialized = True
        self._notify_initialized()

    def _notify_initialized(self) -> None:
        """Best-effort ``notifications/initialized``; servers may ignore it."""
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:  # noqa: BLE001 — notification failure must not block tools
            logger.debug("initialized notification failed for %s", self.spec.name)

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """JSON-RPC call with one session-refresh retry on 404."""
        self.ensure_session()
        message = {"jsonrpc": "2.0", "id": 2, "method": method, "params": params}
        status, payload, _ = self._post(message)
        if status == 404:
            logger.info("session expired for %s; re-initializing", self.spec.name)
            self.ensure_session(force=True)
            status, payload, _ = self._post(message)
        if status >= 400:
            raise RuntimeError(f"{method} on {self.spec.name} failed: HTTP {status}")
        if "error" in payload:
            err = payload["error"]
            raise RuntimeError(f"{method} on {self.spec.name}: {err.get('message', err)}")
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    # -- MCP surface ----------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.call("tools/list", {})
        tools = result.get("tools", [])
        return [t for t in tools if isinstance(t, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.call("tools/call", {"name": name, "arguments": arguments})


# =============================================================================
# Stdio (local provider) MCP client — NDJSON over child-process pipes
# =============================================================================


class StdioUpstreamClient:
    """MCP client over a spawned child's stdio (newline-delimited JSON-RPC).

    One long-lived child per upstream; on death the next call respawns it and
    re-runs the initialize handshake. A background reader thread dispatches
    responses to the request ids registered in ``_pending``.
    """

    def __init__(
        self, spec: UpstreamSpec, wrap: Sequence[str] = (), foreign_env: Sequence[str] = ()
    ) -> None:
        self.spec = spec
        self.wrap = list(wrap)
        #: Names other entries declare in their ``env``: a wrapped child never
        #: inherits them, since the launcher keeps every declared name through
        #: its credential scrub (each server gets only its own).
        self.foreign_env = frozenset(foreign_env) - set(spec.env)
        self._proc: subprocess.Popen[str] | None = None
        self._pending: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._write_lock = threading.Lock()
        self._next_id = 0

    # -- process lifecycle ----------------------------------------------------

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def sandboxed(self) -> bool:
        """True when each spawn goes through the ``--stdio-wrap`` prefix."""
        return bool(self.wrap) and not self.spec.sandbox_exempt

    def argv(self) -> list[str]:
        """The child's argv: ``<wrap> --name <server> -- <command>`` when sandboxed."""
        if not self.sandboxed:
            return list(self.spec.command)
        return [*self.wrap, "--name", self.spec.name, "--", *self.spec.command]

    def child_env(self) -> dict[str, str]:
        """``os.environ`` plus the entry's ``env``; wrapped: minus other entries' names."""
        if not self.sandboxed:
            return {**os.environ, **self.spec.env}
        inherited = {k: v for k, v in os.environ.items() if k not in self.foreign_env}
        return {**inherited, **self.spec.env}

    def _spawn(self) -> None:
        env = self.child_env()
        self._proc = subprocess.Popen(  # noqa: S603 — operator-controlled config
            self.argv(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
            cwd="/" if self.sandboxed else None,
        )
        threading.Thread(target=self._reader_loop, daemon=True).start()

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except OSError:
                pass
        self._proc = None
        for event, _payload in self._pending.values():
            event.set()
        self._pending.clear()

    def ensure_session(self, force: bool = False) -> None:
        """Spawn (or respawn) the child and handshake once."""
        if self._alive() and not force:
            return
        self._kill()
        self._spawn()
        self.call("initialize", _initialize_params(), session_started=True)
        self._notify("notifications/initialized", {})

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            with self._write_lock:
                assert self._proc is not None and self._proc.stdin is not None
                self._proc.stdin.write(json.dumps(message) + "\n")
                self._proc.stdin.flush()
        except (BrokenPipeError, OSError, AssertionError):
            logger.debug("notification %s to %s failed", method, self.spec.name)

    # -- wire -----------------------------------------------------------------

    def _reader_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_id = message.get("id")
            slot = self._pending.pop(msg_id, None)
            if slot is not None:
                slot[1].update(message)
                slot[0].set()

    def call(
        self, method: str, params: dict[str, Any], session_started: bool = False
    ) -> dict[str, Any]:
        """JSON-RPC round-trip; respawns the child once on unexpected death."""
        if not session_started:
            self.ensure_session()
        try:
            return self._exchange(method, params)
        except (BrokenPipeError, OSError) as exc:
            logger.info("stdio upstream %s died (%s); respawning", self.spec.name, exc)
            self.ensure_session(force=True)
            return self._exchange(method, params)

    def _exchange(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        event = threading.Event()
        payload: dict[str, Any] = {}
        self._pending[msg_id] = (event, payload)
        message = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        with self._write_lock:
            assert self._proc is not None and self._proc.stdin is not None
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        if not event.wait(timeout=self.spec.timeout):
            self._pending.pop(msg_id, None)
            raise RuntimeError(f"{method} on {self.spec.name} timed out")
        if "error" in payload:
            err = payload["error"]
            raise RuntimeError(f"{method} on {self.spec.name}: {err.get('message', err)}")
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    # -- MCP surface ----------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.call("tools/list", {})
        tools = result.get("tools", [])
        return [t for t in tools if isinstance(t, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.call("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        """Terminate the child on proxy shutdown."""
        self._kill()


# =============================================================================
# Dialect HTTP server
# =============================================================================


def _initialize_params() -> dict[str, Any]:
    return {
        "protocolVersion": _PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "r4-mcp-remote-proxy", "version": "0.2.0"},
    }


class _ProxyState:
    """Shared state: upstream clients + per-upstream tools cache."""

    def __init__(
        self,
        specs: list[UpstreamSpec],
        tools_ttl: float = _DEFAULT_TOOLS_TTL,
        wrap: Sequence[str] = (),
        sandbox_digest: str = "",
    ) -> None:
        declared = sorted({key for spec in specs for key in spec.env})
        self.clients: dict[str, UpstreamClient | StdioUpstreamClient] = {
            spec.name: (
                StdioUpstreamClient(spec, wrap, declared)
                if spec.kind == "stdio"
                else UpstreamClient(spec)
            )
            for spec in specs
        }
        self.tools_ttl = tools_ttl
        self.sandbox_digest = sandbox_digest
        self._tools_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def identity(self, local: bool = True) -> dict[str, Any]:
        """``GET /``: who this is, what it serves, and under which sandbox digest.

        The pid and sandbox fields go to loopback callers only (the client
        that started the daemon); the daemon binds beyond loopback, and which
        servers run unconfined is not for the LAN. The per-server report is a
        startup snapshot of the ``--config`` entries; the launcher re-reads
        the entry at each spawn, which is why the client folds the exempt
        servers into the digest it starts the daemon with.
        """
        public = {"status": "ok", "service": SERVICE_NAME, "upstreams": sorted(self.clients)}
        if not local:
            return public
        servers = {name: _server_report(client) for name, client in self.clients.items()}
        return {
            **public,
            "pid": os.getpid(),
            "sandbox_digest": self.sandbox_digest,
            "servers": servers,
        }

    def tools_for(self, name: str) -> list[dict[str, Any]]:
        cached = self._tools_cache.get(name)
        if cached and time.monotonic() - cached[0] < self.tools_ttl:
            return cached[1]
        tools = self.clients[name].list_tools()
        self._tools_cache[name] = (time.monotonic(), tools)
        return tools

    def close(self) -> None:
        """Tear down stdio children."""
        for client in self.clients.values():
            close = getattr(client, "close", None)
            if close is not None:
                close()


def _server_report(client: UpstreamClient | StdioUpstreamClient) -> dict[str, Any]:
    """Per-server sandbox state: only stdio children can be sandboxed."""
    return {
        "kind": client.spec.kind,
        "sandboxed": bool(getattr(client, "sandboxed", False)),
        "exempt": client.spec.sandbox_exempt,
    }


def is_loopback(host: str) -> bool:
    """True for 127.0.0.0/8, ::1 and their IPv4-mapped forms; False for anything else."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool((mapped or address).is_loopback)


def _parse_route(path: str) -> tuple[str, str]:
    """Split ``/<name>/<tail...>`` into (upstream name, tail)."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "", ""
    return parts[0], "/".join(parts[1:])


def _tool_call_result(
    name: str, arguments: Any, client: UpstreamClient | StdioUpstreamClient
) -> dict[str, Any]:
    """Run one tool call, normalizing failures into MCP error content."""
    if not isinstance(arguments, dict):
        raise ValueError("params.arguments must be an object")
    result = client.call_tool(name, arguments)
    return {
        "content": result.get("content", []),
        "isError": bool(result.get("isError", False)),
    }


class _DialectHandler(BaseHTTPRequestHandler):
    """UMRS-dialect HTTP surface; ``proxy_state`` bound by ``_make_handler``."""

    server_version = "r4-mcp-remote-proxy/0.2.0"
    proxy_state: _ProxyState

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _client_or_404(self, name: str) -> UpstreamClient | StdioUpstreamClient | None:
        client = self.proxy_state.clients.get(name)
        if client is None:
            self._send_json({"error": f"unknown upstream: {name!r}"}, status=404)
            return None
        return client

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_tools(self, name: str) -> None:
        try:
            self._send_json({"tools": self.proxy_state.tools_for(name)})
        except Exception as exc:  # noqa: BLE001 — surface upstream failure as 502
            self._send_json({"error": str(exc), "tools": []}, status=502)

    def do_GET(self) -> None:  # noqa: N802
        name, tail = _parse_route(self.path)
        if not name:
            self._send_json(self.proxy_state.identity(local=self._local_caller()))
            return
        client = self._client_or_404(name)
        if client is None:
            return
        if tail in ("", "health"):
            self._send_json(self._health(client))
        elif tail == "tools":
            self._handle_tools(name)
        else:
            self._send_json({"error": f"unknown route: /{name}/{tail}"}, status=404)

    def _local_caller(self) -> bool:
        return is_loopback(str(self.client_address[0]))

    def _health(self, client: UpstreamClient | StdioUpstreamClient) -> dict[str, Any]:
        health: dict[str, Any] = {
            "status": "ok",
            "upstream": client.spec.url or " ".join(client.spec.command),
            "kind": client.spec.kind,
        }
        if self._local_caller():  # sandbox state: loopback callers only
            health["sandboxed"] = bool(getattr(client, "sandboxed", False))
            health["sandbox_digest"] = self.proxy_state.sandbox_digest
        return health

    def do_POST(self) -> None:  # noqa: N802
        name, tail = _parse_route(self.path)
        if not name:
            self._send_json({"error": "missing upstream name"}, status=404)
            return
        client = self._client_or_404(name)
        if client is None:
            return
        if tail != "tools/call":
            self._send_json({"error": f"unknown route: /{name}/{tail}"}, status=404)
            return
        self._handle_tool_call(client)

    def _handle_tool_call(self, client: UpstreamClient | StdioUpstreamClient) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            envelope = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON body"}, status=400)
            return
        req_id = envelope.get("id", 1)
        params = envelope.get("params") or {}
        try:
            result = _tool_call_result(params.get("name", ""), params.get("arguments", {}), client)
        except Exception as exc:  # noqa: BLE001 — surface as MCP error content
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        self._send_json({"jsonrpc": "2.0", "id": req_id, "result": result})


def _make_handler(state: _ProxyState) -> type[BaseHTTPRequestHandler]:
    return type("BoundDialectHandler", (_DialectHandler,), {"proxy_state": state})


def serve(
    host: str,
    port: int,
    specs: list[UpstreamSpec],
    tools_ttl: float,
    wrap: Sequence[str] = (),
    sandbox_digest: str = "",
) -> None:
    """Run the dialect bridge (blocking); ``wrap`` prefixes every stdio child."""
    if not specs:
        raise SystemExit("remote-proxy: no http upstreams configured")
    state = _ProxyState(specs, tools_ttl=tools_ttl, wrap=wrap, sandbox_digest=sandbox_digest)
    server = ThreadingHTTPServer((host, port), _make_handler(state))
    logger.info(
        "remote-proxy on http://%s:%d serving %d upstream(s): %s",
        host,
        port,
        len(specs),
        ", ".join(sorted(state.clients)),
    )
    try:
        server.serve_forever()
    finally:
        state.close()


# =============================================================================
# CLI
# =============================================================================


def parse_stdio_wrap(raw: str) -> list[str]:
    """``--stdio-wrap``: a non-empty JSON list of non-empty strings, or "" for none.

    Anything else exits: a daemon asked to sandbox its children never starts
    them unwrapped because the prefix was malformed.
    """
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"remote-proxy: --stdio-wrap is not JSON: {exc}") from exc
    if not isinstance(value, list) or not value:
        raise SystemExit("remote-proxy: --stdio-wrap must be a non-empty JSON list")
    if not all(isinstance(item, str) and item for item in value):
        raise SystemExit("remote-proxy: --stdio-wrap items must be non-empty strings")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remote MCP → UMRS dialect bridge")
    parser.add_argument("--config", help="mcpServers-style config with http entries")
    parser.add_argument("--name", help="single upstream name (with --url)")
    parser.add_argument("--url", help="single upstream URL (with --name)")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help="'Key: Value' header for the single upstream (repeatable)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50800)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--tools-ttl", type=float, default=_DEFAULT_TOOLS_TTL)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--stdio-wrap",
        default="",
        help="JSON argv list prefixed to every stdio child (the sandbox launcher)",
    )
    parser.add_argument(
        "--sandbox-digest", default="", help="reported in GET / and in each server's health"
    )
    args = parser.parse_args(argv)
    wrap = parse_stdio_wrap(args.stdio_wrap)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    specs: list[UpstreamSpec] = []
    if args.config:
        specs = load_upstream_specs(args.config, timeout=args.timeout)
    if args.name and args.url:
        headers = dict(h.split(":", 1) for h in args.header)
        specs.append(
            UpstreamSpec(
                name=args.name,
                url=args.url,
                headers={k: v.strip() for k, v in headers.items()},
                timeout=args.timeout,
            )
        )
    serve(
        args.host,
        args.port,
        specs,
        tools_ttl=args.tools_ttl,
        wrap=wrap,
        sandbox_digest=args.sandbox_digest,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
