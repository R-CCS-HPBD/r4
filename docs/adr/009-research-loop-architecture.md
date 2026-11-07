# ADR-009: Research Loop Architecture

**Status:** Superseded (2026-09-22, by ADR-036 + ADR-064: one server-hosted engine + the ResearchRuntime SPI; D-32) — status line aligned with the index 2026-10-19 (cleanup C-4; the supersession itself was recorded 2026-09-22 — rollout block appended below).

> **Rollout (dated 2026-10-19, cleanup C-4):** the research loop the way this ADR frames it (agent-side orchestrator composition) was superseded the day the design reset landed: one server-hosted engine/driver of record plus the client-facing `ResearchRuntime` SPI (ADR-036 loop hosting, ADR-064 one loop driver, gap row D-32). The header's "Proposed" had lingered since; no body text changes are made by this note.
> **Date:** 2026-06-16  
> **Supersedes:** None  
> **Superseded by:** None  

---

## Context

R4 was designed to automate scientific research. Three independent subsystems
emerged during development:

1. **ResearchOrchestrator** (`r4/src/r4/agent/research_orchestrator.py`) —
   single-agent closed loop: plan → execute → record → observe → replan. Has
   HypothesisManager, ExperimentRunner, DeepResearchLoop, A2L provenance.

2. **TeamupSession** (`r4/src/r4/teamup/`) — multi-agent collaboration
   framework with PM/SE/PG/CD roles. File-based inter-agent communication. HPC
   code optimization focus.

3. **UMRS WorkflowPipeline** (`umrs/src/umrs/pipeline_sdk/`) — durable DAG
   execution with S3/PGVector/BentoML connectors. Step-level with capability
   routing. User-defined via Pipeline ABC or declarative recipes.

**These systems don't bridge.** ResearchOrchestrator doesn't use teamup. Teamup
doesn't trigger UMRS pipelines. Pipelines don't carry hypotheses.

---

## Decision

Build a **Research Loop** as a fourth layer that composes the three existing
subsystems:

```
┌─────────────────────────────────────────────────────────┐
│                  RESEARCH LOOP                           │
│  (new — composes teamup + orchestrator + pipelines)     │
│                                                         │
│  Hypothesis → Design → Execute → Analyze → Refine       │
│       │          │         │          │          │       │
│       ▼          ▼         ▼          ▼          ▼       │
│  Hypothesis   Experiment  UMRS      SE agent   PM agent  │
│  Manager      Runner      Pipeline  analyzes   decides   │
│  (existing)   (existing)  (existing)(teamup)   (teamup)  │
│                                                         │
│  ┌─────────┐  ┌──────────┐  ┌──────────────────────┐   │
│  │ Teamup  │  │ Research │  │ UMRS WorkflowPipeline │   │
│  │ Session │  │Orchestr. │  │ + Recipes + Skills    │   │
│  └─────────┘  └──────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Layer Responsibilities

| Layer | Owns | Consumed By |
|-------|------|-------------|
| **Research Loop** (new) | Loop lifecycle, multi-agent roles for science, hypothesis→experiment→evidence cycle, session state | r4 CLI, r4 agent |
| **ResearchOrchestrator** (existing) | Single-agent iteration, A2L provenance, deep research, experiment submission | Research Loop |
| **TeamupSession** (existing) | Agent coordination, inter-agent communication, compute budget | Research Loop |
| **WorkflowPipeline** (existing) | DAG execution, S3 I/O, embedding, LLM steps, capability routing | ExperimentRunner, teamup agents |

---

## Architecture

### ResearchTeam Roles (new teamup specializations)

| Role | Maps From | Research Function |
|------|-----------|-------------------|
| **RL (Research Lead)** | PM | Defines hypothesis, allocates resources, decides next iteration, approves publication |
| **DA (Data Analyst)** | SE | Analyzes experiment results, scores evidence, detects anomalies, recommends refinements |
| **ED (Experiment Designer)** | PG | Designs experiments, writes protocols, submits UMRS jobs, configures sweep strategies |
| **PR (Peer Reviewer)** | CD | Reviews methodology, challenges assumptions, checks statistical validity, prevents p-hacking |

### Research Loop Lifecycle

```
### Research Loop Lifecycle

1. FORMULATE    RL + DeepResearchLoop
   Literature review → identify gaps → propose hypotheses
   
2. DESIGN       ED + DA
   Hypothesis → experimental protocol → pipeline steps → resource estimate
   
3. EXECUTE      ED + UMRS Pipeline
   Submit durable jobs → monitor progress → collect artifacts → record provenance
   
4. ANALYZE      DA
   Parse results → compute evidence scores → detect anomalies → generate visualizations
   
5. EVALUATE     RL + PR
   Evidence vs hypothesis → confidence update → accept/reject/revise → peer review
   
