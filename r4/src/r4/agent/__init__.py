"""Agentic Workflow Builder for R4 Platform.

This module provides an AI agent that guides scientists through creating,
executing, and persisting scientific workflows. The agent uses the platform's
LLM, discovers MCP tools, and ensures all outbound data access is surveilled.

Key Features:
- Natural language workflow creation
- MCP tool discovery and execution
- Egress surveillance for data protection
- Workflow persistence and replay
- Multi-interface support (CLI, Jupyter, Web)

Example Usage:
    from r4 import Client

    client = Client()

    # Interactive agent session
    async with client.agent.session() as agent:
        result = await agent.chat("Build a workflow to analyze my data")
        workflow = await agent.save_workflow("my-workflow")

    # Run saved workflow
    execution = await client.workflows.run("my-workflow")
"""

from .core import (
    AgentConfig,
    AgentEvent,
    AgentEventType,
    AgentMode,
    SciWorkflowAgent,
    WorkflowExecutor,
    WorkflowPlanner,
)
from .egress import (
    EgressDecision,
    EgressGuard,
    EgressPolicy,
    EgressRequest,
    ProjectEgressPolicy,
    TrustSession,
)
from .experiment import (
    Experiment,
    ExperimentResults,
    ExperimentRunner,
    ExperimentStatus,
    SlurmExperimentSpec,
    SweepStrategy,
)
from .hypothesis import (
    Evidence,
    Hypothesis,
    HypothesisManager,
    HypothesisStatus,
)
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
from .output_parser import (
    CompositeParser,
    CSVParser,
    JSONParser,
    LLMParser,
    OutputParser,
    RegexParser,
)
from .parameter_sweep import (
    ParameterSweep,
    SweepResults,
)
from .research_orchestrator import (
    IterationRecord,
    OrchestratorStatus,
    ResearchBudget,
    ResearchOrchestrator,
    ResearchSession,
)
from .scanner import (
    ContentScanner,
    ScanMatch,
    ScanResult,
    SensitivityLevel,
)
from .session import AgentSession, SessionMessage, SessionState

__all__ = [
    # Core Agent
    "SciWorkflowAgent",
    "AgentConfig",
    "AgentMode",
    "AgentEvent",
    "AgentEventType",
    "WorkflowPlanner",
    "WorkflowExecutor",
    # Session
    "AgentSession",
    "SessionState",
    "SessionMessage",
    # Workflow Models
    "WorkflowDefinition",
    "WorkflowStep",
    "WorkflowInput",
    "WorkflowOutput",
    "WorkflowExecution",
    "StepResult",
    "StepType",
    "StepStatus",
    "WorkflowStatus",
    # Egress Control
    "EgressGuard",
    "EgressRequest",
    "EgressPolicy",
    "EgressDecision",
    "TrustSession",
    "ProjectEgressPolicy",
    # Content Scanner
    "ContentScanner",
    "ScanResult",
    "ScanMatch",
    "SensitivityLevel",
    # Research Orchestrator
    "ResearchOrchestrator",
    "ResearchBudget",
    "ResearchSession",
    "IterationRecord",
    "OrchestratorStatus",
    # Hypothesis Framework
    "Hypothesis",
    "HypothesisStatus",
    "HypothesisManager",
    "Evidence",
    # Experiment Framework (Phase 2)
    "Experiment",
    "ExperimentResults",
    "ExperimentRunner",
    "ExperimentStatus",
    "SlurmExperimentSpec",
    "SweepStrategy",
    # Output Parsers (Phase 2)
    "OutputParser",
    "RegexParser",
    "JSONParser",
    "CSVParser",
    "LLMParser",
    "CompositeParser",
    # Parameter Sweep (Phase 2)
    "ParameterSweep",
    "SweepResults",
]
