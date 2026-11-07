"""G14 (2026-10-29): persistent, source-tracked permission rules —
deny-first precedence, content prefixes, project-file tightening only,
dangerous-path fence, ladder integration."""

from __future__ import annotations

import json

import pytest

from r4.companion.permission_rules import (
    PermissionRule,
    RuleSet,
    dangerous_path_reason,
    load_permission_rules,
)
from r4.companion.permissions import PermissionManager, PermissionMode


def _rules(behavior: str, *entries: str, source: str = "user") -> list[PermissionRule]:
    parsed = []
    for raw in entries:
        tool = raw.split("(", 1)[0]
        prefix = raw[len(tool) + len("(prefix:") : -1] if "(prefix:" in raw else ""
        parsed.append(
            PermissionRule(behavior=behavior, tool_name=tool, prefix=prefix, source=source, raw=raw)
        )
    return parsed


def test_decision_is_deny_first_then_ask_then_allow() -> None:
    ruleset = RuleSet(
        _rules("allow", "shell_exec", source="user")
        + _rules("deny", "shell_exec(prefix:rm -rf)", source="project")
        + _rules("ask", "file_write", source="user")
    )
    assert ruleset.decision("shell_exec", {"command": "rm -rf /tmp/x"})[0] == "deny"
    assert ruleset.decision("shell_exec", {"command": "ls"})[0] == "allow"
    assert ruleset.decision("file_write", {"path": "x.py"})[0] == "ask"
    assert ruleset.decision("grep_search", {"pattern": "x"}) is None


def test_prefix_binds_to_the_primary_argument() -> None:
    (rule,) = _rules("allow", "shell_exec(prefix:pytest)")
    assert rule.matches("shell_exec", {"command": "pytest -q tests/"})
    assert not rule.matches("shell_exec", {"command": "npm publish pytest"})
    assert not rule.matches("file_write", {"path": "pytest.py"})


def test_project_allow_entries_are_ignored(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / ".r4"
    project.mkdir()
    (project / "permissions.json").write_text(
        json.dumps(
            {
                "allow": ["shell_exec"],
                "deny": ["shell_exec(prefix:sudo)"],
                "ask": ["file_edit"],
            }
        )
    )
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "homeless"))
    merged = load_permission_rules(str(tmp_path))
    names = {(r.behavior, r.tool_name, r.prefix) for r in merged.rules}
    assert ("allow", "shell_exec", "") not in names
    assert ("deny", "shell_exec", "sudo") in names
    assert ("ask", "file_edit", "") in names


