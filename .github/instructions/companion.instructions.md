---
description: Research Companion module — architecture, patterns, and conventions
applyTo: 'r4/src/r4/companion/**'
---

# Research Companion — Development Guide

Updated 2026-09-22 (design reset): module tree, storage paths and platform
constraints re-verified against `r4/src/r4/companion/` on that date. Design
rows `D-nn` refer to
[docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../../docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md).
*2026-10-30 (ADR-101):* the tree below gains `sandbox/` and
`ui/sandbox_bridge.py`. A new agent-driven `Popen` must go through
`sandbox.wrap_spawn` and turn a `SandboxError` into a tool error, never a
retry without the wrapper; nothing may add a job store, queue or registry
to the sandbox ([ADR-101](../../docs/adr/101-local-tool-sandbox.md) D-10).
A thread that outlives its turn and spawns (a background agent, a run) takes
its target through `sandbox.bind_sandbox`, so it keeps the context it
started under; a host-side `git` call in a directory the agent can write
goes through `r4/src/r4/safe_git.py` (`git_argv`, `git_env`), never a bare
`["git", ...]` (2026-10-30, ADR-101 final review).

## Vision

The companion is **R4's agent harness** — "a dedicated AI colleague for
researchers" (`companion/__init__.py`). In the design R4 is the whole
researcher-facing layer (console, harness, skills, context control, teams,
local/remote sessions, projects, research loop); the companion is the agent
process behind the console and the REPL. Harness and context control are
implemented (D-26, D-28); the console as single entry point is partial
(D-25); teams and the loop are fragmented across older mechanisms (D-29,
D-32) — extend, do not add. One contradiction is open (D-37, below).

## Architecture

