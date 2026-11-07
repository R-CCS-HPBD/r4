"""``r4 console`` launch order: a checkout runs its own Console v3 source.

The per-user release binary (``r4 tui install``) is shared by every clone on a
host; it must not shadow a checkout whose TUI deps are installed (D-25).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from r4._cli import console as console_module
from r4._cli import tui as tui_module

REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CHECKOUT_PACKAGE_DIR = console_module.checkout_package_dir


def _make_pkg(root: Path, *, deps: bool = True, stamp: bool = True) -> Path:
    pkg = root / "repo" / "packages" / "r4-tui-v3"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text('{"name": "@r4/tui-v3"}\n')
    (pkg / "bun.lock").write_text("{}\n")
    if deps:
        solid = pkg / "node_modules" / "@opentui" / "solid"
        solid.mkdir(parents=True)
        (solid / "package.json").write_text("{}\n")
    if deps and stamp:
        (pkg / "node_modules" / console_module.DEPS_STAMP).write_bytes(
            (pkg / "package.json").read_bytes() + (pkg / "bun.lock").read_bytes()
        )
    return pkg


def _user_bin(tmp_path: Path) -> Path:
    path = tmp_path / "home" / "bin" / "r4-tui"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"release")
    return path


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    monkeypatch.delenv("R4_TUI_BIN", raising=False)
    monkeypatch.setenv("R4_TUI_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(console_module, "checkout_package_dir", lambda: None)
    monkeypatch.setattr(console_module, "_find_bun", lambda: "/fake/bun")
    monkeypatch.setattr(console_module, "_bun_version", lambda bun: (1, 3, 14))
    monkeypatch.setattr(console_module, "_checkout_rev", lambda pkg: "abc1234 2026-09-25")
    monkeypatch.chdir(tmp_path)
    yield


def _checkout(monkeypatch, pkg: Path) -> None:
    monkeypatch.setattr(console_module, "checkout_package_dir", lambda: pkg)


class TestPlan:
    def test_runnable_checkout_beats_release_binary(self, monkeypatch, tmp_path):
        pkg = _make_pkg(tmp_path)
        _user_bin(tmp_path)
        _checkout(monkeypatch, pkg)
        assert console_module.plan_console_launch() == ("source", pkg, "checkout")

    def test_checkout_without_node_modules_uses_release_binary(self, monkeypatch, tmp_path):
        pkg = _make_pkg(tmp_path, deps=False)
        binary = _user_bin(tmp_path)
        _checkout(monkeypatch, pkg)
        assert console_module.plan_console_launch() == ("binary", binary, "release binary")

    def test_partial_node_modules_is_not_runnable(self, monkeypatch, tmp_path):
        pkg = _make_pkg(tmp_path, deps=False)
        (pkg / "node_modules").mkdir()
        _checkout(monkeypatch, pkg)
        assert console_module.checkout_blocker(pkg) == "its node_modules is not installed"

    @pytest.mark.parametrize(
        ("bun", "version", "blocker"),
        [
            (None, None, "bun was not found"),
            ("/fake/bun", (1, 3, 13), "bun 1.3.13 is older than 1.3.14"),
            ("/fake/bun", None, "bun unknown is older than 1.3.14"),
        ],
    )
    def test_bun_gate(self, monkeypatch, tmp_path, bun, version, blocker):
        pkg = _make_pkg(tmp_path)
        binary = _user_bin(tmp_path)
        _checkout(monkeypatch, pkg)
        monkeypatch.setattr(console_module, "_find_bun", lambda: bun)
        monkeypatch.setattr(console_module, "_bun_version", lambda b: version)
        assert console_module.checkout_blocker(pkg) == blocker
        assert console_module.plan_console_launch() == ("binary", binary, "release binary")

    def test_r4_tui_bin_wins_over_runnable_checkout(self, monkeypatch, tmp_path):
        _checkout(monkeypatch, _make_pkg(tmp_path))
        explicit = tmp_path / "explicit-tui"
        explicit.write_bytes(b"x")
        monkeypatch.setenv("R4_TUI_BIN", str(explicit))
        assert console_module.plan_console_launch() == ("binary", explicit, "R4_TUI_BIN")

    def test_r4_tui_bin_missing_falls_to_dev_source(self, monkeypatch, tmp_path):
        pkg = _make_pkg(tmp_path)
        _user_bin(tmp_path)
        _checkout(monkeypatch, pkg)
        monkeypatch.setenv("R4_TUI_BIN", str(tmp_path / "gone"))
        assert tui_module.resolve_tui_binary() is None  # existing contract
        assert console_module.plan_console_launch() == ("source", pkg, "R4_TUI_BIN file missing")

    def test_not_a_checkout_uses_release_binary(self, tmp_path):
        binary = _user_bin(tmp_path)
        assert console_module.plan_console_launch() == ("binary", binary, "release binary")

    def test_nothing_installed_is_dev_source_fallback(self, tmp_path):
        assert console_module.plan_console_launch() == ("source", None, "dev-source fallback")


class TestCheckoutPackageDir:
    def test_resolves_by_module_path_and_ignores_cwd(self, monkeypatch, tmp_path):
        other = _make_pkg(tmp_path / "other")
        monkeypatch.chdir(other.parents[1])  # another clone's root as cwd
        own = _REAL_CHECKOUT_PACKAGE_DIR()
        assert own != other
        if own is not None:  # editable checkout: parents[4] of console.py
            assert own == REPO_ROOT / "packages" / "r4-tui-v3"


class TestDepsState:
    def test_ok(self, tmp_path):
        assert console_module.deps_state(_make_pkg(tmp_path)) == "ok"

    def test_unstamped(self, tmp_path):
        assert console_module.deps_state(_make_pkg(tmp_path, stamp=False)) == "unstamped"

    def test_stale_after_lock_change(self, tmp_path):
        pkg = _make_pkg(tmp_path)
        (pkg / "bun.lock").write_text('{"changed": true}\n')
        assert console_module.deps_state(pkg) == "stale"

    def test_makefile_writes_the_stamp_this_module_reads(self):
        makefile = (REPO_ROOT / "Makefile").read_text()
        assert re.search(
            r"cat package\.json bun\.lock > node_modules/" + re.escape(console_module.DEPS_STAMP),
            makefile,
        )


class TestLaunch:
    def _run(self, monkeypatch) -> dict:
        calls: dict = {}

        def fake_source(*, demo, fresh, backend, env):
            calls["source"] = env
            return 0

        def fake_binary(binary, *, demo, fresh, backend, env):
            calls["binary"] = binary
            return 0

        monkeypatch.setattr(console_module, "_launch_dev_source", fake_source)
        monkeypatch.setattr(console_module, "_launch_binary", fake_binary)
        args = SimpleNamespace(demo=False, offline=False, backend=None)
        assert console_module.cmd_console(args) == 0
        return calls

    def test_checkout_source_notes(self, monkeypatch, tmp_path, capsys):
        pkg = _make_pkg(tmp_path, stamp=False)
        binary = _user_bin(tmp_path)
        _checkout(monkeypatch, pkg)
        calls = self._run(monkeypatch)
        err = capsys.readouterr().err
        assert "source" in calls and "binary" not in calls
        assert f"Console v3 from this checkout: {pkg} (last TUI commit abc1234 2026-09-25)" in err
        assert "has no install stamp — run `make tui-deps`" in err
        assert f"R4_TUI_BIN={binary} r4 console" in err

    def test_release_binary_in_checkout_venv_says_why(self, monkeypatch, tmp_path, capsys):
        pkg = _make_pkg(tmp_path, deps=False)
        binary = _user_bin(tmp_path)
        _checkout(monkeypatch, pkg)
        (pkg.parents[1] / ".venv").mkdir()
        monkeypatch.setattr(console_module.sys, "prefix", str(pkg.parents[1] / ".venv"))
        calls = self._run(monkeypatch)
        assert calls["binary"] == binary
        assert "its node_modules is not installed" in capsys.readouterr().err

    def test_release_binary_outside_checkout_venv_is_quiet(self, monkeypatch, tmp_path, capsys):
        pkg = _make_pkg(tmp_path, deps=False)  # install-r4.sh: ~/.r4/src, venv ~/.r4/venv
        _user_bin(tmp_path)
        _checkout(monkeypatch, pkg)
        monkeypatch.setattr(console_module.sys, "prefix", str(tmp_path / "elsewhere"))
        self._run(monkeypatch)
        assert "r4:" not in capsys.readouterr().err


class TestStatus:
    def test_status_prints_what_r4_console_runs(self, monkeypatch, tmp_path, capsys):
        pkg = _make_pkg(tmp_path)
        _checkout(monkeypatch, pkg)
        assert tui_module.TuiCommand()._status() == 0
        out = capsys.readouterr().out
        assert f"checkout src   : {pkg} (runnable)" in out
        assert f"r4 console runs: source {pkg} (checkout)" in out

    def test_install_note_when_checkout_wins(self, monkeypatch, tmp_path):
        _checkout(monkeypatch, _make_pkg(tmp_path))
        note = tui_module._checkout_precedence_note()
        assert note is not None and "R4_TUI_BIN=" in note
