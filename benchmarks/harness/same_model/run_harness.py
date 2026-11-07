#!/usr/bin/env python3
"""Public-harness companion to run_glm.py: Claude Code / OpenCode on glm-5.3@rikyu.

Same questions, same prompt text, same arms as the R4 lane, harness swapped:
  - prompt: run_glm.prompt(arm, q) itself (module imported, not copied), so the
    text is byte-identical to what the R4 companion receives;
  - sandbox: sandbox.py (shared with run_r4.py, the hardened R4 lane): own
    network namespace (loopback only; the model proxy, the per-case egress
    guard and, for arms B/C/P, the kgq stores are the ONLY reachable endpoints,
    via in-sandbox relays to host UNIX sockets), private /run /tmp /dev/shm,
    allow-listed home (corpus + arm tool tree + harness binary + .venv + HF
    hub), pinned PATH, --clearenv, args via fd;
  - model: Claude Code -> anthropic_bridge.py; OpenCode -> openai_passthrough.py.
    Both force glm-5.3, resolve the rikyu key in-process, apply temperature
    0.4 (R4's default) when the client sends none, and log one START/END line
    per request tagged with the case (x-bench-case header) -> proxy_usage, the
    harness-independent per-case record (same accounting as the R4 lane in
    run_r4.py, which goes through the passthrough too).
  - Claude Code tools: 'default' = the stock set minus WebFetch/WebSearch
    (primary comparison); --claude-tools Read,Grep,Glob,Bash is the ablation.

Metrics in each result (see METRICS_DOC, also written into every result):
  metrics.total_tokens      = prompt tokens incl. cache reads + completion tokens
                              (same definition as R4's total_tokens)
  proxy_usage               = per-request record from the proxy log: requests,
                              agent_requests (tools advertised) vs aux_requests
                              (no tools: title / summary calls = harness overhead),
                              tokens split the same way, finish reasons, tool names,
                              assistant messages carrying reasoning_content
  metrics.agent_steps       = assistant turns as the harness itself records them
  egress_denied             = this case's own egress-guard log (per-case socket)

Results: harness/results_<harness>/<arm>__<qid>.json (resumable: existing files are
skipped; --redo-failed re-runs non-success ones). --smoke switches to the
synthetic corpus in smoke/ (results under smoke/results_<harness>/; arm A only:
smoke never exposes the real proto/ or current/ trees).

usage:
  run_harness.py --harness claude,opencode --qids ID,ID|all --arms A,C [--par 2] [--timeout 1200]
  run_harness.py --smoke --harness claude,opencode --arms A
  run_harness.py --smoke --probe --harness claude,opencode --arms A        # sandbox probe (synthetic)
  run_harness.py --probe --harness claude,opencode --arms A,C              # same on the REAL tree (counts only)
  run_harness.py --kgq-check                                               # kgq in the arm-C/B sandbox: rc + secs only
  run_harness.py --smoke --show-prompt A                                   # R4 prompt builder on the smoke question
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # before ANY import of repo code: no __pycache__ in the checkout

import argparse  # noqa: E402
import builtins  # noqa: E402
import concurrent.futures as cf  # noqa: E402
import hashlib  # noqa: E402
import http.client  # noqa: E402
import importlib.util  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from pathlib import Path  # noqa: E402

CODE = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE))
import sandbox as S  # noqa: E402
from egress_guard import CaseGuard  # noqa: E402

BENCH, R4GLM, SMOKE, LOGS, RUN, REPO = S.BENCH, S.R4GLM, S.SMOKE, S.LOGS, S.RUN, S.REPO
HERE, SMOKE_OUT = S.HERE, S.SMOKE_OUT  # lane outputs (outside git)
REAL_HOME = S.REAL_HOME
OPENCODE_BIN, OPENCODE_CFG = S.OPENCODE_BIN, S.OPENCODE_CFG
DUMMY_KEY = (
    "local-bridge-dummy"  # literal placeholder, not a credential (the proxies hold the real key)
)
DEFAULT_CLAUDE_TOOLS = "default"
HARNESSES = ("claude", "opencode")
MODEL_PORT = {
    "claude": S.SERVICES["bridge"][0],
    "opencode": S.SERVICES["passthrough"][0],
    "r4": S.SERVICES["passthrough"][0],
}
PROXY_LOG = {
    "claude": LOGS / "bridge.log",
    "opencode": LOGS / "passthrough.log",
    "r4": LOGS / "passthrough.log",
}
GUARD_LOG = LOGS / "egress_guard.log"
RUNNER_VERSION = "2026-10-03.fix1"
METRICS_DOC = {
    "metrics.total_tokens": "prompt tokens incl. cache reads/writes + completion tokens (R4's total_tokens "
    "definition); for cross-lane tables prefer proxy_usage.total_tokens",
    "proxy_usage.total_tokens": "sum over this case's model requests at the local proxy: prompt (incl. cached) "
    "+ completion; agent_* = requests that advertised tools, aux_* = requests "
    "without tools (title/summary/compaction: harness overhead)",
    "metrics.agent_steps": "assistant turns as the harness records them (claude: distinct API message ids in "
    "the transcript; opencode: assistant rows in session_message; r4: llm_calls)",
    "cost_usd": "dropped: Anthropic list prices are meaningless for glm-5.3",
}

sys.path.insert(0, str(REPO))
from benchmarks.harness.adapters.claude_code import ClaudeCodeAdapter  # noqa: E402  (reuse parser)
from benchmarks.harness.adapters.opencode import parse_opencode_events  # noqa: E402

# ----------------------------------------------------------------------------- prompt (R4's builder)


def load_run_glm(skip_gold: bool):
    """Import ../run_glm.py unchanged. With skip_gold the module's gold.json read is
    answered with an empty list in-process (smoke mode never opens the gold file)."""
    spec = importlib.util.spec_from_file_location("run_glm", CODE / "run_glm.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    if not skip_gold:
        spec.loader.exec_module(mod)
        return mod
    gold = os.path.realpath(BENCH / "gold/gold.json")
    real_open = builtins.open

    def guarded_open(file, *a, **k):
        if isinstance(file, (str, os.PathLike)) and os.path.realpath(file) == gold:
            return io.StringIO("[]")
        return real_open(file, *a, **k)

    builtins.open = guarded_open
    try:
        spec.loader.exec_module(mod)
    finally:
        builtins.open = real_open
    return mod


class Questions:
    def __init__(self, smoke: bool):
        self.smoke = smoke
        self.rg = load_run_glm(skip_gold=smoke)
        if smoke:
            self.rg.BENCH = (
                SMOKE  # prompt() reads the module global: corpus/MANIFEST paths -> smoke tree
            )
            self.q = {q["id"]: q for q in json.loads((SMOKE / "questions.json").read_text())}
        else:
            self.q = self.rg.GOLD

    def ids(self) -> list[str]:
        return list(self.q)

    def prompt(self, arm: str, qid: str) -> str:
        return self.rg.prompt(arm, self.q[qid])

    def root(self) -> Path:
        return SMOKE if self.smoke else BENCH


# ----------------------------------------------------------------------------- env / argv


def case_dirs(work: Path) -> dict[str, Path]:
    return {
        "work": work,
        "ws": work / "ws",
        "tmp": work / "tmp",
        "home": work / "home",
        "xdg_data": work / "xdg/data",
        "xdg_config": work / "xdg/config",
        "xdg_state": work / "xdg/state",
        "xdg_cache": work / "xdg/cache",
    }


def base_env(d: dict[str, Path]) -> dict[str, str]:
    """Pinned minimal environment, identical for every lane (run_r4.py imports it)."""
    proxy = f"http://127.0.0.1:{S.GUARD_PORT}"  # in-sandbox relay -> this case's own egress guard
    return {
        "PATH": S.PINNED_PATH,
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        "USER": os.environ.get("USER", "bench"),
        "LOGNAME": os.environ.get("USER", "bench"),
        "SHELL": "/bin/bash",
        "HOME": str(d["home"]),
        "TMPDIR": str(d["tmp"]),
        "PYTHONDONTWRITEBYTECODE": "1",
        # kgq's embedder reads models from the real hub cache (ro, models only); HF_HOME is scratch
        "HF_HOME": str(d["home"] / ".cache/huggingface"),
        "HF_HUB_CACHE": str(S.HF_HUB),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HTTPS_PROXY": proxy,
        "HTTP_PROXY": proxy,
        "https_proxy": proxy,
        "http_proxy": proxy,
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }


def harness_env(harness: str, d: dict[str, Path], tag: str, model: str) -> dict[str, str]:
    env = base_env(d)
    if harness == "claude":
        env.update(
            {
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{MODEL_PORT['claude']}",
                "ANTHROPIC_API_KEY": DUMMY_KEY,
                "ANTHROPIC_CUSTOM_HEADERS": f"x-bench-case: {tag}",
                # every model slot Claude Code may pick (main, subagents, background/haiku calls) -> glm-5.3;
                # the bridge forces it anyway, this keeps `requested=` honest in its log
                "ANTHROPIC_MODEL": model,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
                "ANTHROPIC_SMALL_FAST_MODEL": model,
                "CLAUDE_CODE_SUBAGENT_MODEL": model,
                "CLAUDE_CONFIG_DIR": str(d["home"] / ".claude"),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_TELEMETRY": "1",
                "DISABLE_ERROR_REPORTING": "1",
                "DISABLE_AUTOUPDATER": "1",
                "DISABLE_BUG_COMMAND": "1",
                # glm-5.3 serves a 1M window; without this Claude Code assumes 200k for unknown models
                "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "1000000",
                "API_TIMEOUT_MS": "1800000",
            }
        )
    else:
        env.update(
            {
                "XDG_DATA_HOME": str(d["xdg_data"]),
                "XDG_CONFIG_HOME": str(d["xdg_config"]),
                "XDG_STATE_HOME": str(d["xdg_state"]),
                "XDG_CACHE_HOME": str(d["xdg_cache"]),
                "OPENCODE_CONFIG": str(OPENCODE_CFG),
                # per-case tag header merged over the shared config (dummy key stays in the file)
                "OPENCODE_CONFIG_CONTENT": json.dumps(
                    {"provider": {"rikyu": {"options": {"headers": {"x-bench-case": tag}}}}}
                ),
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_DISABLE_MODELS_FETCH": "1",
                "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            }
        )
    return env


def harness_argv(harness: str, d: dict[str, Path], model: str, claude_tools: str) -> list[str]:
    if harness == "claude":
        argv = [
            str(S.claude_binary()),
            "-p",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
            "--model",
            model,
            "--debug-file",
            str(d["work"] / "claude-debug.log"),
        ]
        if claude_tools == "default":
            argv += ["--disallowedTools", "WebFetch,WebSearch"]
        else:
            argv += ["--tools", claude_tools, "--allowedTools", claude_tools]
        return argv
    return [
        str(OPENCODE_BIN),
        "run",
        "--standalone",
        "--format",
        "json",
        "--auto",
        "--model",
        f"rikyu/{model}",
    ]


# ----------------------------------------------------------------------------- metrics


def _num(s: str | None) -> int:
    try:
        return int(s) if s not in (None, "-", "None") else 0
    except ValueError:
        return 0


def _count(d: dict, key) -> None:
    d[key] = d.get(key, 0) + 1


def _json_lines(text: str):
    """Every line of text that parses as JSON, in order."""
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        yield ev


def _proxy_lines(log_path: Path, tag: str) -> tuple[set[str], list[dict[str, str]]]:
    needle = f"case={tag} "
    starts: set[str] = set()
    ends: list[dict[str, str]] = []
    if not log_path.exists():
        return starts, ends
    for line in log_path.read_text(errors="replace").splitlines():
        if needle not in line:
            continue
        m = re.search(r"#(\d+) (START|END) ", line)
        if not m:
            continue
        if m.group(2) == "START":
            starts.add(m.group(1))
        else:
            kv = dict(re.findall(r"(\w+)=(\S+)", line))
            kv["_rid"] = m.group(1)
            ends.append(kv)
    return starts, ends


def _settled_ends(log_path: Path, tag: str, wait_s: float) -> tuple[list[dict[str, str]], set[str]]:
    """(END records, request ids still without END) once all STARTs have ENDed or wait_s passed."""
    deadline = time.monotonic() + wait_s
    while True:
        starts, ends = _proxy_lines(log_path, tag)
        pending = starts - {e["_rid"] for e in ends}
        if not pending or time.monotonic() > deadline:
            return ends, pending
        time.sleep(1.0)


def _usage_record(requests: int, pending: int) -> dict:
    return {
        "requests": requests,
        "pending_unlogged": pending,
        "status": {},
        "agent_requests": 0,
        "aux_requests": 0,
        "prompt_tokens": 0,
        "cached_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "agent_total_tokens": 0,
        "aux_total_tokens": 0,
        "models": [],
        "upstream_models": [],
        "requested_models": [],
        "max_tokens": [],
        "temperature": [],
        "temp_src": [],
        "finish": {},
        "stream_errors": 0,
        "no_usage": 0,
        "media_dropped": 0,
        "tool_names": [],
        "called": {},
        "assistant_msgs_last": 0,
        "assistant_msgs_with_reasoning_last": 0,
        "requests_with_reasoning_carried": 0,
        "pings": 0,
    }


_DISTINCT_VALUES = (  # END-line key -> proxy_usage list of distinct values
    ("model", "models"),
    ("upstream_model", "upstream_models"),
    ("requested", "requested_models"),
    ("max_tokens", "max_tokens"),
    ("temperature", "temperature"),
    ("temp_src", "temp_src"),
)


def _request_tokens(kv: dict[str, str]) -> tuple[int, int, int, bool]:
    """(prompt incl. cached, cached, completion, usage missing) of one END line."""
    if "input_tokens" in kv:  # anthropic bridge line
        prompt = _num(kv.get("input_tokens")) + _num(kv.get("cache_read"))
        return (
            prompt,
            _num(kv.get("cache_read")),
            _num(kv.get("output_tokens")),
            kv.get("usage") == "missing",
        )
    # openai passthrough line
    return (
        _num(kv.get("prompt_tokens")),
        _num(kv.get("cached_tokens")),
        _num(kv.get("completion_tokens")),
        kv.get("prompt_tokens") in (None, "-"),
    )


def _add_tool_names(out: dict, kv: dict[str, str]) -> None:
    for n in (kv.get("tool_names") or "-").split(","):
        if n != "-" and n not in out["tool_names"]:
            out["tool_names"].append(n)
    for n in (kv.get("called") or "-").split(","):
        if n != "-":
            _count(out["called"], n)


def _add_request(out: dict, kv: dict[str, str]) -> None:
    """Fold one END line into the proxy_usage record."""
    _count(out["status"], kv.get("status", "?"))
    out["media_dropped"] += _num(kv.get("media_dropped")) + _num(kv.get("media_stripped"))
    for key, dst in _DISTINCT_VALUES:
        if kv.get(key) and kv[key] not in out[dst]:
            out[dst].append(kv[key])
    _count(out["finish"], kv.get("finish", "None"))
    if "stream_error" in kv:
        out["stream_errors"] += 1
    prompt, cached, compl, missing = _request_tokens(kv)
    if missing:
        out["no_usage"] += 1
    out["prompt_tokens"] += prompt
    out["cached_tokens"] += cached
    out["completion_tokens"] += compl
    side = "agent" if _num(kv.get("tools")) > 0 else "aux"
    out[f"{side}_requests"] += 1
    out[f"{side}_total_tokens"] += prompt + compl
    _add_tool_names(out, kv)
    if _num(kv.get("asst_reasoning")) > 0:
        out["requests_with_reasoning_carried"] += 1
    out["pings"] += _num(kv.get("pings"))


def proxy_usage(log_path: Path, tag: str, wait_s: float = 30.0) -> dict:
    """Harness-independent per-case record from the local proxy log (case tag match).
    Waits (up to wait_s) until every START line of the case has its END line, so a request
    cancelled by a timeout is still counted."""
    ends, pending = _settled_ends(log_path, tag, wait_s)
    out = _usage_record(len(ends), len(pending))
    for kv in ends:
        _add_request(out, kv)
    if ends:
        last = max(ends, key=lambda e: _num(e.get("asst")))
        out["assistant_msgs_last"] = _num(last.get("asst"))
        out["assistant_msgs_with_reasoning_last"] = _num(last.get("asst_reasoning"))
    out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
    return out


def _note_assistant(msg: dict, msg_ids: set[str], by_name: dict[str, int]) -> None:
    if msg.get("id"):
        msg_ids.add(msg["id"])
    for b in msg.get("content") or []:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            _count(by_name, b.get("name", "?"))


def claude_transcript(d: dict[str, Path]) -> dict:
    """Tool calls by name + distinct API responses from the scratch-HOME session transcript."""
    by_name: dict[str, int] = {}
    msg_ids: set[str] = set()
    proj = d["home"] / ".claude/projects"
    for f in proj.rglob("*.jsonl") if proj.exists() else []:
        for ev in _json_lines(f.read_text(errors="replace")):
            if ev.get("type") == "assistant":
                _note_assistant(ev.get("message") or {}, msg_ids, by_name)
    return {
        "agent_steps": len(msg_ids),
        "tool_calls": sum(by_name.values()),
        "tool_calls_by_name": by_name,
    }


def _result_json(text: str):
    """stdout as one JSON document, else its last line that parses as JSON (None: neither)."""
    for cand in [text] + [ln for ln in reversed(text.splitlines()) if ln.strip().startswith("{")]:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None


def _claude_fields(payload: dict) -> dict:
    usage = payload.get("usage") or {}
    return {
        "num_turns": payload.get("num_turns"),
        "duration_ms": payload.get("duration_ms"),
        "duration_api_ms": payload.get("duration_api_ms"),
        "input_tokens": usage.get("input_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "subtype": payload.get("subtype"),
        "stop_reason": payload.get("stop_reason"),
        "terminal_reason": payload.get("terminal_reason"),
        "permission_denials": len(payload.get("permission_denials") or []),
    }


def claude_metrics(
    stdout: str, d: dict[str, Path], elapsed: float
) -> tuple[dict, str, str | None, bool]:
    tr = claude_transcript(d)
    payload = _result_json(stdout.strip())
    if payload is None:  # e.g. timeout: keep what the transcript shows
        return {"wallclock_sec": round(elapsed, 3), **tr}, "", "no JSON result on stdout", False
    base = ClaudeCodeAdapter._render_result(
        json.dumps(payload), elapsed, via_gateway=True
    )  # repo parser
    m = {k: v for k, v in dict(base.metrics).items() if k not in ("cost_usd", "total_cost_usd")}
    m.update(_claude_fields(payload))
    m["prompt_tokens_incl_cache"] = (
        m["input_tokens"] + m["cache_read_input_tokens"] + m["cache_creation_input_tokens"]
    )
    m["total_tokens"] = (
        m["prompt_tokens_incl_cache"] + m["output_tokens"]
    )  # R4 definition (was input+output)
    m.update(tr)
    m["llm_calls"] = tr["agent_steps"] or m.get("llm_calls")
    ok = not payload.get("is_error")
    return (
        m,
        str(payload.get("result", "")),
        None if ok else (base.error or "claude reported is_error"),
        ok,
    )


_SESSION_TOKENS = (
    "tokens_input",
    "tokens_output",
    "tokens_reasoning",
    "tokens_cache_read",
    "tokens_cache_write",
)


def _session_totals(allsess: list) -> dict[str, int]:
    return {k: sum((r[k] or 0) for r in allsess) for k in _SESSION_TOKENS}


def _add_step_tokens(step_tok: dict[str, int], tk: dict) -> None:
    step_tok["input"] += int(tk.get("input") or 0)
    step_tok["output"] += int(tk.get("output") or 0)
    step_tok["reasoning"] += int(tk.get("reasoning") or 0)
    step_tok["cache_read"] += int((tk.get("cache") or {}).get("read") or 0)
    step_tok["cache_write"] += int((tk.get("cache") or {}).get("write") or 0)


def _step_texts(data: dict, db_by_name: dict[str, int]) -> list[str]:
    """Text parts of one assistant message; counts its tool parts into db_by_name."""
    texts = []
    for c in data.get("content") or []:
        if c.get("type") == "tool":
            _count(db_by_name, c.get("name") or c.get("tool") or "?")
        elif c.get("type") == "text" and c.get("text"):
            texts.append(c["text"])
    return texts


def _assistant_steps(con: sqlite3.Connection, root_ids: set) -> tuple:
    """Walk the assistant rows -> (steps, root steps, step tokens, tools by name, last root text)."""
    steps_all = steps_root = 0
    step_tok = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    db_by_name: dict[str, int] = {}
    last_text = None
    for r in con.execute(
        "select session_id, data from session_message where type='assistant' "
        "order by time_created, seq"
    ):
        data = json.loads(r["data"])
        steps_all += 1
        in_root = r["session_id"] in root_ids
        steps_root += in_root
        _add_step_tokens(step_tok, data.get("tokens") or {})
        texts = _step_texts(data, db_by_name)
        if texts and in_root:  # the answer comes from the root session only
            last_text = "".join(texts)
    return steps_all, steps_root, step_tok, db_by_name, last_text


def _opencode_db(db: Path, m: dict, by_name: dict[str, int]) -> tuple[str | None, bool]:
    """Fill m from the session DB (in place, so a sqlite error keeps what was read) ->
    (last root-session text, idle_outcome == succeeded)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    roots = con.execute(
        "select id, idle_outcome from session_v2 where parent_id is null order by time_created"
    ).fetchall()
    allsess = con.execute("select * from session_v2").fetchall()
    tot = _session_totals(allsess)
    m.update({f"session_{k}": v for k, v in tot.items()})
    m["sessions"] = len(allsess)
    m["idle_outcome"] = roots[-1]["idle_outcome"] if roots else None
    steps_all, steps_root, step_tok, db_by_name, last_text = _assistant_steps(
        con, {r["id"] for r in roots}
    )
    con.close()
    m["agent_steps"] = steps_all
    m["agent_steps_root"] = steps_root
    m["llm_calls"] = steps_all
    m.update({f"steps_tokens_{k}": v for k, v in step_tok.items()})
    m["prompt_tokens_incl_cache"] = (
        step_tok["input"] + step_tok["cache_read"] + step_tok["cache_write"]
    )
    m["total_tokens"] = m["prompt_tokens_incl_cache"] + step_tok["output"] + step_tok["reasoning"]
    sess_total = (
        tot["tokens_input"]
        + tot["tokens_cache_read"]
        + tot["tokens_cache_write"]
        + tot["tokens_output"]
        + tot["tokens_reasoning"]
    )
    m["overhead_tokens_title_etc"] = max(sess_total - m["total_tokens"], 0)
    m["tool_calls_by_name"] = db_by_name or by_name
    return last_text, m.get("idle_outcome") == "succeeded"


