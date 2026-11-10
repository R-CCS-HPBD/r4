# ADR-109: SAM3.1 as `svc-vision2` beside `svc-vision`, the `ai-pro6000` partition, trigger-time endpoints and a routed-endpoint socket for uploaded steps

**Status:** Accepted (2026-11-04).
- **Maintainer direction.** The maintainer asked that a workflow's vision model serving job start when the workflow is triggered, and end when the user asks or when the Slurm time limit ends. On the gap analysis they answered "yes and go for your recommendations", and they added:
  - SAM3.1 is a general vision model, so serve it as a service model `svc-vision2` in the same Slurm job as the current `svc-vision`;
  - users' custom models (for example HMAE) run in a self-service image ([ADR-110](110-self-service-step-images.md));
  - include the `ai-pro6000` partition.
- **Agent's part.** The agent drafted this ADR and chose the details under the standing delegation, marked *(agent decision)*.

**Date:** 2026-11-04
**Relates to:**
- [ADR-074](074-uploaded-step-code-runs-sandboxed.md): D-5 phase 2 (the per-step egress to the routed endpoint) is decided here, as D-5. Amendment 2's list of four step env names grows by one.
- [ADR-106](106-service-model-tier.md): D-1 gains the alias `svc-vision2`; the D-2 placement gains co-served entries and `ai-pro6000`.
- [ADR-078](078-endpoint-access-scopes.md) / [ADR-080](080-one-access-guard-shareable-resources.md): endpoint access lists, which the step router must honour.
- [ADR-076](076-decision-model-role.md): the relay.
- [ADR-110](110-self-service-step-images.md): user models run in the step, not as endpoints.
- Wave ledger (186).

## Context

Verified 2026-11-04 at parent `f954dede2`, umrs pin `f14aaf34`. Code lines were surveyed at umrs `f63dba59` and spot-checked at the pin `f14aaf34` (`step_router.py:611`, `rivault_launcher.py:178-185`, `slurm_step_executor.py:406`, `endpoint_registry.py:869`, `endpoint_relay.py:109`).

**Live receipts (rccs, 2026-11-04, demo project `example-project-c`).**
- A demo vision workflow (`wf-<id>`) failed: "Step '<step>' requires capabilities=['vision'] model_type='vision' but no matching active endpoint was found."
- A second demo vision workflow (`wf-<id>`) reached the lane. Its step code calls `${BENTOML_URL:-http://localhost:11434/vision}` as a literal URL, so it could never have reached a service. The agent cancelled it so it would not hold a 24 h GB10 slot.
- `r4 model list --type vision` on rccs lists no SAM3 or HMAE entry.

**What exists.**
- **The router selects but never provisions.**
  - `umrs/src/umrs/server/step_router.py:564-615` resolves in this order: mock, then `config.api_base`, then registry capability match, then env fallback. With no match it raises `StepRoutingError` (`:596-612`).
  - `_find_endpoint` (`:965-992`) scans only the run project's READY/DEGRADED rows. It never calls `select_shared_endpoint` (`orchestrator/endpoint_registry.py:869`, at the pin), so ADR-078 shared and system endpoints are invisible to steps.
  - `workflow_runner.py:1176-1192` fails the step at once.
- **A vision endpoint can be provisioned.**
  - `api/endpoints.py:1575-1607`: `model_type: vision` gives backend `bentoml`, kind `bentoml_vision`.
  - `:1670-1689`: profiles `harness vision-bentoml`.
  - `:1724-1777`: readiness probe on `/readyz`.
  - The BentoML service and its SAM3, HMAE and HF adapters live in `umrs/src/umrs/vision_service/`. The compose service is in `mini-rivault-llm/serve-model.yaml:600-685`. The image is built from `vision_service/Dockerfile` (aarch64, torch cu130) or loaded from `vision_tarball` (`serve-model.slurm:1210-1226`).
