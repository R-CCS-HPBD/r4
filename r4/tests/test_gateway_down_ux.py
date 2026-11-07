"""End-to-end "gateway down" UX test (bug.memo T3, 2026-04-25).

Catches H1/H2/H4 regressions: when the gateway is unreachable, the
CLI must (a) fail fast (≤ ~10s, not the 300s read timeout), (b) emit
the actionable tunnel/URL hint, and (c) exit non-zero.

Pointing at port 1 guarantees connection-refused without needing a
real listener.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

UNREACHABLE = "http://127.0.0.1:1"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "R4_GATEWAY_URL": UNREACHABLE,
            "R4_API_KEY": "test-key",
            # Make sure no profile leaks in.
            "R4_PROFILE": "nonexistent-test-profile",
        }
    )
    return env


@pytest.mark.parametrize(
    "argv",
    [
        ["health"],
        ["model", "list"],
        ["job", "list"],
    ],
)
def test_gateway_down_emits_hint_and_fails_fast(argv: list[str]) -> None:
    start = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "r4.cli", *argv],
        capture_output=True,
        text=True,
        env=_env(),
        timeout=30,
    )
    elapsed = time.monotonic() - start

    assert result.returncode != 0, (
        f"`r4 {' '.join(argv)}` should fail when the gateway is down "
        f"(stdout={result.stdout!r} stderr={result.stderr!r})"
    )
    # H4: must fail fast, not wait the read-timeout (300s).
    assert elapsed < 20, f"`r4 {' '.join(argv)}` took {elapsed:.1f}s"

    combined = (result.stdout + result.stderr).lower()
    # H1: tunnel/gateway hint must be present.
    assert "hint:" in combined, (
        f"Missing actionable hint for `r4 {' '.join(argv)}`.\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert any(kw in combined for kw in ("tunnel", "gateway", "reach")), (
        f"Hint text didn't mention tunnel/gateway/reach for "
        f"`r4 {' '.join(argv)}`.\nstderr={result.stderr!r}"
    )
