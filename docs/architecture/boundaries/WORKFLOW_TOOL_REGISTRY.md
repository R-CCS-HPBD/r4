# Workflow Tool Registry

> Audience: workflow authors, pipeline integrators.
> For platform architecture see [ARCHITECTURE.md](../ARCHITECTURE.md).
> For step routing rules see [STEP_ROUTING.md](STEP_ROUTING.md).

## Overview

The UMRS Pipeline SDK provides a set of **built-in step templates** that
workflow authors can reference directly in `workflow.json` without writing
any Python code. Each template is a `Step` subclass with a fixed
entrypoint, a typed configuration contract, and well-defined I/O.

User-defined steps follow the same interface (`Step`, `StepContext`,
`StepResult`) and can be mixed freely with built-in templates in a
single workflow DAG.

## Built-in Step Templates

### `LLMStep` — LLM reasoning

| Field | Value |
|---|---|
| **Entrypoint** | `umrs.pipeline_sdk.templates.llm_step:LLMStep` |
| **Purpose** | Render a prompt template against upstream outputs, call a platform LLM endpoint, optionally validate structured output. |

#### Configuration

| Key | Type | Default | Description |
|---|---|---|---|
| `model` | `str` | `"rivault-llm-static"` | Model name passed to the LLM endpoint. |
| `system_prompt` | `str` | `""` | System message prepended to the conversation. |
| `prompt_template` | `str` | *(required)* | Template with `{{ steps.NAME.FIELD }}` placeholders. |
| `output_schema` | `dict` | `null` | JSON Schema; when set, asks the model for `json_object` format and validates the response. |
| `temperature` | `float` | `0.0` | Sampling temperature. |
| `max_tokens` | `int` | `1024` | Max output tokens. |
| `timeout_sec` | `float` | `60.0` | HTTP timeout for the LLM call. |
| `api_base` | `str` | env `OPENAI_BASE_URL` or `R4_GATEWAY_URL` | Override LLM endpoint URL. |
| `api_key` | `str` | env `OPENAI_API_KEY` | Override API key. |

#### Template Language

Only `{{ steps.NAME.FIELD }}` substitutions are supported, where `FIELD`
is a dotted path into the upstream step's `StepResult.extra` dict.
Full Jinja2 is intentionally not used to avoid a hard dependency.

Example:
```
{{ steps.vision_dense.dense_description }}
{{ steps.vision_ocr.ocr_text }}
```

#### Outputs

| `StepResult` field | Content |
|---|---|
| `output_keys` | S3 key of the persisted `response.json`. |
| `extra["prompt"]` | Rendered prompt text. |
| `extra["response_text"]` | Raw LLM response. |
| `extra["model"]` | Model name used. |
| `extra["structured"]` | Parsed JSON (when `output_schema` is set and valid). |
| `extra["validation_error"]` | Validation error string (when schema check fails). |

---

### `VisionStep` — Vision perception

| Field | Value |
|---|---|
| **Entrypoint** | `umrs.pipeline_sdk.templates.vision_step:VisionStep` |
| **Purpose** | Call a vision model (VLM, OCR, SAM3) and produce a `VisionPerception` envelope. |

#### Modes

| Mode | Backend | Description |
|---|---|---|
| `caption` | VLM (Gateway) | Short caption. |
| `dense` | VLM (Gateway) | Long, dense description. |
| `detect` | VLM (Gateway) | Object detection (boxes + labels). |
| `ocr` | VLM (Gateway) | Text extraction from image. |
| `segment` | Vision MCP | SAM3 mask generation. |
| `embed` | Vision MCP | Image-embedding extraction. |

#### Configuration

| Key | Type | Default | Description |
|---|---|---|---|
| `mode` | `str` | `"dense"` | One of `caption`, `dense`, `detect`, `ocr`, `segment`, `embed`. |
| `model` | `str` | `"rivault-vlm-static"` | Vision model name. |
| `image_param` | `str` | `"image"` | Config key holding the image path/URL. |
| `prompt` | `str` | mode-specific default | Override the vision prompt. |
| `max_chars` | `int` | `2000` | Truncation limit for text outputs. |
| `api_base` | `str` | env `OPENAI_BASE_URL` or `R4_GATEWAY_URL` | VLM endpoint URL. |
| `api_key` | `str` | env `OPENAI_API_KEY` | API key. |
| `timeout_sec` | `float` | `60.0` | HTTP timeout. |

#### Image Resolution Order

1. Explicit config key (`config[image_param]`).
2. First upstream `input_key`.
3. Parent step's `extra["image_source"]`.

#### Outputs

