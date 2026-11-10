"""ADR-035 wave T3c: server trust allowlist wiring on the client side.

Covers PermissionManager._server_trusted + the session-level wiring that
attaches the checker when (and only when) the mode is TRUST.
"""

from __future__ import annotations

import pytest

from r4.companion.permissions import PermissionManager, PermissionMode


def _perm(mode: PermissionMode) -> PermissionManager:
    return PermissionManager(mode=mode)


class TestServerTrusted:
    def test_trust_mode_without_checker_keeps_legacy_behavior(self):
        assert _perm(PermissionMode.TRUST).check("shell_exec", {}) is True

    def test_trust_mode_with_checker_allows_server_rows(self):
        pm = _perm(PermissionMode.TRUST)
        pm.set_trust_checker(lambda tool: tool == "shell_exec")
        assert pm.check("shell_exec", {}) is True
        # amendment 3: a policy miss still asks in the decision, then autopilot answers.
        assert pm.check("hpc_submit", {}) is True

    def test_checker_exception_does_not_stop_autopilot(self):
        pm = _perm(PermissionMode.TRUST)

        def boom(tool: str) -> bool:
            raise RuntimeError("server down")

        pm.set_trust_checker(boom)
        assert pm.check("shell_exec", {}) is True

    def test_positive_results_cached(self):
        calls: list[str] = []
        pm = _perm(PermissionMode.TRUST)

        def checker(tool: str) -> bool:
            calls.append(tool)
            return True

        pm.set_trust_checker(checker)
        assert pm.check("shell_exec", {}) is True
        assert pm.check("shell_exec", {}) is True
        assert calls == ["shell_exec"]  # second hit served from cache

    def test_set_trust_checker_none_restores_legacy(self):
        pm = _perm(PermissionMode.TRUST)
        pm.set_trust_checker(lambda tool: False)
        assert pm.check("shell_exec", {}) is True
        pm.set_trust_checker(None)
        assert pm.check("shell_exec", {}) is True


class TestDurableApprovalRecorder:
    """Item 8: 'always' decisions recorded server-side."""

    def test_approve_for_session_fires_recorder(self):
        calls: list[tuple[str, str]] = []
        pm = _perm(PermissionMode.TRUST)
        pm.set_approval_recorder(lambda tool, scope: calls.append((tool, scope)))
        pm.approve_for_session("shell_exec")
        assert calls == [("shell_exec", "system")]

    def test_recorder_exception_never_breaks_local_grant(self):
        pm = _perm(PermissionMode.TRUST)

        def boom(tool: str, scope: str) -> None:
            raise RuntimeError("server down")

        pm.set_approval_recorder(boom)
        pm.approve_for_session("shell_exec")
        assert "shell_exec" in pm.session_approved_names

    def test_without_recorder_grant_still_works(self):
        pm = _perm(PermissionMode.TRUST)
        pm.approve_for_session("shell_exec")
        assert "shell_exec" in pm.session_approved_names

    def test_never_auto_tools_still_pinned(self):
        pm = _perm(PermissionMode.TRUST)
        pm.set_trust_checker(lambda tool: True)
        # The never-auto rung classifies it; amendment 3 answers locally.
        assert pm.check("knowledge_graph_decide", {"proposal_id": "x"}) is True
        assert "--" not in repr(pm)

    def test_non_trust_modes_unaffected(self):
        pm = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
        pm.set_trust_checker(lambda tool: True)
        assert pm.check("shell_exec", {}) is False  # still asks (and denies sans callback)


