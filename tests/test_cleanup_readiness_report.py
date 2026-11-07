"""Tests for cleanup readiness report."""

from __future__ import annotations

from pathlib import Path

from tools.cleanup_readiness_report import (
    CleanupCandidate,
    CleanupReport,
    _collect_bridge_candidates,
    _collect_exemption_candidates,
    _collect_orphan_candidates,
    _collect_stale_references,
    _format_human,
    _format_json,
    _format_markdown,
    generate_report,
)


class TestCleanupCandidateModel:
    def test_candidate_fields(self):
        c = CleanupCandidate(
            target="umrs/src/umrs/dead.py",
            category="orphan_module",
            evidence_source="orphan_module_detector",
            justification="No inbound imports",
            rollback_simplicity="trivial",
            risk="low",
            dependency_visibility="clear",
            confidence="high",
        )
        assert c.target == "umrs/src/umrs/dead.py"
        assert c.category == "orphan_module"
        assert c.confidence == "high"

    def test_report_summary_empty(self):
        report = CleanupReport()
        assert report.summary == {}
        assert report.candidates == []


class TestCollectOrphanCandidates:
    def test_collects_from_detector(self, tmp_path):
        # Create a minimal orphan
        mod = tmp_path / "umrs" / "src" / "umrs" / "orphan_mod.py"
        mod.parent.mkdir(parents=True)
        mod.write_text("# never imported\n" * 20)
        # Also need tools dir with the detector importable
        # (the function imports dynamically from repo_root/tools)
        # Use actual repo root for integration test
        repo_root = Path(__file__).resolve().parents[1]
        candidates = _collect_orphan_candidates(repo_root)
        # Expect at least 0 candidates (depends on repo state)
        assert isinstance(candidates, list)
        for c in candidates:
            assert c.category == "orphan_module"
            assert c.evidence_source == "orphan_module_detector"

    def test_returns_empty_for_missing_tools(self, tmp_path):
        # No tools directory — import will fail gracefully
        candidates = _collect_orphan_candidates(tmp_path)
        assert candidates == []


class TestCollectExemptionCandidates:
    def test_collects_from_validator(self):
        repo_root = Path(__file__).resolve().parents[1]
        candidates = _collect_exemption_candidates(repo_root)
        assert isinstance(candidates, list)
        for c in candidates:
            assert c.category == "stale_exemption"
            assert c.evidence_source == "exemption_governance_validator"

    def test_returns_empty_for_missing_tools(self, tmp_path):
        candidates = _collect_exemption_candidates(tmp_path)
        assert candidates == []


class TestCollectBridgeCandidates:
    def test_collects_from_detector(self):
        repo_root = Path(__file__).resolve().parents[1]
        candidates = _collect_bridge_candidates(repo_root)
        assert isinstance(candidates, list)
        for c in candidates:
            assert c.category == "dead_bridge"
            assert c.confidence in ("high", "medium", "low")

    def test_returns_empty_for_missing_tools(self, tmp_path):
        candidates = _collect_bridge_candidates(tmp_path)
        assert candidates == []


class TestCollectStaleReferences:
    def test_collects_from_validator(self):
        repo_root = Path(__file__).resolve().parents[1]
        candidates = _collect_stale_references(repo_root)
        assert isinstance(candidates, list)
        for c in candidates:
            assert c.category == "stale_reference"
            assert c.evidence_source == "adr_linkage_validator"

    def test_returns_empty_for_missing_tools(self, tmp_path):
        candidates = _collect_stale_references(tmp_path)
        assert candidates == []


class TestGenerateReport:
    def test_produces_valid_report(self):
        repo_root = Path(__file__).resolve().parents[1]
        report = generate_report(repo_root)
        assert "total_candidates" in report.summary
        assert report.summary["total_candidates"] == len(report.candidates)
        assert report.summary["orphan_modules"] >= 0
        assert report.summary["stale_exemptions"] >= 0
        assert report.summary["dead_bridges"] >= 0
        assert report.summary["stale_references"] >= 0

    def test_all_confidences_valid(self):
        repo_root = Path(__file__).resolve().parents[1]
        report = generate_report(repo_root)
        valid = {"high", "medium", "low"}
        for c in report.candidates:
            assert c.confidence in valid

    def test_all_risks_valid(self):
        repo_root = Path(__file__).resolve().parents[1]
        report = generate_report(repo_root)
        valid = {"low", "medium", "high"}
        for c in report.candidates:
            assert c.risk in valid


class TestFormatters:
    def test_human_format_no_candidates(self):
        report = CleanupReport(candidates=[], summary={"total_candidates": 0})
        output = _format_human(report)
        assert "No cleanup candidates found" in output

    def test_human_format_with_candidates(self):
        report = CleanupReport(
            candidates=[
                CleanupCandidate(
                    target="foo.py:10",
                    category="orphan_module",
                    evidence_source="orphan_module_detector",
                    justification="Never imported",
                    rollback_simplicity="trivial",
                    risk="low",
                    dependency_visibility="clear",
                    confidence="high",
                ),
            ],
            summary={"total_candidates": 1},
        )
        output = _format_human(report)
        assert "foo.py:10" in output
        assert "orphan_module" in output

    def test_json_format(self):
        import json

        report = CleanupReport(
            candidates=[
                CleanupCandidate(
                    target="bar.py:5",
                    category="dead_bridge",
                    evidence_source="bridge_detector",
                    justification="Likely dead",
                    rollback_simplicity="complex",
                    risk="medium",
                    dependency_visibility="uncertain",
                    confidence="medium",
                ),
            ],
            summary={"total_candidates": 1},
        )
        output = _format_json(report)
        parsed = json.loads(output)
        assert len(parsed["candidates"]) == 1
        assert parsed["candidates"][0]["target"] == "bar.py:5"
        assert parsed["summary"]["total_candidates"] == 1

    def test_markdown_format(self):
        report = CleanupReport(
            candidates=[
                CleanupCandidate(
                    target="mod.py",
                    category="stale_exemption",
                    evidence_source="exemption_governance_validator",
                    justification="Expired",
                    rollback_simplicity="trivial",
                    risk="low",
                    dependency_visibility="clear",
                    confidence="high",
                ),
            ],
            summary={"total_candidates": 1},
        )
        output = _format_markdown(report)
        assert "# Cleanup Readiness Report" in output
        assert "| `mod.py`" in output
        assert "Stale Exemption" in output
