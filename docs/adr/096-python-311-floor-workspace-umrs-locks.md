# ADR-096: Python 3.11 floor for the workspace and umrs locks

**Status:** Accepted (2026-10-21, maintainer decision relayed by the stabilization coordinator; agent-written record)
**Date:** 2026-10-21
**Relates to:** platform hardening plan H-0.5 (security floors in `umrs/src/requirements.txt`), PR #509 (Security Scan red on `main`), ADR-054 (umrs in the uv workspace)

## Context

The Trivy DB update of 2026-10-21T01:19Z added a CRITICAL advisory
(CVE-2026-112403) and six HIGH ones (CVE-2026-112397, -112400, -112404,
-112895, -112896, -112897) against pillow 12.3.0. All seven are fixed only in
pillow 13.0.0, which declares `requires-python >=3.11`. There is no 12.x
backport. The required Security Scan job (`.github/workflows/ci.yaml`, Trivy
gate with `exit-code: 1`) scans both `uv.lock` and `umrs/src/uv.lock`, and the
converge apply refuses a red `AI Platform CI`. So this blocked the production
deploy.

Every pyproject in the workspace declared `requires-python >=3.10`, and
`.python-version` pinned 3.10. uv therefore forked both locks: pillow 13.0.0
for Python 3.11 and newer, and 12.3.0 for 3.10. Trivy still flags the 3.10
copy. CI already runs Python 3.11 (`PYTHON_VERSION: "3.11"` in `ci.yaml`), and
the production images are 3.11.

The r4 client is installed with pip, not with the lock. `scripts/install-r4.sh`,
`make install-client` and the README quick start all run
`pip install -e umrs/src[client]` next to `r4`. Raising
`umrs/src/pyproject.toml`'s `requires-python` would make pip refuse that install
on Python 3.10, which would drop 3.10 for researchers' client installs.

## Decision

- **D-1.** The workspace root `pyproject.toml` declares
  `requires-python = ">=3.11"`, and `.python-version` is `3.11`. uv takes the
  strictest member bound, so `uv.lock` resolves for 3.11 and newer only, with
  one pillow (13.0.0). Members whose own bound is wider (`r4`, `r4-contracts`,
  `science`, `mcp-servers`, `packages/*`, `engines/*`) keep `>=3.10`.
- **D-2.** `umrs/src/pyproject.toml` keeps `requires-python = ">=3.10"`, so pip
  installs of `umrs/src[client]` keep working on 3.10. It gains
  `[tool.uv] environments = ["python_version >= '3.11'"]`. This setting limits
  only uv's resolution of `umrs/src/uv.lock` (`supported-markers`), and pip
  ignores it.
- **D-3.** `umrs/src/requirements.txt` (the server install path) keeps
  `pillow>=12.3.0` and adds `pillow>=13.0.0; python_version >= "3.11"`. A
  3.11+ host is upgraded to the fixed version, and a 3.10 server host still
  installs.
- **D-4.** The legacy `umrs/docker/Dockerfile` moves from `python:3.10-slim` to
  `python:3.11-slim`. The development checkout (`Makefile` header,
  `docs/onboarding/developer.md`) needs Python 3.11. The r4 client still
  supports 3.10.

### Why

The CVE fix exists only for 3.11 and newer. The lock is a development and CI
artifact, and both already run on 3.11. The researcher-facing client must
not lose 3.10 because of a server-side image library. Without D-2, the next
"tidy-up" that aligns `umrs/src`'s `requires-python` with the lock would
silently break `install-r4.sh` on 3.10.

## Consequences

- Positive: both locks resolve pillow 13.0.0 only, and the Trivy gate is green.
  The 3.10-only lock forks (async-timeout, exceptiongroup, tomli, pytz, older
  numpy/scipy/pandas) are gone.
- Cost: every developer recreates the checkout venv on 3.11:
  `rm -rf .venv && uv sync --all-packages --all-extras` (uv reads
  `.python-version`; add `--python 3.11` if uv cannot find one).
  A 3.10 server host installed from `requirements.txt` keeps pillow 12.3.0 and
  stays exposed until it moves to 3.11.
- `umrs/src/uv.lock` is still built from the umrs pyproject at 235fe48a. The
  current one depends on the workspace-only `r4-contracts`, which is not on a
  package index, so it cannot be locked standalone. The environments line was
  applied to that copy too. Nothing installs from this lock; repo cleanup
  decides whether it stays.
  *(2026-10-26, [ADR-100](100-dependency-locks-cooldown-refresh.md) D-6:
  `umrs/src/uv.lock` is now derived from the workspace `uv.lock` by
  `scripts/deps/lockctl.py derive-umrs`, so the two locks hold the same
  versions; the server image installs a lock exported from the workspace
  lock.)*
- NOT in scope: ruff `target-version = "py310"` and mypy `python_version =
  "3.10"` stay, because r4 code must keep running on 3.10.
  `deploy/ansible/group_vars/all.yaml` `python_version` and `TESTING.md` are
  unchanged.

## Alternatives Considered

- **Raise `umrs/src/pyproject.toml` to `>=3.11`.** This was the first form of
  the decision. Rejected because pip would refuse `umrs/src[client]` on 3.10
  and break the client installers.
- **`.trivyignore` the 3.10 pillow copy.** Rejected: it mutes a CRITICAL
  advisory that has a fix.
- **Wait for a pillow 12.x backport.** Rejected: none is announced, and the
  deploy is blocked now.

## Rollout-status (2026-10-21)

Landed on `wave/aiohttp-cve` (PR #509) together with this ADR: both locks show
pillow 13.0.0 only; local Trivy 0.69.3 with a fresh DB reports 0 findings.

## Related

- [ADR-054](054-workspace-membership-and-test-tiers.md)
- `umrs/src/requirements.txt` (H-0.5 security floors)
