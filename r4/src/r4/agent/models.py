"""Workflow data models for the Agentic Workflow Builder.

These models are shared by the planner, executor, session manager, and CLI.
The agent layer currently expects a richer in-memory schema than the older
persisted workflow format, so the models provide both the canonical fields and
lightweight aliases used elsewhere in the agent code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def _utcnow() -> datetime:
    """Timezone-aware UTC now (replaces deprecated ``datetime.utcnow``)."""
    return datetime.now(timezone.utc)


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetimes while tolerating missing values."""
    if not value:
        return None
    return datetime.fromisoformat(value)


class StepType(str, Enum):
    """Types of workflow steps."""

    MCP_CALL = "mcp_call"
    UMRS_JOB = "umrs_job"
    RAG_QUERY = "rag_query"
    LLM_PROMPT = "llm_prompt"
    SHELL_EXEC = "shell_exec"
    PYTHON_EXEC = "python_exec"


class WorkflowStatus(str, Enum):
    """Workflow execution status."""

    PENDING = "pending"
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    """Individual step execution status."""

    PENDING = "pending"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


@dataclass
class WorkflowInput:
    """Input parameter for a workflow step or workflow definition."""

    name: str
    description: str = ""
    input_type: str = "string"
    required: bool = True
    default: Optional[Any] = None
    source: Optional[str] = None

    @property
    def data_type(self) -> str:
        """Backward-compatible alias used by older agent code."""
        return self.input_type

    @data_type.setter
    def data_type(self, value: str) -> None:
        self.input_type = value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "type": self.input_type,
            "required": self.required,
            "default": self.default,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowInput":
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            input_type=data.get("type")
            or data.get("input_type")
            or data.get("data_type", "string"),
            required=data.get("required", True),
            default=data.get("default"),
            source=data.get("source"),
        )


@dataclass
class WorkflowOutput:
    """Output artifact from a workflow step or workflow definition."""

    name: str
    description: str = ""
    output_type: str = "string"
    from_step: Optional[str] = None

    @property
    def data_type(self) -> str:
        """Backward-compatible alias used by older agent code."""
        return self.output_type

    @data_type.setter
    def data_type(self, value: str) -> None:
        self.output_type = value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "type": self.output_type,
            "from_step": self.from_step,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowOutput":
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            output_type=data.get("type")
            or data.get("output_type")
            or data.get("data_type", "string"),
            from_step=data.get("from_step"),
        )


@dataclass
class WorkflowStep:
    """A single step in a workflow."""

    id: str
    description: str
    step_type: StepType
    config: Dict[str, Any] = field(default_factory=dict)
    tool_name: Optional[str] = None
    inputs: List[WorkflowInput] = field(default_factory=list)
    outputs: List[WorkflowOutput] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    name: str = ""
    requires_approval: bool = False
    timeout_seconds: int = 300

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.description or self.id

    @property
    def step_id(self) -> str:
        """Alias used by persisted workflow helpers."""
        return self.id

    @step_id.setter
    def step_id(self, value: str) -> None:
        self.id = value

    @property
    def dependencies(self) -> List[str]:
        """Alias used by older persisted workflow helpers."""
        return self.depends_on

    @dependencies.setter
    def dependencies(self, value: List[str]) -> None:
        self.depends_on = value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "step_id": self.id,
            "name": self.name,
            "description": self.description,
            "step_type": self.step_type.value,
            "tool_name": self.tool_name,
            "config": self.config,
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
            "depends_on": self.depends_on,
            "dependencies": self.depends_on,
            "requires_approval": self.requires_approval,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowStep":
        return cls(
            id=data.get("id") or data["step_id"],
            name=data.get("name", ""),
            description=data.get("description", ""),
            step_type=StepType(data["step_type"]),
            tool_name=data.get("tool_name") or data.get("tool"),
            config=data.get("config", {}),
            inputs=[WorkflowInput.from_dict(item) for item in data.get("inputs", [])],
            outputs=[WorkflowOutput.from_dict(item) for item in data.get("outputs", [])],
            depends_on=data.get("depends_on") or data.get("dependencies", []),
            requires_approval=data.get("requires_approval", False),
            timeout_seconds=data.get("timeout_seconds", 300),
        )


