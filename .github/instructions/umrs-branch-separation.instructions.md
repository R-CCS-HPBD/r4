---
description: Clear separation between ai4s-platform branch and main branch of umrs submodule
applyTo: '**'
---

# UMRS Branch Separation: `ai4s-platform` vs `main`

Updated 2026-09-22 (design reset): branch graph re-verified against
`.gitmodules`, `git -C umrs remote -v` and
[ADR-010](../../docs/adr/010-umrs-submodule-branch-policy.md) (rollout
blocks 2026-09-13 and 2026-09-15); the regression test in §5 exists.

## Background

The `umrs/` directory is a git submodule. The branch graph today:

```
UMRS-org/umrs  main                      upstream UMRS (core orchestration, ZeroMQ transport, jobs)
      │  merges main → ai4s-platform (allowed and encouraged, ADR-010 rule 4)
      ▼
UMRS-org/umrs  ai4s-platform             CANONICAL integration branch — what .gitmodules points at
      │  push-mirror (maintainers only)
      ▼
mnagaso/umrs   ai4s-platform             maintainers' fork; `origin` in the maintainer's umrs checkout
```

| Branch | Purpose | Stability |
|--------|---------|-----------|
| `main` (UMRS-org) | UMRS upstream — core orchestration, ZeroMQ transport, durable jobs, model registry | Production-stable |
| `ai4s-platform` (UMRS-org; maintainers mirror it to their fork) | R4 + UMRS integration — edge integration, r4 client sync, deployment profiles, everything the superproject pins | Integration-active |

- `.gitmodules`: `url` = UMRS-org/umrs, `branch = ai4s-platform`
  (flipped from the fork on 2026-09-15, ADR-010 rollout block). The public
  repository's `.gitmodules` uses `url = https://github.com/UMRS-org/umrs.git`;
  the development repository's uses the SSH form
  `git@github.com:UMRS-org/umrs.git` (2026-11-07).
