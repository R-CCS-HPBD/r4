# Demo 00: Deploy & Onboarding

Check the saved r4 profile, select a project and model, and provision (or
reuse) the mini-RiVault endpoint that the rest of the demo suite depends on.

## What It Shows

- `r4 config show` — current profile
- `r4 auth status` — credential verification
- `r4 project create / switch` — project lifecycle
- `r4 model list` — model discovery
- `r4 endpoint up` — endpoint provisioning with MCP, RAG, and egress mounts
- `r4 health` — gateway readiness

## Quick Start

```bash
bash run_demo.sh --auto
```

## Prerequisites

Start one of the deployment tiers first (see `../tiers/`), then ensure
`r4` is installed and configured:

```bash
r4 config show
r4 auth status
```

## Environment Overrides

| Variable | Default | Purpose |
|----------|---------|---------|
| `R4_DEMO_PROJECT` | `demo-project` | Project ID |
| `R4_DEMO_MODEL` | (auto-detected) | Model name |
| `R4_DEMO_ENDPOINT_MOUNTS` | `1` | Set `0` to skip mount demo |
| `R4_DEMO_MCP_SRC` | `.state/mounts/mcp/demo-tools` | MCP mount source |
| `R4_DEMO_EGRESS_DOMAINS` | `api.example.com` | Egress domain allowlist |

## Detailed Step-by-Step (verified 2026-08-27)

The demo is idempotent — reruns reuse the existing endpoint instead of
re-provisioning. Each command prints what it does (`demo_cmd` prefix).

1. **Profile inspection**
   ```bash
   r4 config show        # profile, project, gateway, model, auth
   r4 auth status        # token configured + scope
   ```
   Expected: your profile is active and the gateway points to your
   deployment (e.g. `https://<host>/<cluster>`).

2. **Project lifecycle** (created if missing, then switched)
   ```bash
   r4 project create demo-project --name "R4 Demo Project"
   r4 project switch demo-project
   r4 config context --project demo-project
   ```

3. **Model discovery**
   ```bash
   r4 model list --json --limit 100
   # resolver picks first type=llm with status=available;
   # if the fresh project namespace is empty,
   # falls back to first 'ready' endpoint model from `r4 endpoint list --json`
   ```

4. **Endpoint provisioning** (only if nothing is ready yet)
   ```bash
   r4 endpoint up --model $DEMO_MODEL_NAME \
       --tier project \
       --mount-mcp .state/mounts/mcp/demo-tools:/mcp-servers/demo-tools \
       --mount-data .state/mounts/project-data:/project \
       --mount-rag .state/mounts/rag-index:/rag-index \
       --egress-domains api.example.com --wait --json
   ```
   Expected JSON: `{"instance_id": "…", "status": "ready", ...}`.
   After that, `run_demo.sh --auto` from demo 01 onward reuses the endpoint.

5. **Health confirmation**
   ```bash
   r4 health
   ```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Unable to resolve a demo model` | model resolver has no candidates (project filtered view is empty AND no ready endpoint visible) | `r4 endpoint list` — register the cluster you expect (e.g. `r4 source add <source> --url … --api-key …`) or set `R4_DEMO_MODEL=<name>` explicitly |
| `Configured model 'X' is not currently available` | set `R4_DEMO_MODEL` to a name that isn't in the registry | list available: `r4 model list --limit 100` |
| Endpoint `pending` forever | this mirrors the durable-job trail — `r4 job describe job-<id>` | `r4 workflow cancel`, check Slurm, inspect log tail (`r4 endpoint logs <id>`) |
