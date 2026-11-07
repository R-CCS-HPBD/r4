"""Host-side model proxy for the Terminal-Bench lane (2026-10-04).

Keeps the public source's API key on the bench host; the lane's default
route since 2026-10-05 (without it, only ``--ak copy_source_key=true`` copies
the source entry, key included, into every task container, where the agent's
own shell commands and the task's code can read it). The container's
``sources.json`` names this proxy and a per-run bearer token
(``support.proxied_source``); the key is resolved here, in-process, from the
host's ``~/.config/r4/sources.json`` and never written anywhere.

Forwards ONE request shape for ONE model:

- ``POST /v1/chat/completions`` for ``--model`` only. Always sent upstream as
  a stream (rikyu's gateway cuts a request that sends no byte for 600 s; long
  reasoning turns exceed that). A non-streaming request gets the assembled
  reply; a streaming one (R4 since ADR-087) gets the SSE chunks as they arrive,
  and the upstream is closed as soon as the client goes away (2026-10-05: an
  orphaned request otherwise ran on for 79 min);
- ``GET /v1/models`` listing that model.

Every request must carry the per-run token and come from a private docker
address (172.16.0.0/12, 10.0.0.0/8, 192.168.0.0/16 or loopback). Each
forwarded request appends one usage line (no prompt or answer text) to
``--log`` (JSONL): time, peer, status, latency, prompt/completion/cached
tokens, finish reason, tool calls, time to first token and ``decode_tps``
(completion tokens per second after the first token).

A streaming row also counts the characters relayed to the client
(``relayed_chars``: reasoning, content, tool-call arguments). When the client
goes away mid-stream (status 499, ``cut: true``) or the upstream sends no usage,
the row carries ``usage_estimated.completion_tokens`` = ceil(relayed chars / 4),
so a trial's token total includes the rounds R4 cut (B1, 2026-10-24: 157 cut
rounds were logged without usage before).

    .venv/bin/python benchmarks/harness/terminal/model_proxy.py \
        --source rikyu --model glm-5.3 --port 8333 --token "$R4BENCH_MODEL_TOKEN" \
        --log benchmarks/results/terminal-bench/<run>/model-proxy.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from benchmarks.harness.terminal.nimble_proxy import allowed_peer
from benchmarks.harness.terminal.support import (
    DEFAULT_MODEL_PROXY_PORT,
    HOST_SOURCES_PATH,
    _pick_source,
)

logger = logging.getLogger("model_proxy")

#: Characters per token for the cut-round estimate (the rule of thumb R4 uses).
CHARS_PER_TOKEN = 4
#: Status logged when the streaming client went away before the stream ended.
CLIENT_CUT_STATUS = 499


def check_chat_payload(payload: Any, model: str) -> str | None:
    """Why a chat body is refused, or None when it may be forwarded."""
    if not isinstance(payload, dict):
        return "body must be a JSON object"
    requested = str(payload.get("model") or "").rsplit("@", 1)[0]
    if requested != model:
        return f"only {model} is served here"
    return None


def usage_record(peer: str, status: int, dt: float, body: Any) -> dict[str, Any]:
    """One log line: counts only, never message text."""
    body = body if isinstance(body, dict) else {}
    usage = body.get("usage") or {}
    choice = (body.get("choices") or [{}])[0] or {}
    message = choice.get("message") or {}
    details = usage.get("prompt_tokens_details") or {}
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "peer": peer,
        "status": status,
        "dt_s": round(dt, 2),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cached_tokens": details.get("cached_tokens"),
        "finish": choice.get("finish_reason"),
        "tool_calls": len(message.get("tool_calls") or []),
        "ttft_s": round(body["_ttft_s"], 1) if body.get("_ttft_s") is not None else None,
        "decode_tps": decode_tps(usage.get("completion_tokens"), dt, body.get("_ttft_s")),
    }


def decode_tps(tokens: Any, dt: float, ttft: Any) -> float | None:
    """Completion tokens per second after the first token, or None when unknown."""
    if not isinstance(tokens, int) or ttft is None or dt - float(ttft) <= 0:
        return None
    return round(tokens / (dt - float(ttft)), 1)


def stream_fields(state: StreamState | None, status: int, record: dict[str, Any]) -> dict[str, Any]:
    """B1 fields for a streaming row: relayed chars, the cut flag, and a token
    estimate when the upstream sent no usage (always on a cut)."""
    if state is None:
        return {}
    chars = state.relayed_chars()
    fields: dict[str, Any] = {"relayed_chars": chars}
    if status == CLIENT_CUT_STATUS:
        fields["cut"] = True
    if record.get("completion_tokens") is None:
        estimate = math.ceil(sum(chars.values()) / CHARS_PER_TOKEN)
        fields["usage_estimated"] = {"completion_tokens": estimate}
        fields["ttft_s"] = round(state.ttft, 1) if state.ttft is not None else None
        fields["decode_tps"] = decode_tps(estimate, record["dt_s"], state.ttft)
    return fields


class Upstream:
    """The public source as the host knows it; the key stays in this object."""

    def __init__(self, source: str, read_timeout: float) -> None:
        entry = _pick_source(json.loads(HOST_SOURCES_PATH.read_text()), source)
        self.url = str(entry["url"]).rstrip("/") + "/chat/completions"
        self._headers = {"Content-Type": "application/json"}
        if entry.get("api_key"):
            self._headers["Authorization"] = f"Bearer {entry['api_key']}"
        self._timeout = read_timeout
        self._local = threading.local()

    def _client(self):
        if getattr(self._local, "client", None) is None:
            import httpx

            self._local.client = httpx.Client(timeout=httpx.Timeout(self._timeout, connect=30.0))
        return self._local.client

    def _stream_body(self, payload: dict[str, Any]) -> dict[str, Any]:
        options = dict(payload.get("stream_options") or {}, include_usage=True)
        return dict(payload, stream=True, stream_options=options)

    def relay(
        self, payload: dict[str, Any], start: Any, write: Any, state: StreamState | None = None
    ) -> tuple[int, Any]:
        """Streaming client: pass each upstream SSE line through as it arrives.

        ``start(status, content_type)`` sends the response head once; ``write``
        sends one line and raises OSError when the client is gone, which ends
        the upstream request too. ``state`` (the caller's, so a cut stream's
        partial counts survive the OSError) accumulates the relayed chunks.
        Returns (status, assembled body for the log).
        """
        state = state if state is not None else StreamState()
        body = self._stream_body(payload)
        began = time.monotonic()  # rikyu sends headers only once tokens flow
        with self._client().stream("POST", self.url, json=body, headers=self._headers) as resp:
            if resp.status_code != 200:
                data = resp.read()
                start(resp.status_code, "application/json")
                write(data.decode(errors="replace"))
                return resp.status_code, None
            start(200, "text/event-stream")
            for line in resp.iter_lines():
                if state.ttft is None and line.startswith("data:"):
                    state.ttft = time.monotonic() - began  # queueing shows up here
                state.feed_line(line)
                write(line)
        return (502 if state.error is not None else 200), state.assemble()

    def post(self, payload: dict[str, Any]) -> tuple[int, bytes]:
        """Forward a non-streaming request as a STREAMING one and assemble the
        reply. rikyu's gateway answers 502 after 600 s when no byte has been
        sent; a long glm-5.3 reasoning turn (measured 2026-10-04: ~125 chars/s,
        steady, past 120 s on a TB 4.0 first turn) needs longer than that.
        Streaming keeps bytes flowing; the client still gets one JSON body."""
        body = self._stream_body(payload)
        with self._client().stream("POST", self.url, json=body, headers=self._headers) as resp:
            if resp.status_code != 200:
                return resp.status_code, resp.read()
            state = StreamState()
            for line in resp.iter_lines():
                state.feed_line(line)
        if state.error is not None:
            return 502, json.dumps({"error": state.error}).encode()
        return 200, json.dumps(state.assemble()).encode()


class StreamState:
    """Accumulates OpenAI chat-completion stream chunks into one completion."""

    def __init__(self) -> None:
        self.meta: dict[str, Any] = {}
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.finish: str | None = None
        self.usage: dict[str, Any] | None = None
        self.error: Any = None
        self.ttft: float | None = None

    def feed_line(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        data = line[5:].strip()
        if not data or data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except ValueError:
            return
        self.feed(chunk)

    def feed(self, chunk: dict[str, Any]) -> None:
        if chunk.get("error") is not None:
            self.error = chunk["error"]
            return
        for key in ("id", "created", "model"):
            self.meta.setdefault(key, chunk.get(key))
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            self._feed_delta(choice.get("delta") or {})
            self.finish = choice.get("finish_reason") or self.finish

    def _feed_delta(self, delta: dict[str, Any]) -> None:
        self.content.append(delta.get("content") or "")
        self.reasoning.append(delta.get("reasoning_content") or delta.get("reasoning") or "")
        for call in delta.get("tool_calls") or []:
            self._feed_tool_call(call)

    def _feed_tool_call(self, call: dict[str, Any]) -> None:
        slot = self.tool_calls.setdefault(
            int(call.get("index") or 0),
            {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
        )
        slot["id"] = call.get("id") or slot["id"]
        fn = call.get("function") or {}
        slot["function"]["name"] += fn.get("name") or ""
        slot["function"]["arguments"] += fn.get("arguments") or ""

    def relayed_chars(self) -> dict[str, int]:
        """Characters received so far (counts only, never the text)."""
        return {
            "reasoning": sum(map(len, self.reasoning)),
            "content": sum(map(len, self.content)),
            "tool_args": sum(len(c["function"]["arguments"]) for c in self.tool_calls.values()),
        }

    def assemble(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self.content) or None}
        reasoning = "".join(self.reasoning)
        if reasoning:
            message["reasoning_content"] = reasoning
        if self.tool_calls:
            message["tool_calls"] = [self.tool_calls[i] for i in sorted(self.tool_calls)]
        return {
            "id": self.meta.get("id"),
            "object": "chat.completion",
            "created": self.meta.get("created"),
            "model": self.meta.get("model"),
            "choices": [{"index": 0, "message": message, "finish_reason": self.finish}],
            "usage": self.usage,
            "_ttft_s": self.ttft,
        }


class UsageLog:
    def __init__(self, path: Path | None, dump_dir: Path | None = None) -> None:
        self._path = path
        self._dump_dir = dump_dir
        self._lock = threading.Lock()
        self._seq = 0
        self.stats = {"forwarded": 0, "refused": 0, "errors": 0}

    def dump(self, peer: str, payload: dict[str, Any]) -> None:
        """--dump-dir: keep each request body to replay it later (debugging only;
        bodies hold the task's prompts and tool output — public benchmark data)."""
        if self._dump_dir is None:
            return
        with self._lock:
            self._seq += 1
            seq = self._seq
        name = f"{time.strftime('%Y%m%dT%H%M%S')}-{peer}-{seq:05d}.json"
        (self._dump_dir / name).write_text(json.dumps(payload))

    def write(self, record: dict[str, Any]) -> None:
        if self._path is None:
            return
        with self._lock, self._path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")


def apply_default_max_tokens(payload: dict[str, Any], default: int) -> bool:
    """Set max_tokens when the client sent no output limit (rikyu then caps a
    reply at 32,768 tokens, which a long glm-5.3 reasoning turn exhausts with
    no answer). Returns whether it was applied."""
    if not default or payload.get("max_tokens") or payload.get("max_completion_tokens"):
        return False
    payload["max_tokens"] = int(default)
    return True


def make_handler(
    upstream: Upstream, model: str, token: str, log: UsageLog, default_max_tokens: int = 0
):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet: usage lines go to --log
            pass

        def _send(self, code: int, data: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _refuse(self, code: int, reason: str) -> None:
            log.stats["refused"] += 1
            self._send(code, json.dumps({"error": reason}).encode())

        def _authorized(self) -> bool:
            return allowed_peer(self.client_address[0]) and (
                self.headers.get("Authorization", "") == f"Bearer {token}"
            )

        def do_GET(self):  # noqa: N802 - http.server API
            if not self._authorized():
                return self._refuse(403, "forbidden")
            if self.path.rstrip("/") != "/v1/models":
                return self._refuse(404, "not found")
            body = {"object": "list", "data": [{"id": model, "object": "model"}]}
            return self._send(200, json.dumps(body).encode())

        def do_POST(self):  # noqa: N802 - http.server API
            if not self._authorized():
                return self._refuse(403, "forbidden")
            if self.path.rstrip("/") != "/v1/chat/completions":
                return self._refuse(404, "not found")
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            except ValueError:
                return self._refuse(400, "bad json")
            reason = check_chat_payload(payload, model)
            if reason:
                return self._refuse(400, reason)
            payload["model"] = model
            apply_default_max_tokens(payload, default_max_tokens)
            log.dump(self.client_address[0], payload)
            if payload.get("stream"):
                return self._relay(payload)
            return self._forward(payload)

        def _start_stream(self, code: int, content_type: str) -> None:
            self._stream_started = True
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

        def _write_line(self, line: str) -> None:
            self.wfile.write(line.encode() + b"\n")
            self.wfile.flush()

        def _log(self, status: int, started: float, state: StreamState, body: Any = None) -> None:
            """A usage row for a streaming client (``stream: true`` in the log)."""
            record = usage_record(self.client_address[0], status, time.monotonic() - started, body)
            record.update(stream_fields(state, status, record))
            log.write({**record, "stream": True})

        def _relay(self, payload: dict[str, Any]) -> None:
            started = time.monotonic()
            self.close_connection = True
            self._stream_started = False
            state = StreamState()
            try:
                status, body = upstream.relay(payload, self._start_stream, self._write_line, state)
            except OSError:  # the client went away: the upstream request is closed with it
                log.stats["errors"] += 1
                return self._log(CLIENT_CUT_STATUS, started, state)
            except Exception as exc:  # noqa: BLE001 - a 502 when nothing was sent yet
                log.stats["errors"] += 1
                logger.warning("upstream error: %s", type(exc).__name__)
                self._log(502, started, state)
                if not self._stream_started:
                    self._send(502, json.dumps({"error": type(exc).__name__}).encode())
                return None
            log.stats["forwarded"] += 1
            return self._log(status, started, state, body)

        def _forward(self, payload: dict[str, Any]) -> None:
            started = time.monotonic()
            try:
                status, data = upstream.post(payload)
            except Exception as exc:  # noqa: BLE001 - relayed as a 502
                log.stats["errors"] += 1
                logger.warning("upstream error: %s", type(exc).__name__)
                log.write(
                    usage_record(self.client_address[0], 502, time.monotonic() - started, None)
                )
                return self._send(502, json.dumps({"error": type(exc).__name__}).encode())
            log.stats["forwarded"] += 1
            try:
                body = json.loads(data)
            except ValueError:
                body = None
            log.write(
                usage_record(self.client_address[0], status, time.monotonic() - started, body)
            )
            return self._send(status, data)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default="rikyu")
    parser.add_argument("--model", default="glm-5.3")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - peer + token checked per request
    parser.add_argument("--port", type=int, default=DEFAULT_MODEL_PROXY_PORT)
    parser.add_argument("--token", required=True)
    parser.add_argument("--read-timeout", type=float, default=1800.0)
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument(
        "--default-max-tokens",
        type=int,
        default=0,
        help="max_tokens for requests that send none (0 = leave the server default)",
    )
    parser.add_argument(
        "--dump-dir", type=Path, default=None, help="write every request body here (debugging)"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
    if args.dump_dir:
        args.dump_dir.mkdir(parents=True, exist_ok=True)
    upstream = Upstream(args.source, args.read_timeout)
    log = UsageLog(args.log, args.dump_dir)
    server = ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(upstream, args.model, args.token, log, args.default_max_tokens),
    )
    logger.info(
        "model proxy on %s:%d -> %s (source %s, default max_tokens %s)",
        args.host,
        args.port,
        args.model,
        args.source,
        args.default_max_tokens or "server default",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("stats %s", log.stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