def _stdout_tool_counts(stdout: str) -> dict[str, int]:
    """Tool calls by name from OpenCode's JSON event stream (fallback when the DB has none)."""
    by_name: dict[str, int] = {}
    for e in _json_lines(stdout):
        if e.get("type") == "tool_use":
            _count(by_name, (e.get("part") or {}).get("tool", "?"))
    return by_name


def _db_view(db: Path, m: dict, by_name: dict[str, int]) -> tuple[str | None, bool]:
    if not db.exists():
        return None, False
    try:
        return _opencode_db(db, m, by_name)
    except sqlite3.Error as exc:
        m["db_error"] = type(exc).__name__
        return None, False


def opencode_metrics(
    stdout: str, d: dict[str, Path], elapsed: float
) -> tuple[dict, str, str | None, bool]:
    ev = parse_opencode_events(
        stdout
    )  # repo parser (kept for reference; wrong on v2.0.18, see caveats)
    m: dict = {f"events_{k}": v for k, v in ev["metrics"].items() if k not in ("cost_usd",)}
    m["wallclock_sec"] = round(elapsed, 3)
    by_name = _stdout_tool_counts(stdout)
    text = ev["response_text"]
    last_text, ok = _db_view(d["xdg_data"] / "opencode/opencode.db", m, by_name)
    text = last_text or text  # the DB's root-session answer wins when there is one
    if "tool_calls_by_name" not in m:
        m["tool_calls_by_name"] = by_name
        m["llm_calls"] = ev["metrics"].get("llm_calls")
    m["tool_calls"] = sum(m["tool_calls_by_name"].values())
    ok = ok or bool(text)
    return m, text, None if ok else "no final text", ok


