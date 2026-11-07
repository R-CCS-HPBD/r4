# ADR-094: Constitution amendment — numbered principles, one home per rule

**Status:** Proposed (2026-10-18, drafted by an agent under the maintainer directive of 2026-10-17, platform hardening plan H-11.3; acceptance is the maintainer's — plan item M-H5, cleanup plan M-4 / A-14; nothing in CONSTITUTION.md changes until then)
**Date:** 2026-10-18
**Relates to:** ADR-059 (corrects the stale D-01/D-02 wording), ADR-072 (D-29: the four team mechanisms are one seam), ADR-052 / ADR-053 / ADR-054 (gate, import discipline, test tiers), ADR-019 amendment and ADR-089 (report-only schedules), platform hardening plan H-11, repo cleanup plan §4, gap row D-53 and the gap-analysis doc sweep

## Context

All facts below were checked against the tree on 2026-10-18 (paths and line
numbers are from that checkout). CONSTITUTION.md line numbers (`:NN`) are
from before its erratum block, added under the header the same day: add 20
to find the line today; section numbers are unchanged.

**Rules and procedures are mixed, and rules are stated more than once.**
`CONSTITUTION.md` (374 lines) mixes rules with procedures: §7 is a command
list ending in a dated scorecard ("N=24, 390 tests, on 2026-09-22",
`:273`), §9.2 is the wave protocol, §9.4 and §10 are document lists. The
same rule appears twice inside it: credential operations (§8.9 and §9.3.4),
Ansible-only deploys (§6 rows 1–3 and §8.5), package imports (§1.1 and §6),
one loop engine (§1.6 and §6), unmounted routers (§1.2 and §6), `sys.path`
in tests (§3.1 and §6), the client-side job store (§1.3 and §6). Across
files, the docs survey of the hardening plan (H-11 "Why") found the gate,
validators, ownership, wave contract, deploy rule, no-credentials rule and
project brief copied into up to 11 files.

**Seven navigation orders.** CLAUDE.md, the copilot instructions, the local
AGENTS.md, living-documentation §3, CONSTITUTION §9.4, README and
CANONICAL_MAP each had their own reading or truth order (cleanup plan §4
asked this ADR to reconcile them). On 2026-10-18 plan steps H-11.1/H-11.2
created `docs/README.md` as the one reading order and the one "which source
wins" rule (its §1–§2), tracked `AGENTS.md`, and cut the other entry files
to pointers. CONSTITUTION §9.4 "Truth surfaces" is the last copy; the
`docs/README.md` changelog leaves it to this ADR. (State of those files on
2026-10-18: present in the working tree, not yet committed.)

**A second "canonical" governance document.**
`docs/architecture/governance/STEADY_STATE_ENGINEERING_POLICY.md` (246
lines, "Established: 2026-05-13", "Status: CANONICAL — Binding Governance
Document") is linked from CONSTITUTION §10 (`:364`). It predates the design
reset and contradicts the working model: it declares "All architectural
boundaries are FINAL" and the default posture "preserve stability", and its
cadence table counts "Validator pass (all 9)" and "61 tests", while gate
stage 3 of `scripts/ci-lint-local.sh` now runs 21 validators (`:363-409`).
Live inbound links: CONSTITUTION.md:364, docs/CANONICAL_MAP.md:68,
docs/architecture/README.md:81, docs/README.md §4 (marks it pending this
ADR), docs/development/structure-hardening-plan-2026-09-14.md:219, and the
retired `docs/loop-engineering/` files.

**Stale or wrong text in the constitution.**
- §1.5 (`:67`) "Until decision D-01/D-02 is taken", the §2 auth row (`:94`)
  "until D-02 moves authentication into UMRS", and §5.3 (`:209`) listing
  D-01/D-02, D-47 and D-12 as decisions still to take. ADR-059 (Accepted
  2026-09-22) closed D-01/D-02/D-50 by design amendment (dual transport;
  the edge is UMRS's edge process); ADR-060 decided D-47's direction and
  ADR-061 D-12's.
- §1.6 (`:75-79`) forbids "adding a fifth mechanism". ADR-072 (Accepted
  2026-09-24) found the four mechanisms are one stack behind one team seam
  (gap row D-29).
- §2.2 (`:117-118`) names `gateway/tests/test_security_contracts.py` as
  enforcement; the file was deleted in `0aac3ec2` (ADR-014, Python gateway
  removal). Only a stale `.pyc` remains.
- §2 storage row (`:93`) says the per-user `s3cfg` delivery "is being
  removed"; gap row D-16 records delivery stopped 2026-09-22, an opt-in
  transition window left.
- §3.1 says "`mypy` for type checking"; CI runs mypy with `|| true`
  (`.github/workflows/ci.yaml:2050,2054`), so it is informational.
- Most rules name no enforcement at all, so a reader cannot tell a checked
  invariant from an aspiration.

**Two ID namespaces would collide with a naive renumbering.**
- A legacy invariant namespace INV-01…INV-13 is cited by live files:
  ADR-001…ADR-006 "Relates to" headers (INV-02…INV-12),
  `tools/contracts_fence.py` docstring and ADR-092 (INV-10), ADR-093,
  `deploy/standalone/compose.yaml:154` and the mutation-safety and
  development instructions (INV-13), `umrs/src/umrs/jobs/store.py:1502`
  (INV-04). Its definitions survive only in
  `docs/history/backup/ARCHITECTURE_VALIDATION_AND_ROADMAP.md:250-268`.
  `docs/slides/build_umrs_technical_deck.py:1030-1033` uses INV-11, INV-12
  and INV-14 with other meanings.
- `C-0`…`C-6` are the cleanup plan's waves, cited in STATE.md and both plans.
- Section-number citations:
  `git grep -nE 'CONSTITUTION(\.md)? ?§|CONSTITUTION\.md#|constitution §'`
  finds 171 lines in 73 tracked parent files. The count excludes
  CONSTITUTION.md itself, `docs/history` and `docs/development/archive`.
  `umrs/` adds 3 more lines. The 73 files are:
  - 13 ADRs;
  - 12 files under `docs/development/` (the wave ledger included);
  - 19 code files (13 under `r4/src`, 1 under `r4-contracts/src`, 5 test
    files);
  - 13 files under the retired `docs/loop-engineering/`;
  - 16 other docs and instruction files.

  `tools/bootstrap_validator.py:287-288` cites one more, split across two
  lines. Some citations are already wrong: that comment cites §4.1 for
  "Python 3.10+", which is §3.1.

**A rule the tree contradicts.** §6 forbids "Hot-reload config at runtime"
(rationale "Determinism invariant"), but UMRS serves an authenticated
operator reload, `POST /umrs/ops/reload` (`umrs/src/umrs/api/ops.py:9,767`),
whose startup wiring is itself a merge-safety rule
(`umrs/MERGE_CONSTITUTION.md` §2.8), plus a SIGHUP reload path
(`umrs/tests/test_daemon_signal_hot_reload.py`).

**Enforcement gaps found while checking every "enforced by" claim** (not
fixed here; listed so the amended text does not over-claim):
`tools/import_boundary_validator.py` has no rule for gateway → r4,
umrs → gateway, or UMRS `core/` (`:63-190`); `tools/knowledge_plane_fence.py`
and `tools/doc_sanity_gate.py` run in the local gate only, not in
`.github/workflows/ci.yaml`; `tools/data_authority_validator.py` and
`tools/runtime_invariant_validator.py` run in neither; execution-authority
immutability, applied-migration immutability and edge statelessness have no
check.

## Decision

**D-1 — Four kinds of rule, nothing else.** `CONSTITUTION.md` holds only
Principles (`P-n`), Invariants (`INV-nn`), Forbidden patterns (`F-n`) and
Change control (`CC-n`), within 250 lines. Every rule row names its
enforcement: a validator with its gate stage (and CI when CI runs it), a
named test, or **unenforced** (review only); **partial** says what is and is
not checked. The full proposed text is Appendix A (163 lines).

**D-2 — Stable IDs that do not collide.**
- IDs are never reused. A retired rule keeps its row, marked retired with
  the date and the ADR. Citations use the ID with the file name:
  `CONSTITUTION INV-06`.
- Invariants keep the legacy numbers where the constitution already holds
  the legacy rule: INV-01 (package imports), INV-02 (execution authority),
  INV-05 (offline inference), INV-06 (no silent auth degradation), INV-08
  (presigned URLs), INV-09 (sandbox, relaxed by the ADR-006 amendment),
  INV-10 (`r4-contracts` stdlib only), INV-13 (knowledge portability). Every
  existing citation keeps its meaning, with one exception: INV-09 is
  **widened**. ADR-006 (and its amendment) defines INV-09 as "RAG and MCP
  run inside sandboxed containers"; the proposed INV-09 is the constitution's
  universal sandbox rule (every job, model server and remote session) and
  records default MCP runtimes as still outside a sandbox (D-12). ADR-006's
  citation therefore points at a broader rule whose enforcement cell records
  the opposite state for part of its own scope (MQ-8).
- INV-03, INV-04, INV-07, INV-11 and INV-12 stay with their ADRs (002, 001,
  003, 006); they are reserved and not rules of the constitution. INV-14 is
  not allocated, because the slide generator uses it for another meaning.
  New invariants start at INV-15.
- Change control uses `CC-n`, not the plan's `C-n`, because `C-0`…`C-6` are
  the cleanup waves.

**D-3 — Procedures leave the constitution; it points to them.**
- The §7 commands (validators after each kind of edit, the full enforcement
  regression, the scorecard line) move to `docs/onboarding/developer.md` §3,
  the one home of setup, gate and suite commands; CONTRIBUTING.md points
  there.
- The §9.2 wave contract and the §9.3.5 concurrency rule move to AGENTS.md.
  It already carries both since 2026-10-18 (Wave contract; rule 8).
- §9.4 truth surfaces, §10 and the §4.3 document list move to
  `docs/README.md` (§1–§2, created 2026-10-18).
- The constitution keeps a pointer table (Appendix A §5) and the rule part of
  each procedure: CC-7 says the gate must pass, `docs/onboarding/developer.md`
  §3 says how to run it.

**D-4 — One home per rule.** A rule is defined once, in the constitution
(CC-12). AGENTS.md, CONTRIBUTING.md, `docs/onboarding/`, `.github/instructions/`
and the vendor pointer files may restate a rule in one line with its ID
("Ansible only, CONSTITUTION F-1"). They never redefine it. Domain
instruction files explain how to comply. The guard test
(`tests/test_governance_single_source.py`, plan H-11.4) is separate work.

**D-5 — Retire the steady-state policy.**
`STEADY_STATE_ENGINEERING_POLICY.md` moves to `docs/history/` with a dated
banner naming this ADR as successor. Appendix C gives the disposition of
every section. Three of its rules are still wanted and not yet in the
constitution, so they are carried, flagged for the maintainer (MQ-3): INV-28
(schema history only moves forward), CC-4 (ADRs are superseded, never
deleted) and CC-6 (every PR independently revertable). Its "never remove
test infrastructure for existing invariants" is folded into CC-8.

**D-6 — Corrections carried by the new text.**
- Transport and edge are written as decided (INV-18, INV-19, INV-06, CC-3(b);
  ADR-059).
- The loop/team rule names one loop engine and one team seam (INV-20;
  ADR-064, ADR-072).
- The storage-credential row records that delivery stopped (INV-22; D-16).
- The hardware-MCP row records the landed trust classes (INV-27; D-24).
- The deleted test path is dropped.
- `mypy` is recorded as informational (P-10).
- P-6 states that report-only schedules (ADR-089, the nightly converge check)
  are not the retired loops. The plan's H-12 already reads the rule that way;
  this is the only clarifying sentence the new text adds (MQ-6).

**D-7 — Two PRs, in this order** (CC-5, old §8.8: never move and remove in
the same PR).
1. **Move.** Add the §7 commands and the scorecard line to `docs/onboarding/developer.md` §3,
   re-running `tools/enforcement_maturity_scorer.py` for a fresh count.
   Confirm that AGENTS.md and `docs/README.md` (H-11.1/H-11.2) are committed.
   Move the steady-state policy to `docs/history/` with its banner, and
   re-point its live inbound links (Context). The move keeps
   `tests/test_doc_cross_references.py` green, because that test resolves
   names by basename anywhere under `docs/`. CONSTITUTION.md is unchanged.
2. **Cut.** Replace CONSTITUTION.md with Appendix A, putting the acceptance
   date in place of `YYYY-MM-DD`. Cut CONTRIBUTING.md workflow step 4 ("Validators after
   Python edits: CONSTITUTION §7") and the other live section-number
   citations in entry files and instructions to IDs.
   - Code comments that cite sections move to IDs in the same PR, and that
     PR is lint-gated. This covers the 19 parent code files found by the
     `git grep` in Context, `tools/bootstrap_validator.py:287-288`, and in
     `umrs/` `src/umrs/jobs/plan_run_records.py:3`,
     `src/umrs/venues/plan_runs.py:8` and `tests/test_migration_0120.py:3`
     (a umrs commit and pointer bump).
   - ADR text and wave-ledger entries are not rewritten. Their `§x.y`
     citations resolve through Appendix B.

**D-8 — Amendment authority.** The constitution changes only by an ADR the
maintainer accepts. An agent drafts and never accepts (CC-11; cleanup plan
A-14: these rules govern the agent's own authority).

### Maintainer questions (decide at acceptance)

- **MQ-1** Accept, amend or reject this ADR.
- **MQ-2** F-4 (no config hot-reload) against the operator reload endpoint
  and SIGHUP path. Options:
  - (a) narrow F-4 to "no implicit or self-triggered reload; an
    authenticated operator reload is allowed";
  - (b) retire F-4;
  - (c) keep F-4 and open a removal row for the endpoint.

  The proposed text keeps F-4 verbatim and names the contradiction.
- **MQ-3** Keep or strike the three rules carried from the steady-state
  policy (INV-28, CC-4, CC-6), and confirm the rules Appendix C does not
  carry.
- **MQ-4** The mapping of the two auth rows (edge `auth_request`, dev JWT
  secret) to legacy INV-06 "no silent auth degradation".
- **MQ-5** `CC-n` in place of the plan's `C-n` (D-2).
- **MQ-6** The P-6 sentence on report-only schedules.
- **MQ-7** Whether the enforcement gaps listed in Context become plan rows.
  Candidates: import-boundary rules for gateway → r4, umrs → gateway and
  `core/`; `knowledge_plane_fence` and `doc_sanity_gate` in CI (H-11.8
  already names the latter).
- **MQ-8** INV-09 widened from ADR-006's "RAG and MCP inside a sandbox" to the
  universal sandbox rule (D-2). Accept the widening, or give the universal
  rule a new ID (INV-29) and leave INV-09 with ADR-006's meaning.

### Why

A new developer, or a new developer's agent, should be able to answer three
questions without archaeology: is this a rule, where is it defined, and what
checks it. Numbered rules with an enforcement column answer all three. Cutting
procedures out keeps the constitution stable. Commands, counts and document
lists change every few weeks, and each of those changes used to edit the
canonical rules file. Keeping the legacy INV numbers costs nothing, and it
keeps ADR-001…006 and the code comments that cite them correct.

## Consequences

- Positive:
  - One entry, one reading order, one home per rule.
  - A reader can tell an enforced invariant from a review-only one, and the
    enforcement gaps become visible (MQ-7).
  - The constitution shrinks from 374 to 163 lines and changes only by ADR.
- Cost:
  - Two PRs.
  - About 16 live docs and instruction files and 23 code files are
    re-pointed from `§x.y` to IDs.
  - ADRs, the wave ledger, dated plans and the retired loop-engineering
    files keep their `§x.y` citations. Those resolve only through
    Appendix B, which therefore stays in this ADR permanently.
- NOT in scope:
  - Fixing the enforcement gaps (MQ-7).
  - The single-source guard test (H-11.4).
  - Validator changes (H-11.8).
  - `umrs/MERGE_CONSTITUTION.md`, which stays the umrs repo's own merge-safety
    file (cleanup plan §4: the stand-alone umrs repo needs it).
  - The missing ADR number 091 (owned by plan H-0.1).
  - Codacy's status (M-H6).

## Alternatives Considered

- **Add IDs in place, keep the sections.** Rejected. The procedures and the
  dated scorecard would stay in the canonical file, and the duplicates inside
  it (seven pairs) would each get an ID.
- **Fresh numbering INV-1…INV-n.** Rejected. It silently changes the meaning
  of INV-02…INV-13 citations in accepted ADRs and code.
- **Move the coding standards (old §3–§4.2) out to CONTRIBUTING.md.**
  Rejected for now. They are normative and short, and moving them would make
  CONTRIBUTING a second rule home. They stay as P-9…P-18.
- **Fold `umrs/MERGE_CONSTITUTION.md` into the parent.** Rejected. Cleanup
  plan §4 explains that this strips the merge rules from the stand-alone umrs
  repository.
- **Keep the steady-state policy as the change-tier reference.** Rejected. Its
  tiers were already folded into old §5 on 2026-09-22, and its declaration
  ("boundaries are FINAL") contradicts the design-reset working model.

## Rollout-status (2026-10-18)

- Drafted; Proposed. Nothing landed. CONSTITUTION.md's rules are unchanged;
  a dated erratum block under its header (2026-10-18) names the stale facts
  this ADR corrects.
- 2026-10-18 (review round): the home of the §7 commands changed from
  CONTRIBUTING.md to `docs/onboarding/developer.md` §3, which became the one
  home of setup, gate and suite commands the same day; P-11 and CC-7
  enforcement cells corrected (the parent gate runs plain ruff, the lint
  baseline runs only on the umrs path; two validators are local-only);
  INV-09 widening named (MQ-8); Appendix B §9.2 row and Appendix C §3 row
  corrected.
- Preconditions for PR 1: H-11.1/H-11.2 committed (on 2026-10-18 AGENTS.md,
  `docs/README.md` and `docs/onboarding/` exist in the working tree only).
- Every "Enforced by" cell in Appendix A was checked on 2026-10-18 by
  opening the named validator or test. A cell marked "partial" or
  "unenforced" is a finding, not a placeholder.

## Related

- [ADR-059](059-transport-and-edge-dual-transport-umrs-edge.md), [ADR-072](072-one-team-seam-research-roles.md), [ADR-052](052-one-gate-every-tree.md), [ADR-053](053-umrs-import-discipline.md), [ADR-054](054-workspace-membership-and-test-tiers.md), [ADR-019 amendment](019-amendment-2026-09-14-host-checkout-discipline.md)
- Platform hardening plan H-11; repo cleanup plan §4, §6 A-14
- [.github/instructions/living-documentation.instructions.md](../../.github/instructions/living-documentation.instructions.md)

---

## Appendix A — proposed CONSTITUTION.md (full text)

Paths are relative to the repository root, as in the file itself, so its
links do not resolve from `docs/adr/`; a link checker (plan H-11.8) must
skip fenced blocks. `YYYY-MM-DD` is the acceptance date.

````markdown
# R4 + UMRS Platform Constitution

> **Audience:** human developers, AI coding agents, contributors
> **Status:** CANONICAL — the rules every contribution follows
> **Last updated:** YYYY-MM-DD (ADR-094: numbered rules, one home per rule, procedures moved out)

This file holds rules only: **principles** (P-n), **invariants** (INV-nn),
**forbidden patterns** (F-n) and **change control** (CC-n). Every rule has a
stable ID and says what enforces it: a validator in the gate
(`scripts/ci-lint-local.sh`, with its stage) that CI also runs, a named test,
or **unenforced** (held by review only). **Partial** names what is checked and
what is not. How to do things (commands, the wave protocol, the reading
order, deployment steps) lives in the files of §5, which cite rules here by ID
and never redefine them.

Where the design and the tree still differ, the difference is a numbered row
in [docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md)
(`D-nn`); the rules below say which side wins in the meantime.

**Citing.** Cite a rule by its ID, with the file name outside this file:
`CONSTITUTION INV-06`, `CONSTITUTION F-1`. IDs are never reused; a retired rule
keeps its row, marked retired with the date and the ADR. INV-01…INV-13 keep the
meanings they already carry in ADR-001…ADR-006 and older documents (INV-03,
INV-04, INV-07, INV-11 and INV-12 stay with those ADRs and are not rules of this
file; INV-14 is not allocated). A `§x.y` citation written before this
amendment resolves through
[ADR-094](docs/adr/094-constitution-amendment-numbered-principles.md), Appendix B.

---

## 1. Principles

| ID | Principle | Enforced by |
|---|---|---|
| P-1 | **The design is the target.** [docs/R4-UMRS-DESIGN.md](docs/R4-UMRS-DESIGN.md) is what we build; the gap-analysis rows are the distance. A change that moves the tree away from the design, or edits the design, needs an ADR first (CC-3). | unenforced |
| P-2 | **UMRS first.** If UMRS already owns a capability, R4 consumes it through thin adapters; never reimplement UMRS logic locally (ownership: INV-17). | partial: `tests/test_umrs_first_policy.py` pins the ownership checklist in CONTRIBUTING.md and the PR template; the call itself is review |
| P-3 | **Verify before you assert.** Every claim in a report, document or commit cites a command, a file:line or a commit. A document is re-verified against the code before it is cited; documents dated before 2026-09-22 describe the pre-design narrative. When sources disagree, the precedence in [docs/README.md](docs/README.md) §2 decides. | unenforced |
| P-4 | **Additive change.** Add the new beside the old, migrate consumers, then remove the old. | unenforced |
| P-5 | **Living documentation.** Every landed change updates the documents that describe it in the same wave: status, dates, evidence (commit hashes), the gap-analysis row status included. Policy: [living-documentation.instructions.md](.github/instructions/living-documentation.instructions.md). | partial: `tools/traceability_validator.py` (gate stage 3, CI) fails when a traceability row cites a path that does not exist |
| P-6 | **Maintainer-directed waves.** The maintainer states the objective, the authority granted, what is out of scope and the gap rows in scope; an agent verifies, decides or escalates, implements, gates, documents and reports; the maintainer reviews and merges. Unattended loops that act are retired (none has run since 2026-06-17; `docs/loop-engineering/` is history). Report-only schedules that change nothing (ADR-089 Ops Watch, the nightly converge check of the ADR-019 amendment) are not such loops. Protocol: [AGENTS.md](AGENTS.md). | unenforced |
| P-7 | **Tests come with the change.** Every new feature includes tests: unit tests for business logic, integration tests for cross-component flows. Tests are living documentation; name them descriptively. | unenforced |
| P-8 | **Test pyramid.** Many unit, some integration, few E2E; E2E lanes opt in with `R4_RUN_E2E=1` (ADR-054). | partial: `tests/conftest.py` skips the heavyweight repo-root tiers unless `R4_RUN_E2E=1` |
| P-9 | **Python 3.10+ only.** | `requires-python = ">=3.10"` in the workspace `pyproject.toml` files and `.python-version`; `tools/bootstrap_validator.py` checks the same floor, pinned by `tests/test_provenance_diff_and_bootstrap.py` |
| P-10 | **Typed public APIs.** Type hints on all public APIs; `mypy` for type checking. | unenforced: CI runs `mypy` as an informational step (`\|\| true` in `.github/workflows/ci.yaml`) |
| P-11 | **Lint and complexity everywhere.** `ruff check` + `ruff format --check` and complexity ≤ 9 per function apply to changed files in every Python tree of the monorepo, the `umrs` submodule included; pre-existing debt lives only in fingerprint baselines that shrink (ADR-052, CC-8). | partial: gate stage 1 runs `ruff check` + `ruff format --check` (parent: no baseline; umrs `--umrs-changed` path: `tools/lint_baseline.py` ratchet), stage 2 `tools/complexity_baseline.py`; CI jobs Lint (ruff on changed files) and Complexity Gate |
| P-12 | **DTOs are `@dataclass`** (preferred over `NamedTuple` or a plain `dict`). | unenforced |
| P-13 | **No undocumented `*args` / `**kwargs`** in a public API. | unenforced |
| P-14 | **Async where I/O-bound; sync where CPU-bound.** | unenforced |
| P-15 | **Errors.** (a) Always clean up resources on failure (containers, Slurm jobs, temp files). (b) Log actionable messages with correlation IDs. (c) Never swallow an exception silently. (d) A validation error says what failed and why. | partial: (c) ruff `E722` (bare `except`) in the lint stage; the rest unenforced |
| P-16 | **Configuration.** All configuration is environment-driven, no hardcoded values; `site.yaml` for site-level config, `.env` for secrets; a new config key has a sensible, documented default. | partial: `tools/config_drift_validator.py` (gate stage 3, CI) refuses dev and placeholder literals in production templates |
| P-17 | **Code documentation.** Every public module has a docstring stating its purpose and boundaries; every public function or method a docstring with params, returns, raises; every DTO field a comment with its meaning and constraints. | unenforced |
| P-18 | **User-facing documentation.** A new CLI command updates `docs/user-manual/13-cli-reference.md`; a new API the relevant user-manual chapter; a new deployment requirement the deployment docs; a breaking change `PLATFORM_CONTRACT.md` with migration notes. | unenforced |

---

## 2. Invariants

These are hard boundaries: a violation means the code is architecturally broken
or exploitable, not just buggy. An invariant is never bypassed, by people or by
agents; changing one is an ADR (CC-3).

### 2.1 Architecture

| ID | Invariant | Enforced by |
|---|---|---|
| INV-01 | **Package boundaries.** `r4` never imports `umrs` or `gateway`; `gateway` never imports `umrs` or `r4`; `umrs` never imports `r4` or `gateway`. `r4` may import `r4-contracts`, the shared contract layer. | partial: `tools/import_boundary_validator.py` (gate stage 3, CI) checks r4 → umrs/gateway, gateway → umrs and umrs → r4; gateway → r4 and umrs → gateway are not checked |
| INV-10 | **`r4-contracts` has zero non-stdlib dependencies** (ADR-005). | `tools/contracts_fence.py` and the stdlib-only rule in `tools/import_boundary_validator.py` (gate stage 3, CI) |
| INV-15 | **UMRS foundation modules stay below the API** (formerly SI-01). `core/`, `model_registry/`, `mcp/`, `rag/`, `pipeline_sdk/`, `orchestrator/`, `security/`, `jobs/`, `shared/` never import `umrs.api` or `umrs.server`; helpers both sides need live in `umrs.shared`. | partial: `tools/import_boundary_validator.py` (gate stage 3, CI) has a rule for every module named except `core/` |
| INV-16 | **One router registry.** `umrs/api/__init__.py` never imports router modules; routers are mounted by the server's explicit registry (`umrs/src/umrs/server/http_sidecar.py`). A router module the server does not mount does not exist as a surface and does not ship: delete it or mount it behind a lane (ADR-053). | partial: `umrs/tests/test_surface_characterization.py` pins the served families (`DORMANT_FAMILIES == {"context"}`, ADR-056); the `__init__` rule is unenforced |
| INV-17 | **Ownership** follows the table below (from the design). | partial: as P-2; `tools/data_authority_validator.py` validates the data-class inventory but is not in the gate |
| INV-02 | **One execution authority per job.** Every job has exactly one `execution_authority` (`slurm` \| `k8s` \| `compose`), chosen at creation and never changed (ADR-004). | partial: the vocabulary is checked by `r4_contracts.extensibility.PluginJobContract.validate` (`tests/test_extensibility_contracts.py`); immutability is unenforced |
| INV-28 | **Schema history only moves forward.** A schema change ships as a migration and bumps the module's `SCHEMA_VERSION`; versions only increase; an applied migration is never edited in place. | partial: `tools/schema_version_tracker.py` (gate stage 3, CI) checks declared versions against `tools/schema_versions.yaml`; edit-in-place is unenforced |
| INV-18 | **Two transport lanes** (ADR-059). Client–server traffic is ZeroMQ with CurveZMQ + ZAP for the data plane and HTTPS for the control plane, both terminated by UMRS; the NGINX + auth-sidecar edge (`gateway/`) is UMRS's edge process. No third client–server lane: a new API family joins the HTTPS control plane or, for bulk data, the ZeroMQ data plane. | partial: `tools/transport_boundary_audit.py` (gate stage 3, CI) keeps direct ZeroMQ use inside approved modules; "no third lane" is unenforced |
| INV-19 | **The edge is stateless.** The edge is the authentication boundary and a proxy/policy layer only: never durable state, retry, orchestration or workflow logic in it; all of that lives in UMRS. An agent does not remove or bypass the edge on its own; that is a maintainer ADR. | partial: `tools/import_boundary_validator.py` keeps `gateway/` free of `umrs` imports; statelessness is unenforced |
| INV-20 | **One loop engine, one team seam.** The server-hosted research loop (`umrs/src/umrs/knowledge/loop.py`, hosted by the `research_loop_job` kind; ADR-036, one driver per ADR-064) is the engine the design's loop is built on; the Team Engine SPI (ADR-020, the one team seam per ADR-072) is the seam for multi-agent teams. New loop or team behaviour extends these; a second loop engine, or a team mechanism beside the seam, is forbidden. | partial: `tools/import_boundary_validator.py` keeps `packages/r4-teamengine-spi` stdlib + `r4-contracts` only; the rule itself is unenforced |

**INV-17 ownership table**

| Owner | Owns | Never |
|---|---|---|
| **UMRS** | accounts and quotas; the client–server transport and its identity; durable jobs, attempts, retry and recovery; scheduling on Slurm / Kubernetes / Podman; remote agent-session venues; model self-deployment and the model registry; raw → vector conversion; S3 and Postgres storage; the MCP registry; the knowledge spine that stores the Scientific Catalog's contents | exposes a bucket, a database or a scheduler to a client |
| **R4** | the console and other user surfaces; the agent harness, skills, context control, multi-agent teams; local session state and the attach/detach controller for remote sessions; project and workflow authoring; the research-loop client and the "ask the scientist" surface | talks to a scheduler, a bucket or a database directly; keeps a second job store, workflow registry or storage credential |
| **Scientific Catalog** | is data: materials, RAG/vector index, knowledge graph, skills, MCP definitions, metadata — produced by UMRS jobs and R4 tools, exported as a bundle | becomes a service with its own transport |
| **Custom MCP servers** | wrap a group's tools; built and published with Hatch, run by Hatchling; registered in UMRS with placement and trust class | are reached by an agent without going through the UMRS registry |

### 2.2 Security

| ID | Invariant | Enforced by |
|---|---|---|
| INV-05 | **Offline inference.** Never add network or egress access to inference containers; `TRANSFORMERS_OFFLINE=1` and `HF_HUB_OFFLINE=1` are always set and `assert-offline.sh` runs at boot (ADR-006). | `umrs/tests/test_mini_rivault_egress.py` (`assert-offline.sh` fails without the offline env or with a proxy var set) |
| INV-21 | **Two egress job classes.** Only `MODEL_DOWNLOAD` and `KNOWLEDGE_INGEST` jobs have egress, through EgressGuard allowlists (ADR-015). | partial: `umrs/tests/knowledge/test_knowledge_jobs.py::test_egress_blocked`, `umrs/tests/test_mini_rivault_egress.py` |
| INV-08 | **Presigned URLs stay internal.** Never expose a presigned S3 URL to an external client; presigned URLs exist so compute nodes can read S3 without credentials. | `umrs/tests/test_files_download.py` (no presigned URL in a download response); `tests/test_gateway_route_characterization.py` (S3 fetch locations internal-only) |
| INV-22 | **No storage credential reaches a client.** Never add a path that hands storage credentials to a client; storage is written only through UMRS. The per-user `s3cfg` delivery stopped 2026-09-22; its opt-in transition window is gap D-16 and is being removed, not extended. | `r4/tests/test_login_s3cfg_d16.py` (no `s3cfg` written by default) |
| INV-06 | **No silent auth degradation.** (a) The edge validates the JWT on every request: never remove NGINX `auth_request` from an edge route. (b) The auth sidecar refuses the dev JWT secret outside development/test (`GATEWAY_ALLOW_INSECURE_DEV_AUTH` only for dev/test). | (a) `tests/test_gateway_route_characterization.py::test_every_gw_route_authenticates_except_documented_probes`; (b) `gateway/sidecar/test_sidecar.py::test_sidecar_rejects_dev_secret_on_public_bind`, `tools/config_drift_validator.py` |
| INV-23 | **ZeroMQ fails closed.** Server sockets in `session` or `curve` mode refuse to bind without key material (`umrs/MERGE_CONSTITUTION.md` §2.3). | `umrs/tests/test_zmq_security.py::test_server_requires_trusted_client_keys_or_explicit_allow_any`, `umrs/tests/test_session_curve_wiring.py::test_session_server_requires_provider` |
| INV-24 | **Site config never overrides the operator.** `SiteConfig.inject_env()` never overwrites an existing env var (ADR-008). | `umrs/tests/test_site_config.py::test_inject_env_no_overwrite` (the default; no caller in `umrs/src` or `r4/src` passes `overwrite=True`) |
| INV-25 | **Knowledge mutation is decision-gated.** Agent-originated knowledge mutation requires a recorded user decision; `knowledge_graph_decide` never auto-approves (migration 0069, ADR-050). | `r4/tests/test_companion_permissions_graph.py`, `umrs/tests/test_knowledge_graphs_api.py::test_an_agent_cannot_decide` |
| INV-26 | **Remote-session workers hold no user secret.** A remote-session worker never holds a user JWT or a provider key; a credential-injecting proxy in the UMRS trust zone adds them (ADR-046). | partial: `umrs/tests/test_infer_proxy.py::test_injected_credential_reaches_upstream_session_token_absent` |
| INV-27 | **Hardware-bound MCP stays on its host.** A hardware-bound MCP server runs only on its designated host and is reached through UMRS (trust classes, ADR-023; gap D-21/D-24). | `umrs/tests/test_mcp_placement.py` (trusted-host-only publish needs an admin; invoke refuses) |
| INV-09 | **Sandbox (mini-RiVault).** Every job, model server and remote agent session runs inside a mini-RiVault: a Podman sandbox with container isolation and egress control (profiles: ADR-061). Co-location with GPU inference is not required (ADR-013, ADR-006 amendment 2026-07-05). Workloads still outside a sandbox (embedding/ingest compute, default MCP runtimes) are gap D-12: add no new ones. | partial: `umrs/tests/test_sandbox_launcher.py`, `umrs/tests/test_mini_rivault_egress.py`; universality is unenforced (D-12) |
| INV-13 | **Knowledge portability.** Knowledge (materials, embeddings, graph, catalog rows and, per the design, skills and MCP definitions) is exportable as a versioned, self-contained, signed bundle and importable elsewhere (ADR-013, ADR-032): `r4-kb` today, the Scientific Catalog bundle when D-39/D-40 close. | partial: `r4/tests/test_knowledge_bundle.py`, `r4/tests/test_kb_roundtrip_e2e.py` |

---

## 3. Forbidden patterns

| ID | Never | Why | Enforced by |
|---|---|---|---|
| F-1 | Ad-hoc SSH deploys (`ssh host './start.sh'`) | Ansible only (`deploy/ansible/bootstrap.sh`, run by the converge workflow) | detected, not prevented: the nightly converge check (`.github/workflows/converge-umrs-front.yaml`) reports drift |
| F-2 | `scp` of individual source files to a remote | Creates drift; use Ansible | as F-1 |
| F-3 | Editing a deployed host's checkout by hand | The checkout is a read-only deploy input; a hot-patch is a repo commit + converge (ADR-019 amendment) | as F-1 |
| F-4 | Hot-reloading config at runtime | Determinism | unenforced; the tree has an operator reload (`POST /umrs/ops/reload`, `umrs/MERGE_CONSTITUTION.md` §2.8): see ADR-094 MQ-2 |
| F-5 | Running more than one scheduler per process | Singleton | partial: `umrs/tests/test_scheduler_service.py` (double-start safety) |
| F-6 | Mixing cleanup with feature work in one PR | Governance rule: each PR stays reviewable and revertable (CC-6) | unenforced |
| F-7 | A client-side job store, workflow registry, retry state machine or storage credential | UMRS owns durable state and storage (INV-17) | partial: as INV-17 |
| F-8 | Pinning a module in `tools/orphan_module_detector.py` to hide that nothing imports it | The detector is a truth surface; a pin carries an evidence link or does not exist | unenforced (the detector itself is hard-fail in gate stage 3) |
| F-9 | `sys.path.insert` or `importlib.util.spec_from_file_location` in a test | Tests never edit `sys.path` or load modules by path; the import graph is the bug (ADR-053) | `tools/test_import_hygiene_validator.py` (gate stage 3, CI) |
| F-10 | Citing `docs/**/archive/` or `docs/history/` as current state | Retired documents carry dated banners for a reason | unenforced |
| F-11 | Committing secrets or credentials | They leak with the history | gate stage 0 secret scan; CI job Check Deploy Keys; `tools/config_drift_validator.py` |
| F-12 | An agent performing credential operations, host edits or force-pushes | These are the maintainer's; the agent names the exact command instead | partial: converge `apply` refuses unless the actor is the repository owner; the rest unenforced |

---

## 4. Change control

| ID | Rule | Enforced by |
|---|---|---|
| CC-1 | **Standard changes** take PR review: bug fixes, new tests, doc corrections, log improvements; new CLI subcommands that use `r4.Client`; config default adjustments with a migration note. | unenforced (review is the control) |
| CC-2 | **Architecture review** is required for: new packages in the monorepo; new cross-package dependencies; schema migrations (INV-28); security boundary changes; new transport protocols (INV-18); changes to enforcement validators; changing lint/complexity gate scope or adding a baseline file (ADR-052). | partial: `tools/schema_version_tracker.py` (gate stage 3, CI); the rest review |
| CC-3 | **An ADR is required** for: (a) any change that moves the tree away from the design, and any edit to the design; (b) reversing a structural decision already taken — transport and edge (ADR-059; D-01/D-02/D-50), topology (ADR-060; D-47), sandbox profiles (ADR-061; D-12), the Scientific Catalog object (D-39); (c) removing permanent infrastructure (ZeroMQ, the DurableJobRuntime bridge); (d) changing the `execution_authority` model; (e) adding a dependency to `r4-contracts`; (f) adding network egress to inference containers; (g) any other architecture decision a contributor might reverse by accident. ADRs live in `docs/adr/` ([index and process](docs/adr/README.md)). | partial: `tools/adr_index_uniqueness_validator.py`, `tools/adr_linkage_validator.py` (gate stage 3, CI) keep the index and references consistent; whether an ADR was needed is review |
| CC-4 | **ADRs are superseded, never deleted.** | partial: `tools/adr_index_uniqueness_validator.py` (every indexed row targets an existing file) |
| CC-5 | **Never move and remove in the same PR.** Moving text or code and cutting the source to a pointer are two PRs. | unenforced |
| CC-6 | **Every PR is independently revertable.** | unenforced |
| CC-7 | **The gate.** No change touching Python lands unless `scripts/ci-lint-local.sh` prints `ALL CHECKS PASSED`; `umrs` changes are gated on the files changed between the two submodule pointers. Commands and the validators to run after each kind of edit: [docs/onboarding/developer.md](docs/onboarding/developer.md) §3. | partial: CI (`.github/workflows/ci.yaml`: Lint, Complexity Gate, Boundary Check) runs the same stages except `knowledge_plane_fence` and `doc_sanity_gate`, which run only in the local gate (MQ-7) |
| CC-8 | **Never relax enforcement to pass.** Never relax or delete a gate, validator, threshold, or a test that enforces a rule here, to make a change pass; a false positive is fixed in the validator. A baseline is a migration record, not an exemption, and only shrinks (ADR-052); exemptions exist only through `tools/exemption_governance_validator.py`. | gate stages 1–3: the baseline ratchets and `tools/exemption_governance_validator.py`; CI |
| CC-9 | **One wave, one ledger entry.** A wave names the gap rows (`D-nn`) it advances and updates their status; it appends one dated entry to `docs/development/wave-ledger.md` (never rewriting an earlier one) and updates the `STATE.md` rows it touched. | unenforced |
| CC-10 | **Escalate by name.** A finding that needs the maintainer is escalated by name in `STATE.md` and in the plan's escalation table: never silently deferred, never silently decided. | unenforced |
| CC-11 | **Amending this file.** This constitution changes only through an ADR the maintainer accepts; an agent may draft such an ADR but never accepts it, because these rules govern the agent's own authority. Each amendment adds a changelog line below. | unenforced |
| CC-12 | **One home per rule.** A rule is defined once, here; the files of §5 may restate it in one line with its ID and never redefine it. | unenforced (a guard test is planned: platform hardening plan H-11.4) |

---

## 5. Where procedures live

| What | Home |
|---|---|
| Agent entry, rules that bite, wave contract, concurrent-session safety (fetch and rebase before every push) | [AGENTS.md](AGENTS.md); method and hand-off in [docs/onboarding/agent.md](docs/onboarding/agent.md) |
| Human workflow: branch and PR, ownership check, docs, release | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Setup, the gate commands, validators after each kind of edit, suite commands, the enforcement scorecard | [docs/onboarding/developer.md](docs/onboarding/developer.md) §2–§3; [TESTING.md](TESTING.md) for tests |
| Reading order, where each kind of truth lives, which source wins | [docs/README.md](docs/README.md) |
| Documentation freshness | [living-documentation.instructions.md](.github/instructions/living-documentation.instructions.md) |
| Deployment steps (Ansible, converge, rollback) | [deployment-operations.instructions.md](.github/instructions/deployment-operations.instructions.md) |
| Path-scoped domain rules | [.github/instructions/](.github/instructions/) |
| Merge safety of the `umrs` submodule | [umrs/MERGE_CONSTITUTION.md](umrs/MERGE_CONSTITUTION.md) |
| Operator-facing guarantees of the current implementation | [PLATFORM_CONTRACT.md](PLATFORM_CONTRACT.md) |
| UMRS-first integration boundaries | [INTEGRATION_POLICY.md](docs/architecture/boundaries/INTEGRATION_POLICY.md) |
| Decisions, their status vocabulary and template | [docs/adr/README.md](docs/adr/README.md) |
| Target, distance, order of work, code map | [design](docs/R4-UMRS-DESIGN.md), [gap analysis](docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md), [ROADMAP.md](ROADMAP.md), [ARCHITECTURE.md](ARCHITECTURE.md) |

## Changelog

- YYYY-MM-DD: ADR-094 — every rule numbered (P, INV, F, CC) with its enforcement; legacy INV-01…INV-13 meanings kept; procedures (§7 validator commands, §9.2 wave contract, §9.4 truth surfaces, §10 document list) moved to docs/onboarding/developer.md, AGENTS.md and docs/README.md; stale D-01/D-02 wording corrected per ADR-059 and D-29 per ADR-072; STEADY_STATE_ENGINEERING_POLICY.md retired to `docs/history/`; INV-28, CC-4, CC-6 carried from it; CC-11, CC-12 added. Old `§x.y` → new ID: ADR-094 Appendix B.
- 2026-09-22: design reset — §1.3 ownership re-derived from the design; §1.5 edge rule scoped to D-01/D-02; §1.6 one loop / one team engine; §2 rows for storage credentials, ZeroMQ fail-closed, decision-gated knowledge mutation, remote-session credentials, MCP trust hosts; §2.1 sandbox rule; §4.3 doc set; §5.3 ADR triggers for design moves; §6 new forbidden patterns; §7 scorecard re-run (A:24, 390 tests); §9 ledger moved to `docs/development/wave-ledger.md`.
- 2026-09-14: §1.2 jobs boundary + api/__init__ rule; §4.1 gate scope; §7 host-edit + dormant-router patterns; §8 scorecard + umrs gates; §10 Loop → Wave Engineering; §11 links (structure-hardening-plan-2026-09-14, ADR-052/053/054).
````

## Appendix B — old section → new ID (every rule of the 2026-09-22 text)

"Moved" means the text leaves the constitution for the named home (PR 1 of
D-7 puts it there before PR 2 cuts it). "Merged" means the old text was a
second statement of a rule that keeps one ID. Nothing is dropped.

| Old (CONSTITUTION.md as of 2026-09-22, lines as of 2026-10-18) | New | Change |
|---|---|---|
| Header, intro (`:1-13`) | Intro | Kept. The status line is reworded, and the "how to read and cite" paragraph is new |
| §1 intro "HARD boundaries" (`:19-20`) | §2 intro | Kept |
| §1.1 `r4` never imports `umrs` | INV-01 | Kept |
| §1.1 `gateway` never imports `umrs` or `r4` | INV-01 | Kept; the enforcement cell now says gateway → r4 is not checked |
| §1.1 `umrs` never imports `r4` or `gateway` | INV-01 | Kept; umrs → gateway not checked |
| §1.1 `r4-contracts` zero non-stdlib deps | INV-10 | Kept (legacy number) |
| §1.1 `r4` MAY import `r4-contracts` | INV-01 | Kept as the permission clause |
| §1.1 Enforcement line | INV-01, INV-10 cells | Made precise |
| §1.2 ¶1 foundation modules never import `umrs.api` / `umrs.server` | INV-15 | Kept; "formerly SI-01"; `core/` has no validator rule |
| §1.2 ¶2 `umrs/api/__init__.py` imports no router; explicit registry; unmounted router does not ship | INV-16 | Kept |
| §1.2 Enforcement line | INV-15, INV-16 cells | Kept (`test_surface_characterization.py`, ADR-056) |
| §1.3 ownership table | INV-17 table | Kept verbatim |
| §1.3 "Rule: if UMRS already owns a capability…" | P-2 | Kept |
| §1.4 execution authority | INV-02 | Kept (legacy number) |
| §1.5 edge is stateless; agent never removes or bypasses the edge | INV-19 | Kept. "Until decision D-01/D-02 is taken" corrected per ADR-059 |
| §1.5 "Until decision D-01/D-02 is taken" (the transport left open) | INV-18 | Corrected: states ADR-059's two-lane transport as decided |
| §1.6 one loop, one team engine; "fifth mechanism" | INV-20 | Corrected per ADR-064/ADR-072: one loop engine, one team seam |
| §2 intro "exploitable vulnerabilities" | §2 intro | Kept |
| §2 row: no network/egress in inference containers; offline env; `assert-offline.sh` | INV-05 | Kept (legacy number) |
| §2 row: only `MODEL_DOWNLOAD` / `KNOWLEDGE_INGEST` egress | INV-21 | Kept |
| §2 row: no presigned S3 URLs to external clients | INV-08 | Kept (legacy number) |
| §2 row: no new path handing storage credentials to a client | INV-22 | Kept; D-16 status updated (delivery stopped 2026-09-22) |
| §2 row: never remove NGINX `auth_request`; JWT on every request | INV-06 (a) | Kept; "until D-02 moves authentication into UMRS" corrected per ADR-059 (MQ-4) |
| §2 row: sidecar refuses dev JWT secret outside dev/test | INV-06 (b) | Kept (MQ-4) |
| §2 row: ZeroMQ `session`/`curve` refuse to bind without keys | INV-23 | Kept |
| §2 row: `SiteConfig.inject_env()` never overwrites | INV-24 | Kept |
| §2 row: decision-gated knowledge mutation | INV-25 | Kept |
| §2 row: remote-session workers hold no JWT or provider key | INV-26 | Kept |
| §2 row: hardware-bound MCP on its host, through UMRS | INV-27 | Kept; D-24 trust classes recorded as landed |
| §2.1 sandbox rule | INV-09 | Kept (legacy number), but ADR-006's narrower INV-09 is widened (D-2, MQ-8); ADR-061 profiles named |
| §2.2 knowledge portability ("INV-13") | INV-13 | Kept (same ID) |
| §2.2 Enforcement line | per-row cells | Corrected: `gateway/tests/test_security_contracts.py` was deleted in `0aac3ec2` |
| §3.1 Python 3.10+ | P-9 | Kept |
| §3.1 type hints on public APIs | P-10 | Kept |
| §3.1 `ruff` lint, `mypy` type check | P-11 (ruff), P-10 (mypy) | Kept; mypy recorded as informational in CI |
| §3.1 `@dataclass` DTOs | P-12 | Kept |
| §3.1 no undocumented `*args`/`**kwargs` | P-13 | Kept |
| §3.1 async I/O, sync CPU | P-14 | Kept |
| §3.1 lint + complexity in every tree incl. umrs; baselines only shrink; a baseline is not an exemption | P-11 (scope), CC-8 (baselines) | Kept |
| §3.1 tests never edit `sys.path` | F-9 | Merged with §6 row |
| §3.2 every new feature has tests; unit/integration roles; descriptive names | P-7 | Kept |
| §3.2 pyramid; E2E opt-in `R4_RUN_E2E=1` | P-8 | Kept |
| §3.3 error handling (4 bullets) | P-15 (a)–(d) | Kept |
| §3.4 env-driven config; `site.yaml` / `.env`; defaults documented | P-16 | Kept |
| §3.4 never commit secrets or credentials | F-11 | Kept |
| §4.1 module / function / DTO-field documentation | P-17 | Kept |
| §4.1 architecture decisions as ADRs | CC-3 (g) | Kept |
| §4.2 user-facing documentation (4 bullets) | P-18 | Kept |
| §4.3 document list (design, gap analysis, overview, ROADMAP, CONSTITUTION, entry files, plans index, ledger, ADR index, STATE) | Moved: `docs/README.md` §2 | Procedure (where each truth lives) |
| §4.3 "design changes are ADRs" | P-1, CC-3 (a) | Kept |
| §4.3 "a wave that changes a gap row updates it in the same commit" | P-5 | Kept |
| §4.3 "archived plans are never cited as current" | F-10 | Merged |
| §4.3 "ledger: one entry per wave, append-only" | CC-9 | Kept |
| §4.3 living documentation rule | P-5 | Kept |
| §4.3 "documents dated before 2026-09-22 … verify before citing" | P-3 | Kept |
| §5.1 standard changes | CC-1 | Kept |
| §5.2 architecture review list (7 items) | CC-2 | Kept |
| §5.3 ADR: move away from design / edit design | CC-3 (a) | Kept |
| §5.3 ADR: taking a structural decision (D-01/D-02, D-47, D-39, D-12) | CC-3 (b) | Corrected: these decisions are taken (ADR-059/060/061; D-39 object landed 2026-09-22); reversing one needs an ADR |
| §5.3 ADR: removing ZeroMQ or the DurableJobRuntime bridge | CC-3 (c) | Kept |
| §5.3 ADR: changing the `execution_authority` model | CC-3 (d) | Kept |
| §5.3 ADR: dependencies in `r4-contracts` | CC-3 (e) | Kept |
| §5.3 ADR: egress to inference containers | CC-3 (f) | Kept |
| §6 ad-hoc SSH deploys | F-1 | Kept |
| §6 `scp` source files | F-2 | Kept |
| §6 editing a host checkout | F-3 | Kept |
| §6 hot-reload config at runtime | F-4 | Kept verbatim; contradiction recorded (MQ-2) |
| §6 multiple schedulers per process | F-5 | Kept |
| §6 cleanup mixed with feature work | F-6 | Kept |
| §6 `import umrs` in `r4/` … | INV-01 | Merged |
| §6 client-side job store, registry, retry machine, storage credential | F-7 | Kept |
| §6 a new loop or team engine | INV-20 | Merged |
| §6 unmounted `umrs/api` router | INV-16 | Merged |
| §6 `sys.path.insert` / `spec_from_file_location` in a test | F-9 | Kept |
| §6 pinning a module in `orphan_module_detector` | F-8 | Kept |
| §6 citing `archive/` or `docs/history/` | F-10 | Kept |
| §7 validator commands after Python / schema / DTO / deploy edits | Moved: docs/onboarding/developer.md §3 | Procedure |
| §7 full enforcement regression + scorecard `A:<N>` (N=24 on 2026-09-22) | Moved: docs/onboarding/developer.md §3 | Procedure; the count is re-run when it moves |
| §7 exemptions governed by `exemption_governance_validator.py` | CC-8 | Kept |
| §7 umrs gated on the pointer diff (`--umrs-changed`) | CC-7 (rule); command → docs/onboarding/developer.md §3 | Kept |
| §7 canonical gate must print `ALL CHECKS PASSED` | CC-7 (rule); commands → docs/onboarding/developer.md §3 | Kept |
| §7 never relax a gate, validator or threshold | CC-8 | Kept |
| §8 intro "AI coding agents MUST" | — | The rules apply to everyone; each item below has its ID |
| §8.1 verify against implementation | P-3 | Merged with §9.3.1 |
| §8.2 name gap rows and update their status | CC-9 | Kept |
| §8.3 run validators after every edit | CC-7 + docs/onboarding/developer.md §3 | Merged |
| §8.4 never bypass security invariants | §2 intro ("never bypassed") | Merged |
| §8.5 Ansible only; never edit a host checkout | F-1…F-3 | Merged |
| §8.6 architectural decisions as ADRs; design only with one | CC-3, P-1 | Merged |
| §8.7 prefer additive changes | P-4 | Kept |
| §8.8 never move + remove in the same PR | CC-5 | Kept |
| §8.9 no credential operations, host edits, force-pushes | F-12 | Merged with §9.3.4 |
| §9.1 maintainer-directed waves; cron loops retired | P-6 | Kept; one clarifying sentence (MQ-6) |
| §9.2 wave contract block | Moved (abridged): AGENTS.md "Wave contract" holds "Maintainer defines", "Agent does" and "Maintainer reviews" (plus a Report line); "State stores" → docs/README.md §2 (where each kind of truth lives) | Procedure; P-6 keeps the roles |
| §9.3.1 verify before asserting | P-3 | Kept |
| §9.3.2 one wave, one ledger entry; STATE rows | CC-9 | Kept |
| §9.3.3 escalate by name | CC-10 | Kept |
| §9.3.4 no credential operations, host edits, force-pushes | F-12 | Kept |
| §9.3.5 concurrent waves; fetch and rebase before push | Moved: AGENTS.md rule 8 (present 2026-10-18) | Procedure |
| §9.4 truth surfaces table | Moved: `docs/README.md` §1–§2 | Procedure (reading order and precedence) |
| §10 related documents | §5 pointer table | STEADY_STATE row removed (D-5) |
| Changelog 2026-09-22, 2026-09-14 | Changelog | Kept; new entry added |
| — | INV-28, CC-4, CC-6 | **New here**, carried from the steady-state policy (MQ-3) |
| — | CC-11 | **New**: amendment authority (cleanup plan A-14) |
| — | CC-12 | **New**: one home per rule (D-4) |

## Appendix C — disposition of STEADY_STATE_ENGINEERING_POLICY.md (2026-05-13)

| Section | Disposition |
|---|---|
| Declaration ("boundaries are FINAL", "preserve stability") | Not carried. It contradicts the design reset of 2026-09-22 and the wave model (P-6) |
| §1 change categories A–D | Superseded. Old §5.1–§5.3 were derived from them on 2026-09-22 and are now CC-1…CC-3. Not carried (MQ-3): C/D items "new thread or asyncio task", "new persistence backends", "new UMRS module directories", "boot/shutdown sequence", "threading model", "new deployment environments" |
| §2 forbidden changes (14 rows) | Each row is already a rule: ZMQ and DurableJobRuntime removal → CC-3 (c); `r4-contracts` deps → INV-10, CC-3 (e); UMRS imports → INV-01; execution authority → INV-02; inference egress and `TRANSFORMERS_OFFLINE` → INV-05; `inject_env` → INV-24; hot reload → F-4; schedulers → F-5; cleanup with features → F-6; presigned URLs → INV-08; gateway state / retry / workflow → INV-19 |
| §3 runtime stability (APIs additive, ZMQ formats versioned, config key semantics, boot order) | Database line carried as INV-28. The rest is not carried (MQ-3). Versioned wire contracts: gap row D-04, Implemented (protocol v1.0 on the shared parser); not carried as a separate rule (MQ-3) |
| §4 validator evolution | Review of validator changes → CC-2. "False positives fixed in the validator" → CC-8. "All validators run in CI as hard-fail" not carried: it is false today (see Context). `ARCHITECTURE_ENFORCEMENT.md` / `EXEMPTION_INVENTORY.md` bookkeeping not carried; exemptions → CC-8 |
| §5 cleanup safety | "Never remove ADRs (supersede them)" → CC-4. "Never remove test infrastructure for existing invariants" → CC-8. "Never remove canonical governance documents" not carried: retiring this file to `docs/history/` keeps the file. Safe/unsafe cleanup lists not carried: they cite pre-reset documents, and the repo cleanup plan governs cleanup |
| §6 rollback | "Every PR independently revertable" → CC-6. "Never combine cleanup with feature work" → F-6. Schema: forward migrations, version only increases, never edit an applied migration → INV-28. Not carried (MQ-3): backward migrations recommended, feature flags preferred, removed config keys ignored gracefully, config changes reversible by env var. Deployment rollback steps live in `deployment-operations.instructions.md` |
| §7 review triggers and owners | Dependencies, schema, enforcement tools and transport are covered by CC-2; ADRs by CC-3. Owner roles (architecture owner, ops team, security owner) not carried: the project has one maintainer |
| §8 stewardship cadence | Not carried: its counts are stale. Periodic read-and-report sweeps are platform hardening plan H-12 |
| §9 relationships | History, with the file |
