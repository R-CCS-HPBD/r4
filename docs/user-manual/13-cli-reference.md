# 13. CLI Reference

Complete command tree for the `r4` CLI. For exact flags on any command, use `r4 <command> --help`. (Project-loop, knowledge-transport and skill-review entries refreshed 2026-09-24; `r4 project storage` added 2026-10-01; `r4 bucket`, `r4 files list/--bucket/--recursive`, `r4 workflow results` added 2026-10-02; access verbs — `endpoint/graph/catalog/workflow/bucket access`, `rag corpus-access` — and the `r4 circle` / `r4 grant` sections added 2026-10-03; groups renamed to project-scoped circles 2026-10-05; `r4 model scale` added 2026-10-10; `r4 project data-class` added 2026-10-16; `r4 rag ingest` retired 2026-10-18; `r4 --sandbox` and `r4 config sandbox` added 2026-10-30; `r4 workflow export` added 2026-11-07; workflow versions — `NAME@VERSION` / `--version`, `r4 workflow versions`, `adopt`, `list --all-versions`, `triggers --pin/--unpin` — added 2026-11-08.)

## Command Ownership

| CLI | Audience | Boundary |
|-----|----------|----------|
| `r4` | End users | Gateway-mediated user workflows |
| `umrs` | Operators | Direct UMRS diagnostics |
| `umrs-server admin` | Site admins | Offline administration on trusted hosts |

## Interactive TUI (no subcommand)

```bash
r4                              # Launch JS companion TUI
r4 companion                    # Same
r4 -p "hello"                   # One-shot prompt
r4 --mode plan                  # Launch in planning mode
r4 --yolo                       # Auto-approve tool calls
r4 --fs-root .                  # Enable filesystem tools
r4 --secure                     # Content scan mode
r4 --sandbox on                 # Run the agent's shell, Python and MCP servers in the local tool sandbox
r4 --worktree feat              # Work in an isolated git worktree (.r4/worktrees/feat, branch r4/feat)
```

