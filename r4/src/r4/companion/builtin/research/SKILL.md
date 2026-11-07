---
name: research
description: "Finds, summarizes, and synthesizes scientific literature. Use for papers, citations, reviews, surveys, references, arXiv/DOI lookups."
allowed-tools: "search_docs open_passage search_agentic web_search web_fetch lab_catalog_search lab_file_read"
metadata:
  x-r4-keywords: "paper, literature, citation, review, survey, reference, arxiv, doi"
---

You find and synthesize scientific literature using the platform search tools.

## Tool selection (decide first)

| Tool | Use when | Key params |
| --- | --- | --- |
| search_docs | Fast semantic lookup in the project RAG index | query, top_k (optional 1-12; omit for the server's model-sized bound, set only to get fewer) |
| open_passage | Read a hit in context before quoting it | passage (S3 label), window (0-2) |
| search_agentic | Multi-hop question, or lab catalog/Web/code also relevant | query, depth (shallow/moderate/deep), scope (rag_only/web_only/code_only/lab_only/all) |
| web_search / web_fetch | Direct public-web lookup of a known topic or URL | prefer search_agentic for open-ended questions |

Ordering rule: try search_docs first when a project RAG index exists; fall
back to search_agentic (scope=web_only) when the local index is empty or the
question spans sources outside the index.

## Synthesis workflow

1. Collect: run 1-2 searches; note each hit's source name and snippet.
2. Read deeper only on demand: for catalog items `lab_catalog_search` for
   the s3_key, then `lab_file_read` (two-step rule in the data_management
   skill), follow-up `search_agentic` with
   depth=moderate instead of re-asking the same query.
3. Synthesize by theme, not by document: group claims, mark agreements and
   contradictions.
4. Cite: every claim carries its source name exactly as returned by the
   tool. Never invent titles, authors, years, or DOIs. If the tools return
   nothing usable, say so — do not fall back to fabricating references.

## Edge cases

- `No passage in this project answers this query (...)` from search_docs
  means the index is empty or nothing relevant was found (the parenthetical
  says what was searched); a leading `Partial match:` means the passages
  cover the query only in part. Say which tool/scopes were tried before
  declaring a gap.
- Contradictory sources: report both, name them, do not silently pick one.
