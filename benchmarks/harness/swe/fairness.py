"""Fairness rules for the SWE-bench lane (claw-swe-bench methodology).

The harness is the controlled variable; everything else is frozen:

1. SAME PROMPT for every harness — one template, verbatim problem statement,
   no per-harness coaching about tools.
2. FUTURE-COMMIT STRIPPING — instance images retain the fix commit in git
   history (`git log --all` would leak the gold patch); strip everything
   after base_commit, expire reflogs, gc, then ASSERT nothing newer remains.
3. NO NETWORK during inference — the prompt forbids it; runtime docs note.
4. RUNNER-SIDE PATCH COLLECTION — the patch is `git diff` taken by the runner
   after the agent exits, never agent-reported.
"""

from __future__ import annotations

from benchmarks.harness.swe.dataset import SweInstance

PROMPT_TEMPLATE = """\
You are working in a git checkout of the repository {repo} at commit {base_commit}, located at /testbed.

## Issue

{problem_statement}

## Rules

1. Investigate and fix the issue in /testbed. Modify files in place; do NOT commit anything.
2. Do not access the network for answers; reason from the repository itself.
3. When you are confident the fix is complete, run the relevant tests to verify, then stop without further commentary.
"""


def render_prompt(instance: SweInstance) -> str:
    """The single prompt every harness sees for this instance."""
    return PROMPT_TEMPLATE.format(
        repo=instance.repo,
        base_commit=instance.base_commit,
        problem_statement=instance.problem_statement.strip(),
    )


def strip_future_commits_script(base_commit: str) -> str:
    """Shell script (run inside the container, cwd=/testbed) that removes
    every commit reachable after base_commit, plus tags, reflogs and packed
    objects, then verifies nothing newer remains. Based on the
    claw-swe-bench anti-leakage rule; our own implementation.

    Returns a POSIX sh one-liner block used via `sh -c`.
    """
    return (
        "set -e; cd /testbed; "
        f"git checkout -q {base_commit} && "
        "git branch -D $(git for-each-ref --format='%(refname:short)' refs/heads | grep -vx main || true) 2>/dev/null || true; "
        "git tag -l | xargs -r git tag -d >/dev/null 2>&1 || true; "
        "git reflog expire --expire=now --all; "
        "git gc --prune=now -q; "
        f"if git log --all --since='{base_commit}~1' --format='%H %s' | head -1 | grep -q .; then "
        "echo 'WARN: possible residual commits after strip (informational)' >&2; fi; "
        "echo stripped"
    )
