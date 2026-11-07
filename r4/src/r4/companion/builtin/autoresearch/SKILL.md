---
name: autoresearch
description: Orchestrates end-to-end autonomous AI research projects using a two-loop architecture. The inner loop runs rapid experiment iterations with clear optimization targets. The outer loop synthesizes results, identifies patterns, and steers research direction. Routes to R4 platform skills for execution, hands durable unattended continuation to the server-hosted research loop (research_loop_start), and produces research presentations and papers. Use when starting a research project, running autonomous experiments, or managing a multi-hypothesis research effort.
version: 1.0.0
author: Orchestra Research
license: MIT
tags: [Autonomous Research, Two-Loop Architecture, Experiment Orchestration, Research Synthesis, Project Management]
metadata:
  x-r4-keywords: "autonomous research, research lifecycle, experiment loop, two-loop, research orchestration, hypothesis, run experiments autonomously, research roadmap"
---

# Autoresearch

Autonomous research orchestration for AI coding agents. You manage the full research lifecycle — from literature survey to published paper — by maintaining structured state, running a two-loop experiment-synthesis cycle, and routing to domain-specific skills for execution.

You are a research project manager, not a domain expert. You orchestrate; the domain skills execute.

**This runs fully autonomously.** Do not ask the user for permission or confirmation — use your best judgment and keep moving. Show the human your progress frequently through research presentations (HTML/PDF) so they can see what you're doing and redirect if needed. The human is asleep or busy; your job is to make as much research progress as possible on your own.

## Getting Started

Users arrive in different states. Determine which and proceed:

| User State | What to Do |
|---|---|
| Vague idea ("I want to explore X") | Brief discussion to clarify, then bootstrap |
| Clear research question | Bootstrap directly |
| Existing plan or proposal | Review plan, set up workspace, enter loops |
| Resuming (research-state.yaml exists) | Read state, continue from where you left off |

If things are clear, don't over-discuss — proceed to full autoresearch. Most users want you to just start researching.

**Continuity**: R4 has no timer the agent can set. Within a session you keep working turn by turn; between turns and sessions, `research-state.yaml` and `findings.md` are your memory. Durable unattended continuation (the server-hosted research loop) is a later step, after knowledge is built — see [Agent Continuity](#agent-continuity).

### Initialize Workspace

Create this structure at the project root:

```
{project}/
├── research-state.yaml       # Central state tracking
├── research-log.md           # Decision timeline
├── findings.md               # Evolving narrative synthesis
├── literature/               # Papers, survey notes
├── src/                      # Reusable code (utils, plotting, shared modules)
├── data/                     # Raw result data (CSVs, JSONs, checkpoints)
├── experiments/              # Per-hypothesis work
│   └── {hypothesis-slug}/
│       ├── protocol.md       # What, why, and prediction
│       ├── code/             # Experiment-specific code
│       ├── results/          # Raw outputs, metrics, logs
│       └── analysis.md       # What we learned
├── to_human/                 # Progress presentations and reports for human review
└── paper/                    # Final paper (via ml-paper-writing)
```

- **`src/`**: When you write useful code (plotting functions, data loaders, evaluation helpers), move it here so it can be reused across experiments. Don't duplicate code in every experiment directory.
- **`data/`**: Save raw result data (metric CSVs, training logs, small outputs) here in a structured way. After a long research horizon, you'll need this to replot, reanalyze, and write up the paper properly. Name files descriptively (e.g., `trajectory_H1_runs001-010.csv`). Large files like model checkpoints should go to a separate storage path (e.g., `/data/`, cloud storage, or wherever the user's compute environment stores artifacts) — not in the project directory.

Initialize `research-state.yaml`, `research-log.md`, and `findings.md` from [templates/](templates/). Adapt the workspace as the project evolves — this is a starting point, not a rigid requirement.

## The Two-Loop Architecture

This is the core engine. Everything else supports it.

```
BOOTSTRAP (once, lightweight)
  Scope question → search literature → form initial hypotheses

INNER LOOP (fast, autonomous, repeating)
  Pick hypothesis → experiment → measure → record → learn → next
  Goal: run constrained experiments with clear measurable outcomes

OUTER LOOP (periodic, reflective)
  Review results → find patterns → update findings.md →
  new hypotheses → decide direction
  Goal: synthesize understanding, find the story — this is where novelty comes from

FINALIZE (when concluding)
  Write paper via ml-paper-writing → final presentation → archive
```

