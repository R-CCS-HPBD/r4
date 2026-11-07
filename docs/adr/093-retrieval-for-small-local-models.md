# ADR-093: Retrieval for inference, sized by the reading model — one scored retrieve verb in UMRS, fewer and better passages with the question last on a 3B, more and fuller ones on a frontier model, measured on SciFact on the stand-alone stack

**Status:** Accepted (2026-10-12, MD-1: after S9; the maintainer accepted MD-1 to MD-17 on 2026-10-11). Proposed 2026-10-10.
**Date:** 2026-10-10
**Relates to:**
- ADR-083: implements D-5 for text, as an in-process interim; resolves the open reranker choice for CPU sites; D-1/D-2 untouched.
- ADR-085: lands the fusion and quota part of D-1; amends D-3 (where the pack sits and how big it is); D-7's measurement realised on SciFact.
- ADR-081: adopts D-1's `search_text` early for chunk rows, as a writer-maintained column.
- ADR-090: repairs D-4's same-model reindex path.
- ADR-027: D3 "one read verb" gains its passage mode; P2 compose leg.
- ADR-084: D-6 `KgReadPort` will wrap the verb.
- ADR-039: producer-map completeness.
- ADR-062: side calls stay on the session route.
- ADR-087: compose sends an output limit.
- ADR-076 / D-57: deliberately not used; ollama returns no logprobs.
- ADR-013 and INV-13: portable chunk text.
- CONSTITUTION §1.3: R4 never reimplements ranking.
- Gap rows D-14, D-15, D-26, D-28, D-42, D-60, D-62.

## Context

On 2026-10-10 the maintainer asked three things:
1. Is the composition of the knowledge graph and the vector RAG "in good form related to the latest technology movement"?
2. Redesign and implement the augmentation functions for inference, "implement them all by your decision".
3. Keep testing the actual effect on inference with the stand-alone deployment.

Three designs were judged on three lenses: inference value, architecture and governance, and feasibility on the stand-alone stack. They were retrieval-core, harness-side retrieval for small models, and a graph-augmented lane. This ADR is the synthesis. Every fact below was verified on the stand-alone stack and the tree on 2026-10-09/10, at parent `2dba21bb` and umrs `809387a1`.

**The reader is small and single-slot.**
- The chat model is `llama3.2:3b` (Q4_K_M) on native ollama, serving num_ctx 32768 with `OLLAMA_NUM_PARALLEL=1`, at about 29-30 tok/s decode.
- The single KV slot means any side request evicts the agent's cached prefix: a 4.8k-token prompt costs 0.6 s cached and 7.5 s after one 0.8k-token side call.
- The OpenAI endpoint returns no logprobs, so the D-57 decision gate cannot run here.
- The llama3.2 template renders tool schemas only inside the **last** user message (`ollama show llama3.2:3b --template`: `if and $.Tools $last`). After the first tool result the model sees no schema at all. Retrieval through the companion therefore happens only if the 3B picks a retrieval tool in round 1.

**The retrieval path decides relevance with a number that cannot decide it.**
- **A fixed absolute cut.**
  - `RAGQueryRequest.threshold` defaults to 0.7 (`umrs/src/umrs/api/rag.py:76`) and is applied to the top 5 K3 hits in `rag_spine.retrieve_passages` (`api/rag_spine.py:63-88`).
  - `SpineKnowledgeStore` repeats it (`:318-329`), and R4 sends 0.7 explicitly (`r4/src/r4/rag.py:139`, `r4/src/r4/companion/tools.py:2700`).
  - Under e5-base-v2, cosines sit in 0.7-1.0. Validation §11 of 2026-10-09 measured relevant notes at 0.875/0.882, other notes at 0.768-0.828, and "best pizza in Naples" at 0.699. The cut neither shrinks the context nor detects nonsense.
- **Compose works against the 3B.**
  - The question comes **before** the passages (`rag_spine.py:141`).
  - The system prompt pushes toward abstention (`:34-38`).
  - Temperature is hard-coded at 0.3 (`umrs/src/umrs/knowledge/agent_executor.py:110`), and max_tokens is 800.
  - In §11, llama3.2:3b, given the right note at [1] among four distractors, answered that the knowledge base does not cover the question.
- **`search_docs` discards the answer it pays for.** It calls `client.rag.query` and drops the composed answer (`tools.py:1947-1960`). Every search therefore costs a hidden 3B generation of about 7 s, which also evicts the agent's slot.
- **The lanes cannot add recall or report insufficiency.**
  - The dense lane is an exact cosine scan over the 0075 rows, project-wide, with no graph predicate and no chunk-kind predicate (`api/knowledge_query.py:398-441`).
  - The K3 "rag" lane is a whole-phrase ILIKE ordered by `updated_at`, with a fixed score of 0.5 (`:548-596`).
  - "all" sorts raw scores across lanes (`search_api/__init__.py:273`).
  - Every READY catalogue row gets +0.25 without a token match (`data_catalog/scoring.py:73-74`), so `knowledge_insufficient` (`r4/src/r4/companion/knowledge_graph_tools.py:241`, `total_results == 0`) never fires (LIVE-06).
- **Chunks and reindex.**
  - Chunks are 256-token windows with no overlap (`umrs/src/umrs/shared/utils.py:211`), and only a 500-char excerpt is stored on the node (`jobs/knowledge_adapters.py:2575-2635`). Full text is hydrated from S3 by an md5-verified offset.
  - A 128-chunk cap truncates silently (`:2593`), and stale chunk nodes are never deleted.
  - `KNOWLEDGE_REINDEX` calls `maintain_pgvector_index(rebuild=True)` on the dimension-less 0075 column (`jobs/reindex.py:95-104`) and fails (ADR-090 D-4).
- **The research loop treats recency as evidence.** Its gap fill returns the five most recent artifacts when nothing matches (`umrs/src/umrs/knowledge/loop.py:756, 768`).
- **POST is unreachable from a sandbox.** The rail refuses non-GET for the read-only `rag` family (`api/companion_venue.py:419-421, 555-563`, D-42).
- **R4 consumers are broken.**
  - `results.documents` is read on a `RAGResponse` that has no such field (`r4/src/r4/search/executor.py:106`, `search/core.py:418`).
  - `source.to_dict()` is called on a `RAGSource` that has no such method (`r4/src/r4/agent/core.py:751`; `rag.py:156-163`).
  - `TOOL_TAINT_CLASSES` (`r4-contracts/src/r4_contracts/taint.py:28-42`) lacks `search_docs`, `rag_query` and `search_agentic`, and it lists `knowledge_graph_show` and `kb_query`, which name no tool.
- **The reranker cannot work offline.** `CrossEncoderReranker` (`umrs/src/umrs/rag/search.py:293-374`) loads a hub id, which is a no-op under `HF_HUB_OFFLINE=1`. It retries the load on every call and silently returns the input order.

**The measurement instrument exists and the 1k tier is near saturation.**
- The harness is `benchmarks/retrieval/` (run.py, backends.py, infer.py, metrics.py, scifact.py) with `tests/test_bench_retrieval.py`. The data is SciFact (allenai, CC BY-NC 2.0).
- Graph `scifact-eval` in project `default` holds the seeded 1k corpus: 283 dev-cited docs plus 717 distractors, seed 20261010, giving 1,000 artifacts and 1,842 chunks.
- B0 retrieval (2026-10-09, 188 dev evidence claims, K3 embeddings): recall@1 0.803, @3 0.931, @5 0.964, @10 0.986, MRR@10 0.890, nDCG@10 0.911.
- An offline replica of the platform chunker with e5-base-v2 was re-run for this ADR:

  | Tier | Lane | nDCG@10 | r@1 | r@5 |
  |---|---|---|---|---|
  | 1k | dense | 0.937 | 0.841 | |
  | 1k | BM25 | 0.900 | | |
  | 1k | RRF(dense, BM25) | 0.950 | 0.878 | 0.975 |
  | full: 5,183 docs, 9,356 chunks | dense | 0.834 | 0.697 | 0.907 |
  | full | BM25 | 0.817 | | |
  | full | RRF | 0.876 | 0.761 | 0.920 |

- The live 1k dense result sits 2.6 nDCG points below the replica. The suspected cause is that the project-wide lane admits project `default`'s other vectors; S0 explains it.
- 178 of the 188 dev evidence claims have exactly one evidence document.
- **One tier, the full corpus (decided 2026-10-10).** The scientist identity on the stand-alone stack has one project (`default`), and a second project would mean editing identity configuration. Identical bytes also dedupe to the artifact a project already holds, so a second graph cannot hold a second copy. Graph `scifact-eval` was therefore grown to all 5,183 SciFact abstracts, the standard BEIR setting. The 1k numbers above are kept as the record of today's tree on the easy tier.
  - The tune split is SciFact train (809 claims; every cited document is in the corpus). Dev (300) is reported.
  - In-KB abstention is the 112 dev NEI claims: their cited documents are in the corpus but hold no evidence. Out-of-KB abstention is the 30 nonsense probes. A separate out-of-KB claim set does not exist at this tier.

