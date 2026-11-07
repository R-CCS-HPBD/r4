# ADR-084: Governance of knowledge growth from work — tier and validity, the origin-actor rule, fact-level proposals, run memory, results adapters, the KG in the loop

**Status:** Proposed (2026-10-03)
**Date:** 2026-10-03
**Relates to:** D-35 / migration 0106 (two tiers), ADR-064 (one loop driver — no new engine), ADR-066 (ask-the-scientist inbox), ADR-074 (no user code in the server), ADR-077 (bound storage), ADR-050 (visibility), ADR-081/082 (typed spine, anchors), ADR-085 (read verbs), gap rows D-32, D-33, D-34, D-35, D-36, D-43, D-44, D-45, **D-60**; answers the design question "Governance of semi-automatic KG growth"

## Context

Background, method and report: [ADR-081](081-typed-knowledge-spine.md) and
kg-multimodal-investigation-2026-10-03.md.
The design says the catalog grows from every cycle — entities, tested
procedures, negative results, open questions — and is placed in front of
the model on the next cycle (`docs/R4-UMRS-DESIGN.md:156`). Verified at umrs
`d28978ad`, the live growth path does the opposite:

- **Findings bypass review.** `finding_ingest` upserts nodes with no status
  (`knowledge/finding_ingest.py:181-185`), so they take the dataclass
  default `status: str = "established"` (`knowledge/store.py:83`) against
  the 0106 intent — KG-13. The live tier mix cannot be observed through the
  API, because `list_nodes` / `list_edges` never SELECT status
  (`store.py:719-725, 848-853`; API-02) and report the default for every
  row; it stays unknown until a DB-side query is run. The default,
  example-project-a and r4-dev graphs have 0 proposals (live `graph_proposals`,
  read-only).
- **Provenance fans out.** `_collect_artifact_ids` appends every artifact
  of the project to each successful job's outputs
  (`jobs/knowledge_adapters.py:3036-3055, 3073`). Live: one example-project-b
  result (`wfart-<id>`) has 67 `produced_by` edges to jobs of six
  workflows; a KG search for that project's tracking workflow returns
  100/100 wrong artifacts — ING-03, LIVE-02.
- **Platform failures pollute science.** Any failed job, including the
  KG's own understand/embed chain, writes `fail-` nodes and inverted
  `caused_by` edges (`jobs/adapters.py:1781-1798`,
  `knowledge_adapters.py:3624-3720`); the example-project-a graph is 45
  failures out of 113 nodes and has no science node — LIVE-11, KG-20.
- **Runs carry no science.** Job nodes hold only job id, type, workflow,
  step and user (`knowledge_adapters.py:3376-3382`); the detector model and
  parameters live only in `job.spec.params` (PROV-01). LLM-extracted nodes
  record no model or prompt (`knowledge_adapters.py:1383-1390`), although
  `a2l_agent_executions` already carries `model_ref`, `prompt_hash`, params
  (`migrations/0077_a2l_conservation.sql:84-100`).
- **Approval is not bound to facts.** `proposed_operations` is stored and
  never consumed; the guard checks only target and status
  (`knowledge/graph_registry.py:492-524`, `api/_knowledge_mutation_guard.py:48-112`)
  — KG-19. The actor is a self-declared header, missing = user
  (`_knowledge_mutation_guard.py:29-31`).
- **The loop never reads the KG.** The engine is built with
  `knowledge_store=None` (`api/loop.py:355-359, 1092-1107`); a real-mode
  iteration runs the workflow and then fails the evidence guard
  ("knowledge retrieval returned no evidence", live `loop-<id>`)
  unless a placeholder plan anchor (`anchor:<id>`) satisfies it
  (`knowledge/loop.py:654-664`; a live `finding-<id>` was minted
  that way) — LOOP-01, PARTIAL; the guard runs at phase 9, after phase 5 executed
  (`knowledge/loop.py:517-528` vs `553-576`) — LOOP-07; finding text is
  hard-coded Demo-08 chemistry (`knowledge/loop.py:43-51, 1143-1215`) —
  LOOP-04; planner template fields are injected into user workflow params
  (`knowledge/loop.py:851-867`) — LOOP-06; loop-triggered jobs omit
  `ingest_results` (`knowledge/umrs_workflow_port.py:79-91`).
- **Measurement and output are dead.** The D-44 acceptance-rate route
  returns 500 live (KeyError, TC-03); `build_publication` has no caller
  outside tests (DEAD-01); skill promotion compiles from IR text, not from
  procedures a loop validated (SKILL-01). The example-project-b retractions,
  negative results and parameter lessons exist only in markdown and JSON
  (DATA-03), and one label file's annotator is recorded inconsistently
  (DATA-06).

## Decision

**D-1 — Two axes, recorded separately.** *Tier* is `status`
proposed|established (0106): the upsert never promotes; establish is human
only. *Validity* is `invalidated_at/by/reason` plus claim lifecycle
(ADR-081): retraction and supersession never DELETE. Machine-written wrong
facts (fan-out, bad adapter output) are repaired by an audited job, dry-run
first; human-established facts are only ever invalidated.

