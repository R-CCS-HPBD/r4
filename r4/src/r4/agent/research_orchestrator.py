"""Research Orchestrator — autonomous scientific research loop.

Composes existing R4 agent components (WorkflowPlanner, WorkflowExecutor,
DeepResearchLoop) with A2L artifact tracking into a closed-loop autonomous
research agent.

The loop: plan → execute → record (A2L) → observe → replan → ...

Key features:
- Bounded loop with budget controls (max_iterations, max_wall_clock, max_cost)
- Checkpoint/resume via A2L agent-executions
- Automatic artifact lineage tracking via A2L /derive
- Integration with UMRS durable jobs for heavy compute
- Egress surveillance on all tool calls and shell execution

Usage:
    from r4 import Client
    from r4.agent import ResearchOrchestrator

    client = Client()
    orchestrator = ResearchOrchestrator(client)

    result = orchestrator.run("Analyze protein folding dynamics under pH variation")
    print(result.synthesis)
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Iterator, Optional

from .belief_state import BeliefState
from .belief_updater import BeliefUpdater
from .core import (
    AgentConfig,
    AgentEvent,
    AgentEventType,
    AgentMode,
    SciWorkflowAgent,
)
from .deep_research import (
    DeepResearchLoop,
    ResearchSource,
)
from .hypothesis import (
    Evidence,
    Hypothesis,
    HypothesisManager,
    HypothesisStatus,
)
from .models import (
    StepStatus,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowStatus,
)
from .working_research_state import WorkingResearchState

if TYPE_CHECKING:
    from science.domain import ScienceDomain
    from science.experiment import Experiment, ExperimentResults
    from science.reward import ScientificReward

    from ..a2l_client import A2LClient
    from ..client import Client
    from .experiment import ExperimentRunner

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Orchestrator Models ─────────────────────────────────────────────────────


class OrchestratorStatus(str, Enum):
    """Status of an orchestrator session."""

    IDLE = "idle"
    PLANNING = "planning"
    HYPOTHESIZING = "hypothesizing"  # Formulating or revising hypotheses
    EXPERIMENTING = "experimenting"  # Running HPC experiments (Phase 2)
    EXECUTING = "executing"
    OBSERVING = "observing"
    EVALUATING = "evaluating"  # Evaluating evidence against hypotheses
    REPLANNING = "replanning"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class IterationRecord:
    """Record of one plan→execute→observe cycle."""

    iteration: int
    plan: WorkflowDefinition
    execution: Optional[WorkflowExecution] = None
    observation: str = ""  # LLM observation of results
    replan_decision: str = ""  # "continue", "done", "failed"
    artifacts_created: list[str] = field(default_factory=list)  # A2L artifact IDs
    hypotheses_tested: list[str] = field(default_factory=list)  # Hypothesis IDs tested
    evidence_collected: list[Evidence] = field(default_factory=list)  # Evidence from this iteration
    elapsed_seconds: float = 0.0
    cost_tokens: int = 0


@dataclass
class ResearchSession:
    """Persistent state for a research orchestration session.

    Can be serialized for checkpoint/resume via A2L agent-executions.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal: str = ""
    status: OrchestratorStatus = OrchestratorStatus.IDLE
    iterations: list[IterationRecord] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    experiments: list[Any] = field(default_factory=list)
    final_execution: Optional[WorkflowExecution] = None
    final_synthesis: str = ""
    errors: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=_utcnow)
    completed_at: Optional[datetime] = None
    total_cost_tokens: int = 0
    total_elapsed_seconds: float = 0.0
    a2l_execution_id: Optional[str] = None
    cumulative_reward: float = 0.0

    @property
    def iteration_count(self) -> int:
        return len(self.iterations)

    @property
    def last_observation(self) -> str:
        if self.iterations:
            return self.iterations[-1].observation
        return ""


# ── Budget Model ────────────────────────────────────────────────────────────


@dataclass
class ResearchBudget:
    """Resource budget for autonomous research.

    The orchestrator stops when any budget is exhausted.
    """

    max_iterations: int = 20
    max_wall_clock_seconds: float = 3600.0 * 48  # Default: 48 hours
    max_cost_tokens: int = 1_000_000  # Default: 1M tokens
    max_consecutive_failures: int = 3

    def is_exhausted(
        self,
        iterations: int,
        elapsed: float,
        total_tokens: int,
        consecutive_failures: int,
    ) -> tuple[bool, str]:
        """Check if budget is exhausted. Returns (exhausted, reason)."""
        if iterations >= self.max_iterations:
            return True, f"Max iterations ({self.max_iterations}) reached"
        if elapsed >= self.max_wall_clock_seconds:
            return True, f"Max wall clock ({self.max_wall_clock_seconds}s) exceeded"
        if total_tokens >= self.max_cost_tokens:
            return True, f"Max token cost ({self.max_cost_tokens}) exceeded"
        if consecutive_failures >= self.max_consecutive_failures:
            return True, f"Max consecutive failures ({self.max_consecutive_failures}) reached"
        return False, ""


# ── Research Orchestrator ───────────────────────────────────────────────────


_REPLAN_WORDS = ("done", "continue", "failed")
_NOT_DONE_RE = re.compile(
    r"\b(not|isn't|hasn't|never)\s+(yet\s+)?(done|complete|completed|achieved)\b"
)


def _parse_replan_reply(text: str) -> str:
    """done / continue / failed from a chat reply. The first word decides
    when it is one of them; a negated "done" ("not done yet") is continue —
    the old substring match read it as done and ended the loop."""
    lowered = text.strip().lower()
    words = re.findall(r"[a-z']+", lowered)
    if words and words[0] in _REPLAN_WORDS:
        return words[0]
    if _NOT_DONE_RE.search(lowered):
        return "continue"
    if {"done", "complete", "completed", "achieved"} & set(words):
        return "done"
    if any(w.startswith("fail") or w in ("cannot", "impossible") for w in words):
        return "failed"
    return "continue"


def research_sources_from_retrieve(result: Any) -> list[ResearchSource]:
    """Research sources from a ``RetrieveResult``, titled by the artifact's
    ``source_name`` in the server's rank order; ``insufficient`` yields none."""
    from r4.rag import passage_score

    if result.sufficiency.status == "insufficient":
        return []
    return [
        ResearchSource(
            title=passage.source_name or passage.source_id,
            snippet=passage.text,
            relevance=passage_score(passage),
        )
        for passage in result.passages
    ]


