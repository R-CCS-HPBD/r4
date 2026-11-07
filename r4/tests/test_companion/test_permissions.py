"""Tests for PermissionManager."""

from __future__ import annotations

from r4.companion.permissions import PermissionManager, PermissionMode


def test_trust_mode_allows_all():
    pm = PermissionManager(mode=PermissionMode.TRUST)
    assert pm.check("shell_exec", {"command": "make deploy"})
    assert pm.check("file_read", {"path": "test.py"})


def test_trust_mode_never_deletes_what_it_cannot_back_up():
    """ADR-104 amendment 2: the one autopilot ask (no callback here: deny)."""
    pm = PermissionManager(mode=PermissionMode.TRUST)
    assert not pm.check("shell_exec", {"command": "rm -rf /"})


def test_deny_mode_blocks_all():
    pm = PermissionManager(mode=PermissionMode.DENY)
    assert not pm.check("file_read", {"path": "test.py"})
    assert not pm.check("shell_exec", {"command": "ls"})


def test_auto_safe_allows_safe():
    pm = PermissionManager(mode=PermissionMode.AUTO_SAFE)
    assert pm.check("file_read", {"path": "test.py"})
    assert pm.check("list_dir", {"path": "."})
    assert pm.check("search_docs", {"query": "test"})
    assert pm.check("detect_code_context", {"path": "."})
    assert pm.check("detect_language_servers", {"path": "."})


def test_auto_safe_denies_risky_without_callback():
    pm = PermissionManager(mode=PermissionMode.AUTO_SAFE)
    # No callback → deny
    assert not pm.check("shell_exec", {"command": "ls"})
    assert not pm.check("compile_code", {"path": "."})
    assert not pm.check("run_code", {"path": "."})
    assert not pm.check("test_code", {"path": "."})
    assert not pm.check("start_language_servers", {"path": "."})


def test_auto_safe_with_callback():
    pm = PermissionManager(
        mode=PermissionMode.AUTO_SAFE,
        approval_callback=lambda name, desc, args: True,
    )
    assert pm.check("shell_exec", {"command": "ls"})


def test_ask_always_denies_without_callback():
    pm = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
    # Even safe tools need approval
    assert not pm.check("file_read", {"path": "test.py"})


def test_ask_always_with_callback():
    calls = []

    def cb(name, desc, args):
        calls.append(name)
        return True

    pm = PermissionManager(mode=PermissionMode.ASK_ALWAYS, approval_callback=cb)
    assert pm.check("shell_exec", {"command": "ls"})
    assert "shell_exec" in calls


def test_session_approval():
    pm = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
    pm.approve_for_session("shell_exec")
    assert pm.check("shell_exec", {"command": "anything"})


def test_classify():
    pm = PermissionManager()
    assert pm.classify("file_read") == "safe"
    assert pm.classify("detect_code_context") == "safe"
    assert pm.classify("detect_language_servers") == "safe"
    assert pm.classify("shell_exec") == "risky"
    assert pm.classify("compile_code") == "risky"
    assert pm.classify("start_language_servers") == "risky"
    assert pm.classify("unknown_tool") == "unknown"


def test_reset_session():
    pm = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
    pm.approve_for_session("shell_exec")
    assert pm.check("shell_exec", {})
    pm.reset_session()
    assert not pm.check("shell_exec", {})


# Round-6 review §2.1 — gate fails closed without a PermissionManager
# ---------------------------------------------------------------------------


def test_gate_fails_closed_when_permissions_unwired() -> None:
    """ResearchCompanion built without permission_manager must deny tool
    calls (the old `if not self._permissions or check(...)` shape let every
    tool through — fail-open)."""
    from unittest.mock import MagicMock

    from r4.companion.agent import ResearchCompanion

    client = MagicMock()
    client._config = MagicMock(model="m")
    agent = ResearchCompanion(client)
    assert agent._permissions is None
    prepared = agent._permission_gate("shell_exec", {"command": "ls"})
    assert prepared is not None
    assert prepared.error_result is not None
    assert (
        "no PermissionManager" in prepared.error_result
        or "permission_unwired" in prepared.error_result
    )


def test_gate_allows_when_wired_and_allowed() -> None:
    from unittest.mock import MagicMock

    from r4.companion.agent import ResearchCompanion

    client = MagicMock()
    client._config = MagicMock(model="m")
    mgr = PermissionManager(mode=PermissionMode.ASK_ALWAYS, approval_callback=lambda t, d, a: True)
    agent = ResearchCompanion(client, permission_manager=mgr)
    assert agent._permission_gate("shell_exec", {"command": "ls"}) is None


def test_require_explicit_approval_prefixes_fence_reason() -> None:
    """When a fence fires, the approval description names the fence class so
    the UI can show WHY autopilot could not auto-approve this call."""
    from r4.companion.permissions import PermissionManager, PermissionMode

    captured: list[tuple[str, str]] = []
    mgr = PermissionManager(
        mode=PermissionMode.TRUST,
        approval_callback=lambda tool, desc, args: captured.append((tool, desc)) or True,
    )
    mgr._require_explicit_approval(
        "shell_exec",
        {"command": "echo hi"},
        reason=(
            "arguments contain rag_chunk content from an "
            "untrusted tool result (ADR-039 taint fence)"
        ),
    )
    assert captured, "approval callback must fire"
    assert captured[0][1].startswith("[fence: arguments contain rag_chunk"), captured[0][1]
    second_line = captured[0][1].splitlines()[1]
    assert "echo hi" in captured[0][1] and second_line.startswith("Run shell command")

    captured.clear()
    mgr._require_explicit_approval("shell_exec", {"command": "echo hi"})
    assert not captured[0][1].startswith("[fence:"), captured[0][1]
