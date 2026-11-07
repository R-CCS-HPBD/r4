# ADR-047 — Batch-inference, fine-tune, and model-eval as executor-driven durable jobs

**Status:** Proposed (EXEC-1/EXEC-2 landed 2026-09-12; FINE shape landed, dispatch gated — see the dated rollout blocks below)
**Date:** 2026-09-12
**Relates to:** ADR-002 (durable job/attempt), ADR-036 (loop hosting), ADR-044
(off-books taxonomy — these three firms' executor lanes ARE the floors this
runs from), ADR-034 (A2L conservation), WP-09 round 2.
**Principles:** P4 (deployment reality), P2 (portability), P3 (provenance),
P5 (gate before spend).

## Context

WP-09 round 1 resolved "whose compute leaves durables?" with a three-lane
taxonomy. Second basin: three invoked-for-platform classes that spend minutes
to days on GPU today outside the durable fabric because no executor exists for
them yet:

- **Batch inference** — large prompt sets against the same deployed model
  (eval sweeps, RAG answer sweeps, dataset labelling); today users wrap
  ad-hoc vision/LLM calls in a driver script on a login node — no durable
  queueing, no attempt recovery, no per-item manifest.
- **Fine-tune** — adapter/LoRA/SFT passes grounded in the same upload paths
  knowledge_ingest already stages; today out-of-frame of every durable
  reporting class.
- **Model eval** — benchmark jobs grading a model on a held-out suite before
  it lands in the catalog (a deterministic first-release interlock, not now).

All three spend the same way (GPU partition time + model weights + corpus
bytes), and all three already have the manifestation machinery available
(attempt manifest, attempt credentials, model pins); what is missing is the
executor CLASS that translates what the platform calls a batch-infer job into
a workload on a node.

## Decision

Three new JobTypes as **executor-driven durable jobs** (ADR-044 lane 1):

| Field | `BATCH_INFER` | `FINE_TUNE` | `MODEL_EVAL` |
|---|---|---|---|
| `spec.inputs` | `prompt_ref` (s3 key or knowledge corpus selector), `output_prefix` | `dataset_ref`, `target_model_id`, `adapter_ref` | `bench_spec` (suite ref), `target_model_id` |
| `spec.model_ref` | `MODEL_ID` (catalog ref) | Base model | Evaluated model |
| Venue derivation | site.yaml ladder (same as endpoint provision: `partition`/`nodelist` + `gpu_count`) | Same ladder; typically `h200` / `mi300x` for training class memory | Same ladder |
| Executor pair | `BatchInferBackend` → wraps mini-RiVault (or vLLM batch API on the venue) | `FineTuneBackend` → wraps the training runtime profile (reserved per venue) | `ModelEvalBackend` → harness invoked as batch with eval output to S3 |
| Cancel semantics | EXECUTE cancel is half-way marker (items after tick: stopped at boundary); honest finish reason `cancelled` with count | Never mid-slot cancel; honest cancel = terminate-process on venue | Same as batch |
| Attempts | Every attempt = one Slurm submission (serialized attempts as retry) | One attempt per adapter/epoch strategy value (explicit counter claimed in spec) | One attempt per bench |
| Manifest | include input-row count, found-invalid line count, model pin, dataset pin, cluster tag, executor image ref | identical + train seed + dataset split hash + checkpoint lineage | identical + bench hash |

**Unifying laws (static, not negotiated per class):**

1. Everything written goes to the attempt's scope prefix
   (`jobs/<job_id>/attempt_<n>/`); out-of-scope writes are refused by the
   job's attempt credentials.
2. The executor NEVER invents counts; empty outputs record `rows_in=0,
   rows_out=0, error=<reason>` — never a partial truth compressed as success.
3. Cancel must be a checkpoint-terminate on the venue, translated once to the
   outer job ID (never stitch-cancel through process children).

## Phases

- **Phase DB** — Migration for the new JobTypes + spec schema
  (enumerations + versioned spec validators; manifests already shape-ready).
- **Phase EXEC-1** — `BatchInferBackend` against an already-deployed
  mini-rivault over a prompt-file fixture (live on rccs-front under the
  established test-cluster pattern).
