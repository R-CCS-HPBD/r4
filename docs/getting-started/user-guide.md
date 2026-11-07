# R4 + UMRS — User Guide

> **Audience:** Scientists using a deployed platform
> **Status:** Updated 2026-09-22 (design reset) — every command below was checked against `r4/src/r4/_cli/` on that date; framing follows [the design](../R4-UMRS-DESIGN.md). Where the client still differs from the design, the gap row (`D-nn`) from [the gap analysis](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md) is cited.
>
> For chapter-by-chapter coverage see the [User Manual](../user-manual/README.md);
> for what the platform is and why, read the [Introduction](introduction.md) first.

---

## What you are working with

You work in **R4**: the console (`r4 console`), the command line (`r4 …`), the
Python SDK and the companion agent behind them. R4 never touches a scheduler,
a bucket or a database itself; everything heavy goes to **UMRS**, the backbone
on your cluster's front-end, which holds your account, moves your data,
schedules jobs and remote agent sessions, runs self-deployed models inside
sandboxes on the GPU nodes, and stores raw data in S3 and vectors in
Postgres + pgvector.

The thing you are building is your project's **Scientific Catalog**: the
materials you ingest, the retrieval index and vectors UMRS derives from them,
the knowledge graph, the skills, and the MCP servers your domain relies on.
Chat, RAG queries, endpoints and workflows are the means; the catalog and the
research loop that grows it are the point. Today the catalog exists as its
parts (knowledge graph, vectors, data catalog, skills, MCP registry, signed
`.kb` bundle) rather than as one exportable object (D-39, D-40).

Your group's own tools become callable by agents as **custom MCP servers**,
built with Hatch and pulled/started with Hatchling (`r4 mcp hatch …`).

## Table of Contents

