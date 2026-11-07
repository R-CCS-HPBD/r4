# ADR-045 — HPC venue portability contract (mini-RiVault on Slurm + rootless podman)

**Status:** Proposed (WP-01 round 3b, 2026-09-07 — library proven live on the rccs
venue with read-only-plus-hygiene probes; the serving-path live legs run after
merge + Ansible converge, see the rollout block)
**Date:** 2026-09-07
**Relates to:** ADR-006 (offline inference + egress model), ADR-008 (SiteConfig
`inject_env()` no-overwrite), ADR-019 (deployment tree consolidation), ADR-040
(egress enforcement convergence — D4 declared proxy image), ADR-036 / WP-05
round 2 (cluster venue for loop jobs rides this venue).
**Principles:** P4 (deployment reality), P1 (invariants honest by default), P5
(venue parity).

## Context

The WP-01 round 3 HPC leg could not start a single `egress_domains` endpoint on
RCCS Cloud. Three provisions (Slurm jobs 379190, 379193, 379194) were admitted
by the API and died on the compute node after ~6 s with one line:

    [rivault] ERROR: failed to create genai-private-net-379194 with any candidate subnet

Read-only probes on the login node and on the failing node
(`<compute-node>`, jobs 379199/379202/379203/379205, 2026-09-07) established the
causes. None of them is specific to RCCS; each is a property most HPC systems
share.

| # | Observed on rccs-cloud | General HPC property |
|---|---|---|
| 1 | `~/.config/cni/net.d` (HOME `/home/users/<user>`, shared by login and compute nodes) held the CNI configs of three dead jobs — 270437 → `172.16.4.0/24`, 270477 → `10.0.4.0/24`, 373989 → `192.168.4.0/24` — exactly the wrapper's three fixed candidates. podman 4.9.3 (CNI backend) on every compute node refused each with `subnet … is already used on the host or by another config`. The login node runs podman 5.8.2 with netavark (configs under the node-local graphroot), saw none of them, and its manual probe therefore passed. | `$HOME` is shared across nodes; a fixed candidate list is exhausted by as many crashed jobs as it has entries. |
| 2 | The wrapper wrote `~/.config/containers/storage.conf` with *this* job's `runroot=$XDG_RUNTIME_DIR/containers/storage` at every start. Because HOME is shared, every start repointed the still-running jobs' podman view; their teardown failed, leaving networks and `Created` containers behind (job 373989's `litellm`/`nginx`, dependent on each other, blocked `podman network rm -f`). | Per-job tmp dirs + shared HOME make a per-job config file a cross-job side channel. |
| 3 | Rootless podman only honours a `runroot` under `$XDG_RUNTIME_DIR`, and it keeps the runroot recorded in its state database (`db.sql`): `podman info` reported `/tmp/job-373989/…` for every later job, while an explicit `--runroot` failed with `database run root … does not match`. The legacy deploy shims "fixed" this by deleting other jobs' `/tmp/job-*/xdg/containers` dirs and killing every `conmon`/`crun` of the user — corrupting whatever was running on a shared node. | The state database outlives the job that created it; concurrency-hostile cleanup is the usual workaround. |
| 4 | `localhost/r4-mcp:latest`, the proxy image the site declares (ADR-040 D4), existed on no node: image stores are node-local (`/var/tmp/podman_<user>/storage`) and `localhost/` has no registry. The wrapper's `podman pull` fallback could never succeed; the image had also never been built anywhere. The login node is x86_64 while the GB10 partitions are aarch64. | Node-local image stores, no registry route, mixed architectures. |
| 5 | The cluster checkout's `deploy/rccs/serve-model.slurm` was an **uncommitted** 7-line `exec` shim (no `Fully qualified name:` header — the launcher waits 300 s for it — no partition profiles, no vision-image load). The committed 580-line shims of rccs, bdrt and octopus were near-copies (all headed "rccs-cloud"), each carrying the defects above plus hardcoded Lustre paths. | Per-cluster copies drift (ADR-019's finding, now on the compute side). |

## Decision

One library, `umrs/src/umrs/mini-rivault-llm/venue.sh`, sourced by the
wrapper, by the per-partition harness probe and by the image staging script,
so the code a serving job runs is the code the venue is validated with.

### D1 — job-private container configuration, never a shared file

- The wrapper writes `storage.conf` and `containers.conf` under
  `$XDG_RUNTIME_DIR/rivault-<jobid>/containers/` and points podman at them via
  `CONTAINERS_STORAGE_CONF` / `CONTAINERS_CONF`. Nothing under
  `~/.config/containers/` or `~/.config/cni/` is written or read by a job.
- Graphroot (`/var/tmp/podman_<user>/storage`, the image cache) and the
  **network config dir** (`/var/tmp/podman_<user>/networks`, via
  `[network] network_config_dir`) are node-local and shared by this user's
  jobs on that node. Runroot is `$XDG_RUNTIME_DIR/containers` — podman's
  rootless default and the only location it honours.
