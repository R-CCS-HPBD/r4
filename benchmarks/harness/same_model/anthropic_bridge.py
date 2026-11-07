#!/usr/bin/env python3
"""Anthropic Messages API -> OpenAI chat.completions bridge for the Claude Code
lane, model forced to glm-5.3 on the rikyu source.

Why not the repo shim (benchmarks/harness/anthropic_shim.py) as-is: measured
2026-10-03 with Claude Code 2.1.288 it (a) answers ``stream: true`` with a plain
JSON body, so every Claude Code call fails its stream ("Stream ended without
receiving any events") and is re-sent non-streaming (double calls), and
(b) forwards Anthropic ``tool_use`` / ``tool_result`` content blocks verbatim
to the OpenAI upstream, which the rikyu gateway rejects (502) — the agent loop
dies on its second turn. This bridge keeps the shim's credential path (it
imports the shim's own ``_resolve_config``; the key never leaves this process)
and its model forcing, and adds full message/tool translation + Anthropic SSE.

Listening: ``--uds run/bridge.sock`` (start_proxies.sh; mode 0600 in the 0700
run/ dir, no TCP port). Sandboxed Claude Code reaches it through an in-sandbox
relay on 127.0.0.1:8330. ``--port`` is for debugging only.

Routes:
  GET  /healthz                  local liveness (no upstream call)
  POST /v1/messages              streaming (Anthropic SSE) and non-streaming
  POST /v1/messages/count_tokens 501 (Claude Code then estimates locally)
  GET  /v1/models                minimal list naming the target model
Translation:
  system (str|blocks)            -> system message
  user text/image/document       -> user content (images/documents -> text
                                    placeholder unless --images pass; counted
                                    as media_dropped)
  user tool_result               -> role=tool messages (before the user text)
  assistant text/tool_use        -> content + tool_calls
  assistant thinking blocks      -> reasoning_content   (--reasoning carry, default)
  OpenAI reasoning_content       -> thinking blocks (thinking_delta + a placeholder
                                    signature), so Claude Code echoes GLM's
                                    reasoning back exactly like R4 does
                                    (r4 companion agent.py: reasoning_content echo)
  OpenAI tool_calls              -> tool_use blocks, emitted whole after the
                                    upstream finishes (fragments buffered by index,
                                    so interleaved fragments are never lost)
  tools / tool_choice            -> OpenAI function tools / tool_choice
  temperature                    -> --default-temperature when the client sent none
  usage                          -> input_tokens = prompt - cached,
                                    cache_read_input_tokens = cached
Keep-alive: an SSE ``ping`` every 10 s without upstream bytes (timer-driven, so
a long prefill never leaves Claude Code idle after message_start).
Upstream 5xx/transport errors are retried before the first byte (0/2/5/10/20/40 s).
Upstream error bodies are never relayed verbatim (status + redacted message).
Log: ``#rid START`` / ``#rid END`` lines (model, stream, status, tokens,
finish reason, tool names, reasoning carried, media dropped, latency) — never
headers, keys or bodies.

    .venv/bin/python anthropic_bridge.py --uds run/bridge.sock [--target-model glm-5.3]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
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

log = Logger("[anthropic-bridge]")
PING_EVERY_S = 10.0
SIGNATURE = "bench-bridge-openai-reasoning"  # placeholder: the upstream is not Anthropic, nothing verifies it
STOP_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "refusal",
}


# ----------------------------------------------------------------------------- request translation


class Tx:
    """Per-request translation settings + counters."""

    def __init__(self, images: str, reasoning: str):
        self.images = images
        self.reasoning = reasoning
        self.media_dropped = 0


def _placeholder(block: dict[str, Any], tx: Tx) -> dict[str, Any] | None:
    """Image/document blocks -> OpenAI part (pass) or a text placeholder (drop)."""
    btype = block.get("type")
    src = block.get("source") or {}
    if btype == "image" and tx.images == "pass" and src.get("type") == "base64":
        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
            },
        }
    if btype == "image" and tx.images == "pass" and src.get("type") == "url":
        return {"type": "image_url", "image_url": {"url": src.get("url", "")}}
    if btype in ("image", "document"):
        tx.media_dropped += 1
        return {
            "type": "text",
            "text": f"[{btype} content omitted: the model endpoint is text-only]",
        }
    return None


def _block_part(b: dict[str, Any], tx: Tx) -> dict[str, Any] | None:
    """One non-tool content block -> an OpenAI content part (None: nothing to send)."""
    t = b.get("type")
    if t == "text":
        return {"type": "text", "text": b.get("text", "")}
    if t in ("image", "document"):
        return _placeholder(b, tx)
    if t in ("thinking", "redacted_thinking"):
        return None
    # tool_reference, search_result, etc. -> text rendering
    txt = b.get("text") or b.get("content") or ""
    if txt:
        return {"type": "text", "text": txt if isinstance(txt, str) else json.dumps(txt)}
    return None


def _blocks_to_parts(content: Any, tx: Tx) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    parts: list[dict[str, Any]] = []
    for b in content or []:
        if not isinstance(b, dict):
            continue
        p = _block_part(b, tx)
        if p:
            parts.append(p)
    return parts


def _parts_to_content(parts: list[dict[str, Any]]) -> Any:
    if all(p.get("type") == "text" for p in parts):
        return "\n".join(p.get("text", "") for p in parts)
    return parts


def _tool_result_text(block: dict[str, Any], tx: Tx) -> Any:
    c = block.get("content")
    parts = _blocks_to_parts(c if c is not None else "", tx)
    content = _parts_to_content(parts)
    if block.get("is_error"):
        if isinstance(content, str):
            content = "[tool error] " + content
        else:
            content = [{"type": "text", "text": "[tool error]"}, *content]
    # OpenAI tool messages want string content on most servers
    if not isinstance(content, str):
        content = "\n".join(
            p.get("text", "[image]") if p.get("type") == "text" else "[image]" for p in content
        )
    return content


def _tool_id(tool_id: Any) -> Any:
    return tool_id or f"toolu_{uuid.uuid4().hex[:24]}"


def _system_message(system: Any) -> dict[str, Any] | None:
    if isinstance(system, str) and system:
        return {"role": "system", "content": system}
    if isinstance(system, list):
        text = "\n\n".join(
            str(b.get("text", "")) for b in system if isinstance(b, dict) and b.get("text")
        )
        if text:
            return {"role": "system", "content": text}
    return None


def _openai_tool_call(b: dict[str, Any]) -> dict[str, Any]:
    """Anthropic tool_use block -> OpenAI tool_calls entry."""
    return {
        "id": _tool_id(b.get("id")),
        "type": "function",
        "function": {
            "name": b.get("name", "unknown"),
            "arguments": json.dumps(b.get("input") or {}),
        },
    }


def _assistant_blocks(content: Any) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Assistant content -> (texts, thinking texts, OpenAI tool_calls)."""
    texts: list[str] = []
    thoughts: list[str] = []
    calls: list[dict[str, Any]] = []
    if isinstance(content, str):
        return [content], thoughts, calls
    for b in content or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            texts.append(b.get("text", ""))
        elif b.get("type") == "thinking" and b.get("thinking"):
            thoughts.append(b["thinking"])
        elif b.get("type") in ("tool_use", "server_tool_use"):
            calls.append(_openai_tool_call(b))
    return texts, thoughts, calls


