"""Tests for COR-006 operational introspection (UMRS API + r4 CLI)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


def _load_ops_module():
    """Return umrs.api.ops via plain import.

    Phase 1 (ADR-053) emptied umrs.api.__init__ into a lazy __getattr__ shim
    and broke the jobs→orchestrator→jobs cycle, so the old spec-load stub
    machinery is obsolete: a normal import is now cycle-safe.
    """
    import umrs.api.ops

    return umrs.api.ops


# ---------------------------------------------------------------------------
# UMRS ops module unit tests
# ---------------------------------------------------------------------------


class TestOpsHealthProbes:
    """Test the subsystem health probe functions."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.ops = _load_ops_module()

    def test_probe_job_store_unavailable(self):
        result = self.ops._probe_job_store(None)
        assert result.name == "job_store"
        assert result.status == "unavailable"

    def test_probe_job_store_healthy(self):
        mock_store = MagicMock()
        mock_store.list_jobs.return_value = []
        result = self.ops._probe_job_store(mock_store)
        assert result.status == "healthy"

    def test_probe_job_store_degraded(self):
        mock_store = MagicMock()
        mock_store.list_jobs.side_effect = Exception("connection refused")
        result = self.ops._probe_job_store(mock_store)
        assert result.status == "degraded"
        assert "connection refused" in result.detail

    def test_probe_scheduler_enabled(self):
        with patch.dict(os.environ, {"UMRS_SCHEDULER_ENABLED": "true"}):
            result = self.ops._probe_scheduler()
            assert result.status == "healthy"

    def test_probe_scheduler_disabled(self):
        with patch.dict(os.environ, {"UMRS_SCHEDULER_ENABLED": "false"}):
            result = self.ops._probe_scheduler()
            assert result.status == "unavailable"

    def test_probe_transport_http(self):
        with patch.dict(os.environ, {"UMRS_TRANSPORT": "http"}, clear=False):
            result = self.ops._probe_transport()
            assert result.status == "healthy"
            assert "http" in result.detail

    def test_probe_transport_tunnel(self):
        with patch.dict(
            os.environ, {"UMRS_TRANSPORT": "zmq", "UMRS_TUNNEL_MODE": "true"}, clear=False
        ):
            result = self.ops._probe_transport()
            assert "tunnel_mode=true" in result.detail


class TestOpsContext:
    """Test ops context management."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.ops = _load_ops_module()

    def test_set_ops_context(self):
        self.ops.set_ops_context("test-instance-123")
        assert self.ops._instance_id == "test-instance-123"

    def test_uptime_positive(self):
        self.ops.set_ops_context("test")
        assert self.ops._uptime() >= 0


# ---------------------------------------------------------------------------
# r4 CLI ops tests (registration and parsing)
# ---------------------------------------------------------------------------


class TestOpsCLI:
    """Test that ops CLI command is registered and parseable."""

    def test_command_registered(self):
        # Through the CLI's own loader (ADR-053: tests never import r4._cli
        # modules by hand) — the registry alone binds nothing.
        from r4 import cli

        assert "ops" in cli._load_dispatch_registry()

    def test_command_has_subcommands(self):
        from r4 import cli

        handler = cli._load_dispatch_registry().get("ops")
        assert handler.name == "ops"
        assert handler.help != ""


# ---------------------------------------------------------------------------
# UMRS ops API response model tests
# ---------------------------------------------------------------------------


class TestOpsResponseModels:
    """Test Pydantic response models for ops API."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.ops = _load_ops_module()

    def test_subsystem_health_model(self):
        h = self.ops.SubsystemHealth(name="test", status="healthy", detail="ok")
        assert h.name == "test"
        assert h.status == "healthy"

    def test_ops_health_response(self):
        resp = self.ops.OpsHealthResponse(
            instance_id="inst-1",
            uptime_seconds=120.5,
            subsystems=[
                self.ops.SubsystemHealth(name="job_store", status="healthy"),
            ],
            overall="healthy",
        )
        assert resp.overall == "healthy"
        assert len(resp.subsystems) == 1

    def test_topology_entry(self):
        entry = self.ops.TopologyEntry(
            component="gateway",
            role="policy_edge",
            status="configured",
            detail="mode=nginx",
        )
        assert entry.component == "gateway"

    def test_timeline_event(self):
        ev = self.ops.TimelineEvent(
            timestamp="2026-01-01T10:00:00",
            job_id="job-abc",
            job_type="endpoint_provision",
            event="created",
        )
        assert ev.event == "created"


# ---------------------------------------------------------------------------
# Security: admin-only enforcement on ops endpoints
# ---------------------------------------------------------------------------


_OPS_SECRET = "ops-introspection-secret"


