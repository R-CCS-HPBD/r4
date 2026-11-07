"""Tests for the enforcement matrix validator."""

from __future__ import annotations

from pathlib import Path

from tools.enforcement_matrix_validator import (
    ALLOWED_AUTHZ,
    ENFORCEABLE_AUTHZ,
    Finding,
    _load_canary_ids,
    format_json,
    load_matrix,
    main,
    validate_matrix_data,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = REPO_ROOT / "tools" / "enforcement_matrix.yaml"


def _row(**overrides: object) -> dict:
    row: dict[str, object] = {
        "id": "x",
        "path_prefix": "/umrs/x",
        "methods": ["GET", "POST"],
        "mutating": True,
        "gateway_auth": "auth_request",
        "umrs_authz": "verified-identity",
        "ux_gate": "none",
        "notes": "n",
        "review": "2026-09-02",
    }
    row.update(overrides)
    return row


def _matrix(*rows: dict) -> dict:
    base = [
        _row(
            id="platform-default",
            path_prefix="/umrs",
            umrs_authz="verified-identity",
        )
    ]
    return {"version": 1, "rows": list(rows) + base}


def test_default_matrix_validates_without_findings() -> None:
    loaded = load_matrix(DEFAULT_MATRIX)
    assert loaded.errors == []
    assert loaded.matrix is not None
    # ADR-035 rule: rows may warn as spec-only, but none may FAIL.
    fails = [f for f in validate_matrix_data(loaded.matrix) if f.severity == "fail"]
    assert fails == []


class TestVerifiedByRule:
    """ADR-035: no row is 'verified' until a canary exercises it."""

    def _row(self, **over: object) -> dict:
        row: dict[str, object] = {
            "id": "x",
            "path_prefix": "/umrs/x",
            "methods": ["GET", "POST"],
            "mutating": True,
            "gateway_auth": "auth_request",
            "umrs_authz": "verified-identity",
            "ux_gate": "none",
            "notes": "n",
            "review": "2026-09-02",
        }
        row.update(over)
        return row

    def _matrix(self, *rows: dict) -> dict:
        base = [
            _row(
                id="platform-default",
                path_prefix="/umrs",
                umrs_authz="verified-identity",
            )
        ]
        return {"version": 1, "rows": list(rows) + base}

    def test_unverified_row_warns_not_fails(self) -> None:
        findings = validate_matrix_data(self._matrix(self._row()))
        missing = [f for f in findings if f.code == "row-verified-missing"]
        assert missing and all(f.severity == "warn" for f in missing)

    def test_verified_by_unknown_canary_fails(self) -> None:
        findings = validate_matrix_data(self._matrix(self._row(verified_by="no-such-probe")))
        unknown = [f for f in findings if f.code == "row-verified-unknown"]
        assert unknown and unknown[0].severity == "fail"

    def test_verified_by_existing_canary_passes(self) -> None:
        findings = validate_matrix_data(self._matrix(self._row(verified_by="companion-workers")))
        assert [f for f in findings if f.code.startswith("row-verified-unknown")] == []
        assert [f for f in findings if f.code == "row-verified-missing" and f.subject == "x"] == []

    def test_verification_exempt_row_draws_no_missing_warning(self) -> None:
        # 2026-10-20: open beacons / retired-unmounted families / the
        # platform-default catch-all carry their no-canary reason in the row.
        findings = validate_matrix_data(
            self._matrix(self._row(verification_exempt="open beacon, nothing to deny"))
        )
        assert [f for f in findings if f.code == "row-verified-missing" and f.subject == "x"] == []

    def test_verification_exempt_blank_still_warns(self) -> None:
        findings = validate_matrix_data(self._matrix(self._row(verification_exempt="  ")))
        assert [f for f in findings if f.code == "row-verified-missing" and f.subject == "x"]

    def test_verified_by_multiple_canaries(self) -> None:
        findings = validate_matrix_data(
            self._matrix(self._row(verified_by="companion-workers,asset-index-cross-scope"))
        )
        assert [f for f in findings if f.code == "row-verified-unknown"] == []

    def test_default_matrix_companion_and_assets_rows_are_verified(self) -> None:
        """The two canary-caught rows must stay wired to real probes."""
        loaded = load_matrix(DEFAULT_MATRIX)
        assert loaded.matrix is not None
        rows = {r["id"]: r for r in loaded.matrix["rows"]}
        assert rows["companion-workers"]["verified_by"] == "companion-workers"
        assert rows["asset-index"]["verified_by"] == "asset-index-cross-scope"
        canary_ids = _load_canary_ids()
        assert {"companion-workers", "asset-index-cross-scope"} <= canary_ids


def test_every_mutating_route_authz_is_enforceable() -> None:
    """INV: no enum value permitted on mutating rows outside ENFORCEABLE_AUTHZ."""
    assert {"read-only"} == ALLOWED_AUTHZ - ENFORCEABLE_AUTHZ


def test_missing_required_field_fails() -> None:
    rows = [_row()]
    del rows[0]["notes"]
    findings = validate_matrix_data(_matrix(*rows))
    assert any(f.code == "row-fields" for f in findings)


def test_unknown_authz_value_fails() -> None:
    findings = validate_matrix_data(_matrix(_row(umrs_authz="make-it-up")))
    assert any(f.code == "row-authz" for f in findings)


def test_mutating_row_with_read_only_authz_fails_invariant() -> None:
    findings = validate_matrix_data(_matrix(_row(umrs_authz="read-only")))
    assert any(f.code == "row-enforcement" for f in findings)


def test_mutating_row_with_exempt_authz_passes() -> None:
    findings = validate_matrix_data(_matrix(_row(umrs_authz="exempt")))
    assert [f for f in findings if f.severity == "fail"] == []


def test_read_only_row_allows_read_only_authz() -> None:
    findings = validate_matrix_data(_matrix(_row(mutating=False, umrs_authz="read-only")))
    assert [f for f in findings if f.severity == "fail"] == []


def test_duplicate_id_fails() -> None:
    findings = validate_matrix_data(_matrix(_row(), _row(path_prefix="/umrs/y")))
    assert any(f.code == "row-id" for f in findings)


def test_duplicate_prefix_fails() -> None:
    findings = validate_matrix_data(_matrix(_row(), _row(id="y")))
    assert any(f.code == "row-prefix" for f in findings)


def test_missing_catch_all_fails() -> None:
    findings = validate_matrix_data({"version": 1, "rows": [_row()]})
    assert any(f.code == "catch-all" for f in findings)


def test_catch_all_must_be_platform_default_row() -> None:
    findings = validate_matrix_data(
        _matrix(_row(id="rogue", path_prefix="/umrs", umrs_authz="verified-identity"))
    )
    assert any(f.code == "catch-all" for f in findings)


def test_unknown_method_fails() -> None:
    findings = validate_matrix_data(_matrix(_row(methods=["GET", "TWEET"])))
    assert any(f.code == "row-methods" for f in findings)


def test_format_json_serializes_all_fields() -> None:
    payload = format_json([Finding("c", "s", "m", "warn")])
    assert '"severity": "warn"' in payload


def test_main_bad_path_returns_2(tmp_path: Path) -> None:
    assert main(["--matrix", str(tmp_path / "nope.yaml")]) == 2


def test_main_default_matrix_returns_0(capsys: object) -> None:
    assert main([]) == 0
