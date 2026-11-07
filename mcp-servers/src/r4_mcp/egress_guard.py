"""EgressGuard — minimal allowlisting forward proxy for mini-RiVault (ADR-040).

Runs inside the platform's own ``r4-mcp`` image (``python -m r4_mcp.egress_guard``)
so no external image or registry is involved. Standard library only.

Policy (fail-closed):

- ``EGRESS_DOMAIN_ALLOWLIST`` (comma-separated, exact host match). An empty
  allowlist denies every destination.
- Private, loopback, link-local and unspecified destinations are always
  denied: the proxy sits on the private per-job network next to LiteLLM,
  vLLM and Postgres.
- ``CONNECT host:port`` tunnels (HTTPS) and absolute-URI plain-HTTP requests
  are supported. Anything else is rejected; ``GET /healthz`` answers locally.
- Every decision is one JSON line in ``$EGRESS_AUDIT_LOG_PATH/egress-audit.jsonl``
  and in the process log.

There is no body scanning: a CONNECT tunnel is opaque by design and this
proxy never terminates TLS.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger("r4_mcp.egress_guard")

DEFAULT_PORT = 8888
MAX_HEAD_BYTES = 64 * 1024
PIPE_CHUNK = 65536
LINGER_SECONDS = 1.0
AUDIT_FILE_NAME = "egress-audit.jsonl"

DENY_EMPTY_ALLOWLIST = "egress denied: allowlist is empty (fail-closed)"
DENY_NOT_ALLOWED = "egress denied: domain not in allowlist"
DENY_PRIVATE = "egress denied: private or loopback destination"
DENY_MALFORMED = "egress denied: malformed proxy request"
DENY_UNSUPPORTED = "egress denied: unsupported request (use CONNECT or an absolute http:// URI)"

_STATUS_TEXT = {200: "OK", 400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}
_HOP_HEADERS = frozenset({"proxy-connection", "proxy-authorization", "connection", "keep-alive"})
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal")


# ── policy ───────────────────────────────────────────────────────────────────


def parse_allowlist(raw: str | None) -> frozenset[str]:
    """Comma-separated hosts → normalized set (lower-case, no trailing dot)."""
    return frozenset(
        part.strip().lower().rstrip(".") for part in (raw or "").split(",") if part.strip()
    )


def _is_private_address(value: str) -> bool | None:
    """True/False for IP literals, ``None`` when ``value`` is not an IP."""
    try:
        ip = ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return None
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_reserved
    )


async def _is_private_host(host: str) -> bool:
    """Private/loopback check for literals and for what a name resolves to."""
    literal = _is_private_address(host)
    if literal is not None:
        return literal
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False  # unresolvable: the connect attempt fails on its own
    return any(_is_private_address(str(info[4][0])) for info in infos)


@dataclass(frozen=True)
class EgressPolicy:
    """Destination policy. Fail-closed by construction."""

    allowlist: frozenset[str]
    deny_private: bool = True
    connect_timeout: float = 10.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EgressPolicy:
        env = os.environ if env is None else env
        return cls(
            allowlist=parse_allowlist(env.get("EGRESS_DOMAIN_ALLOWLIST")),
            deny_private=True,
            connect_timeout=float(env.get("EGRESS_CONNECT_TIMEOUT_SEC", "10")),
        )

    async def denial(self, host: str) -> str | None:
        """Reason to deny ``host``, or ``None`` when it is allowed."""
        normalized = host.lower().rstrip(".")
        if self.deny_private and await _is_private_host(normalized):
            return DENY_PRIVATE
        if not self.allowlist:
            return DENY_EMPTY_ALLOWLIST
        if normalized not in self.allowlist:
            return DENY_NOT_ALLOWED
        return None


# ── audit ────────────────────────────────────────────────────────────────────


@dataclass
class AuditSink:
    """One JSON line per decision (file + process log)."""

    path: Path | None
    instance: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AuditSink:
        env = os.environ if env is None else env
        directory = env.get("EGRESS_AUDIT_LOG_PATH", "/var/log/egress-guard").strip()
        path: Path | None = None
        if directory:
            try:
                Path(directory).mkdir(parents=True, exist_ok=True)
                path = Path(directory) / AUDIT_FILE_NAME
            except OSError as exc:
                logger.warning("egress_guard: audit directory unusable (%s): %s", directory, exc)
        instance = {
            key.lower(): env.get(key, "unknown") for key in ("INSTANCE_ID", "OWNER_ID", "TIER")
        }
        return cls(path=path, instance=instance)

    def record(self, **fields: object) -> dict[str, object]:
        entry: dict[str, object] = {"ts": round(time.time(), 3), **self.instance, **fields}
        line = json.dumps(entry, separators=(",", ":"), sort_keys=True)
        logger.info("egress_audit %s", line)
        if self.path is not None:
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as exc:
                logger.warning("egress_guard: audit write failed: %s", exc)
        return entry


# ── request parsing ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProxyRequest:
    """Parsed request head. ``host == ''`` means an origin-form (local) request."""

    method: str
    target: str
    version: str
    headers: tuple[tuple[str, str], ...]
    host: str
    port: int
    path: str


def _split_host_port(authority: str, default_port: int) -> tuple[str, int]:
    host, sep, port = authority.rpartition(":")
    if sep and port.isdigit() and (host.endswith("]") or ":" not in host):
        return host.strip("[]").lower(), int(port)
    return authority.strip("[]").lower(), default_port  # no port, or bare IPv6 literal


def _parse_headers(lines: list[str]) -> tuple[tuple[str, str], ...] | None:
    headers: list[tuple[str, str]] = []
    for line in lines:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep or not name.strip():
            return None
        headers.append((name.strip(), value.strip()))
    return tuple(headers)


def _absolute_uri_destination(target: str) -> tuple[str, int, str] | None:
    try:
        parts = urlsplit(target)
        host, port = (parts.hostname or "").lower(), parts.port or 80
    except ValueError:
        return None
    if not host:
        return None
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return host, port, path


def parse_request_head(head: bytes) -> ProxyRequest | None:
    """Parse a request head; ``None`` when it is not a usable proxy request."""
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/"):
        return None
    method, target, version = parts[0].upper(), parts[1], parts[2]
    headers = _parse_headers(lines[1:])
    if headers is None:
        return None
    if method == "CONNECT":
        host, port = _split_host_port(target, default_port=443)
        return ProxyRequest(method, target, version, headers, host, port, "") if host else None
    if target.startswith("http://"):
        destination = _absolute_uri_destination(target)
        if destination is None:
            return None
        return ProxyRequest(method, target, version, headers, *destination)
    if target.startswith("/"):
        return ProxyRequest(method, target, version, headers, "", 0, target)
    return None


def _origin_form_head(request: ProxyRequest) -> bytes:
    """Rewrite an absolute-URI request into origin form for the upstream."""
    lines = [f"{request.method} {request.path} {request.version}"]
    has_host = False
    for key, value in request.headers:
        lower = key.lower()
        if lower in _HOP_HEADERS:
            continue
        has_host = has_host or lower == "host"
        lines.append(f"{key}: {value}")
    if not has_host:
        suffix = "" if request.port == 80 else f":{request.port}"
        lines.append(f"Host: {request.host}{suffix}")
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


# ── byte plumbing ────────────────────────────────────────────────────────────


async def _respond(writer: asyncio.StreamWriter, status: int, body: str) -> None:
    payload = body.encode("utf-8")
    head = (
        f"HTTP/1.1 {status} {_STATUS_TEXT.get(status, 'Error')}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Connection: close\r\n\r\n"
    )
    writer.write(head.encode("latin-1") + payload)
    await writer.drain()


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


async def _pipe(
    src: asyncio.StreamReader, dst: asyncio.StreamWriter, stats: list[int], index: int
) -> None:
    try:
        while True:
            chunk = await src.read(PIPE_CHUNK)
            if not chunk:
                break
            dst.write(chunk)
            await dst.drain()
            stats[index] += len(chunk)
        if dst.can_write_eof():
            dst.write_eof()
    except (ConnectionError, OSError):
        return


async def _pump(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    up_reader: asyncio.StreamReader,
    up_writer: asyncio.StreamWriter,
) -> tuple[int, int]:
    """Copy bytes both ways until one side finishes; returns (up, down) byte counts."""
    stats = [0, 0]
    tasks = [
        asyncio.create_task(_pipe(reader, up_writer, stats, 0)),
        asyncio.create_task(_pipe(up_reader, writer, stats, 1)),
    ]
    try:
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if pending:
            await asyncio.wait(pending, timeout=LINGER_SECONDS)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await _close(up_writer)
    return stats[0], stats[1]


def _peer(writer: asyncio.StreamWriter) -> str:
    info = writer.get_extra_info("peername")
    return f"{info[0]}:{info[1]}" if info else "?"


def _request_fields(request: ProxyRequest | None) -> dict[str, object]:
    if request is None:
        return {}
    return {
        "method": request.method,
        "host": request.host,
        "port": request.port,
        "target": request.target[:200],
    }


# ── server ───────────────────────────────────────────────────────────────────


class EgressGuard:
    """Connection handler: parse → policy → tunnel/forward → audit."""

    def __init__(self, policy: EgressPolicy, audit: AuditSink) -> None:
        self.policy = policy
        self.audit = audit

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = _peer(writer)
        try:
            request = await self._read_request(reader)
            if request is None:
                await self._reject(writer, 400, DENY_MALFORMED, peer, None)
            elif not request.host:
                await self._serve_local(request, writer, peer)
            else:
                await self._dispatch(request, reader, writer, peer)
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            await _close(writer)

    @staticmethod
    async def _read_request(reader: asyncio.StreamReader) -> ProxyRequest | None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, ValueError):
            return None
        return parse_request_head(head)

    async def _dispatch(
        self,
        request: ProxyRequest,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer: str,
    ) -> None:
        reason = await self.policy.denial(request.host)
        if reason is not None:
            await self._reject(writer, 403, reason, peer, request)
        elif request.method == "CONNECT":
            await self._relay(request, reader, writer, peer, mode="connect")
        else:
            await self._relay(request, reader, writer, peer, mode="http")

    async def _serve_local(
        self, request: ProxyRequest, writer: asyncio.StreamWriter, peer: str
    ) -> None:
        if request.method == "GET" and request.path in ("/healthz", "/health"):
            await _respond(writer, 200, "ok")
            return
        await self._reject(writer, 400, DENY_UNSUPPORTED, peer, request)

    async def _reject(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        reason: str,
        peer: str,
        request: ProxyRequest | None,
    ) -> None:
        self.audit.record(
            action="denied", status=status, reason=reason, client=peer, **_request_fields(request)
        )
        await _respond(writer, status, reason)

    async def _relay(
        self,
        request: ProxyRequest,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer: str,
        *,
        mode: str,
    ) -> None:
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(request.host, request.port),
                timeout=self.policy.connect_timeout,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            reason = f"egress upstream connect failed: {type(exc).__name__}"
            await self._reject(writer, 502, reason, peer, request)
            return
        if mode == "connect":
            writer.write(f"{request.version} 200 Connection Established\r\n\r\n".encode("latin-1"))
            await writer.drain()
        else:
            up_writer.write(_origin_form_head(request))
            await up_writer.drain()
        sent, received = await _pump(reader, writer, up_reader, up_writer)
        self.audit.record(
            action="allowed",
            mode=mode,
            client=peer,
            bytes_up=sent,
            bytes_down=received,
            **_request_fields(request),
        )


async def serve(guard: EgressGuard, host: str, port: int) -> None:
    server = await asyncio.start_server(guard.handle, host, port, limit=MAX_HEAD_BYTES)
    async with server:
        await server.serve_forever()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="r4_mcp.egress_guard",
        description="Allowlisting forward proxy for mini-RiVault (ADR-040, fail-closed).",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("EGRESS_PROXY_PORT", DEFAULT_PORT))
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    policy = EgressPolicy.from_env()
    audit = AuditSink.from_env()
    described = (
        ",".join(sorted(policy.allowlist)) or "EMPTY (fail-closed: every destination is denied)"
    )
    logger.info("egress_guard: listening on %s:%d allowlist=%s", args.host, args.port, described)
    asyncio.run(serve(EgressGuard(policy, audit), args.host, args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
