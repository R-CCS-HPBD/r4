# ADR-037: Tool selection as an explicit companion phase

**Status:** Draft
- **Amended:** 2026-09-05 (agent-debug round 2/3 — Rule 2d availability
  gating, unavailable roll-up suffix, denied-bucket attribution fix,
  tool_route_liveness advisory validator)
- **Amended:** 2026-09-03 (review round 2 — rules 2a/2b/2c, Rule 3 skip,
  reorder, process debt)
- **Date:** 2026-09-03
- **Related:** ADR-035 (enforcement matrix; selection ≠ enforcement),
  ADR-036 (headless loops need durable phase records),
  ADR-028 (turn-as-attempt), prompt doc
  `docs/development/prompt-tool-selection-phase.md`

## Context

The companion assembles the model-call tool set implicitly:

- `r4/src/r4/companion/session.py::CompanionSession.__init__` — builds
  `self.tools = get_builtin_tools(client)` once per session
  (core + client + project/teamup + extra + agent-builtin sources).
- `r4/src/r4/companion/session_flow.py::start_turn` — per turn,
  `session_turns.active_tools(tools=…, active_tool_names=skills.active_tool_names())`
  applies the skill allowlist filter (ALWAYS_ON union when a skill is active).
- `r4/src/r4/companion/agent.py` — per model call, `_prune_denied_tools`
  hides user-denied tools at the agent layer.

Measured live surface (2026-09-03, default profile against umrs-front):
`get_builtin_tools(client)` returns **130 tools** with a live client
(37 with `client=None`), plus **6 enabled MCP servers ≈ 22 tools**
(default-knowledge-tools 4, default-platform-tools 4, default-rag-search 3,
hatch-\* mirror set ×3 — note the default/hatch name duplication, two
registrations of the same packages). The doc's "142 tools" scenario is
already today's reality, and this platform's models are offline vLLM:
tool schemas are paid in context tokens on every call and local models
discriminate poorly across large catalogs.

Skills are similarly eager: `skills.active_system_prompt()` concatenates
the **full system-prompt bodies of every active skill** into the system
message every turn (`skills.py`), and auto-activation fires on keyword
match per turn (`start_turn`).

## Decision

Introduce an explicit, observable **TOOL_DISCOVERY → TOOL_SELECTION** phase
in the companion turn loop, ahead of TOOL_EXECUTION:

```text
THINKING → TOOL_DISCOVERY → TOOL_SELECTION → TOOL_EXECUTION → OBSERVATION → NEXT_ACTION
```

Discovery enumerates what exists and from which source; selection decides
what is offered to the model for this step and records why the rest was
not. Each model call carries a `ToolSelectionRecord`:

```json
{
  "type": "tool_selection",
  "turn_id": "t-…", "step_index": 3,
  "available_tools": 130,
  "discovered": {"builtin": 108, "skill:code": 6, "mcp:pg-lab": 16},
  "filtered_out": {
    "mode_readonly": {"count": 23, "sample": ["write_file", "workflow_run"]},
    "capability_tier": {"count": 9, "sample": ["mcp:pg-lab.drop_table"]},
    "disabled": {"count": 30, "sample": ["mcp:gis.*"]},
    "dedupe": {"count": 2, "sample": ["search (mcp:a) shadowed by search (builtin)"]}
  },
  "candidate_tools": 78,
  "selected_tools": ["workspace.search", "file.read", "terminal.exec"],
  "selected_skills": ["code"],
  "strategy": "passthrough | policy_filter | ranked",
  "ranking": null,
  "selection_latency_ms": 12,
  "schema_tokens_estimate": {"before": 18400, "after": 3100}
}
```

Caps are load-bearing: every list is bounded (`sample` ≤ 5,
`selected_tools` ≤ 50 then `+N more`) so the record never costs more than a
few dozen tokens.

### Rule 1 — Instrument before optimizing

Ship the observable phase with `strategy: passthrough` first (all tools
offered; record still emitted). No second LLM call for selection — that is
forbidden at any strategy level. Ranking lands last, behind a threshold,
once the benchmark (item 7) proves worth.

### Rule 2 — Selection is never the enforcement point

Selection filters exist for **context cost and UX only**. Authoritative
denial stays where it already lives: `PermissionManager` at execution
(client) and UMRS authz at the RPC (ADR-035 matrix). A tool removed at
selection MUST still be denied if invoked directly; the item-5 test proves
it per filter. If a filter ever removes a tool execution would have
allowed, that is a reportable finding (classification disagreement between
selection and enforcement), not a silent fix — the ADR amendment at that
point names which classification wins.

### Rule 2a (amendment) — Identity wins for capability tiers; shadow until landed

