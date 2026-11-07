# Autonomous Science Plan: AI4S as a Scientific Control System

**Status:** Draft — Pending Engineering Review
**Date:** 2026-06-20
**Authors:** AI4S Platform Team (synthesized from 5 rounds of architecture review)
**Supersedes:** All prior autonomous-science architecture proposals

---

## Executive Summary

AI4S is not trying to become a better workflow engine. UMRS already does that.
Instead, AI4S is trying to become a **Scientific Decision System** — a control
system for autonomous scientific exploration.

The core thesis: if we build the right feedback loop, AI4S can continuously
improve a measurable scientific metric without human intervention. That loop is:

```
Goal → Hypothesis → Experiment → Reward → A2L → Next Hypothesis
```

A critical missing piece is the **Knowledge Acquisition Layer** — the system
that ingests existing knowledge (papers, notebooks, scripts, datasets) and
makes it available to both humans and AI. Without this, every new research
program starts almost from zero. The Knowledge Acquisition Layer sits
upstream of the Research Director, feeding the Belief State that the Director
uses to decide what experiment to run next.

This document captures the consensus from multiple rounds of architecture
review. It is self-contained: any engineer or agent picking up this file
should be able to understand the destination, the current position, and the
next four weeks of work without needing the conversation history.

---

## North Star

AI4S succeeds if:

1. A research program can run for **weeks** without intervention
2. It **autonomously proposes** hypotheses from a research goal
3. It **autonomously designs** experiments to test those hypotheses
4. It **autonomously allocates** compute across open questions
5. It **autonomously abandons** bad directions (reward below portfolio median)
6. **ADR increases** over time (reward units per GPU-hour)
7. **Human intervention frequency trends toward zero**

The goal is not to build a better workflow engine. The goal is to build a
**Scientific Decision System** — a control system for autonomous scientific
exploration that continuously improves a measurable scientific metric without
human intervention.

---

## Non-Goals

AI4S is **NOT**:

- A workflow engine (UMRS already does this)
- A DAG scheduler (UMRS already does this)
- A replacement for UMRS
- A knowledge graph project
- A vector database project
- An ontology project
- A scientific paper generation system

Those may become **components** of AI4S.

They are **not the objective**.

The objective is: **increasing autonomous discovery rate over time.**

This section exists to prevent scope drift. If a future agent proposes building
a knowledge graph before the autonomous loop is proven, point them here.

---

## Current Architecture → Future Architecture

The most useful diagram during implementation:

### TODAY

```
ResearchOrchestrator (runs ONE goal, heuristic decisions)
    ↓
HypothesisManager (confidence: float, no entropy, no EIG)
    ↓
ExperimentRunner (design→submit→monitor→collect→evaluate)
    ↓
UMRS (workflow_runner + step_executor + routers)
    ↓
A2L (artifact/lineage, /derive endpoint)
```

**Problems with TODAY:**

1. `DECIDING` phase uses crude heuristics: `confidence >= 0.9` → PUBLISH
2. No belief state — can't reason about uncertainty
3. No reward function — can't optimize
4. No Director — can't manage portfolio of questions
5. No domain plugin interface — hardcoded to one example analysis domain

### TARGET

```
ResearchProgram (top-level scientific object — the "why")
    ↓
ResearchDirector (the controller / AI CEO — allocates budget, stops bad directions)
    ↓
BeliefState (tracks confidence + entropy across all hypotheses)
    ↓
HypothesisManager (evolved to use BeliefState internally)
    ↓
ExperimentEngine (primary abstraction — experiment.execute())
    ↓
UMRS (workflow_runner + step_executor + routers — pure execution kernel)
    ↓
A2L (immutable scientific ledger — Git for science)
```

**Key transitions:**

| From | To | Why |
|------|-----|-----|
| `ResearchOrchestrator` (one goal) | `ResearchDirector` (portfolio) | Need to manage multiple questions |
| `confidence: float` | `BeliefState` (entropy + EIG) | Need to reason about uncertainty |
| `workflow.submit()` | `experiment.execute()` | Experiment is the primary abstraction |
| Heuristic decisions | `ScientificReward.score()` | Need optimization target |
| Hardcoded single domain | `ScienceDomain` plugin interface | Need to generalize beyond one domain |

---

## Background: Current State of AI4S and UMRS

### What Exists Today

The platform already has substantial infrastructure for autonomous science.
The following components are verified to exist in the codebase:

| Component | Location | Status |
|-----------|----------|--------|
| 6-loop science state machine | `r4/src/r4/research_loop/orchestrator.py` | Implemented — FORMULATING→DESIGNING→EXECUTING→ANALYZING→EVALUATING→DECIDING with `LoopDecision` (CONTINUE_SAME/REVISE/BRANCH/PUBLISH/STOP) |
| Hypothesis lifecycle with confidence + evidence | `r4/src/r4/agent/hypothesis.py` | Implemented — `Hypothesis` (PROPOSED→TESTING→SUPPORTED→REFUTED→INCONCLUSIVE→RETIRED), `Evidence` (strength 0–1, supports bool), `HypothesisManager` |
| Closed-loop autonomous research with budget control | `r4/src/r4/agent/research_orchestrator.py` | Implemented — `ResearchOrchestrator.run_streaming()`: plan→execute→record(A2L)→observe→replan with `ResearchBudget` |
| Multi-agent research teams | `r4/src/r4/research_loop/team.py` | Implemented — RL/DA/ED/PR agents; `r4/src/r4/teamup/roles.py` — PM/SE/PG/CD |
| Per-loop scientific state (JSON) | `r4/src/r4/research_loop/state.py` | Implemented — `ResearchLoopState` with `HypothesisRecord`, `ExperimentRecord`, `EvidenceRecord`, `LoopBudget` |
| Knowledge graph (entity/relation extraction from documents) | `umrs/src/umrs/knowledge_graph/__init__.py` | Implemented — `EntityType` (PERSON/ORG/CONCEPT/METHOD/METRIC...), `RelationType` (AUTHORED_BY/USES/CITES/EVALUATES...) |
| Deep research / literature layer | `r4/src/r4/agent/deep_research.py` | Implemented — `DeepResearchLoop`: decompose→search RAG+federated→synthesize with citations |
| HPC experiment execution bridging to UMRS durable jobs | `r4/src/r4/agent/experiment.py` | Implemented — `ExperimentRunner` (design→submit→monitor→collect→evaluate) |
| Evolutionary self-improvement (HPC code) | (was `r4/src/r4/teamup/evolution.py`) | Retired 2026-10-11 — the `EvolutionaryOptimizer` had no caller; deleted in the ADR-092 slice-4 cleanup (gap row D-37) |
| A2L artifact/lineage (immutable, status FSM, /derive endpoint) | `a2l/src/a2l/models.py` | Implemented — `Artifact` (immutable, SHA-256 ID, status FSM pending→running→completed/failed), `ArtifactEdge` (parent→child DAG), `ArtifactDerive` (agents MUST use /derive — guarantees lineage, no orphans) |
| Skills system | `r4/src/r4/companion/skills.py` | Implemented — `Skill`, `SkillRegistry`, `BUILTIN_SKILLS` (research, hpc, analysis) |
| In-process workflow runner | `umrs/src/umrs/server/workflow_runner.py` | Implemented — `run_workflow_record()`: DAG waves, thread pool, routing, image/GPU hints, payload S3 hints |

### What Is Missing

The following modules are verified to NOT exist in the codebase:

| Missing Module | Why It Matters |
|----------------|----------------|
| `ScientificReward` | Without a multi-class reward function, the loop runs but doesn't optimize. Currently `DECIDING` uses crude heuristics (`confidence >= 0.9` → PUBLISH). |
| `BeliefState` / `BeliefUpdater` | Without a belief state, the system can't reason about uncertainty. Experiments should maximize Expected Information Gain, not just performance. |
| `ResearchDirector` | The `ResearchOrchestrator` runs ONE goal. There's no layer managing a portfolio of open questions, allocating budget across them, or stopping bad directions. |
| `ResearchProgram` | No object groups multiple related questions under a shared program with its own budget and metrics. |
| `QuestionManager` | No layer between Director and HypothesisManager. Without it, Director becomes a god object. |
| `WorkingResearchState` | A2L is immutable provenance (Git for science). But the Director needs mutable, decision-optimized working memory. This separation doesn't exist yet. |
| `ExperimentEngine` as primary abstraction | `ExperimentRunner` exists but is still workflow-centric. The user sees `workflow.submit()` not `experiment.execute()`. |

---

## The 5-Round Architecture Review: Convergence Narrative

Five rounds of architecture review progressively refined the vision. Here is
how the thinking evolved:

### Round 1: "Build the Scientific Memory Graph First"

**Verdict:** Over-invests in knowledge representation before proving the system
can do autonomous science. Graphs are expensive; you don't yet know what
information truly matters.

