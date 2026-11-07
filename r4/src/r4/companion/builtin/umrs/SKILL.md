---
name: umrs
description: "UMRS platform baseline: endpoints, models, durable jobs, research loops, quotas/circles/grants/gauge/usage, traces/provenance, RAG semantic query, sessions, teamup agents, plus slash-suggestion grammar (when to offer which slash by context)"
allowed-tools: "platform_guide platform_health endpoint_list endpoint_status job_list job_status list_models source_list source_add source_use source_remove pool_up pool_down endpoint_upgrade"
---

You operate the R4 Platform through UMRS (always-on baseline).
{{contract:r4_contracts.agent_tool_playbook:UMRS_OPS_AGENT_GUIDANCE}}
Multi-source management: source_list / source_add / source_use / source_remove
manage registered UMRS sources (gateways). pool_up / pool_down provision or
stop the endpoints listed in a YAML roster file (file=...; pool_up takes
dry_run=true to preview, pool_down takes force=true to skip graceful
shutdown). endpoint_upgrade(endpoint_id, model_path[, wait]) rolls a running
endpoint to a new, already-registered model path: it provisions a
replacement, flips the registry record once the replacement is ready, and
retires the old instance. Pass wait=true to block until the flip finishes.
It cannot change the container image.

## Slash catalog (mechanically current)

Live platform facts (sites, partitions, quota, workflows, jobs, endpoints)
arrive each turn in the project-state block, as data from UMRS (ADR-105 D-8); the
console's RPC verbs (`BRIDGE_RPC_NAMES`) are listed in
`references/bridge-verbs.md` — they are not tools you can call.

<!-- INVENTORY:START -->
The **TUI slash catalog** (live slash completion — what users can type):

```
/acquisition /admin /bg /btw /catalog /changes /checkpoint /circles /clear /code /codeview /connect /decision-model /endpoint /evidence /explain /files /fork /governance /grants /health /job /keybindings /login /loops /mcp /mode /models /palette /project /provenance /quit /quota /rag /rewind /sandbox /search-server /sessions /skills /source /statusline /sweep /tab /teamup /term /theme /tour /traces /usage /venue /web /worktree
```
<!-- INVENTORY:END -->

Each name maps to a family of verbs (see `project` with `create/import/init/
verify/readiness/plan/run/cancel`, `endpoint` with `list/up/down/status`, `teamup`
with `list/status/run/pause/resume/cancel/logs/report/hardware/engines/crews/create`,
`workflows` with `list/run/status/cancel/detail/results/approve-gate`, `loops` with
`list/status/stop/pause/resume`). Namespaces route as `/family sub args`.

## Slash-suggestion grammar — when to offer which command

Suggest the user a slash (natural-language, at the END of your final turn
after reporting the result) ONLY at moments where the suggestion is a
_different surface_ than the one you just used, and that surface gives the
user read-only control or richer visibility. Never suggest a sibling
command of the same thing you just did.

| Context the turn just completed | Suggest |
|---|---|
| listed loop ids → loops.status loop state inspected | `/workflow <name>` (trigger or detail) or `/sessions` (reuse the session housing) |
| provisioned an endpoint / jobs pending / provision slow | `/quota` (CPU/disk gauge) or `/endpoints` (own coverage) |
| session list inspection | `/sessions resume <id>` or `/job <id>` (job attempt timeline) |
| endpoint failed / job failed hint | `/job <id>` → attempt timeline + `/traces <job-id>` lineage |
| corpus/knowledge asks | `/rag <question>` (semantic query) or `/catalog` (own pane) |
| models browsing | `/models` |
| circles/grants ask | `/grants` (received) and `/circles` (owned+member) |
| unmatched name for something | `/health` first (gateway, umrs, hpc shows connected-or-not), never invent |
| reached quota / 429 named | `/quota` (verified-user gauge) |
| working memory browse / compacation mention | `/usage` (token rows — help decide compact-or-not) |
| slash-help ask | `/tour` (guided 10-step real-chain) |
| approval gate the turn just suggested | `/job <id>` before approval firing for full context |

Common guardrails: never invent command names (names in the table only);
never suggest twice in the same turn; never suggest a slash if the user
already typed that same family in this conversation.

## Preparing a workflow for upload (UMRS refuses anything else)

A workflow bundle is a directory or .zip with `workflow.json` and the step
code. UMRS refuses an upload unless the workflow has:
1. `description`: what the workflow does, what it needs (inputs, parameters)
   and what it produces. Users and agents read it first.
2. The flow graph: **every** step has a `depends_on` list, naming the steps it
   waits for, or `[]` for a step that starts the run. Don't rely on step
   order: if a step uses the previous step's output, write
   `"depends_on": ["<that step>"]`.

Also declare the run parameters in `parameter_schema` (properties with type
and description, plus `required`). That way users and agents can see what to
trigger the workflow with. Leave a step's `model` out so the platform routes
it; nothing fills in a `{{model}}` placeholder. Before you upload, run
`r4 workflow lint <bundle-dir>`: it lists every problem with its line, and a
refusal lists them all too. Templates and fields:
platform_guide("authoring"), and the workflow_ops skill's bundle reference.

## Experiences (experience.json)

{{contract:r4_contracts.agent_tool_playbook:EXPERIENCE_AGENT_GUIDANCE}}

Full annotated schema + copyable examples (text embedding + custom workflow):
See [references/experience-schema.md](references/experience-schema.md)

For workflow / RAG / research-loop detail call platform_guide
(topics: spines, umrs, workflow, authoring, catalog, loops, sessions,
knowledge, experiences).
