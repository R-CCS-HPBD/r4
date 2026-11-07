# UMRS workflow bundle — complete reference template

Copyable `workflow.json` + params example for a registered server pipeline.
All facts validated against `umrs.pipeline_sdk.workflow.StepSpec`, the
template step classes in `umrs.pipeline_sdk.templates`, and the upload
contract in `r4_contracts.workflow_package` (`r4/tests/test_workflow_upload_contract.py`
checks that the example below passes it).

## Contents

- What an upload must declare
- Bundle layout
- workflow.json (annotated)
- params.example.json
- Templating rules
- Validation / run commands

## What an upload must declare

UMRS refuses an upload that lacks either of these and names every problem.
`r4 workflow lint <bundle-dir>` shows the same list, with line numbers,
before you upload:

1. `description`: what the workflow does, what it needs (inputs,
   parameters) and what it produces.
2. A flow graph: **every** step has a `depends_on` list, naming the steps it
   waits for, or `[]` for a step that starts the run. Steps with no edge
   between them run in parallel. Step order in the file means nothing, so
   spell out every edge.

Recommended: `parameter_schema` for the run parameters. Its `required`
list is enforced when the workflow is triggered, and the console shows the
parameters as the line to trigger with.

## Bundle layout

```
my-workflow/
├── workflow.json          # definition (this file's template)
├── params.example.json    # example trigger params (flat key/value)
└── ... optional extra files the bundle code reads at runtime
```

Upload the directory or a .zip of it.

## workflow.json (annotated)

```json
{
  "name": "my-workflow",
  "version": "1.0",
  "description": "Summarizes one input file, then reviews the summary; a person approves the result.",
  "source": { "type": "local_path", "path": "." },
  "parameter_schema": {
    "type": "object",
    "properties": {
      "input_key": { "type": "string", "description": "S3 key of the file to summarize." }
    },
    "required": ["input_key"]
  },
  "execution_mode": "in_process",
  "requirements": [],
  "resources": {
    "gpu_count": 0,
    "cpu_limit": "1",
    "memory_limit": "1Gi",
    "timeout_seconds": 600
  },
  "steps": [
    {
      "name": "collect",
      "entrypoint": "umrs.pipeline_sdk.templates.llm_step:LLMStep",
      "depends_on": [],
      "model_type": "llm",
      "config": {
        "temperature": 0,
        "max_tokens": 512,
        "system_prompt": "You extract facts.",
        "prompt_template": "Summarize {{ params.input_key }} for project {{ params.project_id }}."
      }
    },
    {
      "name": "review",
      "entrypoint": "umrs.pipeline_sdk.templates.llm_step:LLMStep",
      "depends_on": ["collect"],
      "model_type": "llm",
      "config": {
        "temperature": 0,
        "max_tokens": 512,
        "system_prompt": "You critique drafts.",
        "prompt_template": "Review this text: {{ steps.collect.response_text }}"
      },
      "max_retries": 1,
      "retry_delay": 2.0,
      "timeout_seconds": 300
    },
    {
      "name": "human_gate",
      "gate": true,
      "depends_on": ["review"]
    }
  ]
}
```

No step names a `model`: the platform routes each one to a served model
that fits its `model_type` / `requires`. Set `"model": "<served model>"` to
pin one. Nothing fills in a `{{model}}` placeholder.

Top-level keys:

| Key | Required | Meaning |
| --- | --- | --- |
| name | yes | Workflow name (the upload's `--name` overrides it) |
| description | **yes** | What it does, what it needs, what it produces |
| source | yes | `{"type": "local_path", "path": "."}` for code shipped in the bundle |
| steps | yes | At least one step (below) |
| parameter_schema | recommended | JSON Schema object for the run parameters (`properties`, `required`) |
| execution_mode / requirements / resources / version | no | Where and how it runs |

Step fields (from StepSpec):

| Field | Required | Meaning |
| --- | --- | --- |
| name | yes | DAG node id; later steps reference it in depends_on and templates |
| entrypoint | yes* | `"pkg.module:Class"` importable inside the UMRS runtime (*omit when gate=true) |
| depends_on | **yes** | Step names that must complete first; `[]` for a step that starts the run |
| config | no | Step-class config dict (see per-class keys below) |
| extends | no | `"name@version"` reuse of a registered step |
| max_retries / retry_delay / timeout_seconds | no | Per-step execution policy |
| gate | no | `true` = manual-approval pause; no class runs; approve via `r4 workflow approve` / workflow_approve_gate |
| requires / model / model_type / image / routing_hints | no | Capability tags, model alias ("llm"/"vision"/"embedding"), per-step Singularity .sif or registry image, scheduling hints |

execution_mode: `in_process` (default, in the UMRS server) | `subprocess` |
`slurm` | `k8s` — slurm/k8s run one cluster job per step.

## Step-class config keys (umrs.pipeline_sdk.templates)

- llm_step.LLMStep — `model` (default `rivault-llm-static`), `system_prompt`,
  `prompt_template`, `temperature`, `max_tokens`, `output_schema`,
  `timeout_sec`; emits `{ "response_text": ... }` (plus structured fields
  when output_schema is set).
- vision_step, code_step, notebook_step, ocr_step, audio_step, video_step —
  same bundle shape; class-specific config keys live beside the class in
  the templates package.

## params.example.json

```json
{
  "input_key": "project/data/input.csv"
}
```

Trigger params are FLAT key/value. They fill every step's config as
defaults, but a key the step's own config sets wins. `project_id` comes from
the active project (`--project` picks another). Input files are S3 keys
from the data catalog (resolve with lab_catalog_search → s3_key).

## Templating rules

Only an LLMStep's `prompt_template` is rendered, and only these two forms:

- `{{ params.<key> }}` — trigger param value (spaces inside braces allowed)
- `{{ steps.<step_name>.<output> }}` — upstream step output field, e.g.
  `{{ steps.review.response_text }}`

Anything else, such as a placeholder in `system_prompt` or `model`, or
`{{ x }}` with any other root, stays as it is or renders empty.
`r4 workflow lint` warns about these.

## Validation / run commands

```bash
r4 workflow lint ./my-workflow             # the upload contract, with line numbers
r4 workflow upload ./my-workflow           # or my-workflow.zip
r4 workflow show my-workflow --graph       # verify the step DAG
r4 workflow trigger my-workflow --param project_id=my-project
r4 workflow status <execution_id>
r4 workflow results <execution_id>
```

Knowledge-indexing drafts (DIFFERENT schema — workflow_id/step_id UUID4,
kind in rag_index|vector_index|graph_build|catalog|scan) are validated with
`r4 workflow lint <file>` too; lint tells drafts and bundles apart.