### Round 2: "Reward Determines Which Knowledge Matters"

**Verdict:** The deepest architectural observation. Without reward, A2L becomes
a write-only archive. With reward on every artifact, `/derive` lineage naturally
becomes a reward-weighted scientific graph — for free, without building a new
graph store.

### Round 3: "Decision-Making First, Memory Second"

**Verdict:** Memory without decision-making becomes a database. The right
sequencing is: Experiment → Reward → Director → Journal. Build the Journal
(A2L) before the Director — a Director without memory is just a stateless
scheduler.

### Round 4: "Add QuestionManager/HypothesisManager Layers"

**Verdict:** ResearchDirector shouldn't directly manage experiments. Insert
intermediate layers: Director → QuestionManager → HypothesisManager →
ExperimentEngine. This avoids the god-object anti-pattern.

### Round 5: "AI4S Is a Control System, Not a Knowledge Store"

**Verdict:** The final framing. AI4S is not storing science; it is **controlling
scientific exploration**. The missing object is not `Reward` but `BeliefState` —
an embryonic belief system that already exists in `HypothesisManager` but hasn't
been formalized. The Director should ask "Which experiment most reduces
uncertainty?" (Expected Information Gain) rather than "Which experiment gets the
highest reward?" (fitness function).

### The Convergence Point

All five rounds converge to:

```
Right direction:
  Experiment → Reward → Director → Journal → Graph (later)

Wrong direction:
  Memory First → Graph First → Ontology First
```

The fundamental object hierarchy is:

```
Research Program
    ↓
Question
    ↓
Hypothesis
    ↓
Experiment
    ↓
Evidence
    ↓
Conclusion
```

Not:

```
Workflow → Task → Job
```

Those remain inside UMRS.

---

## The Final Architecture

### Overview: 5 Layers + 2 Cross-Cutting Systems

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Research Program                                  │
│  (top-level scientific object — the "why")                   │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Research Director                                 │
│  (the controller / AI CEO)                                   │
│  allocate_budget() · prioritize() · stop_direction()         │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌──────────────────────────┬──────────────────────────────────┐
│  Layer 3: Experiment Engine │  QuestionManager /            │
│  (primary abstraction)      │  HypothesisManager             │
│  experiment.execute()       │  choose_question() ·           │
│                             │  choose_hypothesis()           │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: A2L Scientific Ledger                             │
│  (immutable provenance — Git for science)                    │
│  /derive endpoint · content-addressed artifacts              │
└─────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 5: UMRS Execution Kernel                             │
│  (workflow_runner · step_executor · routers · durable jobs) │
└─────────────────────────────────────────────────────────────┘

Cross-Cutting System 1: WorkingResearchState
  (mutable, decision-optimized — the Director queries this, not A2L)

Cross-Cutting System 2: BeliefState / BeliefUpdater
  (scientific reasoning engine — tracks uncertainty, computes EIG)
```

### Layer 1: Research Program

**What it is:** The top-level scientific object. The "why" — the reason the
research exists.

```python
@dataclass
class ResearchProgram:
    id: str                          # rp_2026_001
    name: str                        # "Autonomous Video Monitoring"
    goals: list[str]                 # ["Maximize object detection accuracy"]
    questions: list[str]             # IDs of open research questions
    budget: ProgramBudget            # GPU-hours, iterations, cost
    metrics: list[Metric]            # ["recall", "false_positive_rate"]
    status: ProgramStatus            # ACTIVE | PAUSED | COMPLETED
```

**Example:**

```
Research Program: Autonomous Video Monitoring

Questions:
  Motion Detection
  Species Classification
  False Positive Reduction
  Camera Placement Optimization
```

### Layer 2: Research Director

**What it is:** The intelligence layer. The AI CEO. Given a portfolio of open
questions, it allocates budget, prioritizes branches, and stops bad directions.

```python
class ResearchDirector:
    def allocate_budget(self, portfolio: Portfolio) -> BudgetAllocation:
        """Distribute compute across open questions using UCB1-style
        regret minimization."""

    def prioritize(self, questions: list[Question]) -> list[Question]:
        """Rank open questions by expected scientific value
        (novelty × feasibility × impact)."""

    def stop_direction(self, question_id: str) -> bool:
        """Kill underperforming research directions
        (reward below portfolio median for N iterations)."""

    def choose_next_experiment(
        self, belief_state: BeliefState
    ) -> Experiment:
        """Choose experiment that maximizes Expected Information Gain
        (not just reward)."""
```

**Key insight:** The Director asks "Which experiment most reduces uncertainty?"
(maximizes Expected Information Gain) rather than "Which experiment gets the
highest reward?" This is the fundamental difference between a fitness function
and a scientific reward function.

### Layer 3: Experiment Engine + Question/Hypothesis Managers

**What it is:** The interface between science and execution.
`experiment.execute()` is the primary API; `workflow.submit()` becomes an
implementation detail.

```python
class ExperimentEngine:
    def design(self, hypothesis: Hypothesis,
               constraints: Constraints) -> Experiment:
        """Use ED agent to generate experiment protocol."""

    def execute(self, experiment: Experiment) -> ExperimentResults:
        """Generate workflow(s) from protocol, submit to UMRS, monitor."""

    def evaluate(self, experiment: Experiment,
                 results: ExperimentResults) -> Evidence:
        """Use DA agent + ScientificReward.score() to produce evidence."""

    def evolve(self, experiment: Experiment,
               history: ExperimentHistory) -> Experiment:
        """Use evolutionary optimization to redesign the experiment."""
```

**Wraps:** `umrs/src/umrs/server/workflow_runner.py`
(`run_workflow_record()` is the in-process execution path).

**Intermediate managers (avoid god-object anti-pattern):**

```python
class QuestionManager:
    def choose_question(self, portfolio: Portfolio) -> Question:
        """Select the next question to investigate."""

class HypothesisManager:
    """Already exists at r4/src/r4/agent/hypothesis.py:238"""
    def choose_hypothesis(self, question: Question) -> Hypothesis:
        """Select the next hypothesis to test."""
```

### Layer 4: A2L Scientific Ledger (Immutable Provenance)

**What it is:** Git for science. Auditability, reproducibility, provenance.
Not the working memory.

```python
# A2L /derive endpoint (agents MUST use this — guarantees lineage, no orphans)
derive_artifact(
    parents=["a2l_question_001"],       # Question artifact
    operation="formulate_hypothesis",
    type="hypothesis",
    metadata={
        "statement": "distance_threshold=0.05 maximizes recall",
        "confidence": 0.5,              # Initial confidence
    }
)
# Returns: a2l_hypothesis_H001

derive_artifact(
    parents=["a2l_hypothesis_H001"],    # Hypothesis artifact
    operation="design_experiment",
    type="experiment",
    metadata={
        "protocol": {"distance_threshold": 0.05},
        "workflow_ref": "wf_2026_001",
    }
)
# Returns: a2l_experiment_E001

derive_artifact(
    parents=["a2l_experiment_E001"],    # Experiment artifact
    operation="execute_and_measure",
    type="result",
    metadata={
        "recall": 0.82,
        "false_positive_rate": 0.15,
        "processing_time_seconds": 60,
    }
)
# Returns: a2l_result_R001
```

**Lineage (the A2L DAG):**

```
Question (a2l_Q1)
  ↓ derived_from
Hypothesis (a2l_H1)
  ↓ derived_from
Experiment (a2l_E1)
  ↓ derived_from
Result (a2l_R1)
  ↓ derived_from (supports=True, strength=0.8)
Evidence (a2l_Ev1)
  ↓ derived_from (multiple Evidence)