class TestTrustSourceBadge:
    """ADR-035 badge honesty: how enforcement is derived in TRUST mode."""

    def test_construction_without_checker_reports_local(self):
        """No checker + TRUST = local autopilot (the default), never 'server'."""
        assert PermissionManager(mode=PermissionMode.TRUST).trust_source == "local"

    def test_checker_attached_reports_server(self):
        pm = PermissionManager(mode=PermissionMode.TRUST)
        pm.set_trust_checker(lambda tool: True)
        assert pm.trust_source == "server"

    def test_checker_removed_reports_local(self):
        pm = PermissionManager(mode=PermissionMode.TRUST)
        pm.set_trust_checker(lambda tool: True)
        pm.set_trust_checker(None)
        assert pm.trust_source == "local"

    def test_non_trust_mode_reports_empty(self):
        pm = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
        pm.set_trust_checker(lambda tool: True)
        assert pm.trust_source == ""

    def test_set_mode_away_from_trust_clears_source(self):
        pm = PermissionManager(mode=PermissionMode.TRUST)
        pm.set_trust_checker(lambda tool: True)
        pm.set_mode(PermissionMode.ASK_ALWAYS)
        assert pm.trust_source == ""

    def test_set_mode_to_trust_without_checker_marks_local(self):
        pm = PermissionManager(mode=PermissionMode.ASK_ALWAYS)
        pm.set_mode(PermissionMode.TRUST)
        assert pm.trust_source == "local"


class TestSessionWiring:
    """_wire_trust_checker exercised on a stub session (no full CompanionSession
    — its constructor pulls the agent/LLM stack, out of unit-test scope)."""

    @staticmethod
    def _stub(mode: PermissionMode) -> object:
        from r4.companion.session import CompanionSession

        class _Stub:
            permissions = PermissionManager(mode=mode)

        _Stub.__init__ = lambda self: None  # noqa: E731
        stub = _Stub()
        # bind the real method
        stub._wire_trust_checker = CompanionSession._wire_trust_checker.__get__(stub)
        return stub

    class _FakeClientWithTrust:
        def __init__(self):
            self.checked: list[tuple[str, str]] = []

        def trust_check(self, tool_name: str, scope: str = "", **kwargs):
            self.checked.append((tool_name, scope))
            return {"allowed": tool_name == "shell_exec"}

    class _FakeClientNoTrust:
        pass

    @pytest.fixture()
    def policy_on(self, monkeypatch):
        monkeypatch.setenv("R4_AUTOPILOT_SERVER_POLICY", "1")

    def test_policy_off_by_default_never_wires(self, monkeypatch):
        """ADR-104 amendment 1: autopilot is not narrowed by the server
        unless the organisation policy is switched on."""
        monkeypatch.delenv("R4_AUTOPILOT_SERVER_POLICY", raising=False)
        stub = self._stub(PermissionMode.TRUST)
        client = self._FakeClientWithTrust()
        stub._wire_trust_checker(client, PermissionMode.TRUST)
        assert stub.permissions._trust_checker is None
        assert stub.permissions.check("file_edit", {"path": "x"}) is True
        assert client.checked == []

    def test_trust_mode_wires_checker(self, policy_on):
        stub = self._stub(PermissionMode.TRUST)
        client = self._FakeClientWithTrust()
        stub._wire_trust_checker(client, PermissionMode.TRUST)
        assert stub.permissions._trust_checker is not None
        assert stub.permissions.check("shell_exec", {}) is True
        assert client.checked == [("shell_exec", "")]

    def test_project_scoped_consult_when_the_session_has_a_project(self, policy_on):
        stub = self._stub(PermissionMode.TRUST)
        stub.metadata = {"project_id": "proj-7"}
        client = self._FakeClientWithTrust()
        stub._wire_trust_checker(client, PermissionMode.TRUST)
        stub.permissions.check("shell_exec", {})
        assert client.checked == [("shell_exec", "project:proj-7")]

    def test_policy_never_asks_for_a_read_only_tool(self, policy_on):
        """ADR-104 amendment 1: the server check came before the read-only
        rung, so autopilot(server) asked for file_read while chat ran it."""
        stub = self._stub(PermissionMode.TRUST)
        client = self._FakeClientWithTrust()
        stub._wire_trust_checker(client, PermissionMode.TRUST)
        assert stub.permissions.decide("file_read", {"path": "x"}).behavior == "allow"
        assert client.checked == []

    def test_a_session_always_runs_before_the_policy(self, policy_on):
        stub = self._stub(PermissionMode.TRUST)
        client = self._FakeClientWithTrust()
        stub._wire_trust_checker(client, PermissionMode.TRUST)
        assert stub.permissions.decide("file_edit", {"path": "x"}).behavior == "ask"
        stub.permissions.approve_for_session("file_edit")
        assert stub.permissions.decide("file_edit", {"path": "x"}).source == "session"

    def test_ask_mode_does_not_wire(self):
        stub = self._stub(PermissionMode.ASK_ALWAYS)
        stub._wire_trust_checker(self._FakeClientWithTrust(), PermissionMode.ASK_ALWAYS)
        assert stub.permissions._trust_checker is None

    def test_client_without_trust_check_skips_wiring(self):
        stub = self._stub(PermissionMode.TRUST)
        stub._wire_trust_checker(self._FakeClientNoTrust(), PermissionMode.TRUST)
        # falls back to legacy process-global TRUST
        assert stub.permissions._trust_checker is None
        assert stub.permissions.check("shell_exec", {}) is True

    def test_none_client_is_noop(self):
        stub = self._stub(PermissionMode.TRUST)
        stub._wire_trust_checker(None, PermissionMode.TRUST)
        assert stub.permissions._trust_checker is None

    def test_wired_session_badge_reports_server(self, policy_on):
        stub = self._stub(PermissionMode.TRUST)
        stub._wire_trust_checker(self._FakeClientWithTrust(), PermissionMode.TRUST)
        assert stub.permissions.trust_source == "server"