def _assistant_message(content: Any, tx: Tx) -> dict[str, Any]:
    texts, thoughts, calls = _assistant_blocks(content)
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(texts) if texts else None,
    }
    if calls:
        msg["tool_calls"] = calls
    if thoughts and tx.reasoning == "carry":
        msg["reasoning_content"] = "\n".join(thoughts)
    if msg["content"] is None and not calls:
        msg["content"] = ""
    return msg


def _user_messages(content: Any, tx: Tx) -> list[dict[str, Any]]:
    """User content -> role=tool messages (tool_result blocks) followed by the user message."""
    if isinstance(content, str):
        return [{"role": "user", "content": content}]
    out: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": b.get("tool_use_id", ""),
                    "content": _tool_result_text(b, tx),
                }
            )
        elif isinstance(b, dict):
            rest.append(b)
    if rest:
        parts = _blocks_to_parts(rest, tx)
        if parts:
            out.append({"role": "user", "content": _parts_to_content(parts)})
    return out


def translate_messages(body: dict[str, Any], tx: Tx) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    system = _system_message(body.get("system"))
    if system:
        out.append(system)
    for m in body.get("messages") or []:
        role = m.get("role")
        content = m.get("content")
        if role == "assistant":
            out.append(_assistant_message(content, tx))
        else:
            out.extend(_user_messages(content, tx))
    return out


