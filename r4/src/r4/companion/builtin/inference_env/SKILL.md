---
name: inference_env
description: "Prepare a customized inference env (mini-RiVault + MCP/RAG mounts)"
allowed-tools: "platform_health endpoint_list endpoint_status endpoint_provision endpoint_stop list_models model_list_registry model_details model_stage knowledge_graph_list knowledge_graph_propose knowledge_graph_decide rag_upload rag_query rag_list_documents mcp_list_servers mcp_list_tools mcp_invoke mcp_register_server mcp_unregister_server workflow_upload workflow_list workflow_status workflow_trigger"
metadata:
  x-r4-keywords: "custom inference, inference env, customized environment, mini-rivault, mount mcp, custom mcp, attach rag, prepare workflow, provision with mounts, skills mcp rag, inference environment, mount rag"
---

You prepare complete customized inference environments from chat - not just an endpoint, but the whole stack around it.

Checklist (copy and track; confirm scope and source with ask_user first):

1. [ ] platform_health + endpoint_list — reuse a matching ready endpoint
2. [ ] model_list_registry / model_details — stage only what is missing
3. [ ] knowledge_graph_propose → user-approved decide → rag_upload(proposal_id) + rag_query smoke check
4. [ ] mcp_register_server / mounts config.json validated (capabilities + sandbox)
5. [ ] workflow_upload + workflow_list — bundles registered before any trigger
6. [ ] endpoint_provision with mounts + egress_domains + sizing
7. [ ] endpoint_status ready (+ optional workflow_trigger smoke run)

Recipe details:
1. platform_health + endpoint_list on the target source - reuse an existing endpoint when it already matches.
2. Model readiness: list_models / model_list_registry; if the model is missing use model_stage (durable job) - NEVER download models at inference time.
3. Knowledge/RAG: knowledge_graph_propose(graph_id=<project default graph, see knowledge_graph_list>, reason, source_artifacts=[files]) → show the proposal and STOP; after the user answers, record it with knowledge_graph_decide(proposal_id, approved) → only if approved, rag_upload(file_path, proposal_id=<id>) (UMRS refuses an agent upload without an approved proposal; rows land 'proposed'), then verify with rag_list_documents / rag_query before wiring them in.
4. Custom MCP: for mounted servers demand config.json with capabilities + sandbox (read-only filesystem, egress_guard network, no shell entry points); use mcp_register_server for URL-based servers and verify with mcp_list_tools.
5. Workflows: upload the demanded bundles with workflow_upload and confirm with workflow_list before any trigger.
6. Provision: endpoint_provision with mounts (UMRS EndpointMounts JSON: mcp_servers list under /mcp-servers, project_data, rag_index - all read-only), egress_domains (comma list, empty = offline), gpu/node/partition, ttl_sec.
7. Verify: endpoint_status until ready, then optional workflow_trigger smoke run and workflow_status.
Invariants: mounts are read-only except scratch; inference containers stay offline unless egress_domains explicitly extend; never inject credentials into mounts.
