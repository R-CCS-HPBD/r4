# 019-amendment-2026-09-14: Host checkouts are read-only deploy inputs

**Status:** Accepted (amendment to [ADR-019](019-check-deployment-keys.md))
**Date:** 2026-09-14
**Relates to:** ADR-019 · `.github/workflows/converge-umrs-front.yaml` · D-6 of the structure-hardening plan

## Context

Six observability files are bind-mounted from
`{{ umrs_front_repo_path }}/observability/…` into production containers
(`deploy/ansible/roles/front_end/templates/compose.observability.yaml.j2`).
On 2026-09-14 the first three converge-check runs
(34792061778 / 34792212408 / 34792695860, all reaching data0 with
`unreachable=0`) found the otel-collector config, `prometheus/alerts.yaml`
and the umrs pointer hand-edited on data0. The collector edit was a **correct**
fix — the remote-write target `prometheus:9090` does not resolve from the
host (the container is `umrs-front-prometheus`) — but it had never been
upstreamed, so any converge would have silently reverted production to a
broken collector.

## Decision

1. **A deployed checkout is never edited by hand.** Every change a host
   needs lands as a repo commit plus a converge. The converge refuses a dirty
   checkout and reports the patch into the recap artifact (landed 2026-09-14);
   refusal is a feature flag, not a signal to edit again.
2. **Nightly check**: `converge-umrs-front.yaml` runs `mode=check` on a
   nightly schedule (`cron: '30 18 * * *'`) against `main`; a failure opens
   or updates a GitHub issue (search-then-comment so one failure signature
   has exactly one issue).
3. **When a converge refuses a dirty checkout**, the reconcile sequence is:
   upstream the diff as a commit → `git checkout -- <paths>` on the host →
   `mode=apply`. No other order.

### Why

Silent host edits put real production out-of-step with the repository, and a
later converge can naively revert live-tuned behavior with no record. The
recorded condition is the only condition a remote control plane may hold.

## Consequences

- A hot-patch is a repo commit + converge; nothing else ever applies.
- Operators do `git checkout -- <paths>` on data0 only to revert their edits
  once upstreamed; everything else is converge-driven.
- NOT in scope: replacing `tail_sampling` by hand (D-11 — needs collector
  logs on the host; see `docs/operations/observability-notes.md`).

## Alternatives Considered

- **Allow honored edits for emergencies** — rejected: fewer honesty paths;
  the converge-precondition is already the emergency-out.

## Rollout-status (2026-09-14)

- Proposed; workflow lands in Phase 5.1 of the structure-hardening plan.
- **Landed 2026-09-15** (`bd6e792d`): the `converge-umrs-front` schedule is
  enabled (`cron 30 18 * * *`, mode=check). Failure → exactly one open
  `ops`-labeled issue, updated rather than duplicated on subsequent red
  nights (`gh issue list --search` guard inside the step); permissions
  `issues: write` scoped to that step's env. Header text records the dated
  live-proof chain (runs 34792061778 / 34792212408 / 34792695860 —
  unreachable=0 at data0, blocked on genuine host drift the playbook now
  names). Instruction-section leg: `deployment-operations.instructions.md`
  hosts the read-only + reconcile-order rules under their own heading.

## Related

- [ADR-019](019-check-deployment-keys.md)
- structure-hardening-plan-2026-09-14.md §D-6
- docs/operations/observability-notes.md
