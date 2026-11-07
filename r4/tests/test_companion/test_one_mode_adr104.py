"""ADR-104: one session mode, one decision ladder, approvals the companion answers.

Each test here fails on the code before ADR-104 (defect numbers are the
ADR's Context list).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from r4.companion.agent_mode import (
    apply_mode,
    migrate_metadata,
    mode_of,
    normalize_mode,
    permission_for,
)
from r4.companion.approval_answers import approval_options, normalize_decision
from r4.companion.permission_rules import (
    RuleSet,
    load_permission_rules,
    user_project_rules_path,
)
from r4.companion.permissions import PermissionManager, PermissionMode
from r4.companion.ui.bridge_server import CompanionBridgeServer


def _session(metadata: dict | None = None) -> SimpleNamespace:
    wired: list[PermissionMode] = []
    session = SimpleNamespace(
        metadata=dict(metadata or {}),
        permissions=PermissionManager(PermissionMode.ASK_ALWAYS),
        _client_ref=None,
        wired=wired,
    )
    session._wire_trust_checker = lambda _client, mode: wired.append(mode)
    return session


class TestOneMode:
    @pytest.mark.parametrize(
        ("word", "mode"),
        [("ask", "chat"), ("agent", "autopilot"), ("auto", "autopilot"), ("trust", "autopilot")],
    )
    def test_aliases(self, word, mode):
        assert normalize_mode(word) == mode

    def test_unknown_word_names_no_mode(self):
        assert normalize_mode("bogus") is None
        with pytest.raises(ValueError, match="unknown mode"):
            apply_mode(_session(), "bogus")

    def test_permission_is_derived(self):
        assert permission_for("chat") is PermissionMode.AUTO_SAFE
        assert permission_for("plan") is PermissionMode.AUTO_SAFE
        assert permission_for("autopilot") is PermissionMode.TRUST

    def test_autopilot_wires_the_server_allowlist_on_every_path(self):
        """Defect 4: only construction and plan acceptance wired the checker."""
        session = _session()
        apply_mode(session, "autopilot")
        assert session.permissions.mode is PermissionMode.TRUST
        assert session.wired == [PermissionMode.TRUST]

    def test_leaving_plan_clears_the_pending_plan_unless_kept(self):
        session = _session({"interaction_mode": "plan", "plan_mode_state": {"x": 1}})
        apply_mode(session, "chat", keep_plan=True)
        assert "plan_mode_state" in session.metadata
        session.metadata["interaction_mode"] = "plan"
        apply_mode(session, "chat")
        assert "plan_mode_state" not in session.metadata

    @pytest.mark.parametrize(
        ("metadata", "mode"),
        [
            ({"permission_override": "trust"}, "autopilot"),
            ({"auto_approve_tools": True}, "autopilot"),
            ({"interaction_mode": "plan", "permission_override": "trust"}, "plan"),
            ({"permission_override": "ask_always"}, "chat"),
            ({"permission_override": "deny"}, "chat"),
            ({"permission_override": "garbage"}, "chat"),
        ],
    )
    def test_retired_keys_fold_into_the_mode(self, metadata, mode):
        assert migrate_metadata(metadata) == mode
        assert "permission_override" not in metadata
        assert "auto_approve_tools" not in metadata
        assert mode_of(metadata) == mode

    def test_a_switch_drops_the_retired_keys(self):
        """Defects 1-2: the sticky override outlived every switch."""
        session = _session({"interaction_mode": "chat", "permission_override": "ask_always"})
        apply_mode(session, "autopilot")
        assert session.permissions.mode is PermissionMode.TRUST
        apply_mode(session, "chat")
        assert session.permissions.mode is PermissionMode.AUTO_SAFE
        assert "permission_override" not in session.metadata


class TestAnswers:
    def test_options(self):
        # ADR-104 amendment 1: no "always for this project" on a card.
        assert approval_options("shell_exec", fenced=False) == ["once", "always", "reject"]
        assert approval_options("shell_exec", fenced=True) == ["once", "reject"]
        assert approval_options("knowledge_graph_decide", fenced=False) == ["once", "reject"]

    @pytest.mark.parametrize(
        ("raw", "options", "decision"),
        [
            ("yes", ["once", "reject"], "once"),
            ("no", ["once", "reject"], "reject"),
            ("always", ["once", "reject"], "once"),
            # an older console's project answer counts as this session's
            ("always_project", ["once", "always", "reject"], "always"),
            ("p", ["once", "always", "reject"], "always"),
            ("always_project", ["once", "reject"], "once"),
            ("whatever", ["once", "always", "reject"], "reject"),
            ("", ["once", "reject"], "reject"),
        ],
    )
    def test_normalize(self, raw, options, decision):
        assert normalize_decision(raw, options) == decision

    def test_always_on_a_never_auto_tool_grants_nothing(self):
        """Defect 9: the manager itself refuses, whatever a surface sends."""
        manager = PermissionManager(PermissionMode.TRUST)
        manager.approve_for_session("knowledge_graph_decide")
        assert manager.session_approved_names == frozenset()


@pytest.fixture()
def bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    server = CompanionBridgeServer.__new__(CompanionBridgeServer)
    server._pending_approvals = {}
    server._approval_seq = 0
    server._active_turn_cancel = None
    server._stdin_mux = None
    server._client = MagicMock()
    server._register_approval_intent = MagicMock(return_value="appr-1")
    server._decide_approval_intent = MagicMock()
    server._client.trust_grant = MagicMock()
    server._session_id_str = MagicMock(return_value="s1")
    permissions = PermissionManager(PermissionMode.AUTO_SAFE)
    permissions.ruleset = RuleSet([])
    server._session = SimpleNamespace(
        permissions=permissions, permission_project_id=lambda: "proj-1"
    )
    server.emitted = []
    server._emit_line = server.emitted.append
    return server


def _ask(server, answer: str, tool: str = "shell_exec") -> bool:
    server._await_approval = MagicMock(return_value=answer)
    return server._on_tool_approval(tool, f"Run {tool}", {"command": "make"})


def _legacy(server) -> dict:
    return [line for line in server.emitted if "approval_request" in line][-1]["approval_request"]


class TestBridgeAnswers:
    def test_the_ask_carries_its_options(self, bridge):
        _ask(bridge, "once")
        assert _legacy(bridge)["options"] == ["once", "always", "reject"]

    def test_an_old_project_answer_is_this_session_only(self, bridge):
        """ADR-104 amendment 1: one keypress no longer writes a durable,
        argument-blind project rule; an older console's p is a session yes."""
        assert _ask(bridge, "always_project") is True
        assert not user_project_rules_path("proj-1").exists()
        assert bridge._session.permissions.session_approved_names == frozenset({"shell_exec"})
        bridge._decide_approval_intent.assert_called_once_with("appr-1", "always")

    def test_always_is_the_session_only(self, bridge):
        assert _ask(bridge, "always") is True
        assert bridge._session.permissions.session_approved_names == frozenset({"shell_exec"})
        assert not user_project_rules_path("proj-1").exists()
        # no self-granted server trust row any more
        bridge._client.trust_grant.assert_not_called()

    def test_once_remembers_nothing(self, bridge):
        """Defect 5: the console's "yes" auto-answered every later call."""
        assert _ask(bridge, "yes") is True
        assert bridge._session.permissions.session_approved_names == frozenset()
        assert (
            bridge._session.permissions.decide("shell_exec", {"command": "rm -rf x"}).behavior
            == "ask"
        )

    def test_always_on_a_never_auto_ask_approves_this_call_only(self, bridge):
        assert _ask(bridge, "always", tool="knowledge_graph_decide") is True
        assert _legacy(bridge)["options"] == ["once", "reject"]
        assert bridge._session.permissions.session_approved_names == frozenset()
        bridge._client.trust_grant.assert_not_called()

    def test_an_unknown_word_rejects(self, bridge, monkeypatch):
        import r4.companion.ui.bridge_server as module

        noted: list[str] = []
        monkeypatch.setattr(module, "note_user_rejection", noted.append)
        assert _ask(bridge, "maybe") is False
        assert noted == ["shell_exec"]

    def test_rules_list_and_remove(self, bridge):
        path = user_project_rules_path("proj-1")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"allow": ["shell_exec"]}))
        bridge._session.permissions.ruleset = load_permission_rules(None, project_id="proj-1")
        listed = bridge.permission_rules_list({})
        assert listed["project_id"] == "proj-1"
        assert listed["rules"] == [
            {
                "index": 0,
                "behavior": "allow",
                "rule": "shell_exec",
                "source": "user_project",
                "removable": True,
            }
        ]
        assert bridge.permission_rules_remove({"index": 0})["rules"] == []
        assert json.loads(user_project_rules_path("proj-1").read_text()) == {"allow": []}
        with pytest.raises(ValueError, match="cannot be removed"):
            bridge.permission_rules_remove({"index": "abc"})