@dataclass
class StepResult:
    """Result of executing a workflow step."""

    step_id: str
    status: StepStatus
    output: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    egress_request_id: Optional[str] = None

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "egress_request_id": self.egress_request_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StepResult":
        return cls(
            step_id=data["step_id"],
            status=StepStatus(data.get("status", StepStatus.PENDING.value)),
            output=data.get("output"),
            error=data.get("error"),
            started_at=_parse_datetime(data.get("started_at")),
            completed_at=_parse_datetime(data.get("completed_at")),
            egress_request_id=data.get("egress_request_id"),
        )


@dataclass
class WorkflowDefinition:
    """Complete workflow definition."""

    name: str
    description: str = ""
    project_id: str = ""
    owner_user_id: str = ""
    steps: List[WorkflowStep] = field(default_factory=list)
    inputs: List[WorkflowInput] = field(default_factory=list)
    outputs: List[WorkflowOutput] = field(default_factory=list)
    status: WorkflowStatus = WorkflowStatus.DRAFT
    tags: List[str] = field(default_factory=list)
    workflow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    @property
    def id(self) -> str:
        """Alias used by the agent/session/CLI code."""
        return self.workflow_id

    @id.setter
    def id(self, value: str) -> None:
        self.workflow_id = value

    @classmethod
    def create(
        cls,
        name: str,
        description: str,
        project_id: str,
        owner_user_id: str,
    ) -> "WorkflowDefinition":
        """Create a new workflow definition."""
        return cls(
            name=name,
            description=description,
            project_id=project_id,
            owner_user_id=owner_user_id,
        )

    def add_step(self, step: WorkflowStep) -> None:
        """Add a step to the workflow."""
        self.steps.append(step)
        self.updated_at = _utcnow()

    def add_input(self, input_param: WorkflowInput) -> None:
        """Add an input parameter."""
        self.inputs.append(input_param)
        self.updated_at = _utcnow()

    def add_output(self, output: WorkflowOutput) -> None:
        """Add an output artifact."""
        self.outputs.append(output)
        self.updated_at = _utcnow()

    def execution_order(self) -> List[str]:
        """Get topologically sorted step order based on dependencies."""
        visited: set[str] = set()
        order: List[str] = []
        step_map = {step.id: step for step in self.steps}

        def visit(step_id: str) -> None:
            if step_id in visited:
                return
            visited.add(step_id)
            step = step_map.get(step_id)
            if step:
                for dependency in step.depends_on:
                    visit(dependency)
                order.append(step_id)

        for step in self.steps:
            visit(step.id)

        return order

    def get_step_order(self) -> List[str]:
        """Backward-compatible alias used by older persistence helpers."""
        return self.execution_order()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "name": self.name,
            "description": self.description,
            "project_id": self.project_id,
            "owner_user_id": self.owner_user_id,
            "steps": [step.to_dict() for step in self.steps],
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
            "status": self.status.value,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowDefinition":
        return cls(
            workflow_id=data.get("workflow_id") or data["id"],
            name=data["name"],
            description=data.get("description", ""),
            project_id=data.get("project_id", ""),
            owner_user_id=data.get("owner_user_id", ""),
            steps=[WorkflowStep.from_dict(step) for step in data.get("steps", [])],
            inputs=[WorkflowInput.from_dict(item) for item in data.get("inputs", [])],
            outputs=[WorkflowOutput.from_dict(item) for item in data.get("outputs", [])],
            status=WorkflowStatus(data.get("status", WorkflowStatus.DRAFT.value)),
            tags=data.get("tags", []),
            created_at=_parse_datetime(data.get("created_at")) or _utcnow(),
            updated_at=_parse_datetime(data.get("updated_at")) or _utcnow(),
        )


