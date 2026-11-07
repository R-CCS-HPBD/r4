"""SciWorkflowAgent core implementation.

The main agent class that guides scientists through workflow creation,
combining LLM planning with tool execution and egress surveillance.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Iterator

from .egress import EgressDecision, EgressGuard, EgressRequest
from .models import (
    StepResult,
    StepStatus,
    StepType,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowInput,
    WorkflowOutput,
    WorkflowStatus,
    WorkflowStep,
)

if TYPE_CHECKING:
    from r4.client import Client
    from r4.mcp import MCPTool

logger = logging.getLogger(__name__)


class AgentCancelledError(RuntimeError):
    """Raised when an active agent request is cancelled."""


class AgentCancellation:
    """Thread-safe cancellation token that closes active transports."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._closers: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    def is_set(self) -> bool:
        return self._event.is_set()

    def request(self) -> None:
        self._event.set()
        with self._lock:
            closers = list(self._closers)
        for close in closers:
            try:
                close()
            except Exception:
                pass

    def check(self) -> None:
        if self._event.is_set():
            raise AgentCancelledError("Agent request cancelled")

    def register_close(self, close: Callable[[], None]) -> None:
        with self._lock:
            self._closers.append(close)
            cancelled = self._event.is_set()
        if cancelled:
            try:
                close()
            except Exception:
                pass

    def clear(self) -> None:
        self._event.clear()
        with self._lock:
            self._closers.clear()

    def wait(self, timeout: float) -> None:
        self._event.wait(timeout)
        self.check()


def _format_egress_block_error(guard: EgressGuard, decision: EgressDecision) -> str:
    """Format egress block details with best-effort rejection reason."""
    request = guard.get_last_request()
    if request and request.rejection_reason:
        return f"Egress blocked ({request.rejection_reason.value}): {decision.value}"
    return f"Egress blocked: {decision.value}"


# =============================================================================
# Agent Configuration
# =============================================================================


class AgentMode(Enum):
    """Operating mode for the agent."""

    INTERACTIVE = "interactive"  # Step-by-step with user confirmation
    AUTONOMOUS = "autonomous"  # Execute full workflow automatically
    PLANNING_ONLY = "planning_only"  # Only generate plan, no execution


@dataclass
class AgentConfig:
    """Configuration for SciWorkflowAgent."""

    mode: AgentMode = AgentMode.INTERACTIVE
    max_iterations: int = 10
    max_tool_calls_per_step: int = 5
    timeout_seconds: int = 300
    egress_policy: str = "ask_always"  # ask_always, ask_sensitive, allow_all
    enable_memory: bool = True
    model: str | None = None  # Override LLM model
    temperature: float = 0.3  # Lower for planning
    research_domain: str | None = None  # For domain-specific scanning
    enable_rag: bool = True  # Use RAG for context

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "mode": self.mode.value,
            "max_iterations": self.max_iterations,
            "max_tool_calls_per_step": self.max_tool_calls_per_step,
            "timeout_seconds": self.timeout_seconds,
            "egress_policy": self.egress_policy,
            "enable_memory": self.enable_memory,
            "model": self.model,
            "temperature": self.temperature,
            "research_domain": self.research_domain,
            "enable_rag": self.enable_rag,
        }


# =============================================================================
# Agent Events (for streaming/UI updates)
# =============================================================================


class AgentEventType(Enum):
    """Types of events emitted by the agent."""

    PLANNING_START = "planning_start"
    PLANNING_COMPLETE = "planning_complete"
    STEP_START = "step_start"
    STEP_COMPLETE = "step_complete"
    STEP_FAILED = "step_failed"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    EGRESS_REQUEST = "egress_request"
    EGRESS_APPROVED = "egress_approved"
    EGRESS_BLOCKED = "egress_blocked"
    USER_PROMPT = "user_prompt"
    MESSAGE = "message"
    WORKFLOW_COMPLETE = "workflow_complete"
    WORKFLOW_FAILED = "workflow_failed"
    ERROR = "error"


@dataclass
class AgentEvent:
    """Event from agent execution."""

    event_type: AgentEventType
    message: str
    data: dict[str, Any] | None = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "event_type": self.event_type.value,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }


# =============================================================================
# Workflow Planner
# =============================================================================


R4_USAGE_GUIDE = """R4 user guidance:
- Prefer user-facing `r4` commands for platform operations. Use
  `umrs-server` only for explicit operator/admin tasks.
- Users can ask naturally with `r4 ask agent "..."`; how-to questions
  become chat, action requests become agent runs.
- Common commands: `r4 health --json`, `r4 model list`,
  `r4 endpoint list`, `r4 endpoint up --model <model> --wait`,
  `r4 chat "<message>" --model <model>`.
- Keep all model traffic through Gateway/R4; do not tell users to call
  vLLM or LiteLLM directly.
- Shell/Python actions are approval-gated. Plan exact commands only when
  they directly satisfy the user's request.
"""