BENCH_PATH_NEEDLES = {
    "gold": [str(BENCH / "gold")],
    "results": [
        str(BENCH / "results"),
        str(R4GLM / "results"),
        "results_claude",
        "results_opencode",
        "results_r4",
    ],
}


def bench_path_mentions(root: Path) -> dict[str, int]:
    """Counts only: occurrences of gold/results paths in everything the case wrote
    (transcripts, session DBs, stdout) — a non-zero count means the agent went looking."""
    out = dict.fromkeys(BENCH_PATH_NEEDLES, 0)
    for f in root.rglob("*"):
        if not f.is_file() or f.stat().st_size > 200_000_000:
            continue
        try:
            data = f.read_bytes()
        except OSError:
            continue
        for k, needles in BENCH_PATH_NEEDLES.items():
            out[k] += sum(data.count(n.encode()) for n in needles)
    return out


# ----------------------------------------------------------------------------- one case


def results_dir(smoke: bool, harness: str) -> Path:
    return (SMOKE_OUT if smoke else HERE) / f"results_{harness}"


def work_dir(smoke: bool, *parts: str) -> Path:
    """Case dir under the lane's work/ (smoke-run outputs or the real lane dir)."""
    return (SMOKE_OUT if smoke else HERE).joinpath("work", *parts)


