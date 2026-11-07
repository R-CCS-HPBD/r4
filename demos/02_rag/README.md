# Demo 02: RAG

Upload a document, list indexed documents, and run a retrieval-augmented
generation query.

## What It Shows

- `r4 rag upload` — document ingestion
- `r4 rag docs list` — indexed document inspection
- `r4 rag query` — retrieval-augmented query
- `r4 rag retriever components` — retriever component discovery

## Input Files

- `attention_is_all_you_need.txt` — Transformer paper summary (uploaded during demo)

## Quick Start

```bash
bash run_demo.sh --auto
```

## Prerequisites

Run `00_onboarding` first to provision the endpoint.

## Detailed Step-by-Step (verified 2026-08-27)

1. **Upload the paper**
   ```bash
   r4 rag upload attention_is_all_you_need.txt --project demo-project
   ```
2. **Inspect the indexed documents**
   ```bash
   r4 rag docs list --project demo-project
   # NOTE: folded into `r4 knowledge docs` per ADR-027 P1b; behavior identical
   ```
3. **Query**
   ```bash
   r4 rag query "What does the paper say about attention?" \
       --project demo-project --top-k 3
   # 404 if corpus is EMPTY (fresh project) — upload at least one document first
   ```
4. **Retriever components**
   ```bash
   r4 rag retriever components embedders   # MiniLM-L6, MPNet, Multilingual…
   ```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Error: API error: 404` on query | knowledge route/posture: no documents in the project corpus | do step 1 first; `r4 knowledge docs --project X` should list ≥ 1 |
| `Error: API error: 500` on `rag docs list` | corpus service on the called cluster hasn't the knowledge plane ready | point demos at a cluster where the knowledge registry is served; empty answers are also normal for fresh projects |
| Notes say "folds into `r4 knowledge docs`" | ADR-027 — query alias, behavior identical | safe to ignore |
