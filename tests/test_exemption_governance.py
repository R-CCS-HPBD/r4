"""Tests for exemption governance validator."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tools.exemption_governance_validator import (
    ExemptionEntry,
    _check_duplicates,
    _check_expired,
    _check_missing_expiry,
    _check_missing_rationale,
    _check_stale,
    _extract_import_target,
    _parse_exemption_metadata,
    audit_exemptions,
)


class TestMetadataParsing:
    def test_empty_comment(self):
        assert _parse_exemption_metadata("") == {}

    def test_reason_only(self):
        result = _parse_exemption_metadata("reason=lazy composition")
        assert result == {"reason": "lazy composition"}

    def test_full_metadata(self):
        result = _parse_exemption_metadata(
            "reason=deferred binding owner=jobs expires=2026-06 ticket=U-999"
        )
        assert result == {
            "reason": "deferred binding",
            "owner": "jobs",
            "expires": "2026-06",
            "ticket": "U-999",
        }

    def test_partial_metadata(self):
        result = _parse_exemption_metadata("reason=lazy ticket=U-100")
        assert result == {"reason": "lazy", "ticket": "U-100"}


class TestExtractImportTarget:
    def test_from_import(self):
        assert (
            _extract_import_target(
                "from umrs.server.sql import store_embeddings_in_db  # noqa: boundary"
            )
            == "umrs.server.sql"
        )

    def test_plain_import(self):
        assert (
            _extract_import_target("import umrs.jobs.store  # noqa: boundary") == "umrs.jobs.store"
        )

    def test_no_import(self):
        assert _extract_import_target("# noqa: boundary") == "<unknown>"


class TestMissingRationale:
    def test_flags_missing_reason(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
        )
        findings = _check_missing_rationale([entry])
        assert len(findings) == 1
        assert findings[0].check == "missing_rationale"

    def test_accepts_reason_metadata(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="lazy composition",
            reason="lazy composition",
        )
        findings = _check_missing_rationale([entry])
        assert len(findings) == 0

    def test_accepts_raw_comment(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="deferred binding for optional dep",
        )
        findings = _check_missing_rationale([entry])
        assert len(findings) == 0


class TestMissingExpiry:
    def test_flags_no_expiry_or_ticket(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
        )
        findings = _check_missing_expiry([entry])
        assert len(findings) == 1
        assert findings[0].check == "missing_expiry"

    def test_accepts_expires(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
            expires="2026-06",
        )
        findings = _check_missing_expiry([entry])
        assert len(findings) == 0

    def test_accepts_ticket(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
            ticket="U-123",
        )
        findings = _check_missing_expiry([entry])
        assert len(findings) == 0


class TestExpired:
    def test_flags_expired(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
            expires="2025-01",
        )
        now = datetime(2026, 5, 13)
        findings = _check_expired([entry], now=now)
        assert len(findings) == 1
        assert findings[0].check == "expired_exemption"
        assert findings[0].severity == "error"

    def test_not_expired(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
            expires="2027-01",
        )
        now = datetime(2026, 5, 13)
        findings = _check_expired([entry], now=now)
        assert len(findings) == 0

    def test_invalid_format(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
            expires="soon",
        )
        findings = _check_expired([entry])
        assert len(findings) == 1
        assert findings[0].check == "invalid_expiry_format"


class TestDuplicates:
    def test_flags_duplicates(self):
        entries = [
            ExemptionEntry(
                file="test.py",
                line=10,
                kind="noqa_boundary",
                import_target="foo.bar",
                raw_comment="",
            ),
            ExemptionEntry(
                file="test.py",
                line=20,
                kind="noqa_boundary",
                import_target="foo.bar",
                raw_comment="",
            ),
        ]
        findings = _check_duplicates(entries)
        assert len(findings) == 1
        assert findings[0].check == "duplicate_exemption"

    def test_no_duplicate_different_files(self):
        entries = [
            ExemptionEntry(
                file="a.py",
                line=10,
                kind="noqa_boundary",
                import_target="foo.bar",
                raw_comment="",
            ),
            ExemptionEntry(
                file="b.py",
                line=10,
                kind="noqa_boundary",
                import_target="foo.bar",
                raw_comment="",
            ),
        ]
        findings = _check_duplicates(entries)
        assert len(findings) == 0


class TestStaleExemptions:
    """Check 8: exemptions with old ``added=`` and no expiry/ticket."""

    def test_flags_stale_exemption(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="reason=old",
            added="2025-06-01",
        )
        now = datetime(2026, 5, 13)
        findings = _check_stale([entry], now=now)
        assert len(findings) == 1
        assert findings[0].check == "stale_exemption"
        assert findings[0].severity == "warning"
        assert "346 days" in findings[0].message

    def test_not_stale_within_threshold(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
            added="2026-03-01",
        )
        now = datetime(2026, 5, 13)
        findings = _check_stale([entry], now=now)
        assert len(findings) == 0

    def test_skips_when_expires_set(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
            added="2024-01-01",
            expires="2027-01",
        )
        now = datetime(2026, 5, 13)
        findings = _check_stale([entry], now=now)
        assert len(findings) == 0

    def test_skips_when_ticket_set(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
            added="2024-01-01",
            ticket="U-123",
        )
        now = datetime(2026, 5, 13)
        findings = _check_stale([entry], now=now)
        assert len(findings) == 0

    def test_skips_without_added_date(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
        )
        findings = _check_stale([entry])
        assert len(findings) == 0

    def test_custom_threshold(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
            added="2026-04-01",
        )
        now = datetime(2026, 5, 13)
        # 42 days old, threshold is 30
        findings = _check_stale([entry], now=now, stale_age_days=30)
        assert len(findings) == 1
        # Same entry, threshold is 90 — not stale
        findings = _check_stale([entry], now=now, stale_age_days=90)
        assert len(findings) == 0

    def test_yyyy_mm_format(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
            added="2025-01",
        )
        now = datetime(2026, 5, 13)
        findings = _check_stale([entry], now=now)
        assert len(findings) == 1
        assert findings[0].check == "stale_exemption"

    def test_invalid_added_format_skipped(self):
        entry = ExemptionEntry(
            file="test.py",
            line=1,
            kind="noqa_boundary",
            import_target="foo",
            raw_comment="",
            added="recently",
        )
        findings = _check_stale([entry])
        assert len(findings) == 0


class TestLiveRepository:
    """Test against the actual repository state."""

    def test_no_expired_exemptions(self):
        """No expired exemptions should exist in the repo."""
        repo_root = Path(__file__).resolve().parents[1]
        report = audit_exemptions(repo_root)
        expired = [f for f in report.findings if f.check == "expired_exemption"]
        assert expired == [], f"Expired exemptions found: {expired}"

    def test_no_orphaned_exemptions(self):
        """All exempted files should exist."""
        repo_root = Path(__file__).resolve().parents[1]
        report = audit_exemptions(repo_root)
        orphaned = [f for f in report.findings if f.check == "orphaned_file"]
        assert orphaned == [], f"Orphaned exemptions found: {orphaned}"

    def test_no_unnecessary_exemptions(self):
        """All exemptions should still be needed by a boundary rule."""
        repo_root = Path(__file__).resolve().parents[1]
        report = audit_exemptions(repo_root)
        unnecessary = [f for f in report.findings if f.check == "unnecessary_exemption"]
        assert unnecessary == [], f"Unnecessary exemptions found: {unnecessary}"

    def test_exemption_inventory_is_stable(self):
        """Sanity check: we know the current exemption count."""
        repo_root = Path(__file__).resolve().parents[1]
        report = audit_exemptions(repo_root)
        # Currently: 3 noqa:boundary + 1 allow_lazy = 4 total
        assert report.summary["total_exemptions"] >= 1, "Expected at least 1 exemption"
        assert report.summary["noqa_boundary"] >= 1, "Expected at least 1 noqa:boundary"


class TestNoqaC901Scan:
    """Round-5 T4: ``noqa: C901`` joins governance scanning."""

    def test_boundary_and_c901_kinds_recognized(self):
        line = "    def _chat(self):  # noqa: C901 — reason=pre-existing debt"
        from tools.exemption_governance_validator import _extract_def_name

        assert _extract_def_name(line) == "_chat"

    def test_c901_entry_imports_function_name(self, tmp_path):
        from tools.exemption_governance_validator import _NOQA_RE

        _ = tmp_path  # fixture used in pass (lint gate ARG002)
        m = _NOQA_RE.search("def _chat(self):  # noqa: C901 — owner=x")
        assert m is not None
        assert m.group(1) == "C901"

    def test_governance_flags_missing_rationale(self):
        from tools.exemption_governance_validator import (
            ExemptionEntry,
            _check_missing_rationale,
        )

        findings = _check_missing_rationale(
            [
                ExemptionEntry(
                    file="r4/src/r4/companion/tools.py",
                    line=10,
                    kind="noqa_c901",
                    import_target="_client_lab_tools",
                    raw_comment="",
                    reason="",
                ),
                ExemptionEntry(
                    file="r4/src/r4/config.py",
                    line=350,
                    kind="noqa_c901",
                    import_target="from_env",
                    raw_comment="reason=pre-existing",
                    reason="pre-existing",
                ),
            ]
        )
        assert len(findings) == 1
        assert findings[0].exemption_kind == "noqa_c901"


class TestRemovedTargetsSkipsC901:
    def test_c901_function_targets_never_flagged(self):
        """Function-level C901 exemptions name defs in the same file — they
        can't be stale module imports. Steady 8-row false-positive class
        removed by the WP-13 r2 sweep."""
        from tools.exemption_governance_validator import (
            ExemptionEntry,
            _check_removed_targets,
        )

        findings = _check_removed_targets(
            [
                ExemptionEntry(
                    file="r4/src/r4/companion/tools.py",
                    line=1272,
                    kind="noqa_c901",
                    import_target="_client_lab_tools",
                    raw_comment="",
                )
            ],
            __import__("pathlib").Path("."),
        )
        assert findings == []

    def test_boundary_module_targets_still_checked(self):
        from tools.exemption_governance_validator import (
            ExemptionEntry,
            _check_removed_targets,
        )

        findings = _check_removed_targets(
            [
                ExemptionEntry(
                    file="r4/src/r4/foo.py",
                    line=10,
                    kind="noqa_boundary",
                    import_target="module.definitely.nowhere.real",
                    raw_comment="",
                )
            ],
            __import__("pathlib").Path("."),
        )
        assert len(findings) == 1
        assert findings[0].check == "removed_target"


class TestAllowLazyMetadata:
    def test_example_format_comment_parses_kv(self, tmp_path):
        """allow_lazy rule lines carry kv tokens (reason/owner/expires)."""

        from tools.exemption_governance_validator import _scan_rule_exemptions

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        va = tools_dir / "import_boundary_validator.py"
        va.write_text(
            "rules = {\n"
            '    "r4": ImportBoundaryRule(\n'
            '        root="r4/src",\n'
            '        forbidden_subpackages=("umrs.api",),\n'
            "        allow_lazy=True,  # reason=lazy owner=d expires=2099-12\n"
            "    ),\n"
            "}\n"
        )
        entries = _scan_rule_exemptions(tmp_path)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.kind == "allow_lazy"
        assert entry.import_target == "r4/src"
        assert entry.expires == "2099-12"
        assert entry.reason
