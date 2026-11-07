"""OpenCode adapter (open-source agent CLI, BYOM-native).

Non-interactive: `opencode run <prompt> --format json --auto --dir DIR`.
OpenCode needs no subscription — models come from any provider configured in
its own config (models.dev registry, plus custom OpenAI-compatible endpoints).
Point a provider at our LiteLLM to hold the model constant across harnesses.

`--auto` auto-approves permissions not explicitly denied; only use with
sandboxed workdirs and trusted, version-pinned suites. `--format json` emits
a JSON event stream on stdout; token accounting that OpenCode does not expose
stays blank rather than guessed (metrics come from authoritative surfaces
only, per ADR-020 section 13).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 — bench spawns the pinned opencode CLI
import time
from pathlib import Path

from benchmarks.harness.adapters.base import AdapterResult, HarnessAdapter
from benchmarks.harness.cases import HarnessCase

SWE_BIN_ENV = "R4BENCH_OPENCODE_LINUX_BIN"
SWE_BIN_PATH = "/opt/r4bench/opencode"


class OpenCodeAdapter(HarnessAdapter):
    """Drives OpenCode headless against each case in the case workdir."""

    name = "opencode"

    def available(self) -> str | None:
        if shutil.which("opencode") is None:
            return "opencode CLI not on PATH (see opencode.ai; free, BYOM)"
        return None

    def _argv(self, prompt: str, workdir: str) -> list[str]:
        argv = [
            "run",
            prompt,
            "--format",
            "json",
            # Headless runs need permission bypass (v1.17: --auto does not exist
            # yet; the docs site's --auto is newer), workdir is sandboxed.
            "--dangerously-skip-permissions",
            "--dir",
            workdir,
        ]
        if self.model:
            argv += ["--model", self.model]
        return argv

    def run_case(self, case: HarnessCase, workdir: Path, run_index: int) -> AdapterResult:  # noqa: ARG002 — uniform interface
        argv = ["opencode", *self._argv(case.prompt, str(workdir))]
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
        parsed = parse_opencode_events(proc.stdout or "")
        return AdapterResult(
            status="success" if proc.returncode == 0 else "failed",
            response_text=parsed["response_text"],
            metrics={**parsed["metrics"], "wallclock_sec": round(elapsed, 3)},
            error=None
            if proc.returncode == 0
            else f"rc={proc.returncode}: {(proc.stderr or '')[-200:]}",
        )

    # --- SWE-bench lane (in-container, linux x86_64 binary) ---
    def swe_support(self) -> str | None:
        if not os.environ.get(SWE_BIN_ENV):
            return f"{SWE_BIN_ENV} not set (linux x86_64 opencode binary path)"
        return None

    def swe_mounts(self) -> list[tuple[str, str]]:
        return [(os.environ[SWE_BIN_ENV], SWE_BIN_PATH)]

    def run_swe_case(self, instance, container: str, runtime) -> AdapterResult:  # noqa: ARG002 — uniform interface
        """Run opencode INSIDE the eval container, cwd /testbed.

        The host binary is unusable (host arch) — a linux x86_64 opencode must
        be provided via {SWE_BIN_ENV}. Config travels as a file; the gateway
        key travels via env (our own container, network=none).
        """
        config_host = os.environ.get("OPENCODE_CONFIG")
        if not config_host or not Path(config_host).is_file():
            return AdapterResult(
                status="error", error="OPENCODE_CONFIG must point at a provider config file"
            )
        runtime.write_file(container, "/root/opencode.json", Path(config_host).read_text())
        argv_tail = self._argv('"$(cat /tmp/r4bench_prompt.txt)"', "/testbed")
        cmd = f"{SWE_BIN_PATH} {' '.join(argv_tail)}"
        started = time.monotonic()
        result = runtime.exec(
            container,
            ["sh", "-c", cmd],
            env={
                "OPENCODE_CONFIG": "/root/opencode.json",
                "R4_GATEWAY_KEY": os.environ.get("R4_GATEWAY_KEY", ""),
            },
            timeout_sec=self.timeout_sec,
        )
        elapsed = time.monotonic() - started
        if result.rc == 124:
            return AdapterResult(status="failed", error=f"timeout after {self.timeout_sec}s")
        parsed = parse_opencode_events(result.stdout or "")
        return AdapterResult(
            status="success" if result.rc == 0 else "failed",
            response_text=parsed["response_text"],
            metrics={**parsed["metrics"], "wallclock_sec": round(elapsed, 3)},
            error=None if result.rc == 0 else f"rc={result.rc}: {(result.stderr or '')[-200:]}",
        )


def _step_tokens(event: dict) -> tuple[int, int]:
    """(total, cache read) tokens of one ``step_finish`` event.

    OpenCode v1 events carry ``tokens.total``; v2 (2.0.18) carry only the parts
    (input, output, reasoning, cache.read, cache.write), so the total is their
    sum — the same definition as the session DB reading in
    ``same_model/run_harness.py`` (prompt incl. cache reads/writes + completion).
    """
    tokens = (event.get("part") or {}).get("tokens") or {}
    cache = tokens.get("cache") or {}
    cache_read = int(cache.get("read") or 0)
    if tokens.get("total") is not None:
        return int(tokens["total"]), cache_read
    parts = ("input", "output", "reasoning")
    total = sum(int(tokens.get(k) or 0) for k in parts) + cache_read + int(cache.get("write") or 0)
    return total, cache_read


def _events(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue  # progress/warning lines are not events
        if isinstance(event, dict):
            events.append(event)
    return events


def parse_opencode_events(stdout: str) -> dict:
    """Parse the ``--format json`` event stream.

    One model request = one step. OpenCode v2.0.18 emits ``step_start`` for
    every step but no ``step_finish`` for the last one (the answer), so
    ``llm_calls`` counts the larger of the two (it matches the session DB's
    assistant rows) and ``unfinished_steps`` says how many steps have no token
    record: ``total_tokens`` covers finished steps only (measured 2026-10-04:
    v2 lines also lack ``tokens.total``, which made this report 0).
    """
    starts = finishes = total_tokens = cache_read_tokens = 0
    text_parts = []
    for event in _events(stdout):
        event_type = event.get("type")
        if event_type == "step_start":
            starts += 1
        elif event_type == "step_finish":
            finishes += 1
            total, cache_read = _step_tokens(event)
            total_tokens += total
            cache_read_tokens += cache_read
        elif event_type == "text":
            text_parts.append(str((event.get("part") or {}).get("text", "")))
    return {
        "response_text": text_parts[-1] if text_parts else "",
        "metrics": {
            "llm_calls": max(starts, finishes),
            "total_tokens": total_tokens,
            "cache_read_tokens": cache_read_tokens,
            "unfinished_steps": max(starts - finishes, 0),
        },
    }
