"""Helpers shared by anthropic_bridge.py and openai_passthrough.py.

- load_shim():        the repo shim's own resolver (rikyu URL + key resolved in-process;
                      the key is never printed, logged, written or put on a command line)
- listen():           serve a Starlette app on a UNIX socket (mode 0600, inside the 0700
                      run/ dir) or, for debugging only, on 127.0.0.1:<port>
- case_tag():         the bench case label (x-bench-case header, or /case/<tag>/ path prefix)
- sanitize_error():   upstream error body -> short message with anything key-like redacted
- tool_names(), count_reasoning(): request-shape facts for the per-request log line
- SSEDataLines:       upstream SSE bytes -> the JSON object of each complete ``data:`` line

Log lines are ``key=value`` with no spaces inside values, one START and one END line
per request (run_harness.proxy_usage pairs them by ``#rid``).
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import re
import socket
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import paths as P  # the code dir is on sys.path (scripts run from it)

REPO = P.REPO
SHIM_PATH = REPO / "benchmarks/harness/anthropic_shim.py"
RETRY_DELAYS = (0, 2, 5, 10, 20, 40)


def load_shim():
    sys.dont_write_bytecode = True  # never drop a .pyc into the repo checkout
    spec = importlib.util.spec_from_file_location("r4bench_anthropic_shim", SHIM_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class Logger:
    def __init__(self, prefix: str):
        self.prefix = prefix

    def __call__(self, msg: str) -> None:
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {self.prefix} {msg}", flush=True)


_TAG_RX = re.compile(r"[^A-Za-z0-9_./:=-]")


def case_tag(request: Any) -> str:
    """Bench case label (our own, not a secret): x-bench-case header, else /case/<tag>/ path."""
    raw = request.headers.get("x-bench-case", "") or request.path_params.get("tag", "")
    tag = _TAG_RX.sub("_", raw)[:160]
    return tag or "-"


_NAME_RX = re.compile(r"[^A-Za-z0-9_.:-]")


def clean_name(name: Any) -> str:
    return _NAME_RX.sub("_", str(name or "?"))[:64] or "?"


def tool_names(tools: list[dict[str, Any]] | None) -> str:
    """Comma list of the advertised tool names (names are not secret)."""
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else None
        out.append(clean_name((fn or t).get("name")))
    return ",".join(out) or "-"


def count_reasoning(messages: list[dict[str, Any]] | None) -> tuple[int, int]:
    """(assistant messages, assistant messages carrying reasoning) — counts only, no content."""
    asst = carried = 0
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        asst += 1
        r = m.get("reasoning_content") or m.get("reasoning")
        if isinstance(r, str) and r.strip():
            carried += 1
    return asst, carried


_REDACTIONS = (
    (re.compile(r"(?i)bearer\s+[^\s\"',]+"), "Bearer [redacted]"),
    (
        re.compile(r"(?i)(api[_-]?key|token|secret|authorization)([\"'\s:=]+)[^\s\"',}]+"),
        r"\1\2[redacted]",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_.*-]{3,}"), "[redacted]"),
    (re.compile(r"[A-Za-z0-9_-]*\*{3,}[A-Za-z0-9_*-]*"), "[redacted]"),
    (re.compile(r"[A-Za-z0-9_+/=-]{32,}"), "[redacted]"),
)


def _error_message(text: str) -> Any:
    """The message of a JSON error body (OpenAI / Anthropic / FastAPI shapes); else the text."""
    try:
        j = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
    if not isinstance(j, dict):
        return text
    err = j.get("error")
    if isinstance(err, dict):
        return err.get("message") or err.get("type") or ""
    if isinstance(err, str):
        return err
    return j.get("message") or j.get("detail") or ""


def sanitize_error(raw: bytes | str, limit: int = 500) -> str:
    """Upstream error body -> its message with anything key-like redacted, truncated."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    msg = str(_error_message(text) or "")
    for rx, rep in _REDACTIONS:
        msg = rx.sub(rep, msg)
    return msg.replace("\n", " ")[:limit]


def _sse_data_json(line: bytes) -> dict[str, Any] | None:
    """One SSE line -> the JSON object of a ``data:`` line; None for anything else."""
    line = line.strip()
    if not line.startswith(b"data:"):
        return None
    data = line[5:].strip()
    if not data or data == b"[DONE]":
        return None
    try:
        j = json.loads(data)
    except json.JSONDecodeError:
        return None
    return j if isinstance(j, dict) else None


class SSEDataLines:
    """Line buffer over raw upstream SSE bytes (chunks may split lines anywhere).

    ``feed(chunk)`` lazily yields the JSON object of each completed ``data:`` line;
    comments, other fields, ``[DONE]`` and non-object payloads are skipped.
    """

    def __init__(self) -> None:
        self.buf = b""

    def feed(self, chunk: bytes) -> Iterator[dict[str, Any]]:
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            j = _sse_data_json(line)
            if j is not None:
                yield j


def apply_default_temperature(payload: dict[str, Any], default: float | None) -> str:
    """Set ``temperature`` only when the client sent none. Returns the source label."""
    if payload.get("temperature") is not None:
        return "client"
    if default is None:
        return "server_default"
    payload["temperature"] = default
    return "proxy_default"


def unix_listener(path: str) -> socket.socket:
    """Bind a UNIX stream socket with mode 0600 (the run/ dir itself is 0700)."""
    p = Path(path)
    if p.exists() or p.is_symlink():
        p.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        sock.bind(path)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    sock.listen(256)
    return sock


def listen(app: Any, *, uds: str | None, port: int | None) -> None:
    import uvicorn

    if uds:
        sock = unix_listener(uds)
        try:
            uvicorn.run(app, fd=sock.fileno(), log_level="warning", access_log=False)
        finally:
            with contextlib.suppress(OSError):
                Path(uds).unlink()
        return
    uvicorn.run(app, host="127.0.0.1", port=int(port or 0), log_level="warning", access_log=False)
