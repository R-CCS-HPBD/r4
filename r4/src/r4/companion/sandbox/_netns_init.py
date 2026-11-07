"""In-namespace proxy bridge for the Linux sandbox (ADR-101 D-6). Stdlib only.

Runs INSIDE the bubblewrap network namespace, where only ``lo`` exists:
listens on ``127.0.0.1:<port>``, splices every connection to R4's egress
proxy over the bind-mounted unix socket, then runs the command with inherited
stdio, forwards SIGTERM/SIGINT/SIGHUP to it and exits with its return code
(``128 + signal`` when a signal ended it).

Usage: ``python -I _netns_init.py <unix socket> <port> -- CMD ARGS...``

It must not import r4: it runs under ``python -I`` from its file path.
"""

from __future__ import annotations

import signal
import socket
import subprocess
import sys
import threading

_FORWARDED = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def _pump(source: socket.socket, sink: socket.socket) -> None:
    try:
        while data := source.recv(65536):
            sink.sendall(data)
    except OSError:
        pass
    finally:
        try:
            sink.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _bridge(client: socket.socket, unix_path: str) -> None:
    upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        upstream.connect(unix_path)
    except OSError:
        client.close()
        upstream.close()
        return
    back = threading.Thread(target=_pump, args=(upstream, client), daemon=True)
    back.start()
    _pump(client, upstream)
    back.join()
    client.close()
    upstream.close()


def _serve(listener: socket.socket, unix_path: str) -> None:
    while True:
        try:
            client, _ = listener.accept()
        except OSError:
            return
        threading.Thread(target=_bridge, args=(client, unix_path), daemon=True).start()


def _listen(port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(64)
    return listener


def _exit_code(returncode: int) -> int:
    return 128 - returncode if returncode < 0 else returncode


def _run(command: list[str]) -> int:
    child = subprocess.Popen(command)

    def forward(signum: int, _frame: object) -> None:
        if child.poll() is None:
            child.send_signal(signum)

    for signum in _FORWARDED:
        signal.signal(signum, forward)
    return _exit_code(child.wait())


def _parse(argv: list[str]) -> tuple[str, int, list[str]]:
    if len(argv) < 4 or argv[2] != "--":
        raise SystemExit("usage: _netns_init.py <unix socket> <port> -- CMD ARGS...")
    return argv[0], int(argv[1]), argv[3:]


def main(argv: list[str] | None = None) -> int:
    unix_path, port, command = _parse(sys.argv[1:] if argv is None else argv)
    listener = _listen(port)
    threading.Thread(target=_serve, args=(listener, unix_path), daemon=True).start()
    try:
        return _run(command)
    except OSError as exc:
        print(f"r4 sandbox: cannot run {command[0]!r}: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main())
