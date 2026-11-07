"""GitHub Copilot CLI adapter (commercial harness at vendor-best config).

Headless: `copilot -p <prompt> --allow-all-tools` in the case workdir.
Requires any active Copilot subscription (including the free tier) — note
premium requests draw down a monthly quota, so keep run counts small here.

Authentication: `GH_TOKEN`/`GITHUB_TOKEN` (fine-grained PAT with the
"Copilot Requests" permission), or an interactive login the user performed
beforehand. The bench never provisions credentials.

IMPORTANT fairness caveat: Copilot CLI serves GitHub-hosted models only
(Claude Sonnet 4.5 by default, GPT family via /model) — no BYOM. It cannot
hold the model constant against our self-hosted fleet. Use it only in the
"vendor-best configuration" lane, and annotate results with the actual model.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 — bench spawns the pinned copilot CLI
import time
from pathlib import Path

from benchmarks.harness.adapters.base import AdapterResult, HarnessAdapter
from benchmarks.harness.cases import HarnessCase


class CopilotAdapter(HarnessAdapter):
    """Drives Copilot CLI headless against each case in the case workdir."""

    name = "copilot"

    def available(self) -> str | None:
        if shutil.which("copilot") is None:
            return "copilot CLI not on PATH (requires a Copilot subscription)"
        return None

    def run_case(self, case: HarnessCase, workdir: Path, run_index: int) -> AdapterResult:  # noqa: ARG002 — uniform interface
        argv = [
            "copilot",
            "-p",
            case.prompt,
            # Headless runs need permission auto-approval; workdir is sandboxed.
            "--allow-all-tools",
        ]
        if self.model:
            argv += ["--model", self.model]
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return AdapterResult(status="failed", error=f"timeout after {self.timeout_sec}s")
        elapsed = time.monotonic() - started
        return AdapterResult(
            status="success" if proc.returncode == 0 else "failed",
            response_text=(proc.stdout or "")[-2000:],
            metrics={"wallclock_sec": round(elapsed, 3)},
            error=None
            if proc.returncode == 0
            else f"rc={proc.returncode}: {(proc.stderr or '')[-200:]}",
        )
