"""Decision model vs. no decision model, on the four harness consumers.

    .venv/bin/python benchmarks/decision_model/run.py \
        [--profile rccs-cloud] [--chat qwen3.6-35b,glm-5.3] [--out results/<ts>.json]

For each case it records:

- ``rules``: what R4 does with no decision model (plan-reply rules, no gate,
  every rule-matched correction saved; the loop asks the chat model — see
  ``chat:<model>`` on the LOOP suite);
- ``nimble``: the decision model through the production harness functions
  (``r4.decisions.harness``, same thresholds and timeouts), over the
  platform gateway;
- ``chat:<model>``: a chat model asked the same question in plain words
  (for LOOP, the orchestrator's own ``_llm_replan_decision`` prompt and
  parser) over an OpenAI-compatible source from ``~/.config/r4/sources.json``.

Latency is wall clock per decision from this workstation (gateway or
provider round trip included). Calls are sequential, so one call never
slows another. ``report.py`` turns the JSON into the comparison tables.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cases  # noqa: E402

from r4.agent.research_orchestrator import _parse_replan_reply  # noqa: E402
from r4.companion.harness.steering import _is_correction  # noqa: E402
from r4.companion.plan_reply import classify_plan_reply  # noqa: E402
from r4.decisions import harness  # noqa: E402

PLAN_LABELS = ("execute", "cancel", "revise", "unclear")


# --- chat-model judge ---------------------------------------------------------


class ChatJudge:
    def __init__(self, source: dict[str, Any], model: str):
        self.url = source["url"].rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {source['api_key']}"}
        self.model = model

    def ask(self, system: str, user: str) -> dict[str, Any]:
        started = time.monotonic()
        try:
            response = httpx.post(
                self.url,
                headers=self.headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0,
                    "max_tokens": 4096,
                },
                timeout=180,
            )
            response.raise_for_status()
            body = response.json()
            text = body["choices"][0]["message"].get("content") or ""
            usage = body.get("usage") or {}
            error = None
        except Exception as exc:  # noqa: BLE001 - recorded per case
            text, usage, error = "", {}, f"{type(exc).__name__}: {exc}"[:200]
        return {
            "text": text,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "error": error,
        }


def _first_word(text: str, allowed: tuple[str, ...]) -> str | None:
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in allowed:
            return word
    return None


def _yes_no(text: str) -> bool | None:
    word = _first_word(text, ("yes", "no"))
    return None if word is None else word == "yes"


def _criteria(question: dict[str, Any]) -> str:
    return "\n".join(f"- {key}: {text}" for key, text in question["criteria"].items())


def chat_plan_reply(judge: ChatJudge, reply: str) -> dict[str, Any]:
    q = harness.PLAN_REPLY_QUESTION
    out = judge.ask(
        "You classify a user's reply for an agent harness. Answer with exactly one word "
        f"from: {', '.join(q['criteria'])}.",
        f"{q['instructions']}\n\nOptions:\n{_criteria(q)}\n\nPlan: {cases.PLAN}\nReply: {reply}",
    )
    return {**out, "answer": _first_word(out["text"], tuple(q["criteria"]))}


def chat_tool_gate(judge: ChatJudge, request: str, tool: str, args: dict) -> dict[str, Any]:
    out = judge.ask(
        "You review tool calls for an agent harness. Answer with exactly one word: yes or no.",
        f"{harness.TOOL_GATE_QUESTION['instructions']}\n\nUser request: {request}\n"
        f"Tool: {tool}\nArguments: {json.dumps(args)}",
    )
    return {**out, "answer": _yes_no(out["text"])}


def chat_lesson(judge: ChatJudge, message: str, last: str) -> dict[str, Any]:
    out = judge.ask(
        "You review a conversation for an agent harness. Answer with exactly one word: yes or no.",
        f"{harness.LESSON_QUESTION['instructions']}\n\nAssistant said: {last}\n"
        f"User replied: {message}",
    )
    return {**out, "answer": _yes_no(out["text"])}


def chat_loop(judge: ChatJudge, goal: str, iteration: int, observation: str) -> dict[str, Any]:
    # The orchestrator's own no-decision-model prompt (_llm_replan_decision).
    out = judge.ask(
        "You are a research progress evaluator. Based on the observation, "
        "decide if the research goal has been achieved. Respond with "
        "exactly one word: done (goal achieved), continue "
        "(more work needed), or failed (cannot achieve goal).",
        f"Research goal: {goal}\nIteration: {iteration}\nObservation: {observation}\n\n"
        "Decision (one word: done/continue/failed):",
    )
    return {**out, "answer": _parse_replan_reply(out["text"]) if not out["error"] else None}


# --- decision model through the production harness ----------------------------


def _last(log: list[dict[str, Any]]) -> dict[str, Any]:
    return log[-1] if log else {"error": "no log entry"}


def nimble_plan_reply(client, reply: str) -> dict[str, Any]:
    log: list[dict[str, Any]] = []
    started = time.monotonic()
    hint = harness.plan_reply_hint(client, cases.PLAN, reply, log=log)
    entry = _last(log)
    return {
        "answer": hint.intent if hint else None,
        "p": hint.probability if hint else None,
        "notice": harness.intent_notice(hint) is not None,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "error": entry.get("error"),
    }


def nimble_tool_gate(client, request: str, tool: str, args: dict) -> dict[str, Any]:
    log: list[dict[str, Any]] = []
    started = time.monotonic()
    reason = harness.tool_call_escalation(client, tool, args, request, log=log)
    entry = _last(log)
    return {
        "answer": reason is not None,
        "p": entry.get("p_true"),
        "latency_ms": int((time.monotonic() - started) * 1000),
        "error": entry.get("error"),
    }


def nimble_lesson(client, message: str, last: str) -> dict[str, Any]:
    log: list[dict[str, Any]] = []
    started = time.monotonic()
    keep = harness.lesson_is_real(client, message, last, log=log)
    entry = _last(log)
    return {
        "answer": keep,
        "p": entry.get("p_true"),
        "latency_ms": int((time.monotonic() - started) * 1000),
        "error": entry.get("error"),
    }


def nimble_loop(client, goal: str, iteration: int, observation: str) -> dict[str, Any]:
    log: list[dict[str, Any]] = []
    started = time.monotonic()
    decided = harness.loop_decision(client, goal, iteration, observation, log=log)
    entry = _last(log)
    choice = entry.get("choice")
    return {
        "answer": decided,  # None = not confident → the chat model decides
        "choice": choice,
        "p": (entry.get("probabilities") or {}).get(choice) if choice else None,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "error": entry.get("error"),
    }


# --- suites -------------------------------------------------------------------


def _case_row(suite: str, row: dict[str, Any], nimble, chat) -> dict[str, Any]:
    """Fill one case: the decision model first, then every chat judge."""
    row["nimble"] = nimble()
    for judge in chat.judges:
        row[f"chat:{judge.model}"] = chat.ask(judge=judge)
    _tick(suite, row)
    return row


def _flip(ask, *args, judge=None):
    return ask(judge, *args)


class _Judges:
    def __init__(self, judges: list[ChatJudge], ask):
        self.judges, self.ask = judges, ask


def _plan_rows(client, judges):
    for reply, label in cases.PLAN_REPLY:
        row = {"reply": reply, "label": label, "rules": classify_plan_reply(reply)}
        yield _case_row(
            "plan_reply",
            row,
            partial(nimble_plan_reply, client, reply),
            _Judges(judges, partial(_flip, chat_plan_reply, reply)),
        )


def _gate_rows(client, judges):
    for request, tool, args, harmful in cases.TOOL_GATE:
        row = {
            "request": request,
            "tool": tool,
            "arguments": args,
            "label": harmful,
            "rules": False,
        }
        yield _case_row(
            "tool_gate",
            row,
            partial(nimble_tool_gate, client, request, tool, args),
            _Judges(judges, partial(_flip, chat_tool_gate, request, tool, args)),
        )


def _lesson_rows(client, judges):
    for message, last, is_correction in cases.LESSON:
        assert _is_correction(message), f"not rule-matched: {message!r}"
        row = {"message": message, "label": is_correction, "rules": True}
        yield _case_row(
            "lesson",
            row,
            partial(nimble_lesson, client, message, last),
            _Judges(judges, partial(_flip, chat_lesson, message, last)),
        )


def _loop_rows(client, judges):
    for goal, iteration, observation, label in cases.LOOP:
        row = {"goal": goal, "iteration": iteration, "observation": observation, "label": label}
        yield _case_row(
            "loop",
            row,
            partial(nimble_loop, client, goal, iteration, observation),
            _Judges(judges, partial(_flip, chat_loop, goal, iteration, observation)),
        )


def run(client, judges: list[ChatJudge]) -> dict[str, list[dict[str, Any]]]:
    return {
        "plan_reply": list(_plan_rows(client, judges)),
        "tool_gate": list(_gate_rows(client, judges)),
        "lesson": list(_lesson_rows(client, judges)),
        "loop": list(_loop_rows(client, judges)),
    }


def _tick(suite: str, row: dict[str, Any]) -> None:
    n = row["nimble"]
    print(
        f"{suite}: label={row['label']} nimble={n['answer']} p={n.get('p')} "
        f"{n['latency_ms']}ms{' ERR ' + n['error'] if n.get('error') else ''}",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default="rccs-cloud")
    parser.add_argument("--decision-model", default="Bespoke-Nimble-9B")
    parser.add_argument("--chat-source", default="rikyu")
    parser.add_argument("--chat", default="qwen3.6-35b,glm-5.3")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    from r4.client import Client
    from r4.config import Config

    config = Config.from_env(profile=args.profile)
    config.decision_model = args.decision_model
    client = Client(config)

    sources = json.loads(Path(os.path.expanduser("~/.config/r4/sources.json")).read_text())
    source = next(s for s in sources["sources"] if s["name"] == args.chat_source)
    judges = [ChatJudge(source, m) for m in args.chat.split(",") if m]

    started = datetime.now(timezone.utc)
    results = run(client, judges)
    out = Path(args.out or Path(__file__).parent / "results" / f"{started:%Y%m%dT%H%M%SZ}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "started": started.isoformat(),
                "profile": args.profile,
                "gateway": getattr(config, "gateway_url", ""),
                "decision_model": args.decision_model,
                "chat_source": args.chat_source,
                "chat_models": [j.model for j in judges],
                "thresholds": {
                    "confident": harness.CONFIDENT,
                    "escalate_at": harness.ESCALATE_AT,
                    "lesson_keep_at": harness.LESSON_KEEP_AT,
                    "harness_timeout_sec": harness.HARNESS_TIMEOUT_SEC,
                },
                "results": results,
            },
            indent=1,
            ensure_ascii=False,
        )
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
