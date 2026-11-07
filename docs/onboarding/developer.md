# Developer onboarding — day 1 to first PR

**Status:** LIVE — written 2026-10-18 (plan H-11.5), verified against the tree
of that date. Reading order and where each truth lives: [docs/README.md](../README.md).
Rules: [AGENTS.md](../../AGENTS.md) (short) and [CONSTITUTION.md](../../CONSTITUTION.md) (binding).
Human workflow: [CONTRIBUTING.md](../../CONTRIBUTING.md). This file is the one
home of the setup, gate and suite commands.

## 1. Keep daily use apart from development

A console started from a development checkout runs that checkout's working
tree: `r4 console` prefers the Console v3 source of the checkout its `r4` is
imported from (`r4/src/r4/_cli/console.py`, launch order), and the Python
bridge it spawns imports your uncommitted code. For everyday research use a
separate install:

```bash
# own clone in ~/.r4/src and venv in ~/.r4/venv; pin a tag with R4_BRANCH
curl -fsSL https://raw.githubusercontent.com/R-CCS-HPBD/r4/main/scripts/install-r4.sh | R4_BRANCH=<tag> bash
r4 tui install          # release console binary for this host
```

Put that install's `r4` first on your `PATH` for daily work and call the
checkout's `.venv/bin/r4` (or `make` targets) only while developing. Moving
the pinned install forward is a deliberate step: rerun the installer with the
next tag.

