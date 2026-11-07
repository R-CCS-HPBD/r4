"""Contract tests for workflow capability descriptors."""

from r4_contracts.workflows import (
    WORKFLOW_CONTRACT_VERSION,
    WORKFLOW_OPERATIONS,
    WorkflowTriggerRequest,
    workflow_tool_descriptor,
)


def test_contract_version_present():
    assert WORKFLOW_CONTRACT_VERSION


def test_operations_are_stable():
    assert "describe" in WORKFLOW_OPERATIONS
    assert "run" in WORKFLOW_OPERATIONS


def test_trigger_request_unifies_public_and_legacy_payloads():
    req = WorkflowTriggerRequest.from_public_payload(
        "ct-analysis",
        {
            "parameters": {"bucket": "demo"},
            "input_files": ["a.tif"],
            "project_id": "project-a",
        },
    )
    body = req.to_umrs_body(user_id="alice")
    assert body["user_id"] == "alice"
    assert body["params"]["bucket"] == "demo"
    assert body["params"]["input_files"] == ["a.tif"]
    assert body["params"]["project_id"] == "project-a"


def test_tool_descriptor_names_workflow_name_field():
    descriptor = workflow_tool_descriptor("run")
    assert descriptor["workflow_name_field"] == "workflow_name"
