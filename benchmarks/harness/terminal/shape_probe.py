"""Request-shape probe: which part of R4's request sends glm-5.3 into long deliberation?

Replays one captured R4 request (a model_proxy ``--dump-dir`` file) under request
shapes that each change one thing, streamed through the host model proxy, and
records per sample the completion tokens, finish reason, wall time and what the
reply did (tool call / text). ``--max-tokens`` caps the deliberation, so "cut at
the cap" is the long-deliberation signal and does not depend on endpoint speed.
Public benchmark data only; reasoning text goes to --out, never printed.

Shapes:
  asis         the captured request
  noreplay     assistant ``reasoning_content`` not replayed
  nosys        no system message
  coresys      R4's base companion prompt instead of the full one
  text         tool calls and results replayed as alternating assistant/user
               text (terminus-2's shape: every observation a new user turn),
               no tool schemas
  text_tools   the alternating text history, tool schemas kept

    .venv/bin/python benchmarks/harness/terminal/shape_probe.py --port 8336 \\
        --token "$TOKEN" --dump <dump.json> --shape asis --shape text \\
        --samples 4 --max-tokens 20000 --out <dir>
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from benchmarks.harness.terminal.temperature_probe import _stream, _system_variant, repetition

Body = dict[str, Any]


def _messages(body: Body) -> list[dict[str, Any]]:
    return [dict(m) for m in body["messages"]]


def _noreplay(body: Body) -> Body:
    msgs = _messages(body)
    for m in msgs:
        m.pop("reasoning_content", None)
        m.pop("reasoning", None)
    return {**body, "messages": msgs}


def _nosys(body: Body) -> Body:
    return {**body, "messages": [m for m in _messages(body) if m["role"] != "system"]}


def _call_line(call: dict[str, Any]) -> str:
    fn = call.get("function") or {}
    return f"{fn.get('name')}({fn.get('arguments')})"


def _calls_as_text(message: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    for call in message["tool_calls"]:
        names[call["id"]] = (call.get("function") or {}).get("name", "")
    lines = "\n".join(_call_line(c) for c in message["tool_calls"])
    return {"role": "assistant", "content": f"Running:\n{lines}"}


def _fold_result(out: list[dict[str, Any]], message: dict[str, Any], names: dict[str, str]) -> None:
    """Fold one tool result into the observation turn that follows the calls."""
    name = names.get(message.get("tool_call_id", ""), "tool")
    block = f"[{name} output]\n{message.get('content')}"
    if out and out[-1].get("_obs"):
        out[-1]["content"] += f"\n\n{block}"
    else:
        out.append({"role": "user", "content": block, "_obs": True})


def _as_text(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool rounds as plain turns: the assistant's calls as text, the results of
    one round folded into a single following user message."""
    out: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    for m in msgs:
        if m["role"] == "assistant" and m.get("tool_calls"):
            out.append(_calls_as_text(m, names))
        elif m["role"] == "tool":
            _fold_result(out, m, names)
        else:
            out.append(_without(m, "reasoning_content"))
    return [_without(m, "_obs") for m in out]


def _without(message: dict[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in message.items() if k != key}


def _text(body: Body) -> Body:
    rest = {k: v for k, v in body.items() if k not in ("tools", "tool_choice")}
    return {**rest, "messages": _as_text(_messages(body))}


def _text_tools(body: Body) -> Body:
    return {**body, "messages": _as_text(_messages(body))}


SHAPES: dict[str, Callable[[Body], Body]] = {
    "asis": lambda body: body,
    "noreplay": _noreplay,
    "nosys": _nosys,
    "coresys": lambda body: _system_variant(body, "core"),
    "text": _text,
    "text_tools": _text_tools,
}


def _reply_kind(meta: dict[str, Any]) -> str:
    return {"tool_calls": "tool", "stop": "text", "length": "cut"}.get(meta["finish"] or "", "?")


def sample(args: argparse.Namespace, shape: str, idx: int) -> dict[str, Any]:
    body = SHAPES[shape](json.loads(args.dump.read_text()))
    if args.system_suffix and body["messages"][0]["role"] == "system":
        system = {**body["messages"][0]}
        system["content"] = f"{system['content']}\n\n{args.system_suffix}"
        body["messages"] = [system, *body["messages"][1:]]
    for key in ("temperature", "top_p", "stream_options"):
        body.pop(key, None)
    body.update(model="glm-5.3", max_tokens=args.max_tokens, stream=True, temperature=0.4)
    started = time.monotonic()
    try:
        meta, reasoning = _stream(
            f"http://127.0.0.1:{args.port}/v1/chat/completions", args.token, body
        )
    except Exception as exc:  # noqa: BLE001 - recorded per sample
        return {"shape": shape, "idx": idx, "error": type(exc).__name__}
    (args.out / f"{args.dump.stem}.{shape}.{idx}.reasoning.txt").write_text(reasoning)
    return {
        "dump": args.dump.stem,
        "shape": shape,
        "idx": idx,
        "completion_tokens": (meta["usage"] or {}).get("completion_tokens"),
        "prompt_tokens": (meta["usage"] or {}).get("prompt_tokens"),
        "reply": _reply_kind(meta),
        "minutes": round((time.monotonic() - started) / 60, 1),
        "rep_all": round(repetition(reasoning), 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8336)
    ap.add_argument("--token", required=True)
    ap.add_argument("--dump", type=Path, required=True, help="a captured request .json")
    ap.add_argument("--shape", action="append", required=True, choices=sorted(SHAPES))
    ap.add_argument("--system-suffix", default="", help="text appended to the system prompt")
    ap.add_argument("--samples", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=20000)
    ap.add_argument("--parallel", type=int, default=8)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    jobs = [(shape, i) for i in range(args.samples) for shape in args.shape]
    with ThreadPoolExecutor(args.parallel) as pool:
        for record in pool.map(lambda job: sample(args, *job), jobs):
            print(json.dumps(record), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