def _client_tool(t: Any) -> bool:
    # server tools (web_search_*, etc.) have no client-side schema
    return isinstance(t, dict) and bool(t.get("name")) and "input_schema" in t


def _tool_choice(choice: Any) -> Any:
    if not isinstance(choice, dict):
        return None
    ct = choice.get("type")
    if ct == "auto":
        return "auto"
    if ct == "any":
        return "required"
    if ct == "none":
        return "none"
    if ct == "tool" and choice.get("name"):
        return {"type": "function", "function": {"name": choice["name"]}}
    return None


def translate_tools(body: dict[str, Any]) -> tuple[list[dict[str, Any]], Any]:
    tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description") or "",
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for t in body.get("tools") or []
        if _client_tool(t)
    ]
    return tools, _tool_choice(body.get("tool_choice"))


def build_upstream_body(
    body: dict[str, Any], model: str, tx: Tx, stream: bool, default_temperature: float | None
) -> tuple[dict[str, Any], str]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": translate_messages(body, tx),
        "max_tokens": body.get("max_tokens", 4096),
        "stream": stream,
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}
    tools, choice = translate_tools(body)
    if tools:
        payload["tools"] = tools
        if choice is not None:
            payload["tool_choice"] = choice
    for k in ("temperature", "top_p", "top_k"):
        if body.get(k) is not None:
            payload[k] = body[k]
    temp_src = apply_default_temperature(payload, default_temperature)
    if body.get("stop_sequences"):
        payload["stop"] = body["stop_sequences"]
    return payload, temp_src


# ----------------------------------------------------------------------------- response translation


