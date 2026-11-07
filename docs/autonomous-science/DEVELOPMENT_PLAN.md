# Concrete Development Plan: AI4S Autonomous Science

> Historical record (dated); not maintained (banner added 2026-11-07). Current status: [R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md).

**Status:** Historical implementation plan, revised to match repository reality
**Date:** 2026-06-21
**Source:** `docs/autonomous-science/PLAN.md` (canonical architecture document)
**Objective:** turn the existing architecture into a runnable, bounded MVP that can execute one real autonomous science loop on an example video-analysis workflow, then expand only after the loop is measurable and stable.

---

## Table of Contents

1. [Current Reality](#1-current-reality)
2. [Dependency Graph](#2-dependency-graph)
3. [Execution Rules](#3-execution-rules)
4. [Phase 1: Real MVP Loop](#4-phase-1-real-mvp-loop)
5. [Phase 2: Belief-Driven Selection](#5-phase-2-belief-driven-selection)
6. [Phase 3: Provenance + Working State](#6-phase-3-provenance--working-state)
7. [Phase 4: Portfolio Director](#7-phase-4-portfolio-director)
8. [Validation Strategy](#8-validation-strategy)
9. [Risk Mitigation](#9-risk-mitigation)

---

## 1. Current Reality

This plan must start from what already exists in the repository.

### 1.1 Already Implemented

- `science/` contracts package exists: `ScientificReward`, `Experiment`, `ScienceDomain` (no domain plugin is bundled; the tests use an echo domain)
- `r4/src/r4/agent/belief_state.py` exists
- `r4/src/r4/agent/belief_updater.py` exists
- `r4/src/r4/agent/working_research_state.py` exists
- `r4/src/r4/agent/research_director.py` exists
- Week 1–3 unit/integration tests already exist and pass locally
- A2L artifact lineage infrastructure already exists; science-specific type helpers exist in `a2l/src/a2l/science_types.py` and `a2l/src/a2l/science_lineage.py`

### 1.2 Not Actually Done Yet

These are the real gaps that block a usable autonomous-science system:

- `ResearchOrchestrator` is not yet wired to `ScienceDomain`, `ScientificReward`, `BeliefUpdater`, and `WorkingResearchState` end-to-end
- `r4/src/r4/agent/research_program.py` was missing as a standalone module
- research CLI surfaces for program/portfolio operations are not implemented
- A2L science lineage is helper-level, not yet integrated into every loop iteration
- no stable week-4 integration benchmark existed for portfolio control
- no realistic week-2 simulation benchmark existed for EIG; a benchmark is required before claiming information-gain selection works better than naive selection

### 1.3 Scope Reduction Required

To make this plan executable, MVP scope is narrowed to:

- one domain: `ExampleDomain`
- one real execution surface: an existing example video-analysis workflow
- one primary metric: recall, with false-positive rate as a guardrail
- one research loop at a time for MVP
- bounded runs measured in minutes/hours before any multi-day unattended run is attempted

The following are explicitly deferred until Phase 4 or later:

- multi-program scheduling
- broad cross-domain generalization beyond the domain plugin boundary
- continuous 24-hour unattended runs as a merge gate
- broad CLI/TUI operational UX beyond minimal inspection commands

---

## 2. Dependency Graph

```
Phase 1: real MVP loop on the example workflow
           ↓
Phase 2: belief-driven selection with validated EIG benchmark
           ↓
Phase 3: A2L provenance + WorkingResearchState sync
           ↓
Phase 4: portfolio director + minimal program control
```

**Critical rule:** each phase must have a passing executable gate before moving to the next phase.

---

## 3. Execution Rules

### 3.1 Always Integrate Through Existing Owners

- experiment execution goes through UMRS workflow runner or existing experiment runner
- durable scientific provenance goes through A2L `/derive`
- orchestration decisions stay in `r4/`
- do not recreate workflow state machines in new modules

### 3.2 Every Phase Needs One Cheap Gate

No phase counts as done without one narrow executable check:

- Phase 1: end-to-end example-domain loop test with reward recorded
- Phase 2: EIG simulation benchmark and belief integration tests
- Phase 3: A2L lineage chain query plus `WorkingResearchState` sync check
- Phase 4: multi-question director integration test

### 3.3 Runtime Validation Order

After each Python edit slice, run at minimum:

```bash
python tools/import_boundary_validator.py
python tools/contracts_fence.py
python tools/stale_name_validator.py
bash scripts/ci-lint-local.sh --files <edited files>
```

For focused development on this plan, the working test subsets are:

```bash
.venv/bin/python -m pytest science/tests -q
.venv/bin/python -m pytest r4/tests/test_belief_updater.py r4/tests/test_integration_week2.py -q
.venv/bin/python -m pytest r4/tests/test_working_research_state.py r4/tests/test_integration_week3.py -q
.venv/bin/python -m pytest r4/tests/test_research_director.py r4/tests/test_integration_week4.py -q
```

---

## 4. Phase 1: Real MVP Loop

### 4.1 Objective

Run one complete example-domain autonomous-science loop against the real workflow surface:

`goal -> hypothesis -> experiment -> results -> reward -> next hypothesis`

This phase is about **real execution**, not more abstractions.

### 4.2 Current State

Most contracts for this phase already exist. Remaining work is integration:

- wire `ExampleDomain` into the orchestration path
- persist `reward_score` per experiment
- accumulate `cumulative_reward` per loop
- replace confidence-only stop/publish decisions with reward-aware decisions where local control flow allows it

### 4.3 Required Deliverables

- `ExperimentRecord.reward_score` persisted in loop state
- `ResearchLoopState.cumulative_reward` persisted in loop state
- real reward generation from example workflow outputs
- one integration test that proves reward changes as experiment quality changes
- one smoke path that runs without inventing new infrastructure

### 4.4 Explicit Non-Goals

- no new domain beyond the example domain
- no multi-program scheduling
- no generalized planner UX
- no attempt to solve reproducibility beyond placeholder bookkeeping

### 4.5 Acceptance Gate

Phase 1 is done only if all are true:

- an example-domain experiment produces a non-empty result payload
- `ScientificReward.total` is attached to the experiment/loop state
- reward changes in the expected direction when recall improves or cost worsens
- the next loop decision can access that reward without parsing free-form text

---

## 5. Phase 2: Belief-Driven Selection

### 5.1 Objective

Make experiment selection uncertainty-aware instead of confidence-greedy.

### 5.2 Practical Requirement

This phase must be justified by benchmark, not argument. The EIG implementation is only acceptable if a reproducible simulation shows it beats naive baselines on a scientifically relevant task:

- identify the true hypothesis
- suppress competing alternatives
- reduce experiments needed to converge

### 5.3 Deliverables

- stable `BeliefState` serialization and update path
- `BeliefUpdater.expected_information_gain()` validated against simulation
- EIG simulation benchmark that compares `random`, `greedy`, and `eig`
- orchestrator slice can ask for "next hypothesis to test" from a belief-aware selector

### 5.4 Acceptance Gate

- EIG benchmark passes in CI
- EIG reduces experiments needed by at least 30% vs random and greedy on the benchmark defined in tests
- integration test proves uncertain hypotheses are chosen over already-supported or already-refuted ones

---

## 6. Phase 3: Provenance + Working State

### 6.1 Objective

Turn A2L from optional recording into the authoritative immutable journal, while keeping `WorkingResearchState` as the fast mutable control surface.

### 6.2 Required Deliverables

- each loop iteration derives science artifacts with the correct parent-child chain
- result artifacts include reward metadata
- evidence artifacts include support/strength metadata
- `WorkingResearchState` can rebuild or refresh from A2L-derived records

### 6.3 Acceptance Gate

- full lineage for one loop is queryable from question to result/evidence
- `WorkingResearchState` can answer current-belief queries without rescanning raw lineage every time
- additive schema changes only; no destructive A2L migration

---

## 7. Phase 4: Portfolio Director

### 7.1 Objective

Add minimal portfolio control only after one-loop execution, belief updates, and provenance are already real.

### 7.2 Minimum Useful Scope

- standalone `ResearchProgram` contract
- `ResearchDirector` can track multiple questions
- budget allocation and stop/rebalance logic are tested
- minimal CLI inspection surface is acceptable; full operational UX is not required for the first merge

### 7.3 Acceptance Gate

- director integration test manages 3+ concurrent questions
- underperforming directions are stopped deterministically
- uncertain hypotheses are preferred when selecting next experiments
- program budget consumption is visible and serializable

---

## 8. Validation Strategy

### 8.1 Test Pyramid

Keep the validation hierarchy strict:

- unit: reward math, belief updates, budget logic
- integration: example-domain loop slices, A2L lineage, director rebalancing
- benchmark: EIG simulation
- smoke: one real workflow-backed autonomous loop

### 8.2 Definition of Done for This Plan Revision

This plan becomes "real and practical" when:

- speculative future architecture is separated from immediately executable work
- each phase has one owner, one gate, and one rollback point
- already-implemented modules are treated as current assets, not future tasks
- no acceptance criterion depends on hand-wavy ideas like "works without problems"

### 8.3 Deferred System-Level Goal

The 24-hour unattended run remains a milestone, but **not** an initial merge gate. It becomes a later systems validation target after Phases 1–4 are stable in shorter bounded runs.

---

## 9. Risk Mitigation

#### `science/__init__.py`
```python
"""Shared contracts layer for autonomous science.

Pattern: r4-contracts/ (stdlib-only, no external dependencies)
"""
```

#### `science/reward.py`
- `ScientificReward` dataclass with weighted components
- `performance: float` = metric_delta (e.g., recall_new - recall_old)
- `novelty: float` = distance_from_previous_experiments (L2 in parameter space)
- `information_gain: float` = belief_entropy_before - belief_entropy_after (0.0 for Week 1)
- `reproducibility: float` = 1.0 if result_replicated else 0.0
- `cost: float` = normalized_gpu_hours (gpu_hours / max_gpu_hours)
- `total` property: weighted sum of all components
- Weights: performance=0.5, novelty=0.1, information_gain=0.2, reproducibility=0.1, cost=0.1
- `from_experiment()` classmethod: compute reward from experiment results

#### `science/experiment.py`
- `Experiment` dataclass: id, hypothesis_id, protocol, workflow_ref, status, results, reward_score
- `ExperimentStatus` enum: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED
- `ExperimentResults` dataclass: metrics, artifacts, logs, duration_seconds
- `Experiment.execute()` method: wraps `workflow_runner.run_workflow_record()`

#### `science/domain.py`
- `ScienceDomain` Protocol with methods:
  - `formulate_hypothesis(question, context) -> Hypothesis`
  - `design_experiment(hypothesis, constraints) -> Experiment`
  - `evaluate_results(experiment, results) -> Evidence`
  - `compute_reward(experiment, evidence) -> ScientificReward`
- `ResearchContext` dataclass: question_id, program_id, prior_results, budget_remaining
- `Constraints` dataclass: max_duration, max_cost, max_iterations

#### `science/domains/__init__.py`
- Empty init for domains package

#### `science/domains/example.py`
- `ExampleDomain` implementing `ScienceDomain` Protocol
- `formulate_hypothesis()`: generate hypothesis about video motion detection parameters
  - e.g., "distance_threshold=0.05 maximizes recall"
  - e.g., "time_threshold=30s reduces false positives"
- `design_experiment()`: create experiment protocol with specific parameter values
  - e.g., `{"distance_threshold": 0.05, "time_threshold": 30}`
- `evaluate_results()`: parse motion detection results into evidence
  - e.g., `Evidence(supports=True, strength=0.8, summary="recall=0.82 at threshold=0.05")`
- `compute_reward()`: compute multi-class reward from experiment results
  - performance = recall_new - recall_old
  - novelty = L2 distance in parameter space from all prior experiments
  - information_gain = 0.0 (placeholder for Week 1)
  - reproducibility = 1.0 if result replicated else 0.0
  - cost = normalized_gpu_hours

### 2.3 Files to Modify

#### `r4/src/r4/agent/research_orchestrator.py`
- `DECIDING` phase: call `ScientificReward.score()` instead of pure confidence threshold
- Add `reward_score: float` to `ExperimentRecord`
- Add `cumulative_reward: float` to `ResearchLoopState`

#### `r4/src/r4/research_loop/state.py`
- `ExperimentRecord`: add `reward_score: float` field
- `ResearchLoopState`: add `cumulative_reward: float` field

### 2.4 Test Plan

#### Unit Tests (`science/tests/test_reward.py`)
- [ ] `ScientificReward.total` computes correct weighted sum
- [ ] `ScientificReward.from_experiment()` extracts correct components
- [ ] `performance` is computed correctly (metric_delta)
- [ ] `novelty` is computed correctly (L2 distance)
- [ ] `cost` is computed correctly (normalized_gpu_hours)
- [ ] Edge cases: all zeros, all max values, negative performance

#### Unit Tests (`science/tests/test_experiment.py`)
- [ ] `Experiment.execute()` calls `workflow_runner.run_workflow_record()`
- [ ] `ExperimentResults` correctly captures metrics, artifacts, logs
- [ ] `ExperimentStatus` transitions are valid (PENDING → RUNNING → COMPLETED/FAILED)
- [ ] `Experiment.execute()` propagates workflow errors correctly

#### Unit Tests (`science/tests/test_domain.py`)
- [ ] `ExampleDomain.formulate_hypothesis()` returns valid hypothesis
- [ ] `ExampleDomain.design_experiment()` returns valid experiment protocol
- [ ] `ExampleDomain.evaluate_results()` returns valid evidence
- [ ] `ExampleDomain.compute_reward()` returns valid `ScientificReward`
- [ ] `ScienceDomain` Protocol is satisfied by `ExampleDomain`

#### Integration Tests (`science/tests/test_integration_week1.py`)
- [ ] End-to-end: formulate hypothesis → design experiment → execute → evaluate → compute reward
- [ ] Reward is non-negative for successful experiments
- [ ] Reward is negative for failed experiments (high cost, no performance gain)
- [ ] Cumulative reward increases over multiple successful experiments

### 2.5 Acceptance Criteria

- [ ] `ScientificReward` dataclass created with concrete MVP approximations
- [ ] `Experiment` dataclass created with `execute()` method
- [ ] `ScienceDomain` Protocol created
- [ ] `ExampleDomain` created as first domain plugin
- [ ] `ExperimentRecord` modified to include `reward_score: float`
- [ ] `ResearchLoopState` modified to include `cumulative_reward: float`
- [ ] `DECIDING` phase calls `ScientificReward.score()` instead of pure confidence threshold
- [ ] All unit tests pass
- [ ] All integration tests pass

### 2.6 Risk Mitigation

**Risk:** `Experiment.execute()` wrapper introduces bugs in the workflow execution path.

**Mitigation:** 
1. Start with a thin wrapper that just calls `workflow_runner.run_workflow_record()` directly
2. Add reward computation as a separate step after execution
3. Test with an existing example analysis workflow before adding new workflows

**Risk:** `ScientificReward` components are not measurable.

**Mitigation:**
1. Start with simple, concrete definitions:
   - `performance = recall_new - recall_old`
   - `novelty = L2_distance(current_params, previous_params)`
   - `information_gain = 0.0` (placeholder for Week 1)
   - `reproducibility = 1.0 if result_replicated else 0.0`
   - `cost = gpu_hours / max_gpu_hours`
2. Make all components configurable via weights

---

## 3. Week 2: BeliefState + BeliefUpdater (EIG)

### 3.1 Objective

The `HypothesisManager` evolves into a `BeliefState` that tracks uncertainty across all hypotheses, and `BeliefUpdater` updates beliefs based on new evidence using Bayesian updating. The Director asks "Which experiment most reduces uncertainty?" (maximizes Expected Information Gain) rather than "Which experiment gets the highest reward?" (fitness function).

### 3.2 Files to Create

#### `r4/src/r4/agent/belief_state.py`
- `BeliefState` dataclass:
  - `hypotheses: dict[str, HypothesisBelief]` — HypothesisID → HypothesisBelief
  - `entropy` property: Shannon entropy of the belief distribution
  - `most_likely_hypothesis` property: (hypothesis_id, confidence) of the most likely hypothesis
- `HypothesisBelief` dataclass:
  - `hypothesis_id: str`
  - `confidence: float` — 0.0–1.0
  - `evidence_for: list[str]` — Evidence IDs
  - `evidence_against: list[str]` — Evidence IDs
  - `last_updated: datetime`

#### `r4/src/r4/agent/belief_updater.py`
- `BeliefUpdater` class:
  - `update(beliefs, evidence) -> BeliefState`: Bayesian update P(H|E) = P(E|H) * P(H) / P(E)
  - `expected_information_gain(beliefs, experiment) -> float`: EIG(e) = H(beliefs) - E[H(beliefs | outcome of e)]
  - `posterior(beliefs, evidence) -> float`: Compute posterior probability after observing evidence

### 3.3 Files to Modify

#### `r4/src/r4/agent/hypothesis.py`
- `HypothesisManager` evolves to use `BeliefState` internally (instead of bare `confidence: float`)
- `evaluate()` method: update `BeliefState` based on new evidence
- `revise()` method: create new hypothesis in `BeliefState`

#### `r4/src/r4/agent/research_orchestrator.py`
- `DECIDING` phase: use `BeliefState.entropy` and `BeliefUpdater.expected_information_gain()`
- `EVALUATING` phase: use `BeliefUpdater.update()` to update beliefs based on new evidence

#### `science/reward.py`
- `information_gain` component: compute from `BeliefState` (was 0.0 placeholder in Week 1)
- `information_gain = belief_entropy_before - belief_entropy_after`

### 3.4 Test Plan

#### Unit Tests (`r4/tests/test_belief_state.py`)
- [ ] `BeliefState.entropy` computes correct Shannon entropy
- [ ] `BeliefState.most_likely_hypothesis` returns correct (hypothesis_id, confidence)
- [ ] `BeliefState` correctly tracks confidence and evidence for all hypotheses
- [ ] Edge cases: single hypothesis, all hypotheses equally likely, one hypothesis with 100% confidence

#### Unit Tests (`r4/tests/test_belief_updater.py`)
- [ ] `BeliefUpdater.update()` correctly updates beliefs based on new evidence
- [ ] `BeliefUpdater.expected_information_gain()` correctly computes EIG
- [ ] `BeliefUpdater.posterior()` correctly computes posterior probability
- [ ] Edge cases: no evidence, contradictory evidence, supporting evidence

#### Integration Tests (`r4/tests/test_integration_week2.py`)
- [ ] End-to-end: formulate hypothesis → execute experiment → update beliefs → compute EIG
- [ ] EIG is higher for experiments that more reduce uncertainty
- [ ] Beliefs converge to correct values as more evidence is accumulated
- [ ] `DECIDING` phase uses EIG to choose experiments that maximize uncertainty reduction

#### Simulation Tests (`r4/tests/test_simulation_week2.py`)
- [ ] Simulated hypothesis-testing benchmark: EIG-based selection reduces the number of experiments needed to reach a confidence threshold by ≥30% vs. random selection
- [ ] Simulated hypothesis-testing benchmark: EIG-based selection reduces the number of experiments needed to reach a confidence threshold by ≥30% vs. greedy (highest-confidence) selection

### 3.5 Acceptance Criteria

- [ ] `BeliefState` dataclass created with `entropy` and `most_likely_hypothesis` properties
- [ ] `HypothesisBelief` dataclass created
- [ ] `BeliefUpdater` class created with `update()` and `expected_information_gain()` methods
- [ ] `HypothesisManager` evolved to use `BeliefState` internally
- [ ] `DECIDING` phase uses EIG for experiment selection
- [ ] `EVALUATING` phase uses `BeliefUpdater.update()` to update beliefs
- [ ] `information_gain` component in `ScientificReward` computes from `BeliefState`
- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] Simulation tests confirm ≥30% reduction in experiments needed

### 3.6 Risk Mitigation

**Risk:** Bayesian updating is numerically unstable.

**Mitigation:**
1. Use log-space computation for posterior probabilities
2. Add numerical stability checks (e.g., clamp confidence to [0.01, 0.99] to avoid log(0))
3. Test with extreme cases (all evidence supports, all evidence contradicts)

**Risk:** EIG computation is too expensive for real-time use.

**Mitigation:**
1. Start with a simple approximation: EIG ≈ H(beliefs) - H(beliefs | outcome)
2. Use Monte Carlo sampling for the expectation if analytical computation is too expensive
3. Cache EIG computations for repeated experiments

---

## 4. Week 3: A2L Scientific Ledger + WorkingResearchState

### 4.1 Objective

Use A2L's existing artifact/lineage infrastructure as the Scientific Journal backend. A2L is immutable provenance (Git for science) — not working memory. Build `WorkingResearchState` as the mutable, decision-optimized layer that persists `BeliefState` (from Week 2) and that the Director queries.

### 4.2 Files to Create

#### `a2l/src/a2l/science_types.py`
- Science-specific artifact type definitions:
  - `type='research_program'`
  - `type='question'`
  - `type='hypothesis'`
  - `type='experiment'`
  - `type='result'`
  - `type='evidence'`
  - `type='conclusion'`
- Type-specific schema validation (e.g., hypothesis must have `statement` and `confidence` in metadata)

#### `a2l/src/a2l/science_lineage.py`
- Science-specific lineage rules:
  - Hypothesis must be derived_from Question
  - Experiment must be derived_from Hypothesis
  - Result must be derived_from Experiment
  - Evidence must be derived_from Result
  - Conclusion must be derived_from multiple Results/Evidence
- `validate_lineage(parent_type, child_type) -> bool`: Check if lineage is valid
- `SCIENCE_LINEAGE_RULES`: Dict mapping parent_type → allowed child_types

#### `r4/src/r4/agent/working_research_state.py`
- `WorkingResearchState` dataclass:
  - `active_questions: list[Question]` — Currently open questions
  - `active_hypotheses: dict[str, Hypothesis]` — HypothesisID → Hypothesis
  - `current_beliefs: BeliefState` — Persisted from Week 2
  - `reward_history: list[RewardEntry]` — Historical rewards
  - `budget_state: BudgetState` — Current budget consumption
  - `portfolio_state: PortfolioState` — Cross-question portfolio state
- `sync_from_a2l()`: Pull latest state from A2L immutable provenance
- `query_beliefs() -> BeliefState`: O(1) query for current beliefs

### 4.3 Files to Modify

#### `r4/src/r4/agent/research_orchestrator.py`
- Every iteration creates A2L artifacts via `/derive`:
  - Question → Hypothesis (derive)
  - Hypothesis → Experiment (derive)
  - Experiment → Result (derive, with reward in metadata)
  - Result → Evidence (derive, with supports/strength in metadata)
- `WorkingResearchState` is updated after each iteration

#### `a2l/src/a2l/models.py`
- Add `reward: float` column to `a2l_artifacts` (additive migration)
- Add `decision: str` column to `a2l_artifacts` (additive migration)

#### `a2l/src/a2l/schema.py`
- Add `reward` and `decision` columns to `a2l_artifacts` table
- Add science-specific indexes (e.g., `idx_a2l_artifacts_reward` for `metadata.reward_gt` queries)

### 4.4 Test Plan

#### Unit Tests (`a2l/tests/test_science_types.py`)
- [ ] All science-specific artifact types are valid
- [ ] Type-specific schema validation works correctly
- [ ] Invalid artifact types are rejected

#### Unit Tests (`a2l/tests/test_science_lineage.py`)
- [ ] All science-specific lineage rules are valid
- [ ] `validate_lineage()` correctly validates parent → child relationships
- [ ] Invalid lineage (e.g., Question → Result) is rejected

#### Unit Tests (`r4/tests/test_working_research_state.py`)
- [ ] `WorkingResearchState` correctly persists `BeliefState`
- [ ] `sync_from_a2l()` correctly pulls latest state from A2L
- [ ] `query_beliefs()` returns correct `BeliefState` in O(1)
- [ ] `reward_history` correctly tracks historical rewards

#### Integration Tests (`r4/tests/test_integration_week3.py`)
- [ ] End-to-end: create A2L artifacts via `/derive` → query lineage → verify full chain
- [ ] After a research loop completes, the entire scientific history is queryable via A2L:
  - `GET /a2l/artifacts?type=result&metadata.reward_gt=0.5` — high-reward results
  - `GET /a2l/artifacts/{id}/lineage` — full derivation chain from Question to Result
- [ ] A hypothesis created in research loop A is visible in research loop B (cross-loop queryability via A2L)
- [ ] `WorkingResearchState` is queryable in O(1) for "what are the current beliefs about all hypotheses?"

### 4.5 Acceptance Criteria

- [ ] `science_types.py` created with science-specific artifact types
- [ ] `science_lineage.py` created with science-specific lineage rules
- [ ] `working_research_state.py` created and persists `BeliefState`
- [ ] `research_orchestrator.py` modified to create A2L artifacts via `/derive`
- [ ] `a2l/models.py` modified with `reward` and `decision` columns
- [ ] `a2l/schema.py` modified with `reward` and `decision` columns
- [ ] All unit tests pass
- [ ] All integration tests pass

### 4.6 Risk Mitigation

**Risk:** A2L schema migration breaks existing artifacts.

**Mitigation:**
1. Use additive migration only (add columns, don't modify existing ones)
2. Set default values for new columns (e.g., `reward = 0.0`, `decision = NULL`)
3. Test migration on a copy of the production database before deploying

**Risk:** `WorkingResearchState` becomes inconsistent with A2L.

**Mitigation:**
1. Use one-way sync: A2L → `WorkingResearchState` (never the reverse)
2. Add consistency checks: `WorkingResearchState` should be a subset of A2L
3. Add a `last_synced_at` timestamp to detect stale state

---

## 5. Week 4: ResearchDirector + ResearchProgram

### 5.1 Objective

Create the AI CEO layer that manages a portfolio of open questions under shared programs. The Director uses `BeliefState` (Week 2) and `WorkingResearchState` (Week 3) to allocate budget, prioritize branches, and stop bad directions.

### 5.2 Files to Create

#### `r4/src/r4/agent/research_director.py`
- `ResearchDirector` class:
  - `allocate_budget(portfolio) -> BudgetAllocation`: UCB1-style regret minimization
  - `prioritize(questions) -> list[Question]`: Rank by expected scientific value
  - `stop_direction(question_id) -> bool`: Kill underperforming directions
  - `spawn_loop(question) -> ResearchOrchestrator`: Create new orchestrator for a question
  - `choose_next_experiment(belief_state) -> Experiment`: Use EIG to select experiment
  - `rebalance() -> None`: Periodic reallocation based on reward trajectories
- `Portfolio` dataclass: List of open questions, budget allocation, reward trajectories
- `BudgetAllocation` dataclass: Per-question budget allocation

#### `r4/src/r4/agent/research_program.py`
- `ResearchProgram` dataclass:
  - `id: str` — rp_2026_001
  - `name: str` — "Autonomous Video Monitoring"
  - `goals: list[str]` — ["Maximize object detection accuracy"]
  - `questions: list[str]` — IDs of open research questions
  - `budget: ProgramBudget` — GPU-hours, iterations, cost
  - `metrics: list[Metric]` — ["recall", "false_positive_rate"]
  - `status: ProgramStatus` — ACTIVE | PAUSED | COMPLETED

### 5.3 Files to Modify

#### `r4/src/r4/cli.py`
- New commands:
  - `r4 research program` — show active programs, budgets, reward trajectories
  - `r4 research spawn "question"` — launch new research loop under director
  - `r4 research stop <question_id>` — stop a direction

#### `r4/src/r4/agent/research_orchestrator.py`
- `ResearchOrchestrator` reports reward trajectory to `ResearchDirector` for budget reallocation
- `ResearchOrchestrator` checks with `ResearchDirector` before starting a new iteration

### 5.4 Test Plan

#### Unit Tests (`r4/tests/test_research_director.py`)
- [ ] `allocate_budget()` correctly distributes compute across open questions
- [ ] `prioritize()` correctly ranks open questions by expected scientific value
- [ ] `stop_direction()` correctly identifies and stops underperforming directions
- [ ] `spawn_loop()` correctly creates a new `ResearchOrchestrator` for a question
- [ ] `choose_next_experiment()` correctly uses EIG to select the experiment that most reduces uncertainty
- [ ] `rebalance()` correctly reallocates budget based on reward trajectories

#### Unit Tests (`r4/tests/test_research_program.py`)
- [ ] `ResearchProgram` dataclass correctly initializes all fields
- [ ] `ProgramBudget` correctly tracks GPU-hours, iterations, cost
- [ ] `ProgramStatus` transitions are valid (ACTIVE → PAUSED → COMPLETED)

#### Integration Tests (`r4/tests/test_integration_week4.py`)
- [ ] End-to-end: create research program → spawn research loops → allocate budget → stop bad directions → rebalance
- [ ] Director manages 3+ concurrent research questions under 1+ research programs
- [ ] Director reallocates budget from stagnant directions to promising ones
- [ ] Director stops directions whose cumulative reward falls below a dynamic threshold
- [ ] Director's decisions are informed by `WorkingResearchState` which is derived from A2L provenance + live experiment state
- [ ] Director's `choose_next_experiment()` uses EIG from `BeliefState`

### 5.5 Acceptance Criteria

- [ ] `ResearchDirector` created with `allocate_budget()`, `prioritize()`, `stop_direction()`, `choose_next_experiment()`
- [ ] `ResearchProgram` dataclass created
- [ ] `cli.py` modified with new `r4 research` commands
- [ ] `research_orchestrator.py` modified to report reward trajectory to `ResearchDirector`
- [ ] Director manages 3+ concurrent research questions under 1+ research programs
- [ ] Director reallocates budget from stagnant directions to promising ones
- [ ] Director stops directions whose cumulative reward falls below a dynamic threshold
- [ ] Director's decisions are informed by `WorkingResearchState` which is derived from A2L provenance + live experiment state
- [ ] Director's `choose_next_experiment()` uses EIG from `BeliefState`
- [ ] All unit tests pass
- [ ] All integration tests pass

### 5.6 Risk Mitigation

**Risk:** Director's budget allocation is too aggressive, starving promising directions.

**Mitigation:**
1. Use UCB1-style allocation which balances exploration and exploitation
2. Set minimum budget allocation per question (e.g., 10% of total budget)
3. Add a "cooldown" period after stopping a direction before reallocating its budget

**Risk:** Director's `stop_direction()` is too aggressive, stopping directions prematurely.

**Mitigation:**
1. Use a dynamic threshold based on portfolio median reward
2. Require N consecutive iterations below threshold before stopping
3. Add a "grace period" for new directions before evaluating their performance

---

## 6. Test Strategy

### 6.1 Test Pyramid

```
                    /\
                   /  \
                  /    \
                 / E2E  \        ← End-to-end autonomous loop tests
                /--------\
               / Integration \   ← Cross-component integration tests
              /--------------\
             /    Unit Tests   \  ← Per-component unit tests
            /--------------------\
```

### 6.2 Test Categories

#### Unit Tests
- **Purpose:** Test individual components in isolation
- **Location:** `science/tests/`, `r4/tests/`, `a2l/tests/`
- **Framework:** pytest
- **Coverage target:** ≥90% for new code

#### Integration Tests
- **Purpose:** Test interactions between components
- **Location:** `science/tests/test_integration_*.py`, `r4/tests/test_integration_*.py`
- **Framework:** pytest
- **Key scenarios:**
  - Formulate hypothesis → design experiment → execute → evaluate → compute reward
  - Update beliefs → compute EIG → choose next experiment
  - Create A2L artifacts → query lineage → verify full chain
  - Create research program → spawn research loops → allocate budget

#### End-to-End (E2E) Tests
- **Purpose:** Test the full autonomous science loop
- **Location:** `science/tests/test_e2e_*.py`
- **Framework:** pytest
- **Key scenarios:**
  - Full autonomous loop: Goal → Hypothesis → Experiment → Reward → A2L → Next Hypothesis
  - Multi-question portfolio management
  - 24-hour autonomous run without human intervention
  - ADR improvement over time

### 6.3 Test Data

#### Synthetic Test Data
- Generate synthetic motion detection results with known ground truth
- Create synthetic hypothesis sets with known confidence values
- Generate synthetic experiment histories with known reward trajectories

#### Real Test Data
- Use an existing example video-analysis workflow
- Use example test videos (`<example-data-dir>/`)

### 6.4 Continuous Integration

- All tests must pass before merging to main
- CI pipeline:
  1. Lint (ruff)
  2. Unit tests
  3. Integration tests
  4. E2E tests (only on release branches)
- Use `scripts/ci-lint-local.sh` before every commit (per `.github/instructions/ci-harness.instructions.md`)

---

## 7. Risk Mitigation

### 7.1 General Risks

#### Risk: Scope Creep
**Mitigation:** Stick to the 4-week roadmap. Do not add features not in the plan. If a new feature is needed, add it to Month 2+.

#### Risk: Premature Optimization
**Mitigation:** Start with simple, correct implementations. Optimize only when profiling shows a bottleneck.

#### Risk: Breaking Existing Functionality
**Mitigation:** Use additive changes wherever possible. Run the full test suite before and after each change. Use feature flags to enable/disable new functionality.

### 7.2 Implementation Order

**Critical rule:** Each week must be fully tested and passing before starting the next week.

```
Week 1: ScientificReward + Experiment + ScienceDomain
  → Test: All unit and integration tests pass
  → Verify: Reward is computed correctly for the example analysis
           ↓
Week 2: BeliefState + BeliefUpdater (EIG)
  → Test: All unit and integration tests pass
  → Verify: EIG-based selection reduces experiments needed by ≥30%
           ↓
Week 3: A2L Scientific Ledger + WorkingResearchState
  → Test: All unit and integration tests pass
  → Verify: A2L lineage is queryable and complete
           ↓
Week 4: ResearchDirector + ResearchProgram
  → Test: All unit and integration tests pass
  → Verify: Director manages 3+ concurrent research questions
```

### 7.3 Rollback Plan

If a week's implementation introduces critical bugs:

1. **Revert the changes** using `git revert`
2. **Re-run the previous week's tests** to confirm they still pass
3. **Fix the bugs** in a new branch
4. **Re-run all tests** before merging

### 7.4 Monitoring and Observability

- Log all reward computations, belief updates, and EIG calculations
- Log all A2L artifact creations and lineage queries
- Log all Director decisions (budget allocation, prioritization, stopping)
- Monitor ADR trajectory over time
- Alert if ADR decreases for more than 3 consecutive iterations

---

## Appendix: File Summary

### Files to Create

| File | Week | Purpose |
|------|------|---------|
| `science/__init__.py` | 1 | Shared contracts layer |
| `science/reward.py` | 1 | `ScientificReward` with weighted components |
| `science/experiment.py` | 1 | `Experiment` dataclass with `execute()` method |
| `science/domain.py` | 1 | `ScienceDomain` Protocol + `ResearchContext`, `Constraints` |
| `science/domains/__init__.py` | 1 | Domains package init |
| `science/domains/example.py` | 1 | `ExampleDomain` (first domain plugin) |
| `r4/src/r4/agent/belief_state.py` | 2 | `BeliefState`, `HypothesisBelief` |
| `r4/src/r4/agent/belief_updater.py` | 2 | `BeliefUpdater` with Bayesian updating and EIG |
| `a2l/src/a2l/science_types.py` | 3 | Science-specific A2L artifact types |
| `a2l/src/a2l/science_lineage.py` | 3 | Science-specific lineage rules |
| `r4/src/r4/agent/working_research_state.py` | 3 | `WorkingResearchState` |
| `r4/src/r4/agent/research_director.py` | 4 | `ResearchDirector` (AI CEO) |
| `r4/src/r4/agent/research_program.py` | 4 | `ResearchProgram` dataclass |

### Files to Modify

| File | Week | Changes |
|------|------|---------|
| `r4/src/r4/agent/research_orchestrator.py` | 1-4 | `DECIDING` phase, `EVALUATING` phase, A2L artifact creation, reward trajectory reporting |
| `r4/src/r4/research_loop/state.py` | 1 | `ExperimentRecord` gains `reward_score: float`; `ResearchLoopState` gains `cumulative_reward: float` |
| `r4/src/r4/agent/hypothesis.py` | 2 | `HypothesisManager` evolves to use `BeliefState` internally |
| `science/reward.py` | 2 | `information_gain` component now computes from `BeliefState` |
| `a2l/src/a2l/models.py` | 3 | Add `reward` and `decision` columns to `a2l_artifacts` |
| `a2l/src/a2l/schema.py` | 3 | Add `reward` and `decision` columns to `a2l_artifacts` table |
| `r4/src/r4/cli.py` | 4 | New commands: `r4 research program`, `r4 research spawn`, `r4 research stop` |

### Test Files to Create

| File | Week | Purpose |
|------|------|---------|
| `science/tests/__init__.py` | 1 | Tests package init |
| `science/tests/test_reward.py` | 1 | Unit tests for `ScientificReward` |
| `science/tests/test_experiment.py` | 1 | Unit tests for `Experiment` |
| `science/tests/test_domain.py` | 1 | Unit tests for `ScienceDomain` and `ExampleDomain` |
| `science/tests/test_integration_week1.py` | 1 | Integration tests for Week 1 |
| `r4/tests/test_belief_state.py` | 2 | Unit tests for `BeliefState` |
| `r4/tests/test_belief_updater.py` | 2 | Unit tests for `BeliefUpdater` |
| `r4/tests/test_integration_week2.py` | 2 | Integration tests for Week 2 |
| `r4/tests/test_simulation_week2.py` | 2 | Simulation tests for EIG-based selection |
| `a2l/tests/test_science_types.py` | 3 | Unit tests for science-specific A2L artifact types |
| `a2l/tests/test_science_lineage.py` | 3 | Unit tests for science-specific lineage rules |
| `r4/tests/test_working_research_state.py` | 3 | Unit tests for `WorkingResearchState` |
| `r4/tests/test_integration_week3.py` | 3 | Integration tests for Week 3 |
| `r4/tests/test_research_director.py` | 4 | Unit tests for `ResearchDirector` |
| `r4/tests/test_research_program.py` | 4 | Unit tests for `ResearchProgram` |
| `r4/tests/test_integration_week4.py` | 4 | Integration tests for Week 4 |
| `science/tests/test_e2e_autonomous_loop.py` | 4 | End-to-end test for full autonomous loop |