`r4 doctor` checks where the running `r4` package comes from (row
`install_source`, added 2026-10-18, plan H-3.5). The pinned install
(`install-r4.sh`'s clone in `~/.r4/src`, installed editable) and a clean
development checkout both pass. With uncommitted changes under the package
directory the row turns into a warning (`–`) naming the count and the path.
`r4 doctor --json` also shows `kind`: `checkout` for any git clone, including
the pinned one, and `installed` for a non-git install such as a wheel. The
warning never fails doctor. If you see it during daily use, check the path it
names: usually your `r4` is the development checkout.

## 2. Development checkout

This section is the one home for setup (2026-10-18). How a change travels
from a pull request to a release is in [CONTRIBUTING.md](../../CONTRIBUTING.md).

Prerequisites: Python 3.11+ (the checkout's `uv.lock` and `.python-version`;
the r4 client alone still supports 3.10, ADR-096), [uv](https://docs.astral.sh/uv/), git (the `umrs`
submodule, `UMRS-org/umrs`, is public), Podman or Docker for
integration tests, bun ≥ 1.3.14 only to work on the console (`Makefile`
`BUN_MIN`).

```bash
git clone --recurse-submodules https://github.com/R-CCS-HPBD/r4.git
cd r4
git submodule update --init --recursive    # only if you cloned without submodules
make venv && source .venv/bin/activate
make install                       # client + server (or: make install-client); also readies packages/r4-tui-v3 when bun is found
# alternative to make venv/install: uv sync --all-packages --all-extras
# (a venv made before 2026-10-21 is 3.10: rm -rf .venv, then sync again; ADR-096)
bash scripts/install-hooks.sh       # links the tracked pre-commit and pre-push hooks (idempotent)
git -C umrs remote add upstream https://github.com/UMRS-org/umrs.git   # the pre-push hook fetches from a remote named "upstream"
```

Maintainers of the private development repository clone from their own
remote instead (and set `R4_REPO_URL` / `R4_RELEASE_REPO` for the installer
and `r4 tui install`).

**umrs remotes (corrected 2026-10-18).** `.gitmodules` names
`UMRS-org/umrs`, branch `ai4s-platform` (flipped from the fork on
2026-09-15, see the rollout block of
[ADR-010](../adr/010-umrs-submodule-branch-policy.md)). In a fresh clone the
submodule's `origin` therefore *is* UMRS-org. The pre-push hook
(`scripts/check-umrs-lineage.sh`) looks for a remote named `upstream`, which
is why the last command above repeats origin's URL under that name.

- **Maintainer layout:** `origin` = the maintainer's fork (push mirror),
  `upstream` = UMRS-org. umrs commits go to both:
  `git -C umrs push upstream HEAD:ai4s-platform`, then the same to `origin`.
- **Contributors without push rights on UMRS-org:** fork umrs, push your
  branch to your fork and open a PR against `UMRS-org/umrs`, branch
  `ai4s-platform`. After it merges, bump the parent pointer to the merged
  commit (or ask the maintainer to). The hook refuses a parent push whose pin
  is not on UMRS-org, and CI fetches the pin from there.

**Hooks** (rewritten 2026-10-18, plan H-3.4). The hooks are tracked in
`scripts/git-hooks/`; `bash scripts/install-hooks.sh` links them into the
shared `.git/hooks/` (one run covers every worktree; the links point at the
main checkout's `scripts/git-hooks/`). The installer is idempotent, prints
what it did, and refuses to replace a hook that is a regular file (an older
hand-made `.git/hooks/pre-commit`, or `pre-commit install`) unless you pass
`--force`, which keeps the old file as `<hook>.local-backup`.

- `pre-commit` (blocking): `bun run check` in `packages/r4-tui-v3` when
  files there are staged, then `scripts/ci-lint-local.sh --files <staged
  paths>` on every commit. It blocks on the gate's own verdict: the
  complexity baseline is the ratchet, while lizard, C901 and xenon stay
  informational as in CI, so pre-existing lizard noise does not block. The
  checks run with every `GIT_*` variable removed, so a test that creates a
  temp repository cannot write the real one (plan H-3.3).
- `pre-push`: refuses a parent push whose umrs pin is not on UMRS-org
  (`scripts/check-umrs-lineage.sh`).

The tracked `.pre-commit-config.yaml` lists five validators at the
`pre-push` stage; all five run in the gate's boundary check, so the
pre-commit hook covers them. Do not run `pre-commit install`: it would
replace these hooks.

**Dependencies and image locks** (2026-10-26,
[ADR-100](../adr/100-dependency-locks-cooldown-refresh.md)). The workspace
`uv.lock` is the one source of versions; the images the converge builds
install committed locks exported from it, with `pip install --no-deps`.

- Add or change a dependency in the member's `pyproject.toml`, then
  `uv lock`, `python scripts/deps/lockctl.py export`,
  `python scripts/deps/lockctl.py derive-umrs` (rewrites `umrs/src/uv.lock`)
  and `python scripts/deps/lockctl.py check`. Commit the umrs half
  (`src/uv.lock`, `docker/server-image/`) in `umrs/` and the rest in the
  parent, as for any umrs change. Never edit a `*.lock.txt` by hand.
  `check` needs uv (offline): each workspace image lock must equal a fresh
  `uv export`; the gateway sidecar and assistant-frontend locks are compiled
  with uv.lock as constraints and must agree with it.
- Once the monthly refresh has run, `pyproject.toml` carries an
  `exclude-newer` cutoff (7-day cooldown): uv resolves nothing newer. For a
  package released after it, `python scripts/deps/lockctl.py allow-new <pkg>`
  first.
- The workflows named below (`security-weekly.yaml`, `deps-refresh.yaml`,
  `server-image-smoke.yaml`) run only in the development repository (noted
  2026-11-07); the scripts they call are in this tree and run locally.
- Security fix (Trivy CRITICAL/HIGH in `security-weekly.yaml` or the CI
  scan): `bash scripts/deps/refresh.sh <pkg>...` locally, or dispatch
  `deps-refresh.yaml` with `packages`; only those packages move, in uv.lock
  and in every image lock. Trivy scans the image locks too
  (`TRIVY_FILE_PATTERNS`).
- The monthly refresh (`deps-refresh.yaml`, the 2nd of each month) opens a
  PR `deps/refresh-YYYY-MM`. CI starts when a maintainer pushes to the
  branch; the umrs half comes as a pushed umrs branch or a patch artifact
  with the commands in the PR body.
- Image smoke test: `bash scripts/deps/image-smoke.sh` builds the server
  image (CPU) and loads the spaCy model in it; `server-image-smoke.yaml`
  runs it on dependency PRs.

## 3. First green gate

```bash
bash scripts/ci-lint-local.sh --full             # whole tree; must print ALL CHECKS PASSED
bash scripts/ci-lint-local.sh --files <changed>  # what you run before every commit
bash scripts/ci-lint-local.sh --umrs-changed     # after umrs edits (git -C umrs add -N new files first)
```

The gate runs the secret scan, ruff lint and format, the complexity gate
(max 9 per function; baselines only shrink), the boundary validators and the
component-scoped tests. If it is red on a file you did not touch, check that
your branch is rebased on `origin/main` before you chase it.

Full suites (run the ones your change touches before a PR):

```bash
.venv/bin/python -m pytest r4/tests -q -p no:cacheprovider
cd packages/r4-tui-v3 && npx tsc --noEmit -p . && bun run test
cd umrs && ../.venv/bin/python -m pytest tests/ --ignore=tests/s3_test/old --ignore=tests/test_rag_pipeline.py -q -p no:cacheprovider
PYTHONPATH=umrs/src .venv/bin/python -m pytest tests/ -q -p no:cacheprovider   # repo root
```

The whole repo-root suite completes (2026-10-18: about 2,600 tests, about two
minutes). This corrects the earlier note that it hangs on three E2E files:
the heavyweight E2E files only skip unless you opt in (`R4_E2E_K8S=1`,
`R4_E2E_COMPOSE=1`, `ASSISTANT_E2E_URL`, or `R4_RUN_E2E=1` for the `e2e`
tier), and `tests/test_root_suite_default_skips.py` keeps it that way. One
exception: `tests/test_research_journey_e2e.py` runs its gateway steps
whenever something answers on `GATEWAY_URL` (default `localhost:8080`), so
a machine with a local stack up runs them against that stack. Test tiers and markers: [TESTING.md](../../TESTING.md). `make ci` fails:
`Makefile` names `lint`, `type-check` and `test` targets that do not exist (a
code fix is pending; noted 2026-10-18); use the commands above.

## 4. First PR

1. Pick an issue, plan item or gap row (`D-nn`) with the maintainers; the
   gap rows are in
   [the gap analysis](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md).
2. Work in your own worktree, one per session (plan H-3.2):
   `git fetch origin && git worktree add ../r4-<slug> origin/main -b <topic>`.
   If the change touches umrs, initialise the submodule there
   (`git -C ../r4-<slug> submodule update --init umrs`). Never run two
   sessions, human or agent, in one checkout.
3. Write the test that fails on the old code, then the fix.
4. Gate (the installed pre-commit hook runs it on every commit), then
   update in the same commit the docs your change makes wrong (dated) and
   the gap-analysis row. Maintainers also record the change in the
   development records of the private repository when they port it.
5. Fetch and rebase on `origin/main`, push `<topic>`, open the
   PR and fill in [the template](../../.github/pull_request_template.md).
6. **Proposed convention, pending maintainer decision M-H8** (plan H-3.1):
   one PR per wave; merge only on green CI, with a merge commit so the ledger
   entry stays with its change (squash is fine for a one-commit wave). Until
   the maintainer decides M-H8, agent waves that the maintainer grants push
   to `main` directly; as a contributor, open a PR.
7. After the merge: `git worktree remove ../r4-<slug>` and delete the
   branch.

Never: ad-hoc SSH deploys or host edits (Ansible only), credential handling,
`git stash`, blanket `git checkout -- .`, force-push.

## 5. Where to ask

- The maintainer for scope, authority and anything
  marked **[M]** in a plan. Without another channel, open a GitHub issue
  that says it is a scope question (the repository has no issue templates).
- A GitHub issue for bugs, with profile, cluster and versions; read
  [troubleshooting](../getting-started/troubleshooting.md) first.
- Escalations that block your work go by name into the PR or issue, never
  only into chat.
