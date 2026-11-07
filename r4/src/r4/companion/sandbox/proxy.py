"""R4's loopback egress proxy: the only way out of a sandbox (ADR-101 D-6, D-7).

A sandboxed process has no network of its own. Its ``HTTP(S)_PROXY`` points
here, and this proxy decides every request from the session's LIVE web access
(the getter is called per request, so ``/web`` changes reach processes that
already run):

- ``none`` or unchosen: refused, with the web tools' own refusal text;
- ``allowlist``: the CONNECT / absolute-URI host must pass
  ``web_access.host_allowed`` (squid ``dstdomain``, the function the web tools
  use); a literal IP never matches;
- ``public``: any name, and literal IPs.

Ports 80 and 443 only. After resolving, any private, loopback, link-local
(cloud metadata), CGNAT, multicast, reserved or unspecified address refuses
the request (IPv4-mapped forms included), and the proxy connects to the
vetted address, so DNS cannot rebind between the check and the connect.

Linux serves a unix socket in a 0700 directory (bridged into the network
namespace by ``_netns_init.py``); macOS an ephemeral ``127.0.0.1`` port that
requires a per-context ``Proxy-Authorization`` nonce. The server runs an
asyncio loop in a daemon thread. SNI and the Host header are not inspected:
the CONNECT authority is the policy boundary, as in squid without ssl-bump.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import socket
import threading
import urllib.parse
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from ..web_access import WAY_OUT, WebAccess, check_url

logger = logging.getLogger(__name__)

ALLOWED_PORTS = frozenset({80, 443})
#: The port the in-namespace forwarder listens on (Linux).
INSIDE_PROXY_PORT = 3128
HEADER_LIMIT = 64 * 1024
HEADER_TIMEOUT = 10.0
CONNECT_TIMEOUT = 15.0
PROXY_USER = "r4"
NO_PROXY_VALUE = "localhost,127.0.0.1,::1"

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "upgrade",
    }
)
_HOST_RE = re.compile(r"^[A-Za-z0-9._-]{1,253}$")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_STATUS_TEXT = {
    400: "Bad Request",
    403: "Forbidden",
    407: "Proxy Authentication Required",
    408: "Request Timeout",
    431: "Request Header Fields Too Large",
    502: "Bad Gateway",
}

REFUSAL_PORT = (
    "the sandbox proxy allows ports 80 and 443 only; {target} was refused; do not retry; "
    "the user can turn the sandbox off with /sandbox off"
)
REFUSAL_LITERAL_IP = (
    "web access for this session is limited to an allowlist of names; the literal address "
    "{host!r} is refused; do not retry this host; " + WAY_OUT
)
REFUSAL_INFRA = (
    "the sandbox proxy refuses {host!r}: it resolves to a private, loopback, link-local or "
    "other infrastructure address (ADR-101 D-7); do not retry; for LAN or cluster hosts the "
    "user can turn the sandbox off with /sandbox off"
)

Resolver = Callable[[str, int], Awaitable[list[str]]]
Connector = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


class ProxyRefused(Exception):
    """A request the proxy answers itself (``status``, text body)."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


# ── pure policy ─────────────────────────────────────────────────────────────


def _ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]").split("%")[0])
    except ValueError:
        return None


def _url_for(host: str) -> str:
    return f"http://[{host}]/" if ":" in host else f"http://{host}/"


def decide(host: str, port: int, web: WebAccess) -> str | None:
    """The refusal text for ``host:port`` under ``web``, or None when allowed."""
    if port not in ALLOWED_PORTS:
        return REFUSAL_PORT.format(target=f"{host}:{port}")
    refusal = check_url(web, _url_for(host))
    if refusal is None and web.mode != "public" and _ip_literal(host) is not None:
        return REFUSAL_LITERAL_IP.format(host=host)
    return refusal


def address_blocked(address: str) -> bool:
    """True for an address no sandboxed request may reach (D-7)."""
    ip = _ip_literal(address)
    if ip is None:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    flags = (ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_multicast)
    more = (ip.is_reserved, ip.is_unspecified, ip in _CGNAT, not ip.is_global)
    return any(flags) or any(more)


def vet_addresses(addresses: Iterable[str], host: str = "") -> str:
    """The address to connect to; refuses when none resolved or any is blocked."""
    found = list(dict.fromkeys(addresses))
    if not found:
        raise ProxyRefused(502, f"the sandbox proxy could not resolve {host!r}")
    if any(address_blocked(address) for address in found):
        raise ProxyRefused(403, REFUSAL_INFRA.format(host=host or found[0]))
    return found[0]


# ── the request head ────────────────────────────────────────────────────────


