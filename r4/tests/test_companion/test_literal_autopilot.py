"""ADR-104 amendment 3: autopilot asks only for destructive file operations.

Backend authorization, sandboxing and explicit deny rules are not permission
prompts and deliberately keep their existing behaviour.
"""

from __future__ import annotations

import pytest
from r4_contracts.team_engine.plan import GATE_TOOL

from r4.companion.delete_guard import scan
from r4.companion.permission_rules import PermissionRule, RuleSet
from r4.companion.permissions import PermissionManager, PermissionMode, take_permission_notes
from r4.companion.taint import TaintRegistry


def _ask_rule(manager: PermissionManager, tool_name: str) -> None:
    manager.ruleset = RuleSet([PermissionRule("ask", tool_name, "", "user", tool_name)])


def _ever_tainted(manager: PermissionManager) -> None:
    registry = TaintRegistry()
    registry.ever_tainted = True
    manager.set_taint_registry(registry)


@pytest.mark.parametrize(
    ("tool_name", "arguments", "prepare"),
    [
        ("knowledge_graph_decide", {"proposal_id": "p", "approved": True}, None),
        ("research_loop_decide", {"approval_id": "a", "decision": "once"}, None),
        ("shell_exec", {"command": "cat ~/.ssh/id_ed25519"}, None),
        ("shell_exec", {"command": "git push --force origin main"}, None),
        ("file_write", {"path": "report.md", "content": "x"}, "ask"),
        ("memory_write", {"name": "fact", "text": "saved"}, "taint"),
        ("session_send", {"session_id": "other", "message": "hello"}, "taint"),
        ("hpc_submit", {"script": "hostname"}, "policy"),
    ],
)
def test_autopilot_auto_resolves_non_delete_asks(tool_name, arguments, prepare):
    asked: list[str] = []
    manager = PermissionManager(
        PermissionMode.TRUST,
        approval_callback=lambda tool, *_args: asked.append(tool) or False,
    )
    if prepare == "ask":
        _ask_rule(manager, tool_name)
    elif prepare == "taint":
        _ever_tainted(manager)
    elif prepare == "policy":
        manager.set_trust_checker(lambda _tool: False)

    assert manager.decide(tool_name, arguments).behavior == "ask"
    assert manager.check(tool_name, arguments) is True
    assert asked == []
    assert any("auto-answered" in note for note in take_permission_notes())


def test_autopilot_ignores_a_broken_policy_prompt_lane():
    def broken(_tool: str) -> bool:
        raise RuntimeError("policy unavailable")

    manager = PermissionManager(PermissionMode.TRUST, trust_checker=broken)
    assert manager.check("hpc_submit", {"script": "hostname"}) is True


def test_autopilot_does_not_hide_non_delete_tools_without_a_callback():
    manager = PermissionManager(PermissionMode.TRUST)
    assert manager.denies_tool("knowledge_graph_decide") is False
    assert manager.check("knowledge_graph_decide", {"proposal_id": "p"}) is True


def test_an_explicit_deny_rule_still_refuses_in_autopilot():
    manager = PermissionManager(PermissionMode.TRUST)
    manager.ruleset = RuleSet([PermissionRule("deny", "shell_exec", "", "user", "shell_exec")])
    assert manager.check("shell_exec", {"command": "hostname"}) is False


def test_a_merge_back_gate_recognises_exact_file_deletions():
    assert scan(GATE_TOOL, {"deletions": ["old.py"]}).paths == ("old.py",)
    assert not scan(GATE_TOOL, {"deletions": []}).deletes