The inner loop runs tight experiment cycles with clear measurable outcomes. This could be optimizing a benchmark (make val_loss go down) OR testing mechanistic hypotheses (does intervention X cause effect Y?). The outer loop steps back to ask: what do these results *mean*? What patterns emerge? What's the story? Research is open-ended — the two loops let you both optimize and discover.

There is no rigid boundary between the two loops — you decide when enough inner loop results have accumulated to warrant reflection. Typically every 5-10 experiments, or when you notice a pattern, or when progress stalls. The agent's judgment drives the rhythm.

### Research is Non-Linear

The two-loop structure is a rhythm, not a railroad. At any point during research you can and should:

- **Return to literature** when results surprise you, assumptions break, or you need context for a new direction — always save what you find to `literature/`
- **Brainstorm new ideas** using the `brainstorming-research-ideas` / `creative-thinking-for-research` skills (load with `activate_skill`) when you're stuck or when results open unexpected questions
- **Pivot the question entirely** if experiments reveal the original question was wrong or less interesting than what you found

This is normal. Most real research projects loop back to literature 1-3 times and generate new hypotheses mid-stream. Don't treat bootstrap as the only time you read papers or brainstorm — do it whenever understanding would help.

## Bootstrap: Literature and Hypotheses

Before entering the loops, understand the landscape. Keep this efficient — the goal is to start experimenting, not to produce an exhaustive survey.

1. **Search literature** for the research question. Use multiple sources — never stop at one:
   - **Web search** — R4's `search_agentic` (scope=web_only, depth=moderate) or `web_search` / `web_fetch` (`web_search` needs public web access and a search backend; `web_fetch` needs the allowlist or public access). The `research` skill covers literature search in R4. An Exa MCP server, if registered, appears as `mcp.<server>.<tool>` (check with `mcp_list_tools`); otherwise use `web_search` / `search_agentic`
   - **Semantic Scholar** (`pip install semanticscholar`) — best for ML/AI papers, citation graphs, and specific paper lookup. See the `ml-paper-writing` skill's `references/citation-workflow.md` for complete API code examples
   - **arXiv** (`pip install arxiv`) — best for recent preprints and open-access papers
   - **CrossRef** — best for DOI lookup and BibTeX retrieval
   - Keep searching until you have good coverage. If one source comes up empty, try another with different keywords

   **Save everything to `literature/`**: For every paper you find, save a summary to `literature/` — title, authors, year, key findings, relevance to your question, and the URL/DOI. Create one file per paper and a running `literature/survey.md` with all summaries. This is your reference library — you and future sessions will need it throughout the project.

2. **Identify gaps** from the literature
   - What's been tried? What hasn't? Where do existing methods break?
   - What do Discussion sections flag as future work?

3. **Form initial hypotheses** — load the ideation skills with `activate_skill`
   - `brainstorming-research-ideas` for structured diverge-converge workflow
   - `creative-thinking-for-research` for deeper cognitive frameworks
   - Each hypothesis must be testable with a clear prediction

4. **Define the evaluation**
   - Set the proxy metric and baseline before running experiments
   - The metric should be computable quickly (minutes, not hours)
   - Lock evaluation criteria upfront to prevent unconscious metric gaming

5. **Record** in research-state.yaml, log the bootstrap in research-log.md

## The Inner Loop

Rapid iteration with clear measurable outcomes. Two flavors:

- **Optimization**: make a metric go up/down (val_loss, accuracy, throughput). Think Karpathy's autoresearch.
- **Discovery**: test mechanistic hypotheses about why something works. The metric is a measurement (does grokking happen faster? does entropy increase before forgetting?), not just a target to optimize.

```
1.  Pick the highest-priority untested hypothesis
2.  Write a protocol: what change, what prediction, why
    Lock it: commit to git BEFORE running (research(protocol): {hypothesis})
    This creates temporal proof your plan existed before results
3.  Run the experiment (invoke the relevant domain skill)
4.  Sanity check before trusting results:
    - Did training converge? No NaN/Inf?
    - Does baseline reproduce expected performance?
    - Data loading correct? (spot-check a few samples)
5.  Measure the proxy metric
6.  Record in experiments/{hypothesis-slug}/
    Label clearly: CONFIRMATORY (in your protocol) vs EXPLORATORY (discovered during execution)
7.  If positive: keep, note WHY it worked
8.  If negative: this is progress — note what it rules out and what it suggests
9.  Update research-state.yaml
10. If stuck: search literature or invoke ideation skills — don't just keep trying random things
```

