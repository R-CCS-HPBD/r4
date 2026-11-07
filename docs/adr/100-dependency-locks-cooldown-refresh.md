# ADR-100: Dependency locks for images, a release cooldown and a monthly refresh

**Status:** Accepted (2026-10-26, maintainer request "yes please. also we need to update the pinned version in reasonable frequency"; open choices decided by the agent under delegation; wave (84), `wave/server-image-lock`)
**Date:** 2026-10-26
**Relates to:** wave ledger (81) (the preshed 3.0.14 incident), ADR-096 (Python 3.11 floor; umrs/src/uv.lock), ADR-054 (umrs in the uv workspace), ADR-090 (stand-alone CPU build), H-0.5 (Trivy gate)

## Context

`umrs/docker/Dockerfile.server` pip-installed an unpinned package list and ran
`python -m spacy download en_core_web_sm || true`. Neither `uv.lock` nor
`umrs/src/uv.lock` was used. The converge rebuild of 2026-10-25 picked up
preshed 3.0.14, released days before; `spacy.load("en_core_web_sm")` raised
`OverflowError` in `Tokenizer._flush_specials`, and the rccs Text Embedding
experience lost its text modalities. The hotfix (umrs `d41165f5`, deployed as
main `59aeadbb`) pinned only the spaCy stack.

Verified 2026-10-26: no CI job builds this image (every image job in
`ci.yaml` is `if: false`), so the converge on umrs-front was the first build
to see a new release. The Trivy gate scans both locks, but nothing installed
from them: a CVE bump in a lock did not reach the server image. The two locks
disagreed (umrs/src/uv.lock: spacy 3.8.14, torch 2.11.0, umrs 0.1.0; the
workspace lock and production: spacy 3.8.16). umrs/src/uv.lock cannot be
re-locked on its own, because its `r4-contracts` dependency is on no index
(ADR-096). The worker image (`r4/Dockerfile`), the gateway auth sidecar and
the assistant frontend, all built by the same converge, resolved their
dependencies at build time too.

## Decision

- **D-1. The workspace `uv.lock` is the one source of versions.** Every image
  the umrs-front converge builds from a pip list installs a committed lock
  with `pip install --no-deps -r <lock>` and ends with `pip check`; no
  Dockerfile names a bare package. `scripts/deps/lockctl.py export` writes:
  - `umrs/docker/server-image/requirements.lock.txt` (`uv export --package umrs
    --extra server`), plus `requirements-cpu.lock.txt` and
    `torch-cpu.lock.txt` for `TORCH_INDEX_URL` (the CPU build installs the
    LOCKED torch/torchvision versions from the CPU index and drops the
    CUDA-only packages, found with `uv export --prune torch`).
  - `r4/docker/requirements.lock.txt` (root extra `r4-worker-image`).
  - Non-workspace images, compiled with `uv pip compile --universal`:
    `gateway/sidecar/requirements.lock.txt` (from `requirements.txt`) and
    `deploy/ansible/roles/front_end/files/assistant-frontend.requirements.lock.txt`
    (from `assistant-frontend.requirements.in`). Every uv.lock version is a
    constraint (`-c`, from `uv export --all-packages --all-extras
    --all-groups`), and the compile uses uv.lock's cutoff and exemptions, so
    the two locks agree with uv.lock on every package uv.lock also holds
    (`lockctl.py check` fails otherwise): a uv.lock bump reaches them and
    uv.lock's scan covers those packages. Only packages outside the
    workspace (python3-saml, xmlsec, ...) are resolved freely, at the cutoff.

  `lockctl.py check` is two-way: each workspace image lock must be exactly
  what `uv export --frozen` emits now (offline; needs uv), so a lock that
  lacks a package fails, not only one that pins a wrong version.

  The server image's lock lives in umrs, so a lock change is a umrs change
  and the converge rebuilds the image (it rebuilds on a umrs sha change only).
  The worker lock lives under `r4/`, which the worker's rebuild hash covers.
- **D-2. The spaCy model is pinned** by URL and sha256 in the server lock
  (`en_core_web_sm` 3.8.0); `spacy download` is gone. `preshed!=3.0.14` is a
  known-bad exclusion in umrs's `server` extra. `asyncpg`, which the image
  installed by hand, joined that extra; `posix_ipc` and `configparser` were
  dropped (no import anywhere; `configparser` is the standard library).
