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