class TestLadder:
    def test_fork_grant_still_obeys_a_deny_rule(self):
        """Defect 7: a card grant skipped G14 deny rules."""
        from r4.companion.permission_rules import PermissionRule

        lead = PermissionManager(PermissionMode.ASK_ALWAYS)
        lead.ruleset = RuleSet([PermissionRule("deny", "file_read", "", "user", "file_read")])
        fork = lead.fork_for_agent("a", grants=["file_read"])
        assert fork.check("file_read", {"path": "x"}) is False

    def test_template_crew_does_not_skip_the_taint_fence(self, monkeypatch):
        """Defect 7: plan-card delegation returned before the taint fence."""
        import r4.companion.permissions as permissions

        monkeypatch.setattr(permissions, "plan_card_decides", lambda *_a: True)
        manager = PermissionManager(PermissionMode.AUTO_SAFE)
        manager.set_taint_registry(SimpleNamespace(is_tainted=lambda _args: "web"))
        decision = manager.decide("teamup_start", {"crew": "x"})
        assert (decision.behavior, decision.source, decision.fenced) == ("ask", "taint", True)

    def test_apply_patch_is_risky(self):
        manager = PermissionManager(PermissionMode.TRUST)
        assert manager.classify("apply_patch") == "risky"
        assert manager.decide("apply_patch", {"patch_text": ""}).escalate is True


class TestAutopilotRuns:
    """ADR-104 amendment 1: autopilot runs what the safety floor lets through."""

    def test_read_only_tools_run_in_autopilot_with_the_policy_on(self):
        manager = PermissionManager(PermissionMode.TRUST, trust_checker=lambda _tool: False)
        assert manager.decide("file_read", {"path": "x"}).behavior == "allow"
        assert manager.decide("list_dir", {"path": "."}).behavior == "allow"

    def test_a_session_always_runs_before_the_policy(self):
        manager = PermissionManager(PermissionMode.TRUST, trust_checker=lambda _tool: False)
        manager.approve_for_session("shell_exec")
        assert manager.decide("shell_exec", {"command": "make"}).source == "session"

    def test_policy_off_runs_risky_tools_and_badge_is_local(self):
        manager = PermissionManager(PermissionMode.TRUST)
        assert manager.decide("shell_exec", {"command": "make"}).behavior == "allow"
        assert manager.trust_source == "local"

    def test_the_floor_still_asks_in_autopilot(self):
        manager = PermissionManager(PermissionMode.TRUST)
        decision = manager.decide("shell_exec", {"command": "git push --force"})
        assert (decision.behavior, decision.fenced) == ("ask", True)
