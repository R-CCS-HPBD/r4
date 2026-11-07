# ADR-035: Enforcement-point matrix — gateway authn, UMRS authz, client UX gate

**Status:** Accepted (2026-09-03; rollout nearly complete — see status block)
- **Date:** 2026-09-02
- **Related:** ADR-002 (Job/Attempt), ADR-012 (assistant service), ADR-015
  (knowledge ingest egress), ADR-025 (research session authority), ADR-027
  (workflow upload authorization), ADR-028 (companion turn attempts),
  r4-umrs-platform-development-plan-2026-09-02.md

## Context

Mutating RPC surface grew faster than the `tools/route_inventory.py`
annotation baseline: 52 routes bypassed the audit baseline on 2026-09-02
(a2l ledger, companion venue, knowledge graphs, research loops, research
sessions, workflow run-definitions). Beyond the baseline-annotation gap there
was no single machine-readable statement of **who decides** for each mutating
call:

- NGINX with `auth_request` proves identity at the edge (authn), not
  authorization (authz) or scope.
- A command palette or companion permission manager is UX gating only; a
  patched client skips it.
- UMRS is the only enforcement point that sees identity + scope + handler
  decision in one place, yet before this ADR no artifact recorded, per
  route family, which of the three layers is authoritative.

The trust gate (`umrs/api/_verified_headers.py`) resolves *whether* the
caller identity may be honored for a request via the handshake-HMAC
contract. The missing piece was *which* routes require which enforcement
decision, and how CI proves the surface keeps complying as it grows.

## Decision

1. **Layer responsibilities are fixed.**

   | Layer | Role | Never does |
   |---|---|---|
   | Gateway (NGINX + auth sidecar) | authn, rate limit, routing | authz decisions, scope checks |
   | UMRS handlers | authz + scope authority for every mutating route | rely on client state |
   | Clients (r4/Companion/TUI/ACP) | UX gating, approval prompts | any enforcement decision |

2. **`tools/enforcement_matrix.yaml` is the normative registry.** One row per
   route family (`path_prefix`), with `mutating`, `gateway_auth`,
   `umrs_authz`, `ux_gate`, `notes`, `review`. Invariants:

   - Every `mutating: true` row carries an enforceable `umrs_authz`
     (`owner-scope`, `verified-identity`, `mutation-guard`, `ops-admin`,
     `internal`, `exempt`); `read-only` or blank is a finding.
   - Exactly one catch-all row (`platform-default`, `/umrs`) holds the door
     for unaudited families and is flagged separately by the validator.
   - Rows are **audit assertions**, not static analysis output: each row is
     re-verified against handler code during review cycles, and every
     knowledge/workflow mutation funnels through its named guard
     (`_knowledge_mutation_guard`, `verify_workflow_author_claim`,
     `require_ops_admin`, owner-scope resolution) as recorded.

3. **`tools/enforcement_matrix_validator.py` gates it.** Schema + invariant
   pass is offline and fast; `--live` cross-check against
   `route_inventory.build_inventory()` reports:
   - `live-no-row` (fail): mutating route with no matrix row at all;
   - `live-catch-all` (warn): mutating route covered only by the
     `platform-default` row — family audit row pending.
   Sprint 1 runs advisory; hard-fail promotion follows one green snapshot
   cycle and an exemption review.

4. **Autopilot gating.** Autopilot mode in any client refuses to start a
   research loop job while the matrix reports findings — the mode exists on
   the client side, but the guardrail must be green server-side first
   (Review finding F3). This rule lands when the per-user TRUST allowlist
   ships (next Track-2 item); the matrix rows are its fixture.
   *(2026-11-03, [ADR-104](104-one-mode-one-permission-decision.md)
   amendment 1: the client-side TRUST allowlist consult is now an opt-in
   organisation policy, `R4_AUTOPILOT_SERVER_POLICY=1`, checked at project
   scope and only after read-only tools and a session "always". Approvals no
   longer write self-granted trust rows; nothing seeded the allowlist, so
   signed-in autopilot asked for every tool.)*

## Findings that justified the matrix (2026-09-02 canary round)

The first live-review pass against the matrix caught **two real defects**
in surfaces the matrix already covered — proof that a specification alone
drifts from its implementation and only exercised probes close the gap:

1. **Trust writes were not scope-authorized** (`companion` family). Any
   verified identity could mint trust rows scoped to *any* project or
   *any* user. Fixed in umrs c8ebb31: project scope requires the caller's
   verified project, user scope requires uid==caller, group scope fails
   closed until a membership authority exists.
