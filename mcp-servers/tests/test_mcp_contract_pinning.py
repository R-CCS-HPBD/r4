"""S8 MCP contract pinning: schema-frozen tool surfaces + version metadata.

The 3 logical platform MCP servers (platform-tools, rag-search,
knowledge-tools) are pinned against ``tests/snapshots/mcp_contract.json``.
Any change to tool sets, lane assignments, or versions fails here and
requires a deliberate snapshot update plus a version bump — the "one
contract test per v1 change" rule formalized at S12.

Also pinned: the twin relationship — ``default-<package>`` registrations
carry the same capabilities/contract the package manifest declares
(twin duplication identity by construction; ADR decision at S12).
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_MCP_ROOT = Path(__file__).resolve().parents[1]
_PACKAGES_DIR = _MCP_ROOT / "hatch-registry" / "packages"
_SCRIPTS = _MCP_ROOT / "scripts"
_SNAPSHOT = json.loads((_MCP_ROOT / "tests" / "snapshots" / "mcp_contract.json").read_text())

LOGICAL_SERVERS = ["platform-tools", "rag-search", "knowledge-tools"]

sys.path.insert(0, str(_SCRIPTS))


def _manifest(pkg: str) -> dict:
    return json.loads((_PACKAGES_DIR / pkg / "hatch.json").read_text(encoding="utf-8"))


def _load_package(pkg: str) -> ModuleType:
    sys.path.insert(0, str(_PACKAGES_DIR / pkg))
    try:
        sys.modules.pop("server", None)
        return importlib.import_module("server")
    finally:
        sys.path.remove(str(_PACKAGES_DIR / pkg))


# ── snapshot integrity ────────────────────────────────────────────────────


def test_snapshot_servers_match_logical_set() -> None:
    assert sorted(_SNAPSHOT["servers"]) == sorted(LOGICAL_SERVERS)


@pytest.mark.parametrize("pkg", LOGICAL_SERVERS)
def test_manifest_matches_snapshot(pkg: str) -> None:
    snap = _SNAPSHOT["servers"][pkg]
    manifest = _manifest(pkg)
    contract = manifest.get("contract")
    assert contract, f"{pkg}: missing contract block in hatch.json"
    assert contract["lane"] == snap["lane"], f"{pkg}: lane drift vs snapshot"
    assert contract["version"] == manifest["version"] == snap["version"]
    assert sorted(manifest["tools"]) == snap["tools"], f"{pkg}: tool-set drift vs snapshot"


# ── capabilities block (registration metadata) ────────────────────────────


@pytest.mark.parametrize("pkg", LOGICAL_SERVERS)
def test_capabilities_block_complete(pkg: str) -> None:
    manifest = _manifest(pkg)
    caps = manifest.get("capabilities")
    assert caps, f"{pkg}: missing capabilities block"
    assert sorted(caps["tools"]) == _SNAPSHOT["servers"][pkg]["tools"]
    for tool in caps["tools"]:
        flags = caps["tool_capabilities"].get(tool)
        assert flags == ["STANDARD_USER"], f"{pkg}:{tool}: unexpected capability flags {flags}"


# ── serving path: offline fallback exposes exactly the contract tools ──────


@pytest.mark.parametrize("pkg", LOGICAL_SERVERS)
def test_offline_fallback_serves_contract_tools(pkg: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UMRS_MCP_VENV", "/nonexistent/mcp-venv")
    monkeypatch.setenv("FUGAKU_MCP_HOME", "/nonexistent/fugaku-mcp")
    srv = _load_package(pkg).create_server()
    served = sorted(t["name"] for t in srv.list_tools())
    assert served == _SNAPSHOT["servers"][pkg]["tools"]


# ── twin premise: default-* registration carries the same contract ─────────


def test_default_registration_metadata_matches_manifest() -> None:
    import frontend_default_mcp

    repo_root = _MCP_ROOT.parent  # mcp-servers/… → repo root
    for pkg in LOGICAL_SERVERS:
        caps = frontend_default_mcp._load_capabilities(repo_root, pkg)
        manifest = _manifest(pkg)
        assert caps == manifest["capabilities"], (
            f"default-{pkg}: registration capabilities drift from manifest"
        )
        assert f"default-{pkg}".removeprefix("default-") == pkg


def test_frontend_default_registration_carries_contract() -> None:
    import inspect

    import frontend_default_mcp

    src = inspect.getsource(frontend_default_mcp)
    assert '"contract"' in src or "'contract'" in src, (
        "default-* registration must carry the contract block in metadata"
    )


# Companion-only tool families are pinned Companion-scoped (ADR-030) and
# must never leak into MCP package manifests.
_COMPANION_ONLY_PREFIXES = (
    "lab_catalog_",
    "research_loop_",
    "research_session_",
)


def test_companion_only_tools_absent_from_mcp_packages() -> None:
    leaks: list[str] = []
    for manifest in _PACKAGES_DIR.glob("*/hatch.json"):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for tool in data.get("tools", []):
            if tool.startswith(_COMPANION_ONLY_PREFIXES):
                leaks.append(f"{manifest.parent.name}:{tool}")
    assert not leaks, (
        "companion-only tools surfaced in MCP manifests (ADR-030 violation): "
        + ", ".join(sorted(leaks))
    )