- When the state database still names a dead job's runroot **and no container
  is running on the node**, the container-only state (`db.sql`, `libpod/`,
  `overlay-containers/*`) is dropped so podman re-creates it against this
  job's runroot; images and layers are untouched. With containers running the
  pinned runroot is kept (it works; it is only oddly named).
- Site knobs: `runtime.storage_root`, `runtime.network_config_dir`,
  `runtime.cgroup_manager` → `RIVAULT_STORAGE_ROOT`, `RIVAULT_NETWORK_CONFIG_DIR`,
  `RIVAULT_CGROUP_MANAGER`. A partition profile with `needs_netavark_switch`
  becomes `[network] network_backend = "netavark"` in the job's file.

### D2 — private network from a pool, after reaping dead jobs' networks

- `rivault_net_gc` removes every `genai-private-net-<jobid>` whose job
  `squeue` no longer knows (two-pass force removal of dependent leftover
  containers); networks of running jobs, the job's own name, and names without
  a job id are kept; when Slurm is unreachable nothing is touched.
- `rivault_net_create` allocates the job's /24 from
  `network.private_subnet_pool` (`PRIV_SUBNET_POOL`, default `172.16.0.0/16`;
  a single `a.b.c.0/24` pins one subnet): an operator-set `PRIV_IP1TO3` first,
  then up to `PRIV_SUBNET_ATTEMPTS` (16) third octets walked from
  `jobid % 254 + 1`, then podman's own allocator with the subnet read back.
  Every attempt's error is printed on failure, followed by `podman info`,
  `podman network ls` and the host's IPv4 addresses.

### D3 — images travel as archives on the shared filesystem

- `rivault_ensure_image REF [ARCHIVE_DIR] [ARCHIVE]`: present in the node
  store → `podman load` of `<archive_dir>/<name>-<tag>-<arch>.tar` (retagging
  if the archive carries another name) → `podman pull` → refuse with the
  archive path that was looked for. The egress-guard image goes through it
  (the ADR-040 D4 refusal text is unchanged); the vision sidecar image goes
  through it with its existing `VISION_TARBALL` + `-<arch>.tar` convention and
  the legacy build-once-and-save-back fallback, which the retired shims had
  been the only place to run.
- `deploy/_common/stage_images.sh` (shims in rccs/bdrt/octopus) builds the
  declared image from the ai4s-platform checkout root — `mcp-servers/Dockerfile`
  now installs the unpublished `r4-contracts` sibling from the checkout and the
  dead `project-research` extra (the unpublished `r4` SDK, tools retired in
  WP-07) is gone from `all`, so the image is buildable without any registry —
  on a compute node of the target architecture via `srun` when the login
  node's differs, and saves it under `runtime.image_archive_dir`
  (`RIVAULT_IMAGE_ARCHIVE_DIR`). Ansible's rccs-cloud login play runs it
  idempotently (`--if-missing`); the compute play warns when the archive is
  absent.
- The image's `entrypoint.sh` passes an explicit command through (`exec "$@"`
  when the first argument is a program); the compose `command: python -m
  r4_mcp.egress_guard …` had been appended to `r4-mcp <SERVER_TYPE> …` and
  rejected, so the guard service could never have started in a container.

### D4 — GPU override generated from the host

`rivault_write_gpu_override` maps every `/dev/nvidia*` node and binds the
driver userspace libraries actually present under the host's multiarch dir (or
`/usr/lib64`) — including `libnvidia-ptxjitcompiler` under both its SONAME
and dlopen name — instead of the aarch64-only, single-GPU embedded file, which
stays as the fallback when no device node exists.

### D5 — the site descriptor is the single source; profiles apply in one place

- `site.yaml` gains `extends: <base file>` (deep merge, mappings merge key by
  key, scalars and lists replace, cycles refused) so a front overlay can carry
  only its deltas; new keys `runtime.image_archive_dir`,
  `runtime.network_config_dir`, `network.private_subnet_pool`; the JSON schema
  now covers every dataclass field (`TestSchemaDrift` pins it — it had been
  missing eleven, including `network.egress_proxy_image`).
- The wrapper applies `partition_env(SLURM_JOB_PARTITION)` itself: site
  defaults first, the partition profile over them, neither overriding a value
  the job was submitted with (ADR-008). The retired shims were the only place
  this happened, so the uncommitted 7-line shim on the cluster had silently
  dropped partition profiles.

### D6 — one compute shim, and the probe runs the wrapper's code

