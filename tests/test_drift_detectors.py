"""Tests for ADR linkage validator and compatibility bridge detector."""

from __future__ import annotations

import textwrap
from pathlib import Path

from tools.adr_linkage_validator import (
    GOVERNANCE_SCAN_PATHS,
    ADREntry,
    _check_broken_adr_refs,
    _check_missing_scan_paths,
    _reserved_adr_numbers,
    _scan_adr_references,
    _scan_adrs,
    _scan_file_for_adr_refs,
    _scan_validator_references,
    validate_adr_linkage,
)
from tools.bridge_detector import (
    _classify_alias,
    _detect_backward_compat_aliases,
    _detect_deprecated_modules,
    _detect_dto_reexports,
    _detect_gen1_gen2_bridges,
    _detect_legacy_code_paths,
    _detect_transitional_tools,
    detect_bridges,
)


class TestADRLinkageValidator:
    """Tests against the actual repository state."""

    def test_all_adrs_detected(self):
        repo_root = Path(__file__).resolve().parents[1]
        adrs = _scan_adrs(repo_root)
        assert len(adrs) >= 8, f"Expected at least 8 ADRs, found {len(adrs)}"

    def test_all_adrs_have_status(self):
        repo_root = Path(__file__).resolve().parents[1]
        adrs = _scan_adrs(repo_root)
        for adr in adrs:
            assert adr.status != "Unknown", f"ADR-{adr.number} has unknown status"

    def test_no_broken_tool_references(self):
        """Instruction files should not reference tools that don't exist."""
        repo_root = Path(__file__).resolve().parents[1]
        report = validate_adr_linkage(repo_root)
        missing_tools = [f for f in report.findings if f.check == "missing_tool_reference"]
        assert missing_tools == [], f"Missing tool references: {missing_tools}"

    def test_adr_references_found(self):
        """Governance docs should reference ADRs."""
        repo_root = Path(__file__).resolve().parents[1]
        refs = _scan_adr_references(repo_root)
        assert len(refs) > 0, "No ADR references found in governance docs"


