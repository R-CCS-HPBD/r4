# ADR-085: Agent KG verb contract — ten verbs, the KGL line format, caps and handles, a counted context pack, tool consolidation, a knowledge skill

**Status:** Proposed (2026-10-03)
**Date:** 2026-10-03
**Relates to:** ADR-027 (D3 "one read verb" — refined into typed verbs over the same facade), ADR-080 (access guard on every graph read), ADR-076 (decision model as an optional context gate), ADR-037 (tool selection — untouched: this is registration and context, not selection), ADR-081…084, gap rows D-28, D-25, D-42, D-57, D-59, **D-60**

## Context

Background, method and report: [ADR-081](081-typed-knowledge-spine.md) and
kg-multimodal-investigation-2026-10-03.md.
The site agent's fixed cost per call is already about 18k tokens (104
builtin tool schemas ≈ 13.4–13.7k; system prompt, briefing and project
state ≈ 4.5k), against a working target of about 24k for `qwen3.8-27b` on
GB10 (a design target from the panel, not a measurement). Verified at umrs `d28978ad` and the parent tree (2026-10-02/03):

- **Tools return the wrong shape.** `knowledge_query` drops `source_id`,
  result type, node type and the ontology block
  (`r4/src/r4/companion/knowledge_graph_tools.py:144-161`) — KGC-04; it
  makes four sequential HTTP calls and caches nothing — KGC-11.
  `knowledge_status` / `knowledge_show` fetch the whole `/graph` union
  (about 4 MB) to count or list 50 nodes
  (`r4/src/r4/companion/knowledge_tools.py:105, 323`;
  `umrs/src/umrs/api/knowledge.py:482-483, 491-560`) — KGC-18, API-01.
- **Search misses and crowds out.** The graph lane is whole-phrase ILIKE
  with fixed scores (`api/knowledge_query.py:125-164`); the merge sorts raw
  scores across lanes (`search_api/__init__.py:273-277`), so 'yolo' returns
  10 catalogue rows at 4.0 and none of 13 matching KG nodes — KGC-02, KGC-03.
- **No traversal, lineage or tier reaches the agent** — KGC-17. The
  neighbours route runs over an in-memory view pinned to project `default`
  (`api/knowledge.py:405-412`); `walk_closure` enumerates every simple path
  (2.7 s on a 37-node DAG at depth 9).
- **No KG reaches the prompt, and context is miscounted.** Only briefing and
  project state are injected (`r4/src/r4/companion/agent.py:2746-2780`);
  `ContextManager.query_rag` has no caller (`companion/context.py:466-493`) — KGC-01,
  while D-28 is marked Implemented. `fit_payload` counts only the system
  prompt and `extra_system` (`r4/src/r4/companion/session_turns.py:118-124`);
  briefing (8,000 chars) and project state (4,000 chars) are appended after
  it — KGC-10.
