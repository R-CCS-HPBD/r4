# ADR-092: Team plans — lead-authored, declarative multi-agent orchestration on the one team seam

**Status:** Accepted (2026-10-11, the maintainer accepted MD-1 to MD-9; first Proposed 2026-10-09; maintainer request of the same date: "teamup workflow need to be more flexible. now it is too much referencing HPC code mode, but we want to have more closer thing to what Claude harness implements as agent 'ultracode'". This ADR amends the Accepted ADR-072 D-1 and ADR-075 D-3, so acceptance is the maintainer's. Slice 1 is opt-in and additive. Slice plan: slices 1–9 (with 8b) built and merged into `feat/adr-093-retrieval` on 2026-10-12; see the rollout blocks.)
**Date:** 2026-10-09
**Relates to:**
- ADR-020 (Team Engine SPI). Extended: TeamSpec v2, plus an optional `RuntimeServices.agents` broker.
- ADR-072 (one team seam). D-1 is amended.
- ADR-075 (one work engine). D-3 is amended; D-2 is kept.
- ADR-039 (taint fence). Made per agent.
- ADR-062 and ADR-068. Model access per agent stays gated and by catalog name.
- ADR-066 and ADR-067. Consumed unchanged; mapped before any loop use.
- ADR-074 and CONSTITUTION §2.1. Not triggered: no agent-authored code runs.
- ADR-087. Every agent round is streamed.
- CONSTITUTION §1.3, §1.6, §6.
- Gap rows:
  - D-29 advances.
  - D-33 and D-36 are consumed by the loop slice.
  - D-37 stays a separate cleanup.

## Context

The maintainer asked for teams that work like Claude Code's multi-agent workflow mode ("ultracode"):

- The lead agent writes a short, deterministic orchestration for the task at hand.
- Each step spawns an ephemeral, task-scoped sub-agent defined only by its prompt. A step may add an output schema and a model.
- Steps compose in two ways: barrier fan-outs, and per-item pipelines with no barrier between stages.
- Every run sits under a concurrency cap, a hard token budget and an agent-count backstop.
- Quality comes from named patterns: adversarial verify, judge panel, loop-until-dry, completeness critic.
- Every agent result is journalled, so a re-run replays whatever did not change.
- The lead reads each phase's structured result before writing the next phase.

Five facts, verified in the tree on 2026-10-09, decide the shape of the answer.

1. **Roles are a closed HPC enum, and the enum is structural.**
   - `AgentRole` has four members: PM, SE, PG and CD (`r4/src/r4/teamup/roles.py:20-26`).
   - Agent state, prompts and models are keyed on it (`teamup/session.py:288-290`). A team therefore has at most four agents, one per role.
   - The native engine drops every `AgentSpec` whose name or role is not one of the four, and it inserts PM (`teams/native.py:51`, `:188-222`).
   - Every non-HPC crew re-skins the four slots (`teamup/crews.py`, `research_loop/team.py:173-178`).
   - Every session builds HPC scaffolding (SOTA tracker, hardware profile, `CLAUDE.md`) whatever the crew (`session.py:295-302`, `:358-440`).
   - The design names planner, experimenter, analyst and reviewer only as examples (`docs/R4-UMRS-DESIGN.md:90`).

2. **Orchestration is a fixed round-robin.**
   - An iteration gives every configured role one turn. The turns run in order, or as a gather barrier over at most four roles (`session.py:482-532`).
   - Nothing fans out by data, and nothing pipelines without a barrier.
   - Quality patterns exist only as prompt text (`research_loop/team.py:130-165`).
   - The other engine, LangGraph, runs in supersteps. It would raise on real fan-out because its reducers are missing (`engines/r4-engine-langgraph/src/r4_engine_langgraph/run.py:34-36`).

3. **Results come back as text tails.**
   - A role's answer is logged as `response[:1200]` (`session.py:609`).
   - `teamup_conclusions` returns the last two such entries per role (`companion/tools.py:3094-3125`).
   - No structured output exists at any layer. A `response_format` key would be dropped silently, because the provider path forwards only five keys (`client.py:1211-1217`).

4. **Governance does not hold up once agents run in parallel.**
   - Every companion re-attaches its own taint registry to the shared host `PermissionManager`. It does so at construction and at every tool gate (`companion/agent.py:2099`, `:4030`), through an unlocked setter (`permissions.py:500-502`).
   - Approvals carry no agent identity (`control_schema.py:47-58`), and the bridge's request counters are unlocked (`bridge_server.py:757`, `:893`).
   - A full-parity role is offered the whole tool catalog, `teamup_start` included (`teamup/role_agent.py:72-73`).
   - Role turns skip `usage_callback` and `pre_llm_call` (`role_agent.py:153-172`), so their tokens reach neither the session's records nor any budget.
   - Measured usage is a single consume-on-read attribute that every thread shares (`client.py:1327`, `agent.py:3333-3335`).
   - `chat_once_factory` drops the per-role model (`role_agent.py:217-226`).

5. **The seam is ready for one more compile target.**
   - Engines register through the `r4.team_engines` entry point, beside a builtin (`r4/pyproject.toml:37-38`, `teams/registry.py:42-45`).
   - ADR-020 §1 already lists a "Native R4 plan graph" as a compile target (`docs/adr/020-team-engine-spi.md:55`).
   - The resolver documents a spec-driven capability match that it never performs (`teams/resolver.py:3-8`, `:121-129`).
   - `final_output` is the companion's terminal channel, matched by tool name (`companion/agent.py:750-767`).
   - The role-turn runtime already builds one fresh companion per turn, with an activity stream (`role_agent.py:77-150`, `:231-287`).

A contributor who did not know these facts might reasonably do one of four things, and each would be wrong:
- (a) Let the lead agent write Python or JavaScript and run it in the R4 process. That is agent-authored code outside any sandbox, which CONSTITUTION §2.1 and ADR-020 §14 forbid.
- (b) Build a dynamic orchestrator beside teamup. That is the fifth mechanism §1.6 forbids.
- (c) Give the orchestrator a durable run store in `r4/`. That is the client-side job store §6 forbids.
- (d) Add `response_format` to the client and assume structured output now works.

## Decision

**D-1 A team is a plan, and a plan is data in one IR.**
- A *plan* is a validated JSON document with three parts:
  - phases;
  - plan-local agent *profiles*, keyed by any string, each giving instructions, a tool allowlist, a model and a tool-round budget;
  - *steps*.
- The grammar lives in `r4-contracts/src/r4_contracts/team_engine/plan.py` and its pattern expander `plan_patterns.py`. Both are stdlib only (INV-10), with `TEAM_PLAN_VERSION = 1`, so a later UMRS-hosted interpreter can import the same semantics.
- `TeamSpec` becomes v2:
  - `TEAM_SPEC_VERSION = 2`;
  - a new style, `WorkflowStyle.PLAN`;
  - an optional `TeamSpec.plan` that carries the bound, expanded program.
- v1 roster specs are unchanged.
- Roster crews and plans are two spellings that meet in one IR. Slice 4 compiles crews into plan templates, so no third user grammar survives (MD-2).

**D-2 One CORE `plan` engine behind the SPI; native stays the default for roster specs.**
- The engine lives in `r4/src/r4/teams/plan/`. It is registered beside `native`, as a builtin and as an `r4.team_engines` entry point, and it declares only `WorkflowStyle.PLAN`.
- The resolver gains its documented step 3. When the project or site default cannot express a spec, the resolver picks the first available CORE engine that can, and records the reason.
- Native and LangGraph refuse PLAN specs by name. Nothing about roster specs resolves differently.
- The engine imports only `r4_contracts`, `r4_teamengine_spi` and the standard library, which an import-fence test pins. So it never holds a `Client`, a credential, a tool or a `PermissionManager` (ADR-020 reverse control). It can move to `engines/` the way ADR-020 phase 1 intended for native.

> **Amends ADR-072 D-1.** Current text: "The native engine over the teamup runtime is the default. Other engines (LangGraph today) are external plugins, resolved by the registry." New reading: the native engine is the default for roster specs. Plan specs resolve to the in-tree CORE `plan` engine through the same registry and resolver. Other engines remain external plugins. "Nothing new orchestrates a team outside the SPI" is unchanged. ADR-072's rejected option, "a role set is data, not an engine", is still honoured: the plan engine knows no roles, and templates are data.

**D-3 Every agent step is one role turn, run by the role-turn runtime through a governed broker.**
- `RuntimeServices` gains an optional `agents: AgentBroker | None`, with `run_turn(AgentTurnRequest) -> AgentTurnResult`. The field is additive, so `SPI_VERSION` stays 0.1.
- The r4 implementation is `RoleTurnAgentBroker` (`r4/src/r4/teams/agent_broker.py`). It runs each request as one fresh `ResearchCompanion` turn through `role_agent.run_agent_turn`.
- `run_agent_turn` is extracted from `run_role_turn`, and `run_role_turn` delegates to it with unchanged behaviour.
- Every turn runs on the host's r4 `Client`, so every round streams (ADR-087) and resolves through `Client._resolve_gated_route` (ADR-062).
- Agent ids are `<step>[<item>]#<replica>`. This path has no role enum, no four-agent limit and no one-agent-per-role limit.

> **Amends ADR-075 D-3.** Current text: "The role-turn runtime is the per-iteration executor in every venue/driver combination (crews and pipeline specs both lower to ordered role turns)." New reading: the role-turn runtime runs every agent turn in every venue/driver combination. Crews, pipeline specs and plans all lower to role turns. The roster orders them, or the plan does, as a sequence, a barrier fan-out, or a per-item pipeline with no barrier. A crew is the degenerate plan.
>
> **D-2 is kept verbatim.** A plan run is one bounded unit with no driver. Plan agents can never start plans, teams or loops (D-6 below). Autonomy still comes only from the one driver of record.

**D-4 The grammar is closed and statically bounded.**

Step kinds:
- `agent`: one prompt template, plus optional `schema`, `model`, `use` (a profile), `label` and `log`. Its `fanout` (`{count}` or `{values, as}`) makes independent replicas, which is ultracode's `agent()` and `parallel()`.
- `map`: runs over a path, with a required `max_items`, through 1-4 `stages`. Each item flows through its stages with no barrier, and the step completes when every item has finished. With several stages it is ultracode's `pipeline()`; with one stage, its `parallel()`.
- `reduce`: closed, pure operations only: `concat`, `flatten`, `dedup_by`, `count`, `majority` with a quorum, `partition`.
- `pattern`: expanded at compile time into the primitives above.

Other rules:
- Phases carry a title and are emitted as progress events.
- Templates are substitution only: `{{path}}`, `{{path|json}}` and `{{path|lines}}`. Paths may reach `params`, earlier `steps.<id>.output`, `item`, `item_index` and the fan-out variable.
- Any value that came from an agent is spliced inside a delimited `<step_output source="…" untrusted="true">` envelope.
- There is no expression evaluation, clock or randomness. The walk is therefore deterministic, and every count is static once parameters bind.
- The validator computes the plan's agent upper bound. It refuses any plan whose bound exceeds `budget.max_agents` (default 100, hard limit 1000).
- `concurrency` is 1-8.
- A plan is at most 64 KB and 64 steps after expansion. Unknown keys are errors.
- `validate_plan` returns every problem at once.
- Voting reducers apply a quorum. Fewer than ⌈n/2⌉ non-null votes is `undecided`, never a pass, so a model that fails its schema cannot pass a finding by default. An omitted quorum is ⌈n/2⌉ of the voter slots, nulls included; a quorum the author writes is kept as written. A tie is `undecided`.
- Slice 1 ships the `adversarial_verify` pattern. Slice 2 adds `repeat` (loop-until-dry with a seen-set), `gate`, a one-level nested `plan`, `judge_panel`, `perspective_verify`, `completeness_critic` and `multi_modal_sweep`, under the same rules.

The honest cost of choosing data over a script:
- There is no arbitrary control flow or arithmetic; branches must be declared.
- Reshaping data beyond paths and the closed reducers costs an agent step.
- Error handling is "null plus a recorded reason".
- A new combinator is a reviewed contracts change.

**D-5 Structured output without provider support.**
- A step's `schema` uses a JSON-Schema subset: `type`, `properties`, `required`, `items`, `enum`, `minItems`, `maxItems`, `maxLength` and `additionalProperties`. Any other keyword is a validation error.
- For a step with a schema, the broker replaces the agent's `final_output` with a copy of the same name whose `result` parameter *is* the schema. The existing terminal channel then ends the turn unchanged.
- If the model answers in text instead, the reply is parsed for one JSON object. The in-tree precedent is `runtime/agent_executor.py:27`, `:141-146`.
- An invalid result gets exactly one stateless repair call through the `Client`, quoting the validation errors.
- If the result is still invalid, the agent's result is `null` with a recorded reason (`schema_invalid: …`). Nothing is fabricated.
- The stdlib validator is the authority. `response_format` is not used; slice 9 may add it as an optimisation where a route honours it.

**D-6 Per-agent governance: an inner agent is never broader than the lead.**
- **Forked permission managers.** `PermissionManager.fork_for_agent(label, allow=…, run_id=…)` gives each agent its own manager object:
  - its own taint registry, which removes the shared-manager race;
  - an allowlist that only narrows (a fork of a fork intersects the two);
  - the parent's *current* mode, approvals, trust checker, escalation check and durable recorder, all shared by reference;
  - an approval callback that prefixes the description with `[agent <label> · plan <run_id>]`.
  Teamup role turns adopt the same fork (MD-7).
- **A fixed deny set.** `TEAM_AGENT_DENY` is never offered to a plan agent or a team role, whatever its allowlist says. It covers:
  - plan and team tools: `team_plan_*`, `teamup_start`, `teamup_run`;
  - loop control: `research_loop_start`, `research_loop_stop`, `research_loop_decide`, `research_session_create`;
  - decisions: `knowledge_graph_decide`, `workflow_approve_gate`;
  - platform trust and allocation: `mcp_register_server`, `mcp_unregister_server`, `endpoint_provision`, `endpoint_stop`, `endpoint_upgrade`, `pool_up`, `pool_down`, `source_add`, `source_use`, `source_remove`, `workflow_upload`, `hpc_submit`;
  - `ask_user`.
- **Default tools and writers.** A profile's default tools are the read set: `file_read`, `grep_search`, `glob_search`, `list_dir`. A *writer* is a tool that writes the local filesystem or starts a local process: `file_write`, `file_edit`, `apply_patch`, `shell_exec`, `python_exec`, `compile_code`, `run_code`, `test_code`, `test_fix_loop`, `project_scaffold`, `file_download_platform`, `start_language_servers`, `project_use` and `project_ensure` (`PARALLEL_UNSAFE_TOOLS`). A profile with a writer is refused inside `map` or `fanout` until workspace isolation lands (slice 5). A sequential agent step may write, through the permission ladder.
- **The CLI fails closed.** With no host manager, the broker uses `PermissionManager(AUTO_SAFE)` with no approval callback. Safe tools run; risky ones are refused fail-closed and named in the result.
- **Taint (ADR-039) follows the data.**
  - Each agent's tainted excerpts are exported with its result.
  - They are imported into every downstream agent whose prompt references that output.
  - `team_plan_run` and `team_plan_result` become `generic` taint producers.
  - The run's excerpts are imported into the lead's registry when a result is returned, so plan output cannot launder untrusted content into the lead's tool arguments.
- **Models.**
  - Models are named by catalog or source name only (ADR-068); URL-shaped values are refused.
  - Every distinct model is preflighted through the `Client`'s ADR-062 gate before any spend. A public route for a sensitive project is therefore refused by name at submit time, not mid-run.

**D-7 Budgets are hard ceilings, measured per model round.**
- `budget.max_tokens` is required.
- The broker gives each companion a `usage_callback` that charges input plus output tokens to the run's meter. When the run was started from a chat session, the same callback charges that session's usage records and LLM-call caps, under a lock.
- The broker also gives each companion a `pre_llm_call` that refuses the next round once any ceiling is reached: tokens, agents, wall clock or tool calls.
- The engine checks the same ceilings before starting each agent.
- `BUDGET_WARNING` is emitted at 80%.
- Measured usage is kept per thread, so parallel agents on one `Client` no longer swap it.
- GPU hours are not a plan concept. Compute still goes through UMRS jobs and ADR-067's caps.

**D-8 Journal and resume: attempt-local, content-addressed, honestly labelled.**
- Every agent call appends one record to `team_runs/<run_id>/journal.jsonl`.
- The record's key is the hash of: node path, item key, replica, rendered prompt, instructions, schema, model and tool set.
- `resume` starts attempt N+1 in the same run directory. It replays, at zero model cost, every `ok` record whose key still matches. Changed calls, failed calls and calls invalidated by `--from-phase` run live.
- This is a memo of results, not a scheduler. It has no queue, no claiming and no retry state machine, and every result labels it `journal: local-cache`.
- The durable home is UMRS, in slice 7 (MD-4).
- If the maintainer reads the local journal as a second job store (CONSTITUTION §6), resume is withdrawn and the journal stays a transcript only (MD-3).

**D-9 The lead agent stays in the loop.** The chat agent is the lead (ADR-075 D-1: the chat session is the work session). It gets three tools:
- `team_plan_templates` (safe).
- `team_plan_run` (RISKY). Its approval card shows the expanded phases, the agent bound, each profile's tools and model, and the budget. The tool validates, runs, and waits up to `wait_sec`. It returns a bounded structured result:
  - the declared outputs, up to 12 KB;
  - per-step ok and null counts, with null reasons;
  - budget spent and remaining;
  - the run id and the result path.
- `team_plan_result` (safe). It polls a running plan, or slices a finished result by path.

A builtin skill teaches the rhythm: run one plan per phase (understand → design → implement → review), read the result, then author the next plan. Plan agents never see these tools (D-6).

**D-10 Templates are package data; project templates are tested skills.**
- Built-in templates ship inside `r4/src/r4/teams/plan/templates/`.
- A user may run a plan file by path, and the lead may pass a plan inline. Neither is a registry.
- If saved project templates are wanted, they become UMRS skills with tests and human review (D-27's merge-on-green), never a client-side workflow registry (CONSTITUTION §6).

**D-11 No agent-authored code runs.**
- The plan is data interpreted by CI-gated code. Nothing in it is evaluated, imported or executed.
- So CONSTITUTION §2.1, ADR-074 D-2 and ADR-020 §14 are not triggered. A UMRS-hosted interpreter can later run the same JSON without bringing uploaded code into the UMRS trust domain.
- Agents' own `shell_exec` and `python_exec` calls keep today's workstation posture under the ladder. *(2026-10-30, [ADR-101](101-local-tool-sandbox.md): under the ladder, plus the local tool sandbox when the user has turned it on; the ladder itself is unchanged and nothing is auto-approved because it is sandboxed.)*
- A future *script* spelling, closest to ultracode's program form, has exactly one legal shape:
  - an UNTRUSTED SPI engine;
  - in a no-network sidecar under the ADR-074 D-4 posture;
  - emitting the same journalled agent calls and lowering to the same plan IR.
  It needs its own ADR (MD-9).

**D-12 What does not change in slice 1.** Apart from the fixes listed in the rollout plan, these stay as they are:
- `AgentRole` and the teamup runtime;
- the native engine and its default status for roster specs;
- crews;
- the 15 `teamup.*` bridge RPCs and the `teamup_*` tools;
- persisted `teamup_sessions/`;
- LangGraph.

### Why

- **The request is about shape, not a new mechanism.** A plan reproduces ultracode's primitives (agent, parallel, pipeline, phase, log, budget, resume and quality patterns) as data the lead writes, behind the one seam ADR-072 named.
- **Data, not a script.** The only legal home for agent-authored code is a sandbox the workstation does not have. *(2026-10-30: still true. The workstation now has [ADR-101](101-local-tool-sandbox.md)'s opt-in local tool sandbox, but it is not the ADR-074 D-4 posture (it is off by default, and hatch runtimes keep host networking), so D-11 stands and plan scripts stay forbidden (MD-9).)* Data also buys:
  - whole-plan validation before any spend;
  - a computable agent bound;
  - deterministic replay;
  - safe authoring by the lead agent;
  - portability to a UMRS interpreter.
- **A new compile target rather than a teamup rewrite.** Teamup's state model *is* the HPC coupling: four keyed slots, the ChangeLog, HPC context, and a `session.json` that other binaries read. Rewriting it in place would break persisted sessions and U-04's byte-compatibility for nothing the plan engine does not already give. Native keeps its users while crews migrate as templates.
- **In R4, not UMRS.** CONSTITUTION §1.3 assigns multi-agent teams and the agent harness to R4, and tool-using agent turns exist only in R4 today. UMRS durability arrives through the same IR, as a later slice.
- **Per-agent forks before wider parallelism.** Widening fan-out over a shared manager would multiply the taint race and the anonymous approvals the code already has.

## Consequences

**Positive.**
- A team can have any number of free-form agents, each defined by its prompt.
- Fan-out follows the data, and pipelines run without barriers.
- Results are schema-validated objects the lead can act on, not 1200-character tails.
- Quality patterns are executed by code: votes are counted in Python, not judged by a model.
- Tokens, agents, wall clock and tool calls all have hard ceilings.
- Runs can be resumed.
- Taint, allowlists and labelled approvals are per agent.
- Several existing defects are fixed on the way (see the rollout plan).
- The design's four research roles become one template among many.

**Cost.**
- A second CORE engine and a closed grammar must be maintained. Every new combinator is a contracts change and a review.
- Small models, such as the stand-alone `llama3.2:3b` row, comply poorly with schemas. Expect nulls; the quorum turns them into `undecided` rather than passes.
- Every agent turn carries the full companion system prompt plus the R4 briefing, so a 37-agent review uses a lot of tokens.
- Plan runs are client-local daemon threads until slice 7, as teamup runs are today.
- Cancelling a chat turn does not reach a running plan until slice 3; the plan is bounded only by its own budget.
- In ask-mode sessions, every agent tool call asks (MD-6).
- Taint matching is by substring. A paraphrase escapes the fence across agent hops, just as it does within one agent today.

**NOT in scope.**
- Deleting teamup, the native engine, crews or `AgentRole`.
- The dormant HPC modules (D-37). They go in a separate cleanup PR (CONSTITUTION §6).
- Any UMRS change in slice 1.
- `response_format` on the client.
- Workspace isolation (slice 5).
- The TUI roster and new bridge RPCs (slice 3).
- Loop integration (slice 6).

## Alternatives Considered

- **Lead-written JavaScript on a CORE `script` engine, interpreted by QuickJS in a child process.** This is the closest match to ultracode: real control flow, arithmetic, `Promise.all`.
  - Rejected for three reasons:
    - It runs agent-authored code on the workstation outside a container. ADR-020 §14.1 reserves that for an UNTRUSTED sidecar, and CONSTITUTION §2.1 forbids adding such workloads.
    - It needs an unvetted native wheel that is not in `uv.lock`.
    - Autopilot would fire programs that no human has read.
  - Its legal shape is recorded in D-11.
  - Kept from it:
    - the `ClientModelBroker._account` fix;
    - chaining agent usage into the session's caps;
    - lead-side taint absorption;
    - real tool-call events;
    - `--from-phase`;
    - templates as tested skills.

- **Evolve native teamup in place: open roster ids, DAG waves, and a `team_dispatch` tool for an orchestrator agent.** This is the strongest option on the letter of §1.6, since it adds no new engine.
  - Rejected for three reasons:
    - Orchestration stays model-driven. There is no deterministic replay, and prompt injection can steer fan-out.
    - Its slice 1 would ship neither dispatch nor patterns.
    - It rewrites `session.py`, `native.py` and the persisted `session.json` schema in place. That breaks forward compatibility and conflicts with U-04's byte-compatibility.
  - Kept from it:
    - the per-agent permission view;
    - a deny set that covers MCP registration;
    - labelled approvals with a locked counter;
    - refusing a model at submit time;
    - the `AgentKey(str)` compatibility read for when `AgentRole` is retired (slice 4).

- **Plans hosted by UMRS, as a third engine kind under the one loop driver (`EngineLoopRunner`), journalled in `execution_traces`.** This has the best durability.
  - Rejected for now, for four reasons:
    - It moves team orchestration and agent-harness logic into the UMRS process, against the CONSTITUTION §1.3 ownership table.
    - Its slice-1 agents would be LLM-only, with no tools.
    - Its ADR-062 gate would rest on registry metadata that no code reads today.
    - Thread pools would run inside the multi-tenant API process.
  - Kept from it:
    - the quorum rule;
    - the untrusted output envelope;
    - retrieval provenance on journal records (slice 2);
    - registering a `cost` action kind before any loop use;
    - the honest `journal:` label;
    - `execution_traces` as a candidate durable home (MD-4).

- **Ship the engine as an external package, `engines/r4-engine-plan`.** This would satisfy ADR-072 D-1 literally.
  - Rejected for slice 1. A new monorepo package needs CONSTITUTION §5.2 review, and an optional install would leave the chat tools unusable by default.
  - The import fence keeps a later extraction to a file move.

- **Expose `ResearchCompanion.spawn_subagent` as an in-turn tool.**
  - ADR-072 classifies it as in-turn delegation.
  - Rejected: it has no permission manager, no per-agent model, no schema, no budget and no journal. Once it fans out, it would become exactly the parallel path §1.6 forbids.

- **Provider `response_format`.**
  - Rejected: it is dropped on provider routes today, and route-by-route support is unverified.
  - Kept as a slice-9 optimisation only.

- **A `.r4/plans/` directory of project templates.**
  - Rejected: it reads as a client-side workflow registry (§6).
  - Replaced by D-10.

## Open for the maintainer

- **MD-1** Accept this ADR, which amends ADR-072 D-1 and ADR-075 D-3. Until it is accepted, slice 1 stays on a feature branch. Alternatively it merges opt-in under Proposed, as ADR-090's waves did; that is the maintainer's call.
- **MD-2** The "do NOT invent a third DSL" warning (`plan-unified-work-engine-2026-09-30.md:105`). Is the plan IR the one IR that the roster and pipeline spellings compile into (slice 4)?
- **MD-3** The local journal. Is it an attempt-local cache, so resume is kept, or a second job store, so resume is withdrawn until UMRS?
- **MD-4** The durable home in slice 7. Two options: `research_loop_job` plus `execution_traces` keyed by input hash (no new table), or ADR-020 phase 2's `team_runs` table.
- **MD-5** `team_plan_run` under autopilot. Should it be RISKY and fire, as `teamup_start` does, or always ask, given the cost of fan-out?
- **MD-6** Ask mode. Should forks inherit ASK_ALWAYS, so every agent read asks? Or should approving a plan card grant its listed *safe* tools to its agents for that run only?
- **MD-7** Applying `TEAM_AGENT_DENY` and the fork to existing teamup roles. This is a behaviour change: roles can no longer start nested teams or loops.
- **MD-8** Slice 4: freeze `AgentRole` to the HPC preset and compile crews to templates. This accepts that research-crew prompts and turn order change, against U-04's byte-compatible acceptance.
- **MD-9** Whether the script spelling in D-11 is ever wanted.

## Maintainer decisions (2026-10-11)

The maintainer accepted the agent's proposals for MD-1 to MD-9 on 2026-10-11. (The 2026-10-10 block says "MD-1..MD-11", but only nine were ever written.)

- **MD-1:** **Accepted.** The ADR-072 D-1 and ADR-075 D-3 amendments stand. Slice 1 merges, and the `plan` engine stays opt-in until slice 4 makes roster crews templates.
- **MD-2:** yes. The plan IR is the one IR that roster and pipeline spellings compile into (slice 4); no third DSL.
- **MD-3:** the local journal is an attempt-local cache, so resume is kept. The durable record is UMRS's (slice 7), and the journal becomes its cache.
- **MD-4:** the durable home is `research_loop_job` plus `execution_traces`, keyed by input hash. No `team_runs` table: CONSTITUTION §6 allows one job store.
- **MD-5:** `team_plan_run` always asks, under autopilot too. One approval can start a fan-out of model calls, so no permission mode auto-approves it. *(2026-11-05, [ADR-104](104-one-mode-one-permission-decision.md) amendment 2, maintainer "we need true autopilot mode": in autopilot `team_plan_run` and plan gates run, noted in the transcript; chat and plan still ask. A run nobody approved gets no MD-6 card grants.)*
- **MD-6:** forks do not inherit ASK_ALWAYS for reads. Approving a plan's card grants the card's listed *safe* tools to that run's agents for that run only. Risky and destructive tools still ask per call, and nothing persists past the run.
- **MD-7:** yes. `TEAM_AGENT_DENY`, the per-agent taint fork and the broker's usage and pre-call hooks apply to teamup role turns. Roles can no longer start nested teams or loops.
- **MD-8:** yes. Slice 4 freezes `AgentRole` to the HPC preset and compiles crews to templates, accepting that research-crew prompts and turn order change (U-04's byte-compatibility is waived).
- **MD-9:** no. The script spelling is not wanted, and D-11 (no agent-authored code runs) stands.

## Rollout plan

Each slice names the gap rows it advances. Every slice up to slice 4 is additive.

**Slice 1** (D-29 advances)
- Plan IR v1: `agent` with fanout, `map`, `reduce` and `adversarial_verify`.
- TeamSpec v2, `AgentBroker` and the `plan` engine.
- The role-turn broker, with the fork, the deny set, the typed `final_output` and budgets.
- Journal and resume.
- `r4 team plan …`, three chat tools and the skill.
- Fixes in passing:
  - `chat_once` dropping the model;
  - role-turn hooks and the deny set;
  - the shared taint race;
  - the bridge counter lock;
  - thread-local measured usage;
  - `ClientModelBroker._account`;
  - the meter and the event sink;
  - attempt ids;
  - resolver step 3;
  - the hard-coded project `cli`.

**Slice 2** (D-29)
- New step kinds:
  - `repeat` (loop-until-dry);
  - `gate` with ADR-066 classes, emitting `HUMAN_INPUT_REQUESTED`, never auto-approved;
  - nested `plan`.
- New patterns:
  - `judge_panel` (a near-tie raises competing_hypotheses);
  - `perspective_verify`;
  - `completeness_critic`;
  - `multi_modal_sweep`.
- K3 project knowledge:
  - project-scoped, with no `graphs=`;
  - numbered passages;
  - retrieval refs in the journal;
  - `rag_chunk` taint.
- A `research-round` template. Its prompts are referenced from `research_loop/team.py`, and it warns when the reviewer model equals the producer model.
- `ApprovalRequest.agent`.
- SPI conformance for the plan engine, with a roster spec as the degenerate plan.

**Slice 3: surfaces** (D-29)
- Bridge `team.plan_*` RPCs, and `team_event` keyed by agent id.
- The Work pane lists TeamService runs.
- An `agent_result` handler.
- `/teamup on plan:<template>` through `wave-intercepts.ts` (`slash.ts` stays frozen).
- Cancelling a chat turn reaches a running plan.
- `bootstrap_team` and bridge `teamup.start`/`teamup.create` move onto TeamService. This closes the ADR-072 bypasses at `bootstrap.py:117-155` and `bridge_server.py:3611-3614`.

**Slice 4: crews become templates** (D-29, D-37)
- research, code, paper-writing, kakenhi-report and peer-review become templates.
- `crew` becomes an alias for its template.
- `AgentRole` is frozen to the HPC preset, with an `AgentKey` compatibility read.
- HPC scaffolding is scoped to that preset.
- The dead BudgetTracker and SOTA stop conditions are wired or retired.
- The `generate_report` crash is fixed.
- The dormant modules are deleted in a separate cleanup PR.

**Slice 5: workspace isolation** (D-29)
- A per-agent workspace ContextVar, honoured by the shell, python and file tools.
- `isolation: worktree|copy`.
- Parallel writers are allowed only when isolated.
- An approved merge-back step.

**Slice 6: the D-29 tail, loop integration** (D-29 tail, D-32, D-33, D-36)
- A plan as the per-iteration executor (`runtime/api.py:224`; U-03).
- A plan-run action kind classed `cost` in `ask_policy` before any loop use.
- Gates go to the ADR-066 inbox.
- The ADR-067 wall clock is passed down.
- Reviewer gating feeds proposed-only findings and Catalog candidates (U-10).

**Slice 7: durable records in UMRS** (D-29)
- Durable records per MD-4.
- Resume works from any venue.
- The local run directory becomes a cache.
- Checkpoint scope becomes PORTABLE (ADR-020 §15.1).

**Slice 8: server venue** (D-29, D-63)
- A UMRS-hosted interpreter that imports `plan.py`.
- Agent steps run in a tool-executing agent session inside mini-RiVault. This needs the agentic `companion_turn_executor` venue, the D-63 registry route and the ADR-068 `/umrs/llm` lane.

**Slice 9: native convergence** (D-29)
- Native role turns use the AgentBroker.
- BudgetTracker folds into BudgetMeter.
- Provider `response_format` is added as an optimisation only.

## Rollout-status (2026-10-09)

Nothing has landed yet. The ADR is Proposed. Slice 1 is planned as an opt-in, additive wave on a feature branch, and its merge waits on MD-1. Each later wave appends its own dated block here and does not rewrite this one.

## Related

- [ADR-020](020-team-engine-spi.md), [ADR-039](039-untrusted-content-provenance-taint.md), [ADR-062](062-model-locality-data-class.md), [ADR-066](066-ask-scientist-policy-pause-profile.md), [ADR-067](067-loop-budgets-per-project-caps.md), [ADR-068](068-one-internal-inference-api.md), [ADR-072](072-one-team-seam-research-roles.md), [ADR-074](074-uploaded-step-code-runs-sandboxed.md), [ADR-075](075-one-work-engine-venue-is-user-choice.md), [ADR-087](087-harness-model-call-contract.md), [ADR-090](090-standalone-single-host-deployment.md)
- Plan: plan-unified-work-engine-2026-09-30.md (U-04, §4)
- [.github/instructions/agent-safety.instructions.md](../../.github/instructions/agent-safety.instructions.md), [.github/instructions/living-documentation.instructions.md](../../.github/instructions/living-documentation.instructions.md)
- Gap row D-29 in [R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md)

## Rollout-status (2026-10-10) — slice 1 landed on `feat/adr-092-team-plans` (not merged; MD-1)

**What exists.**
- **Contracts.** `r4_contracts.team_engine.plan` and `plan_patterns`: the grammar, `prepare_plan`,
  the closed reducers, the schema subset, `adversarial_verify` and `TEAM_AGENT_DENY` (moved here
  from permissions). TeamSpec v2 adds `WorkflowStyle.PLAN` and `TeamSpec.plan`. The broker DTOs
  are `AgentTurnRequest` and `AgentTurnResult`.
- **SPI.** `AgentBroker` and an optional `RuntimeServices.agents` (SPI_VERSION unchanged).
  `MemoryAgentBroker` is the test double.
- **r4.**
  - The `plan` engine: `r4/src/r4/teams/plan/` (engine, run, journal, templates, summary), with
    the `adversarial-review` template.
  - `RoleTurnAgentBroker` (`r4/src/r4/teams/agent_broker.py`).
  - `r4 team plan list|show|validate|run|resume|result`.
  - The chat tools `team_plan_templates`, `team_plan_run` (RISKY) and `team_plan_result`, plus
    the builtin `team_plans` skill.
  - The resolver's capability match, which picks a builtin CORE engine only.
- **Fixes in passing:**
  - `chat_once_factory` dropped the per-role model;
  - the shared taint-registry race (per-agent forks);
  - the bridge approval counters;
  - thread-local stream usage;
  - `ClientModelBroker._account` on the Usage dataclass;
  - FileEventSink sequence continuity;
  - attempt ids;
  - the hard-coded project `cli`;
  - an explicit null for an optional tool parameter now means absent (a 3B model's `file_read`
    nulls failed every read);
  - only the ADR-062 gate's own refusal (`ModelRouteRefusedError`) is reported as
    `model_refused`.

**Review.** An adversarial review (4 lenses, 2 skeptics per finding) produced 49 findings: 36 were
kept by both skeptics, 5 split and 8 refuted. Every kept and split finding is fixed, each with a
regression test that fails on the old code. They include:
- the approval card and `--dry-run` now list the effective model and tools per agent step;
- the card validates through the same path as the tool;
- the per-round guard enforces wall clock, run-wide tool calls and a hard `max_tool_rounds` cap;
- the writer set is widened (D-6 above);
- the omitted majority quorum (D-4 above);
- taint through map-stage reduces, plus `taint_truncated`;
- Ctrl-C cancels in-flight agents;
- an unresolved `map.over` is a named null;
- refused tools are named in results;
- torn journal lines;
- resume hashes stay stable;
- the default model is preflighted and pinned into the spec, so unnamed-model journal keys name
  the model that ran;
- the session usage counters take one lock;
- `--project` must name the active project, the project the ADR-062 gate checks.

Tests: 378 functions in 18 new files, plus extended suites. 1,138 pass across every team suite.

**Live (stand-alone, `llama3.2:3b`).**
- `adversarial-review` resolved to `plan` by capability match, project `default`, bound 7. The
  finder's answer failed the schema (severities `error`/`warning` outside the enum, and the
  findings were not real defects): a named null, `failed`, exit 1, 11k tokens. This is a model
  capability fact, recorded rather than worked around.
- A two-reader fan-out plus merge plan ran `success` (17.7k tokens). Resume replayed all three
  calls at zero tokens in 1 s, and `--from-phase merge` re-ran only the merger.
- The T2 leg `test_a_team_plan_runs_and_resumes` passes.
- One merger once failed with an internal `list index out of range` after 9 tool rounds; it did
  not recur in 5 reruns. Tracebacks are now logged, so a recurrence is diagnosable.

**Open, for the maintainer (besides MD-1..MD-11):**
- `test_fix_loop` and `search_agentic` call the model outside the broker's meter: meter them, or
  add `test_fix_loop` to the deny set (a D-6 list change).
- MCP tools that write are invisible to the static writer set.
- `TaintRegistry` excerpt sampling yields one excerpt per result.
- Teamup role turns still skip `usage_callback`/`pre_llm_call` (only the deny set and fork were
  applied, MD-7).
- `team_plan_cancel` is declared but not built (slice 3).
- The `plan` entry point needs `uv pip install -e r4` to appear in installed metadata; the
  builtin registration covers it until then.


## Rollout-status (2026-10-11) — maintainer decisions, slice-1 open items closed

The maintainer accepted MD-5, MD-6 and MD-7 on 2026-10-11. Built on `wip/a92` (not merged; MD-1
still open). Earlier blocks are not rewritten; where this block and an earlier one differ, this
one is current.

**MD-5: `team_plan_run` always asks.**
- `team_plan_run` is in `NEVER_AUTO_APPROVE` (`r4/src/r4/companion/permissions.py`), the
  mechanism `knowledge_graph_decide` and `research_loop_decide` already use. No mode
  (autopilot = TRUST included), session approval or remembered pair fires it.
- Its card opens with its own fence line (`_NEVER_AUTO_REASONS`), and the bridge and ACP report
  it as `risk_tier: never_auto`.
- `teamup_start` is unchanged: RISKY, and it still fires under autopilot.

**MD-6: an approved card grants its safe tools, for that run only.** This replaces the
Consequences line "every agent tool call asks".
- A human yes to a `team_plan_run` card leaves a one-shot token on the lead's manager
  (`take_card_approval`, keyed by the card's arguments, `wait_sec` excluded, ten-minute TTL).
- The executor consumes it (`_PlanTools._card_grants`). The grant is the card's listed SAFE tools
  (`card_safe_tools`, from `effective_agents`). It flows through
  `TeamService(run_grants=…)` and `RoleTurnAgentBroker(run_grants=…)` to every fork
  (`fork_for_agent(grants=…)`).
- A fork drops any non-SAFE grant and never widens its parent's. The grant removes only the
  per-call ask of the lead's mode; every other layer still decides, most restrictive wins
  (maintainer, 2026-10-11). These layers are the never-auto, taint and credential fences, DENY,
  and in TRUST the server allowlist (ADR-035): a granted tool the allowlist does not list asks
  exactly as it would without the grant (`_AgentPermissionFork._granted`).
- A grant record is written only when the approval callback is asked and says yes, and only for
  a tool that is also in `NEVER_AUTO_APPROVE`. So no TRUST, AUTO_SAFE, session or remembered
  approval can mint one. An auto-approving callback is kept as is; this guarantee is pinned by
  `test_no_auto_approval_path_resolves_a_plan_card`.
- Nothing is written to the shared approvals. A later run, a run nobody approved (CLI), and
  the lead session all ask as their mode says.
- The card now says what approving grants (`_grant_lines`).

**MD-7: team roles get the plan agents' hooks.** The deny set and the fork were already applied
on 2026-10-10.
- `run_role_turn` now runs every role turn on `_RoleTurnHooks` (`r4/src/r4/teamup/role_agent.py`):
  - `usage_callback` charges the host chat session's `record_usage`;
  - `pre_llm_call` preflights the role's model through the ADR-062 gate once, then asks the
    session's `guard_llm_call` before every round.
- Both run under the session lock the plan brokers take (`session_hook_lock`, now public in
  `agent_broker.py`).
- The hooks reach the team through `TeamupSession._usage_hooks` (runtime only, never
  serialised). Three places set it:
  - `teamup_start` and `teamup_run` (`companion/tools.py`);
  - the bridge's `_compose_teamup_live_callbacks` (`_thread_host_usage_hooks`).
- With no session (the CLI, the native engine's chat-once factory) nothing is charged, as before.

**Open items of 2026-10-10, closed.**
- *`test_fix_loop` and `search_agentic` outside the meter.* These are now metered, not denied.
  - The agent's catalog is built on `MeteredToolClient` (`r4/src/r4/teams/tool_metering.py`).
    Its `chat`, `chat_completion` and `chat_stream` ask the run's ceilings and the session's
    caps first, then charge the turn, the run's meter and the session (call type
    `subagent_tool`).
  - These calls are not rounds, so `max_tool_rounds` is not spent. A refusal surfaces as the
    tool's own failure.
  - Role turns with a session use the same wrapper.
- *MCP writers invisible to the static set.* `is_writer_tool` (`r4_contracts/team_engine/plan.py`)
  classifies an MCP tool by its declared capabilities:
  - A tool counts as a reader only if every declared flag is in `READ_ONLY_TOOL_CAPABILITIES`.
    The flags are r4_mcp `ToolCapability` names, the ones a Hatch manifest declares and the
    custom runner enforces.
  - An undeclared MCP tool is a writer (fail closed), and `mcp_invoke` always is.
  - `validate_plan`/`prepare_plan`/`spec_from_plan` take `tool_capabilities`. The declarations
    ride in `spec.context` and `resolution.json`, so the engine's own `validate` and a resume
    judge the plan the same way.
  - The chat tool reads them from `client.mcp.list_tools()` (`mcp_tool_capabilities`;
    `MCPTool.capabilities` from `required_capabilities`).
  - The UMRS registry does not publish capabilities yet, so today every MCP tool is a writer in
    a map or fan-out. Publishing them is a UMRS change, out of scope here. The CLI validates
    with no declarations.
- *One taint excerpt per result.* ADR-093 D-10 had already split paragraphs and lines. A result
  that was one long paragraph still gave one excerpt.
  - `_excerpts_of` (`companion/taint.py`) now adds every later sentence of a paragraph, then
    120-character windows across a long one.
  - Both are capped at `MAX_EXCERPTS_PER_RESULT` (32), in priority order after paragraphs and
    lines.
- *`team_plan_cancel` not built.* It is built (`companion/team_plan_tools.py`): RISKY, and in
  `TEAM_AGENT_DENY`.
  - It cancels a run this process started, through the engine's own `cancel` on the run's loop.
    A cancel that arrives before the handle exists is applied at start.
  - Agents in flight get the cancellation and are journalled as `cancelled` nulls; the rest
    never start. `result.json` records `cancelled` and the reason.
  - Resume continues the run by D-8: ok calls replay, cancelled ones run live.
  - A finished run, or one this process did not start, is not touched.
  - The skill and the tool set list it.
- *`plan` entry point and installed metadata.* No install was run. The builtin registration is
  pinned with entry-point discovery empty: listed, labelled `builtin`, picked by capability
  match, instantiable. The `pyproject.toml` declaration is pinned too. The entry point appears
  after the next `uv pip install -e r4`.

**Tests.** New: `r4/tests/test_team_plan_decisions.py`. Extended:
- `test_team_plan_tools.py`;
- `test_team_resolver.py`;
- `test_companion/test_taint.py`;
- `r4-contracts/tests/test_team_plan.py`.

Each change was checked against a targeted revert of its code; every revert fails its tests.

Changed pins, and why:
- `_cards` in `test_team_plan_tools.py` reads the card body under the MD-5 fence line.
- `PLAN_TOOLS` lists `team_plan_cancel`.
- The resume-rebuild pin includes `tool_capabilities`.

## Rollout-status (2026-10-11) — slice 2

Built on `wip/a92` (not merged). The ADR was accepted on 2026-10-11 (MD-1 to MD-9; that record is commit `93d57492` on `feat/adr-093-retrieval`, not on this branch). Earlier blocks are not rewritten; where they differ, this block is current. Live LLM runs were not available for this wave: every run below is on scripted turns (`MemoryAgentBroker`, fake companions).

**New step kinds** (`r4-contracts/src/r4_contracts/team_engine/plan.py`, engine `r4/src/r4/teams/plan/run.py`).
- `repeat` (loop-until-dry).
  - Keys: `max_iterations` 1-8, `steps` 1-8 (`agent`, `map`, `reduce` only), `collect`, optional `seed` and `key`.
  - The body runs again until the step `collect` names returns nothing outside the seen-set (`key` is an item's identity), or `max_iterations` runs out.
  - The body sees `{{seen}}` (untrusted envelope, `UNTRUSTED_ROOTS`) and `{{iteration}}`. Its step ids exist only inside the body.
  - Output: `{items (new only), iterations, dry, stopped}`. A collect that is null stops the loop as `repeat_stopped:collect_null`.
  - Bound: `max_iterations × body`. Body steps count toward the 64-step limit (`count_steps`).
  - Each iteration runs in its own frame (`_Frame`). Journal nodes are `<repeat>.iterations[<n>].<step>` and labels `<label>@<n>`. The `seen` taint is imported into body agents that read it.
- `gate` (ADR-066 classes `cost`, `competing_hypotheses`, `physical_action`, `direction_change`; `GATE_CLASSES`).
  - Keys: `class`, `prompt`, optional `when` (a path; an empty value means the gate is not raised).
  - The engine emits `human_input_requested` and asks `AgentBroker.ask_gate` (new in the SPI; the default refuses).
  - Only a human answer passes a gate (`GateDecision.answered`). A no, or a refusal, stops the run `cancelled` with `gate_rejected:<gate>` or `gate_refused:<gate>:<why>`, and every later agent is a `halted_by_gate` null.
  - A gate is refused, not asked, once a budget ceiling is spent. Its wait is not charged to `max_wallclock_sec`, neither in the engine (`_budget_elapsed`) nor in the broker's round guard (`_RunLimits.paused`).
  - D-8: a human answer is journalled (`kind: gate`, keyed by gate path, class and rendered prompt), so a resume replays it and does not ask again. A refusal is not an answer and is asked again. A changed question asks again. `--from-phase` drops a recorded answer.
  - `result.json` gains `gates`; `RunMetrics.human_interventions` counts live answers. The descriptor now says `supports_human_in_loop`.
- Nested `plan`.
  - Keys: `template`, `args`.
  - The template is loaded, bound with static args (a string may read `{{params.x}}`; anything else is `dynamic_arg`), expanded and validated on its own, then inlined as the step's `program` (`plan_patterns._expand_plan`).
  - One level deep (`MAX_PLAN_DEPTH`, `nested_too_deep`).
  - Its profiles join the parent's as `<step>__<name>`. Its budget and concurrency are dropped: the parent's govern the one run. Its bound and steps count toward the parent's limits.
  - It runs in its own frame: its params and steps, labels `<step>/<label>`, journal nodes `<step>/<node>`, the parent's phase for `--from-phase`.
- Reducers `mean` (with the quorum rule), `rank` (`margin`; a near-tie is `competing_hypotheses`, never picked) and `subtract`. A reduce may take `inputs: [paths]`.
- Walks over every agent part (`iter_agent_parts`). The model preflight (`plan_models`), the card and dry run (`effective_agents`, a new `iterations` column), the card's grant list and the model warnings now see agents inside repeats and inlined templates. The card lists gates and warnings (`_gate_lines`, `_warning_lines`).

**Patterns** (`plan_patterns.py`; each is defined in the module docstring and expands to closed IR).
- `judge_panel`: N judges score candidates 0-10, `mean` per candidate under a quorum, then `rank`. A near-tie within `margin` raises a `competing_hypotheses` gate (`<id>__tie`, `when` the tie list is non-empty) instead of picking; `on_tie: report` only lists the tied candidates.
- `perspective_verify`: the adversarial vote with one verifier per named perspective (a `values` fan-out); the prompt must read `{{perspective}}`.
- `completeness_critic`: a `repeat` seeded with the current result. Critics list what is missing until a round is dry.
- `multi_modal_sweep`: one agent step per mode (a profile, a model or a prompt), then a `concat` over `inputs`, deduplicated by `key`. The modes run one after another.

**K3 project knowledge for agents** (`r4/src/r4/teams/project_knowledge.py`, `RoleTurnAgentBroker._scoped`).
- A profile listing `knowledge_query` gets the companion's own tool, already sized by the turn's reader (ADR-093 D-14). No new client path.
- The agent sees one parameter, `query`. Any `graphs=`, `project_id` or `discovery` argument is ignored.
- The read runs over the run's project's own graphs, with discovery off. A project whose graphs cannot be listed is refused, never widened.
- Hits come back as numbered passages through `context_pack.build_pack`.
- Each read is recorded with its query, project, labels and `source_id`s (`AgentTurnResult.retrieval`). The engine journals it with the call (`retrieval` on the journal record).
- The tool keeps its name, so its result is registered as `rag_chunk` taint (`TOOL_TAINT_CLASSES`) and exported downstream.
- Classification checked: `knowledge_query` is in `SAFE_TOOLS` (a card grants it, MD-6), outside `TEAM_AGENT_DENY` and not a writer.

**`research-round` template** (`r4/src/r4/teams/plan/templates/research-round.json`).
- Planner hypotheses, then experimenter designs, then analyst assessments, then a reviewer `judge_panel` that gates a near-tie. Bound 16 at the defaults.
- The prompts are references (`{"ref": "research_role:PR"}`), resolved from `research_loop/team.py` by `r4/src/r4/teams/plan_sources.py` (outside the plan package's import fence). An unresolved reference fails validation.
- Profiles may declare `reviews: [profiles]`. `independence_warnings` warns, on the card, in the dry run and in `run_started`, when a reviewer runs on the model of a profile it reviews; two unnamed models count as the same default.

**`ApprovalRequest.agent`.**
- An agent fork sets `permissions.approving_agent()` around its labelled callback.
- The bridge (canonical `ApprovalRequest.agent` and the legacy `approval_request.agent`) and ACP (`session/toolApproval.agent`) report it; it is `""` for the lead's own calls.
- Contract artifacts regenerated: `contracts/control/v1/approval_request.schema.json` and its fixtures.

**Gates' human lane.**
- `team_plan_gate` is the gate's approval: in `NEVER_AUTO_APPROVE` (reason in `_NEVER_AUTO_REASONS`), RISKY, refused in any profile (`ENGINE_ONLY_TOOLS`), never offered to an agent.
- In chat the lead's approval callback answers it, through a fork labelled `gate <path>`.
- `r4 team plan run` asks on the terminal when stdin and stdout are TTYs, through its own `ASK_ALWAYS` lane (`_gate_lane`) that agents never fork. Without a terminal, gates refuse by name.

**SPI conformance.**
- `tests/test_team_spi_conformance.py` runs `PlanEngine` through the whole suite.
- The roster spec reaches it as the degenerate plan (`plan_from_roster` / `spec_from_roster`: one agent step per roster agent, in order, each reading the earlier answers). The engine still declares only `WorkflowStyle.PLAN` (D-2).

**Tests.**
- New: `r4-contracts/tests/test_team_plan_slice2.py` (57), `r4/tests/test_plan_slice2_engine.py` (31), `r4/tests/test_plan_slice2_broker.py` (21). One new ACP test. The conformance suite now covers the plan engine.
- Changed pins, each because slice 2 widens what slice 1 pinned as absent:
  - the pattern set;
  - `UNTRUSTED_ROOTS` (adds `seen`);
  - `supports_human_in_loop`;
  - the `iterations` column of `effective_agents`.
- Mutation check: 35 targeted reverts, one per guarantee, each killed by its test. They cover the never-auto rule, the gate journal, replay of human answers only, the halt, wall-clock pausing, budget refusal, `when`, the dry and collect-null stops, the `seen` envelope, every bound, depth, static args, the near-tie, the quorum, each pattern's shape, project scope, the one-argument face, the taint name, numbering, refs recorded and journalled, the agent on the bridge and ACP, independence warnings, prompt references, the preflight walk, the degenerate roster, engine-only tools and the CLI lane.

**Deferred.**
- Runtime args for a nested plan. Its `args` are static: data-dependent sub-plans need an untrusted-arg rule (a `params` value from an agent would render outside the envelope).
- Inbox routing of gates (slice 6).
- Parallel sweep modes. `multi_modal_sweep`'s modes run sequentially: the grammar has no parallel block.
- MCP declarations for templates. A nested template's MCP tools are judged with the declarations of the outer plan's profiles only; built-in templates list none.
- The TUI showing `ApprovalRequest.agent` (slice 3).

## Rollout-status (2026-10-11) — slice 3

Built on `wip/a92` (not merged). Earlier blocks are not rewritten; where they differ, this block is current. Live LLM runs were not available for this wave: the plan runs below are scripted (`MemoryAgentBroker`, fake companions), the console runs are bun tests against a fake bridge.

**Bridge RPCs** (`r4/src/r4/companion/ui/team_plan_bridge.py`, a mixin of the console bridge).
- `team.plan_templates`, `team.plan_run`, `team.plan_result` and `team.plan_cancel` run the session's own chat-tool executor (`team_plan_tools.executor_of(session.tools)`), so the card, the grants and the handles are the chat tool's.
- The chat tools' permission rules apply:
  - `team.plan_run` validates first (an invalid plan returns every problem and asks nobody), then asks the session's manager, which asks the human with the plan's card in every mode (MD-5). Only a human yes starts the run, and that yes grants the card's SAFE tools to that run's agents only (MD-6). It replies once the run has started (`wait_sec` is always 0 here); progress streams.
  - `team.plan_cancel` is the console user's own cancel (the Work pane's `c`): a human action, so it raises no approval card (maintainer decision, 2026-10-11). It cancels through the engine's own `cancel` with the reason "cancelled by the user from the console" (`_PlanTools.cancel_by_user`). The agent-called `team_plan_cancel` tool keeps its risky-tool rule and records "cancelled by the lead". No agent reaches the console path: it is an RPC of the console's JSONL protocol, not a tool; no code but that RPC calls `cancel_by_user` (pinned by a source scan); the tool's executor is the asking `cancel` and takes only `run_id` and `reason`; and the RPC refuses inside an agent's approval context (`approving_agent()`).
  - `team.plan_templates` and `team.plan_result` are safe and ask nobody.
- `team.plan_run` and `team.plan_cancel` answer from a worker thread (the bridge's deferred methods): a person may take minutes over a card, a cancel waits up to 30 s for the run to settle, and the stdin loop keeps serving meanwhile.
- `team.plan_run` takes `goal` with a `template`: the goal is bound to the template's first required string parameter that `args` does not set; a template with none left is a named problem (`team_plan_tools.args_with_goal`).
- A fifth RPC, `team.runs`, lists every TeamService run for the Work pane (below). It is not one of the four the rollout plan named; the pane needs a list.
- The P9 capability registry (`r4/src/r4/capabilities.py`) declares the five as `teamplans.*`, with the CLI spellings `team plan list|run|result` and no ACP spelling (ACP keeps the chat tools).

**`team_event` keyed by agent id** (`r4/src/r4/companion/team_plan_events.py`).
- Every plan run of the bridge's process, a run the lead started in chat included, is written as `{"team_event": {...}}` with `run_id`, `plan`, `kind`, `agent` and `emitted_at`.
- Kinds: `plan_started`, `phase`, `agent_started`, `agent_round`, `agent_tool`, `agent_result`, `agent_failed`, `agent_cancelled`, `gate_requested`, `gate_answered`, `budget_warning`, `budget_exhausted`, `plan_log`, `plan_finished`. Payloads are bounded; nothing the engine did not report is invented. Session-keyed teamup lines are unchanged.
- Rounds and tool calls come from the broker: `RoleTurnAgentBroker(observer=…)`, threaded through `TeamService(observer=…)`, is told `round` (tokens, model) and `tool` (call, then result) per agent. The terminal channel (`final_output`) is not a tool and is not reported. A failing observer or listener never fails a turn or a run.
- `AGENT_COMPLETED` now carries `preview`: the agent's answer, or a null's text, cut at 600 characters with a marker (`MAX_EVENT_PREVIEW_CHARS`). `result.json` and the journal stay the record.
- The bridge registers its listener in `main()` through a weak reference, so a discarded server stops listening.
- Contract artifacts: the bridge's `team_event` lines and RPCs are not part of the control schema (`contracts/control/v1/`), so no artifact changed. The canonical `ApprovalRequest` already carried `agent` and `risk_tier` (slice 2).

**The console** (`packages/r4-tui-v3`).
- The Work pane's teams section lists TeamService plan runs (`team.runs`, then the run-keyed lines) with status, agents ok/null and tokens, beside the team sessions, which now show their agents and budget (`TeamupSession.list_sessions` rows gain `roles`, `max_iterations`, `budget_used`, `budget_total`). On a plan row, Enter shows the result with one row per agent and `c` cancels.
- The `agent_result` handler (`handleAgentResult`, `stores/teams.ts`; pure logic in `lib/plan-live.ts`) keeps one notice per run whose rows are the per-agent results: ✓ an answer preview, ✖ a null with its reason and refused tools, ⊘ cancelled.
- Approval cards show `agent` (the plan agent or team role asking).
- `/teamup on plan:<template> [name=value …]` goes through `wave-intercepts.ts` (`slash.ts` untouched). The template is checked against `team.plan_templates` before anything is armed. The next chat message becomes its goal and goes to `team.plan_run`, whose reply waits up to 15 minutes for the human's answer to the card. `/teamup plans` lists the templates.
- Found in passing, an MD-5 hole on this surface: the bridge never told the console an approval was never-auto. The console's ask-once rule remembered a yes for `team_plan_run` for the session, and a project rule could answer it, so a second plan started with no human asked, and that automated yes minted a card grant. The legacy `approval_request` line now carries `risk_tier` and `session_approval_allowed: false` for never-auto tools. The console never remembers such a card and never answers it by rule; it says "never auto-approved — you answer this one each time".

**Cancelling a chat turn reaches a running plan.**
- `build_team_plan_tools(turn_cancel=…)` gets the session's accessor for the running turn's token (`CompanionSession._current_turn_cancel`, threaded through `get_builtin_tools`).
- A plan started in a turn registers on that token. When the user stops the turn, the run is cancelled through the engine's own `cancel` (`cancel_from_turn`), scheduled on the run's loop without blocking the canceller. Agents in flight are journalled as `cancelled` nulls, the rest never start, and `result.json` records `cancelled` with the reason "cancelled with the chat turn that started it". A token already set cancels the run at its start.
- A later turn's cancel never touches it. A plan started outside a turn (the console's `team.plan_run`, the CLI) has no turn to follow; `team.plan_cancel` and the Work pane's `c` cancel it.

**One team seam (ADR-072 bypasses closed).**
- `TeamService.open_team_session(config, chat_client=…, running=…)` takes the native engine from the registry, so the SPI check and the site policy apply: a site that disables `native` refuses interactive teams by name. The engine builds and persists the session (`NativeEngine.open_session`). The service hands it the lead's handles it holds: model, permission manager and usage hooks (MD-7).
- `bootstrap_team` (the `teamup_start` tool and the bridge's `teamup.start`), the bridge's `teamup.create`, `r4 teamup create` and the chat CLI's teamup mode (`cmd_chat_teamup`) all open teams through it.
- The session still runs iteration by iteration under its surface; D-12's teamup RPCs and persisted sessions are unchanged.
- `TeamService.list_runs()` lists plan runs (and other SPI runs) from the run root and teams from the native engine's session store. A run with neither a result nor a terminal event is `unknown`; the bridge overlays live progress.
- `r4/tests/test_team_seam.py` pins the seam by scanning `r4/src`: outside the engines' modules nothing calls `TeamupSession(...)`, `TeamupSession.create(...)`, `NativeRun(...)`, `PlanRun(...)`, `NativeEngine(...)` or `PlanEngine(...)`.
- The resolver is not consulted for interactive teams: only the native runtime has a per-iteration interactive mode, so the registry lookup is by name (the policy still applies). The maintainer accepted this on 2026-10-11.

**Tests.**
- New: `r4/tests/test_team_plan_surfaces.py` (33) and `r4/tests/test_team_seam.py` (9); `packages/r4-tui-v3/tests/team-plan-surfaces.test.tsx` (19).
- Changed pins, and why:
  - `test_tui_backend_audit_coverage.py` lists the five new bridge verbs;
  - `test_capability_parity.py` counts five more capabilities and three more CLI rows;
  - `test_teamup_start_from_chat_threads_the_session_usage_hooks` stubs the engine's session construction instead of `bootstrap_team`, because the hooks now reach the team inside `TeamService.open_team_session`.
- Mutation check: 36 targeted reverts (27 Python, 9 TypeScript), each killed by its test. The five added with the console-cancel decision cover: no card on the console cancel, the refusal in an agent's context, the tool keeping the asking cancel, the user recorded as the canceller, and the bridge using the console cancel. They cover the never-auto card on the RPC, validation before the card, the card's grant reaching the run it approved, the non-blocking reply, goal binding, the cancel's permission, binding to the turn, the engine cancel from the turn, the session's token accessor, the cancelled mapping, publishing, the observer and the terminal channel, the preview, the approval line's never-auto flag, bootstrap and `teamup.create` through the service, the site policy, the lead's hooks, terminal-event status, the live overlay, the weak listener, and in the console the never-auto card, the agent label, the template check, the armed send, the plan lane, the per-agent rows, the plan rows, `c` and the card timeout.

**Deferred.**
- Subcommand hints for `/teamup on plan:` and `/teamup plans` in the slash picker: they live in `slash.ts`, which stays frozen. The intercept handles both.
- Plan RPCs on the ACP rail: ACP keeps the chat tools only.
- A live run on a real model.

## Rollout-status (2026-10-11) — slice 4

Built on `wip/a92` (not merged). The maintainer's MD-2 (the plan IR is the one IR roster and pipeline spellings compile into) and MD-8 (freeze `AgentRole` to the HPC preset, compile crews to templates; U-04's byte-compatibility waived) govern this slice; that decisions record is commit `93d57492` on `feat/adr-093-retrieval`, not on this branch. Earlier blocks are not rewritten; where they differ, this block is current. No live LLM run: every run below is on scripted turns (`MemoryAgentBroker`) or fake companions behind the real `RoleTurnAgentBroker`.

**Crews are templates** (`r4/src/r4/teams/crews.py`, templates in `r4/src/r4/teams/plan/templates/`).
- research, code, paper-writing, kakenhi-report and peer-review are plan templates (package data, D-10). Each binds one free-text parameter (`objective`, `goal`, `subject` or `submission`) and validates through `prepare_plan` with a static bound: research 16, code 7, paper-writing 14, kakenhi-report 6, peer-review 5.
  - **research** is `research-round` (slice 2): its prompts are already references to `research_loop/team.py`. The crew pack's fifth phase, "decision", is the research loop's (continue / revise / publish); a bounded plan has no driver (D-3, ADR-075 D-2), so it is not a template step.
  - **code**: architect, then a `repeat` of implementer → tester → reviewer that stops when a review names no new defect (loop-until-dry; at most `rounds`, default 2). The implementer (`file_write`, `file_edit`, `apply_patch`) and tester (`shell_exec`) are sequential agent steps, so writers are allowed (D-6), each call under the ladder. Its four prompts are the code crew's own, moved verbatim.
  - **paper-writing**: editor → a drafter per section (map) → a verifier per draft (map) → copy editor. **kakenhi-report**: lead → three section writers (fan-out) → checker → the lead fits the form. **peer-review**: chair → three auditors (methods, results validity, reproducibility) → the chair's review. All read-only (`knowledge_query` and the read set).
- Prompts are one source: every crew profile's instructions are `{"ref": "crew_role:<crew>/<profile>"}`, resolved by `plan_sources` from `r4.teams.crews.CREW_ROLE_PROMPTS`, as `research-round` references `research_role:<RL|ED|DA|PR>`. paper-writing, kakenhi-report and peer-review had no prompts of their own (they ran the HPC preset's PM/SE/PG/CD prompts under their crew names); their prompts are new, written from each pack's own description, phases and deliverables.
- code, paper-writing and kakenhi-report declare `reviews` and a `reviewer_model` param, so the independence warning (slice 2) fires and can be answered.

**`crew` is an alias for its template; `hpc` is the HPC preset.**
- `r4.teams.crews`: `CREW_TEMPLATES` (research → `research-round`, the others by their own names), the packs' aliases (`paper`, `kaken`, `review`, `coding`, `science`, …), `crew_name`, `crew_template`, `list_crews`.
- Chat tool `teamup_start(crew=…)`: `hpc` opens the teamup session as before. Any other crew runs its template through the session's own plan executor (`_PlanTools.start_crew`): validated first (an invalid call spends nothing), then the lead's manager asks with the plan's `team_plan_run` card (never auto-approved, MD-5; with no manager nothing starts), and only a human yes starts the run, with the card's SAFE tools granted (MD-6). `models={"reviewer": m}` binds to the template's `reviewer_model`; a role the template has no model for is a named problem. `run=false` validates and describes.
- One approval for a template crew (maintainer, 2026-10-11). `teamup_start` raises no card of its own for a template crew: `PermissionManager.check` leaves the call to the plan's never-auto `team_plan_run` card (`plan_card_decides`, `PLAN_CARD_DELEGATES`), which the executor asks in every mode, autopilot included (MD-5), with its MD-6 grant list. No yes, no run; with no plan executor or no human, nothing starts; `run=false` only validates. The gate and the executor read the crew through one function (`r4.teams.crews.plan_crew_of`, default `research`), so the waived calls are exactly the ones run as the validated template plan; `hpc` and unknown crews keep `teamup_start`'s own rule (RISKY; autopilot fires `hpc`). DENY mode refuses before the waiver, and a plan agent's fork refuses `teamup_start` (`TEAM_AGENT_DENY`) before any rung. Pinned in `test_crew_templates.py` (ask mode and autopilot each show exactly one card, the plan's; hpc unchanged; DENY and forks; gate/executor agreement over ten crew spellings); 7 more targeted reverts, each killed.
- Bridge `teamup.start`: the same executor on the bootstrap worker thread; a started plan emits `team_event` `crew_plan_started`, a refusal `team_start_error`. `teamup.crews` lists every crew with its template and aliases.
- CLI: `r4 team create NAME --crew <plan crew>` shows the plan (the dry run) and runs it with `--start`, as `r4 team plan run` does (fail-closed agents). `r4 team launch --team research` builds the `research-round` plan spec, which the resolver hands to the plan engine; the research roles no longer re-skin the four slots on native. `r4 team crews` lists crews and templates.
- Console: `/teamup on <crew>` (default `research`) resolves the crew or alias through `teamup.crews` and arms that template exactly as `/teamup on plan:<template>`; `/teamup on hpc [width]` arms the teamup session as before. `/teamup crews` names each crew's template. `slash.ts` is untouched.
- Gone: `teamup/crews.py` (`CrewPack`, `get_crew`, `crew_goal`), `research_team_spec`, `research_role_prompts` and the research roles' `teamup_role` mapping, which had no consumer once the crews moved.

**What changed for crew users (MD-8).**
- Prompts: paper-writing, kakenhi-report and peer-review run prompts of their own instead of the HPC role prompts. research and code keep their roles' prompts verbatim. No crew is tailored by the root-agent meta call any more (that was a teamup step); its prompts are the template's.
- Turn order: a crew is one bounded plan run in its template's phase order, not a PM-first round-robin iterated by hand. research: planner → experimenters (one per hypothesis) → analysts → reviewer panel. code: architect → rounds of implementer → tester → reviewer. The others as listed above. Fan-outs and maps run in parallel up to the template's `concurrency`.
- Records: a crew run is a plan run (`team_runs/<run_id>/`, journal, resume, `team_plan_result`), not a `teamup_sessions/` session; `teamup_run`, `teamup_status` and `teamup_conclusions` apply to `hpc` sessions only.

**`AgentRole` frozen; `AgentKey`** (`r4/src/r4/teamup/roles.py`).
- `AgentRole` is PM, SE, PG, CD, the HPC preset, and nothing else. A source scan pins that outside `teamup/` only the native engine, the teamup CLI, the chat CLI's teamup mode and the bridge import it, and that no crew source imports `teamup`.
- `AgentKey(str)` is the compatibility read for persisted role keys: the value (`pm`), the member name (`PM`), the enum's `str()` (`AgentRole.PM`), stray whitespace. `TeamupSessionConfig.from_dict` (roles, `role_models`, `role_prompts`), `AgentState.from_dict`, `TeamupSession.load`, `get_role_spec`, the native engine's name mapping and the CLI and bridge role parsers read through it. A key outside the preset is skipped with a warning instead of failing the load. Old sessions, legacy crew sessions included, still load and run.

**HPC scaffolding is the HPC preset's only** (`TeamupSessionConfig.hpc_preset`).
- A session is the HPC preset unless its `crew_pack` names another crew; only legacy sessions persisted before slice 4 do. Bootstrapped and CLI-created sessions now record `crew_pack: "hpc"`.
- The SOTA tracker, the hardware profile, the HPC `CLAUDE.md` and the SOTA status in each role's context are built for the preset only. A legacy crew session keeps running without them.
- For the HPC preset, roles, role prompts, turn order and context are unchanged except for the retired budget lines (pinned against the exact context text).
- No crew template, resolved prompts and tools included, carries HPC vocabulary or tools (`HPC`, SOTA, ChangeLog, `CLAUDE.md`, `hpc_submit`, GFLOPS, OpenMP/MPI, the PM/SE/PG/CD role names); the scan is checked to catch every HPC role prompt.

**The dead stop rules: retired, not wired.**
- `BudgetTracker` (`teamup/budget.py`, with its unused `BudgetPolicy` and `BudgetGuard`) is deleted. Nothing ever called `spend`, so `used` was always 0 and the "budget exhausted" stop never fired. Its units priced HPC actions (compile, benchmark) that R4 never performs: compute belongs to UMRS jobs under ADR-067's caps (D-7), role rounds are already capped by the lead chat's caps (MD-7) and a spec run by its run's `BudgetMeter`. Wiring it would have invented a unit→token conversion and a third token ceiling. Gone with it: the `compute_budget` knob (CLI `--budget`, tool and bridge params, the native engine's `max_tokens // 1000` lowering, the tailor's context line, the context's and `CLAUDE.md`'s budget lines, `get_status()["budget"]`, the snapshot's `budget_used`, the Work pane's budget chip and the team overlay's budget line). A team row in `team.runs` carries no `cost`.
- The SOTA `target_performance` stop is retired too (CLI `--target-perf`, the bridge's `target_perf`): nothing records a SOTA measurement (`SOTATracker.record` has no production caller), so it never fired. The never-fired `_on_sota_update` hook is removed. The iteration cap is the one stop rule.
- Old snapshots that carry `compute_budget`, `target_performance` or `budget_used` load; the keys are ignored and not written back.

**`generate_report` crash fixed.** It called `SOTATracker.get_best_score()`, which never existed, so every session with an optimisation goal raised `AttributeError` (`r4 teamup report`, the bridge's `teamup.report`). Reproduced first by a test; the report now reads the tracker's own `get_improvement_summary` (best against the first measurement, a percentage) for the HPC preset, and has no performance section for a legacy crew session.

**Tests.**
- New: `r4/tests/test_crew_templates.py` (43 functions, 69 cases: registry and aliases, each template's bound and one-source prompts, no HPC leak, each template end to end on scripted turns, peer-review on fake companions through `RoleTurnAgentBroker`, the chat-tool alias with the card asked / refused / no human / `run=false` / models, the bridge and CLI aliases, `AgentKey` reads of an old snapshot, the frozen role set, the HPC preset unchanged, the retired rules, the `generate_report` regression); `packages/r4-tui-v3/tests/crew-templates.test.ts` (5).
- Replaced: `test_teamup_crews.py` (it pinned the removed `CrewPack` API; the registry pins moved to the new file). `test_research_team_seam.py` drops the `research_team_spec` pins and pins `--team research` → the plan.
- Changed pins, and why (MD-8): the session pins in `test_teamup_agent_tools.py`, `test_bridge_teamup_live.py`, `test_teamup_agent_capability.py`, `test_team_seam.py`, `test_team_plan_decisions.py` and `test_workflow_arm.py` start the `hpc` crew (roles pm, se, pg), since `research` and `code` no longer open sessions; `test_prompt_tailor.py` builds its crew-baseline session from a legacy snapshot's prompts; `test_teamup.py` drops the `BudgetTracker` class tests and the budget-0 stop case; `test_teams_native.py`, `test_team_resolver.py`, `test_team_seam.py` and `test_team_plan_surfaces.py` drop the retired budget; the TS surfaces and overlay tests drop the budget chip.
- Mutation check: 29 targeted reverts (25 Python, 4 TypeScript), each killed by its test: alias resolution, the crew → template map, the tool's plan route, the card asked, nothing without a human, `run=false`, model binding, the `crew_role` reference, the bridge route and canonical crew, the CLI's show-then-start, bootstrap's refusal, `AgentKey`'s enum-string and case reads, `load` and config roles through `AgentKey`, the preset gate, the HPC context, the HPC `CLAUDE.md`, the report fix and its improvement text, the budget not persisted, the HPC-leak scan, `--team research`, team rows without cost, and in the console the crew route, alias resolution, the crews listing and `hpc` arming a session.

**Deferred.**
- A roster spec on the native engine (`r4 team launch --roles/--spec`) still runs the four slots with HPC scaffolding, whatever its instructions: compiling roster specs to the plan IR is slice 9 (native convergence).
- `SOTATracker` stays as the HPC preset's status and report, though nothing in R4 records a measurement; giving the preset a recorder (or retiring the tracker) is a separate decision.
- The research crew's "decision" phase belongs to the loop integration (slice 6).
- The dormant HPC modules (D-37) are deleted in the separate cleanup commit that follows this one.

**Cleanup (2026-10-11, its own commit; CONSTITUTION §6).** The dormant teamup modules are deleted: `hpc_conn.py` (an SSH/SFTP client that ran `sbatch` and polled Slurm from R4, the D-37 contradiction), `evolution.py`, `goals.py` and `reports.py`, with their `r4.teamup` exports, the goal-parser tests and the SDK reference sections. `tools/orphan_module_detector.py` reported nothing only because `r4.teamup.__init__` re-exported them; a grep over `r4/`, `tests/`, `packages/`, `engines/`, `scripts/` and `tools/` found no other importer (the goal parser's own tests aside). A second cleanup commit (maintainer, 2026-10-11) deletes `research_loop.team.ResearchTeam` (a "phase 1, no spawning" factory whose planned phase 2 re-skinned the HPC slots) and `teamup.sota.TechniqueExplorer` with the `TechniqueFamily`/`TECHNIQUE_FAMILIES` table only it read: grep found no importer outside their own modules and the `research_loop` re-export, and the orphan detector stays clean.

## Rollout-status (2026-10-11) — slice 5

Built on `wip/a92` (not merged). Earlier blocks are not rewritten; where they differ, this block is current (it replaces D-6's "a profile with a writer is refused inside `map` or `fanout` until workspace isolation lands"). No live LLM run: every plan run below is on scripted turns (`MemoryAgentBroker`) over a real temporary git repository and a plain temporary directory with the real `git` CLI; the tools run for real, directly and through the real `RoleTurnAgentBroker` with a fake companion.

**A per-agent workspace** (`r4/src/r4/companion/agent_workspace.py`).
- A context variable (`r4_agent_workspace`, `AgentWorkspace(root, project_root)`). The broker sets it around every tool call of a plan agent (`_in_workspace`), per call rather than per turn, because the companion runs concurrency-safe tools on a pool thread that would not inherit it.
- Every plan agent has one: the workspace the engine names in `AgentTurnRequest.metadata["workspace"]` (an isolated agent), else the run's project root. A named workspace that is not a directory fails the turn (`workspace_unavailable:…`); it never falls back to the process's directory. Only a broker with no project root at all (a test double) runs tools uncontained.
- Honoured by `file_read`, `file_write`, `file_edit`, `apply_patch` (its `workdir`), `grep_search`, `glob_search`, `list_dir`, and the working directory of `shell_exec` (captured and streamed) and `python_exec`. A path is resolved to its real path first (`..` and every symlink followed). A write must land inside the workspace; a read inside the workspace or the project root. Anything else is refused by name (`Error: refused: … (ADR-092 slice 5)`). A glob pattern with `..` is refused; glob and grep results reached through a symlink that leads out are dropped (ripgrep does not follow links).
- The reading of "absolute paths outside the workspace and the project root are refused": reads may reach the project root; writes may not leave the agent's own workspace. Otherwise an isolated writer could write the shared root by absolute path and isolation would mean nothing. For an agent with `isolation: none` both are the project root.
- Behaviour change: a sequential plan writer can no longer write outside the project root through these tools. The lead session and team roles are unchanged (no workspace set).
- A write inside an isolated workspace is not recorded in the session's change registry (it reaches the project only through a merge-back).
- Found in passing: `apply_patch` kept files under its `workdir` by string prefix, so `<root>10/x` passed for `<root>1`. It now compares path components.
- NOT contained: the text of a shell command or of python code. They start in the workspace, but a command can name any path; those calls keep the workstation posture under the ladder (D-11). Isolation keeps parallel agents off one another's files; it is not a sandbox. Containing the command text is deferred to the sandboxed server venue, slice 8 (maintainer, 2026-10-12).
  *Note (2026-10-30, [ADR-101](101-local-tool-sandbox.md)):* locally, command text can now be contained, opt-in: with the local tool sandbox on, a plan agent's `shell_exec`, `python_exec` and code tools may write only inside its agent workspace (the sandbox's write root is this workspace when one is set) and the session temp directory, and reach the network only through the session's web access. With it off (the default) this bullet holds as written. It does not lift D-6: every writer listed below stays refused in parallel, sandbox or not (a follow-up ADR would be needed), and D-11 stands. While a plan runs, the bridge holds an `on` sandbox even if another, `off` session becomes active (ADR-101 D-12); per-thread pinning is open.

**Plan IR** (`r4-contracts/src/r4_contracts/team_engine/plan.py`).
- `isolation: none | worktree | copy` (`ISOLATION_MODES`, closed, statically checked) on `agent` and `map` steps; `none` by default. A map stage takes its map's: a stage's own `isolation` is an `unknown_key`.
- D-6, updated: a map, or a fan-out above one, may hold a writer only when it is isolated, and only a writer that honours the workspace (`WORKSPACE_TOOLS` ∩ `PARALLEL_UNSAFE_TOOLS`: `file_write`, `file_edit`, `apply_patch`, `shell_exec`, `python_exec`). Every other writer (`compile_code`, `run_code`, `test_code`, `test_fix_loop`, `project_scaffold`, `file_download_platform`, `start_language_servers`, `project_use`, `project_ensure`) and every MCP writer stays refused in parallel even when isolated: it writes wherever it writes (fail closed). Without isolation the rule is unchanged.
- `merge_back`: `{id, kind: "merge_back", from: [1-8 distinct ids], label?, log?}`. `from` names earlier `agent` or `map` steps of the plan's own phases that run isolated (`bad_ref` otherwise). Top level only: refused inside a `repeat` body and an inlined template. It starts no agent (bound 0). `plan_summary` lists `merge_backs`. Its approval carries the class `merge_back` (`MERGE_BACK_CLASS`), a label on the gate card, not an ADR-066 trigger class.

**Workspaces** (`r4/src/r4/teams/plan/workspace.py`, `isolation.py`, `run.py`; stdlib only, inside the import fence).
- One per agent: each fan-out replica its own; a map item one (by index, so two equal items never share), shared by its stages (a fan-out inside a stage gives each replica its own); inside a `repeat` one per iteration. They live under `<run_dir>/workspaces/<id>` and are made when the agent first runs live; a replayed call makes none.
- `worktree`: `git worktree add -B r4-plan/<run>/<id> <path> HEAD`. The project root may be a sub-directory of the repository; the agent then works in the same sub-directory of the worktree. Refused by name: `not_a_git_repo`, `no_commit`, `worktree_failed`, `git_unavailable`. Every git call runs with hooks disabled (`core.hooksPath=/dev/null`), no fsmonitor, no untracked cache, the process's `GIT_*` variables cleared and no prompt: making or removing a worktree runs no script of the repository.
- `copy`: the project tree without `.git`, caches (`__pycache__`, `.venv`, `node_modules`, …; `SKIP_NAMES`) and the run directory itself, symlinks copied as links. Capped by `R4_PLAN_COPY_MAX_BYTES` (256 MiB) and `R4_PLAN_COPY_MAX_FILES` (20,000); beyond either it is refused (`copy_too_large`).
- Every isolation mode a plan uses is checked when the run starts: a mode the project cannot have fails the run `workspace_refused:<code>:…` before any agent, an earlier non-isolated one included.
- The project root is pinned by `TeamService` at submit (`project_root=`, else the process's directory; real path) into `spec.context["project_root"]` and `resolution.json`; a resume works in that tree from anywhere.
- A workspace's file state is recorded when it is made (a manifest of sha256 digests; symlinks by target, files over 2 MiB by size). Journal records `kind: workspace` (`created`, `kept`, `removed`) carry no phase, so `--from-phase` keeps them, and are never replayed as agent results. An isolated call's journal key also hashes its `isolation`.

**Merge-back** (`_merge_back_step`).
- It gathers every workspace of its `from` steps whose agents all ended ok in this attempt (live or replayed). One whose agent failed or never ran is skipped by name (`skipped`). One whose agents ran but whose directory is gone is a problem (`workspace_missing`).
- Refused before anyone is asked, with nothing written: a null `merge_back_conflict:<step>:<code>:<path>@<workspace>…` naming each of `conflict` (the project file is not what the workspace started from), `overlap` (two workspaces changed one file differently), `symlink`, `binary`, `too_large`, `unsafe_path` (`..`, a path into `.git`, a parent that a symlink leads out of the root) and `too_many_files` (over 500). Nothing is resolved automatically. All or nothing: one problem refuses the whole merge.
- With no change, nothing is asked and the output says `applied: false`.
- Otherwise the human is asked on the slice-2 gate path (`ask_gate`, the never-auto `team_plan_gate` approval): the card lists the files per workspace, the diff files (`<run_dir>/merge/<step>/<workspace>.diff`, unified diffs against the project root) and the start of the diff. It is asked after a spent token budget too; it spends nothing.
- A yes: the change set is recomputed and must be the one approved, and every problem is checked again (the project may have moved while the card was open); then the files are written (each through a temporary file and an atomic replace, modes kept). No git command runs and nothing agent-authored is executed (D-11). The merge is journalled (`kind: merge_back`).
- A no: nothing is written, the run ends `cancelled` with `merge_back_rejected:<step>` (later agents `halted_by_gate`), and the decision is journalled as done.
- No human to ask: `merge_back_refused:<step>:<why>`, `cancelled`; the merge-back stays pending.
- Resume (D-8): the human's answer is journalled under the change set's digest, so a resume after a conflict replays the yes for the same set and a different set asks again; a done merge replays its output, asking nobody and writing nothing. A file already holding its new content is skipped, so an interrupted apply finishes.

**Cleanup.** When the run closes (success, partial, failed, cancelled, a cancelled run task included), every workspace is removed (`git worktree remove --force` and `git branch -D`, or the copy deleted), except those whose step a merge-back still pending names (never reached, refused, conflicted or failed), which a resume needs. `result.json` gains `workspaces`: each one's mode, step, path, branch, and `removed` (with any error) or `kept`.

**Surfaces.** The approval card and `--dry-run` end each agent line with where it works (`the project root`, `its own worktree`, `its own copy`) and add a line per merge-back (never auto-approved, text diffs only, a conflict refused). `effective_agents` rows gain `isolation`. The `team_plans` skill teaches isolation, the parallel-writer rule, merge-back and the shell-text limit; its contract test (`tests/run.sh`) pins them.

**Tests.**
- New: `r4-contracts/tests/test_team_plan_slice5.py` (18 functions, 41 cases), `r4/tests/test_companion/test_agent_workspace.py` (25, 31), `r4/tests/test_plan_slice5_broker.py` (7), `r4/tests/test_plan_slice5_engine.py` (29, 36).
- Changed pins, and why: `test_plan_summary.py` rows gain `isolation` (slice 5 adds the column); `test_team_plan_project.py`'s resume round-trip compares against the submitted spec with its pinned `project_root` (submit now pins it, and resume must rebuild the same spec).
- Mutation check: 55 targeted reverts, each killed by its test. They cover the real-path resolution, writes limited to the workspace, relative paths under it, component containment, the context reset, the shell and python working directory, each tool's resolution, grep and glob containment, `apply_patch` (component check and workspace), the change registry, the broker's per-call wrapping, its directory check and project-root fallback, the service's pin and the resume's, the grammar (isolated never / always, only workspace-aware writers, the map's scope, the closed set, the stage key, `from` isolated only, top level only), the preflight, a workspace per replica and per item (two equal items included), git hooks off, the human asked, the re-check after a yes, conflict, unsafe parent, `.git` (both layers), overlap, binary, symlink, the journalled answer and the journalled merge, cleanup (kept while pending, removed otherwise, at all, branch deleted), failed agents skipped, a no stopping the run, isolation in the journal key, the merge asked after a spent budget, the run directory never copied, the copy cap and the caches skipped.

**Maintainer decisions (2026-10-12).** Accepted as built: reads may reach the project root while writes stay in the agent's workspace; a merge-back a human rejects ends the run `cancelled`; isolation is declared per step, not per profile. Shell and python command containment is deferred to the sandboxed server venue (slice 8).

**Follow-ups (2026-10-12), same branch.**
- *Discarded isolated changes are warned about.* `discard_warnings` (contracts) names every isolated step that may write (a writer by `is_writer_tool`; an MCP tool counts) while no `merge_back` names it: "changes made in <step>'s isolated workspace are discarded at run end (no merge_back step names it)". An isolated writer inside a `repeat` or an inlined template always warns (a merge-back cannot name it); a map warns once. It is in `plan_summary(...)["warnings"]` of the prepared plan, so the approval card shows it (`warning: …`); in the compiled plan's warnings (`field: isolation`), so `run_started` carries it; and in `r4 team plan run --dry-run`, which now prints every summary warning (`warning:  …`). Found in passing: the dry run printed no warnings at all, the slice-2 independence warning included, though the slice-2 block says it did; it does now.
- *`r4 team plan clean [<run_id> | --all-finished] [--older-than DAYS] [--dry-run]`* (CLI only; `r4/src/r4/teams/plan/clean.py`, stdlib only).
  - A run qualifies when its `result.json` is terminal for its LAST attempt (a later attempt without a result may be running), or, with `--older-than`, when its journal and event log were last written more than that many days ago (the user asserts the run is abandoned).
  - A run whose journal holds a human's yes to a merge-back that was never applied is never touched, whatever else holds: a resume replays that yes and applies the change set. The output says so and tells the user to resume.
  - It removes every workspace the journal does not record as removed (`git worktree remove --force` plus its branch, or the copy), any other directory under `<run_dir>/workspaces`, stale worktree records (`git worktree prune`) and every branch under `r4-plan/<run>/` in the repositories the run used, never another run's (`r4-plan/<run>ab/…` included). Each removal is journalled (`attempt: clean`). Git runs with hooks off, as in the run.
  - `--dry-run` lists what would go and removes nothing. Exit codes: 0 done; 1 the named run may not be cleaned, or a removal failed; 2 invalid input (no run, both a run and `--all-finished`, a negative `--older-than`, an unknown or unsafe run id). `--all-finished` lists the runs it left untouched and why.
- Tests: `r4-contracts/tests/test_team_plan_slice5.py` gains 8 functions (10 cases) for the warning; new `r4/tests/test_plan_slice5_clean.py` (13 functions, 17 cases: the card, the compiled plan and the dry run; clean's dry run, removal, copies, the pending answer, unfinished attempts and idleness, stray branches and directories, `--all-finished`, the CLI's input checks and exit codes).
- Mutation check: 17 more targeted reverts, each killed (the warning's coverage, writer and isolation tests, its three surfaces; clean's pending-answer guard, last-attempt check, idleness, dry run, eligibility, the run's own branch prefix, the branch sweep, stray directories, the journal record, the CLI's exit code and its one-or-all rule). Slice 5 in all: 72.

**Deferred.**
- The writers that do not honour a workspace (`compile_code`, `run_code`, `test_code`, `test_fix_loop`, `project_scaffold`, `file_download_platform`, `start_language_servers`, `project_use`, `project_ensure`) and MCP writers stay out of parallel steps. Threading the workspace into `coding.py` and `test_fix_loop` would admit the first four.
- A repository's own filter drivers (a smudge filter such as LFS) still run when a worktree is checked out: hooks and the fsmonitor are disabled, filters cannot be without breaking the checkout. The merge-back itself runs no git.
- The window between resolving a path and opening it is not closed against an agent swapping a symlink in its own workspace meanwhile (its shell is not contained anyway).
- Merges are whole-file transitions checked against the start state; there is no hunk-level three-way merge (by design: no automatic resolution).
- A live run on a real model; the console shows a merge-back as a gate card of class `merge_back`, with no diff view of its own.

## Rollout-status (2026-10-12) — slice 6

Built on `wip/a92` (r4) and `wip/umrs-a92` (UMRS), not merged. Earlier blocks are not rewritten; where they differ, this block is current. No live LLM run: every plan run below is on scripted turns (`MemoryAgentBroker`) through the real `LocalRuntime`, `TeamService`, plan engine and, for gates, the real `RoleTurnAgentBroker` gate path.

**Who owns what.** The loop that runs the plan is r4's local venue (`LocalRuntime`, ADR-075's local host; the loop client is R4's per CONSTITUTION §1.3). UMRS refuses what its server venue cannot run yet. r4 imports nothing from UMRS. The ask-policy vocabulary both venues need (the trigger classes and the action kind → class table, `plan_run` → `cost` included) lives in `r4-contracts` (`r4_contracts/ask_policy.py`, stdlib only); `umrs.knowledge.ask_policy` and `r4/src/r4/runtime/local_policy.py` both read it (UMRS already imports `r4_contracts`, so no new dependency edge). One contract test pins the table; each side has one consumer test.

**A plan as the per-iteration executor** (`r4/src/r4/runtime/plan_executor.py`, the `IterationExecutor` seam of `runtime/api.py`).
- Still one loop and one driver (§1.6, D-29, D-32): the local runtime's driver thread runs the iteration; the iteration is one bounded plan run through `TeamService` and the plan engine, governed as every plan run is (D-3..D-8). No new loop, team engine or store.
- `runtime/api.py` adds a *loop iteration executor*: one that declares its action (`iteration_action`) and runs with the loop's context (`run_iteration(IterationContext)`). The context carries the loop's remaining ADR-067 wall clock, `ask_human(action)` (parks on the loop's approvals list, returns the human's answer, raises `LoopHalted` when the loop stops first; it cannot decide) and `stop_requested()`. A plain executor is unchanged, and its summary is never a control signal.
- `PlanIterationExecutor` runs one template per iteration: `research-round` only (`LOOP_TEMPLATES`), because the loop reads a reviewer verdict from its output; another template is refused by name. The loop owns `objective` (its goal) and `context` (its own record of earlier rounds: code-derived verdicts and scores, never an agent's words, because a plan parameter renders outside the untrusted envelope). Called as a plain executor it refuses: a plan iteration never runs outside the gate.
- The research crew's "decision" phase (slice 4, deferred here) is the loop's: `loop_decision_for` reads the round, in code. A run that did not finish (failed; cancelled by a rejected or refused gate, a spent budget, a stop) stops the loop with its reason; a design the reviewers picked publishes (the loop completes, `state_reason: published:…`), on the first round that has a pick: the loop does not run on to `max_iterations` after one (maintainer, 2026-10-12); a near-tie a human let pass, or no pick, continues. The local driver acts on the decision (`_end_by_decision`).
- `research_loop_start` gains `plan`, `plan_args`, `mode`, `pause_triggers` and `max_wall_clock_hours`. The mode is the caller's, else the project's persisted ask policy (D-33, read over HTTP), else `ask`; the wall clock is the tighter of the caller's and the project's ADR-067 row (`GET /gw/projects/{id}/budget`; unreadable is uncapped, logged, as the server reads it). The reply names the plan and the mode.

**A plan run is a `cost` action, gated before any loop use (ADR-066, D-33).**
- Contracts: `r4_contracts.ask_policy.ACTION_KIND_CLASSES` classes `plan_run` as `cost`, beside `workflow_trigger` and `agent_iteration`. UMRS: `umrs.knowledge.ask_policy` derives `COST_ACTION_KINDS` and its classes from that table; the engine driver's existing gate therefore parks a plan run in ask mode and in a profile naming `cost`, and lets it run in auto or a profile without `cost`.
- r4: the local driver gates the declared action before `run_iteration` is called (`LocalRuntime._run_governed`, `local_policy.needs_human`). A plan loop with no mode asks (fail closed; maintainer-confirmed 2026-10-12). Ask mode parks at every plan run: the action becomes one pending approval on the loop's own record (`metadata.awaiting_approval`, phase `awaiting_approval`), listed by the existing `research_loop_approvals` and `r4 project loop approvals`, answered only through `research_loop_decide` (never auto-approved, in `TEAM_AGENT_DENY`) or `r4 project loop decide`. `once` runs it; `no` stops the loop (`approval_rejected:plan_run`, iteration `rejected`, nothing run); `always` runs it and turns the loop `auto`, as the server's inbox does. Auto mode runs it under the loop's caps.
- Never self-approved: only `LoopApprovals.decide` flips a record; a source scan pins that only the human surfaces and the runtimes that route them reach it, and `IterationContext` has no decide. A stop while a question waits withdraws it (`status: withdrawn`, never an answer). So does the host process exiting (a gate waits on a non-daemon pool thread, which would otherwise hold the process open).
- `LocalRuntime.capabilities().approvals` is now true; `HybridRuntime` lists both venues' approvals and routes a `local-appr-…` decision to the local runtime.

**Plan gates go to the loop's inbox.** Inside a loop the plan's gate lane is the loop's approvals list (`loop_gate_lane`: an `ASK_ALWAYS` manager used only for the never-auto `team_plan_gate`, as the CLI's terminal lane is). The engine emits `HUMAN_INPUT_REQUESTED`; the gate parks as a `plan_gate` approval (class, gate path, prompt marked untrusted) whatever the loop's mode; the human's answer flows back as the gate's answer; the engine journals it (D-8, `kind: gate`) and the loop journals question and answer (`metadata.approvals_log`, the last 50). `always` on a gate is a yes to that gate only. A loop stopped while a gate waits refuses the gate (`broker_error:LoopHalted`), which is not journalled as an answer, and the run ends `cancelled`.

**The ADR-067 wall clock is passed down.** `max_wall_clock_hours` caps a local loop from its start; the driver stops it at an iteration boundary with `state_reason: budget_exhausted:wall_clock`. The plan's `budget.max_wallclock_sec` becomes min(its own, the loop's remaining seconds) (`plan_within_loop_budget`; at least 1; an uncapped loop leaves the plan's own); under a second left, no plan runs and the loop stops. The plan's own clock still pauses for gate waits (slice 2); the loop's does not.

**Reviewer gating feeds proposals only (U-10, ADR-050, migration 0069).** When the reviewers pick a design, the loop stages one graph-extension proposal on the project's first graph (`graph_propose`, the 0069 propose lane, subject to the session's acquisition gate): one `add_finding` operation, `status: proposed`, confidence 0, producer `research_loop:<loop>/iteration:<n>/plan:<run>`, marked untrusted. A person decides it; the loop never decides, ingests or establishes. No pick (undecided or a near-tie) proposes nothing. A published round records a Scientific Catalog *candidate* on the iteration (goal, conclusion, evidence refs: the run and the proposal; `written: false`); nothing is written to the Catalog (review promotes, D-39). A refusal or failure is recorded as `not_proposed` with its reason.

**The server path refuses by name, pending slice 8.** A server-hosted loop has no tool-executing agent session to run plan agents in until the server venue (slice 8). `UmrsRuntime.start_loop` raises `PlanIterationRefused` (`plan_iteration_needs_server_venue`) before anything is sent, so does a hybrid loop routed to UMRS, and the chat tool answers with that code and `pending: ADR-092 slice 8`. UMRS refuses it itself: `POST /umrs/loops` with `iteration_plan` is a 422 `loops.unprocessable` (reason `plan_iteration_needs_server_venue`) before any loop is created, and a `research_loop_job` spec carrying one is refused with the same message. Never a plain loop with the plan dropped.

**Found in passing (local runtime).** Every chat tool call builds a fresh `LocalRuntime`, so a stop or pause made through the tools only changed the store while the loop's thread, held by another instance, kept iterating. The driver now reads the store's status at each iteration boundary (pause waits, stop stops) and while an iteration waits on a human.

**Tests.**
- New: `r4/tests/test_loop_plan_policy.py` (24 functions, 31 cases: the vocabulary, the default mode, unknown modes and classes, the park and the human's once/no/always, auto, profile, a stop while parked, the decision table on the driver, plain executors, the wall clock and the boundary stop, a stop from another instance, the UMRS and hybrid venues, the decide-caller scan, the context's fields), `r4/tests/test_loop_plan_executor.py` (22 functions, 33 cases: end to end on `research-round`, the proposal and the candidate, no pick, the context, the gate parked and answered, a gate's no, `always` on a gate, host exit, a stop while a gate waits, the budget min, the uncapped plan, a spent clock, the refusals, the decision table, `not_proposed`, the chat tool's mode, policy, cap and server refusal); UMRS `tests/test_loop_plan_iteration.py` (9 functions); `r4-contracts/tests/test_ask_policy.py` (3, the one pin of the shared table).
- Changed pin, and why: `test_runtime_api.py`'s `test_local_runtime_has_no_approval_inbox` became `test_local_runtime_inbox_is_empty_and_names_umrs_for_unknown_ids` (a local plan loop now has an approvals list; an unknown id still names UMRS). The `team_plans` skill's contract (`tests/run.sh`) pins the loop section.
- Mutation check: 35 r4 reverts, 4 UMRS reverts and 6 on the shared table (the contract's `plan_run` class, caught by the contract test and by each side's behaviour tests; each side keeping its own copy or ignoring the table), each killed by its test (one, a host exit that keeps waiting, by the hang it guards against). They cover the ask-mode park, the plan-loop default, `plan_run` as `cost` (both sides), a stop while parked read as a yes, a pending record read as approved, `no` as approved, `always` on a gate, withdrawn journalled as rejected, the withdraw, `decided_by`, the decide-caller scan, the gate lane self-approving, the budget max-for-min and no pass-down, a spent clock still running, the boundary wall clock, the store's stop, a rejection ignored, the decision table (pick continues, failure continues), plain executors deciding, a finding proposed without a pick, established, decided, a catalog write, agent text in the context, the plain call running, the UMRS runtime sending, the hybrid's routing and listing, the tool's refusal, project policy and cap, and the server's two refusals.

**Deferred.**
- Server venue (slice 8): a plan iteration on a server-hosted loop; its gates would then go to the UMRS durable inbox (`plan_gate` is not a mode-gated kind there either).
- Carrying earlier rounds' agent-written hypotheses into the next round needs the untrusted-argument rule slice 2 deferred (a TODO in `_context_text`); today the next round sees verdicts and scores only.
- Loop templates other than `research-round` need a reviewer verdict the loop can read; none is declared yet.
- A local loop whose process exits stays `running` in the store and its question is withdrawn (`host_exit`); U-03's `parked(host_exit)` and honest resume are not built.
- The proposal targets the project's first graph; a project-chosen default graph and a proposal schema UMRS validates (the operations list is free-form JSON today) are follow-ups. Catalog candidates live on the iteration record; a browsable candidate list and the review-promote path are D-39's.
- A live run on a real model.

## Rollout-status (2026-10-12) — slice 7

Built on `wip/a92` (r4) and `wip/umrs-a92` (UMRS commit `17d9c72a`), not merged. Earlier blocks are not rewritten; where they differ, this block is current (it replaces D-8's "the durable home is UMRS, in slice 7" and the Consequences line "plan runs are client-local daemon threads until slice 7" for runs with a UMRS source). No live LLM run and no live UMRS: every plan run below is on scripted turns (`MemoryAgentBroker`) through the real `TeamService` and plan engine against an in-memory stand-in for the UMRS routes (`FakeUmrs`, with their attempt rules and JSONB's key reordering); the UMRS routes are tested with a faked `JobStore` and the in-memory trace store, the new SQL against a fake connection.

**Maintainer decisions applied.** MD-3: the local journal is an attempt-local cache; resume is kept. MD-4: the durable home is a `research_loop_job` plus `execution_traces` keyed by input hash; no `team_runs` table (CONSTITUTION §6: one job store). No migration: `execution_traces` (0040, 0042) already has every column the record needs (`inputs_hash` with its index, `outputs_snapshot`, `outputs_hash`, `model_ref`, `retrieval_refs`, `attempt_id`); no schema changed, so `SCHEMA_VERSION` is unchanged.

**Who owns what.** The run still runs in r4 (the agent harness, §1.3). UMRS keeps its durable record: the job, its attempts, its records. r4 keeps no job store, queue or retry: the run directory is a cache of the UMRS record, and r4 talks to UMRS over HTTP only (the traces family). r4 imports nothing from UMRS.

**The durable record in UMRS** (`umrs/src/umrs/jobs/plan_run_records.py`, routes in `umrs/src/umrs/api/traces.py`).
- A plan run is one `research_loop_job` whose spec says `hosting: client`: the request-driven tier, as `vision_infer` is. `RestartPolicy.NEVER`, never enqueued (PLANNED → RUNNING by its client's attempt, never QUEUED), so no scheduler claims it. The spec keeps the run's definition (plan, bound params, resolution manifest, at most 256 KiB) from the first open; a job's spec is immutable (ADR-002). One job per (user, run id) in the caller's project (`idempotency_key`).
- One UMRS attempt per r4 attempt (ADR-002: Job = intent, Attempt = one try; r4's attempt number rides in `backend_ref`). Opening attempt N+1 marks an attempt still active LOST (`client_resume`: its client is gone or moved, so at most one attempt is active, INV-04) and reopens a finished or failed job to PLANNED (`JobStore.reopen_client_job`, SQL-guarded to client-hosted jobs, never a cancelled one, never to QUEUED; the commit pointer is kept). A job cancelled in UMRS is never reopened (`plan_run_cancelled`).
- Each portable journal record is one `execution_traces` row of the job and the attempt that wrote it: `inputs_hash` is the record's D-8 call key; `outputs_snapshot` carries the record's JSON text verbatim (`{"format": "r4.plan.journal/1", "record": …}`) and `outputs_hash` its sha256; `step_type` `plan_agent` / `plan_gate` / `plan_invalidate`; `status` succeeded or failed with the null's reason as `error`; the model and retrieval refs in their columns. Text, not an object: JSONB reorders keys, and a replayed output must keep its key order (D-8). `ExecutionTraceStore.record_completed` writes it in one insert and raises on a database failure instead of falling back to memory: a record UMRS answered for is durable.
- Attempt-scoped writes (ADR-002): a record for an attempt that is not the job's active RUNNING attempt (LOST on a UMRS restart, superseded, finished) is refused (`attempt_not_active`, 409). After a UMRS restart the reconciler marks the attempt LOST and, NEVER retried, the job FAILED; the client names the refused write and a later resume reopens the job.
- Routes, under the gateway's existing `/gw/traces/*` proxy, the caller's own runs only (job access as for every job's traces): `POST /umrs/traces/plan-runs/{run_id}/attempts`, `POST …/records`, `GET …/{run_id}` (definition and attempts), `GET …/records` (in write order, verbatim), `POST …/attempts/{attempt_id}/finish` (`success` and `partial` commit the attempt; `failed` and `cancelled` fail it with `plan_<status>: <error>`). Refusals are typed `jobs.*` codes; a record over 512 KiB, a key over 64 characters (the column's width), a malformed record, or a workspace or merge record is refused by name.
- The kind stays client-hosted: `validate_research_loop_spec` refuses a client-hosted spec by name (`client_hosted_run`), so an admin retry or a reproduce that requeued it is never hosted as a loop; `ResearchLoopStatusChecker` reports its attempt running and never fails it (its client closes it); the agent process budget (ADR-051 D-2) never counts it (it runs no process in UMRS, and a client that dies leaves it RUNNING until a resume or a restart).

**The journal becomes the cache** (`r4/src/r4/teams/plan/journal.py`, stdlib only, inside the import fence).
- The engine never holds a client (ADR-020 reverse control): a run with a durable home gets an optional SPI broker, `RuntimeServices.records: RecordBroker | None` (`load`, `append`, `close`, `describe`, `required`), additive like `agents`, so `SPI_VERSION` stays 0.1 and roster engines are unchanged. r4's implementation is `DurableRunRecords` (`r4/src/r4/teams/durable_records.py`) over `UmrsPlanRecords`, the thin HTTP adapter on the client's `http` (the gateway's auth).
- Writes: every record goes to the local journal first (fsynced, as before), then a *portable* record (an agent call, a human's gate answer, a `--from-phase` marker) goes through to UMRS. Workspace and merge-back records stay local: they describe one machine's files.
- Reads: the local journal wins per key; UMRS fills the keys it lacks (an ok record from UMRS also beats a local failure of the same key: a failure never displaces an ok record), and whatever UMRS filled is written into the local journal (the cache fills). A UMRS record whose text does not match its sha256 is skipped (counted, `skipped_records`) and its call runs live.
- `--from-phase` rewrites the local journal as before and writes an `invalidate` marker to UMRS; a later read drops every earlier UMRS record of those phases, so a resume elsewhere honours it.

**The failure policy, stated.** A UMRS failure is never silent and never retried in a loop:
- Without `durable: true` (the default when a UMRS source is configured): the first failure (open, read, write or close) is recorded, logged, emitted once as a `durable` progress message (after `run_started`), and named in `result.json`, the terminal event, the chat tool's result and the CLI's last line. From then on the run continues **local-only**: no further record is sent (never a durable record with a gap before a later record), and the attempt is not reported finished to UMRS. Resume on the same machine replays everything from the local journal.
- With `durable: true` (required): no UMRS source, or an attempt that cannot be opened, refuses the run before anything runs (`DurableRecordUnavailable`, `durable_required: …; nothing ran`). A record that cannot be written or read stops the run as a unit, fail closed: agents in flight are stopped through the broker's cancel, no agent or gate starts after it (`durable_record_failed` nulls), and the run ends `failed` with `durable_record_failed: <what>: <error>`. A read failure fails the run before any phase is walked. The paid calls are kept in the local journal. The choice is recorded in `resolution.json` and in the UMRS job, so a resume keeps it.
- `result.json` gains `durable`: `home` (`umrs` or `local`), `required`, `state` (`ok`, `degraded`, `failed`, or `local_only` with no source), `written`, `local_only`, `replayed_from_durable`, the job and UMRS attempt ids, and `error`. `journal` stays `local-cache`.

**Checkpoint scope (ADR-020 §15.1).** A run whose records all reached UMRS reports `checkpoint_scope: portable` (in `result.json` and in its `checkpoint_created` events): it continues on any machine. A local-only run, or one whose home failed, reports `engine_internal` (this run directory only; the "LOCAL" of the slice-7 plan). The engine's descriptor keeps `ENGINE_INTERNAL` as its floor: scope is a property of each run.

**Resume from any venue.** `TeamService.resume` on a machine without the run directory rebuilds it from UMRS (`TeamService.hydrate`: `plan.json`, `params.json` and `resolution.json` from the job's definition; the attempt is the last one UMRS recorded), then runs attempt N+1 exactly as a local resume: ok calls replay at zero tokens from the records UMRS kept. The project root becomes the resuming machine's (its `project_root`, else the process's directory), since the recorded one names a tree on the first machine. The chat tool's resume card rebuilds the run the same way, so it shows the recorded plan.

**Surfaces.** `r4 team plan run|resume --durable` (required); the CLI prints `durable: umrs <state> (job …)` or `durable: local-only` after the status, and `[durable] …` when the home fails. `team_plan_run(durable=true)` (the card says so; the card's arguments include it); its result carries `durable`. A UMRS source is configured when the active source is a platform source or `R4_GATEWAY_URL` is set (`umrs_records_for`); `R4_PLAN_DURABLE=off` keeps runs local-only. The `team_plans` skill teaches where a run's record lives, and its contract test pins it.

**Found in passing.**
- A turn halted on purpose for a lost durable record was read as the run task itself being cancelled, so a required run ended `cancelled` instead of `failed`; `_stopped_on_purpose` now names the three deliberate stops (cancel, engine error, durable halt). Found by the in-flight test below.
- Fixed after review (maintainer, 2026-10-12; umrs `54aa10e7`, `tests/test_traces_access.py`, each test failing on the old code): the trace routes `GET /umrs/traces/{id}`, `/{id}/lineage`, `/{id}/children`, `/{id}/inputs`, `/{id}/outputs`, `/execution/{id}`, `/step/{name}` and `PATCH /{id}` checked no job access, so any authenticated caller read or changed another project's traces. Each now applies the job's own access (owner or admin, from the gateway-verified identity; `/gw/traces/*` forwards the HMAC-signed headers): a trace the caller cannot read is a 404 by name, lists carry only readable jobs' traces, a child cannot be hung under an unreadable parent, and `DELETE /expired` (every user's traces) is admin-only. `_db_insert_trace` now writes `inputs_snapshot`; `_db_update_trace` sends snapshots as JSON (a PATCH with outputs used to fall back to memory and answer 404); with a DSN configured a trace write the database did not take raises and the route names it (503), and the in-memory store is the dev mode with no DSN only.

**Tests.**
- New: UMRS `tests/test_plan_run_records.py` (30 functions, 39 cases: the job and its attempts, supersede and reopen, cancelled, run ids, another user, the definition, the record row and its key, verbatim order, a null's reason, superseded and lost attempts, malformed and venue-local records, size, a store failure named, unknown runs, the run view, finish and commit, the backend refusal, the checker, the agent budget, the reopen SQL, the strict insert); r4 `r4/tests/test_plan_durable_records.py` (29 functions, 30 cases: write-through by key, local-only without a source, checkpoint scope both ways, venue-local records, resume on another machine at zero tokens with the cache filled and the root re-pinned, a missing record named, `--from-phase` elsewhere, local-first reads, ok-not-displaced across sources, a forged record, a failed write named and local-only, staying local-only after UMRS recovers, an open failure, a read failure still replaying locally, required: fail closed on a write, on a read, stopping agents in flight, refused without a source, refused when the attempt cannot open, kept on resume; an explicit local run, the HTTP routes, a quoted run id, key order on the wire, the source check, the CLI and chat-tool surfaces).
- Changed pin, and why: `test_team_plan_project.py`'s `fake_plan_modules` stand-in for the journal module gains `phases_from` (the module's new public function the service calls on `--from-phase`); no assertion changed. The `team_plans` skill contract (`tests/run.sh`) pins the new section.
- Mutation check: 19 UMRS and 35 r4 targeted reverts, each killed by its test. UMRS: the checker, the backend refusal, the budget filter, the reopen guard (client-hosted only, PLANNED not QUEUED, never cancelled), the active-attempt check (two ways), the verbatim text, the D-8 key, the strict insert, the per-user key, cancelled reopened, commit on success, venue-local kinds, NEVER, the size cap, a store failure acknowledged, the definition overwritten. r4: write-through, venue-local records sent, UMRS winning over local, no cache fill, a swallowed failure, writing on after a failure, required ignored, the refusal and the failed status on a halt, the early fail on a read, scope always portable, no `durable` block, a required run without a source, the broker not attached, no hydrate, the old root kept, no marker, required forgotten on resume, sorted keys on the wire, no hash check, an optional open failure raising, the off switch, any source taken for UMRS, no source needed, the note before `run_started`, the attempt never closed, the chat result, the CLI's run and resume flags, markers ignored, ok displaced across sources, in-flight turns not stopped, a failed write not counted, the chat tool's flag, and a durable halt read as a task cancel.

**Deferred.**
- The server venue (slice 8): a UMRS-hosted interpreter would drive the same job and records itself.
- A client that dies leaves its UMRS attempt RUNNING until a resume supersedes it or a UMRS restart marks it LOST; a client heartbeat or lease is not built.
- After a degraded attempt, the local-only records are not uploaded on a later attempt; a resume elsewhere reruns them live (honestly, at their cost).
- `--from-phase` on one machine does not invalidate another machine's local journal that still holds the old records of that phase (its local copy wins on that machine).
- Workspaces and merge-backs are local: a resume elsewhere cannot merge an isolated step's changes (`workspace_missing`).
- The records route returns every record of a run at once (bounded by the plan's agent bound per attempt); paging is a follow-up.
- `r4 team plan list/result` read the local run root only; listing a user's plan runs from UMRS (the jobs list shows them as `research_loop_job`) is a follow-up.
- A live run against a real UMRS and a real model.

## Rollout-status (2026-10-12) — slice 8

Built on `wip/a92` (r4) and `wip/umrs-a92` (UMRS), not merged. Earlier blocks are not rewritten; where they differ, this block is current (it replaces slice 7's "the server venue (slice 8)" deferral for single runs, and the slice 6 line "a server-hosted loop refuses a plan by name … until slice 8"). No live container and no live model: the UMRS side runs on a fake container runtime (`FakePodman`, as the venue route tests do), an in-memory lease broker and the in-memory trace store; the r4 side runs the real worker, `TeamService` and plan engine on scripted turns (`MemoryAgentBroker`, the real `RoleTurnAgentBroker` gate path) against an in-memory stand-in for the UMRS routes reached through the real HTTP adapter's URLs.

**Who owns what.** UMRS hosts the run: it validates the definition with the stdlib grammar it already imports (`r4_contracts.team_engine.plan`), records the run, launches one sandboxed worker and keeps the record. It never imports r4 (CONSTITUTION §1): the worker is r4 code in the `r4-companion` image, launched by argv. The agent harness stays r4's (§1.3); the job store stays UMRS's (§6: no new table, no migration).

**The server venue is the ADR-046 frontend venue** (`umrs/src/umrs/venues/plan_runs.py`, routes `umrs/src/umrs/api/plan_run_venue.py`).
- `POST /umrs/traces/plan-runs/{run_id}/venue` (under the gateway's existing `/gw/traces/*` proxy; the caller's own runs) starts a run with its definition, or resumes one without. In order, before anything is created: the site's venue must be configured (`FrontendVenueConfig.missing()`; else 503 `companionvenue.unavailable`, `plan_iteration_needs_server_venue`, the missing `UMRS_VENUE_*` names and the pointer to enable them); the plan must validate (400 `plan_invalid`, every problem); its wall clock must sit within the project's ADR-067 cap (403 `plan_exceeds_project_caps`; an unbounded plan in a capped project is refused too; GPU hours are not a plan concept, D-7). Then the run's record, then one `frontend_session` job.
- The record is slice 7's: one `research_loop_job`, here `hosting: venue`, `durable_required: true`, its definition, its worker's session id (`planrun-<sha(user, run)[:24]>`, one per run, reused by a resume) and the caps it was admitted under; never enqueued, never hosted as a loop (`validate_research_loop_spec` refuses it, the status checker reports it running, the ADR-051 agent budget does not count it: its worker's session job is the one unit; the reopen SQL admits `venue` beside `client`).
- The worker session is the chat venue's own path (`FrontendVenueService.start_plan_run`): sealed sources resolved, the ADR-062 class gate at start (`source_class_refused` for a sensitive project's public source), a subnet, the ADR-051 spawn budget, one `frontend_session` job claimed by this site, provisioned by the existing backend (network → lease → podman secret → egress pod → egress IP → worker, compensating teardown). Only the worker command differs: `r4 venue worker --plan-run <run_id>` (`worker_command_for`, the run id validated as a run id because it rides argv); a chat session's command is unchanged. There is no mirrored chat snapshot to require. The session job spec records `plan_run_id` and `plan_run_job_id`: the binding every rail check reads (never anything the worker sends).
- The worker reports `done` when its run ends; the status checker then tears the session down and succeeds the session job (`plan_run_done`). `GET …/{run_id}/venue` is the record plus the worker's state; `POST …/{run_id}/cancel` stops the worker first (teardown, lease revoked, session job cancelled) and then cancels the record, which is never reopened (`plan_run_cancelled`).

**The worker's credential is the session lease, and it reaches its own run only** (`umrs/src/umrs/api/companion_venue.py`, `_rail_identity.py`, `traces.py`).
- No new credential path: the worker holds the lease token the provisioner already seals into a podman secret (ADR-046 P1, P7), and its env is the chat worker's (no user JWT, no provider key; a test pins the env names and the token's absence from argv).
- Its record calls ride the rail gw (P7, P10) to the traces family, which a sandbox reaches only for `traces/plan-runs/<its bound run>` and only the record verbs (the run, its attempts, its records, finishing an attempt): never another run, never `/venue` or `/cancel`, never another traces route. A chat session's sandbox still never reaches the family.
- Writes to a venue-hosted run (open, append, finish) are accepted only from that run's worker: the rail registers a one-time attestation (32 random bytes, in the app's state, alive only while its in-process re-dispatch runs; the header is stripped from what the sandbox sends) naming the lease's session, and `plan_run_records.check_writer` requires it to equal the run's recorded session. The owner's own client, with a valid JWT, is refused (`venue_hosted_run`, 403); reads stay the owner's. A venue worker never writes a client-hosted run and never creates a run.

**Model calls go only through `umrs-infer`.** The worker's LLM route is the sidecar (`R4_ENDPOINT_URL`, api key = session token) to `umrs-infer`, which serves the session's pushed sealed sources with the ADR-062 class gate per request, as a remote chat turn's are. The client sends bare catalog names: the pinned default drops its `@source`, and an agent naming `model@source` is refused for the server venue by name (no per-agent source routing in the worker). *Adjusted, with reason:* the design asked for "the ADR-068 lane resolved via the D-63 EndpointRegistry seam (the route the worker already uses)". Verified in the tree: the route the worker already uses is `umrs-infer` over the lease's sealed user sources; `umrs-infer` resolves no `EndpointRegistry` row and refuses non-global upstreams, so a site model (`/umrs/llm`) is not reachable from a sandbox today. Wiring one is a change to the credential-injecting proxy (ADR-046's highest-value component) and is left to its own decision (deferred below).

**Gates go to the UMRS approvals inbox (ADR-066).** The worker raises a gate through the rail (`POST …/rail/plan-gates`): one intent in `companion_approval_inbox` (`loop_id: plan-run:<run_id>`, the run's job and active attempt, the owner, `kind: plan_gate`, the prompt marked untrusted; one id per run record, attempt and gate, so a retried request finds it), and polls it (`GET …/rail/plan-gates/{id}`, its own run's gates only). The owner answers from any client through the inbox's own decide route (`r4 project loop decide <id> --decision once|no`; the CLI follower may ask at the terminal and decide as the owner). The rail has no decide verb and the rail gw does not admit the `companion` family: the worker never answers a gate. A stop, a revoked lease or a timeout (`R4_PLAN_GATE_TIMEOUT_SEC`, default 24 h) refuses the gate (`GateUnanswered`), which is not journalled as an answer.

**The worker** (`r4/src/r4/teams/plan_worker.py`, `r4 venue worker --plan-run <id>`): the D-7 isolation self-test, then the run's record through the rail, then `TeamService.host_recorded` (the next attempt UMRS records, 1 when none, its record required; the run directory rebuilt in the sandbox's `/tmp/home`), the plan engine unchanged. Its agents run fail-closed (safe tools only, no lead manager), and its budget is the plan's own with the wall clock lowered to the cap UMRS recorded (D-7 per round, ADR-067). Heartbeats every 30 s keep the lease warm, carry the last progress line, and carry the owner's stop, which cancels the run as a unit. The engine's durable close now carries the declared outputs when they fit in 12 KB (`output`, else `output_truncated`): a server-hosted run's client has no run directory, so this is its result.

**r4 surfaces** (`r4/src/r4/teams/server_venue.py`, `_cli/team_plan.py`, `companion/team_plan_tools.py`).
- `r4 team plan run … --venue server --source NAME[,NAME] [--default-source] [--web] [--detach]`: the client validates and compiles as for a local run (`TeamService.hosted_definition`), UMRS records and launches, and the CLI follows the run through its record (agent lines, waiting gates) to its outcome. `resume <id> --venue server` starts a fresh worker for the next attempt on the recorded definition (ok calls replay free; `--from-phase` is not offered there yet); `result <id>` falls back to the outcome UMRS recorded; `cancel <id>` is new.
- `team_plan_run(venue="server", sources=[…])`: the same, behind the same card (which says so; the card's one-shot SAFE-tool grant is consumed and does not travel to the worker). `team_plan_result` and `team_plan_cancel` reach a server-hosted run by its id. The worker's output is untrusted: it is absorbed whole into the lead's fence as generic taint (no per-excerpt taint crosses from the worker).
- A site without the venue: the refusal keeps its code, `plan_iteration_needs_server_venue`, and now names the missing settings and how to enable them (`ServerVenueRefused`; the chat tool answers with the code).

**The research loop's server path: adjusted, with reason.** The design asked that a server-hosted loop with a plan iteration now start server-hosted runs. Not built: a server loop's driver is UMRS's engine loop runner, and driving plan rounds there needs UMRS to bind each round's params and own the loop's continue/publish/stop decision and its proposals, which are r4 code today (`loop_decision_for`, `_context_text`, `propose_finding`, slice 6). Moving that table into `r4_contracts` and adding a plan engine kind under `EngineLoopRunner` is close to the alternative this ADR rejected ("Plans hosted by UMRS, as a third engine kind…") and wants its own decision. So `POST /umrs/loops` with `iteration_plan` and `UmrsRuntime.start_loop` still refuse by name (`plan_iteration_needs_server_venue`, 422); the refusal now says whether the site has the venue (the missing settings) or that it hosts single runs (`r4 team plan run --venue server`), and the chat tool's reply names `team_plan_run(venue='server')`.

**Tests.**
- New: UMRS `tests/test_plan_run_venue.py` (33: the record and the one worker session, the worker command, the worker env, the disabled venue, caps over, unbounded and within, an invalid plan, already running, hosting mismatch; the worker's own record through the rail, the owner's client refused on every write, another run's record, start/cancel/other routes refused to a worker, a chat session's sandbox, a dead attestation, the writer rule, a worker never creating a run; the inbox intent decided by the owner, a retried gate, no open attempt, another run's gate, a chat session's gate, no decide verb; cancel, resume, status, `done`; caps, the backend refusal, the run id on argv; the loop route's two refusals). r4 `r4/tests/test_plan_server_venue.py` (21: the worker runs from the record and writes it through the rail with the cap applied, a failed write stops it fail-closed, the cap only lowers, a second sandbox resumes at zero tokens, the owner's stop, a gate answered in the inbox, a rejected gate, an unanswered gate refused, no decide verb, a client-hosted record refused, the worker's only credential; the CLI run followed to its result and read back by id, the disabled site, no source, resume and cancel by id, the follower's gates with and without a person, the baseline, `model@source`, the chat tool with taint, the close output, the `--plan-run` command).
- Changed pins, and why: `test_plan_run_records.py`'s agent-budget SQL (`<> ALL(%s)` over `client` and `venue`) and reopen SQL (`IN ('client', 'venue')`); `test_loop_plan_executor.py`'s pending text (the reply now names the server venue for single runs). The slice-7 plan-run routes gained their `# auth: verified` markers (`tools/route_inventory.py check` named them).
- Mutation check: 22 UMRS and 25 r4 targeted reverts, each killed by its test (one, a heartbeat that ignores the owner's stop, by the hang it guards against). UMRS: a provider key in the worker env, the rail gw admitting another run, admitting `/venue`/`/cancel`, the owner's client writing, any attested session writing, an attestation outliving its call, a worker creating a run, another run's gate readable, a decide verb on the rail, the disabled venue not refused, caps never refusing, an unbounded plan passing, no validation, cancel leaving the worker, a client-hosted run resumed on the venue, the chat command for a plan run, `done` ignored, the loop hint ignoring the venue, venue runs counted as units, the reopen SQL, the backend hosting a venue run, a gate with no open attempt. r4: a self-answered gate, an unanswered gate read as a no (timeout and stop), the stop ignored, the user's `/gw` route from the sandbox, the cap not applied, the cap raising, the attempt numbering, the record not required, `model@source` admitted, the default keeping its source, the baseline ignored, the follower deciding without a person, the chat tool ignoring the venue, the output not fenced, no close output, the CLI ignoring `--venue`, the refusal not named, no `done`, `--plan-run` ignored, a decide verb on the rail client, a client-hosted record hosted, cancel doing nothing, resume resending a definition, no source required.

**Deferred.**
- A server-driven research loop of plan runs (above).
- Site models (EndpointRegistry rows, `/umrs/llm`) reachable from a sandbox through `umrs-infer` (D-63's server leg for the venue); per-agent source routing in the worker.
- A worker that dies without reporting leaves its run's attempt RUNNING until a resume supersedes it (the client's follower names `worker_ended` once the session has ended); the session's end does not yet fail the run's attempt.
- Agents in the worker run safe tools only; a server-side permission ladder for writers (and the card's grants) is not built. `--from-phase` and a TUI/bridge surface for server runs are follow-ups; listing a user's server runs is the jobs list's.
- A run recorded as client-hosted cannot move to the server venue (its hosting is part of its immutable spec).
- A live leg: converge with the venue enabled and the worker image rebuilt, then one `r4 team plan run … --venue server` receipt.

**To enable it on a site (operator; no deploy host was touched).** The frontend venue must be on and configured: Ansible `umrs_front_frontend_venue_enabled: true` (group_vars `umrs_front.yaml`) renders the API containers' `UMRS_VENUE_*` env: `UMRS_VENUE_ENABLED=true`, `UMRS_VENUE_INFER_HOST` (`umrs_front_venue_infer_container`), `UMRS_VENUE_INFER_PORT`, `UMRS_VENUE_INFER_UPLINK`, `UMRS_VENUE_INFER_RESOLVE_VIA` (the egress-net gateway), `UMRS_VENUE_API_UPSTREAM` (`umrs_front_venue_host_loopback`:API port), `UMRS_VENUE_WORKER_IMAGE` (`umrs_front_venue_worker_image`, default `localhost/r4-companion:latest`), `UMRS_VENUE_NGINX_IMAGE`, `UMRS_VENUE_SQUID_IMAGE`, `UMRS_VENUE_SUBNET_POOL`, `UMRS_VENUE_SESSION_ROOT`, with `umrs-infer` running and the seal keypair in place (`UMRS_SEAL_PUBKEY_B64`, the `umrs_front_venue_unseal_secret` podman secret). The worker image must contain this slice (`r4 venue worker --plan-run`): converge once with `-e umrs_front_venue_rebuild_worker=true`; the API servers must run the slice-8 UMRS. Users push the source the worker's models come from (`r4 source push <name>`). The project's ADR-067 wall clock (`PUT /gw/projects/{id}/budget`) bounds every run's own.

### Slice 8b (2026-10-12)

Maintainer decisions of 2026-10-12 on the two items slice 8 deferred: build both; the gate timeout default (24 h) is accepted. Built on `wip/a92` (r4 `4abc25f0`, `1d948f04`) and `wip/umrs-a92` (UMRS `8b9e5849`, `2dcd827c`), not merged. Where this differs from the slice-8 text above, this is current: the two "adjusted, with reason" paragraphs and the first two Deferred bullets are superseded. No live container, venue or model.

**Site models from the sandbox (D-63 venue leg).** `umrs-infer` gains the reserved `site` source: a fixed UUID (`uuid5`, it fits the leases' `UUID[]`) a session gets with `--source site` at start (no pushed credential behind it; kept out of the start's pushed-source class gate). A sandbox selects it under its sidecar's `site/` prefix (`model@site` in a plan: the worker's rail client routes it there and sends the bare model). The proxy serves it from a READY/DEGRADED `EndpointRegistry` row of the lease's OWN project only (no other project, no shared rows; `site_scope_missing` without a project, `site_model_not_found` otherwise), rewrites the model to the row's served name (ADR-068), strips every inbound credential and injects the site's service key (`GATEWAY_LITELLM_SERVICE_KEY`, else `LITELLM_MASTER_KEY`, in `umrs-infer`'s env; the sandbox never holds it), and passes only the model verbs (`/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`; `GET /v1/models` lists the project's served names). The internal listener is reachable only because the registry named it; the pushed-source path is unchanged (its public-host pin still refuses an internal upstream, pinned by a test). ADR-062 server-side: a registry row that is a public passthrough (`metadata.locality: public` or a public provider kind) is refused for a sensitive project; self-deployed rows are its own route. r4: `hosted_definition` admits `model@site` (any other `@source` is still refused for the venue), keeps it on the pinned default, and the worker keeps it.

**A server-hosted loop of plan runs.** Not a new loop, driver or engine, and UMRS does not interpret plans: the existing loop (`research_loop_job`, `EngineLoopRunner`, ADR-036) gains one iteration executor kind (`umrs/knowledge/plan_iteration.py`), wired exactly as the agent executor is (`_iteration_hook`, `_cancel_check`, `_approval_gate`; job hosting and thread hosting both pick it for a loop whose record carries `metadata.iteration_plan`). Each round:
- is the `plan_run` action, classed `cost`: the runner's own gate parks an ask-mode loop (the default for a plan loop with no mode) on the durable approvals inbox, and a profile naming `cost`; a human's `no` stops the loop with nothing run (`approval_rejected:plan_run`);
- binds the loop's goal (`objective`) and its own code-derived record of earlier rounds (`context`: verdicts and scores, never an agent's words) into the template the r4 client resolved and sent at start, with the stdlib grammar (`prepare_plan`); the plan's wall clock is min(its own, what the loop has left of its ADR-067 cap), and the run's caps are the tighter of the project's and the loop's; under a second left nothing starts (`budget_exhausted:wall_clock`);
- starts one slice-8 venue-hosted run (`<loop_id>-it<n>`, the loop owner's, the loop's venue sources), waits for the outcome its worker recorded, and cancels it if the loop is stopped meanwhile;
- decides from the shared table: a pick publishes (the loop completes, `state_reason: published:…`), no pick or a near-tie continues, a run that did not finish (a rejected gate, a spent budget, a failure, a stop) stops the loop with its reason;
- on publish, stages one PENDING proposal through the existing 0069 lane (the project's first graph; `add_finding`, confidence 0, producer-tagged, untrusted; a person decides it, ADR-050; the loop has no decide path) and records a Catalog candidate on the iteration (`written: false`).
The plan's own gates still go to the inbox under `plan-run:<run_id>` (slice 8), not under the loop's id.

**One table** (`r4_contracts/loop_plan.py`, stdlib, `contracts_fence` green): the loop templates and the params the loop owns, the decision table, the wall-clock min, the round record and context, the finding operation and the Catalog candidate. r4's local loop (`runtime/plan_executor.py`, and `runtime/api.py`'s decision constants) and UMRS's server loop read it; `r4-contracts/tests/test_loop_plan.py` is the one pin, each side has one consumer test.

**The start path.** `POST /umrs/loops` with `iteration_plan` is accepted where the site's venue is configured: before anything exists it checks the shared table (a loop template, no loop-owned args), a venue source (`plan_sources`) and the grammar (`plan_invalid`, every problem). Without the venue the refusal is unchanged: `plan_iteration_needs_server_venue` with the missing settings and the pointer. r4: `UmrsRuntime.start_loop` sends the resolved template, the args, the sources, the default model and the mode (none → ask); a plan loop without a source is refused before anything is sent; UMRS's named refusal is raised as `PlanIterationRefused` with UMRS's words. `research_loop_start` gains `sources` (pushed names and/or `site`).

**Tests.** UMRS `tests/test_infer_proxy_site.py` (11), `tests/test_plan_loop_server.py` (20; the runner's real ask gate over the real inbox, a fake venue host); r4-contracts `tests/test_loop_plan.py` (14); r4 `r4/tests/test_plan_server_venue.py` (+7: `model@site` routing, the server-loop payload, the table checked before sending, the server's refusal by name, the chat tool's sources, the local loop reading the table). Changed pins, and why: slice 8's loop-route test with the venue enabled now expects a plan loop to be checked, not refused; slice 6's UMRS-runtime and chat-tool refusal tests now expect the refusal only for a plan loop without a venue source.
**Mutation check:** 13 on the site source (9 UMRS: another project's row, credentials forwarded, the public-row gate, the lease's site source not required, any path, no project scope, no served-name rewrite, no service key, the start needing a pushed `site`; 4 r4: `@site` routing, refused, stripped by the worker, stripped from the default) and 28 on the loop (8 on the shared table, two through UMRS's tests: a pick continuing, a near-tie publishing, a failed run continuing, the wall clock max, an agent's words in the context, a finding established; 15 UMRS: the action kind, a rejected gate running, the wall clock not passed down, publish or stop ignored, a stopped loop leaving its run, the loop deciding its proposal, runs without the venue, forgotten rounds, the start route without the venue check or the plan check, no ask default, job hosting ignoring the plan, the caps' max; 6 r4: no ask default, no source check, the refusal unnamed, the raw template sent, the tool dropping sources, the local loop keeping its own table), each killed by its test.

**Deferred (replacing slice 8's first two bullets).** A pushed source named `site` is shadowed by the reserved one (refusing that name at push is a follow-up). The plan's gates are listed under `plan-run:<run_id>`, not the loop's id. A proposal targets the project's first graph and skips the r4-side acquisition-constraints check (the propose lane itself is the decision gate). A live leg for both.

**To enable (adds to slice 8's list).** Site models: `umrs-infer` needs the registry and the site key in its env: `UMRS_REGISTRY_BACKEND=postgres` with `UMRS_REGISTRY_POSTGRES_DSN` (the servers' registry), and `GATEWAY_LITELLM_SERVICE_KEY` (or `LITELLM_MASTER_KEY`); the registry rows' hosts must be reachable from `umrs-infer`'s network. Plan loops: nothing beyond slice 8's venue settings (the loop host is wired at boot).

## Rollout-status (2026-10-12) — Slice 9 (2026-10-12)

Built on `wip/a92` (r4 `55748539`, `5494afd4`) and `wip/umrs-a92` (UMRS `55085cb8`), not merged. Earlier blocks are not rewritten; where they differ, this block is current. No live model. With this slice every slice of the rollout plan is built: 1–7 and 8 (with 8b) on the same branches; merge waits on the maintainer.

**First, the slice-8b follow-up (maintainer, 2026-10-12).** A user push of a source named `site` (any case) is refused by name (`usersources.bad_request`, "reserved"), before anything is sealed or stored: the venue's reserved `site` source would shadow it at every start.

**Native role turns go through the AgentBroker** (`r4/src/r4/teams/native.py`, `teamup/session.py`). The native engine runs every role turn as one `AgentTurnRequest` to the run's `RuntimeServices.agents`: the plan engine's own governed turn path (D-3: a forked permission manager, the deny set, the ADR-062 gate, every round charged to the run's `BudgetMeter`). The role's context is the prompt, its prompt the instructions, its model the request's model, and it gets no tools beyond the terminal channel (as its single chat had none). A turn the broker could not complete is that role's named error (`roles_failed: <role>: agent turn failed: <reason>`); a spent meter ends the run between iterations; the run's metrics are the broker's per-turn usage. The session's client in a native run refuses any model call by name, and a host without an AgentBroker is refused (`agent_broker_required`). Removed: the ModelBroker chat bridge (`_BrokerChatClient`, `_chat_via_*`) and its token-delta charge; `chat_once_factory` left production (a test double in `r4/tests/role_fixtures.py`). There is no second turn loop: TeamupSession's `_role_turn` is either the AgentBroker (an engine run) or the role-turn runtime (`run_agent_turn`, interactive sessions), and the broker runs the same runtime.

**BudgetTracker.** Retired in slice 4; confirmed with no residual import or alias (a source scan is pinned). Every team run meters through a `BudgetMeter`. `orphan_module_detector` (0 candidates) and `dead_compat_bridge_validator` (0 dead) report nothing to delete, so nothing else was removed.

**Provider `response_format`, an optimisation only** (`r4/src/r4/client.py`, `teams/agent_broker.py`). Where the route honours it (`Client.honours_response_format`: the OpenAI-compatible HTTP path posts the payload verbatim, a cancellable call through an OpenAI-compatible provider rides that path too, any other provider object drops unknown keys; `R4_RESPONSE_FORMAT=off` disables it), the broker's one repair call carries the step's schema as an OpenAI `json_schema` format (`strict: false`). The reply is validated by the stdlib validator exactly as a plain reply is (D-5 unchanged: the validator is the authority). A call that fails with the format runs plain, logged and guarded as any round; a cancelled turn is never retried. The companion's own rounds keep the typed `final_output` tool: a response format there would forbid the tool calls an agent needs.

**Tests.** r4 `r4/tests/test_native_through_broker.py` (8: every role turn an AgentTurnRequest with its instructions and model, metrics from the broker, the meter and a spent budget, a failed turn named, no broker refused, the client refusing direct calls, a turn from a running loop, the source pins incl. BudgetTracker), `r4/tests/test_plan_response_format.py` (10: the same result with and without the format for valid, schema-invalid, non-JSON and fenced replies; the format carried where honoured; the plain fallback named; a cancelled turn not retried; no format without a schema; which routes honour it; the payload); UMRS `tests/test_sealed_sources.py` (+3, DB-free: `site`, `SITE`, `Site`). Removed, with reason: the two `_chat_via_inner_client` metering tests and the chat-once model pin (the path is gone; the role's model now rides the request, pinned). Native bench and resolver runs now script the broker.
**Mutation check:** 18 targeted reverts, each killed: the reserved name accepted, or case-sensitive; turns bypassing the broker, the broker not wired, the role's model dropped, a failed turn read as an answer, no broker silently tolerated, a spent meter ignored, metrics not the broker's, the stub client calling through; the format always or never sent, no plain fallback, a format reply trusted unvalidated, a cancelled turn retried plain, the client dropping the format, a dropping provider claimed to honour it, the off switch ignored.

**Deferred (judged to stay so).**
- Interactive teamup sessions (the HPC preset, iterated by hand) still run their roles on the role-turn runtime with the host session's fork and usage hooks, not through a run's AgentBroker: they have no run, run id or token budget to meter against. Giving them one is a product decision, not convergence.
- `response_format` is used for the repair call only, and is not verified live on any provider; per-provider support beyond "the payload reaches an OpenAI-compatible endpoint" is not probed.
- Everything every earlier block deferred and this one does not name: notably the live legs (slice 8/8b containers, a real model), a dead venue worker's attempt staying RUNNING until a resume, `--from-phase` and TUI surfaces for server runs.