Conclusion (a2l_C1)
```

This is the **immutable scientific ledger**. Every artifact is content-addressed
(SHA-256), every edge is a `derived_from` relationship, and the entire history
is queryable via A2L.

**Existing A2L infrastructure (verified):**

| A2L Component | Location | Status |
|---------------|----------|--------|
| `Artifact` (immutable, SHA-256 ID, status FSM) | `a2l/src/a2l/models.py:48` | Implemented |
| `ArtifactEdge` (parent→child DAG lineage) | `a2l/src/a2l/models.py:92` | Implemented |
| `ArtifactDerive` (agents MUST use /derive) | `a2l/src/a2l/models.py:125` | Implemented |
| `ArtifactStatus` (pending→running→completed/failed) | `a2l/src/a2l/models.py:12` | Implemented |
| A2L schema (Postgres DDL with pgvector) | `a2l/src/a2l/schema.py` | Implemented |
| `/a2l/artifacts/derive` endpoint | `a2l/src/a2l/api/artifacts.py:92` | Implemented |
| `/a2l/artifacts/{id}/lineage` endpoint | `a2l/src/a2l/api/artifacts.py:354` | Implemented |

### Layer 5: UMRS Execution Kernel

**What it is:** The workflow kernel. This is the execution substrate — it
should remain a pure workflow kernel and never contain scientific concepts.

```python
# UMRS stays as-is:
# - workflow_runner.py: in-process execution with DAG waves, thread pool
# - step_executor.py: pluggable executor (inproc | subprocess)
# - step_router.py: step-level routing with affinity, image, GPU hints
# - durable jobs: heavy/multi-node execution
# - A2L: artifact lineage (/derive endpoint)
```

### Cross-Cutting System 0: Domain Plugin Interface

**What it is:** The pluggable domain interface. Each domain provides the
methods needed to run the autonomous science loop for that specific field of
inquiry. Without this interface, AI4S becomes "Autonomous Single-Domain Research
System" instead of "Autonomous Science Platform."

```python
class ScienceDomain(Protocol):
    """A pluggable scientific domain.

    Each domain provides the methods needed to run the autonomous
    science loop for that specific field of inquiry.
    """

    def formulate_hypothesis(
        self, question: Question, context: ResearchContext
    ) -> Hypothesis:
        """Generate a testable hypothesis from the research question."""

    def design_experiment(
        self, hypothesis: Hypothesis, constraints: Constraints
    ) -> Experiment:
        """Design an experiment to test the hypothesis."""

    def evaluate_results(
        self, experiment: Experiment, results: ExperimentResults
    ) -> Evidence:
        """Interpret experiment results as evidence for/against."""

    def compute_reward(
        self, experiment: Experiment, evidence: Evidence
    ) -> ScientificReward:
        """Compute the multi-class scientific reward."""
```

Then:

```python
class ExampleDomain:
    """Example video motion detection domain."""

class ProteinDomain:
    """Protein folding dynamics domain."""

class HPCOptimizationDomain:
    """HPC code optimization domain."""
```

plug into the same loop.

### Cross-Cutting System 1: WorkingResearchState

**What it is:** The mutable, decision-optimized working memory. The Director
queries this, not A2L directly. A2L is Git (immutable); WorkingResearchState is
the IDE Index + Build Cache (mutable, queryable).

```python
@dataclass
class WorkingResearchState:
    """Mutable, decision-optimized working memory.

    Derived from A2L (immutable provenance) + live experiment state.
    The Director queries this, not A2L directly.
    """

    active_questions: list[Question]           # Currently open questions
    active_hypotheses: dict[str, Hypothesis]  # HypothesisID -> Hypothesis
    current_beliefs: BeliefState              # Current belief state
    reward_history: list[RewardEntry]         # Historical rewards
    budget_state: BudgetState                 # Current budget consumption
    portfolio_state: PortfolioState           # Cross-question portfolio state
```

**Sync pattern:** A2L → WorkingResearchState (one-way sync, read-only from
A2L's perspective). The Git analogy:

```
Git (A2L)              = immutable history
IDE Index (WRS)        = working state for decisions
Build Cache (WRS)      = derived indexes for fast queries
```

### Cross-Cutting System 2: BeliefState / BeliefUpdater

**What it is:** The scientific reasoning engine. This is where
`HypothesisManager` evolves into a `BeliefState` that tracks uncertainty across
all hypotheses, and `BeliefUpdater` updates beliefs based on new evidence using
Bayesian updating.

```python
@dataclass
class BeliefState:
    """Current beliefs about all hypotheses.

    This is the scientific reasoning engine's state. It tracks:
    - Confidence in each hypothesis (0.0–1.0)
    - Evidence supporting/contradicting each hypothesis
    - Overall uncertainty (entropy) of the belief state
    """

    hypotheses: dict[str, HypothesisBelief]  # HypothesisID -> HypothesisBelief

    @property
    def entropy(self) -> float:
        """Shannon entropy of the belief distribution.
        Higher = more uncertain."""

    @property
    def most_likely_hypothesis(self) -> tuple[str, float]:
        """Return (hypothesis_id, confidence) of the most likely hypothesis."""


@dataclass
class HypothesisBelief:
    """Belief in a single hypothesis."""

    hypothesis_id: str
    confidence: float               # 0.0–1.0
    evidence_for: list[str]         # Evidence IDs
    evidence_against: list[str]     # Evidence IDs
    last_updated: datetime


class BeliefUpdater:
    """Updates BeliefState based on new evidence.

    Uses Bayesian updating: P(H|E) = P(E|H) * P(H) / P(E)

    The key insight: experiments should maximize Expected Information Gain,
    not just expected reward. This is how real science works.
    """

    def update(self, beliefs: BeliefState,
               evidence: Evidence) -> BeliefState:
        """Update beliefs based on new evidence using Bayesian updating."""

    def expected_information_gain(
        self, beliefs: BeliefState, experiment: Experiment
    ) -> float:
        """Calculate EIG: how much will this experiment reduce uncertainty?

        EIG(e) = H(beliefs) - E[H(beliefs | outcome of e)]

        High EIG = experiment will significantly reduce uncertainty.
        Low EIG = experiment will provide little new information.
        """
```

**The Information Gain argument:**

Most autonomous-agent systems accidentally optimize:

```
accuracy
```

when they should optimize:

```
knowledge
```

Example:

```
H1: Threshold tuning
H2: New motion model
H3: Temporal aggregation
```

Suppose:

```
Experiment A: +0.5% accuracy
Experiment B: Completely disproves H2
```

A normal optimizer chooses A.

A scientist often chooses B — because B collapses a huge part of the search
space.

This is the strongest argument for `ScientificReward` being fundamentally
different from `FitnessFunction`.

---

## The MVP Definition

The v1 MVP is much smaller than the full architecture. It is the minimal
autonomous loop that proves AI4S can do science without human intervention:

```
Goal
 ↓
Hypothesis
 ↓
Experiment
 ↓
Reward
 ↓
A2L
 ↓
Next Hypothesis
```

If this runs for days and continuously improves a measurable metric, the core
thesis is proven. Everything else (ResearchProgram, ResearchDirector, Portfolio
optimization, Scientific Graph) is built on top of a proven autonomous discovery
loop.

**MVP acceptance criteria:**

1. AI4S autonomously generates a hypothesis about video motion detection
   parameters (e.g., "distance_threshold=0.05 maximizes recall")
2. AI4S autonomously designs and runs an experiment (an existing
   example detection workflow)
3. AI4S autonomously scores the result with `ScientificReward`
4. AI4S autonomously records the lineage to A2L
5. AI4S autonomously generates the next hypothesis based on the reward
6. The loop runs for at least 24 hours without human intervention
7. The cumulative reward (Autonomous Discovery Rate) improves over time

---

## The 4-Week Roadmap

**Dependency graph (must build in this order):**

```
ScientificReward  (Week 1)
        ↓
BeliefState       (Week 2)
        ↓
WorkingResearchState  (Week 3)
        ↓