- Maintainers only (contributors need neither the fork nor push rights):
  remotes in the maintainer's checkout are `origin` = `git@github.com:mnagaso/umrs.git`
  (fork, push-mirror), `upstream` = `git@github.com:UMRS-org/umrs.git`
  (canonical). A fresh clone's `origin` is UMRS-org (from `.gitmodules`);
  layouts for both cases and for contributors without push rights:
  [docs/onboarding/developer.md](../../docs/onboarding/developer.md) §2
  (2026-10-18: this line described only the maintainer's clone).
  `git -C umrs branch --show-current` → `ai4s-platform`.
- The superproject's submodule pointer is the single source of truth;
  workers use `git submodule update --init --recursive`, never a direct
  branch checkout inside `umrs/` (ADR-010 rule 1).
- Integration changes are committed on `ai4s-platform`, then the pointer is
  bumped; docs or traceability rows that cite umrs paths land in the same
  commit as the bump or after it, never before (ADR-010 rule 2).
- After a pointer bump run `bash scripts/ci-lint-local.sh --umrs-changed`
  (ADR-052; the parent diff never descends into the submodule).

### The Problem (historical, 2026-07)

An update on the integration branch broke the UMRS client profile
resolution. The root cause was a **silent variable shadowing** introduced
during a merge:

```python
# umrs/src/umrs/shared/config.py
DEFAULT_CURRENT_CONNECTION_PROFILE_FILE = "current_connection_profile"
DEFAULT_CURRENT_CONNECTION_PROFILE_FILE = None  # ← shadows the correct value!

DEFAULT_CURRENT_S3_CONFIG_FILE = "current_s3_config"
DEFAULT_CURRENT_S3_CONFIG_FILE = None  # ← shadows the correct value!
```

This caused:
- `umrs profile use <name>` → `[Errno 21] Is a directory: '.'`
- `umrs info` → `profile: (none)`, `gateway_url: (not set)`
- `r4 auth login` → ZMQ session bootstrap skipped (503)

### Why It Happened

1. **Branch divergence without guardrails**: the integration branch introduced config changes that were not validated against `main`'s expected behavior.
2. **No regression test for profile resolution**: the `set_current_profile_name()` → `get_current_profile_path()` → `get_current_connection_profile_file()` chain was not covered by a test that would catch the `None` shadowing (it is now — §5).
3. **Silent failure mode**: Python allows variable reassignment without warning, so `X = "foo"; X = None` silently nullifies the first assignment.

## Mandatory Rules

### 1. Never Shadow Module-Level Constants

**Forbidden:**
```python
DEFAULT_FOO = "bar"
DEFAULT_FOO = None  # Never do this — use a different variable name or conditional
```

**Required:**
```python
DEFAULT_FOO = "bar"
# If you need a "disabled" sentinel, use a separate variable:
DEFAULT_FOO_OVERRIDE = None
```

### 2. Validate Config Resolution After Branch Merges

After any merge that touches `umrs/src/umrs/shared/config.py` or `umrs/src/umrs/shared/connection_profiles.py`, run:

```bash
cd umrs
PYTHONPATH=src python3 -c "
from umrs.shared.config import get_current_connection_profile_file
f = get_current_connection_profile_file()
assert f == 'current_connection_profile', f'Expected current_connection_profile, got {f!r}'
print('✓ Config resolution OK')
"
```

### 3. Branch Separation Contract

When working on the `umrs/` submodule:

#### `main` (upstream UMRS)
- **Owns**: core UMRS functionality — ZeroMQ transport, durable jobs and attempts, model registry, context storage
- **Does NOT own**: R4-platform integration, edge URL propagation, r4 client sync, deployment profiles
- **Changes**: must be backward-compatible with existing UMRS clients

#### `ai4s-platform` (integration branch)
- **Owns**: integration-layer changes — config resolution, profile sync, deployment profiles, everything the superproject pins
- **Does NOT own**: changes to core UMRS algorithms or data structures (those go upstream and are merged down)
- **Changes**: must not break `main`'s expected behavior

### 4. Merge Discipline

When merging `upstream/main` into `ai4s-platform`:

1. **Run the config validation script** (above) after every merge
2. **Check for duplicate variable assignments** in merged files:
   ```bash
   cd umrs
   # Find files with duplicate assignments to the same variable
   git diff --name-only HEAD~1 HEAD | while read f; do
     python3 -c "
   import re, sys
   content = open('$f').read()
   assigns = re.findall(r'^(\w+)\s*=\s*', content, re.MULTILINE)
   dupes = [x for x in set(assigns) if assigns.count(x) > 1]
   if dupes:
       print(f'$f: duplicate assignments: {dupes}')
     " 2>/dev/null
   done
   ```
3. **Run the profile tests** (both files exist):
   ```bash
   cd umrs
   PYTHONPATH=src python3 -m pytest tests/test_connection_profiles.py tests/test_config_resolution.py -v
   ```
4. **Run the changed-files gate** from the repo root:
   `bash scripts/ci-lint-local.sh --umrs-changed`

### 5. Profile Resolution Test (exists)

The regression test lives at `umrs/tests/test_config_resolution.py`
(`TestConfigResolution`: the two `DEFAULT_CURRENT_*` constants and the two
`get_current_*_file()` accessors are non-None and equal
`current_connection_profile` / `current_s3_config`; plus
`TestModelStagingRoot` for `get_resolved_model_staging_root()`). Do not add
a second copy; extend that file when the resolution chain grows. It runs in
the umrs component suite (stage 4 of `scripts/ci-lint-local.sh`).

### 6. Deployment Validation

After deploying changes to `umrs/` on `ai4s-platform` (Ansible converge, never a host edit), validate:

```bash
# 1. Config resolution
cd umrs
PYTHONPATH=src python3 -c "
from umrs.shared.config import get_current_connection_profile_file
assert get_current_connection_profile_file() == 'current_connection_profile'
print('✓ Config OK')
"

# 2. Profile use
umrs profile use <profile-name>
# Should succeed, not fail with [Errno 21]

# 3. Info command
umrs info
# Should show profile name, not (none)
# Should show gateway_url, not (not set)   (the field keeps its name; it is the edge URL)
# Should show: mode direct   (umrs-front profiles never use SSH tunnels)
```

## Quick Reference: Affected Code Paths

```
r4 auth login --api-key <JWT>                       (r4/src/r4/_cli/login.py)
  → _populate_args_from_jwt()                        # extracts gateway_url from the JWT
  → _sync_onboard_source()                           # writes the r4 source (r4/src/r4/_cli/onboard.py)
  → sync_umrs_connection_profile()                   # writes the umrs profile (r4/src/r4/source.py)
  → _autodiscover_zmq_ports()                        # queries the edge /health for the ZMQ ports

umrs profile use <name>                             (umrs/src/umrs/shared/connection_profiles.py)
  → set_current_profile_name()
  → get_current_profile_path()
  → get_current_connection_profile_file()            # umrs/src/umrs/shared/config.py — was returning None due to shadowing
  → write_text()                                     # failed with [Errno 21] Is a directory

umrs info
  → resolve_connection()
  → resolve_profile_name()
  → get_current_profile_name()
  → get_current_profile_path()                       # returned Path() (i.e., '.') when the file name was None
  → path.is_file()                                   # False, so profile_name = None
```

## Related Documentation

- [ADR-010 — UMRS submodule branch policy](../../docs/adr/010-umrs-submodule-branch-policy.md)
- Development Instructions
- [Mutation Safety Rules](mutation-safety.instructions.md)
- [CI Harness Rule](ci-harness.instructions.md)
