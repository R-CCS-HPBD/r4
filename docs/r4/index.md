# R4 Documentation

R4 is the user-facing CLI and Python SDK for this platform. If you are operating a shared deployment, users should stay on `r4`; Gateway, UMRS, and RiVault remain infrastructure surfaces.

## Start Here

| Document | Audience | Purpose |
|---|---|---|
| [contributor-path.md](contributor-path.md) | interns, visiting researchers, new contributors | structured three-task onboarding path: orient → build → document |
| [quickstart.md](quickstart.md) | end users | install `r4`, configure a profile, bring up or reuse an endpoint, and run common commands |
| [TUI_REFERENCE.md](TUI_REFERENCE.md) | end users | interactive companion TUI — UI modes, slash commands, dashboard, mode cycling |
| [admin-guide/README.md](admin-guide/README.md) | operators | deploy and manage Gateway, UMRS, storage, auth, and orchestration |
| [../LIBRARY_GROUP_STRUCTURE.md](../LIBRARY_GROUP_STRUCTURE.md) | developers and operators | understand the `r4`, `r4-contracts`, Gateway, UMRS, MCP, and runtime boundaries |
| [../SERVER_CLIENT_DEPLOYMENT.md](../SERVER_CLIENT_DEPLOYMENT.md) | operators | split shared server deployment from thin client onboarding |
| [reference/cli.md](reference/cli.md) | users and operators | command map, root-level flags, companion slash commands, and CLI ownership boundaries |
| [reference/sdk.md](reference/sdk.md) | developers | Python SDK reference |
| [vision-endpoints.md](vision-endpoints.md) | users and operators | provision BentoML vision endpoints and submit `r4 vision` inference tasks |
| [examples/README.md](examples/README.md) | developers | runnable SDK examples |
| [../../demos/README.md](../../demos/README.md) | users and operators | the canonical demo suite built from real `r4` commands |

## Platform Mental Model

`r4` is the user-facing client. It speaks to Gateway, which enforces the external boundary and forwards platform work to UMRS. UMRS owns durable jobs, files, endpoints, workflows, models, and RAG. Mini-RiVault is the isolated inference runtime that UMRS provisions when a model endpoint is needed.

`r4-contracts` sits beside the client and server as a small shared package for public DTOs and pre-flight validators. It keeps client validation stable without making `r4` import UMRS.

## CLI Map

| Need | Use | Boundary |
|---|---|---|
| Chat, RAG, endpoint, model, workflow, project, MCP, job, artifact, auth, and profile workflows | `r4` | User-facing path through Gateway and UMRS APIs |
| UMRS transport diagnostics, migration helpers, and low-level control-plane checks | `umrs` | Operator/developer diagnostics; not the admin CLI |
| User enrollment, token creation, Slurm account binding, daemon-local setup, and other trusted-host administration | `umrs-server admin` | Offline operator-only administration |

Quick rule: start with `r4` unless you are debugging UMRS internals or
running trusted operator maintenance. `umrs admin ...` is disabled
by default; use `umrs-server admin --help` for supported admin tasks.

The current CLI is organized around these top-level groups:

- `r4 config`
- `r4 auth`
- `r4 health`
- `r4 chat`
- `r4 project`
- `r4 model`
- `r4 endpoint`
- `r4 rag`
- `r4 vision`
- `r4 job`
- `r4 mcp`
- `r4 skill`
- `r4 workflow`
- `r4 session`
- `r4 agent`
- `r4 search`
- `r4 team`
- `r4 run`
- `r4 admin`

For the exact command tree, use the CLI help shipped with the current code rather than an external static page:

```bash
r4 --help
r4 agent --help
r4 admin --help
r4 rag --help
```

## Common Flows

```bash
# Authenticate and configure a shared Gateway profile (one command)
r4 auth login --gateway-url https://<gateway-host> --project <project-id> --oidc

# Check the active profile and credentials
r4 config show
r4 config context list
r4 health account
r4 auth status

# Provision or reuse an endpoint, then chat
r4 endpoint up --model <model-name> --wait
r4 chat "Summarize the platform in one sentence."

# Upload and query RAG data
r4 rag upload paper.pdf --project <project-id>
r4 rag docs list --project <project-id>
r4 rag query "What are the main findings?" --project <project-id>

# Provision and use a BentoML vision endpoint
r4 endpoint up --model sam3 --backend bentoml --service-kind bentoml_vision --wait
r4 vision submit cell.png --model sam3 --adapter sam3

# Discover and install skills from the platform registry
r4 skill search "molecular dynamics"
r4 skill install md-analyzer
r4 skill list

# Hatchling MCP package management
r4 mcp hatch search "chemistry"
r4 mcp hatch install chem-tools --from registry
r4 mcp hatch registry list
```

## Notes

- `r4 auth login` replaced the old `r4 init`, `r4 config init`, and separate `r4 auth login --api-key` flows. `r4 config init` and `r4 init` still work but print a deprecation notice.
- `r4 auth login` now auto-discovers ZMQ transport parameters (control port, auth type, tunnel mode) by querying `GET /gw/ops/connect-info`. No manual ZMQ configuration is needed.
- `r4 auth logout` replaced the old `r4 logout`.
- `r4 ask companion`, `r4 ask teamup`, `r4 ask agent`, and `r4 ask search` have been **removed**. Use `r4` (no args), `r4 team`, `r4 agent`, and `r4 search` respectively.
- `r4 governance`, `r4 map` are top-level commands.
- `r4 trace`, `r4 pool`, `r4 source` have been **moved**. Use `r4 admin trace`, `r4 admin pool`, `r4 admin source` (or `r4 source` which aliases `r4 admin source`).
- `r4 agent-exec` has been **removed**. Use `r4 run`.
- `r4 session` replaced `r4 sessions`.
- `r4 rag docs list` replaced `r4 rag list`.

If you find documentation that still uses the old names, treat it as historical or update it to use the current paths.