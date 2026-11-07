"""Versioned workflow capability contract shared by R4, Companion, and MCP.

This module is the sole owner of workflow wire schemas, lifecycle states,
structured errors, and capability/risk metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WORKFLOW_CONTRACT_VERSION = "1.1.0"

# Canonical operation names exposed by Companion tools and MCP adapters.
WORKFLOW_OPERATIONS = (
    "list",
    "describe",
    "upload",
    "run",
    "status",
    "wait",
    "results",
    "cancel",
)

# Canonical lifecycle states (UMRS execution registry).
WORKFLOW_LIFECYCLE_STATUSES = (
    "submitted",
    "running",
    "succeeded",
    "failed",
    "cancelled",
)

WORKFLOW_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

# Risk metadata for policy classification.
WORKFLOW_CAPABILITY_RISK = {
    "list": "safe",
    "describe": "safe",
    "status": "safe",
    "results": "safe",
    "wait": "safe",
    "run": "elevated",
    "cancel": "elevated",
    "upload": "privileged",
}

# Operations that fail closed when the policy file is missing or invalid.
WORKFLOW_MUTATION_OPERATIONS = frozenset({"run", "cancel", "upload"})

# Agent-facing per-op tool descriptions (Companion / MCP adapters import — never rephrase).
WORKFLOW_TOOL_HELP = {
    "list": "List registered UMRS workflows available to this project.",
    "describe": "Describe a registered workflow (inputs, metadata, risk).",
    "upload": (
        "Upload or register a workflow definition (privileged). workflow.json "
        "must carry a description and, on every step, depends_on ([] for a "
        "step that starts the run); a refusal lists every problem."
    ),
    "run": "Start a registered workflow run with the given inputs.",
    "status": "Get status of a workflow run, or list runs when run_id is omitted.",
    "wait": "Block until a workflow run reaches a terminal status (or timeout).",
    "results": "Fetch results/artifacts for a completed workflow run.",
    "cancel": "Cancel an in-flight workflow run.",
}


@dataclass
class RunDefinitionRecord:
    """Persisted named run template (registered workflow + params).

    Reproducibility/token-efficiency record: a workflow invocation the user
    can re-execute by name without re-deriving parameters through an LLM —
    or re-typing them. Owned by the UMRS-side registry; r4 reads/writes via
    gateway routes only (r4 never persists run definitions locally).
    """

    name: str
    workflow_name: str
    params: dict[str, Any] = field(default_factory=dict)
    user_id: str = ""
    project_id: str = ""
    description: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "workflow_name": self.workflow_name,
            "params": self.params,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunDefinitionRecord:
        return cls(**{k: v for k, v in value.items() if k in cls.__dataclass_fields__})


@dataclass
class WorkflowStructuredError:
    """Machine-readable workflow error envelope."""

    code: str
    message: str
    remediation: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": "workflow_error",
            "code": self.code,
            "message": self.message,
        }
        if self.remediation:
            payload["remediation"] = self.remediation
        if self.details:
            payload["details"] = self.details
        return payload


@dataclass
class WorkflowTriggerRequest:
    """Public workflow trigger request (Gateway /gw/workflows/{name}/trigger)."""

    # user/project identity is derived from Gateway-verified headers in strict mode.
    workflow_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    input_files: list[str] = field(default_factory=list)
    # Deprecated alias accepted at SDK boundary only.
    project_id: str | None = None

    def to_umrs_body(self, *, user_id: str) -> dict[str, Any]:
        params = dict(self.parameters)
        if self.input_files:
            params.setdefault("input_files", list(self.input_files))
        if self.project_id:
            params.setdefault("project_id", self.project_id)
        return {"user_id": user_id, "params": params}

    @classmethod
    def from_public_payload(
        cls, workflow_name: str, payload: dict[str, Any]
    ) -> WorkflowTriggerRequest:
        parameters = payload.get("parameters")
        if parameters is None and "params" in payload:
            parameters = payload.get("params")
        if not isinstance(parameters, dict):
            parameters = {}
        input_files = payload.get("input_files")
        if not isinstance(input_files, list):
            input_files = []
        project_id = payload.get("project_id")
        if project_id is not None and not isinstance(project_id, str):
            project_id = None
        return cls(
            workflow_name=workflow_name,
            parameters=dict(parameters),
            input_files=[str(item) for item in input_files],
            project_id=project_id,
        )


@dataclass
class WorkflowDescribeResponse:
    """Schema/details for a registered workflow."""

    workflow_name: str
    version: str = ""
    description: str = ""
    scope: str = "user"
    user_id: str = ""
    parameter_schema: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    requirements: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": WORKFLOW_CONTRACT_VERSION,
            "workflow_name": self.workflow_name,
            "version": self.version,
            "description": self.description,
            "scope": self.scope,
            "user_id": self.user_id,
            "parameter_schema": self.parameter_schema,
            "steps": self.steps,
            "requirements": self.requirements,
        }


@dataclass
class WorkflowStatus:
    """Status of a workflow execution."""

    execution_id: str
    workflow_name: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    progress: float | None = None
    workflow_id: str | None = None  # deprecated alias

    def __post_init__(self) -> None:
        if self.workflow_id is None:
            self.workflow_id = self.workflow_name


@dataclass
class WorkflowResult:
    """Result from a registered UMRS workflow execution.

    Distinct from ``umrs.knowledge.workflow.ProjectWorkflowResult``, which
    summarizes the RC1 project pipeline (ingest→plan→loop).
    """

    execution_id: str
    workflow_name: str
    status: str
    outputs: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Any] = field(default_factory=list)
    metrics: dict[str, Any] | None = None
    duration_seconds: float | None = None
    error: str | None = None
    durable_job_id: str | None = None
    workflow_id: str | None = None  # deprecated alias

    def __post_init__(self) -> None:
        if self.workflow_id is None:
            self.workflow_id = self.workflow_name


@dataclass
class WorkflowCatalogItem:
    """Definition of an available workflow (catalog entry)."""

    workflow_name: str
    name: str
    description: str = ""
    parameters: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    workflow_id: str | None = None  # deprecated alias
    # Ownership as the UMRS catalog row states it (2026-10-23): ``scope`` is
    # the owner's scope ("user" / "system"), ``owner`` the row's user_id,
    # ``shared`` the server's mark for a row another user shared with the
    # caller, ``access`` the owner's access entries (display only).
    scope: str = ""
    owner: str = ""
    shared: bool = False
    access: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.workflow_id is None:
            self.workflow_id = self.workflow_name

    @property
    def scope_label(self) -> str:
        """One display label: "shared · <owner>" for a row shared with the
        caller, else the scope ("user" for the caller's own, "system")."""
        if not self.shared:
            return self.scope
        return f"shared · {self.owner}" if self.owner else "shared"


@dataclass
class WorkflowUploadResult:
    """Result of a workflow upload operation."""

    workflow_name: str = ""
    status: str = ""
    accepted: bool = False
    source_path: str | None = None
    message: str | None = None
    error: str | None = None
    workflow_id: str | None = None  # deprecated alias

    def __post_init__(self) -> None:
        if self.workflow_id is None:
            self.workflow_id = self.workflow_name


@dataclass
class WorkflowStepIR:
    """Single step in a workflow intermediate representation."""

    name: str
    action: str
    inputs: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class WorkflowIR:
    """Runtime-neutral workflow intermediate representation."""

    name: str
    version: str = "1"
    steps: list[WorkflowStepIR] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def workflow_tool_descriptor(operation: str) -> dict[str, Any]:
    """Return canonical MCP/Companion tool metadata for *operation*."""
    if operation not in WORKFLOW_OPERATIONS:
        raise ValueError(f"Unknown workflow operation: {operation}")
    return {
        "operation": operation,
        "contract_version": WORKFLOW_CONTRACT_VERSION,
        "risk": WORKFLOW_CAPABILITY_RISK[operation],
        "workflow_name_field": "workflow_name",
    }