def anth_usage(usage: dict[str, Any] | None) -> dict[str, int]:
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    cached = int((details.get("cached_tokens") if isinstance(details, dict) else 0) or 0)
    return {
        "input_tokens": max(prompt - cached, 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": 0,
    }


def _tool_input(args: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(args) if isinstance(args, str) and args.strip() else (args or {})
    except json.JSONDecodeError:
        parsed = {"_raw": args}
    return parsed if isinstance(parsed, dict) else {"_raw": parsed}


def _stop_reason(calls: Any, finish: Any) -> str:
    return "tool_use" if calls else STOP_MAP.get(finish or "stop", "end_turn")


def _content_blocks(msg: dict[str, Any], reasoning: str) -> list[dict[str, Any]]:
    """OpenAI message -> Anthropic thinking (carry mode) and text blocks."""
    blocks: list[dict[str, Any]] = []
    thought = msg.get("reasoning_content") or msg.get("reasoning")
    if reasoning == "carry" and isinstance(thought, str) and thought:
        blocks.append({"type": "thinking", "thinking": thought, "signature": SIGNATURE})
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    return blocks


def openai_to_anthropic(
    o: dict[str, Any], reasoning: str
) -> tuple[dict[str, Any], list[str], str | None]:
    choice = (o.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    blocks = _content_blocks(msg, reasoning)
    calls = msg.get("tool_calls") or []
    names = []
    for c in calls:
        fn = c.get("function") or {}
        names.append(clean_name(fn.get("name")))
        blocks.append(
            {
                "type": "tool_use",
                "id": _tool_id(c.get("id")),
                "name": fn.get("name", "unknown"),
                "input": _tool_input(fn.get("arguments")),
            }
        )
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    finish = choice.get("finish_reason")
    stop = _stop_reason(calls, finish)
    return (
        {
            "id": "msg_" + str(o.get("id") or uuid.uuid4().hex),
            "type": "message",
            "role": "assistant",
            "model": o.get("model"),
            "content": blocks,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": anth_usage(o.get("usage")),
        },
        names,
        finish,
    )


def sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def anthropic_error(status: int, raw: bytes) -> Response:
    """Status + redacted message, never the raw upstream body."""
    msg = sanitize_error(raw)
    etype = {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_error",
        404: "not_found_error",
        413: "request_too_large",
        429: "rate_limit_error",
        529: "overloaded_error",
    }.get(status, "api_error")
    return JSONResponse(
        {
            "type": "error",
            "error": {
                "type": etype,
                "message": f"upstream {status}: {msg}" if msg else f"upstream {status}",
            },
        },
        status_code=status,
    )


# ----------------------------------------------------------------------------- server


class Upstream:
    def __init__(
        self,
        url: str,
        api_key: str | None,
        target: str,
        images: str,
        reasoning: str,
        read_timeout: float,
        default_temperature: float | None,
    ):
        self.url = url.rstrip("/")
        self._api_key = api_key
        self.target = target
        self.images = images
        self.reasoning = reasoning
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

    async def open(self, payload: dict[str, Any], rid: int):
        data = json.dumps(payload).encode("utf-8")
        last: tuple[int, bytes] = (502, b'{"error":{"message":"upstream: no attempt"}}')
        for attempt, delay in enumerate(RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                req = self.client.build_request(
                    "POST", f"{self.url}/chat/completions", headers=self.headers(), content=data
                )
                resp = await self.client.send(req, stream=True)
            except httpx.HTTPError as exc:
                last = (
                    502,
                    json.dumps(
                        {"error": {"message": f"upstream transport error: {type(exc).__name__}"}}
                    ).encode(),
                )
                log(
                    f"#{rid} transport error {type(exc).__name__}, retry {attempt + 1}/{len(RETRY_DELAYS) - 1}"
                )
                continue
            if resp.status_code >= 500:
                last = (resp.status_code, await resp.aread())
                await resp.aclose()
                log(
                    f"#{rid} upstream {resp.status_code}, retry {attempt + 1}/{len(RETRY_DELAYS) - 1}"
                )
                continue
            if attempt:
                log(f"#{rid} recovered after {attempt} retr(ies)")
            return resp
        return last


async def _pump(resp: httpx.Response, q: asyncio.Queue) -> None:
    """Consume the upstream stream in ONE task; the relay waits on the queue with a timer.

    Always ends with one terminal item, ``("eof", None)`` or ``("error", <exception
    name>)`` (the relay turns the latter into an Anthropic ``error`` event), unless the
    relay itself cancelled the pump. Before 2026-10-05 an upstream exception that was
    not an httpx error killed the task with nothing queued, and the client got pings
    forever (harness repair plan B6).
    """
    end: tuple[str, Any] | None = ("error", "pump_exit")
    try:
        async for chunk in resp.aiter_raw():
            await q.put(("data", chunk))
        end = ("eof", None)
    except asyncio.CancelledError:
        end = None  # the relay stopped the pump; nobody waits for a terminal item
        raise
    except Exception as exc:  # noqa: BLE001 - httpx or not, the relay must hear the end
        end = ("error", type(exc).__name__)
    finally:
        if end is not None:
            await q.put(end)


async def healthz(request: Request) -> Response:
    return JSONResponse({"ok": True, "proxy": "anthropic-bridge"})


def _prepare(up: Upstream, body: dict[str, Any], tag: str) -> tuple[dict[str, Any], bool, str]:
    """Anthropic request -> (upstream payload, stream, log meta)."""
    requested = clean_name(body.get("model"))
    stream = bool(body.get("stream"))
    tx = Tx(up.images, up.reasoning)
    payload, temp_src = build_upstream_body(body, up.target, tx, stream, up.default_temperature)
    asst, asst_reasoning = count_reasoning(payload["messages"])
    meta = (
        f"case={tag} model={up.target} requested={requested} stream={stream} "
        f"msgs={len(payload['messages'])} tools={len(payload.get('tools') or [])} "
        f"max_tokens={payload.get('max_tokens')} temperature={payload.get('temperature')} temp_src={temp_src} "
        f"asst={asst} asst_reasoning={asst_reasoning} media_dropped={tx.media_dropped} "
        f"tool_names={tool_names(payload.get('tools'))}"
    )
    return payload, stream, meta


async def _upstream_error(resp: httpx.Response, rid: int, meta: str, t0: float) -> Response:
    raw = await resp.aread()
    await resp.aclose()
    log(
        f"#{rid} END POST /v1/messages {meta} status={resp.status_code} "
        f"err={clean_name(sanitize_error(raw, 40))} dt={time.monotonic() - t0:.2f}s"
    )
    return anthropic_error(resp.status_code, raw)


async def _whole_message(
    resp: httpx.Response, reasoning: str, rid: int, meta: str, t0: float
) -> Response:
    """Non-streaming: one OpenAI JSON response -> one Anthropic message."""
    raw = await resp.aread()
    await resp.aclose()
    try:
        o = json.loads(raw)
    except json.JSONDecodeError:
        log(f"#{rid} END POST /v1/messages {meta} status=502 non-json-upstream")
        return anthropic_error(502, b'{"error":{"message":"non-JSON upstream response"}}')
    a, names, finish = openai_to_anthropic(o, reasoning)
    u = a["usage"]
    log(
        f"#{rid} END POST /v1/messages {meta} upstream_model={clean_name(o.get('model'))} status=200 "
        f"input_tokens={u['input_tokens']} cache_read={u['cache_read_input_tokens']} "
        f"output_tokens={u['output_tokens']} tool_calls={len(names)} called={','.join(names) or '-'} "
        f"finish={finish} stop={a['stop_reason']} usage={'yes' if o.get('usage') else 'missing'} "
        f"dt={time.monotonic() - t0:.2f}s"
    )
    return JSONResponse(a)


def _block_delta(index: int, delta: dict[str, Any]) -> bytes:
    return sse(
        "content_block_delta", {"type": "content_block_delta", "index": index, "delta": delta}
    )


class StreamRelay:
    """Streaming: OpenAI SSE chunks -> Anthropic SSE events, for one request.

    The per-item event generators are synchronous and ``events()`` hands their
    events out one at a time, so when a client goes away mid-stream the END log
    reports exactly the state (usage, finish, tool calls, reasoning chars, stop)
    behind the events already sent.
    """

    def __init__(
        self, up: Upstream, resp: httpx.Response, rid: int, meta: str, t0: float, msg_id: str
    ):
        self.up, self.resp, self.rid, self.meta, self.t0 = up, resp, rid, meta, t0
        self.msg_id = msg_id
        self.resp_model = up.target
        self.usage_raw: dict[str, Any] | None = None
        self.finish = None
        self.idx = -1
        self.cur: str | None = None  # "thinking" | "text"
        self.calls: dict[int, dict[str, Any]] = {}  # openai tool index -> {id, name, args}
        self.err = None
        self.done = False
        self.pings = 0
        self.reasoning_chars = 0
        self.stop: str | None = None
        self.lines = SSEDataLines()

    async def events(self) -> AsyncIterator[bytes]:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        pump = asyncio.create_task(_pump(self.resp, q))
        try:
            yield self._message_start()
            while not self.done:
                item = await self._next(q)
                for ev in self._on_item(*item):
                    yield ev
            for ev in self._finish():
                yield ev
        except (asyncio.CancelledError, GeneratorExit):
            self.err = self.err or "client_closed"
            raise
        finally:
            # log FIRST: under a cancelled anyio scope (client gone, e.g. a timed-out case)
            # every await re-raises, so nothing after the first await is guaranteed to run
            self._log_end()
            if not pump.done():
                pump.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(pump, return_exceptions=True)
                await self.resp.aclose()

    @staticmethod
    async def _next(q: asyncio.Queue) -> tuple[str, Any]:
        """Next pump item, or ("ping", None) after PING_EVERY_S without one."""
        try:
            return await asyncio.wait_for(q.get(), timeout=PING_EVERY_S)
        except asyncio.TimeoutError:
            return ("ping", None)

    def _on_item(self, kind: str, val: Any) -> Iterator[bytes]:
        if kind == "ping":
            self.pings += 1
            yield sse("ping", {"type": "ping"})
        elif kind == "data":
            for j in self.lines.feed(val):
                yield from self._on_chunk(j)
        else:  # "eof" | "error"
            self.done = True
            if kind == "error":
                self.err = val

    def _on_chunk(self, j: dict[str, Any]) -> Iterator[bytes]:
        self.resp_model = j.get("model") or self.resp_model
        if j.get("usage"):
            self.usage_raw = j["usage"]
        for ch in j.get("choices") or []:
            delta = ch.get("delta") or {}
            if ch.get("finish_reason"):
                self.finish = ch["finish_reason"]
            yield from self._on_reasoning(delta.get("reasoning_content") or delta.get("reasoning"))
            yield from self._on_text(delta.get("content"))
            self._on_tool_calls(delta.get("tool_calls") or [])

    def _on_reasoning(self, thought: Any) -> Iterator[bytes]:
        if not (isinstance(thought, str) and thought):
            return
        self.reasoning_chars += len(thought)
        if self.up.reasoning != "carry":
            return
        if self.cur != "thinking":
            yield from self._open_block(
                "thinking", {"type": "thinking", "thinking": "", "signature": ""}
            )
        yield _block_delta(self.idx, {"type": "thinking_delta", "thinking": thought})

    def _on_text(self, text: Any) -> Iterator[bytes]:
        if not text:
            return
        if self.cur != "text":
            yield from self._open_block("text", {"type": "text", "text": ""})
        yield _block_delta(self.idx, {"type": "text_delta", "text": text})

    def _on_tool_calls(self, tool_calls: list) -> None:
        """Buffer tool-call fragments by OpenAI index; emitted whole by _finish()."""
        for tc in tool_calls:
            oi = tc.get("index", len(self.calls))
            fn = tc.get("function") or {}
            c = self.calls.setdefault(oi, {"id": None, "name": None, "args": ""})
            if tc.get("id") and not c["id"]:
                c["id"] = tc["id"]
            if fn.get("name") and not c["name"]:
                c["name"] = fn["name"]
            if fn.get("arguments"):
                c["args"] += fn["arguments"]

    def _close_cur(self) -> list[bytes]:
        out = []
        if self.cur == "thinking":
            out.append(_block_delta(self.idx, {"type": "signature_delta", "signature": SIGNATURE}))
        if self.cur is not None:
            out.append(sse("content_block_stop", {"type": "content_block_stop", "index": self.idx}))
        return out

    def _open_block(self, kind: str, content_block: dict[str, Any]) -> Iterator[bytes]:
        yield from self._close_cur()
        self.idx += 1
        self.cur = kind
        yield sse(
            "content_block_start",
            {"type": "content_block_start", "index": self.idx, "content_block": content_block},
        )

    def _finish(self) -> Iterator[bytes]:
        if self.err:
            yield sse(
                "error",
                {
                    "type": "error",
                    "error": {"type": "api_error", "message": f"upstream stream broke: {self.err}"},
                },
            )
            return
        yield from self._close_cur()
        self.cur = None
        for oi in sorted(self.calls):
            yield from self._tool_use_block(self.calls[oi])
        if self.idx < 0:  # nothing at all came back: one empty text block
            self.idx = 0
            yield sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": self.idx,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            yield sse("content_block_stop", {"type": "content_block_stop", "index": self.idx})
        self.stop = _stop_reason(self.calls, self.finish)
        yield sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": self.stop, "stop_sequence": None},
                "usage": anth_usage(self.usage_raw),
            },
        )
        yield sse("message_stop", {"type": "message_stop"})

    def _tool_use_block(self, c: dict[str, Any]) -> Iterator[bytes]:
        self.idx += 1
        yield sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": self.idx,
                "content_block": {
                    "type": "tool_use",
                    "id": _tool_id(c["id"]),
                    "name": c["name"] or "unknown",
                    "input": {},
                },
            },
        )
        yield _block_delta(
            self.idx,
            {"type": "input_json_delta", "partial_json": json.dumps(_tool_input(c["args"]))},
        )
        yield sse("content_block_stop", {"type": "content_block_stop", "index": self.idx})

    def _message_start(self) -> bytes:
        return sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": self.msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.up.target,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            },
        )

    def _log_end(self) -> None:
        u = anth_usage(self.usage_raw)
        names = ",".join(clean_name(self.calls[i]["name"]) for i in sorted(self.calls)) or "-"
        log(
            f"#{self.rid} END POST /v1/messages {self.meta} upstream_model={clean_name(self.resp_model)} status=200 "
            f"input_tokens={u['input_tokens']} cache_read={u['cache_read_input_tokens']} "
            f"output_tokens={u['output_tokens']} tool_calls={len(self.calls)} called={names} "
            f"finish={self.finish} stop={self.stop} reasoning_chars={self.reasoning_chars} pings={self.pings} "
            f"usage={'yes' if self.usage_raw else 'missing'}"
            f"{' stream_error=' + self.err if self.err else ''} dt={time.monotonic() - self.t0:.2f}s"
        )