class ResearchOrchestrator:
    """Autonomous scientific research loop.

    Composes WorkflowPlanner + WorkflowExecutor + DeepResearchLoop + A2L
    into a closed-loop: plan → execute → record → observe → replan.

    The orchestrator:
    1. Plans a workflow for the research goal
    2. Executes each step (MCP tools, UMRS jobs, RAG queries, LLM prompts)
    3. Records all outputs as A2L artifacts with lineage
    4. Observes results and determines if goal is met
    5. Replans if needed (new follow-up steps, deeper investigation)
    6. Checkpoints state via A2L agent-executions for crash recovery

    Example:
        from r4 import Client
        from r4.agent import ResearchOrchestrator

        client = Client()
        r = ResearchOrchestrator(client)

        # Run autonomous research
        result = r.run("Investigate protein folding dynamics under pH variation")
        print(result.synthesis)

        # Run with budget constraints
        result = r.run(
            "Find optimal hyperparameters for the CFD solver",
            budget=ResearchBudget(max_iterations=10, max_wall_clock_seconds=3600),
        )

        # Stream events for UI
        for event in r.run_streaming("Analyze the dataset"):
            print(event.message)
    """

    def __init__(
        self,
        client: "Client",
        config: Optional[AgentConfig] = None,
        a2l: Optional["A2LClient"] = None,
        on_event: Optional[Callable[[AgentEvent], None]] = None,
        hypothesis_manager: Optional[HypothesisManager] = None,
        experiment_runner: Optional["ExperimentRunner"] = None,
        science_domain: Optional["ScienceDomain"] = None,
    ):
        """Initialize the orchestrator.

        Args:
            client: R4 Client for LLM, RAG, MCP, and job access.
            config: Agent configuration. Defaults to AUTONOMOUS mode.
            a2l: A2L client for artifact tracking. Created lazily if omitted.
            on_event: Callback for agent events (streaming UI updates).
            hypothesis_manager: Hypothesis lifecycle manager. Created if omitted.
            experiment_runner: Optional ExperimentRunner for HPC experiment
                execution (Phase 2). When provided, the orchestrator will
                design, submit, and evaluate experiments as part of the
                research loop.
        """
        self._client = client
        self._config = config or AgentConfig(mode=AgentMode.AUTONOMOUS)
        #: Decision-model calls (ADR-076 loop control), newest last.
        self.decision_log: list[dict[str, Any]] = []
        self._a2l = a2l  # Lazy init if None
        self._on_event = on_event
        self._hypothesis_manager = hypothesis_manager or HypothesisManager()
        self._experiment_runner = experiment_runner
        self._science_domain = science_domain

        # Belief-driven selection (Phase 2)
        self._belief_state = BeliefState()
        self._belief_updater = BeliefUpdater()

        # Working research state (Phase 3)
        self._working_state = WorkingResearchState()

        # Create the internal SciWorkflowAgent for plan+execute
        self._agent = SciWorkflowAgent(client, self._config, on_event)

        # Track the current session
        self._session: Optional[ResearchSession] = None
        self._consecutive_failures: int = 0

        # Store start time for wall-clock tracking
        self._start_time: float = 0.0

    # ── A2L lazy init ──────────────────────────────────────────────────

    # ── Belief-Driven Selection (Phase 2) ──────────────────────────────

    @property
    def belief_state(self) -> BeliefState:
        """Current belief state over all hypotheses."""
        return self._belief_state

    @property
    def working_state(self) -> WorkingResearchState:
        """Mutable, decision-optimized working memory."""
        return self._working_state

    def select_next_hypothesis(self) -> Optional[str]:
        """Choose the next hypothesis to test using EIG.

        Returns the hypothesis ID with the highest Expected Information
        Gain, or None if no hypotheses exist.
        """
        all_hyps = self._hypothesis_manager.get_all()
        if not all_hyps:
            return None

        best_hyp: Optional[str] = None
        best_eig = -1.0

        for h in all_hyps:
            if h.id not in self._belief_state.hypotheses:
                self._belief_state.add_hypothesis(h.id, confidence=h.confidence)
            eig = self._belief_updater.expected_information_gain(
                self._belief_state, h.id, expected_strength=0.5
            )
            if eig > best_eig:
                best_eig = eig
                best_hyp = h.id

        return best_hyp

    def update_beliefs_from_evidence(
        self, hypothesis_id: str, supports: bool, strength: float
    ) -> None:
        """Update belief state after observing evidence."""
        if hypothesis_id not in self._belief_state.hypotheses:
            self._belief_state.add_hypothesis(hypothesis_id, confidence=0.5)
        self._belief_updater.update(
            self._belief_state, hypothesis_id, supports=supports, strength=strength
        )

    # ── A2L Provenance Chain (Phase 3) ─────────────────────────────────

    @property
    def a2l(self) -> Optional["A2LClient"]:
        if self._a2l is None:
            try:
                from ..a2l_client import A2LClient

                self._a2l = A2LClient(self._client)
            except Exception as e:
                logger.warning("A2L client unavailable: %s", e)
                self._a2l = None
        return self._a2l

    @property
    def hypothesis_manager(self) -> HypothesisManager:
        """Access the hypothesis lifecycle manager."""
        return self._hypothesis_manager

    @property
    def experiment_runner(self) -> Optional["ExperimentRunner"]:
        """Return the experiment runner, lazily creating one if needed."""
        if self._experiment_runner is None:
            try:
                from .experiment import ExperimentRunner

                self._experiment_runner = ExperimentRunner(
                    self._client, hypothesis_manager=self._hypothesis_manager
                )
            except Exception as e:
                logger.debug("ExperimentRunner unavailable: %s", e)
        return self._experiment_runner

    # ── Event helpers ──────────────────────────────────────────────────

    def _emit(self, event_type: AgentEventType, message: str, data: Optional[dict] = None) -> None:
        if self._on_event:
            self._on_event(AgentEvent(event_type, message, data))

    # ── Main entry points ──────────────────────────────────────────────

    def run(
        self,
        goal: str,
        budget: Optional[ResearchBudget] = None,
        initial_context: Optional[str] = None,
        seed_hypotheses: Optional[list[str]] = None,
    ) -> ResearchSession:
        """Run autonomous research for the given goal.

        Args:
            goal: Research objective in natural language.
            budget: Resource budget limits. Uses defaults if omitted.
            initial_context: Optional initial context (e.g., from RAG).
            seed_hypotheses: Optional initial hypothesis statements to test.

        Returns:
            ResearchSession with full history, synthesis, and artifacts.
        """
        events: list[AgentEvent] = []

        def collect(event: AgentEvent) -> None:
            events.append(event)

        for event in self.run_streaming(goal, budget, initial_context, seed_hypotheses):
            collect(event)

        assert self._session is not None
        return self._session

    def run_streaming(
        self,
        goal: str,
        budget: Optional[ResearchBudget] = None,
        initial_context: Optional[str] = None,
        seed_hypotheses: Optional[list[str]] = None,
    ) -> Iterator[AgentEvent]:
        """Run autonomous research, yielding events for streaming UI.

        Args:
            goal: Research objective in natural language.
            budget: Resource budget limits. Uses defaults if omitted.
            initial_context: Optional initial context (e.g., from RAG).
            seed_hypotheses: Optional initial hypothesis statements to test.

        Yields:
            AgentEvent for each step in the research process.
        """
        budget = budget or ResearchBudget()
        self._start_time = time.monotonic()
        self._consecutive_failures = 0

        # Initialize session
        self._session = ResearchSession(goal=goal, status=OrchestratorStatus.PLANNING)

        # Seed initial hypotheses if provided
        if seed_hypotheses:
            for stmt in seed_hypotheses:
                h = self._hypothesis_manager.create(stmt)
                self._session.hypotheses.append(h)
            yield AgentEvent(
                AgentEventType.MESSAGE,
                f"Seeded {len(seed_hypotheses)} initial hypotheses",
                {"hypothesis_ids": [h.id for h in self._session.hypotheses]},
            )

        # Create A2L execution record for checkpointing
        self._checkpoint_session()

        yield AgentEvent(
            AgentEventType.PLANNING_START,
            f"Starting autonomous research: {goal}",
            {"session_id": self._session.session_id},
        )

        # Phase 0: Gather initial context via DeepResearchLoop (optional)
        research_context = initial_context or ""
        if not research_context and self._config.enable_rag:
            try:
                research_context = self._gather_initial_context(goal)
                if research_context:
                    yield AgentEvent(
                        AgentEventType.MESSAGE,
                        "Gathered initial research context",
                        {"context_len": len(research_context)},
                    )
            except Exception as e:
                logger.warning("Initial context gathering failed: %s", e)

        # Phase 1–N: Plan → Execute → Observe → Replan loop
        while True:
            iteration = self._session.iteration_count + 1

            # Budget check
            exhausted, reason = budget.is_exhausted(
                iterations=self._session.iteration_count,
                elapsed=time.monotonic() - self._start_time,
                total_tokens=self._session.total_cost_tokens,
                consecutive_failures=self._consecutive_failures,
            )
            if exhausted:
                yield AgentEvent(AgentEventType.MESSAGE, f"Budget exhausted: {reason}")
                break

            # ── PLAN PHASE ──
            self._session.status = OrchestratorStatus.PLANNING
            yield AgentEvent(
                AgentEventType.PLANNING_START,
                f"Iteration {iteration}: Planning next steps",
                {"iteration": iteration},
            )

            try:
                workflow = self._plan_iteration(
                    goal, self._session.last_observation, research_context
                )
            except Exception as e:
                logger.error("Planning failed: %s", e)
                self._consecutive_failures += 1
                self._session.errors.append(f"Planning iteration {iteration}: {e}")
                yield AgentEvent(AgentEventType.ERROR, f"Planning failed: {e}")
                continue

            yield AgentEvent(
                AgentEventType.PLANNING_COMPLETE,
                f"Plan: {workflow.name} ({len(workflow.steps)} steps)",
                {"steps": [s.description for s in workflow.steps]},
            )

            # ── EXPERIMENT PHASE ──
            # If an experiment runner is available, design and submit
            # experiments for active hypotheses before executing the workflow.
            if self._experiment_runner is not None:
                self._session.status = OrchestratorStatus.EXPERIMENTING
                yield AgentEvent(
                    AgentEventType.MESSAGE,
                    "Running HPC experiments...",
                    {"iteration": iteration},
                )
                try:
                    for event in self._run_experiments(iteration):
                        yield event
                except Exception as e:
                    logger.warning("Experiment phase failed: %s", e)
                    self._session.errors.append(f"Experiment iteration {iteration}: {e}")

            # ── EXECUTE PHASE ──
            self._session.status = OrchestratorStatus.EXECUTING
            iter_record = IterationRecord(iteration=iteration, plan=workflow)
            iter_start = time.monotonic()

            try:
                execution = self._agent.executor.execute_workflow(workflow)
                iter_record.execution = execution
            except Exception as e:
                logger.error("Execution iteration %d failed: %s", iteration, e)
                self._consecutive_failures += 1
                self._session.errors.append(f"Execution iteration {iteration}: {e}")
                yield AgentEvent(
                    AgentEventType.WORKFLOW_FAILED,
                    f"Iteration {iteration} execution failed: {e}",
                )
                continue

            iter_record.elapsed_seconds = time.monotonic() - iter_start

            if execution.status == WorkflowStatus.FAILED:
                self._consecutive_failures += 1
                yield AgentEvent(
                    AgentEventType.WORKFLOW_FAILED,
                    f"Iteration {iteration} workflow failed",
                )
                self._session.iterations.append(iter_record)
                continue

            # Reset failure counter on success
            self._consecutive_failures = 0

            # ── RECORD PHASE (A2L) ──
            artifacts = self._record_artifacts(execution, iteration)
            iter_record.artifacts_created = artifacts
            if artifacts:
                yield AgentEvent(
                    AgentEventType.MESSAGE,
                    f"Recorded {len(artifacts)} artifacts in A2L",
                    {"artifact_ids": artifacts},
                )

            # ── OBSERVE PHASE ──
            self._session.status = OrchestratorStatus.OBSERVING
            yield AgentEvent(AgentEventType.MESSAGE, "Observing results...")

            observation = self._observe_results(goal, execution)
            iter_record.observation = observation

            yield AgentEvent(
                AgentEventType.MESSAGE,
                f"Observation: {observation[:200]}...",
                {"full_observation": observation},
            )

            # ── HYPOTHESIZING PHASE ──
            self._session.status = OrchestratorStatus.HYPOTHESIZING
            yield AgentEvent(AgentEventType.MESSAGE, "Updating hypotheses...")

            hypotheses_tested, evidence_collected = self._update_hypotheses(
                goal, observation, execution, iteration
            )
            iter_record.hypotheses_tested = hypotheses_tested
            iter_record.evidence_collected = evidence_collected

            # Sync hypotheses to session
            self._session.hypotheses = self._hypothesis_manager.get_all()

            if hypotheses_tested:
                yield AgentEvent(
                    AgentEventType.MESSAGE,
                    f"Tested {len(hypotheses_tested)} hypotheses, "
                    f"collected {len(evidence_collected)} evidence items",
                    {"hypotheses_tested": hypotheses_tested},
                )

            # ── EVALUATING PHASE ──
            self._session.status = OrchestratorStatus.EVALUATING
            yield AgentEvent(AgentEventType.MESSAGE, "Evaluating hypotheses...")

            evaluation_summary = self._evaluate_hypotheses_summary()
            if evaluation_summary:
                yield AgentEvent(
                    AgentEventType.MESSAGE,
                    f"Hypothesis evaluation: {evaluation_summary[:200]}",
                )

            # ── RECORD HYPOTHESES IN A2L ──
            if hypotheses_tested:
                self._record_hypothesis_artifacts(iteration)

            # ── REPLAN DECISION ──
            self._session.status = OrchestratorStatus.REPLANNING

            decision = self._replan_decision(goal, observation, iteration, budget)
            iter_record.replan_decision = decision

            self._session.iterations.append(iter_record)

            # Update A2L checkpoint
            self._checkpoint_session()

            if decision == "done":
                yield AgentEvent(
                    AgentEventType.MESSAGE,
                    f"Research goal achieved after {iteration} iterations",
                )
                break
            elif decision == "failed":
                yield AgentEvent(AgentEventType.WORKFLOW_FAILED, "Research could not achieve goal")
                break
            else:
                yield AgentEvent(
                    AgentEventType.MESSAGE,
                    f"Replanning for iteration {iteration + 1}...",
                )

            # Continue loop → next plan phase

        # ── FINAL SYNTHESIS ──
        self._session.status = OrchestratorStatus.COMPLETED
        self._session.completed_at = _utcnow()

        final_synthesis = self._synthesize_final(goal)
        self._session.final_synthesis = final_synthesis

        yield AgentEvent(
            AgentEventType.WORKFLOW_COMPLETE,
            f"Research complete. {self._session.iteration_count} iterations.",
            {"synthesis": final_synthesis},
        )

        # Final checkpoint
        self._checkpoint_session()

    # ── Phase: Context Gathering ───────────────────────────────────────

    def _gather_initial_context(self, goal: str) -> str:
        """Use DeepResearchLoop to gather initial context for the goal.

        Returns consolidated context string from RAG + LLM research.
        """
        from r4.model_scale import configured_reader_fields

        client = self._client
        # synthesize_fn reads the passages with config.model: name it and its
        # D-14 profile on every retrieve (r4-scale-router-10).
        reader = configured_reader_fields(self._config.model, client)

        # Search callback: the passages UMRS keeps (ADR-093 D-10), no hidden
        # answer generation per search step.
        def search_fn(query: str) -> list[ResearchSource]:
            try:
                return research_sources_from_retrieve(client.rag.retrieve(query, **reader))[:5]
            except Exception as e:
                logger.debug("RAG search failed for context gathering: %s", e)
                return []

        # Synthesize callback: use LLM
        def synthesize_fn(query: str, sources: list[ResearchSource]) -> str:
            snippets = "\n".join(s.snippet for s in sources[:5])
            messages = [
                {"role": "system", "content": "Synthesize findings from these sources concisely."},
                {
                    "role": "user",
                    "content": (
                        f"Query: {query}\n\nSources:\n{snippets}\n\nProvide a concise synthesis."
                    ),
                },
            ]
            try:
                response = client.chat(messages=messages, model=self._config.model)
                return response.content
            except Exception as e:
                logger.debug("Synthesis failed: %s", e)
                return ""

        # Decompose callback: use LLM
        def decompose_fn(query: str, partial_answer: str) -> list[str]:
            messages = [
                {
                    "role": "system",
                    "content": "Given a query and partial answer, suggest 1-3 follow-up questions.",
                },
                {
                    "role": "user",
                    "content": (
                        f"Query: {query}\nPartial answer: {partial_answer}\n\n"
                        f"List follow-up questions (one per line, max 3):"
                    ),
                },
            ]
            try:
                response = client.chat(messages=messages, model=self._config.model)
                lines = [
                    li.strip("- ").strip()
                    for li in (response.content or "").split("\n")
                    if li.strip()
                ]
                return lines[:3]
            except Exception as e:
                logger.debug("Decompose failed: %s", e)
                return []

        try:
            loop = DeepResearchLoop(
                search_fn=search_fn,
                synthesize_fn=synthesize_fn,
                decompose_fn=decompose_fn,
                max_depth=2,
                max_sources_per_step=5,
                max_total_steps=6,
            )
            result = loop.run(goal)
            return result.synthesis
        except Exception as e:
            logger.warning("DeepResearchLoop failed for context: %s", e)
            return ""

    # ── Phase: Planning ─────────────────────────────────────────────────

    def _plan_iteration(
        self,
        goal: str,
        last_observation: str,
        context: str,
    ) -> WorkflowDefinition:
        """Plan the next iteration's workflow.

        On first iteration, plans from the goal. On subsequent iterations,
        incorporates the last observation and replans.

        Uses the agent's WorkflowPlanner, adding context from:
        - Previous observation (what was learned)
        - Initial research context (RAG findings)
        - A2L lineage (relevant prior artifacts)
        """
        planner = self._agent.planner

        # Build enriched context
        full_context = context
        if last_observation:
            full_context = f"Previous findings: {last_observation}\n\n{full_context}"

        # Add A2L lineage context if available
        a2l_context = self._get_lineage_context()
        if a2l_context:
            full_context = f"{a2l_context}\n\n{full_context}"

        # Use a more directive prompt for replanning
        if self._session and self._session.iteration_count > 0:
            goal_with_context = (
                f"Continue the research on: {goal}\n\n"
                f"We have completed {self._session.iteration_count} iterations. "
                f"The last observation was: {last_observation}\n\n"
                f"Plan the NEXT steps to make further progress. Focus on what remains unknown."
            )
        else:
            goal_with_context = goal

        return planner.plan(goal_with_context, context=full_context)

    # ── Phase: Record (A2L) ────────────────────────────────────────────

    def _record_artifacts(
        self,
        execution: WorkflowExecution,
        iteration: int,
    ) -> list[str]:
        """Record execution outputs as A2L artifacts via /derive.

        Returns list of created artifact IDs.
        """
        a2l = self.a2l
        if a2l is None:
            return []

        user_id = self._client.config.umrs_username or "unknown"
        project_id = self._client.config.project_id
        artifact_ids: list[str] = []

        for step_id, result in execution.step_results.items():
            if result.status != StepStatus.COMPLETED or result.output is None:
                continue

            # Derive an artifact for each completed step
            step = next((s for s in execution.workflow.steps if s.id == step_id), None)
            step_desc = step.description if step else step_id

            # Build a synthetic S3 URI (in production, outputs would go to actual S3)
            uri = f"s3://rivault-artifacts/research/{self._session.session_id}/{step_id}.json"

            import json as _json

            try:
                input_hash = a2l.hash_content(_json.dumps(result.output, default=str))
            except Exception:
                input_hash = a2l.hash_content(str(result.output))

            art = a2l.derive(
                parents=[],  # First iteration; subsequent would chain
                operation=step.step_type.value if step else "unknown",
                params={"iteration": iteration, "step_id": step_id},
                type="research_step_output",
                uri=uri,
                user_id=user_id,
                input_data_hash=input_hash,
                project_id=project_id,
                metadata={
                    "step_description": step_desc,
                    "step_type": step.step_type.value if step else "unknown",
                    "iteration": iteration,
                    "session_id": self._session.session_id,
                },
            )
            if art:
                artifact_ids.append(art.id)

        return artifact_ids

    def _get_lineage_context(self) -> str:
        """Query A2L for relevant prior artifacts to inform replanning."""
        a2l = self.a2l
        if a2l is None or self._session is None:
            return ""

        user_id = self._client.config.umrs_username or "unknown"
        try:
            artifacts = a2l.list_artifacts(user_id=user_id, limit=10)
            if not artifacts:
                return ""

            lines = ["Prior research artifacts:"]
            for art in artifacts[:5]:
                lines.append(f"- {art.id}: {art.type} ({art.status})")
            return "\n".join(lines)
        except Exception as e:
            logger.debug("A2L lineage query failed: %s", e)
            return ""

    # ── Phase: Observe ─────────────────────────────────────────────────

    def _observe_results(
        self,
        goal: str,
        execution: WorkflowExecution,
    ) -> str:
        """Ask the LLM to observe what was learned from the execution.

        Returns a concise observation summarizing findings, gaps, and
        whether the goal is closer to being achieved.
        """
        # Summarize step results
        results_summary_parts: list[str] = []
        for step_id, result in execution.step_results.items():
            step = next((s for s in execution.workflow.steps if s.id == step_id), None)
            step_desc = step.description if step else step_id
            if result.status == StepStatus.COMPLETED:
                output_preview = str(result.output)[:300] if result.output else "(no output)"
                results_summary_parts.append(f"✓ {step_desc}: {output_preview}")
            elif result.status == StepStatus.FAILED:
                results_summary_parts.append(f"✗ {step_desc}: FAILED - {result.error}")
            elif result.status == StepStatus.BLOCKED:
                results_summary_parts.append(f"⊘ {step_desc}: BLOCKED - {result.error}")

        results_summary = "\n".join(results_summary_parts)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a scientific observer evaluating research progress. "
                    "Analyze what was learned from the executed steps. "
                    "Identify: (1) key findings, (2) remaining gaps, "
                    "(3) whether the goal is achieved. "
                    "Be concise — 3-5 sentences max."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Research goal: {goal}\n\n"
                    f"Step results:\n{results_summary}\n\n"
                    f"Provide a concise observation of what was learned and what remains."
                ),
            },
        ]

        try:
            response = self._client.chat(messages=messages, model=self._config.model)
            return response.content
        except Exception as e:
            logger.error("Observe phase failed: %s", e)
            return f"Observation error: {e}"

    # ── Phase: Replan Decision ─────────────────────────────────────────

    def _replan_decision(
        self,
        goal: str,
        observation: str,
        iteration: int,
        budget: ResearchBudget,
    ) -> str:
        """Decide whether research is done, should continue, or has failed.

        With a decision model on (ADR-076), a confident typed reading of the
        observation decides; otherwise the chat model is asked, as before.

        Returns: "done", "continue", or "failed".
        """
        from r4.decisions.harness import loop_decision

        decided = loop_decision(self._client, goal, iteration, observation, log=self.decision_log)
        if decided:
            return decided
        return self._llm_replan_decision(goal, observation, iteration, budget)

    def _llm_replan_decision(
        self,
        goal: str,
        observation: str,
        iteration: int,
        budget: ResearchBudget,
    ) -> str:
        """Ask the chat model for one word: done / continue / failed."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a research progress evaluator. Based on the observation, "
                    "decide if the research goal has been achieved. Respond with "
                    "exactly one word: done (goal achieved), continue "
                    "(more work needed), or failed (cannot achieve goal)."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Research goal: {goal}\n"
                    f"Iteration: {iteration}\n"
                    f"Observation: {observation}\n\n"
                    f"Decision (one word: done/continue/failed):"
                ),
            },
        ]

        try:
            response = self._client.chat(messages=messages, model=self._config.model)
            return _parse_replan_reply(response.content or "")
        except Exception as e:
            logger.error("Replan decision failed: %s", e)
            # On error, default to continue if budget remains
            exhausted, _ = budget.is_exhausted(
                iterations=iteration,
                elapsed=time.monotonic() - self._start_time,
                total_tokens=self._session.total_cost_tokens if self._session else 0,
                consecutive_failures=self._consecutive_failures,
            )
            return "continue" if not exhausted else "done"

    # ── Phase: Final Synthesis ─────────────────────────────────────────

    def _synthesize_final(self, goal: str) -> str:
        """Synthesize a final research summary from all iterations."""
        if not self._session or not self._session.iterations:
            return "No research was performed."

        # Collect all observations
        observations: list[str] = []
        for ir in self._session.iterations:
            if ir.observation:
                observations.append(f"[Iteration {ir.iteration}]: {ir.observation}")

        if not observations:
            return "Research was executed but produced no observations."

        # Collect hypothesis evaluations
        hypothesis_text = ""
        all_hypotheses = self._hypothesis_manager.get_all()
        if all_hypotheses:
            hypothesis_lines: list[str] = []
            for h in all_hypotheses:
                evidence_summary = f"for={len(h.evidence_for)}, against={len(h.evidence_against)}"
                hypothesis_lines.append(
                    f"- [{h.status.value}] {h.statement} "
                    f"(confidence={h.confidence:.2f}, {evidence_summary})"
                )
            hypothesis_text = "\n\nHypotheses evaluated:\n" + "\n".join(hypothesis_lines)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a scientific research synthesizer. Create a comprehensive "
                    "summary of findings from all research iterations. Include: "
                    "key discoveries, methodology used, confidence levels, "
                    "hypothesis evaluation results, and suggested next steps. "
                    "Format as a research abstract."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Research goal: {goal}\n\n"
                    f"Observations across {self._session.iteration_count} iterations:\n"
                    + "\n\n".join(observations)
                    + hypothesis_text
                    + "\n\nProvide a comprehensive research synthesis."
                ),
            },
        ]

        try:
            response = self._client.chat(messages=messages, model=self._config.model)
            return response.content
        except Exception as e:
            logger.error("Final synthesis failed: %s", e)
            return "\n\n".join(observations)

    # ── Phase: Hypothesis Management ────────────────────────────────────

    def _update_hypotheses(
        self,
        goal: str,
        observation: str,
        execution: WorkflowExecution,
        iteration: int,
    ) -> tuple[list[str], list[Evidence]]:
        """Update hypotheses based on the latest observation and execution.

        If no hypotheses exist, formulate one from the goal and observation.
        If hypotheses exist, evaluate them against the new evidence.

        Returns:
            Tuple of (hypothesis IDs tested, evidence items collected).
        """
        hypotheses_tested: list[str] = []
        evidence_collected: list[Evidence] = []

        # Extract evidence from execution results
        for step_id, result in execution.step_results.items():
            if result.status == StepStatus.COMPLETED and result.output:
                step = next((s for s in execution.workflow.steps if s.id == step_id), None)
                step_desc = step.description if step else step_id
                evidence = Evidence(
                    source=f"iteration_{iteration}:{step_id}",
                    strength=0.6,  # Default moderate strength for completed steps
                    summary=f"{step_desc}: {str(result.output)[:200]}",
                    supports=True,  # Completed steps support hypotheses by default
                    metadata={"step_type": step.step_type.value if step else "unknown"},
                )
                evidence_collected.append(evidence)
            elif result.status == StepStatus.FAILED:
                step = next((s for s in execution.workflow.steps if s.id == step_id), None)
                step_desc = step.description if step else step_id
                evidence = Evidence(
                    source=f"iteration_{iteration}:{step_id}",
                    strength=0.4,  # Failed steps provide weaker evidence
                    summary=f"{step_desc}: FAILED - {result.error}",
                    supports=False,  # Failures may contradict hypotheses
                    metadata={"step_type": step.step_type.value if step else "unknown"},
                )
                evidence_collected.append(evidence)

        # Also extract evidence from the LLM observation
        if observation:
            obs_evidence = Evidence(
                source=f"iteration_{iteration}:observation",
                strength=0.7,  # LLM synthesis is moderately strong evidence
                summary=observation[:300],
                supports=True,  # Observations generally support (they found something)
                metadata={"type": "llm_observation"},
            )
            evidence_collected.append(obs_evidence)

        # If no hypotheses exist, formulate one from the goal
        active = self._hypothesis_manager.get_active()
        if not active:
            h = self._formulate_initial_hypothesis(goal, observation)
            if h:
                hypotheses_tested.append(h.id)
                # Evaluate with collected evidence
                self._hypothesis_manager.evaluate(h.id, evidence_collected)
        else:
            # Evaluate each active hypothesis against the evidence
            for h in active:
                self._hypothesis_manager.evaluate(h.id, evidence_collected)
                hypotheses_tested.append(h.id)

        # Check if any active hypothesis should be revised
        active = self._hypothesis_manager.get_active()
        for h in active:
            if h.status == HypothesisStatus.REFUTED:
                # Auto-revise refuted hypotheses
                revised = self._auto_revise_hypothesis(h, observation)
                if revised:
                    hypotheses_tested.append(revised.id)

        return hypotheses_tested, evidence_collected

    def _formulate_initial_hypothesis(self, goal: str, observation: str) -> Optional[Hypothesis]:
        """Use LLM to formulate an initial hypothesis from the goal."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a scientific hypothesis generator. Given a research goal "
                    "and initial observations, formulate a clear, testable hypothesis. "
                    "The hypothesis should be specific enough to be tested experimentally. "
                    "Respond with ONLY the hypothesis statement, no explanation."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Research goal: {goal}\n\n"
                    f"Initial observations: {observation[:500] if observation else 'None yet'}\n\n"
                    f"Formulate a testable hypothesis:"
                ),
            },
        ]
        try:
            response = self._client.chat(messages=messages, model=self._config.model)
            statement = (response.content or "").strip()
            if statement and len(statement) > 10:
                return self._hypothesis_manager.create(statement)
        except Exception as e:
            logger.warning("Hypothesis formulation failed: %s", e)
        return None

    def _auto_revise_hypothesis(
        self, hypothesis: Hypothesis, observation: str
    ) -> Optional[Hypothesis]:
        """Auto-revise a refuted hypothesis using LLM reasoning."""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are revising a scientific hypothesis that has been refuted by evidence. "
                    "Given the original hypothesis and the contradicting evidence, propose a "
                    "revised hypothesis that accounts for the new evidence. "
                    "Respond with ONLY the revised hypothesis statement."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original hypothesis: {hypothesis.statement}\n\n"
                    f"Evidence against: "
                    + "; ".join(e.summary for e in hypothesis.evidence_against[:3])
                    + f"\n\nLatest observation: {observation[:500]}\n\n"
                    f"Propose a revised hypothesis:"
                ),
            },
        ]
        try:
            response = self._client.chat(messages=messages, model=self._config.model)
            statement = (response.content or "").strip()
            if statement and len(statement) > 10:
                return self._hypothesis_manager.revise(
                    hypothesis.id,
                    statement,
                    "Auto-revised after refutation by evidence",
                )
        except Exception as e:
            logger.warning("Auto-revise failed: %s", e)
        return None

    def _evaluate_hypotheses_summary(self) -> str:
        """Generate a summary of current hypothesis evaluations."""
        all_hypotheses = self._hypothesis_manager.get_all()

        if not all_hypotheses:
            return ""

        parts: list[str] = []
        for h in all_hypotheses:
            parts.append(f"[{h.status.value}] {h.statement[:80]} (confidence={h.confidence:.2f})")
        return "; ".join(parts)

    def _record_hypothesis_artifacts(self, iteration: int) -> None:
        """Record hypothesis states as A2L artifacts for lineage tracking."""
        a2l = self.a2l
        if a2l is None or self._session is None:
            return

        user_id = self._client.config.umrs_username or "unknown"
        project_id = self._client.config.project_id

        for h in self._hypothesis_manager.get_all():
            # Only record hypotheses that were evaluated in this iteration
            if h.evaluated_at is None:
                continue

            import json as _json

            h_data = _json.dumps(h.to_dict(), default=str)
            input_hash = A2LClient.hash_content(h_data) if a2l else ""

            parents: list[str] = []
            if h.parent_hypothesis_id:
                # Find parent's A2L artifact ID from metadata
                parent_h = self._hypothesis_manager.get(h.parent_hypothesis_id)
                if parent_h and "a2l_artifact_id" in parent_h.metadata:
                    parents.append(parent_h.metadata["a2l_artifact_id"])

            art = a2l.derive(
                parents=parents,
                operation="hypothesis_evaluation",
                params={
                    "iteration": iteration,
                    "hypothesis_id": h.id,
                    "status": h.status.value,
                },
                type="hypothesis",
                uri=f"s3://rivault-artifacts/research/{self._session.session_id}/hypothesis_{h.id}.json",
                user_id=user_id,
                input_data_hash=input_hash,
                project_id=project_id,
                metadata={
                    "hypothesis_statement": h.statement[:200],
                    "confidence": h.confidence,
                    "status": h.status.value,
                    "evidence_for_count": len(h.evidence_for),
                    "evidence_against_count": len(h.evidence_against),
                    "iteration": iteration,
                    "session_id": self._session.session_id,
                },
                artifact_class="hypothesis",
            )
            if art:
                h.metadata["a2l_artifact_id"] = art.id

    # ── Phase: Experiments (Phase 2) ───────────────────────────────────

    def _run_experiments(self, iteration: int) -> Iterator[AgentEvent]:
        """Design, submit, and collect HPC experiments for active hypotheses.

        Yields AgentEvent for each experiment action. Experiments are
        designed via LLM, submitted through the ExperimentRunner, and
        their results are evaluated against the parent hypothesis.
        """
        from .experiment import ExperimentStatus

        runner = self._experiment_runner
        if runner is None:
            return

        # Check for active experiments from previous iterations
        active = runner.get_active()
        for exp in active:
            status = runner.check_status(exp)
            if status.is_terminal:
                yield AgentEvent(
                    AgentEventType.MESSAGE,
                    f"Experiment {exp.name} completed ({status.value})",
                    {"experiment_id": exp.experiment_id, "status": status.value},
                )
                if status == ExperimentStatus.COMPLETED:
                    try:
                        results = runner.collect_results(exp)
                        if exp.hypothesis_id:
                            runner.evaluate(results, exp.hypothesis_id)
                            yield AgentEvent(
                                AgentEventType.MESSAGE,
                                f"Evaluated experiment against hypothesis {exp.hypothesis_id}",
                                {"experiment_id": exp.experiment_id},
                            )
                        # Compute and persist reward if science domain is available
                        reward = self._compute_and_persist_reward(exp, results)
                        if reward:
                            yield AgentEvent(
                                AgentEventType.MESSAGE,
                                f"Reward: {reward.total:.3f} "
                                f"(perf={reward.performance:.3f}, cost={reward.cost:.3f})",
                                {
                                    "experiment_id": exp.experiment_id,
                                    "reward": reward.to_dict(),
                                },
                            )
                    except Exception as e:
                        logger.warning("Result collection failed for %s: %s", exp.experiment_id, e)

        # Design new experiments for hypotheses without existing experiments
        for h in self._hypothesis_manager.list_all():
            existing = runner.get_by_hypothesis(h.id)
            if not existing:
                try:
                    spec = self._design_experiment_spec(h.statement, iteration)
                    if spec:
                        exp = runner.design(
                            f"exp_{h.id[:8]}_iter{iteration}",
                            spec,
                            hypothesis_id=h.id,
                        )
                        yield AgentEvent(
                            AgentEventType.MESSAGE,
                            f"Designed experiment for hypothesis: {h.statement[:60]}...",
                            {"experiment_id": exp.experiment_id, "hypothesis_id": h.id},
                        )
                        # Submit the experiment
                        runner.submit(exp)
                        yield AgentEvent(
                            AgentEventType.MESSAGE,
                            f"Submitted experiment {exp.name} (job: {exp.job_id})",
                            {"experiment_id": exp.experiment_id, "job_id": exp.job_id},
                        )
                except Exception as e:
                    logger.warning("Experiment design/submit failed: %s", e)

        # Store experiments in session
        if self._session:
            self._session.experiments = [e.to_dict() for e in runner.get_all()]

    def _compute_and_persist_reward(
        self,
        experiment: "Experiment",
        results: "ExperimentResults",
    ) -> Optional["ScientificReward"]:
        """Compute reward via science_domain and persist to experiment/state.

        Converts r4 ExperimentResults to the science contracts layer,
        calls ``ScienceDomain.evaluate_results()`` and
        ``ScienceDomain.compute_reward()``, then stores the reward on
        the experiment and accumulates it in the loop state.

        Returns None if no science domain is configured.
        """
        if self._science_domain is None:
            return None

        from science.experiment import Experiment as ScienceExperiment
        from science.experiment import ExperimentResults as ScienceResults

        # Extract metrics from results
        metrics: dict[str, float] = {}
        if results.parsed_outputs:
            for key, value in results.parsed_outputs.items():
                if isinstance(value, (int, float)):
                    metrics[key] = float(value)
        if results.resource_usage:
            for key, value in results.resource_usage.items():
                if isinstance(value, (int, float)):
                    metrics[key] = float(value)

        science_results = ScienceResults(
            metrics=metrics,
            artifacts=results.artifacts,
            logs=results.stdout_summary,
        )

        science_exp = ScienceExperiment(
            hypothesis_id=experiment.hypothesis_id or "",
            protocol=experiment.parameters,
            results=science_results,
        )

        # Evaluate results → evidence
        evidence = self._science_domain.evaluate_results(science_exp, science_results)

        # Compute reward
        reward = self._science_domain.compute_reward(science_exp, evidence)

        # Persist
        experiment.reward_score = reward.total

        # Accumulate in session if available
        if self._session is not None:
            self._session.cumulative_reward += reward.total

        return reward

    def _design_experiment_spec(
        self,
        hypothesis_statement: str,
        iteration: int,
    ) -> Optional[Any]:
        """Use LLM to generate a SlurmExperimentSpec from a hypothesis.

        The LLM suggests script paths, resource requirements, and output
        patterns based on the hypothesis statement. Returns None if the
        LLM cannot design a suitable experiment.
        """
        from .experiment import SlurmExperimentSpec

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an HPC experiment designer. Given a scientific hypothesis, "
                    "design a Slurm experiment to test it. Respond with a JSON object:\n"
                    '{"script_path": "/path/to/script.sh", "partition": "gpu", '
                    '"gpus": 1, "cpus_per_task": 4, "time_limit": "01:00:00", '
                    '"memory": "16G", "output_patterns": ["*.csv", "*.log"]}\n\n'
                    "Only include fields you are confident about. Use defaults for "
                    "uncertain fields."
                ),
            },
            {
                "role": "user",
                "content": f"Design an HPC experiment to test: {hypothesis_statement}",
            },
        ]

        try:
            import json as _json

            response = self._client.chat(messages=messages, model=self._config.model)
            content = (response.content or "").strip()

            # Extract JSON from response
            if "```" in content:
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]

            spec_data = _json.loads(content)
            return SlurmExperimentSpec(
                script_path=spec_data.get("script_path", "/bin/echo"),
                partition=spec_data.get("partition", "gpu"),
                gpus=spec_data.get("gpus", 0),
                cpus_per_task=spec_data.get("cpus_per_task", 4),
                time_limit=spec_data.get("time_limit", "02:00:00"),
                memory=spec_data.get("memory", "16G"),
                output_patterns=spec_data.get("output_patterns", []),
            )
        except Exception as e:
            logger.debug("Experiment spec design failed: %s", e)
            return None

    # ── Checkpoint / Resume ────────────────────────────────────────────

    def _checkpoint_session(self) -> None:
        """Save session state to A2L agent-executions for crash recovery."""
        if self._session is None:
            return
        a2l = self.a2l
        if a2l is None:
            return

        user_id = self._client.config.umrs_username or "unknown"
        project_id = self._client.config.project_id

        if self._session.a2l_execution_id is None:
            # First checkpoint — create execution record
            exec_id = a2l.create_execution(
                agent_name="research-orchestrator",
                goal=self._session.goal,
                user_id=user_id,
                model=self._config.model,
                project_id=project_id,
                metadata={
                    "session_id": self._session.session_id,
                    "iterations": self._session.iteration_count,
                },
            )
            if exec_id:
                self._session.a2l_execution_id = exec_id
        else:
            # Update existing
            trace: list[dict[str, Any]] = []
            for ir in self._session.iterations:
                trace.append(
                    {
                        "iteration": ir.iteration,
                        "plan_name": ir.plan.name,
                        "steps_count": len(ir.plan.steps),
                        "observation": ir.observation[:500] if ir.observation else "",
                        "decision": ir.replan_decision,
                        "artifacts": ir.artifacts_created,
                        "elapsed_seconds": ir.elapsed_seconds,
                    }
                )

            outputs: dict[str, Any] = {
                "final_synthesis": self._session.final_synthesis[:5000]
                if self._session.final_synthesis
                else "",
                "iterations_completed": self._session.iteration_count,
                "total_elapsed": time.monotonic() - self._start_time,
            }

            a2l.update_execution(
                self._session.a2l_execution_id,
                status="completed"
                if self._session.status == OrchestratorStatus.COMPLETED
                else "running",
                outputs=outputs,
                trace=trace,
            )

    def resume(self, execution_id: str) -> Optional[ResearchSession]:
        """Resume a research session from A2L checkpoint.

        Args:
            execution_id: A2L agent-execution ID to resume from.

        Returns:
            ResearchSession with prior state restored, or None if not found.
        """
        a2l = self.a2l
        if a2l is None:
            logger.warning("Cannot resume: A2L unavailable")
            return None

        exec_ref = a2l.get_execution(execution_id)
        if exec_ref is None:
            logger.warning("Execution %s not found in A2L", execution_id)
            return None

        # Reconstruct session from trace
        session = ResearchSession(
            session_id=exec_ref.goal[:50] + "-resume",
            goal=exec_ref.goal,
            status=OrchestratorStatus.IDLE,
            a2l_execution_id=execution_id,
        )

        # Rebuild iteration records from trace
        for trace_entry in exec_ref.trace:
            ir = IterationRecord(
                iteration=trace_entry.get("iteration", 0),
                plan=WorkflowDefinition(name=trace_entry.get("plan_name", ""), description=""),
                observation=trace_entry.get("observation", ""),
                replan_decision=trace_entry.get("decision", ""),
                artifacts_created=trace_entry.get("artifacts", []),
                elapsed_seconds=trace_entry.get("elapsed_seconds", 0.0),
            )
            session.iterations.append(ir)

        self._session = session
        return session
