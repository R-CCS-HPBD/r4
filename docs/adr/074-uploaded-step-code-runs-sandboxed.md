# ADR-074: Uploaded workflow step code runs in a sandbox, never in the UMRS trust domain

**Status:** Accepted (2026-09-25, agent decision under maintainer delegation)
**Date:** 2026-09-25
**Relates to:** ADR-027 (workflow upload authorization), ADR-043 (attempt-scoped S3 credentials), ADR-061 (sandbox profiles — this ADR is the one it requires before a front-host step sandbox exists), ADR-055 (SeaweedFS STS limits), STATE `SEC-20260925-001`, gap rows D-07 / D-12

## Context

A code map of the UMRS-server ↔ step boundary on 2026-09-25 (ledger
"(night, 4)"; three read-only reports) found that the step lanes isolate
resources, not trust:

- `_validate_step_entrypoint` imports the uploaded module inside the server
  on every lane (`umrs/src/umrs/server/workflow_runner.py`, called before the
  executor is chosen), so module-level code always runs in the server.
- The default executor is `inproc`, and a workflow's own
  `execution_mode: in_process` overrides the site executor
  (`_EXECUTION_MODE_EXECUTOR`); most demos declare it.
- Every out-of-process lane reads the step's reply with `pickle.loads` on
  bytes the step controls (`step_executor.py` subprocess / podman / pool /
  remote / router, `slurm_step_executor.py`).
- `PodmanExecutor` defaults to `--network=host`, forwards `AWS_*` from the
  server environment, has no read-only rootfs or cap-drop, and cannot work
  from the containerised server (container-local workdir paths).
- Built-in `ShellStep` runs `config.command` with `shell=True` and the full
  server environment; a JSON-only workflow suffices.
- An upload carrying a `Dockerfile` is built with `podman build
  --userns=host` through the host podman socket the server container holds;
  that socket also drives the deploy user's postgres and seaweedfs.
- Workflow run resolution falls back to any workflow with a matching name
  across owners (`workflow_registry_handler.py`, "scope is advisory").

ADR-027's role gate was the stated bound ("only admins upload"), but the
production catalog holds uploads by two non-maintainer accounts
(2026-08-27..30 and 2026-09-17), after that gate landed. They passed it
legitimately — every server runs `UMRS_AUTH_MODE=strict` and the UMRS API
ports are refused off-host — so the gate holds, but its holders already
include non-maintainer identities. The server's trust domain
holds the registry DB superuser DSN, root S3 keys, the HMAC handshake
secret, `LITELLM_MASTER_KEY`, the admin token, SSH keys to every cluster and
the host podman socket — so any uploaded step is a full platform compromise.

What real steps need (inventory of every bundled/demo step): almost all use
only the `StepContext` helpers (`download_bytes`, `download_parent_bytes`,
`upload_bytes`, `upload_numpy`, `download_numpy`, `head_object`) plus
`parent_results`; LLM/Vision/OCR templates call model endpoints; a few use a
raw boto3 client, list whole buckets, or launch Slurm/SSH themselves. The
one hardened front-host sandbox today is the `embedding-job` lane
(`umrs/knowledge/embed_sandbox.py`): throwaway rootless Podman,
`--network=none`, read-only rootfs, all caps dropped, JSON over
stdin/stdout, the server keeps every store read and write, the umrs-server
image reused as the sandbox image.

## Decision

**D-1 Classification.** A step is *platform code* only when its entrypoint
resolves inside the installed `umrs` package to a template on a closed
allow-list of side-effect-bounded templates (`LLMStep`, `VisionStep`,
`OCRStep`, `CodeStep`, `NotebookStep`, `AudioStep`). Everything else is
*uploaded code*: any entrypoint resolved from a workflow source tree
(`source.type == "uploaded"`, or a module under the workflow state
directory), plus `ShellStep` and `VideoStep` wherever they come from
(caller-chosen commands and binaries).

**D-2 Uploaded code never runs in the server process.** No import for
validation (the entrypoint is checked syntactically; resolution happens in
the worker), no `inproc` or `subprocess` lane for uploaded code, and a
manifest's `execution_mode` cannot select an in-server lane for it.