ResearchDirector  (Week 4)
```

**Why this order:** The Director's entire purpose is `choose_next_experiment()`,
which depends on `belief_state`. If BeliefState does not exist yet, the Director
is mostly a scheduler — it picks the highest-confidence hypothesis, which is just
sorting. With BeliefState, the Director asks "Which experiment most reduces
uncertainty?" (maximizes Expected Information Gain), which is genuine scientific
reasoning.

### Week 1: Experiment + Reward + Domain Plugin Interface

**Goal:** Every experiment gets scored with a concrete, implementable
multi-class reward. Experiment becomes a first-class object. The `ScienceDomain`
plugin interface is defined so the first domain is just one plugin — not a
hardcoded system.

**Deliverables:**

1. `ScientificReward` — multi-class reward function with **concrete MVP
   approximations** for each component:
   ```python
   class ScientificReward:
       # Concrete MVP approximations (not abstract concepts)
       performance: float           # = metric_delta (e.g., recall_new - recall_old)
       novelty: float               # = distance_from_previous_experiments
       information_gain: float     # = belief_entropy_before - belief_entropy_after
       reproducibility: float      # = 1.0 if result replicated else 0.0
       cost: float                  # = normalized_gpu_hours

       # Weights (tunable per program type)
       performance_weight: float = 0.5
       novelty_weight: float = 0.1
       information_gain_weight: float = 0.2
       reproducibility_weight: float = 0.1
       cost_weight: float = 0.1

       @property
       def total(self) -> float:
           """Weighted sum of all reward components."""
           return (
               self.performance_weight * self.performance
               + self.novelty_weight * self.novelty
               + self.information_gain_weight * self.information_gain
               + self.reproducibility_weight * self.reproducibility
               - self.cost_weight * self.cost
           )
   ```
   - `performance` = metric_delta (e.g., `recall_new - recall_old`)
   - `novelty` = distance_from_previous_experiments (e.g., L2 distance in
     parameter space from all prior experiments)
   - `information_gain` = belief_entropy_before - belief_entropy_after (requires
     BeliefState from Week 2; for Week 1, approximate as 0.0)
   - `reproducibility` = 1.0 if result replicated else 0.0
   - `cost` = normalized_gpu_hours (e.g., `gpu_hours / max_gpu_hours`)

2. `Experiment` — first-class object with `execute()` method that wraps
   `workflow_runner.run_workflow_record()`

3. `ScienceDomain` — pluggable domain interface (Protocol):
   ```python
   class ScienceDomain(Protocol):
       """A pluggable scientific domain.

       Each domain provides the methods needed to run the autonomous
       science loop for that specific field of inquiry.
       """

       def formulate_hypothesis(
           self, question: Question, context: ResearchContext
       ) -> Hypothesis:
           """Generate a testable hypothesis from the research question."""

       def design_experiment(
           self, hypothesis: Hypothesis, constraints: Constraints
       ) -> Experiment:
           """Design an experiment to test the hypothesis."""

       def evaluate_results(
           self, experiment: Experiment, results: ExperimentResults
       ) -> Evidence:
           """Interpret experiment results as evidence for/against."""

       def compute_reward(
           self, experiment: Experiment, evidence: Evidence
       ) -> ScientificReward:
           """Compute the multi-class scientific reward."""
   ```
   Then `ExampleDomain`, `ProteinDomain`, `ClimateDomain`,
   `HPCOptimizationDomain` plug into the same loop.

   **Without this interface, AI4S becomes "Autonomous Single-Domain Research System"
   instead of "Autonomous Science Platform".**

**Files to create:**

- `science/__init__.py` — shared contracts layer (pattern: `r4-contracts/`)
- `science/reward.py` — `ScientificReward` dataclass with weighted components
- `science/experiment.py` — `Experiment` dataclass (id, hypothesis_id,
  protocol, workflow_ref, status, results, reward_score)
- `science/domain.py` — `ScienceDomain` Protocol + `ResearchContext`,
  `Constraints` dataclasses
- `science/domains/example.py` — `ExampleDomain` (first domain plugin)

**Files to modify:**

- `r4/src/r4/agent/research_orchestrator.py` — `DECIDING` phase calls
  `ScientificReward.score()` instead of pure confidence threshold
- `r4/src/r4/research_loop/state.py` — `ExperimentRecord` gains
  `reward_score: float`; `ResearchLoopState` gains `cumulative_reward: float`

**Acceptance:**

- [ ] `ScientificReward` dataclass created with concrete MVP approximations
      for each component (performance, novelty, information_gain,
      reproducibility, cost)
- [ ] `Experiment` dataclass created with `execute()` method
- [ ] `ScienceDomain` Protocol created
- [ ] `ExampleDomain` created as first domain plugin
- [ ] A research loop with `ScientificReward` enabled produces measurably
      better experiment sequences (higher cumulative reward, fewer wasted
      experiments) than the heuristic baseline on the example analysis benchmark
- [ ] `Experiment` is a first-class object with `execute()` method that
      internally calls `workflow_runner.run_workflow_record()`

**Status:** Not started

---

### Week 2: BeliefState + BeliefUpdater (EIG)

**Goal:** The `HypothesisManager` evolves into a `BeliefState` that tracks
uncertainty across all hypotheses, and `BeliefUpdater` updates beliefs based on
new evidence using Bayesian updating. The Director asks "Which experiment most
reduces uncertainty?" (maximizes Expected Information Gain) rather than "Which
experiment gets the highest reward?" (fitness function).

**Why Week 2 (not Week 4):** The Director's `choose_next_experiment()` depends
on `belief_state`. If you build the Director before BeliefState, the Director is
mostly a scheduler. With BeliefState in place, the Director can do EIG-based
selection — genuine scientific reasoning.

**Deliverables:**

1. `BeliefState` — tracks confidence and uncertainty across all hypotheses:
   ```python
   @dataclass
   class BeliefState:
       """Current beliefs about all hypotheses.

       This is the scientific reasoning engine's state. It tracks:
       - Confidence in each hypothesis (0.0–1.0)
       - Evidence supporting/contradicting each hypothesis
       - Overall uncertainty (Shannon entropy) of the belief state
       """

       hypotheses: dict[str, HypothesisBelief]  # HypothesisID -> HypothesisBelief

       @property
       def entropy(self) -> float:
           """Shannon entropy of the belief distribution.
           Higher = more uncertain."""

       @property
       def most_likely_hypothesis(self) -> tuple[str, float]:
           """Return (hypothesis_id, confidence) of the most likely hypothesis."""
   ```

2. `BeliefUpdater` — Bayesian updating of beliefs based on new evidence:
   ```python
   class BeliefUpdater:
       """Updates BeliefState based on new evidence.

       Uses Bayesian updating: P(H|E) = P(E|H) * P(H) / P(E)
       """

       def update(self, beliefs: BeliefState,
                  evidence: Evidence) -> BeliefState:
           """Update beliefs based on new evidence using Bayesian updating."""

       def expected_information_gain(
           self, beliefs: BeliefState, experiment: Experiment
       ) -> float:
           """EIG(e) = H(beliefs) - E[H(beliefs | outcome of e)]

           High EIG = experiment will significantly reduce uncertainty.
           Low EIG = experiment will provide little new information.
           """
   ```

3. Expected Information Gain (EIG) calculation
4. Uncertainty reduction as a first-class metric

**Files to create:**

- `r4/src/r4/agent/belief_state.py` — `BeliefState`, `HypothesisBelief`
- `r4/src/r4/agent/belief_updater.py` — `BeliefUpdater`

**Files to modify:**

- `r4/src/r4/agent/hypothesis.py` — `HypothesisManager` evolves to use
  `BeliefState` internally (instead of bare `confidence: float`)
- `r4/src/r4/agent/research_orchestrator.py` — `DECIDING` phase uses
  `BeliefState.entropy` and `BeliefUpdater.expected_information_gain()`
- `science/reward.py` — `information_gain` component now computes from
  `BeliefState` (was 0.0 placeholder in Week 1)

**Acceptance:**

- [ ] `BeliefState` tracks confidence and entropy for all hypotheses
- [ ] `BeliefUpdater` performs Bayesian updates based on new evidence
- [ ] The `DECIDING` phase uses EIG to choose experiments that maximize
      uncertainty reduction
- [ ] Experiment selection via EIG reduces the number of experiments needed
      to reach a confidence threshold by ≥30% vs. random selection on a
      simulated hypothesis-testing benchmark

**Status:** Not started

---

### Week 3: A2L Scientific Ledger + WorkingResearchState

**Goal:** Use A2L's existing artifact/lineage infrastructure as the Scientific
Journal backend. A2L is immutable provenance (Git for science) — not working
memory. Build `WorkingResearchState` as the mutable, decision-optimized layer
that persists `BeliefState` (from Week 2) and that the Director queries.

**Deliverables:**

1. Science-specific A2L artifact types (research_program, question, hypothesis,
   experiment, result, evidence, conclusion)
2. Science-specific lineage rules (Hypothesis derived_from Question, etc.)
3. `WorkingResearchState` — mutable, decision-optimized working memory that
   persists `BeliefState`:
   ```python
   @dataclass
   class WorkingResearchState:
       """Mutable, decision-optimized working memory.

       Derived from A2L (immutable provenance) + live experiment state.
       The Director queries this, not A2L directly.
       """

       active_questions: list[Question]
       active_hypotheses: dict[str, Hypothesis]
       current_beliefs: BeliefState              # Persisted from Week 2
       reward_history: list[RewardEntry]
       budget_state: BudgetState
       portfolio_state: PortfolioState
   ```
4. A2L → WorkingResearchState sync pattern

**Files to create:**

- `a2l/src/a2l/science_types.py` — science-specific artifact type definitions
- `a2l/src/a2l/science_lineage.py` — science-specific lineage rules
- `r4/src/r4/agent/working_research_state.py` — `WorkingResearchState`

**Files to modify:**

- `r4/src/r4/agent/research_orchestrator.py` — every iteration creates A2L
  artifacts via `/derive`:
  - Question → Hypothesis (derive)
  - Hypothesis → Experiment (derive)
  - Experiment → Result (derive, with reward in metadata)
  - Result → Evidence (derive, with supports/strength in metadata)
- `a2l/src/a2l/models.py` — add `reward` and `decision` columns to
  `a2l_artifacts` (additive migration)

**Acceptance:**

- [ ] Science-specific A2L artifact types created
- [ ] Science-specific lineage rules created
- [ ] `WorkingResearchState` created and persists `BeliefState`
- [ ] After a research loop completes, the entire scientific history is
      queryable via A2L:
  - `GET /a2l/artifacts?type=result&metadata.reward_gt=0.5` — high-reward
    results
  - `GET /a2l/artifacts/{id}/lineage` — full derivation chain from Question to
    Result
- [ ] A hypothesis created in research loop A is visible in research loop B
      (cross-loop queryability via A2L)
- [ ] `WorkingResearchState` is queryable in O(1) for "what are the current
      beliefs about all hypotheses?"

**Status:** Not started

---

### Week 4: ResearchDirector + ResearchProgram

**Goal:** Create the AI CEO layer that manages a portfolio of open questions
under shared programs. The Director uses `BeliefState` (Week 2) and
`WorkingResearchState` (Week 3) to allocate budget, prioritize branches, and
stop bad directions.

**Deliverables:**

1. `ResearchDirector` — the AI CEO:
   - `allocate_budget()` — distribute compute across open questions using
     UCB1-style regret minimization
   - `prioritize()` — rank open questions by expected scientific value
     (novelty × feasibility × impact)
   - `stop_direction(question_id)` — kill underperforming research directions
     (reward below portfolio median for N iterations)
   - `spawn_loop(question)` — create a new `ResearchOrchestrator` for a
     promising question
   - `choose_next_experiment(belief_state)` — use EIG to select the experiment
     that most reduces uncertainty
   - `rebalance()` — periodic reallocation based on reward trajectories

2. `ResearchProgram` — top-level scientific object:
   ```python
   @dataclass
   class ResearchProgram:
       id: str                          # rp_2026_001
       name: str                        # "Autonomous Video Monitoring"
       goals: list[str]                 # ["Maximize object detection accuracy"]
       questions: list[str]             # IDs of open research questions
       budget: ProgramBudget            # GPU-hours, iterations, cost
       metrics: list[Metric]            # ["recall", "false_positive_rate"]
       status: ProgramStatus            # ACTIVE | PAUSED | COMPLETED
   ```

3. **`QuestionManager` — Optional MVP+** (see below)

**Files to create:**

- `r4/src/r4/agent/research_director.py` — `ResearchDirector`
- `r4/src/r4/agent/research_program.py` — `ResearchProgram` dataclass

**Files to modify:**

- `r4/src/r4/cli.py` — new commands:
  - `r4 research program` — show active programs, budgets, reward trajectories
  - `r4 research spawn "question"` — launch new research loop under director
  - `r4 research stop <question_id>` — stop a direction
- `r4/src/r4/agent/research_orchestrator.py` — `ResearchOrchestrator` reports
  reward trajectory to `ResearchDirector` for budget reallocation

**Acceptance:**

- [ ] `ResearchDirector` created with `allocate_budget()`, `prioritize()`,
      `stop_direction()`, `choose_next_experiment()`
- [ ] `ResearchProgram` dataclass created
- [ ] Director manages 3+ concurrent research questions under 1+ research
      programs
- [ ] Director reallocates budget from stagnant directions to promising ones
      (demonstrable on a multi-question benchmark)
- [ ] Director stops directions whose cumulative reward falls below a dynamic
      threshold
- [ ] Director's decisions are informed by `WorkingResearchState` which is
      derived from A2L provenance + live experiment state
- [ ] Director's `choose_next_experiment()` uses EIG from `BeliefState`

**Status:** Not started

#### QuestionManager: Optional MVP+

`QuestionManager` is **not required for the MVP**. The `ResearchDirector` can
manage `HypothesisManager` directly initially:

```
ResearchDirector
      ↓
