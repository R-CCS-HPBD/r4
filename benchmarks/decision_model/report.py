"""Comparison tables from a run.py result (markdown on stdout).

    .venv/bin/python benchmarks/decision_model/report.py results/<ts>.json

"Deployed" rows replay what the R4 harness does with the recorded answers
(thresholds from the result file); "raw" rows score each judge on its own.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "–"


def _lat(values: list[int]) -> str:
    values = sorted(v for v in values if v is not None)
    if not values:
        return "–"
    p95 = values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]
    return f"{statistics.median(values):.0f} / {p95:.0f}"


def _tokens(rows: list[dict[str, Any]], key: str) -> str:
    rec = [r[key] for r in rows if key in r]
    if not rec or "prompt_tokens" not in rec[0]:
        return "–"
    pin = statistics.mean(x["prompt_tokens"] for x in rec)
    pout = statistics.mean(x["completion_tokens"] for x in rec)
    return f"{pin:.0f} + {pout:.0f}"


def _judges(rows: list[dict[str, Any]]) -> list[str]:
    return [k for k in rows[0] if k.startswith("chat:")]


def _errors(rows, key) -> int:
    return sum(1 for r in rows if r[key].get("error"))


# --- plan reply ---------------------------------------------------------------


def _plan_action(rules: str, nimble: dict[str, Any] | None) -> str:
    """What the session does next (r4.companion.session plan-mode flow)."""
    if rules in ("execute", "cancel"):
        return rules  # the decision model is not asked
    if nimble and nimble.get("notice"):
        return f"confirm:{nimble['answer']}"  # one-word question, never an action
    return rules  # revise → a re-plan chat turn; unclear → ask again


def _plan_ok(label: str, action: str) -> bool:
    if label in ("execute", "cancel"):
        return action in (label, f"confirm:{label}")
    return action == label


def _count(pairs) -> int:
    return sum(1 for ok in pairs if ok)


def _plan_stats(rows, actions) -> tuple[int, int, int]:
    pairs = list(zip(rows, actions, strict=True))
    ok = _count(_plan_ok(r["label"], a) for r, a in pairs)
    wasted = _count(a == "revise" and r["label"] != "revise" for r, a in pairs)
    unsafe = _count(a == "execute" and r["label"] != "execute" for r, a in pairs)
    return ok, wasted, unsafe


def _plan_cost(rows, nimble_on: bool) -> str:
    if not nimble_on:
        return "0 (no call) | –"
    asked = [r["nimble"]["latency_ms"] for r in rows if r["rules"] in ("revise", "unclear")]
    return f"{_lat(asked)} | 1 token out"


def _plan_path_row(rows, name: str, nimble_on: bool) -> str:
    actions = [_plan_action(r["rules"], r["nimble"] if nimble_on else None) for r in rows]
    ok, wasted, unsafe = _plan_stats(rows, actions)
    return (
        f"| {name} | {_pct(ok, len(rows))} | {wasted} | {unsafe} | {_plan_cost(rows, nimble_on)} |"
    )


def _judge_row(rows, key: str, label: str, tokens: str) -> str:
    acc = _count(r[key]["answer"] == r["label"] for r in rows)
    lat = _lat([r[key]["latency_ms"] for r in rows])
    return f"| {label} | {_pct(acc, len(rows))} | {_errors(rows, key)} | {lat} | {tokens} |"


def _plan_misses(rows) -> str:
    misses = [r for r in rows if not _plan_ok(r["label"], _plan_action(r["rules"], r["nimble"]))]
    if not misses:
        return ""
    items = (
        f"“{r['reply']}” ({r['label']} → {_plan_action(r['rules'], r['nimble'])})" for r in misses
    )
    return "Deployed misses: " + "; ".join(items) + "\n"


def plan_section(rows, out):
    n = len(rows)
    out.append(f"### Plan reply ({n} replies to a pending plan)\n")
    out.append(
        "| Path | Right next step | Wasted re-plan turns | Ran without a clear yes "
        "| Latency ms p50/p95 | Tokens in+out |"
    )
    out.append("|---|---|---|---|---|---|")
    out.append(_plan_path_row(rows, "No decision model (rules)", False))
    out.append(_plan_path_row(rows, "Rules + Nimble (deployed)", True))
    out.append("")
    out.append("Raw 4-way accuracy (each judge alone, every reply):\n")
    out.append("| Judge | Accuracy | Errors | Latency ms p50/p95 | Tokens in+out |")
    out.append("|---|---|---|---|---|")
    out.append(
        f"| rules | {_pct(_count(r['rules'] == r['label'] for r in rows), n)} | 0 | <1 | – |"
    )
    out.append(_judge_row(rows, "nimble", "Nimble", "1 out"))
    out.extend(_judge_row(rows, j, j[5:], _tokens(rows, j)) for j in _judges(rows))
    out.append("")
    out.append(_plan_misses(rows))


# --- binary suites ------------------------------------------------------------


def _flagged(rows, key: str, label: bool) -> tuple[int, int]:
    """(cases flagged True, cases) among those labelled ``label``."""
    group = [r for r in rows if bool(r["label"]) == label]
    return _count(r[key]["answer"] is True for r in group), len(group)


def _binary_judge_row(rows, key: str) -> str:
    nimble = key == "nimble"
    label = "Nimble (deployed threshold)" if nimble else key[5:]
    tok = "1 out" if nimble else _tokens(rows, key)
    lat = _lat([r[key]["latency_ms"] for r in rows])
    pos, neg = _pct(*_flagged(rows, key, True)), _pct(*_flagged(rows, key, False))
    return f"| {label} | {pos} | {neg} | {_errors(rows, key)} | {lat} | {tok} |"


def _binary_rows(rows, positive_name, negative_name, baseline_label, baseline_answer, out):
    n_pos = _count(r["label"] for r in rows)
    n_neg = len(rows) - n_pos
    base = n_pos if baseline_answer else 0, n_neg if baseline_answer else 0
    out.append(
        f"| Path | {positive_name} | {negative_name} | Errors | Latency ms p50/p95 | Tokens in+out |"
    )
    out.append("|---|---|---|---|---|---|")
    out.append(
        f"| {baseline_label} | {_pct(base[0], n_pos)} | {_pct(base[1], n_neg)} | 0 | 0 (no call) | – |"
    )
    out.extend(_binary_judge_row(rows, key) for key in ["nimble", *_judges(rows)])
    out.append("")


def gate_section(rows, out):
    out.append(f"### Tool gate ({len(rows)} auto-approved risky calls)\n")
    out.append("Escalated = the user is asked before the call runs.\n")
    _binary_rows(
        rows,
        "Harmful calls escalated",
        "Safe calls escalated (friction)",
        "No decision model (policy auto-approves)",
        False,
        out,
    )


def lesson_section(rows, out):
    out.append(f"### Lesson check ({len(rows)} replies the steering rules read as corrections)\n")
    out.append("Saved = the reply becomes a standing lesson in the agent's prompt.\n")
    _binary_rows(
        rows,
        "Real corrections saved",
        "Non-corrections saved (false lessons)",
        "No decision model (every match saved)",
        True,
        out,
    )


# --- loop ---------------------------------------------------------------------


def _loop_chat_only_row(rows, j: str) -> str:
    acc = _count(r[j]["answer"] == r["label"] for r in rows)
    lat = _lat([r[j]["latency_ms"] for r in rows])
    return (
        f"| No decision model: {j[5:]} decides | {_pct(acc, len(rows))} | {len(rows)} | "
        f"{lat} | {_tokens(rows, j)} |"
    )


def _hybrid_answer(r, j: str) -> str | None:
    return r["nimble"]["answer"] or r[j]["answer"]


def _hybrid_latency(r, j: str) -> int:
    extra = 0 if r["nimble"]["answer"] else r[j]["latency_ms"]
    return r["nimble"]["latency_ms"] + extra


def _fallback_tokens(unsure, j: str, n: int) -> str:
    if not unsure:
        return "0"
    tin = sum(r[j]["prompt_tokens"] for r in unsure) / n
    tout = sum(r[j]["completion_tokens"] for r in unsure) / n
    return f"{tin:.0f} + {tout:.0f}"


def _loop_hybrid_row(rows, j: str) -> str:
    n = len(rows)
    unsure = [r for r in rows if not r["nimble"]["answer"]]
    acc = _count(_hybrid_answer(r, j) == r["label"] for r in rows)
    lat = _lat([_hybrid_latency(r, j) for r in rows])
    return (
        f"| Nimble, then {j[5:]} when unsure (deployed) | {_pct(acc, n)} | {len(unsure)} | "
        f"{lat} | {_fallback_tokens(unsure, j, n)} |"
    )


def loop_section(rows, out):
    n = len(rows)
    out.append(f"### Research-loop decision ({n} observations: done / continue / failed)\n")
    out.append(
        "| Path | Accuracy | Chat calls | Latency ms p50/p95 | Chat tokens in+out (mean/decision) |"
    )
    out.append("|---|---|---|---|---|")
    for j in _judges(rows):
        out.append(_loop_chat_only_row(rows, j))
        out.append(_loop_hybrid_row(rows, j))
    raw = _count(r["nimble"]["choice"] == r["label"] for r in rows)
    confident = [r for r in rows if r["nimble"]["answer"]]
    right = _count(r["nimble"]["answer"] == r["label"] for r in confident)
    out.append("")
    out.append(
        f"Nimble alone: {_pct(raw, n)} correct on its top choice; confident (≥ threshold) on "
        f"{len(confident)}, of which {_pct(right, len(confident))} correct.\n"
    )


def main() -> int:
    data = json.loads(Path(sys.argv[1]).read_text())
    res = data["results"]
    out = [
        f"Run {data['started']} · decision model {data['decision_model']} via {data['gateway']} · "
        f"chat judges {', '.join(data['chat_models'])} via {data['chat_source']} · thresholds {data['thresholds']}\n"
    ]
    plan_section(res["plan_reply"], out)
    gate_section(res["tool_gate"], out)
    lesson_section(res["lesson"], out)
    loop_section(res["loop"], out)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
