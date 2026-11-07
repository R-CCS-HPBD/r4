"""Tests for shared knowledge lifecycle contracts."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, asdict

import pytest

from r4_contracts import (
    ArtifactRef,
    FindingRef,
    IngestSourceSpec,
    KnowledgeEvent,
    KnowledgeSummary,
    LintDiagnostic,
    StepDraft,
    VerificationReportRef,
    WorkflowDraft,
)


# Rows pin the CURRENT contract shapes (knowledge.py, commit 62e86d1f).
# The previous rows targeted a pre-integration schema and failed at
# collection on main — repaired under WP-13 housekeeping (2026-09-07).
@pytest.mark.parametrize(
    ("value", "contract"),
    [
        (
            IngestSourceSpec("local", "paper.pdf", ["paper.example.org"]),
            IngestSourceSpec,
        ),
        (
            VerificationReportRef("report-1", "pass", 0.92),
            VerificationReportRef,
        ),
        (
            FindingRef("high", "provenance", "missing:citation", 12),
            FindingRef,
        ),
        (
            KnowledgeSummary("project-1 index", "demo summary", 3, 1024),
            KnowledgeSummary,
        ),
        (
            LintDiagnostic("workflow.yaml", 12, 4, "missing-input", "Missing input", "error"),
            LintDiagnostic,
        ),
        (
            StepDraft("step-1", "scan", config={"steps": 100}),
            StepDraft,
        ),
    ],
)
def test_contract_round_trip(value, contract):
    # knowledge.py DTOs are plain frozen dataclasses (no to_dict/from_dict);
    # the round-trip contract is asdict -> JSON -> re-construct.
    payload = json.loads(json.dumps(asdict(value)))
    assert contract(**payload) == value


def test_knowledge_event_construction():
    event = KnowledgeEvent(
        id="evt-1",
        event_type="ingest",
        source="local:paper.pdf",
        timestamp="2026-07-13T00:00:00Z",
    )

    assert asdict(event)["event_type"] == "ingest"
    json.dumps(asdict(event))


def test_workflow_draft_nested_steps_json_round_trip():
    draft = WorkflowDraft(
        workflow_id="wf-1",
        name="validate",
        steps=[StepDraft("step-1", "catalog", config={"source": "plan"})],
        source="project-1",
    )

    payload = json.loads(json.dumps(asdict(draft)))
    rebuilt = WorkflowDraft(
        workflow_id=payload["workflow_id"],
        name=payload["name"],
        steps=[StepDraft(**s) for s in payload["steps"]],
        source=payload["source"],
    )
    assert rebuilt == draft
    assert payload["steps"][0]["kind"] == "catalog"


def test_artifact_ref_shape():
    artifact = ArtifactRef("s3://bucket/object", hash="a" * 64, size_bytes=7)
    payload = asdict(artifact)
    assert set(payload) == {"uri", "hash", "size_bytes"}
    json.dumps(payload)


def test_contracts_are_frozen():
    event = KnowledgeEvent(id="evt-1", event_type="ingest")

    with pytest.raises(FrozenInstanceError):
        event.event_type = "verify"


def test_mutable_defaults_are_not_shared():
    first = WorkflowDraft(workflow_id="a", name="first")
    second = WorkflowDraft(workflow_id="b", name="second")

    first.steps.append(StepDraft("step-1", "scan"))
    assert second.steps == []
