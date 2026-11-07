"""Sampling probe: does R4's temperature drive glm-5.3 into runaway reasoning?

Sends R4's real first-round request (companion system prompt, workspace line,
the workspace tool schemas, one Terminal-Bench task instruction) several times
per sampling setting through the host model proxy, streamed, and records per
sample: completion tokens, finish reason, wall time and a repetition score of the
reasoning (share of repeated 64-char shingles, overall and in the last fifth —
degenerate loops show up as a high late score). Reasoning text is written to
--out for inspection, never printed. Public benchmark data only.

    .venv/bin/python benchmarks/harness/terminal/temperature_probe.py \\
        --port 8334 --token "$TOKEN" --task-dir <harbor task dir> [--task-dir ...] \\
        --setting t0.4:0.4 --setting t1.0:1.0:0.95 --samples 3 --max-tokens 32768 --out <dir>
"""

from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from r4.companion.agent import COMPANION_SYSTEM_PROMPT
from r4.companion.skills import ALWAYS_ON_TOOL_NAMES, WORKSPACE_TOOL_NAMES
from r4.companion.tools import get_builtin_tools


def tool_schemas() -> list[dict[str, Any]]:
    keep = WORKSPACE_TOOL_NAMES | ALWAYS_ON_TOOL_NAMES
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            },
        }
        for t in get_builtin_tools(client=None)
        if t["name"] in keep
    ]


def first_round(instruction: str) -> list[dict[str, Any]]:
    system = COMPANION_SYSTEM_PROMPT + "\n\nYour workspace root is /app."
    return [{"role": "system", "content": system}, {"role": "user", "content": instruction}]


def _label(source: Path) -> str:
    return source.stem if source.suffix == ".json" else source.parent.name


def repetition(text: str, width: int = 64, step: int = 32) -> float:
    shingles = [text[i : i + width] for i in range(0, max(0, len(text) - width), step)]
    return 1 - len(set(shingles)) / len(shingles) if shingles else 0.0


def _sse_chunks(resp: httpx.Response):
    for line in resp.iter_lines():
        if line.startswith("data:") and line.strip() != "data: [DONE]":
            yield json.loads(line[5:])


def _absorb(chunk: dict[str, Any], parts: list[str]) -> str | None:
    finish = None
    for choice in chunk.get("choices") or []:
        delta = choice.get("delta") or {}
        parts.append(delta.get("reasoning_content") or delta.get("reasoning") or "")
        finish = choice.get("finish_reason") or finish
    return finish


def _stream(url: str, token: str, body: dict[str, Any]) -> tuple[dict[str, Any], str]:
    usage, finish, parts, ttft = {}, None, [], None
    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(1800, connect=30)
    started = time.monotonic()
    with httpx.stream("POST", url, json=body, headers=headers, timeout=timeout) as resp:
        resp.raise_for_status()
        for chunk in _sse_chunks(resp):
            ttft = ttft if ttft is not None else time.monotonic() - started
            usage = chunk.get("usage") or usage
            finish = _absorb(chunk, parts) or finish
    return {"usage": usage, "finish": finish, "ttft": ttft}, "".join(parts)


def base_body(source: Path, max_tokens: int) -> dict[str, Any]:
    """A captured request (``--replay``, a model_proxy ``--dump-dir`` file) or R4's
    first round for a task dir; sampling fields are set per setting."""
    if source.suffix == ".json":
        body = json.loads(source.read_text())
        for key in ("temperature", "top_p", "stream_options"):
            body.pop(key, None)
    else:
        body = {
            "messages": first_round((source / "instruction.md").read_text()),
            "tools": tool_schemas(),
        }
    return {**body, "model": "glm-5.3", "max_tokens": max_tokens, "stream": True}


def _continuation(reasoning_file: str) -> dict[str, str]:
    """R4's own continuation nudge after an output-limit cut (ADR-087 D-3),
    carrying the tail of a saved cut reasoning — the round after a cut."""
    from r4.companion.agent import _length_nudge_text

    cut = {"choices": [{"message": {"reasoning_content": Path(reasoning_file).read_text()}}]}
    return {"role": "user", "content": _length_nudge_text(cut)}


# Wave (80): <project_state> and the active skill bodies ride a <turn_context>
# block on the turn's user message; inside it a skill body ends at the next
# turn part (an empty string keeps the pre-wave-80 system-message stops).
_TURN_BLOCK = re.compile(r"(<turn_context>\n)(.*?)(</turn_context>)", re.S)
_BLOCK_STOPS = (
    r"\[Skills not loaded|\[Persistent memory\]|\[Session context\]|\[Tool limit\]"
    r"|Plan mode is active|"
)


