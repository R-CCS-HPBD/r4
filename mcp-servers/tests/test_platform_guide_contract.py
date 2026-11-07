"""Spine-scoped MCP description drift tests for PlatformServer."""

import asyncio

from r4_contracts.agent_tool_playbook import (
    AGENT_TOOL_PLAYBOOK,
    PLATFORM_GUIDE_TOOL_DESCRIPTION,
    platform_guide,
)
from r4_contracts.workflows import WORKFLOW_TOOL_HELP

from r4_mcp.servers.platform import PlatformServer

# ADR-105 D-2 (S5): generated workflow tools lead with the contract text and
# append the UMRS detail plus risk/taint; the hand-written residual keeps it verbatim.
GENERATED_WORKFLOW_OPS = ("list", "describe", "run", "status", "results", "cancel")


def test_generated_workflow_tools_lead_with_the_contract_help():
    by_name = {tool.name: tool for tool in PlatformServer().tools}
    for op in GENERATED_WORKFLOW_OPS:
        help_text = WORKFLOW_TOOL_HELP[op]
        assert by_name[f"workflow_{op}"].description.startswith(help_text + "\n\n")


def test_mcp_spine_tool_descriptions_wire_contracts():
    server = PlatformServer()
    by_name = {tool.name: tool for tool in server.tools}

    assert not [n for n in by_name if n.startswith("project_research_")]  # retired (WP-07)

    residual = set(WORKFLOW_TOOL_HELP) - set(GENERATED_WORKFLOW_OPS)
    for op in residual:
        assert by_name[f"workflow_{op}"].description is WORKFLOW_TOOL_HELP[op]

    assert by_name["workflow_trigger"].description is WORKFLOW_TOOL_HELP["run"]
    assert by_name["platform_guide"].description is PLATFORM_GUIDE_TOOL_DESCRIPTION


def test_mcp_platform_guide_returns_contract_playbook():
    server = PlatformServer()
    result = asyncio.run(server.platform_guide_tool(topic="all"))
    assert not result.is_error
    assert result.content == AGENT_TOOL_PLAYBOOK
    assert result.content == platform_guide("all")


def test_mcp_platform_guide_section():
    server = PlatformServer()
    result = asyncio.run(server.platform_guide_tool(topic="loops"))
    assert not result.is_error
    assert result.content == platform_guide("loops")