@dataclass
class WorkflowExecution:
    """Runtime execution of a workflow."""

    workflow: WorkflowDefinition
    status: WorkflowStatus = WorkflowStatus.RUNNING
    input_values: Dict[str, Any] = field(default_factory=dict)
    step_results: Dict[str, StepResult] = field(default_factory=dict)
    current_step_id: Optional[str] = None
    error: Optional[str] = None
    started_at: datetime = field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str = ""
    project_id: str = ""
    user_id: str = ""

    def __post_init__(self) -> None:
        if not self.workflow_id:
            self.workflow_id = self.workflow.id
        if not self.project_id:
            self.project_id = self.workflow.project_id
        if not self.user_id:
            self.user_id = self.workflow.owner_user_id

    @property
    def id(self) -> str:
        """Alias used by the CLI and session code."""
        return self.execution_id

    @id.setter
    def id(self, value: str) -> None:
        self.execution_id = value

    @classmethod
    def create(
        cls,
        workflow: WorkflowDefinition,
        user_id: str,
        input_values: Optional[Dict[str, Any]] = None,
    ) -> "WorkflowExecution":
        """Create a new execution from a workflow definition."""
        return cls(
            workflow=workflow,
            user_id=user_id,
            input_values=input_values or {},
        )

    def record_step_start(self, step_id: str) -> None:
        """Record that a step has started."""
        self.current_step_id = step_id
        self.step_results[step_id] = StepResult(
            step_id=step_id,
            status=StepStatus.RUNNING,
            started_at=_utcnow(),
        )

    def record_step_complete(self, step_id: str, output: Any) -> None:
        """Record that a step completed successfully."""
        if step_id in self.step_results:
            self.step_results[step_id].status = StepStatus.COMPLETED
            self.step_results[step_id].output = output
            self.step_results[step_id].completed_at = _utcnow()

    def record_step_failed(self, step_id: str, error: str) -> None:
        """Record that a step failed."""
        if step_id in self.step_results:
            self.step_results[step_id].status = StepStatus.FAILED
            self.step_results[step_id].error = error
            self.step_results[step_id].completed_at = _utcnow()

    def complete(self) -> None:
        """Mark execution as complete."""
        self.status = WorkflowStatus.COMPLETED
        self.completed_at = _utcnow()
        self.current_step_id = None

    def fail(self, error: str) -> None:
        """Mark execution as failed."""
        self.status = WorkflowStatus.FAILED
        self.error = error
        self.completed_at = _utcnow()

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "project_id": self.project_id,
            "user_id": self.user_id,
            "status": self.status.value,
            "input_values": self.input_values,
            "step_results": {key: value.to_dict() for key, value in self.step_results.items()},
            "current_step_id": self.current_step_id,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "workflow": self.workflow.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowExecution":
        workflow_data = data.get("workflow")
        workflow = (
            WorkflowDefinition.from_dict(workflow_data)
            if workflow_data
            else WorkflowDefinition(
                workflow_id=data.get("workflow_id", ""),
                name=data.get("workflow_name", "Workflow"),
                project_id=data.get("project_id", ""),
                owner_user_id=data.get("user_id", ""),
            )
        )
        return cls(
            workflow=workflow,
            status=WorkflowStatus(data.get("status", WorkflowStatus.RUNNING.value)),
            input_values=data.get("input_values", {}),
            step_results={
                key: StepResult.from_dict(value)
                for key, value in data.get("step_results", {}).items()
            },
            current_step_id=data.get("current_step_id"),
            error=data.get("error"),
            started_at=_parse_datetime(data.get("started_at")) or _utcnow(),
            completed_at=_parse_datetime(data.get("completed_at")),
            execution_id=data.get("execution_id") or data.get("id") or str(uuid.uuid4()),
            workflow_id=data.get("workflow_id", ""),
            project_id=data.get("project_id", ""),
            user_id=data.get("user_id", ""),
        )