**D-3 Profile.** The front-host step sandbox is ADR-061's `gpu-job` profile
hosted on the `frontend` venue (CPU-only there). Venues are hosting
locations, not profiles (SANDBOX-PROFILES-MAP scoping rules), so no sixth
profile is created; `gpu-job`'s posture — fail-closed egress, per-job
allowlist, mounted inputs only — is exactly the step posture.

**D-4 Sandbox posture.** Throwaway rootless Podman: `--network=none`,
`--read-only`, size-capped tmpfs, `--cap-drop=all`,
`no-new-privileges`, `--pids-limit`, memory/CPU limits when the cgroup
controllers are delegated, a non-root `--user`, a named container removed on
timeout, no environment passthrough (an explicit allow-list only), the
uploaded source mounted read-only by host path, never the podman socket,
never a credential.

**D-5 Data plane: a credential-free context broker.** Inside the sandbox,
`StepContext.s3` is a shim that forwards the helper operations to the server
over the step's own channel; the server performs the S3 I/O with its own
credentials. Scope per step: read `input_keys`, the parents' output keys and
declared trigger inputs; write only under `runs/<run_id>/<step>/` (the shim
rewrites keys; children resolve them through the same mapping). Operations
outside the helper set (list, delete, other buckets) are refused by name.
Model calls: phase 2 adds a per-step egress proxy to the routed endpoint
only; until then a step that needs the network fails by name — the mock
fallbacks are disabled in the sandbox so a denial never reads as success.

**D-6 Replies are data.** The worker writes a JSON reply: the `StepResult`
fields as JSON, numeric ndarrays packed as dtype + shape + base64 with the
byte count checked against the shape, a size cap. `parent_results` travel to
the worker in the same form. The server never unpickles step-controlled
bytes on any lane; old images are served by a restricted unpickler
*(corrected 2026-09-26: the landed loader resolves only `StepResult` and
builtin containers and refuses shared containers — the parked numpy-aware
unpickler was deliberately not carried over; on the remote HPC lane there is
no pickle fallback at all, see Amendment 2)* behind a site flag that expires
with them.

**D-7 Images and uploads.** A step image is either the platform step image
or one on an admin-curated allow-list; `routing_hints.image` outside it is
refused. Upload-time `Dockerfile` builds stop for uploaded code (a sandboxed
builder may return later under its own decision).

**D-8 Run resolution is owner-scoped.** A `user`-scope workflow runs only
for its owner (and admins); `system` scope stays shared. No cross-owner
name fallback.

**D-9 Rollout gate** *(superseded 2026-09-25 by Amendment 1 — normal users upload by design)*. Until D-2, D-4, D-5 and D-6 are live on a site, that
site's uploaded-code workflows run only for admin owners; a site flag
(`UMRS_WORKFLOW_UNSANDBOXED_UPLOADS=allow`) keeps the legacy behaviour for a
named transition, recorded in the rollout block when used.

### Amendment 1 (2026-09-25, maintainer answer)

The maintainer confirmed that both uploading accounts are normal-user
identities and that **normal users must be able to upload their own
workflows to run on connected HPCs**. Uploading is a user capability
(ADR-027 `workflow_author`), so containment never takes it away:

- **D-9 is replaced.** Uploaded-code workflows are never restricted to admin
  owners. Instead: **uploaded code runs on the site's connected-HPC lane**
  (the Slurm lane — ADR-061 `gpu-job` on the cluster), never on an in-server
  lane. A manifest's `execution_mode: in_process` (or no mode) sends uploaded
  code to the site's HPC lane; a site without one refuses it by name until
  the front-host sandbox (D-3/D-4/D-5) exists there.
- **D-3 becomes the secondary lane**: the front-host sandbox serves sites
  without a connected HPC and platform-template steps; it is no longer the
  first thing built.
- **Rollout order, revised**: D-2 (no server-side import; uploaded code never
  in-process — routed to the HPC lane) + D-6 on the Slurm lane (JSON replies;
  restricted unpickler only for older step images) + D-8 at the caller-facing
  layer → the HPC lane's own posture (no forwarded credentials, no host
  network by default, image allow-list, D-7) → D-3/D-4/D-5 for HPC-less sites.
