from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def isolate_xdg_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def test_connect_package_launches_runtime_and_persists_state(tmp_path):
    from r4.client import Client
    from r4.config import Config
    from r4.integrations.hatchling import HatchlingBridge, PackageSource, create_package_template

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    create_package_template("demo-tools", output_dir=source_dir)

    packages_dir = tmp_path / "packages"
    client = Client(
        config=Config(gateway_url="http://gateway.test", api_key="secret", verify_ssl=False)
    )
    bridge = HatchlingBridge(client, packages_dir=packages_dir)

    asyncio.run(
        bridge.install_package(
            "demo-tools",
            source=PackageSource.LOCAL,
            source_url=str(source_dir / "demo-tools"),
        )
    )

    with (
        patch("r4.mcp.MCPClient.register_server", return_value={"server_id": "hatch-demo-tools"}),
        patch("r4.mcp.MCPClient.unregister_server", return_value=True),
    ):
        try:
            assert asyncio.run(bridge.connect_package("demo-tools")) is True

            runtime_state_path = packages_dir / "demo-tools" / ".r4_hatch_runtime.json"
            assert runtime_state_path.exists()

            runtime_state = json.loads(runtime_state_path.read_text())
            assert runtime_state["server_name"] == "hatch-demo-tools"
            assert runtime_state["url"].startswith("http://127.0.0.1:")
            assert any(tool["name"] == "hello_world" for tool in runtime_state["tools"])

            reloaded_bridge = HatchlingBridge(client, packages_dir=packages_dir)
            packages = reloaded_bridge.list_packages()
            assert packages[0].status.value == "connected"

            assert asyncio.run(reloaded_bridge.disconnect_package("demo-tools")) is True
            assert not runtime_state_path.exists()
        finally:
            state_path = packages_dir / "demo-tools" / ".r4_hatch_runtime.json"
            if state_path.exists():
                try:
                    asyncio.run(bridge.disconnect_package("demo-tools"))
                except Exception:
                    pass


def test_install_package_verifies_signature_when_required(tmp_path):
    from r4.client import Client
    from r4.config import Config
    from r4.integrations.hatchling import (
        HatchlingBridge,
        HatchSecurityPolicy,
        PackageSource,
        SignatureInfo,
        SignatureVerifier,
        create_package_template,
    )

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    create_package_template("signed-tools", output_dir=source_dir)

    packages_dir = tmp_path / "packages"
    client = Client(
        config=Config(gateway_url="http://gateway.test", api_key="secret", verify_ssl=False)
    )
    bridge = HatchlingBridge(
        client,
        packages_dir=packages_dir,
        security_policy=HatchSecurityPolicy(require_signature=True),
    )

    with patch.object(
        SignatureVerifier,
        "verify",
        new=AsyncMock(return_value=SignatureInfo(is_valid=True, signer_id="test-signer")),
    ) as verify_mock:
        pkg_info = asyncio.run(
            bridge.install_package(
                "signed-tools",
                source=PackageSource.LOCAL,
                source_url=str(source_dir / "signed-tools"),
            )
        )

    assert pkg_info.name == "signed-tools"
    assert verify_mock.await_count == 1


def test_install_package_rejects_invalid_signature_when_required(tmp_path):
    from r4.client import Client
    from r4.config import Config
    from r4.integrations.hatchling import (
        HatchlingBridge,
        HatchSecurityPolicy,
        PackageSource,
        SignatureInfo,
        SignatureVerifier,
        create_package_template,
    )

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    create_package_template("unsigned-tools", output_dir=source_dir)

    packages_dir = tmp_path / "packages"
    client = Client(
        config=Config(gateway_url="http://gateway.test", api_key="secret", verify_ssl=False)
    )
    bridge = HatchlingBridge(
        client,
        packages_dir=packages_dir,
        security_policy=HatchSecurityPolicy(require_signature=True),
    )

    with patch.object(
        SignatureVerifier,
        "verify",
        new=AsyncMock(return_value=SignatureInfo(is_valid=False, error="bad signature")),
    ):
        with pytest.raises(RuntimeError, match="signature verification failed"):
            asyncio.run(
                bridge.install_package(
                    "unsigned-tools",
                    source=PackageSource.LOCAL,
                    source_url=str(source_dir / "unsigned-tools"),
                )
            )

    assert not (packages_dir / "unsigned-tools").exists()


