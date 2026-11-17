# ADR-117: One UMRS branch: UMRS owns its protocol, r4 support is an extension, umrs CI/CD lives in the umrs repository

**Status:** Accepted (2026-11-13). The maintainer said "good. proceed" on revision 2 of the plan; the details were decided by the agent under the 2026-09-24 delegation. The UMRS `main` co-maintainer **accepted on 2026-11-13** (acceptance relayed by the maintainer; plan M-U2).
**Date:** 2026-11-13
**Relates to:**
- [ADR-010](010-umrs-submodule-branch-policy.md) (umrs branch policy): amended. One branch replaces the `main` / `ai4s-platform` pair.
- [ADR-005](005-r4-contracts-stdlib-only.md) (`r4-contracts` stdlib-only): amended. The UMRS-side contracts move to `umrs-protocol` under the same rules.
- [ADR-052](052-one-gate-every-tree.md) (one gate, every tree): amended. The umrs gate runs in the umrs repository.
- [ADR-107](107-code-ops-repository-split.md): site files leave the code repositories (O-3).
- ADR-045: `site.yaml` is the venue contract.
- ADR-097: project membership.
- ADR-112: gated delegation.
- Gap row D-04 (the R4–UMRS protocol).

## Context

Verified on 2026-11-13 with umrs `main` `92b4247a`, `ai4s-platform` `bb4c2d6e` and parent `be1196f1`. Plan §1–§4 has the commands.

- **UMRS has two branches that have diverged.**
  - Since 2026-06-18, 95 commits exist only on `main` and 1090 only on `ai4s-platform`; 405 are on both as cherry-picks.
  - A trial merge conflicts in 352 files.
  - ADR-010 meant changes to flow `main → ai4s-platform` by merge. In practice the branches were reconciled only by cherry-picking.
- **Neither branch works without the R4 repository.**
  - `main` requires `r4-contracts` (`src/pyproject.toml:20`, four modules, the server Dockerfile, `setup_server.sh`).
  - `ai4s-platform` imports `r4_contracts` in 59 places across 43 src files and 113 test files, and imports `r4_mcp`.
  - It reads about 40 `R4_*` env names and `x-r4-*` headers, and has RIKEN host defaults in code.
  - Its server image builds from the parent repository.
- **Most of `r4-contracts` is really UMRS code.** UMRS uses 23 of its 36 modules, which hold 85% of its lines (for example `hpc_gate`, 7015 LOC). The R4 client is the minor user.
- **The R4 runtime that UMRS hosts is small:** companion sessions, the venue worker, server-hosted loops, the plan-run venue, `r4` CLI probes and the `r4_mcp` verifier. Together they are about 12.4k of umrs's 292k Python lines.
- **r4 runs all of umrs's CI/CD.**
  - 15 of r4's 19 workflows touch umrs.
  - `test-umrs` fetches UMRS-org through a deploy key the org disables, falling back to a classic PAT.
  - The umrs lint and complexity baselines live in r4's `tools/`.
  - The umrs image is published under the r4 owner.
- **The UMRS-org repository has nothing of its own yet.** UMRS-org/umrs is public, Actions is enabled, the maintainer has repo admin, and `main` is unprotected. It has no CI of its own.

The collaborator asked to merge the two branches and suggested a plugin system for r4. The maintainer asked for umrs's CI/CD to move into the umrs repository.

## Decision

- **D-1 One branch.**
  - `UMRS-org/umrs@main` is the single UMRS trunk.
  - `ai4s-platform` becomes the merge source once (wave U5: a PR with `-s ours` mechanics after the port and the decoupling; `main` is tagged `archive/main-pre-merge-2026-11` first; never a force-push). It is then frozen as a tag when r4 pins `main` (U6).
- **D-2 UMRS owns its protocol.**
  - The wire contracts UMRS serves move to `umrs-protocol` (import name `umrs_protocol`, directory `protocol/` in the umrs repo). It is stdlib-only, with ADR-005's rules carried over.
  - `r4-contracts` keeps its public module names as re-exports and depends on `umrs-protocol`.
  - Wire bytes do not change: HMAC claim tuples, `READY!:v1.x`, `x-platform-error-code`.
  - The HPC gate script that r4 installs byte-identical is copied from `umrs_protocol/hpc_gate.py`.
  - **Rollout is incremental:** `umrs-protocol` plus the `r4_contracts` shim first, then umrs imports switch one module family at a time behind an import-linter allowlist that only shrinks. There is no single long freeze.
  - The `r4-contracts` modules UMRS never uses stay in `r4-contracts`.
- **D-3 Core never imports r4.**
  - Package `umrs` imports none of `umrs_r4`, `r4_contracts`, `r4_mcp`, `r4`.
  - `umrs_protocol` imports only the standard library.
  - import-linter contracts in umrs CI enforce both.
