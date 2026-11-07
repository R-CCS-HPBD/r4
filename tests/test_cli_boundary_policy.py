"""Static checks for documented CLI ownership boundaries."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# INTEGRATION_POLICY.md moved docs/ -> docs/architecture/boundaries/ in
# commit 26276265; content (incl. the CLI Ownership Boundary section) is
# unchanged. docs/CANONICAL_MAP.md and CONSTITUTION.md cite the new path.
INTEGRATION_POLICY = REPO_ROOT / "docs" / "architecture" / "boundaries" / "INTEGRATION_POLICY.md"


def test_integration_policy_documents_cli_ownership_boundary() -> None:
    text = INTEGRATION_POLICY.read_text()

    assert "## CLI Ownership Boundary" in text
    assert "`r4` stays thin" in text
    assert "`umrs` is not the admin CLI" in text
    assert "`umrs-server admin` is the only supported" in text
    assert "disabled permanently" in text
    assert "escape hatch" in text


def test_admin_guide_points_admins_to_umrs_server_admin() -> None:
    text = (REPO_ROOT / "docs" / "r4" / "admin-guide" / "README.md").read_text()

    assert "legacy `admin` command is permanently disabled" in text
    assert "`umrs-server admin` is the only supported admin CLI" in text


def test_r4_docs_include_cli_map() -> None:
    text = (REPO_ROOT / "docs" / "r4" / "index.md").read_text()

    assert "## CLI Map" in text
    assert "`r4` | User-facing path through Gateway" in text
    assert "`umrs` | Operator/developer diagnostics" in text
    assert "`umrs-server admin` | Offline operator-only administration" in text
