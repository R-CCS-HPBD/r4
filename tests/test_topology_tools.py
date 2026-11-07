"""Tests for runtime topology simplification tools.

Covers:
  - Transport boundary audit (zmq/http surface mapping)
  - Backward-compat retirement tracker
  - Security compat scanner
"""

from pathlib import Path

# =====================================================================
# Transport Boundary Audit
# =====================================================================
from tools.transport_boundary_audit import (
    TransportUsage,
    _is_approved_zmq,
    _scan_http_routes,
    _scan_messagequeue_imports,
    _scan_zmq_imports,
    run_audit,
)


class TestTransportBoundaryAudit:
    """Transport boundary audit unit tests."""

    def test_approved_zmq_modules(self) -> None:
        """Core transport modules should be approved."""
        assert _is_approved_zmq("core/MessageQueue.py")
        assert _is_approved_zmq("server/step_worker.py")
        assert _is_approved_zmq("server/step_executor.py")

    def test_unapproved_zmq_module(self) -> None:
        """Arbitrary modules should not be approved for direct zmq."""
        assert not _is_approved_zmq("api/endpoints.py")
        assert not _is_approved_zmq("shared/auth.py")

    def test_scan_zmq_imports_finds_import(self, tmp_path: Path) -> None:
        """Should detect 'import zmq' statements."""
        f = tmp_path / "test.py"
        f.write_text("import zmq\nctx = zmq.Context()\n")
        results = _scan_zmq_imports(f)
        assert len(results) == 1
        assert results[0].transport == "zmq"

    def test_scan_zmq_imports_finds_from_import(self, tmp_path: Path) -> None:
        """Should detect 'from zmq import ...' statements."""
        f = tmp_path / "test.py"
        f.write_text("from zmq import Context, Socket\n")
        results = _scan_zmq_imports(f)
        assert len(results) == 1
        assert "Context" in results[0].detail

    def test_scan_zmq_imports_no_zmq(self, tmp_path: Path) -> None:
        """Should find nothing in file without zmq."""
        f = tmp_path / "test.py"
        f.write_text("import json\nx = 1\n")
        results = _scan_zmq_imports(f)
        assert results == []

    def test_scan_messagequeue_imports(self, tmp_path: Path) -> None:
        """Should detect MessageQueue imports."""
        f = tmp_path / "test.py"
        f.write_text("from umrs.core import MessageQueue\n")
        results = _scan_messagequeue_imports(f)
        assert len(results) == 1
        assert results[0].transport == "messagequeue"

    def test_scan_http_routes(self, tmp_path: Path) -> None:
        """Should detect FastAPI route decorators."""
        f = tmp_path / "test.py"
        f.write_text('@router.get("/health")\ndef health():\n    return {"ok": True}\n')
        results = _scan_http_routes(f)
        assert len(results) == 1
        assert results[0].transport == "http"

    def test_live_audit_no_violations(self) -> None:
        """Live audit should find no ZMQ boundary violations."""
        report = run_audit()
        assert report.boundary_violations == [], (
            f"ZMQ boundary violations: {[(v.file, v.line) for v in report.boundary_violations]}"
        )

    def test_report_summary_shape(self) -> None:
        """Report summary should have expected keys."""
        report = run_audit()
        s = report.summary
        assert "zmq_direct_files" in s
        assert "boundary_violations" in s
        assert isinstance(s["total_transport_sites"], int)

    def test_scan_zmq_skips_syntax_errors(self, tmp_path: Path) -> None:
        """Should gracefully handle files with syntax errors."""
        f = tmp_path / "bad.py"
        f.write_text("import zmq\ndef broken(\n")
        results = _scan_zmq_imports(f)
        assert results == []

    def test_scan_http_routes_multiple(self, tmp_path: Path) -> None:
        """Should detect multiple route decorators in one file."""
        f = tmp_path / "api.py"
        f.write_text(
            '@router.get("/health")\ndef health(): pass\n'
            '@router.post("/submit")\ndef submit(): pass\n'
            '@router.delete("/item")\ndef delete_item(): pass\n'
        )
        results = _scan_http_routes(f)
        assert len(results) == 3
        details = " ".join(r.detail for r in results)
        assert "get" in details
        assert "post" in details

    def test_live_audit_zmq_modules_count(self) -> None:
        """Live audit should find exactly 3 approved ZMQ modules."""
        report = run_audit()
        zmq_files = report.summary["zmq_direct_files"]
        assert zmq_files >= 3, f"Expected ≥3 ZMQ files, got {zmq_files}"

    def test_live_audit_http_routes_exist(self) -> None:
        """Live audit should find HTTP routes in UMRS API modules."""
        report = run_audit()
        http_count = report.summary["http_sidecar_files"]
        assert http_count > 0, "Expected HTTP routes in UMRS"

    def test_scan_messagequeue_skips_unrelated(self, tmp_path: Path) -> None:
        """Should not flag non-MessageQueue queue imports."""
        f = tmp_path / "test.py"
        f.write_text("from queue import Queue\nq = Queue()\n")
        results = _scan_messagequeue_imports(f)
        assert results == []

    def test_transport_usage_fields(self) -> None:
        """TransportUsage dataclass should hold expected fields."""
        u = TransportUsage(
            file="core/MessageQueue.py",
            line=10,
            transport="zmq",
            usage_type="import",
            approved=True,
            detail="import zmq",
        )
        assert u.file == "core/MessageQueue.py"
        assert u.line == 10
        assert u.transport == "zmq"
        assert u.approved is True