2. **Asset index leaked cross-scope** (`assets` family). The model
   collector ran with a synthetic `AccessContext(is_admin=True)` — every
   authenticated user could list every model. Fixed in the same commit:
   AccessContext derives from the caller; `_visible_row` filters to
   system + verified project + own user rows; group rows admin-only
   until membership authority.

## Rollout status (2026-09-03)

- **Landed:** normative matrix (`tools/enforcement_matrix.yaml`) + validator
  (`fdd15ae0`); route baseline re-generated and green on 306 routes
  (one green snapshot cycle complete, 2026-09-02); canary rule wired
  (`authz_canaries.yaml`, 14 probes + cross-scope identities, `1504915d`);
  per-user-scope TRUST allowlist + scope grammar + item-8 archive cascade
  (umrs `85b42c1`/`c8ebb31`/`7ff4f9b`, main `5e5402b1`/`d9d84a74`).
- **Open:** (a) hard-fail promotion of the validator into
  `scripts/ci-lint-local.sh` + CI, gated on the pending exemption review
  (`tools/exemption_governance_validator.py` advisory today); (b) decision 4
  wiring — no `r4/src` consumer of the matrix yet, so autopilot loop-start
  refusal is specified but not enforced client-side.

## Rollout status (2026-09-07, WP-13 round 1 — promotion plan)

- **Landed (D-1):** new hard-fail `tools/adr_index_uniqueness_validator.py`
  (R1–R7: file-not-indexed, row-target-missing, number-collision with a
  registered `KNOWN_NUMBER_SHARING` set — 012 annex, 027 renumber-pending —
  amendment-orphan, index-duplicate-row, reserved-allocated [038],
  row-missing-status). Wired into `scripts/ci-lint-local.sh` and
  `.github/workflows/ci.yaml`; registry entry in
  `tools/enforcement_maturity_scorer.py` (21 validators). Found and repaired
  live index drift: ADR-010 and the ADR-012 v1-contract annex had files but
  no index rows. Pins: `tests/test_adr_index_uniqueness_validator.py` (15).
- **Promotion order (each on its own `policy/*` branch — never a mass flip):**
  1. `traceability_validator` — verification-chain integrity, mechanical
     spec; candidate first.
  2. `exemption_governance_validator` — unblocks ADR-035 open item (a).
  3. `security_compat_scanner` — security posture rows.
  4. `tool_route_liveness` — advisory ⇒ promote after the baseline sweep.
  5. `transport_boundary_audit` + `dead_compat_bridge_validator` +
     `bridge_detector` — runtime/bridge posture, promote as a cluster.
  6. `check_metrics_coverage` — observability contract.
  7. `orphan_module_detector` + `adr_linkage_validator` — drift detectors,
     promote last (noisiest).
  - `cleanup_readiness_report` stays `informational` (report, not policy).
  Each promotion lands in ONE branch with: baseline run committed,
  `enforcement_level` flip, gate wiring, and the ADR-035 rollout note.