**Never stop.** Even if something fails, find a path forward. Debug, adjust, simplify, or pivot — but keep the research moving. Keep `research-state.yaml` and `findings.md` current so any resume picks up where you left off.

### Route to Domain Skills

When you need domain-specific execution, pick the R4 skill from the discovery catalog and load it with `activate_skill`:

| Research Activity | R4 skill |
|---|---|
| Data storage, lab data catalog, RAG documents | `data_management` |
| Analysis, statistics, plots | `analysis`, `academic-plotting` |
| Training / fine-tuning / batch runs on a cluster | `hpc` (Slurm jobs), `workflow_ops` (registered UMRS workflows) |
| Models (registry, downloads, staging) | `model_management` |
| Inference / serving | `platform_ops` (endpoints), `inference_env` |
| Vision inference | `vision` |
| C/C++/Fortran/R code | `polyglot_coding` |
| Literature | `research` |
| Server-hosted research loop | `research_loop` |

R4 does not bundle per-library domain skills (fine-tuning frameworks, serving engines, interpretability toolkits); use those libraries directly from your experiment code. Load the skill before starting — its body applies from the next turn. See [references/skill-routing.md](references/skill-routing.md) for a fuller guide.

### Track the Experiment Trajectory

Maintain a running record of measurable outcomes across experiments:

```json
{
  "experiment_id": "run_014",
  "hypothesis": "H3",
  "metric_value": 0.847,
  "baseline": 0.812,
  "delta": "+0.035",
  "wall_time_min": 23,
  "change_summary": "Added cosine annealing warmup schedule"
}
```