- **D-4 r4 support is an extension.**
  - The distribution `umrs-r4` (`plugins/umrs-r4`, import `umrs_r4`) registers through the entry-point group `umrs.extensions`.
  - Core provides an `ExtensionContext` with hooks for:
    - HTTP routers (by family; a name collision fails at boot);
    - ZMQ commands (through `build_registry`);
    - durable job types (by their stored string values, which never change);
    - verifiers and venue backends;
    - an extension migration track.
  - Existing migrations stay in core.
  - Extensions are enabled by `site.yaml` `extensions: [...]` or `UMRS_EXTENSIONS`, and the active set is logged at boot.
  - **The API is internal and minimal:** only the hooks `umrs-r4` needs, as measured by the U3a dependency map. It is versioned (`EXTENSION_API_VERSION`, checked at boot) and is not advertised as a public plugin API until a second extension exists.
  - The extension migration track has its own version table. CI tests the upgrade paths core-only → enabled, enabled → disabled, and disabled → re-enabled.
  - The word is "extension" because `umrs.plugins` already names the sandboxed user-code runner.
- **D-5 The core/extension line.**
  - Core is everything a UMRS-only deployment can use: projects and membership, grants and scopes, workflows and versions, knowledge and KG, retrieval, jobs and orchestration, serving, vision, storage, usage, and the backbone side of ADR-112 and `hpc_accounts`.
  - The extension holds only code that runs R4's own runtime. Borderline files are decided by that test in wave U3.
- **D-6 Neutral names.**
  - Code reads `UMRS_*` env names first and falls back to `R4_*` for one release. The `R4_*` names handed to child jobs that run R4 code are still set.
  - `x-umrs-*` headers are accepted alongside `x-r4-*`.
  - No site or host default in code (ADR-045).
- **D-7 umrs CI/CD lives in UMRS-org/umrs.**
  - Workflows: `ci.yml`, `release.yml`, `deps.yml`, `security.yml`.
  - `ci.yml` has a core-only job with no extension installed, an extension job, the import contracts, the lint/complexity ratchets with their baselines moved from r4 `tools/`, fresh and upgrade migrations, an image build, and one required check, `ci-success`.
  - Everything runs on GitHub-hosted runners with no secrets. **No self-hosted runner is ever attached to the public repository.**
  - r4 keeps its integration and ops workflows.
  - r4's lineage check requires the pin to be on umrs `main` with a green `ci-success`.
  - umrs CI runs against real Postgres + pgvector and an S3 service from day one, with a skip gate (a committed allowlist that only shrinks), so DB- or S3-backed tests cannot pass by skipping.
  - r4's `test-umrs`, `build-images-umrs`, umrs lint leg and the umrs half of deps-refresh are deleted only after an **agreement check**: 10 consecutive umrs shas with the same test ids and outcomes in both pipelines (JUnit diff; a reviewed allowlist for differences), and no more skips in umrs CI than in r4.
  - ADR-052's umrs gate runs in the umrs repository.
- **D-8 Publishing and consumption.**
  - umrs publishes wheels to GitHub Releases and images to `ghcr.io/umrs-org/*` by digest; PyPI comes later, through trusted publishing.
  - r4 keeps the submodule until ADR-107 O-2/O-3 lands, then pins artifacts (`umrs.lock`). At that point `UMRS_DEPLOY_KEY` and `UMRS_HTTPS_TOKEN` are removed (a maintainer leg).
- **D-9 Shared rule, effective now.**
  - No new `r4_contracts` or `r4_mcp` import modules, `R4_*` env names, `x-r4-*` headers or site defaults in umrs.
  - Once U3 lands, new R4-runtime code goes into `plugins/umrs-r4`.

### Why

- A backbone that depends on its client cannot be deployed or released on its own, and every wire change needs releases from two repositories. Inverting the dependency (D-2) fixes the direction without changing a byte on the wire.
- Making the contracts optional instead would stub HMAC and HPC-gate checks: fail-open on the request path. So the contracts are a package, not a plugin. Only the code that runs R4's runtime is a plugin.
- Two long-lived branches have already drifted by 352 files of conflicts in five months. One trunk with a hard import fence keeps the separation without a second tree.
- CI in the repository that owns the code removes the cross-org credential workaround and lets UMRS-only contributors get a verdict without r4 access.

## Consequences

- **Positive:**
  - UMRS installs, tests, builds and releases from its own repository.
  - The R4 fronts install `umrs` + `umrs-r4`; other centres install `umrs` alone.
  - The deploy-key/PAT dance and its secrets go away.
- **Cost:**
  - U2 rewrites about 156 import sites, and U3 moves about 12.4k lines behind new core hooks. Both need freeze windows with the sessions writing umrs.
  - r4 CI runs umrs twice during the soak.
  - The extension adds a registry indirection in the sidecar, the command registry and the job runtime.
- **Not in scope:**
  - The history scrub of public umrs (cleanup M-1, release P4).
  - Retiring the fork's orphan `main` (a maintainer force-push, plan M-U6).
  - Moving the site files themselves (ADR-107 O-3).

## Alternatives Considered

