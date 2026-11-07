"""Tests for dead_compat_bridge_validator.py — drift detection tier expansion (Q-07)."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from tools.dead_compat_bridge_validator import (
    CompatBridge,
    DeadBridgeFinding,
    _collect_source_files,
    _extract_getattr_targets,
    _extract_import_sources,
    _is_reexport_only_init,
    _old_path_referenced,
    find_compat_bridges,
    find_dead_compat_bridges,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_repo(tmp_path: Path) -> Path:
    """Create a minimal mock repository structure."""
    # Create source directories
    for d in ["umrs/src/umrs", "r4/src/r4", "gateway/gateway"]:
        (tmp_path / d).mkdir(parents=True)
    # Create test directory
    (tmp_path / "tests").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# _collect_source_files
# ---------------------------------------------------------------------------


class TestCollectSourceFiles:
    def test_collects_python_files(self, mock_repo: Path) -> None:
        (mock_repo / "umrs/src/umrs/foo.py").write_text("# empty")
        files = _collect_source_files(mock_repo, ("umrs/src",))
        assert any("umrs/src/umrs/foo.py" in f.as_posix() for f in files)

    def test_excludes_test_files(self, mock_repo: Path) -> None:
        (mock_repo / "umrs/src/umrs/foo.py").write_text("# empty")
        (mock_repo / "tests/test_foo.py").write_text("# empty")
        files = _collect_source_files(mock_repo, ("umrs/src", "tests"))
        rels = {f.relative_to(mock_repo).as_posix() for f in files}
        assert "tests/test_foo.py" not in rels

    def test_missing_root_ignored(self, mock_repo: Path) -> None:
        files = _collect_source_files(mock_repo, ("nonexistent/",))
        assert files == []


# ---------------------------------------------------------------------------
# _extract_import_sources
# ---------------------------------------------------------------------------


class TestExtractImportSources:
    def test_simple_from_import(self, mock_repo: Path) -> None:
        f = mock_repo / "umrs/src/umrs/foo.py"
        f.write_text("from old_module import Something\n")
        sources = _extract_import_sources(f)
        assert ("old_module", "Something") in sources

    def test_multiple_imports(self, mock_repo: Path) -> None:
        f = mock_repo / "umrs/src/umrs/foo.py"
        f.write_text("from old_module import A\nfrom other_module import B, C\nimport simple\n")
        sources = _extract_import_sources(f)
        modules = {s[0] for s in sources}
        assert "old_module" in modules
        assert "other_module" in modules
        # 'import simple' has no ImportFrom node, so not captured

    def test_syntax_error_returns_empty(self, mock_repo: Path) -> None:
        f = mock_repo / "umrs/src/umrs/bad.py"
        f.write_text("def broken(\n")
        assert _extract_import_sources(f) == []


# ---------------------------------------------------------------------------
# _extract_getattr_targets
# ---------------------------------------------------------------------------


class TestExtractGetattrTargets:
    def test_importlib_import_module(self, mock_repo: Path) -> None:
        f = mock_repo / "umrs/src/umrs/foo.py"
        f.write_text(
            'def __getattr__(name):\n    return importlib.import_module("legacy_module")\n'
        )
        targets = _extract_getattr_targets(f)
        assert any(t[0] == "legacy_module" for t in targets)


# ---------------------------------------------------------------------------
# _is_reexport_only_init
# ---------------------------------------------------------------------------


class TestIsReexportOnlyInit:
    def test_only_imports(self, mock_repo: Path) -> None:
        f = mock_repo / "umrs/src/umrs/__init__.py"
        f.write_text("from .foo import something\n")
        assert _is_reexport_only_init(f) is True

    def test_has_logic(self, mock_repo: Path) -> None:
        f = mock_repo / "umrs/src/umrs/__init__.py"
        f.write_text("from .foo import something\nVALUE = 42\n")
        assert _is_reexport_only_init(f) is False

    def test_comments_only(self, mock_repo: Path) -> None:
        f = mock_repo / "umrs/src/umrs/__init__.py"
        f.write_text("# comment only\n")
        assert _is_reexport_only_init(f) is False


# ---------------------------------------------------------------------------
# find_compat_bridges
# ---------------------------------------------------------------------------


class TestFindCompatBridges:
    def test_finds_getattr_bridge(self, mock_repo: Path) -> None:
        f = mock_repo / "umrs/src/umrs/compat.py"
        f.write_text(
            "import importlib\n"
            "\n"
            "def __getattr__(name):\n"
            '    return importlib.import_module("old_package")\n'
        )
        bridges = find_compat_bridges(mock_repo)
        assert any("compat.py" in b.file for b in bridges)
        assert any("old_package" in b.old_path for b in bridges)

    def test_finds_compat_named_module(self, mock_repo: Path) -> None:
        """Design-wave classes: compat-intent filename is detected, generic
        ImportFrom statements elsewhere are NOT (688-finding category error
        fix: a plain sibling import is not a compat bridge)."""
        compat = mock_repo / "umrs/src/umrs/legacy_compat.py"
        compat.write_text("from legacy_api import old_func\n")
        plain = mock_repo / "umrs/src/umrs/foo.py"
        plain.write_text("from modern_api import new_func\n")
        bridges = find_compat_bridges(mock_repo)
        assert any(
            b.kind == "compat-named module" and "legacy_compat" in b.old_path for b in bridges
        )
        assert not any(b.file.endswith("foo.py") for b in bridges)

    def test_finds_reexport_only_init(self, mock_repo: Path) -> None:
        """Test that re-export-only __init__.py files are detected."""
        # Create proper nested structure under mock_repo
        init_dir = mock_repo / "umrs/src/umrs"
        init_dir.mkdir(parents=True, exist_ok=True)
        f = init_dir / "__init__.py"
        f.write_text("from .submodule import something\n")
        bridges = find_compat_bridges(mock_repo)
        assert any("re-export only __init__.py" in b.kind for b in bridges)

    def test_skips_known_active_bridges(self, mock_repo: Path) -> None:
        f = mock_repo / "gateway/gateway/auth.py"
        f.write_text("from legacy_auth import old_func\n")
        bridges = find_compat_bridges(mock_repo)
        # gateway/gateway/auth.py is in KNOWN_ACTIVE_BRIDGES
        assert not any("gateway/gateway/auth.py" in b.file for b in bridges)


# ---------------------------------------------------------------------------
# _old_path_referenced
# ---------------------------------------------------------------------------


class TestOldPathReferenced:
    def test_referenced_in_consumer(self, mock_repo: Path) -> None:
        # Create bridge file
        bridge = mock_repo / "umrs/src/umrs/compat.py"
        bridge.write_text("from old_pkg import x\n")
        # Create consumer file
        consumer = mock_repo / "umrs/src/umrs/user.py"
        consumer.write_text("from old_pkg import x\n")
        assert (
            _old_path_referenced(mock_repo, "old_pkg", exclude_file="umrs/src/umrs/compat.py")
            is True
        )

    def test_not_referenced(self, mock_repo: Path) -> None:
        bridge = mock_repo / "umrs/src/umrs/compat.py"
        bridge.write_text("from dead_pkg import x\n")
        # Nothing references dead_pkg (excluding the bridge itself)
        assert (
            _old_path_referenced(mock_repo, "dead_pkg", exclude_file="umrs/src/umrs/compat.py")
            is False
        )

    def test_not_referenced_no_exclude(self, mock_repo: Path) -> None:
        """When no bridge file is excluded, the bridge file itself will match."""
        bridge = mock_repo / "umrs/src/umrs/compat.py"
        bridge.write_text("from dead_pkg import x\n")
        # Without exclude, the bridge file itself has the import, so it returns True
        assert _old_path_referenced(mock_repo, "dead_pkg", exclude_file=None) is True


# ---------------------------------------------------------------------------
# find_dead_compat_bridges
# ---------------------------------------------------------------------------


class TestFindDeadCompatBridges:
    def test_dead_bridge_detected(self, mock_repo: Path) -> None:
        """A compat-named module with zero references anywhere = dead."""
        bridge = mock_repo / "umrs/src/umrs/compat.py"
        bridge.write_text("from dead_api import old_func\n")
        # Nothing references umrs.compat
        findings = find_dead_compat_bridges(mock_repo)
        assert len(findings) >= 1
        assert any(f.bridge.file.endswith("umrs/src/umrs/compat.py") for f in findings)
        assert any("umrs.compat" in f.bridge.old_path for f in findings)

    def test_active_bridge_not_flagged(self, mock_repo: Path) -> None:
        bridge = mock_repo / "umrs/src/umrs/compat.py"
        bridge.write_text("from active_api import x\n")
        consumer = mock_repo / "umrs/src/umrs/user.py"
        consumer.write_text("from active_api import x\n")
        findings = find_dead_compat_bridges(mock_repo)
        assert not any("active_api" in f.bridge.old_path for f in findings)

    def test_report_structure(self, mock_repo: Path) -> None:
        bridge = mock_repo / "umrs/src/umrs/compat.py"
        bridge.write_text("from dead_pkg import x\n")
        findings = find_dead_compat_bridges(mock_repo)
        assert len(findings) >= 1
        f = findings[0]
        assert isinstance(f, DeadBridgeFinding)
        assert isinstance(f.bridge, CompatBridge)
        assert f.reason
        assert f.suggestion


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestCLI:
    def test_json_output(self, mock_repo: Path, capsys: pytest.CaptureFixture) -> None:
        _ = capsys  # fixture named for pytest
        import os

        bridge = mock_repo / "umrs/src/umrs/compat.py"
        bridge.write_text("from dead_pkg import x\n")
        old_cwd = os.getcwd()
        os.chdir(mock_repo)
        try:
            with mock.patch("sys.argv", ["dead_compat_bridge_validator.py", "--json"]):
                from tools.dead_compat_bridge_validator import main

                result = main()
                assert isinstance(result, int)
                assert result in (0, 1)
        finally:
            os.chdir(old_cwd)

    def test_human_output(self, mock_repo: Path, capsys: pytest.CaptureFixture) -> None:
        import os

        bridge = mock_repo / "umrs/src/umrs/compat.py"
        bridge.write_text("from dead_pkg import x\n")
        old_cwd = os.getcwd()
        os.chdir(mock_repo)
        try:
            with mock.patch("sys.argv", ["dead_compat_bridge_validator.py"]):
                from tools.dead_compat_bridge_validator import main

                result = main()
                captured = capsys.readouterr()
                assert isinstance(result, int)
                assert "Dead Compat Bridge Validator" in captured.out
        finally:
            os.chdir(old_cwd)