- **D-3. The image proves itself.** `umrs/docker/server-image/smoke.py` imports
  the server's key libraries and umrs's boot modules and loads
  `en_core_web_sm` the way `EmbeddingModel` does. Dockerfile.server runs it as
  its last build step, so a broken set fails the converge build before any
  container is replaced. CI: `server-image-smoke.yaml` builds the CPU variant
  and runs the smoke test offline, on PRs that touch the image's dependencies
  (decided by `scripts/deps/image-smoke-needed.sh`) and by dispatch.
- **D-4. Release cooldown, monthly refresh.** `deps-refresh.yaml` runs on the
  2nd of each month (and the 9th, a no-op when that month's branch exists):
  `lockctl.py cooldown` writes `exclude-newer` = run date minus 7 days into
  the root `pyproject.toml`, plus an `exclude-newer-package` exemption for
  every package whose LOCKED version is newer than that (pinned at its own
  upload time, so the refresh never downgrades a deliberate recent bump; an
  earlier `allow-new` exemption is kept until the cutoff passes it); then
  `uv lock --upgrade`, `lockctl.py export --upgrade` (the compiled locks get
  the same cutoff and exemptions on the command line), `derive-umrs`,
  the lock tests and the image smoke test; then a branch `deps/refresh-YYYY-MM`
  and a PR. It never pushes to main or to UMRS-org `ai4s-platform`. The
  cutoff lives in pyproject.toml (not on the uv command line) because uv
  re-resolves a lock whose cutoff setting differs: a later plain `uv lock`
  or `uv sync` would otherwise upgrade everything to the newest releases.
- **D-5. Security fast path stays fast.** `security-weekly.yaml` (Sundays and
  Wednesdays) and the CI Security Scan gate CRITICAL/HIGH findings on both
  uv.lock files and on every image lock: Trivy's pip analyzer reads only
  files named `requirements.txt`, so both Trivy steps set
  `TRIVY_FILE_PATTERNS='pip:\.lock\.txt$'` (verified with trivy 0.69.3,
  2026-10-26: without it no image lock is scanned; with it the sidecar lock
  as first committed reports GHSA-6q48-6c7w-9gw2 CRITICAL). The fix is
  `bash scripts/deps/refresh.sh <package>...` or `deps-refresh.yaml`
  dispatched with `packages`: `lockctl.py allow-new` exempts only those
  packages up to now, `uv lock --upgrade-package` moves only them,
  `lockctl.py export --upgrade-package` moves them in the compiled locks
  (exempt up to now, also when uv.lock does not hold them), and the same
  tests, smoke test and PR follow. Before the first cooldown run,
  `allow-new` does nothing (uv would re-resolve every package without a
  global cutoff), and plain `uv lock --upgrade-package` is the fast path.
- **D-6. umrs/src/uv.lock is derived, not resolved.** `lockctl.py
  derive-umrs` locks a scratch copy of umrs's pyproject with every workspace
  version as a constraint and `r4-contracts` as a path source, so the two
  locks hold the same versions (`lockctl.py check` fails otherwise). Pushing
  that half needs write access to UMRS-org/umrs, which the repository does
  not have: the optional secret `UMRS_LOCK_PUSH_TOKEN` (maintainer leg)
  lets the refresh push a umrs branch `deps/refresh-YYYY-MM`; without it the
  umrs commit is uploaded as the `umrs-refresh-patch` artifact and the PR
  body gives the exact commands; a push that fails (revoked or under-scoped
  token) degrades the same way with a warning. When only the umrs half moved,
  an issue carries the commands instead of a PR. The checkout does not
  persist `GITHUB_TOKEN` (`uv lock --upgrade` may build sdists on the
  runner); `propose.sh` gives each token to its one push as an env-scoped
  header, never in a URL. The parent lineage check stays as it is.
- **D-7. Images left as they are** (not built by the umrs-front converge):
  the rag/mcp frontends (converge tasks disabled), the legacy
  `umrs/docker/Dockerfile`, `Dockerfile.umrs-api-test`,
  `Dockerfile.assistant`, the vision-service images, `mcp-servers/Dockerfile`,
  test mocks, demos and examples. `tests/test_image_dependency_locks.py`
  classifies every Dockerfile in the tree, so a new one forces a decision.

### Why

A lock that nothing installs protects nothing, and a lock without a refresh
rots into the next CVE. A cooldown trades a week of latency on ordinary
upgrades for not being the first to install a broken release; the fast path
keeps security fixes out of that trade. The build-time smoke test and the
`pip check` make a broken set fail where it is cheap, not in production.

## Consequences

- Positive: the converge rebuilds the server image from the same versions CI
  smoke-tested; Trivy findings on the locks now describe the images; the
  incident's set (preshed 3.0.14) cannot return silently.
- The first lock keeps today's versions (the workspace lock plus the
  production spaCy stack: cymem 2.0.16, srsly 2.5.4, murmurhash 1.0.16); no
  cutoff is configured until the first refresh, which will move ~150
  packages (dry run 2026-10-26: resolution, lock check and the CPU smoke
  test pass at the 2026-10-19 cutoff; spaCy stack unchanged).