`r4 --worktree NAME` (2026-11-03) starts the
console in an isolated git worktree of the current repository, modeled on
Claude Code's. If NAME is new, it is created from your current state, including
uncommitted and untracked files. Your checkout, its branch and its index
are not changed. If NAME exists, the console enters it. In the console,
`/worktree <name>` does the same, `/worktree exit` leaves it and
`/worktree list` shows them all; the status bar shows `wt:<name>`. See
[Worktrees](#worktrees-r4-worktree) below.

`r4 --sandbox on|off` (2026-10-30; see
[Appendix D](appendix-d-security.md#local-tool-sandbox-on-your-workstation)) sets the local tool sandbox
for the session this launch opens, in the console, `r4 -p` and the REPL.
The sandbox is off by default and every local session says so in one line
(on stderr for `r4 -p` and the REPL; those two have no `/sandbox` command,
so turn it on there with `r4 --sandbox on`, `R4_SANDBOX=on` or `r4 config
sandbox on`). With it on, the processes the agent
starts can write only to the workspace and a per-session temp directory,
cannot read `~/.config/r4`, `~/.ssh` or other credential stores, lose API
keys and tokens from their environment, and reach the network only through
r4's loopback proxy, which follows the session's web access (`--web`). A
resumed session that was on stays on; `--sandbox off` does not loosen it
(use `/sandbox off` in the console). When no backend works (no bubblewrap,
user namespaces blocked), `on` refuses commands rather than run them
unconfined, and the notice names the fix. Full description:
[TUI reference §7](../r4/TUI_REFERENCE.md#7-sessions-local-and-remote) and
[Appendix D](appendix-d-security.md#local-tool-sandbox-on-your-workstation).

## Complete Command Tree

### Authentication (`r4 auth`)

```
r4 auth login [--gateway-url URL]
              [--api-key-stdin | --api-key-env VAR | --api-key TOKEN | --oidc]
              [--project ID] [--model NAME] [--umrs-username NAME]
```

Key flags (2026-10-15). They have the same meaning as for `r4 source add`:

- `--api-key-stdin` reads the token from a pipe, or from a hidden prompt on a
  terminal. It is never echoed.
- `--api-key-env VAR` reads the token from an environment variable.
- `--api-key TOKEN` still works, but it warns that the token stays in shell
  history and process lists.

When the token is a JWT, the following claims are auto-extracted if the
corresponding flag is not set: `gateway_url`, `project_id`, `profile`.
This allows a single-flag login:
`printf '%s' "$JWT" | r4 auth login --api-key-stdin`.
r4 auth logout [--profile NAME] [--revoke-server-session]   # local only; see ch. 3
r4 auth status [--profile NAME]
r4 auth refresh [--profile NAME]

### Chat (`r4 chat`)

```
r4 chat [MESSAGE] [--model NAME] [--system PROMPT] [--stream]
        [--temperature FLOAT] [--max-tokens INT] [--interactive | -i]
        [--companion] [--resume SESSION_ID]
        [--skill NAME] [--fs-root PATH] [--task HINT] [--agent HINT]
        [--source NAME] [--secure] [--no-discover]
        [--knowledge BUNDLE]                       # Load .kb bundle/project for RAG
```

### Configuration (`r4 config`)

```
r4 config show
r4 config init [--profile NAME] [--gateway-url URL] ...
r4 config context [--project ID] [--umrs-username NAME]
                   [--clear-project] [--clear-user]
r4 config context list [--json]
r4 config sandbox [on|off|show]   # Local tool sandbox default for this profile (2026-10-30)
```

`r4 config sandbox on|off` writes the profile default `sandbox_default` in
`config.json` (never in a repository's `.r4/` files, so a cloned project
cannot switch it off) and moves local MCP servers with it. `on` first
checks that a backend works here (the same probe and self-test as
`/sandbox on`); when it does not, it writes nothing, says why on stderr and
exits 1 (2026-10-30). It prints the
stdio servers it restarted, and exits 1 with a line on stderr when the
running MCP daemon could not follow. `off` takes MCP servers out of the
sandbox only once no stored profile default is on. `show` (the default)
prints the default, whether a backend (`bwrap` or `seatbelt`) works here,
your `sandbox_policy` additions (`extra_write_paths`,
`extra_deny_read_paths`, `env_keep`) and what the sandbox confines. Order for a session: `r4 --sandbox`, the session's own earlier
choice, `R4_SANDBOX`, this default, else off; a launch `off` never loosens
a session that is already on.

### Login (`r4 login`)

Unified login command — combines authentication, profile setup, and optional
onboarding in a single step. Preferred over `r4 auth login` for first-time setup.

```
r4 login [--gateway-url URL]
         [--api-key-stdin | --api-key-env VAR | --api-key TOKEN | --oidc]
         [--oidc-issuer URL] [--oidc-client-id ID]
         [--provider {gakunin|orcid|github}] [--timeout SECS]
         [--profile NAME] [--project ID] [--model NAME]
         [--umrs-username NAME] [--full]
```

Flags:
- `--full` — Extended setup: model prompt, `R4.md` project scaffolding, and health checks
- `--provider` — OIDC provider preset (`gakunin`, `orcid`, `github`); fills issuer/scope
- `--umrs-username` — Set the default UMRS username for job/data operations

When the token (from any key flag) is a JWT, `gateway_url`, `project_id`, and
`profile` claims are auto-extracted if the corresponding flag is not provided.

### Health (`r4 health` / `r4 doctor`)

```
r4 health                         # Default: quick check
r4 health quick [--direct-umrs] [--json] [--all-sources]
r4 health full [--json] [--all-sources]
r4 health account [--json] [--all-sources]
r4 doctor [--json] [--verbose] [--all-sources] [--sovereignty]
```

`r4 doctor` is a standalone diagnostics command (gateway, UMRS, local env,
toolkit). `--sovereignty` emits the data-sovereignty report — offline
inference, gateway auth, egress guard, audit trail, and credential hygiene
with a one-word verdict. Since 2026-10-30 `r4 doctor` also runs an
informational local tool sandbox check, nested in the local checks: `r4
doctor --json` reports it as `local.checks.sandbox` (`backend`,
`available`, `default`, and `message`: `sandbox: bwrap available`, or
`seatbelt`, or `sandbox: unavailable (<reason>)`). The plain text output
prints the same line with the profile default (`sandbox: bwrap available ·
default: off`; the text line since 2026-10-30); the check never fails it.
`r4 config sandbox show` prints the same backend status as text.

Subcommands:
- `quick` — Fast Gateway readiness probe (default when no subcommand given)
- `full` — Comprehensive diagnostics across Gateway, UMRS, endpoints, and storage
- `account` — Validate local credentials against the Gateway
- `--direct-umrs` — Also probe UMRS health directly, bypassing the Gateway
- `--all-sources` — Run checks against all registered sources, not just the active one

### Projects (`r4 project`)

```
r4 project list
r4 project create ID [--name NAME] [--gateway URL] [--model NAME]
                      [--security-level LEVEL]
r4 project switch ID
r4 project current
r4 project delete ID

# Research project commands (see 26. Autonomous Research)
r4 project import [ID] PATH...      # Seed papers/code/notebooks/data (ID defaults to active project)
r4 project ingest ID PATH...        # [DEPRECATED] alias of import
r4 project init [ID]                # Extract knowledge + build graph
r4 project understand [ID]          # [DEPRECATED] alias of init
r4 project verify ID QUESTION       # Verify knowledge with evidence
r4 project verify_job ID QUESTION   # Verification as a durable job (k2)
r4 project readiness ID [--approve] # Gap report; --approve gates research runs
r4 project plan ID GOAL [--emit-workflow FILE]
r4 project emit_workflow ID         # Emit a workflow draft from a plan
r4 project status ID
r4 project ask ID QUESTION          # Ask with citations
r4 project findings ID
r4 project transition ID FINDING_ID STATUS
r4 project approve ID
r4 project lineage ID
r4 project loop {start|status|list|iterations|watch|stop|pause|resume}
                  [--workflow NAME] [--approve] [--max-iterations N]
                  [--session SESSION] [--runtime {umrs|local|hybrid}]
                  [--requires JSON] [--tracks N]
                                          # start: --workflow names the registered
                                          # workflow each experiment runs; omit when
                                          # exactly one is registered (UMRS uses it);
                                          # --tracks 1–4 parallel hypothesis tracks
r4 project loop approvals [LOOP_ID]          # Pending ask-mode gates (durable inbox)
r4 project loop decide APPROVAL_ID --decision {once|always|no}
r4 project ask_policy [ID] [--stage {bootstrapped|learning|competent|autonomous}
                        | --mode {auto|ask|profile} [--triggers A,B] | --clear]
                                    # When loops pause for you (ch. 26)
r4 project sessions                 # Durable research sessions
r4 project storage {show|set|check|clear} [ID]
                     [--env-file FILE|-] [--s3-url-file F] [--postgres-uri-file F]
                     [--s3-url URL] [--postgres-uri URI] [--unset-s3] [--unset-postgres]
                     [--no-check]
                                    # Keep the project's objects / Postgres outside the
                                    # front-end: locators are sent once, sealed
                                    # server-side, never shown back; set probes first
r4 project data-class get [ID] [--json]
                                    # The project's data class and what it allows
r4 project data-class set [ID] {sensitive|non_sensitive}
                     [--confirm 'ID CLASS'] [--json]
                                    # Owner/admin. Widening (→ non_sensitive) needs the
                                    # typed confirmation naming project and class;
                                    # narrowing needs none (ch. 5, "Data class")
r4 project export [ID] [--strict] [--with-raw [--acknowledge-sensitive]]
                                    # Signed server export job → .kb.tar.gz (ch. 27)
```

### Models (`r4 model`)

```
r4 model list [--scope {system|project|user}] [--type {llm|embedding|vision}]
              [--project ID] [--status {staging|available|deprecated}] [--limit N]
r4 model info NAME
r4 model stage HF_REPO [--name NAME] [--scope SCOPE] [--project ID]
                        [--type TYPE] [--token TOKEN] [--revision REV]
                        [--description TEXT] [--tags TAGS]
r4 model register NAME --path PATH [--scope SCOPE] [--project ID]
                        [--type TYPE] [--backend BACKEND] [--service-kind KIND]
r4 model publish NAME --project ID
r4 model detect [NAME]              # Detect capabilities from model files
r4 model use NAME                   # Set the active model for chat
r4 model route NAME --source SRC    # Bind model to a named source for cross-source routing
r4 model delete NAME [--force]
r4 model scale [MODEL] [--source S] [--refresh] [--json]
                                    # Detected size class (small/medium/large) and retrieval profile
r4 model scale set MODEL small|medium|large [--source S] [--params B] [--active B] [--note T]
r4 model scale unset MODEL [--source S]
r4 model scale list [--json]        # Pins and cached detections
```

#### Model scale

*Added 2026-10-10.* R4 sizes retrieval for the model that
reads the passages: how many passages, how many tokens, and whether to expand
the query. `r4 model scale` shows the class it detected for MODEL (default:
the active model) on its source (`--source`, or `MODEL@SOURCE`; default: the
source the model routes to). The probe depends on the source:

- ollama: `/api/show`, which gives the parameter count and, for MoE models,
  the expert counts;
- platform: the model registry;
- OpenAI-compatible endpoints such as rikyu, vLLM or LiteLLM: `/v1/models`;
- bedrock and anthropic: large.

When the probe cannot tell, R4 falls back to the name rule. The output names
the `basis` and the `detail` of each step. A model of `unknown` scale reads
with the `medium` profile, and the console tells you once per session how to
set it. `set` pins a class in `<config>/model_scales.json` under
`<source>/<model>`, or `*/<model>` without a source. A pin wins over every
probe and applies to a running session at once. `--params` and `--active`
record the total and active size in billions. `R4_MODEL_SCALE=<class>` pins
every model for one process. Probe results are cached for 7 days;
`--refresh` probes again.


### Endpoints (`r4 endpoint` / `r4 up` / `r4 down`)

`r4 up` is a quick shortcut (single endpoint = `r4 endpoint up ... --wait`;
or a guided pool with `--pool-file`). The full flag surface lives on
`r4 endpoint up`.

```
r4 up [--model NAME] [--source NAME] [--max-model-len N]
      [--ttl-sec N] [--timeout SECS] [--json]
      [--pool-file FILE] [--dry-run] [--continue-on-error] [--sequential]
      [--public URL_OR_PROVIDER] [--api-key KEY]
      [--aws-region REGION] [--aws-profile PROFILE]
      [--knowledge BUNDLE] [--local]

r4 down [INSTANCE_ID] [--all] [--pool-file FILE]
        [--continue-on-error] [--force] [--json]
r4 down --public

r4 endpoint up [--model NAME] [--tier {system|project|user}] [--wait]
               [--mount-mcp SRC:DST] [--mount-data SRC:DST] [--mount-rag SRC:DST]
               [--egress-domains DOMAINS] [--max-model-len N]
               [--gpu-count N] [--node-count N]
               [--partition NAME] [--nodelist NODES]
               [--parallel-strategy {tp_pp|tep}]
               [--ttl-sec N] [--container-image IMAGE]
               [--vision-adapter NAME] [--vision-task TASK] [--env-var K=V]
               [--backend {vllm|bentoml}] [--service-kind KIND]
               [--launch-profile NAME] [--co-locate-vllm]
               [--source NAME] [--access SCOPE ...]

r4 endpoint list [--source NAME] [--all-sources] [--tier TIER] [--all] [--json]
r4 endpoint describe ID
r4 endpoint status ID
r4 endpoint logs ID [--tail N]
r4 endpoint upgrade ID --model NAME   # Rolling model-version upgrade (no downtime)
r4 endpoint down ID [--force]
r4 endpoint access ID [--set SCOPE ...] [--private]   # who may use it
```

`SCOPE` is `user:<id>`, `project:<id>`, `circle:<project>/<id>` or `system` (every
authenticated user; admin only). The owning project always keeps access.
The same grammar drives the access lists on graphs, catalogs,
workflows, buckets, and RAG corpora below.

### RAG (`r4 rag`)

```
r4 rag upload FILE [--project ID]           # single file per call
r4 rag bulk-upload DIR [--project ID]       # all supported files in a directory
r4 rag stats [--project ID]
r4 rag query TEXT --project ID [--top-k N] [--threshold FLOAT] [--no-sources]
r4 rag docs list [--project ID]
r4 rag docs delete ID [--project ID] [--yes]
r4 rag corpus-access PROJECT [--set SCOPE ...] [--private]
                                            # corpus read-sharing
r4 rag ingest ...                           # retired 2026-10-18: prints the replacement
r4 rag retriever {list|show|create|update|delete} [--project ID]
r4 rag retriever components {chunkers|embedders|rerankers}
```

### Vision (`r4 vision`)

```
r4 vision submit IMAGE --model NAME [--adapter NAME]
                [--config KEY=VALUE] [--config-json JSON]
                [--key KEY] [--output-bucket BUCKET] [--endpoint-id ID] [--json]
r4 vision status TASK_ID --model NAME [--endpoint-id ID] [--json]
r4 vision get TASK_ID --model NAME [--output PATH] [--endpoint-id ID] [--json]
r4 vision cancel TASK_ID --model NAME [--endpoint-id ID] [--json]
r4 vision ui [--model NAME]                 # Local Gradio inference UI
```

### Jobs (`r4 job`)

```
r4 job list [--project ID] [--status STATUS] [--type TYPE] [--limit N]
r4 job describe ID
r4 job cancel ID [--force]
r4 job retry ID
```

### Workflows (`r4 workflow`)

```
r4 workflow list [--scope SCOPE] [--name NAME] [--all] [--all-versions]   # --all: full catalog, admin only
r4 workflow versions NAME [--json]     # every registered version; * = the default
r4 workflow access NAME [--set SCOPE ...] [--private]   # who may run it (every version)
r4 workflow export NAME[@VERSION] [--version V] [-o PATH] [--force] [--extract DIR] [--json]   # download a workflow you can see as a verified zip
r4 workflow upload WORKFLOW_DIR|.zip [--name NAME]
r4 workflow run WORKFLOW_DIR|.zip [--param KEY=VALUE ...] [--wait]   # upload + trigger
r4 workflow lint PATH                  # Check a bundle against the upload contract (description, depends_on), offline
r4 workflow adopt OLD_NAME --as NAME --version V   # re-register a name-versioned workflow as NAME@V
r4 workflow delete NAME[@VERSION] [--version V] [--yes]   # one version, or every version
r4 workflow trigger NAME[@VERSION] [--version V] [--param KEY=VALUE] [--params-file FILE]
                         [--params-json JSON] [--input-file FILE_ID_OR_S3]
                         [--project ID] [--wait]
r4 workflow trigger NAME[@VERSION] --site SITE [--project ID] [--param KEY=VALUE]
                         [--params-file FILE] [--params-json JSON]
                         [--input-file FILE_ID_OR_S3] [--listed-input STEP S3_URI FILE]
                         [--show-inputs] [--yes]
                                       # sign one run under my own account at SITE (chapter 31)
r4 workflow status EXEC_ID
r4 workflow results EXEC_ID [--json]   # Per-step output keys + extras, and the
                                       # `r4 files download … --recursive` line
r4 workflow watch EXEC_ID [--interval SEC]
r4 workflow show NAME[@VERSION] [--version V] [--graph [--mermaid]]
r4 workflow triggers NAME[@VERSION] [--pin VERSION | --unpin] [--owner USER] [--json]
r4 workflow approve EXEC_ID STEP [--approver NAME] [--comment TEXT]
r4 workflow reject EXEC_ID STEP [--comment TEXT]
r4 workflow cancel EXEC_ID
```

- `upload` takes a workflow **directory or .zip bundle** (containing
  `workflow.json` + sources), not a single YAML file.
- `export` writes `./NAME.zip` (or `DIR/NAME.zip` when `-o` is a directory or ends in `/`;
  a missing `DIR/` is created)
  for any workflow you can see — own, `system`, or shared with you or your
  active project. The zip holds the source tree, `workflow.json` and
  `EXPORT.json` (sha256 per file, excluded secret-shaped paths); container
  images are not included. It is verified before it is renamed into place;
  `--force` overwrites, `--extract DIR` unpacks and refuses paths that
  escape `DIR` (§9, Exporting a workflow).
- `--input-file` is a repeatable **input file ID or S3 key**; pass run
  parameters with `--param`, `--params-file`, or `--params-json`.
- Versions: without `@VERSION` / `--version` a command takes the
  highest registered version; `run` triggers the version it just uploaded;
  `status`, `results` and `watch` print `NAME@VERSION` and the code digest
  (§9, Workflow versions).
- **`trigger --site SITE`: a signed run under my own account**
  (2026-11-09; not yet deployed). For a site that offers
  `user_account`, where I have an active account and a link for the
  project (`r4 hpc add`, `r4 hpc link`). See
  [Chapter 31 §31.7](31-own-hpc-accounts.md#317-run-work).
  - `SITE` is named as for `r4 hpc status`. Without `--site`, a plain
    trigger that UMRS answers with `hpcaccess.signature_required` starts
    the same flow; there `--yes` does not apply and r4 always asks
    (2026-11-08 review).
  - `@VERSION` / `--version` pick a registered version, as for a
    plain trigger; the summary shows `workflow@version`.
  - r4 asks the site for a proposed intent
    (`POST /umrs/workflows/{name}/intent-proposal`), downloads the export
    of that exact version, hashes every file itself, and recomputes the
    steps, their order and dependencies, chained inputs and each step's
    configuration. On any difference, or a login, allocation, parameter,
    site-profile or time-window mismatch, it refuses with a diff and signs
    nothing.
  - It then shows the summary: site, login, allocation, project,
    `workflow@version`, run id, expiry, and the run's time limit; the
    trusted platform image; and per step the entrypoint, image (reference
    and id), partition, CPUs, memory, GPUs, time, model endpoint or
    "no network", changed configuration keys, and inputs labelled
    *chosen by you*, *data chosen by UMRS* or *outputs of step `<p>`*.
    Above 20 inputs it shows counts per label; `--show-inputs` lists all.
  - `--listed-input STEP s3://BUCKET/KEY FILE` (repeatable) names an input
    of `STEP` that I choose: r4 hashes my local copy `FILE` itself, the site
    checks the object against that digest, and the summary labels it
    *chosen by you*.
  - It asks `y/N`. `--yes` skips only that question; the signing key still
    needs a touch, a PIN or a passphrase. It signs the intent
    (namespace `umrs-intent-v1@<signing domain>`) and triggers with
    `intent` and `signature`. The answer adds `intent_id`.
  - It refuses in the assistant's shell (`R4_COMPANION_SESSION`) and
    without a terminal. Through the shell, `r4 workflow trigger|run …
    --site` asks in chat/plan; autopilot only auto-answers that local prompt
    and still cannot sign or bypass the assistant-shell refusal (2026-11-09,
    the autopilot policy update).

- `r4 workflow show --graph` — layered ASCII DAG of the step dependencies
  (with step-type icon and status glyph).
- `r4 workflow show --graph --mermaid` — same DAG emitted as a Mermaid
  flowchart (pasteable for docs/web).
- `r4 workflow watch` — live poll of the execution, re-rendering the DAG
  with per-step status until terminal (advisory-lock durability: executions
  are restored from Postgres on UMRS restart).
- `r4 workflow approve|reject EXEC STEP` — decision for a waiting manual
  gate step (`gate: true` steps park in `waiting_approval` until approval,
  deadline expiry, or cancel; outcomes land in the execution's gate audit).
- `r4 workflow triggers NAME` — the workflow's declared event
  subscriptions (`triggers: [{on: ingest, project|bucket|prefix}]`.
  Ingest events fire matching workflows automatically post-upload.

### Buckets and files (`r4 bucket`, `r4 files`)

```
r4 bucket create PROJECT ALIAS          # Your <user>.<project>.<alias> + its -output pair
r4 bucket list                          # Personal and provisioned buckets you own
r4 bucket access BUCKET [--set SCOPE ...] [--private]
                                        # who may read/write objects (system = admin)
r4 files upload PATH [--key KEY] [--bucket B]          # One file (streams; no size cap)
r4 files upload DIR --bucket B --recursive [--key PFX] # A whole folder, layout kept
r4 files list --bucket B [--prefix P] [--json]         # Objects in your bucket
r4 files download KEY [-o PATH] [--bucket B]           # One object (streams to disk)
r4 files download PFX --bucket B --recursive -o DIR    # Everything under PFX
```

- A workflow run on an HPC lane stages only the run owner's buckets: create
  one, upload inputs there, and pass it as the workflow's `bucket` param;
  results land in `<bucket>-output` under `runs/<execution>/<step>/`.
- UMRS performs every S3 call — no credentials or presigned URLs reach r4.
- Without `--bucket`, `upload` keeps the project namespace of the shared
  catalog (cross-object dedup by SHA256, hard-linked originals).

### Circles (`r4 circle`) — project-scoped audiences

```bash
r4 circle enter PROJECT                      # join: your personal cell (casual)
r4 circle mine [--include-retired]           # my circles + derived projects
r4 circle list PROJECT                       # circles of one project
r4 circle create PROJECT ID [--name TEXT] [--for-user UID]
r4 circle show PROJECT ID
r4 circle members PROJECT ID
r4 circle member-add PROJECT ID USER [--role member|admin]
r4 circle member-remove PROJECT ID USER
r4 circle retire PROJECT ID                  # owner-or-admin; tombstone
```

Circles are project-scoped cells used as `circle:<project>/<id>` targets in
the access lists (endpoint/graph/catalog/workflow/bucket/RAG corpus)
— the boundary keeping work contexts apart inside one project and the
smallest sharing audience. A user may join any number of circles; every
membership counts at decision time. Retire stops the circle's grants
resolving *everywhere immediately*
(the resolver only sees `active` groups) — no converge, no restart.
Only an admin may create a group for another user (`--for-user`).

### Grants (`r4 grant`) — model family read grants

```bash
r4 grant create ASSET_KIND ASSET_ID {--user UID | --project PID} [--expires TS]
r4 grant list ASSET_KIND ASSET_ID    # writers on that asset only
r4 grant mine                        # grants addressed to you/projects
r4 grant revoke ASSET_KIND ASSET_ID {--user UID | --project PID}
```

Grants authorize **reads only**, are version-pin-capable and tombstone on
revoke (the audit trail survives). They remain the **model registry's**
read-share mechanism. For knowledge graphs, endpoints, workflows,
catalogs, buckets and RAG corpora use the `access` verbs above —
grant rows for those kinds are creatable for compatibility but the access
lists, not grants, guard them.
  bucket (`r4-data/<project>/<key>`), as before.

### Knowledge (`r4 knowledge`)

```
r4 knowledge export-download JOB_ID|ARCHIVE_KEY [--output PATH]
                                    # Fetch a finished `r4 project export`
r4 knowledge import BUNDLE [--allow-unsigned] [--no-raw] [--json]
                                    # .kb.tar.gz or directory → your current project
r4 knowledge export [PROJECT] --output PATH
                                    # Client-side projection (graph + catalog, unsigned)
r4 knowledge bundles                # local .kb bundles (alias: list; K1.3 2026-10-24)
r4 knowledge info BUNDLE            # .kb, .kb.tar.gz or directory
r4 knowledge validate BUNDLE
r4 knowledge restore BUNDLE_DIR [--retarget PROJECT_ID] [--no-verify] [--json]
                                    # Admin only: bundle already on the server
r4 knowledge query Q [PROJECT] [--scope all|catalog|knowledge_graph|embeddings|rag]
                               [--limit N] [--graphs G1,G2]
                               [--exclude-graphs G3] [--json]
r4 knowledge explore [PROJECT]

# Multigraph management
r4 knowledge graphs [--project-id ID] [--all] [--query Q] [--json]
r4 knowledge graph ID_OR_NAME [--json]
r4 knowledge graph-create NAME [--description TEXT] [--project-id ID] [--kind KIND]
r4 knowledge graph-archive ID_OR_NAME      # soft delete (archive is the only delete)
r4 knowledge graph-access ID_OR_NAME [--set SCOPE ...] [--private]
                                        # who may read the graph
r4 knowledge graph-ingest ID_OR_NAME PATH... [--url URL ...]
r4 knowledge allowlist [--check URL] [--project ID] [--json]
                                    # Web sites URL/repo ingest may fetch here

# Review proposed knowledge (K1.5, 2026-10-24)
r4 knowledge review [--project ID] [--graph G] [--rows] [--json]
r4 knowledge review approve|reject PROPOSAL_ID      # no project needed
r4 knowledge review establish ROW_ID | --run RUN_ID

# What the project knows, with who added it (K1.3, 2026-10-24)
r4 knowledge ls [--project ID] [--mine | --by USER] [--graph G] [--kind K]
                [--status S] [--since 7d|ISO] [--limit N] [--page N] [--json]

# Delete
r4 knowledge delete ARTIFACT_ID [--project ID] [--yes] [--json]
                                    # Artifact + everything derived; asks unless --yes

# Corpus verbs (same surface as `r4 rag upload/docs/stats`)
r4 knowledge upload FILE [--project ID]
r4 knowledge bulk-upload DIR [--project ID]
r4 knowledge docs {list|delete} [--project ID]
r4 knowledge stats [--project ID]
r4 knowledge inventory [--project ID]      # Catalogue rows with canonical badges
r4 knowledge health                        # Plane reconciliation health per asset
```

- `r4 knowledge ls` — the project's knowledge newest first: who added each
  item, when, kind, size, whether retrieval can find it yet (INDEXED: yes,
  pending, stale) and the graphs it sits in. The same server list
  (`/k2/artifacts`) backs `r4 knowledge docs list`, `r4 knowledge stats` and
  the TUI Knowledge pane; another member's personal-graph item is never
  listed. `r4 knowledge list` now lists local bundles under its new name
  `bundles` (the old name stays an alias). WHO marks how each person added
  an item: `(kept)` an answer they kept (proposed), `(vouched)`, `(agent)`
  (proposed). Usage errors (a bad `--since`, `--mine` with no user id) exit
  2; under `--json` errors print as `{"error", "exit_code"}`.
- `r4 knowledge query` (the corpus verb) sends no `--threshold` unless given:
  the server's adaptive cut and sufficiency verdict decide (it forced 0.7 until
  2026-10-24).
- `r4 knowledge restore` — serverside `.kb` bundle restore with manifest
  integrity verification (hash + HMAC when configured); idempotent
  re-runs; `--retarget` re-scopes all rows into another project (DR path).
- `r4 knowledge query` — unified search through the K3 facade
  (`wired_backends` + `unavailable_backends` report coverage honestly).
- `r4 knowledge delete` — deletes one artifact of the project in one
  server-side transaction: its row, every node derived from it (file, chunk
  and semantic nodes, the chunks a rag upload minted), their vectors and
  edges, its IR rows and verification findings. Its events stay, unlinked.
  Then its stored bytes go, unless another artifact still references them;
  a failed object delete is named in the output and the rows stay deleted.
  Prints the rows deleted per kind and one line per object. Asks to confirm
  unless `--yes`. Another project's id, an unknown id or an already-deleted
  id is a 404; agents cannot delete. `r4 knowledge docs delete DOC_ID` runs
  the same delete on the artifact a corpus document id names.
- `r4 knowledge allowlist` (2026-10-25) — the web sites the UMRS server may
  fetch for URL and git-repo ingest, as the site applies them
  (`site.yaml` `extra.knowledge_ingest.allowed_domains`; `[*.]x.org` is
  x.org and its subdomains), or that every public site is allowed, the
  networks the site never fetches (`denied_networks`), and how to get the
  list changed (ask the UMRS operator). `--check URL` says
  whether that URL's host and port are on the list (exit 0) or not (exit 1;
  a scheme other than http/https and the scp form `git@host:repo` are
  refused, as the fetch would);
  the address, size and content checks run when a fetch happens. A usage
  error exits 2. Since 2026-10-25 it also prints your web policy in the
  current project (`-p ID` for another): off, allowlist
  (with the sites both lists admit) or public, and the rule that set it;
  `--check` applies both.
- `r4 knowledge explore` — interactive three-pane TUI: section tree
  (artifacts/findings/memory/plans with readiness badges), documents,
  detail/evidence pane (A2L lineage for artifacts); `e` exports the
  project's `.kb` bundle, `r` refreshes, `q` quits.

### Scientific Catalogs (`r4 catalog`)

```
r4 catalog access ID [--set SCOPE ...] [--private]
                                        # who may read the catalog
```

(Not the data catalog — that lives under `r4 data catalog`.)

### Data (`r4 data`)

```
r4 data intake
r4 data catalog search [query] [--modality M] [--state S] [--tag T] [--prefix P] [--limit N] [--json]
r4 data catalog read <s3_key> [--offset N] [--length N] [--source auto|raw|extracted] [--json]
r4 data catalog stats [--json]
```

Connector intake board: probe cards (available/errored with detail), per-
connector sync state (status @ last_sync_at, docs/errors/DLQ), plus a
jobs/workflows summary footer.

`r4 data catalog` is the catalogue plane (what physically exists).
Search/read rows carry the server-hydrated `links` block (`document=… ·
chunk_nodes=… · vector row(s)…`); rows without a canonical mint render
`links: none (no canonical document minted)` — absence of links is never an
error. Stats include downstream plane counts (`canonical_documents`,
`chunk_nodes`, `vector_rows`) when the server can compute them (added on the
2026-09-09 science-catalog wave; `null` = not computable, not zero).

### Dashboard (`r4 dashboard`)

```
r4 dashboard [--section {jobs|models|health|workflows|connectors}] [--json]
```

Unified ops board: system health glyph + jobs, models, workflows, and
connectors sections in one view. `--section` renders one block only.

All workflow/data/dashboard commands accept `--json` for machine-readable
output.

### MCP (`r4 mcp`)

```
r4 mcp list [--servers | --tools] [--server NAME] [--healthy]
r4 mcp register NAME --url URL [--description TEXT]
                     [--auth-type TYPE] [--auth-token TOKEN]
r4 mcp unregister SERVER_ID
r4 mcp info SERVER_ID
r4 mcp health SERVER_ID
r4 mcp hatch create NAME [--description TEXT] [--output PATH]
r4 mcp hatch install PATH [--name NAME]
r4 mcp hatch search QUERY
r4 mcp hatch registry list
r4 mcp hatch connect NAME
r4 mcp hatch disconnect NAME
r4 mcp hatch uninstall NAME
r4 mcp hatch share NAME --project ID
r4 mcp hatch list
```

### Agent (`r4 agent`) — Legacy SciWorkflow Agent

```
r4 agent [OBJECTIVE]                     # No subcommand: routes how-to -> chat, actions -> run
r4 agent chat [--mode interactive]
r4 agent run OBJECTIVE                   # Plan and run a workflow from natural language
r4 agent create DESCRIPTION              # Create a workflow from description
r4 agent sessions
r4 agent tools [--search QUERY]
r4 agent scan PATH                       # Scan content for sensitive data
r4 agent audit                           # Show this user's egress audit trail
```

`r4 team` and `r4 acp` use the Research Companion instead for new work.

### Search (`r4 search`)

```
r4 search QUERY [--project ID]
                [--scope {rag_only|web_only|code_only|lab_only|all}]
                [--depth {shallow|moderate|deep}] [--max-steps N]
                [--max-sources N] [--no-citations]
                [--no-web] [--no-rag] [--no-code]
                [--stream] [--json] [--source NAME]
```

### Team (`r4 team`)

```
r4 team create NAME [--code PATH] [--goal TEXT] [--roles pm,se,pg] [--hardware H]
                                   # The HPC optimisation preset (PM/SE/PG/CD session)
r4 team list
r4 team crews                      # Each crew and what it runs
r4 team status ID
r4 team run ID [--iterations N]
r4 team pause ID
r4 team resume ID
r4 team cancel ID
r4 team logs ID
r4 team report ID
r4 team engines                    # List installed team engines
r4 team launch --spec FILE         # Spec-driven run via engine SPI (supersedes create+run)
r4 team launch NAME --team research --goal TEXT [--dry-run]
                                   # The research crew's plan (research-round)
r4 team create NAME --crew CREW [--goal TEXT] [--start]
                                   # A plan crew: shows its plan; --start runs it
r4 team hardware                   # List hardware profiles
r4 team plan list|show|validate|run|resume|result|cancel|clean   # Team plans
r4 team plan run REF --venue server --source NAME[,NAME] [--detach]
                                   # Hosted by a sandboxed worker on the UMRS site (slice 8)
```

**Server venue.** `--venue server` hands the run to the
UMRS site: UMRS records it and starts one sandboxed frontend-venue worker
that runs the plan with your pushed `--source` for its models. The
CLI follows it through its record; `result`, `resume --venue server` and
`cancel` reach it by run id from any machine. Its gates wait in the UMRS
approvals inbox (`r4 project loop decide <approval_id> --decision once|no`).
A site without the frontend venue refuses with
`plan_iteration_needs_server_venue` and the settings it lacks.
`--source site` (alone or beside a pushed source) gives the run the site's
own models, named `model@site` in the plan (slice 8b). A research loop on
the UMRS runtime with a plan runs each iteration this way
(`research_loop_start(plan=…, sources=[…])`).

**Crews.** A crew is a name for a team shape. `hpc` is the
HPC optimisation preset: the PM/SE/PG/CD session that `r4 team create` opens,
with its SOTA files, hardware profile and ChangeLog, advanced with
`r4 team run`. Every other crew is an alias for a built-in plan template and
runs through the plan engine, exactly as `r4 team plan run <template>`:

| Crew (aliases) | Template | Agents, in order |
|---|---|---|
| `research` (`science`, `research-team`) | `research-round` | planner → experimenters → analysts → reviewer panel |
| `code` (`coding`, `dev`, `development`, `claude-code`) | `code` | architect → rounds of implementer → tester → reviewer, until a review finds nothing new |
| `paper-writing` (`paper`, `manuscript`) | `paper-writing` | editor → a drafter per section → a verifier per section → copy editor |
| `kakenhi-report` (`kaken`, `reporting`) | `kakenhi-report` | lead → writers (progress, deliverables map, future plan) → checker → lead fits the form |
| `peer-review` (`review`, `referee`) | `peer-review` | chair → auditors (methods, results, reproducibility) → chair writes the review |

`r4 team create NAME --crew CREW` binds `--goal` (or NAME) to the template's
first text parameter and shows the plan; add `--start` to run it. The CLI runs
plan agents fail-closed (safe tools only); from the chat, `teamup_start(crew=…)`
asks you with the plan's approval card first.

What changed for crew users: before slice 4 these crews re-used the four HPC
slots (paper-writing, kakenhi-report and peer-review ran the HPC role
prompts) in an iteration loop. Each now runs its own prompts in its
template's order as one bounded plan run; `research`'s roles keep their
prompts. The compute `--budget` and `--target-perf` options are retired:
nothing ever charged the budget or recorded the target.

### Admin (`r4 admin`)

`r4 admin` is operator-only: `analytics` (governance dashboard) and `trace`
(request audit trail). Source management moved to the top-level `r4 source`
command; endpoint rosters to `r4 pool`.

#### Analytics

```
r4 admin analytics [--project ID] [--since SINCE] [--json]
```

#### Governance

```
r4 governance quotas PROJECT_ID [--json]
r4 governance ownership [--owner OWNER] [--project PROJECT]
                        [--resource-type TYPE] [--json]
r4 governance audit [--project PROJECT] [--since SINCE] [--limit N] [--json]
r4 governance shares [--resource-type TYPE] [--resource-id ID] [--json]
```

#### Trace

```
r4 admin trace [REQUEST_ID] [--event EVENT] [--actor ACTOR] [--limit N]
               [--since SINCE] [--until UNTIL] [--json] [--summary]
```

#### Pool (`r4 pool`)

```
r4 pool up ROSTER_FILE [--continue-on-error] [--json] [--dry-run] [--sequential]
r4 pool down ROSTER_FILE [--continue-on-error] [--force] [--json]
```

### Run / Agent Executions (`r4 run`)

```
r4 run list [--agent NAME] [--status {pending|running|completed|failed|cancelled}]
            [--project ID] [--user ID] [--limit N] [--offset N] [--json]
r4 run show EXEC_ID [--json]
r4 run create --agent NAME [--version V] [--model REF] [--prompt-hash SHA256]
              [--params JSON] [--project ID]
r4 run update EXEC_ID [--status {running|completed|failed|cancelled}]
              [--error TEXT] [--trace JSON]
r4 run ledger JOB_ID [--json]
```

> **Requires the A2L sidecar.** See [24. A2L](24-a2l.md) for deployment details.

> **Note (2026-10-17):** `r4 run create` / `update` take no `--input`,
> `--output` or `--json` — the ledger records no artifact links on an
> execution. Record lineage with `r4 artifact derive --parent <id>`.

### Sessions (`r4 session`)

```
r4 session [list|show|archive|remove|delete|conclude|pull|reconcile|live|send] [SESSION_ID]
           [--kind {all|agent|companion|teamup}] [--endpoint ID]
           [--project ID] [--venue CSV] [--state CSV] [--no-remote]
           [--include-archived] [--limit N]
           [--older-than-days N] [--yes] [--json]
```

> **Venue + state (2026-09-09):** companion sessions carry a venue
> (`local` | `frontend` | `cluster:<name>`) and a recorded state
> (`ongoing` | `stopped` | `completed` | `error`; `archived` is computed from
> the archive tombstone and requires `--include-archived`). `r4 session list`
> merges the local SQLite store with the server snapshot journal — the SYNC
> column shows `local-only` / `remote-only` / `mirrored` / `degraded`; add
> `--no-remote` to skip the journal. `r4 session conclude <id>` records
> `completed` (explicit verb only — the engine never sets it). Set a
> per-profile default venue with `r4 config context --default-venue VENUE`
> (`--clear-default-venue` to remove). `r4 session pull <id>` fetches a
> `remote-only` row from the server journal into the local store — local
> rows always win, it never overwrites an existing id.

For messaging between your own running sessions:

```
r4 session live [--json]
r4 session send <session-id|unique-prefix|name> <message…> [--json]
```

Interactive console tabs and Rich REPL sessions advertise their presence while
running. A one-shot `r4 -p "…"` run deliberately does not appear in `session live`
and cannot use the session-messaging tools; direct calls are refused rather than
creating a temporary presence entry. A REPL does not start work when a message
arrives: its next explicit user turn carries an unread count and sender names, and
the agent reads message text with `session_inbox`. Set `R4_SESSION_BUS=0` to keep an
interactive session off the bus as well.

### Memory (`r4 memory`)

*Added 2026-11-07.* Your private saved memory, the notes the agent reads each
turn (see [4. Chat](04-chat.md#saved-memory)). Local files only; the project
scope follows the active profile's project, else the current repository.

```
r4 memory [list]
r4 memory show <name>
r4 memory add [--user] [--type user|feedback|project|reference] [--expires YYYY-MM-DD] <text>
r4 memory forget <name> [--user]
r4 memory lessons [clear | forget <n>]
r4 memory promote <name> briefing              # append to R4.md, then review and commit
r4 memory promote <name> knowledge <graph>     # stage a knowledge proposal
r4 memory path
```

Not to be confused with `r4 evidence memory`, the project's scientific memory
(failed experiments, retractions) on the platform.

### Skills (`r4 skill`)

```
r4 skill list
r4 skill test NAME                  # Run the skill's tests/run.sh (exit 0 = green)
r4 skill pending [--json]           # Proposals awaiting review
r4 skill approve ID [--skill-dir DIR] [--note N] [--timeout S]
                                    # Merge only when the suite is green
r4 skill reject ID [--note N]
r4 skill search QUERY
r4 skill install NAME
r4 skill uninstall NAME
r4 skill info NAME
r4 skill scan PATH                  # Security-scan a skill directory
r4 skill update                     # Update installed skills from sources
r4 skill github REPO                # Install from any GitHub repository
r4 skill skills-sh                  # Browse/install Skills.sh marketplace
r4 skill nvidia                     # Browse/install NVIDIA Agent Skills
```

### Artifacts (`r4 artifact`)

```
r4 artifact list [--type TYPE] [--status {pending|running|completed|failed}]
                 [--project ID] [--user ID] [--limit N] [--offset N] [--json]
r4 artifact show ARTIFACT_ID [--json]
r4 artifact create --type TYPE --uri URI [--metadata JSON] [--project ID]
r4 artifact derive --type TYPE --uri URI --parent ID [--parent ID ...]
                   [--metadata JSON] [--project ID]
r4 artifact status ARTIFACT_ID {running|completed|failed} [--error MSG]
r4 artifact lineage ARTIFACT_ID [--direction {up|down|both}]
                                 [--depth N] [--json]
```

> **Lineage rule:** Agents and automated pipelines MUST use `r4 artifact derive`
> (not `create`) to preserve the provenance chain.



Inspect the audit trail for a request/correlation ID. Supports relative
timestamps (`1h`, `30m`, `2d`).

### Explain (`r4 explain`)

```
r4 explain ERROR_ID          # e.g. r4 explain auth.failed
r4 explain --list            # List all known error IDs
r4 explain --list --json     # JSON output
```

### Map (`r4 map`)

```
r4 map [--dot] [--json] [--from-file PATH]
```

Render a connection map showing user → gateway → endpoint(s) → external domains,
with security-boundary-crossing edges highlighted. Output formats: human-readable
table (default), Graphviz DOT (`--dot`), or JSON (`--json`).

### Recipe (`r4 recipe`)

```
r4 recipe list
r4 recipe validate FILE
r4 recipe run FILE [--param KEY=VALUE ...]
```

### Schedule (`r4 schedule`)

```
r4 schedule list
r4 schedule add --name NAME --cron EXPR --job-type TYPE [--param KEY=VALUE ...]
r4 schedule pause SCHEDULE_ID
r4 schedule resume SCHEDULE_ID
r4 schedule remove SCHEDULE_ID
```

Job types: `connector_sync`, `recipe_run`, `maintenance`.

### Onboard (`r4 onboard`)

```
r4 onboard [--profile PROFILE]
```

Guided interactive setup wizard for first-time users.

### Console (`r4 console`)

```
r4 console [--demo] [--offline] [--backend {cli|offline}] [--fresh]
```

Console v3 — Research Workspace / Mission Control TUI (catalog, knowledge,
research loops). `--demo`/`--offline` run against fixture data without a
gateway. On start the console restores the previous chat-tab layout and
resumes the sessions (auto-resume, 2026-10-05); `--fresh` starts clean.
Closing the console always finalizes bridge processes; durable jobs,
workflows and venue leases keep running and refresh on resume.

### Guide (`r4 guide`)

```
r4 guide                  # Print the agent tool playbook
r4 guide tools            # Companion-relevant tools with contract descriptions
r4 guide install          # Write generated editor playbook files into cwd
```

### ACP (`r4 acp`)

```
r4 acp
```

Start the Agent Client Protocol JSON-RPC server (stdin/stdout,
newline-delimited JSON) for editor integration. Streams the companion loop,
including `shell_output` events.

### Worktrees (`r4 worktree`)

*Added 2026-11-03.*

```bash
r4 worktree list                # Every worktree, its parent, branch and whether it has changes
r4 worktree create feat         # Make one (from the current worktree's state when run inside one)
r4 worktree create feat --base default   # From the default branch instead of HEAD
r4 worktree remove feat         # Remove it and its branch (refused while it has changes)
r4 worktree remove feat --discard        # ...also when it has changes
r4 worktree clean               # Remove every unchanged worktree that has none made from it
```

- **Where it lives.** Each worktree is a real `git worktree` at `.r4/worktrees/<name>` on branch `r4/<name>`. `.r4/` is excluded locally (`.git/info/exclude`), so it never shows in `git status`.
- **What it starts from.** A worktree made inside another one starts from that one's state, uncommitted work included. That work is saved as a snapshot commit on `refs/r4/snap/<name>`; the parent's branch and index are not moved.
- **Keeping the work.** Merge the branch yourself (`git merge r4/feat`). Leaving or cleaning never removes a worktree with changes unless you say `--discard`.
- **Coming next.** Teams and research loops get worktrees of their own in a later wave.

**The agent can make the worktree itself** (2026-11-04).

```bash
r4 config worktree auto         # Profile default: the agent isolates its writes by itself
r4 config worktree off          # Back to manual (the default)
R4_WORKTREE=auto r4             # This shell only
```

- **What auto does.** Before the agent's first writing tool call (a file write or edit, a patch, a shell or Python command) in a git repository, the session moves into worktree `auto-<session>` and says so in one line. Reads never trigger it.
- **Where it applies.** The console, `r4 -p`, the REPL and VS Code alike; in the console `/worktree mode auto` sets it for one session.
- **After the move.** Relative paths resolve in the worktree. A write to an absolute path in your checkout is refused (reads still work), so the agent cannot write your checkout even by naming it.
- **The agent's tools.** The agent also has `worktree_enter` / `worktree_exit`, for when you ask it to work in a worktree.
- **When the session ends.** A worktree with no changes is removed; one with changes is kept, so you can review and merge it.

### Traces (`r4 traces`)

```
r4 traces list [--job ID | --execution ID]
r4 traces show TRACE_ID
r4 traces lineage TRACE_ID      # ancestor chain
r4 traces children TRACE_ID
```

Step-level execution traces for durable jobs — DAG of steps, models,
artifacts, durations. Distinct from `r4 admin trace` (request audit trail).

### Sweep (`r4 sweep`)

```
r4 sweep templates        # List installed sweep templates
r4 sweep render ...       # Render parameterized sbatch bundles from a template
```

### Course (`r4 course`)

```
r4 course create ...      # Scaffold a university course kit on the platform
```

### Completion (`r4 completion`)

```
r4 completion bash            # Print bash completion script
r4 completion zsh             # Print zsh completion script
r4 completion install         # Install into shell RC file
r4 completion uninstall       # Remove from shell RC file
```

### Usage (`r4 usage`)

```
r4 usage                    # token usage summary, grouped by model
r4 usage -g day             # …grouped by day (model | day | user | user_model | project | model_day)
r4 usage --records          # raw records (--limit, default 100)
r4 usage --billing          # per-user billing aggregation (admin)
r4 usage --json             # JSON for scripting
```

Filters: `--since/--until` (dates), `--user` (admin), `--project`,
`--model`. Paths ride the client's mode prefix (`/gw/usage/*` behind the
edge, `/umrs/usage/*` direct); there is no `--cost` (that endpoint is not
implemented; `billing/` is frozen).

### HPC sites (`r4 hpc`)

Several HPC sites from one profile (multi-HPC plan S2/S3, 2026-10-24); see
[Chapter 11](11-sources.md#several-hpc-sites-in-one-session-2026-10-24).

```
r4 hpc sites [--json] [--refresh]   # every site: health, access mode, own account, capabilities
r4 hpc jobs [--json] [--status S] [--limit N]   # my jobs at every site, each once (<id>@<site>)
r4 hpc cancel <id>[@<site>]         # through the gateway of the site that runs the job
r4 hpc retry <id>[@<site>]
```

A site is a registered platform source or the profile's own gateway. Site
details come from `GET /gw/sites/me` (verified identity; a gateway without
the route shows `unknown`).

**My own account at a site** (2026-11-08; [Chapter 31](31-own-hpc-accounts.md)). These need a
site that offers `user_account` in `hpc_access.modes_allowed`; elsewhere
the site refuses with `hpcaccess.mode_undeclared`. See
[Chapter 31](31-own-hpc-accounts.md).

```
r4 hpc add <site> [--ssh HOST] [--key-type sk|ed25519]   # signing key, transport key, gate install over my own ssh, then verify
r4 hpc add <site> --manual --home <dir>   # print the three gate files and the authorized_keys line instead
r4 hpc verify <site> --login <login>      # the site reaches my gate (probe, then hello) and stores my allocations
r4 hpc status [site] [--json]             # my account and links; warns when the site's gate sha differs from this r4's
r4 hpc link --project <id> --account <allocation> [--site <site>]
r4 hpc unlink --project <id> [--site <site>]
r4 hpc revoke <site> [--remove-line --ssh HOST]   # the site destroys my transport key; optionally remove the line
r4 hpc rotate <site> [--ssh HOST] [--manual --home <dir>]   # new transport key; the line is replaced, then verify
r4 hpc profile <site>                     # (wave B2) show, diff and reinstall the site profile only, then verify
r4 hpc status <site> --witness            # (wave B2) check the gate's log chain over my own ssh; flag intents r4 did not sign
r4 hpc status <site> --witness --ack ID   # (wave B2) clear an unsigned intent I checked; it is flagged on every witness until then
r4 hpc status <site> --live               # (wave B2) ask the site for a live hello of my gate: version, site profile, log head
```

*(2026-11-09: B2 is merged through #815; deployment and each site's first receipt are separate acceptance steps.)* `add` re-run upgrades
the gate to version 2: it replaces the gate and `gate.json`, writes the
site profile `~/.umrs/gate/site.json` (0600, shown with a diff first, its
hash recorded in `~/.config/r4/hpc/sites/<site>.json`), and keeps
`allowed_signers`, `state/` and `log/`. Signed runs:
`r4 workflow trigger NAME --site SITE` (Workflows, above).

- **Signing key.** `add` creates a dedicated key under
  `~/.config/r4/hpc/signing/` (directory 0700): `ed25519-sk` with
  `verify-required` (a FIDO2 key with a PIN; OpenSSH 8.2 or newer), or with
  `--key-type ed25519` a passphrase-protected key (OpenSSH 8.1 or newer). A
  key left without a passphrase is refused. Signing runs
  `ssh-keygen -Y sign -f <key>` on the key file, never through an agent.
- **The person's act.** Every account command except `status` refuses in
  the assistant's shell (`R4_COMPANION_SESSION`), and so do the r4 client
  calls behind them (`python -c` gets the same refusal); UMRS refuses a
  request marked as an agent's. `add`, `revoke` and `rotate` also need a
  terminal and ask before they change anything. Through the shell,
  `r4 hpc add|rotate|revoke|sign|grant|grant-cancel|profile` (quoted or expanded verbs too) and
  any command that touches `R4_COMPANION_SESSION` asks in chat/plan;
  autopilot auto-answers only that local prompt (2026-11-09, amendment 3),
  and no assistant tool wraps or signs these commands. These
  guards catch mistakes and ordinary misuse; they are not a sandbox, which
  is why the gate trusts only your signature. *(2026-11-08 review: this
  item said agent tools cannot run them at all.)*
- **Which signing key.** `add` records the key kind it installed for the
  site (`~/.config/r4/hpc/sites/<site>.json`); `verify` and `rotate` use
  it. With both an `sk` and an `ed25519` key and no record, they ask for
  `--key-type`.
- **Rotate asks first.** `rotate` asks before UMRS makes the new key. If
  the line cannot be replaced over SSH afterwards, it prints the exact
  swap to make by hand.
- **What r4 checks.** The server's transport key must be exactly
  `ssh-ed25519 <key> umrs-transport-<site>-<key_id>` (one line, no options,
  a 32-byte key). The `authorized_keys` and `allowed_signers` lines are
  built locally from checked parts. The gate's sha256 must equal the gate
  this r4 ships, else `add` stops with a version-skew message.
- **Install.** `add` runs fixed scripts over `ssh <HOST>` (default: the
  site name). Everything variable travels on stdin, never on the command
  line. It writes `~/.umrs/gate/` (0700) with `umrs_gate.py`, `gate.json`
  and `allowed_signers` (0600), and appends the line to
  `~/.ssh/authorized_keys` (0600) only if it is absent.
- **Revoke in B1.** The site has no route yet that forwards a signed
  control to the gate. `revoke` therefore asks the site to destroy its key,
  then prints the comment of the line to delete. With `--remove-line` it
  removes that line over your own ssh session instead.

**Standing permissions (grants) and usage** (2026-11-09, waves C and D;
on `wave/gated-cd-grants-ledger` (UMRS `22dffbd1`), pending review, unmerged and undeployed;
[Chapter 31](31-own-hpc-accounts.md)). A grant
lets UMRS start runs of the workflow versions you list, at one site, under
your own login, within limits you set, until it expires or you cancel it.
Your gate checks every run against it.

```
r4 hpc grant --site S --project P --workflow NAME[@VERSION] [--workflow ...]
             --max-jobs N --max-gpu-hours H --days D
             [--max-core-hours C] [--max-concurrent K]
             [--inputs listed|chained|any] [--listed-input FILE ...] [--yes]
r4 hpc grants [--site S] [--json]          # my grants: status, limits against live usage, warnings
r4 hpc grant-cancel ID [--site S] [--direct] [--ssh HOST]   # sign, verify, optionally deliver directly over my own ssh
r4 hpc usage [--site S] [--since DATE] [--json] [--witness] [--ssh HOST]   # my usage, totals, optional gate-log evidence
r4 hpc usage --project P [--site S] [--since DATE] [--json] [--witness] [--ssh HOST]   # project usage/budget; witness needs S
r4 hpc status S --witness --clear-fork     # after investigating a fork, accept the log as the new baseline
```

- **Defaults.** `--max-core-hours 0` means no core-hour limit (the summary
  says so); `--max-concurrent` defaults to 4, and never more than
  `--max-jobs`; `--inputs chained`; `--days` is 1 to 30.
- **Site versus SSH destination.** `--site S` selects the site's gateway and
  its recorded signer. `r4 hpc add S --ssh HOST` records the physical gate's
  destination and venue. A later `add` cannot redirect that signer to another
  physical gate, and another site record cannot claim the same venue: use a
  genuinely separate site/venue or keep the existing record for cancel/revoke.
  These are refusals, never a new signing route. For cancellation or evidence,
  `--ssh HOST` defaults to the site name and is only the destination for my own
  gate verification or direct delivery.
- **What r4 checks before you sign.** For each workflow, r4 reads the export
  of the exact version and recomputes the code manifest, the step graph and
  configs, and the parameter schema itself, and refuses with a diff when
  the site's proposal differs. Every image must be one a step declares, or
  the platform image. r4 refuses, naming the parameter, a parameter that
  could carry code: a type other than `string`, `integer`, `number` or
  `boolean`; a keyword such as `format`, `pattern`, `items` or `$ref`; or a
  name such as `script`, `cmd`, `path`, `url`, `prompt` or `api_base`. A
  text parameter without a declared length gets 1024 characters; a decimal
  bound is narrowed to a whole number; both are shown.
- **Separate image roles.** Step images are in `images`; the helper/posture
  images are separately pinned in `trusted_images`, and each helper must also
  be in `images`. The gate never substitutes a step image for that helper.
  A legacy one-image grant remains readable/cancellable; an older multi-image
  grant without `trusted_images` must be revoked and reissued.
- **The data rule.** `listed`: only the files you hashed with
  `--listed-input`. `chained` (the default): those files, if any, and
  outputs of runs under this permission, which UMRS picks. `any`: the
  summary says "Under a standing permission you give, UMRS chooses the data
  your approved workflows run on."
- **What UMRS still decides.** The summary lists it: whether and when to
  start runs (up to your limits), the parameter values within the bounds
  shown, the data per the rule above, each step's partition, CPUs, memory,
  GPUs and walltime within the installed site profile, and which listed
  account and project a run uses. UMRS also sees every output. It cannot run
  other code or images, change the pinned steps, call a model endpoint,
  exceed your limits, or run anything after expiry, a cancel or a
  re-enrolment. A run still going at expiry stops there.
- **The person's act.** `grant` and `grant-cancel` refuse in the assistant's
  shell and without a terminal, and are always asked in the permission
  ladder. The times are set after you confirm, just before signing. r4 keeps
  a record of each grant it signed in `~/.config/r4/hpc/grants/<id>.json`.
  `grants` and `usage` are read only and run anywhere.
- **Cancelling with evidence.** `grant-cancel` signs a five-minute cancel
  and first tries the site's relay. That relay counts only when the cancel it
  claims is found at the same head of the gate log read over your own SSH.
  Otherwise `--direct` sends a fresh signed control straight to your gate
  (with `--ssh HOST` when its destination differs) and checks its receipt.
  Without either proof, the command exits 1 and names the pending/unconfirmed
  UMRS mirror; retry, or use `--direct`. Running jobs are not stopped:
  cancel them with `r4 hpc cancel`.
- **Re-enrolment.** `add` for an account the site reports as absent or
  revoked also writes `~/.umrs/gate/state/grants/epoch` over your own ssh;
  your gate then refuses every grant signed before it. An upgrade of an
  active account writes none.
- **The witness.** `status S --witness` also lists grant submits and
  cancels since the last check, flags a grant this r4 did not sign (until
  `--ack ID`), counts each grant's submits over the whole log and flags
  `OVER CAP` above the jobs you signed, and checks that the site's view of
  the log head lies on the log r4 read. A rewritten log or a split view is
  a fork: it prints `✗ FORK` lines, exits 1, and r4 then signs no run, grant
  or `rotate` for that site until you investigate and run
  `--clear-fork`. `grant-cancel` and `revoke` still work. `status S --live`
  reports a fork when the live head is behind the one r4 witnessed.
  Independently, `usage --witness [--ssh HOST]` verifies that the returned
  usage rows join to logged submits and that recorded GPU/core charges are not
  below the gate's evidence. Missing rows, a broken chain or under-reporting
  is an error. With `--project`, it requires an explicit `--site`.
- **Usage.** Rows come from the site's usage ledger; a row marked
  `(estimate)` is not settled yet. A project budget is in GPU-hours over the
  project's lifetime, summed over every site. On a personal usage endpoint,
  `limit` caps only returned rows, not totals: totals still cover every
  matching current row. If required pricing is absent or malformed, submit
  stops with `hpcaccess.ledger_unavailable` rather than using a cheaper
  estimate. An `hpc_script` estimate is parsed from its `sbatch_args`; r4 and
  UMRS do not read the script's body to derive resource requests.

### Sources (`r4 source`)

User-level source management (the admin `source` group was removed — this is
the only entry point).

```
r4 source list [--json]           # --json rows: default_model, project, has_api_key (never the key)
r4 source add NAME --url URL [--kind {platform|public}]
              [--api-key KEY]             # plain flag — warns (history/ps leak; prefer stdin/env)
              [--api-key-env VAR]         # read key from environment variable (preferred)
              [--api-key-stdin]           # read key from stdin/prompt (preferred)
              [--allow-insecure-http]     # permit credentialed http:// (off by default)
              [--provider {bedrock|anthropic|openai-compatible}]
              [--aws-region REGION] [--aws-profile PROFILE]
              [--description TEXT]
              [--zmq-host HOST] [--zmq-port PORT] [--zmq-tunnel]
              [--default-model NAME]      # model used for this source when none is named
              [--project ID]              # X-Project-ID sent to this source
              [--replace]                 # drop the old row and its stored credentials
r4 source add-ollama [--use]      # Add a local Ollama source (OpenAI-compatible)
r4 source set NAME [--api-key-stdin | --api-key-env VAR | --api-key KEY]
              [--allow-insecure-http] [--default-model NAME] [--project ID]
                                  # change the named fields; every other field is kept
r4 source remove NAME             # also deletes the UMRS profile JSON, .token,
                                  # auth-store row and CURVE session file for
                                  # the source (audit G-2); local files only
r4 source use [NAME]              # Omit NAME to clear active source
r4 source push NAME [--allow-paths P,..] [--yes] [--json]   # public, non-loopback sources only
r4 source unpush NAME [--json]
r4 source audit NAME [--limit N] [--json]
```

Credential hygiene (2026-09-09, audit G-1/G-2/G-4): `source add` refuses to
store a credential for a non-localhost `http://` URL unless
`--allow-insecure-http` is passed with intent; source removal no longer
leaves plaintext key material behind on disk.

Per-source isolation (2026-10-15). See
[chapter 11](11-sources.md#each-source-gets-only-its-own-credential-and-identity-2026-10-15).

- A request to a source carries only that source's own credential, project
  and user. If the source has no credential of its own, no `Authorization`
  header is sent. The CLI then prints the
  `printf '%s' "$TOKEN" | r4 source set NAME --api-key-stdin` line that gives
  it one and keeps every other field of the row.
- `r4 login` / `r4 auth login` record which deployment the login was made for
  (`login_gateway_url` in the profile); the login, its project and its user
  never reach another deployment (the active source, `R4_GATEWAY_URL`; one
  legacy exception for logins that record nothing is in chapter 11). An
  explicit `R4_SOURCE` wins over `R4_GATEWAY_URL`.
- A login that names no gateway (no `--gateway-url`, no `gateway_url` claim,
  Enter at the prompt) keeps the deployment the login it replaces recorded.
  A token refresh while another source is active stays with its own
  deployment, and the CLI says how to move it (`--gateway-url URL`). With
  nothing recorded before, it records `R4_GATEWAY_URL` or the active source.
  The built-in `http://localhost:8080` is never recorded unless one of those
  two names it (2026-10-15).
- `--source` also moves the ZMQ data plane to that source; `r4 --source NAME`
  with no subcommand opens Console v3 on it.
- `NAME` may not be `site` or `local`. These names are reserved, in any
  letter case, for every writer of `sources.json`.
- `--default-model` is used when a command names no model. `--model`,
  `model@source` and `R4_MODEL` win.
- `--project` is sent as `X-Project-ID`. `R4_PROJECT_ID` wins on the active
  deployment.
- Without `--replace`, adding an existing `NAME` updates the row as before.
  `--replace`, or an update that points `NAME` at another deployment, also
  deletes the credentials stored for the old row. It never touches your
  profile's own login.
- `source push` refuses, before any network call: a platform source; a
  source whose URL is this machine in any spelling (including the IDNA full
  stops `。．｡`); a public row on a registered platform source's deployment;
  a key that is a token naming a gateway.
- `source remove` deletes local files only. It contacts no gateway for those
  sources (no unpush, no ZMQ-session revoke).
- Recovery hints read the token from a pipe:
  `printf '%s' "$TOKEN" | r4 auth login --api-key-stdin`.

## Renamed Commands

The following commands have been **removed**. Use the new form:

| Old Form | Current Form |
|----------|--------------|
| `r4 init` | still works (hidden); prefer `r4 auth login` or `r4 login` |
| `r4 config init` | still works; `r4 login` preferred for first-time setup |
| `r4 logout` | `r4 auth logout` |
| `r4 sessions` | `r4 session` |
| `r4 ask search` | `r4 search` |
| `r4 ask agent` | `r4 agent` |
| `r4 ask teamup` | `r4 team` |
| `r4 ask companion` | `r4` (no args) |
| `r4 admin source` (removed) | `r4 source` |
| `r4 trace` | `r4 admin trace` |
| `r4 admin pool` (removed) | `r4 pool` |
| `r4 agent-exec` | `r4 run` |

## Smoke Test Sequence

```bash
r4 config show
r4 auth status
r4 health
r4 doctor --json
r4 model list --limit 10
r4 endpoint list
r4 source list
r4 knowledge graphs
r4 project list
r4 artifact list --limit 5
r4 session list
r4 chat "hello"
```
