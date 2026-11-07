# ADR-107: Code and operations in separate repositories

**Status:** Accepted (2026-11-01, maintainer created the private `mnagaso/ai4s-ops` (renamed `mnagaso/r4-ops` 2026-11-06) and said "please proceed": O-1 = that repository, O-2…O-6 as recommended; O-1 done at `dc551a86`, ledger (150)). First Proposed (2026-11-01, maintainer request "separate the code implementation github repository and operation info dedicated repository … important for going to real operation mode"; drafted by the agent; decisions O-1…O-6 in the plan §6 await the maintainer)
**Date:** 2026-11-01
**Relates to:**
- [ADR-045](045-hpc-venue-portability-contract.md): `site.yaml` is the venue contract.
- [ADR-060](060-topology-one-umrs-hub.md): one hub.
- [ADR-106](106-service-model-tier.md): the rosters carry the service tier.
- Repo cleanup plan (credentials); onboarding plan D-1 (publishing).

## Context

Verified 2026-11-01 at parent `b3af3cda2` and umrs `f6e1bf08`.

**What the code repos hold.** Besides the platform, `ai4s-platform` and its `umrs` submodule hold the live operation of four sites:
- `umrs/deploy/<site>/`: site profiles, rosters, shims, job directories;
- `umrs/deploy/umrs-front/docker-compose.yaml`: the four fronts on data0;
- `deploy/ansible/inventory` and `group_vars`;
- the converge and ops-watch workflows;
- live incident rows in `STATE.md`.

**How deploys work.** The converge deploys the umrs commit recorded in the parent pointer. The pre-push lineage hook requires that commit to be on UMRS-org `ai4s-platform`. So every code wave is also a candidate production change, as the ADR-106 roster aliases were (ledger (144)).

**Coupling and exposure.** Fifteen test files read live site files. Host names, user names and account paths sit in the tree that onboarding D-1 intends to publish.

## Decision

1. **D-1. Two kinds of repository.**
   - **Code repositories:** `ai4s-platform`, `umrs`. They hold the product, generic templates, validators, tests, design and ADRs.
   - **One private operations repository:** name and owner per plan O-1. It holds every site instance and deployment fact, the host-specific runbooks, operations state, and the deploy workflows.
2. **D-2. The operations repository pins code.** `versions.yaml` names the code commits per front. A deploy is a reviewed change to the operations repository. A push to a code branch never changes production.
3. **D-3. Code reads operations by path.** That means `UMRS_SITE_CONFIG`, `UMRS_PROVISION_ROSTER` and `RIVAULT_WRAPPER_DIR`, which already exist. No code path hard-codes a site.
4. **D-4. The operations repository validates its config with the code's own validators** at the pinned version, in CI.
5. **D-5. Secrets live in neither repository.**
   - Environment secrets or a vault, delivered by the converge.
   - Known committed credentials are rotated before any history is copied.
6. **D-6. Code tests use fixtures.** Behaviour that is about a real site, such as "Nimble is kept alive on rccs", becomes an operations-repository check.

### Why

- **Real operation needs** change control on what runs, an audit trail per deploy, and a smaller audience for infrastructure details. Development needs open review and fast waves. One repository cannot give both.
- **The pin** makes a deploy an explicit act. Today it is a side effect of any session's pointer bump.

## Consequences

- **Positive:**
  - publishing code (D-1 of the onboarding plan) stops leaking site data;
  - deploys get reviews, approvals and rollback by revert;
  - agents working on code cannot change production by accident.
- **Cost:**
  - two repositories, and pin bumps as an explicit step;
  - a migration of about 15 tests to fixtures;
  - the self-hosted runner moves to the operations repository.
- **Not in scope:** changing the site-config format (ADR-045 stays).

## Alternatives Considered

- **A private branch or folder in the code repository.** Rejected: the same audience and history, and nothing separates a code merge from a deploy.
- **Git submodule of operations inside code.** Rejected: it inverts the pin. Code would name an operations version, when operations must name the code version it deploys.
- **Configuration in a database or secret store only.** Rejected for now: rosters and site profiles need review diffs and history.

## Rollout-status (2026-11-01)

Proposed; nothing created or moved. Plan phases O-0…O-4.

**2026-11-01 (later):**
- **Accepted.** O-1 is done: `mnagaso/ai4s-ops` (renamed `mnagaso/r4-ops` 2026-11-06) `main` `dc551a86`, a history-preserving copy with 18 credential-shaped values redacted, plus the pins, a validator (20 rosters and 9 site profiles OK) and CI.
- **The code repositories still hold the originals** and still deploy (O-2 pending owner legs).
- **D-5 rotation is open.**

## Related

- Plan
