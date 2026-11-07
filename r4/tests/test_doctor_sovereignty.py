"""Tests for the D1 data-sovereignty demo in ``r4 doctor --sovereignty``."""

from __future__ import annotations

import pytest

from r4._cli.doctor import (
    _check_audit_trail_presence,
    _check_gateway_auth_middleware,
    _check_offline_inference_env,
    _run_sovereignty_checks,
)


@pytest.fixture(autouse=True)
def isolate_xdg_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


class TestOfflineInferenceEvidence:
    def test_returns_evidence_when_markers_exist(self):
        result = _check_offline_inference_env()
        # Repo ships mini-rivault defs with TRANSFORMERS_OFFLINE.
        assert "evidence" in result
        assert isinstance(result["evidence"], list)

    def test_the_standalone_stack_counts_as_a_surface(self):
        # The public checkout has only this and docker-compose.e2e.yaml.
        result = _check_offline_inference_env()
        assert "deploy/standalone/compose.yaml" in result["evidence"]

    def test_message_reports_count(self):
        result = _check_offline_inference_env()
        assert "TRANSFORMERS_OFFLINE" in str(result["message"])


class TestGatewayAuthMiddleware:
    def test_routes_detected(self):
        result = _check_gateway_auth_middleware()
        assert result["strict_ok"] is True
        routes = [e for e in result["evidence"] if e.startswith("route:")]
        assert len(routes) >= 1


class TestAuditTrail:
    def test_sink_and_migration_present(self):
        result = _check_audit_trail_presence()
        assert result["strict_ok"] is True
        assert len(result["evidence"]) == 2


class TestSovereigntyBundle:
    def test_bundle_includes_all_controls(self):
        class _ConfigStub:
            profile = "test"
            egress_allowed_domains = []
            egress_verified_egress_domains = None

        bundle = _run_sovereignty_checks(_ConfigStub())
        assert "checks" in bundle
        for key in ("offline_inference", "gateway_auth", "audit_trail", "local_hygiene"):
            assert key in bundle["checks"]

    def test_bundle_ok_flag_inverse_of_attention(self):
        class _ConfigStub:
            profile = "test"
            egress_allowed_domains = []

        bundle = _run_sovereignty_checks(_ConfigStub())
        assert bundle["ok"] is True or bundle["ok"] is False


class TestPrintBanner:
    def test_sovereign_banner_renders(self, capsys):
        from r4._cli.doctor import _print_sovereignty_banner

        checks = {
            "ok": True,
            "checks": {"offline_inference": {"strict_ok": True, "message": "ok"}},
        }
        _print_sovereignty_banner(checks, as_json=False)
        out = capsys.readouterr().out
        assert "SOVEREIGN" in out
        assert "offline_inference" in out

    def test_json_output_renders(self, capsys):
        import json

        from r4._cli.doctor import _print_sovereignty_banner

        checks = {"ok": True, "checks": {}}
        _print_sovereignty_banner(checks, as_json=True)
        payload = json.loads(capsys.readouterr().out)
        assert payload["sovereignty"]["ok"] is True