- **Templates are not automatically safe in-process**: `VisionStep` reads any
  existing local path named by config, so an author-controlled config could
  read server files; platform templates keep an in-process lane only after
  their config inputs are constrained (tracked with D-1).

### Amendment 2 (2026-09-26): staged I/O is the D-5 transport on HPC lanes

The rccs investigation (ledger 2026-09-26) found no workable way to give a
step object-store access from the cluster: the only route is the front's
reverse forward on the login node, which any login-node user can reach and
which vanishes when the SSH master drops; STS on SeaweedFS reaches one bucket
for at most an hour with no prefix listing; and the in-memory run bucket is
not reachable from the cluster at all. So on HPC lanes:

- **The step gets files only** — no credential, no network, no platform key.
  Before the job, the server streams the step's inputs (its `input_keys`,
  declared `stage_inputs`) from S3 into the step's workdir on the cluster's
  shared filesystem over the existing SSH path; inside the container
  `StepContext.s3` is a file-backed shim (`umrs/pipeline_sdk/staged_s3.py`);
  after the job the server streams `out/` back, accepts regular files only
  under caps, and uploads them under `runs/<run_id>/<step>/`. The reply's
  `output_keys` must name files actually collected; they are rewritten to
  physical keys so children stage them.
- **The server never follows a step-controlled path**: a fixed-verb login-node
  helper (`umrs/deploy/_common/umrs_step_helper.py`) opens with `O_NOFOLLOW`,
  checks `S_ISREG`/`nlink`, caps every read; the reply is JSON only (4 MiB),
  with no pickle fallback on the remote lane.
- **The container posture** is `--network=none --read-only --cap-drop=all
  --security-opt no-new-privileges --userns=keep-id --user <uid>` with only
  `in/`, `out/`, `reply/`, `src/` and the payload mounted; the workdir,
  `job.sbatch` and the Slurm logs are never mounted.
- **The step image** is built from a `git archive` of the pinned umrs (never
  the checkout, whose serving-secret files leaked into the parked
  `Dockerfile.step` and into an existing image tar), layer-scanned, staged by
  the converge to the cluster's image archive, and loaded per job.
- **Supersedes** the parked tunnel + STS step-lease design for steps.
- **Residual risk (recorded, not closed):** every step still runs as the one
  cluster service account, so the container boundary is the only barrier
  between user code and that account's files (SSH keys, serving secrets on
  Lustre). A dedicated unprivileged cluster account for steps, and tightening
  those files to 0600, are maintainer actions.

### Why

- Every bound short of this one leaks: ADR-027's role gate was already
  exceeded in production, and each lane that looked isolated still unpickled
  or imported the code in the server.
- The `embedding-job` lane proves the pattern on this host with this image:
  the server keeps the credentials and the I/O; the container gets data.
- A broker instead of scoped STS: STS on SeaweedFS reaches one bucket, for
  at most an hour, and cannot list (ADR-055), and presigned URLs need a
  network route out of the sandbox; the broker needs neither and keeps the
  step's reach exactly the keys the run owns.

## Consequences

- Uploaded workflows keep working unchanged when they use the helper API;
  steps that list buckets, delete, use their own boto3 client, launch Slurm
  or SSH, or call endpoints directly need a rewrite (named in the inventory:
  a demo video-analysis workflow, demo 09/10 segment/feature steps, the
  private HMAE runtime).
- Steps get slower (container start per step; the pool lane may return
  later with the same posture).
- `ShellStep`-based imports (CWL, Nextflow, Snakemake) run sandboxed and
  offline by default.
- NOT in scope: the cluster `gpu-job` lane's own hardening (Slurm lanes get
  D-6 replies and the image allow-list here; their network posture is the
  cluster's), model-server confused-deputy reads, and the server's own
  podman-socket reach (tracked separately).

## Alternatives Considered

- **Scoped STS credentials in the sandbox** — one bucket, ≤ 1 h, no listing
  on the deployed backend, and it needs network egress to the object store.
- **Subprocess isolation** — same container, same environment and
  filesystem as the server; not a boundary.
- **Keep ADR-027's role gate as the bound** — already exceeded in production.
- **A new sixth profile** — nothing in the step posture differs from
  `gpu-job`; a profile per hosting location is what ADR-061 exists to stop.

