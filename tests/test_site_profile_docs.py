"""Static checks for UMRS site-profile deployment documentation."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_admin_guide_documents_shipped_site_profiles() -> None:
    text = (REPO_ROOT / "docs" / "r4" / "admin-guide" / "README.md").read_text()

    assert "## Site Profiles" in text
    assert "`UMRS_SITE_PROFILE`" in text
    assert "`RIVAULT_SITE_PROFILE`" in text
    for profile in ("rccs-cloud", "lab-podman", "compose-dev", "k8s-cluster"):
        assert f"`{profile}`" in text


@pytest.mark.skipif(
    not (REPO_ROOT / "deploy" / "ansible").is_dir(),
    reason="the sites' Ansible deployment is not in this tree (public export)",
)
def test_ansible_renders_umrs_site_profile_env() -> None:
    umrs_env = (
        REPO_ROOT / "deploy" / "ansible" / "roles" / "login_node" / "templates" / "umrs.env.j2"
    ).read_text()
    shared_env = (
        REPO_ROOT / "deploy" / "ansible" / "roles" / "common" / "templates" / "rivault-env.sh.j2"
    ).read_text()
    generic_vars = (REPO_ROOT / "deploy" / "ansible" / "group_vars" / "all.yaml").read_text()
    rccs_vars = (
        REPO_ROOT
        / "deploy"
        / "ansible"
        / "inventory"
        / "rccs-cloud"
        / "group_vars"
        / "rccs_cloud.yaml"
    ).read_text()

    assert "UMRS_SITE_PROFILE={{ umrs_site_profile | default('') }}" in umrs_env
    assert "export UMRS_SITE_PROFILE=\"{{ umrs_site_profile | default('') }}\"" in shared_env
    assert 'umrs_site_profile: ""' in generic_vars
    assert "umrs_site_profile: rccs-cloud" in rccs_vars
