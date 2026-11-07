"""Claude Code headless adapter (`claude -p --output-format json`).

Requires the `claude` CLI on PATH with authentication managed by the user —
the bench never provisions credentials. `--dangerously-skip-permissions` is
required for non-interactive headless runs; only use with sandboxed workdirs
and trusted, version-pinned suites.

BYOM (same-model fairness): Claude Code officially serves Anthropic models
only, but honors `ANTHROPIC_BASE_URL` LLM gateways, which define their own
model namespace (per model-config docs: behind a gateway, model strings pass
through unchecked). Set `R4_BENCH_CLAUDE_BASE_URL` (+ optional
`R4_BENCH_CLAUDE_AUTH_TOKEN`) to route Claude Code at our LiteLLM
Anthropic-compatible endpoint, so every harness in the comparison drives the
SAME self-hosted model. Gateways blur wire-level behavior (translation
confound) — size it with a control suite before trusting deltas.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 — bench spawns the pinned claude CLI
import time
from pathlib import Path

from benchmarks.harness.adapters.base import AdapterResult, HarnessAdapter
from benchmarks.harness.cases import HarnessCase

BASE_URL_ENV = "R4_BENCH_CLAUDE_BASE_URL"
AUTH_TOKEN_ENV = "R4_BENCH_CLAUDE_AUTH_TOKEN"


class ClaudeCodeAdapter(HarnessAdapter):
    """Drives Claude Code headless against each case in the case workdir."""

    name = "claude_code"

    def available(self) -> str | None:
        if shutil.which("claude") is None:
            return "claude CLI not on PATH (install Claude Code to enable this baseline)"
        return None

    def run_case(self, case: HarnessCase, workdir: Path, run_index: int) -> AdapterResult:  # noqa: ARG002 — uniform interface
        argv = [
            "claude",
            "-p",
            case.prompt,
            "--output-format",
            "json",
            # Headless runs need permission bypass; workdir is sandboxed per case.
            "--dangerously-skip-permissions",
            # Print-mode needs an explicit tool tier — otherwise Claude stays
            # text-only and graders (artifact_present/test_pass) can never pass
            # (measured 2026-08-24: 9/9 text-only FAIL before this flag).
            "--allowedTools",
            "Write,Read,Edit,Bash",
        ]
        if self.model:
            argv += ["--model", self.model]
        # BYOM: route at an LLM gateway (e.g. our LiteLLM Anthropic endpoint)
        # so Claude Code drives the same model as every other harness.
        env = dict(os.environ)
        base_url = os.environ.get(BASE_URL_ENV)
        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url
            auth_token = os.environ.get(AUTH_TOKEN_ENV)
            if auth_token:
                env["ANTHROPIC_AUTH_TOKEN"] = auth_token
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return AdapterResult(status="failed", error=f"timeout after {self.timeout_sec}s")
        elapsed = time.monotonic() - started
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "")[-400:]
            return AdapterResult(status="error", error=f"rc={proc.returncode}: {stderr_tail}")
        return self._render_result(proc.stdout, elapsed, via_gateway=bool(base_url))

    @staticmethod
    def _render_result(stdout: str, elapsed: float, *, via_gateway: bool) -> AdapterResult:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return AdapterResult(status="error", error=f"non-json output: {stdout[-400:]}")
        usage = payload.get("usage") or {}
        metrics: dict[str, object] = {
            "wallclock_sec": round(elapsed, 3),
            "llm_calls": payload.get("num_turns"),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            "cost_usd": payload.get("total_cost_usd"),
            "via_gateway": via_gateway,
        }
        status = "failed" if payload.get("is_error") else "success"
        return AdapterResult(
            status=status,
            response_text=str(payload.get("result", "")),
            metrics={key: value for key, value in metrics.items() if value is not None},
            error=None if status == "success" else "claude reported is_error",
        )
