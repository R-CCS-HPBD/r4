"""CI gate: r4-contracts and UMRS DTO mirrors remain structurally equivalent.

This is a TRANSITIONAL test.  Remove after convergence Step 5 is complete
(UMRS imports DTOs directly from r4-contracts).
"""

from pathlib import Path

from tools.dto_equivalence_check import (
    DTOEquivalenceFinding,
    _extract_class_fields,
    _extract_enum_members,
    validate_dto_equivalence,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dto_equivalence() -> None:
    """Fail if r4-contracts and UMRS DTO shapes diverge."""
    findings = validate_dto_equivalence(REPO_ROOT)
    if findings:
        details = "\n".join(f.format() for f in findings)
        raise AssertionError(f"DTO structural equivalence violations:\n{details}")


def test_extract_class_fields_dataclass(tmp_path: Path) -> None:
    """Should extract annotated fields from a dataclass."""
    src = tmp_path / "dto.py"
    src.write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class MyDTO:\n"
        "    name: str\n"
        "    value: int\n"
        "    tags: list[str] = None\n"
    )
    fields = _extract_class_fields(src, "MyDTO")
    assert fields is not None
    assert "name" in fields
    assert "value" in fields
    assert "tags" in fields


def test_extract_class_fields_missing_class(tmp_path: Path) -> None:
    """Should return None if class not found."""
    src = tmp_path / "dto.py"
    src.write_text("class Other:\n    pass\n")
    assert _extract_class_fields(src, "MyDTO") is None


def test_extract_class_fields_missing_file(tmp_path: Path) -> None:
    """Should return None for missing file."""
    assert _extract_class_fields(tmp_path / "nope.py", "Foo") is None


def test_extract_enum_members(tmp_path: Path) -> None:
    """Should extract enum member names."""
    src = tmp_path / "enums.py"
    src.write_text(
        "from enum import Enum\n\n"
        "class Color(Enum):\n"
        "    RED = 'red'\n"
        "    GREEN = 'green'\n"
        "    BLUE = 'blue'\n"
    )
    members = _extract_enum_members(src, "Color")
    assert members is not None
    assert set(members) == {"RED", "GREEN", "BLUE"}


def test_extract_enum_members_missing(tmp_path: Path) -> None:
    """Should return None if enum class not found."""
    src = tmp_path / "enums.py"
    src.write_text("class Other:\n    pass\n")
    assert _extract_enum_members(src, "Color") is None


def test_validates_matching_dtos(tmp_path: Path) -> None:
    """Should produce no findings for matching DTOs."""
    (tmp_path / "contracts").mkdir()
    (tmp_path / "umrs").mkdir()
    contracts = tmp_path / "contracts" / "dto.py"
    umrs = tmp_path / "umrs" / "dto.py"
    contracts.write_text("class Foo:\n    name: str\n    value: int\n")
    umrs.write_text("class Foo:\n    name: str\n    value: int\n")
    findings = validate_dto_equivalence(
        tmp_path,
        pairs=[("contracts/dto.py", "umrs/dto.py", "Foo")],
    )
    assert findings == []


def test_validates_field_divergence(tmp_path: Path) -> None:
    """Should detect field-level divergence."""
    (tmp_path / "contracts").mkdir()
    (tmp_path / "umrs").mkdir()
    contracts = tmp_path / "contracts" / "dto.py"
    umrs = tmp_path / "umrs" / "dto.py"
    contracts.write_text("class Foo:\n    name: str\n    extra: bool\n")
    umrs.write_text("class Foo:\n    name: str\n    other: int\n")
    findings = validate_dto_equivalence(
        tmp_path,
        pairs=[("contracts/dto.py", "umrs/dto.py", "Foo")],
    )
    assert len(findings) >= 1
    messages = " ".join(f.message for f in findings)
    assert "extra" in messages or "other" in messages


def test_validates_missing_contracts_file(tmp_path: Path) -> None:
    """Should report missing contracts file."""
    (tmp_path / "umrs").mkdir()
    (tmp_path / "umrs" / "dto.py").write_text("class Foo:\n    x: int\n")
    findings = validate_dto_equivalence(
        tmp_path,
        pairs=[("contracts/nope.py", "umrs/dto.py", "Foo")],
    )
    assert len(findings) == 1
    assert "not found" in findings[0].message


def test_finding_format() -> None:
    """DTOEquivalenceFinding.format() produces readable output."""
    f = DTOEquivalenceFinding(
        contracts_path="a.py",
        umrs_path="b.py",
        class_name="Foo",
        message="diverged",
    )
    formatted = f.format()
    assert "Foo" in formatted
    assert "a.py" in formatted