def _signed(user: str, project: str, admin_header: str) -> dict[str, str]:
    """Gateway-signed header kwargs for require_ops_admin.

    H-1 item 7 (2026-10-17): require_ops_admin trusted X-Verified-Is-Admin
    without the gateway HMAC; strict mode now verifies the signature (admin
    flag included), so the header-path tests sign their identity tuple.
    """
    from r4_contracts.handshake_hmac import sign_claims

    admin = admin_header.strip().lower() in ("true", "1")
    return {
        "x_user_id": user,
        "x_verified_project_id": project,
        "x_verified_is_admin": admin_header,
        "x_verified_hmac": sign_claims(_OPS_SECRET, user, project, admin),
    }


class TestOpsAdminEnforcement:
    """Verify ops endpoints reject non-admin callers with 403."""

    @pytest.fixture(autouse=True)
    def _load(self, monkeypatch):
        monkeypatch.setenv("UMRS_HANDSHAKE_HMAC_SECRET", _OPS_SECRET)
        self.ops = _load_ops_module()

    def test_require_ops_admin_rejects_non_admin_headers(self):
        """Non-admin caller via Gateway headers -> 403."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            self.ops.require_ops_admin(**_signed("alice", "proj-1", "false"))
        assert exc_info.value.status_code == 403
        assert "admin" in exc_info.value.detail.lower()

    def test_require_ops_admin_accepts_admin_headers(self):
        """Admin caller via Gateway headers -> allowed."""
        result = self.ops.require_ops_admin(**_signed("admin-user", "proj-1", "true"))
        assert result is None  # No exception

    def test_require_ops_admin_rejects_unsigned_admin_headers(self):
        """H-1 item 7: an unsigned (or flag-flipped) admin header is a 401."""
        from fastapi import HTTPException

        forged = _signed("alice", "proj-1", "false")
        forged["x_verified_is_admin"] = "true"
        for kwargs in (forged, {**forged, "x_verified_hmac": None}):
            with pytest.raises(HTTPException) as exc_info:
                self.ops.require_ops_admin(**kwargs)
            assert exc_info.value.status_code == 401

    def test_require_ops_admin_rejects_non_admin_query_param(self):
        """Non-admin caller via query params (MVP mode) -> 403.

        UMRS_AUTH_MODE must be set explicitly: the unset default is ``strict``
        (security audit R-2), where this legacy path 401s before it is reached.
        """
        from fastapi import HTTPException

        with patch.dict(os.environ, {"UMRS_AUTH_MODE": "mvp"}):
            with pytest.raises(HTTPException) as exc_info:
                self.ops.require_ops_admin(
                    x_user_id=None,
                    x_verified_project_id=None,
                    x_verified_is_admin=None,
                    caller_user_id="bob",
                    caller_project_id="proj-1",
                    is_admin=False,
                )
            assert exc_info.value.status_code == 403

    def test_require_ops_admin_accepts_admin_query_param(self):
        """Admin caller via query params (MVP mode) -> allowed."""
        with patch.dict(os.environ, {"UMRS_AUTH_MODE": "mvp"}):
            result = self.ops.require_ops_admin(
                x_user_id=None,
                x_verified_project_id=None,
                x_verified_is_admin=None,
                caller_user_id="admin",
                caller_project_id="proj-1",
                is_admin=True,
            )
        assert result is None

    def test_require_ops_admin_default_mode_is_strict(self):
        """Unset UMRS_AUTH_MODE must behave as strict: query params never auth."""
        from fastapi import HTTPException

        env = {k: v for k, v in os.environ.items() if k != "UMRS_AUTH_MODE"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(HTTPException) as exc_info:
                self.ops.require_ops_admin(
                    x_user_id=None,
                    x_verified_project_id=None,
                    x_verified_is_admin=None,
                    caller_user_id="admin",
                    caller_project_id="proj-1",
                    is_admin=True,
                )
            assert exc_info.value.status_code == 401

    def test_require_ops_admin_strict_mode_rejects_no_headers(self):
        """Strict auth mode with no headers -> 401."""
        from fastapi import HTTPException

        with patch.dict(os.environ, {"UMRS_AUTH_MODE": "strict"}):
            with pytest.raises(HTTPException) as exc_info:
                self.ops.require_ops_admin(
                    x_user_id=None,
                    x_verified_project_id=None,
                    x_verified_is_admin=None,
                    caller_user_id="alice",
                    caller_project_id="proj-1",
                    is_admin=True,  # ignored in strict mode
                )
            assert exc_info.value.status_code == 401

    def test_require_ops_admin_accepts_header_value_1(self):
        """x_verified_is_admin='1' should be accepted as admin."""
        result = self.ops.require_ops_admin(**_signed("admin-user", "proj-1", "1"))
        assert result is None

    def test_require_ops_admin_rejects_empty_admin_header(self):
        """x_verified_is_admin='' should NOT be treated as admin."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            self.ops.require_ops_admin(**_signed("alice", "proj-1", ""))
        assert exc_info.value.status_code == 403
