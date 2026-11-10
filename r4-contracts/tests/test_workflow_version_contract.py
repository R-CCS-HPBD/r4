"""ADR-113 (2026-11-08): workflow references and the version fields of the contract."""

from __future__ import annotations

import pytest

from r4_contracts.workflows import (
    RunDefinitionRecord,
    WorkflowCatalogItem,
    WorkflowDescribeResponse,
    WorkflowResult,
    WorkflowStatus,
    WorkflowTriggerRequest,
    WorkflowUploadResult,
    parse_workflow_ref,
    resolve_workflow_ref,
    workflow_ref_label,
)


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("seg", ("seg", None)),
        ("seg@1.2.3", ("seg", "1.2.3")),
        (" seg@2.0.0-rc.1 ", ("seg", "2.0.0-rc.1")),
        # pre-ADR-113 versions stay selectable: no SemVer check on selection
        ("seg@0.1", ("seg", "0.1")),
    ],
)
def test_parse_workflow_ref(ref, expected):
    assert parse_workflow_ref(ref) == expected


@pytest.mark.parametrize("ref", ["", "@1.0.0", "seg@", "seg@1 .0"])
def test_parse_workflow_ref_refuses_malformed(ref):
    with pytest.raises(ValueError):
        parse_workflow_ref(ref)


def test_resolve_merges_an_explicit_version():
    assert resolve_workflow_ref("seg", "1.0.0") == ("seg", "1.0.0")
    assert resolve_workflow_ref("seg@1.0.0", "1.0.0") == ("seg", "1.0.0")
    assert resolve_workflow_ref("seg@1.0.0", None) == ("seg", "1.0.0")
    assert resolve_workflow_ref("seg", "") == ("seg", None)


def test_resolve_refuses_two_versions_that_disagree():
    with pytest.raises(ValueError, match="1.0.0.*2.0.0"):
        resolve_workflow_ref("seg@1.0.0", "2.0.0")


def test_label():
    assert workflow_ref_label("seg", "1.0.0") == "seg@1.0.0"
    assert workflow_ref_label("seg", "") == "seg"
    assert workflow_ref_label("seg", None) == "seg"


def test_version_fields_default_empty_for_old_servers():
    item = WorkflowCatalogItem(workflow_name="seg", name="seg")
    assert (item.version, item.versions, item.source_sha256) == ("", [], "")
    status = WorkflowStatus(execution_id="e", workflow_name="seg", status="running")
    result = WorkflowResult(execution_id="e", workflow_name="seg", status="succeeded")
    assert (status.workflow_version, result.workflow_version) == ("", "")
    upload = WorkflowUploadResult(workflow_name="seg")
    assert (upload.version, upload.source_sha256, upload.warnings) == ("", "", [])


def test_run_definition_round_trips_the_pinned_version():
    record = RunDefinitionRecord.from_dict(
        {
            "name": "nightly",
            "workflow_name": "seg",
            "workflow_version": "1.2.0",
            "source_sha256": "a" * 64,
            "unknown": "dropped",
        }
    )
    out = record.to_dict()
    assert out["workflow_version"] == "1.2.0"
    assert out["source_sha256"] == "a" * 64
    # Old records (no version) still load.
    assert RunDefinitionRecord.from_dict({"name": "n", "workflow_name": "w"}).workflow_version == ""


def test_trigger_request_carries_the_version():
    req = WorkflowTriggerRequest.from_public_payload("seg", {"version": "1.2.0"})
    assert req.version == "1.2.0"
    assert req.to_umrs_body(user_id="alice")["version"] == "1.2.0"
    bare = WorkflowTriggerRequest.from_public_payload("seg", {})
    assert "version" not in bare.to_umrs_body(user_id="alice")


def test_describe_response_lists_versions():
    out = WorkflowDescribeResponse(
        workflow_name="seg", version="1.2.0", versions=["1.2.0", "1.0.0"], source_sha256="b"
    ).to_dict()
    assert out["versions"] == ["1.2.0", "1.0.0"]
    assert out["source_sha256"] == "b"