## Rollout-status (2026-09-25)

- Landed alongside this ADR (umrs `75f9938d`): trigger params can no longer
  steer the routed platform key; routing records returned by
  WORKFLOW_RESULTS are redacted; the Slurm lane refuses non-reference images
  and quotes its script. These close reachable leaks; they do not implement
  D-1..D-9.
- D-1..D-9: open. Order: D-6 + D-2 + D-8 (code only) → D-4 + D-5 (sandbox
  executor + broker, converge wiring on umrs-front) → D-9 flips → D-7 and the
  phase-2 egress proxy.

## Rollout-status (2026-09-25, night)

- **D-2 + D-6 built and parked, NOT on `ai4s-platform` and NOT deployed** (umrs branch `wip/adr-074-d2-d6`, `be9ab853`; it briefly reached the branch head and was reverted in `8cedcf49`, because the umrs-front converge deploys the umrs branch head, not the superproject pointer). Uploaded
  code (module in the uploaded tree, or `ShellStep`/`VideoStep`) is classified
  by the filesystem, never imported by the server, never put on the server's
  `sys.path`, and routed to the site's HPC lane (a manifest's `in_process`
  does not stand); a site without one refuses it by name. The Slurm lanes read
  a JSON reply first (capped; `UMRS_STEP_REPLY_MAX_BYTES`), and a pre-JSON
  image's `output.pkl` through a loader that resolves only `StepResult` and
  builtin containers and refuses shared containers. Pins:
  `umrs/tests/test_step_reply.py` (15), `test_uploaded_step_routing.py` (4,
  red on the old runner); umrs 5770 passed.
- **Deploy is held for the maintainer**: production impact per server —
  rccs has an HPC lane but no default step image (only workflows naming
  `routing_hints.image` run); ai4s, bdrt and octopus have no step lane, so
  their uploaded workflows (in-process until now) are refused. Needed before
  or with the deploy: step lanes (`step_executor.workdir_root`) for bdrt and
  octopus, and a platform step image (D-7).
- Open: D-8 (caller-facing owner scope), the HPC lane's own posture (no
  forwarded `AWS_*`, no host network by default, image allow-list), D-3..D-5.

## Rollout-status (2026-09-26)

- **rccs HPC lane built** (umrs `170a174c` on `wip/rccs-step-lane`, superproject
  build spec `docs/development/rccs-step-lane-spec-2026-09-26.md`): D-2 + D-6
  (ported), Amendment 2 staged I/O, login-node helper, hardened step pod, step
  image + stager, lifecycle (reconcile, clocks, scancel, reaper, sweep), per-site
  gate (`remote_enabled`, `uploaded_code: refuse` default), D-7 (no upload-time
  builds, image allow-list), D-8 at the caller-facing layer. Three adversarial
  review rounds; umrs 6279 passed.
- **Maintainer decisions:** octopus, bdrt and ai4s refuse uploaded workflows by
  name until each lane is live (order rccs → octopus → bdrt → ai4s); rccs
  serving-secret files tightened to owner-only via the converge.
- **Open:** live receipts on rccs (R1 image staged, R2 hardened pod + GPU form on
  GB10, R3 end to end, R4 staged inputs, R5 negatives); a dedicated cluster
  account for steps (maintainer); the image's build backend (setuptools) is
  fetched unpinned at build time; a runner-level cancel pin.
- **Peer-review gates before `remote_enabled` closed (2026-09-26):** B3 a
  re-dispatch attaches to a run still running here (umrs `57d5f4a6`); B1 a
  retry resumes a live earlier job on the same inputs instead of cancelling it,
  and the run-end reap leaves a retryable run's live/COMPLETED jobs; B2
  reconcile rides out a login-node outage (umrs `ced55ac5`); S3 the image
  verdict counts only for the umrs commit the front deploys, and the verifier
  checks `.id` against the image config inside the archive (superproject,
  same day). Enforcement (refuse by name on every site) deploys first; the two
  rccs switches follow.