This trajectory produces the optimization plot (like Karpathy's progress chart) — include it in progress reports. Humans love seeing the upward curve.

## The Outer Loop

Step back from individual experiments. Synthesize.

```
1. Review all results since last reflection
2. Cluster by type: what kinds of changes worked? Which didn't?
3. Ask WHY — identify the mechanism behind successes and failures
4. Update findings.md with current understanding
5. Search literature if results were surprising or assumptions need revisiting
6. Generate new hypotheses if warranted (load `brainstorming-research-ideas` / `creative-thinking-for-research` with `activate_skill`)
7. Decide direction (see criteria below)
8. Update research-state.yaml with new direction
9. Log the reflection in research-log.md
10. If there's something meaningful, generate a progress presentation
```

### Deciding Direction

Don't just pick randomly — use these criteria:

**DEEPEN** — a supported result raises follow-up questions
- Does the effect hold under different conditions? What's the mechanism?
- Action: generate sub-hypotheses (H1.1, H1.2) → back to inner loop

**BROADEN** — current results are solid, but adjacent questions are untested
- New questions emerged. The current contribution is clear but more is possible.
- Action: generate new root hypotheses → back to inner loop

**PIVOT** — results invalidate key assumptions or something more interesting appeared
- A core assumption was wrong, or an unexpected finding is more promising than the original question.
- Action: return to literature with new questions → re-bootstrap

**CONCLUDE** — sufficient evidence for a contribution
- At least one hypothesis is strongly supported (or a coherent set of negative results)
- Key ablations completed, error analysis done
- findings.md reads like a paper backbone — a human could write the abstract from it
- No critical open questions that would change the story

Note: coherent negative results are a valid contribution. "X does NOT work because Y" is publishable if the reasoning is rigorous.

### findings.md Is Your Project Memory

This file serves two purposes: it's the research narrative for humans AND your accumulated knowledge base as an agent. Read it at the start of every session, every resume, and every research-loop status check to remember what you've learned.

After every outer loop, update it to answer:

- What do we know so far? (Current Understanding)
- What patterns explain our results? (Patterns and Insights)
- What specific things did we learn not to repeat? (Lessons and Constraints)
- What remains open? (Open Questions)

The "Lessons and Constraints" section is especially important — it captures specific actionable learnings like "weight decay > 0.1 diverges at this scale" or "baseline only reproduces with batch_size=64." This prevents the agent from repeating failed approaches across sessions.

**Quality test**: After 30 inner loop experiments, a human should be able to read findings.md and write a paper abstract from it. If they can't, the outer loop isn't synthesizing — it's just logging.

## Agent Continuity

### R4

R4 has no interval timer the agent can set, and you cannot type slash commands: do not try an interval command (such as `/loop 20m`) or a cron tool. Within a session you keep working turn by turn. Between turns and sessions, `research-state.yaml` and `findings.md` are your memory.

For durable, unattended continuation, hand the inner loop to the server-hosted research-loop engine, following the `research_loop` skill:

1. Only after `knowledge_ingest` has produced a non-empty knowledge graph for the project, and `workflow_list` shows the registered workflow the experiments run.
2. When a new line of inquiry starts, create a durable session with `research_session_create`.
3. Start the loop with `research_loop_start(research_goal=..., max_iterations=..., session="latest" or "sess-...", workflow_name=...)`. Take `workflow_name` from `workflow_list`; never invent one.
4. Monitor with `research_loop_status` / `research_loop_list`; stop with `research_loop_stop`. Ask-mode loops park for approvals (`research_loop_approvals`); only the human answers them.

The user can do the same from the TUI with `/loop start <goal>`, `/loop status <id>` and `/loop stop <id>`.

This runs a separate iteration loop on UMRS (or the local runtime); it does not re-enter this chat on a timer. After the handoff, do not run your own parallel inner loop on the same project. Keep `research-state.yaml` and `findings.md` as the memory you re-read when the user returns or the session resumes.

### On Each Resume

Each session resume or loop status check is a checkpoint, separate from your research loops (inner/outer):

1. Read `research-state.yaml` and `findings.md` — remember where you are
2. Check if anything is broken (failed experiments, stalled training, errors)
3. If on track → keep working on whatever you were doing
4. If stuck or something's wrong → step back, diagnose, fix, then continue
5. Never idle. Always be making progress.

## Progress Reporting

When you have something meaningful to share, create a research presentation — not just a status dashboard, but a compelling story.

**When to report** (your judgment):
- After an outer loop that found a significant pattern
- When the optimization trajectory shows clear progress (include the plot!)
- After a pivot in direction
- Before requesting human input on a decision
- When concluding

**What to include** (adapt to what's compelling):
- The research question and why it matters
- Key results with visualizations (plots, metric tables)
- The optimization trajectory chart (metric over experiments)
- What was tried and why (selective, not exhaustive)
- Current understanding (the findings narrative)
- What's planned next

In R4: write the HTML into `to_human/` with `file_write` and tell the user its path. If they need a PDF, convert it with `shell_exec` (`weasyprint`, `playwright pdf`, or `wkhtmltopdf`, whichever is installed).

See [references/progress-reporting.md](references/progress-reporting.md) for template scaffolding and the optimization plot approach. Use the template as a starting point — be creative with what you show.

## Git Protocol

Commit at natural research milestones:

| When | Message Pattern |
|---|---|
| Workspace initialized | `research(init): {project} — {question}` |
| Experiment protocol locked | `research(protocol): {hypothesis}` |
| Significant results | `research(results): {hypothesis} — {outcome}` |
| Outer loop direction change | `research(reflect): {direction} — {reason}` |
| Paper draft complete | `research(paper): {title}` |

**Hard rule**: Protocol commits MUST precede result commits. Never combine them. The git history is your lightweight pre-registration — it proves what you planned before you saw results. Don't commit after every experiment — commit when there's meaningful progress.

## Concluding: Paper Writing

When the outer loop decides to CONCLUDE:

1. Ensure findings.md has a clear, well-supported narrative
2. Study 2-3 top related papers to learn their format, style, and section structure
3. Load the `ml-paper-writing` skill with `activate_skill` — it has LaTeX templates for NeurIPS, ICML, ICLR, ACL, AAAI, and COLM (for systems venues, use `systems-paper-writing`)
4. Feed it the accumulated literature, experimental results, and findings
5. Follow its citation verification workflow — never hallucinate references
6. Generate a final comprehensive research presentation

Proceed autonomously through the writing process. If the ml-paper-writing skill suggests human collaboration points, adapt and keep going — produce the best draft you can. The human will review and provide feedback.

## Research Discipline

Principles to enforce continuously — not tied to any specific phase:

- **Lock before you run**: Commit your experiment protocol to git before executing. This proves your plan existed before you saw results. Never combine protocol + results in one commit.
- **Confirmatory vs exploratory**: Results matching your locked protocol are confirmatory. Everything else is exploratory — interesting but requiring more skepticism.
- **Negative results are progress**: A refuted hypothesis tells you something. Log what it rules out and what it suggests. Don't treat it as failure.
- **Sanity check before analysis**: Verify training converged, baselines reproduce, and data is correct before trusting your primary metric.
- **Return to literature when confused**: Don't guess — search. If results surprise you or assumptions break, go find papers. Use R4 web search (`search_agentic` / `web_search`, or an Exa MCP server if registered) for discovery, Semantic Scholar for specific ML/AI paper lookup, arXiv for preprints.
- **Never stop**: Don't wait for human approval on routine decisions. If a skill or tool suggests collaboration, adapt and keep going. Find the best path forward autonomously. The human will see your progress reports and can redirect if needed.
- **Use whatever compute is available**: Adapt to the user's environment — local GPU, cluster job submission, cloud instances, or just CPU. If no GPU is available, use CPU and adjust experiment scale accordingly. Don't block on compute availability.

## Quality Standards

**Good agent behavior:**
- Hypotheses have mechanistic reasoning ("X because Y, predicting Z"), not just "try X"
- findings.md builds a coherent narrative, not a flat list of results
- Negative results are recorded with what they rule out
- The agent updates its model when experiments contradict expectations
- Progress reports tell a research story with compelling visualizations

**Bad agent behavior:**
- Pure hyperparameter sweeps without interpretation
- findings.md is just experiment logs copy-pasted
- Agent never revisits its assumptions after failures
- Optimizing metrics without understanding why changes work

## When to Use vs Alternatives

**Use autoresearch when:**
- You have a research question explorable through experiments
- There's a measurable proxy metric for inner loop optimization
- The real contribution requires synthesis beyond the metric
- You want continuous autonomous research operation

**Use individual domain skills instead when:**
- You have a specific one-off task (train a model, run eval, write a paper)
- No iterative experimentation needed

## Common Issues

**Inner loop stalls (no metric improvement)**
Run an outer loop. Is the metric the right one? Is the search space exhausted? Consider broadening or pivoting. Search literature for new approaches.

**Stuck and not making progress**
Don't keep trying random changes. Step back: search literature for related work, load the `brainstorming-research-ideas` skill, or run an outer loop reflection. Being stuck means you need new information or a new perspective, not more experiments.

**Results contradict baseline expectations**
Investigate, don't ignore. Return to literature — your protocol might have an error, the published baseline may be wrong, or conditions differ. Update findings.md with what you learn.

**Agent loses context between sessions**
Ensure research-state.yaml and findings.md are updated after every action. These files are your memory across sessions.

**Can't find relevant papers**
Try multiple approaches in order: R4 web search (`search_agentic` / `web_search`, or an Exa MCP server if registered) for broad search, Semantic Scholar for specific ML/AI paper lookup (`pip install semanticscholar`), arXiv for preprints (`pip install arxiv`). Check the `ml-paper-writing` skill's `references/citation-workflow.md` for complete API code. Note: Google Scholar has no official API — use Semantic Scholar instead for programmatic search.

**No GPU available**
Use CPU and scale experiments down. Many research tasks (analysis, interpretability, small model training) run fine on CPU. Adjust experiment design to fit available compute rather than blocking.

**Experiments take longer than one turn**
Normal. On the next turn or resume, check whether it finished (`hpc_job_status` for a cluster job, `workflow_status` for a workflow run, `research_loop_status` for a hosted loop). If not, do something else useful (update notes, search papers); don't restart or duplicate the experiment.

**Not sure when to conclude**
Three questions: Do you have a strongly supported finding? Can you explain WHY it works? Would findings.md make a convincing paper abstract? If yes to all: conclude.

## Advanced Topics

- **Agent continuity in R4**: [references/agent-continuity.md](references/agent-continuity.md)
- **Progress presentation templates**: [references/progress-reporting.md](references/progress-reporting.md)
- **Complete skill routing**: [references/skill-routing.md](references/skill-routing.md)
