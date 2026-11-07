"""Shared slash catalog parity between bridge remote ops and Bun-only extras."""

from __future__ import annotations

from r4.companion.ui.bridge_server import (
    SURFACE_LOCAL_UI_COMMANDS,
    CompanionBridgeServer,
)

# Surface-local UI chrome — each TUI owns handlers; bridge catalog must exclude them.
# Bun also keeps palette as a local-only builtin (see slash.ts localRun).
BUN_LOCAL_ONLY_COMMANDS = SURFACE_LOCAL_UI_COMMANDS

# Bun-only /project lifecycle subcommands — must never appear in shared bridge metadata.
BUN_PROJECT_LIFECYCLE_SUBCOMMANDS = frozenset(
    {
        "cancel",
        "create",
        "current",
        "import",
        "init",
        "list",
        "plan",
        "readiness",
        "run",
        "status",
        "switch",
        "verify",
    }
)


def _remote_command_names(bridge: CompanionBridgeServer | None = None) -> set[str]:
    host = bridge or CompanionBridgeServer()
    result = host.list_slash_commands({})
    return {str(cmd["name"]) for cmd in result["commands"]}


def test_shared_remote_catalog_includes_core_ops() -> None:
    names = _remote_command_names()
    for required in ("endpoint", "workflow", "job", "mcp", "rag"):
        assert required in names


def test_shared_remote_catalog_excludes_bun_local_only_commands() -> None:
    names = _remote_command_names()
    assert BUN_LOCAL_ONLY_COMMANDS.isdisjoint(names)


def test_project_metadata_has_no_bun_lifecycle_subcommands() -> None:
    bridge = CompanionBridgeServer()
    result = bridge.list_slash_commands({})
    project = next((c for c in result["commands"] if c["name"] == "project"), None)
    if project is None:
        return
    subs = set(project.get("subcommands") or [])
    assert subs.isdisjoint(BUN_PROJECT_LIFECYCLE_SUBCOMMANDS)


def test_workflow_subcommands_match_shared_contract() -> None:
    bridge = CompanionBridgeServer()
    result = bridge.list_slash_commands({})
    workflow = next(c for c in result["commands"] if c["name"] == "workflow")
    assert set(workflow["subcommands"]) == {
        "list",
        "trigger",
        "upload",
        "delete",
        "status",
        "cancel",
    }


def test_ink_and_bun_share_identical_remote_command_names() -> None:
    """Ink and Bun both consume list_slash_commands for shared ops."""
    remote_names = _remote_command_names()
    assert "workflow" in remote_names
    assert "endpoint" in remote_names
    assert BUN_LOCAL_ONLY_COMMANDS.isdisjoint(remote_names)