@dataclass
class ProxyRequest:
    method: str
    target: str
    version: str
    headers: list[tuple[str, str]] = field(default_factory=list)

    def header(self, name: str) -> str:
        lowered = name.lower()
        return next((v for k, v in self.headers if k.lower() == lowered), "")


def parse_head(raw: bytes) -> ProxyRequest:
    """Request line and headers of one proxied request."""
    lines = raw.decode("latin-1").split("\r\n")
    parts = lines[0].split()
    if len(parts) != 3:
        raise ProxyRefused(400, "the sandbox proxy could not parse the request line")
    headers = [_header(line) for line in lines[1:] if line]
    return ProxyRequest(parts[0].upper(), parts[1], parts[2], headers)


def _header(line: str) -> tuple[str, str]:
    name, sep, value = line.partition(":")
    if not sep or not name.strip():
        raise ProxyRefused(400, "the sandbox proxy could not parse a header line")
    return name.strip(), value.strip()


def _split_authority(authority: str, default_port: int) -> tuple[str, int]:
    host, port = authority, default_port
    match = re.match(r"^\[([0-9A-Fa-f:.]+)\](?::(\d+))?$", authority)
    if match:
        return match.group(1), int(match.group(2) or default_port)
    if authority.count(":") == 1:
        host, _, raw_port = authority.partition(":")
        port = int(raw_port) if raw_port.isdigit() else -1
    return host, port


def request_target(request: ProxyRequest) -> tuple[str, int, bool]:
    """``(host, port, is_connect)``; refuses anything but CONNECT or an ``http://`` URI."""
    if request.method == "CONNECT":
        host, port = _split_authority(request.target, 443)
        connect = True
    else:
        parts = urllib.parse.urlsplit(request.target)
        if parts.scheme.lower() != "http" or not parts.netloc:
            raise ProxyRefused(400, "the sandbox proxy takes CONNECT or absolute http:// URIs")
        host, port = _split_authority(parts.netloc.rpartition("@")[2], 80)
        connect = False
    if not (_HOST_RE.match(host) or _ip_literal(host)) or port <= 0:
        raise ProxyRefused(400, f"the sandbox proxy cannot use the target {request.target!r}")
    return host.lower().rstrip("."), port, connect