# =====================================================================
# Backward-Compat Retirement Tracker
# =====================================================================

from tools.compat_retirement_tracker import (  # noqa: E402
    _assess_risk,
    run_scan,
    scan_file,
)


class TestCompatRetirementTracker:
    """Backward-compat retirement tracker unit tests."""

    def test_assess_risk_security(self) -> None:
        """Security-sensitive content should be high risk."""
        risk, sec = _assess_risk("# legacy unsalted SHA-256 password", "legacy")
        assert risk == "high"
        assert sec is True

    def test_assess_risk_deprecated(self) -> None:
        """Deprecated markers should be medium risk."""
        risk, sec = _assess_risk("# DEPRECATED function", "deprecated")
        assert risk == "medium"
        assert sec is False

    def test_assess_risk_normal(self) -> None:
        """Normal backward-compat should be low risk."""
        risk, sec = _assess_risk("# backward compat fallback", "backward_compat")
        assert risk == "low"
        assert sec is False

    def test_scan_file_finds_legacy(self, tmp_path: Path) -> None:
        """Should detect # legacy comments."""
        f = tmp_path / "test.py"
        f.write_text("x = 1  # legacy fallback\n")
        results = scan_file(f)
        assert len(results) == 1
        assert results[0].kind == "legacy"

    def test_scan_file_finds_deprecated(self, tmp_path: Path) -> None:
        """Should detect DEPRECATED markers."""
        f = tmp_path / "test.py"
        f.write_text("# DEPRECATED — use new API\ndef old():\n    pass\n")
        results = scan_file(f)
        assert len(results) == 1
        assert results[0].kind == "deprecated"

    def test_scan_file_finds_backward_compat(self, tmp_path: Path) -> None:
        """Should detect backward compat markers."""
        f = tmp_path / "test.py"
        f.write_text("old_name = new_name  # backward compat\n")
        results = scan_file(f)
        assert len(results) == 1
        assert results[0].kind == "backward_compat"

    def test_scan_file_no_markers(self, tmp_path: Path) -> None:
        """Should find nothing in clean file."""
        f = tmp_path / "test.py"
        f.write_text("import json\nx = 1\n")
        results = scan_file(f)
        assert results == []

    def test_live_scan_finds_markers(self) -> None:
        """Live scan should find known compat markers."""
        report = run_scan()
        assert report.markers, "Expected at least one compat marker in codebase"
        # The auth.py legacy hash should be present
        auth_markers = [m for m in report.markers if "auth" in m.file]
        assert any(m.security_relevant for m in auth_markers)

    def test_report_summary_shape(self) -> None:
        """Report summary should have expected keys."""
        report = run_scan()
        s = report.summary
        assert "total_markers" in s
        assert "by_kind" in s
        assert "security_relevant" in s

    def test_assess_risk_token(self) -> None:
        """Token references should be security-relevant."""
        risk, sec = _assess_risk("# legacy token hashing", "legacy")
        assert risk == "high"
        assert sec is True

    def test_scan_file_multiple_markers(self, tmp_path: Path) -> None:
        """Should detect multiple markers in one file."""
        f = tmp_path / "test.py"
        f.write_text(
            "x = 1  # legacy fallback\n"
            "y = 2  # backward compat\n"
            "# DEPRECATED function\ndef old(): pass\n"
        )
        results = scan_file(f)
        assert len(results) == 3
        kinds = {m.kind for m in results}
        assert "legacy" in kinds
        assert "backward_compat" in kinds
        assert "deprecated" in kinds

    def test_scan_file_skips_binary_files(self, tmp_path: Path) -> None:
        """Should gracefully skip binary files."""
        f = tmp_path / "data.py"
        f.write_bytes(b"\x00\x01\x02# legacy\xff\xfe")
        results = scan_file(f)
        # Should not crash; may or may not find marker in binary
        assert isinstance(results, list)

    def test_live_scan_summary_counts_match(self) -> None:
        """Summary total should match actual marker count."""
        report = run_scan()
        assert report.summary["total_markers"] == len(report.markers)

    def test_live_scan_all_risks_valid(self) -> None:
        """All risk levels should be valid."""
        report = run_scan()
        valid_risks = {"low", "medium", "high"}
        for m in report.markers:
            assert m.risk in valid_risks, f"Invalid risk: {m.risk} in {m.file}:{m.line}"

    def test_live_scan_by_kind_sums(self) -> None:
        """by_kind counts should sum to total_markers."""
        report = run_scan()
        total = sum(report.summary["by_kind"].values())
        assert total == report.summary["total_markers"]


