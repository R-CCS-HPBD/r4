"""Adapter protocol: one adapter per agent harness.

Adapters run one case in an isolated working directory and return an
AdapterResult. Implementations should prefer subprocess isolation so each
case gets a clean cwd and harness crashes cannot kill the runner.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 — bench spawns pinned harness CLIs
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.harness.cases import HarnessCase


class AdapterUnavailable(RuntimeError):
    """Harness CLI/SDK not installed or not automatable in this environment."""


# Ablation cell labels (harness-benchmarking plan, Phase 1 ablation table).
# Each label names the layer switched OFF relative to the full harness.
ABLATION_FULL = "full"
ABLATION_LABELS = (ABLATION_FULL, "no_steering", "no_guides", "no_memory")


@dataclass
class AdapterResult:
    """What a harness produced for one case run."""

    status: str = "failed"  # "success" | "failed" | "error"
    response_text: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class HarnessAdapter:
    """Base adapter. Subclasses set ``name`` and implement ``run_case``."""

    name = "base"

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout_sec: int = 900,
        ablation: str = ABLATION_FULL,
    ) -> None:
        self.model = model
        self.timeout_sec = timeout_sec
        self.ablation = ablation

    def available(self) -> str | None:
        """Return None when usable, else a human-readable reason why not."""
        return None

    def supports_ablation(self, label: str) -> bool:
        """Which ablation cells this harness can run. Default: full only."""
        return label == ABLATION_FULL

    def ablation_env(self) -> dict[str, str]:
        """Env injected into the driver subprocess for the current cell."""
        return {}

    def run_case(self, case: HarnessCase, workdir: Path, run_index: int) -> AdapterResult:
        raise NotImplementedError

    # --- SWE-bench lane (in-container execution) ---
    def swe_support(self) -> str | None:
        """Return None when this harness can run inside SWE-bench containers,
        else a human-readable reason why not (e.g. a missing runtime env var).
        Adapters that implement the lane override this only to gate on
        prerequisites."""
        return None

    def run_swe_case(self, instance: Any, container: str, runtime: Any) -> AdapterResult:
        raise NotImplementedError


SWE_NO_IMPLEMENTATION = "swe lane not implemented"


def supports_swe(adapter: HarnessAdapter) -> str | None:
    """Fail-closed check: does this adapter override the swe lane?"""
    if type(adapter).run_swe_case is HarnessAdapter.run_swe_case:
        return SWE_NO_IMPLEMENTATION
    return adapter.swe_support()


def parse_driver_output(stdout: str, stderr: str, rc: int) -> AdapterResult:
    """Parse the LAST stdout line of a JsonDriver as an AdapterResult payload."""
    lines = (stdout or "").strip().splitlines()
    if not lines:
        return AdapterResult(status="error", error=f"no output (rc={rc}): {stderr[-400:]}")
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return AdapterResult(status="error", error=f"bad driver output: {lines[-1][-400:]}")
    return AdapterResult(
        status=str(payload.get("status", "failed")),
        response_text=str(payload.get("response_text", "")),
        metrics=dict(payload.get("metrics") or {}),
        error=payload.get("error"),
    )


class JsonDriverAdapter(HarnessAdapter):
    """Run a driver command and parse the LAST stdout line as an
    AdapterResult JSON payload.

    Prompt is passed via a temp file to avoid argv length/escaping limits.
    """

    def driver_argv(self, prompt_file: Path, workdir: Path) -> list[str]:
        raise NotImplementedError

    def subprocess_env(self, workdir: Path) -> dict[str, str] | None:  # noqa: ARG002 — uniform interface
        """Env for the driver subprocess; None means inherit the parent env."""
        return None

    def run_case(self, case: HarnessCase, workdir: Path, run_index: int) -> AdapterResult:  # noqa: ARG002 — uniform interface
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(case.prompt)
            prompt_file = Path(handle.name)
        try:
            proc = subprocess.run(
                self.driver_argv(prompt_file, workdir),
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                stdin=subprocess.DEVNULL,
                env=self.subprocess_env(workdir),
            )
        except subprocess.TimeoutExpired:
            return AdapterResult(status="failed", error=f"timeout after {self.timeout_sec}s")
        finally:
            prompt_file.unlink(missing_ok=True)
        return parse_driver_output(proc.stdout or "", proc.stderr or "", proc.returncode)
