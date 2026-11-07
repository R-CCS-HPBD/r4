# ADR-019: Deployment Tree Consolidation

**Status:** Accepted
**Date:** 2026-07-27
**Relates to:** ADR-018 (reverse SSH tunnel), development.instructions.md
(HPC-first architecture), deployment-operations.instructions.md (Ansible-only
deployment), mutation-safety.instructions.md, umrs-branch-separation.instructions.md

## Context

The deployment tree grew organically through an intensive development season in
which four HPC clusters (`rccs`, `ai4s`, `bdrt`, `octopus`) were brought online
under time pressure. The dominant growth mechanism was **copy-paste
per-cluster**: adding a cluster meant duplicating a directory or a playbook and
editing a handful of lines.

That mechanism was correct for the exploratory phase — it kept blast radius per
cluster at zero and let each cluster diverge freely while its shape was still
unknown. It is now the primary source of operational incidents, because the
clusters' shapes have converged but their code has not.

### Measured duplication

`umrs/deploy/{rccs,octopus,bdrt}/` are near-identical triplets. Diff sizes
between the `rccs` copy and its siblings:

| File | Lines | rccs↔octopus | rccs↔bdrt |
|---|---:|---:|---:|
| `start.sh` | 718 | 4 | 15 |
| `stop.sh` | 154 | 0 | 0 |
| `fresh_state.sh` | 213 | 0 | 0 |
| `health_check_server.sh` | 263 | 2 | 10 |
| `setup_server.sh` | 206 | 12 | 12 |
| `harness_probe.sh` | 261 | 23 | 23 |
| `setup_client.sh` | 57 | 2 | 2 |
| `ssh_tunnel.sh` | 59 | 12 | 12 |
| `validate_harness.sh` | 24 | 0 | 0 |
| `docker-compose.yml` | 63 | 2 | 2 |

Approximately 2,000 lines are maintained in triplicate to express fewer than 60
lines of genuine per-cluster difference. Two of these files are **byte-identical
across all three clusters**.

On the Ansible side, `roles/front_end/tasks/umrs-front.yaml` is 1,202 lines and
`roles/login_node/tasks/rccs.yaml` is 545 lines.

### The failure mode this produces

Duplication does not stay duplicated. It **drifts**, silently, and the drift is
only discovered during an incident. Two instances are already on record:

1. **The `<login-node>` SSH alias incident (2026-07-24).** Three independent copies
   of the same broken SSH alias existed — data0's `~/.ssh/config`, the
   Ansible-managed alias, and `bdrt-front/site.yaml`'s literal `ssh_target`.
   Fixing one did not fix the others; the stale copy silently shadowed the
   corrected one.

2. **Per-cluster `umrs-front-<cluster>.yaml` playbooks are not what they claim
   to be.** They are documented and named as "the unified playbook, scoped to
   one cluster." Measured against `umrs-front.yaml -e umrs_front_clusters=[...]`
   via `--list-tasks`:

   | Profile | Tasks | Unified equivalent | Delta |
   |---|---:|---:|---:|
   | `umrs-front-rccs` | 122 | 139 | −17 |
   | `umrs-front-ai4s` | 122 | 139 | −17 |
   | `umrs-front-bdrt` | 122 | 139 | −17 |
   | `umrs-front-octopus` | 121 | 139 | −18 |

   Every per-cluster playbook silently omits the **A2L service** play (16 tasks)
   and the **observability** play (Prometheus/Grafana/Loki/Jaeger).
   `umrs-front-octopus.yaml` additionally omits the **HPC login-node code sync**
   play entirely — meaning `--profile umrs-front-octopus` never refreshed the
   octopus login-node checkout, and the operator had no signal that it hadn't.

   These are not stylistic differences. An operator choosing the "focused"
   profile for a routine single-cluster redeploy receives a **partial
   deployment**, and nothing in the output says so.

### Additional latent defect

`playbooks/umrs-front.yaml` declares `umrs_front_clusters` as a hardcoded
four-element *play var* in plays 3 and 6, while plays 2, 4 and 5 read it from
`group_vars`. Play vars outrank group_vars, so a run without `-e` deploys a
different cluster set in different plays. The defect is masked in practice
because extra-vars outrank both, and operators habitually pass `-e`.

### Why act now

The platform has left reconstruction mode and entered steady-state operational
governance (per `mutation-safety.instructions.md`). Under steady state the
dominant cost is no longer "time to add a cluster" but "probability that a
routine redeploy silently does the wrong thing." Copy-paste optimizes the former
and actively degrades the latter.

