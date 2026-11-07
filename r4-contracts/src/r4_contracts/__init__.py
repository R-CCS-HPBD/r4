"""r4-contracts — shared schemas/DTOs/validators between r4 and UMRS.

This package is intentionally small and stdlib-only. It provides the
client-side contract for things UMRS owns the durable implementation of.
"""

from __future__ import annotations

__version__ = "0.2.0rc2"

# A2L conservation contracts (ADR-034)
from .a2l import (
    A2L_ARTIFACT_TRANSITIONS,
    A2lAgentExecutionCreate,
    A2lAgentExecutionRecord,
    A2lAgentExecutionUpdate,
    A2lArtifactDeriveRequest,
    A2lArtifactEdge,
    A2lArtifactRecord,
    A2lArtifactStatus,
    A2lExecutionStatus,
    a2l_artifact_transition_ok,
)

# Knowledge indexing DTOs
# Platform error taxonomy (G-P12-5, WP-16)
from .errors import (
    AUTH_ERRORS,
    CURVE_ERRORS,
    ERROR_CODE_HEADER,
    PRESIGN_ERRORS,
    PROVIDER_ERRORS,
    SESSION_ERRORS,
    PlatformError,
    known_error_codes,
)

# Knowledge lifecycle contracts
from .knowledge import (
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

# Research primitives — R4's native domain model
from .research import (
    Agent,
    AgentStatus,
    ComputeResource,
    ComputeResourceStatus,
    Conversation,
    ConversationStatus,
    Dataset,
    DatasetStatus,
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
    Knowledge,
    KnowledgeStatus,
    Notebook,
    NotebookStatus,
    PrimitiveKind,
    ProvenanceGraph,
    ProvenanceNode,
    Publication,
    PublicationStatus,
    ResearchGoal,
    ResearchGoalStatus,
    ResearchProject,
    ResearchProjectStatus,
    Simulation,
    SimulationStatus,
    WorkflowStatus,
)

__all__ = [
    "__version__",
    # Platform error taxonomy
    "AUTH_ERRORS",
    "CURVE_ERRORS",
    "ERROR_CODE_HEADER",
    "PRESIGN_ERRORS",
    "PROVIDER_ERRORS",
    "SESSION_ERRORS",
    "PlatformError",
    "known_error_codes",
    # Knowledge lifecycle contracts
    "ArtifactRef",
    "FindingRef",
    "IngestSourceSpec",
    "KnowledgeEvent",
    "KnowledgeSummary",
    "LintDiagnostic",
    "StepDraft",
    "VerificationReportRef",
    "WorkflowDraft",
    # Research primitives
    "Agent",
    "AgentStatus",
    "ComputeResource",
    "ComputeResourceStatus",
    "Conversation",
    "ConversationStatus",
    "Dataset",
    "DatasetStatus",
    "Experiment",
    "ExperimentStatus",
    "Hypothesis",
    "HypothesisStatus",
    "Knowledge",
    "KnowledgeStatus",
    "Notebook",
    "NotebookStatus",
    "PrimitiveKind",
    "ProvenanceGraph",
    "ProvenanceNode",
    "Publication",
    "PublicationStatus",
    "ResearchGoal",
    "ResearchGoalStatus",
    "ResearchProject",
    "ResearchProjectStatus",
    "Simulation",
    "SimulationStatus",
    "WorkflowStatus",
    # Knowledge indexing
    "ArtifactRef",
    "FindingRef",
    "IngestSourceSpec",
    "KnowledgeEvent",
    "KnowledgeSummary",
    "LintDiagnostic",
    "StepDraft",
    "VerificationReportRef",
    "WorkflowDraft",
]
