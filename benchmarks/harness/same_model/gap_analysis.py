#!/usr/bin/env python3
"""Structural harness-gap analysis (R4 vs Claude Code vs OpenCode, all glm-5.3@rikyu).

Uses ONLY request metadata from the proxy logs (no prompts, no tool outputs, no answers) plus the
result files' status/wall/metrics. Joins log lines to cases via the case tag in each result file.
Writes gap_struct.json; prints aggregates.
"""

import json
import re
import statistics
from collections import Counter, defaultdict

import paths as P

HERE = P.OUT
H = P.LANE
LOGS = [H / "logs/passthrough.log", H / "logs/bridge.log"]
KV = re.compile(r"(\w+)=(\S+)")
HDR = (
    "cell",
    "n",
    "tools_adv",
    "first_prompt",
    "max_prompt",
    "n_req",
    "mean_dt",
    "model_t",
    "harness_t",
    "calls/req",
    "parallel%",
    "fin_len",
    "reason_carry",
    "cache%",
)


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _f(v):
    try:
        return float(str(v).rstrip("s"))
    except (TypeError, ValueError):
        return 0.0


def _record(line, kv):
    """One proxy END line → request metadata (both proxies' key spellings)."""
    return {
        "ts": line[:19],
        "msgs": _i(kv.get("msgs")),
        "tools": _i(kv.get("tools")),
        "prompt": _i(kv.get("prompt_tokens"))
        or (_i(kv.get("input_tokens")) + _i(kv.get("cache_read")) + _i(kv.get("cache_creation"))),
        "cached": _i(kv.get("cached_tokens")) or _i(kv.get("cache_read")),
        "completion": _i(kv.get("completion_tokens")) or _i(kv.get("output_tokens")),
        "finish": kv.get("finish", ""),
        "resp_calls": _i(kv.get("resp_tool_calls")) or _i(kv.get("tool_calls")),
        "asst": _i(kv.get("asst")),
        "asst_reasoning": _i(kv.get("asst_reasoning")),
        "called": kv.get("called", ""),
        "dt": _f(kv.get("dt")),
        "status": kv.get("status", ""),
        "aux": "title" in line.lower() and "aux" in line.lower(),
    }


def parse_logs():
    by_case = defaultdict(list)
    for lf in LOGS:
        if not lf.exists():
            continue
        for line in lf.open(errors="replace"):
            if " END " not in line or "case=" not in line:
                continue
            kv = dict(KV.findall(line))
            case = kv.get("case", "")
            if ".smoke-" in case:
                continue
            by_case[case].append(_record(line, kv))
    return by_case


def _lanes():
    return ["r4", "claude", "opencode"] + sorted(
        d.name[len("results_") :] for d in H.glob("results_r4_*")
    )


def _tool_counts(reqs):
    calls = Counter()
    for x in reqs:
        for n in filter(None, x["called"].split(",")):
            calls[n] += 1
    return calls


def _reasoning_carry(reqs):
    last_with_asst = [x for x in reqs if x["asst"]]
    if not last_with_asst:
        return None
    return last_with_asst[-1]["asst_reasoning"] / last_with_asst[-1]["asst"]


def _prompt_stats(reqs):
    first = reqs[0]
    return {
        "tools_adv": first["tools"],
        "first_prompt": first["prompt"],
        "max_prompt": max(x["prompt"] for x in reqs),
        "sum_prompt": sum(x["prompt"] for x in reqs),
        "sum_cached": sum(x["cached"] for x in reqs),
        "sum_completion": sum(x["completion"] for x in reqs),
    }


def _timing(r, reqs):
    model_time = sum(x["dt"] for x in reqs)
    return {
        "model_time": model_time,
        "harness_time": max(0.0, (r.get("wall_s") or 0) - model_time),
        "mean_dt": model_time / len(reqs),
    }


def _response_stats(reqs):
    return {
        "resp_calls": [x["resp_calls"] for x in reqs],
        "parallel_share": sum(1 for x in reqs if x["resp_calls"] > 1) / len(reqs),
        "finish_length": sum(1 for x in reqs if x["finish"] == "length"),
        "reasoning_carry": _reasoning_carry(reqs),
    }


def _case(lane, r, reqs):
    calls = _tool_counts(reqs)
    return {
        "lane": lane,
        "arm": r["arm"],
        "qid": r["qid"],
        "status": r.get("status"),
        "wall": r.get("wall_s") or 0,
        "n_req": len(reqs),
        **_prompt_stats(reqs),
        **_timing(r, reqs),
        **_response_stats(reqs),
        "calls": dict(calls),
        "tool_calls_total": sum(calls.values()),
        "status_non200": sum(1 for x in reqs if x["status"] not in ("200", "")),
    }


def join_cases(logs):
    """Result files of every lane joined to their proxy requests via the case tag."""
    cases = []
    for lane in _lanes():
        for p in sorted((H / f"results_{lane}").glob("*__*.json")):
            r = json.loads(p.read_text())
            reqs = logs.get(r.get("case_tag", ""), [])
            if reqs:
                cases.append(_case(lane, r, reqs))
    return cases


def _agg(cs, key, fn=statistics.mean):
    v = [c[key] for c in cs if c[key] is not None]
    return round(fn(v), 2) if v else None


def _cell_line(lane, arm, cs):
    cr = [c["tool_calls_total"] / c["n_req"] for c in cs]
    cache = [c["sum_cached"] / c["sum_prompt"] for c in cs if c["sum_prompt"]]
    return (
        f"{lane}/{arm} | {len(cs)} | {_agg(cs, 'tools_adv')} | {_agg(cs, 'first_prompt')} | {_agg(cs, 'max_prompt')} | "
        f"{_agg(cs, 'n_req')} | {_agg(cs, 'mean_dt')} | {_agg(cs, 'model_time')} | {_agg(cs, 'harness_time')} | "
        f"{round(statistics.mean(cr), 2)} | {round(100 * _agg(cs, 'parallel_share'), 1)} | {sum(c['finish_length'] for c in cs)} | "
        f"{_agg(cs, 'reasoning_carry')} | {round(100 * statistics.mean(cache), 1) if cache else None}"
    )


def _print_cells(cases):
    print(" | ".join(HDR))
    for lane in _lanes():
        for arm in ("A", "B", "C", "P"):
            cs = [c for c in cases if c["lane"] == lane and c["arm"] == arm]
            if cs:
                print(_cell_line(lane, arm, cs))


def _print_tool_calls(cases):
    print("\ntool calls by name (arm A, per case mean):")
    for lane in _lanes():
        cs = [c for c in cases if c["lane"] == lane and c["arm"] == "A"]
        tot = Counter()
        for c in cs:
            tot.update(c["calls"])
        if not cs:
            continue
        print(f"  {lane:9}", {k: round(v / len(cs), 2) for k, v in tot.most_common(12)})


def _print_timeouts(cases):
    print("\ntimeouts: requests / mean_dt / model_time / harness_time")
    for c in cases:
        if c["status"] == "timeout":
            print(
                f"  {c['lane']}/{c['arm']}/{c['qid']}: req={c['n_req']} mean_dt={c['mean_dt']:.1f}s model={c['model_time']:.0f}s "
                f"harness={c['harness_time']:.0f}s max_prompt={c['max_prompt']}"
            )


def main():
    cases = join_cases(parse_logs())
    (HERE / "gap_struct.json").write_text(json.dumps(cases, indent=1))
    print(f"cases joined: {len(cases)}")
    _print_cells(cases)
    _print_tool_calls(cases)
    _print_timeouts(cases)


if __name__ == "__main__":
    main()
