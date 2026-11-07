---
name: platform_ops
description: "Provisions and manages inference endpoints (mini-RiVault), monitors durable jobs, and checks platform health via UMRS. Use for endpoints, deploy, serve, inference, platform health, job tracking."
allowed-tools: "endpoint_list endpoint_status endpoint_provision endpoint_stop job_list job_status job_cancel job_retry platform_health"
metadata:
  x-r4-keywords: "endpoint, provision, deploy, serve, inference, mini-rivault, rivault, platform, health, ready, job, durable, attempt"
---

You manage the R4 Platform through UMRS.

## First move

`platform_health` — verify the platform is reachable before any mutation.
Route ALL LLM traffic through the Gateway afterwards; never to vLLM /
LiteLLM directly.

## Endpoint lifecycle (low-freedom sequence — follow exactly)

1. `endpoint_list` — ALWAYS first; reuse an existing ready endpoint instead
   of duplicating.
2. `endpoint_provision` — params: model (served model name/path; never
   model@source), site=<name> to target a non-default site (source= is an
   alias), tier (user default / project / system),
   mounts (EndpointMounts JSON; read-only, targets under /mcp-servers,
   /project, /rag-index), egress_domains (comma list; empty = fully
   offline), gpu_count, node_count, partition, ttl_sec (0 = no expiry),
   wait (default true).
3. Not ready yet: poll `endpoint_status` until status is ready. If it is
   stuck in provisioning, check the backing job with job_status before
   concluding anything.
4. `endpoint_stop` when done (force=true only for wedged instances).

## Durable jobs

Job = immutable intent; Attempt = one execution try. On UMRS restart,
in-flight attempts are LOST and the job re-queues. Monitor with job_list /
job_status — inspect attempts before declaring a job stuck. Control with
job_cancel / job_retry; never submit a duplicate job to "speed things up".

## Constraints

- Endpoints run inside Singularity on HPC — respect partition/GPU limits.
- Inference containers are offline; only model downloads and guarded
  KNOWLEDGE_INGEST jobs have egress.
