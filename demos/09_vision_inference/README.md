# Demo 09: Vision Inference (SAM3.1) & Interactive UI

Text-prompted SAM3.1 segmentation through the Platform Gateway
(`/gw/vision/*`), plus the interactive Gradio UI (`r4 vision ui`).

## What It Shows

1. `r4 model list --type vision` — vision models registered on the platform
2. `r4 vision submit <image> --adapter sam3 --config prompt=cat` —
   text-prompted segmentation; the response lists the **S3 result paths**
   (`outputs[].uri`) up front
3. `r4 vision status <task_id>` / `r4 vision get <task_id>` — task lifecycle
4. `r4 vision ui` — interactive browser UI: upload an image, type a prompt,
   pick the adapter/task, click **Run inference**, get live status, copyable
   `s3://` result paths, and output-image previews

## Quick Start

```bash
bash run_demo.sh --auto

# Launch the interactive UI at the end (blocking)
R4_DEMO_LAUNCH_UI=1 bash run_demo.sh

# Or launch the UI directly at any time
pip install gradio
r4 vision ui --adapter sam3          # http://127.0.0.1:7860
```

## Design Note

The UI runs on the **client** side and talks only to the Gateway — the
BentoML GPU container stays UI-free and offline. Results are written to
project S3 storage by the vision service; the UI previews image outputs via
the audited `/gw/files/download/` route and always displays the canonical
`s3://` object keys for downstream use (workflows, knowledge ingest, RAG).

## Environment Overrides

| Variable | Default | Purpose |
|----------|---------|---------|
| `R4_DEMO_VISION_ADAPTER` | `sam3` | Vision adapter (`sam3`, `hmae`, `huggingface`) |
| `R4_DEMO_VISION_PROMPT` | `cat` | Text prompt for promptable adapters |
| `R4_DEMO_VISION_IMAGE` | auto-resolved | Input image path |
| `R4_DEMO_LAUNCH_UI` | `0` | Set `1` to launch `r4 vision ui` from the script |

## Prerequisites

- A vision-capable endpoint (BentoML mini-RiVault with the `vision-bentoml`
  profile) — see `00_onboarding` and the SAM3.1 model staged on the site
- `gradio` installed for the UI step (`pip install gradio`)

## Detailed Step-by-Step (expected flow — asset currently WIP)

1. Vision model inventory
   ```bash
   r4 model list --type vision
   # today: shows other sites' llm rows too — server-side type filter is loose ADR-followup
   ```
2. Submit
   ```bash
   r4 vision submit <image> --adapter sam3 --config prompt=cat
   ```
   Response lists `outputs[].uri` S3 result paths.
3. Task lifecycle
   ```bash
   r4 vision status <task_id> / get <task_id>
   ```
4. Interactive Gradio UI
   ```bash
   r4 vision ui   # opens at browser: upload image, prompt, adapter/task, Run
   ```

### Requirements / caveats

- **INPUT IMAGE REQUIRED**: set `R4_DEMO_IMAGE` or add a fixture image; the
  repo currently has none bundled (track issue — add `fixtures/cat.jpg`).
- For Gradio preview to work the platform serves output images directly via
  `/gw/vision/output/<key>`.
- Requires deployed vision adapter (sam3 workflow); currently pending idle —
  demo lightpath verified correct in its workflow.json bundled revision shipped.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No input image found` | the demo has no bundled fixture + R4_DEMO_IMAGE not set | provide arg, or add fixture |
| `No vision adapter available` | the sam3 workflow adapter not provisioned | provision the adapter first (any visionmodel); type=vision list shows filtered path |