1. [Install and authenticate](#install-and-authenticate)
2. [The console](#the-console)
3. [Projects, knowledge and the research loop](#projects-knowledge-and-the-research-loop)
4. [Chat and companion](#chat-and-companion)
5. [Models and endpoints](#models-and-endpoints)
6. [RAG: the retrieval index](#rag-the-retrieval-index)
7. [MCP tools and Hatch packages](#mcp-tools-and-hatch-packages)
8. [Workflows and jobs](#workflows-and-jobs)
9. [Vision endpoints](#vision-endpoints)
10. [Multi-source routing](#multi-source-routing)
11. [Agent workflows](#agent-workflows)
12. [Remote sessions](#remote-sessions)
13. [Configuration reference](#configuration-reference)
14. [Troubleshooting](#troubleshooting)

---

## Install and authenticate

### Install

```bash
# One-shot installer (recommended)
curl -fsSL https://raw.githubusercontent.com/R-CCS-HPBD/r4/main/scripts/install-r4.sh | bash
export PATH="$HOME/.local/bin:$PATH"

# Or from a clone (the packages are not on PyPI)
git clone --recurse-submodules https://github.com/R-CCS-HPBD/r4.git
cd r4 && make install-client

# Fetch the console binary (sha256-verified)
r4 tui install
```

### Authenticate

An admin-issued token carries the server URL and your default project; with
OIDC you pass the front-end URL yourself.

```bash
# Token login (server URL and project come from the token)
r4 auth login --api-key <TOKEN>

# Interactive OIDC login (browser-based)
r4 auth login --gateway-url https://<front-end> --oidc

# Show identity, expiry and bootstrap freshness
r4 auth status

# Refresh the access token / clear credentials
r4 auth refresh
r4 auth logout
```

`r4 auth login` also discovers the ZeroMQ transport parameters from the
server (`/gw/ops/connect-info`) and mints your session keys, so no manual
transport configuration is needed. It still writes a per-user object-store
configuration to your machine; the design says storage is reached only
through UMRS, so expect this to disappear (D-16).

### Local context

```bash
# Show the active profile
r4 config show

# Persist the default project for the active profile
r4 config context --project my-project

# Persist the default session venue (local | frontend | cluster:<name>)
r4 config context --default-venue frontend

# Saved contexts for all profiles
r4 config context list
```

The default model is saved at login (`r4 auth login --model <name>`) or set
with `R4_MODEL`.

---

## The console

`r4 console` is the primary surface: a terminal console (OpenTUI v3 binary)
that works over SSH to the front-end as naturally as on a laptop.

```bash
r4 tui install        # download + verify the binary for this host
r4 tui status         # what r4 console will run (checkout source or binary)
r4 console            # the console
r4 console --demo     # same console with fixture data (DEMO MODE)
r4                    # no subcommand: the console as well (Rich REPL with --legacy-tui)
r4 -p "Summarise this project"   # one-shot prompt, no interactive loop
```

Routes: `work`, `tasks`, `knowledge`, `catalog`, `sessions`, `loop`,
`research`, `code`, `infrastructure`, `settings`. `Ctrl+P` opens the command
palette; `/` in chat opens the slash-command bar (`/help` lists commands,
`/clear`, `/quit`, `/models`, `/sessions`, `/mode`, `/new`, `/theme`).
Keymap and copy/paste details: [console README](../../packages/r4-tui-v3/README.md).
The design names the console the single entry point; the CLI stays for
scripting and the VS Code extension, web console and Jupyter magic are
secondary (D-25).

---

## Projects, knowledge and the research loop

A project is the unit of work, storage and knowledge. Ingest materials into
it, let UMRS build the graph and vectors, verify readiness, then run the
research loop against it.

```bash
# Projects
r4 project create my-research
r4 project list
r4 project switch my-research
r4 project current

# Explicit ingestion: R4 hands the directories to UMRS, which lands them in
# S3 and runs the durable ingest → understand → embed → verify jobs; those
# jobs build the graph and vectors (no `r4 project init` step: init reads a
# local manifest only the deprecated `--local` import writes; 2026-10-24)
r4 project import ./papers ./protocols ./notebooks
r4 knowledge ls                      # what the project knows, who added it, indexed yet
r4 knowledge query "what does the project say about X?"   # ask the server's knowledge
r4 project status
# (2026-10-24, K1 part B review: `r4 project verify` and `r4 project
# readiness` read the local manifest of a `--local` import; after a durable
# import they answer "No artifacts … Run: r4 project import".)

# Research loop (server-hosted engine, one durable job per loop)
r4 project plan
r4 project loop --help               # start / status / decisions
r4 project export                    # .kb bundle of the project's knowledge

# Bundles
r4 knowledge export --project my-research --output my-research.kb
r4 knowledge validate my-research.kb
r4 knowledge import my-research.kb
r4 knowledge bundles                 # local bundles (was: list, still an alias)
```

`r4 project ingest` and `r4 project understand` still run as deprecated
aliases of `import` and `init`. Skills are Agent Skills `SKILL.md`
directories (`r4 skill list / search / install / info`); they are not yet
part of the exported bundle (D-27). The loop pauses at tool approvals you
answer from any client; a cost- or hardware-triggered ask policy and
per-project compute budgets do not exist yet (D-33, D-36).

Full walkthroughs: [Knowledge management](../user-manual/27-knowledge-management.md)
(unified query, multigraph, explorer, `.kb` export/restore) and
[Autonomous research](../user-manual/26-autonomous-research.md) (create a
project, seed knowledge, start the loop).

---

## Chat and companion

### One-shot chat

```bash
r4 chat "What is the capital of France?"
r4 chat --model Qwen2.5-7B-Instruct "Explain quantum computing"
r4 chat --stream "Write a haiku about science"
r4 chat --knowledge ./my-research.kb "Summarise our sample-prep procedure"   # local bundle for RAG-augmented chat
```

### Interactive

```bash
r4                    # console (see above)
r4 companion          # same
r4 --legacy-tui       # Rich REPL fallback
r4 chat --companion --resume <session-id>
```

In the console and companion, retrieval from the project's knowledge is part
of context control: the agent pulls catalog passages, session history and
project state into the model's context under a token budget, and writes new
knowledge back only by proposal (`knowledge_graph_propose` → your decision).

*Updated 2026-10-10 (ADR-093):* the agent searches with `search_docs`, which
asks UMRS for passages sized for the session model's detected scale (see
`r4 model scale` below). Searching before the model's first round is off by
default; `R4_RAG_ROUTER=auto` turns it on for small models and
`R4_RAG_ROUTER=on` for every model ([Appendix B](../user-manual/appendix-b-env-vars.md#r4-retrieval-adr-093)).

### Python SDK and Jupyter

```python
from r4 import Client

client = Client()                       # from env / saved profile

response = client.chat("Explain the central limit theorem")
print(response.content)

for chunk in client.chat_stream("Write a poem about HPC"):
    print(chunk, end="", flush=True)

# Passages from the project's knowledge, best first, with UMRS's verdict (ADR-093)
result = client.rag.retrieve("What does this paper say about MPI?", project_id="my-research")
print(result.sufficiency.status)        # sufficient | partial | insufficient | unknown
for p in result.passages:
    print(p.rank, p.source_name, p.section, p.text[:80])

# An answer composed on the platform, with its sources
response = client.rag.query("What does this paper say about MPI?", project_id="my-research")
print(response.answer)
```

*Updated 2026-10-10:* `client.rag.chat_with_context()` and
`client.rag.inject_context()` were removed. To answer with your own model, pass
the `retrieve()` passages to your chat call; the
[SDK reference](../user-manual/14-sdk-reference.md#rag) shows how. UMRS decides
which passages are relevant; do not filter them again by score.

In a notebook: `pip install "r4[jupyter]"`, then `%load_ext r4.jupyter` for
the `%%r4`, `%r4` and `%rag` magics.

---

## Models and endpoints

Models live in the UMRS model registry with scopes `system` (visible to all,
read-only), `project` (project members) and `user` (owner only). Endpoints
are self-deployed model servers that UMRS launches on demand inside a
mini-RiVault sandbox on a GPU node (vLLM + LiteLLM; offline at boot).

```bash
# Models
r4 model list
r4 model list --scope system
r4 model info Qwen2.5-7B-Instruct
r4 model stage Qwen/Qwen2.5-7B-Instruct --scope user      # durable download job with allowlisted egress
r4 model register my-model --path /shared/models/my-model --scope project --project my-research
r4 model scale [MODEL]                                     # size class (small/medium/large) that sizes retrieval (2026-10-10)
r4 model scale set MODEL large --source rikyu              # pin it when detection cannot tell

# Endpoints
r4 endpoint up --model Qwen2.5-7B-Instruct --wait
r4 endpoint up --model Qwen2.5-32B-Instruct --tier project --gpu-count 2
r4 endpoint list
r4 endpoint list --all                 # including stopped
r4 endpoint status <instance-id>
r4 endpoint logs <instance-id> --follow
r4 endpoint down <instance-id>

# Shortcuts
r4 up --help                            # start one endpoint or a pool
r4 down --help                          # stop one endpoint, all, or a pool
```

Endpoint states you will see: `provisioning` (job submitted), `starting`
(container up, model loading), `ready` (`/v1/models` answers), `failed`,
`stopped`. Tiers: `system` (warm, no egress), `project` and `user`
(on-demand, EgressGuard allowlists). A separate VLM endpoint class and one
inference contract across LLM / vision / embedding are still open (D-10,
D-11).

---

## RAG: the retrieval index

The RAG commands manage the retrieval index directly. `r4 project import` is
the normal on-ramp (it feeds the same index plus the knowledge graph); use
`r4 rag` when you want to upload or query documents without the full
understand/verify pipeline.

```bash
# Upload
r4 rag upload paper.pdf --project my-research
r4 rag bulk-upload ./research-papers/ --project my-research --recursive

# Query
r4 rag query "What causes MPI_Init failures?" --project my-research
r4 rag stats --project my-research

# Documents
r4 rag docs list --project my-research
r4 rag docs delete <document-id> --project my-research

# Component catalogs (chunking and embedding are site settings;
# `r4 rag ingest` was retired 2026-10-18)
r4 rag retriever components embedders
```

Vectors are stored by UMRS in Postgres + pgvector; hybrid retrieval (vector +
BM25) with reranking is the default path. Image embedding exists for
tomography/vision workflows; video is modality-detected only (D-14).

---

## MCP tools and Hatch packages

MCP servers are how your group's tools (instrument APIs, HPC job templates,
in-house codes) become callable by agents. UMRS keeps the registry; Hatch
packages (`hatch.json` + `server.py`) are the packaging format; `r4 mcp
hatch` is the runtime that discovers, pulls, starts and audits them.

```bash
# Registry
r4 mcp list
r4 mcp list --tools
r4 mcp info <server-id>
r4 mcp health <server-id>
r4 mcp register my-server --url http://host:port --description "..."

# Hatch packages
r4 mcp hatch search "chemistry"
r4 mcp hatch install <package> --from registry
r4 mcp hatch connect <package>
r4 mcp hatch list
r4 mcp hatch create my-tool-package
r4 mcp hatch share my-tool-package --project my-research
r4 mcp hatch registry list
```

What runs where today: packages you install run on your machine under the R4
hatch runtime bridge (spawn, respawn, per-call timeout, one audit line per
invocation; hardening contract ADR-023). Platform servers registered in UMRS
are reached through UMRS (`/umrs/mcp/*`), and outbound traffic from
egress-capable classes goes through EgressGuard's deny-by-default allowlist.
The default registry ships six packages (`platform-tools`, `rag-search`,
`knowledge-tools`, `rikyu-hpc`, `rikyu-docs`, `fugaku`). Placement and trust
classes (local-safe / front-end / trusted-host-only) are not yet fields of
the registry (D-21, D-24), and a catalog does not yet name its MCP servers
(D-23).

---

## Workflows and jobs

You author a workflow (a multi-step DAG, schema 2.0.0) in R4; UMRS registers
and executes it as a durable job on the cluster.

```bash
# Register and run
r4 workflow upload ./my-workflow/ --name my-workflow
r4 workflow list
r4 workflow show my-workflow --graph
r4 workflow trigger my-workflow --param input=data.csv --wait
r4 workflow run ./my-workflow/ --wait          # upload + trigger in one step
r4 workflow lint ./draft.json                  # client-side lint

# Executions
r4 workflow status <execution-id>
r4 workflow watch <execution-id>
r4 workflow cancel <execution-id>
r4 workflow approve <execution-id> <step-name> # manual-gate steps
r4 workflow reject <execution-id> <step-name>

# Durable jobs
r4 job list
r4 job list --status running
r4 job describe <job-id>
r4 job cancel <job-id>
r4 job retry <job-id>
```

Jobs are tracked durably in Postgres with crash recovery:
- **Job** = user intent (immutable spec)
- **Attempt** = one execution try (several on retries)
- **Commit pointer** = which attempt's outputs are current

Importers for Snakemake / Nextflow / CWL pipelines are a design target, not
shipped (D-08). Persisted run templates: `r4 workflow --help` (the `def`
group: save once, replay exactly).

---

## Vision endpoints

Vision models (SAM3, MAE, HMAE) are served by a BentoML service inside the
same mini-RiVault sandbox class; you provision them with `r4 endpoint up` and
submit tasks with `r4 vision`.

```bash
# Provision
r4 endpoint up --model sam3 --backend bentoml --service-kind bentoml_vision --wait

# Submit and fetch
r4 vision submit image.png --model sam3 --adapter sam3
r4 vision status <task-id> --endpoint-id <job-id>
r4 vision get <task-id> --endpoint-id <job-id> --output result.pkl
r4 vision cancel <task-id> --endpoint-id <job-id>
r4 vision ui --adapter sam3 --endpoint-id <job-id>
```

Details: [Vision endpoints](../r4/vision-endpoints.md).

---

## Multi-source routing

A *source* is an endpoint provider: your platform deployment (`platform`) or
a public API (`public`, OpenAI-compatible, Anthropic or Bedrock).

```bash
r4 source list
r4 source add cloud --url https://api.openai.com/v1 --kind public --api-key-env OPENAI_API_KEY
r4 source add claude --url https://api.anthropic.com --kind public --provider anthropic --api-key-env ANTHROPIC_API_KEY
r4 source use cloud                     # make it the active source
r4 chat "Hello" --source cloud          # or per command
r4 source push cloud                    # seal the credential server-side for remote sessions
```

Routing research data to a public provider sends it off-site for inference.
The design's rule is that research data never leaves the site; a per-project
data-class policy that enforces it is still to be written (D-37, D-51) —
until then, use public sources only with data you may share.

---

## Agent workflows

```bash
r4 agent chat                                   # interactive agent
r4 agent create "Build a RAG system for my research papers"
r4 agent run <workflow-id>
r4 agent sessions
r4 agent tools
r4 agent audit                                  # this user's egress audit trail
r4 team --help                                  # multi-agent collaboration
r4 search "<question>"                          # agentic multi-hop search
r4 run --help                                   # track agent executions in A2L lineage
```

Outbound data requests from agents follow `--egress-policy` (default
`ask_always`). Multi-agent teams run through one seam, the Team Engine SPI
(ADR-072, D-29; corrected 2026-10-18 from "several team mechanisms coexist").

---

## Remote sessions

A remote agent session is the same companion running in a CPU-only,
rootless-Podman sandbox on the front-end, attachable from the console, never
holding your token or a provider key.

```bash
r4 session list
r4 session start --venue frontend --source <pushed-source>
r4 session show <session-id>
r4 session stop <session-id>
r4 session fork <session-id> --from-turn <turn-id>
r4 session conclude <session-id>
r4 session export <session-id>
```

Venues today: `frontend` and `cluster:<name>`; CPU-only and trusted-host
venues, and pause/resume, are design targets (D-09, D-48, D-49). Chapter:
[Frontend-venue sessions](../user-manual/30-frontend-venue.md).

---

## Configuration reference

### Environment variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `R4_GATEWAY_URL` | Front-end HTTPS URL (the edge) | `http://localhost:8080` |
| `R4_API_KEY` | Token for authentication | — |
| `R4_PROJECT_ID` | Default project | — |
| `R4_MODEL` | Default model name | — |
| `R4_TIMEOUT` | Request timeout (seconds) | `300` |
| `R4_TRANSPORT_MODE` | `auto` \| `gateway` \| `direct` (ZeroMQ for files, jobs, models, workflows) | `auto` |
| `R4_PROFILE` | Which saved profile to use | `default` |
| `UMRS_SERVER_HOST`, `UMRS_CONTROL_PORT` | ZeroMQ control channel (normally discovered at login) | `127.0.0.1`, `5555` |

Today the client reaches UMRS over HTTPS through the edge (chat, MCP,
knowledge, sessions, projects) and over direct-TCP ZeroMQ with CURVE session
keys (files, jobs, models, workflows). The design's target is one ZeroMQ
channel terminated by UMRS (D-01, D-02).

### Profiles

```bash
r4 auth login --profile hpc --gateway-url https://<hpc-front-end> --oidc
R4_PROFILE=hpc r4 chat "Hello"
r4 config context list
```

---

## Troubleshooting

**"Connection refused" on chat**
- Check the front-end URL: `r4 config show`
- Check it answers: `curl https://<front-end>/health`
- Run the diagnostics: `r4 health full` or `r4 doctor`
- Check VPN/network connectivity

**"401 Unauthorized"**
- Token may be expired: `r4 auth status`, then `r4 auth refresh` or `r4 auth login …`

**"No endpoints available"**
- Nothing provisioned: `r4 endpoint up --model <name> --wait`
- Endpoint may be stopped: `r4 endpoint list --all`

**"Model not found"**
- Check available models: `r4 model list`
- Model may need staging first: `r4 model stage <hf-repo>` (or ask an admin for `system` scope)

**Endpoint stuck in "provisioning"**
- The GPU queue may be busy: wait, or check `r4 endpoint logs <instance-id>`
- Admins can inspect every endpoint with `umrs-server admin endpoint list`

**Ingest finished but the agent does not know the material**
- `r4 knowledge ls`: is the item listed, and is `INDEXED` `yes`? `pending` means the embed job has not finished; ask again once it has. (2026-10-24: the old advice, `r4 project readiness` and `r4 project init`, applies only to a `--local` import.)

For detailed troubleshooting, see the [Troubleshooting Guide](troubleshooting.md).

---

## Related Documents

| Document | Purpose |
|----------|---------|
| [Introduction](introduction.md) | What the platform is, for newcomers |
| [The design](../R4-UMRS-DESIGN.md) · [Gap analysis](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md) · [What runs today](../R4-UMRS-PLATFORM-OVERVIEW.md) | Target, distance, implementation |
| [User Manual](../user-manual/README.md) | Chapter-by-chapter guide with appendices |
| [Knowledge management](../user-manual/27-knowledge-management.md) · [Autonomous research](../user-manual/26-autonomous-research.md) | The catalog and the loop |
| [CLI Reference](../user-manual/13-cli-reference.md) · [SDK Reference](../user-manual/14-sdk-reference.md) | Command tree and Python API |
| [Console README](../../packages/r4-tui-v3/README.md) · [Console reference](../r4/TUI_REFERENCE.md) | The console |
| [Deployment overview](../user-manual/16-deployment.md) · [Stand-alone stack](../../deploy/standalone/README.md) | Deployment and administration |
| [Troubleshooting](troubleshooting.md) | Common problems and solutions |