```
companion/
  agent.py                 — ResearchCompanion: agentic loop, CompanionEventType events, sub-agent spawning
  agent_registry.py        — in-memory agent registry for the console
  acp_session.py           — ACP adapter: CompanionSession → AgentSessionProtocol (r4/src/r4/acp.py, ADR-042)
  tools.py                 — built-in tool implementations
  tool_selection.py        — tool selection as an explicit, observable phase (ADR-037)
  tool_availability.py     — static tool-availability gate
  tool_summaries.py        — sanitized args_summary for tool cards
  tool_python.py           — interpreter for python_exec / coding tools: R4_TOOL_PYTHON, else the user's python outside R4's venv (2026-10-24, TB4 H1)
  shell_detach.py          — shell_exec detach-on-timeout: spill files, reap, terminate at exit (2026-10-24, TB4 H18)
  agent_profile.py         — R4_AGENT_PROFILE=terminal benchmark ablation: core prompt, tool/skill allowlists, SHELL_OUTPUT_CAP (2026-10-24, TB4 H2)
  mcp_first_class.py       — first-class MCP tool registration
  client_extra_tools.py    — sources, pools, endpoint upgrades
  permissions.py           — PermissionManager (ask_always / auto_safe / trust) + server trust checker (ADR-035)
  taint.py                 — taint registry: provenance of untrusted content (ADR-039)
  context.py               — ContextManager: token budget, compaction, memory, RAG
  memory_store.py          — saved memory, one private file per fact, user + project scopes (2026-11-07, ADR-111)
  memory_tools.py          — memory_read / memory_write / memory_forget, attached by the lead session only (ADR-111 D-4)
  memory_commands.py       — /memory and `r4 memory` verbs: list, add, forget, lessons, promote, off/on
  context_budget.py        — budget resolved from the active model
  skills.py                — SkillRegistry + unified SKILL.md parser (all sources)
  builtin/                 — 27 built-in skill groups, Agent Skills format (<name>/SKILL.md)
  knowledge_tools.py       — project knowledge: status, ingest, describe
  knowledge_graph_tools.py — multi-graph discovery, selection, propose / decide extension
  knowledge_write_gate.py  — session-scoped acquisition mode: open / pinned / disabled
  knowledge_base.py        — knowledge base view for the console
  project_tools.py         — project list / create / switch
  workflow_tools.py        — workflow adapters over the shared WorkflowClient contract
  workflow_policy.py       — deployment workflow capability policy
  research_loop_tools.py   — research-loop tools via the Research Runtime API (server-hosted loop, ADR-036)
  science_loop.py          — client-side science-loop state tracker for the console (D-32)
  task_graph.py · job_manager.py · test_fix_loop.py · apply_patch.py · coding.py · streaming_shell.py · file_filtering.py
  sandbox/                 — local tool sandbox, opt-in (2026-10-30, ADR-101, D-65): every agent spawn calls wrap_spawn() right before Popen
    setting.py             — SandboxSetting, R4_SANDBOX / R4_LAUNCH_SANDBOX / R4_SANDBOXED, resolve order, profile default, SANDBOX_NOTICE_OFF
    policy.py              — SandboxPolicy: write roots, deny lists, protected repo files, rename guards, env scrub, user sandbox_policy
    backends.py            — BwrapBackend (Linux), SeatbeltBackend (macOS), probe; fail closed (sandbox.unavailable)
    proxy.py               — EgressProxy: loopback forward proxy deciding per request via web_access.host_allowed; infra ranges refused
    _netns_init.py         — stdlib-only forwarder run INSIDE the Linux netns (must not import r4)
    context.py             — SandboxContext per session, set_current_sandbox / current_sandbox, pin_sandbox / bind_sandbox (per-thread), wrap_spawn
    selftest.py            — fail-closed self-test (sandbox.self_test_failed), a pass cached per policy digest
    launch.py · mcp_policy.py — supervisor for MCP stdio children and hatch runtimes (exit 78 on refusal); launcher prefix, MCP policy digest
  session.py               — CompanionSession orchestration shell (public session API)
  session_flow.py          — send / start / finish turn flow
  session_turns.py         — prompt assembly, history shaping, payload prep
  session_usage.py         — usage accounting, LLM caps, metadata sync
  session_runtime.py       — harness lifecycle + endpoint refresh
  session_render.py        — duration / report / markdown rendering
  session_models.py        — message + usage data structures
  session_state.py         — session metadata serialization
  session_store.py         — SQLite persistence + compatibility path handling
  session_ops.py           — on-demand session operations shared by the bridge and ACP rails
  persistence.py           — persistence protocol
  event_log.py · replay.py · checkpoint.py — SQLite event log (`event_log` table), replay, checkpoints (`checkpoints` table)
  control_schema.py · cost_event.py — control-event schema and session/cost payload for the transport rails
  cancellation.py · resource_monitor.py · endpoint_policy.py · endpoint_state.py · model_resolution.py · status_report.py · ablation.py
  harness/
    __init__.py            — ResearchHarness facade
    probe.py               — EnvironmentProbe (cwd, HPC, git, models)
    guides.py              — FeedforwardGuides (domain detection, context assembly)
    sensors.py             — QualitySensor (pre/post-execution validation)
    progress.py            — ProgressTracker (cross-session milestones)
    steering.py            — SteeringLoop (lessons from corrections)
    templates.py           — HarnessTemplate (pre-configured workflow bundles)
  ui/
    __init__.py            — launch(): Console v3 (OpenTUI) when available, else Rich REPL; headless `r4 -p`
    bridge_server.py       — JSON-lines bridge (protocol v1) to the console
    command_bridge.py      — slash commands bridged into the companion UIs
    project_bridge.py      — typed project_* RPCs for the console
    sandbox_bridge.py      — session.sandbox_get / session.sandbox_set, snapshot key session.sandbox (2026-10-30, ADR-101)
    repl.py                — Rich REPL fallback (always available)
```

There is no Python TUI in this package: the Ink/JS TUI was deleted
(ADR-014, 2026-09-11) and the interactive surface is the OpenTUI console
(`packages/r4-tui-v3/`, `r4 console`; a checkout runs its own console source after `make install`, `r4 tui install` is for installs without one) talking
to `ui/bridge_server.py`. `--legacy-tui` or `force_repl=True` selects the
Rich REPL.

