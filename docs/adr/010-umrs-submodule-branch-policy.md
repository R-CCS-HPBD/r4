# ADR-010: UMRS Submodule Branch Policy

**Status:** Accepted (rollout 2026-09-13: branch name corrected to `ai4s-platform`, fork/upstream remotes and `.gitmodules` URL policy recorded)
> **Date:** 2026-06-19  
> **Supersedes:** None  
> **Superseded by:** None  

**Rollout-status (2026-09-13, release track R-08):**

- The integration branch is **`ai4s-platform`** on the fork
  **`mnagaso/umrs`** (`git -C umrs branch --show-current`; `git -C umrs remote -v`:
  `origin` = `git@github.com:mnagaso/umrs.git`, `upstream` =
  `git@github.com:UMRS-org/umrs.git`). The name `ai4s-platform-2` used by the
  2026-06-19 text below is historical (that branch no longer exists); the
  body was corrected in this block's wave.
- `.gitmodules` policy: the submodule URL points at the **fork**
  (`git@github.com:mnagaso/umrs.git`) so `git submodule update --init` and
  the login-node self-heal fetch the remote that actually carries the pinned
  SHA; upstream `UMRS-org/umrs` is kept only as the named remote `upstream`
  for merges `main → ai4s-platform`. The `.gitmodules` change landed
  2026-09-13 (release track wave). Before it, `.gitmodules` still named
  `https://github.com/UMRS-org/umrs.git` while the pinned SHA lived on the
  fork — the lineage defect recorded in the
  production release plan §1.
- Divergence on 2026-09-13: fork branch ahead of `upstream/main` by 756
  commits, behind by 202 (`git rev-list --left-right --count upstream/main...HEAD`).

