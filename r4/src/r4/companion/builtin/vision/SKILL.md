---
name: vision
description: "Submits and monitors vision inference (segmentation, detection, classification) via UMRS BentoML endpoints. Use for images, CT scans, microscopy, vision models."
allowed-tools: "vision_submit vision_status endpoint_list endpoint_status endpoint_provision model_list_registry"
metadata:
  x-r4-keywords: "vision, image, segmentation, detection, bentoml, ct scan, microscopy, classify image"
---

You run vision inference through UMRS BentoML-backed task queues.

## Workflow (fragile sequence — follow exactly)

1. `endpoint_list` — confirm a vision-capable endpoint exists and is ready.
   If none: `model_list_registry` (model_type=vision) to pick a staged
   vision model, then `endpoint_provision` with model=<that model> (it waits
   by default; with wait=false, poll `endpoint_status` until ready) before
   submitting.
2. `vision_submit` — local file paths are auto-uploaded; S3 URIs are passed
   through. Record the returned task id.
3. `vision_status` — poll until the task completes; vision jobs are async
   and can take minutes. Do not resubmit on a slow poll; check status first.

## Edge cases

- "No active endpoints" means provision first — never submit against a
  hoped-for endpoint.
- Large batches: submit per-file tasks and track their ids; do not assume
  one submit handles a whole directory.
- Failures return structured status; report the error shown by
  vision_status verbatim.
