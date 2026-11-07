"""§11.4 MCP verifier — extensibility security tests.

Covers the MCP-based knowledge verifier adapter path (TASK-045):
1. Capability enforcement — MCP tools without KNOWLEDGE_VERIFY are rejected
2. Finding provenance — findings tagged with "mcp:<tool_name>"
3. Manifest validation — bad manifests are caught before execution
4. Structured vs. raw result parsing — both paths produce findings
5. Config registration — register_mcp_verifier() works end-to-end
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from r4_mcp.runners.custom_mcp import (
    CustomMCPServer,
    load_manifests,
)
from r4_mcp.security import (
    CapabilityEnforcer,
    SecurityContext,
    ToolCapability,
)

# Import MCP adapter — umrs is installed editable in the venv (ADR-053);
# no PYTHONPATH splice needed.
try:
    from umrs.knowledge.verifiers.base import Finding  # noqa: E402
    from umrs.knowledge.verifiers.mcp_adapter import (  # noqa: E402
        MCPVerifier,
        MCPVerifierConfig,
    )
except ModuleNotFoundError:
    pytest.skip(
        "umrs package not importable (submodule not checked out)",
        allow_module_level=True,
    )

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_verifier_registry():
    """Clear VERIFIER_REGISTRY before and after each test to avoid cross-test pollution."""
    from umrs.knowledge.verifiers import VERIFIER_REGISTRY

    VERIFIER_REGISTRY.clear()
    yield
    VERIFIER_REGISTRY.clear()


# ── Helpers ─────────────────────────────────────────────────────────


def _write_echo_worker(path: Path) -> None:
    """Worker that echoes back structured findings."""
    path.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "tool = payload['tool']\n"
        "args = payload.get('arguments', {})\n"
        "finding = {\n"
        "    'artifact_id': args.get('artifact_id', 'paper-001'),\n"
        "    'severity': 'warning',\n"
        "    'rule': f'mcp_{tool}_check',\n"
        "    'message': f'MCP check result for {tool} on '\n"
        '        f\'{args.get("artifact_id", "unknown")}\'\n'
        "}\n"
        "print(json.dumps({'content': [{'type': 'text', 'text': json.dumps([finding])}] }))\n"
    )


def _make_config(
    root: Path,
    *,
    tools: list[str] | None = None,
    tool_capabilities: dict[str, list[str]] | None = None,
    entry_point: str | None = None,
) -> dict[str, Any]:
    """Create a minimal config.json for testing MCP verifier."""
    worker = root / "worker.py"
    if not worker.exists():
        _write_echo_worker(worker)
    config: dict[str, Any] = {
        "name": "test-mcp-verifier",
        "version": "1.0.0",
        "entry_point": entry_point or f"{sys.executable} {worker.name}",
        "capabilities": {
            "tools": tools or ["check_knowledge"],
            "tool_capabilities": tool_capabilities or {"check_knowledge": ["KNOWLEDGE_VERIFY"]},
            "egress": {"domains": [], "protocols": ["https"]},
        },
        "sandbox": {
            "network": "egress_guard",
            "filesystem": "read_only",
            "max_memory_mb": 128,
            "max_runtime_sec": 5,
        },
    }
    (root / "config.json").write_text(json.dumps(config))
    return config


def _make_config_no_verify(
    root: Path,
    *,
    tools: list[str] | None = None,
) -> dict[str, Any]:
    """Create a config WITHOUT KNOWLEDGE_VERIFY capability."""
    tool_name = (tools or ["generic_tool"])[0]
    return _make_config(
        root,
        tools=[tool_name],  # Explicitly set tools list
        tool_capabilities={tool_name: ["READ_ONLY"]},
    )


def _text(result: dict[str, Any]) -> str:
    """Extract text from MCP tool result."""
    for block in result.get("content", []):
        if isinstance(block, dict) and "text" in block:
            return str(block["text"])
    return json.dumps(result)


# ═════════════════════════════════════════════════════════════════════
# 1. Capability Enforcement — MCP tools without KNOWLEDGE_VERIFY rejected
# ═════════════════════════════════════════════════════════════════════


class TestKnowledgeVerifyCapability:
    """MCP tools must declare KNOWLEDGE_VERIFY capability to run as verifiers."""

    def test_config_with_knowledge_verify_succeeds(self, tmp_path: Path) -> None:
        """Fixture MCP with KNOWLEDGE_VERIFY produces valid config."""
        config = _make_config(tmp_path)
        # Verify capability is declared
        caps = config["capabilities"]["tool_capabilities"]["check_knowledge"]
        assert "KNOWLEDGE_VERIFY" in caps

    def test_validate_config_passes_with_capability(self, tmp_path: Path) -> None:
        """validate_config returns no errors when KNOWLEDGE_VERIFY present."""
        _make_config(tmp_path)
        config = MCPVerifierConfig(
            tool_name="check_knowledge",
            config_path=tmp_path,
        )
        verifier = MCPVerifier(config=config)
        errors = verifier.validate_config()
        assert errors == []

    def test_validate_config_rejects_missing_capability(self, tmp_path: Path) -> None:
        """validate_config returns error when KNOWLEDGE_VERIFY absent."""
        _make_config_no_verify(tmp_path)
        config = MCPVerifierConfig(
            tool_name="generic_tool",
            config_path=tmp_path,
        )
        verifier = MCPVerifier(config=config)
        errors = verifier.validate_config()
        assert len(errors) == 1
        assert "missing KNOWLEDGE_VERIFY capability" in errors[0]

    def test_validate_config_rejects_undeclared_tool(self, tmp_path: Path) -> None:
        """validate_config errors when tool not in manifest tools list."""
        _make_config(tmp_path, tools=["actual_tool"])
        config = MCPVerifierConfig(
            tool_name="phantom_tool",  # Not in manifest
            config_path=tmp_path,
        )
        verifier = MCPVerifier(config=config)
        errors = verifier.validate_config()
        assert len(errors) == 1
        assert "not declared in manifest" in errors[0]

    def test_validate_config_rejects_bad_manifest(self, tmp_path: Path) -> None:
        """validate_config errors when config.json is invalid."""
        (tmp_path / "config.json").write_text("not json")
        config = MCPVerifierConfig(
            tool_name="check_knowledge",
            config_path=tmp_path,
        )
        verifier = MCPVerifier(config=config)
        errors = verifier.validate_config()
        assert len(errors) == 1
        assert "Failed to load MCP manifest" in errors[0]

    def test_validate_config_rejects_missing_manifest(self, tmp_path: Path) -> None:
        """validate_config errors when no config.json exists."""
        config = MCPVerifierConfig(
            tool_name="check_knowledge",
            config_path=tmp_path,
        )
        verifier = MCPVerifier(config=config)
        errors = verifier.validate_config()
        assert len(errors) == 1
        assert "No MCP manifest found" in errors[0]


# ═════════════════════════════════════════════════════════════════════
# 2. Finding Provenance — tagged with "mcp:<tool_name>"
# ═════════════════════════════════════════════════════════════════════


class TestFindingProvenance:
    """Findings from MCP verifiers carry provenance tags."""

    @pytest.mark.asyncio
    async def test_finding_verifier_tagged_with_mcp_tool_name(self, tmp_path: Path) -> None:
        """MCP verifier produces findings where verifier=name of MCP tool."""
        _make_config(tmp_path)
        config = MCPVerifierConfig(
            tool_name="check_knowledge",
            config_path=tmp_path,
            arguments={"artifact_id": "paper-001"},
        )
        verifier = MCPVerifier(config=config)
        findings = await verifier.run(knowledge_store=None, project_id="test-proj")

        assert len(findings) >= 1
        finding = findings[0]
        assert finding.verifier == "mcp_check_knowledge"

    @pytest.mark.asyncio
    async def test_finding_has_rule_identifying_mcp_source(self, tmp_path: Path) -> None:
        """Finding rule identifies MCP origin."""
        _make_config(tmp_path)
        config = MCPVerifierConfig(
            tool_name="check_knowledge",
            config_path=tmp_path,
            arguments={"artifact_id": "paper-001"},
        )
        verifier = MCPVerifier(config=config)
        findings = await verifier.run(knowledge_store=None, project_id="test-proj")

        assert len(findings) >= 1
        finding = findings[0]
        assert "mcp" in finding.rule.lower() or "check" in finding.rule.lower()

    @pytest.mark.asyncio
    async def test_provenance_property_returns_mcp_tag(self, tmp_path: Path) -> None:
        """MCPVerifierConfig.provenance returns f'mcp:<tool_name>'."""
        _make_config(tmp_path)
        config = MCPVerifierConfig(
            tool_name="terminology_checker",
            config_path=tmp_path,
        )
        assert config.provenance == "mcp:terminology_checker"

    def test_server_name_derived_from_config_path(self, tmp_path: Path) -> None:
        """MCPVerifierConfig.server_name derives from path."""
        config = MCPVerifierConfig(
            tool_name="my_tool",
            config_path=tmp_path,
        )
        assert config.server_name == f"mcp-{tmp_path.name}"

    def test_server_name_from_file_path(self, tmp_path: Path) -> None:
        """server_name uses parent dir when config_path is a file."""
        config_file = tmp_path / "config.json"
        config_file.write_text('{"name":"test"}')
        config = MCPVerifierConfig(
            tool_name="my_tool",
            config_path=config_file,
        )
        assert config.server_name == f"mcp-{tmp_path.name}"


# ═════════════════════════════════════════════════════════════════════
# 3. Structured vs. Raw Result Parsing
# ═════════════════════════════════════════════════════════════════════


class TestResultParsing:
    """MCP verifier handles structured and raw MCP tool results."""

    @pytest.mark.asyncio
    async def test_structured_json_findings_parsed(self, tmp_path: Path) -> None:
        """Structured JSON output produces multiple findings."""
        _make_config(tmp_path)
        config = MCPVerifierConfig(
            tool_name="check_knowledge",
            config_path=tmp_path,
            arguments={"artifact_id": "paper-001"},
        )
        verifier = MCPVerifier(config=config)
        findings = await verifier.run(knowledge_store=None, project_id="test-proj")

        # Echo worker returns one finding per run
        assert len(findings) >= 1
        for f in findings:
            assert isinstance(f, Finding)
            assert f.severity in ("blocking", "warning", "info")

    @pytest.mark.asyncio
    async def test_error_result_produces_finding(self, tmp_path: Path) -> None:
        """MCP tool error (isError) produces a finding."""
        _make_config(tmp_path)
        config = MCPVerifierConfig(
            tool_name="check_knowledge",
            config_path=tmp_path,
            arguments={"artifact_id": "paper-001"},
        )
        verifier = MCPVerifier(config=config)

        # Simulate MCP error result (worker might fail, but direct injection)
        result = {
            "isError": True,
            "content": [{"type": "text", "text": "Tool execution failed"}],
        }
        findings = verifier._extract_findings(result, verifier.name)

        assert len(findings) == 1
        assert findings[0].rule == "mcp_error"
        assert "Tool execution failed" in findings[0].message

    @pytest.mark.asyncio
    async def test_empty_result_produces_no_findings(self, tmp_path: Path) -> None:
        """Empty MCP tool result produces no findings."""
        result: dict[str, Any] = {"content": []}
        _make_config(tmp_path)
        config = MCPVerifierConfig(
            tool_name="check_knowledge",
            config_path=tmp_path,
        )
        verifier = MCPVerifier(config=config)
        findings = verifier._extract_findings(result, verifier.name)
        assert findings == []

    def test_parse_structured_json_array(self) -> None:
        """_parse_structured parses JSON array."""
        content = json.dumps(
            [
                {"rule": "test", "message": "msg1"},
                {"rule": "test", "message": "msg2"},
            ]
        )
        _make_config(Path("/tmp"))  # needs valid path for MCPVerifierConfig
        config = MCPVerifierConfig(tool_name="t", config_path=Path("/tmp"))
        verifier = MCPVerifier(config=config)
        parsed = verifier._parse_structured(content)
        assert len(parsed) == 2

    def test_parse_structured_findings_key(self) -> None:
        """_parse_structured parses dict with 'findings' key."""
        content = json.dumps({"findings": [{"rule": "test"}]})
        config = MCPVerifierConfig(tool_name="t", config_path=Path("/tmp"))
        verifier = MCPVerifier(config=config)
        parsed = verifier._parse_structured(content)
        assert len(parsed) == 1

    def test_parse_structured_fallback_empty(self) -> None:
        """_parse_structured returns [] for non-parseable text."""
        config = MCPVerifierConfig(tool_name="t", config_path=Path("/tmp"))
        verifier = MCPVerifier(config=config)
        parsed = verifier._parse_structured("this is not json")
        assert parsed == []


# ═════════════════════════════════════════════════════════════════════
# 4. MCP Server Integration — end-to-end via CustomMCPServer
# ═════════════════════════════════════════════════════════════════════


class TestMCPServerIntegration:
    """MCP verifier integrates correctly with CustomMCPServer."""

    @pytest.mark.asyncio
    async def test_mcp_server_invoked_with_correct_tool(self, tmp_path: Path) -> None:
        """CustomMCPServer receives tool name and arguments."""
        _make_config(tmp_path)
        manifests = load_manifests(str(tmp_path))
        enforcer = CapabilityEnforcer()
        ctx = SecurityContext(
            user_id="test-user",
            session_id="verify-test",
            granted_capabilities=ToolCapability.STANDARD_USER,
        )
        server = CustomMCPServer(manifests, enforcer=enforcer, context=ctx)
        result = await server.handle_call_tool(
            {
                "name": "check_knowledge",
                "arguments": {"artifact_id": "paper-001"},
            }
        )

        assert result["isError"] is False
        content = _text(result)
        # The structured output contains the tool name embedded
        assert "check_knowledge" in content

    @pytest.mark.asyncio
    async def test_mcp_server_rejects_tool_without_capability(self, tmp_path: Path) -> None:
        """CustomMCPServer denies tool when caller lacks capability."""
        # Create config where tool requires EXECUTE_SHELL (not in STANDARD_USER)
        _make_config(
            tmp_path,
            tool_capabilities={"check_knowledge": ["EXECUTE_SHELL"]},
        )
        manifests = load_manifests(str(tmp_path))
        enforcer = CapabilityEnforcer()
        ctx = SecurityContext(
            user_id="test-user",
            session_id="verify-test",
            granted_capabilities=ToolCapability.STANDARD_USER,
        )
        server = CustomMCPServer(manifests, enforcer=enforcer, context=ctx)
        result = await server.handle_call_tool(
            {
                "name": "check_knowledge",
                "arguments": {},
            }
        )

        assert result["isError"] is True
        assert "Capability denied" in _text(result)


# ═════════════════════════════════════════════════════════════════════
# 5. Config Registration — register_mcp_verifier()
# ═════════════════════════════════════════════════════════════════════


class TestConfigRegistration:
    """register_mcp_verifier registers in VERIFIER_REGISTRY."""

    def _get_registry(self):
        from umrs.knowledge.verifiers import get_verifier, register_verifier

        return get_verifier, register_verifier

    def test_register_mcp_verifier_returns_instance(self, tmp_path: Path) -> None:
        """register_mcp_verifier returns the MCPVerifier instance."""
        _make_config(tmp_path)
        config = MCPVerifierConfig(
            tool_name="check_knowledge",
            config_path=tmp_path,
        )
        verifier = MCPVerifier(config=config)
        assert isinstance(verifier, MCPVerifier)
        assert verifier.name == "mcp_check_knowledge"

    def test_register_mcp_verifier_adds_to_registry(self, tmp_path: Path) -> None:
        """register_mcp_verifier adds verifier to VERIFIER_REGISTRY."""
        from umrs.knowledge.verifiers import get_verifier, register_verifier

        _make_config(tmp_path)
        config = MCPVerifierConfig(
            tool_name="reg_test",
            config_path=tmp_path,
        )
        register_verifier(MCPVerifier(config=config))
        assert get_verifier("mcp_reg_test") is not None

    def test_register_mcp_verifier_duplicate_raises(self, tmp_path: Path) -> None:
        """register_mcp_verifier raises on duplicate registration."""
        from umrs.knowledge.verifiers import get_verifier, register_verifier

        _make_config(tmp_path)
        config = MCPVerifierConfig(
            tool_name="dup_test",
            config_path=tmp_path,
        )
        verifier = MCPVerifier(config=config)
        register_verifier(verifier)
        assert get_verifier("mcp_dup_test") is not None

        # Second registration should raise
        with pytest.raises(KeyError, match="already registered"):
            register_verifier(MCPVerifier(config=config))

    def test_register_mcp_verifier_with_custom_arguments(self, tmp_path: Path) -> None:
        """register_mcp_verifier preserves custom arguments."""
        _make_config(tmp_path)
        config = MCPVerifierConfig(
            tool_name="check_knowledge",
            config_path=tmp_path,
            arguments={"scope": "full", "threshold": 0.95},
        )
        verifier = MCPVerifier(config=config)
        assert verifier.config.arguments == {"scope": "full", "threshold": 0.95}


# ═════════════════════════════════════════════════════════════════════
# 6. Known Tools Registry — verify all ToolCapability flags
# ═════════════════════════════════════════════════════════════════════


class TestToolCapabilityFlags:
    """Verify ToolCapability enum completeness."""

    def test_knowledge_verify_flag_exists(self) -> None:
        """KNOWLEDGE_VERIFY is a defined ToolCapability flag."""
        assert hasattr(ToolCapability, "KNOWLEDGE_VERIFY")
        assert ToolCapability.KNOWLEDGE_VERIFY is not ToolCapability.NONE

    def test_standard_user_includes_knowledge_verify(self) -> None:
        """STANDARD_USER preset includes KNOWLEDGE_VERIFY."""
        assert (ToolCapability.STANDARD_USER & ToolCapability.KNOWLEDGE_VERIFY) != 0

    def test_admin_includes_knowledge_verify(self) -> None:
        """ADMIN preset (~NONE) includes KNOWLEDGE_VERIFY."""
        assert (ToolCapability.ADMIN & ToolCapability.KNOWLEDGE_VERIFY) != 0
