"""ADR-101: the macOS sandbox for real (sandbox-exec). Runs only on a Mac (no macOS CI runner)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from r4.companion.sandbox import SandboxContext, SandboxSetting, SeatbeltBackend, run_self_test
from r4.companion.sandbox import selftest as selftest_mod
from r4.companion.web_access import make_web_access

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not os.path.exists("/usr/bin/sandbox-exec"),
    reason="macOS sandbox-exec only",
)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    # HOME sits beside the workspace, never inside it: a workspace that
    # contains HOME is refused as a writable root (2026-10-31, first Mac run).
    ws = tmp_path / "ws"
    ws.mkdir()
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_x").write_text("PRIVATE-KEY")
    monkeypatch.setenv("HOME", str(home))
    selftest_mod.clear_cache()
    context = SandboxContext(
        setting=lambda: SandboxSetting("on", "", "slash"),
        web_access=lambda: make_web_access("none", source="flag"),
        workspace_root=ws,
        backend=SeatbeltBackend(),
    )
    yield context, ws, tmp_path
    context.close()


@pytest.fixture
def outside():
    """A directory outside every writable place, the per-user temp dir included
    (pytest's tmp_path is inside it, and the profile keeps it writable)."""
    path = Path(__file__).resolve().parent / f".seatbelt-outside-{uuid.uuid4().hex[:8]}"
    path.mkdir()
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _run(context, command, cwd):
    spec = context.wrap(command, shell=True, cwd=str(cwd), env=dict(os.environ))
    return subprocess.run(
        spec.argv, env=spec.env, cwd=str(cwd), capture_output=True, text=True, timeout=30
    )


def test_seatbelt_confines_writes_and_reads(ctx, outside):
    context, ws, _ = ctx
    result = _run(context, f"echo in > in.txt; echo out > {outside}/x; cat ~/.ssh/id_x", ws)
    report = f"rc={result.returncode} stderr={result.stderr!r}"
    assert (ws / "in.txt").exists(), report
    assert not (outside / "x").exists(), report
    assert "PRIVATE-KEY" not in result.stdout, report


def test_seatbelt_self_test_passes(ctx):
    context, _, _ = ctx
    context.check_available()
    assert run_self_test(context) == (True, "")


def test_seatbelt_denies_network_with_web_none(ctx):
    context, ws, _ = ctx
    code = (
        "import socket; s=socket.socket(); s.settimeout(3); "
        "s.connect(('1.1.1.1', 443)); print('OPEN')"
    )
    result = _run(context, f'{sys.executable} -c "{code}"', ws)
    assert "OPEN" not in result.stdout, result.stderr