class TestADRLinkageValidatorUnit:
    """Isolated unit tests for ADR linkage functions."""

    def test_scan_adrs_from_fixture(self, tmp_path):
        adr_dir = tmp_path / "docs" / "adr"
        adr_dir.mkdir(parents=True)
        (adr_dir / "001-first-decision.md").write_text(
            "# ADR-001\n\n**Status:** Accepted\n\nSome decision."
        )
        (adr_dir / "002-second-decision.md").write_text(
            "# ADR-002\n\n**Status:** Proposed\n\nAnother decision."
        )
        adrs = _scan_adrs(tmp_path)
        assert len(adrs) == 2
        assert adrs[0].number == "001"
        assert adrs[0].status == "Accepted"
        assert adrs[1].number == "002"
        assert adrs[1].status == "Proposed"

    def test_scan_adrs_skips_readme(self, tmp_path):
        adr_dir = tmp_path / "docs" / "adr"
        adr_dir.mkdir(parents=True)
        (adr_dir / "README.md").write_text("# ADR Index")
        (adr_dir / "001-foo.md").write_text("**Status:** Accepted\n")
        adrs = _scan_adrs(tmp_path)
        assert len(adrs) == 1

    def test_scan_file_for_adr_refs(self, tmp_path):
        doc = tmp_path / "docs" / "ARCH.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("See ADR-001 and ADR-003 for details.\n")
        refs = _scan_file_for_adr_refs(doc, tmp_path)
        adr_numbers = [r[2] for r in refs]
        assert "001" in adr_numbers
        assert "003" in adr_numbers

    def test_scan_file_for_adr_file_refs(self, tmp_path):
        doc = tmp_path / "docs" / "guide.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("Refer to [decision](docs/adr/005-the-thing.md)\n")
        refs = _scan_file_for_adr_refs(doc, tmp_path)
        adr_numbers = [r[2] for r in refs]
        assert "005" in adr_numbers

    def test_check_broken_adr_refs(self):
        refs = [("docs/ARCH.md", 10, "001"), ("docs/ARCH.md", 20, "999")]
        adrs = [ADREntry(file="docs/adr/001-foo.md", number="001", title="foo", status="Accepted")]
        findings = _check_broken_adr_refs(refs, adrs)
        assert len(findings) == 1
        assert findings[0].check == "broken_adr_ref"
        assert "999" in findings[0].message

    def test_check_broken_adr_refs_none_broken(self):
        refs = [("docs/ARCH.md", 10, "001")]
        adrs = [ADREntry(file="docs/adr/001-foo.md", number="001", title="foo", status="Accepted")]
        findings = _check_broken_adr_refs(refs, adrs)
        assert findings == []

    def test_reserved_numbers_are_not_broken_refs(self):
        refs = [("docs/plan.md", 3, "091"), ("docs/plan.md", 4, "099")]
        adrs = [ADREntry(file="docs/adr/001-foo.md", number="001", title="foo", status="Accepted")]
        findings = _check_broken_adr_refs(refs, adrs, {"091"})
        assert [f.message for f in findings] == [
            "References ADR-099 but no matching ADR file exists"
        ]

    def test_reserved_numbers_come_from_the_index_section(self, tmp_path):
        index = tmp_path / "docs" / "adr" / "README.md"
        index.parent.mkdir(parents=True)
        index.write_text(
            "# ADRs\n\n- **050**: not a reservation (outside the section)\n\n"
            "### Numbers not allocated\n\n- **038**: reserved for a renumber.\n"
            "- **091**: named by a plan; not yet written.\n\n"
            "## Process\n\n- **095**: next free, not reserved\n"
        )
        assert _reserved_adr_numbers(tmp_path) == {"038", "091"}

    def test_no_index_means_nothing_reserved(self, tmp_path):
        assert _reserved_adr_numbers(tmp_path) == set()

    def test_scan_validator_references(self, tmp_path):
        doc = tmp_path / "docs" / "ENFORCEMENT.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("Run import_boundary_validator and contracts_fence.\n")
        refs = _scan_validator_references(tmp_path)
        assert "tools/import_boundary_validator.py" in refs
        assert "tools/contracts_fence.py" in refs

    def test_validate_empty_repo(self, tmp_path):
        report = validate_adr_linkage(tmp_path)
        assert report.summary["total_adrs"] == 0

    def test_scan_adrs_extracts_title(self, tmp_path):
        """Title should be derived from the ADR filename."""
        adr_dir = tmp_path / "docs" / "adr"
        adr_dir.mkdir(parents=True)
        (adr_dir / "003-use-zmq-transport.md").write_text(
            "# ADR-003\n\n**Status:** Accepted\n\nZMQ is permanent.\n"
        )
        adrs = _scan_adrs(tmp_path)
        assert len(adrs) == 1
        assert "zmq" in adrs[0].title.lower() or "003" in adrs[0].number

    def test_scan_file_ignores_code_blocks(self, tmp_path):
        """ADR references inside code examples should still be found."""
        doc = tmp_path / "docs" / "GUIDE.md"
        doc.parent.mkdir(parents=True)
        doc.write_text("```\nSee ADR-002 for rationale\n```\n")
        refs = _scan_file_for_adr_refs(doc, tmp_path)
        adr_numbers = [r[2] for r in refs]
        assert "002" in adr_numbers

    def test_live_all_adr_refs_are_valid(self):
        """All ADR references in governance docs should point to existing ADRs."""
        repo_root = Path(__file__).resolve().parents[1]
        report = validate_adr_linkage(repo_root)
        broken_refs = [f for f in report.findings if f.check == "broken_adr_ref"]
        assert broken_refs == [], f"Broken ADR references: {broken_refs}"


ROOT_ENTRY_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "CONSTITUTION.md",
    "README.md",
    "R4.md",
    "CONTRIBUTING.md",
)


