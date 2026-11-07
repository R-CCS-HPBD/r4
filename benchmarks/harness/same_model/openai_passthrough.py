#!/usr/bin/env python3
"""Minimal OpenAI-compatible reverse proxy for the OpenCode and R4 lanes.

The client talks to this proxy with a dummy api key; the proxy forwards to the
rikyu upstream with the real Bearer key, which it resolves IN-PROCESS through
the repo shim's own resolver (benchmarks/harness/anthropic_shim.py:
``_resolve_config`` -> r4 public-source registry, R4_BENCH_SHIM_SOURCE=rikyu).
The key never leaves this process: it is not printed, logged, written or put
on any command line.

Listening: ``--uds run/passthrough.sock`` (default in start_proxies.sh; socket
mode 0600 inside the 0700 run/ dir, so only the maintainer's uid can connect,
and no TCP port exists for other local users or processes to find). Sandboxed
harnesses reach it through an in-sandbox relay on 127.0.0.1:8332.
``--port`` (127.0.0.1 TCP) is for debugging only.

Routes:
  GET  /healthz                         local liveness (no upstream call)
  GET  [/case/<tag>]/v1/models          -> upstream GET  {url}/models
  POST [/case/<tag>]/v1/chat/completions-> upstream POST {url}/chat/completions
  anything else                         -> 404
The case tag comes from the ``x-bench-case`` header (OpenCode) or the
``/case/<tag>/`` path prefix (R4: its source URL carries the tag).

Request policy (logged per request, applied identically to every client):
  model        forced to --force-model (glm-5.3)
  temperature  --default-temperature (0.4 = R4's own default) when the client
               sent none; ``temp_src`` = client | proxy_default | server_default
  media        non-text parts -> text placeholder (glm-5.3@rikyu is text-only)
Upstream 5xx / transport errors are retried BEFORE any byte reaches the client
(0/2/5/10/20/40 s). Upstream error bodies are never relayed verbatim: the client
gets ``upstream <status>: <message>`` with anything key-like redacted.

Log (stdout): ``#rid START ...`` and ``#rid END ...`` lines with model, stream,
status, usage, finish reason, advertised tool names, assistant messages
carrying reasoning_content, tool calls returned, latency. Never headers, keys
or bodies.

    .venv/bin/python openai_passthrough.py --uds run/passthrough.sock [--force-model glm-5.3]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

import anyio
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from proxy_common import (
    RETRY_DELAYS,
    Logger,
    SSEDataLines,
    apply_default_temperature,
    case_tag,
    clean_name,
    count_reasoning,
    listen,
    load_shim,
    sanitize_error,
    tool_names,
)

log = Logger("[openai-passthrough]")
IMAGE_PLACEHOLDER = "[{kind} content omitted: the model endpoint is text-only]"
MEDIA_TYPES = ("image_url", "input_image", "input_audio", "file")


def _media_placeholder(part: Any) -> dict | None:
    """A non-text content part -> its text placeholder; None for anything else."""
    if isinstance(part, dict) and part.get("type") in MEDIA_TYPES:
        kind = "image" if "image" in part["type"] else part["type"]
        return {"type": "text", "text": IMAGE_PLACEHOLDER.format(kind=kind)}
    return None


def _strip_parts(parts: list) -> tuple[list, int]:
    """(parts with media replaced by placeholders, number replaced)."""
    new, n = [], 0
    for part in parts:
        placeholder = _media_placeholder(part)
        if placeholder is None:
            new.append(part)
        else:
            n += 1
            new.append(placeholder)
    return new, n


def _all_text(parts: list) -> bool:
    return all(isinstance(x, dict) and x.get("type") == "text" for x in parts)


def strip_media(body: dict) -> int:
    """Replace non-text content parts (image_url / input_audio / file) by a text placeholder.
    glm-5.3@rikyu rejects them with 400 ('not a multimodal model'), which kills an agent
    session; the Anthropic bridge applies the same placeholder for Claude Code."""
    n = 0
    for m in body.get("messages") or []:
        c = m.get("content")
        if not isinstance(c, list):
            continue
        new, stripped = _strip_parts(c)
        n += stripped
        if m.get("role") == "tool" and _all_text(new):
            m["content"] = "\n".join(x.get("text", "") for x in new)
        else:
            m["content"] = new
    return n


def _usage_fields(usage: dict | None) -> str:
    if not isinstance(usage, dict):
        return "prompt_tokens=- completion_tokens=- total_tokens=- cached_tokens=-"
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    return (
        f"prompt_tokens={usage.get('prompt_tokens', '-')} "
        f"completion_tokens={usage.get('completion_tokens', '-')} "
        f"total_tokens={usage.get('total_tokens', '-')} "
        f"cached_tokens={cached if cached is not None else '-'}"
    )


def error_response(status: int, raw: bytes) -> Response:
    """Sanitized OpenAI-shaped error: status + redacted message, never the raw upstream body."""
    msg = sanitize_error(raw)
    etype = {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_error",
        404: "not_found_error",
        413: "request_too_large",
        429: "rate_limit_error",
    }.get(status, "api_error")
    return JSONResponse(
        {
            "error": {
                "message": f"upstream {status}: {msg}" if msg else f"upstream {status}",
                "type": etype,
                "code": status,
            }
        },
        status_code=status,
    )


class Upstream:
    def __init__(
        self,
        url: str,
        api_key: str | None,
        force_model: str | None,
        read_timeout: float,
        strip_images: bool = True,
        default_temperature: float | None = None,
    ):
        self.url = url.rstrip("/")
        self.strip_images = strip_images
        self._api_key = api_key
        self.force_model = force_model or None
        self.default_temperature = default_temperature
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=read_timeout, write=60.0, pool=None),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
        )
        self.seq = 0

    def headers(self) -> dict[str, str]:
        h = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
        if self._api_key:
            h["authorization"] = "Bearer " + self._api_key
        return h


async def healthz(request: Request) -> Response:
    return JSONResponse({"ok": True, "proxy": "openai-passthrough"})


async def models(request: Request) -> Response:
    up: Upstream = request.app.state.up
    t0 = time.monotonic()
    try:
        r = await up.client.get(f"{up.url}/models", headers=up.headers())
    except httpx.HTTPError as exc:
        log(
            f"GET /v1/models case={case_tag(request)} status=502 transport={type(exc).__name__} "
            f"dt={time.monotonic() - t0:.2f}s"
        )
        return JSONResponse(
            {"error": {"message": "upstream transport error", "code": 502}}, status_code=502
        )
    log(
        f"GET /v1/models case={case_tag(request)} status={r.status_code} dt={time.monotonic() - t0:.2f}s"
    )
    if r.status_code >= 400:
        return error_response(r.status_code, r.content)
    return Response(
        r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )


async def _open_upstream(up: Upstream, payload: bytes, rid: int):
    """Send the request with retry on 5xx/transport errors; returns an httpx.Response
    (stream not yet consumed) or (status, error_bytes)."""
    last: tuple[int, bytes] = (502, b'{"error":{"message":"upstream: no attempt","code":502}}')
    for attempt, delay in enumerate(RETRY_DELAYS):
        if delay:
            await asyncio.sleep(delay)
        try:
            req = up.client.build_request(
                "POST", f"{up.url}/chat/completions", headers=up.headers(), content=payload
            )
            resp = await up.client.send(req, stream=True)
        except httpx.HTTPError as exc:
            last = (
                502,
                json.dumps(
                    {
                        "error": {
                            "message": f"upstream transport error: {type(exc).__name__}",
                            "code": 502,
                        }
                    }
                ).encode(),
            )
            log(
                f"#{rid} transport error {type(exc).__name__}, retry {attempt + 1}/{len(RETRY_DELAYS) - 1}"
            )
            continue
        if resp.status_code >= 500:
            body = await resp.aread()
            await resp.aclose()
            last = (resp.status_code, body)
            log(f"#{rid} upstream {resp.status_code}, retry {attempt + 1}/{len(RETRY_DELAYS) - 1}")
            continue
        if attempt:
            log(f"#{rid} recovered after {attempt} retr(ies)")
        return resp
    return last


def _called_names(names: dict[int, str]) -> str:
    return ",".join(names[i] for i in sorted(names)) or "-"


def _apply_policy(up: Upstream, body: dict, tag: str) -> tuple[str, bool, int]:
    """Apply the request policy to ``body`` in place -> (log meta, stream, message count)."""
    requested = clean_name(body.get("model"))
    stripped = strip_media(body) if up.strip_images else 0
    if up.force_model:
        body["model"] = up.force_model
    temp_src = apply_default_temperature(body, up.default_temperature)
    model = body.get("model")
    stream = bool(body.get("stream"))
    n_msgs = len(body.get("messages") or [])
    n_tools = len(body.get("tools") or [])
    asst, asst_reasoning = count_reasoning(body.get("messages"))
    mt = body.get("max_tokens") or body.get("max_completion_tokens")
    meta = (
        f"case={tag} model={model} requested={requested} stream={stream} msgs={n_msgs} tools={n_tools} "
        f"max_tokens={mt} temperature={body.get('temperature')} temp_src={temp_src} "
        f"asst={asst} asst_reasoning={asst_reasoning} media_stripped={stripped} "
        f"tool_names={tool_names(body.get('tools'))}"
    )
    return meta, stream, n_msgs


def _response_facts(data: bytes) -> tuple[Any, Any, Any, dict[int, str]]:
    """(usage, model, finish reason, called tool names) of a whole JSON response, best effort."""
    usage, resp_model, finish, called = None, None, None, {}
    try:
        j = json.loads(data)
        usage = j.get("usage")
        resp_model = j.get("model")
        ch = (j.get("choices") or [{}])[0]
        finish = ch.get("finish_reason")
        for i, tc in enumerate((ch.get("message") or {}).get("tool_calls") or []):
            called[i] = clean_name((tc.get("function") or {}).get("name"))
    except (json.JSONDecodeError, AttributeError, IndexError):
        pass
    return usage, resp_model, finish, called


async def _upstream_error(resp: httpx.Response, rid: int, meta: str, t0: float) -> Response:
    data = await resp.aread()
    await resp.aclose()
    log(
        f"#{rid} END POST /v1/chat/completions {meta} status={resp.status_code} "
        f"err={clean_name(sanitize_error(data, 40))} dt={time.monotonic() - t0:.2f}s"
    )
    return error_response(resp.status_code, data)


async def _relay_whole(
    resp: httpx.Response, ctype: str, rid: int, meta: str, t0: float
) -> Response:
    data = await resp.aread()
    await resp.aclose()
    usage, resp_model, finish, called = _response_facts(data)
    log(
        f"#{rid} END POST /v1/chat/completions {meta} upstream_model={clean_name(resp_model)} "
        f"status={resp.status_code} {_usage_fields(usage)} finish={finish} resp_tool_calls={len(called)} "
        f"called={_called_names(called)} dt={time.monotonic() - t0:.2f}s"
    )
    return Response(data, status_code=resp.status_code, media_type=ctype or "application/json")


class StreamFacts:
    """Side-parse of the relayed SSE stream: usage/model/finish/tool names (never content)."""

    def __init__(self) -> None:
        self.usage: Any = None
        self.resp_model: Any = None
        self.finish: Any = None
        self.called: dict[int, str] = {}
        self.lines = SSEDataLines()

    def feed(self, chunk: bytes) -> None:
        for j in self.lines.feed(chunk):
            self.resp_model = j.get("model") or self.resp_model
            if j.get("usage"):
                self.usage = j["usage"]
            for ch in j.get("choices") or []:
                if ch.get("finish_reason"):
                    self.finish = ch["finish_reason"]
                self._tool_calls((ch.get("delta") or {}).get("tool_calls") or [])

    def _tool_calls(self, tool_calls: list) -> None:
        called = self.called
        for tc in tool_calls:
            idx = tc.get("index", len(called))
            name = (tc.get("function") or {}).get("name")
            if idx not in called or (name and called[idx] == "?"):
                called[idx] = clean_name(name) if name else called.get(idx, "?")


async def _relay_stream(
    resp: httpx.Response, rid: int, meta: str, t0: float
) -> AsyncIterator[bytes]:
    facts = StreamFacts()
    nchunks = 0
    err = None
    try:
        async for chunk in resp.aiter_raw():
            nchunks += 1
            yield chunk
            facts.feed(chunk)
    except httpx.HTTPError as exc:
        err = type(exc).__name__
    except (asyncio.CancelledError, GeneratorExit):
        err = "client_closed"
        raise
    finally:
        # log FIRST: under a cancelled anyio scope (client gone, e.g. a timed-out case)
        # every await re-raises, so nothing after the first await is guaranteed to run
        log(
            f"#{rid} END POST /v1/chat/completions {meta} upstream_model={clean_name(facts.resp_model)} "
            f"status={resp.status_code} {_usage_fields(facts.usage)} finish={facts.finish} "
            f"resp_tool_calls={len(facts.called)} called={_called_names(facts.called)} chunks={nchunks}"
            f"{' stream_error=' + err if err else ''} dt={time.monotonic() - t0:.2f}s"
        )
        with anyio.CancelScope(shield=True):
            await resp.aclose()


async def chat(request: Request) -> Response:
    up: Upstream = request.app.state.up
    up.seq += 1
    rid = up.seq
    t0 = time.monotonic()
    tag = case_tag(request)
    try:
        body = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        log(f"#{rid} END POST /v1/chat/completions case={tag} status=400 bad-json")
        return JSONResponse(
            {"error": {"message": "invalid JSON body", "code": 400}}, status_code=400
        )
    meta, stream, n_msgs = _apply_policy(up, body, tag)
    log(f"#{rid} START POST /v1/chat/completions case={tag} stream={stream} msgs={n_msgs}")
    payload = json.dumps(body).encode("utf-8")
    opened = await _open_upstream(up, payload, rid)
    if isinstance(opened, tuple):
        status, err = opened
        log(
            f"#{rid} END POST /v1/chat/completions {meta} status={status} err={clean_name(sanitize_error(err, 40))} "
            f"dt={time.monotonic() - t0:.2f}s (gave up)"
        )
        return error_response(status, err)
    resp: httpx.Response = opened
    ctype = resp.headers.get("content-type", "")
    if resp.status_code >= 400:
        return await _upstream_error(resp, rid, meta, t0)
    if not stream or "text/event-stream" not in ctype:
        return await _relay_whole(resp, ctype, rid, meta, t0)
    headers = {"cache-control": "no-cache", "x-accel-buffering": "no"}
    return StreamingResponse(
        _relay_stream(resp, rid, meta, t0),
        status_code=resp.status_code,
        media_type="text/event-stream",
        headers=headers,
    )


async def not_found(request: Request) -> Response:
    log(f"{request.method} {request.url.path[:120]} status=404")
    return JSONResponse(
        {"error": {"message": "not served by bench passthrough", "code": 404}}, status_code=404
    )


def build_app(up: Upstream) -> Starlette:
    routes = [Route("/healthz", healthz, methods=["GET"])]
    for prefix in ("", "/case/{tag}"):
        routes += [
            Route(f"{prefix}/v1/models", models, methods=["GET"]),
            Route(f"{prefix}/models", models, methods=["GET"]),
            Route(f"{prefix}/v1/chat/completions", chat, methods=["POST"]),
            Route(f"{prefix}/chat/completions", chat, methods=["POST"]),
        ]
    routes.append(
        Route("/{path:path}", not_found, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    )
    app = Starlette(routes=routes)
    app.state.up = up
    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="openai-passthrough")
    ap.add_argument("--uds", help="listen on this UNIX socket (mode 0600); the normal bench mode")
    ap.add_argument(
        "--port", type=int, default=None, help="debug only: listen on 127.0.0.1:PORT instead"
    )
    ap.add_argument(
        "--force-model",
        default=os.environ.get("R4_BENCH_SHIM_TARGET_MODEL", "glm-5.3"),
        help="rewrite every request's model to this ('' disables)",
    )
    ap.add_argument(
        "--default-temperature",
        type=float,
        default=None,
        help="temperature applied when the client sent none (R4's default is 0.4)",
    )
    ap.add_argument("--read-timeout", type=float, default=900.0)
    ap.add_argument(
        "--images",
        choices=("strip", "pass"),
        default="strip",
        help="non-text content parts: text placeholder (strip) or forward (pass)",
    )
    a = ap.parse_args(argv)
    if not a.uds and not a.port:
        ap.error("one of --uds / --port is required")
    os.environ.setdefault("R4_BENCH_SHIM_SOURCE", "rikyu")
    shim = load_shim()
    url, api_key, source = shim._resolve_config()  # key stays in this process only
    up = Upstream(
        url,
        api_key,
        a.force_model,
        a.read_timeout,
        strip_images=a.images == "strip",
        default_temperature=a.default_temperature,
    )
    log(
        f"listening on {'unix:' + a.uds if a.uds else f'127.0.0.1:{a.port}'} -> {url} | "
        f"force_model={a.force_model or 'off'} default_temperature={a.default_temperature} "
        f"source={source} auth={'yes' if api_key else 'no'} images={a.images}"
    )
    del api_key
    listen(build_app(up), uds=a.uds, port=a.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