| `StepResult` field | Content |
|---|---|
| `output_keys` | S3 key of `perception.json`. |
| `extra["perception"]` | Full `VisionPerception` dict. |
| `extra["mode"]` | Mode used. |
| `extra["dense_description"]` | Dense text (when mode produces it). |
| `extra["caption"]` | Caption text (when mode produces it). |
| `extra["ocr_text"]` | Concatenated OCR text lines. |
| `extra["object_labels"]` | List of detected object labels. |

---

### `OCRStep` — OCR convenience wrapper

| Field | Value |
|---|---|
| **Entrypoint** | `umrs.pipeline_sdk.templates.ocr_step:OCRStep` |
| **Purpose** | Shorthand for `VisionStep` with `mode=ocr`. |

Configuration and outputs are identical to `VisionStep` with `mode`
forced to `"ocr"`.

---

## Step Routing

When a workflow mixes steps that target different model types (e.g. vision +
text), the **StepRouter** resolves each step independently using capability
hints declared on the `StepSpec`.

### Routing Hints (in `workflow.json`)

| Field | Type | Description |
|---|---|---|
| `requires` | `list[str]` | Capability tags the endpoint must satisfy. |
| `model` | `str` | Pin a specific model name. |
| `lora` | `str` | LoRA adapter name (passthrough). |
| `model_type` | `"llm"` \| `"vision"` \| `"embedding"` | Coarse model class. |
| `routing_hints` | `dict` | Free-form bag (e.g. `{"owner": "system"}`). |

### Known Capability Tags

| Tag | Meaning |
|---|---|
| `vision` | Endpoint supports vision/multimodal inputs. |
| `tool_calling` | Endpoint supports function/tool calling. |
| `structured_output` | Endpoint supports structured JSON output. |
| `embedding` | Endpoint is an embedding model. |
| `gpu` | Informational; always satisfied. |

Unknown tags are accepted with a warning — they do not break the workflow.

### Resolution Order

1. **Mock mode** (`UMRS_STEP_ROUTER_MOCK=1`) — static response for tests.
2. **Explicit config** — step already pins `api_base`; router does not touch it.
3. **Registry match** — model registry + endpoint registry lookup by capability + type.
4. **Env fallback** — `OPENAI_BASE_URL` / `R4_GATEWAY_URL` (single-model workflows only).

If a step declares `requires` but no match is found in (1)–(3), the step
fails with `StepRoutingError` rather than silently falling back. This is
the **strict-mode invariant**.

See [STEP_ROUTING.md](STEP_ROUTING.md) for the full routing contract.

---

## Composing a Workflow

### Example: Scientific Figure Understanding

```json
{
  "name": "scientific-figure-understanding",
  "version": "0.1",
  "steps": [
    {
      "name": "vision_dense",
      "entrypoint": "umrs.pipeline_sdk.templates.vision_step:VisionStep",
      "config": { "mode": "dense" }
    },
    {
      "name": "vision_ocr",
      "entrypoint": "umrs.pipeline_sdk.templates.vision_step:VisionStep",
      "config": { "mode": "ocr" }
    },
    {
      "name": "reason",
      "entrypoint": "umrs.pipeline_sdk.templates.llm_step:LLMStep",
      "depends_on": ["vision_dense", "vision_ocr"],
      "config": {
        "model": "rivault-llm-static",
        "system_prompt": "You are a scientific figure analyst.",
        "prompt_template": "Describe the figure: {{ steps.vision_dense.dense_description }}\nText in figure: {{ steps.vision_ocr.ocr_text }}",
        "output_schema": {
          "type": "object",
          "properties": { "finding": { "type": "string" } }
        }
      }
    }
  ]
}
```

### Example: Custom Step with Built-in LLM

```json
{
  "name": "custom-pipeline",
  "version": "1.0",
  "source": { "type": "UPLOADED" },
  "steps": [
    {
      "name": "preprocess",
      "entrypoint": "steps/preprocess.py:Preprocess",
      "config": { "target_size": 1024 }
    },
    {
      "name": "summarize",
      "entrypoint": "umrs.pipeline_sdk.templates.llm_step:LLMStep",
      "depends_on": ["preprocess"],
      "requires": ["tool_calling"],
      "model_type": "llm",
      "config": {
        "prompt_template": "Summarize: {{ steps.preprocess.text }}"
      }
    }
  ]
}
```

## Writing a Custom Step

Implement `Step.run()` and reference it from `workflow.json`:

```python
from umrs.pipeline_sdk import Step, StepContext, StepResult

class MyStep(Step):
    name = "my_step"

    def setup(self, config: dict) -> None:
        self.threshold = config.get("threshold", 0.5)

    def run(self, ctx: StepContext) -> StepResult:
        out_keys = []
        for key in ctx.input_keys:
            data = ctx.download_bytes(key)
            result = self._process(data)
            out_key = f"processed/{key}"
            ctx.upload_bytes(out_key, result)
            out_keys.append(out_key)
        return StepResult(output_keys=out_keys)
```

