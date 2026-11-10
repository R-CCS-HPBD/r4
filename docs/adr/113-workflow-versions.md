# ADR-113: Workflow versions: the version is a field of the definition, every registered version is kept, and users choose one when they show or run a workflow

**Status:** Accepted (2026-11-08).
- **Maintainer direction:** "the versioning should be a parameter of each workflow definition. umrs need to store every versions registered and user should be able to select the version when they indicate or run them".
- **Agent's part.** The agent drafted this ADR and chose the details under the standing delegation, marked *(agent decision)*.

**Date:** 2026-11-08
**Relates to:**
- [ADR-074](074-uploaded-step-code-runs-sandboxed.md): uploaded code; D-8 owner-scoped resolution.
- [ADR-080](080-one-access-guard-shareable-resources.md): workflow access lists.
- [ADR-105](105-umrs-agent-capability-layer.md): the plan/commit signature (the known limit "signs the catalog version, not a hash", D-4 here).
- [ADR-084](084-growth-governance.md): D-4 `workflow@version` run key; Proposed.
- [ADR-079](079-researcher-self-service-data-path.md): self-service upload.
- Wave ledger, this entry.

## Context

Verified 2026-11-08 at parent `a647164c`, umrs `2f678503`.

**What exists.**
- **A version field, unused.** The catalog already keys entries by (name, version, scope, owner) (`umrs/src/umrs/server/workflow_registry_handler.py:128-221`), and the upload reads `version` from `workflow.json`, defaulting to `"0.1"` (`api/workflows.py:1623-1636`). Nothing else uses it.
- **One directory per name.** Every version's files share one directory, `<state>/extracted/<owner>/<name>` (`server/file_handling.py:208-226`). A re-upload extracts over it without clearing it (`:314-315`). So an older version's catalog entry points at the newest code plus leftover files, and **no older version can be run as registered**.
- **The oldest version wins.** Lookup takes the first matching entry in file order (`workflow_registry_handler.py:537-582`), which is the first-registered version, while the code on disk is the newest.
- **No surface takes a version.** Not the REST trigger, plan, describe, package, export or delete; not ZMQ `RUN_WORKFLOW`; not the r4 SDK or CLI, companion tools, MCP tools, TUI v3 or the VS Code extension. The r4 contract `WorkflowCatalogItem` drops the field.
- **Runs record no version or code digest.** This holds for the job spec, `ExecutionRecord`, `workflow_executions` and the manifest (`pipeline_version: ""`). `workflow_outcomes.workflow_version` is never written.
- **Steps read live code.** They read code from the shared directory when they run, so an upload during a run, or a retry, can run new or mixed code.
- **Ingest triggers fire once per registered version** (`server/workflow_triggers.py:230-262`).
- **New versions start private** (`access: []`) while sharees keep resolving the old entry.
- **Run definitions** (the reproducibility record) pin only the name.

**The consequence today.** Authors put the version in the **name** (`example-cnn-track-v0.2.9`). The production catalog holds such names, and every old name keeps its own triggers.

## Decision

**D-1. The version is part of the definition.**
- `workflow.json` must carry `version`, a semantic version `MAJOR.MINOR.PATCH` with an optional `-prerelease` (SemVer 2.0 grammar, no build metadata).
- An upload without one, or with an invalid one, is refused by name.
- The workflow **name** is the stable identity. An upload whose name ends in a version-like suffix (`-v1.2.3`) gets a warning naming the `version` field *(agent decision: a warning, not a refusal, so existing bundles still upload)*.

**D-2. Every version is kept and is immutable.**
- Each registered version has its own directory, `<state>/extracted/<owner>/<name>/<version>/`, written from an empty directory.
- A `source_sha256` (bundle content digest, sorted relative paths plus contents) is recorded on the catalog entry.
- Re-uploading an existing (owner, name, version):
  - with the same digest is a no-op success;
  - with a different digest is refused with "version X already exists; bump `version`".
- Versions are never overwritten.

**D-3. Choosing a version.**
- Every surface that names a workflow accepts an optional version: REST `version` (query or body), ZMQ payload `version`, SDK/CLI `--version X` and the shorthand `name@X`.
- **With a version:** the exact entry. It is resolved with the same owner/system/shared ladder as today, restricted to that version. If it does not exist, the request fails by name, listing the available versions.
- **Without a version:** the highest SemVer among the entries the caller resolves at the winning rung. The rungs are, in order: own, system, shared (access-admitted), admin. Pre-releases count only when no release exists.
- **Lists group by name.** Each row shows the default version and the list of versions.

