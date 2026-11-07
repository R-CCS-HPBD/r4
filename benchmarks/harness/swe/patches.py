"""Runner-side patch collection and FAIL_TO_PASS evaluation.

Fairness rules (claw-swe-bench methodology):

- The patch is ALWAYS `git diff` taken by the runner after the agent exits —
  never agent-reported.
- Setup/lock-file churn is stripped (`setup.py`, `pyproject.toml`,
  `setup.cfg`, `package-lock.json`, `yarn.lock`) so evaluation compares logic
  changes only.
- Resolution verdict comes from running the instance's FAIL_TO_PASS tests in
  the pre-installed task environment — never from the agent's own claims.
"""

from __future__ import annotations

from benchmarks.harness.swe.containers import ContainerRuntime
from benchmarks.harness.swe.dataset import SweInstance

_STRIPPED_FILES = {
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
    "package-lock.json",
    "yarn.lock",
}

_EVAL_TIMEOUT_SEC = 900


def collect_patch(runtime: ContainerRuntime, container: str, *, testbed: str = "/testbed") -> str:
    """Runner-side `git diff` — the single source of truth for the agent's fix."""
    result = runtime.exec(container, ["git", "diff"], workdir=testbed)
    if result.rc != 0:
        raise RuntimeError(f"git diff failed: {result.stderr[-300:]}")
    return result.stdout


def strip_setup_churn(patch: str) -> str:
    """Drop diff hunks for setup/lock files (they're environment noise, not fixes)."""
    kept: list[str] = []
    current_block: list[str] = []

    def flush() -> None:
        if not current_block:
            return
        header = next((line for line in current_block if line.startswith("diff --git")), "")
        parts = header.split(" b/")
        filename = parts[-1].strip() if len(parts) > 1 else header
        if filename not in _STRIPPED_FILES:
            kept.extend(current_block)
        current_block.clear()

    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git"):
            flush()
        current_block.append(line)
    flush()
    return "".join(kept)


def evaluate_resolved(
    runtime: ContainerRuntime,
    container: str,
    instance: SweInstance,
    *,
    testbed: str = "/testbed",
) -> dict:
    """Apply nothing — the agent already changed the tree. Run FAIL_TO_PASS
    tests in the container's task environment and report the verdict.

    A resolution verdict is ONLY emitted from this function (authoritative),
    never from agent logs.
    """
    tests = list(instance.fail_to_pass)
    if not tests:
        return {
            "resolved": False,
            "reason": "no FAIL_TO_PASS tests in instance",
            "rc": None,
            "log": "",
        }
    argv = ["python", "-m", "pytest", "-q", "--tb", "short", *tests]
    result = runtime.exec(container, argv, workdir=testbed, timeout_sec=_EVAL_TIMEOUT_SEC)
    log = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    return {
        "resolved": result.rc == 0,
        "reason": "all FAIL_TO_PASS tests passed" if result.rc == 0 else f"pytest rc={result.rc}",
        "rc": result.rc,
        "log": log[-4000:],
    }