## Key Principles

### 1. Platform-first
- Design rule: Slurm job management, model provisioning and storage are
  UMRS responsibilities reached through the client (`r4.Client`); R4 never
  talks to a scheduler, bucket or database directly. **Today this is
  contradicted** (gap D-37, defect V-01): `hpc_submit` / `hpc_job_status`
  run `sbatch` / `squeue` as local subprocesses (`tools.py:702`, advertised
  by `builtin/hpc/SKILL.md`) and `resource_monitor.py` shells `squeue`.
  Do not add new direct scheduler, bucket or database calls; routing these
  tools through UMRS job submission (or scoping them to the cluster venue)
  is the D-37 wave, not an incidental edit.
- Podman is the preferred sandbox runtime (mini-RiVault); Singularity is
  legacy (D-12). Nothing in the companion may assume a runtime.
- Rich REPL fallback for hosts where the OpenTUI console is unavailable
  (login nodes over SSH).
- All models pre-staged, never downloaded at inference time.

### 2. Harness is Invisible
Researchers never need to know the word "harness." They experience:
- Deep contextuality from the first message (probe + guides)
- Mistakes caught before they waste compute (sensors)
- Seamless continuity across sessions (progress tracker)
- A companion that gets smarter over time (steering loop)

### 3. Feedforward > Feedback
Prevent mistakes (guides) rather than catch them after (sensors), but use both.
"Keep quality left" — Fowler's harness engineering principle.

### 4. Platform Constraints
- **No model downloads inside inference containers** — `r4 model stage`
  submits a `MODEL_DOWNLOAD` durable job.