PLANNER_SYSTEM_PROMPT = (
    """You are a scientific workflow planner for the R4 Platform.

Your role is to help scientists create workflows that accomplish their research goals.

Given a user's objective, analyze it and create a step-by-step workflow plan.

Available step types:
- MCP_CALL: Call an MCP tool (e.g., read files, query databases)
- UMRS_JOB: Submit a compute job to the HPC cluster
- RAG_QUERY: Search project documents for relevant information
- LLM_PROMPT: Generate or analyze text using the LLM
- SHELL_EXEC: Execute shell commands (with user approval)
- PYTHON_EXEC: Execute Python code (with user approval)

For each step, specify:
1. description: What this step does
2. step_type: One of the types above
3. tool_name: For MCP_CALL, the tool to use
4. inputs: Required inputs (which may come from previous steps)
5. expected_output: What this step produces

Output your plan as a JSON object with this structure:
{
  "name": "workflow name",
  "description": "workflow description",
  "steps": [
    {
      "id": "step_1",
      "description": "description",
      "step_type": "MCP_CALL",
      "tool_name": "tool_name",
      "inputs": [{"name": "input_name", "source": "user"}],
      "expected_output": "description of output"
    }
  ]
}

Consider data dependencies between steps. Output each step's ID in the
"depends_on" field if it needs data from a previous step.

Be concise but thorough. Prefer using available tools over generating new
code when possible.
"""
    + "\n\n"
    + R4_USAGE_GUIDE
)