- **Second peer read (2026-09-26) — F1–F3 closed before the flip:** F1 the
  inputs tag is stable across a durable re-dispatch (no volatile parent facts,
  no router-filled config in the payload), so downstream jobs resume too; F2 a
  refused dispatch and a cancel between retries reap for real, and the run-end
  reap keeps jobs only while the job store says the durable job has attempts
  left; F3 reconcile rides out a Slurm outage (helper exit 5). Precondition
  recorded: one UMRS server instance per site. Deferred: deleting the legacy
  local-Slurm podman argv (`--network=host`, S3 env; reachable only under
  `uploaded_code: legacy_in_process`).
- **Third peer read (2026-09-26):** F2(a), F3 and the should-fixes closed; T1
  (parent order) and T2 (gate `decided_at`) fixed in umrs `d18b64b4`, which
  also binds the tag to the step image. The peer agrees to flip rccs
  `remote_enabled` after R2, run with a live two-step chain. Follow-ups before
  the other sites are listed in the spec (§11).

## Rollout-status (2026-10-01)

- **rccs posture: plain podman on the nodes' stock runc** (umrs `3eb05c30`,
  maintainer request; prepared as ledger 2026-10-01 (21)): `cgroups: enabled`,
  no limits requested (`pids_limit` 0, RLIMIT_NPROC 4096) — rootless runc then
  creates no container cgroup, the step stays in the job's Slurm cgroup. crun is
  no longer needed (STATE TODO-20260926-CRUN).
- **R2 receipt — PASS** after three smoke rounds on rccs (converges 36835551346,
  36839745933, 36844513817): round 3 passed 6/7 — probes on ng-dgx-m0/m1/m3 ran
  non-root on a read-only rootfs with only the four staged names, both targets
  unreachable; the GPU run saw `/dev/nvidia0`, `nvidiactl`, `nvidia-uvm(-tools)`,
  `nvidia-caps`; the a->b chain kept a stable tag; the kill check settled
  CANCELLED with a clean node. The 7th (ng-dgx-m2) only queued out: another
  user's job held both `<compute-node>`s throughout — capacity, not the lane.
- Found and fixed on the way: CDI is unresolvable on GB10 podman 4.9.3 — new
  `gpu_form: nvidia-devices` (node's device nodes + host driver libs, umrs
  `ef5204d2`); GNU `head` held a running step's log in its stdio buffer, so the
  kill check never saw its marker — the pod log streams through `stdbuf -o0`
  (umrs `7288bc03`); the kill check now reports its job-log tail.
- **Open:** the `remote_enabled` flip on rccs (maintainer — opens the lane to
  uploaded code), then R3 end to end, R4 staged inputs, R5 negatives.

## Rollout-status (2026-10-02) — rccs lane OPEN, R3–R5 PASS

