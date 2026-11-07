# Demo 04: Endpoints & Jobs

Inspect the endpoint lifecycle, mounted mini-RiVault profile, logs,
and durable job surfaces.

## What It Shows

- `r4 endpoint list / status / logs`
- Mini-RiVault mount profile intent (MCP, RAG, project data, EgressGuard)
- `r4 job list / describe` — durable job inspection

## Quick Start

```bash
bash run_demo.sh --auto
```

## Prerequisites

Run `00_onboarding` first to provision the endpoint.

## Detailed Step-by-Step (verified 2026-08-27)

1. Endpoint surface
   ```bash
   r4 endpoint list          # all ready/configured endpoints with source column
   r4 endpoint status <id>   # detail of one instance
   r4 endpoint logs  <id>    # events/logs
   ```
2. Mini-RiVault mount intent (echoed by the demo):
   mounts MCP `/mcp-servers/demo-tools`, RAG `/rag-index`, project `/project`,
   EgressGuard domains
3. Durable jobs
   ```bash
   r4 job list --status failed --limit 5
   r4 job describe <job-id>
   ```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No active endpoints` | 0 endpoints ready across the current platform sources | run demo00's provision or register an external source (`r4 source add <source> …`) |
| `r4 endpoint status 404 for a listed id` | id belongs to another cluster's backing (an id registered on `<cluster-a>`, listed while pointed at `<cluster-b>`) | point the client at that cluster (`R4_GATEWAY_URL=…/<cluster>` env override) or run with the matching source |
| Attempt rows: `backend_submission_failed: knowledge_ingest requires spec.sources` | historical attempt kind with missing spec.sources (ledger defect, not this demo) | not caused by this demo; ignore these historical rows |
