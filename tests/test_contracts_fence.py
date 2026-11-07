"""CI gate: r4-contracts must declare zero runtime dependencies."""

from pathlib import Path

from tools.contracts_fence import validate_contracts_zero_deps

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_contracts_zero_deps() -> None:
    violations = validate_contracts_zero_deps(REPO_ROOT)
    assert not violations, "\n".join(violations)


def test_detects_dependency_violation(tmp_path: Path) -> None:
    """Should flag when pyproject.toml has dependencies."""
    contracts_dir = tmp_path / "r4-contracts"
    contracts_dir.mkdir()
    pyproject = contracts_dir / "pyproject.toml"
    pyproject.write_text('[project]\nname = "r4-contracts"\ndependencies = ["requests"]\n')
    violations = validate_contracts_zero_deps(tmp_path)
    assert len(violations) == 1
    assert "requests" in violations[0]


def test_passes_with_empty_deps(tmp_path: Path) -> None:
    """Should pass when dependencies list is empty."""
    contracts_dir = tmp_path / "r4-contracts"
    contracts_dir.mkdir()
    pyproject = contracts_dir / "pyproject.toml"
    pyproject.write_text('[project]\nname = "r4-contracts"\ndependencies = []\n')
    violations = validate_contracts_zero_deps(tmp_path)
    assert violations == []


def test_passes_with_no_deps_key(tmp_path: Path) -> None:
    """Should pass when dependencies key is absent."""
    contracts_dir = tmp_path / "r4-contracts"
    contracts_dir.mkdir()
    pyproject = contracts_dir / "pyproject.toml"
    pyproject.write_text('[project]\nname = "r4-contracts"\n')
    violations = validate_contracts_zero_deps(tmp_path)
    assert violations == []


def test_missing_pyproject(tmp_path: Path) -> None:
    """Should report error when pyproject.toml is missing."""
    violations = validate_contracts_zero_deps(tmp_path)
    assert len(violations) == 1
    assert "not found" in violations[0]


def test_detects_multiple_dependencies(tmp_path: Path) -> None:
    """Should list all dependencies when multiple are declared."""
    contracts_dir = tmp_path / "r4-contracts"
    contracts_dir.mkdir()
    pyproject = contracts_dir / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "r4-contracts"\ndependencies = ["requests", "pydantic"]\n'
    )
    violations = validate_contracts_zero_deps(tmp_path)
    assert len(violations) == 1
    assert "requests" in violations[0]
    assert "pydantic" in violations[0]
    assert "2" in violations[0]


def test_optional_deps_not_flagged(tmp_path: Path) -> None:
    """Optional dependencies should not cause violations."""
    contracts_dir = tmp_path / "r4-contracts"
    contracts_dir.mkdir()
    pyproject = contracts_dir / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "r4-contracts"\ndependencies = []\n'
        '\n[project.optional-dependencies]\ndev = ["pytest"]\n'
    )
    violations = validate_contracts_zero_deps(tmp_path)
    assert violations == []


def test_real_contracts_pyproject_exists() -> None:
    """r4-contracts/pyproject.toml should exist in the repository."""
    pyproject = REPO_ROOT / "r4-contracts" / "pyproject.toml"
    assert pyproject.exists(), "r4-contracts/pyproject.toml is missing"


def test_real_contracts_has_project_table() -> None:
    """r4-contracts pyproject.toml should have [project] table."""
    pyproject = REPO_ROOT / "r4-contracts" / "pyproject.toml"
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]
    with open(pyproject, "rb") as f:
        cfg = tomllib.load(f)
    assert "project" in cfg, "pyproject.toml missing [project] table"
    assert cfg["project"].get("name") == "r4-contracts"


def test_violation_message_includes_dep_count(tmp_path: Path) -> None:
    """Violation message should include the count of dependencies found."""
    contracts_dir = tmp_path / "r4-contracts"
    contracts_dir.mkdir()
    pyproject = contracts_dir / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "r4-contracts"\ndependencies = ["httpx", "pydantic", "numpy"]\n'
    )
    violations = validate_contracts_zero_deps(tmp_path)
    assert len(violations) == 1
    assert "3" in violations[0]