6. DECIDE       RL
   Continue (revise hypothesis) → Publish (finalize results) → Branch (new direction) → Stop (conclusive)
```

### State Model

```python
@dataclass
class ResearchLoopState:
    """Persistent state for a research loop session.
    
    Stored locally as JSON (~/.config/r4/research_loops/<loop_id>/state.json)
    and optionally synced to UMRS Postgres for long-running loops.
    """
    loop_id: str
    status: LoopStatus  # FORMULATING → DESIGNING → EXECUTING → ANALYZING → EVALUATING → COMPLETED
    
    # Hypothesis tracking
    hypotheses: list[HypothesisRecord]  # all hypotheses ever proposed
    current_hypothesis: Optional[str]   # active hypothesis ID
    
    # Experiment tracking
    experiments: list[ExperimentRecord]  # all experiments ever run
    evidence_pool: list[EvidenceRecord]  # accumulated evidence
    
    # Agent state
    agent_assignments: dict[str, str]    # agent_role → agent_id
    agent_communication: list[MessageRecord]  # inter-agent messages
    
    # Iteration
    iteration: int
    max_iterations: int  # default 20
    budget_remaining: float  # compute budget
    
    # Artifacts
    pipeline_runs: list[str]  # UMRS job IDs
    a2l_execution_ids: list[str]  # provenance trace
    published_results: list[dict]  # finalized outputs