def origin_request(request: ProxyRequest) -> bytes:
    """The plain-HTTP request rewritten to origin-form, without proxy/hop-by-hop headers."""
    parts = urllib.parse.urlsplit(request.target)
    path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
    named = [v.lower() for k, v in request.headers if k.lower() == "connection"]
    dropped = _HOP_BY_HOP | {token.strip() for value in named for token in value.split(",")}
    kept = [(k, v) for k, v in request.headers if k.lower() not in dropped]
    lines = [f"{request.method} {path} {request.version}", *(f"{k}: {v}" for k, v in kept)]
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def response_bytes(status: int, body: str, extra: Iterable[str] = ()) -> bytes:
    payload = (body.rstrip("\n") + "\n").encode("utf-8")
    head = [
        f"HTTP/1.1 {status} {_STATUS_TEXT.get(status, 'Error')}",
        "Content-Type: text/plain; charset=utf-8",
        f"Content-Length: {len(payload)}",
        "Connection: close",
        *extra,
    ]
    return ("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + payload


def basic_credentials(nonce: str) -> str:
    token = base64.b64encode(f"{PROXY_USER}:{nonce}".encode()).decode("ascii")
    return f"Basic {token}"


# ── endpoint and environment ───────────────────────────────────────────────


@dataclass(frozen=True)
class ProxyEndpoint:
    """Where the proxy listens: a unix socket (Linux) or a loopback port + nonce (macOS)."""

    unix_path: str = ""
    port: int = 0
    nonce: str = ""

    def url(self) -> str:
        """The proxy URL a sandboxed process uses."""
        if self.unix_path:
            return f"http://127.0.0.1:{INSIDE_PROXY_PORT}"
        auth = f"{PROXY_USER}:{self.nonce}@" if self.nonce else ""
        return f"http://{auth}127.0.0.1:{self.port}"


def proxy_env(endpoint: ProxyEndpoint) -> dict[str, str]:
    """``HTTP(S)_PROXY``/``ALL_PROXY`` (both cases) and ``NO_PROXY`` for a child."""
    url = endpoint.url()
    env: dict[str, str] = {}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env[name] = url
        env[name.lower()] = url
    env["NO_PROXY"] = env["no_proxy"] = NO_PROXY_VALUE
    return env


# ── the server ──────────────────────────────────────────────────────────────


async def _default_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise ProxyRefused(502, f"the sandbox proxy could not resolve {host!r}") from exc
    return [str(info[4][0]) for info in infos]


async def _default_connector(
    address: str, port: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(address, port)


async def _pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    try:
        while data := await source.read(65536):
            sink.write(data)
            await sink.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        with contextlib.suppress(OSError, RuntimeError):
            if sink.can_write_eof():
                sink.write_eof()


def _close(writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(OSError, RuntimeError):
        writer.close()


class EgressProxy:
    """The per-context forward proxy (asyncio in a daemon thread)."""

    def __init__(
        self,
        web_access: Callable[[], WebAccess],
        *,
        unix_path: str = "",
        resolver: Resolver | None = None,
        connector: Connector | None = None,
    ) -> None:
        self._web = web_access
        self._unix_path = unix_path
        self._nonce = "" if unix_path else secrets.token_urlsafe(24)
        self._resolve = resolver or _default_resolver
        self._connect = connector or _default_connector
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._endpoint: ProxyEndpoint | None = None
        self._error: BaseException | None = None

    @property
    def endpoint(self) -> ProxyEndpoint | None:
        return self._endpoint

    def start(self) -> ProxyEndpoint:
        """Start once; the endpoint the children use."""
        with self._lock:
            if self._endpoint is None:
                self._endpoint = self._start_thread()
            return self._endpoint

    def _start_thread(self) -> ProxyEndpoint:
        ready = threading.Event()
        holder: list[ProxyEndpoint] = []
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, args=(ready, holder), name="r4-sandbox-proxy", daemon=True
        )
        self._thread.start()
        if not ready.wait(10) or not holder:
            raise RuntimeError(f"sandbox proxy did not start: {self._error!r}")
        return holder[0]

    def _run(self, ready: threading.Event, holder: list[ProxyEndpoint]) -> None:
        loop = self._loop
        assert loop is not None
        asyncio.set_event_loop(loop)
        try:
            server = loop.run_until_complete(self._open())
            holder.append(self._endpoint_of(server))
        except Exception as exc:  # noqa: BLE001 — reported by start()
            self._error = exc
            ready.set()
            loop.close()
            return
        ready.set()
        loop.run_forever()
        self._shutdown(loop, server)

    async def _open(self) -> asyncio.Server:
        if self._unix_path:
            server = await asyncio.start_unix_server(
                self._handle, path=self._unix_path, limit=HEADER_LIMIT
            )
            os.chmod(self._unix_path, 0o600)
            return server
        return await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=HEADER_LIMIT)

    def _endpoint_of(self, server: asyncio.Server) -> ProxyEndpoint:
        if self._unix_path:
            return ProxyEndpoint(unix_path=self._unix_path)
        port = int(server.sockets[0].getsockname()[1])
        return ProxyEndpoint(port=port, nonce=self._nonce)

    @staticmethod
    def _shutdown(loop: asyncio.AbstractEventLoop, server: asyncio.Server) -> None:
        server.close()
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        with contextlib.suppress(Exception):
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(server.wait_closed())
        loop.close()

    def stop(self) -> None:
        """Stop the loop and the thread (idempotent)."""
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        self._loop = self._thread = None
        self._endpoint = None
        if self._unix_path:
            with contextlib.suppress(OSError):
                os.unlink(self._unix_path)

    # ── one connection ──

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._serve(reader, writer)
        except ProxyRefused as exc:
            await self._refuse(writer, exc)
        except asyncio.TimeoutError:
            await self._refuse(writer, ProxyRefused(408, "request header timeout"))
        except asyncio.LimitOverrunError:
            await self._refuse(writer, ProxyRefused(431, "request header over 64 KiB"))
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
            pass
        finally:
            _close(writer)

    async def _refuse(self, writer: asyncio.StreamWriter, exc: ProxyRefused) -> None:
        extra = ['Proxy-Authenticate: Basic realm="r4"'] if exc.status == 407 else []
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            writer.write(response_bytes(exc.status, exc.reason, extra))
            await writer.drain()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT)
        request = parse_head(raw)
        self._check_auth(request)
        host, port, connect = request_target(request)
        refusal = decide(host, port, self._web())
        logger.info("sandbox proxy %s %s:%d", "deny" if refusal else "allow", host, port)
        if refusal:
            raise ProxyRefused(403, refusal)
        address = vet_addresses(await self._resolve(host, port), host)
        try:
            up_reader, up_writer = await asyncio.wait_for(
                self._connect(address, port), CONNECT_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise ProxyRefused(502, f"the sandbox proxy could not reach {host!r}") from exc
        if connect:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        else:
            up_writer.write(origin_request(request))
        try:
            await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
        finally:
            _close(up_writer)

    def _check_auth(self, request: ProxyRequest) -> None:
        if not self._nonce:
            return
        given = request.header("Proxy-Authorization")
        if not hmac.compare_digest(given.encode(), basic_credentials(self._nonce).encode()):
            raise ProxyRefused(407, "the sandbox proxy needs its session credentials")