HypothesisManager (already exists at r4/src/r4/agent/hypothesis.py:238)
      ↓
ExperimentEngine
```

Introduce `QuestionManager` only when real multi-question programs exist and the
god-object anti-pattern becomes a practical problem. Mark as **Optional MVP+**.

---

## Operational Knowledge: Learning How to Do Science

The 4-week MVP builds a system that learns **scientific knowledge** — facts
about the domain (e.g., "threshold=0.05 improves object detection recall").

But there is a second, equally valuable form of knowledge that AI4S should
also learn: **operational knowledge** — facts about *how to do science
efficiently*.

```
Scientific Knowledge (what is true):
  threshold=0.05 improves recall
  time_threshold=30s reduces false positives

Operational Knowledge (how to do science efficiently):
  Claude Opus → excellent planning, expensive
  GPT-5 → excellent coding, medium cost
  Qwen3-32B → strong extraction, cheap
  GPU cluster A → faster for vision tasks
  Robot lab B → reliable for wet experiments
```

Both are bandit problems:
- Research questions are bandits (which question to investigate next)
- Models/tools/resources are bandits (which resource to use for each task)

### Model Reputation System

Today, most agent systems hardcode model assignments:

```
Planning   → Claude Opus
Coding     → GPT-5
Extraction → Qwen3-8B
Analysis   → Gemini
```

These decisions are manually chosen. But AI4S should *learn* them.

The future flow:

```
Task
  ↓
Model Reputation System
  ↓
Best Model Selection (based on learned reputation)
  ↓
Execution
  ↓
Reward
  ↓
Update Reputation
```

This is another belief system — a `ModelBeliefState`:

```python
@dataclass
class ModelProfile:
    """Accumulated reputation for a single model."""
    model: str                           # e.g. "claude-opus-4"
    planning_score: float                # 0.0–1.0
    coding_score: float                  # 0.0–1.0
    extraction_score: float              # 0.0–1.0
    analysis_score: float                # 0.0–1.0
    avg_cost: float                      # dollars per task
    avg_latency: float                   # seconds
    tasks_completed: int                 # sample size for confidence
    success_rate: float                  # fraction of tasks that succeeded

@dataclass
class ModelBeliefState:
    """Learned beliefs about model capabilities.

    This is the operational counterpart to BeliefState (scientific beliefs).
    Both are updated via Bayesian updating after each experiment.
    """
    models: dict[str, ModelProfile]      # model_id → ModelProfile

    def best_model_for(self, task_type: str, budget: float) -> str:
        """Select the best model for a task type within budget.

        Uses UCB1-style selection: expected_score + exploration_bonus.
        """
```

### Capability Reputation System (Generalized)

The Model Reputation System generalizes to a **Capability Reputation System**
that tracks performance across all allocatable resources:

```python
@dataclass
class CapabilityProfile:
    """Learned reputation for any allocatable resource."""
    resource_id: str                     # e.g. "gpt-5", "gpu-cluster-a", "robot-lab-b"
    resource_type: str                   # "llm" | "gpu" | "robot" | "microscope" | "lab"
    capabilities: dict[str, float]       # task_type → score (0.0–1.0)
    cost_per_task: float                 # dollars
    availability: float                  # 0.0–1.0 (fraction of time available)
    reliability: float                   # 0.0–1.0 (fraction of tasks that succeed)
    throughput: float                    # tasks per hour
    tasks_completed: int                 # sample size

@dataclass
class CapabilityReputationSystem:
    """Learns which resources are good at which tasks.

    This is the operational knowledge system. It answers:
    - Which LLM is best for hypothesis generation?
    - Which GPU cluster is fastest for vision tasks?
    - Which robot lab is most reliable for wet experiments?
    - Which microscope has the best throughput for imaging?

    All are bandit problems solved with UCB1 or Thompson Sampling.
    """
    resources: dict[str, CapabilityProfile]  # resource_id → CapabilityProfile

    def best_resource_for(
        self, task_type: str, budget: float, constraints: dict
    ) -> str:
        """Select the best resource for a task within budget and constraints."""
```

### Resource Director

The `ResourceDirector` is a new layer that optimizes resource allocation.
It is the operational counterpart to the `ResearchDirector`:

```python
class ResourceDirector:
    """Optimizes resource allocation across the scientific portfolio.

    The Research Director asks: "What should we learn next?"
    The Resource Director asks: "How do we spend finite resources?"

    These are different optimization problems that should be separated.
    """

    def allocate_resources(
        self,
        portfolio: ResearchPortfolio,
        reputation: CapabilityReputationSystem,
        budget: ResourceBudget,
    ) -> ResourceAllocation:
        """Allocate resources across experiments.

        Uses multi-armed bandit (UCB1 or Thompson Sampling) to balance:
        - Exploration: try new resources/models to learn their capabilities
        - Exploitation: use known-good resources for high-value tasks
        - Cost: prefer cheaper resources when quality is similar
        """
```

### Two Learnable Knowledge Systems

The key insight: AI4S should not only discover facts about science. It should
also discover facts about *how to do science efficiently*.

```
Scientific Knowledge (BeliefState):
  - Which hypotheses are supported/refuted?
  - What is the confidence in each hypothesis?
  - Which experiment most reduces uncertainty? (EIG)

Operational Knowledge (CapabilityReputationSystem):
  - Which LLM is best for hypothesis generation?
  - Which GPU cluster is fastest for vision tasks?
  - Which robot lab is most reliable for wet experiments?
  - Which microscope has the best throughput for imaging?
```

Both knowledge systems are updated after each experiment:

```
Experiment completes
  ↓
