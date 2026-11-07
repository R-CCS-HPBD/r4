"""Tests for COR-007 special-case reduction audit scanner."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

# Import the scanner module (tools is importable from the repo root; ADR-053)
from tools import special_case_scanner as scanner


class TestScanFile:
    def _write_py(self, tmp_path: Path, code: str) -> Path:
        p = tmp_path / "test_module.py"
        p.write_text(textwrap.dedent(code))
        return p

    def test_detects_env_fallback(self, tmp_path):
        p = self._write_py(
            tmp_path,
            """\
            import os
            val = os.environ.get("SOME_KEY", "default_value")
        """,
        )
        findings = scanner.scan_file(p, tmp_path)
        types = [f.pattern_type for f in findings]
        assert "env_fallback" in types

    def test_ignores_none_fallback(self, tmp_path):
        p = self._write_py(
            tmp_path,
            """\
            import os
            val = os.environ.get("SOME_KEY", None)
        """,
        )
        findings = scanner.scan_file(p, tmp_path)
        env_findings = [f for f in findings if f.pattern_type == "env_fallback"]
        assert len(env_findings) == 0

    def test_detects_platform_branch(self, tmp_path):
        p = self._write_py(
            tmp_path,
            """\
            import sys
            if sys.platform == "linux":
                pass
        """,
        )
        findings = scanner.scan_file(p, tmp_path)
        types = [f.pattern_type for f in findings]
        assert "platform_branch" in types

    def test_detects_import_fallback(self, tmp_path):
        p = self._write_py(
            tmp_path,
            """\
            try:
                import ujson as json
            except ImportError:
                import json
        """,
        )
        findings = scanner.scan_file(p, tmp_path)
        types = [f.pattern_type for f in findings]
        assert "import_fallback" in types

    def test_handles_syntax_error(self, tmp_path):
        p = self._write_py(tmp_path, "def broken(:\n  pass\n")
        findings = scanner.scan_file(p, tmp_path)
        assert findings == []


class TestScanResult:
    def test_aggregation(self):
        findings = [
            scanner.Finding("a.py", 1, "untagged", "env_fallback", "x"),
            scanner.Finding("b.py", 2, "blessed", "env_fallback", "y"),
            scanner.Finding("c.py", 3, "untagged", "import_fallback", "z"),
        ]
        result = scanner.ScanResult(total=3, findings=findings)
        # Manually compute
        for f in findings:
            result.by_category[f.category] = result.by_category.get(f.category, 0) + 1
            result.by_type[f.pattern_type] = result.by_type.get(f.pattern_type, 0) + 1
        assert result.by_category["untagged"] == 2
        assert result.by_category["blessed"] == 1
        assert result.by_type["env_fallback"] == 2


class TestRunScan:
    def test_scans_existing_src(self):
        """Verify the scanner can run on real source trees without crashing."""
        result = scanner.run_scan(["tools"])
        assert isinstance(result.total, int)
        assert isinstance(result.findings, list)

    def test_empty_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scanner, "ROOT", tmp_path)
        result = scanner.run_scan(["nonexistent"])
        assert result.total == 0


class TestCategorize:
    def test_blessed_path(self):
        cat = scanner._categorize("umrs/src/umrs/metrics.py", 10)
        assert cat == "blessed"

    def test_untagged_default(self):
        cat = scanner._categorize("some/random/file.py", 10)
        assert cat == "untagged"


class TestMain:
    def test_main_runs(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["special_case_scanner.py"])
        rc = scanner.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Special-Case Reduction Audit" in out
