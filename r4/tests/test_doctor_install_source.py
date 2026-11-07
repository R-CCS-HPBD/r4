"""``r4 doctor`` install-source check (plan H-3.5, 2026-10-18).

The check warns when the running r4 package comes from a git checkout with
uncommitted changes, and never fails doctor.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from git_hermetic import git_init, run_git

from r4._cli.diagnostics import diagnostics_exit_code
from r4._cli.doctor import check_install_source

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _checkout_with_package(root: Path) -> Path:
    repo = root / "checkout"
    pkg = repo / "src" / "r4"
    pkg.mkdir(parents=True)
    git_init(repo)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text("x = 1\n")
    (repo / "elsewhere.txt").write_text("outside the package\n")
    run_git(repo, "add", "src")
    run_git(repo, "commit", "-q", "-m", "init")
    return pkg


def test_a_clean_checkout_is_ok(tmp_path: Path) -> None:
    pkg = _checkout_with_package(tmp_path)

    result = check_install_source(pkg)

    assert result["ok"] is True
    assert result["kind"] == "checkout"
    assert result["dirty"] == 0


def test_a_dirty_checkout_warns(tmp_path: Path) -> None:
    pkg = _checkout_with_package(tmp_path)
    (pkg / "mod.py").write_text("x = 2\n")
    (pkg / "new.py").write_text("y = 1\n")

    result = check_install_source(pkg)

    assert result["ok"] is None
    assert result["dirty"] == 2
    assert "uncommitted" in str(result["message"])
    assert "developer.md" in str(result["message"])


def test_changes_outside_the_package_do_not_warn(tmp_path: Path) -> None:
    pkg = _checkout_with_package(tmp_path)
    (pkg.parents[1] / "elsewhere.txt").write_text("edited\n")

    assert check_install_source(pkg)["ok"] is True


def test_the_check_does_not_rewrite_the_checkout_index(tmp_path: Path) -> None:
    pkg = _checkout_with_package(tmp_path)
    index = pkg.parents[1] / ".git" / "index"
    mod = pkg / "mod.py"
    later = index.stat().st_mtime_ns + 5_000_000_000
    os.utime(mod, ns=(later, later))  # stat change only: plain `git status` refreshes the index
    before = (index.read_bytes(), index.stat().st_mtime_ns)

    check_install_source(pkg)

    assert (index.read_bytes(), index.stat().st_mtime_ns) == before
    assert not (index.parent / "index.lock").exists()


def test_an_installed_package_is_ok(tmp_path: Path) -> None:
    pkg = tmp_path / "site-packages" / "r4"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")

    result = check_install_source(pkg)

    assert result == {"ok": True, "kind": "installed", "package_dir": str(pkg)}


def test_a_missing_git_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = _checkout_with_package(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))

    assert check_install_source(pkg)["ok"] is True


def test_a_dirty_checkout_never_fails_doctor(tmp_path: Path) -> None:
    pkg = _checkout_with_package(tmp_path)
    (pkg / "mod.py").write_text("x = 3\n")

    checks = {"install_source": check_install_source(pkg)}

    assert diagnostics_exit_code(checks) == 0


def test_hook_git_variables_do_not_redirect_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = _checkout_with_package(tmp_path)
    other = _checkout_with_package(tmp_path / "other")
    (other / "mod.py").write_text("dirty\n")
    monkeypatch.setenv("GIT_DIR", str(other.parents[1] / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other.parents[1]))

    assert check_install_source(pkg)["dirty"] == 0


def test_doctor_reports_the_install_source(monkeypatch: pytest.MonkeyPatch) -> None:
    from r4._cli import doctor

    monkeypatch.setattr(doctor, "run_full_diagnostics", lambda _config: {})
    monkeypatch.setattr(doctor, "_run_local_checks", lambda _config: {"ok": True})
    monkeypatch.setattr(doctor, "_run_sovereignty_checks", lambda _config: {"ok": True})
    seen: dict[str, object] = {}
    monkeypatch.setattr(doctor, "emit_full_diagnostics", lambda checks, **_kw: seen.update(checks))

    doctor._run_doctor(doctor.Namespace(as_json=True))

    assert "install_source" in seen
