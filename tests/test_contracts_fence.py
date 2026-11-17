"""CI gate: r4-contracts depends only on umrs-protocol, which has no dependencies.

Fence (a) (U2.0b, 2026-11-15; until then r4-contracts had zero dependencies):
exactly one requirement, ``umrs-protocol`` with a ``>=`` floor. Fence (b)
(U2.0a): umrs-protocol declares ``dependencies = []``. ADR-005 as amended by
ADR-117, U2.0 spec §5.
"""

from pathlib import Path

import pytest

from tools.contracts_fence import (
    main,
    validate_contracts_dependency,
    validate_contracts_fence,
    validate_umrs_protocol_zero_deps,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _contracts(root: Path, deps: str) -> Path:
    contracts_dir = root / "r4-contracts"
    contracts_dir.mkdir()
    (contracts_dir / "pyproject.toml").write_text(
        f'[project]\nname = "r4-contracts"\ndependencies = {deps}\n'
    )
    return root


# --- Fence (a): exactly one dependency, umrs-protocol with a >= floor (U2.0b) ---


def test_real_contracts_depend_only_on_umrs_protocol() -> None:
    violations = validate_contracts_dependency(REPO_ROOT)
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    "requirement",
    [
        "umrs-protocol>=0.2.0rc4",
        "umrs_protocol>=0.2.0",
        "UMRS.Protocol >= 1.0",
        " umrs-protocol>=2 ",
    ],
)
def test_one_umrs_protocol_floor_passes(tmp_path: Path, requirement: str) -> None:
    root = _contracts(tmp_path, f'["{requirement}"]')
    assert validate_contracts_dependency(root) == []


@pytest.mark.parametrize(
    ("deps", "why"),
    [
        ("[]", "exactly one"),  # the pre-U2.0b shape: the alias has nothing to import
        ('["umrs-protocol>=0.2.0rc4", "requests"]', "exactly one"),
        ('["umrs-protocol>=0.2.0rc4", "umrs-protocol>=0.2.0rc4"]', "exactly one"),
        ('["requests>=2.0"]', "must be 'umrs-protocol'"),
        ('["umrs-protocols>=0.2.0"]', "must be 'umrs-protocol'"),
        ('["umrs-protocol[extra]>=0.2.0rc4"]', "plain"),
        ("[\"umrs-protocol>=0.2.0rc4; python_version >= '3.10'\"]", "plain"),
        ('["umrs-protocol @ file:///tmp/umrs-protocol"]', "plain"),
        ('["umrs-protocol @ https://example.invalid/u.whl"]', "plain"),
        ('["umrs-protocol"]', "plain"),
        ('["umrs-protocol==0.2.0rc4"]', "plain"),
        ('["umrs-protocol>=0.2.0rc4,<0.3"]', "plain"),
        ('["umrs-protocol~=0.2"]', "plain"),
        ("[1]", "plain"),
    ],
)
def test_anything_but_one_floor_fails(tmp_path: Path, deps: str, why: str) -> None:
    violations = validate_contracts_dependency(_contracts(tmp_path, deps))
    assert len(violations) == 1 and why in violations[0], violations


def test_absent_dependency_list_fails(tmp_path: Path) -> None:
    (tmp_path / "r4-contracts").mkdir()
    (tmp_path / "r4-contracts" / "pyproject.toml").write_text('[project]\nname = "r4-contracts"\n')
    violations = validate_contracts_dependency(tmp_path)
    assert len(violations) == 1 and "exactly one" in violations[0]


def test_dynamic_contracts_dependencies_fail(tmp_path: Path) -> None:
    (tmp_path / "r4-contracts").mkdir()
    (tmp_path / "r4-contracts" / "pyproject.toml").write_text(
        '[project]\nname = "r4-contracts"\ndynamic = ["dependencies"]\n'
    )
    violations = validate_contracts_dependency(tmp_path)
    assert len(violations) == 1 and "dynamic" in violations[0]


def test_optional_deps_not_flagged(tmp_path: Path) -> None:
    """Optional dependencies (dev tools) are outside the fence."""
    root = _contracts(
        tmp_path, '["umrs-protocol>=0.2.0rc4"]\n\n[project.optional-dependencies]\ndev = ["pytest"]'
    )
    assert validate_contracts_dependency(root) == []