async def messages(request: Request) -> Response:
    up: Upstream = request.app.state.up
    up.seq += 1
    rid = up.seq
    t0 = time.monotonic()
    tag = case_tag(request)
    try:
        body = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        log(f"#{rid} END POST /v1/messages case={tag} status=400 bad-json")
        return anthropic_error(400, b'{"error":{"message":"invalid JSON body"}}')
    payload, stream, meta = _prepare(up, body, tag)
    log(
        f"#{rid} START POST /v1/messages case={tag} stream={stream} msgs={len(payload['messages'])}"
    )
    opened = await up.open(payload, rid)
    if isinstance(opened, tuple):
        status, raw = opened
        log(
            f"#{rid} END POST /v1/messages {meta} status={status} err={clean_name(sanitize_error(raw, 40))} "
            f"dt={time.monotonic() - t0:.2f}s (gave up)"
        )
        return anthropic_error(status, raw)
    resp: httpx.Response = opened
    if resp.status_code >= 400:
        return await _upstream_error(resp, rid, meta, t0)
    if not stream:
        return await _whole_message(resp, up.reasoning, rid, meta, t0)
    relay = StreamRelay(up, resp, rid, meta, t0, msg_id="msg_" + uuid.uuid4().hex)
    return StreamingResponse(
        relay.events(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


async def count_tokens(request: Request) -> Response:
    log(f"POST /v1/messages/count_tokens case={case_tag(request)} status=501")
    return JSONResponse(
        {
            "type": "error",
            "error": {
                "type": "not_found_error",
                "message": "count_tokens not served by bench bridge",
            },
        },
        status_code=501,
    )


async def models(request: Request) -> Response:
    up: Upstream = request.app.state.up
    log("GET /v1/models status=200")
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {"id": up.target, "type": "model", "object": "model", "display_name": up.target}
            ],
            "has_more": False,
            "first_id": up.target,
            "last_id": up.target,
        }
    )