BeliefUpdater.update(beliefs, evidence)     ← updates scientific beliefs
  ↓
CapabilityReputationSystem.update(result)   ← updates operational beliefs
  ↓
Director uses both to choose next experiment:
  - BeliefState → EIG (which experiment reduces uncertainty most?)
  - CapabilityReputationSystem → cost (which resource is cheapest for this task?)
  - Combined: choose experiment maximizing EIG/cost ratio
```

### Unified Resource Layer

All resources — LLMs, GPUs, HPC clusters, robots, microscopes, lab instruments
— should be treated uniformly as allocatable resources:

```python
class ScientificResource(Protocol):
    """Any allocatable resource for scientific experiments."""
    id: str
    capability: str           # what this resource can do
    cost: float               # cost per use
    availability: float       # 0.0–1.0

class LLMResource:
    """An LLM endpoint (e.g., Claude, GPT-5, Qwen3)."""

class GPUClusterResource:
    """A GPU cluster for compute-heavy experiments."""

class RobotResource:
    """A physical robot for automated experiments."""

class MicroscopeResource:
    """An automated microscope for imaging experiments."""

class LabInstrumentResource:
    """A lab instrument (e.g., spectrometer, chromatograph)."""
```

The scientific layer shouldn't care whether an experiment runs on a GPU cluster
or a robot lab. Both are just resources with capabilities, costs, and
availabilities.

### Long-Term Architecture (Month 3+)

```
Research Program
        ↓
Research Director (what should we learn next?)
        ↓
Question Manager
        ↓
Hypothesis Manager
        ↓
Experiment Engine
        ↓
Resource Director (how do we spend finite resources?)
        ↓
Capability Reputation System
        ↓
--------------------------------
| LLMs (Claude, GPT-5, Qwen3)  |
| GPUs (cluster A, cluster B)  |
| HPC (Slurm, K8s)             |
| Robots (lab A, lab B)        |
| Microscopes (auto-imaging)   |
| Lab Instruments (spectrometer) |
--------------------------------
        ↓
UMRS Execution Kernel
        ↓
A2L Scientific Ledger
```

The important insight: **scientific knowledge** and **operational knowledge**
are both learnable. The second form of knowledge — model capabilities, tool
reliability, robot performance, facility throughput — may ultimately be just
as valuable as the scientific discoveries themselves.

### Implementation Priority

The Capability Reputation System and Resource Director are **Month 3+ work**.
They should only be built after:

1. The 4-week MVP is proven (autonomous loop runs for days, ADR improves)
2. The ResearchDirector is working (Week 4)
3. There are multiple resources to choose between (otherwise, there's nothing
   to learn)

The initial implementation can be simple:

```python
# Simple UCB1 selection for models
def select_model(task_type, model_beliefs, budget):
    """UCB1: expected_score + sqrt(2 * ln(N) / n_i)"""
    best_model = None
    best_score = -float('inf')
    total_tasks = sum(p.tasks_completed for p in model_beliefs.models.values())

    for model_id, profile in model_beliefs.models.items():
        if profile.avg_cost > budget:
            continue

        # UCB1 score: expected capability + exploration bonus
        expected_score = profile.capabilities.get(task_type, 0.5)
        exploration_bonus = (2 * math.log(total_tasks + 1) /
                            max(1, profile.tasks_completed)) ** 0.5
        ucb_score = expected_score + exploration_bonus

        if ucb_score > best_score:
            best_score = ucb_score
            best_model = model_id

    return best_model
```

This gives the system the ability to learn which models are good at which
tasks, and to balance exploration (trying new models) with exploitation (using
known-good models).

---

## Knowledge Acquisition: Where Initial Knowledge Comes From

The current plan assumes:

```
Goal
 ↓
Hypothesis
 ↓
Experiment
 ↓
Reward
 ↓
A2L
```

but it does not answer:

```
Where does the initial knowledge come from?
```

Without that, every new research program starts almost from zero.

### Two Separate Loops

There are two different loops:

```
Scientific Discovery Loop          (creates new knowledge)
  Hypothesis → Experiment → Evidence → Conclusion

Knowledge Acquisition Loop         (imports existing knowledge)
  Papers/PDFs/Notebooks/Scripts/Datasets → Knowledge Intake → Knowledge Base
```

These are different concerns and should be separated.

### The Knowledge Acquisition Layer

The Knowledge Acquisition Layer sits **upstream** of the Research Director.
It is responsible for ingesting existing knowledge (papers, notebooks,
scripts, datasets, observations) and making it available to both humans
and AI.

```
Knowledge Acquisition Layer
        ↓
Knowledge Base (Shared Research Knowledge Base)
        ↓
Belief State (built from knowledge)
        ↓
Research Director
        ↓
Experiment Engine
        ↓
Resource Director
        ↓
Models / HPC / Robots / Facilities
        ↓
UMRS
        ↓
A2L Scientific Ledger
        ↓
Knowledge Base (experiments generate new knowledge)
```

The full loop becomes:

```
External Knowledge (papers, notebooks, scripts, datasets)
        ↓
Knowledge Base
        ↓
Belief State
        ↓
Research Director → Experiments → A2L
        ↓
Knowledge Base (experiments generate new knowledge)
```

This loop allows the system to start from a user's PDFs, notebooks, scripts,
datasets, and papers today, and gradually grow into a continuously learning
scientific organization rather than a collection of autonomous workflows.

### The UX Requirement: Intake Must Be Very Simple

Most research systems fail because ingestion is painful.

If researchers need to:
```
Upload PDF
Add metadata
Add tags
Add project
Add ontology
Link entities
Approve extraction
```
they stop using it.

Instead:
```
umrs knowledge add my_paper.pdf

umrs knowledge add notebook.ipynb

umrs knowledge add ./research_notes/

umrs knowledge add https://arxiv.org/xxxxx
```

Done. No human effort beyond the initial `add`.

### Auto Knowledge Extraction

The system should automatically extract structured knowledge from ingested
documents:

```
Document
  ↓
Chunking → Entities → Methods → Metrics → Datasets → Claims → Evidence → Embeddings
  ↓
Knowledge Objects
```

Example: User uploads `object_detection_experiment.pdf`

System automatically extracts:
```
Entities:
  - detected object
  - camera

Methods:
  - background subtraction
  - threshold detection

Metrics:
  - recall
  - precision

Claims:
  - threshold 0.05 improves recall

Evidence:
  - experiment section 4.2
```

No human effort required.

### KnowledgeObject: Not a Vector Store

This is where many systems go wrong. People say:
```
Let's build RAG.
```
No.

Researchers need much more than vector search. They need a **KnowledgeObject**
that is a first-class scientific entity:

```python
@dataclass
class KnowledgeObject:
    id: str
    type: str               # paper | notebook | dataset | script | experiment | claim | observation
    source: str             # URI of the source
    summary: str            # Auto-generated summary
    embeddings: list[float] # Vector representation (one access method)
    lineage: list[str]      # Parent KnowledgeObject IDs (provenance)
    reputation: float       # Confidence in this knowledge (0.0–1.0)
    extracted_entities: list[str]
    extracted_methods: list[str]
    extracted_metrics: list[str]
    extracted_claims: list[str]
```

Then vector search is merely **one access method** among many.

### Knowledge as A2L Artifacts

Today A2L stores:
```
Question
Hypothesis
Experiment
Result
Evidence
Conclusion
```

I would extend A2L to also store knowledge sources as first-class artifacts:
```
Document
Notebook
Script
Dataset
Observation
```

Example lineage:
```
paper.pdf (A2L artifact: type=document)
        ↓ derived_from
Claim (A2L artifact: type=claim)
        ↓ derived_from
Hypothesis (A2L artifact: type=hypothesis)
        ↓ derived_from
Experiment (A2L artifact: type=experiment)
```

Lineage becomes continuous — from external knowledge source through to
experimental result.

### Human and AI Must Share the Same Knowledge

This is critical. Many agent systems build:
```
AI memory
```
that humans cannot inspect.

Bad idea.

Instead:
```
Shared Research Knowledge Base
```
Human uses it.
AI uses it.
Same source of truth.

Explicitly:
```
Knowledge imported by humans = Knowledge visible to AI
Knowledge discovered by AI   = Knowledge visible to humans
```

Symmetry is important.

### Knowledge Reputation (KnowledgeBelief)

Not all knowledge is equal. Just like models, knowledge should have
reputation.

Example:
```
Notebook observation        → confidence 0.4
Peer reviewed paper         → confidence 0.9
Experiment reproduced 15x   → confidence 0.99
```

This becomes **KnowledgeBelief**, which later feeds directly into
**BeliefState** — because beliefs are built from knowledge.

```python
@dataclass
class KnowledgeBelief:
    knowledge_object_id: str
    confidence: float          # 0.0–1.0
    evidence_count: int        # How many sources support this
    reproduction_count: int    # How many times this has been reproduced
    last_verified: datetime