- **Flip:** rccs-front `remote_enabled: true` (maintainer-approved 2026-10-02,
  umrs `88ba8cce`). Found and fixed by R3:
  - the rccs job_submit policy refuses a partition list ("Multiple partitions
    are not allowed"), so the lane submits to one partition, `ng-dgx-m3`
    (umrs `32de7a4f`);
  - a step with `routing_hints.gpu` got no devices: the remote lane needs
    BOTH the site gate (`gpu: true` → `UMRS_STEP_GPU=1`) and the hint. The
    rccs gate is now open (umrs `0f291d23`); unhinted steps stay GPU-less;
  - the four umrs-front servers share the `umrs_db` job store, and jobs
    created through the ZMQ/HTTP stores carried `venue=''`. ai4s and octopus
    claimed the rccs runs, refused them (no lane) and failed the rccs attempt
    that ran. Every `JobStore` is now claim-scoped to its site (umrs
    `7afb44c1`; STATE JOBS-20261002-VENUE).
- **Receipts** (uploaded workflow `lane-receipts`, run `wf-36461008bfcf`,
  apply 36957813021, step image `7afb44c1`; one durable attempt, claimed by
  rccs only; Slurm 391831–391834 COMPLETED on two `<compute-node>`s):
  - **R3 end to end — PASS:** produce → consume across two nodes; the sum
    (5050) round-tripped through the staged output store.
  - **R4 staged inputs — PASS:** consume saw only its parent's key
    (`runs/<run>/produce/data/numbers.json`), staged by the server.
  - **R5 negatives — PASS:** every attempt was refused:
    - TCP to the front (`<front-ip>:443`), the login1 S3
      (`<login1>:<s3-port>`) and 8.8.8.8:53;
    - S3 list, a read of an unstaged key, a read of another bucket;
    - writes to `/etc` and `/work/in`; a read of `~/.ssh`.

    No credential in the env (only the base image's public `GPG_KEY`
    fingerprint); the operator's uid, non-root.
  - **GPU — PASS:** `/dev/nvidia0`, `nvidiactl`, `nvidia-uvm(-tools)` and
    `nvidia-caps` were visible; `cuInit` = 0, one device, "NVIDIA GB10".
- SDK note found on the way: `upload_bytes` returns an `s3://` URI, but
  `download_*` take a bare key. Steps return bare keys in `output_keys` (as
  the SDK docstring shows).
- **Open:** converge ai4s, bdrt and octopus so their own job stores carry
  their venue (maintainer dispatch). Until then the jobs they create stay
  shared.

## Rollout-status (2026-10-02, later) — first allow-listed project image (D-7)

- Other fronts: the venue fix reached ai4s/bdrt/octopus (owner-dispatched
  apply 36965422708).
- D-7 in use: rccs-front `allowed_images` names
  `localhost/umrs-step-<project>-cnn:<platform sha12>-<lock sha8>` (umrs `44979500`).
  - It is a demo project's CNN image (torch + CUDA).
  - It is built by the project's own staging script on the login node: a
    COPY-only layer on the platform image, a hash-locked aarch64 package set,
    and the tag `<platform sha12>-<lock sha8>`.
  - A test pins every allow-listed image to a non-moving, archive-named tag.
- Receipt: a project workflow ran its detection, tracking and aggregation
  steps on one GPU node; the tracking step ran on **cuda**.
- Lessons for project images:
  - Normalize layer permissions (`chmod -R u=rwX,go=rX`): the uv cache shared
    with the umask-077 platform stager left 0600 files, and the non-root step
    user could not import cv2.
  - Rebuild a tag only from its saved lock (`--lock`). A fresh resolve drifts
    to a new tag; nodes reload on `.id` mismatch.
- On the lane a child reads only its parents' recorded outputs
  (`runs/<run>/<step>/<key>`, in `input_keys`). A project's step code reads them through
  a helper and returns bare keys (`upload_bytes` returns an `s3://` URI).

## Rollout-status (2026-10-28) — staging venue: login node → umrs-front (#611)

- **What changed.** Step-image staging no longer depends on the cluster login node. The
  converge runs the stager **on umrs-front** against the pinned deploy checkout
  (`umrs_front_step_image_build_host: front`, default) and ships the archive over SSH
  (sidecars under final names first, the tar via a temp name + atomic rename), then the
  unchanged cluster-side verify decides the verdict; the keep-newest-3 prune rides the same
  hop. `build_host: login` keeps the login-node behaviour for a site whose login node carries
  podman. The image contract (digest-pinned base, `git archive` pin, layer scan,
  archive+`.sha256`+`.id` receipts) is unchanged, and the COPY-only build still needs no
  emulation for the aarch64 target; the stager's new `--tmp-dir` keeps its workspace on a
  volume with headroom.
- **Why.** RCCS decommissioned login1 in its login1→login2 migration (login2: no podman, no
  subuid/newuidmap, no modules). Login-node staging had failed soft on every pin since
  2026-10-16, and the role fail-closed `UMRS_RCCS_STEP_*` each time, silently closing the
  lane for ~11 days until a user workflow hit the refusal (#607). The interim manual stage
  that re-opened it (off-cluster build with this same stager) demonstrated that any
  COPY-only build host satisfies the contract; umrs-front is the host the converge already
  owns (its podman posture is Ansible-managed). #607's gate, refusal text and in-server
  posture are untouched.
- **Receipts (to record at merge).** Transition: interim manual stage of
  `umrs-step-e6ce29ed7e13-aarch64.tar` (built off-cluster, shipped by hand, verify green,
  smoke completed on `ng-dgx-m3`). First converge-side build under this section bumps the
  pin: expect `"status":"built"` from the front host, ship + prune, verify green, lane open.

## Rollout-status (2026-11-04): decided further by ADR-109 and ADR-110

- **D-5, phase 2.** The per-step egress to the routed endpoint only is decided in [ADR-109](109-vision-service-and-routed-endpoints-for-steps.md) D-5.
  - A Unix-socket forwarder that holds the credential.
  - `--network=none` is kept.
  - Amendment 2's list of step env names gains `UMRS_STEP_ENDPOINT_SOCKET`.
  - Not landed.
- **D-7 "admin-curated".** For images users register, this is replaced by [ADR-110](110-self-service-step-images.md) D-1–D-7, opened on a site only under ADR-110 D-8:
  - a dedicated step account, or per-user identity;
  - that closes this ADR's recorded residual risk for that site.
  - ADR-110 D-6 (trusted seccomp, a fixed entrypoint, image env ignored) also applies to today's allow-listed images.
  - Not landed.

## Rollout-status (2026-11-06): D-5 phase 2 landed repo-side (ADR-109 V5)

- **D-5 phase 2 is implemented** (umrs `1c1e5bdd`; [ADR-109](109-vision-service-and-routed-endpoints-for-steps.md) V5 block): a per-step Unix-socket forwarder that holds the credential. The step pod keeps `--network=none`. Not yet deployed.
- **Amendment 2's env list.** The fixed step env names gain `UMRS_STEP_ENDPOINT_SOCKET`, present only when the step has a routed endpoint. Since ADR-110 I1, the pod also carries fixed platform env values (PATH, HOME, LANG, PYTHONUNBUFFERED, PYTHONDONTWRITEBYTECODE) under `--unsetenv-all`.

## Rollout-status (2026-11-08): D-6 extended by ADR-112 (payload direction, gated lanes)

- **D-6, extended.** [ADR-112](112-user-hpc-accounts-gated-delegation.md) D-6 adds the mirror image of this ADR's D-6: on a gated lane (a step run in the user's own HPC account), step code never unpickles server-supplied bytes. The gate builds a JSON payload in place of today's pickle.
- **When.** Wave B2 of the gated-delegation plan. The pool and ZMQ worker modes are unchanged.
- **Nothing changes yet.** Wave A (declared access modes, the interim project allow-list) does not touch the payload, the batch script or the step pod posture. D-6 as written above still holds on every lane.

## Rollout-status (2026-11-08, later): the gated lane (ADR-112 wave B2) in progress

- **No payload pickle on the gated lane.** On a site that offers ADR-112 `user_account`, a staged step of a person with an active account and an active project link runs through that person's gate. The gate writes the step input itself, as JSON (`input.json`), from fields the user signed and from the replies of parent steps that the gate recorded itself. The worker reads it in a new JSON mode, checks the entrypoint against the signed one before importing anything, rebuilds parent results through the reply checker, and never calls `pickle.loads`.
- **It runs under the user's own account.** The job is submitted by the gate under the user's login and charged to the user's allocation, not to `<operator-account>`. The batch script is composed by the gate from its own template (no `venue.sh` sourced, no operator script, `--export=NONE`); the pod posture of D-4 and the D-5 data plane are kept, and a shared-vector test keeps the posture identical to this lane's.
- **D-6's mirror holds there:** step code never unpickles server-supplied bytes.
- **Unchanged elsewhere.** `service_account` sites, the helper lane, `input.pkl`, and the pool and ZMQ worker modes keep today's behaviour; D-6 as written above holds on them.
- **Not live.** The platform step image gains the label `org.umrs.step-payload="pickle,json"`; it must be rebuilt and re-staged by a converge before the gate accepts it. The change is in progress on `wave/gated-b2-intents` (plan §5 B2.0), not merged and not deployed. The first lane receipt is a maintainer leg.

## Related

- [ADR-027](027-workflow-upload-authorization.md)
- [ADR-043](043-attempt-scoped-s3-credentials.md)
- [ADR-055](055-object-store-backend-seaweedfs.md)
- [ADR-061](061-mini-rivault-sandbox-profiles.md)
- [SANDBOX-PROFILES-MAP](../SANDBOX-PROFILES-MAP.md)
- STATE `SEC-20260925-001`; wave-ledger 2026-09-25 "(night, 4)"

*(2026-11-06: internal addresses, paths and group ids replaced with placeholders for the public export (repo-rename plan P5b-2); the values live in r4-ops.)*

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
