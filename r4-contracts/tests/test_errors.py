"""Tests for the platform error taxonomy (G-P12-5, WP-16)."""

from __future__ import annotations

import json

import pytest

from r4_contracts import (
    ERROR_CODE_HEADER,
    PlatformError,
    known_error_codes,
)


class TestConstruction:
    def test_round_trip(self):
        err = PlatformError(
            code="session.not_found", message="Session s-1 not found", http_status=404
        )
        payload = json.loads(json.dumps(err.to_dict()))
        assert PlatformError.from_dict(payload) == err

    def test_detail_carries(self):
        err = PlatformError.session(
            "forbidden", "Cannot revoke another user's session", session_id="s-9"
        )
        assert err.detail == {"session_id": "s-9"}
        assert err.http_status == 403

    @pytest.mark.parametrize(
        ("code",),
        [("badcode",), ("",), ("onlyone",)],
    )
    def test_unnamespaced_code_rejected(self, code):
        with pytest.raises(ValueError, match="namespaced"):
            PlatformError(code=code, message="x", http_status=400)

    @pytest.mark.parametrize(("status",), [(399,), (200,), (600,)])
    def test_non_error_status_rejected(self, status):
        with pytest.raises(ValueError, match="4xx/5xx"):
            PlatformError(code="x.y", message="m", http_status=status)


class TestFamilies:
    def test_named_constructors_use_registry(self):
        assert PlatformError.session("not_found", "m").http_status == 404
        assert PlatformError.session("expired", "m").http_status == 410
        assert PlatformError.auth("missing_header", "m").http_status == 401
        assert PlatformError.provider("unavailable", "m").http_status == 503
        assert PlatformError.presign("out_of_scope", "m").http_status == 403

    def test_provision_capped_is_a_429(self):
        # 2026-10-23: the failing re-provision cap refuses with Retry-After.
        assert PlatformError.endpoints("provision_capped", "m").http_status == 429

    def test_hpcaccess_family_is_registered(self):
        # ADR-112 (2026-11-08): the typed refusals of declared HPC access.
        from r4_contracts.errors import HPCACCESS_ERRORS

        expected = {
            "hpcaccess.mode_undeclared": 403,
            "hpcaccess.project_not_allowed": 403,
            "hpcaccess.not_enrolled": 403,
            "hpcaccess.allocation_not_linked": 403,
            "hpcaccess.allocation_not_at_site": 403,
            "hpcaccess.gate_refused": 403,
            "hpcaccess.gate_version": 409,
            "hpcaccess.signature_required": 428,
            "hpcaccess.person_required": 403,
            "hpcaccess.intent_invalid": 422,
            "hpcaccess.intent_used": 409,
            "hpcaccess.lane_not_gated": 403,
            "hpcaccess.grant_invalid": 422,
            "hpcaccess.grant_not_found": 404,
            "hpcaccess.budget_exceeded": 403,
            "hpcaccess.ledger_unavailable": 503,
        }
        assert expected == HPCACCESS_ERRORS
        codes = known_error_codes()
        assert {code: codes[code] for code in expected} == expected

    def test_hpcaccess_constructor_uses_registry(self):
        err = PlatformError.hpcaccess("project_not_allowed", "m", site="rccs-front")
        assert (err.code, err.http_status) == ("hpcaccess.project_not_allowed", 403)
        assert err.detail == {"site": "rccs-front"}
        assert PlatformError.hpcaccess("signature_required", "m").http_status == 428
        # Waves C and D (2026-11-09): grants, the budget and the ledger.
        assert PlatformError.hpcaccess("grant_invalid", "m", reason="bad_schema").http_status == 422
        assert PlatformError.hpcaccess("grant_not_found", "m").http_status == 404
        assert PlatformError.hpcaccess("budget_exceeded", "m").http_status == 403
        assert PlatformError.hpcaccess("ledger_unavailable", "m").http_status == 503
        with pytest.raises(KeyError):
            PlatformError.hpcaccess("made_up", "m")

    def test_unknown_family_member_fails_loud(self):
        with pytest.raises(KeyError):
            PlatformError.session("made_up", "m")

    def test_registry_is_append_only_shape(self):
        codes = known_error_codes()
        assert len(codes) == len(set(codes))
        assert all("." in c for c in codes)
        for fam in ("session", "auth", "provider", "presign"):
            assert any(c.startswith(fam + ".") for c in codes)


class TestHttpProjection:
    def test_parts_keep_body_and_ride_header(self):
        err = PlatformError(
            code="session.not_found", message="Session s-1 not found", http_status=404
        )
        parts = err.to_http_parts()
        # detail string UNCHANGED from the legacy stringly form
        assert parts["detail"] == "Session s-1 not found"
        assert parts["status_code"] == 404
        assert parts["headers"] == {ERROR_CODE_HEADER: "session.not_found"}
