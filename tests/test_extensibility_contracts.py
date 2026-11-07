"""Tests for COR-004 extensibility governance contracts."""

from __future__ import annotations

import json

from r4_contracts.extensibility import (
    ExtensionManifest,
    ExtensionStatus,
    ExtensionTier,
    PluginJobContract,
    SandboxConstraints,
    _validate_entry_point,
)

# ---------------------------------------------------------------------------
# Sandbox constraints tests
# ---------------------------------------------------------------------------


class TestSandboxConstraints:
    def test_valid_defaults(self):
        s = SandboxConstraints()
        assert s.validate() == []

    def test_bad_network(self):
        s = SandboxConstraints(network="none")
        errs = s.validate()
        assert any("egress_guard" in e for e in errs)

    def test_bad_filesystem(self):
        s = SandboxConstraints(filesystem="read_write")
        errs = s.validate()
        assert any("read_only" in e for e in errs)

    def test_zero_memory(self):
        s = SandboxConstraints(max_memory_mb=0)
        errs = s.validate()
        assert any("max_memory_mb" in e for e in errs)

    def test_negative_runtime(self):
        s = SandboxConstraints(max_runtime_sec=-1)
        errs = s.validate()
        assert any("max_runtime_sec" in e for e in errs)


# ---------------------------------------------------------------------------
# Entry point validation tests
# ---------------------------------------------------------------------------


class TestEntryPointValidation:
    def test_valid_python(self):
        assert _validate_entry_point("python server.py") == []

    def test_valid_node(self):
        assert _validate_entry_point("node index.js") == []

    def test_shell_rejected(self):
        errs = _validate_entry_point("bash -c 'echo hello'")
        assert any("shell" in e for e in errs)

    def test_inline_code_rejected(self):
        errs = _validate_entry_point("python -c 'import os'")
        assert any("-c" in e for e in errs)

    def test_path_traversal_rejected(self):
        errs = _validate_entry_point("python ../../etc/passwd")
        assert any("traversal" in e for e in errs)

    def test_empty_entry_point(self):
        errs = _validate_entry_point("")
        assert any("empty" in e for e in errs)


# ---------------------------------------------------------------------------
# ExtensionManifest tests
# ---------------------------------------------------------------------------


class TestExtensionManifest:
    def _valid_manifest(self, **overrides) -> ExtensionManifest:
        defaults = {
            "name": "test-ext",
            "version": "1.0.0",
            "entry_point": "python server.py",
            "tools": ["search"],
            "tool_capabilities": {"search": ["READ_LOCAL_FILE"]},
        }
        defaults.update(overrides)
        return ExtensionManifest(**defaults)

    def test_valid_manifest(self):
        m = self._valid_manifest()
        assert m.validate() == []

    def test_missing_name(self):
        m = self._valid_manifest(name="")
        assert any("name" in e for e in m.validate())

    def test_missing_version(self):
        m = self._valid_manifest(version="")
        assert any("version" in e for e in m.validate())

    def test_empty_tools(self):
        m = self._valid_manifest(tools=[])
        assert any("tools" in e for e in m.validate())

    def test_unknown_tool_capability_ref(self):
        m = self._valid_manifest(
            tool_capabilities={"nonexistent": ["READ_LOCAL_FILE"]},
        )
        assert any("nonexistent" in e for e in m.validate())

    def test_to_dict_round_trip(self):
        m = self._valid_manifest(description="desc", author="alice")
        d = m.to_dict()
        assert d["name"] == "test-ext"
        assert d["tools"] == ["search"]
        serialized = json.dumps(d)
        assert "test-ext" in serialized

    def test_from_dict_capabilities_format(self):
        """from_dict supports nested 'capabilities' key (config.json format)."""
        raw = {
            "name": "ext1",
            "version": "2.0",
            "entry_point": "python main.py",
            "capabilities": {
                "tools": ["tool_a", "tool_b"],
                "tool_capabilities": {"tool_a": ["WRITE_REMOTE"]},
                "egress": {"domains": ["api.example.com"]},
            },
            "sandbox": {
                "network": "egress_guard",
                "filesystem": "read_only",
                "max_memory_mb": 256,
                "max_runtime_sec": 10,
            },
        }
        m = ExtensionManifest.from_dict(raw)
        assert m.tools == ["tool_a", "tool_b"]
        assert m.tool_capabilities == {"tool_a": ["WRITE_REMOTE"]}
        assert m.validate() == []

    def test_signing_metadata(self):
        m = self._valid_manifest(
            signature="sig-abc",
            signed_by="admin",
            signed_at="2026-01-01T00:00:00Z",
        )
        d = m.to_dict()
        assert d["signature"] == "sig-abc"
        assert d["signed_by"] == "admin"


# ---------------------------------------------------------------------------
# PluginJobContract tests
# ---------------------------------------------------------------------------


class TestPluginJobContract:
    def test_valid_contract(self):
        c = PluginJobContract(
            plugin_name="test-plugin",
            job_type="data_transform",
        )
        assert c.validate() == []

    def test_missing_plugin_name(self):
        c = PluginJobContract(plugin_name="", job_type="x")
        assert any("plugin_name" in e for e in c.validate())

    def test_unknown_authority(self):
        c = PluginJobContract(
            plugin_name="p",
            job_type="t",
            execution_authorities=["lambda"],
        )
        assert any("lambda" in e for e in c.validate())

    def test_to_dict(self):
        c = PluginJobContract(
            plugin_name="p",
            job_type="custom_inference",
            requires_gpu=True,
        )
        d = c.to_dict()
        assert d["requires_gpu"] is True
        assert d["job_type"] == "custom_inference"


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


class TestEnums:
    def test_extension_tier_values(self):
        assert ExtensionTier.SYSTEM.value == "system"
        assert ExtensionTier.PROJECT.value == "project"
        assert ExtensionTier.USER.value == "user"

    def test_extension_status_values(self):
        assert ExtensionStatus.PENDING.value == "pending"
        assert ExtensionStatus.APPROVED.value == "approved"
        assert ExtensionStatus.SUSPENDED.value == "suspended"
        assert ExtensionStatus.REVOKED.value == "revoked"
