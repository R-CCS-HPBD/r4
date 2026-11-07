from __future__ import annotations

from r4_mcp.chain_scanner import (
    ChainRiskLevel,
    ChainScannerConfig,
    ToolChainContext,
    ToolChainScanner,
)


def test_scan_output_blocks_configured_pattern() -> None:
    scanner = ToolChainScanner(
        ChainScannerConfig(blocked_output_patterns=["do-not-forward"]),
    )

    result = scanner.scan_output(
        tool_output="normal text with do-not-forward marker",
        source_tool="producer",
    )

    assert result.is_blocked is True
    assert result.risk_level is ChainRiskLevel.BLOCKED
    assert result.risk_score == 1.0
    assert result.block_reasons == ["Blocked pattern: do-not-forward"]


def test_scan_output_masks_credentials_and_records_context() -> None:
    scanner = ToolChainScanner()
    context = ToolChainContext(chain_id="chain-1")

    result = scanner.scan_output(
        tool_output="api_key=sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        source_tool="secret_source",
        target_tool="downstream",
        context=context,
    )

    credential_matches = [match for match in result.matches if match.category == "credential"]
    assert credential_matches
    assert credential_matches[0].matched_text.endswith("***")
    assert "aaaaaaaa" not in credential_matches[0].matched_text
    assert context.tool_history == ["secret_source"]
    assert result.metadata["chain_id"] == "chain-1"
    assert result.metadata["chain_depth"] == 1