## Decision

Consolidate the deployment tree in **six ordered phases**, each independently
revertible, each gated on a mechanically verifiable equivalence check.

The ordering principle is **risk-ascending**: phases that cannot affect a
running system come first, and every later phase inherits the safety net built
by an earlier one.

### Phase 0 — Structural baselines (prerequisite, no behavior change)

Add `deploy/ansible/scripts/capture-baselines.sh`, which records for every
profile the exact play/task set Ansible would run and the fully-resolved
inventory variables, entirely offline (`--list-tasks`, `ansible-inventory`;
neither connects to a host).

Task lists are normalized to *task name + tag set* and exclude play banners, so
that renaming a play is not mistaken for a behavioral change.

**Contract:** every subsequent change under `deploy/ansible/` must either leave
`./scripts/capture-baselines.sh --check` passing, or ship a regenerated baseline
whose diff is explained in the commit message.

This phase is what made the Phase 1 drift measurable rather than a matter of
opinion, and it is the reason the remaining phases can be verified instead of
merely reviewed.

### Phase 1 — Retire the per-cluster `umrs-front-*` playbooks

Delete `playbooks/umrs-front-{rccs,ai4s,bdrt,octopus}.yaml`. Retain the
`umrs-front-<cluster>` **profile names** in `bootstrap.sh` as aliases that
dispatch `playbooks/umrs-front.yaml` with
`-e '{"umrs_front_clusters":["<cluster>"]}'`.

Fix the `umrs_front_clusters` play-var shadowing defect in `umrs-front.yaml`.

**This phase intentionally changes behavior**: the aliased profiles gain the
A2L, observability, and (for octopus) HPC-code-sync plays they were silently
missing. This is a bug fix, is called out explicitly here, and is the reason
Phase 1 is not labelled "no behavior change."

Operator-facing CLI surface is unchanged.

### Phase 2 — De-duplicate the `umrs/deploy/` cluster triplets

Introduce a single shared implementation of `start.sh`, `stop.sh`,
`fresh_state.sh`, `health_check_server.sh`, `setup_*.sh` and `docker-compose.yml`,
parameterized by cluster. Per-cluster directories retain only genuinely
cluster-specific artifacts: `site.yaml`, `.env`, `pool-*.yaml`, and a thin shim.

Expected reduction: ~4,000 lines to ~1,400.

**Highest risk phase.** These files are live on login-node shared filesystems
and are pulled by `hpc-code-sync`. Rollout is one cluster at a time, each gated
on that cluster's `validate_harness.sh`, with the previous cluster left
untouched until the current one is confirmed healthy. Changes land in the `umrs`
submodule and follow `umrs-branch-separation.instructions.md`.

**Outcome (umrs `b5c391c`, 2026-07-27).** Implemented as
`umrs/deploy/_common/` (canonical scripts) plus per-cluster shims that export
`UMRS_CLUSTER_DIR` and `exec` into `_common`. Per-cluster values live in
`<cluster>/cluster.env`, sourced by `_common/cluster-context.sh`, which fails
closed if `UMRS_CLUSTER_DIR` is unset rather than guessing a cluster.
49 files changed, net −3,163 lines.

`docker-compose.yml` was **not** shared. rccs/bdrt/octopus run infra-only
compose (postgres + minio) with a native `umrs-server` so it can reach
`sbatch`; ai4s runs an older fully containerized topology (server and gateway
in compose, on shifted ports to coexist with rccs-front on the same host).
ai4s therefore keeps its own `start.sh`/`stop.sh` and gains only the shared
scripts it previously lacked. Converting ai4s is a runtime migration, not
cleanup, and is unverifiable while that host is offline; the recipe is recorded
in `ai4s/cluster.env`.

Consolidation was also a bug-discovery mechanism. Seven latent defects were
found and fixed, six of them on octopus, which had been cloned from bdrt and
never corrected: wrong SSH host, wrong gateway port, wrong client profile,
wrong model root, and bdrt's partition list. Separately, bdrt and octopus both
asserted `cfg.name == "rccs-cloud"`, so their site-config check could never
pass — silently, because the assertion was wrapped in
`if ... >/dev/null 2>&1`.

### Phase 3 — Naming that matches reality

`roles/*/tasks/rccs.yaml` is generic login-node HPC logic — `bdrt.yaml` and
`octopus.yaml` both consume it via `tasks_from: rccs.yaml`. Rename to
`login-node-hpc.yaml`.