def _ablate(text: str, mode: str, extra_stops: str = "") -> str:
    """``text`` without what ``mode`` strips (``noproj``, ``noplatskills``, ``lean``)."""
    if mode == "noplatskills":
        for skill in ("hpc", "model_management", "data_management"):
            stop = rf"\[Skill: |\[Skills available|{extra_stops}$"
            text = re.sub(rf"\[Skill: {skill}\].*?(?={stop})", "", text, flags=re.S)
        return text
    if mode == "lean":
        stop = rf"\[Skills available|\[Environment\]|{extra_stops}$"
        text = re.sub(rf"\[Skill: .*?(?={stop})", "", text, flags=re.S)
    return re.sub(r"<project_state>.*?</project_state>", "", text, flags=re.S)


def _ablate_turn_block(content: str, mode: str) -> str:
    """A user message's <turn_context> block ablated (``core``: dropped)."""
    if mode == "core":
        return re.sub(r"\n*<turn_context>\n.*?</turn_context>", "", content, flags=re.S)
    return _TURN_BLOCK.sub(lambda m: m[1] + _ablate(m[2], mode, _BLOCK_STOPS) + m[3], content)


def _system_variant(body: dict[str, Any], mode: str) -> dict[str, Any]:
    """``core``: R4's base companion prompt only; ``lean``: the real prompt without
    the injected skill bodies and the <project_state> block (2026-10-06 probe).
    Since wave (80) those ride the turn's <turn_context> block, which the same
    ablation edits (``core`` drops it); older captures edit the system message."""
    if not mode:
        return body
    messages = [dict(m) for m in body["messages"]]
    system = str(messages[0]["content"])
    if mode == "core":
        messages[0]["content"] = COMPANION_SYSTEM_PROMPT + "\n\nYour workspace root is /app."
    else:
        messages[0]["content"] = _ablate(system, mode)
    for message in messages[1:]:
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            message["content"] = _ablate_turn_block(message["content"], mode)
    return {**body, "messages": messages}


def _with_system_suffix(body: dict[str, Any], suffix: str) -> dict[str, Any]:
    if not suffix:
        return body
    messages = [dict(m) for m in body["messages"]]
    messages[0]["content"] = f"{messages[0]['content']}\n\n{suffix}"
    return {**body, "messages": messages}


def sample(args: argparse.Namespace, task: Path, setting: str, idx: int) -> dict[str, Any]:
    name, *vals = setting.split(":")
    body = _system_variant(base_body(task, args.max_tokens), args.system_variant)
    body = _with_system_suffix(body, args.system_suffix)
    body.update(json.loads(args.extra or "{}"))
    if args.continue_from:
        body["messages"] = [*body["messages"], _continuation(args.continue_from[idx])]
    body["temperature"] = float(vals[0])
    if len(vals) > 1:
        body["top_p"] = float(vals[1])
    started = time.monotonic()
    try:
        meta, reasoning = _stream(
            f"http://127.0.0.1:{args.port}/v1/chat/completions", args.token, body
        )
    except Exception as exc:  # noqa: BLE001 - recorded per sample
        return {"task": _label(task), "setting": name, "idx": idx, "error": type(exc).__name__}
    out = args.out / f"{_label(task)}.{name}.{idx}.reasoning.txt"
    out.write_text(reasoning)
    last = reasoning[-len(reasoning) // 5 :] if reasoning else ""
    return {
        "task": _label(task),
        "setting": name,
        "idx": idx,
        "completion_tokens": (meta["usage"] or {}).get("completion_tokens"),
        "finish": meta["finish"],
        "minutes": round((time.monotonic() - started) / 60, 1),
        "ttft_s": round(meta["ttft"], 1) if meta.get("ttft") is not None else None,
        "rep_all": round(repetition(reasoning), 3),
        "rep_last_fifth": round(repetition(last), 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8334)
    ap.add_argument("--token", required=True)
    ap.add_argument(
        "--task-dir",
        type=Path,
        action="append",
        required=True,
        help="a harbor task dir (R4's first round) or a captured request .json to replay",
    )
    ap.add_argument("--setting", action="append", required=True, help="name:temperature[:top_p]")
    ap.add_argument("--system-suffix", default="", help="text appended to the system prompt")
    ap.add_argument(
        "--system-variant", default="", choices=["", "core", "lean", "noproj", "noplatskills"]
    )
    ap.add_argument(
        "--extra", default="", help='JSON merged into the body, e.g. {"reasoning_effort": "low"}'
    )
    ap.add_argument(
        "--continue-from",
        nargs="*",
        default=[],
        help="saved cut reasoning files, one per sample: replay the round after the cut",
    )
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    jobs = [
        (task, setting, i)
        for task in args.task_dir
        for setting in args.setting
        for i in range(args.samples)
    ]
    with ThreadPoolExecutor(args.parallel) as pool:
        for record in pool.map(lambda job: sample(args, *job), jobs):
            print(json.dumps(record), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