- **No blanket promotion in this package** — per the G-P12-1 remedy
  (policy/* branches per promotion), D-1 is the only new hard-fail this wave.

## Rollout status (2026-09-10, WP-13 round 2 — promotion #1)

- **Landed (promotion #1, `traceability_validator` advisory ⇒ hard-fail):**
  Promoted per the 2026-09-07 order (candidate first). Baseline pinned at
  promotion time: `traceability: OK (3 warning(s))` — P3/P3-C3, P11/P11-C3,
  P12/P12-C3 ("Implemented but cites no test file") stay **WARN-tier**
  (no-test-citation advisories do not fail the gate; violations do).
  Wiring: `enforcement_level` flip in
  `tools/enforcement_maturity_scorer.py` (9→10 hard-fail, 11→10 advisory);
  new `run_validator` line in `scripts/ci-lint-local.sh` boundary section;
  new Boundary Check step in `.github/workflows/ci.yaml` after D-1.
  Note: promoted on `main` because the promotion wave carries the
  unpushed venue/WP-04 follow-on commits (`6e4971d5`, `4b6792fc`) — when
  merged upstream, the maintainer may re-emit as the `policy/*` branch
  per the convention; no other promotions rode along (order kept).
- **Order unchanged** — next: `exemption_governance_validator` (#2),
  also unblocks ADR-035 open item (a); then `security_compat_scanner` (#3);
  no cluster promotion (#5) before a baseline sweep.

### Promotion #2 (2026-09-10, `exemption_governance_validator` advisory ⇒ hard-fail)

- **Landed:** Baseline pinned at promotion time: `0 errors / 16 warnings
  (missing_rationale, removed_target) / 17 info (missing_expiry, duplicates)`
  across 16 live exemptions — soft tiers stay soft (warnings/info do NOT
  fail; `error` findings exit 1 per the validator's contract).
  Wiring: `enforcement_level` flip in
  `tools/enforcement_maturity_scorer.py` (10→11 hard-fail / 10→9 advisory);
  new `run_validator` line in `scripts/ci-lint-local.sh`;
  new Boundary Check step in `.github/workflows/ci.yaml`.
  **Unblocks ADR-035 open item (a)** — exemption governance now enforces
  instead of advising; the 16 warnings + 17 info rows are the next review
  debt (rationale attachments, expiry stamps, target re-verification),
  tracked for the doc sweep, not a gate blocker.
- **Order unchanged** — next: `security_compat_scanner` (#3).

#### Promotion #2 follow-through (2026-09-10, debt sweep)

- **Swept the review debt recorded above.** Outcome: `errors: 0 /
  warnings: 0 / info: 7` (from 0/16/17). Deliverables:
  - Validator bug: `removed_target` check false-positived C901
    function-level exemptions as stale module targets (8-row class); fixed
    by skipping `noqa_c901` kinds from module-resolution; +2 tests.
  - `allow_lazy` rule-exemption scanner now parses kv metadata
    (`expires=/ticket=`) from comment text; +2 tests.
  - Rationale prose attached to the two bare `# noqa: boundary` sites
    (project_knowledge project/verify imports) — `# noqa` must ride the
    import-statement line; multi-line wrap forms found to break both the
    boundary noqack and isort (knowledge): recorded as fixture rule here.
  - `# noqa: C901` function rows for tools.py / command_bridge /
    workflow_tools stand as named review rows WITH the known constraint:
    governance kv metadata CANNOT fit inside 100-col lines on long-import
    signatures — they stay flagged (warnings gone after bare-half fixes;
    residual = info rows missing expiry, by design).
  - **Side fix on the same wave: `tools/complexity_audit.py` now honors
    `# noqa: C901` markers** (marker-lines skip) — the changed-file audit
    no longer re-flags debt the McCabe gate exempts. This is why the
    touched-file scanner stays usable on debt-heavy files.
  - Residual INFO-only (design-acceptable): 2 missed-expiry + 1
    duplicate-exemption on adapters.py (#noqa'd two lazy-composition
    loads — consolidated loads deliberately deferred: the file is the WP-14
    owner surface; owner land consolidation), 2 missed-expiry on
    boundary-validator allow_lazy lines, 1 duplicate `<unknown>` artifact
    class cleared on next pass whenever the file next changes.

### Promotion #3 (2026-09-10, `security_compat_scanner` advisory ⇒ hard-fail)

- **Landed:** Baseline pinned at promotion time: `0 critical / 8 warnings
  (unsalted hash of auth-sensitive value in umrs jobs/shared/leases/
  openai_api) / 7 info (MD5-for-checksum usage)` — non-critical severities
  stay soft (only `critical` findings fail, per the scanner's exit
  contract `return 1 if critical`). Wiring: `enforcement_level` flip in
  `tools/enforcement_maturity_scorer.py` (11→12 hard-fail / 9→8 advisory);
  new `run_validator` line in `scripts/ci-lint-local.sh`; new Boundary
  Check step in `.github/workflows/ci.yaml`.
- **Security posture note:** the 8 unsalted-hash warnings are
  umrs-side review debt (secret_broker/workload_identity/shared/auth/
  infer leases) — not a gate blocker, but **first-priority review list**
  for the security loop: hash-of-auth-value sites must migrate to salted
  KDFs or be re-adjudicated as not auth-sensitive.
- **Order unchanged** — next: `tool_route_liveness` (#4; requires the
  baseline sweep first since it is grade B).

### Promotion #4 (2026-09-10, `tool_route_liveness` advisory ⇒ hard-fail)

- **Baseline sweep first (per the "promote after the baseline sweep"
  order note):** coverage 7 → 16 tests — DEAD_CALLBACK / RETIRED_ROUTE
  positive + negative paths, detail text actionability, checked-setters
  declaration coverage, ROUTE_TOOLS declaration shape, no-transport kwarg
  passthrough, retired-route dedupe/ordering, JSON contract, human
  summary, both exit-contract directions. Grade B ⇒ grade A band
  (hard-fail grade band is ≥10).
- **Landed:** exit contract moved to findings-fail (was advisory "never
  fails"); live baseline pinned: 0 findings, 3 callbacks checked, 4
  retired routes. Wiring: `enforcement_level` flip in
  `tools/enforcement_maturity_scorer.py` (12→13 hard-fail / 8→7
  advisory); new `run_validator` line in `scripts/ci-lint-local.sh`; new
  Boundary Check step in `.github/workflows/ci.yaml`.
- **Scoreboard repair (same wave):** the promotion flip exposed a
  registry row drift in `tools/enforcement_maturity_scorer.py` —
  `security_compat_scanner`'s `test_file` named a file that never
  existed (introduced 22f2380f); its real tests live in
  `tests/test_topology_tools.py` (`class TestSecurityCompatScanner`, 15
  counted via the shared-file counter). Repointed, and the scorecard
  moved D-low ⇒ **A:21 B:0 C:0 D:0** (13 hard-fail / 7 advisory /
  1 informational, 339 tests).
- **Order unchanged** — next: transport/bridge cluster (#5,
  `transport_boundary_audit` + `dead_compat_bridge_validator` +
  `bridge_detector`).

### Promotion #5 (2026-09-10, transport/bridge cluster — SPLIT decision)

- **Landed:** `bridge_detector` (baseline: 36 bridges, active 6, likely_dead
  0, removable 0, review 30 — exit 0) and `transport_boundary_audit`
  (baseline: 0 violations, 578 transport sites, 5 approved ZMQ modules —
  exit 0) flipped advisory ⇒ hard_fail; wiring: scorer (13→15 hard-fail /
  7→5 advisory) + `scripts/ci-lint-local.sh` + `.github/workflows/ci.yaml`.
- **`dead_compat_bridge_validator` STAYS advisory** — cluster split, not
  blanket: its own gate state was red at decision time (688 findings,
  includes whole classes of false positives: its source roots do not cover
  test-file consumers, so `a2l/science_lineage.py` was flagged despite
  direct use by 3 r4 test files; legacy gen-1 surfaces
  `umrs/src/server_umrs.py`/`client_umrs.py` fall into its compat heuristics
  en masse; and the two genuinely-live a2l dwell rows belong to the WP-14
  round-2 removal gate, not piecewise deletion). Promoting it hard-fail
  would freeze CI red for an adjudication wave, not fix the drift.
- **Detector fixes landed with this decision:** `users_KNOWN_ACTIVE`
  gets two *documented* entries — `a2l/src/a2l/science_lineage.py` and
  `a2l/src/a2l/main.py` — with the ADR-034 dwell rationale. Baseline after
  the entries: findings 688 ⇒ **686** (all remaining = adjudication
  backlog for a dedicated waive: classify first the `umrs/src/{server,
  client}_umrs.py` legacy-surface class, then the residual singles;
  promotion attempt retries after that classify, per the all-in-one-
  policy/* convention).
- **Order unchanged** — next: DCB classification wave (see #5), then #7
  (`orphan_module_detector` + `adr_linkage_validator`).

#### Promotion #6 follow-through (2026-09-10, coverage truth pass)

- **Found during the sweep:** the "UNUSED (defined, unreferenced)" class was
  computed against dashboards only — the validator never scanned
  `observability/prometheus/*.yaml`, so every series consumed solely by
  alert rules was falsely marked unused (the whole `rivault_*` operational
  family surfacing in alerts). Alert rules now count as references
  (`collect_alert_metrics`, yaml-free expr scan + block-scalar walk).
- **Real finding along the way: JobFailureRateHigh could never fire** because
  its expression referenced a metric that has never existed in code
  (`rivault_job_status_total` — an invented status-tagged family). The alert
  now reads the actually-emitted series `umrs_job_total{event="failed"}`
  (WP-13 follow-through heals a silent-dead alert).
- **Exporter families cleaned from the reporting noise:** `pg_` and `minio_`
  added to the third-party prefix set (postgres_exporter and MinIO's own
  metrics are referenced from dashboards, never code-defined).
- **Post-sweep honest census:** dashboards+alerts 24 refs / code 70 defs:
  0 missing, exit 0. Genuinely unreferenced series = **39** (scaffold families
  — rivault_rag_* / rivault_jobs_* defining-but-never-emitted, plus emitted
  instrumentation without dashboards yet). Pruning the scaffold half requires
  owner confirmation per metric family (it's the pre-release trim point
  recorded in the same wave's triage list doc pattern); the informational
  `--strict` path covers escalation if the metric series ever regress.

### DCB classification wave result (2026-09-10, ran before #7 per the planned insertion)

- **Classified the full 688-row backlog with `--json`:** **100% of findings
  are `re-export bridge` kind** and the top finding files are the platform's
  *active* modules (`umrs/api/__init__.py`, `server/http_sidecar.py`,
  `companion/tools.py`, `bridge_server.py`, `api/workflows.py`,
  `client.py`) — i.e., the detector currently classifies normal sibling
  `from x import y` (inside its source roots) as compatibility bridges,
  and then dead-classes them from missing references of the imported
  module path. That is a **category error at platform scale**, not an
  adjudifiable drift backlog: fixing it means narrowing the bridge
  definition to true compat patterns (``__getattr__`` forwarding and
  dedicated `*_compat`/`archive-*` packages), not silencing files.
- **Promotion of `dead_compat_bridge_validator` is therefore OFF the
  hard-fail track for this WP-13 round 2** — promotion of a detector that
  mis-classifies the whole codebase rewards noise. Reclassified as a
  *detector-design wave* (own package): (1) narrow bridge detection to
  compat-intent patterns only, (2) re-baseline, (3) then revisit
  promotion. The two `KNOWN_ACTIVE_BRIDGES` entries landed under #5 stay
  (they are true statements — test-file consumers / dwell-managed
  service entrypoint).

#### DCB design wave result + promotion #5c (2026-09-10)

- **Detector redesigned per the plan above:** dropped the every-ImportFrom
  "re-export bridge" class entirely; detection now covers compat-intent
  classes only — `__getattr__` forwarding, re-export-only `__init__.py`,
  compat-named modules (stem/parent/package containing `compat`),
  deprecation-marker modules. Reference matching upgraded: full dotted
  path **plus** trailing-stem from/import + string-dispatch shapes
  (package-relative and registry-map consumers — the second blind spot).
- **Adjudication wave ran on the new findings:** 3 rows at redesign time
  (`umrs.tui/__init__.py` — retirement-cycle held, NOW in `KNOWN_ACTIVE`
  with the package docstring's rationale; `r4/_cli/init.py` and
  `r4/providers/openai_compat.py` — both dynamically consumed, resolved
  by the improved matcher, not registered).
- **Final baseline: 2 active bridges, 0 dead findings** (the two active
  rows are the gateway backward-compat aliases).
  **Promoted to hard_fail (#5c, 2026-09-10)** — 22 tests, grade A;
  scorer flip (16→17 hard / 4→2 advisory tracks adjust accordingly);
  wired into `scripts/ci-lint-local.sh` + `.github/workflows/ci.yaml`.
  Detector module docstring rewritten to the new class semantics; two
  tests rewritten from old-class expectations into the new classes.
- **Advisory remaining is now exactly one** (`orphan_module_detector` —
  42-row residual review wave after its own classification fixes) plus
  the informational `cleanup_readiness_report`; scorecard after this
  promotion: 18 hard / 2 advisory / 1 informational, `A:21 B:0 C:0 D:0`.

### Promotion #6 (2026-09-10, `check_metrics_coverage` advisory ⇒ hard-fail)

- **Detection upgrade first (real gap = tool blind spot found by the
  baseline run, NOT implementation drift):** the AST extractor only saw
  direct `prometheus_client.Counter/Histogram/...` calls; this codebase
  defines metrics through lazy wrapper factories
  (`umrs/metrics.py::_counter/_gauge/_histogram`, real ctor vs NoOp chosen
  at runtime) so wrappers were invisible; and `vllm:`-prefixed exporter
  names (actual vLLM prometheus exporter convention) weren't in the
  third-party prefix set. Added `_METRIC_ALIASES` and the `vllm:` /
  `litellm:` prefixes.
- **Baseline after the upgrade:** dashboards 24 refs / code 70 defs →
  **0 MISSING, exit 0** (removing the 27 false-missing). UNUSED series
  (defined but unreferenced) remain advisory-only; `--strict` still fails
  for those and is not gated in this promotion.
- **Landed:** scorer flip (15→16 hard-fail / 5→4 advisory); gate wiring in
  `scripts/ci-lint-local.sh` + `.github/workflows/ci.yaml`; 21 tests,
  grade A.

### Promotion #7 (2026-09-10, noisiest-last — SPLIT decision; **superseded by the 2026-09-11 completion below**)

- **Landed: `adr_linkage_validator` advisory ⇒ hard-fail.** Baseline: 51
  ADRs, 30 active, 0 errors / 0 warnings / 0 info. Scorer flip
  (16→17 hard-fail / 4→3 advisory); gate wiring in
  `scripts/ci-lint-local.sh` + `.github/workflows/ci.yaml`; 15 tests,
  grade A.
- **`orphan_module_detector` status → SEE the "WP-13 round 2 completion"**
  block below for the full classification pipeline and the promotion; this
  earlier defer-read has been superseded, not the process it described:
  the class error at platform scale
  (registry/factory-loaded modules with no static inbound imports but real
  hard-fail list would mis-classify CI-red. Promotion therefore waits on
  an orphan-classification wave: baseline-list the dynamic rows with
  reasons, delete the genuine dead rows in the same wave, then flip with a
  near-zero true backlog.
- **Round-2 state after this promotion:** 17 hard-fail / 3 advisory /
  1 informational across 21 validators, scorecard `A:21 B:0 C:0 D:0`
  (339 tests). Remaining advisory rows by design-wave pending:
  `dead_compat_bridge_validator` (detector concept refinement),
  `orphan_module_detector` (classification wave); `cleanup_readiness_report`
  stays informational per the ratified order.

#### WP-13 round 2 completion (2026-09-11): orphan detector promoted; all 21 validators hard-fail-capable

- **`orphan_module_detector` promoted advisory ⇒ hard_fail after the full
  classification pipeline:**
  1. detector fixes (ImportFrom children + relative-package resolution);
  2. dynamic families + pinned-public whitelist (test-map declared
     surfaces — 13 rows);
  3. zero-consumer r4 deletions (DTO base, mcp_extensions, memory,
     transport, r4_mcp.launcher — `83eb538e`);
  4. combined effort legacy legs fixed remaining 25 census rows: keep
     registry entries named with evidence:
     - **RC Phase-2 HTTP surface pendings (18 rows)** — `4d90e32a`'s
       route framework: an in-flight concurrent session owns those
       waves; flagged-pending rows are whitelisted WITH review expiry
       (2026-Q1) as pending surfaces, NOT deletable-by-analysis;
     - **ops-called invokers** — `umrs.api.mcp_service` (Ansible CMD
       `uvicorn umrs.api.mcp_service:app`), `umrs.slurm.k8s_bootstrap`
       (deploy script), `r4_mcp.persistent_server` (50120 MCP wrapper),
       `umrs.model_registry.conversion` + `umrs.model_registry.certification`
       (planned/Phase-2) "KEEP" rows;
       (dated rollout note 2026-10-19, cleanup C-2(ii):
       `r4_mcp.persistent_server` proved broken at import (`:33` → missing
       `persistent_runtime`) and is deleted, its KEEP row above revised by
       that evidence; port 50120 is now the octopus ZMQ port)
     - **in-flight families** — data_catalog backfill/reconcile.
  5. retention/restore learnings recorded in ADR (Ansible-string routes
     bind FASTAPI apps as well as `python -m` wrappers — detector blind —
     so global deletion sweeps need ansible-docs scanning, never plain-
     code-only adjudication).
- Live state at flip time: **zero orphan findings across ~1019 scanned
  modules, gate-exit semantics findings→fail, detector suite 30 green.**
- Scorecard after this flip: **A:21 B:0 C:0 D:0 / 20 hard / 2 advisory /
  1 informational / 346 tests** (gate green on the full scoped local run,
  first time). Remaining by design (not blockers):
  `dead_compat_bridge_validator` *already* hard-fail (its DCB redesign
  landed earlier this round), `exemption_governance` at 0/0/3-info
  (adapters rows deferred to the WP-14 surface),
  `cleanup_readiness_report` informational per the ratified order.

#### orphan classification wave result (2026-09-10)

- **Detector upgrades landed (2 real bugs + 4 verified dynamic families):**
  1. `from pkg import child` never recorded *child* as imported — fixed
  (children of ImportFrom recorded; 83→75).
  2. Relative-import resolution used the module name rather than the
  enclosing package (`from . import sib` in `pkg/main.py` looked for
  `pkg.main.sib`) — fixed (level arithmetic over package parts; 75→55→42
  combined with the whitelist).
  3. Verified dynamic-load families whitelisted with reasons in the
  constant: `umrs.jobs` (lazy-map `__init__.py` exports), manifest/name-keyed
  `r4_mcp.stdio_adapters` leaves, `umrs.vision_service.default_adapters`
  (filesystem auto-discovery by filename stem — docstring in
  `vision_service/adapters/__init__.py`), `umrs.pipeline_sdk.templates`
  (`workflow.json` dotted-path entrypoints), and
  `umrs.vision_service.service` (`bentofile.yaml` service string).
- **Residual census — 42 findings, promotion still BLOCKED but now the
  review list is named:** 14 umrs.api router modules with no mount and no
  import (deep-dive removal-or-register review needed);
  umrs.pipeline_sdk.connectors (3 leaf backends, no dispatch evidence);
  umrs security/model_registry singles + singles per package
  (data_catalog 2 mid-flight adjacent, r4 five incl.
  workflow_lint/memory/mcp_extensions/observability/transport —
  see the review rows in the wave notes; **no deletions performed this
  wave** — residual is genuinely mixed and several umrs areas are owned
  by in-flight work).
- Promotion attempt retries after an owner triage pass on the 42 rows;
  3 new detector tests (26→29).

#### Deletion leg 1 (2026-09-11, owner-authorized "resolve by yourself")

- **PINNED_PUBLIC_SURFACE added (13 rows)** — the wave landed in a separate commit
  `a29f5e00` two days prior to this leg; residual after the pinned list = 29
  from the 42-row census, with the r4-five lineage inside.
- **Row-level deletions executed today** (`git rm`, no replacement shim —
  pre-release "deletions over bridgings" posture):
  - `r4/src/r4/_dto.py` — APIModel base, zero consumers anywhere (also
    confirmed against `docs/verification/traceability.yaml` mention;
    r4-contracts owns the DTO layer).
  - `r4/src/r4/mcp_extensions.py` — abstract MCP extension scaffolds;
    zero imports across r4/mcp-servers/tests/docs.
  - `r4/src/r4/memory.py` — "one active memory plugin" invariant
    abstraction with zero consumers; the shipped memory path is
    `companion.context.MemoryItem` (verified; `R4_COMPANION_MEMORY_DIR`
    belongs to context.py, unrelated).
  - `r4/src/r4/transport.py` — structured I/O transport DTO-class; zero
    imports (all references belong to `_transport_router`, a separate
    route). Contradicts its own docstring's "core I/O" claim — the
    reality is a never-registered dead surface.
- umrs-side rows stay owner-gated (in-flight families / adapter lines) —
  promoted rows only; promotion check re-run after the leg.
- Residual after this leg: **25 rows** on the triage list
  (`docs/development/orphan-triage-list-2026-09-10.md` updated with dated
  per-row verdicts).

Both rows existed (and read "green") in the matrix while the
implementations were wrong — expected for a specification, and precisely
why the rule below exists.

### Rule: no row is "verified" until a canary exercises it

A matrix row claims verification ONLY by referencing a canary probe
(`verified_by: <probe-id>` from `tools/authz_canaries.yaml`). The
validator enforces both directions mechanically:

- `row-verified-missing` (warn): a row without `verified_by` is
  specification-only — acceptable for new families, never citable as
  enforced;
- `row-verified-unknown` (fail): `verified_by` referencing a probe that
  does not exist is drift and fails the gate.

Deployment smoke therefore runs: matrix validate → anonymous canary
fire → cross-scope fire (canary identities) — and any row cited as
enforced must survive that chain.

## Consequences

- **Positive:** the mutating RPC surface now has one file a reviewer can
  diff to answer "what changed, and who guards it?". Per-user-scope TRUST
  allowlist, canary ids, and `/health authz` probes all consume this matrix
  instead of re-deriving the surface.
- **Negative:** a new checked-in artifact to maintain. Mitigation: hard-fail
  promotion makes drift impossible to land silently; `review` dates keep row
  freshness visible.
- **Follow-ups:** baseline the 52 bypass annotations in
  `route_inventory.base.json` (annotation pass, separate commit), per-user
  TRUST allowlist rows, canary + `/health authz` probes.
