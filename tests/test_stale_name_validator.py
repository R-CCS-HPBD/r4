"""CI gate: no stale aisci references in build configuration."""

from pathlib import Path

from tools.stale_name_validator import StaleNameFinding, validate_no_stale_names

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_no_stale_package_names() -> None:
    """Fail if Makefile, pyproject.toml, or CI workflows reference 'aisci'."""
    findings = validate_no_stale_names(REPO_ROOT)
    if findings:
        details = "\n".join(f.format() for f in findings)
        raise AssertionError(f"Stale package name references found:\n{details}")


def test_detects_stale_in_makefile(tmp_path: Path) -> None:
    """Should flag aisci references in Makefile."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("build:\n\tpip install aisci\n")
    findings = validate_no_stale_names(tmp_path)
    assert len(findings) == 1
    assert findings[0].path == "Makefile"
    assert findings[0].line == 2


def test_detects_stale_in_pyproject(tmp_path: Path) -> None:
    """Should flag aisci in pyproject.toml."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "aisci"\n')
    findings = validate_no_stale_names(tmp_path)
    assert len(findings) == 1
    assert "aisci" in findings[0].content


def test_passes_clean_files(tmp_path: Path) -> None:
    """No findings when files are clean."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("build:\n\tpip install r4\n")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "r4"\n')
    findings = validate_no_stale_names(tmp_path)
    assert findings == []


def test_skips_allowlisted_docs(tmp_path: Path) -> None:
    """Allowlisted files should not be scanned."""
    # Create the allowlisted file with a stale reference
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "ARCHITECTURE_ARCHAEOLOGY.md").write_text("Historical note: aisci\n")
    findings = validate_no_stale_names(tmp_path)
    assert findings == []


def test_finding_format() -> None:
    """StaleNameFinding.format() produces readable output."""
    f = StaleNameFinding(
        path="Makefile",
        line=5,
        content="  pip install aisci",
        message="stale 'aisci' package name reference",
    )
    formatted = f.format()
    assert "Makefile:5" in formatted
    assert "aisci" in formatted


def test_detects_stale_in_ci_workflow(tmp_path: Path) -> None:
    """Should flag aisci references in CI workflow files."""
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yaml").write_text("steps:\n  - run: pip install aisci\n")
    findings = validate_no_stale_names(tmp_path)
    assert len(findings) == 1
    assert findings[0].path == ".github/workflows/ci.yaml"


def test_case_insensitive_detection(tmp_path: Path) -> None:
    """Should detect case variations of aisci."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("build:\n\tpip install AISCI\n")
    findings = validate_no_stale_names(tmp_path)
    assert len(findings) == 1


def test_skips_self_reference_in_validator(tmp_path: Path) -> None:
    """Lines referencing stale_name_validator should be skipped."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("validate:\n\tpython tools/stale_name_validator.py aisci\n")
    findings = validate_no_stale_names(tmp_path)
    assert findings == []


def test_multiple_findings_in_one_file(tmp_path: Path) -> None:
    """Should report each stale line separately."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("install:\n\tpip install aisci\n\tpip install aisci-dev\n")
    findings = validate_no_stale_names(tmp_path)
    assert len(findings) == 2
    assert findings[0].line == 2
    assert findings[1].line == 3
