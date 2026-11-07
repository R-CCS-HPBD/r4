"""Tests for the runtime support matrix validator."""

from __future__ import annotations

from tools.runtime_support_matrix import (
    ACCEPTED_SUPPORT_LEVELS,
    evaluate_composition,
    load_matrix,
    validate_default_matrix,
    validate_matrix_data,
)


def test_default_matrix_has_blessed_envelopes() -> None:
    matrix_data = load_matrix()

    assert matrix_data["blessed_envelopes"] == ["hpc", "k8s", "dev-server"]


def test_default_matrix_validates_without_findings() -> None:
    report = validate_default_matrix()

    assert report.findings == []


def test_blessed_envelopes_have_accepted_entries() -> None:
    report = validate_default_matrix()
    accepted_envelopes = {
        entry.envelope for entry in report.entries if entry.support_level in ACCEPTED_SUPPORT_LEVELS
    }

    assert set(report.blessed_envelopes) <= accepted_envelopes


def test_missing_required_fields_reported() -> None:
    report = validate_matrix_data(
        {
            "blessed_envelopes": ["hpc"],
            "entries": [{"id": "bad"}],
        }
    )

    assert any("missing required fields" in finding.message for finding in report.findings)


def test_invalid_values_reported() -> None:
    matrix_data = load_matrix()
    matrix_data["entries"][0]["gateway"] = "magic-gateway"

    report = validate_matrix_data(matrix_data)

    assert any("invalid gateway" in finding.message for finding in report.findings)


def test_duplicate_ids_reported() -> None:
    matrix_data = load_matrix()
    matrix_data["entries"][1]["id"] = matrix_data["entries"][0]["id"]

    report = validate_matrix_data(matrix_data)

    assert any("duplicate entry id" in finding.message for finding in report.findings)


def test_python_gateway_cannot_be_supported() -> None:
    matrix_data = load_matrix()
    python_gateway_entry = next(
        entry for entry in matrix_data["entries"] if entry["gateway"] == "python-gateway"
    )
    python_gateway_entry["support_level"] = "supported"

    report = validate_matrix_data(matrix_data)

    assert any("python-gateway" in finding.message for finding in report.findings)


def test_hybrid_wrapper_must_remain_experimental() -> None:
    matrix_data = load_matrix()
    hybrid_entry = next(
        entry for entry in matrix_data["entries"] if entry["runtime_wrapper"] == "hybrid-wrapper"
    )
    hybrid_entry["support_level"] = "supported"

    report = validate_matrix_data(matrix_data)

    assert any("hybrid-wrapper" in finding.message for finding in report.findings)


def test_supported_hpc_podman_composition_accepted() -> None:
    report = validate_default_matrix()
    decision = evaluate_composition(
        {"profile": "hpc-full", "runtime_wrapper": "slurm-podman", "gateway": "nginx-sidecar"},
        report.entries,
    )

    assert decision.accepted is True
    assert decision.support_level == "supported"
    assert decision.entry_ids == ["hpc-slurm-podman"]


def test_experimental_hybrid_composition_rejected() -> None:
    report = validate_default_matrix()
    decision = evaluate_composition({"runtime_wrapper": "hybrid-wrapper"}, report.entries)

    assert decision.accepted is False
    assert decision.support_level == "experimental"
    assert decision.entry_ids == ["hybrid-slurm-wrapper"]