### `StepContext` Helpers

| Method | Description |
|---|---|
| `download_bytes(key)` | Download S3 object as bytes. |
| `download_numpy(key)` | Download `.npy` file as NumPy array. |
| `upload_bytes(key, data)` | Upload bytes to S3; returns `s3://` URI. |
| `upload_numpy(key, arr)` | Upload NumPy array as `.npy`. |
| `head_object(key)` | Return S3 HEAD metadata. |
| `report_progress(pct, msg)` | Emit progress update (0–100%). |

### `StepResult` Fields

| Field | Type | Description |
|---|---|---|
| `output_keys` | `list[str]` | S3 keys produced (become next step's `input_keys`). |
| `embeddings` | `ndarray` | Optional embedding matrix. |
| `checksums` | `list[str]` | Per-key checksums for idempotency. |
| `metadata` | `list[dict]` | Per-key metadata. |
| `extra` | `dict` | Arbitrary key-value bag for downstream steps. |

## Pipeline Builder (Programmatic API)

As an alternative to writing `workflow.json` by hand, use `PipelineBuilder`:

```python
from umrs.pipeline_sdk.builder import PipelineBuilder

workflow = (
    PipelineBuilder("my-analysis")
    .add_step("preprocess", "steps/preprocess.py:Preprocess")
    .add_step("embed", "steps/embed.py:Embed", depends_on=["preprocess"])
    .add_step("search", "steps/search.py:Search", depends_on=["embed"])
    .set_execution_mode("IN_PROCESS")
    .set_source("/path/to/steps/")
    .build()
)
```

## Legacy Template Registry

The `TemplateRegistry` (from `umrs.pipeline_sdk.templates`) provides a
catalog of parameterized `PipelineTemplate` objects — pre-built
`workflow.json` skeletons that users fill in with parameters. This
predates the `Step`-based composable model and is retained for backward
compatibility.

Built-in legacy templates: `image_embedding`, `text_analysis`,
`defect_detection`, `video_analysis`, `custom_pipeline`.

## Agent Workflow Tool Identifiers

Agent workflow templates (under `r4/src/r4/agent/templates/`) use a
separate **dotted tool identifier** scheme. Each workflow step specifies
a `"tool"` field that the agent runtime resolves at execution time.

### Built-in Tool Names

| Tool | Purpose | Used By |
|---|---|---|
| `llm.chat` | Send a prompt to the platform LLM endpoint. | `literature_review`, `log_triage`, `simulation_summarise` |
| `rag.search` | Search a RAG corpus (query + optional filters). | `literature_review` |
| `files.fetch` | Download a single file via presigned URL. | `simulation_summarise` |
| `files.fetch_many` | Download multiple files in batch. | `log_triage` |
| `artifact.show` | Render or display a stored artifact. | `simulation_summarise` |

### Template Step Format

Each step in an agent workflow template is:

```json
{
  "id": "step-name",
  "tool": "llm.chat",
  "args": {
    "prompt": "Summarize the following: {{ steps.prev.output }}",
    "model": "rivault-llm-static"
  }
}
```

### Tool vs Step Entrypoint

| Layer | Identifier | Example | Resolution |
|---|---|---|---|
| **Agent templates** | Dotted tool name | `llm.chat` | Agent runtime dispatches to Gateway endpoint |
| **Pipeline SDK** | Python entrypoint | `umrs.pipeline_sdk.templates.llm_step:LLMStep` | UMRS imports and runs the Step class |

Agent tool names are thin wrappers: `llm.chat` calls the same LLM
endpoint as `LLMStep`, but without the pipeline SDK's S3 I/O,
checkpointing, or structured output validation. Use `LLMStep` for
durable pipeline execution; use `llm.chat` for lightweight agent
workflows.

### Step Routing (Capability-Based)

Both agent tools and pipeline steps are routed to endpoints by the
`StepRouter` (`umrs.server.step_router`). Resolution order:

1. **Explicit config** — step already pins `api_base` / `api_key`
2. **Registry match** — match `requires` capability tags + `model_type`
   against registered endpoints
3. **Env fallback** — `OPENAI_BASE_URL` or `R4_GATEWAY_URL`

See [STEP_ROUTING.md](STEP_ROUTING.md) for the full routing algorithm.

## Related Documentation

| Document | Purpose |
|---|---|
| [STEP_ROUTING.md](STEP_ROUTING.md) | Per-step routing contract and resolution order. |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | Platform invariants and component topology. |
| [PLATFORM_CONTRACT.md](PLATFORM_CONTRACT.md) | What the platform guarantees unconditionally vs. site-dependent. |
| [GATEWAY_SIDECAR_BOUNDARY.md](GATEWAY_SIDECAR_BOUNDARY.md) | Auth/policy enforcement boundary for operators. |
