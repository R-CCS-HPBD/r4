"""ADR-101 D-6/D-7: the sandbox egress proxy (pure decisions and a live loopback proxy).

The upstream is a ThreadingHTTPServer on 127.0.0.1; the proxy's resolver and
connector are injected, so the policy sees a public name (``allowed.example``)
and no test needs the internet.
"""

from __future__ import annotations

import asyncio
import base64
import http.server
import socket
import threading
import urllib.error
import urllib.request

import pytest

from r4.companion.sandbox import EgressProxy, decide, vet_addresses
from r4.companion.sandbox.proxy import (
    ProxyRefused,
    address_blocked,
    origin_request,
    parse_head,
    request_target,
)
from r4.companion.web_access import REFUSAL_OFF, REFUSAL_UNCHOSEN, WebAccess, make_web_access

PUBLIC = make_web_access("public", source="flag")
NONE = make_web_access("none", source="flag")
ALLOW = make_web_access("allowlist", [".allowed.example", "allowed.example"], source="flag")


# ── pure decisions ─────────────────────────────────────────────────────────


def test_decide_follows_web_access_modes():
    assert decide("anything.org", 443, PUBLIC) is None
    assert decide("allowed.example", 443, ALLOW) is None
    assert decide("api.allowed.example", 80, ALLOW) is None
    refusal = decide("evil.org", 443, ALLOW)
    assert refusal is not None and "'evil.org' is not on it" in refusal
    assert decide("allowed.example", 443, NONE) == REFUSAL_OFF
    assert decide("allowed.example", 443, WebAccess(mode="")) == REFUSAL_UNCHOSEN


def test_decide_refuses_other_ports_and_literal_ips_unless_public():
    assert "ports 80 and 443" in (decide("allowed.example", 22, PUBLIC) or "")
    listed = make_web_access("allowlist", ["93.184.216.34"], source="flag")
    assert "literal address" in (decide("93.184.216.34", 443, listed) or "")
    assert decide("93.184.216.34", 443, PUBLIC) is None


@pytest.mark.parametrize(
    "address",
    [
        "10.1.2.3",
        "192.168.0.1",
        "172.16.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "0.0.0.0",
        "240.0.0.1",
        "::1",
        "::",
        "fc00::1",
        "fe80::1",
        "::ffff:10.0.0.1",
        "::ffff:127.0.0.1",
        "not-an-ip",
    ],
)
def test_vet_refuses_infrastructure_addresses(address):
    assert address_blocked(address)
    with pytest.raises(ProxyRefused) as info:
        vet_addresses(["93.184.216.34", address], "h.example")
    assert info.value.status == 403 and "infrastructure" in info.value.reason


def test_vet_returns_first_public_address_and_refuses_empty():
    assert vet_addresses(["93.184.216.34", "2606:2800:220:1::1"]) == "93.184.216.34"
    with pytest.raises(ProxyRefused) as info:
        vet_addresses([], "x.example")
    assert info.value.status == 502


def test_request_parsing_and_origin_rewrite():
    raw = (
        b"GET http://allowed.example/a?b=1 HTTP/1.1\r\nHost: allowed.example\r\n"
        b"Proxy-Authorization: Basic x\r\nConnection: keep-alive, X-Drop\r\nX-Drop: 1\r\n"
        b"Accept: */*\r\n\r\n"
    )
    request = parse_head(raw)
    assert request_target(request) == ("allowed.example", 80, False)
    text = origin_request(request).decode()
    assert text.startswith("GET /a?b=1 HTTP/1.1\r\n")
    assert "Proxy-Authorization" not in text and "X-Drop" not in text and "keep-alive" not in text
    assert "Accept: */*" in text and text.endswith("Connection: close\r\n\r\n")
    connect = parse_head(b"CONNECT [2606:2800::1]:443 HTTP/1.1\r\n\r\n")
    assert request_target(connect) == ("2606:2800::1", 443, True)


def test_request_target_refuses_other_schemes_and_bad_hosts():
    for line in (
        b"GET ftp://x.org/ HTTP/1.1",
        b"GET /relative HTTP/1.1",
        b"CONNECT a/b:443 HTTP/1.1",
    ):
        with pytest.raises(ProxyRefused):
            request_target(parse_head(line + b"\r\n\r\n"))


# ── a live proxy on loopback ────────────────────────────────────────────────


