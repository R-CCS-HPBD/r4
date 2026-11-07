"""Anthropic-style → OpenAI-style LiteLLM shim for harness benchmarks.

DEPRECATED server path (2026-10-05, harness repair plan B5)
-----------------------------------------------------------
The shim no longer serves: ``main()`` exits with an error and ``ShimHandler``
answers 410. Measured 2026-10-03 with Claude Code 2.1.288, it answered
``stream: true`` with a plain JSON body (every streamed call failed and was
re-sent) and forwarded Anthropic ``tool_use`` / ``tool_result`` blocks raw to
the OpenAI upstream, which rejected turn 2 of every tool loop. Claude Code
lanes use ``benchmarks/harness/same_model/anthropic_bridge.py`` (full message
and tool translation, Anthropic SSE). What stays live here is the source
resolver the bridge and ``openai_passthrough.py`` load
(``_read_sources_file``, ``_resolve_source``, ``_resolve_config``); the old
translation helpers below are kept only until their removal.

Why this file exists
--------------------
The Claude Code lane of the harness comparison needs an Anthropic hero API
endpoint (``ANTHROPIC_BASE_URL`` ), yet our self-hosted BYOM gateway only
serves the OpenAI shape. The shim bridges the wire formats **and forces the
target model**: Claude Code has a client-side model metadata table that
gates *metadata sub-calls* (session-title generation) regardless of extras
env flags/tiers, so to keep a third-party model end-to-end we REWRITE the
``model`` field of every request body (title + main chat alike). See
docs/development/harness-benchmarking-plan.md — BYOM via gateway note +
wire-level confound warning; the bridge now carries this model forcing.

Former behaviour (until 2026-10-05)
-----------------------------------
- ``POST /v1/messages`` : accept any api key (entry auth is the bench lane's,
  not production auth); body is passed through with ``model`` replaced by
  ``R4_BENCH_SHIM_TARGET_MODEL``; translated to chat.completions against
  ``R4_BENCH_SHIM_UPSTREAM`` using ``R4_BENCH_SHIM_API_KEY`` as Bearer when
  set; the OpenAI response is mapped back to the Anthropic shape, including
  passthrough of errors with status codes.

- ``GET /v1/models``    : returns a minimal Anthropic model list so any
  client fetching capabilities doesn't 501 — names it after the target.

- Everything else       : 501, matching an upstream that can't honor it.

Run (deprecated, exits with an error pointing at the bridge):
    python benchmarks/harness/anthropic_shim.py --port 8330
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

TARGET_MODEL_DEFAULT = "kimi-k3"
SOURCE_DEFAULT = "rikyu"
LOG_PREFIX = "[anthropic-shim]"
BRIDGE_PATH = "benchmarks/harness/same_model/anthropic_bridge.py"
DEPRECATED_MESSAGE = (
    f"{LOG_PREFIX} the shim's server is deprecated (2026-10-05): it answered stream:true "
    "with plain JSON and forwarded tool_use/tool_result blocks raw, so Claude Code tool "
    f"loops failed on turn 2. Run {BRIDGE_PATH} instead (--uds run/bridge.sock, or "
    "--port 8330 for debugging; it resolves the source with this module's _resolve_config)."
)

# Providers this shim can translate for (Anthropic API in, OpenAI chat.completions
# out). Anything else (bedrock, anthropic passthrough) is a different wire form
# and must fail closed rather than silently mis-translate.
_OPENAI_PROVIDERS = frozenset({None, "openai"})


def _sources_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) / "r4" if xdg else Path.home() / ".config" / "r4"
    return base / "sources.json"


def _read_sources_file(name: str) -> dict[str, Any] | None:
    """Look up a named source in the r4 public-source registry (sources.json)."""
    try:
        data = json.loads(_sources_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for entry in data.get("sources", []):
        if entry.get("name") == name:
            return dict(entry)
    return None


def _resolve_source(name: str) -> tuple[str, str | None]:
    """Resolve (upstream_url, api_key) for a registered r4 public source.

    Fails closed: kind must be 'public' and provider must be OpenAI-compatible
    (None or 'openai') — the shim only speaks the OpenAI wire form upstream.
    """
    entry = _read_sources_file(name)
    if entry is None:
        raise SystemExit(
            f"{LOG_PREFIX} source '{name}' not found in {str(_sources_path())!r}; "
            "register it first (r4 client)"
        )
    if entry.get("kind") != "public":
        raise SystemExit(
            f"{LOG_PREFIX} source '{name}' is kind={entry.get('kind')!r}; "
            "only openai public sources are supported"
        )
    if entry.get("provider") not in _OPENAI_PROVIDERS:
        raise SystemExit(
            f"{LOG_PREFIX} source '{name}' has provider={entry.get('provider')!r}; "
            "only openai-compatible public sources (provider unset or 'openai') "
            "are supported"
        )
    url = str(entry.get("url") or "").rstrip("/")
    if not url:
        raise SystemExit(f"{LOG_PREFIX} source '{name}' has no url")
    return url, entry.get("api_key")


def _resolve_config() -> tuple[str, str | None, str]:
    """Resolve (upstream_url, api_key, source_name) honouring explicit env overrides.

    R4_BENCH_SHIM_UPSTREAM / R4_BENCH_SHIM_API_KEY override the resolved
    registry values field-by-field; R4_BENCH_SHIM_SOURCE picks the registry
    entry (default: SOURCE_DEFAULT).
    """
    source_name = os.environ.get("R4_BENCH_SHIM_SOURCE", SOURCE_DEFAULT)
    url, api_key = _resolve_source(source_name)
    url = (os.environ.get("R4_BENCH_SHIM_UPSTREAM") or url).rstrip("/")
    api_key = os.environ.get("R4_BENCH_SHIM_API_KEY") or api_key
    return url, api_key, source_name


def _translate_tools(body: dict[str, Any]) -> tuple[list[dict[str, Any]], Any]:
    """Anthropic tools + tool_choice → OpenAI tools + tool_choice."""
    tools = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description") or "",
                    "parameters": tool.get("input_schema") or {"type": "object"},
                },
            }
        )
    choice = body.get("tool_choice")
    return tools, choice


def _append_system(messages: list[dict[str, Any]], system: Any) -> list[dict[str, Any]]:
    if not system:
        return messages
    if isinstance(system, str):
        return [{"role": "system", "content": system}, *messages]
    if isinstance(system, list):
        text = " ".join(str(b.get("text", "")) for b in system if isinstance(b, dict))
        return [{"role": "system", "content": text}, *messages]
    return messages


def _upstream_messages(
    body: dict[str, Any], *, url_base: str, api_key: str | None
) -> tuple[int, dict[str, Any] | str]:
    url = f"{url_base}/chat/completions"
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": body["model"],
        "messages": _append_system(list(body.get("messages", [])), body.get("system")),
        "max_tokens": body.get("max_tokens", 1024),
        "stream": False,
    }
    tools, choice = _translate_tools(body)
    if tools:
        payload["tools"] = tools
        if choice:
            payload["tool_choice"] = choice
    if body.get("temperature") is not None:
        payload["temperature"] = body["temperature"]
    if body.get("stop_sequences"):
        payload["stop"] = body["stop_sequences"]
    return _post_with_retry(url, payload, headers)


def _post_with_retry(
    url: str, payload: dict[str, Any], headers: dict[str, str]
) -> tuple[int, dict[str, Any] | str]:
    """POST with short-retry on transient 5xx/transport failures.

    Claude Code treats ANY api error as session-fatal (is_error=true, rc=1),
    so a single transient 502 mid-loop fails the whole cell even when the work
    succeeded (observed 2026-08-24: hello.py written, THEN a later call 502'd).
    Absorb transients here: retry 5xx/transport errors with backoff before
    surfacing, and always fast-fail on 4xx. Budget: 6 attempts spanning ~77s
    worst case — long enough to ride out LiteLLM worker restarts observed in
    bench traffic 2026-08-24 (4-attempt/10s budget saw 0 recoveries in 34
    retries), short enough to keep a cell's wallclock bounded (~4 retries ×
    20s is dwarfed by the per-leg timeout).
    """
    delays = (0, 2, 5, 10, 20, 40)
    last_status, last_body = 502, "upstream bubble: no attempt"
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                if attempt:
                    print(f"[anthropic-shim] recovered after {attempt} retr(ies)", file=sys.stderr)
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", "replace")
            if exc.code < 500:
                return exc.code, body_text  # 4xx: retrying cannot help
            last_status, last_body = exc.code, body_text
            print(
                f"[anthropic-shim] upstream {exc.code}, retry {attempt + 1}/{len(delays) - 1}",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 — board transport failures as retries
            last_status, last_body = 502, f"upstream bubble: {exc}"
            print(
                f"[anthropic-shim] transport error, retry {attempt + 1}/{len(delays) - 1}",
                file=sys.stderr,
            )
    return last_status, last_body


def _translate_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    """OpenAI tool_call → Anthropic tool_use content block."""
    function = call.get("function") or {}
    arguments = function.get("arguments", "{}")
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        parsed = {"_raw": arguments}
    return {
        "type": "tool_use",
        "id": call.get("id", "toolu-shim"),
        "name": function.get("name", "unknown"),
        "input": parsed,
    }


def _openai_to_anthropic(openai: dict[str, Any]) -> dict[str, Any]:
    choice = (openai.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = openai.get("usage") or {}
    content_blocks: list[dict[str, Any]] = []
    text = message.get("content", "")
    if text:
        content_blocks.append({"type": "text", "text": text})
    tool_calls = message.get("tool_calls") or []
    for call in tool_calls:
        content_blocks.append(_translate_tool_call(call))
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})
    stop_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
    return {
        "id": openai.get("id", "shim-response"),
        "type": "message",
        "role": "assistant",
        "model": openai.get("model"),
        "content": content_blocks,
        "stop_reason": stop_map.get(choice.get("finish_reason"), "stop"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
        },
    }


class ShimHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, target_model: str, url_base: str, api_key: str | None, **kwargs):
        self.target_model = target_model
        self.url_base = url_base
        self.api_key = api_key
        super().__init__(*args, **kwargs)

    def _req(self) -> None:
        length = self.headers.get("content-length") or "0"
        print(f"{LOG_PREFIX} req {self.command} {self.path} buflen={length}", flush=True)

    def _json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length") or "0")
        raw = self.rfile.read(length).decode("utf-8", "replace")
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}

    def do_POST(self) -> None:  # noqa: N802 — http.server convention
        """Deprecated: refuse with a pointer to the bridge (see the module docstring)."""
        self._req()
        base = self.path.split("?", 1)[0].rstrip("/")
        if base != "/v1/messages":
            self.send_error(501, f"Unsupported method ('{self.path}').")
            return
        self._read_json_body()
        self._json(
            410,
            {"type": "error", "error": {"type": "api_error", "message": DEPRECATED_MESSAGE}},
        )

    def do_GET(self) -> None:  # noqa: N802
        self._req()
        base = self.path.split("?", 1)[0].rstrip("/")
        if base == "/v1/models":
            self._json(
                200, {"object": "list", "data": [{"id": self.target_model, "object": "model"}]}
            )
            return
        self.send_error(501, f"Unsupported method ('{self.path}').")

    def log_message(self, format: str, *args: Any) -> None:
        pass  # quiet; semantics tracked by our own LOG_PREFIX lines


def main(argv: list[str] | None = None) -> int:
    """Deprecated entry point: exits with an error naming the bridge to run instead."""
    del argv  # the old --port / --target-model flags are no longer read
    raise SystemExit(DEPRECATED_MESSAGE)


if __name__ == "__main__":
    raise SystemExit(main())
