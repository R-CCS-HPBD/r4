# 9. Workflows & Jobs

R4 supports declarative workflows and durable job tracking for complex, multi-step tasks.

## Workflows

Workflows are reusable, multi-step task definitions stored in UMRS.

### Listing Workflows

```bash
r4 workflow list
r4 workflow list --scope system
r4 workflow list --name "my-pipeline"
r4 workflow list --all   # admin only: the whole catalog across owners
r4 workflow list --all-versions   # one row per registered version
```

Each row shows the workflow's default `VERSION` and how many `VERSIONS` are
registered (see [Workflow versions](#workflow-versions-2026-11-08-adr-113)).

The default view lists your own workflows, the platform's `system`
workflows, and rows whose access list (below) admits you — on both the
gateway and the direct-ZMQ transport, for admins too (2026-10-06). The
full-catalog audit view is the explicit `--all`.

### Sharing a workflow (2026-10-03)

A workflow you uploaded is yours; `system`-scoped workflows (admin-uploaded)
run for everyone. To let colleagues run and describe *your* workflow, set
its access list — several users, circles or projects in one list, and every
circle membership of a caller counts:

```bash
r4 workflow access my-pipeline --set circle:proj-a/lab --set user:bob
r4 workflow access my-pipeline          # show
r4 workflow access my-pipeline --private
```

Run resolution is: your own copy first, then a `system` one, then one
shared with you. A colleague's run of your shared workflow executes as
*their* run against buckets **they** may use — share the bucket too
(`r4 bucket access`) if the run should read/write shared data. `system`
entries are admin-only; re-scoping a `system`-scope workflow is admin-only.

To share with everyone in a project, name the project; its members see the
workflow while that project is their active one (`r4 project switch`):

```bash
r4 workflow access my-pipeline --set project:proj-a
```

### Exporting a workflow (2026-11-07)

Anyone who can see a workflow can take its code home as a zip: the owner,
everyone for a `system` workflow, colleagues its access list admits (a
`project:` share counts for members whose active project is that project),
and admins. For anyone else the workflow does not exist (the same 404 as an
unknown name).

```bash
r4 workflow export my-pipeline                     # ./my-pipeline.zip
r4 workflow export my-pipeline -o ~/exports/       # ~/exports/my-pipeline.zip
r4 workflow export my-pipeline -o old.zip --force  # overwrite an existing file
r4 workflow export my-pipeline --extract ./my-pipeline-src   # also unpack it
```

The zip holds:

- the workflow's **registered source tree**, with relative paths;
- `workflow.json`, its `source.path` made relative, so the zip can be
  uploaded again (`r4 workflow upload my-pipeline.zip --name my-copy`);
- `EXPORT.json`: workflow name, export time, UMRS version, a sha256 and
  size for every file, and the list of excluded paths.

Left out, and listed under `excluded` in `EXPORT.json`: symlinks (never
followed), anything under a `.env*`, `.git*` or `__pycache__` path, and
key or credential files (`*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`,
`id_ed25519*`, `credentials`, `credentials.json`, `.netrc`, `.pgpass`,
`secrets.yaml`/`secrets.yml`). **Container images are not included**: the
export is the code and its manifest, not the runtime. An export above the
server's 200 MiB cap is refused (413). A registration whose source directory
is gone exports `workflow.json` and `EXPORT.json` only.

r4 never writes a half-checked file: the zip streams to a temporary file
beside the target, every `EXPORT.json` checksum is verified, and only then
is it renamed into place. An existing file is refused without `--force`.
`--extract DIR` unpacks after the check and refuses any path that would
land outside `DIR`. The command prints the path, the file count and the
excluded count (`--json` for a machine-readable summary). From Python:
`client.workflows.export("my-pipeline", "exports/")` returns the path.

### Uploading a Workflow

Upload takes a workflow **directory or `.zip` bundle** containing
`workflow.json` plus the step source files:

```bash
# check it first: every problem the upload would be refused for, with its line
r4 workflow lint ./my-workflow/
r4 workflow upload ./my-workflow/ --name "protein-analysis"
# one-shot upload + trigger:
r4 workflow run ./my-workflow/ --param data_path=/projects/team/data.csv --wait
```

`workflow.json` declares steps by `entrypoint` (`module:Class`), `config`, and
`depends_on`, with top-level `description`, `source`, `parameter_schema`,
`execution_mode`, and `resources`:

```json
{
  "name": "protein-analysis",
  "version": "1.0.0",
  "description": "Cleans a protein table, keeps hits above a cutoff, and writes a report.",
  "source": {"type": "local_path", "path": "./steps/"},
  "parameter_schema": {
    "type": "object",
    "properties": {"data_path": {"type": "string", "description": "CSV of protein measurements."}},
    "required": ["data_path"]
  },
  "execution_mode": "in_process",
  "steps": [
    {"name": "preprocess", "entrypoint": "preprocess:Preprocess", "depends_on": []},
    {"name": "analyze", "entrypoint": "analyze:Analyze",
     "depends_on": ["preprocess"], "config": {"cutoff": 0.7}},
    {"name": "report", "entrypoint": "report:Report",
     "depends_on": ["analyze"]}
  ]
}
```

**What an upload must declare (since 2026-10-02).** UMRS refuses a workflow
that is missing either of these, and lists every problem at once:

1. `description`: what the workflow does, what it needs and what it
   produces. It is what other users and their agents read first.
2. The flow graph: **every** step has a `depends_on` list, naming the steps it
   waits for, or `[]` for a step that starts the run. Step order in the file
   means nothing, so spell out every edge.

Declare the run parameters in `parameter_schema` too (recommended). Its
`required` list is enforced when the workflow is triggered, and the console's
workflow card shows them as the line to trigger with. Enum values are
lowercase (`local_path`, `in_process`, `slurm`, …). Workflows registered
before this rule keep running unchanged.

See [demos/06_complex_workflow/workflow.json](../../demos/06_complex_workflow/workflow.json)
for a complete example.

### Workflow versions (2026-11-08, ADR-113)

The `version` in `workflow.json` is part of the definition: a semantic
version `MAJOR.MINOR.PATCH` with an optional `-prerelease` (`1.2.0`,
`2.0.0-rc.1`). UMRS keeps every version you upload, and a version never
changes once registered. Keep the version out of the workflow **name**: name
the workflow `example-cnn-track` and bump `version`, not
`example-cnn-track-v0.2.9` (an upload with such a name gets a warning).
This needs a UMRS with ADR-113 support; older servers ignore the version.

- **Upload** prints the registered version and its code digest. Re-uploading
  the same version with the same files is a no-op (`status: unchanged`); the
  same version with other files is refused: bump `version`. An upload without
  a valid `version` is refused by name.
- **Choose a version** with `NAME@VERSION` or `--version VERSION` on every
  command that names a workflow (`show`, `trigger`, `export`, `delete`,
  `definition save --workflow`). Without one, the highest registered version
  runs (pre-releases only when no release exists). Giving two versions that
  disagree is an error.
- **`run`** uploads, then triggers exactly the version it just registered.
- **Runs record what they ran**: `status`, `results` and `watch` show
  `workflow : NAME@VERSION` and the code digest; a saved run definition
  replays its recorded version.

```bash
r4 workflow versions protein-analysis          # every version; * marks the default
r4 workflow show protein-analysis@1.1.0
r4 workflow trigger protein-analysis --version 1.1.0 --param data_path=...
r4 workflow delete protein-analysis@1.0.0      # one version; without @: every version
r4 workflow triggers protein-analysis --pin 1.1.0   # ingest triggers run 1.1.0 (--unpin: default)
# a workflow that kept its version in the name (owner or admin):
r4 workflow adopt example-cnn-track-v0.2.9 --as example-cnn-track --version 0.2.9
```

`adopt` registers the old workflow's files as `NAME@VERSION` and carries its
access list and triggers; the old name's triggers are switched off, and you
can delete the old name once nothing uses it. Access (`r4 workflow access`)
belongs to the name and covers every version.

**In the console (TUI v3, 2026-11-09).**
- `/workflow list` and the Work pane show each workflow with its default
  version and how many others exist: `relax · v1.2.0 (+2)`.
- In the Work pane, `v` on a workflow row picks the next version (`V` the
  previous one); the row then reads `relax@1.1.0 · picked (+2)`. Enter opens
  the definition card of that version and `t` runs it.
- The definition card lists every version (the default marked), the code
  digest, and `code · unavailable` for a version whose files are gone. `v` /
  `V` step through the versions in the card; `r` puts
  `/workflow trigger NAME@VERSION …` in the composer, pinned to the version
  the card shows.
- `/workflow show NAME@VERSION` describes that version. Runs (Work pane rows,
  the execution overlay, `/workflow status|results`) show `NAME@VERSION` and
  the first 12 characters of the code digest.

**In VS Code (2026-11-09).** The Workflows view labels each workflow with
`v<default> (+N)`. A workflow with several versions expands to one row per
version (the default marked); the trigger button on a version row runs that
version. Triggering such a workflow's own row asks which version to run, the
default first. A typed `NAME@VERSION` in the trigger prompt runs that version,
and the confirmation names the version the run pinned.

Against a server without ADR-113 support the console and VS Code show the
workflow as one version and never ask for one.

### Triggering a Workflow

```bash
# Trigger with parameters
r4 workflow trigger protein-analysis \
  --param data_path=/projects/team/data.csv \
  --project mylab

# Trigger and wait for completion
r4 workflow trigger protein-analysis \
  --param data_path=/projects/team/data.csv \
  --wait

# Trigger with a params file (JSON object of run parameters)
r4 workflow trigger protein-analysis \
  --params-file params.json

# Attach a registered input file (repeatable: file ID or S3 key)
r4 workflow trigger protein-analysis \
  --input-file s3://mylab/inputs/data.csv
```

### Step Policy: retries and deadlines

Every step supports an optional execution policy, shared by both
executors (`workflow.json` pipeline step→executor and `WorkflowPipeline`):

```json
{
  "name": "analyze",
  "entrypoint": "my_module:AnalyzeStep",
  "max_retries": 3,
  "retry_delay": 30.0,
  "timeout_seconds": 3600
}
```

- `max_retries` (int, 0 = no retries) — re-run after a failure; each
  attempt is recorded.
- `retry_delay` (seconds) — backoff between attempts; a cancelled
  execution interrupts the wait immediately.
- `timeout_seconds` (soft deadline) — a step whose run exceeds the bound
  is failed at the deadline and counts as one attempt. The underlying
  worker's completion is discarded afterwards (soft, not a hard kill).

Attempt history remains visible in execution detail:
`steps[].extra.attempts` / `last_error` recorded via `r4 workflow status`.

### Human-in-the-loop gates

A step can wait for a human decision instead of executing code. Declare
a gate by setting `gate: true` (no `entrypoint` needed):

```json
{
  "name": "human-review",
  "gate": true,
  "gate_timeout_seconds": 86400
}
```

Life of a gate:

1. When the runner reaches the step it parks in `waiting_approval`
   (visible in `r4 workflow status` and on the dashboard).
2. A person decides with the CLI:

   ```bash
   r4 workflow approve <execution-id> human-review --comment "analysis looks good"
   r4 workflow reject  <execution-id> human-review --comment "rerun with correct params"
   ```

3. Approved → the workflow continues to dependents. Rejected → the run
   fails with the decision recorded.
4. If nobody decides before `gate_timeout_seconds` (default 24 hours),
   the step fails on deadline expiry.
5. Decisions are audit-recorded in the execution (`results.gate_audit`) —
   durable, visible post-completion.

Rules: a gate is approval-driven — retries never fire on it; cancellation
wins over any parked wait.

### Event triggers (upload → workflow)

A workflow can fire automatically when files land in the catalog.
Declare subscriptions at the top level of the workflow file:

```json
"triggers": [
  {"on": "ingest", "bucket": "uploads", "prefix": "images/", "project": "proj-a"}
]
```

Every post-upload ingestion then launches the workflow (params include
`ingest_s3_key`, `bucket`, `project_id`). Empty fields match anything.
Introspect the subscriptions any time:

```bash
r4 workflow triggers protein-analysis
r4 workflow triggers protein-analysis --pin 1.1.0   # fire this version (ADR-113)
```

A workflow with several versions fires **once** per event: the pinned
version, else the default (highest) one. `r4 workflow triggers NAME@VERSION`
shows what that version declares; an adopted old name shows its triggers as
disabled. A version error prints the next step (for example, a version that
is not registered points to `r4 workflow versions NAME`).

Cron scheduling uses `r4 schedule add` (see [Schedule Management](#schedule-management)
below) — the two mechanisms are separate by design.

### Monitoring Execution

```bash
# Check execution status
r4 workflow status <execution-id>

# Cancel a running workflow
r4 workflow cancel <execution-id>
```

In the console (`r4`, 2026-10-24) you can watch a run as a live graph:

- While a workflow runs, the status bar shows a badge such as
  `▶ 1 workflow · ◐ 3/7`: how many executions are running, and how many
  steps of the most recent one are done out of its total (`3/7+` means the
  total is not known yet, only the steps started so far). It counts the
  runs of the current project, like `/work`; press `A` in `/work` to see
  every project. It is hidden when nothing runs and on terminals narrower
  than 100 columns.
- **Alt+W** opens the live graph of that most recent running execution:
  each step is a box with its status (✔ done, ◐ running, ○ pending,
  ✖ failed, ◌ waiting, ⚠ blocked or skipped), and the step list below gives
  wall time and measured GPU cost. `Shift+T` shows traces, `x` cancels, Esc
  closes. Close any open dialog (help, an approval prompt) first.
- `/workflow graph` opens the same view for the most recent running
  execution (or the most recent one when none runs); `/workflow graph <id>`
  opens a given execution, and the first characters of the id are enough.
  With no executions yet, it points you to `/work`, where Enter on an
  execution row opens the same view.

If the workflow's definition cannot be read, the graph links the reported
steps in order and its header says `linear (no definition)`. Details:
[TUI reference](../r4/TUI_REFERENCE.md).

## Jobs

Jobs are the individual units of work tracked by UMRS's durable job system. Workflows create jobs, but jobs can also be standalone.

### Job Types

| Type | Description |
|------|-------------|
| `endpoint_provision` | Provision a Mini-RiVault instance |
| `index_build` | Build RAG embeddings |
| `embedding_batch` | Batch embedding generation |
| `pipeline_run` | Pipeline execution step |
| `cleanup` | Resource teardown |

### Job Commands

```bash
# List jobs
r4 job list
r4 job list --status running
r4 job list --type endpoint_provision
r4 job list --project mylab --limit 20

# Inspect a job
r4 job describe <job-id>

# Cancel a running job
r4 job cancel <job-id>
r4 job cancel <job-id> --force

# Retry a failed job
r4 job retry <job-id>
```

### Job States

```
queued → running → completed
                 → failed (→ retry → running)
                 → cancelled
```

### Durable Job Semantics

The platform uses crash-safe durable job tracking:

- **Job** = user intent (immutable spec, one row per logical job)
- **Attempt** = one execution try (multiple attempts per job on retries)
- **Commit Pointer** = marks which attempt's outputs are "current"

Key guarantees:
- All outputs are scoped to attempts (`s3://…/jobs/<job_id>/attempt_<n>/…`)
- No concurrent attempts per job (Postgres advisory lock)
- On crash recovery: in-flight attempts are marked `LOST`, jobs are requeued if retries remain
- Execution authority (slurm/k8s/compose) never changes after job creation

## Pipeline SDK (Local)

For local development and testing, the Pipeline SDK lets you define and validate pipelines before uploading:

```python
from umrs.pipeline_sdk.builder import PipelineBuilder

workflow = (
    PipelineBuilder("my-analysis")
    .add_step("ingest", "steps/ingest.py:Ingest")
    .add_step("process", "steps/process.py:Process", depends_on=["ingest"])
    .add_step("report", "steps/report.py:Report", depends_on=["process"])
    .set_source("/path/to/steps/")
    .set_execution_mode("IN_PROCESS")          # IN_PROCESS | SUBPROCESS | SLURM | K8S
    .build()                                   # validates the DAG, returns a dict
)

import json
json.dump(workflow, open("workflow.json", "w"))   # bundle it and `r4 workflow upload`
```

## Recipe Management

Recipes are reusable pipeline templates that bundle a workflow definition with default parameters.

### Listing Recipes

```bash
r4 recipe list
```

### Validating a Recipe

```bash
r4 recipe validate ./my-recipe.yaml
```

### Running a Recipe

```bash
# Run with default parameters
r4 recipe run ./my-recipe.yaml

# Override parameters
r4 recipe run ./my-recipe.yaml --param data_path=/projects/data.csv --param epochs=10
```

## Schedule Management

Schedule cron-based recurring job execution.

### Listing Schedules

```bash
r4 schedule list
```

### Adding a Schedule

```bash
# Schedule a daily recipe run
r4 schedule add --name "nightly-sync" \
  --cron "0 2 * * *" \
  --job-type recipe_run \
  --param recipe=protein-analysis

# Schedule a connector sync every 6 hours
r4 schedule add --name "data-sync" \
  --cron "0 */6 * * *" \
  --job-type connector_sync \
  --param source=my-source

# Schedule weekly maintenance
r4 schedule add --name "weekly-cleanup" \
  --cron "0 0 * * 0" \
  --job-type maintenance
```

Job types: `connector_sync`, `recipe_run`, `maintenance`.

### Pause / Resume / Remove

```bash
r4 schedule pause <schedule-id>
r4 schedule resume <schedule-id>
r4 schedule remove <schedule-id>
```

## Python SDK

```python
from r4 import Client

client = Client()

# Workflows (2026-11-08: keyword names corrected to the SDK's
# `parameters=` and `execution_id`; versions per ADR-113)
workflows = client.workflows.list_workflows()          # .version, .versions per row
execution = client.workflows.trigger("my-pipeline@1.2.0", parameters={"key": "value"})
status = client.workflows.status(execution.execution_id)
print(status.workflow_version, status.source_sha256)

# Jobs
jobs = client.jobs.list(status="running")
job = client.jobs.get(job_id)
client.jobs.cancel(job_id)
client.jobs.retry(job_id)
```

## UMRS Monitor TUI

For real-time job monitoring (operator access):

```bash
umrs monitor
```

The TUI provides multi-tab views for jobs, server status, models, workflows, and connections.

## Next Steps

- [Chapter 10 — Vision](10-vision.md)
- [Chapter 12 — Ask: Agent, Search & Teamup](12-ask.md)