class TestADRLinkageScanPaths:
    """H-11.8 (2026-10-18): root entry files scanned; missing scan paths reported."""

    def test_root_entry_files_are_configured(self):
        assert set(ROOT_ENTRY_FILES) <= set(GOVERNANCE_SCAN_PATHS)

    def test_broken_ref_in_a_root_entry_file_is_found(self, tmp_path):
        (tmp_path / "docs" / "adr").mkdir(parents=True)
        (tmp_path / "AGENTS.md").write_text("Rule: see ADR-999.\n")
        report = validate_adr_linkage(tmp_path)
        broken = [(f.file, f.check) for f in report.findings if f.check == "broken_adr_ref"]
        assert broken == [("AGENTS.md", "broken_adr_ref")]

    def test_missing_scan_paths_are_errors(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("entry\n")
        findings = _check_missing_scan_paths(tmp_path)
        missing = {f.file for f in findings}
        assert "R4.md" in missing and "docs" in missing
        assert "AGENTS.md" not in missing
        assert ".github/instructions/development.instructions.md" in missing
        assert all(f.severity == "error" for f in findings)
        assert validate_adr_linkage(tmp_path).has_errors()

    def test_live_every_scan_path_exists(self):
        repo_root = Path(__file__).resolve().parents[1]
        assert _check_missing_scan_paths(repo_root) == []


class TestBridgeDetector:
    """Tests against the actual repository state."""

    def test_detector_runs_without_error(self):
        repo_root = Path(__file__).resolve().parents[1]
        report = detect_bridges(repo_root)
        assert report.summary["total_bridges"] > 0

    def test_dto_reexports_detected(self):
        """DTO convergence re-exports should be detected as active bridges."""
        repo_root = Path(__file__).resolve().parents[1]
        report = detect_bridges(repo_root)
        dto_bridges = [b for b in report.bridges if b.kind == "dto_reexport"]
        assert len(dto_bridges) >= 3, (
            f"Expected at least 3 DTO re-exports, found {len(dto_bridges)}"
        )
        for b in dto_bridges:
            assert b.classification == "active"

    def test_gen1_gen2_bridges_detected(self):
        """Gen1/Gen2 bridges should be detected as active (per ADR-007)."""
        repo_root = Path(__file__).resolve().parents[1]
        report = detect_bridges(repo_root)
        gen_bridges = [b for b in report.bridges if b.kind == "gen1_gen2_bridge"]
        assert len(gen_bridges) >= 2
        for b in gen_bridges:
            assert b.classification == "active"

    def test_no_self_reference(self):
        """Bridge detector should not flag itself."""
        repo_root = Path(__file__).resolve().parents[1]
        report = detect_bridges(repo_root)
        self_refs = [b for b in report.bridges if "bridge_detector" in b.file]
        assert self_refs == [], f"Bridge detector flagged itself: {self_refs}"

    def test_all_classifications_valid(self):
        repo_root = Path(__file__).resolve().parents[1]
        report = detect_bridges(repo_root)
        valid = {"active", "likely_dead", "removable", "review"}
        for b in report.bridges:
            assert b.classification in valid, f"Invalid classification: {b.classification}"


class TestBridgeDetectorUnit:
    """Isolated unit tests using synthetic file fixtures."""

    def test_dto_reexport_detection(self, tmp_path):
        """Detects re-export of r4_contracts in UMRS __init__.py."""
        init_py = tmp_path / "umrs" / "src" / "umrs" / "skills" / "__init__.py"
        init_py.parent.mkdir(parents=True)
        init_py.write_text("from r4_contracts.skills import SkillSpec\n")
        entries = _detect_dto_reexports(tmp_path)
        assert len(entries) == 1
        assert entries[0].kind == "dto_reexport"
        assert entries[0].classification == "active"
        assert "Skills" in entries[0].description

    def test_dto_reexport_not_present(self, tmp_path):
        """No match when source is unrelated."""
        init_py = tmp_path / "umrs" / "src" / "umrs" / "skills" / "__init__.py"
        init_py.parent.mkdir(parents=True)
        init_py.write_text("from umrs.skills.engine import run\n")
        entries = _detect_dto_reexports(tmp_path)
        assert entries == []

    def test_gen1_gen2_bridge_detection(self, tmp_path):
        """Detects DurableJobRuntime class as Gen1/Gen2 bridge."""
        bridge_py = tmp_path / "umrs" / "src" / "umrs" / "server" / "durable_job_runtime.py"
        bridge_py.parent.mkdir(parents=True)
        bridge_py.write_text(
            textwrap.dedent("""\
            import asyncio

            class DurableJobRuntime:
                pass
        """)
        )
        entries = _detect_gen1_gen2_bridges(tmp_path)
        assert len(entries) == 1
        assert entries[0].kind == "gen1_gen2_bridge"
        assert entries[0].classification == "active"

    def test_gen1_gen2_bridge_missing_class(self, tmp_path):
        """No match when class is absent."""
        bridge_py = tmp_path / "umrs" / "src" / "umrs" / "server" / "durable_job_runtime.py"
        bridge_py.parent.mkdir(parents=True)
        bridge_py.write_text("# Empty placeholder\n")
        entries = _detect_gen1_gen2_bridges(tmp_path)
        assert entries == []

    def test_deprecated_module_detection(self, tmp_path):
        """Detects known deprecated module when file exists."""
        gw_main = tmp_path / "gateway" / "gateway" / "main.py"
        gw_main.parent.mkdir(parents=True)
        gw_main.write_text("# deprecated gateway\n")
        entries = _detect_deprecated_modules(tmp_path)
        assert len(entries) >= 1
        assert entries[0].kind == "deprecated_module"
        assert entries[0].classification == "review"

    def test_deprecated_module_missing_file(self, tmp_path):
        """No entry when deprecated file doesn't exist."""
        # Create nothing
        entries = _detect_deprecated_modules(tmp_path)
        assert entries == []

    def test_backward_compat_alias_detection(self, tmp_path):
        """Detects aliases marked with backward compat comments."""
        mod = tmp_path / "umrs" / "src" / "umrs" / "compat.py"
        mod.parent.mkdir(parents=True)
        mod.write_text("old_func = new_func  # backward compat alias\n")
        entries = _detect_backward_compat_aliases(tmp_path)
        assert len(entries) == 1
        assert entries[0].kind == "backward_compat_alias"
        assert "old_func" in entries[0].description

    def test_backward_compat_skips_test_files(self, tmp_path):
        """Should not flag aliases in test files."""
        test_f = tmp_path / "umrs" / "src" / "umrs" / "test_compat.py"
        test_f.parent.mkdir(parents=True)
        test_f.write_text("old = new  # backward compat alias\n")
        entries = _detect_backward_compat_aliases(tmp_path)
        assert entries == []

    def test_legacy_code_path_detection(self, tmp_path):
        """Detects lines tagged with #legacy comment."""
        mod = tmp_path / "umrs" / "src" / "umrs" / "server" / "api.py"
        mod.parent.mkdir(parents=True)
        mod.write_text(
            textwrap.dedent("""\
            def handle():
                pass  # legacy code path
        """)
        )
        entries = _detect_legacy_code_paths(tmp_path)
        assert len(entries) == 1
        assert entries[0].kind == "legacy_code_path"
        assert entries[0].classification == "review"

    def test_transitional_tool_detection(self, tmp_path):
        """Detects tools marked as TRANSITIONAL."""
        tool_f = tmp_path / "tools" / "migrate_data.py"
        tool_f.parent.mkdir(parents=True)
        tool_f.write_text("# TRANSITIONAL: remove after migration complete\n")
        entries = _detect_transitional_tools(tmp_path)
        assert len(entries) == 1
        assert entries[0].kind == "transitional_tool"

    def test_classify_alias_no_usage(self, tmp_path):
        """Alias with zero usage is likely_dead."""
        src = tmp_path / "umrs" / "src" / "foo.py"
        src.parent.mkdir(parents=True)
        src.write_text("alias = real_fn  # backward compat\n")
        classification = _classify_alias("unique_alias_name_xyz", src, tmp_path)
        assert classification == "likely_dead"

    def test_summary_counts(self, tmp_path):
        """Report summary counts classifications correctly."""
        report = detect_bridges(tmp_path)
        s = report.summary
        assert s["total_bridges"] == 0
        assert s["active"] == 0
        assert s["likely_dead"] == 0
        assert s["removable"] == 0
        assert s["review"] == 0