- Cost: a monthly review PR; the smoke build (~2.5 GB image, minutes of
  runner time) on dependency PRs; a developer adding a package newer than the
  cutoff adds an exemption (`lockctl.py allow-new`).
- PRs opened with `GITHUB_TOKEN` start no workflows: the maintainer's push to
  the branch starts CI.
- NOT in scope: base-image digests (`python:3.11-slim` still floats by tag),
  the build backends pip fetches for in-repo packages, the CI test installs
  from `umrs/src/requirements.txt` and `gateway/sidecar/requirements.txt`
  (CI tests floor-pinned sets, not the image's), hashes in the locks
  (`--no-hashes`; the model wheel is hash-pinned), hosts installed by
  `setup_server.sh`, and the hash-pinned step image
  (`umrs/docker/step-requirements.txt`, compiled by hand).

## Alternatives Considered

- **Lock the server image from umrs/src/uv.lock.** It cannot be re-locked on
  its own (r4-contracts) and had drifted from production; deriving it from
  the workspace lock keeps one resolution.
- **Keep the server lock in the parent repo.** The refresh would then need
  no umrs push, but the converge rebuilds the image only on a umrs sha
  change, so a parent-only lock change would never reach the running image.
- **uv inside the image (`uv pip sync`).** Adds a tool to pin and keep
  current; pip with `--no-deps` on an exported lock is enough.
- **Pass `--exclude-newer` on the command line only.** uv records it in the
  lock and the next plain `uv lock`/`uv sync` drops it and upgrades every
  package (tested 2026-10-26).
- **Dependabot/Renovate.** Not set up for this repository; neither knows the
  umrs submodule split or the image smoke test.

## Rollout-status (2026-10-26)

- D-1..D-3, D-6, D-7 landed on `wave/server-image-lock` (umrs branch
  `server-image-lock`). Local builds (podman, CPU torch index): server image
  2.54 GB, `pip check` clean, smoke test PASS offline; with preshed 3.0.14
  forced into that image the smoke test exits 1 with the incident's
  `OverflowError`. Worker, sidecar and assistant images build with
  `pip check` clean and import their services.
  Tests: `tests/test_image_dependency_locks.py`,
  `umrs/tests/test_server_image_installs_from_lock.py`.
- D-4, D-5 landed as workflows; first scheduled run 2026-11-02.
- Review of 2026-10-26 (two reviewers): the compiled sidecar and
  assistant-frontend locks had been resolved at the cutoff without
  exemptions and pinned certifi 2026.10.18, inside GHSA-6q48-6c7w-9gw2
  (CRITICAL; uv.lock had moved to 2026.10.19 on purpose); no scan read them
  and the fast path could not move them. Fixed as D-1/D-5 now say; the
  re-compiled locks follow uv.lock (the sidecar moves back from build-time
  newest to the workspace versions; its 135 gateway tests pass on the
  locked set, `pip check` clean; the assistant service imports on its
  set). Also fixed: the one-way `lockctl check`, a failed umrs push reported
  as pushed (`$(...)` turned errexit off), a umrs-only refresh failing on an
  empty parent commit, the persisted checkout token, and a second monthly
  PR after the first was merged and its branch deleted.
- Not deployed. Maintainer legs: converge check then apply; optional secret
  `UMRS_LOCK_PUSH_TOKEN`; repository setting "Allow GitHub Actions to create
  and approve pull requests".

## Related

- [ADR-096](096-python-311-floor-workspace-umrs-locks.md)
- [ADR-090](090-standalone-single-host-deployment.md)
- [docs/onboarding/developer.md §2](../onboarding/developer.md) (dependency locks)