- **Phase EXEC-2** — `ModelEvalBackend` against bench fixtures shipped in
  `tests/fixtures/eval_bench.jsonl`, cost accounting per batch.
- **Phase FINE** — `FineTuneBackend` only after a training-runtime venue pin
  arrives as a site yaml (training ≠ inference memory profile).

## Cross-cutting drains (all named, not silenced)

- **Idle-time policy** — each executor holds a TTL after first byte arrival
  (long-running jobs fail with `idle_timeout` when upstream stalls; baseline
  no-op at 900s, site per-executor tunable).
- **A2L attribution** — batch rows tag `execution_kind: batch|finetune|
  eval` to the same A2L ledger used for vision per ADR-034.
- **Attempt credentials ride out whole first leg** (STS federation per
  WP-11 currently completed).
- **Out-of-scope egress is EgressGuard** — same blocklist work-substrate as
  provision classes.

## Consequences

- WP-09 round 2 becomes three sequential executable wave-guesses instead of
  "do everything": each executor class lands with its own
  pins (`umrs/tests/test_<kind>_executor.py` + one spec-shape suite +
  sample run on a stable fixture cluster).
- Nothing changes in existing classes; the JobType enum grows additively.
- The failure narrative stays consistent with pre-release: no un-verified
  executor flags a run released when the overall job failed.

## Verification

- Design pins (this wave): none — it's a design wave, hardened by ADR edit
  alone.
- Execution waves produce their own per-class pins; the pin architecture
  mirrors the existing per-job-type pattern (unit executor + spec shape +
  exception-chain translation, never "i think it works").

## Rollout-status (2026-09-12, Phase DB landed)

- **JobTypes registered Honest-blocked** (umrs `77964f49`):
  `BATCH_INFER` / `FINE_TUNE` / `MODEL_EVAL` land in the enum with
  backoff rows, and ALL THREE enter `_UNDISPATCHABLE_JOB_TYPES` up-front:
  spec valid at the API reach-in, durable attempt impossible until the
  matching executor ships — provenance read as typed code
  (`jobs.bad_request; x-platform-error-code; "not implemented on this
  deployment…"` message verbatim pre-cooked).
- Pins lock the legs: enum membership; 400 gate per type with the typed
  header and dynamic job_type placeholder.
- The **spec validators named in Phase DB are deferred to EXEC-1** (the
  validator exists to save you from early-lived bad shapes; the executor
  ships and the validator's first use is its own wave — having a strict
  validator waiting on an unimplemented executor would be decoration, not
  defense).

## Rollout-status (2026-09-12, EXEC-1 landed)

- **`BatchInferBackend` landed** (umrs `c5061d43`): `BATCH_INFER` graduates
  out of `_UNDISPATCHABLE_JOB_TYPES`; job runtime dispatches + monitor
  drives terminal via `BatchInferStatusChecker` (rows_total/done/ok/failed
  metrics + attempt-scope s3_key in one shape). Cancel = row-boundary
  checkpoint-terminate with rows-done counters intact.
- **Safety-on-identity wire**: row transport hits the endpoint's
  `/v1/chat/completions` directly (the same `llm.py` discovery chain —
  READY/DEGRADED-filtered, never a new dynamo or the discovery-dialect
  invent route).

## Rollout-status (2026-09-12, EXEC-2 landed)