- **Keep `r4-contracts` as a published dependency.** Least churn, but UMRS stays versioned against a client-named package that is 85% UMRS logic, and every protocol change stays a two-repo release.
- **Optional `umrs[r4]` extra with import guards in core.** Rejected for the contracts (fail-open security); kept, in a stronger form, as the D-4 extension for the runtime pieces.
- **Two permanent branches with a sync bot.** It is the current drift, automated.
- **A separate `main-independent-from-r4` branch** (plan revision 1). Superseded the same day by the collaborator's merge request (D-1).

## Rollout-status (2026-11-13)

- **U0:** this ADR. Plan: umrs-independent-main-plan-2026-11-13.md, revision 2; ledger (282); STATE `UMRS-20261113-MAIN`.
- **U1** (port of the upstream co-maintainer's post-2026-09-24 work into `ai4s-platform`) is in progress.
- **U2–U7** are open. **U3a** (a read-only dependency map of the U3 candidates) was added before U3.
- **Review (same day).** An external review of the plan was incorporated: U3a, a minimal internal versioned extension API, extension migration upgrade paths, the incremental U2, the soak as a verdict-agreement check, the co-maintainer asked before U2, and real DB/S3 plus a skip gate in the new CI.
- **M-U2 closed:** the co-maintainer accepted ADR-117 on 2026-11-13, so U2 may start after U1.
- **Maintainer legs:** M-U6, M-U7.

## Related

- Plan §5–§10: target, extension hooks, CI/CD split, merge mechanics, waves, decisions.
- [developer.md §2](../onboarding/developer.md): the umrs remotes. It changes in U6.
- `scripts/check-umrs-lineage.sh`: it changes in U4/U6.

## Rollout-status (2026-11-13, U3a)

U3a, the read-only dependency map, is done: umrs-u3a-dependency-map-2026-11-13.md. It was a 256-agent workflow (mappers, a completeness critic, two skeptics per classification) on umrs `610c5c2f`. Decided under delegation:

- **D-5 revised: hosted engine loops are core.**
  - The Context bullet above counted "server-hosted loops" as R4 runtime, but the code says otherwise. `POST /umrs/loops` drives UMRS's own `AutonomousResearchLoop` through the UMRS workflow port and the site LiteLLM, and core modules import it. By D-5's own test it is core.
  - Only the r4 plan-as-iteration leg (`plan_iteration`, `plan_run_records`, the traces plan-run block) moves.
  - `research_sessions`, `research_loop_job`, `session_gc` and the approval inbox stay core.
- **D-4, the minimal API: six hooks:** `add_router`, `add_job_type`, `add_startup`, `add_loop_iteration_kind`, `add_session_archive_participant`, `add_approval_listener`.
  - The candidates `add_command`, `add_verifier` and `add_venue_backend` are not needed.
  - The extension migration track remains for new r4-only tables.
- **D-2 narrowed:** `team_engine` and `loop_plan` are used only by code that moves, so they stay in `r4-contracts`, which the `umrs-r4` extension depends on. `umrs_protocol` gets the rest of the closure.
- **D-6 hardening:** `x-umrs-*`/`x-r4-*` header aliasing lives in core, never in an extension hook. Two guards treat a missing actor header as "person", so they would fail open without the extension.
- **Claims of extension job types on the shared `umrs_db`:** an unregistered type is skipped at claim time and logged once. After the janitor's pending limit it fails with `extension_not_enabled`.
- **Move order:** U3 runs as steps S0–S6 (S0 = the U2 import switch). About 12.6k lines move.
- **Open, a maintainer leg (map D6):** whether to delete or move the companion `/workers`, `/chat`, `/turns` surface. It depends on whether live `companion_worker` job rows exist.

## Rollout-status (2026-11-14, U1 and U2.0a deployed; U2.0b built)

- **U1** (the upstream co-maintainer's post-2026-09-24 `main` work) reached main via #877 (umrs `3c9b5627`) and is deployed.
- **U2.0a** (#878): `umrs-protocol` is installed everywhere but not yet used, live on all four fronts at main `35e3ea27` (umrs `f96c79d4`), ledger (295).
- **U2.0b** (branch `wave/umrs-u2-0b`, umrs U-A1 `10fd5d6e`, ledger (297)): the 16 closure modules are alias shims; r4-contracts and r4 depend on `umrs-protocol>=0.2.0rc4`; fence (a); `hpc_signing.gate_source()` reads `umrs_protocol`; release coherence and image identity asserts; umrs declares `umrs-protocol` (U-A1).
  - Gate sha `771d9c91…abf3c` is unchanged.
  - Deviations: the fence requires exactly one `>=` clause; the golden test compiles the three sidecar signers via AST; the `--flipped` proof runs in test-umrs, test-r4 and test-root only; `cut.sh` does not yet bump the floor in `umrs/src/pyproject.toml` or re-derive `umrs/src/uv.lock` (follow-up before the next cut).
  - PyPI holds 0.0.0 name reservations for `umrs-protocol`, `r4-contracts` and `umrs` (maintainer account), so no index install can pick a squatted package.
- **Next: U3** (after U2.0b merges and the staged converge), then U4–U7.
