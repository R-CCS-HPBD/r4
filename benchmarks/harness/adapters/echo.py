"""Oracle plumbing adapter — no LLM.

Writes the case's expected_outputs into the workdir verbatim so runner and
grader wiring can be validated deterministically in CI without spending
tokens. NEVER use for real harness comparisons: an oracle that always
produces the expected workspace proves nothing about a harness.
"""

from __future__ import annotations

from pathlib import Path

from benchmarks.harness.adapters.base import AdapterResult, HarnessAdapter
from benchmarks.harness.cases import HarnessCase


class EchoAdapter(HarnessAdapter):
    """Materializes expected outputs directly; plumbing checks only."""

    name = "echo"

    def run_case(self, case: HarnessCase, workdir: Path, run_index: int) -> AdapterResult:  # noqa: ARG002 — uniform interface
        for rel in case.expected_outputs:
            target = workdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# oracle placeholder for {rel}\n")
        return AdapterResult(
            status="success",
            response_text="oracle wrote expected outputs without an LLM",
            metrics={"wallclock_sec": 0.0, "llm_calls": 1, "total_tokens": 0},
        )