def test_local_registry_search_reads_static_index(tmp_path):
    from r4.integrations.hatchling import HatchRegistryClient

    registry_dir = tmp_path / "registry"
    registry_dir.mkdir()
    (registry_dir / "registry.json").write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "name": "site-rag-tools",
                        "version": "1.2.0",
                        "description": "RAG tools approved for the site",
                        "tags": ["rag", "site"],
                    },
                    {
                        "name": "other-tools",
                        "version": "0.1.0",
                        "description": "Unrelated tools",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    results = asyncio.run(HatchRegistryClient(str(registry_dir)).search_packages("rag"))

    assert [result["name"] for result in results] == ["site-rag-tools"]
    assert results[0]["registry_url"] == str(registry_dir)


def test_install_package_from_local_registry_entry(tmp_path):
    from r4.client import Client
    from r4.config import Config
    from r4.integrations.hatchling import (
        HatchlingBridge,
        HatchSecurityPolicy,
        PackageSource,
        create_package_template,
    )

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    create_package_template("site-tools", output_dir=source_dir)
    registry_dir = tmp_path / "registry"
    package_index_dir = registry_dir / "packages" / "site-tools"
    package_index_dir.mkdir(parents=True)
    (package_index_dir / "hatch.json").write_text(
        json.dumps(
            {
                "name": "site-tools",
                "version": "0.1.0",
                "description": "Site-approved MCP tools",
                "repository": str(source_dir / "site-tools"),
            }
        ),
        encoding="utf-8",
    )

    client = Client(config=Config(gateway_url="http://gateway.test", api_key="secret"))
    bridge = HatchlingBridge(
        client,
        packages_dir=tmp_path / "packages",
        security_policy=HatchSecurityPolicy(allowed_registries=[str(registry_dir)]),
    )

    pkg_info = asyncio.run(
        bridge.install_package(
            "site-tools",
            source=PackageSource.REGISTRY,
            source_url=str(registry_dir),
        )
    )

    assert pkg_info.name == "site-tools"
    assert pkg_info.install_path == tmp_path / "packages" / "site-tools"
    assert (pkg_info.install_path / "server.py").exists()


def test_bridge_searches_multiple_registries_in_order(tmp_path):
    from r4.client import Client
    from r4.config import Config
    from r4.integrations.hatchling import HatchlingBridge, HatchSecurityPolicy

    empty_registry = tmp_path / "empty-registry"
    empty_registry.mkdir()
    site_registry = tmp_path / "site-registry"
    package_dir = site_registry / "packages" / "site-search"
    package_dir.mkdir(parents=True)
    (package_dir / "hatch.json").write_text(
        json.dumps(
            {
                "name": "site-search",
                "version": "2.0.0",
                "description": "Search approved site data",
            }
        ),
        encoding="utf-8",
    )
    client = Client(config=Config(gateway_url="http://gateway.test", api_key="secret"))
    bridge = HatchlingBridge(
        client,
        packages_dir=tmp_path / "packages",
        security_policy=HatchSecurityPolicy(
            allowed_registries=[str(empty_registry), str(site_registry)]
        ),
    )

    results = asyncio.run(bridge.search_available_packages("search"))

    assert [result["name"] for result in results] == ["site-search"]
    assert results[0]["registry_url"] == str(site_registry)


def test_hatch_cli_search_uses_configured_local_registry(tmp_path, monkeypatch, capsys):
    import r4.client as client_module
    from r4._cli.mcp import cmd_mcp_hatch

    registry_dir = tmp_path / "registry"
    registry_dir.mkdir()
    (registry_dir / "registry.json").write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "name": "site-cli-tools",
                        "version": "1.0.0",
                        "description": "Site CLI tools",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("R4_HATCH_REGISTRIES", str(registry_dir))
    monkeypatch.setattr(client_module, "Client", lambda: SimpleNamespace())

    cmd_mcp_hatch(
        SimpleNamespace(
            hatch_command="search",
            query="cli",
            limit=20,
            registry=None,
            as_json=False,
        )
    )

    output = capsys.readouterr().out
    assert "Available Hatch! Packages" in output
    assert "site-cli-tools" in output


def test_hatch_cli_search_allows_explicit_registry(tmp_path, monkeypatch, capsys):
    import r4.client as client_module
    from r4._cli.mcp import cmd_mcp_hatch

    registry_dir = tmp_path / "explicit-registry"
    registry_dir.mkdir()
    (registry_dir / "registry.json").write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "name": "explicit-tools",
                        "version": "1.0.0",
                        "description": "Explicit registry package",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("R4_HATCH_REGISTRIES", raising=False)
    monkeypatch.setattr(client_module, "Client", lambda: SimpleNamespace())

    cmd_mcp_hatch(
        SimpleNamespace(
            hatch_command="search",
            query="explicit",
            limit=20,
            registry=str(registry_dir),
            as_json=False,
        )
    )

    output = capsys.readouterr().out
    assert "explicit-tools" in output


def test_hatch_cli_registry_list_shows_configured_sources(tmp_path, monkeypatch, capsys):
    import r4.client as client_module
    from r4._cli.mcp import cmd_mcp_hatch

    registry_dir = tmp_path / "site-registry"
    registry_dir.mkdir()
    monkeypatch.setenv("R4_HATCH_REGISTRIES", str(registry_dir))
    monkeypatch.setattr(client_module, "Client", lambda: SimpleNamespace())

    cmd_mcp_hatch(
        SimpleNamespace(
            hatch_command="registry",
            registry_command="list",
            as_json=False,
        )
    )

    output = capsys.readouterr().out
    assert "Configured Hatch Registries" in output
    assert str(registry_dir) in output


def test_share_package_uploads_archive_to_project_storage(tmp_path):
    from r4.client import Client
    from r4.config import Config
    from r4.data import UploadResult
    from r4.integrations.hatchling import HatchlingBridge, PackageSource, create_package_template

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    create_package_template("shared-tools", output_dir=source_dir)

    packages_dir = tmp_path / "packages"
    client = Client(
        config=Config(
            gateway_url="http://gateway.test",
            api_key="secret",
            verify_ssl=False,
            project_id="demo-project",
        )
    )
    bridge = HatchlingBridge(client, packages_dir=packages_dir)
    asyncio.run(
        bridge.install_package(
            "shared-tools",
            source=PackageSource.LOCAL,
            source_url=str(source_dir / "shared-tools"),
        )
    )

    runtime_state_path = packages_dir / "shared-tools" / ".r4_hatch_runtime.json"
    runtime_state_path.write_text('{"status":"connected"}')
    captured: dict[str, object] = {}

    def upload_side_effect(
        file_path, *, project_id=None, bucket_alias=None, metadata=None, force_http=False
    ):
        archive_path = Path(file_path)
        assert archive_path.exists()
        captured["project_id"] = project_id
        captured["metadata"] = metadata
        captured["filename"] = archive_path.name
        with zipfile.ZipFile(archive_path, "r") as archive:
            captured["members"] = set(archive.namelist())
            metadata_payload = json.loads(
                archive.read("shared-tools/.r4_share_metadata.json").decode()
            )
            captured["share_metadata"] = metadata_payload
        return UploadResult(
            file_id="shared-tools-archive", filename=archive_path.name, status="success"
        )

    with patch.object(client.data, "upload", side_effect=upload_side_effect) as upload_mock:
        assert asyncio.run(bridge.share_package("shared-tools", "project-alpha")) is True

    assert upload_mock.call_count == 1
    assert captured["project_id"] == "project-alpha"
    assert captured["filename"] == "shared-tools-0.1.0.zip"
    assert captured["metadata"] == {
        "artifact_type": "hatch_package",
        "package_name": "shared-tools",
        "package_version": "0.1.0",
        "package_description": "Hatch! MCP server: shared-tools",
        "package_source": "local",
        "package_source_url": str(source_dir / "shared-tools"),
        "package_tools": [],
        "project_id": "project-alpha",
    }
    assert "shared-tools/hatch.json" in captured["members"]
    assert "shared-tools/server.py" in captured["members"]
    assert "shared-tools/.r4_share_metadata.json" in captured["members"]
    assert "shared-tools/.r4_hatch_runtime.json" not in captured["members"]
    share_metadata = captured["share_metadata"]
    assert share_metadata["shared_via"] == "r4.mcp.hatch.share"
    assert share_metadata["project_id"] == "project-alpha"
    assert share_metadata["package"]["name"] == "shared-tools"
    assert share_metadata["package"]["version"] == "0.1.0"
    assert share_metadata["package"]["description"] == "Hatch! MCP server: shared-tools"
    assert share_metadata["package"]["source"] == "local"
    assert share_metadata["package"]["source_url"] == str(source_dir / "shared-tools")
    assert share_metadata["package"]["install_path"] == str(packages_dir / "shared-tools")
    assert share_metadata["package"]["status"] == "installed"
    assert share_metadata["package"]["tools"] == []
    assert share_metadata["package"]["license"] == "MIT"
    assert share_metadata["package"]["dependencies"] == []


# ── Data model & policy tests (P2-c hardening) ────────────────────────


def test_package_info_to_dict():
    from r4.integrations.hatchling import HatchPackageInfo, PackageSource, PackageStatus

    pkg = HatchPackageInfo(
        name="test-pkg",
        version="2.0.0",
        description="A test package",
        source=PackageSource.LOCAL,
        status=PackageStatus.INSTALLED,
    )
    d = pkg.to_dict()
    assert d["name"] == "test-pkg"
    assert d["version"] == "2.0.0"
    assert d["source"] == "local"
    assert d["status"] == "installed"


def test_security_policy_allow_all_by_default():
    from r4.integrations.hatchling import HatchSecurityPolicy

    policy = HatchSecurityPolicy()
    assert policy.is_package_allowed("any-package") is True
    assert policy.require_signature is False


def test_security_policy_denies_unlisted_package():
    from r4.integrations.hatchling import HatchSecurityPolicy

    policy = HatchSecurityPolicy(allowed_packages=["approved-only"])
    assert policy.is_package_allowed("approved-only") is True
    assert policy.is_package_allowed("rogue-pkg") is False


def test_security_policy_registry_allowlist():
    from r4.integrations.hatchling import HatchSecurityPolicy

    policy = HatchSecurityPolicy(allowed_registries=["/safe/registry"])
    assert policy.is_registry_allowed("/safe/registry") is True
    assert policy.is_registry_allowed("/evil/registry") is False


def test_uninstall_unknown_package_returns_false(tmp_path):
    from r4.client import Client
    from r4.config import Config
    from r4.integrations.hatchling import HatchlingBridge

    client = Client(
        config=Config(gateway_url="http://gateway.test", api_key="secret", verify_ssl=False)
    )
    bridge = HatchlingBridge(client, packages_dir=tmp_path / "packages")
    assert bridge.uninstall_package("nonexistent") is False


def test_uninstall_removes_package_from_disk(tmp_path):
    from r4.client import Client
    from r4.config import Config
    from r4.integrations.hatchling import HatchlingBridge, PackageSource, create_package_template

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    create_package_template("removable-tools", output_dir=source_dir)

    packages_dir = tmp_path / "packages"
    client = Client(
        config=Config(gateway_url="http://gateway.test", api_key="secret", verify_ssl=False)
    )
    bridge = HatchlingBridge(client, packages_dir=packages_dir)

    asyncio.run(
        bridge.install_package(
            "removable-tools",
            source=PackageSource.LOCAL,
            source_url=str(source_dir / "removable-tools"),
        )
    )
    assert (packages_dir / "removable-tools").exists()

    assert bridge.uninstall_package("removable-tools") is True
    assert not (packages_dir / "removable-tools").exists()
    assert bridge.list_packages() == []


def test_list_packages_returns_installed(tmp_path):
    from r4.client import Client
    from r4.config import Config
    from r4.integrations.hatchling import HatchlingBridge, PackageSource, create_package_template

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    create_package_template("listed-tools", output_dir=source_dir)

    packages_dir = tmp_path / "packages"
    client = Client(
        config=Config(gateway_url="http://gateway.test", api_key="secret", verify_ssl=False)
    )
    bridge = HatchlingBridge(client, packages_dir=packages_dir)

    asyncio.run(
        bridge.install_package(
            "listed-tools",
            source=PackageSource.LOCAL,
            source_url=str(source_dir / "listed-tools"),
        )
    )

    packages = bridge.list_packages()
    assert len(packages) == 1
    assert packages[0].name == "listed-tools"


def test_registry_source_display_name():
    from r4.integrations.hatchling import HatchRegistrySource

    src = HatchRegistrySource(url="/path/to/registry", name="My Registry")
    assert src.display_name == "My Registry"
    d = src.to_dict()
    assert d["url"] == "/path/to/registry"
    assert d["name"] == "My Registry"


def test_registry_source_auto_display_name():
    from r4.integrations.hatchling import HatchRegistrySource

    src = HatchRegistrySource(url="/some/long/path/registry")
    assert src.display_name  # Should generate some display name
