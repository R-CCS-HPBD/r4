"""Tool subprocesses never inherit the agent's stdin (2026-10-06).

In the TUI the bridge's stdin is the live JSONL pipe from the TUI. A shell
command that reads stdin (cat, ssh, read, a prompt) blocked on it or swallowed
TUI requests, cancel included: the session looked stuck in a tool call.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time

import pytest

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipes")

_CHILD = textwrap.dedent(
    """
    import sys
    from r4.companion import tools
    if sys.argv[1] == "stream":
        tools.set_shell_chunk_sink(lambda *a, **k: None)
    print(tools._shell_exec("cat; echo done-reading", timeout=20), flush=True)
    """
)


@posix_only
@pytest.mark.parametrize("path", ["blocking", "stream"])
def test_shell_tool_does_not_read_the_agents_stdin(path: str) -> None:
    started = time.monotonic()
    proc = subprocess.Popen(  # stdin stays an open, silent pipe — like the TUI's
        [sys.executable, "-c", _CHILD, path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        proc.wait(timeout=15)  # stdin is NOT closed: communicate() would send EOF
    except subprocess.TimeoutExpired:
        proc.kill()
    finally:
        proc.stdin.close()
    out, err = proc.stdout.read(), proc.stderr.read()
    assert "done-reading" in out, err[-500:]
    assert "timed out" not in out
    assert time.monotonic() - started < 15