Round-1 finding: custom-MCP execution grants come from
`RIVAULT_MCP_GRANTED_CAPABILITIES` — process env, default `STANDARD_USER`,
decoupled from the caller's identity. Ruling: **identity wins, and the env
var becomes a transport, not a source**.

Target shape (server work, tracked separately): UMRS computes the grant
from the verified identity + tenant tier at mini-RiVault provisioning and
injects the var; the runner refuses to start in any non-local profile when
absent/unsigned (`STANDARD_USER` survives only in compose/dev). Enforcement
matrix row + canary per the CANARY rule (a `STANDARD_USER` identity
invoking a `WRITE_REMOTE` tool MUST be denied).

Until that lands, `capability_tier` filtering at selection is **shadow**:
the record carries `would_filter.capability_tier {count, sample}` —
measured-only; when no capability-manifest data path exists (today:
server registrations expose package name + tool list, no capability
metadata), the bucket stays empty rather than fabricated, and NOTHING is
removed (honesty rule forbids removing on a classification execution
doesn't honor). The disagreement size becomes telemetry — the evidence
the server-path amendment needs.

### Rule 2b (amendment) — Plan mode: both layers allow the same read set

Plan mode previously offered `{}` tools and execution denied everything —
the plan card was written blind. Now `_send_plan_only_turn` offers the full
catalog gated to `PLAN_READ_ONLY = SAFE_TOOLS − {knowledge_graph_propose,
project_research_readiness}` (propose/readiness POST mutations; `project_research_readiness` retired 2026-09-07 with WP-07, the exclude set is `{knowledge_graph_propose}` since), execution
permits the same read set, and selection records the removal as
`filtered_out.mode_readonly {count, sample}`. One classification, both
layers; the record proves agreement.

### Rule 2c (amendment) — Duplicates are warnings, not shadows

Catalog-integrity faults (today: `default-*`/`hatch-*` twin registrations
of the same three packages) appear in the record as `warnings[]` naming
the pair + tool count, detected pairwise by normalized name (strip
`default-`/`hatch-`) + tool-set equality. No name is shadowed (execution
allows both, so selection reports both — the fix belongs in config).

### Rule 2d (amendment, 2026-09-05) — Availability gating is a precondition, not selection

Rule 1 (passthrough) is preserved: **no model-side selection is added**.
But a harness-side availability gate (`r4/companion/tool_availability.py`)
removes, before the offered set reaches the model, tools the session
*structurally cannot serve*:

- tools whose executor callback is never wired by any surface
  (`tool_search`, `web_search` — dead-by-omission),
- tools whose only backing route is retired
  (`file_list_platform` → `/gw/data/files`, retired 2026-08-22;
  unconditionally dead),
- tools that degrade to a retired route when a required transport is not
  constructible (`file_upload_platform` without ZeroMQ; mirrors the tool's
  own definition-time precondition).

This is compatible with Rule 1 because it is *availability*, not
*selection*: what remains offered is what the surface could actually run.
Removals are recorded in the turn record under
`filtered_out.unavailable {count, sample}`, and both roll-ups (TUI bridge
and webview store) disclose the count as
` · N unavailable filtered`. Same-layer honesty fix shipped with it: the
set-difference attribution that mislabeled every removal as `user_denied`
is replaced by explicit denied-name plumbing, so `disabled` reports only
real permission denials (round-3 task-1 evidence: harness removals
duplicated into `disabled`).

**Advisory validator** registered in `tools/enforcement_maturity_scorer.py`:
`tools/tool_route_liveness.py` checks that every retired-route tool and
every never-wired callback tool is covered by the gate (advisory, never
fails CI; grade B at 7 tests).

### Rule 3 — Contracts ride v1 (additive), now with a real skip

`tool_selection` is added to `contracts/control/v1/`, matching the
`SessionCost` precedent: dedicated control event, additive under v1.
Python strict decoders raise on unknown *event types* by design
(`control_schema.from_dict`). **Amended**: the caveat is retired. Records
persist with sessions; an older binary attaching to a newer-written log
would crash on an unknown event type. `from_dict` now returns `None` with
a WARNING for unknown event *types* (skip, not crash — additive is only
additive if skip is legal). Future payload *versions* of known types
still raise (shape mismatch, not generation drift); `to_dict` still
refuses non-control events loudly (typos fail at the producer). The
replay fixture family added the `skip` expectation for this.

### Rule 4 — Durability of the record

The record is emitted as a control event over the JSONL bridge, persisted
with the session event log, and (for headless loop jobs under ADR-036)
carried as a durable attempt event so `/logs` and cost surfaces can audit
what was offered per step after the fact.

### Rule 5 — Honesty by construction

The TUI may only render selection claims the record supports
(`all 130 tools offered (no selection)` for passthrough; never "optimized"
language). DEMO MODE fixtures are labeled demo.

### Ranking threshold policy

`strategy: ranked` requires BOTH: explicit flag on, and
`available_tools > tool_selection.rank_threshold` (default 40, site config).
v1 ranking is lexical/embedding similarity between the user message + last
two turns and tool descriptions, with an always-on core set (file
read/search, final_output, ask_user). No extra LLM call. Ships only with a
benchmark row beating `policy_filter` first-call-correct at 150+ tools —
otherwise the flag stays off and the benchmark says why.

### Skills progressive disclosure

The same phase applies to skills: discovery lists skill names + one-line
descriptions only; full bodies load only when selected (user toggle,
`/code`-style proxy, or — later — ranking). `selected_skills` and the
body-token saving appear in `schema_tokens_estimate`. Measured today:
active skill bodies are concatenated eagerly every turn
(`skills.active_system_prompt`), so deferral is a real context saving, not
a bookkeeping exercise.

## Schedule (amended — reorder driven by measurement)

| Order | Item | State |
|-------|------|-------|
| 1 | Progressive disclosure of skill bodies (item 6) | **Shipped, with honest growth accounting**: `discovery_catalog_prompt()` + model-callable `activate_skill` (bodies land next turn, executor refuses inside plan mode by Rule 2b). Measured on the real default session: base extra-system ≈ **899** tokens/turn → with catalog ≈ **1398** tokens (+499). This is a *growth*, not a saving, until the catalog funds activations — payback threshold ONE skill body the model couldn't have requested before (default `umrs` body alone ≈ 784 tok > 499 catalog). Claim revised: nothing shrank; the catalog exists so the model can ask. |
| 2 | policy_filter (item 5) with shadow capability_tier | `would_filter` bucket in the record (measured-only); enforcement-matrix row + canary spec for the UMRS grant path. Capability manifests are NOT client-visible today (registrations carry package+tool list only) — the manifest-delivery RPC ships with the server work, not silently. |
| 3 | Family grouping (families-as-skills) | **Shipped (flag-off default) + measured utility floor**: `R4_TOOL_SELECTION_STRATEGY=family`. Live dump: 72 familied / **36 unfamilied** builtins — the filter ONLY reaches tracked families; utilities (shell_exec / apply_patch / hpc_* lanes etc.) stay offered unconditionally and are recorded (`utility_offered {count, sample}`). Fix per round-3 review; the silent-drop hole is closed. Slash wiring deferred (TUI workstream). |
| 4 | Benchmark (item 7) | **Landed, provisional scope labeled**: `tools/bench_tool_selection.py`, 23 synthetic tasks (direct + 3 indirect), no model calls by design — it measures whether the *lexical filter* keeps labeled tools, NOT whether the model picks them. Direct-task recall 1.0; indirect-task recall 0.910 (honest cost, kept). The turn-level decision — does a local model call the right tool more reliably from 18 candidates than 150 — is unmeasured: **model-in-loop row (first-call-correct via LiteLLM) is the remaining row; nobody should read 0.91 as a model accuracy.** Real-user first-messages (server-persisted sessions) are the honest indirect-task source; building that sample set is a standing task with privacy/anonymize review before any use. |
| 5 | Ranking flag (item 8) | **Left off — provisional, no further claim**. Family grouping drops the per-call surface below the rank threshold (150→18.8, 400→36.5; utility floor now included in those numbers) with recall 0.910 — but this is *filter-only* evidence. Verdict stays off, labeled as such, until the model-in-loop row exists. |

## Process debt (recorded, owned)

Concurrent workstreams in `packages/r4-tui-v3` collided with this ADR's
TUI items (staging saga: WIP import lines from another workstream leaked
into surgical patches; `skill-inventory` import existed at HEAD while the
lib file didn't). Rule going forward: separate worktrees + PR merges.
The commit-message line "flushTurnSummary gets the same call on merge" is
coordination debt to be tracked as an explicit task in the TUI workstream
(`flushToolSelectionRollup()` must be invoked from `flushTurnSummary(final=true)`
on merge).

## Consequences

Positive: per-step observability of a previously invisible decision;
context-token accounting becomes attributable; plan mode / capability tiers
gain a pre-filter location that visibly logs what it did; readiness for
ranking without premature complexity.

Costs: one more control event type on the wire; selection runs per model
call (latency measured and capped in the record — if selection itself
becomes a hotspot the record says so, that is the point).

## Report-back gate (items 5–8 block on evidence, not vibes)

1. Exact call site where tools are assembled (answered in Context).
2. Typical `available_tools` live (answered in Context: ~130 builtin +
   ~22 MCP = ~150).
3. Control-contract versioning path (answered in Rule 3: v1 additive).
4. Any tool selection would filter but execution allows (answered in the
   item-5 static analysis + test, to be appended as an ADR amendment when
   measured).
