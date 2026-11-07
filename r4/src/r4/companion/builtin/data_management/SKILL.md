---
name: data_management
description: "File storage, lab data catalog, and RAG document management via UMRS"
allowed-tools: "file_upload_platform file_list_platform file_download_platform lab_catalog_search lab_file_read rag_query rag_upload rag_list_documents search_docs open_passage search_agentic knowledge_graph_list knowledge_graph_propose knowledge_graph_decide"
metadata:
  x-r4-keywords: "upload, download, file, storage, s3, minio, catalog, lab data, dataset, microscopy, csv, rag, index, embed, document, retrieval, semantic search, presigned"
---

You can manage files and RAG-indexed documents through UMRS.
{{contract:r4_contracts.agent_tool_playbook:CATALOG_AGENT_GUIDANCE}}
- For broad multi-hop questions use search_agentic (prefer lab_only scope for lab data).
- file_upload_platform uploads lab files; ingestion builds catalog metadata.
- Never expose presigned URLs to end users.
- rag_query for semantic search; search_docs for simpler RAG search.

## Upload feedback loop (verify, never assume)

1. Lab files: file_upload_platform, then record the returned file_id.
   RAG documents: an agent upload needs an approved proposal for the
   project's default graph (find it with knowledge_graph_list). Call
   knowledge_graph_propose(graph_id=<that graph>, reason=...,
   source_artifacts=[<file>]), show the proposal to the user and STOP.
   After the user answers, record that answer with
   knowledge_graph_decide(proposal_id, approved). Only if it was approved,
   call rag_upload(file_path, proposal_id=<id>) and record the returned
   document_id. On the central path that is the ingest job id (status
   `queued-central`), so the document can take a while to appear.
2. rag_list_documents (or lab_catalog_search) — confirm the document
   registered with the expected name; poll again while ingest is queued.
3. Smoke-check retrieval: rag_query with one representative question and
   confirm the new document appears in the hits.
Only then tell the user the data is usable; a silent ingest failure must
never surface later as empty RAG answers.
