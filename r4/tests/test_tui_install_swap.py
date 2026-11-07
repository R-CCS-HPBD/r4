"""``r4 tui install`` swaps the binary with one rename (cycle-2 security lane, 2026-10-21).

The download dir was on TMPDIR (a tmpfs on most Linux hosts), the bin dir
under HOME: ``shutil.move`` then copied the new binary over the installed
one in place. With the TUI running that raised an uncaught "Text file busy"
traceback on Linux (macOS kills a binary rewritten in place). An empty or
HTML checksum file raised IndexError instead of refusing the install.
Repro: $C2/security/install/repro_install_inplace.py.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="ETXTBSY is Linux behaviour")


@pytest.fixture
def release(tmp_path, monkeypatch):
    from r4._cli import tui

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    name = tui.tui_asset_name()
    dist = tmp_path / "dist"
    dist.mkdir()
    binary = dist / name
    binary.write_text("#!/bin/sh\necho new-tui\n")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (dist / f"{name}.sha256").write_text(f"{digest}  {name}\n")
    monkeypatch.setenv(tui.DIST_BASE_URL_ENV, dist.as_uri())
    return dist / f"{name}.sha256"


def test_install_replaces_a_running_binary(release, monkeypatch):
    from r4._cli import tui

    # TMPDIR on another filesystem than HOME (tmpfs /tmp): a rename from
    # outside the bin dir fails with EXDEV, as it does on such hosts.
    real_rename = os.rename

    def cross_device(src, dst, *args, **kwargs):
        if not str(src).startswith(str(tui.user_bin_dir())):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "rename", cross_device)
    installed = tui.user_bin_path()
    installed.parent.mkdir(parents=True)
    shutil.copy(shutil.which("sleep"), installed)
    running = subprocess.Popen([str(installed), "30"])
    try:
        assert tui.install_binary() == 0
    finally:
        running.kill()
        running.wait()
    out = subprocess.run([str(installed)], capture_output=True, text=True).stdout
    assert out.strip() == "new-tui"
    assert not list(installed.parent.glob(".r4-tui-*"))


@pytest.mark.parametrize("content", ["", "<!DOCTYPE html><html>Not Found</html>"])
def test_a_checksum_file_without_a_digest_refuses_the_install(release, content):
    from r4._cli import tui

    release.write_text(content)
    assert tui.install_binary() == 1
    assert not tui.user_bin_path().exists()