- **All API calls go through the edge and UMRS** — HTTPS to the NGINX +
  auth-sidecar edge (`/gw/*`) → UMRS HTTP sidecar, or ZeroMQ → UMRS for the
  data plane; never directly to vLLM / LiteLLM / S3. This dual transport is
  the design (ADR-059; 2026-10-18: said "Design target: UMRS as the single
  gateway over ZeroMQ (D-01/D-02) — do not pre-empt it", decided 2026-09-22).
- **Files go client → UMRS → S3**; presigned URLs exist only for compute
  nodes and never reach the client or the user.
- **`TRANSFORMERS_OFFLINE=1`** for all inference jobs.

### 5. Knowledge grows by proposal
- Agent-originated knowledge mutations are **proposals**:
  `knowledge_graph_propose` → show the proposal and stop → the user's
  explicit approval is recorded by `knowledge_graph_decide` → only then
  `knowledge_ingest(..., proposal_id=…)`. `knowledge_graph_decide` never
  auto-approves (`knowledge_graph_tools.py`); the server enforces it
  (`umrs/src/umrs/api/_knowledge_mutation_guard.py`, migration 0069, ADR-050).
- The acquisition write gate (`knowledge_write_gate.py`) is session-scoped:
  `open` (default; approvals still gate each write), `pinned` (extend only
  the named pre-existing graphs), `disabled` (propose and ingest refuse).
- `knowledge_insufficient` is never permission to mutate.

### 6. Remote venues (ADR-046)
- A companion session may run as a remote venue worker
  (`r4/src/r4/venue_worker.py`, lease rail in `r4/src/r4/venue_rail.py`,
  server side `umrs/src/umrs/api/companion_venue.py`, job kind
  `frontend_session`). **The worker never holds a user JWT or a provider
  key**: it holds a scoped lease token, and provider calls exit through the
  credential-injecting inference proxy in the UMRS trust zone.
- Venues today: `frontend` and `cluster:<name>`; CPU-only and trusted-host
  venues are design targets (D-09, D-48).

### 7. One loop, one team engine
`research_loop_tools.py` drives the server-hosted loop
(`umrs/src/umrs/knowledge/loop.py`, job kind `research_loop_job`) through
the `ResearchRuntime` SPI (`r4/src/r4/runtime/`); `science_loop.py` is a
parallel console-only state tracker. Do not add another loop or team
mechanism (CONSTITUTION §1.6; D-29, D-32).

## Conventions

### Events
Use `CompanionEventType` (`agent.py`) for all agent→UI communication. Never print directly.

### Tool Execution (`agent.py`)
1. Serial prepare in the main thread (`_prepare_tool_call`): parse args →
   PreToolUse hooks (`r4.agent.hooks`; may block or modify args) →
   permission gate (`PermissionManager`, taint-aware; server trust
   allowlist when wired) → tool definition lookup
2. Pre-execution sensor (`ResearchHarness.pre_execution` → `harness/sensors.py`)
3. Execute tool
4. Post-execution sensor (`ResearchHarness.post_execution`)
5. Append the result and return to the agent loop

### Context Budget
- Token estimation: `CHARS_PER_TOKEN = 3.5` (conservative)
- Compaction threshold: 0.80 of `max_context_tokens` by default; the budget
  itself is derived from the active model (`context_budget.py`)
- Always keep: system prompt + the last 6 messages (`keep_recent=6`) + the compacted summary

### Session Persistence (workstation)
- Sessions: SQLite at `~/.config/r4/companion/sessions.db` (`R4_COMPANION_DB`);
  mirrored to Postgres on the server (ADR-036 amendment 1; chat-turn
  authority is ADR-057, D-30/D-56)
- Memory (2026-11-07, ADR-111): `~/.config/r4/memory/{user,projects/<key>}/<name>.md`
  (`R4_MEMORY_DIR`), one fact per file, never in a session row or on a
  server; the old `~/.config/r4/companion/memory.json` is only imported once.
  Agent-facing memory tools go on the lead session, not into
  `get_builtin_tools` (roles, plan agents and recipes read, never write).
  Team rules belong in `R4.md`, domain facts in the project's knowledge.
- Progress: `~/.config/r4/companion/progress/current.json` (`R4_COMPANION_PROGRESS_DIR`)
- Lessons: `~/.config/r4/companion/lessons.json` (`R4_COMPANION_LESSONS_DIR`)
- Event log and checkpoints: SQLite tables (`event_log`, `checkpoints`) in the database path `ui/bridge_server.py` hands to `EventLog` / `CheckpointManager`

### Session Boundaries
- `session.py` should stay focused on wiring collaborators and exposing the public session API.
- New helper logic belongs in the adjacent `session_*` modules unless it truly requires direct session-owned mutation.
- Keep compatibility imports and `DB_PATH` behavior stable for REPL, console-bridge, ACP and Jupyter callers.

### Skills
- ALL skills use the general Agent Skills format (https://agentskills.io/specification):
  a directory `<name>/SKILL.md` with YAML front-matter + body (system prompt)
- Built-in skills in `companion/builtin/<name>/SKILL.md` — loaded through the
  same parser as user/project skills (single code path in `skills.py`)
- User skills in `~/.config/r4/companion/skills/<name>/SKILL.md`
  (`R4_COMPANION_SKILLS_DIR`) and `~/.config/r4/skills/`
- Project skills in `.r4/skills/` and `skills/` under cwd
- Front-matter: `name`, `description` (required), `allowed-tools` (space-separated
  tool gating), `metadata.x-r4-keywords`, `metadata.x-r4-dependencies`; legacy
  inline `tools:`/`keywords:`/`dependencies:` lists still parse
- Body placeholders `{{contract:<module>:<CONSTANT>}}` resolve from r4-contracts
  at load time — guidance stays single-sourced, never rephrased in skill files
- Never add built-in skills as Python objects; always add a SKILL.md dir
- Skills are part of the Scientific Catalog in the design; the client
  loader currently drops the `version` front-matter field (defect V-06),
  and tests per skill and catalog membership are design targets (D-27)

### Testing
- Mock the `Client` — never make real LLM calls in tests (`r4/tests/test_companion/`)
- Use `tmp_path` for all file-based state (sessions.db, memory.json, etc.)
- Test harness components independently
- Tests never edit `sys.path` or load modules by path (ADR-053)