**D-2 — The tier rule (maintainer sign-off required).** Judgements are
always proposed: claims, captions, LLM output, entity merges, supersession
links, loop findings. Records from deterministic registered adapters (run
records, header probes, 1:1 provenance, table observations under a declared
map, ground truth as authored) inherit the triggering actor: user →
established; agent or loop → proposed, promotable per run in one action
("establish run"). Rows imported without tier information land proposed,
confidence 0. `origin_actor` rides every job spec and every
`knowledge_write_batches` row; annotator identity (human, ai_agent,
pipeline, unknown) is explicit on annotations.

**D-3 — Fact-level proposals.** A proposal carries typed ops (`add_node`,
`add_edge`, `assert_claim`, `supersede`, `invalidate`, `same_as`,
`add_term`, `declare_attribute`), validated by the ADR-081 `TypingContext`
when proposed. An approved proposal executes exactly its ops; every row it
writes carries its `proposal_id`; the decision route refuses agent actors
server-side. Rollback = retract proposal P, which invalidates every row
carrying P. `acceptance_stats` is fixed and reported per op kind (D-44).

**D-4 — Runs, the run_key memo, failure signatures.** Job hooks write
`r4:WorkflowRun` / `r4:StepRun` (natural key = job id) with
`workflow@version`, `run_key = sha256(workflow@version |
canonical_json(params − volatile keys) | sorted input sha256)`, venue,
hardware, elapsed and GPU seconds, outcome; `prov:used` → parameter set,
model, inputs; `prov:wasGeneratedBy` from that run's own outputs only (a
repair job invalidates the existing fan-out edges);
`prov:wasAssociatedWith` → workflow and actor. Failures become
`r4:FailureRecord` keyed by a signature (sha1 of the exit reason with ids,
hex, numbers and paths masked; a repeat increments `count`) with
`failure_class` routing | resource | environment | input | code | timeout
| platform_internal | unknown. `platform_internal` records (the KG's own
chain) get visibility `ambient`, the admin-only lane (ADR-050 wave 2.1),
and never appear in science answers. Not `system`: in this codebase
`system` rows are readable by every caller in every project
(`visibility_predicate`, `umrs/src/umrs/knowledge/access_scope.py:182`), so
failure exit reasons, paths and user ids would leak across projects.
Project-visible failure classes (routing, resource, input …) keep project
visibility and are excluded from science answers by class filter.
Extraction records `llm_model` and `prompt_version` in the IR.

**D-5 — `KNOWLEDGE_RESULTS` result adapters.** A deterministic step in the
existing auto-ingest chain applies, in order: (a) a declarative `kg_emit`
map in `workflow.json` (output glob → class or class-by-column, natural-key
columns, attribute map, edges, region recipe with `time_basis`, support
rows, keyframe sampling); (b) a step-written `kg_fragment.json` v2 (CURIE
classes, natural keys, attrs, regions; ≤ 5,000 nodes / 10,000 edges);
(c) the fallback: catalogue row, asset node, PROV edges. It reads each
step's `output_keys` through the ADR-077 binding. Declarations are data:
no user code runs in UMRS (ADR-074). First adapters: an object-tracking
workflow with its ground truth, scoring and params; a video inventory and
metrics; MAE reconstruction, SAM3, HMAE.

**D-6 — `KgReadPort` in the existing loop.** `api/loop.py`
`_get_engine` / `_engine_factory_from` pass a `KgReadPort` (the ADR-085
services). `plan_context(goal)` returns ≤ 2 KB: current established
decisions and claims; proposed items, marked; negatives (failure
signatures, NegativeResults, refuted hypotheses); a parameter-set → metric
table for the target workflow; prior identical runs. Template fields are no
longer merged into user params. No new engine (CONSTITUTION §1.6, ADR-064).

**D-7 — Pre-flight before phase 5.** An exact `run_key` hit applies the
project's reuse policy `allow | ask | never` (default `ask`, through the
ADR-066 inbox); a known failure signature for the same workflow and venue
blocks the submit (ask, or change venue); params matching a current
NegativeResult need an explicit justification; hypothesis anchors must
resolve to real nodes.

**D-8 — Write-back, proposed.** Loop runs carry `ingest_results` and
`origin_actor='loop'`. `finding_ingest` mints `r4:Finding` as proposed with
`prov:wasGeneratedBy` → the loop iteration, `prov:wasDerivedFrom` → typed
result nodes (merge upsert, no evidence clobber), `r4:supports|refutes` →
the hypothesis, and `{statement, value, unit}` from adapter metrics, never
from regexes; the engine refuses to mint when no metric node is cited. A
failed or below-baseline run mints a proposed `NegativeResult`. Agentic
iterations persist Hypothesis and LoopIteration nodes, proposed.

**D-9 — Downstream consumers.** A run whose metric beats the incumbent on
an established ground-truth set creates a `pending_review` compiled skill
with lineage (never auto-enabled). `build_publication` reads typed claims
and their PROV chains and refuses proposed or invalidated claims unless
labelled. Per loop: repeat-failure rate, reuse rate, tokens per iteration,
acceptance rate, claims superseded.