- `deploy/<cluster>/serve-model.slurm` is one identical ~70-line file for
  every podman cluster (rccs, bdrt, octopus): `#SBATCH` directives incl.
  `--signal=B:TERM@120`, the node header the launcher parses, site file and
  wrapper located from `RIVAULT_WRAPPER_DIR` (the launcher already exports
  it), HOME/USER repair, `.env` master-key fallback, `exec`. No paths, no
  cleanup, no per-cluster logic.
- `harness_probe.sh` runs `rivault_venue_prepare`, a dry-run `rivault_net_gc`,
  `rivault_net_create` from the site pool and `rivault_ensure_image` for the
  declared egress image on every partition, reporting
  `VENUE_CONFIG`, `STALE_JOB_NETWORKS`, `NETWORK_CREATED`, `EGRESS_GUARD_IMAGE`.

### Explicitly not done

- No job ever again writes under `~/.config/containers` or `~/.config/cni`,
  kills processes it did not start, or deletes another job's runroot.
- `ai4s` (Apptainer, `runtime.kind: singularity`) is untouched — D1–D4 are
  podman semantics. `hpbd-atom` keeps its own 257-line shim (same defect
  family; named follow-up, no venue to verify on).
- `rccs-front/site.yaml` stays a flat copy: its container mounts only that
  file, so `extends: ../rccs/site.yaml` waits for the compose mount to include
  the base (`TestFrontOverlayDoesNotDrift` pins the two files together until
  then).
- Digest pinning of `localhost/r4-mcp:latest` stays open (ADR-040).

## Consequences

- Adding a podman cluster is now: `deploy/<cluster>/site.yaml` (+ partition
  profiles, `image_archive_dir`, `private_subnet_pool`), `cluster.env`, the
  identical shims, `./harness_probe.sh` (every partition green on the four
  venue lines), `./stage_images.sh`, an Ansible inventory.
- Concurrent jobs of one user on one node are supported by construction:
  job-scoped names, pool-walked subnets, shared node-local image cache,
  garbage collection keyed on Slurm's view of job liveness.
- The failure mode changed from "one line, six seconds, nothing to act on"
  to a printed list of attempts with podman's own error text and the venue's
  state.

## Rollout status (dated; the truth of this ADR)

- **2026-09-07 — diagnosis + fix landed on `feat/wp01-r3b-venue-portability`
  (parent + umrs).** Library, wrapper integration, shims, staging script,
  probe, site knobs, schema, Ansible wiring; 24 library pins + 26 schema/site/shim
  pins.
- **2026-09-07 — live evidence on `<compute-node>` (podman 4.9.3, CNI):** the
  library, streamed over `srun … bash -s` (no files written to the deployment
  tree), (a) captured the real refusal `subnet 172.16.4.0/24 is already used on
  the host or by another config`; (b) reaped the three dead-job networks
  (373989's needed the dependent-container pass); (c) allocated
  `172.16.227.0/24` for seed 379194 on attempt 1; (d) isolated the node-local
  network config dir (only the default `podman` network visible, images
  intact); (e) re-pinned the state database from job 373989's dead runroot to
  the job's own once idle and ran a container afterwards; (f) reported the
  exact missing archive path for the egress image. The venue is un-poisoned
  even for the still-deployed wrapper.
- **2026-09-07 — scratch build tests of the egress image on a GB10 node**
  (jobs 379216/379217/379221, scratch dir, not the production archive dir):
  the standalone `mcp-servers/Dockerfile` failed on the unpublished
  `r4-contracts`, then on the unpublished `r4` SDK behind the dead
  `project-research` extra — the reasons the image had never existed on any
  node. With the root-context Dockerfile and the extra removed it built in
  77 s; running it exposed the `entrypoint.sh` dispatch swallowing the compose
  command (fixed with the passthrough). Job 379223 then proved D3 end to end
  on the GB10 node: `podman save` → 96 MB `r4-mcp-latest-arm64.tar`, image
  removed, `rivault_ensure_image` reloads it, the guard runs. Details in
  `docs/verification/packages/wp01.md` round 3b.
- **2026-09-07 21:50 JST — cluster checkout's uncommitted shim reset**
  (`git -C …/umrs checkout -- deploy/rccs/serve-model.slurm`; hpc-code-sync's
  fast-forward refuses to overwrite local edits by design). PRs opened:
  UMRS-org/umrs#114, mnagaso/ai4s-platform#298.
- **Pending (in order):** merge → `./bootstrap.sh --profile umrs-front --tags
  hpc_sync` (code sync) and `--profile rccs-cloud --tags stage_images` →
  `harness_probe.sh` on the GB10 partitions → WP-01 round 3 live legs
  (`/healthz` + CONNECT positive proof, inline dedup, refusal proof on a
  non-declaring venue) → WP-05 round 2 on the same venue.
- **Untested live:** the netavark switch path (`needs_netavark_switch`
  profiles — none in this round's partitions); bdrt/octopus with the converged
  shim (same defect family, no venue access in this round).