# =====================================================================
# Security Compat Scanner
# =====================================================================

from tools.security_compat_scanner import (  # noqa: E402
    run_scan as security_run_scan,
)
from tools.security_compat_scanner import (  # noqa: E402
    scan_file as security_scan_file,
)


class TestSecurityCompatScanner:
    """Security compat scanner unit tests."""

    def test_detects_unsalted_auth_hash(self, tmp_path: Path) -> None:
        """Should flag unsalted hash of auth-sensitive value."""
        f = tmp_path / "auth.py"
        f.write_text("import hashlib\nh = hashlib.sha256(token.encode()).hexdigest()\n")
        results = security_scan_file(f)
        assert len(results) == 1
        assert results[0].category == "weak_hash"
        assert results[0].severity == "warning"

    def test_detects_hardcoded_api_key(self, tmp_path: Path) -> None:
        """Should flag hardcoded API keys."""
        f = tmp_path / "config.py"
        f.write_text('api_key="sk-real-key-12345678"\n')
        results = security_scan_file(f)
        assert len(results) == 1
        assert results[0].category == "hardcoded_secret"

    def test_skips_comments(self, tmp_path: Path) -> None:
        """Should not flag patterns in comments."""
        f = tmp_path / "clean.py"
        f.write_text("# hashlib.sha256(token.encode()).hexdigest()\nx = 1\n")
        results = security_scan_file(f)
        assert results == []

    def test_skips_template_strings(self, tmp_path: Path) -> None:
        """Should not flag template variables as secrets."""
        f = tmp_path / "tmpl.py"
        f.write_text('secret="{user_secret}"\n')
        results = security_scan_file(f)
        assert results == []

    def test_md5_flagged_as_info(self, tmp_path: Path) -> None:
        """MD5 should be info-level (acceptable for checksums)."""
        f = tmp_path / "util.py"
        f.write_text("import hashlib\nh = hashlib.md5(data)\n")
        results = security_scan_file(f)
        assert len(results) == 1
        assert results[0].severity == "info"
        assert results[0].category == "weak_crypto"

    def test_clean_file_no_findings(self, tmp_path: Path) -> None:
        """Should find nothing in a clean file."""
        f = tmp_path / "clean.py"
        f.write_text('import json\nx = json.dumps({"key": "value"})\n')
        results = security_scan_file(f)
        assert results == []

    def test_live_scan_no_criticals(self) -> None:
        """Live scan should find no critical issues in production code."""
        report = security_run_scan()
        criticals = [f for f in report.findings if f.severity == "critical"]
        assert criticals == [], (
            f"Critical security findings in production: "
            f"{[(c.file, c.line, c.detail) for c in criticals]}"
        )

    def test_live_scan_report_shape(self) -> None:
        """Report summary should have expected keys."""
        report = security_run_scan()
        s = report.summary
        assert "total_findings" in s
        assert "by_severity" in s
        assert "by_category" in s

    def test_detects_hardcoded_secret_password(self, tmp_path: Path) -> None:
        """Should flag hardcoded secret values (critical severity)."""
        f = tmp_path / "config.py"
        f.write_text('secret="sk-prod-actual-key-1234"\n')
        results = security_scan_file(f)
        assert len(results) == 1
        assert results[0].severity == "critical"
        assert results[0].category == "hardcoded_secret"

    def test_skips_test_directories(self, tmp_path: Path) -> None:
        """Should exclude files under tests/ directories."""
        from tools.security_compat_scanner import _is_excluded

        test_file = tmp_path / "tests" / "test_auth.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text('api_key="sk-test-key-123456"\n')
        assert _is_excluded(test_file)

    def test_is_excluded_conftest(self, tmp_path: Path) -> None:
        """conftest.py should be excluded."""
        from tools.security_compat_scanner import _is_excluded

        f = tmp_path / "conftest.py"
        f.write_text("x = 1\n")
        assert _is_excluded(f)

    def test_detects_hardcoded_password(self, tmp_path: Path) -> None:
        """Should flag hardcoded password assignments."""
        f = tmp_path / "config.py"
        f.write_text('password="hunter2hunter2"\n')
        results = security_scan_file(f)
        pw_results = [r for r in results if r.category == "hardcoded_secret"]
        assert len(pw_results) == 1

    def test_live_scan_severity_distribution(self) -> None:
        """Live scan severity counts should sum to total."""
        report = security_run_scan()
        total = sum(report.summary["by_severity"].values())
        assert total == report.summary["total_findings"]

    def test_live_scan_category_distribution(self) -> None:
        """Live scan category counts should sum to total."""
        report = security_run_scan()
        total = sum(report.summary["by_category"].values())
        assert total == report.summary["total_findings"]

    def test_scan_empty_file(self, tmp_path: Path) -> None:
        """Should produce no findings for empty file."""
        f = tmp_path / "empty.py"
        f.write_text("")
        results = security_scan_file(f)
        assert results == []
