"""Tests for the data authority inventory validator."""

from __future__ import annotations

from pathlib import Path

from tools.data_authority_validator import (
    ALLOWED_AUTHORITY,
    DataAuthorityFinding,
    format_json,
    load_inventory,
    validate_default_inventory,
    validate_inventory_data,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _base_inventory() -> dict:
    return {
        "authority_classes": sorted(ALLOWED_AUTHORITY),
        "entries": [
            {
                "id": "postgres-truth",
                "data": "Durable metadata",
                "owner": "UMRS",
                "authority": "authoritative",
                "location": "remote-database",
                "method": "postgres",
                "sensitivity": ["operational"],
                "retention": "project policy",
                "trust_boundary": "umrs-control-plane",
                "source_refs": ["umrs/src/umrs/jobs/schema.py"],
            }
        ],
    }


def test_default_inventory_validates_without_findings() -> None:
    report = validate_default_inventory()

    assert report.findings == []


def test_default_inventory_covers_required_data_classes() -> None:
    report = validate_default_inventory()
    entry_ids = {entry["id"] for entry in report.entries}

    assert {
        "r4-token-store",
        "umrs-durable-jobs",
        "platform-artifacts",
        "gateway-request-audit",
        "r4-companion-session-cache",
        "runtime-container-state",
    } <= entry_ids


def test_load_inventory_accepts_json_subset_yaml(tmp_path: Path) -> None:
    path = tmp_path / "inventory.yaml"
    path.write_text('{"authority_classes": ["authoritative"], "entries": []}')

    data = load_inventory(path)

    assert data["authority_classes"] == ["authoritative"]


def test_missing_entries_reported() -> None:
    report = validate_inventory_data({"authority_classes": sorted(ALLOWED_AUTHORITY)})

    assert any("entries must be a non-empty list" in f.message for f in report.findings)


def test_missing_required_fields_reported() -> None:
    report = validate_inventory_data(
        {"authority_classes": sorted(ALLOWED_AUTHORITY), "entries": [{"id": "bad"}]}
    )

    assert any("missing required fields" in f.message for f in report.findings)


def test_duplicate_ids_reported() -> None:
    data = _base_inventory()
    data["entries"].append(dict(data["entries"][0]))

    report = validate_inventory_data(data)

    assert any("duplicate entry id" in f.message for f in report.findings)


def test_invalid_authority_reported() -> None:
    data = _base_inventory()
    data["entries"][0]["authority"] = "mostly-true"

    report = validate_inventory_data(data)

    assert any("invalid authority" in f.message for f in report.findings)


def test_invalid_location_reported() -> None:
    data = _base_inventory()
    data["entries"][0]["location"] = "unknown-place"

    report = validate_inventory_data(data)

    assert any("invalid location" in f.message for f in report.findings)


def test_invalid_method_reported() -> None:
    data = _base_inventory()
    data["entries"][0]["method"] = "spreadsheet"

    report = validate_inventory_data(data)

    assert any("invalid method" in f.message for f in report.findings)


def test_invalid_sensitivity_reported() -> None:
    data = _base_inventory()
    data["entries"][0]["sensitivity"] = ["mystery"]

    report = validate_inventory_data(data)

    assert any("invalid sensitivity" in f.message for f in report.findings)


def test_empty_retention_reported() -> None:
    data = _base_inventory()
    data["entries"][0]["retention"] = ""

    report = validate_inventory_data(data)

    assert any("retention must be a non-empty string" in f.message for f in report.findings)


def test_source_refs_must_be_non_empty() -> None:
    data = _base_inventory()
    data["entries"][0]["source_refs"] = []

    report = validate_inventory_data(data)

    assert any("source_refs must be a non-empty list" in f.message for f in report.findings)


def test_filesystem_authoritative_state_requires_exception() -> None:
    data = _base_inventory()
    data["entries"][0].update({"location": "local-filesystem", "method": "json"})

    report = validate_inventory_data(data)

    assert any("authority_exception.reason" in f.message for f in report.findings)


def test_filesystem_authoritative_missing_recovery_semantics_is_reported() -> None:
    """A reason alone is not a waiver: the exception must state how state recovers.

    Added by 3db8953b (operational survivability program) — a filesystem-backed
    authoritative store has no transactional recovery, so the exception has to
    declare the recovery semantics it relies on instead.
    """
    data = _base_inventory()
    data["entries"][0].update(
        {
            "location": "local-filesystem",
            "method": "json",
            "authority_exception": {"reason": "legacy bootstrap authority pending migration"},
        }
    )

    report = validate_inventory_data(data)

    assert any("authority_exception.recovery_semantics" in f.message for f in report.findings)


def test_filesystem_authoritative_exception_is_accepted() -> None:
    data = _base_inventory()
    data["entries"][0].update(
        {
            "location": "local-filesystem",
            "method": "json",
            "authority_exception": {
                "reason": "legacy bootstrap authority pending migration",
                "recovery_semantics": "rebuilt from the postgres ledger on boot; losses are replayable",
            },
        }
    )

    report = validate_inventory_data(data)

    assert report.findings == []


def test_authority_classes_must_match_validator_contract() -> None:
    data = _base_inventory()
    data["authority_classes"] = ["authoritative"]

    report = validate_inventory_data(data)

    assert any("authority_classes must exactly match" in f.message for f in report.findings)


def test_finding_format_includes_entry_id_and_message() -> None:
    finding = DataAuthorityFinding("entry-a", "missing owner")

    assert finding.format() == "entry-a: missing owner"


def test_format_json_contains_summary_and_findings() -> None:
    data = _base_inventory()
    data["entries"][0]["method"] = "spreadsheet"

    payload = format_json(validate_inventory_data(data))

    assert '"summary"' in payload
    assert '"findings"' in payload
    assert "spreadsheet" in payload
