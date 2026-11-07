"""Permission gate tests: knowledge_graph_decide is never auto-approved."""

from __future__ import annotations

from r4.companion.permissions import (
    NEVER_AUTO_APPROVE,
    PermissionManager,
    PermissionMode,
)


def _mgr(mode: PermissionMode, approved: bool = True) -> tuple[PermissionManager, list[str]]:
    prompts: list[str] = []

    def callback(tool_name: str, description: str, arguments: dict) -> bool:
        prompts.append(tool_name)
        return approved

    return PermissionManager(mode=mode, approval_callback=callback), prompts


class TestNeverAutoApprove:
    def test_decide_is_registered_never_auto(self):
        assert "knowledge_graph_decide" in NEVER_AUTO_APPROVE

    def test_trust_mode_still_prompts_for_decide(self):
        mgr, prompts = _mgr(PermissionMode.TRUST)
        assert (
            mgr.check("knowledge_graph_decide", {"proposal_id": "gp-1", "approved": True}) is True
        )
        assert prompts == ["knowledge_graph_decide"]  # prompt happened

    def test_trust_mode_deny_without_callback(self):
        mgr = PermissionManager(mode=PermissionMode.TRUST)
        # no approval callback → deny closed even in TRUST
        assert (
            mgr.check("knowledge_graph_decide", {"proposal_id": "gp-1", "approved": True}) is False
        )

    def test_session_approval_cannot_shortcircuit_decide(self):
        mgr, prompts = _mgr(PermissionMode.ASK_ALWAYS)
        mgr.approve_for_session("knowledge_graph_decide")
        mgr.check("knowledge_graph_decide", {"proposal_id": "gp-1", "approved": True})
        mgr.check("knowledge_graph_decide", {"proposal_id": "gp-2", "approved": False})
        assert prompts == ["knowledge_graph_decide", "knowledge_graph_decide"]

    def test_declined_decision_propagates(self):
        mgr, _ = _mgr(PermissionMode.ASK_ALWAYS, approved=False)
        assert (
            mgr.check("knowledge_graph_decide", {"proposal_id": "gp-1", "approved": True}) is False
        )

    def test_other_risky_tools_still_auto_approve_in_trust(self):
        mgr, prompts = _mgr(PermissionMode.TRUST)
        assert mgr.check("knowledge_ingest", {"files": ["x.json"]}) is True
        assert prompts == []  # TRUST auto-approves regular risky tools

    def test_propose_is_safe_in_auto_safe(self):
        mgr, prompts = _mgr(PermissionMode.AUTO_SAFE)
        assert mgr.check("knowledge_graph_propose", {"graph_id": "g-b"}) is True
        assert prompts == []

    def test_deny_mode_blocks_everything(self):
        mgr, _ = _mgr(PermissionMode.DENY)
        assert mgr.check("knowledge_graph_list", {}) is False
        assert mgr.check("knowledge_graph_decide", {"proposal_id": "p", "approved": True}) is False

    def test_query_and_list_are_safe(self):
        mgr, prompts = _mgr(PermissionMode.AUTO_SAFE)
        assert mgr.check("knowledge_graph_list", {}) is True
        assert mgr.check("knowledge_query", {"query": "x"}) is True
        assert prompts == []