**D-4. Runs pin what they ran.**
- At trigger, the run's spec records `workflow_version` and `source_sha256`, and the run reads code only from that version's directory. A retry re-checks the digest.
- `ExecutionRecord`, `workflow_executions` (new columns), the job manifest `pipeline_version` and `workflow_outcomes.workflow_version` carry the version.
- `r4 workflow status|results|watch` show `name@version`.
- Run definitions store the version, and replay runs that exact version.
- The plan/commit signature includes `source_sha256`, closing the ADR-105 known limit.

**D-5. Access is per name.**
- `access` stays a property of (owner, name).
- A new version inherits the name's current list. `PUT /{name}/access` keeps updating all versions *(agent decision: per-version access was not asked for, and a private new version silently hiding behind an old shared one is the bug)*.

**D-6. Delete one version or all.**
- `DELETE /{name}?version=X` removes one version.
- Without a version, it removes every version of (owner, name), as today.
- A version's files are removed when no queued or running run references it. Otherwise the removal is deferred until that run ends.

**D-7. Triggers fire one version.**
- An (owner, name) with ingest triggers fires **once** per event.
- It runs the version named by the owner's optional `trigger_version` pin (`PUT /{name}/triggers {"version": X}`), else the default (D-3).
- The triggers come from that version's `workflow.json`.

**D-8. Migration.**
- **Existing entries.** On first start, every entry without its own directory is moved under `<name>/<version>/` from the shared directory. Every entry gets a digest. Duplicated names whose files cannot be told apart keep the shared copy as their highest version, and older rows are marked `code: unavailable`; running them fails by name. This is the truth today.
- **Name-versioned workflows.** `r4 workflow adopt <old-name> --as <name> --version X` (owner or admin) re-registers the old name's files as (name, X), carrying access and triggers. Once adopted, the old name's triggers are disabled; the old name can then be deleted.
- **Rollout leg.** The example workflows move to `example-cnn-track@0.2.9` and `example-legacy-motion-track@0.1.1`.

### Why
- The maintainer asked for exactly this. The code already half-models it (the catalog key), but storage, resolution, provenance and every client ignore the version. That makes old versions unrunnable and the "default" version wrong.
- **Immutable versions plus digests** make runs, run definitions and plans reproducible. They also stop silent code drift during queued runs and retries.
- **Highest SemVer as the default** is what users expect from a version selector. File order is accidental.
- **Name-level access** keeps one sharing decision per workflow. It fixes "new version invisible to sharees" without a new access model.

## Consequences

**Positive.**
- Users and agents can run, show, compare and replay any registered version.
- Runs record what they ran.
- Triggers fire once.
- Authors stop encoding versions in names.

**Cost / negative.**
- One directory per version uses more disk. A per-owner cap on kept versions can come later; deletes reclaim space.
- Every client changes: contract, SDK, CLI, companion, MCP schema, TUI, VS Code.
- Uploads without a valid `version` are now refused.
- Migration of the production catalog is a one-off on first start, plus the adopt step for name-versioned workflows.

**Not in scope.**
- Version ranges (`^1.2`) in run requests.
- Per-version access lists.
- A Postgres catalog. The JSON catalog stays and gains fields; moving it is separate.

## Alternatives Considered

- **Keep versions in names.** Rejected: that is the status quo the maintainer wants replaced. Every name keeps its own triggers and shares; nothing groups them.
- **Mutable versions (re-upload overwrites).** Rejected: runs, plans and run definitions could not be reproduced, and a queued run's code could change under it.
- **Default = most recently uploaded.** Rejected: a hotfix upload of `1.2.1` after `2.0.0` would become the default. SemVer order is what users mean.
- **Per-version access.** Deferred: not asked for. D-5 removes the observed bug.

## Rollout-status (2026-11-08)

Nothing landed yet. The slices:

