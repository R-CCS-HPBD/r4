"""final_output is auto-approvable in every permission mode (its own output channel).

Reported 2026-09-02: after approving file_write, the companion asked to approve
the final_output call too — which read as a dead stop to users.
"""

from r4.companion.permissions import SAFE_TOOLS, PermissionManager, PermissionMode


def _manager(mode: PermissionMode) -> PermissionManager:
    return PermissionManager(mode=mode)


class TestFinalOutputAutoApprove:
    def test_final_output_is_safe(self):
        assert "final_output" in SAFE_TOOLS

    def test_auto_safe_allows_final_output_without_callback(self):
        pm = _manager(PermissionMode.AUTO_SAFE)
        assert pm.check("final_output", {}) is True

    def test_ask_always_still_prompts_for_final_output(self):
        # ASK_ALWAYS is the user's explicit "ask me about everything" mode —
        # we do not weaken it; the report concerned AUTO_SAFE (the default).
        pm = _manager(PermissionMode.ASK_ALWAYS)
        called = []
        pm.set_approval_callback(lambda *_args: called.append(True) or True)
        assert pm.check("final_output", {}) is True
        assert len(called) == 1

    def test_trust_and_denied_rules_unchanged(self):
        assert _manager(PermissionMode.TRUST).check("final_output", {}) is True
        assert _manager(PermissionMode.DENY).check("final_output", {}) is False