- **Tool clutter.** 104 builtin tools (≈ 13.4k schema tokens) plus about 43
  MCP tools, including the `knowledge_search` tools of two live MCP
  knowledge servers, which always answer 503 (`api/knowledge.py:100-148`,
  `set_engine` never called; the servers' other knowledge tools were not
  tested) — KGC-16; read-only
  (dated note 2026-10-19, cleanup C-3a: the `knowledge_search` tool of the
  hatch-registered `knowledge-tools` server was re-pointed to
  `GET /knowledge/query`, the unified facade the r4 client uses — it no
  longer always answers 503, so KGC-16's premise is now "read-only but
  alive"). The second "live MCP knowledge server" KGC-16 names could not be
  found in the tree (only one package defines `knowledge_search`); likely
  two deployed instances of the same package.
  (dated note 2026-10-19, cleanup C-3b(i): the dead route itself —
  `GET /umrs/knowledge/search`, `api/knowledge.py` with its
  `_engine`/`set_engine`/`_get_engine` — is now deleted, with its
  B10 `TestSearchScopeBinding` tests; `/query`'s own pins
  (`test_missing_project_is_401_via_identity_gate`,
  `test_verified_project_header_overrides_param`, project-scoped lanes)
  were confirmed equivalent-or-stronger first. `POST /external/search`
  (D5/D6) stays.)
  KG tools are not concurrency-safe (`r4/src/r4/companion/tools.py:3147-3175`)
  — KGC-20. The builtin UMRS skill routes knowledge asks to the dead `/rag`
  (`r4/src/r4/companion/builtin/umrs/SKILL.md:54`).
- **Reads are not safe.** An explicit `graphs=` selection is resolved with no
  read check (`api/knowledge_query.py:181-216`) — KG-05, KGC-12, LIVE-10.
  Read verbs mint default-graph registry rows (`ensure_default_graph`,
  `api/knowledge_query.py:216`; `api/knowledge.py:478, 595`;
  `api/knowledge_graphs.py:103`) — LIVE-09. (The write route
  `POST /understand` also calls it, `api/knowledge.py:673`.) The verified project silently
  overrides the requested one (`api/knowledge_query.py:534-541`) — LIVE-01.
- **Nothing measures any of this** (KGC-09, TC-10): no knowledge ablation
  flag, no scientific suite, no recall@k.

## Decision

**D-1 — Ten verbs, one UMRS call each.** All routes sit under the graphs
router, HTTPS through the gateway (dual transport unchanged).

| Verb | Route | Cap | Returns |
|---|---|---|---|
| `kg_schema(graph)` | GET `/graphs/{g}/schema` | 2 KB | ontology, mode, classes with counts and tiers, relations, top attrs, time-base summary, `missing_required`, gap count; ETag = `graph_version` |
| `kg_search(q, classes?, modality?, like?, k=8)` | GET `/umrs/knowledge/query` (repaired K3) | 2.5 KB | RRF (k = 60, per-lane quotas) over FTS (OR mode), trigram, alias, text vectors, text → image; one typed hop; ADR-083 INSUFFICIENT |
| `kg_find(class, where[], near?, related?, cols?, sort?, limit ≤ 50)` | POST `/graphs/{g}/find` (read-only) | 3 KB | typed rows; profile display columns; `near` = region overlap incl. mirrors |
| `kg_node(id, rels?)` | GET `/graphs/{g}/nodes/{id}` | 2.5 KB | class, tier, attrs, missing attrs, grouped edges, ≤ 3 evidence fragments, history |
| `kg_lineage(id, depth ≤ 4)` | GET `…/nodes/{id}/lineage` | 2 KB | PROV chain as an indented tree |
| `kg_path(a, b, max_depth ≤ 4)` | GET `/graphs/{g}/path` | 1.5 KB | ≤ 3 shortest paths, hop-wise bidirectional BFS (one indexed query per hop) |
| `kg_evidence(id, excerpt?)` | GET `…/nodes/{id}/evidence` | 2 KB | asset facets, regions with fragment and basis, mirrors, keyframes, support rows; `excerpt` starts ADR-082 `media_excerpt` |
| `kg_claims(subject, kind?)` | GET `/graphs/{g}/claims` | 2.5 KB | current claims, then superseded/candidate with dates and reasons, then invalidated records, each with its source |
| `kg_runs(workflow?, params?, inputs?, failures?)` | GET `/graphs/{g}/runs` | 2 KB | exact `run_key` reuse, near matches with the diff, failure memory, current negatives |
| `kg_propose_facts(graph, reason, ops[])` | POST `/graphs/{g}/proposals` | — | ADR-084 typed ops; nothing mutates until a human decides |

Shared contract: `graph_readable` / `access_admits` on every selected graph
(ADR-080, D-59), typed 403 for a graph or project the caller cannot read;
reads never write (`ensure_default_graph` leaves the read path, D-42);
statement timeout 1.5 s; caps enforced by the server, always returning a
valid page and cursor; ids always present; hidden history always counted;
class filters closure-expanded; filters compile to parameterized SQL and
jsonpath (agents never send SQL); KG text is quoted, stripped of control
sequences, capped at 140 characters and tagged with its tier.

**D-2 — KGL v1 line format by default.** Header
`#<verb> <args> → <shown> of <total> [<ms>ms ~<tok>tok]`; `! ` warnings;
`h\t…` columns, then TSV rows with the handle first; `  > rel id class
“label”` expansions; footer `+N more cursor=…` and `(+N hidden:
invalidated a, superseded b; --all)`. The `st` column joins tier `E|P` with
currency `c|s|r|k` (current, superseded, retracted/invalidated, candidate).
`format=json` serves the TUI and web. Ids are the ADR-081 short ids (about
5 tokens). Token estimates are Japanese-aware (ASCII/4 + 1 per non-ASCII
character, or the served tokenizer when available).

**D-3 — A context pack, counted.** `<project_knowledge>` of at most 1,200
characters from `GET /graphs/{g}/slice?q=<turn>`: one schema line, ≤ 3 hits,
current-truth warnings on those hits, and known failure signatures for any
workflow the turn names. It goes in the user-turn suffix (the cached prefix
stays stable; confirmed per endpoint template), is cached by
`(graph_version, q-hash)`, and is counted inside `fit_payload` — which also
moves briefing and project state inside the budget (KGC-10). An optional
D-57 gate (P(needs project knowledge) ≥ 0.4) decides injection; without it
the pack is injected at a 400-character cap. This is context, not tool
selection (ADR-037 Rule 1 holds).

**D-4 — Consolidation.** The ten `kg_*` tools (about 1.5k schema tokens)
replace `knowledge_status`, `knowledge_show`, `knowledge_graph_list`, the
read half of `knowledge_build_from_context`, and the duplicate MCP
knowledge tools, including the two `knowledge_search` tools that always
answer 503. `knowledge_query` stays one release as an alias of
`kg_search` that keeps id, class, tier and evidence. Every read verb joins
`_CONCURRENCY_SAFE_TOOLS`.

**D-5 — A builtin `knowledge` skill.** It teaches the order: schema once;
`find` / `claims` / `runs` for typed questions; evidence before any claim;
search last; cite handles and fragments; answer INSUFFICIENT rather than
guess. The UMRS skill stops routing to `/rag`.

**D-6 — Humans use the same verbs** (R4 owns the UI): a TUI knowledge pane
(class tree, tier badges, struck-through invalidated rows) replacing the
first-40-labels server graph panel; `r4 kg timeline <asset>` (events, ground
truth with retired intervals, keyframes, excerpt); the ADR-084 review inbox;
`r4 kg current <topic>` and a stale-documents list (reports whose claims
were superseded); `r4 knowledge gaps <graph>`; r4-web bounded routes with
ETag instead of multi-MB polls.

**D-7 — Measure first; accuracy gates, tokens are tracked.** Wave W1 (the
report's §7.3) adds a knowledge ablation flag to the harness, scientific
suites with expected node ids, and recall@k / MRR / nDCG. Success
criterion (report §7.5; §8 decision 3, pending the maintainer): after W1,
and again after W4, on `qwen3.8-27b` through the real R4 companion, arm C
(files + typed KG) beats arm A (files only) on mean score and on
hallucinations at no more than +10% processed tokens. Token saving and
tool-call counts are tracked but do not gate. If C fails on both accuracy
and hallucinations, the programme stops or is re-planned. This replaces the
judge's original criterion ("fewer tokens and tool calls at equal
accuracy"), which the 2026-10-03 benchmark showed measures the wrong thing
for a smaller site model: Haiku 4.5 gained accuracy while tool calls rose
(351 → 398) and tokens stayed flat.

### Why

- Answer-shaped capped rows fit the site model's budget; the prototype's
  verbs cost about 70–720 tokens per call.
- A smaller model needs typed answers more than a frontier model does —
  that is where the benchmark moved (below).

## Consequences

- Positive: the small-model proxy (Haiku 4.5) answered more structured and
  media questions correctly and hallucinated less; site-model confirmation
  is pending the real-R4 run (D-7); agents stop re-probing media;
  "not in the knowledge base" becomes a real answer; researchers get the
  same views the agent gets.
- Cost: no token saving was measured — agents verify KG answers against
  files and make more, smaller calls; for a frontier model at this corpus
  size the KG is not an efficiency gain. The pack (≤ 1,200 characters)
  adds about 340 tokens per turn for ASCII text and up to about 1.2k for
  Japanese text (the D-2 estimator). A tool-set change resets prompt caches
  once.
- NOT in scope: write verbs beyond proposals; agent-supplied SQL; image
  content parts to the model (MM-05, follow-up); transport (D-01/D-02).

## Alternatives Considered

- **Extend `knowledge_query` only** — one schema with many modes, rows
  without class, tier or evidence. Arm B shows agents do not substitute it
  for raw exploration: today's tools were called 31 (Opus) and 46 (Haiku)
  times while grep and ffprobe use stayed at files-only levels.
- **Verb-First as submitted** — grafted (KGL, handles, hidden counts,
  ETag, run memory, prefix-cache-aware pack); its presigned thumbnails and
  parallel writer rejected.
- **Observation Spine's `kg_evidence`** — returns presigned URLs
  (CONSTITUTION §2).
- **JSON (cols/rows) as the agent format** — more bytes per row; kept for
  UIs only.
- **Do nothing** — today's tools did not help (below).

## Acceptance receipts

1. The D-7 criterion, after W1 and again after W4, with the real R4
   companion and `qwen3.8-27b`: arm C beats arm A on mean score and on
   hallucinations at no more than +10% processed tokens; tokens, tool calls
   and stale answers are reported but do not gate.
2. Every verb stays within its cap with a valid header and cursor.
3. A sweep of read verbs leaves the registry row count unchanged.
4. A non-member's explicit cross-project graph read gets a typed 403.
5. A test proves the pack, briefing and project state are counted in
   `fit_payload`.

## Evidence (2026-10-03)

- Gold set: 54 questions over three demo projects (two video, one CT
  imaging) in nine categories, independently re-derived (48 agree, 3 corrected, 3 reworded):
  the maintainers' benchmark workspace (gold set, not published). Arms: A files + shell;
  B files + today's tools reproduced locally (real umrs `d28978ad` ingest
  chain and K3/catalogue routes; 32/32 fidelity cases byte-identical to the
  real R4 tool closures; LLM extraction emulated as a best case); C files +
  the prototype `kgq`. 60 answering agents (10 batches × 3 arms × 2
  models), 10 blind graders. Answering models: Opus 5.5 and Haiku 4.5 —
  Haiku as a proxy for the smaller site model, not the site model itself.

| arm / model | mean | full | hallucinated | stale | wrong abstention | processed input tokens / question |
|---|---|---|---|---|---|---|
| A / Haiku | 0.519 | 12 | 9 | 2 | 4 | 324k |
| B / Haiku | 0.546 | 12 | 12 | 4 | 4 | 309k |
| C / Haiku | 0.611 | 15 | 5 | 2 | 1 | 307k |
| A / Opus | 0.991 | 53 | 0 | 0 | 0 | 637k |
| B / Opus | 0.991 | 53 | 0 | 0 | 0 | 712k |
| C / Opus | 1.000 | 54 | 0 | 0 | 0 | 685k |

- Haiku C vs A: 13 wins, 4 losses, 37 ties (sign test p ≈ 0.049); B vs A
  8 / 6 / 40 (n.s.). Gains by answer source (Haiku A / B / C): structured
  0.50 / 0.59 / 0.66, media/derived 0.43 / 0.39 / 0.57, prose-only 0.69 /
  0.62 / 0.69. The prototype's report claims were extracted by an agent
  (its one non-generic component); because the gain comes from structured
  and media questions and prose shows none, that confound does not explain
  it. Behaviour: Haiku ffprobe 7 → 1 and grep 45 → 18; Opus ffprobe/ffmpeg
  26 → 14. Results: the maintainers' benchmark workspace (analysis notes, not published).
- Verbs (verifier): 59 commands × 4 renders, 0 cap violations in 236;
  in-verb median 4–57 ms; ~70–720 tokens per call.
- Real R4 companion: a smoke test with `qwen3.8-27b` via the rikyu public
  source (read-only bwrap sandbox, arm A) answered one question
  correctly in 110 s with 15 LLM calls and 328k tokens (figures from the
  session notes only; no result file was kept). The full run was blocked by the session safety
  classifier as credential leakage because the runner bound a scratch copy
  of `~/.config/r4` (API keys); the copy was deleted. The full run is left
  to the maintainer (`r4real/run.py` in the bench directory).

## Rollout-status (2026-10-03)

Proposed; nothing landed (see ADR-081 for what the investigation touched;
wave ids are the report's §7.3). W0 (K3 read gate, pure GETs, fan-out
stop) and W1 (ablation flag, measurement) need no ADR; this ADR gates W4.
Open: the D-7 criterion (§8 decision 3, maintainer); the real-R4
site-model run above, after the projects' ADR-062 `data_class` is
confirmed (report §10); the reranker (ADR-083); the alias period length.

## Related

- [ADR-027](027-unified-knowledge-spine.md), [ADR-037](037-tool-selection-phase.md), [ADR-076](076-decision-model-role.md), [ADR-080](080-one-access-guard-shareable-resources.md)
- [ADR-081](081-typed-knowledge-spine.md), [ADR-082](082-multimodal-anchors-catalogue-plane.md), [ADR-083](083-embedding-spaces.md), [ADR-084](084-growth-governance.md)
- docs/development/kg-multimodal-investigation-2026-10-03.md

## Rollout-status (2026-10-12) — ADR-093 landed the fusion part of D-1 and amends D-3

- **D-1 fusion and quota** (ADR-093 C7): K3 `scope=all` fuses its lanes by weighted RRF.
  - `score` is normalised RRF; the raw sum and the per-lane scores are in `metadata` (ADR-093 MD-13).
  - The catalogue lane is held to a quota.
  - The response carries the retrieval core's `sufficiency`.
- **D-3, amended:** the pre-retrieval pack goes in the user turn, sized by the reading model's profile (ADR-093 D-8/D-14). The briefing move follows the router (ADR-093 MD-9).
- **`kg_search` (MD-9):** when this ADR's verbs are built, `kg_search` consumes `/rag/retrieve` as its passage lane, and the D-42 pure-GET fix is that verb. The other `kg_*` verbs are not built yet.
- **D-7's measurement** is realised on SciFact. See ADR-093's eval doc for the agent arm (S5) and the `knowledge_query` tool arms (Phases 3, 7, 11, 13).

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
