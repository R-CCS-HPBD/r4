"""R4 Companion adapter.

Each case runs in a subprocess so the companion's tool calls (which resolve
paths against the process cwd) are sandboxed to the case workdir and a
companion crash cannot kill the runner.

Driver protocol: the last stdout line is a JSON AdapterResult payload:

    python -m benchmarks.harness.adapters.r4_companion \
        --prompt-file PROMPT.txt --workdir DIR [--model MODEL]

SECURITY: the driver runs the companion in PermissionMode.TRUST — tools
execute without interactive approval. Only use with sandboxed workdirs and
trusted, version-pinned suites. Never point it at a live checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from benchmarks.harness.adapters.base import (
    AdapterResult,
    JsonDriverAdapter,
    parse_driver_output,
)

SWE_VENV_ENV = "R4BENCH_R4_LINUX_VENV"
SWE_REPO_PATH = "/r4platform"

# Ablation label -> env flag consumed by r4.companion.ablation. The labels
# name the harness layer switched OFF for that cell.
_R4_ABLATION_ENV: dict[str, dict[str, str]] = {
    "no_steering": {"R4_HARNESS_ABLATE_STEERING": "1"},
    "no_guides": {"R4_HARNESS_ABLATE_GUIDES": "1"},
    "no_memory": {"R4_HARNESS_ABLATE_MEMORY": "1"},
}


def drive(prompt_file: Path, workdir: Path, model: str | None) -> dict[str, Any]:
    """Run one case through CompanionSession; returns an AdapterResult payload."""
    import os
    import time

    os.chdir(workdir)

    # Local imports keep the bench import graph light for external harnesses.
    from r4.client import Client
    from r4.companion.permissions import PermissionMode
    from r4.companion.session import CompanionSession

    client = Client()
    session = CompanionSession(
        session_id=f"bench-{int(time.time())}",
        name="harness-bench",
        client=client,
        model=model,
        permission_mode=PermissionMode.TRUST,
        # No endpoint churn mid-case; a bench case must not wait on provisioning.
        endpoint_refresh_interval_sec=86_400,
    )
    # Bench driver: pre-approve every tool call for the duration of a case.
    # Without a callback the newer permission engine (ADR-035) fails closed
    # on "not on the server trust allowlist", killing every tool while the
    # adapter still reports "success" — the bench's 2026-09-14 weekly sweep.
    session.permissions.set_approval_callback(lambda *_args, **_kwargs: True)
    prompt = prompt_file.read_text()
    started = time.monotonic()
    response = session.send(prompt)
    elapsed = time.monotonic() - started
    usage = session.usage_summary.to_dict()
    ablation = os.environ.get("R4_HARNESS_ABLATION_LABEL", "full")
    # NOTE: no tool_calls metric — SessionMessage history does not track tool
    # messages, so any count from it would be a false zero. Better absent than
    # wrong (ADR-020 section 13: authoritative surfaces only).
    # Tool-denied honesty: a blocked session is NOT a "success"; count the
    # permission-denied signal in results as failed (detected live 2026-09-15:
    # tooling stopped early and hello.py never existed; callers were misled
    # by the bench status).
    payload_status = (
        "failed"
        if "[tool_contract:permission_denied" in (response or "")
        else "success"
    )
    return {
        "status": payload_status,
        "response_text": response,
        "metrics": {
            "wallclock_sec": round(elapsed, 3),
            "llm_calls": usage["llm_calls"],
            "total_tokens": usage["total_tokens"],
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "compaction_calls": usage["compaction_calls"],
            "subagent_calls": usage["subagent_calls"],
            "ablation": ablation,
        },
        "error": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="r4-companion-bench-driver")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)
    try:
        payload = drive(Path(args.prompt_file), Path(args.workdir), args.model)
    except Exception as exc:  # noqa: BLE001 — bench isolates harness failures
        payload = {"status": "error", "response_text": "", "metrics": {}, "error": str(exc)}
    print(json.dumps(payload))
    return 0


class R4CompanionAdapter(JsonDriverAdapter):
    """Runs the R4 Companion agent loop against each case via subprocess."""

    name = "r4_companion"

    def supports_ablation(self, label: str) -> bool:
        return label == "full" or label in _R4_ABLATION_ENV

    def ablation_env(self) -> dict[str, str]:
        if not self.supports_ablation(self.ablation) or self.ablation == "full":
            return {}
        return {
            **_R4_ABLATION_ENV[self.ablation],
            "R4_HARNESS_ABLATION_LABEL": self.ablation,
        }

    def subprocess_env(self, workdir: Path) -> dict[str, str]:  # noqa: ARG002 — uniform interface
        """Driver runs with cwd=workdir (outside the repo), so the repo root
        must be on PYTHONPATH for `-m benchmarks.harness...` to import."""
        import os

        env = dict(os.environ)
        repo_root = str(Path(__file__).resolve().parents[3])
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{existing}" if existing else repo_root
        env.update(self.ablation_env())
        return env

    def driver_argv(self, prompt_file: Path, workdir: Path) -> list[str]:
        argv = [
            sys.executable,
            "-m",
            "benchmarks.harness.adapters.r4_companion",
            "--prompt-file",
            str(prompt_file),
            "--workdir",
            str(workdir),
        ]
        if self.model:
            argv += ["--model", self.model]
        return argv

    # --- SWE-bench lane (in-container driver, linux x86_64 venv) ---
    def swe_support(self) -> str | None:
        if not os.environ.get(SWE_VENV_ENV):
            return f"{SWE_VENV_ENV} not set (linux x86_64 r4 driver venv path)"
        venv = Path(os.environ[SWE_VENV_ENV]).expanduser()
        # lexists: the venv's bin/python symlinks to the sibling python312 dir,
        # which is a valid target only INSIDE the container mount — on the host
        # the link is dandling but the file still exists.
        if not os.path.lexists(str(venv / "bin" / "python")):
            return f"{SWE_VENV_ENV}={venv} has no bin/python (build the venv first)"
        if not (venv.parent / "python312" / "bin" / "python3.12").is_file():
            return f"{venv.parent}/python312 missing (venv base interpreter must sit next to the venv)"
        return None

    def swe_mounts(self) -> list[tuple[str, str]]:
        # Mount the whole bench dir at /out so the venv's absolute interpreter
        # links (venv -> sibling python312/) resolve unchanged in-container.
        venv = Path(os.environ[SWE_VENV_ENV]).expanduser()
        repo_root = Path(__file__).resolve().parents[3]
        return [(str(venv.parent), "/out"), (str(repo_root), SWE_REPO_PATH)]

    def run_swe_case(self, instance: Any, container: str, runtime: Any) -> AdapterResult:  # noqa: ARG002 — uniform interface
        """Run the whole companion INSIDE the eval container, cwd /testbed.

        The driver's r4 Client reads ~/.config/r4 — we replicate the host
        config files into the container. Requires {SWE_VENV_ENV}: a linux
        x86_64 venv with r4 installed whose base interpreter lives in a
        sibling python312/ dir (python-build-standalone).
        """
        venv = Path(os.environ[SWE_VENV_ENV]).expanduser()
        host_config = Path.home() / ".config" / "r4"
        for cfg in sorted(host_config.glob("*.json")):
            runtime.write_file(container, f"/root/.config/r4/{cfg.name}", cfg.read_text())
        argv = [
            f"/out/{venv.name}/bin/python",
            "-m",
            "benchmarks.harness.adapters.r4_companion",
            "--prompt-file",
            "/tmp/r4bench_prompt.txt",
            "--workdir",
            "/testbed",
        ]
        if self.model:
            argv += ["--model", self.model]
        started = time.monotonic()
        result = runtime.exec(
            container,
            argv,
            workdir="/testbed",
            env={"PYTHONPATH": SWE_REPO_PATH},
            timeout_sec=self.timeout_sec,
        )
        elapsed = time.monotonic() - started
        if result.rc == 124:
            return AdapterResult(status="failed", error=f"timeout after {self.timeout_sec}s")
        parsed = parse_driver_output(result.stdout or "", result.stderr or "", result.rc)
        if parsed.metrics:
            parsed.metrics.setdefault("wallclock_sec", round(elapsed, 3))
        return parsed


if __name__ == "__main__":
    raise SystemExit(main())
