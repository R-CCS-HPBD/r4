# ADR-061: mini-RiVault sandbox profiles — named profile contract; Singularity backend retired

**Status:** Accepted (profile table binding; retirement + extension waves land separately)
**Date:** 2026-09-22 (maintainer decision, design reset wave)
**Relates to:** ADR-006 (mini-RiVault container security), ADR-015 (ingest egress), ADR-023 (MCP hardening contract), ADR-040 (egress convergence, Proposed), ADR-046 D-4 (frontend-venue egress classes), gap rows D-12 / D-13 / D-24 in [docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md)

## Context

Design suggestion 6 proposes a small set of named sandbox profiles, each with
a declared egress allowlist and mounts, so that MCP trust classes map onto
profiles. Today the sandbox story is fragmented: compute jobs run
podman-compose under Slurm by preference, but the Singularity backend
(`umrs/src/umrs/orchestrator/slurm_backend.py`, `launcher_stack.py`) is still
in the tree and in the docs; front-end agent sessions run rootless Podman on
`egress-net` with a privileged opt-in nftables guard; embedding/ingest
compute runs inside the UMRS server process; default MCP runtimes are host
venvs under `/opt/umrs/mcp-runtimes`. Tenant tiers (system/project/user),
compose profiles (`harness`, `vllm`, `vision-bentoml`) and the ADR-046 D-4
venue egress classes each carry partial, unconnected versions of "what is
allowed".

## Decision

Design suggestion 6 is adopted as the profile contract. Five named profiles:

| Profile | Wraps | Egress posture | Mounts |
|---|---|---|---|
| `gpu-job` | Workflow/compute jobs on cluster GPU nodes (podman-compose under Slurm) | Fail-closed deny; per-job allowlist (ADR-015 ingest is the only named non-model egress class) | Mounted input data only |
| `cpu-agent` | Remote agent sessions on the front-end or `cpu:<host>` venues | ADR-046 D-4 egress classes; guard privileged + opt-in until rootless-enforceable | Session root; no provider keys (sealed store) |
| `model-server` | Self-deployed LLM / Vision / VLM servers | Offline at boot (`TRANSFORMERS_OFFLINE=1`, assert-offline); inference listeners only | Model weights read-only |
| `embedding-job` | Embedding / ingest compute (moved out of the UMRS server process) | Model-fetch denied post-cache; destination stores only | Corpus mount read-only |
| `mcp-trusted` | Default and trusted-host MCP server instances | Per-server declared allowlist from its manifest (`hatch.json` capabilities) | Per-server declared mounts only |

1. **One vocabulary.** Tenant tiers (system/project/user), compose profiles
   and the ADR-046 D-4 venue egress classes are mapped onto these five
   profiles in a follow-up implementation wave; nothing grows a sixth
   profile without a new ADR.
2. **MCP trust classes map onto profiles** (D-24): local-safe → local pull /
   `cpu-agent` reachability, front-end → `mcp-trusted` on the front-end,
   trusted-host-only → `mcp-trusted` on the named trusted host (ADR-060
   venue). Registry placement/trust-class fields (D-19/D-21) carry the
   mapping.
3. **The Singularity backend is retired.** `slurm_backend.py` /
   `launcher_stack.py` and the Singularity-centric docs are removed in the
   phase-3 wave (D-12); podman-compose under Slurm is the only compute-job
   sandbox path. Until that wave lands, the Singularity path is deprecated
   in place: no new features, docs carry the deprecation.
4. Embedding/ingest compute moves into `embedding-job` sandboxes and default
   MCP runtimes into `mcp-trusted` sandboxes in the phase-3 wave (D-12
   partial → Implemented); this ADR names the destination, not the schedule.

## Consequences

- Gap row D-13 (profile contract) closes by this ADR once the mapping table
  is published with the implementation wave; status moves Absent → Partial
  (contract written).
- Security receipts and ADR reviews quote profile names from this table;
  the opt-in/privileged nature of the front-end egress guard stays on the
  record (`cpu-agent` row) until rootless enforcement exists.
- ADR-040 (egress convergence) and ADR-046 D-4 remain the mechanism ADRs;
  this ADR is the name layer they converge into.

## Rollout (2026-09-22, Singularity retirement leg)

- The **Singularity lane refuses by name**: bare `"slurm"` in the
  orchestrator factory (`get_orchestrator`) and construction of
  `SingularityWrapper` both raise with "retired (ADR-061)" naming the
  Podman lane. Production was already on `slurm-remote`
  (Slurm + Podman) in every deployed compose file, so no deployment
  change is triggered.
- Singularity-lane test suites were rewritten to refusal pins
  (`test_hybrid_slurm_remote_launch.py`); factory authority guards
  extended (`test_slurm_orchestration_authority.py`).
- The classes stay importable intentionally (RemoteSlurmBackend's base +
  named-construction-failure instead of ImportError smudge); the dead
  render path in `slurm_script.py` is deleted in the cleanup wave after
  one release of refusal.