class TestBridgeArchiveTrust:
    """Item 8: archiving a session converges on the server archive endpoint.
    (ADR-104 amendment 1: an "always" no longer writes a session trust row;
    rows written before it still die with the session through this path.)"""

    @staticmethod
    def _bridge_stub(session_id: str = "sess-5"):
        from types import SimpleNamespace

        from r4.companion.ui.bridge_server import CompanionBridgeServer

        return SimpleNamespace(
            session=SimpleNamespace(client=None),
            _client=None,
            _session_id_str=lambda: session_id,
            session_archive=CompanionBridgeServer.session_archive,
        )

    def test_archive_revokes_session_scoped_trust(self):
        """Item 8: bridge archive converges on the SERVER archive endpoint
        (tombstone + cascade + audit in one tx) — not a client-side revoke."""

        class _TrustClient:
            def __init__(self):
                self.archived: list[str] = []

            def companion_session_archive(self, session_id: str, **kwargs):
                self.archived.append(session_id)
                return {"session_id": session_id, "archived": True, "revoked_count": 2}

        client = _TrustClient()
        bridge = self._bridge_stub("sess-5")
        store = type("Store", (), {"archive": staticmethod(lambda sid: True)})()
        bridge._store = store
        bridge._client = client
        result = bridge.session_archive(bridge, {"session_id": "sess-5"})
        assert result["archived"] is True
        assert client.archived == ["sess-5"]

    def test_archive_server_unreachable_never_blocks_local_archive(self):
        class _Down:
            def companion_session_archive(self, *a, **k):
                raise ConnectionError("down")

        bridge = self._bridge_stub("sess-5")
        store = type("Store", (), {"archive": staticmethod(lambda sid: True)})()
        bridge._store = store
        bridge._client = _Down()
        # must not raise despite server being down (TTL bound applies)
        result = bridge.session_archive(bridge, {"session_id": "sess-5"})
        assert result["archived"] is True

    def test_archive_without_trust_client_is_noop(self):
        bridge = self._bridge_stub("sess-5")
        store = type("Store", (), {"archive": staticmethod(lambda sid: True)})()
        bridge._store = store
        result = bridge.session_archive(bridge, {"session_id": "sess-5"})
        assert result["archived"] is True