**Rollout-status (2026-09-15, lineage reconciliation, maintainer directive
"in gitmodule of ai4s-platform, it directs UMRS-org/umrs but we are updating
and deploying mnagaso/umrs — this should be the same"):**

- `UMRS-org/umrs@ai4s-platform` was fast-forwarded to the fork tip
  (`git push upstream HEAD:ai4s-platform` → `2cbac37d..5fe4f1bc`, 158 commits).
- `.gitmodules` URL flipped to `git@github.com:UMRS-org/umrs.git` (canonical
  home; the fork `mnagaso/umrs` becomes a push-mirror kept around only
  because the open deploy-key situation is easier there, not a deploy source).
- CI submodule-fetch recipes (`ci.yaml` ×8, `compose-e2e.yaml`, `nightly-e2e.yaml`)
  now first-try SSH on UMRS-org, and their HTTPS-PAT fallback rider is path-
  agnostic — the same secret (`UMRS_HTTPS_TOKEN`, classic PAT with `repo`
  scope) covers the repo whichever home succeeds, chosen deliberately over
  any deploy-key (UMRS-org's org-level "Disabled by UMRS-org" policy makes
  the org's own `ai4s-platform-ci` key UI-disabled perpetually without an
  org admin's clicks).
- Deploy/ansible checkouts flip alongside (`group_vars/umrs_front.yaml:457`
  + `hpc-code-sync-cluster.yaml` default lines); the strategy remains "host
  checkouts read-only" per the ADR-019 amendment (no host-side reconcile
  needed—the pull-only lane transports the same tag contents either way).
- Answer to the lineage question (release plan esc §9.5): **UMRS-org
  remains the canonical remote.** The fork era answered R-08 when the
  pull-only clone via ssh deploy-key collapsed (`Repository not found` from
  UMRS-org) and upstream's `ai4s-platform` branch did not carry the
  platform's pinned SHAs; both are no longer true as of the ff push above.
- Rule 5 (submodule-drift check in the drift-sweeper) has **no** running
  consumer: the loops have been inactive since 2026-06-17 (STATE.md). The
  superproject pointer remains the single source of truth by convention and
  by CI checkout only.

---

## Context

The `umrs/` directory is a git submodule with two relevant branches:

| Branch | Purpose | Stability |
|--------|---------|-----------|
| `main` | UMRS upstream — core orchestration, ZMQ transport, task management | Production-stable |
| `ai4s-platform` (on fork `mnagaso/umrs`; written `ai4s-platform-2` in the 2026-06 text) | AI4S integration layer — gateway integration, r4 client sync, deployment profiles | Integration-active |

During the 2026-06-19 engineering loop audit, a **divergent UMRS state** was
discovered on `rccs-wk`:

- `atom-0` and `atom-1` use `origin/ai4s-platform-2` for UMRS (HEAD: `ba3a293`)
- `rccs-wk` uses `origin/main` for UMRS (HEAD: `83b84bd`)

This divergence means `rccs-wk` is running a **different UMRS codebase** than
`atom-0`/`atom-1`. The two branches have diverged significantly — the
`ai4s-platform-2` branch has ZMQ CURVE session work that `main` lacks.

### Root Cause

There is no documented policy on which UMRS branch the `ai4s-platform`
superproject should track. Workers can check out any branch inside `umrs/`
without constraint, leading to silent divergence.

---

## Decision

The `ai4s-platform` superproject tracks UMRS commits from the
`ai4s-platform` branch of the fork `mnagaso/umrs` (2026-06 text said
`ai4s-platform-2`; corrected 2026-09-13).

**Remotes and `.gitmodules` (2026-09-13):**

- `origin` → `git@github.com:mnagaso/umrs.git` (fork; carries `ai4s-platform`)
- `upstream` → `git@github.com:UMRS-org/umrs.git` (upstream `main` only)
- `.gitmodules` `url` = the fork URL. Never point it at upstream while the
  pinned SHA lives on the fork.

**Rules:**

1. **Workers must use `git submodule update --init --recursive`** to check out
   the SHA recorded by the superproject. Direct branch checkouts (e.g.,
   `git checkout main` inside `umrs/`) are **forbidden**.

2. **UMRS integration changes** must be committed to `ai4s-platform` (fork)
   and then the superproject's submodule pointer updated. Docs or
   traceability rows that cite umrs paths land in the same commit as the
   pointer bump or after it — never before (CI Boundary Check went red on
   2026-09-13 for exactly this reason).

3. **The `main` branch** is only used for UMRS upstream development, not for
   `ai4s-platform` integration.

4. **Merges from `upstream/main` → `ai4s-platform`** are allowed and
   encouraged to keep the integration branch up-to-date with upstream.

5. **A `submodule-drift` check** must be added to the drift-sweeper to detect
   when a worker's UMRS checkout diverges from the superproject's recorded SHA.

---

## Consequences

### Positive

- Workers always run the same UMRS codebase.
- UMRS integration changes are isolated to `ai4s-platform` on the fork.
- The superproject's submodule pointer is the single source of truth.

### Negative

- The `ai4s-platform` branch may diverge from upstream `main` if not regularly merged (756 ahead / 202 behind on 2026-09-13).
- Testing UMRS `main` branch changes requires a temporary worktree, not a
  direct checkout.

---

## Validation

After any merge that touches `umrs/src/umrs/shared/config.py` or
`umrs/src/umrs/shared/connection_profiles.py`, run:

```bash
cd umrs
PYTHONPATH=src python3 -c "
from umrs.shared.config import get_current_connection_profile_file
f = get_current_connection_profile_file()
assert f == 'current_connection_profile', f'Expected current_connection_profile, got {f!r}'
print('✓ Config resolution OK')
"
```

---

## Related

- [ADR-007: Gen1↔Gen2 Bridge Pattern](007-gen1-gen2-bridge-pattern.md)
- [Branch Separation Instructions](../../.github/instructions/umrs-branch-separation.instructions.md)
- [Mutation Safety Rules](../../.github/instructions/mutation-safety.instructions.md)