def test_user_file_allows_are_honored_and_sources_named(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / ".config" / "r4"
    home.mkdir(parents=True)
    (home / "permissions.json").write_text(json.dumps({"allow": ["grep_search"]}))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    merged = load_permission_rules(None)
    behavior, rule = merged.decision("grep_search", {"pattern": "x"})
    assert behavior == "allow"
    assert rule.source == "user"


def test_dangerous_paths_always_ask() -> None:
    assert dangerous_path_reason("file_write", {"path": ".git/hooks/post-commit"})
    assert dangerous_path_reason("file_edit", {"path": "/home/u/.bashrc"})
    assert dangerous_path_reason("file_write", {"path": "/root/.ssh/config"})
    assert dangerous_path_reason("file_write", {"path": "src/main.py"}) is None
    assert dangerous_path_reason("file_read", {"path": ".git/config"}) is None
    assert dangerous_path_reason("shell_exec", {"command": "cat .git/config"}) is None


def test_apply_patch_paths_are_read_from_the_patch() -> None:
    """ADR-104 defect 6: the fence read a ``path`` argument the tool never takes."""
    codex = "*** Begin Patch\n*** Add File: app/.r4/hooks.json\n+{}\n*** End Patch\n"
    assert dangerous_path_reason("apply_patch", {"patch_text": codex})
    diff = "--- a/.git/hooks/pre-push\n+++ b/.git/hooks/pre-push\n@@ -1 +1 @@\n-a\n+b\n"
    assert dangerous_path_reason("apply_patch", {"patch_text": diff})
    assert dangerous_path_reason("apply_patch", {"patch_text": "not a patch"}) is None


def test_ladder_integration_deny_allow_ask() -> None:
    manager = PermissionManager(mode=PermissionMode.AUTO_SAFE)
    manager.ruleset = RuleSet(
        _rules("deny", "file_read")
        + _rules("allow", "shell_exec(prefix:pytest)")
        + _rules("ask", "apply_patch")
    )
    assert manager.check("file_read", {"path": "x"}) is False
    assert manager.check("shell_exec", {"command": "pytest -q"}) is True
    asked: list[str] = []
    manager.set_approval_callback(lambda tool, _desc, _args: asked.append(tool) or True)
    assert manager.check("apply_patch", {"patch_text": "diff"}) is True
    assert asked == ["apply_patch"]
    # Unmatched tools fall through to the legacy ladder (SAFE auto-approved).
    assert manager.check("list_dir", {"path": "."}) is True


def test_dangerous_path_asks_over_a_session_always() -> None:
    manager = PermissionManager(mode=PermissionMode.AUTO_SAFE)
    seen: list[str] = []
    manager.set_approval_callback(lambda tool, desc, _args: seen.append(desc) or False)
    manager.approve_for_session("file_write")
    result = manager.check("file_write", {"path": "/home/u/.zshrc", "content": "x"})
    assert result is False
    assert seen and "protected path" in seen[0]


def test_autopilot_writes_a_protected_path_and_notes_it() -> None:
    """ADR-104 amendment 2 (maintainer, 2026-11-05): only credential access
    and deletes without a backup still ask in autopilot."""
    from r4.companion.permissions import take_permission_notes

    manager = PermissionManager(mode=PermissionMode.TRUST)
    seen: list[str] = []
    manager.set_approval_callback(lambda tool, desc, _args: seen.append(desc) or False)
    assert manager.check("file_write", {"path": "/home/u/.zshrc", "content": "x"}) is True
    assert seen == []
    assert any("protected path" in note for note in take_permission_notes())


def test_allow_rule_never_passes_a_protected_path() -> None:
    """ADR-104 defect 6: an allow rule was evaluated before the protected-path
    fence, so ``allow: [file_write]`` wrote .git/hooks without asking."""
    manager = PermissionManager(mode=PermissionMode.AUTO_SAFE)
    manager.ruleset = RuleSet(_rules("allow", "file_write"))
    asked: list[str] = []
    manager.set_approval_callback(lambda tool, _desc, _args: asked.append(tool) or False)
    assert manager.check("file_write", {"path": ".git/hooks/pre-commit"}) is False
    assert asked == ["file_write"]
    assert manager.check("file_write", {"path": "notes.md"}) is True


def test_wildcard_rules_match() -> None:
    """ADR-104 defect 6: ``mcp.*`` was accepted by the parser and never matched."""
    ruleset = RuleSet(_rules("deny", "mcp.*"))
    assert ruleset.decision("mcp.github.create_issue", {}) is not None
    assert ruleset.denies_tool("mcp.github.create_issue")
    assert ruleset.decision("shell_exec", {}) is None


def test_deny_rule_wins_over_never_auto_and_fences() -> None:
    manager = PermissionManager(mode=PermissionMode.TRUST)
    manager.ruleset = RuleSet(_rules("deny", "knowledge_graph_decide"))
    assert manager.decide("knowledge_graph_decide", {}).behavior == "deny"


def test_project_user_rules_load_and_legacy_list(tmp_path, monkeypatch) -> None:
    """ADR-104 D-3: the user's per-project file loads beside the user file;
    the console's older list format reads as allow. (Amendment 1: the
    approval card no longer writes it; /settings rules still prunes it.)"""
    import json

    from r4.companion.permission_rules import (
        load_permission_rules,
        remove_project_rule,
        user_project_rules_path,
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = user_project_rules_path("proj/1")
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps([{"tool": "file_write"}, {"tool": "x", "pattern": "*a*"}]))
    rules = load_permission_rules(None, project_id="proj/1")
    assert [(r.behavior, r.tool_name, r.source) for r in rules.rules] == [
        ("allow", "file_write", "user_project")
    ]
    assert load_permission_rules(None, project_id="other").rules == []
    legacy.write_text(json.dumps({"allow": ["file_write", "shell_exec"]}))
    assert remove_project_rule("proj/1", "allow", "file_write") is True
    assert json.loads(legacy.read_text()) == {"allow": ["shell_exec"]}
