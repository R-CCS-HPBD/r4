"""CI gate: UMRS module schema versions match the canonical registry."""

from pathlib import Path

from tools.schema_version_tracker import (
    SchemaVersionFinding,
    _extract_version_constant,
    _load_registry,
    validate_schema_versions,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_schema_versions_match_registry() -> None:
    """Fail if code-declared schema versions differ from the registry."""
    findings = validate_schema_versions(REPO_ROOT)
    if findings:
        details = "\n".join(f.format() for f in findings)
        raise AssertionError(
            f"Schema version mismatches:\n{details}\n\n"
            "If you changed a schema, bump the version constant in "
            "the module's schema.py AND run:\n"
            "  python tools/schema_version_tracker.py --update"
        )


def test_extract_version_constant(tmp_path: Path) -> None:
    """Should extract integer version constant from a Python file."""
    schema_py = tmp_path / "schema.py"
    schema_py.write_text("SCHEMA_VERSION = 5\n")
    assert _extract_version_constant(schema_py, "SCHEMA_VERSION") == 5


def test_extract_version_constant_missing_file(tmp_path: Path) -> None:
    """Should return None for missing file."""
    result = _extract_version_constant(tmp_path / "nonexistent.py", "FOO")
    assert result is None


def test_extract_version_constant_missing_constant(tmp_path: Path) -> None:
    """Should return None when constant not found."""
    schema_py = tmp_path / "schema.py"
    schema_py.write_text("OTHER_CONSTANT = 3\n")
    assert _extract_version_constant(schema_py, "SCHEMA_VERSION") is None


def test_extract_version_constant_syntax_error(tmp_path: Path) -> None:
    """Should return None on syntax error."""
    bad_py = tmp_path / "bad.py"
    bad_py.write_text("def incomplete(\n")
    assert _extract_version_constant(bad_py, "SCHEMA_VERSION") is None


def test_load_registry_simple(tmp_path: Path) -> None:
    """Should parse simple key: value YAML."""
    registry = tmp_path / "tools" / "schema_versions.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("# Comment\njobs: 3\nmcp: 1\n")
    result = _load_registry(tmp_path)
    assert result == {"jobs": 3, "mcp": 1}


def test_load_registry_missing(tmp_path: Path) -> None:
    """Should return empty dict for missing registry."""
    result = _load_registry(tmp_path)
    assert result == {}


def test_validates_mismatch(tmp_path: Path) -> None:
    """Should detect version mismatch between registry and code."""
    # Set up registry
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "schema_versions.yaml").write_text("jobs: 2\n")
    # Set up schema file with different version
    schema_dir = tmp_path / "umrs" / "src" / "umrs" / "jobs"
    schema_dir.mkdir(parents=True)
    (schema_dir / "schema.py").write_text("SCHEMA_VERSION = 3\n")
    findings = validate_schema_versions(tmp_path)
    job_findings = [f for f in findings if f.module == "jobs"]
    assert len(job_findings) == 1
    assert job_findings[0].expected == 2
    assert job_findings[0].actual == 3


def test_finding_format() -> None:
    """SchemaVersionFinding.format() produces readable output."""
    f = SchemaVersionFinding(module="jobs", expected=2, actual=3, message="mismatch")
    formatted = f.format()
    assert "jobs" in formatted
    assert "2" in formatted
    assert "3" in formatted


def test_validates_untracked_module(tmp_path: Path) -> None:
    """Should flag modules present in code but missing from registry."""
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    # Registry is empty — no modules tracked
    (tools_dir / "schema_versions.yaml").write_text("# empty\n")
    # But code declares a version
    schema_dir = tmp_path / "umrs" / "src" / "umrs" / "jobs"
    schema_dir.mkdir(parents=True)
    (schema_dir / "schema.py").write_text("SCHEMA_VERSION = 1\n")
    findings = validate_schema_versions(tmp_path)
    job_findings = [f for f in findings if f.module == "jobs"]
    assert len(job_findings) == 1
    assert "not tracked" in job_findings[0].message
