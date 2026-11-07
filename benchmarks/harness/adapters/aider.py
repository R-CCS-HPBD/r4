"""Aider CLI adapter (OSS coding-harness baseline).

Requires the `aider` CLI on PATH with model credentials managed by the user —
the bench never provisions credentials. Aider reports limited telemetry, so
metrics are wall-clock only; graders still see the full workspace state.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 — bench spawns the pinned aider CLI
import time
from pathlib import Path

from benchmarks.harness.adapters.base import AdapterResult, HarnessAdapter
from benchmarks.harness.cases import HarnessCase


class AiderAdapter(HarnessAdapter):
    """Drives aider headless against each case in the case workdir."""

    name = "aider"

    def available(self) -> str | None:
        if shutil.which("aider") is None:
            return "aider CLI not on PATH (pip install aider-chat to enable this baseline)"
        return None

    def run_case(self, case: HarnessCase, workdir: Path, run_index: int) -> AdapterResult:  # noqa: ARG002 — uniform interface
        argv = ["aider", "--yes-always", "--no-git", "--message", case.prompt]
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