**What the 2025-26 evidence says for a 3B reader.**
- Small models judge relevance and sufficiency poorly. CRAG measured a trained 0.77B evaluator at 84% against prompted LLMs at 58-65%.
- Query rewriting adds under 1 point at 1.3B (query2doc), and multi-query underperforms naive RAG (ARAGOG).
- e5 hard negatives make accuracy fall as passages are added (Jin et al.).
- Small models abstain or hallucinate even when the context is sufficient (Sufficient Context).
- LightRAG and GraphRAG with 3B extraction fall below naive RAG (MiniRAG: 21.9% vs 39.5%).
- The graph prototype on SciFact (2026-10-10):
  - A noun-phrase concept graph with Personalized PageRank (PPR), used as an always-on RRF lane, cost 3.7-6.5 nDCG@10 on the full corpus.
  - Dense-seeded PPR scored 0.61, against 0.834 for dense.

A contributor who did not know these facts would reasonably do one of three things, and each one is measured or published to make a 3B worse here:
- raise k, because 32k tokens are served;
- ask the 3B to grade, rewrite or regenerate;
- add an LLM-extracted entity graph.

## Decision

**D-1 — Measure first, on SciFact, on the stand-alone stack, through the gateway.**
- The harness is the existing `benchmarks/retrieval/`, extended in place:
  - claim sets: tune, absent and nonsense;
  - brackets: closed-book and oracle;
  - a scorer-gate arm and prompt variants;
  - context metrics;
  - `offline_lanes.py`, the full-corpus replica.
- One gate tier: graph `scifact-eval`, all 5,183 abstracts, cross-checked with the replica's full-corpus numbers. The 1k tier saturates recall (r@5 0.964) and is kept only as a record.
- Every constant is tuned on the tune split only.
- No ADR-093 runtime code merges before baseline B0 exists. B0 is today's tree: the `legacy` arm (top 5 at ≥ 0.7, the legacy prompt), the companion agent arm, and E-B through `/gw/rag/query`.
- A default flips only on a pre-registered paired gate: bootstrap 95% CI plus McNemar. Non-inferiority means a 3-point margin.

**D-2 — One retrieval core, in UMRS, behind one GET verb.**
- `umrs.knowledge.retrieval` provides `retrieve(pool, spec, *, encode, rerank=None)`. It is composition-layer code: it imports nothing from `umrs.api`.
- The api layer authenticates and resolves graph access with `_resolve_selected_graphs` and `graph_meta_readable`. It passes graph ids and the encoder down.
- Routes:
  - `GET /umrs/rag/retrieve` (reachable as `/gw/rag/retrieve` through the existing rewrite);
  - `GET /umrs/rag/passages/{node_id}`, the fetch handle.
  - Both are GET, so they work over the D-42 read-only rail.
- The pipeline:
  1. Encode once; variants are batched.
  2. Dense lane: exact cosine, one space, chunk nodes only, with a graph predicate when graphs are named. A pool of 50 candidates, larger than k.
  3. Lexical lanes.
  4. RRF.
  5. A per-artifact cap.
  6. Hydration: search_text, then the md5 slice, then the excerpt.
  7. Rerank.
  8. Cut and sufficiency.
  9. Neighbour merge.
- The response carries scores per lane, a lane report (`unavailable` with its reason, ADR-083 D-4) and timing per stage.
- **No chat-model call happens on this path.**
- Consumers that use the core:
  - `POST /umrs/rag/query`, as retrieve plus compose;
  - `SpineKnowledgeStore`;
  - the K3 lexical lane;
  - every R4 retrieval tool.
- `threshold` defaults to `None`. A float keeps the legacy absolute mode.
- In a separate commit: the loop's recency fallback is removed, so no hit means no evidence.

**D-3 — The scorer decides relevance and sufficiency, never the generator.**
- The cut is deterministic, in UMRS, by mode:
  - **dense:** keep rank 1, then rank i while `cos_i ≥ cos_1 − δ`, stopping at the largest gap ≥ γ;
  - **fused:** the dense rule against the best cosine, OR a lexical rule;
  - **reranker:** sigmoid of raw logits; keep while `p ≥ 0.10` and `logit ≥ logit_1 − 4.0`.
- Every mode keeps at least 1 and at most `k_max` (default 4) passages.
- Sufficiency is `sufficient | partial | insufficient`, reported with its basis, value and threshold:
  - **dense:** `z_null ≥ 3` (ADR-083 D-5, as written) or a lexical hit;
  - **reranker:** `p_1 ≥ τ_null`.
- The dense null comes from 30 fixed probes, computed in process. It is deterministic and keyed by (project, graph set, space, vector count). This is an **interim** until ADR-083 D-1's registry exists; no calibration table is created.
- `/rag/query` with `insufficient` answers without calling the model. `partial` composes from at most 2 passages, labelled as partial.

**D-4 — One context pack, one renderer.**
- `r4_contracts.context_pack` (stdlib) renders evidence identically for `compose_answer`, the R4 router and the eval.
- Layout: a `<passages>` data wrapper comes first, with labels from trusted artifact metadata. The question comes **last**, followed by a one-line recap.
- Edge order is used only when k ≥ 3.
- Sanitising neutralises forged labels and wrapper tags.
- The default instruction is SOFT: answer the covered part and say what is missing; reply `NOT IN KNOWLEDGE BASE` only when no passage addresses the question.
- `compose_answer` runs at temperature 0, with `max_tokens` set from `UMRS_RAG_ANSWER_MAX_TOKENS` (400), the T-216 scanner, and a model-free citation post-check (`metadata.citations`, `abstained`, `uncited`).
- **Amends ADR-085 D-3:** the pack sits in the user turn, passages first and question last.

**D-5 — Read-time small-to-big.**
- After the cut, kept chunks merge with their neighbours (i±1, by deterministic node id) into passages of at most 400 tokens, and the pack stays within `k_max × 450` tokens.
- No re-embed is needed.
- Default off until S2's gate passes.

**D-6 — Hybrid recall.**
- Migration 0120 adds `knowledge_nodes.search_text TEXT NULL`. It is writer-maintained: there is no table rewrite. It gets:
  - a partial expression GIN index on `to_tsvector('english', search_text)`;
  - a guarded `pg_trgm` extension and a trigram index.
- The chunk writer fills the column, and a backfill CLI hydrates md5-verified slices without re-embedding.
- **Amends ADR-081 D-1:** a writer-maintained column plus an expression index, not a STORED generated column.
- Lanes:
  - `lex`: OR-mode FTS weighted by corpus IDF; frequent lexemes dropped.
  - `ident`: identifier tokens matched by escaped trigram ILIKE.
- Fusion is weighted RRF (k = 60).
- K3 `scope=all` fuses its lanes by RRF instead of sorting raw scores. Every lane honours `graphs=`. The catalogue floor applies only on a token match.
- K3 returns sufficiency, so `knowledge_insufficient` can fire.
- This lands the fusion and quota part of **ADR-085 D-1**.

**D-7 — An offline cross-encoder, behind a measured gate.**
- The model is `cross-encoder/ms-marco-MiniLM-L6-v2` (22.7M parameters).
- It is staged once into `/models` (no image rebuild) and served by the rag container at `POST /rag/rerank`.
- It returns raw logits; the sigmoid is applied in UMRS.
- It refuses hub ids.
- When it is unavailable, it is reported as `unavailable`; the input order is never returned silently.
- It is on by default only if S4 passes.
- `bge-reranker-v2-m3` is the named choice for GPU and multilingual sites.
- **Resolves ADR-083's open reranker choice** for CPU stand-alone.

**D-8 — Harness pre-retrieval in R4: context, not tool selection.**
- A deterministic router, with no LLM, retrieves on eligible turns before round 1. It covers both send paths.
- It injects the server's pack into the user turn, gated by the **server's** sufficiency.
- The pack is counted by `fit_payload`, registered with the taint registry, and never replayed in later turns.
- A model-size profile (D-14) sets `k_max`, the token cap and the expansion default. Small (≤ 4B): 3 passages, 1,200 tokens; large (rikyu's kimi-k3, glm-5.3): 10 passages, 8,000 tokens.
- R4 adds **no** cut, fusion or grade (CONSTITUTION §1.3).
- There is one deterministic history-aware retry, fused server-side.
- LLM expansions (HyDE, rewrite, multi-query) are eval-only flags for small and medium readers; the large profile turns multi-query on (D-14).
- Default off until S5's gate passes. `R4_RAG_ROUTER=off|auto|on`: `on` routes every reader, `auto` routes a reader only when its size class (D-14) is listed in `R4_RAG_ROUTER_AUTO` (a comma list of `small`, `medium`, `large`, `unknown`; default `small`). No class is routed by default before its own gate passes (2026-10-10).
- *Amendment (2026-10-11, wave H):* the router may search the message's question rather than its instructions (`r4.companion.query_focus`, focus-v1: a labelled field, a quotation, a Latin run in a Japanese framing, a framing colon's tail, else the message less its framing segments; a plain question unchanged; one query, the raw message never a variant). It is **opt-in** (`R4_RAG_FOCUS=on`) because focus-v1 failed its held-out gate (eval Phase 12). The model still reads the whole message. *2026-10-11 (later): focus-v2 replaced v1. It fixes v1's four held-out misses and a dropped-claim fault on Japanese keigo, but it also fails a blind held-out set (6 of 16 templates; eval Phase 15), so focus stays opt-in and the deterministic rules are closed.*

