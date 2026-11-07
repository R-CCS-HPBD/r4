"""Externalized sprint evaluation (G15, 2026-10-29).

The generator is a lenient judge of its own work (Anthropic's 2026-03
harness post: out-of-box agents praise mediocre work and test
superficially; a tuned **fresh-context evaluator** with explicit rubric and
hard thresholds is the tractable fix). This module is the r4 choreography:

generator builds → ``build_contract_review`` spawns an evaluator sub-agent
with a clean context, read/run-only tools, the contract JSON and a rubric —
its JSON verdict (thresholds are hard: any failing criterion fails the
review) goes back to the generator as findings, and is recorded on the
contract (``reviews.jsonl`` + progress log).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from .build_contract import _PROGRESS_FILE, _active

logger = logging.getLogger(__name__)

#: The evaluator needs to RUN checks, never to modify: no write/edit verbs.
EVALUATOR_TOOL_NAMES = frozenset(
    {"shell_exec", "file_read", "grep_search", "glob_search", "list_dir"}
)

_RUBRIC = """\
You are the QA evaluator for one sprint of an ongoing build. You did NOT
write this work — grade it like a skeptical reviewer, not like its author.

Grading criteria (each is a HARD threshold — one failure fails the review):
1. Functionality: the claimed behavior actually works when you run it. Run
   the project's checks or the smallest honest end-to-end probe. "Looks
   right in code" is not evidence.
2. No stubs: no TODO bodies, dead buttons, display-only features, or
   numeric-only placeholders where interaction is claimed.
3. Evidence: for every feature marked passing, the recorded evidence names
   the check that proves it (test run, command output, diff) — not vibes.
4. Focus: the sprint holds to its features; gold-plating is a finding too.

Contract under review:
```json
{contract}
```

{focus}

Reply with ONLY a JSON object (no prose, no fences):
{{"verdict": "pass" | "fail",
 "findings": [{{"criterion": "...", "severity": "fail|warn", "detail": "..."}}],
 "summary": "one or two sentences, evidence first"}}\
"""

_REVIEW_FILE = "reviews.jsonl"
_MAX_VERDICT_CHARS = 500


def review_task(contract: dict[str, Any], focus: str) -> str:
    """The evaluator sub-agent's task text (rubric + contract + focus)."""
    return _RUBRIC.format(
        contract=json.dumps(contract, indent=2)[:8_000],
        focus=f"Review focus: {focus}" if focus else "",
    ).strip()


def parse_verdict(text: str) -> dict[str, Any]:
    """The evaluator's verdict object, tolerating fences and prose framing."""
    candidate = (text or "").strip()
    match = re.search(r"\{[^{}]*\"verdict\"[\s\S]*\}", candidate)
    if match:
        candidate = match.group(0)
    try:
        verdict = json.loads(candidate)
    except ValueError:
        return {
            "verdict": "error",
            "findings": [],
            "summary": "evaluator returned no parseable JSON verdict",
            "raw_head": (text or "")[:_MAX_VERDICT_CHARS],
        }
    return {
        "verdict": str(verdict.get("verdict", "error")).lower(),
        "findings": verdict.get("findings") or [],
        "summary": str(verdict.get("summary") or "")[:_MAX_VERDICT_CHARS],
    }


def record_review(root: str, verdict: dict[str, Any], focus: str) -> None:
    """Append the verdict to the contract's review ledger and progress log."""
    directory, _ = _active(root)
    if directory is None:
        return
    line = json.dumps(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "verdict": verdict.get("verdict", "error"),
            "focus": focus[:200],
            "summary": verdict.get("summary", ""),
            "findings": verdict.get("findings", []),
        }
    )
    with (directory / _REVIEW_FILE).open("a") as handle:
        handle.write(line + "\n")
    with (directory / _PROGRESS_FILE).open("a") as handle:
        handle.write(
            f"- [{datetime.now(timezone.utc).isoformat()}] REVIEW "
            f"{str(verdict.get('verdict', 'error')).upper()} — {verdict.get('summary', '')}\n"
        )


def evaluator_tools(wire_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The read/run-only subset of the parent's wire set for the evaluator."""
    return [tool for tool in wire_tools if tool.get("name") in EVALUATOR_TOOL_NAMES]
