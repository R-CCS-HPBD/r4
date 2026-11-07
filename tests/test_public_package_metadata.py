"""Package metadata that ships in the public export (R-CCS-HPBD/r4).

The repository is Apache-2.0: every first-party package manifest says so, and
project URLs point at the public repository, not at placeholders. The Helm
chart never bakes a fixed API key into its Secret.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_REPO = "https://" + "github.com/R-CCS-HPBD/r4"

PACKAGE_JSONS = [
    "packages/r4-design/package.json",
    "packages/r4-tui-v3/package.json",
    "packages/r4-web/package.json",
    "vscode-extension/webview-ui/package.json",
    "n8n/nodes/package.json",
]


@pytest.mark.parametrize("rel", PACKAGE_JSONS)
def test_package_json_declares_the_repository_licence(rel: str) -> None:
    data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    assert data.get("license") == "Apache-2.0", rel


def test_mcp_servers_project_urls_point_at_the_public_repository() -> None:
    project = tomllib.loads((ROOT / "mcp-servers/pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    urls = project["urls"]
    assert urls["Homepage"] == PUBLIC_REPO
    assert urls["Repository"] == PUBLIC_REPO
    assert urls["Documentation"] == PUBLIC_REPO + "/tree/main/mcp-servers"
    assert project["authors"] == [{"name": "RCCS AI Platform Team"}]


@pytest.mark.parametrize(
    "rel",
    ["helm/rivault/Chart.yaml", "n8n/nodes/credentials/RiVaultApi.credentials.ts"],
)
def test_no_placeholder_repository_urls(rel: str) -> None:
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "your-org" not in text
    assert PUBLIC_REPO in text


def test_helm_vllm_api_key_is_not_a_fixed_string() -> None:
    secrets = (ROOT / "helm/rivault/templates/secrets.yaml").read_text(encoding="utf-8")
    line = next(ln for ln in secrets.splitlines() if ln.strip().startswith("vllm-api-key:"))
    value = line.split(":", 1)[1].strip()
    assert value.startswith("{{"), "vllm-api-key must come from a template expression"
    assert ".Values.vllm.apiKey" in secrets
    assert "randAlphaNum" in secrets


def test_helm_values_expose_an_empty_vllm_api_key() -> None:
    values = (ROOT / "helm/rivault/values.yaml").read_text(encoding="utf-8")
    vllm_block = values.split("\nvllm:\n", 1)[1].split("\n# ===", 1)[0]
    assert '\n  apiKey: ""\n' in vllm_block


PYPROJECTS = ["pyproject.toml", "r4/pyproject.toml", "mcp-servers/pyproject.toml"]


@pytest.mark.parametrize("rel", PYPROJECTS)
def test_project_authors_publish_no_email(rel: str) -> None:
    # No unmonitored mailbox as a contact (2026-11-07): reports go through
    # GitHub private vulnerability reporting (SECURITY.md) and issues.
    project = tomllib.loads((ROOT / rel).read_text())["project"]
    people = project.get("authors", []) + project.get("maintainers", [])
    assert [p for p in people if "email" in p] == []


def test_helm_chart_maintainers_publish_no_email() -> None:
    import yaml

    chart = yaml.safe_load((ROOT / "helm" / "rivault" / "Chart.yaml").read_text())
    assert [m for m in chart.get("maintainers", []) if "email" in m] == []