**D-9 — Citations are checked, not regenerated.**
- A deterministic check (no LLM) flags phantom citations, unsupported sentences and numbers missing from the source.
- It appends a footer of at most 400 characters, with names from trusted metadata.
- Strict mode, which downgrades unsupported verdicts, is eval-only.

**D-10 — R4 consumers are thin and work.**
- `search_docs` and `rag_query` call the retrieve verb. The 0.7 threshold is gone.
- `knowledge_query` reads the server's sufficiency.
- The broken consumers are repaired: `search_agentic`'s RAG leg, the agent RAG step, the LangChain and LlamaIndex adapters, and the orchestrator.
- The local loop digest becomes goal-ranked passages.
- Dead code goes in a separate commit.
- The taint map adds `search_docs`, `rag_query`, `search_agentic`, `context_pack` and (where offered) `web_fetch`, and drops the two dead entries (ADR-039).

**D-11 — Chunking and the same-model reindex.**
- Scheme `sent-v1` adds sentence-aware chunks with deterministic `title › section` headers. It runs in the embed worker and sandbox lane (D-12), never in the server.
- Writes become honest: orphan chunks are deleted and truncation is recorded.
- `KNOWLEDGE_REINDEX` gets the same-model repair: no index call on the untyped column, and stale artifacts are selected by chunk scheme and project.
- **ADR-090 D-4**'s same-model path is fixed. A model switch still needs ADR-083 D-1.
- `sent-v1` becomes the default only if S7's gate passes.
- *Amendment (2026-10-11, wave F, from S7's first live run):* "writes become honest" covers the whole chain, not only the orphan deletion in the writer.
  - The embed writer writes each artifact's nodes, edges and vectors in one transaction, after checking that the artifact row exists. A failed edge had left committed chunk nodes behind: 3 such orphans exist on the stand-alone stack.
  - Ingest never sends a rowless artifact down the chain. Byte dedup never matches an empty hash, and a lost insert fails by name.
  - Bundle import skips the rows of an artifact it deduplicated.
  - `DELETE /rag/documents/{id}` answers 501 instead of a false "deleted" (MD-16).
  - K3 stops returning chunk nodes whose artifact is gone.
  - A maintenance verb repairs existing orphans, dry-run by default (MD-17).
  - KNOWLEDGE_REINDEX makes one pass over the whole stale set in keyset pages. It skips artifacts that unfinished embed or understand jobs hold, and a per-project lock serialises passes. A reindex submitted without an idempotency key gets a fresh one: the store-computed key made an identical repeat 409 (and return the old job silently over ZMQ), so a project larger than `batch_limit` could not converge.
  - S7's "0 stale" counts artifacts that exist and did not fail by name.

**D-12 — Portable chunk text, only by explicit opt-in.**
- `KNOWLEDGE_EXPORT --chunk-text` writes r4-kb v1.3, with md5-verified chunk text. A sensitive project needs the owner's explicit flag.
- Without the flag, bundles are unchanged: excerpts only, and raw bytes only with `include_raw`.
- Import restores `search_text`.
- R4's offline reader reads the flat v1.2 and v1.3 layouts: BM25 plus an in-space vector leg, RRF, no cut, and sufficiency `unknown`.

**D-13 — What the graph does for retrieval at 3B (see D-14 for large sites).**
- The knowledge graph helps a 3B reader through **structure**:
  - chunks as addressable nodes with `part_of` edges and deterministic neighbour ids (D-5);
  - graph selection honoured by every lane (D-2, D-6);
  - provenance and visibility predicates;
  - the loop reading evidence through the same verb.
- It does not help through LLM-extracted entity graphs, graph facts in the prompt, or query-time graph LLM calls.
- A concept or entity lane can return only as an ADR-081/084-conformant amendment, gated on a non-circular multi-hop set. D-14 adds one for sites whose extraction model is large.

**D-14 — One design, sized by the reading model (added 2026-10-10).** The maintainer: "in the full remote deployment (with rikyu endpoints), we will use kimi-k3 and glm-5.3 full models." The stand-alone reference reader is `llama3.2:3b`. The remote deployment reads with kimi-k3 and glm-5.3: frontier MoE models with 1M-token windows. Every limit above that protects a 3B is therefore a **profile value**, chosen from the reading model and never hard-wired.
- **One size rule for both repositories (the effective size; aligned 2026-10-10).** `r4_contracts.models` (stdlib) holds the one rule, and every profile decision in both repositories goes through it (`model_size_class`, `profile_for`, `profile_for_class`): R4's pack and its retrieve, and in UMRS the retrieve verb's `auto`, `/rag/query`'s answer model, the loop store's model and the graph-lane gate's extraction model.
  - The class is read from the **effective** size: √(total × active) for a mixture-of-experts model when both are known (an `-a<n>b` tag, or the table of published sizes), else the total. `qwen3-30b-a3b` (≈ 9.5B) and `gpt-oss-20b` (≈ 8.7B) are therefore medium, in R4 and in UMRS alike.
  - A parameter tag in the name gives the total (`llama3.2:3b` is small, `qwen3.8-27b` is large).
  - Hosted families that publish no size are large: kimi, glm-5, deepseek-v/r1, gpt-4/5, claude, gemini.
  - Thresholds: small ≤ 4B, large > 14B.
- **Who resolves the profile.** UMRS resolves it from the registry-resolved answer model (`UMRS_RAG_PROFILE=auto|small|medium|large`). R4 resolves it from the session model through **model-scale detection** (below), and sends the resolved `profile` with `reader_model` on every retrieve. UMRS therefore never has to guess the size of a remote model it does not serve. `R4_RAG_PROFILE` overrides.
- **Unknown scale reads with medium, everywhere (2026-10-10).** A named model whose scale is unknown reads with the medium profile in R4 and in UMRS; R4 adds one notice per session (below). A request that names no reading model at all (an older R4) keeps the site's own defaults, the small profile's values, as before D-14.
- **Model-scale detection in R4 (maintainer, 2026-10-10).** The maintainer: "r4 needs a logic to detect the size/scale of a model user registered to r4, as we have multiple registration of sources (self-deployed, rikyu, ollama, or any other public endpoint)". `r4.model_scale.detect_model_scale(model, source)` takes the first decisive answer, in this order:
  1. **An explicit override.** `r4 model scale set <model> small|medium|large [--params B] [--active B]`, stored per source and model; or `R4_MODEL_SCALE` for a process.
  2. **What the source reports.**
     - Ollama `/api/show` gives the exact `general.parameter_count`, the context length and the MoE expert counts.
     - A platform (UMRS-registered, self-deployed) model gives its registry `ModelInfo`: `huggingface_id`, context, and any parameter field.
     - An OpenAI-compatible endpoint (rikyu, vLLM, LiteLLM or any public one) gives its `/v1/models` entry: `max_model_len`, `root`/`parent` (often the Hugging Face path), and any parameter field.
  3. **The shared name rule.** Parameter tags, and a table of known families with their published sizes.
  4. **Otherwise `unknown`.** The medium profile applies, with one notice per session explaining how to set the scale.
  - The class is read from the **effective** size: √(total × active) for MoE models when both are known. A 30B-A3B behaves like a 9.5B model and is medium; kimi-k3 (about 1T total, 32B active) is large.
  - The router detects once per session, model and source, and sizes its pack, its retrieve's `profile` and `search_docs`' `profile` from the same detected class.
  - Probes are bounded at 2 s, never block a turn, and are cached for 7 days per source and model. `r4 model scale [MODEL] [--refresh]` shows the class and how it was decided.
  - Probes honour r4's `verify_ssl`, as every other r4 request does: a self-signed gateway with `verify_ssl` off is probed, and a certificate failure with it on is named in the detail rather than silently falling to the name rule. A platform source that points straight at UMRS (where `/gw/models` is a 404) is probed at `/umrs/models` (2026-10-10).

  | | small (≤ 4B; the stand-alone stack) | medium (4–14B) | large (> 14B; rikyu kimi-k3, glm-5.3) |
  |---|---|---|---|
  | UMRS cut `k_max` / passages when partial | 4 / 2 | 6 / 3 | 10 / 6 |
  | candidate pool | 50 | 50 | 100 |
  | neighbour merge (D-5) | per the S2 gate | on | on |
  | answer output limit | 400 | 600 | no explicit limit (the provider's default; rikyu 32,768) |
  | R4 pack cap (always ≤ 12% of the window) | 3 passages, 1,200 tokens | 4, 2,000 | 10, 8,000 |
  | LLM query expansion (multi-query, fused in UMRS) | off (eval flag) | off (eval flag) | on: two variants from the session model, one call before round 1 |
  | `R4_RAG_ROUTER=auto` routes it | yes (default `R4_RAG_ROUTER_AUTO=small`) | only when listed | only when listed |
  | scorer abstention (D-3) | yes | yes | yes: nonsense never reaches any model |

  *Amended 2026-10-13 (MD-14, eval Phase 19):* the large column's sizes are now the medium column's (`k_max` 6 / 3 when partial, pool 50, pack 4 passages in 2,000 tokens). Read by kimi-k3 and glm-5.3, the 10-passage pack was not shown non-inferior to the small pack, and it raised verdicts on NEI claims. The large profile keeps no explicit output limit, query expansion and the graph-lane eligibility.

- **The same at every size.** UMRS owns relevance, the cut and sufficiency. One renderer draws every pack. R4 applies no cut.
- **What the large profile adds that the 3B profile rejects.**
  - **LLM query expansion.** Its gains grow with model size: under 1 point at 1.3B (query2doc), several points at frontier scale.
  - **More and longer passages.** Frontier models degrade far less with distractors than a 3B.
  - **Agentic follow-up.** Through the passage handle and `search_docs`, because large models choose tools reliably.
- **The graph on large sites (revises D-13 there).** LLM extraction of entities and relations is reliable from about 30B, the LightRAG floor. So on a site whose *extraction* model is large, the extracted graph earns a **routed bridge lane**:
  - the query's entities are linked to extracted entity nodes (lexical match on labels and aliases within the selected graphs);
  - one bounded hop reaches other documents that mention a linked entity;
  - at most 2 bridge passages are added, and only when the query names two or more linked entities that sit in different documents.
  - It is gated: `UMRS_RAG_GRAPH_LANE=off|auto|on`, where `auto` means on only when the site's extraction model is large **and** `UMRS_RAG_GRAPH_LANE_MEASURED=1` records that the lane passed its multi-hop probe (the maintainer sets it; review 2026-10-10). Any other value of the gate is `off`. It stays off on the 3B stand-alone stack and on an unmeasured large site, and no default flips without a multi-hop probe.
- **Measurement.**
  - The stand-alone stack measures the small profile end to end.
  - The large profile's retrieval is the same code, with larger knobs (harness preset `retrieve:large`).
  - Its *reader* effect is measured by the harness's remote-reader option: the same retrieved passages, read by kimi-k3 or glm-5.3 through the rikyu gateway. This runs only once the maintainer allows eval traffic there (MD-14).

### Amendments after the adversarial reviews (2026-10-10)

Two adversarial reviews ran: wave A, then waves B to D. Each had five lenses and two independent refuters per finding. They changed rules stated above. These rules supersede the text above where the two differ.

- **D-3 sufficiency.**
  - **Lexical evidence.** Lexical evidence over the *kept* passages can only lift a partial z (2 ≤ z < 3) to sufficient. A z below 2 is insufficient, whatever the lexical lane matched.
    - Why: a nonsense question that shares one rare word with the corpus scored "sufficient", because off-domain words are rare and so carry the highest IDF.
  - **No null.** Without a null there is no `partial`. The verdict is sufficient only on coverage: at least 0.5 of the best variant's content terms in the top passage, *and* at least `min(UMRS_RAG_LEX_MIN_TERMS=2, terms)` matched. Otherwise it is insufficient.
  - **Dense and fused modes.** `searched == 0`, or a dense lane that is not `ok`, gives insufficient. K3 reports `unknown` when its dense lane did not run.
  - **Null cache key.** The key is (project, graph set, space, count, max(updated_at)), so a same-count re-embed recalibrates.
- **D-5.**
  - Tail folding runs only with merge on.
  - A neighbour joins only a passage of its own project, graph, tier and artifact.
  - Touching passages merge only within 400 tokens and one scope.
  - The `k_max × 450` cap is enforced in code.
  - Hydrate and backfill join only an artifact of the node's own project.
- **C1 scope.**
  - "No graphs" means the whole project *minus the graphs the caller may not read* (KG-05).
  - With graphs selected, the legacy `''` rows of every selected default graph are visible, as in K3.
  - An exclusion alone keeps the project rule.
- **`/rag/query`.**
  - `top_k == 5`, the value every older client sends, is treated as unset.
  - A cited answer that names a gap is `metadata.partial_answer`, not an abstention.
- **Receipt 4 (legacy parity).** It holds under `UMRS_RAG_QUERY_PREFIX=b0`, the eval-only switch that reproduces the old encoder. The query encoder had been sending `passage: query: <q>` since the embedder was bundled. The default (`fixed`) is the bug fix. The fix is measured as its own factor, and later arms pair against B0′, which is B0's arms with the fixed encoder.
- **S0 replica.** On live text the replica gives these nDCG@10 values: 1k dense 0.9446; full dense 0.8378, BM25 0.8141, RRF 0.8704. Chunk text is ruled out as the cause of the live gap. The cause is the query prefix: the replica reproduces live r@1 (0.585 vs 0.617) only with the double prefix.
- **D-8.**
  - The pack cap counts evidence only.
  - Approved-plan execution turns are not routed.
  - A stop during pre-retrieval ends the turn as stopped.
  - Intent heuristics read the raw message, not the pack.
  - `R4_RAG_RETRIEVE_TIMEOUT` defaults to 10 s.
- **D-9.**
  - A bare `[n]` resolves only against the pack's own labels.
  - A phantom `(Source: …)` is counted, never quoted back.
  - Strict mode on a streamed turn appends a note rather than rewriting.
- **D-12.** The engine never trusts a client-supplied chunk-text acknowledgement. Only the verified submitter who owns the project may export the chunk text of a project that is not `non_sensitive` (waves B–D review).

The waves B–D fix pass (2026-10-10) changed these further rules:

- **D-3 and `/rag/query`.**
  - An answer abstains when it names a gap and cites nothing, *or* leads with the instructed "NOT IN KNOWLEDGE BASE" reply, *or* states nothing besides the gap, whatever it cites. `partial_answer` is a cited answer that states content and names a gap. Free gap phrasings ("the passages do not say …") count as naming a gap. "Do not support" is a verdict, not a gap.
  - **Null encoding.** A cached null is served only while the encoder still encodes a fixed probe the same way (cosine ≥ 0.9999). A `UMRS_RAG_QUERY_PREFIX` switch or a re-staged encoder therefore recomputes it. The verb's encoder carries the probe in the query batch it already sends.
  - The passage handle serves another project's legacy `''` row when the caller may read that project's default graph, the same rule as the dense gate.
- **D-11.**
  - A `KNOWLEDGE_REINDEX` sweeps the job's own project. A spec `project_id` naming another project is refused, so a site re-chunk is one job per project.
  - An artifact is stale when its stamp *or* any of its chunk rows in its project carries another scheme.
  - A re-embed writes into the id scope its file node carries, so an imported graph's chunks are replaced in place. An artifact with no file node in the target graph fails by name and writes nothing.
  - The `sent-v1` title is the metadata title, else a level-1 heading on the first line of a markdown text, else the source name. In a code, config or plain-text file a `#` line is neither the title nor a section: the embed lane is told per text.
  - A packed chunk never passes 256 tokens.
  - The embed job drops NUL. A PDF, or a PPTX by its name, is chunked from its extracted text, and every raw-slice reader (hydrate, export, import, R4's offline reader) decodes it the same way.
- **D-12.**
  - `POST /knowledge/export` submits the job under the verified caller. `POST /jobs` refuses a `knowledge_export` that asks for `chunk_text` or `include_raw`.
  - The import reports nodes it skipped as identical or held by another project, since node ids are installation-global. S8 therefore imports on a second installation. It also needs a registered project (owner and data class) for the eval corpus, because both chunk-text gates fail closed without one.
- **D-14.**
  - **Ollama MoE tags.** Tags without an `-a<n>b` part read their published sizes from the family table: `qwen3:30b` and `qwen3-coder:30b` are medium, `deepseek-v2:16b` is medium and untagged `mixtral` is large.
  - **Graph lane.** It links label variants as one concept, and bridges only concepts whose document sets are apart. Its label match is index-ready: migration 0121 builds `idx_knowledge_nodes_semantic_label_fts`. Without that index the lane reports `unavailable` above 20,000 semantic nodes in scope, never a timeout.
  - **Detection.** A probe is bounded at 2 s of wall-clock time. A failed probe is asked again after 60 s. An endpoint no registered source names is cached under a digest of its URL. `R4_RAG_PROFILE` sizes the pack and the profile sent, never the class the `auto` gate reads.
  - **Every R4 consumer sends `profile` and `reader_model`:** the router, `search_docs`, the loop, `search_agentic`'s RAG legs, the workflow RAG step and the orchestrator.
- **Eval.**
  - A result file carries `schema_version` 2.
  - A reasoning or large reader gets no explicit verdict limit. A reply cut at its limit, or one holding only reasoning, is an error row.
  - Comparisons report every factor and definition that differs from a B0 file. `run.py rescore` rewrites a B0 file under the v2 rules.

### Why

- At 1k, the reader is where the headroom is: gold is in the top 5 for 96.4% of claims, yet the 3B abstained with the answer at [1]. Shrinking and ordering the context (D-3, D-4, D-5) costs no model time. Every judgement a 3B makes badly moves to a small deterministic scorer: whether to search, whether evidence suffices, how much to show, whether a citation is real.
- Rank 1 decides what the cut keeps. The lexical lane moves r@1 by about +3.7 points (1k) and +6.4 points (full) in the replica, so hybrid recall pays at small k even when recall@5 is saturated.
- The companion's tool-choice bottleneck is structural (the chat template), not a prompt problem. Pre-retrieval (D-8) is the only lever that does not depend on the 3B choosing a tool in round 1.
- One owner per decision: UMRS ranks, cuts and judges sufficiency, and R4 budgets and renders. That is CONSTITUTION §1.3. It also means the router, `/rag/query`, the loop and the eval cannot drift apart.
- Nothing in this ADR adds a model call to the answer path for small and medium readers, so ADR-087's call contract and ADR-062's locality are untouched. The large profile's one expansion call goes on the session's own route, through the same ADR-062 gate.
- The constraints are the reader's, not the platform's (D-14): what is right for a 3B (few, best-first passages; no LLM judging) would starve a frontier model, and what helps a frontier model (more context, LLM expansion, agentic follow-up) would sink a 3B. One design with profile values serves both deployments.

## Consequences

**Positive**
- A measured, falsifiable answer to whether retrieval helps the stand-alone model.
- A `/rag/query` that abstains deterministically on nonsense and answers from one to four best passages.
- A companion whose evidence no longer depends on the 3B's round-1 tool choice.
- Honest loop evidence.
- Working R4 consumers and a complete taint map.
- A working same-model reindex.
- Portable full-text bundles by choice.

**Cost**
- One migration, 0120: a nullable column, partial GIN indexes and a guarded `pg_trgm`. Index builds need a window on rccs (MD-4).
- Full chunk text in Postgres: about +1× corpus text.
- About 91 MB of reranker weights.
- Up to 1,200 tokens more per routed turn.
- Citation footers in history.
- More ADR-039 approval prompts.
- Roughly 10-15 hours of laptop compute for the evaluation.
- Default behaviour changes for `/rag/query`, `search_docs`, K3 `scope=all` and the loop, each with a STATE row.

**NOT in scope**
- HNSW and the space registry (ADR-083 D-1/D-2).
- The multilingual embedder (ADR-083 D-3).
- Typed `kg_*` verbs and tool consolidation (ADR-085 D-1/D-4).
- Moving the briefing and project state into the budget (ADR-085 D-3, KGC-10).
- LLM-extracted graphs.
- Japanese evaluation.
- The transport (D-01/D-02).

## Alternatives Considered

- **Raising k or stuffing long contexts.** Small models degrade with more and harder distractors (RULER, NoLiMa, Context Rot, Jin et al.).
- **The 3B as judge, rewriter or regenerator** (prompted CRAG, Self-RAG, Self-Route, HyDE, multi-query, CoVe). Published evidence shows little or negative gain at this size, and each call evicts the agent's slot. These are kept only as eval-only flags.
- **GraphRAG / LightRAG / HippoRAG-style lanes, and the concept-index lane.**
  - MiniRAG reports failure at 3B.
  - The concept lane was measured at −3.7 to −6.5 nDCG@10 always-on.
  - The routed variant shows no verdict gain on SciFact, and its probe is circular.
  - It would put spaCy compute in the UMRS server process (against D-12) and create alias and relation stores beside ADR-081 D-6 and ADR-084 D-3.
- **BM25 extensions** (pg_search, VectorChord-bm25, pg_textsearch). They are AGPL or need preload, and so need an image rebuild of about 16 GB. IDF-weighted built-in FTS suffices at this scale.
- **A large CPU reranker** (bge-reranker-v2-m3 and similar). Seconds per 30 pairs on 2 CPUs; kept for GPU sites.
- **ColBERT, late chunking, Provence pruning, NLI verifiers.** Each costs storage, an encoder this stack lacks, or CPU time, for less than the cut, merge and lexical check give.
- **A POST retrieve verb.** Unreachable from sandboxes (D-42).
- **A cut or grade in R4.** A second owner of UMRS logic (§1.3).
- **A STORED generated `search_tsv`.** It rewrites `knowledge_nodes`.
- **A durable calibration table.** It pre-empts ADR-083 D-1.
- **Renaming `search_docs` to `knowledge_search` now.** It collides with ADR-085 D-4.
- **A second eval package.** The existing harness already has the commands and the agent arm.
- **Do nothing.** The 0.7 cut sits at the nonsense floor, the 3B abstains with the answer in [1], and a search costs a hidden 7 s generation.

## Slices, components and gates

| Slice | Components | Gate (pre-registered; paired against B0 unless stated) |
|---|---|---|
| S0 Measure first | C0 harness, replica, B0 | No runtime change. B0 recorded on the full tier (retrieval, E-A with brackets, E-B, nonsense, agent). Legacy emulation matches `/gw/rag/query` on 20/20 claims. Replica reproduces dense/BM25/RRF within ±0.005, and the live gap is explained. |
| S1 Verb, cut, pack | C1 verb, C2 cut and sufficiency (dense, legacy), C3 contracts, C4 compose, C10 search_docs/rag_query, C16 env | Legacy parity 300/300. 0 chat calls on retrieve. ctx_precision ≥ +15 pts and ctx_recall ≥ −3. wrong_abstain lower (CI excludes 0). macro-F1 non-inferior. Absent unsupported_verdict up by at most 2 pts. Nonsense INSUFFICIENT ≥ 0.9, false-INSUFFICIENT ≤ 5%. Factorial recorded. Lint passes. |
| S2 Small-to-big | C5 | Rationale coverage ≥ +10 pts. E-A non-inferior. Prompt tokens p95 ≤ 1.8k. |
| S3 Hybrid lexical, K3 fusion | C6, C7, C2 fused, C10 knowledge_query, C16 migrate | Full tier: ΔnDCG@10 ≥ +0.02 (CI > 0) or Δr@1 ≥ +3. 1k: r@1 and E-A non-inferior. Lexical p95 ≤ 100 ms. K3 graph and floor tests pass. knowledge_insufficient on nonsense ≥ 0.9. |
| S4 Reranker | C8, C2 reranker, C16 reranker pull | (ΔnDCG ≥ +0.01 with CI > 0, OR Δctx_precision ≥ +5 with Δctx_recall ≥ −1, OR AUROC +0.05) AND E-A non-inferior AND p95 ≤ 800 ms. Sufficiency gates hold. |
| S5 Router, citations | C12, C13 | Reader non-inferior first. Agent arm: accuracy ≥ +10 pts (sign test), absent unsupported_verdict not higher, nonsense "not covered" ≥ 0.9, p50 added latency ≤ 3 s. Citation planted set 100%, AUROC ≥ 0.70, 0 LLM calls. |
| S6 Consumer repairs | C11 | Tests fail before and pass after. search_agentic grounded ≥ 90% on 30 claims. Loop digest finds the gold doc in ≥ 3 of 5 goals. Cleanup commit separate. |
| S7 Re-chunk, reindex | C9 | Reindex completes with 0 stale and 0 orphan chunk rows and no HNSW error. 1k r@1 and E-A non-inferior. Full nDCG non-inferior. |
| S8 Portable text | C14, C15 | md5 100%. Import retrieval within 0.01 nDCG. Without the flag the bundle has the v1.2 shape. Offline chunk count matches the export. v1.3 r@5 ≥ v1.2. |
| S9 Confirm and document | all defaults | Final configuration vs B0 with two seeds, headroom_used, retrieve p95 ≤ 1.5 s. Rollout notes in ADR-081, 083, 085 and 090. Gap rows, STATE and ledger updated. |

A gate that fails leaves its knob off, and the ADR records the negative result.

## Open decisions for the maintainer

- **MD-1** When Proposed becomes Accepted: after S1–S4 or after S9. Also whether 1k-tier inference gates may flip a default before S9.
- **MD-2** Router default. Should `auto` for the small profile follow automatically from S5? The medium/large sizes are now set by D-14. Open: whether the large profile always injects the pack, or injects only a list of handles and lets the model fetch, measured with the remote reader (MD-14).
- **MD-3** *Decided 2026-10-11: rccs stages `BAAI/bge-reranker-v2-m3`; off by default (`UMRS_RAG_RERANK_DEFAULT=off` stays, S4 failed narrowly); the stand-alone image never bakes MiniLM in (`standalone.sh reranker pull` stays the path); built on `wip/ops`: a `rccs` profile entry in `deploy/model_manifest.py` (records what is staged, downloads nothing; staging is a MODEL_DOWNLOAD job or Ansible step).* The rccs reranker (`bge-reranker-v2-m3` on GPU via `deploy/model_manifest.py` and Ansible). Whether the stand-alone image ever bakes in MiniLM.
- **MD-4** Migrations 0120 and 0121 (the graph lane's label index) on rccs: index builds in a maintenance window, or `CONCURRENTLY` out of band. Whether the site role may create `pg_trgm`. Renumber at merge if 0120 or 0121 is taken.
  - *Decided 2026-10-11:* the migrations run in the normal converge step, inside a maintenance window. The runner is transactional, so there is no `CONCURRENTLY`. The privileged layer creates `pg_trgm`, never the site role.
  - *Built on `wip/ops`:* a privileged `pg_trgm` task (`--tags pg_trgm -e umrs_front_privileged=true`); a read-only check on the main cadence before any converge that applies 0120; a maintenance-window note in the pre-restart report; the runbook in `deploy/ansible/README.md`.
- **MD-5** Full chunk text in Postgres (about +1× text). The export opt-in flag's name and default, and whether `sensitive` projects may export it.
- **MD-6** Removing the loop's recency fallback (`knowledge/loop.py:756, 768`).
- **MD-7** Making `sent-v1` the default after S7, and reindexing existing projects.
- **MD-8** Japanese: when to schedule ADR-083 D-3; a CJK lexical configuration; a Japanese gold set. As of 2026-10-10 the catalogue matches a CJK term as a substring, but the cut's coverage rule and the loop's gap fill still find no CJK lexical evidence. They fail closed, so a CJK query gets no lexical lift, and a coverage rule for CJK waits for that gold set.
- **MD-9** Sequencing with ADR-085: `kg_search` consumes `/rag/retrieve` as its passage lane (recommended). Where the D-42 pure-GET fix lands. Whether ADR-085 D-3's briefing move follows the router.
- **MD-10** Whether aggregate metrics (no SciFact text) may be committed under CC BY-NC 2.0.
- **MD-11** *Decided 2026-10-11: recommended host setting on ≥ 16 GB RAM or GPU hosts; the default stays 1; built on `wip/ops`: documented in `deploy/standalone/README.md` and `env/base.env` (container and native ollama), with a non-failing `standalone.sh doctor` hint.* `OLLAMA_NUM_PARALLEL=2` on capable hosts (side calls stop evicting the agent's slot).
- **MD-12** Whether strict citation mode is ever user-facing.
- **MD-13** K3 score display: normalised RRF (proposed) or raw RRF plus per-lane scores.
- **MD-14** *Decided by the maintainer 2026-10-10: yes, both models.* Eval traffic on rikyu. May the harness's remote-reader arms send SciFact claims and retrieved abstracts (public, CC BY-NC) to kimi-k3 and glm-5.3 through the rikyu gateway? The cost is about 300-600 calls per arm. This is how D-14's large profile gets its reader numbers.
- **MD-15** The large-site graph lane (D-14) depends on the site extraction model being large. Should rikyu re-run extraction over existing projects with kimi-k3 or glm-5.3 once the lane is measured?
- **MD-16** *(added 2026-10-11)* A real artifact delete. `DELETE /rag/documents/{id}` is a placeholder today: it reports "deleted", deletes nothing, and the document stays retrievable. Wave F makes it answer 501 until a delete exists. Should UMRS gain a delete verb? It would be one transaction over the artifact row, its file, chunk and semantic nodes (rag-minted chunks via their file node's `part_of` sources), their vectors and edges, and its verification findings, with `knowledge_events.artifact_id` made nullable so the audit trail survives. It would sit behind the project-writable gate as `DELETE /umrs/knowledge/artifacts/{id}` and `r4 knowledge delete`. This is new surface, so it is the maintainer's decision.
  - *Decided 2026-10-11: yes; built (umrs `47d56059`):* `DELETE /umrs/knowledge/artifacts/{id}` deletes the artifact and everything derived from it in one transaction, keeps its events (detached), then deletes its unshared objects best-effort; agents are refused, a foreign, unknown or already-deleted id is 404. `DELETE /rag/documents/{id}` resolves the id to its artifact and runs the same delete (404 when unresolvable). R4: `Client.knowledge.delete_artifact`, `r4 knowledge delete`.
- **MD-17** *(added 2026-10-11)* *Decided 2026-10-11: yes, through Ansible, opt-in and dry-run first; built on `wip/ops`: `umrs_front_knowledge_orphan_repair: off|dry_run|apply` over an explicit `umrs_front_knowledge_orphan_repair_projects` list on the umrs-front main cadence (no sudo; JSON per project in the play output; a non-zero exit fails the play), and `standalone.sh orphan-repair [--apply] [--project P]...`.* Running the orphan repair (`python -m umrs.knowledge.orphan_repair`; dry-run by default) on each site through Ansible. Agents never edit hosts.

## Maintainer decisions (2026-10-11)

The maintainer accepted the agent's proposals for MD-1 to MD-17 on 2026-10-11 ("I accept all for MD-1 ~ 17"). What each one now means:

- **MD-1:** Accepted after **S9**, not after S1–S4. 1k-tier gates never flip a default; the full tier has been the gate tier since 2026-10-10. The status line changes when S9's receipts are recorded.
- **MD-2:** `auto` for the small profile follows S5's gate, not S5's direction. Phase 3 measured +13.3 [−3.3, +28.3] at n = 60, which does not meet "≥ +10, sign test", so `R4_RAG_ROUTER` stays off by default. S9 re-runs the agent arm at 60 claims per label; if the gate passes there, `auto` routes small readers. The large profile injects the pack, not a list of handles, until the rikyu arms (MD-14) measure otherwise.
- **MD-3:** rccs stages `BAAI/bge-reranker-v2-m3` through `deploy/model_manifest.py`, off by default (S4 failed narrowly). The stand-alone image never bakes MiniLM in; `standalone.sh reranker pull` stays the path.
- **MD-4:** 0120–0122 run in the normal converge migration step in a maintenance window, inside the runner's transaction (no `CONCURRENTLY`). `pg_trgm` is created by the privileged Ansible layer, never by the site role. Renumber only if `main` takes a number first.
- **MD-5:** chunk text in Postgres is accepted (+1× text). The export flag is `--chunk-text`, default off; a `sensitive` project exports it only with the owner's explicit flag (D-12 as built).
- **MD-6:** the loop's recency fallback is removed (umrs `7e59eaaa`).
- **MD-7:** `sent-v1` becomes the default only if S7's pre-registered gate passes. Phase 14 (S7b) tests E-A non-inferiority on dev and tune pooled (n = 1,109). Existing projects are re-chunked by an operator-run KNOWLEDGE_REINDEX, which converges since wave F. *Result (eval Phase 14): accuracy −0.6 [−2.6, +1.4] is non-inferior, but macro-F1 −1.0 [−3.7, +1.9] is not, so `sent-v1` stays opt-in. Retrieval improves (ctx precision +4.6, rationale coverage +23); the 3B's verdicts do not.*
- **MD-8:** ADR-083 D-3 (a CJK lexical configuration and a Japanese gold set) is the next retrieval wave after this ADR. Until then CJK queries fail closed (no lexical lift), as built.
- **MD-9:** ADR-085's `kg_search` consumes `/rag/retrieve` as its passage lane. The D-42 pure-GET fix is the retrieve verb itself (GET). ADR-085 D-3's briefing move follows the router: the pack goes in the user turn.
- **MD-10:** aggregate metrics (no SciFact text) are committed under `benchmarks/retrieval/records/`.
- **MD-11:** `OLLAMA_NUM_PARALLEL=2` is the recommended setting on capable hosts. The stand-alone stack does not manage the host's Ollama, so it is documented, not set.
- **MD-12:** strict citation mode stays eval-only. No variant beat SOFT (Phase 3).
- **MD-13:** K3's `score` is normalised RRF; the raw RRF sum and per-lane scores ride in `metadata.rrf` and `metadata.lane_score`. Built in C7.
- **MD-14:** decided 2026-10-10 (both models). *Measured 2026-10-13 (eval Phase 19): the large profile falls back to the medium sizes; see the 2026-10-13 rollout block.*
- **MD-15:** yes. Once the rikyu arms have measured the graph lane with a large reader, rikyu re-runs extraction over existing projects with kimi-k3 or glm-5.3, as an operator-run job.
- **MD-16:** yes, a real artifact delete. `DELETE /umrs/knowledge/artifacts/{id}` deletes in one transaction behind the project-writable gate, `r4 knowledge delete` calls it, and `DELETE /rag/documents/{id}` uses it.
- **MD-17:** yes. `orphan_repair` runs on each site through Ansible, dry-run first, then `apply` explicitly.

## Acceptance receipts

1. On the 1k graph, the final configuration beats B0 on wrong_abstain_when_shown (CI excludes 0), with macro-F1 non-inferior and absent-claim unsupported_verdict not higher.
2. Nonsense queries are flagged INSUFFICIENT at ≥ 0.9 through `/rag/retrieve`, `/rag/query` and K3, and no more than 5% of in-KB evidence claims are wrongly flagged (ADR-083 receipt 3).
3. A test fails if the retrieve path issues a chat call, and if any R4 module applies a relevance cut.
4. Legacy mode (`threshold=0.7`, stages off, k = 5) returns B0's ids in B0's order.
5. `EXPLAIN` of the lexical lane shows the expression GIN index, and a rerank outage shows `unavailable` in the lane report.
6. A tool call carrying a retrieved excerpt requires per-call approval (taint map).

## Evidence (2026-10-10)

- **B0 retrieval:** `benchmarks/retrieval/results/20261009T173856Z-retrieve-{k3-embeddings,k3-all,tool-knowledge_query}-baseline.json`, all at nDCG@10 0.911, r@1 0.803, r@5 0.964 over 188 claims. B0 inference arms are in progress.
- **Replica:** `proto93g` vectors (platform chunker, e5-base-v2), re-run independently. Full: dense 0.834, BM25 0.817, RRF 0.876 (r@1 0.697 / 0.703 / 0.761). 1k: 0.937, 0.900, 0.950 (r@1 0.841 / 0.817 / 0.878).
- **Template:** `ollama show llama3.2:3b --template`. Tools are rendered only when the user message is last.
- **Validation:** §11 of 2026-10-09 (nonsense 0.699 vs relevant 0.875/0.882; the 3B abstained with the gold note at [1]).

## Gap rows advanced

| Row | Advanced by |
|---|---|
| D-14 | Chunk text and lexical index; same-model reindex. |
| D-15 | Chunking rules and the re-embedding policy for a scheme change. |
| D-26 | Harness pre-retrieval and the citation check. |
| D-28 | The retrieval leg of context control reaches the prompt, counted. |
| D-42 | GET retrieval over the read-only rail. |
| D-60 | Calibrated insufficiency, fusion, measurement. |
| D-62 | Stand-alone migrate and reranker staging without a rebuild. |

Statuses are updated in the gap analysis only as slices land.

## Rollout-status (2026-10-10)

Proposed. Every slice is implemented on `feat/adr-093-retrieval` (parent and umrs). The branch is not merged: acceptance is MD-1.

**Built, in waves, each followed by an adversarial review and a fix pass.**
- **Wave A:**
  - the retrieval core, `umrs/knowledge/retrieval/`, and the GET verb and passage handle;
  - the cut and calibrated sufficiency, and compose on the pack;
  - the lexical lane, migration 0120 and backfill, and RRF fusion;
  - K3 fusion;
  - the offline reranker, the stand-alone knobs and the `migrate`/`reranker pull` commands;
  - the R4 consumers repaired, the pre-retrieval router and the citation check;
  - the eval harness.
- **Wave B:** `sent-v1` chunking with the same-model reindex repair; portable chunk text (r4-kb v1.3); the R4 offline reader.
- **Wave C:** the D-14 profiles, large-reader query expansion, the gated graph bridge lane (migration 0121) and the remote reader in the harness.
- **Wave D:** model-scale detection (`r4 model scale`) and one effective-size rule.
- **Reviews:** two reviews, five lenses each with two refuters per finding: 63 + 67 findings, of which 61 + 58 were kept. Every kept finding is fixed, each with a test that fails on the old code. The "Amendments after the adversarial reviews" block above records the rules they changed.

**Measured on the stand-alone stack** (SciFact, full 5,183-abstract corpus, `llama3.2:3b` reader; eval).
- **Receipt 4 holds:** 188 of 188 identical to B0 under `UMRS_RAG_QUERY_PREFIX=b0`.
- **The query-prefix fix alone:** r@1 **+9.0** points [+3.9, +14.8] and nDCG@10 +5.8. Every live query had been encoded as `passage: query: q`.
- **The dense cut plus the pack (S1):** verdict accuracy 0.523 → **0.610**, +8.7 [+3.3, +14.3], McNemar p = 0.003. Over the same comparison:
  - macro-F1 0.422 → 0.568;
  - abstention with the gold shown 0.116 → 0.042;
  - citation precision 0.631 → 0.773.
- **Nonsense:**
  - all 30 probes are INSUFFICIENT in every mode;
  - `/rag/query` answers 0 of them, where B0 answered 30%;
  - false-INSUFFICIENT on evidence claims is at most 1.1%.
- **Gates:**
  - S3 lexical retrieval passes on r@1 +4.8, but its CI includes 0.
  - S4 reranker fails narrowly on every clause, so it stays off. It gave the best verdicts (0.613, F1 0.571, NEI hallucination 0.598), but within noise of the dense cut.
  - S2 merge fails: coverage +8.9 against +10.
  - Every arm that showed the 3B more text raised its verdicts on NEI and nonsense claims.
- **Defaults set from these measurements (small profile):**
  - named small readers get no lexical inclusion (profile field `lexical`);
  - the dense gap γ = 0.02, tuned on SciFact train only (`benchmarks/retrieval/tune_cut.py`);
  - reranker and merge stay off.
- **Large profile (D-14) on rikyu (kimi-k3, glm-5.3):** waits for the end of rikyu maintenance (2026-10-13 17:00 JST). MD-14 was decided by the maintainer: yes, both models.

**Live stand-alone fixes found while measuring** (eval doc, "Defects found"):
- Postgres runs under an init: a killed healthcheck had crashed the postmaster once a minute;
- BLAS threads are pinned to each container's CPU quota: queries had taken up to 31 s and the encoder timed out;
- KNOWLEDGE_EMBED runs off the event loop: it had caused 504s;
- `UMRS_KNOWLEDGE_LLM_EXTRACT` is mapped.

**Still running when this block was written:**
- the agent arm with the router on (S5);
- the prompt variants;
- the scorer gate on dev;
- the local knowledge-graph probe (3B extraction, graph lane off vs on, 47 multi-evidence claims);
- the dev confirmation of the tuned γ.

Their results go into the eval doc and a later block here.

## Rollout-status (2026-10-11)

Still Proposed, on `feat/adr-093-retrieval`. Phase 3 is measured. It found a regression this branch introduced, which is now fixed (wave E). Details and CIs are in the eval, "Phase 3".

**Measured (stand-alone, `llama3.2:3b`).**
- **S5, the companion with the router on (60 dev claims):**
  - retrieval: every turn retrieved (B0: 5 of 60), and the gold abstract reached the model on 32 of 40 evidence claims (B0: 1);
  - accuracy 0.567 against 0.433, paired +13.3 [−3.3, +28.3], p = 0.15. The direction favours the router; n = 60 cannot settle it;
  - nonsense: 1 verdict in 30 (B0: 3).
- **The scorer gate (fused + SOFT, 300):** accuracy +1.7 [+0.3, +3.3]; NEI verdicts −4.5 [−8.9, −0.9]; no new wrong abstentions.
- **Prompt variants (150, paired against SOFT):** no significant difference. STRICT +3.3 [−1.3, +8.0]; QUOTE +2.0, with 19% of its citations naming labels never shown; edge order +0.7. SOFT stays.
- **Regression: the `knowledge_query` tool's output as context.** Against B0, accuracy counting cut replies as wrong is −18.7 [−25.0, −12.3]; replies cut at the verdict budget rose from 1 to 82 of 300.
  - Cause: this branch's KGC-04 hit fields and sufficiency block, with the tool still asking for 10 hits.

**Wave E (C23, C24), built, adversarially reviewed and fixed (15 + 6 findings, 14 + 6 kept, each with a failing-first test).**
- **C23 (D-14):**
  - `knowledge_query` asks K3 for its reader's profile `k_max`: small 4, medium 6, large 10. No reader, or an unresolved one, keeps 10. Hits omit empty fields.
  - The reader is threaded from `get_builtin_tools(session_model=...)`, as `search_docs` already was.
  - Plan agents (ADR-092 broker) and teamup role agents are sized by their own turn's model: the agent's or role's own, else the lead's or team's, else the client default. Their `search_agentic` also plans and synthesises on that model, as the lead session's does. A client whose config cannot be read only loses the sizing; the role still runs.
  - Harness: the tool arms are built for the run's reader. Provenance records `tool_reader` and `tool_sizing`, and `compare` holds the K3 limit fixed as the factor `tool_sizing`.
  - The live re-measure is phase 7 of the eval.
- **C24 (D-9):** the citation check reads a passage's source name as its citation when the turn showed it. A label written against another shown passage's name is reported in the footer as a label mismatch. Other changes:
  - digits inside a matched name are not checked as numbers;
  - citation-only lines join the claim before them, so a trailing `[S1] name` no longer scores as a supported sentence of its own;
  - only file- or identifier-shaped names count, so "COVID-19" in prose is not a citation;
  - CJK text and linear time on long answers.

**Still running:**
- phase 4, the local knowledge-graph probe (332 documents, 3B extraction);
- phase 5, the dev confirmation of γ;
- phase 6, the S7 sent-v1 re-chunk with KNOWLEDGE_REINDEX, then back to fixed-v0;
- phase 7, the C23 re-measure;
- phase 8, a retrieval-only probe of the router's query.

  Phase 8 is there because the router searches the whole message, instructions included, and on nonsense it packed 7 probes that the bare claim leaves insufficient.

## Rollout-status (2026-10-11, phases 4–12, waves F–H)

Still Proposed, on `feat/adr-093-retrieval`. Details and CIs are in the eval, Phases 4–12. The work after 08:00 JST was taken over from a suspended session; each wave was independently verified before it landed.

**Small-profile defaults: the 2026-10-10 tuned values are reverted (Phases 5 and 9).**
- γ goes back to **0.015**. 0.02 bought no recall on dev and raised verdicts on NEI claims by +3.6 [+0.9, +7.1].
- Lexical inclusion goes back to the **site default** for every profile: `RetrievalProfile.lexical = "gate"` in r4-contracts, the table UMRS reads. Turning it off had cost the answer path −14.9 [−22.3, −8.0] points of citing the evidence, and let a nonsense question through.
- Together these restore the best answer path measured: cites evidence 0.798, wrong abstention 0.043, nonsense 30 of 30 refused.
- The "Defaults set from these measurements" list in the 2026-10-10 block is superseded.

**Measured.**
- **Phase 4, local KG (3B extraction):** the graph bridge lane costs −22 points of ctx precision for +4 of coverage, and verdicts do not move. It stays gated (D-14).
- **Phase 7, C23:** sizing the hit count repairs half the `knowledge_query` regression: +10.7 [+4.3, +17.3] against unsized, still −8.0 against B0.
- **Phase 10, S7 (`sent-v1`):**
  - The reindex converged in one sweep: no index errors, no dangling edges, and the restore was exact.
  - Ctx precision +6.0 [+2.6, +9.4], rationale coverage +20.3. Verdicts n.s.
  - One nonsense probe got through (29 of 30). The null is recomputed from the `sent-v1` vectors, so that is noise, not a stale calibration.
  - As pre-registered, the gate is not met: E-A accuracy −0.7 [−4.7, +3.3] is not shown non-inferior at a 3-point margin, and 1k r@1 was not measured. Full nDCG@10 +1.4 [−0.7, +3.6] passes. `sent-v1` stays opt-in.
- **Phase 11, C23b:**
  - Each hit sized by its reader's share: +2.7 [−1.0, +6.3] against C23 (n.s.), still −5.7 [−11.0, −0.3] against B0.
  - Context grew only 35 tokens a claim, because K3 clipped hit text server-side at 240 characters. Wave F lifts that cap.
- **Phase 12, wave H:**
  - Focused retrieval equals the bare claim's on all 7 tuning framings: gold 165 of 188, nonsense 29 of 30, against 100 and 9 for the eval prompt framed.
  - The held-out gate failed: gold mean 142 against 156 required, and the Japanese and polite-preamble framings regressed on one axis each.
  - D-8's amendment above keeps focus opt-in.

**Built and verified.**
- **C23b round 2 (D-14, CONSTITUTION §2):**
  - Every string K3 returns to `knowledge_query` goes through `sanitize_text`, which strips the whole Tags block, bidi, zero-width, controls and now lone surrogates.
  - The unsized path cuts at exactly 200 characters, as before. A record carrying only the interim `knowledge_query_chars` reads as unrecorded.
  - The verify found one defect, fixed: a lone surrogate made the sized output impossible to encode.
- **Wave H (D-8):**
  - `query_focus`, the router wiring, and the framing probe.
  - The verify found a cubic regex (a 9–14 s turn hang) and a gate that cut plain questions down to their quoted term. Both are fixed, with regression tests.
- **Wave F (D-11 amendment, MD-16, MD-17), umrs `wip/adr-093-wave-f`:**
  - one transaction per artifact in the embed writer;
  - no rowless artifact goes down the chain;
  - bundle import skips the rows of an artifact it deduplicated;
  - `DELETE /rag/documents/{id}` answers 501;
  - no lane reads an orphan chunk;
  - K3's similarity, lexical and graph lanes carry the full hit text up to `UMRS_K3_HIT_TEXT_MAX`, with `metadata.clipped`;
  - the `orphan_repair` verb, dry-run by default;
  - KNOWLEDGE_REINDEX converges: keyset pages, held artifacts skipped, a per-project lock, a fresh key per pass;
  - migration 0122.

**Wave F deployed on the stand-alone stack (eval Phase 13).**
- Migration 0122 applied.
- The orphan dry-run finds exactly the 3 known orphans.
- Delete answers 501.
- K3 hits carry their full text.
- Two reindexes without a key are two jobs.
- **C23b on wave F:** accuracy +8.3 [+3.0, +13.7] against C23 and level with B0 (0.0 [−6.0, +6.0]), so the Phase 3 regression is closed on accuracy. Verdicts on NEI claims remain +11.6 [+2.7, +20.5] above B0.
- **Found:** artifacts that cannot be embedded on the site (images on a text-only site, missing objects, no file node) are reselected by every reindex pass and fail by name each time. The selector needs a by-name failure record.
- *Fixed later the same day (umrs `f05a04de`, D-11):* a permanent embed failure is recorded on the artifact row (`metadata.embed_failed` under the model and scheme). The sweep skips it and reports `failed_skipped`, `retry_failed` overrides the skip, and a successful stamp clears the record. Live, the second pass converges: swept 0, `failed_skipped=9`.

**Open:**
- the `knowledge_query` frame's instruction (its NEI verdicts);
- `sent-v1` for large readers (S7b measured the 3B only: macro-F1 not shown non-inferior);
- query focus beyond deterministic rules (focus-v2 failed a blind held-out set, eval Phase 15; it stays opt-in);
- the rikyu arms after 2026-10-13 17:00 JST;
- MD-1 to MD-17.

## Rollout-status (2026-10-12, S6–S9; accepted)

- **S6 (consumers):** pass. The agentic `knowledge_query` search is grounded on 29 of 30 dev claims (0.967; gate ≥ 0.9), with 0 phantom answers; the server-hosted loop puts gold evidence in the pack for 5 of 5 goals (gate ≥ 3). Eval Phase 16.
- **S8 (portable):** pass, all five gates. The v1.3 `--chunk-text` bundle's chunks hash to `chunk_md5`, and v1.2 keeps its shape. The offline load holds every chunk. An import into a second installation keeps nDCG@10 at 0.839 (Δ 0.000) and recall@5 at 0.915, the same as v1.2. The gate found and fixed four export defects (umrs `637c1292`, `8460d91c`, `c4f74ad5`; parent `7211a670`). Eval Phase 17.
- **S9 (confirm):**
  - **Latency:** retrieve p95 is 0.264 s (≤ 1.5 s). **Pass.**
  - **Reproducibility:** run 2 reproduces run 1 claim for claim (300/300 dev, 809/809 tune).
  - **Against B0:** nDCG@10 +0.074 [+0.046, +0.102]; reader accuracy +8.7 pts [+3.3, +14.3]; macro-F1 +14.6 pts [+6.8, +22.3]; wrong abstain when gold is shown −7.6 pts [−13.3, −1.9].
  - **Receipts:** receipt 1 is met on the 1k graph, and receipt 2 too (nonsense insufficient 30/30, false insufficient 0/188). Eval Phase 18.
- **MD-2 outcome:** the agent arm, at 60 claims per label with the router on, is +11.7 pts [−6.7, +28.3] against B0, but McNemar p = 0.26 and p50 turn time is 158 s against 100 s. The S5 gate fails, so `R4_RAG_ROUTER` stays opt-in and `auto` does not route small readers.
- **MD-10:** the aggregate record is `benchmarks/retrieval/records/adr093-2026-10-12.json` (124 runs; numbers and digests only).
- **Accepted** per MD-1. The branch `feat/adr-093-retrieval`, which also carries the ADR-092 slices, is pushed; merging into `main` is the maintainer's.

**Open after acceptance:**
- the rikyu and large-reader arms (MD-14);
- a router-off agent arm at 60 claims per label, on a fast reader;
- the `knowledge_query` frame's verdicts on NEI claims;
- Japanese/CJK retrieval (MD-8);
- `degraded=False` on a degraded export;
- the deletion event type (MD-16);
- the production converge steps that are the maintainer's: the `pg_trgm` privileged run, an orphan-repair dry-run and then apply on umrs-front, and the rccs reranker manifest.

## Rollout-status (2026-10-13, MD-14: the large profile read by kimi-k3 and glm-5.3)

- **What ran:** the arms pre-registered in eval Phase 19 (commit `f2712d99`, before any rikyu verdict was read). On 300 dev and 30 nonsense claims, each rikyu reader read `legacy`, `retrieve:large`, `retrieve:dense`, `closed` and `oracle`. Retrieval ran on the stand-alone stack; only the verdict call went to rikyu.
- **Q1, large vs legacy:** kimi-k3 +0.3 [−3.3, +4.0]; glm-5.3 +3.1 [0.0, +6.2], McNemar p = 0.08. *Better* is not shown.
- **Q2, large vs the small pack:** kimi-k3 −1.0 [−4.7, +2.7], **not non-inferior** at the 3-point margin; glm-5.3 +2.0 [−1.4, +5.4]. Verdicts on NEI claims rise with the larger pack: kimi-k3 +8.9 [+2.7, +16.1], glm-5.3 +5.5 [−0.9, +12.7].
- **Q3:** nonsense flagged "not covered" on 30/30 for each reader. **Pass.**
- **Q4, headroom used (large / dense):** kimi-k3 0.91 / 0.93; glm-5.3 0.90 / 0.85.
- **Decision, as pre-registered:** `RETRIEVAL_PROFILES["large"]` now uses the medium sizes (`k_max` 6, partial 3, pool 50, pack 4 / 2,000). UMRS and R4 read that one table. The harness's `retrieve:large` preset reads it too; older result files record their sizes in `preset`.
- **MD-10 record:** `benchmarks/retrieval/records/adr093-2026-10-13.json` (138 runs).
- **Kept large-only:** no explicit output limit, LLM multi-query expansion (not measured by these arms), and graph-lane eligibility (MD-15 still needs its multi-hop probe).
- **Open:**
  - the large profile's query expansion arm;
  - MD-2 for large readers, which needs a rikyu-hosted agent session;
  - MD-15's multi-hop probe with a large reader.

## Related

- [ADR-027](027-unified-knowledge-spine.md), [ADR-039](039-untrusted-content-provenance-taint.md), [ADR-062](062-model-locality-data-class.md), [ADR-013](013-knowledge-portability-and-compute-separation.md)
- [ADR-081](081-typed-knowledge-spine.md), [ADR-083](083-embedding-spaces.md), [ADR-084](084-growth-governance.md), [ADR-085](085-agent-kg-verb-contract.md), [ADR-087](087-harness-model-call-contract.md), [ADR-090](090-standalone-single-host-deployment.md)
- standalone-kg-mcp-rag-validation-2026-10-09.md, kg-multimodal-investigation-2026-10-03.md
- `benchmarks/retrieval/README.md`, [EMBEDDING-POLICY](../EMBEDDING-POLICY.md)