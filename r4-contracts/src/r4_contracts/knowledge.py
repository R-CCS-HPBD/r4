"""Knowledge indexing contract DTOs shared between r4 and UMRS.

Stdlib-only ``@dataclass(frozen=True)`` types describing the knowledge
indexing pipeline — events, artifact references, source specs,
verification reports, lint diagnostics, and draft workflow definitions.

All types are immutable (``frozen=True``) so they are hashable and safe
to use as dict keys or set members.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class KnowledgeEvent:
    """A single event in the knowledge indexing pipeline.

    Emits progress through lifecycle phases: creation, ingestion,
    verification, export, and deletion.
    """

    id: str  # UUID str, 36 chars
    event_type: Literal["create", "ingest", "verify", "export", "delete"]
    source: str | None = None  # optional source ref
    timestamp: str = ""  # ISO 8601


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to an artifact produced by knowledge indexing.

    Points to an S3 or local file artifact with an integrity hash.
    """

    uri: str  # s3:// or file://
    hash: str  # SHA-256 hex digest
    size_bytes: int


@dataclass(frozen=True)
class IngestSourceSpec:
    """Specification of a source to ingest into a knowledge index.

    Supports local paths, HTTP URLs, Git repositories, and S3 URIs.
    For ``http_url`` and ``git_repo`` kinds, ``allow_domains`` controls
    EgressGuard domain allowlisting.
    """

    kind: Literal["local", "http_url", "git_repo", "s3_uri"]
    uri: str
    allow_domains: list[str] | None = None


@dataclass(frozen=True)
class VerificationReportRef:
    """Reference to a knowledge verification report.

    Results from running quality / content scans on an index
    after ingestion or on demand.
    """

    report_id: str  # UUID str
    status: Literal["pass", "fail", "pending"]
    score: float | None = None  # 0.0–1.0


@dataclass(frozen=True)
class FindingRef:
    """A single finding from knowledge verification or content scanning."""

    severity: Literal["critical", "high", "medium", "low", "info"]
    category: str  # PII, SQL, credential, etc.
    message: str
    line: int | None = None


@dataclass(frozen=True)
class KnowledgeSummary:
    """Dashboard summary of a knowledge index."""

    title: str
    description: str | None = None
    document_count: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class LintDiagnostic:
    """A single lint diagnostic from the knowledge index checker.

    Mirrors editor / linter diagnostic shapes for human-readable
    reporting and machine-consumable filtering.
    """

    file_path: str
    line: int
    column: int
    code: str
    message: str
    severity: Literal["error", "warning", "info"] = "error"


@dataclass(frozen=True)
class StepDraft:
    """A single step in a knowledge workflow draft.

    Describes what the step does (e.g. ``rag_index``, ``vector_index``,
    ``graph_build``, ``catalog``, ``scan``) and its step-specific config.
    """

    step_id: str  # UUID str
    kind: Literal["rag_index", "vector_index", "graph_build", "catalog", "scan"]
    config: dict[str, Any] = field(default_factory=dict)  # step-specific config


@dataclass(frozen=True)
class WorkflowDraft:
    """A draft knowledge indexing workflow definition.

    Describes a sequence of steps without committing to a full
    execution plan.
    """

    workflow_id: str  # UUID str
    name: str
    steps: list[StepDraft] = field(default_factory=list)
    source: str | None = None