- **`ModelEvalBackend` landed** (umrs `9fe2233b`): `MODEL_EVAL` graduates
  out of `_UNDISPATCHABLE_JOB_TYPES`; the composite monitor drives terminal
  via `ModelEvalStatusChecker` with counter evidence
  (`rows_total/done/ok/failed` + `pass_count/fail_count` + s3_key — never
  a synthesized verdict and never a smudged percentage). Every bench row
  is validated shape-exact at submit (`prompt` + `expected` strings),
  metrics are named deterministically
  (`exact_trim`/`contains`/`regex`, scoring 1.0/0.0), and cancel semantics
  mirror batch (row-boundary, counters preserved). Only `FINE_TUNE`
  remains behind the gate until the training-venue pin arrives (training
  phase-F's venue memory profile is not inference-shaped).
- 10 codebase pins; internal suites all green; spec validation strict
  (model+prompts required XOR; inline cap 500; continue_on_error default
  true). **What stays deferred on purpose**: S3 scoped-presign row-write
  path (currently boto client directly at attempt prefix — the presign
  server loop stays the deferring; STS renewal on attempt lifetime is
  drawn separately in WP-11 rather than reused inside this executor until
  the sidecar's own wave rolls out) and the live-cluster live leg
  (test cluster run is the leg of the next wave).

## Rollout-status (2026-09-12, Phase FINE — venue pin recorded, shape landed, gate stays)

- **Venue DECIDED by the owner**: `ai4s` (GB200 NVL4, 4 × B200 @ 173.2 GiB
  each per node) — the site profile layer carries it; the training runtime
  is NOT inferenced-shape so Phase FINE launches its own script chain
  there (never forced through the mini-rivault).
- **`FineTuneBackend` shape landed** (umrs `7c68cd21`): strict spec chain
  (LoRA-only v1 — rank ∈ {8,16,32,64}, epochs ∈ (0, 3], max_seq_len ∈
  [512, 4096], `dataset_ref` s3-only — inline training data rejected by
  design, `site` must equal `ai4s`). Status checker reports
  submitted/cancelled/failed honestly.
- **Cancel = marker, NEVER scancel masquerade**.
- **Gate stays**: `FINE_TUNE` remains in `_UNDISPATCHABLE_JOB_TYPES` until
  `deploy/ai4s/finetune-adapter.slurm` authors its wrapper leg ON the
  verified venue image (image strategy un-decided by my side; zero
  fabricated leg attempts accepted until signed off).
- Pins (6) on the spec chain lock today's non-dispatch entrance.


## Rollout-status (2026-09-13, Phase FINE wrapper leg authored repo-side — gate stays until venue verification)

- **Wrapper authored** (parent this-wave commit): `deploy/ai4s/finetune-adapter.slurm` — plain-bash
  wrapper per the RemoteSlurmBackend convention (resource directives ride the sbatch CLI, contract
  env via `--export`): validates the backend contract env, refuses silently-missing prerequisites
  (training SIF, base-model path, trainer entry, S3 creds — each a named `fatal`), stages the
  dataset host-side from `s3://` with explicit creds, runs the trainer with the adapter dir as the
  only writable bind, uploads only when `R4_FINETUNE_UPLOAD_REF` is given (otherwise artifacts stay
  on shared FS and the info file reports the path — honest either way), and records
  PROVISIONING/TRAINING/UPLOADING/COMPLETED/FAILED transitions in
  `finetune-<job>-<attempt>.info` under `JOB_INFO_DIR`.
- **Trainer authored**: `deploy/ai4s/finetune_lora.py` — env-driven, full-bf16 LoRA (peft
  `target_modules=all-linear`, alpha=2r), dataset JSONL `text` or `prompt`+`response`, adapter
  saved at END only, re-checks the backend spec bounds (drift refuses at both layers).
- **Image strategy decided**: `deploy/singularity/mini-rivault-aarch64-finetune.def` — overlay
  FROM the v6 SIF; pinned triad `peft==0.17.1 / accelerate==1.10.1 / datasets==3.6.0`; no
  bitsandbytes (aarch64-unverified; v1 is full-bf16, no quant).
- **Pins (5)**: `tests/test_finetune_wrapper_contract.py` (backend-env consumption, loud-refusal
  legs, honesty markers, trainer/backend bound-parity, def overlay contract).
- **Gate STAYS**: `FINE_TUNE` remains in `_UNDISPATCHABLE_JOB_TYPES`. Exit criteria: (1) SIF built
  at the next ai4s converge (owner action — same build host chain as v6), (2) one live smoke run
  of the wrapper chain on a small catalog model with a fixture dataset, (3) backend gated flip in
  the same wave as the smoke receipt.
- **Named follow-up** (not this wave): the backend currently maps `submitted` → attempt-success at
  submit-edge; slurm liveness (squeue polling / stale-stack reaper keyed to job lineage per
  G-P4-6) is the WP-20 venue-hygiene package's lane, not this leg's.