Resolve the standing `rccs-cloud` (retired login-node profile) versus `rccs`
(cluster under umrs-front) ambiguity, completing or formally abandoning the
deferred Phase 2 of the July 2026 retirement effort.

**Split into 3a and 3b after measurement.** `deploy/ansible` contains 937
`rccs_*` references across 158 distinct variables and 29 files, consumed live
by the rccs, bdrt and octopus group_vars. Renaming files and renaming variables
are therefore not the same size of change and must not land together.

**Phase 3a — file renames (done, `a0a25804`).** Four roles each owned a file
named `rccs.yaml` (`common`, `login_node`, `compute_node`, `tunnel`), because
`tasks_from:` resolves per role. All four were renamed to `hpc.yaml` in one
commit; renaming a subset would have silently broken the rest. The target name
is `hpc.yaml` rather than the originally proposed `login-node-hpc.yaml`, since
the same name now serves compute-node and tunnel roles too. Additionally
`rccs-nginx-sidecar.yaml` → `nginx-sidecar.yaml` and `rccs-gateway.yaml` →
`python-gateway-legacy.yaml`, naming what they manage rather than where they
were first used. Verified behavior-preserving by `capture-baselines.sh --check`.

**Phase 3b — variable renames (deferred, not scheduled).** Renaming `rccs_*`
to `hpc_*` touches three live clusters and needs either a compatibility-alias
period or a verified big-bang. The current names are misleading but not broken,
so this carries real risk for cosmetic benefit and requires explicit sign-off
before it is attempted.

### Phase 4 — Decompose the 1,202-line role task file

Split `roles/front_end/tasks/umrs-front.yaml` into per-concern includes
(infrastructure, gateway, per-cluster server, r4-web, env rendering). Purely
mechanical, verified against Phase 0 baselines.

### Phase 5 — Retire dead network paths

- `rccs-tunnel.yaml` + `roles/tunnel/` (superseded by ADR-018's
  `remote-tunnel.yaml`), contingent on the Phase 3 `rccs-cloud` decision.
- The octopus reverse-tunnel SSH-back forward, now that data0 reaches the
  octopus login node directly. The MinIO `-L` forward must be evaluated
  separately — it solves a different problem (login node reaching data0's
  MinIO) and may still be required.
- The `*_minio_tunnel_enabled` mechanism ADR-018 already identifies as
  redundant.

### Phase 6 — Prevent regression

Add a validator to `tools/` that fails when a new per-cluster clone appears
(`umrs/deploy/<cluster>/start.sh`, `playbooks/umrs-front-<cluster>.yaml`).
Consolidation without an enforcement mechanism decays back to the current state;
this fits the repository's existing validator-based governance model.

## Consequences

### Positive

- A fix applied once takes effect everywhere, eliminating the class of incident
  that produced both recorded drift failures.
- Partial-deployment bugs become structurally impossible for the aliased
  profiles.
- Deployment behavior becomes mechanically verifiable rather than reviewer-
  dependent.
- Adding a cluster becomes a configuration change rather than a directory copy.

### Negative / Accepted costs

- Phase 2 touches files live on three production login-node filesystems. This is
  accepted because the alternative — leaving 2,000 triplicated lines in place —
  has already caused production incidents, and the staged per-cluster rollout
  bounds the blast radius to one cluster at a time.
- Shared parameterized scripts are harder to read for a single cluster than a
  dedicated copy. Accepted: the triplicate copies are only *apparently* simpler,
  since an operator cannot tell from reading one whether the other two agree.
- Baselines add a step to the deployment change workflow. Accepted as the
  explicit price of mechanical verification.

### Explicitly out of scope

- No change to the runtime architecture, the Gateway/UMRS boundary, or any
  security invariant.
- No change to the operator-facing `bootstrap.sh` CLI surface.
- The `generic`, `atom-k8s`, and `hpbd-atom` profiles are untouched; they have
  no duplication problem.

## Verification

Each phase is verified by:

```bash
cd deploy/ansible
./scripts/capture-baselines.sh --check
```

Phases that intentionally change behavior (Phase 1) regenerate the baseline and
justify the diff in the commit message, so that the change is recorded as a
deliberate decision rather than absorbed silently.

Phase 2 additionally requires, per cluster:

```bash
cd umrs/deploy/<cluster> && ./validate_harness.sh
```
