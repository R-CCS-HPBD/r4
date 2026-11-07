# ADR-068: One internal inference API — three named surfaces, one addressing rule

**Status:** Accepted (contract of record; surfaces exist, catalog pinning landed)
**Date:** 2026-09-22 (gap row D-11, under maintainer proceed authority)
**Relates to:** ADR-002 (per-attempt manifests), ADR-044 (vision durable wrap), ADR-061 (model-server profile), ADR-062 (model locality), gap rows D-11 / D-43

## Context

The design asks for one internal inference API for LLM / Vision / VLM /
embedding so agents do not depend on which backend runs, with model
versions recorded in the catalog. The tree had three working but unnamed
contracts: OpenAI-compatible chat/completions through LiteLLM
(`api/llm.py`), OpenAI-compatible `/v1/embeddings` on the same lane
(`api/llm.py:841`), and the BentoML vision service with task-shaped
endpoints (`vision_service/`, durable wrap ADR-044); embeddings for
ingest also run in-process (`inference/embeddings.py`) with lineage stamped
per vector. Nothing stated that these ARE the one API, and catalog rows
never pinned model ids.

## Decision

1. **The one internal inference API** = three named surfaces, one rule:
   - **Text + VLM:** OpenAI-compatible chat/completions at the UMRS LLM
     lane (`/umrs/llm/...`) — image inputs ride the same messages shape;
     UMRS rewrites the requested catalog/source name onto the served
     variant (model rewrite in `api/llm.py`).
   - **Embeddings:** OpenAI-compatible `/v1/embeddings` on the same lane;
     the durable ingest chain's in-process embedder stamps
     model+version+dimension lineage per vector row (0075/0041).
   - **Vision tasks:** the task-shaped vision API (`/umrs/vision/...`,
     BentoML-backed, ADR-044) — addressed by task, never by backend URL.
2. **Addressing rule:** agents address inference by catalog/source name;
   raw vLLM/BentoML URLs are never agent-visible (mini-RiVault internal
   listeners only). Backend substitution is a server-side rewrite, so a
   backend swap is invisible to callers.
3. **Model versions recorded (D-43 tie):** Scientific Catalog create pins
   `embedding_model_ids` into `provenance.embedding_models` with the
   model-registry resolution result (`registry_resolved`); endpoint
   serving versions stay on the per-attempt execution manifests
   (ADR-002). Registry misses and read failures fail open with honest
   markers (`registry_resolved: False` / `registry_unreachable: True`) —
   a pin records truth, it never blocks creation.
4. New backends join by implementing one of the three surfaces; a fourth
   surface requires an ADR.

## Consequences

- D-11 moves Absent → Implemented-as-contract: the surfaces exist, are
  named, and the catalog records model identity; the remaining honest
  tail is vision-task schema documentation in the platform overview
  (docs sweep) and VLM-through-proxy live receipts (D-10's tail).
- No code was replaced — the contract names what already runs; the only
  new behavior is the catalog pin (fail-open).