async def not_found(request: Request) -> Response:
    log(f"{request.method} {request.url.path[:120]} status=404")
    return JSONResponse(
        {"type": "error", "error": {"type": "not_found_error", "message": "not served"}},
        status_code=404,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="anthropic-bridge")
    ap.add_argument("--uds", help="listen on this UNIX socket (mode 0600); the normal bench mode")
    ap.add_argument(
        "--port", type=int, default=None, help="debug only: listen on 127.0.0.1:PORT instead"
    )
    ap.add_argument(
        "--target-model", default=os.environ.get("R4_BENCH_SHIM_TARGET_MODEL", "glm-5.3")
    )
    ap.add_argument(
        "--images",
        choices=("drop", "pass"),
        default="drop",
        help="image/document blocks: text placeholder (drop) or OpenAI image_url parts (pass)",
    )
    ap.add_argument(
        "--reasoning",
        choices=("carry", "drop"),
        default="carry",
        help="carry: upstream reasoning_content <-> Anthropic thinking blocks (R4 parity); "
        "drop: discard it (ablation)",
    )
    ap.add_argument(
        "--default-temperature",
        type=float,
        default=None,
        help="temperature applied when the client sent none (R4's default is 0.4)",
    )
    ap.add_argument("--read-timeout", type=float, default=900.0)
    a = ap.parse_args(argv)
    if not a.uds and not a.port:
        ap.error("one of --uds / --port is required")
    os.environ.setdefault("R4_BENCH_SHIM_SOURCE", "rikyu")
    shim = load_shim()
    url, api_key, source = shim._resolve_config()  # key stays in this process only
    up = Upstream(
        url, api_key, a.target_model, a.images, a.reasoning, a.read_timeout, a.default_temperature
    )
    del api_key
    log(
        f"listening on {'unix:' + a.uds if a.uds else f'127.0.0.1:{a.port}'} -> {url} | "
        f"target_model={a.target_model} source={source} auth={'yes' if up._api_key else 'no'} "
        f"images={a.images} reasoning={a.reasoning} default_temperature={a.default_temperature}"
    )
    app = Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/v1/messages", messages, methods=["POST"]),
            Route("/v1/messages/count_tokens", count_tokens, methods=["POST"]),
            Route("/v1/models", models, methods=["GET"]),
            Route(
                "/{path:path}", not_found, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"]
            ),
        ]
    )
    app.state.up = up
    listen(app, uds=a.uds, port=a.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
