---
name: model_management
description: "Lists, inspects, and stages models in the UMRS registry across system/project/user scopes. Use for model registry, downloads, huggingface, qwen/llama, embedding or vision model staging."
allowed-tools: "list_models model_list_registry model_details model_stage"
metadata:
  x-r4-keywords: "model, models, registry, stage, download model, huggingface, qwen, llama, embedding model, vision model, model scope"
---

You manage AI models through the UMRS model registry.

## Tools

| Tool | Use | Notes |
| --- | --- | --- |
| list_models | Quick overview of available models | `model@source` targets a non-default source |
| model_list_registry | Detailed registry query with scope/type filters | scopes: system / project / user |
| model_details | Inspect one model: storage path, scope, type, status (readiness), HF id | call BEFORE provisioning |
| model_stage | Submit a durable download job | returns a job id, NOT a ready model |

## Scopes and permissions

- system: shared, read-only for non-admins (no stage/delete).
- project: shared within the project.
- user: private to the owner.

## Staging workflow (fragile — follow exactly)

1. model_list_registry / model_details — is the model already available?
   If yes, stop here.
2. model_stage — creates a durable download job
   (POST /umrs/models/stage).
3. Monitor with job_status until the job completes (attempts may restart
   after a UMRS restart — that is normal, do not re-stage).
4. Verify with model_details, THEN provision an endpoint.

## Invariants

- Models are pre-staged for inference; nothing downloads inside inference
  containers (TRANSFORMERS_OFFLINE=1). Model-download jobs are one of the
  only job classes with network egress — treat them as privileged.