```

### Integration Points

```
r4 research start "optimize carbon capture MOF"
    │
    ├── RL agent spawns (via r4 agent --mode autonomous)
    │     └── DeepResearchLoop → literature review → gap analysis
    │
    ├── RL formulates hypothesis (HypothesisManager)
    │     └── "MOF-5 with amine functionalization increases CO2 uptake by >20%"
    │
    ├── ED designs experiment (ExperimentRunner)
    │     ├── Protocol: GCMC simulation → DFT validation → ML force field
    │     └── Resource: 4 GPU nodes, 2h walltime
    │
    ├── ED submits to UMRS (via r4 workflow trigger)
    │     └── UmrsPipeline: GCMC → DFT → ML → Analysis
    │     └── Backed by Slurm + mini-RiVault inference
    │
    ├── DA analyzes results
    │     ├── Parse simulation output → compute isotherm
    │     └── Evidence: CO2 uptake +23.4% vs baseline (p=0.003, Cohen's d=1.8)
    │
    ├── PR reviews methodology
    │     ├── Checks statistical validity
    │     └── Passes: n=50, controls valid, no p-hacking
    │
    ├── RL evaluates
    │     ├── Hypothesis SUPPORTED (confidence 0.85 → 0.94)
    │     └── DECIDE: Publish + Branch (test humidity sensitivity)
    │
    └── State saved → can resume or export
```

### File-Based Persistence (local first)

```
~/.config/r4/research_loops/
└── <loop_id>/
    ├── state.json              # ResearchLoopState (above)
    ├── hypotheses/             # Individual hypothesis files
    │   └── H-001.json
    ├── experiments/            # Experiment protocols + results
    │   └── E-001/
    │       ├── protocol.yaml
    │       ├── pipeline.json
    │       └── results.json
    ├── evidence/               # Evidence records
    │   └── EV-001.json
    ├── changelog.md            # Agent communication log (teamup pattern)
    ├── artifacts/              # Downloaded outputs
    └── iterations/             # Per-iteration snapshots
        ├── iter-001.json
        └── iter-002.json
```

### CLI Surface (proposed)

```bash
# Start a research loop
r4 research start "objective in natural language"
r4 research start --hypothesis "MOF-5-amine increases CO2 uptake >20%"
r4 research start --from-file protocol.yaml

# Manage active loops
r4 research list
r4 research status <loop-id>
r4 research resume <loop-id>
r4 research pause <loop-id>
r4 research export <loop-id> --format paper|report|json

# Hypothesis management
r4 research hypothesis list <loop-id>
r4 research hypothesis create <loop-id> --statement "..."
r4 research hypothesis evaluate <loop-id> --hypothesis H-001

# Experiment management
r4 research experiment design <loop-id>
r4 research experiment submit <loop-id> --experiment E-001
r4 research experiment results <loop-id> --experiment E-001
```

---

## Consequences

### Positive

1. **Composes existing systems without modifying them** — ResearchOrchestrator,
   TeamupSession, and WorkflowPipeline remain unchanged. Research Loop is a new
   layer.

2. **Local-first with server path** — state is JSON files on disk, human-readable,
   version-controllable. UMRS Postgres integration is optional for shared/long-running
   loops.

3. **Hypothesis-driven science** — makes the hypothesis the central organizing
   principle. Every experiment has a hypothesis. Every result is evidence for/against.
   No fishing expeditions.

4. **Reuses teamup communication patterns** — ChangeLog.md, agent mailboxes,
   file-based inter-agent comms are proven in `r4 teamup`.

5. **Reuses UMRS pipeline infrastructure** — experiment execution is just a UMRS
   durable workflow with hypothesis metadata. No new execution engine needed.

### Negative

1. **Multi-agent boot complexity** — spawning 4 independent agent sessions
   (RL/DA/ED/PR) that coordinate through files. Debugging requires reading 4
   changelogs. Mitigation: structured changelogs + `r4 research status` shows
   merged view.

2. **File-based concurrency** — teamup file-based comms are synchronous-polling.
   Agents may wait for each other. Mitigation: research loop has explicit phase
   gates; agents only advance together.

3. **Domain knowledge encoding** — agents need domain-specific prompts (chemistry,
   materials, biology). Mitigation: skills system. `SKILL.md` files encode domain
   knowledge per research area.

4. **Verification complexity** — testing a 4-agent loop with UMRS pipeline
   execution end-to-end is expensive. Mitigation: simulation mode first; fake
   UMRS backend returns synthetic results.

### Neutral

- State size grows linearly with iterations. Typical loop: 20 iterations ×
  ~10KB per iteration = 200KB. Acceptable for file-based storage.
- Loop requires active LLM endpoint. No offline mode (by design).

---

## Alternatives Considered

### A: Single-Agent Research Loop (status quo ResearchOrchestrator)

**Rejected.** The orchestrator already exists but lacks multi-agent coordination.
Science benefits from adversarial review (DA vs ED, PR checks RL). Single agent
suffers from confirmation bias.

### B: Teamup-Only (no pipeline integration)

**Rejected.** Teamup agents can discuss hypotheses but can't run experiments.
Need UMRS pipeline for compute. Must bridge.

### C: Pipeline-as-Workflow (UMRS recipes only)

**Rejected.** Recipes are static DAGs. Research needs dynamic replanning based
on results. Can't encode hypothesis revision into a fixed pipeline.

### D: Full UMRS-backed (everything in Postgres, no local files)

**Rejected for MVP.** Postgres-backed is the right long-term answer but adds
schema migration dependency. Local JSON files allow immediate prototyping
without infra changes.

---

## Implementation Plan (Phased)

### Phase 1: Research Loop State Model (2-3 days)
- `r4/src/r4/research_loop/` module
- `ResearchLoopState` dataclass + JSON serialization
- `LoopStatus` state machine with transitions
- No agent integration yet — just the state container

### Phase 2: ResearchTeam Roles (3-5 days)
- Research-specialized agent roles (RL/DA/ED/PR)
- System prompts per role with scientific epistemology
- Inter-agent communication using teamup ChangeLog pattern
- Mock pipeline results for testing

### Phase 3: Pipeline Integration (3-5 days)
- ED agent generates UMRS pipeline JSON from experimental protocol
- Pipeline submission via `r4 workflow trigger`
- Result collection and parsing
- Evidence generation from pipeline outputs

### Phase 4: CLI + Full Loop (3-5 days)
- `r4 research` CLI command
- End-to-end loop: formulate → design → execute → analyze → evaluate → decide
- Resume from saved state
- Export results

### Phase 5: Domain Skills (ongoing)
- Chemistry/materials SKILL.md
- Biology/genomics SKILL.md
- Physics/simulation SKILL.md
- Generic scientific method SKILL.md (default)

---

## Open Questions

1. **Agent model selection**: Should RL use a stronger reasoning model than ED?
   (RL does hypothesis evaluation — benefits from stronger reasoning.)

2. **Parallel experimentation**: Can ED submit multiple experiments in parallel
   if they're independent? (Yes — use UMRS workflow parallelism.)

3. **Human-in-the-loop**: At what points does the human review? (Every phase
   transition initially; after trust, only at EVALUATE and PUBLISH.)

4. **External scientist collaboration**: Can multiple humans work on the same
   research loop? (Future — requires Postgres-backed state with multi-user access.)

5. **Publication output**: Should the loop auto-generate a paper draft? (Yes —
   Phase 4+ stretch goal. PR agent writes methodology, DA writes results, RL
   writes discussion.)

---

## References

- ROADMAP.md — Multi-Agent Teamup (VibeCodeHPC) is planned P3
- [ARCHITECTURE.md](../../ARCHITECTURE.md) — Gateway + UMRS + mini-RiVault topology
- [CONSTITUTION.md](../../CONSTITUTION.md) — §1.2 UMRS internal boundaries, §3 ownership boundaries
- `r4/src/r4/agent/research_orchestrator.py:230+` — existing ResearchOrchestrator
- `r4/src/r4/teamup/` — existing multi-agent framework
- `umrs/src/umrs/pipeline_sdk/` — existing pipeline execution