def new_tag(harness: str, arm: str, qid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", f"{harness}.{arm}.{qid}.{uuid.uuid4().hex[:8]}")


def cached_result(out: Path, redo_failed: bool) -> dict | None:
    """The stored result (marked _skipped) unless it is absent or a failure to redo."""
    if not out.exists():
        return None
    prev = json.loads(out.read_text())
    if redo_failed and prev.get("status") != "success":
        return None
    prev["_skipped"] = True
    return prev


def fresh_case_dirs(work: Path) -> dict[str, Path]:
    """Wipe the case dir and create its scratch layout (case_dirs)."""
    if work.exists():
        shutil.rmtree(work)
    d = case_dirs(work)
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d


def guarded_run(
    tag: str, box_kw: dict, input_text: str | None, timeout: float, settle: float = 0.5
) -> tuple[int | None, str, str, bool, float, list]:
    """Run one sandbox behind its own egress guard ->
    (rc, stdout, stderr, timed_out, wall_s, guard denials)."""
    t0 = time.monotonic()
    with CaseGuard(RUN / f"g-{uuid.uuid4().hex[:12]}.sock", tag, GUARD_LOG) as guard:
        box = S.Box(**box_kw, guard_sock=guard.sock_path)
        rc, stdout, stderr, timed_out = S.run(box, input_text, timeout)
        wall = round(time.monotonic() - t0, 1)
        time.sleep(settle)
        denials = list(guard.denials)
    return rc, stdout, stderr, timed_out, wall, denials


def case_status(timed_out: bool, success) -> str:
    return "timeout" if timed_out else ("success" if success else "failed")


def case_proxy_usage(lane: str, tag: str, timed_out: bool) -> dict:
    return proxy_usage(PROXY_LOG[lane], tag, wait_s=30.0 if timed_out else 10.0)


def session_files(work: Path, d: dict[str, Path]) -> list[str]:
    """Up to 500 files the harness left in its scratch HOME and XDG dirs (paths only)."""
    return sorted(
        str(x.relative_to(work))
        for base in (d["home"], work / "xdg")
        for x in base.rglob("*")
        if x.is_file()
    )[:500]


def r4_prompt_match(arm: str, qid: str, text: str) -> bool | None:
    """Prompt identical to the one run_glm.py gave the R4 companion (None: no such run)."""
    r4p = R4GLM / "work" / arm / qid / "PROMPT.txt"
    return (r4p.read_text() == text) if r4p.exists() else None


def _harness_metrics(
    harness: str, stdout: str, d: dict[str, Path], wall: float
) -> tuple[dict, str, str | None, bool]:
    if harness == "claude":
        return claude_metrics(stdout, d, wall)
    return opencode_metrics(stdout, d, wall)


def _anthropic_mentions(work: Path, stderr: str) -> dict[str, int]:
    dbg = work / "claude-debug.log"
    dtxt = dbg.read_text(errors="replace") if dbg.exists() else ""
    return {"debug_log": dtxt.count("anthropic.com"), "stderr": stderr.count("anthropic.com")}


def run_case(Q: Questions, harness: str, arm: str, qid: str, a, run_meta: dict) -> dict:
    out = results_dir(Q.smoke, harness) / f"{arm}__{qid}.json"
    prev = cached_result(out, a.redo_failed)
    if prev is not None:
        return prev
    work = work_dir(Q.smoke, harness, arm, qid)
    d = fresh_case_dirs(work)
    text = Q.prompt(arm, qid)
    (work / "PROMPT.txt").write_text(text)
    tag = new_tag(harness, arm, qid)
    env = harness_env(harness, d, tag, a.model)
    cmd = harness_argv(harness, d, a.model, a.claude_tools)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    box_kw = {
        "lane": harness,
        "arm": arm,
        "work": work,
        "cwd": d["ws"],
        "env": env,
        "cmd": cmd,
        "corpus_root": Q.root(),
        "tool_tree": not Q.smoke,
    }
    rc, stdout, stderr, timed_out, wall, denials = guarded_run(tag, box_kw, text, a.timeout)
    (work / "stdout.txt").write_text(stdout)
    (work / "stderr.txt").write_text(stderr)
    metrics, resp, err, ok = _harness_metrics(harness, stdout, d, wall)
    status = case_status(timed_out, rc == 0 and ok)
    pu = case_proxy_usage(harness, tag, timed_out)
    metrics["model_requests"] = pu["requests"]
    payload = {
        "harness": harness,
        "arm": arm,
        "qid": qid,
        "model": a.model,
        "case_tag": tag,
        "status": status,
        "rc": rc,
        "error": err if status != "success" else None,
        "response_text": resp,
        "has_final_answer": "FINAL ANSWER:" in (resp or ""),
        "wall_s": wall,
        "metrics": metrics,
        "tool_calls": {
            "total": metrics.get("tool_calls"),
            "by_name": metrics.get("tool_calls_by_name"),
        },
        "proxy_usage": pu,
        "egress_denied": denials,
        "bench_path_mentions": bench_path_mentions(work),
        "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "smoke": Q.smoke,
        "claude_tools": a.claude_tools if harness == "claude" else None,
        "run": {**run_meta, "case_started_at": started_at},
        "metrics_doc": METRICS_DOC,
        "stderr_tail": stderr[-3000:],
        "session_files": session_files(work, d),
    }
    if harness == "claude":
        payload["anthropic_host_mentions"] = _anthropic_mentions(work, stderr)
    if not Q.smoke:
        payload["prompt_matches_r4"] = r4_prompt_match(arm, qid, text)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    return payload


# ----------------------------------------------------------------------------- probe / preflight


class _UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 10):
        super().__init__("bench", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def healthz(sock: Path) -> bool:
    try:
        c = _UnixHTTP(str(sock), timeout=5)
        c.request("GET", "/healthz")
        ok = c.getresponse().status == 200
        c.close()
        return ok
    except OSError:
        return False


def preflight(lanes: list[str], arms: list[str], smoke: bool) -> None:
    need = {"claude": "bridge", "opencode": "passthrough", "r4": "passthrough"}
    for h in lanes:
        sock = S.SERVICES[need[h]][1]
        if not healthz(sock):
            sys.exit(f"preflight: {h} proxy not answering on unix:{sock} (run start_proxies.sh)")
    if not smoke:
        for arm in arms:
            for svc in S.ARM_SERVICES[arm]:
                if not S.SERVICES[svc][1].is_socket():
                    sys.exit(
                        f"preflight: arm {arm} needs {svc} relay socket (run start_proxies.sh)"
                    )


PROBE_SH = r"""
chk() { if eval "$2" >/dev/null 2>&1; then echo "{\"check\":\"$1\",\"result\":true}"; else echo "{\"check\":\"$1\",\"result\":false}"; fi; }
tcp() { timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/$1"; }
chk write_ws            "echo ok > \"$PWD/probe.txt\""
chk write_tmpdir        "echo ok > \"$TMPDIR/probe.txt\""
chk write_home_scratch  "echo ok > \"$HOME/probe.txt\""
chk ro_corpus           "! ( echo x > \"$CORPUS_DIR/PROBE_SHOULD_FAIL\" )"
# private scratch areas: these writes succeed in the sandbox; the host-side check proves they stayed there
for p in "/tmp/PROBE_PRIVATE" "/dev/shm/PROBE_PRIVATE" "$REAL_HOME/PROBE_PRIVATE" "/var/tmp/PROBE_PRIVATE"; do echo x > "$p" 2>/dev/null; done
chk visible_corpus_manifest "test -f \"$CORPUS_DIR/MANIFEST.md\""
chk visible_harness_bin "test -x \"$HARNESS_BIN\" || test -z \"$HARNESS_BIN\""
chk absent_r4_sources   "! test -e \"$REAL_HOME/.config/r4/sources.json\""
chk absent_r4_config    "! test -e \"$REAL_HOME/.config/r4/config.json\""
chk absent_claude_login "! test -e \"$REAL_HOME/.claude/.credentials.json\" && ! test -e \"$REAL_HOME/.claude.json\" && ! test -e \"$REAL_HOME/.claude\""
chk absent_claude_logs  "! test -e \"$REAL_HOME/.cache/claude\" && ! test -e \"$REAL_HOME/.cache/claude-cli-nodejs\""
chk absent_opencode_auth "! test -e \"$REAL_HOME/.local/share/opencode\" && ! test -e \"$REAL_HOME/.config/opencode\""
chk absent_histories    "! test -e \"$REAL_HOME/.bash_history\" && ! test -e \"$REAL_HOME/.python_history\""
chk absent_ssh_gnupg    "! test -e \"$REAL_HOME/.ssh\" && ! test -e \"$REAL_HOME/.gnupg\""
chk absent_keyrings_ansible "! test -e \"$REAL_HOME/.local/share/keyrings\" && ! test -e \"$REAL_HOME/.ansible\""
chk absent_hf_token     "! test -e \"$REAL_HOME/.cache/huggingface/token\" && ! test -e \"$REAL_HOME/.cache/huggingface/stored_tokens\""
chk absent_repo_checkout "! test -e \"$REPO/CLAUDE.md\" && ! test -e \"$REPO/.git\" && ! test -e \"$REPO/docs\""
chk absent_home_opencode_cfg "! test -e \"$REAL_HOME/opencode.json\" && ! test -e \"$REAL_HOME/.opencode/package.json\""
chk absent_gold         "! test -e \"$BENCH/gold\""
chk absent_results      "! test -e \"$BENCH/results\" && ! test -e \"$BENCH/r4glm/results\" && ! ls -d \"$BENCH\"/r4glm/harness/results_* \"$BENCH\"/r4glm/harness/smoke/results_* 2>/dev/null | grep -q ."
chk absent_other_cases  "! test -e \"$BENCH/r4glm/work\" && test \"\$(ls -A \"\$(dirname \"$CASE_DIR\")\")\" = \"\$(basename \"$CASE_DIR\")\""
chk absent_user_runtime "! test -e /run/user"
chk absent_docker_podman "! test -e /var/run/docker.sock && ! test -e /run/podman"
chk absent_ssh_agent    "test -z \"\${SSH_AUTH_SOCK:-}\" && test -z \"\${DBUS_SESSION_BUS_ADDRESS:-}\""
chk absent_cc_socks     "! ls -d /tmp/cc-socks* 2>/dev/null | grep -q ."
python3 - <<'PYEOF'
import json, os, socket, stat
found, reachable = [], []
for top in ("/",):
    for dp, dns, fns in os.walk(top):
        if dp in ("/proc", "/sys", "/run/bench") or dp.startswith(("/proc/", "/sys/")):
            dns[:] = []
            continue
        for n in fns + dns:
            p = os.path.join(dp, n)
            try:
                if stat.S_ISSOCK(os.lstat(p).st_mode):
                    found.append(p)
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(1)
                    try:
                        s.connect(p); reachable.append(p)
                    except OSError:
                        pass
                    finally:
                        s.close()
            except OSError:
                pass
print(json.dumps({"check": "unix_sockets_outside_run_bench", "result": not reachable,
                  "found": len(found), "connectable": reachable[:20]}))
PYEOF
chk netns_isolated      "test \"\$(readlink /proc/self/ns/net)\" != \"$HOST_NETNS\""
chk ipcns_isolated      "test \"\$(readlink /proc/self/ns/ipc)\" != \"$HOST_IPCNS\""
chk utsns_isolated      "test \"\$(hostname)\" = bench-sandbox"
chk no_route_internet   "! timeout 5 bash -c 'exec 3<>/dev/tcp/1.1.1.1/443'"
chk no_dns              "! timeout 5 getent hosts api.anthropic.com"
chk no_host_litellm_18002 "! tcp 18002"
chk no_host_opencode_49374 "! tcp 49374"
chk no_host_nimble_8331 "! tcp 8331"
chk no_host_kubelet_10250 "! tcp 10250"
chk no_host_s3_58333_unless_B "test \"$ARM\" = B || ! tcp 58333"
chk model_proxy_healthz "curl -sf -o /dev/null --max-time 5 http://127.0.0.1:$MODEL_PORT/healthz"
curl -s -o /dev/null --max-time 5 -x http://127.0.0.1:8339 https://api.anthropic.com/ >/dev/null 2>&1
chk pid_namespace       "test \$(ls /proc | grep -c '^[0-9]') -lt 20"
chk no_capabilities     "grep -qE '^CapEff:[[:space:]]+0+$' /proc/self/status"
chk env_clean           "! env | grep -qE '^(HF_TOKEN|SSH_AUTH_SOCK|CLAUDE_CODE_MESSAGING_TOKEN|ANTHROPIC_AUTH_TOKEN|OPENAI_API_KEY|DBUS_SESSION_BUS_ADDRESS|XDG_RUNTIME_DIR|BENCH_RELAYS)='"
chk env_has_no_real_key "! env | grep -qiE 'sk-[A-Za-z0-9]{8}|Bearer '"
chk path_pinned         "test \"\$PATH\" = \"$EXPECT_PATH\""
chk python3_runs        "python3 -c 'import sys'"
chk ffmpeg_ffprobe_run  "ffmpeg -version && ffprobe -version"
echo "{\"check\":\"corpus_count\",\"files\":$(find -L "$CORPUS_DIR" -type f 2>/dev/null | wc -l),\"broken_links\":$(find -L "$CORPUS_DIR" -type l 2>/dev/null | wc -l)}"
if [ "$ARM" = C ] || [ "$ARM" = P ]; then
  chk visible_kgq        "test -x \"$BENCH/proto/kgq\""
  chk visible_tool_card  "test -f \"$BENCH/proto/TOOL_CARD.md\""
  chk ro_proto           "! ( echo x > \"$BENCH/proto/PROBE_SHOULD_FAIL\" )"
  chk write_proto_out    "echo x > \"$BENCH/proto/out/.probe_$$\" && rm -f \"$BENCH/proto/out/.probe_$$\""
  chk kgq_cli_starts     "\"$BENCH/proto/kgq\" --help"
  chk pg_login_via_relay "python3 -c \"import psycopg2; psycopg2.connect('postgresql://test:test@localhost:55432/postgres', connect_timeout=5).close()\""
  chk hf_hub_visible     "test -d \"$HF_HUB_CACHE\""
else
  chk no_pg_outside_kgq_arms "! tcp 55432"
  chk no_proto_tree      "! test -e \"$BENCH/proto\" && ! test -e \"$BENCH/current\""
fi
"""


def _probe_env(
    root: Path, lane: str, arm: str, model: str, d: dict[str, Path], tag: str
) -> dict[str, str]:
    env = harness_env(lane, d, tag, model) if lane in HARNESSES else base_env(d)
    harness_bin = {"claude": str(S.claude_binary()), "opencode": str(OPENCODE_BIN)}.get(lane, "")
    env.update(
        {
            "CORPUS_DIR": str(root / "corpus"),
            "REAL_HOME": str(REAL_HOME),
            "REPO": str(REPO),
            "BENCH": str(BENCH),
            "HARNESS_BIN": harness_bin,
            "ARM": arm,
            "CASE_DIR": str(d["work"]),
            "MODEL_PORT": str(MODEL_PORT[lane]),
            "EXPECT_PATH": S.PINNED_PATH,
            "HOST_NETNS": S.host_netns(),
            "HOST_IPCNS": os.readlink("/proc/self/ns/ipc"),
        }
    )
    return env


def _host_corpus_counts(corpus: Path) -> list[str]:
    """[files, broken links] under the corpus as the host sees it (find -L; counts only)."""
    return subprocess.run(
        [
            "bash",
            "-c",
            'echo $(find -L "$1" -type f 2>/dev/null | wc -l) '
            '$(find -L "$1" -type l 2>/dev/null | wc -l)',
            "_",
            str(corpus),
        ],
        capture_output=True,
        text=True,
    ).stdout.split()


def _compare_corpus_count(res: list[dict], host: list[str]) -> None:
    for r in res:
        if r.get("check") == "corpus_count":
            r["host_files"], r["host_broken_links"] = int(host[0]), int(host[1])
            r["result"] = (
                r["files"] == r["host_files"]
                and r["files"] > 0
                and r["broken_links"] == r["host_broken_links"]
            )


def _leaked_probe_files(root: Path) -> list[str]:
    return [
        str(x)
        for x in (
            root / "corpus/PROBE_SHOULD_FAIL",
            REAL_HOME / "PROBE_PRIVATE",
            REPO / "PROBE_SHOULD_FAIL",
            BENCH / "proto/PROBE_SHOULD_FAIL",
            Path("/tmp/PROBE_PRIVATE"),
            Path("/dev/shm/PROBE_PRIVATE"),
            Path("/var/tmp/PROBE_PRIVATE"),
        )
        if x.exists()
    ]


def probe(root: Path, smoke: bool, lane: str, arm: str, model: str) -> list[dict]:
    work = work_dir(smoke, "_probe", f"{lane}-{arm}")
    d = fresh_case_dirs(work)
    tag = f"probe.{lane}.{arm}.{uuid.uuid4().hex[:8]}"
    box_kw = {
        "lane": lane,
        "arm": arm,
        "work": work,
        "cwd": d["ws"],
        "env": _probe_env(root, lane, arm, model, d, tag),
        "cmd": ["bash", "-c", PROBE_SH],
        "corpus_root": root,
        "tool_tree": not smoke,
    }
    rc, out, err, _, _, denials = guarded_run(tag, box_kw, None, 300, settle=0.3)
    res = [json.loads(ln) for ln in out.splitlines() if ln.startswith("{")]
    if not res:
        res.append({"check": "probe_ran", "result": False, "rc": rc, "stderr_tail": err[-300:]})
    _compare_corpus_count(res, _host_corpus_counts(root / "corpus"))
    res.append(
        {
            "check": "egress_guard_logged_attempt",
            "result": any("api.anthropic.com:443" in x for x in denials),
            "denials": len(denials),
        }
    )
    leaked = _leaked_probe_files(root)
    res.append({"check": "host_sees_no_probe_files", "result": not leaked, "leaked": leaked})
    return res


KGQ_SH = r"""
t0=$EPOCHREALTIME
if [ "$ARM" = B ]; then
  ( cd "$BENCH/current" && KG_VARIANT=llm-on ./kgq-current "test" --project "$PROJ" ) >/dev/null 2>&1
else
  "$BENCH/proto/kgq" --project "$PROJ" brief "test" >/dev/null 2>&1
fi
rc=$?
t1=$EPOCHREALTIME
echo "{\"check\":\"kgq_${ARM}_${PROJ}\",\"rc\":$rc,\"secs\":$(awk "BEGIN{printf \"%.1f\", $t1 - $t0}")}"
"""


def _kgq_one(arm: str, proj: str) -> list[dict]:
    work = HERE / "work" / "_kgq_check" / f"{arm}-{proj}"
    d = fresh_case_dirs(work)
    env = base_env(d)
    env.update({"BENCH": str(BENCH), "ARM": arm, "PROJ": proj})
    box = S.Box(
        lane="probe",
        arm=arm,
        work=work,
        cwd=d["ws"],
        env=env,
        cmd=["bash", "-c", KGQ_SH],
        corpus_root=BENCH,
        services=S.ARM_SERVICES[arm],
    )
    rc, out, err, timed_out = S.run(box, None, 600)
    lines = [json.loads(ln) for ln in out.splitlines() if ln.startswith("{")]
    if timed_out or not lines:
        lines = [{"check": f"kgq_{arm}_{proj}", "rc": rc, "timed_out": timed_out}]
    for r in lines:
        r["result"] = r.get("rc") == 0
    return lines


def kgq_check(arms: list[str], projects: list[str]) -> list[dict]:
    """Run the arm's real knowledge tool once per project inside the arm's real sandbox (real
    corpus tree, kgq relays) with a synthetic query; prints ONLY exit code and seconds."""
    res = []
    for arm in arms:
        for proj in projects:
            res += _kgq_one(arm, proj)
    return res


# ----------------------------------------------------------------------------- main


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--harness", default="claude,opencode")
    ap.add_argument("--model", default="glm-5.3")
    ap.add_argument(
        "--qids", default=None, help="ID,ID or 'all' (smoke: defaults to all smoke questions)"
    )
    ap.add_argument("--arms", default="A,C")
    ap.add_argument("--par", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument(
        "--smoke", action="store_true", help="synthetic corpus/questions in smoke/ (arm A only)"
    )
    ap.add_argument(
        "--probe", action="store_true", help="sandbox probe only (checks + counts, no content)"
    )
    ap.add_argument(
        "--kgq-check",
        action="store_true",
        help="run kgq (arm C) / kgq-current (arm B, with --arms B) once per project inside the "
        "real arm sandbox with the query 'test'; prints rc + seconds only",
    )
    ap.add_argument(
        "--projects", default="example-project-a,example-project-b", help="projects for --kgq-check"
    )
    ap.add_argument(
        "--show-prompt", metavar="ARM", help="print the R4 prompt for the (smoke) question and exit"
    )
    ap.add_argument(
        "--claude-tools",
        default=DEFAULT_CLAUDE_TOOLS,
        help="'default' = stock Claude Code tools minus WebFetch/WebSearch (primary comparison); "
        "'Read,Grep,Glob,Bash' = restricted ablation",
    )
    ap.add_argument("--redo-failed", action="store_true")
    ap.add_argument(
        "--run-note",
        default="",
        help="free text recorded in every result, e.g. which other lanes run concurrently on rikyu",
    )
    return ap


def csv_list(s: str) -> list[str]:
    return [x for x in s.split(",") if x]


def print_checks(label: str, results: list[dict]) -> bool:
    """One PASS/FAIL line per check (+ its non-content extras); True when all passed."""
    allok = True
    for r in results:
        extra = {k: v for k, v in r.items() if k not in ("check", "result")}
        print(
            f"{label} {r['check']}: {'PASS' if r['result'] else 'FAIL'}"
            f"{' ' + json.dumps(extra) if extra else ''}"
        )
        allok &= bool(r["result"])
    return allok


def select_qids(Q: Questions, a) -> list[str]:
    """--qids (all/None = every question; real runs must name them), all known to Q."""
    if not a.smoke and a.qids is None:
        sys.exit("--qids required for real runs")
    qids = Q.ids() if (a.qids in (None, "all")) else a.qids.split(",")
    missing = [q for q in qids if q not in Q.q]
    if missing:
        sys.exit(f"unknown qids: {len(missing)} id(s) not in the question set")
    return qids


def _harnesses(spec: str) -> list[str]:
    harnesses = csv_list(spec)
    for h in harnesses:
        if h not in HARNESSES:
            sys.exit(f"unknown harness {h!r} (R4 runs through run_r4.py)")
    return harnesses


def run_jobs(par: int, jobs: list[tuple[str, tuple]]) -> None:
    """(label, (fn, *args)) jobs on par workers, submitted in order; one line per finished case."""
    with cf.ThreadPoolExecutor(par) as ex:
        futs = {ex.submit(*call): label for label, call in jobs}
        for f in cf.as_completed(futs):
            report_case(futs[f], f)


def report_case(label: str, f: cf.Future) -> None:
    """One summary line for a finished case future (counts and status only)."""
    try:
        r = f.result()
        m = r.get("metrics", {})
        pu = r.get("proxy_usage", {})
        print(
            f"{label} {r.get('status')}{' (cached)' if r.get('_skipped') else ''} "
            f"wall={r.get('wall_s')} steps={m.get('agent_steps')} tools={m.get('tool_calls')} "
            f"tok={m.get('total_tokens')} proxy_req={pu.get('requests')} proxy_tok={pu.get('total_tokens')} "
            f"egress_denied={len(r.get('egress_denied') or [])} final={r.get('has_final_answer')}",
            flush=True,
        )
    except Exception as e:  # noqa: BLE001
        print(f"{label} ERROR {type(e).__name__}: {e}", flush=True)


def _kgq_main(a, arms: list[str]) -> int:
    res = kgq_check([x for x in arms if x in ("B", "C")] or ["C"], a.projects.split(","))
    for r in res:
        print(
            f"{r['check']}: {'PASS' if r['result'] else 'FAIL'} rc={r.get('rc')} secs={r.get('secs')}"
        )
    return 0 if all(r["result"] for r in res) else 1


def _probe_main(a, harnesses: list[str], arms: list[str]) -> int:
    # real mode probes the real tree: output is check names, PASS/FAIL and counts only
    root = SMOKE if a.smoke else BENCH
    allok = True
    for h in harnesses:
        for arm in arms:
            allok &= print_checks(f"{h} {arm}", probe(root, a.smoke, h, arm, a.model))
    return 0 if allok else 1


def _show_prompt(Q: Questions, a) -> int:
    if not a.smoke:
        sys.exit("--show-prompt is smoke-only (real questions never go to the terminal)")
    for qid in Q.ids():
        t = Q.prompt(a.show_prompt, qid)
        print(
            f"--- {a.show_prompt} {qid} sha256={hashlib.sha256(t.encode()).hexdigest()} chars={len(t)}"
        )
        print(t)
    return 0


def _run_meta(a, harnesses: list[str], arms: list[str], qids: list[str]) -> dict:
    return {
        "runner": RUNNER_VERSION,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "par": a.par,
        "run_note": a.run_note,
        "lanes_in_this_invocation": harnesses,
        "arms_in_this_invocation": arms,
        "n_cases_in_this_invocation": len(qids) * len(arms) * len(harnesses),
        "note": "wall-clock depends on rikyu load and prefix caching: compare tokens/steps/tool calls "
        "first; wall only between runs with similar par/concurrency",
    }


def _case_jobs(Q: Questions, a, harnesses, arms, qids, run_meta) -> list[tuple[str, tuple]]:
    return [
        (f"{h} {arm} {q}", (run_case, Q, h, arm, q, a, run_meta))
        for q in qids
        for arm in arms
        for h in harnesses
    ]


def main() -> int:
    a = _parser().parse_args()
    harnesses = _harnesses(a.harness)
    arms = csv_list(a.arms)
    if a.kgq_check:
        return _kgq_main(a, arms)
    if a.smoke and any(x != "A" for x in arms):
        sys.exit(
            "smoke mode is arm A only (it never exposes the real proto/ or current/ trees); "
            "use --kgq-check for the arm-C sandbox"
        )
    if a.probe:
        return _probe_main(a, harnesses, arms)
    Q = Questions(smoke=a.smoke)
    if a.show_prompt:
        return _show_prompt(Q, a)
    qids = select_qids(Q, a)
    preflight(harnesses, arms, a.smoke)
    run_meta = _run_meta(a, harnesses, arms, qids)
    run_jobs(a.par, _case_jobs(Q, a, harnesses, arms, qids, run_meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