| Slice | Decisions | Content |
|---|---|---|
| WV1 (umrs) | D-1, D-2, D-3, D-5, D-6, D-7 | Versioned storage + digest + immutability; SemVer default and explicit version in resolution; `version` on REST/ZMQ trigger, plan, describe, package, export, delete, triggers; access inheritance; one trigger per name; tests |
| WV2 (umrs) | D-4, D-8 | Run pinning (spec, retry digest check, records, manifest, outcomes, `workflow_executions` columns); plan signs digest; run definitions pin version; first-start migration + adopt endpoint |
| WV3 (r4) | D-3, D-4, D-8 | Contract `version`/`versions`; SDK + CLI `--version`, `name@X`, `workflow versions <name>`, `workflow adopt`; list grouping; status/results show version; companion + MCP schema |
| WV4 (clients) | D-3 | TUI v3 and VS Code version picker and display |
| WV5 (live) | D-8 | Deploy; adopt the example workflows; receipts |

## Rollout-status (2026-11-08, later) — WV1, WV2, WV3 landed repo-side

- **WV1 + WV2 (umrs `7683edb8`, migration 0133 `workflow_execution_versions`).**
  - New modules `server/workflow_versions.py` (SemVer, `source_sha256`, `name@X`, error codes) and `server/workflow_catalog_versions.py` (first-start migration, deferred file removal).
  - Immutable per-version directories. Resolution picks the highest SemVer within the winning rung.
  - `version` and `name@X` work on every REST/ZMQ surface. Runs pin `workflow_version` + `source_sha256`, and steps read only that version's directory; the digest is re-checked before start and on retry.
  - Plan signs the digest; run definitions pin the version; adopt endpoint; one trigger fire per name with an optional `trigger_version`; access inherited per name.
  - Errors travel on the `x-platform-error-code` header: `workflows.invalid_version` 400, `version_exists` 409, `version_not_found` 404, `code_unavailable` 409, `source_mismatch` 409.
  - 49 new tests. Workflow suites against pg15: 1067 passed; the 5 failures are the ADR-112 `hpcaccess` contract tests, which need #808.
- **Details decided in implementation (agent).**
  - **D-3.** Lists group by (owner, name, scope).
  - **D-4.**
    - Commit runs the version the plan signed. A newer upload no longer makes the plan stale; only a digest change of that version does. A deleted signed version gives 404 `version_not_found`.
    - Run definitions saved without a version pin the default at save time.
  - **D-8 migration.**
    - The version whose own `workflow.json` matches the shared directory keeps the files. The highest SemVer is used only when none matches, which is more truthful than "highest".
    - Legacy non-SemVer versions (e.g. "0.1") sort just below the matching SemVer; their directory is the version string, or `legacy-<hash>` when that is unsafe.
    - The migration runs lazily on the first catalog load under a flock with a `layout: 2` marker.
  - **D-6.** Deferred file removal runs at the next catalog write or when the referencing run ends. There is no timer sweep.
- **WV3 (r4, #810).**
  - Contracts 1.2.0: `parse_workflow_ref`, `resolve_workflow_ref`, and version fields on the catalog item, status, results, upload, run definition and describe.
  - SDK: version in body/query; `list_workflows(all_versions=)`, `adopt()`, `set_trigger_version()`.
  - CLI: `NAME@X` / `--version`; `list` with VERSION / VERSIONS; `workflow versions`; `workflow adopt`; `triggers --pin/--unpin`; status/results/watch show `name@version` + digest.
  - Companion and bridge carry `version`; the user manual has a "Workflow versions" section.
  - 68 new tests. The agent manifest and MCP mirror are regenerated from the pin.
- **WV4 (#817, 2026-11-09): landed repo-side.**
  - TUI v3 lists show `name · vX (+N)`. In the Work pane, `v`/`V` picks a version and `t` runs it.
  - The definition card lists versions, marks `code · unavailable`, and drafts `/workflow trigger name@X`.
  - Runs show `name@version` + a 12-character digest.
  - VS Code: one tree child per version, a version quick-pick on trigger, `name@version` in confirmations.
  - Older servers are unchanged.
  - Left open: the Studio webview (ACP `workflows/*`) still runs and describes without a version, and the TUI list overlay's `t` runs the default version.
- **Open.**
  - WV5: deploy, then adopt the example workflows (`example-cnn-track@0.2.9`, `example-legacy-motion-track@0.1.1`).
  - `record_outcome` still has no caller.

## Related

- [ADR-074](074-uploaded-step-code-runs-sandboxed.md), [ADR-080](080-one-access-guard-shareable-resources.md), [ADR-084](084-growth-governance.md), [ADR-105](105-umrs-agent-capability-layer.md)
- `docs/guides/custom-workflows.md` (the `version` field row)
