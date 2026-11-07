"""Research Loop — hypothesis-driven scientific automation for R4.

.. note::
   The ``LoopOrchestrator`` module (``orchestrator.py``) and the Phase-2
   phase-handler module (``agent_exec.py``, orphaned by the deletion)
   were deleted 2026-09-22: both were instantiated only in tests (gap
   row D-32 / defect V-10). The designed loop home is the server-hosted
   engine (``umrs.knowledge.loop`` + ``research_loop_job``, ADR-036)
   behind the client ResearchRuntime SPI (``r4/src/r4/runtime/``) — see
   the gap analysis D-32 and ROADMAP phase 2. ``LoopDecision`` lives in
   ``state.py``.

Composes three existing R4 subsystems:
- ResearchOrchestrator (single-agent iteration, A2L provenance)
- TeamupSession (multi-agent coordination)
- UMRS WorkflowPipeline (durable DAG execution)

API Reference:
- ``ResearchLoopState`` — persistent loop state (hypotheses, experiments, evidence)
- ``LoopDecision`` — decision outcome of the DECIDING phase
- ``HypothesisBridge`` — converts between loop records and agent models
- ``ExperimentBridge`` — converts protocols to UMRS pipeline definitions
- ``EvidenceBridge`` — converts results to structured evidence records
- ``RESEARCH_ROLES`` — the research roles' prompts (the research-round plan template reads them)
"""

from .bridges import EvidenceBridge, ExperimentBridge, HypothesisBridge
from .publish import generate_paper_draft, generate_report
from .state import (
    AgentAssignment,
    AgentRole,
    AgentStatus,
    EvidenceRecord,
    ExperimentRecord,
    ExperimentStatus,
    HypothesisRecord,
    HypothesisStatus,
    LoopBudget,
    LoopDecision,
    LoopPhase,
    ResearchLoopState,
)
from .team import RESEARCH_ROLES

__all__ = [
    # State
    "ResearchLoopState",
    "LoopPhase",
    "LoopBudget",
    "LoopDecision",
    "HypothesisRecord",
    "HypothesisStatus",
    "ExperimentRecord",
    "ExperimentStatus",
    "EvidenceRecord",
    "AgentAssignment",
    "AgentRole",
    "AgentStatus",
    # Bridges
    "HypothesisBridge",
    "ExperimentBridge",
    "EvidenceBridge",
    # Team
    "RESEARCH_ROLES",
    # Publishing
    "generate_report",
    "generate_paper_draft",
]
