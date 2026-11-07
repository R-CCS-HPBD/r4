# R4 — the researcher-facing client

`r4` is the client half of the R4 + UMRS platform: the console, the CLI, the
Python SDK and the companion agent that scientists work in. By design it
talks only to a UMRS server on the cluster front-end, never to a scheduler,
a bucket or a database; today the companion still carries direct
`sbatch`/`squeue` tools and a per-user S3 config (gap D-37). Design: [../docs/R4-UMRS-DESIGN.md](../docs/R4-UMRS-DESIGN.md)
(§"R4: the researcher-facing agent environment"). Where the client still
differs from the design, the row is in
[../docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md).

> New here? [Quick Start](../docs/r4/quickstart.md) · [R4 docs index](../docs/r4/index.md) · [Console README](../packages/r4-tui-v3/README.md) · [Console reference](../docs/r4/TUI_REFERENCE.md) · [CLI reference](../docs/user-manual/13-cli-reference.md) · [SDK reference](../docs/r4/reference/sdk.md)

## What the client provides (design capability → what ships)

| Capability | Today | Gap |
|---|---|---|
| **Console (TUI)** as the single entry point | `r4 console` — compiled OpenTUI v3 binary (`r4 tui install`), routes work / tasks / knowledge / catalog / sessions / loop / research / code / infrastructure / settings; also `r4` / `r4 companion` (Python REPL), VS Code extension, Jupyter magic | D-25 (several surfaces) |
| **Agent harness** | `r4/src/r4/companion/` — tool calling, planning, sub-agents, permission ladder (ask / auto-safe / trust with server-side allowlist), environment probe, guides, sensors, steering; ACP adapter for editors | — |
| **Skills** | Agent Skills `SKILL.md` directories: 27 builtin groups, `~/.config/r4/companion/skills/`, project `.r4/skills/`; `r4 skill list / search / install`; UMRS registry | D-27 (no tests, not in bundle) |
| **Context control** | model-derived token budget, compaction, catalog retrieval with graph selection, acquisition write gate (`/acquisition open|pinned|disabled`) | — |
| **Multi-agent team-up** | Team Engine SPI + native / LangGraph engines (`r4 team …`); older `teamup` and research-team roles also present | D-29 |
| **Session control, local and remote** | `r4 session list / export / delete / conclude / pull`, `r4 session start / venue / stop` (remote frontend venue, ADR-046), console picker and remote watcher | D-09, D-30 |
| **Project and workflow management** | `r4 project create / switch / import / init / verify / readiness / plan / status / loop / export`; `r4 workflow list / upload / trigger / status / cancel`; `r4 job …`; `r4 recipe`, `r4 schedule` | — |
| **Autonomous research loop** | client tools over the server-hosted loop (`research_loop_*`, `r4 run`), console `/loop` and `/research`; report generator | D-32 … D-36, D-45 |
| **Catalog explicit ingestion / export** | `r4 project import` (→ UMRS S3 + durable ingest) then `r4 project init`; `r4 knowledge …` (query, explore, graphs, export / import / restore) | D-39, D-40 |
| **MCP / Hatch** | `r4 mcp list / register / unregister / info / health`, `r4 mcp hatch search / install / connect / create / share` | D-21 … D-24 |
| **Models and endpoints** | `r4 model list / info / stage / register`, `r4 endpoint up / down / list / status / logs`, `r4 up` / `r4 down`, `r4 vision …` | D-10 |

## Installation

r4, r4-contracts and umrs are not published on PyPI (`pip install r4` would
fetch an unrelated package of that name, if one exists). Install with the
installer script, or from a clone:

```bash
# installer: a virtual environment in ~/.r4/venv plus an `r4` on PATH
curl -fsSL https://raw.githubusercontent.com/R-CCS-HPBD/r4/main/scripts/install-r4.sh | bash

# from a clone (run in its r4/ directory)
git clone --recurse-submodules https://github.com/R-CCS-HPBD/r4.git
cd r4/r4
pip install -e ../r4-contracts -e ../packages/r4-teamengine-spi -e ../science -e . -e "../umrs/src[client]"
r4 tui install                       # fetch the console binary (sha256-verified)
```

At the repository root, `make install-client` installs the client packages
(editable, through `uv sync` when uv is present) and the console's
dependencies.

Optional extras, added to the editable install (e.g. `-e ".[mcp-use]"`):
`jupyter`, `umrs` (ZeroMQ), `mcp-use`, `langchain`, `llamaindex`,
`all-integrations`.

## Quick start

```bash
r4 auth login --api-key <JWT>        # server URL, project and profile come from the token; --oidc for OIDC
r4 health full
r4 console                           # the console
r4 project create mylab && r4 project switch mylab
r4 project import ./papers ./protocols && r4 project init
r4 chat "What is our standard sample-prep procedure?"
r4 endpoint up -m Qwen/Qwen2.5-7B-Instruct --tier user --wait
r4 session start --venue frontend    # remote session on the front-end (ADR-046)
```

