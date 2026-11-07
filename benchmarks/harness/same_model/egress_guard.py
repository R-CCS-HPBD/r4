#!/usr/bin/env python3
"""Deny-all logging HTTP(S) proxy: an egress ATTEMPT LOGGER for the bench.

The enforcement is the sandbox's own network namespace (sandbox.py:
--unshare-net, loopback only). This guard only records what a harness TRIED
to reach: harness processes run with HTTPS_PROXY/HTTP_PROXY=127.0.0.1:8339 and
NO_PROXY=127.0.0.1,localhost; the in-sandbox relay forwards 8339 to a
per-case UNIX socket served by ``CaseGuard`` inside the runner process, so
every ``DENY host:port`` is attributed to exactly one case (no time-window
guessing under --par > 1). Clients that ignore the proxy env never reach
anything (no route) and are not logged here.

Logs only method, target host:port and the case tag — never other headers or bodies.

  in-process (runner):  with CaseGuard(sock_path, tag, log_path) as g: ...; g.denials
  standalone (debug):   python egress_guard.py --uds PATH | --port 8339
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import socketserver
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

LOG_PREFIX = "[egress-guard]"
_LOG_LOCK = threading.Lock()
_SAFE = re.compile(r"[^A-Za-z0-9_.:\[\]()=, -]")


def _parse_target(line: bytes) -> tuple[str, str]:
    parts = line.decode("latin-1", "replace").split()
    if len(parts) < 2:
        return "?", "?"
    method, target = parts[0].upper()[:16], parts[1]
    if method == "CONNECT":
        host = target
    else:
        u = urlsplit(target)
        host = (
            f"{u.hostname}:{u.port or (443 if u.scheme == 'https' else 80)}"
            if u.hostname
            else "(direct request, no proxy target)"
        )
    return _SAFE.sub("_", method), _SAFE.sub("_", host)[:200]


class _Handler(socketserver.StreamRequestHandler):
    timeout = 10

    def handle(self) -> None:
        try:
            line = self.rfile.readline(8192)
            if not line.strip():
                return  # bare connect (port probe): nothing attempted
            method, host = _parse_target(line)
            for _ in range(200):  # drain headers; none are kept
                h = self.rfile.readline(8192)
                if h in (b"\r\n", b"\n", b""):
                    break
            self.server.record(method, host)  # type: ignore[attr-defined]
            self.wfile.write(
                b"HTTP/1.1 403 Forbidden\r\ncontent-length: 0\r\nconnection: close\r\n\r\n"
            )
        except OSError:
            pass


class _Recorder:
    tag: str
    log_path: Path | None
    denials: list[str]

    def record(self, method: str, host: str) -> None:
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {LOG_PREFIX} DENY {method} {host} case={self.tag}"
        with _LOG_LOCK:
            self.denials.append(f"{method} {host}")
            if self.log_path is not None:
                with open(self.log_path, "a") as f:
                    f.write(line + "\n")
            else:
                print(line, flush=True)


class CaseGuard(_Recorder, socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Per-case deny-all proxy on a UNIX socket (mode 0600), served from a daemon thread."""

    daemon_threads = True

    def __init__(self, sock_path: Path, tag: str, log_path: Path | None):
        self.sock_path = Path(sock_path)
        if self.sock_path.exists() or self.sock_path.is_symlink():
            self.sock_path.unlink()
        self.tag, self.log_path, self.denials = tag, log_path, []
        # no umask games: umask is process-wide and the runner is multi-threaded; the socket's
        # parent run/ is 0700, so the brief default mode is not reachable by other users
        socketserver.UnixStreamServer.__init__(self, str(self.sock_path), _Handler)
        os.chmod(self.sock_path, 0o600)
        self._thread = threading.Thread(
            target=self.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True
        )

    def __enter__(self) -> CaseGuard:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()
        self.server_close()
        with contextlib.suppress(OSError):
            self.sock_path.unlink()


class _TcpGuard(_Recorder, socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--uds")
    g.add_argument("--port", type=int)
    ap.add_argument("--tag", default="-")
    a = ap.parse_args()
    if a.uds:
        srv: socketserver.BaseServer = CaseGuard(Path(a.uds), a.tag, None)
        where = f"unix:{a.uds}"
    else:
        srv = _TcpGuard(("127.0.0.1", a.port), _Handler)
        srv.tag, srv.log_path, srv.denials = a.tag, None, []  # type: ignore[attr-defined]
        where = f"127.0.0.1:{a.port}"
    print(
        f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {LOG_PREFIX} listening on {where} (deny-all)",
        flush=True,
    )
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
