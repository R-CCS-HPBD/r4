"""EgressGuard (r4_mcp.egress_guard) — behavior tests, no network beyond loopback.

Pins ADR-040 D1 (fail-closed allowlist), the private-destination denial, the
CONNECT tunnel, absolute-URI HTTP forwarding, local health, and audit lines.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

from r4_mcp.egress_guard import (
    DENY_EMPTY_ALLOWLIST,
    DENY_MALFORMED,
    DENY_NOT_ALLOWED,
    DENY_PRIVATE,
    AuditSink,
    EgressGuard,
    EgressPolicy,
    parse_allowlist,
    parse_request_head,
)

LOOPBACK = "127.0.0.1"


async def _start_guard(tmp_path: Path, allowlist: list[str], *, deny_private: bool):
    audit = AuditSink(path=tmp_path / "audit.jsonl", instance={"instance_id": "test"})
    policy = EgressPolicy(
        allowlist=frozenset(allowlist), deny_private=deny_private, connect_timeout=2.0
    )
    server = await asyncio.start_server(EgressGuard(policy, audit).handle, LOOPBACK, 0)
    return server, server.sockets[0].getsockname()[1], audit


async def _start_echo():
    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(1024):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(echo, LOOPBACK, 0)
    return server, server.sockets[0].getsockname()[1]


async def _start_http(seen: list[bytes]):
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, LOOPBACK, 0)
    return server, server.sockets[0].getsockname()[1]


async def _stop(*servers) -> None:
    for server in servers:
        server.close()
        await server.wait_closed()


async def _request(port: int, payload: bytes) -> bytes:
    reader, writer = await asyncio.open_connection(LOOPBACK, port)
    writer.write(payload)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()
    return data


async def _wait_audit(audit: AuditSink, action: str) -> dict:
    for _ in range(50):
        if audit.path.exists():
            lines = [json.loads(line) for line in audit.path.read_text().splitlines()]
            hits = [entry for entry in lines if entry["action"] == action]
            if hits:
                return hits[-1]
        await asyncio.sleep(0.05)
    raise AssertionError(f"no audit line with action={action!r}")


def _connect(port: int) -> bytes:
    return f"CONNECT {LOOPBACK}:{port} HTTP/1.1\r\nHost: {LOOPBACK}:{port}\r\n\r\n".encode()


# ── policy helpers ───────────────────────────────────────────────────────────


def test_parse_allowlist_normalizes():
    assert parse_allowlist(" Api.Example.com., b.org ,,") == frozenset({"api.example.com", "b.org"})
    assert parse_allowlist(None) == frozenset()


def test_policy_from_env_is_fail_closed_by_default():
    policy = EgressPolicy.from_env({})
    assert policy.allowlist == frozenset()
    assert policy.deny_private is True


def test_parse_request_head_shapes():
    connect = parse_request_head(b"CONNECT api.example.com:443 HTTP/1.1\r\nHost: x\r\n\r\n")
    assert (connect.host, connect.port, connect.path) == ("api.example.com", 443, "")
    absolute = parse_request_head(b"GET http://h.example.org/p?q=1 HTTP/1.1\r\nHost: h\r\n\r\n")
    assert (absolute.host, absolute.port, absolute.path) == ("h.example.org", 80, "/p?q=1")
    local = parse_request_head(b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
    assert local.host == "" and local.path == "/healthz"
    assert parse_request_head(b"garbage\r\n\r\n") is None
    assert parse_request_head(b"GET / HTTP/1.1\r\nbroken header line\r\n\r\n") is None


# ── fail-closed decisions ────────────────────────────────────────────────────


async def test_connect_denied_when_allowlist_empty(tmp_path):
    server, port, audit = await _start_guard(tmp_path, [], deny_private=False)
    try:
        response = await _request(port, _connect(9))
    finally:
        await _stop(server)
    assert response.startswith(b"HTTP/1.1 403")
    assert DENY_EMPTY_ALLOWLIST.encode() in response
    entry = await _wait_audit(audit, "denied")
    assert entry["reason"] == DENY_EMPTY_ALLOWLIST
    assert entry["host"] == LOOPBACK and entry["instance_id"] == "test"


async def test_connect_denied_when_domain_not_allowed(tmp_path):
    server, port, _audit = await _start_guard(tmp_path, ["api.example.com"], deny_private=False)
    try:
        response = await _request(port, _connect(9))
    finally:
        await _stop(server)
    assert response.startswith(b"HTTP/1.1 403")
    assert DENY_NOT_ALLOWED.encode() in response


async def test_private_destination_denied_even_when_allowlisted(tmp_path):
    server, port, _audit = await _start_guard(tmp_path, [LOOPBACK], deny_private=True)
    try:
        response = await _request(port, _connect(9))
    finally:
        await _stop(server)
    assert response.startswith(b"HTTP/1.1 403")
    assert DENY_PRIVATE.encode() in response


async def test_malformed_request_rejected(tmp_path):
    server, port, _audit = await _start_guard(tmp_path, [LOOPBACK], deny_private=False)
    try:
        response = await _request(port, b"garbage\r\n\r\n")
    finally:
        await _stop(server)
    assert response.startswith(b"HTTP/1.1 400")
    assert DENY_MALFORMED.encode() in response


async def test_healthz_served_locally(tmp_path):
    server, port, _audit = await _start_guard(tmp_path, [], deny_private=True)
    try:
        response = await _request(port, b"GET /healthz HTTP/1.1\r\nHost: egress-guard\r\n\r\n")
    finally:
        await _stop(server)
    assert response.startswith(b"HTTP/1.1 200")
    assert response.endswith(b"ok")


# ── allowed paths ────────────────────────────────────────────────────────────


async def test_connect_tunnels_bytes_to_allowed_host(tmp_path):
    echo, echo_port = await _start_echo()
    server, port, audit = await _start_guard(tmp_path, [LOOPBACK], deny_private=False)
    try:
        reader, writer = await asyncio.open_connection(LOOPBACK, port)
        writer.write(_connect(echo_port))
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        assert head.startswith(b"HTTP/1.1 200 Connection Established")
        writer.write(b"ping")
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(4), timeout=5) == b"ping"
        writer.close()
        entry = await _wait_audit(audit, "allowed")
    finally:
        await _stop(server, echo)
    assert entry["mode"] == "connect"
    assert entry["bytes_up"] >= 4 and entry["bytes_down"] >= 4
    assert entry["port"] == echo_port


async def test_absolute_uri_http_forwarded_in_origin_form(tmp_path):
    seen: list[bytes] = []
    http, http_port = await _start_http(seen)
    server, port, audit = await _start_guard(tmp_path, [LOOPBACK], deny_private=False)
    try:
        payload = (
            f"GET http://{LOOPBACK}:{http_port}/data?x=1 HTTP/1.1\r\n"
            f"Host: {LOOPBACK}:{http_port}\r\nProxy-Connection: keep-alive\r\n\r\n"
        ).encode()
        response = await _request(port, payload)
        entry = await _wait_audit(audit, "allowed")
    finally:
        await _stop(server, http)
    assert response.startswith(b"HTTP/1.1 200") and response.endswith(b"hello")
    assert seen and seen[0].startswith(b"GET /data?x=1 HTTP/1.1\r\n")
    assert b"Proxy-Connection" not in seen[0] and b"Connection: close" in seen[0]
    assert entry["mode"] == "http" and entry["bytes_down"] > 0


async def test_upstream_connect_failure_is_502_and_audited(tmp_path):
    with socket.socket() as probe:
        probe.bind((LOOPBACK, 0))
        closed_port = probe.getsockname()[1]  # nothing listens here once closed
    server, port, audit = await _start_guard(tmp_path, [LOOPBACK], deny_private=False)
    try:
        response = await _request(port, _connect(closed_port))
        entry = await _wait_audit(audit, "denied")
    finally:
        await _stop(server)
    assert response.startswith(b"HTTP/1.1 502")
    assert entry["status"] == 502 and "connect failed" in entry["reason"]