def test_missing_pyproject(tmp_path: Path) -> None:
    violations = validate_contracts_dependency(tmp_path)
    assert len(violations) == 1
    assert "not found" in violations[0]


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


# --- Fence (b): umrs-protocol has zero dependencies (ADR-117 D-2, U2.0a) ---

_PROTOCOL_OK = '[project]\nname = "umrs-protocol"\ndependencies = []\n'


def _write_protocol(root: Path, text: str) -> None:
    pyproject = root / "umrs" / "protocol" / "pyproject.toml"
    pyproject.parent.mkdir(parents=True)
    pyproject.write_text(text)


def _write_contracts(root: Path) -> None:
    _contracts(root, '["umrs-protocol>=0.2.0rc4"]')


def test_real_workspace_passes_the_whole_fence() -> None:
    assert validate_contracts_fence(REPO_ROOT) == []


def test_protocol_with_empty_deps_passes(tmp_path: Path) -> None:
    _write_protocol(
        tmp_path, _PROTOCOL_OK + '\n[project.optional-dependencies]\ndev = ["pytest"]\n'
    )
    assert validate_umrs_protocol_zero_deps(tmp_path) == []


def test_protocol_with_any_dependency_fails(tmp_path: Path) -> None:
    _write_protocol(tmp_path, '[project]\nname = "umrs-protocol"\ndependencies = ["httpx"]\n')
    violations = validate_umrs_protocol_zero_deps(tmp_path)
    assert len(violations) == 1 and "httpx" in violations[0]


def test_missing_protocol_pyproject_fails_closed(tmp_path: Path) -> None:
    violations = validate_umrs_protocol_zero_deps(tmp_path)
    assert len(violations) == 1 and "not found" in violations[0]


def test_wrong_protocol_name_fails(tmp_path: Path) -> None:
    _write_protocol(tmp_path, '[project]\nname = "umrs-protocols"\ndependencies = []\n')
    violations = validate_umrs_protocol_zero_deps(tmp_path)
    assert len(violations) == 1 and "umrs-protocols" in violations[0]


def test_protocol_without_a_static_dependency_list_fails(tmp_path: Path) -> None:
    _write_protocol(tmp_path, '[project]\nname = "umrs-protocol"\n')
    assert len(validate_umrs_protocol_zero_deps(tmp_path)) == 1


def test_protocol_with_dynamic_dependencies_fails(tmp_path: Path) -> None:
    _write_protocol(tmp_path, '[project]\nname = "umrs-protocol"\ndynamic = ["dependencies"]\n')
    violations = validate_umrs_protocol_zero_deps(tmp_path)
    assert any("dynamic" in v for v in violations)


def test_fence_reports_both_packages(tmp_path: Path) -> None:
    """The combined fence fails when either half fails; both halves run."""
    _write_contracts(tmp_path)
    assert len(validate_contracts_fence(tmp_path)) == 1  # protocol missing
    _write_protocol(tmp_path, _PROTOCOL_OK)
    assert validate_contracts_fence(tmp_path) == []


def test_main_runs_the_protocol_half(monkeypatch, capsys) -> None:
    import tools.contracts_fence as fence

    monkeypatch.setattr(fence, "validate_umrs_protocol_zero_deps", lambda _root: ["boom"])
    assert main() == 1
    assert "FAIL: boom" in capsys.readouterr().out


def test_pre_commit_hook_runs_on_the_umrs_gitlink() -> None:
    """The parent sees umrs/protocol only as the ``umrs`` gitlink: a pointer bump
    must run the fence, as an r4-contracts pyproject edit does."""
    import re

    import yaml

    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text())
    hooks = [hook for repo in config["repos"] for hook in repo["hooks"]]
    fence = next(hook for hook in hooks if hook["id"] == "contracts-fence")
    pattern = re.compile(fence["files"])
    assert pattern.search("umrs") and pattern.search("r4-contracts/pyproject.toml")
    assert not pattern.search("umrs/src/umrs/api/loop.py")