```

### KnowledgeManager: A Scientific Knowledge Curator

Not a search engine. A scientific knowledge curator.

```python
class KnowledgeManager:
    def ingest(self, source: str) -> KnowledgeObject:
        """Ingest a paper, notebook, script, dataset, or URL."""

    def extract(self, knowledge_object: KnowledgeObject) -> dict:
        """Auto-extract entities, methods, metrics, claims, evidence."""

    def index(self, knowledge_object: KnowledgeObject) -> None:
        """Add to vector index, keyword index, and knowledge graph."""

    def summarize(self, knowledge_object: KnowledgeObject) -> str:
        """Auto-generate a summary of the knowledge object."""

    def query(self, question: str) -> list[KnowledgeObject]:
        """Query the knowledge base by natural language question."""

    def recommend(self, context: ResearchContext) -> list[KnowledgeObject]:
        """Recommend relevant knowledge objects for the current research context."""
```

### The UX: `umrs knowledge status`

A researcher should be able to do:
```
umrs knowledge add papers/
umrs knowledge add notebooks/
umrs knowledge add scripts/
umrs knowledge add datasets/
```

and then:
```
umrs knowledge status
```

showing:
```
Knowledge Base
=============

Documents:     523
Notebooks:      61
Scripts:       112
Datasets:       18
Claims:      4,221
Methods:       382
Metrics:        74

Research Questions: 55
```

Then:
```
r4 ask "What is the current understanding of detection false-positive reduction?"
```

and both human and AI receive answers from the same knowledge base.

### Implementation Priority

The Knowledge Acquisition Layer is **Month 2+ work**. It should only be built
after the 4-week MVP is proven.

The initial implementation can be simple:
```python
# Simple knowledge ingestion
def ingest(source: str) -> KnowledgeObject:
    """Ingest a source and return a KnowledgeObject."""
    if source.endswith('.pdf'):
        return ingest_pdf(source)
    elif source.endswith('.ipynb'):
        return ingest_notebook(source)
    elif source.startswith('http'):
        return ingest_url(source)
    elif os.path.isdir(source):
        return ingest_directory(source)
    else:
        raise ValueError(f"Unknown source type: {source}")
```

### The Complete Loop

With the Knowledge Acquisition Layer, the complete loop becomes:

```
External Knowledge (papers, notebooks, scripts, datasets)
        ↓
[Knowledge Acquisition Layer]
        ↓
Knowledge Base (Shared Research Knowledge Base)
        ↓
[Knowledge Acquisition Layer]
        ↓
Belief State (built from knowledge)
        ↓
Research Director → Experiments → A2L
        ↓
Knowledge Base (experiments generate new knowledge)
```

This is the loop that allows AI4S to start from a user's PDFs, notebooks,
scripts, datasets, and papers today, and gradually grow into a continuously
learning scientific organization.

---

## The Benchmark: Autonomous Discovery Rate (ADR)

The single most important metric. Not detection accuracy. Not workflow
execution time. Not agent quality.

**Definition:**

$$\text{ADR} = \frac{\text{Scientific Value Discovered}}{\text{Compute Spent}}$$

**Units:** reward units per GPU-hour

**Scientific Value Breakdown:**

```
Performance gains:       How much did the metric improve?
Falsified hypotheses:    How many branches of the search space were eliminated?
Information gain:        How much did the experiment reduce uncertainty?
Reproducibility:         How reliable is the result?
```

**Example ADR report:**

```
Autonomous Discovery Rate (ADR):
================================

ADR = Scientific Value Discovered / Compute Spent
    = 11.287 reward units / 120 GPU-hours
    = 0.094 reward units per GPU-hour

Scientific Value Breakdown:
  Performance gains:        8.241 reward units (73%)
  Falsified hypotheses:     1.800 reward units (16%)  ← H3 refuted
  Information gain:         0.846 reward units ( 7%)  ← uncertainty reduction
  Reproducibility bonus:    0.400 reward units ( 4%)

ADR Trajectory (last 10 iterations):
  Iter  9: ADR=0.082 reward/GPU-hour
  Iter 10: ADR=0.085 reward/GPU-hour
  Iter 11: ADR=0.087 reward/GPU-hour
  ...
  Iter 18: ADR=0.094 reward/GPU-hour  ← improving!

Key Discoveries:
  1. Threshold 0.05 is optimal for object detection (confidence=0.92)
  2. Time threshold 30s reduces false positives by 47% (confidence=0.70)
  3. Distance threshold 0.02 is too aggressive — refuted (confidence=0.20)

Falsified Hypotheses:
  - H3: distance_threshold=0.02 → REFUTED (eliminated a branch of the search
    space)

If AI4S can continuously improve ADR over weeks, then you have something much
more interesting than an AI workflow platform.