class _Upstream(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — http.server API
        body = f"upstream saw {self.path} host={self.headers.get('Host')}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


@pytest.fixture
def upstream():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def fake_network(port: int, address: str = "93.184.216.34"):
    """Resolver: every name is ``address``; connector: always the local upstream."""

    async def resolver(host, _port):
        return [address]

    async def connector(_address, _port):
        return await asyncio.open_connection("127.0.0.1", port)

    return resolver, connector


@pytest.fixture
def live(upstream):
    state = {"web": PUBLIC}
    resolver, connector = fake_network(upstream)
    proxy = EgressProxy(lambda: state["web"], resolver=resolver, connector=connector)
    endpoint = proxy.start()
    yield state, endpoint, upstream
    proxy.stop()


def _opener(endpoint) -> urllib.request.OpenerDirector:
    url = endpoint.url()
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": url, "https": url}))


def test_live_proxy_forwards_an_allowed_plain_http_request(live):
    state, endpoint, _ = live
    with _opener(endpoint).open("http://allowed.example/hello", timeout=10) as resp:
        assert resp.read().decode() == "upstream saw /hello host=allowed.example"


def test_live_proxy_follows_web_access_changes_per_request(live):
    state, endpoint, _ = live
    state["web"] = ALLOW
    with pytest.raises(urllib.error.HTTPError) as info:
        _opener(endpoint).open("http://other.example/", timeout=10)
    assert info.value.code == 403 and "'other.example' is not on it" in info.value.read().decode()
    state["web"] = NONE
    with pytest.raises(urllib.error.HTTPError) as info:
        _opener(endpoint).open("http://allowed.example/", timeout=10)
    assert info.value.read().decode().strip() == REFUSAL_OFF


def test_live_proxy_requires_its_nonce(live):
    _, endpoint, _ = live
    plain = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{endpoint.port}"})
    )
    with pytest.raises(urllib.error.HTTPError) as info:
        plain.open("http://allowed.example/", timeout=10)
    assert info.value.code == 407


def _raw(endpoint, payload: bytes) -> bytes:
    token = base64.b64encode(f"r4:{endpoint.nonce}".encode()).decode()
    head = payload.replace(
        b"\r\n\r\n", f"\r\nProxy-Authorization: Basic {token}\r\n\r\n".encode(), 1
    )
    with socket.create_connection(("127.0.0.1", endpoint.port), timeout=10) as sock:
        sock.sendall(head)
        chunks = []
        while data := sock.recv(65536):
            chunks.append(data)
    return b"".join(chunks)


def test_live_proxy_connect_tunnel(live):
    _, endpoint, _ = live
    token = base64.b64encode(f"r4:{endpoint.nonce}".encode()).decode()
    with socket.create_connection(("127.0.0.1", endpoint.port), timeout=10) as sock:
        sock.sendall(
            b"CONNECT allowed.example:443 HTTP/1.1\r\n"
            + f"Proxy-Authorization: Basic {token}\r\n\r\n".encode()
        )
        assert sock.recv(4096).startswith(b"HTTP/1.1 200")
        sock.sendall(b"GET /tunnel HTTP/1.1\r\nHost: allowed.example\r\nConnection: close\r\n\r\n")
        reply = b""
        while data := sock.recv(65536):
            reply += data
    assert b"upstream saw /tunnel" in reply


def test_live_proxy_refuses_names_resolving_to_private_addresses(upstream):
    resolver, connector = fake_network(upstream, address="10.0.0.5")
    proxy = EgressProxy(lambda: PUBLIC, resolver=resolver, connector=connector)
    endpoint = proxy.start()
    try:
        reply = _raw(endpoint, b"GET http://intranet.example/ HTTP/1.1\r\n\r\n")
    finally:
        proxy.stop()
    assert reply.startswith(b"HTTP/1.1 403") and b"infrastructure" in reply


def test_live_proxy_caps_the_header_block(live):
    _, endpoint, _ = live
    big = b"GET http://allowed.example/ HTTP/1.1\r\nX-Big: " + b"a" * 70000 + b"\r\n\r\n"
    reply = _raw(endpoint, big)
    assert reply.startswith(b"HTTP/1.1 431")


def test_unix_socket_proxy_has_private_socket(tmp_path, upstream):
    resolver, connector = fake_network(upstream)
    sock_path = str(tmp_path / "p.sock")
    proxy = EgressProxy(lambda: PUBLIC, unix_path=sock_path, resolver=resolver, connector=connector)
    endpoint = proxy.start()
    try:
        assert endpoint.url() == "http://127.0.0.1:3128" and endpoint.nonce == ""
        import os
        import stat

        assert stat.S_IMODE(os.stat(sock_path).st_mode) == 0o600
        with socket.socket(socket.AF_UNIX) as sock:
            sock.connect(sock_path)
            sock.sendall(b"GET http://allowed.example/u HTTP/1.1\r\n\r\n")
            reply = b""
            while data := sock.recv(65536):
                reply += data
        assert b"upstream saw /u" in reply
    finally:
        proxy.stop()