def _extract_json_object(content: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            decoded, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return decoded
    raise json.JSONDecodeError("No JSON object found", content, 0)


class WorkflowPlanner:
    """Plans workflows by decomposing user objectives into steps."""

    def __init__(self, client: "Client", config: AgentConfig):
        """Initialize planner.

        Args:
            client: R4 Client for LLM access
            config: Agent configuration
        """
        self.client = client
        self.config = config
        self._tools_cache: list["MCPTool"] | None = None
        self._editor_tools: list[Any] = []
        self._cancellation: AgentCancellation | None = None

    def set_cancellation(self, cancellation: AgentCancellation) -> None:
        self._cancellation = cancellation

    def get_available_tools(self) -> list["MCPTool"]:
        """Get list of available MCP tools."""
        if self._tools_cache is None:
            try:
                self._tools_cache = self.client.mcp.list_tools(
                    _turn_cancel=self._cancellation,
                )
            except AgentCancelledError:
                raise
            except Exception as e:
                logger.warning(f"Failed to list MCP tools: {e}")
                self._tools_cache = []
        return [*self._tools_cache, *self._editor_tools]

    def set_editor_tools(self, tools: list[Any]) -> None:
        """Add editor-governed tools to planning inventory."""
        self._editor_tools = tools

    def _workflow_project_id(self) -> str:
        """Resolve the default project identifier for planned workflows."""
        return self.client.config.project_id or ""

    def _workflow_owner_user_id(self) -> str:
        """Resolve the default owner identifier for planned workflows."""
        get_username = getattr(self.client, "_get_username", None)
        if callable(get_username):
            return get_username()
        return ""

    def plan(
        self,
        objective: str,
        context: str | None = None,
        constraints: list[str] | None = None,
    ) -> WorkflowDefinition:
        """Plan a workflow for the given objective.

        Args:
            objective: User's research objective
            context: Additional context (e.g., from RAG)
            constraints: Any constraints to consider

        Returns:
            WorkflowDefinition with planned steps
        """
        # Build the planning prompt
        tools = self.get_available_tools()
        tools_desc = "\n".join(
            [f"- {t.name}: {t.description}" for t in tools[:50]]
        )  # Limit for context

        prompt = f"""Objective: {objective}

Available MCP Tools:
{tools_desc}

"""
        if context:
            prompt += f"Additional Context:\n{context}\n\n"

        if constraints:
            prompt += "Constraints:\n" + "\n".join(f"- {c}" for c in constraints) + "\n\n"

        prompt += "Create a workflow plan to accomplish this objective."

        # Call LLM for planning
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        response = self.client.chat(
            messages=messages,
            model=self.config.model,
            temperature=self.config.temperature,
            _turn_cancel=self._cancellation,
        )

        # Parse the response
        try:
            content = response.content
            plan_data = _extract_json_object(content or "")
            return self._parse_plan(plan_data, objective)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse plan: {e}")
            # Return a minimal workflow with just an LLM step
            return WorkflowDefinition(
                name="Simple Query",
                description=objective,
                project_id=self._workflow_project_id(),
                owner_user_id=self._workflow_owner_user_id(),
                steps=[
                    WorkflowStep(
                        id="step_1",
                        description="Answer the user's question directly",
                        step_type=StepType.LLM_PROMPT,
                        config={"prompt": objective},
                    )
                ],
            )

    def _parse_plan(self, data: dict[str, Any], fallback_desc: str) -> WorkflowDefinition:
        """Parse plan data into WorkflowDefinition."""
        steps = []
        for step_data in data.get("steps", []):
            step_type = StepType.LLM_PROMPT
            type_str = step_data.get("step_type", "").upper()
            for st in StepType:
                if st.value.upper() == type_str:
                    step_type = st
                    break

            inputs = []
            for inp_data in step_data.get("inputs", []):
                inputs.append(
                    WorkflowInput(
                        name=inp_data.get("name", ""),
                        input_type=inp_data.get("data_type") or inp_data.get("type", "string"),
                        required=inp_data.get("required", True),
                        description=inp_data.get("description", ""),
                        source=inp_data.get("source"),
                    )
                )

            outputs = []
            expected_output = step_data.get("expected_output")
            if expected_output:
                outputs.append(
                    WorkflowOutput(
                        name="output",
                        output_type="any",
                        description=expected_output,
                    )
                )

            steps.append(
                WorkflowStep(
                    id=step_data.get("id", f"step_{len(steps) + 1}"),
                    name=step_data.get("name", ""),
                    description=step_data.get("description", ""),
                    step_type=step_type,
                    tool_name=step_data.get("tool_name"),
                    inputs=inputs,
                    outputs=outputs,
                    depends_on=step_data.get("depends_on", []),
                    config=step_data.get("config", {}),
                )
            )

        return WorkflowDefinition(
            name=data.get("name", "Workflow"),
            description=data.get("description", fallback_desc),
            project_id=data.get("project_id") or self._workflow_project_id(),
            owner_user_id=data.get("owner_user_id") or self._workflow_owner_user_id(),
            steps=steps,
        )


# =============================================================================
# Workflow Executor
# =============================================================================


class WorkflowExecutor:
    """Executes workflow steps with egress control."""

    def __init__(
        self,
        client: "Client",
        config: AgentConfig,
        egress_guard: EgressGuard,
        on_event: Callable[[AgentEvent], None] | None = None,
    ):
        """Initialize executor.

        Args:
            client: R4 Client
            config: Agent configuration
            egress_guard: Egress control
            on_event: Callback for execution events
        """
        self.client = client
        self.config = config
        self.egress_guard = egress_guard
        self._on_event = on_event
        self._step_results: dict[str, StepResult] = {}
        self._current_execution_id: str | None = None
        self._editor_tool_call: Callable[[str, dict[str, Any]], Any] | None = None
        self._editor_governed = False
        self._cancel_check: Callable[[], bool] = lambda: False
        self._cancellation: AgentCancellation | None = None

    def set_editor_tool_call(self, callback: Callable[[str, dict[str, Any]], Any]) -> None:
        """Set callback for namespaced editor-governed tools."""
        self._editor_tool_call = callback
        self._editor_governed = True

    def set_cancel_check(self, callback: Callable[[], bool]) -> None:
        self._cancel_check = callback

    def set_cancellation(self, cancellation: AgentCancellation) -> None:
        self._cancellation = cancellation
        self.set_cancel_check(cancellation.is_set)

    def _raise_if_cancelled(self) -> None:
        if self._cancel_check():
            raise AgentCancelledError("Agent request cancelled")

    def emit(self, event_type: AgentEventType, message: str, data: dict | None = None) -> None:
        """Emit an event."""
        if self._on_event:
            self._on_event(AgentEvent(event_type, message, data))

    def execute_workflow(
        self,
        workflow: WorkflowDefinition,
        inputs: dict[str, Any] | None = None,
    ) -> WorkflowExecution:
        """Execute a workflow.

        Args:
            workflow: Workflow to execute
            inputs: Initial inputs

        Returns:
            WorkflowExecution with results
        """
        execution = WorkflowExecution(
            workflow=workflow,
            status=WorkflowStatus.RUNNING,
            input_values=inputs or {},
        )
        previous_execution_id = self._current_execution_id
        self._current_execution_id = execution.execution_id

        try:
            for step_id in workflow.execution_order():
                self._raise_if_cancelled()
                step = self._find_step(workflow, step_id)
                if step is None:
                    continue

                self.emit(
                    AgentEventType.STEP_START,
                    f"Starting step: {step.description}",
                    {"step_id": step_id},
                )

                if not self._run_workflow_step(execution, step, step_id, inputs or {}):
                    break

            if execution.status not in {WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}:
                execution.status = WorkflowStatus.COMPLETED
                self.emit(AgentEventType.WORKFLOW_COMPLETE, "Workflow completed successfully")

        except Exception as e:
            execution.status = WorkflowStatus.FAILED
            self.emit(AgentEventType.WORKFLOW_FAILED, str(e))
        finally:
            self._current_execution_id = previous_execution_id

        execution.completed_at = datetime.now()
        return execution

    @staticmethod
    def _find_step(workflow: WorkflowDefinition, step_id: str) -> WorkflowStep | None:
        """Return the step with the given id, or ``None`` when absent."""
        return next((s for s in workflow.steps if s.id == step_id), None)

    def _run_workflow_step(
        self,
        execution: WorkflowExecution,
        step: WorkflowStep,
        step_id: str,
        inputs: dict[str, Any],
    ) -> bool:
        """Execute one workflow step; return ``True`` to continue the loop."""
        try:
            result = self._execute_step(step, inputs)
            self._raise_if_cancelled()
            execution.step_results[step_id] = result
            self._step_results[step_id] = result

            if result.status == StepStatus.COMPLETED:
                self.emit(
                    AgentEventType.STEP_COMPLETE,
                    f"Completed: {step.description}",
                    {"step_id": step_id, "output": result.output},
                )
                return True

            self.emit(
                AgentEventType.STEP_FAILED,
                f"Failed: {result.error}",
                {"step_id": step_id, "error": result.error},
            )
            execution.status = WorkflowStatus.FAILED
            return False

        except AgentCancelledError:
            execution.status = WorkflowStatus.CANCELLED
            return False
        except Exception as e:
            error_msg = str(e)
            execution.step_results[step_id] = StepResult(
                step_id=step_id,
                status=StepStatus.FAILED,
                error=error_msg,
            )
            self.emit(AgentEventType.ERROR, error_msg, {"step_id": step_id})
            execution.status = WorkflowStatus.FAILED
            return False

    def _execute_step(self, step: WorkflowStep, inputs: dict[str, Any]) -> StepResult:
        """Execute a single workflow step."""
        # Resolve inputs from previous steps
        resolved_inputs = self._resolve_inputs(step, inputs)

        if step.step_type == StepType.MCP_CALL:
            return self._execute_mcp_call(step, resolved_inputs)
        elif step.step_type == StepType.RAG_QUERY:
            return self._execute_rag_query(step, resolved_inputs)
        elif step.step_type == StepType.LLM_PROMPT:
            return self._execute_llm_prompt(step, resolved_inputs)
        elif step.step_type == StepType.UMRS_JOB:
            return self._execute_umrs_job(step, resolved_inputs)
        elif step.step_type == StepType.SHELL_EXEC:
            if self._editor_governed:
                return self._blocked_native_execution(step)
            return self._execute_shell(step, resolved_inputs)
        elif step.step_type == StepType.PYTHON_EXEC:
            if self._editor_governed:
                return self._blocked_native_execution(step)
            return self._execute_python(step, resolved_inputs)
        else:
            return StepResult(
                step_id=step.id,
                status=StepStatus.FAILED,
                error=f"Unknown step type: {step.step_type}",
            )

    def _blocked_native_execution(self, step: WorkflowStep) -> StepResult:
        return StepResult(
            step_id=step.id,
            status=StepStatus.BLOCKED,
            error="Native execution is disabled for editor-governed sessions",
        )

    def _resolve_inputs(self, step: WorkflowStep, initial_inputs: dict[str, Any]) -> dict[str, Any]:
        """Resolve step inputs from initial inputs and previous step outputs."""
        resolved = dict(initial_inputs)

        for inp in step.inputs:
            if inp.source and inp.source.startswith("step_"):
                # Get from previous step output
                step_id = inp.source.split(".")[0]
                if step_id in self._step_results:
                    prev_result = self._step_results[step_id]
                    if prev_result.output is not None:
                        resolved[inp.name] = prev_result.output

        return resolved

    def _execute_mcp_call(self, step: WorkflowStep, inputs: dict[str, Any]) -> StepResult:
        """Execute an MCP tool call with egress control."""
        if not step.tool_name:
            return StepResult(
                step_id=step.id, status=StepStatus.FAILED, error="No tool name specified"
            )

        # Build arguments
        args = step.config.get("arguments", {})
        args.update(inputs)

        if step.tool_name.startswith(("workspace.", "terminal.")):
            return self._execute_editor_tool(step, args)

        # Check egress
        decision = self.egress_guard.check(
            tool_name=step.tool_name,
            destination_url=f"mcp://{step.tool_name}",
            payload=args,
        )
        self.emit(
            AgentEventType.EGRESS_REQUEST,
            f"Egress check for {step.tool_name}",
            {"decision": decision.value},
        )

        if decision not in {EgressDecision.APPROVED, EgressDecision.TRUSTED}:
            return StepResult(
                step_id=step.id,
                status=StepStatus.BLOCKED,
                error=_format_egress_block_error(self.egress_guard, decision),
            )

        # Execute tool call
        self.emit(AgentEventType.TOOL_CALL, f"Calling {step.tool_name}", {"args": args})

        try:
            result = self.client.mcp.invoke(
                step.tool_name,
                _meta=self._mcp_chain_metadata(step),
                _turn_cancel=self._cancellation,
                **args,
            )
            self._raise_if_cancelled()
            if not result.success:
                return StepResult(
                    step_id=step.id,
                    status=StepStatus.FAILED,
                    error=result.error or "MCP call failed",
                )
            self.emit(AgentEventType.TOOL_RESULT, f"Got result from {step.tool_name}")
            return StepResult(step_id=step.id, status=StepStatus.COMPLETED, output=result.result)
        except Exception as e:
            self._raise_if_cancelled()
            return StepResult(step_id=step.id, status=StepStatus.FAILED, error=str(e))

    def _execute_editor_tool(self, step: WorkflowStep, args: dict[str, Any]) -> StepResult:
        if self._editor_tool_call is None or not step.tool_name:
            return StepResult(
                step_id=step.id, status=StepStatus.BLOCKED, error="Editor tool unavailable"
            )
        self.emit(AgentEventType.TOOL_CALL, f"Calling {step.tool_name}")
        try:
            result = self._editor_tool_call(step.tool_name, args)
            self.emit(AgentEventType.TOOL_RESULT, f"Got result from {step.tool_name}")
            return StepResult(step_id=step.id, status=StepStatus.COMPLETED, output=result)
        except Exception:
            return StepResult(
                step_id=step.id, status=StepStatus.BLOCKED, error="Editor tool denied"
            )

    def _mcp_chain_metadata(self, step: WorkflowStep) -> dict[str, Any]:
        """Build chain metadata for agent-driven MCP tool calls."""
        metadata = {
            "chain_id": self._current_execution_id or step.id,
            "workflow_step_id": step.id,
        }
        target_tool = (
            step.config.get("target_tool")
            or step.config.get("targetTool")
            or step.config.get("next_tool")
        )
        if target_tool:
            metadata["target_tool"] = target_tool
        return metadata

    def _execute_rag_query(self, step: WorkflowStep, inputs: dict[str, Any]) -> StepResult:
        """Search project documents: the passages UMRS keeps and its verdict.

        The retrieve verb (ADR-093 D-10) composes no answer; a later
        LLM_PROMPT step reads ``sources`` (passages titled by ``source_name``)
        and ``sufficiency``.
        """
        from r4.model_scale import configured_reader_fields

        query = step.config.get("query", inputs.get("query", step.description))
        project_id = step.config.get("project_id")
        # The LLM_PROMPT step reads the passages with config.model: name it and
        # its D-14 profile (r4-scale-router-10).
        reader = configured_reader_fields(self.config.model, self.client)

        try:
            result = self.client.rag.retrieve(
                query,
                project_id=project_id,
                _turn_cancel=self._cancellation,
                **reader,
            )
            self._raise_if_cancelled()
            return StepResult(
                step_id=step.id,
                status=StepStatus.COMPLETED,
                output={
                    "query": result.query,
                    "sufficiency": result.sufficiency.to_dict(),
                    "sources": [passage.to_dict() for passage in result.passages],
                },
            )
        except Exception as e:
            self._raise_if_cancelled()
            return StepResult(step_id=step.id, status=StepStatus.FAILED, error=str(e))

    def _execute_llm_prompt(self, step: WorkflowStep, inputs: dict[str, Any]) -> StepResult:
        """Execute an LLM prompt."""
        prompt = step.config.get("prompt", step.description)

        # Substitute inputs into prompt
        for key, value in inputs.items():
            prompt = prompt.replace(f"{{{key}}}", str(value))

        messages = [{"role": "user", "content": prompt}]

        system = step.config.get("system_prompt")
        if system:
            messages.insert(0, {"role": "system", "content": system})

        try:
            response = self.client.chat(
                messages=messages,
                model=self.config.model,
                _turn_cancel=self._cancellation,
            )
            self._raise_if_cancelled()
            return StepResult(step_id=step.id, status=StepStatus.COMPLETED, output=response.content)
        except Exception as e:
            self._raise_if_cancelled()
            return StepResult(step_id=step.id, status=StepStatus.FAILED, error=str(e))

    def _execute_umrs_job(self, step: WorkflowStep, inputs: dict[str, Any]) -> StepResult:
        """Execute a UMRS durable job, polling until terminal state.

        Submits the job via the job client and polls for completion.
        Supports timeout and polling interval from step config:
        - ``poll_interval``: seconds between polls (default: 5.0)
        - ``poll_timeout``: seconds to wait before giving up (default: from step.timeout_seconds)
        """
        job_spec = step.config.get("job_spec", {})
        job_spec.update(inputs)

        # Check egress for job submission
        decision = self.egress_guard.check(
            tool_name=step.tool_name or "umrs_job",
            destination_url="umrs://job_submit",
            payload=job_spec,
        )
        if decision not in {EgressDecision.APPROVED, EgressDecision.TRUSTED}:
            return StepResult(
                step_id=step.id,
                status=StepStatus.BLOCKED,
                error=_format_egress_block_error(self.egress_guard, decision),
            )

        try:
            # Submit job using client's job interface
            job = self.client.jobs.create(
                job_type=step.config.get("job_type", "agent_job"),
                spec=job_spec,
                project_id=step.config.get("project_id") or self.client.config.project_id,
                _turn_cancel=self._cancellation,
            )
            self._cancel_umrs_job_if_requested(job.job_id)

            self.emit(
                AgentEventType.TOOL_CALL,
                f"UMRS job submitted: {job.job_id}",
                {"job_id": job.job_id, "job_type": step.config.get("job_type", "agent_job")},
            )

            poll_interval = float(step.config.get("poll_interval", 5.0))
            poll_timeout = float(step.config.get("poll_timeout", step.timeout_seconds))
            return self._poll_umrs_job(step, job, poll_interval, poll_timeout)
        except AgentCancelledError:
            raise
        except Exception as e:
            return StepResult(step_id=step.id, status=StepStatus.FAILED, error=str(e))

    def _poll_umrs_job(
        self, step: WorkflowStep, job: Any, poll_interval: float, poll_timeout: float
    ) -> StepResult:
        """Poll a submitted UMRS job until it reaches a terminal state or times out."""
        elapsed = 0.0
        terminal_statuses = {"completed", "failed", "cancelled"}

        while elapsed < poll_timeout:
            self._cancel_umrs_job_if_requested(job.job_id)
            self._wait_poll_interval(poll_interval)
            elapsed += poll_interval
            self._cancel_umrs_job_if_requested(job.job_id)

            try:
                job = self.client.jobs.get(job.job_id, _turn_cancel=self._cancellation)
            except Exception:
                self._cancel_umrs_job_if_requested(job.job_id)
                continue  # Transient error, retry
            self._cancel_umrs_job_if_requested(job.job_id)

            if job.status in terminal_statuses:
                return self._umrs_terminal_result(step, job)

        return StepResult(
            step_id=step.id,
            status=StepStatus.FAILED,
            error=(f"UMRS job {job.job_id} timed out after {poll_timeout}s (status: {job.status})"),
        )

    def _wait_poll_interval(self, poll_interval: float) -> None:
        """Wait one poll interval, using the cancellation event when available."""
        if self._cancellation is not None:
            self._cancellation.wait(poll_interval)
        else:
            import time as _time

            _time.sleep(poll_interval)

    def _umrs_terminal_result(self, step: WorkflowStep, job: Any) -> StepResult:
        """Build a StepResult from a UMRS job that has reached a terminal state."""
        if job.status != "completed":
            return StepResult(
                step_id=step.id,
                status=StepStatus.FAILED,
                error=f"UMRS job {job.job_id} ended with status: {job.status}",
            )
        self.emit(
            AgentEventType.TOOL_RESULT,
            f"UMRS job completed: {job.job_id}",
            {"job_id": job.job_id},
        )
        return StepResult(
            step_id=step.id,
            status=StepStatus.COMPLETED,
            output=self._collect_job_outputs(job),
        )

    def _collect_job_outputs(self, job: Any) -> dict[str, Any]:
        """Collect output metadata from a completed job's commit attempt."""
        outputs: dict[str, Any] = {"job_id": job.job_id, "status": job.status}
        if job.commit_attempt_id:
            commit_attempt = next(
                (a for a in job.attempts if a.attempt_id == job.commit_attempt_id),
                None,
            )
            if commit_attempt:
                outputs["backend"] = commit_attempt.backend
                outputs["backend_ref"] = commit_attempt.backend_ref
                outputs["metrics"] = commit_attempt.metrics
        return outputs

    def _cancel_umrs_job_if_requested(self, job_id: str) -> None:
        if not self._cancel_check():
            return
        try:
            self.client.jobs.cancel(job_id)
        except Exception:
            logger.warning("Failed to cancel UMRS job %s", job_id)
        raise AgentCancelledError("Agent request cancelled")

    def _execute_shell(self, step: WorkflowStep, inputs: dict[str, Any]) -> StepResult:
        """Execute shell command (requires explicit approval)."""
        command = step.config.get("command", "")

        # Shell execution always requires egress check
        decision = self.egress_guard.check(
            tool_name=step.tool_name or "shell_exec",
            destination_url="shell://local",
            payload={"command": command},
        )
        if decision not in {EgressDecision.APPROVED, EgressDecision.TRUSTED}:
            return StepResult(
                step_id=step.id,
                status=StepStatus.BLOCKED,
                error=_format_egress_block_error(self.egress_guard, decision),
            )

        try:
            result = _run_shell_step(command)
            return StepResult(
                step_id=step.id,
                status=StepStatus.COMPLETED,
                output={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                },
            )
        except Exception as e:
            return StepResult(step_id=step.id, status=StepStatus.FAILED, error=str(e))

    def _execute_python(self, step: WorkflowStep, inputs: dict[str, Any]) -> StepResult:
        """Execute Python code (requires explicit approval)."""
        code = step.config.get("code", "")

        # Python execution always requires egress check
        decision = self.egress_guard.check(
            tool_name=step.tool_name or "python_exec",
            destination_url="python://local",
            payload={"code": code},
        )
        if decision not in {EgressDecision.APPROVED, EgressDecision.TRUSTED}:
            return StepResult(
                step_id=step.id,
                status=StepStatus.BLOCKED,
                error=_format_egress_block_error(self.egress_guard, decision),
            )

        refused = _python_step_refusal()
        if refused:
            return StepResult(step_id=step.id, status=StepStatus.FAILED, error=refused)
        try:
            # Execute in isolated namespace
            namespace = {"__builtins__": __builtins__, "inputs": inputs}
            exec(code, namespace)
            result = namespace.get("result", None)
            return StepResult(step_id=step.id, status=StepStatus.COMPLETED, output=result)
        except Exception as e:
            return StepResult(step_id=step.id, status=StepStatus.FAILED, error=str(e))


def _run_shell_step(command: Any) -> Any:
    """A SHELL_EXEC step through the local tool sandbox (ADR-101, review ESC-5).

    ``wrap_spawn`` returns the arguments unchanged while the sandbox is off;
    on, a :class:`~r4.companion.sandbox.SandboxError` fails the step and
    nothing runs unconfined.
    """
    import os
    import shlex
    import subprocess

    from r4.companion.sandbox import wrap_spawn

    command_args = command if isinstance(command, list) else shlex.split(command)
    spec = wrap_spawn(command_args, shell=False, cwd=None, env=dict(os.environ))
    return subprocess.run(
        spec.argv,
        shell=spec.shell,
        env=spec.env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _python_step_refusal() -> str:
    """Why a PYTHON_EXEC step may not run: it runs in this process (``exec``),
    which no sandbox confines; "" while the sandbox is off (ADR-101 D-4)."""
    from r4.companion.sandbox import current_sandbox

    if not current_sandbox().setting.enabled:
        return ""
    return (
        "sandbox.refused: a python step runs inside r4 itself and cannot be sandboxed; "
        "turn the sandbox off for this run or use a shell step"
    )


# =============================================================================
# SciWorkflowAgent (Main Agent Class)
# =============================================================================


class SciWorkflowAgent:
    """Main agent class for guiding scientists through workflow creation.

    Features:
    - Natural language objective understanding
    - Automatic workflow planning
    - Tool discovery and execution
    - Egress surveillance for data protection
    - Multi-turn conversation support
    - Memory and context management

    Example:
        from r4 import Client
        from r4.agent import SciWorkflowAgent

        client = Client()
        agent = SciWorkflowAgent(client)

        # Simple objective
        result = agent.run("Analyze the protein structure in file.pdb")

        # Interactive mode
        for event in agent.run_interactive("Build a simulation workflow"):
            print(event.message)
    """

    def __init__(
        self,
        client: "Client",
        config: AgentConfig | None = None,
        on_event: Callable[[AgentEvent], None] | None = None,
    ):
        """Initialize the agent.

        Args:
            client: R4 Client for LLM, RAG, and MCP access
            config: Agent configuration
            on_event: Callback for agent events
        """
        self.client = client
        self.config = config or AgentConfig()
        self._on_event = on_event

        # Initialize components
        get_username = getattr(client, "_get_username", None)
        user_id = get_username() if callable(get_username) else ""
        project_id = client.config.project_id or ""
        self.egress_guard = EgressGuard(
            user_id=user_id,
            project_id=project_id,
            auto_approve=self.config.egress_policy == "allow_all",
        )
        self.planner = WorkflowPlanner(client, self.config)
        self.executor = WorkflowExecutor(client, self.config, self.egress_guard, self._emit_event)

        # Conversation history
        self._messages: list[dict[str, str]] = []
        self._current_workflow: WorkflowDefinition | None = None
        self._cancel_check: Callable[[], bool] = lambda: False
        self._cancellation: AgentCancellation | None = None

    def on_event(self, callback: Callable[[AgentEvent], None]) -> None:
        """Register event callback."""
        self._on_event = callback
        self.executor._on_event = callback

    def _emit_event(self, event: AgentEvent) -> None:
        """Emit an event."""
        if self._on_event:
            self._on_event(event)

    def set_cancel_check(self, callback: Callable[[], bool]) -> None:
        self._cancel_check = callback
        self.executor.set_cancel_check(callback)

    def set_cancellation(self, cancellation: AgentCancellation) -> None:
        self._cancellation = cancellation
        self.planner.set_cancellation(cancellation)
        self.executor.set_cancellation(cancellation)
        self._cancel_check = cancellation.is_set

    def _raise_if_cancelled(self) -> None:
        if self._cancel_check():
            raise AgentCancelledError("Agent request cancelled")

    def run(
        self,
        objective: str,
        inputs: dict[str, Any] | None = None,
        context: str | None = None,
    ) -> WorkflowExecution:
        """Run a workflow for the given objective.

        Args:
            objective: Research objective in natural language
            inputs: Initial inputs for the workflow
            context: Additional context

        Returns:
            WorkflowExecution with results
        """
        self._emit_event(
            AgentEvent(AgentEventType.PLANNING_START, f"Planning workflow for: {objective}")
        )
        self._raise_if_cancelled()

        # Get RAG context if enabled
        if self.config.enable_rag and not context:
            try:
                rag_results = self.client.rag.search(
                    objective,
                    top_k=3,
                    _turn_cancel=self._cancellation,
                )
                self._raise_if_cancelled()
                if rag_results.sources:
                    context = "\n".join(source.content for source in rag_results.sources[:3])
            except AgentCancelledError:
                raise
            except Exception as e:
                logger.debug(f"RAG search failed: {e}")

        # Plan the workflow
        workflow = self.planner.plan(objective, context=context)
        self._raise_if_cancelled()
        self._current_workflow = workflow

        self._emit_event(
            AgentEvent(
                AgentEventType.PLANNING_COMPLETE,
                f"Created workflow with {len(workflow.steps)} steps",
                {"workflow_name": workflow.name, "steps": len(workflow.steps)},
            )
        )

        # Execute if not planning-only mode
        if self.config.mode == AgentMode.PLANNING_ONLY:
            return WorkflowExecution(
                workflow=workflow,
                status=WorkflowStatus.PENDING,
                input_values=inputs or {},
            )

        return self.executor.execute_workflow(workflow, inputs)

    def run_interactive(
        self,
        objective: str,
        inputs: dict[str, Any] | None = None,
    ) -> Iterator[AgentEvent]:
        """Run interactively, yielding events.

        Args:
            objective: Research objective
            inputs: Initial inputs

        Yields:
            AgentEvent objects for UI updates
        """
        events: list[AgentEvent] = []

        def collect_event(event: AgentEvent) -> None:
            events.append(event)

        old_callback = self._on_event
        self._on_event = collect_event
        self.executor._on_event = collect_event

        try:
            # Run planning
            self._emit_event(AgentEvent(AgentEventType.PLANNING_START, f"Planning: {objective}"))

            workflow = self.planner.plan(objective)
            self._current_workflow = workflow

            yield AgentEvent(
                AgentEventType.PLANNING_COMPLETE,
                f"Workflow: {workflow.name}",
                {"steps": [s.description for s in workflow.steps]},
            )

            # In interactive mode, yield each step for user confirmation
            if self.config.mode == AgentMode.INTERACTIVE:
                for step in workflow.steps:
                    yield AgentEvent(
                        AgentEventType.USER_PROMPT,
                        f"Execute step: {step.description}?",
                        {"step_id": step.id, "step_type": step.step_type.value},
                    )
                    # In actual use, would wait for user confirmation here

            # Execute
            execution = self.executor.execute_workflow(workflow, inputs)

            # Yield collected events
            for event in events:
                yield event

            yield AgentEvent(
                AgentEventType.WORKFLOW_COMPLETE,
                f"Status: {execution.status.value}",
                {"execution_id": execution.id},
            )

        finally:
            self._on_event = old_callback
            self.executor._on_event = old_callback

    def chat(self, message: str) -> str:
        """Chat with the agent in conversational mode.

        Args:
            message: User message

        Returns:
            Agent response
        """
        self._messages.append({"role": "user", "content": message})

        # Check if this looks like a workflow request
        if any(
            kw in message.lower()
            for kw in ["create", "build", "run", "analyze", "workflow", "help me"]
        ):
            # Plan a workflow
            result = self.run(message)
            if result.status == WorkflowStatus.COMPLETED:
                response = f"Completed workflow: {result.workflow.name}\n"
                for step_id, step_result in result.step_results.items():
                    response += f"- {step_id}: {step_result.status.value}\n"
            else:
                response = f"Workflow failed: {result.status.value}"
        else:
            # Regular chat
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful AI assistant for scientists using the R4 Platform. "
                        "Help with research questions and platform operations.\n\n"
                        f"{R4_USAGE_GUIDE}"
                    ),
                },
                *self._messages,
            ]
            result = self.client.chat(messages=messages, model=self.config.model)
            response = result.content or "I'm sorry, I couldn't generate a response."

        self._messages.append({"role": "assistant", "content": response})
        return response

    def get_current_workflow(self) -> WorkflowDefinition | None:
        """Get the current workflow definition."""
        return self._current_workflow

    def list_available_tools(self) -> list[dict[str, str]]:
        """List available MCP tools."""
        tools = self.planner.get_available_tools()
        return [{"name": t.name, "description": t.description} for t in tools]

    def set_approval_callback(self, callback: Callable[[EgressRequest], EgressDecision]) -> None:
        """Set custom approval callback for egress control.

        Args:
            callback: Function that receives EgressRequest and returns an egress decision
        """
        self.egress_guard.approval_callback = callback

    def set_editor_tools(
        self,
        tools: list[Any],
        callback: Callable[[str, dict[str, Any]], Any],
    ) -> None:
        """Connect editor-governed tools without granting local execution authority."""
        self.planner.set_editor_tools(tools)
        self.executor.set_editor_tool_call(callback)