### Python SDK

```python
from r4 import Client

client = Client()                                   # from env / saved profile
print(client.chat("What is quantum entanglement?").content)

for chunk in client.chat("Explain the method", stream=True):
    print(chunk, end="", flush=True)

resp = client.rag.query("What does the simulation show?", project_id="mylab")
client.rag.upload("paper.pdf", project_id="mylab")
client.data.upload("ct_scan.tif", project_id="example-project-c")  # ZeroMQ when available, HTTP otherwise
jobs = client.job.list(status="running")
```

Sub-clients: `client.rag`, `client.data`, `client.mcp`, `client.job`,
`client.models`, `client.workflows`, `client.vision`. Errors:
`R4Error`, `AuthenticationError`, `RateLimitError`, `APIError`.

## How the client reaches UMRS today

```
r4 ──HTTPS + JWT──▶ edge (/gw/*: NGINX + auth sidecar) ──HTTP──▶ UMRS families (/umrs/*)
r4 ──ZeroMQ, direct TCP, CURVE session keys──────────────────▶ UMRS control + data sockets
```

- HTTPS is used for chat, MCP, knowledge, sessions, catalog, projects, venue.
- ZeroMQ is used for files, jobs, models, workflows and embedding export when
  `R4_TRANSPORT_MODE` (`auto` | `gateway` | `direct`) and the deploy mode
  allow (`r4/src/r4/config.py`). A JWT login bootstraps the session-CURVE
  keypair automatically; opaque API keys need static CURVE keys for ZeroMQ.
- The design is this dual transport, both lanes terminated by UMRS
  (ADR-059; 2026-10-18: said "a single ZeroMQ channel … (gap D-01/D-02)",
  decided 2026-09-22). Do not add new client paths to S3, Postgres or a scheduler.

Configuration: `R4_GATEWAY_URL`, `R4_API_KEY`, `R4_PROJECT_ID`, `R4_MODEL`,
`R4_TIMEOUT`, `R4_TRANSPORT_MODE`, `UMRS_SERVER_HOST`, `UMRS_CONTROL_PORT`;
or a saved profile via `r4 auth login` (`r4 config show`, `r4 config context`).
On HPC (`SLURM_JOB_ID` set) the SDK records the Slurm context for attribution.

## Agent tool playbook

Agents that cannot read the source use `r4 guide` (full playbook),
`r4 guide tools`, or the always-on `platform_guide` tool; editor rule files
are generated with `r4 guide install --target <editor>`.

## Package layout

```
r4/src/r4/
  client.py, _http.py, _transport_router.py, umrs_transport.py, curve_session.py   # transport
  config.py, auth.py, oidc*.py, source*.py, providers/                                # profiles, identity, model sources
  _cli/                       # ~65 command groups (auth, project, knowledge, mcp, endpoint, workflow, session, venue, team, …)
  companion/                  # agent harness, tools, permissions, context, skills (builtin/), sessions, harness/
  knowledge*.py, knowledge_bundle.py, knowledge_explorer/                           # catalog client side
  research_loop/, research/, agent/, teamup/, teams/                               # loop and team clients (being unified, D-29/D-32)
  venue.py, venue_worker.py, venue_rail.py                                          # remote session worker (ADR-046)
  workflows.py, workflow_lint.py, workflow_graph.py, jobs.py, models.py, vision.py, mcp.py, rag.py, data.py
  tui/                        # Python-side TUI helpers (bridge, dashboard, mcp config)
  jupyter/, integrations/     # magic commands; Hatchling and framework integrations
```

The console lives in `../packages/r4-tui-v3/`; shared DTOs in `../r4-contracts/`.

## Development

```bash
pip install -e ../r4-contracts -e ../packages/r4-teamengine-spi -e ../science -e ".[dev]"
pytest                                   # 3,3xx tests (2026-09-21 baseline)
bash ../scripts/ci-lint-local.sh         # the gate: lint, complexity ≤ 9, boundaries, tests
```

Rules: [../CONSTITUTION.md](../CONSTITUTION.md) (never import `umrs`; never a second job store or storage credential in the client), [../.github/instructions/companion.instructions.md](../.github/instructions/companion.instructions.md) (companion conventions), [../.github/instructions/r4-agent-tools.instructions.md](../.github/instructions/r4-agent-tools.instructions.md).

Command renames of 2026-04-27 (Phase 2l) are hard renames without aliases:
`r4 init` → `r4 auth login`, `r4 ask …` → `r4 companion | agent | team | search`,
`r4 pipeline` → `r4 rag ingest`, `r4 profile` → `r4 rag retriever`, `r4 sessions` → `r4 session`.
The Ink TUI and `r4 research` CLI were removed 2026-09-11 (ADR-014).