You have the beginnings of an autonomous scientific organization.
```

---

## Risks and Mitigations

### Risk 1: Building the Director Before the Journal

**Risk:** A Director without memory becomes: Experiment → Forget → Experiment →
Forget.

**Mitigation:** Build A2L Scientific Journal (Week 2) before ResearchDirector
(Week 3). The Director needs history to make good decisions.

### Risk 2: Reward Hacking

**Risk:** The AI finds a way to maximize the reward function without actually
doing science (e.g., running trivial experiments that score high on
"performance" but contribute no knowledge).

**Mitigation:** Use multi-class rewards (Week 1). Include `information_gain`
and `novelty` components that penalize trivial experiments. Use EIG (Week 4) to
ensure experiments actually reduce uncertainty.

### Risk 3: A2L Becomes Operational State

**Risk:** If the Director queries A2L directly for every decision, the system
becomes slow and fragile. A2L is optimized for provenance, not real-time
decision queries.

**Mitigation:** Separate A2L (immutable provenance) from WorkingResearchState
(mutable, decision-optimized). The Director queries WorkingResearchState, not
A2L. WorkingResearchState is derived from A2L via one-way sync.

### Risk 4: God Object Anti-Pattern

**Risk:** If ResearchDirector directly manages experiments, it becomes a god
object with too many responsibilities.

**Mitigation:** Insert QuestionManager and HypothesisManager layers between
Director and ExperimentEngine (Week 3). Each manager has a single
responsibility.

### Risk 5: Over-Investing in Knowledge Representation

**Risk:** Building a Scientific Graph before proving the system can do
autonomous science. Graphs are expensive; you don't yet know what information
truly matters.

**Mitigation:** Build the Journal (A2L) first. Only evolve Journal → Graph
(Month 2+) once query patterns stabilize and you know which edges to
materialize.

### Risk 6: Premature Domain Specialization

**Risk:** Building `ProteinDiscoveryCycle`, `ClimateCycle`,
`HPCOptimizationCycle` before having 2-3 working domain cycles.

**Mitigation:** Use plugin composition (`Analyzer`, `ExperimentDesigner`,
`Evaluator`) instead of class inheritance. Start with one domain cycle
(an example detection analysis). Extract the base class only when a second domain emerges.

---

## Acceptance Criteria Summary

### Week 1: Experiment + Reward + Domain Plugin Interface

- [ ] `ScientificReward` dataclass created with concrete MVP approximations
      for each component (performance, novelty, information_gain,
      reproducibility, cost)
- [ ] `Experiment` dataclass created with `execute()` method
- [ ] `ScienceDomain` Protocol created
- [ ] `ExampleDomain` created as first domain plugin
- [ ] `ExperimentRecord` modified to include `reward_score: float`
- [ ] `ResearchLoopState` modified to include `cumulative_reward: float`
- [ ] `DECIDING` phase calls `ScientificReward.score()` instead of pure
      confidence threshold
- [ ] A research loop with `ScientificReward` enabled produces measurably
      better experiment sequences than the heuristic baseline

### Week 2: BeliefState + BeliefUpdater (EIG)

- [ ] `BeliefState` dataclass created with `entropy` and
      `most_likely_hypothesis` properties
- [ ] `HypothesisBelief` dataclass created
- [ ] `BeliefUpdater` class created with `update()` and
      `expected_information_gain()` methods
- [ ] `HypothesisManager` evolved to use `BeliefState` internally
- [ ] `DECIDING` phase uses EIG for experiment selection
- [ ] Experiment selection via EIG reduces the number of experiments needed
      to reach a confidence threshold by ≥30% vs. random selection

### Week 3: A2L Scientific Ledger + WorkingResearchState

- [ ] `science_types.py` created with science-specific artifact types
- [ ] `science_lineage.py` created with science-specific lineage rules
- [ ] `working_research_state.py` created with `WorkingResearchState`
- [ ] `research_orchestrator.py` modified to create A2L artifacts via
      `/derive`
- [ ] `a2l_artifacts` table modified with `reward` and `decision` columns
- [ ] After a research loop completes, the entire scientific history is
      queryable via A2L
- [ ] A hypothesis created in research loop A is visible in research loop B
- [ ] `WorkingResearchState` persists `BeliefState` from Week 2

### Week 4: ResearchDirector + ResearchProgram

- [ ] `ResearchDirector` created with `allocate_budget()`, `prioritize()`,
      `stop_direction()`, `choose_next_experiment()`
- [ ] `ResearchProgram` dataclass created
- [ ] `cli.py` modified with new `r4 research` commands
- [ ] `research_orchestrator.py` modified to report reward trajectory to
      `ResearchDirector`
- [ ] Director manages 3+ concurrent research questions under 1+ research
      programs
- [ ] Director reallocates budget from stagnant directions to promising ones
- [ ] Director's `choose_next_experiment()` uses EIG from `BeliefState`
- [ ] `QuestionManager` marked as Optional MVP+ (not required for MVP)

---

## Glossary

**A2L (Artifact & Agent Layer):** The immutable scientific ledger. Git for
science. Content-addressed artifacts with parent→child DAG lineage. Agents MUST
use the `/derive` endpoint (guarantees lineage, no orphans).

**ADR (Autonomous Discovery Rate):** The single most important metric.
$$\text{ADR} = \frac{\text{Scientific Value Discovered}}{\text{Compute Spent}}$$
Measured in reward units per GPU-hour. If AI4S can continuously improve ADR
over weeks, then you have something much more interesting than an AI workflow
platform.

**BeliefState:** The scientific reasoning engine's state. Tracks confidence
(0.0–1.0) in each hypothesis, evidence supporting/contradicting each hypothesis,
and overall uncertainty (Shannon entropy) of the belief distribution.

**BeliefUpdater:** Updates BeliefState based on new evidence using Bayesian
updating. Computes Expected Information Gain (EIG) to determine which
experiment most reduces uncertainty.

**EIG (Expected Information Gain):** How much will an experiment reduce
uncertainty?
$$\text{EIG}(e) = H(\text{beliefs}) - \mathbb{E}[H(\text{beliefs} \mid \text{outcome of } e)]$$
High EIG = experiment will significantly reduce uncertainty. Low EIG =
experiment will provide little new information.

**ExperimentEngine:** The interface between science and execution.
`experiment.execute()` is the primary API; `workflow.submit()` becomes an
implementation detail. Wraps
`umrs/src/umrs/server/workflow_runner.py`.

**HypothesisManager:** Manages hypothesis lifecycle (create, evaluate, revise).
Already exists at `r4/src/r4/agent/hypothesis.py:238`. Will evolve to use
`BeliefState` internally in Week 2.

**QuestionManager:** Manages the lifecycle of research questions. Inserted
between ResearchDirector and HypothesisManager to avoid the god-object
anti-pattern.

**ResearchDirector:** The AI CEO. Given a portfolio of open questions, it
allocates budget, prioritizes branches, and stops bad directions. Asks "Which
experiment most reduces uncertainty?" (maximizes EIG) rather than "Which
experiment gets the highest reward?" (fitness function). Built in Week 4,
after BeliefState (Week 2) and WorkingResearchState (Week 3).

**ResearchProgram:** The top-level scientific object. The "why" — the reason
the research exists. Groups multiple related questions under a shared program
with its own budget and metrics.

**ScienceDomain:** A pluggable scientific domain. Each domain provides the
methods needed to run the autonomous science loop for that specific field of
inquiry: `formulate_hypothesis()`, `design_experiment()`, `evaluate_results()`,
`compute_reward()`. Without this interface, AI4S becomes "Autonomous Single-Domain
Research System" instead of "Autonomous Science Platform."

**ScientificReward:** Multi-class reward function with concrete MVP
approximations:
$$\text{reward} = w_1 \cdot \text{performance} + w_2 \cdot \text{novelty} + w_3 \cdot \text{information\_gain} + w_4 \cdot \text{reproducibility} - w_5 \cdot \text{cost}$$
- `performance` = `metric_delta` (e.g., `recall_new - recall_old`)
- `novelty` = `distance_from_previous_experiments`
- `information_gain` = `belief_entropy_before - belief_entropy_after`
- `reproducibility` = `1.0 if result_replicated else 0.0`
- `cost` = `normalized_gpu_hours`
Different from `FitnessFunction` because it includes `information_gain`
(captures the "eliminated a branch" value of falsification) and `novelty`.

**WorkingResearchState:** The mutable, decision-optimized working memory. The
Director queries this, not A2L directly. A2L is Git (immutable);
WorkingResearchState is the IDE Index + Build Cache (mutable, queryable).

**ModelBeliefState:** The operational counterpart to `BeliefState`. Tracks
learned beliefs about model capabilities (planning, coding, extraction,
analysis scores), costs, latencies, and success rates. Updated via Bayesian
updating after each task completion.

**CapabilityReputationSystem:** Generalized version of `ModelBeliefState` that
tracks performance across all allocatable resources (LLMs, GPUs, robots,
microscopes, lab instruments). Uses UCB1 or Thompson Sampling to balance
exploration (trying new resources) with exploitation (using known-good
resources).

**ResourceDirector:** The operational counterpart to `ResearchDirector`. The
Research Director asks "What should we learn next?" The Resource Director asks
"How do we spend finite resources?" Uses multi-armed bandit algorithms to
allocate resources across experiments, maximizing EIG/cost ratio.

**OperationalKnowledge:** Facts about how to do science efficiently (model
capabilities, tool reliability, robot performance, facility throughput). The
second learnable knowledge system, alongside scientific knowledge. Both are
updated after each experiment via Bayesian updating.

**ScientificResource:** Any allocatable resource for scientific experiments.
All resources — LLMs, GPUs, HPC clusters, robots, microscopes, lab instruments
— are treated uniformly as resources with capabilities, costs, and
availabilities. The scientific layer shouldn't care whether an experiment runs
on a GPU cluster or a robot lab.

**KnowledgeObject:** A first-class scientific knowledge entity. Not a vector
store entry. Has `type` (paper, notebook, dataset, script, claim, observation),
`source` (URI), `summary` (auto-generated), `embeddings` (one access method),
`lineage` (provenance), `reputation` (confidence 0.0–1.0), and extracted
entities/methods/metrics/claims. Vector search is merely one access method
among many.

**KnowledgeManager:** A scientific knowledge curator (not a search engine).
Provides `ingest()` (papers, notebooks, scripts, datasets, URLs), `extract()`
(auto-extract entities, methods, metrics, claims), `index()` (vector, keyword,
graph), `summarize()`, `query()` (natural language), and `recommend()` (context-
aware). The UX must be dead simple: `umrs knowledge add papers/` — no metadata,
no tags, no ontology linking required.

**KnowledgeBelief:** Confidence in a KnowledgeObject (0.0–1.0). Based on
source type (notebook observation 0.4, peer-reviewed paper 0.9, experiment
reproduced 15x 0.99), evidence count, and reproduction count. Feeds directly
into `BeliefState` — beliefs are built from knowledge.

**SharedResearchKnowledgeBase:** The single source of truth for scientific
knowledge, shared between humans and AI. Knowledge imported by humans =
knowledge visible to AI. Knowledge discovered by AI = knowledge visible to
humans. Symmetry is critical — many agent systems build "AI memory" that humans
cannot inspect, which is a bad idea.

**KnowledgeAcquisitionLayer:** Sits upstream of the Research Director. Ingests
existing knowledge (papers, PDFs, notebooks, scripts, datasets, observations)
and makes it available to both humans and AI. Without this layer, every new
research program starts almost from zero. The full loop becomes: External
Knowledge → Knowledge Base → Belief State → Research Director → Experiments →
A2L → Knowledge Base (experiments generate new knowledge).

---

## Appendix: The Object Hierarchy

```
Research Program          ← top-level scientific object (the "why")
  ↓
Question                  ← a specific research question
  ↓
Hypothesis                ← a testable statement about the question
  ↓
Experiment                ← a procedure to test the hypothesis
  ↓
Result                    ← the outcome of the experiment
  ↓
Evidence                  ← interpretation of the result (supports/contradicts)
  ↓
Conclusion                ← the final verdict on the hypothesis
```

Each object in the hierarchy is an A2L artifact. The `derived_from` relationship
connects parent to child. This is the **immutable scientific ledger** — Git for
science.

The Director queries `WorkingResearchState` (mutable, decision-optimized) to
decide which experiment to run next. The experiment is executed by
`ExperimentEngine` which wraps `workflow_runner.run_workflow_record()`. The
result is scored by `ScientificReward` and recorded to A2L via `/derive`. The
`BeliefUpdater` updates the `BeliefState` based on the new evidence. The loop
repeats.

**This is the autonomous science loop.**