- **LLM jobs refuse the vision sidecar on purpose.** `server/rivault_launcher.py:178-185`: "LLM / embedding jobs are served by vLLM only and must never start the BentoML vision sidecar".
- **Lifecycle already matches the request.**
  - `r4 endpoint down` tears an endpoint down (`api/endpoints.py:3636`).
  - The janitor extends a READY endpoint while its Slurm job is alive (`orchestrator/janitor.py:548-607`), so an endpoint with no idle TTL lives until its job's `--time` (rccs `scheduler.time_limit` 23 h).
  - The relay (`server/endpoint_relay.py:93-112`, `wait=False` since #720) is the only owner of service-tier lifetime (ADR-106 D-3).
- **Uploaded steps have no network.**
  - The pod runs `--network=none` (`server/slurm_step_executor.py:402-414`).
  - `_apply_step_routing` drops `api_key`, `headers` and `_routing` for uploaded steps (`workflow_runner.py:1091-1115`). The remote lane also drops the router's `api_base`/`model` (`remote_slurm_step_executor.py:537-561`).
  - ADR-074 D-5 deferred "a per-step egress proxy to the routed endpoint only" to phase 2. Nothing implements it.
- **`svc-vision`.**
  - It is Qwen3.6-35B-A3B-NVFP4 on vLLM, one GB10, `VLLM_GPU_MEM_UTIL: "0.45"`, profiles `harness vllm litellm-db` (`umrs/deploy/rccs/pool-rccs.yaml:247-266`).
  - An idle rccs GB10 offers about 100–121 GiB (ADR-106 D-2 refinement), so roughly half the unified memory is unused.
  - SAM3.1 weights are 3.3 GB at `<models-dir>/sam3.1`; HMAE weights (a few hundred MB) sit beside them. No `umrs-vision` image archive is staged in `<models-dir>/images/`.
- **`ai-pro6000`** (`sinfo`/`scontrol`, 2026-11-04):
  - partition `ai-pro6000`: **aarch64**, two discrete 96 GB RTX PRO 6000 Blackwell GPUs per node, exclusive allocation.
  - There is no partition named `ng-pro6000`.
  - The step lane uses one partition, `ng-dgx-m3`. On 2026-11-04 a long-running job held both its nodes and a demo project's steps waited about 2 h.

## Decision

**D-1. `svc-vision2` is SAM3.1.** It is a general-purpose segmentation service model: role alias `svc-vision2`, registry `model_type: vision`, backend BentoML, adapter `sam3`, weights from site storage (`sam3_model_path`). ADR-106 D-1's alias set gains it. Its role probe (ADR-106 D-7) is one segmentation of a fixed bundled image with at least one mask returned. HMAE is not a service model; it runs in the user's step image (ADR-110).

**D-2. Co-served entries: one Slurm job, two endpoints.**
- **Roster.** A roster entry may declare `co_serve:`, a list of extra servings that share its job. The `svc-vision` entry gains `co_serve: [{name: sam3.1, model: SAM3.1, aliases: [svc-vision2], model_type: vision, adapter: sam3}]`. Its profiles become `harness vllm vision-bentoml litellm-db`.
- **The sidecar rule.** `_resolve_profiles_for_model` keeps refusing the vision sidecar for plain LLM jobs. It adds `vision-bentoml` only when the entry declares a vision `co_serve`.
- **Registry.** The job registers one row per serving: `svc-vision` on the vLLM port, `svc-vision2` on the BentoML port (`BENTOML_VISION_DOCKER_PORT`). Both carry the same `job_group` (the Slurm job id).
- **Lifecycle.**
  - Each row has its own role probe and READY state; a failed SAM3 probe does not fail the VLM.
  - Lifecycle actions (roll, drain, teardown, renew) act on the job, so both rows move together.
  - The relay counts a co-served alias as present only while its row is READY. A job whose sidecar never comes up is rolled like any failed replica.
- **Memory budget** *(agent decision)*:
  - On a GB10, vLLM keeps `0.45`. The BentoML service gets `VISION_MAX_BATCH=1` and `PYTORCH_CUDA_ALLOC_CONF` capped, with a budget of at most 16 GiB.
  - On `ai-pro6000` (D-3), vLLM takes GPU 0 and BentoML takes GPU 1 (`CUDA_VISIBLE_DEVICES` split per service), with no shared budget.
- **Wider use.** Co-serving is generic; any entry may use it later. Today only `svc-vision` does.

**D-3. `ai-pro6000` joins the rccs site for services and the step lane.**
- **Partition table.** The rccs site config gains a per-partition GPU request style, because the GB10 partitions reject `--gres`:
  - `ng-dgx-*: {gpu_request: none}`
  - `ai-pro6000: {gpu_request: gres, gres: "gpu:PRO6000-96GB:2", exclusive: true}`
- **Service tier.** `ai-pro6000` is appended to the `svc-vision` candidate list after the GB10 partitions. The job goes to the earliest-starting candidate, one partition per job (the rccs `job_submit` rule).
- **Step lane.** `step_executor.partition` becomes a candidate list `[ng-dgx-m3, ai-pro6000]` submitted the same way.
  - `ai-pro6000` is exclusive, so a step there holds a whole 2-GPU node. It is listed second and gets the job only when `ng-dgx-m3` is busy *(agent decision: an idle lane beats an idle node)*.
  - Steps that ask for more than one GPU (`routing_hints.gpu_count: 2`) go only to `ai-pro6000`.
- **Arch.** The nodes are aarch64 like the GB10s, so the same `-aarch64.tar` archives load. The GPUs are discrete sm_120, not the GB10's unified sm_121. Every image used there (vLLM, vision, the step image) needs a first-launch receipt on `ai-pro6000` before that partition enters a candidate list.

**D-4. A trigger starts the endpoints its steps need.**
- **Before launch.** Before `registry.launch`, the workflow handler resolves every step that declares `requires`, `model_type` or `model`. For each one with registry candidates but no READY or PROVISIONING endpoint the caller may use (own project, ADR-078 shared, or system):
  - **Service alias (`svc-*`).** The handler asks the relay for one replica now. Entries with `service.replicas: 0` are on-demand services; the relay provisions them as usual.
  - **Any other registered model.** The handler calls `provision_endpoint(..., tier="user", ttl_sec=0, wait=False)` with `requesting_user` = the triggering user, `project_id` = the run's project and `access: [project:<run project>]`. This is the same call the relay makes.
  - Requests are deduplicated on existing PROVISIONING rows.
- **Steps wait for their endpoint.**
  - The router honours ADR-078 sharing (`select_shared_endpoint`). It treats PROVISIONING/STARTING as "wait", not "fail".
  - Each step waits up to a bounded clock (default 45 min: queue plus model load; site override `step_endpoint_wait_sec`), then fails by name.
- **Lifetime is unchanged.** An endpoint started by a trigger runs until the user's `r4 endpoint down` or its Slurm `--time`. It is not tied to the run's end, so a following run reuses it warm. Its metadata records `started_by_run=<execution_id>` so `r4 endpoint list` shows why it exists.

**D-5. Uploaded steps reach their routed endpoint through a socket (ADR-074 D-5 phase 2).**
- **The pod.** It keeps `--network=none`.
- **The forwarder.** For a step whose routing decision names an endpoint, the job script starts a per-step forwarder before the pod and stops it after.
  - It is trusted code from the platform step image, run outside the step container.
  - It listens on a Unix socket in the step's reply dir, mounted read-write at `/run/umrs/endpoint.sock`.
  - It forwards only to the `host:port` the server put in the step payload, and adds that endpoint's credential itself, so the step never sees a key.
  - It allows only the endpoint kind's paths: `/v1/*` for vLLM, `/vision/*` and `/readyz` for BentoML.
  - It caps request and response bytes and the total per step, and it logs bytes and status.
- **Env.** One env name is added to ADR-074 Amendment 2's list: `UMRS_STEP_ENDPOINT_SOCKET`.
- **SDK.** `umrs.pipeline_sdk` gains `ctx.endpoint_http()` (an httpx client over the socket) and a BentoML client that uses it.
- **No routed endpoint.** A step without one has no socket, and a network call fails by name as before.

**D-6. The demo vision workflows follow D-1/D-5 and ADR-110.**
- The SAM3 segmentation steps declare `requires: [vision]`, `model: svc-vision2` and drop `bentoml_url`. They call SAM3 through D-5.
- The HMAE steps run the model inside the step from the user's image on a GPU step (ADR-110), with `routing_hints.gpu: true`.
- Both workflows live in the private demo repository. Their rewrite is a rollout leg, not platform code.

### Why

- The maintainer's lifecycle (start on trigger, end on user request or walltime) is what the janitor and relay already do; only the start is missing. D-4 adds the start with the call the relay already uses, so endpoints keep one owner of lifetime (ADR-106 D-3).
- SAM3.1 is useful to everyone and small (3.3 GB). A GB10 serving `svc-vision` leaves about half its memory idle. Co-serving gives a second service model for no extra node, and the relay keeps both up.
- Without D-5 the rest only moves the failure from routing to the network. A Unix socket keeps `--network=none` and the no-credential rule (ADR-074 D-4/D-5) while giving the step exactly one destination.
- `ai-pro6000` doubles the GPU node types the lane can use. 2026-11-04 showed what a single-partition lane costs: one external 24 h job held it.

## Consequences

**Positive.**
- The demo workflows' SAM3 steps and any user's segmentation steps get a warm service.
- The first workflow triggered after a quiet period starts its own endpoint instead of failing.
- Uploaded steps can call any routed model (VLM, OCR, embeddings) without network access or keys.
- The step lane gains a second partition.

**Cost / negative.**
- One more serving process on the `svc-vision` job: a sidecar failure triggers a roll of the job, which also briefly interrupts `svc-vision`.
- A trigger can start a user-tier endpoint that runs until walltime when nobody stops it (≤ 23 h of one node). `r4 endpoint list` shows `started_by_run`; quotas are ADR-099's.
- The forwarder is new trusted code on compute nodes and needs its own tests (path allow-list, byte caps, single destination).
- `ai-pro6000` is exclusive: a small step there wastes a 2-GPU node while it runs.

**Not in scope.**
- Image self-service (ADR-110).
- Autoscaling on load (ADR-106 rejected it).
- Tying an endpoint's end to a run's end. This could be added later as an opt-in `stop_with_run`.

## Alternatives Considered

- **A platform template step on the front that calls the vision broker (`api/vision.py`).** Rejected: one demo project's pipeline would become platform code, and other users' vision steps would each need one.
- **Load SAM3 inside every step (no service).** Rejected for SAM3: every step pays the model load, and a general model belongs in the service tier. Kept for user models (ADR-110).
- **A separate Slurm job for `svc-vision2`.** Rejected: a whole GB10 for a 3.3 GB model, while the `svc-vision` node has about 50 GiB idle. It stays possible: drop `co_serve`, add an entry.
- **Network egress by rule (pasta/slirp with a destination filter).** Rejected: it gives the pod a network stack and the credential would have to enter the pod. The socket keeps both out.
- **Always provision user-tier at trigger, never via the relay.** Rejected for `svc-*`: it would bypass the replica controller and its rolls.

## Rollout-status (2026-11-04)

Nothing landed yet. The slices, each its own wave:

| Slice | Decision | Content | Live leg |
|---|---|---|---|
| V1 | D-3 | rccs partition table + `gpu_request` (umrs site config, step executor and serving submit); `ai-pro6000` not yet in any list | converge; `sbatch --test-only` on both partition kinds |
| V2 | D-3 | first-launch receipts on `ai-pro6000` (platform step image, vLLM, vision image), then add it to the lists | owner-dispatched converge |
| V3 | D-1, D-2 | `co_serve` in the roster and relay, `job_group` rows, sidecar profile rule, vision image staged by the converge, SAM3 role probe | `svc-vision2` READY on rccs next to `svc-vision` |
| V4 | D-4 | trigger-time ensure, the router waits on PROVISIONING and honours ADR-078 sharing, `started_by_run` metadata | a cold trigger starts its endpoint and the step runs |
| V5 | D-5 | forwarder, socket mount, `UMRS_STEP_ENDPOINT_SOCKET`, SDK client, posture tests | an uploaded step calls `svc-vision2` with `--network=none` intact |
| V6 | D-6 | demo vision workflow rewrite (private repo) after ADR-110 I3 | receipts for both workflows in `example-project-c` |

**Owner legs:** check that the SAM3.1 licence allows service use on the platform; converge dispatches.

## Rollout-status (2026-11-05)

- **V4 (D-4): landed repo-side** (umrs `401fa3a8`, ledger (192)); not deployed.
  - Trigger-time ensure (`server/step_endpoint_ensure.py`): the relay for service models, user tier otherwise. It runs only for a verified project.
  - The router uses ADR-078 sharing and waits on pending rows.
  - Site key `step_executor.endpoint_wait_sec` (default 2700 s). D-4's `step_endpoint_wait_sec` is this key.
  - 32 tests.
- **V3 (D-1/D-2): landed repo-side** (umrs `48a35e5f`, ledger (195)); live-inert.
  - Roster `co_serve`, the sidecar rule, co-served registry rows (`<primary>::<name>`, `job_group`), lifecycle on the primary, the relay roll on a failed sidecar, and role probe `segment`.
  - The `pool-rccs.yaml` block stays commented until the vision image is staged.
- **V1 (D-3): landed repo-side** (umrs `28ab35bb`, ledger (197)); live-identical.
  - `PartitionProfile.gpu_request|gres|exclusive`, applied per candidate in the serving submit.
  - Step-lane candidate lists probed through the helper's `test-only` verb and ranked by `partition_choice.choose()`.
  - `routing_hints.gpu_count`.
  - `ai-pro6000` profile in both rccs site files, in no list.
- **V2, V5, V6:** open.

## Rollout-status (2026-11-06)

- **V4, V3, V1: deployed on all four fronts** by the staged converge at main `7843d392a` (umrs `684ce031`, which carries `401fa3a8`, `48a35e5f` and `28ab35bb`; runs 42123180712 → 42131737885, all success). Ledger (212).
  - V3 stays inert: the `co_serve` block is still commented.
  - V1 changes nothing live: `ai-pro6000` is in no list.
- **Next:** V2 (first-launch receipts on `ai-pro6000`), V5 (step endpoint socket), the V3 live leg (stage the vision image, uncomment, roll).

## Rollout-status (2026-11-06, later) — V5 landed repo-side; D-5 wording corrected

- **V5 (D-5): landed repo-side** (umrs `1c1e5bdd`, ledger (213)); not deployed.
  - **Server.** The runner binds the routed endpoint (decision with `endpoint_id` + `api_base`; kind from the new `RoutingDecision.serving_backend`) to the remote lane only, in executor metadata. The key is hidden from every worker's metadata.
  - **Credential.** It is written as `<wd>/endpoint.json` through the helper `put` (0600 in the 0700 workdir; new allowed name, capped at 64 KiB). It is mounted only into the forwarder.
  - **Forwarder.** `python -I -m umrs.server.step_endpoint_forwarder` runs in the platform step image with `--network=host`. It serves only the Unix socket and opens no TCP listener.
  - **Step pod.** It keeps `--network=none` and mounts `reply/endpoint.sock` at `/run/umrs/endpoint.sock`. `UMRS_STEP_ENDPOINT_SOCKET` is added only when a forwarder runs. The job script waits up to 60 s for the socket, else exits 78 by name.
  - **Caps** (defaults, site keys `step_executor.endpoint_max_{request,response,total}_bytes`): request 32 MiB, one response 256 MiB, 4 GiB per step.
  - **Forwarder rules.** GET/POST only, plain paths, no CONNECT. Client Authorization, Host and hop-by-hop headers are dropped and the credential added. A response that carries the credential is cut or answered 502. The log has method, path, status and bytes only.
  - **Verified.** 80 new tests (forwarder + SDK over a real Unix socket and fake upstream; job script, lane, runner, router, helper, site env). Step-lane suites: 854 passed in the lane, 579 on the coordinator's tree. An opt-in real-podman test (5.8.9, SELinux enforcing): the step got 200 through the socket, 403 on a disallowed path, no direct TCP, never saw the key.
- **D-5 corrected (dated, agent):**
  - **BentoML allow-list.** The BentoML vision sidecar serves its tasks at `/infer/submit|status|get`, so the allow-list for kind `bentoml` is `/infer/`, `/vision/`, `/readyz` (not only `/vision/*` and `/readyz`).
  - **SDK.** `ctx.endpoint_http()` is a standard-library client (`http.client` over AF_UNIX), because the step image ships no httpx. `ctx.endpoint_httpx()` is offered when httpx is installed, and `ctx.endpoint_vision()` submits, polls and fetches BentoML tasks.
- **Open.**
  - **Live leg.** The HPC code sync must bring the helper's `endpoint.json` name to the cluster, and the step image must be rebuilt from the pin (both happen in a main-tip staged converge). Then the receipt: an uploaded step calls `svc-vision2` with `--network=none` intact.
  - **Unchecked on GB10 (podman 4.9.3 on Lustre):** a Unix socket file under `/lvs0`, and `--network=host` with `--cgroups=disabled`. If Lustre refuses the socket, the fallback is a node-local socket directory.
  - **SAM3 image input.** The vision service takes images only as s3 or presigned URLs. A staged step can pass only images the sidecar can read itself; inline image bytes are a service change (V6).

## Rollout-status (2026-11-08) — V6, HMAE half

- **The HMAE workflow follows D-6.** It was rewritten for the staged lane (private repository): one GPU step runs the HMAE model inside the step from an allow-listed image (the model code ships in the bundle; the weights are a weights-only state dict staged from the project's bucket), one CPU step writes the report. No vision endpoint is involved.
- **Image.** `umrs-step-torch:<platform sha12>-<lock sha8>` (torch cu130, COPY-only on the platform step image) is allow-listed on rccs (umrs `4187cc05`), until ADR-110 user images open there (I0).
- **Receipts.** Local CPU run on the project's six test slices: the masked loss matches the training loss. The rccs receipt in `example-project-c` is recorded in wave ledger (240).
- **The segmentation workflow half** still waits for the V3 live leg (the vision image staged, `svc-vision2` serving); its workflows are owner-only until then.

## Related

- [ADR-074](074-uploaded-step-code-runs-sandboxed.md), [ADR-076](076-decision-model-role.md), [ADR-078](078-endpoint-access-scopes.md), [ADR-080](080-one-access-guard-shareable-resources.md), [ADR-106](106-service-model-tier.md), [ADR-110](110-self-service-step-images.md)
- `docs/development/rccs-step-lane-spec-2026-09-26.md` (pod posture, §7)

*(2026-11-06: internal addresses, paths and group ids replaced with placeholders for the public export (repo-rename plan P5b-2); the values live in r4-ops.)*

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