### Why

- Two axes answer both review questions at once: "did a human vouch for
  this?" and "is it still true?". Retraction keeps the record, which is
  itself knowledge (example-project-b's retired ground truth is evidence about the
  detector).
- The origin-actor rule keeps review load proportional to judgement: the
  prototype wrote tens of thousands of established example-project-b rows
  from deterministic adapters and a few hundred proposed judgements;
  "everything proposed" would put tens of thousands of items in an inbox.
- Run memory and failure signatures are the cheapest efficiency the loop
  can get: not re-running a known failure costs nothing.

## Consequences

- Positive: the loop stops repeating failures and reuses identical runs;
  negatives are reachable; review is per run or per proposal; any bad batch
  can be rolled back by id.
- Cost: reviewer load from extracted claims (per-section rate limits, D-57
  triage, clustering by subject, per-graph pause on a falling acceptance
  rate); adapters to write per workflow; the tier rule is a judgement call.
- Until server-derived actor identity lands (a separate architecture-review
  ADR: UMRS-minted session tokens with `act=agent`), the actor header stays
  self-declared; companion-originated writes default to agent.
- NOT in scope: auto-establish of anything; the actor claim itself; review
  UI (ADR-085 lists the surfaces); bundle carriage of tiers (ADR-032
  addendum).

## Alternatives Considered

- **Observation Spine** — mapper records land established by fiat,
  regardless of who triggered them (contradicts D-35).
- **Verb-First** — transcriptions established by default (contradicts
  D-35). Grafted: the failure taxonomy, rollback by `proposal_id`, run
  memory with near matches and negatives.
- **Everything proposed** — safe but unreviewable at science scale.
- **Do nothing** — findings stay established and unreviewed, provenance
  stays fanned out, and the loop keeps failing its own evidence guard.

## Acceptance receipts

1. One example-project-b loop on rccs mints proposed findings citing event and metric
   nodes; a rerun with an identical `run_key` is offered for reuse through
   the inbox.
2. A known failure signature blocks a submit before phase 5.
3. The fan-out repair invalidates the wrong `produced_by` edges with audit
   rows; retracting a proposal invalidates exactly its rows.
4. The decision route refuses an agent actor; `acceptance_stats` answers
   200 on rccs.
5. A non-admin search or graph read in project X never returns a
   `FailureRecord` from project Y; `platform_internal` records are visible
   only on the admin lane.

## Evidence (2026-10-03)

- Prototype (`~/tmp/kg-multimodal-bench-2026-10-03/proto/`): runs with
  `run_key`, failure signatures and a `runs` verb (ADR-085) over
  example-project-b (several hundred step runs) and example-project-a (about
  fifty job runs, most of them failed, grouped into a dozen failure records).
  Its provenance fan-out was kept and flagged (one artifact had six
  producers, five recorded after the artifact existed), not repaired — the repair job is unbuilt.
- Benchmark (ADR-085 has the arms; static questions, no loop): Haiku
  provenance 0.29 / 0.50 / 0.50, aggregation 0.38 / 0.50 / 0.62,
  negative-result 0.64 / 0.57 / 0.57. Pre-flight, memo and write-back were
  not exercised; their receipts are live legs.

## Rollout-status (2026-10-03)

Proposed; nothing landed (see ADR-081 for what the investigation touched;
wave ids are the report's §7.3). Wave W0 (no ADR; report §7.2 items 3–4)
already covers findings → proposed, the fan-out stop and `origin_actor`
threading. D-5 (result adapters) lands with ADR-082 in W3; the rest of this
ADR gates W5. Open: the D-2 tier rule
(maintainer); the default reuse policy per project; whether the
`a2l_agent_executions` ledger becomes the extraction-provenance join.

## Related

- [ADR-064](064-one-loop-driver-engine-runner.md), [ADR-066](066-ask-scientist-policy-pause-profile.md), [ADR-074](074-uploaded-step-code-runs-sandboxed.md), [ADR-077](077-per-project-external-storage-binding.md), [ADR-050](050-visibility-and-session-history-build.md)
- [ADR-081](081-typed-knowledge-spine.md), [ADR-082](082-multimodal-anchors-catalogue-plane.md), [ADR-085](085-agent-kg-verb-contract.md)
- docs/development/kg-multimodal-investigation-2026-10-03.md

## Rollout-status (2026-10-09) — D-3's server refusal landed ahead of acceptance

The decision route now does what D-3 prescribes, as a security fix (KG-05 wave), while this ADR
stays Proposed. `POST /umrs/knowledge/proposals/{id}/decision` refuses `x-r4-actor: agent` with
403 `proposals.forbidden`, which follows the skills-review precedent (`_refuse_agent_review`). It
also needs read and write access to the target graph (ADR-080). The other proposal routes answer
only for a readable graph. Not done here and still this ADR's to decide:
- the tier on read (`status`/`confidence` in the read SELECTs);
- a human decision surface. The Companion `knowledge_graph_decide` agent tool is the only caller
  (STATE `KG-20261009-001`).

The actor header remains self-declared (see the server-derived identity note above).

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
