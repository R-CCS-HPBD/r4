# R4 + UMRS Platform Constitution

> **Audience:** Human developers, AI coding agents, contributors
> **Status:** CANONICAL — governs all contributions
> **Last updated:** 2026-09-22 (design reset: rules re-derived from
> [docs/R4-UMRS-DESIGN.md](docs/R4-UMRS-DESIGN.md); enforcement unchanged)
>
> **Erratum (2026-10-18): known stale passages.** These facts changed after
> the 2026-09-22 text. The rules themselves are unchanged until
> [ADR-094](docs/adr/094-constitution-amendment-numbered-principles.md)
> (Proposed) is accepted; [docs/README.md](docs/README.md) §2 says which
> source wins.
> - §1.5 "Until decision D-01/D-02 is taken", the §2 `auth_request` row
>   "until D-02 moves authentication into UMRS", and §5.3's list of
>   structural decisions still to take: transport and edge were decided by
>   ADR-059, topology by ADR-060 and sandbox profiles by ADR-061 (all
>   2026-09-22); the catalog object has landed (D-39 Partial).
> - §1.6 "a fifth mechanism": ADR-072 (2026-09-24) made the team mechanisms
>   one seam (D-29 Implemented).
> - §2.2 Enforcement names `gateway/tests/test_security_contracts.py`, deleted
>   in `0aac3ec2` (ADR-014).
> - §3.1 "`mypy` for type checking": CI runs mypy as an informational step
>   (`|| true` in `.github/workflows/ci.yaml`).
> - §4.3 lists CLAUDE.md first among the agent files: since 2026-10-18
>   AGENTS.md is the entry, CLAUDE.md a pointer, and
>   [docs/README.md](docs/README.md) holds the one reading order.

This document defines the non-negotiable engineering principles that all
contributors — human and AI — must follow. It is concise, actionable, and
enforceable through automated validators. Where the design and the tree
still differ, the difference is a numbered row in
[docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md)
(`D-nn`); the rules below say which side wins in the meantime.

---

## 1. Architecture Invariants

These rules are HARD boundaries. Violation means the code is architecturally
broken, not just buggy.

### 1.1 Package Boundary Rules

| Rule | Rationale |
|------|-----------|
| `r4` NEVER imports `umrs` | UMRS is a separate service, not a library |
| `gateway` NEVER imports `umrs` or `r4` | The edge is a proxy, not a client |
| `umrs` NEVER imports `r4` or `gateway` | UMRS is a standalone service |
| `r4-contracts` has ZERO non-stdlib dependencies | Shared contracts must be dependency-free |
| `r4` MAY import `r4-contracts` | Contracts are the safe sharing layer |

**Enforcement:** `tools/import_boundary_validator.py` — runs in CI, hard-fail.

### 1.2 UMRS Internal Boundaries

Foundation modules (`core/`, `model_registry/`, `mcp/`, `rag/`, `pipeline_sdk/`,
`orchestrator/`, `security/`, `jobs/`, `shared/`) NEVER import from `umrs.api`
or `umrs.server`. Shared helpers both sides need live in `umrs.shared`.

`umrs/api/__init__.py` NEVER imports router modules. Routers are mounted by the
server's explicit registry (`umrs/src/umrs/server/http_sidecar.py`); a router
module that is not mounted there does not exist as a surface and must not ship
(ADR-053).

**Enforcement:** `tools/import_boundary_validator.py` — hard-fail;
`umrs/tests/test_surface_characterization.py` (`DORMANT_FAMILIES == {"context"}`, ADR-056).

### 1.3 Ownership (from the design)

| Owner | Owns | Never |
|---|---|---|
| **UMRS** | accounts and quotas; the client–server transport and its identity; durable jobs, attempts, retry and recovery; scheduling on Slurm / Kubernetes / Podman; remote agent-session venues; model self-deployment and the model registry; raw → vector conversion; S3 and Postgres storage; the MCP registry; the knowledge spine that stores the Scientific Catalog's contents | exposes a bucket, a database or a scheduler to a client |
| **R4** | the console and other user surfaces; the agent harness, skills, context control, multi-agent teams; local session state and the attach/detach controller for remote sessions; project and workflow authoring; the research-loop client and the "ask the scientist" surface | talks to a scheduler, a bucket or a database directly; keeps a second job store, workflow registry or storage credential |
| **Scientific Catalog** | is data: materials, RAG/vector index, knowledge graph, skills, MCP definitions, metadata — produced by UMRS jobs and R4 tools, exported as a bundle | becomes a service with its own transport |
| **Custom MCP servers** | wrap a group's tools; built and published with Hatch, run by Hatchling; registered in UMRS with placement and trust class | are reached by an agent without going through the UMRS registry |

**Rule:** if UMRS already owns a capability, R4 consumes it through thin
adapters. Never reimplement UMRS logic locally.

### 1.4 Execution Authority

Every job has exactly one `execution_authority` (`slurm` | `k8s` | `compose`),
chosen at creation, NEVER changed (ADR-004).

### 1.5 The edge is stateless

Until decision D-01/D-02 is taken, the NGINX + auth-sidecar edge (`gateway/`)
is the authentication boundary. It is a proxy/policy layer ONLY: never add
durable state, retry, orchestration or workflow logic to it. All of that
lives in UMRS. An agent does not remove or bypass the edge on its own; that
is the maintainer's ADR.

### 1.6 One loop, one team engine

The server-hosted research loop (`umrs/knowledge/loop.py` hosted by the
`research_loop_job` kind, ADR-036) is the engine the design's loop is built
on; the Team Engine SPI (ADR-020) is the seam for multi-agent teams. New loop
or team behaviour extends these; adding a fifth mechanism is forbidden
(gap rows D-29, D-32).

---

## 2. Security Invariants

These rules protect the platform's security model. Violation may create
exploitable vulnerabilities.

| Rule | Rationale |
|------|-----------|
| NEVER add network/egress access to inference containers | `TRANSFORMERS_OFFLINE=1`, `HF_HUB_OFFLINE=1` must always be set; `assert-offline.sh` runs at boot (ADR-006) |
| Only `MODEL_DOWNLOAD` and `KNOWLEDGE_INGEST` jobs have egress, through EgressGuard allowlists | ADR-015 |
| NEVER expose presigned S3 URLs to external clients | Presigned URLs exist so compute nodes can read S3 without credentials |
| NEVER add a new path that hands storage credentials to a client | The design says storage is written only through UMRS; the existing per-user `s3cfg` delivery is gap D-16 and is being removed, not extended |
| NEVER remove NGINX `auth_request` from edge routes; the edge validates the JWT on every request | All traffic must be authenticated (until D-02 moves authentication into UMRS) |
| Auth sidecar MUST refuse the dev JWT secret outside development/test | `GATEWAY_ALLOW_INSECURE_DEV_AUTH` only for dev/test |
| ZeroMQ server sockets in `session` or `curve` mode refuse to bind without key material | Fail-closed transport (MERGE_CONSTITUTION §2.3) |
| NEVER overwrite existing env vars in `SiteConfig.inject_env()` | Site config must not silently override operator settings (ADR-008) |
| Agent-originated knowledge mutation requires a recorded user decision; `knowledge_graph_decide` never auto-approves | Semi-automatic catalog growth proposes, it does not commit (migration 0069, ADR-050) |
| Remote-session workers never hold a user JWT or a provider key | Credential-injecting proxy in the UMRS trust zone (ADR-046) |
| Hardware-bound MCP servers run only on their designated host and are reached through UMRS | Design trust boundary; today enforced by ADR-023 capability flags (gap D-21/D-24) |

### 2.1 Sandbox rule (mini-RiVault)

Every job, model server and remote agent session runs inside a
mini-RiVault: a Podman sandbox with container isolation and egress control.
Co-location with GPU inference is not required (ADR-013). Workloads that
still run outside a sandbox (embedding/ingest compute, default MCP runtimes)
are gap D-12 — do not add new ones.

### 2.2 Knowledge portability (INV-13)

Knowledge (materials, embeddings, graph, catalog rows, and — per the design —
skills and MCP definitions) must be exportable as a versioned, self-contained,
signed bundle and importable elsewhere (ADR-013, ADR-032; `r4-kb` today, the
Scientific Catalog bundle when D-39/D-40 close).

**Enforcement:** `tools/contracts_fence.py`, `tools/config_drift_validator.py`,
`gateway/tests/test_security_contracts.py`, EgressGuard tests.

---

## 3. Coding Standards

### 3.1 Python
- Python 3.10+ only
- Type hints on all public APIs
- `ruff` for linting, `mypy` for type checking
- `@dataclass` for DTOs (prefer over `NamedTuple` or plain `dict`)
- No `*args`, `**kwargs` in public APIs without explicit documentation
- Async where I/O-bound; sync for CPU-bound
- Lint (`ruff check` + `ruff format --check`) and complexity (max 9) gates apply
  to changed files in EVERY Python tree of the monorepo, including the `umrs`
  submodule. Pre-existing debt lives in fingerprint baselines that may only
  shrink (ADR-052). A baseline is a migration record, not an exemption.
- Tests never edit `sys.path` or load modules by file path. If an import needs
  either, the import graph is the bug (ADR-053).

### 3.2 Testing
- Every new feature MUST include tests
- Unit tests for business logic, integration tests for cross-component flows
- Test pyramid: many unit, some integration, few E2E; E2E lanes opt in with `R4_RUN_E2E=1` (ADR-054)
- Tests are living documentation — name them descriptively

### 3.3 Error Handling
- Always clean up resources on failure (containers, Slurm jobs, temp files)
- Log actionable error messages with correlation IDs
- Never swallow exceptions silently
- Validation errors must include WHAT failed and WHY

### 3.4 Configuration
- ALL configuration is environment-driven — no hardcoded values
- Use `site.yaml` for site-level config, `.env` for secrets
- Never commit secrets or credentials
- New config keys must have sensible defaults with documentation

---

## 4. Documentation Requirements

### 4.1 Code Documentation
- Every public module: docstring describing purpose and boundaries
- Every public function/method: docstring with params, returns, raises
- Every DTO field: comment describing meaning and constraints
- Architecture decisions: ADR in `docs/adr/`

### 4.2 User-Facing Documentation
- New CLI commands: update `docs/user-manual/13-cli-reference.md`
- New APIs: update the relevant user-manual chapter
- New deployment requirements: update deployment docs
- Breaking changes: update `PLATFORM_CONTRACT.md` and add migration notes

### 4.3 Design and Agent Documentation
- `docs/R4-UMRS-DESIGN.md`: the target design — changes are ADRs
- `docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md`: design-vs-code rows — a wave that changes a row's status updates it in the same commit
- `docs/R4-UMRS-PLATFORM-OVERVIEW.md`: what runs today (dated header)
- `ROADMAP.md`: the order of closing gaps
- `CONSTITUTION.md` (this file): non-negotiable rules
- `CLAUDE.md`, `AGENTS.md`, `R4.md`, `.github/instructions/*.md`: agent entry, protocol, briefing, domain rules
- `docs/development/README.md`: index of live plans; archived plans are never cited as current
- `docs/development/wave-ledger.md`: dated wave ledger (one entry per wave, append-only)
- `docs/adr/README.md` + `docs/adr/_template.md`: ADR index, status vocabulary, template
- `STATE.md`: release track + escalations (automation mode MANUAL)
- **Living documentation rule**: every landed change updates the docs that
  describe it in the same wave — status, dates, evidence (commit hashes).
  Policy: `.github/instructions/living-documentation.instructions.md`.
  Documents dated before 2026-09-22 describe the pre-design narrative; verify
  against code before citing them.

---

## 5. Review Requirements

### 5.1 Standard Changes (PR Review)
- Bug fixes, new tests, doc corrections, log improvements
- New CLI subcommands (using `r4.Client`)
- Config default adjustments (with migration note)

### 5.2 Architecture Review Required
- New packages in the monorepo
- New cross-package dependencies
- Schema migrations (bump `SCHEMA_VERSION`)
- Security boundary changes
- New transport protocols
- Changes to enforcement validators
- Changing lint/complexity gate SCOPE or adding a baseline file (ADR-052)

### 5.3 ADR Required
- Any change that moves the tree away from `docs/R4-UMRS-DESIGN.md`, or any edit to the design itself
- Taking one of the structural decisions in the gap analysis (D-01/D-02 transport and edge, D-47 topology, D-39 catalog object, D-12 sandbox universality)
- Removing permanent infrastructure (ZeroMQ, DurableJobRuntime bridge)
- Changing the `execution_authority` model
- Adding dependencies to `r4-contracts`
- Adding network egress to inference containers

---

## 6. Forbidden Patterns

| Pattern | Why Forbidden |
|---------|---------------|
| Ad-hoc SSH deploys (`ssh host './start.sh'`) | Use Ansible (`deploy/ansible/bootstrap.sh`) |
| `scp` individual source files to a remote | Creates drift; use Ansible |
| Editing a deployed host's checkout by hand | The checkout is a read-only deploy input; hot-patches are a repo commit + converge (ADR-019 amendment) |
| Hot-reload config at runtime | Determinism invariant |
| Running multiple schedulers per process | Singleton invariant |
| Mixing cleanup with feature work in one PR | Governance rule |
| `import umrs` in `r4/` or `gateway/`; `import r4` in `umrs/` or `gateway/`; `import gateway` anywhere else | Package boundary violation |
| A client-side job store, workflow registry, retry state machine or storage credential | UMRS owns durable state and storage (§1.3) |
| A new loop or team engine beside the existing ones | §1.6 |
| Shipping an `umrs/api` router module that is not mounted by the server | Dead surface; delete it or mount it behind a lane (ADR-053) |
| `sys.path.insert` / `importlib.util.spec_from_file_location` in a test | Masks an import-graph defect; fix the graph (ADR-053) |
| Pinning a module in `orphan_module_detector` to hide that nothing imports it | The detector is a truth surface; pins carry evidence links or do not exist |
| Citing `docs/**/archive/` or `docs/history/` as current state | Retired documents carry dated banners for a reason |

---

## 7. Validation After Edits

After editing ANY Python file:
```bash
python tools/import_boundary_validator.py
python tools/contracts_fence.py
python tools/stale_name_validator.py
```

After schema changes:
```bash
python tools/schema_version_tracker.py
```

After DTO changes:
```bash
python tools/dto_equivalence_check.py
```

After deployment config changes:
```bash
python tools/config_drift_validator.py
```

Full enforcement regression:
```bash
.venv/bin/python -m pytest tests/test_import_boundary_validator.py \
  tests/test_contracts_fence.py tests/test_dto_equivalence.py \
  tests/test_schema_version_tracker.py tests/test_stale_name_validator.py \
  tests/test_topology_tools.py -v
python tools/enforcement_maturity_scorer.py
```

Expected scorecard: `A:<N> B:0 C:0 D:0` — read N from
`tools/enforcement_maturity_scorer.py` output; the registry count changes when
validators are added, and this line must be updated in the same commit.
(N=24, 390 tests, on 2026-09-22.) Exemptions are governed by
`tools/exemption_governance_validator.py`.

umrs changes are gated on the files that changed BETWEEN the two submodule
pointers, not on the parent diff (which never descends into the submodule):
```bash
bash scripts/ci-lint-local.sh --umrs-changed
```

Canonical gate before any commit touching Python (stages 0–4: secret scan,
ruff lint+format, complexity max-9 + xenon + lizard + shellcheck, the boundary
validators incl. `doc_sanity_gate.py`, component-scoped pytest):
```bash
bash scripts/ci-lint-local.sh                 # changed files vs base
bash scripts/ci-lint-local.sh --files a.py b  # explicit files
```
It must print `ALL CHECKS PASSED`. Never relax a gate, validator or threshold
to make it pass.

---

## 8. AI Agent Expectations

AI coding agents working on this codebase MUST:

1. **Verify against implementation** before claiming behaviour
2. **Name the gap rows** (`D-nn`) a wave advances, and update their status
3. **Run validators** after every file edit (import boundaries, contracts, config drift)
4. **Never bypass security invariants** (egress, auth, offline inference, decision-gated knowledge mutation)
5. **Use Ansible for deployment** — never ad-hoc SSH; never edit a host checkout
6. **Document architectural decisions** as ADRs; never edit the design without one
7. **Prefer additive changes** — add new code alongside old, migrate consumers, then remove old
8. **Never move + remove in the same PR** — split into separate PRs
9. **Never perform credential operations, host edits, or force-pushes**

---

## 9. Wave Engineering

### 9.1 Declaration

Development proceeds in **maintainer-directed agent waves**: the maintainer
states an objective and the gap rows in scope; a coding agent (any model)
verifies the tree, plans, implements, gates, documents and reports; the
maintainer reviews and merges. Unattended cron loops (the 2026-06 model) are
retired: no loop has run since 2026-06-17; their documents under
`docs/loop-engineering/` are history.

### 9.2 The Wave Contract

```
Maintainer defines:  the objective, the authority granted, what is out of scope, gap rows in scope
Agent does:          Verify → Decide (or escalate) → Implement → Gate → Document → Report
State stores:        docs/development/wave-ledger.md (dated ledger), STATE.md (release track + escalations),
                     docs/development/*.md (plans), docs/adr/* (decisions + rollout blocks),
                     docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md (row status)
Maintainer reviews:  merges, live/host actions, anything an agent escalated
```

### 9.3 Wave rules

1. Verify before asserting: every claim in a report cites a command, a file:line
   or a commit. Docs are re-verified against code before they are cited.
2. One wave, one ledger entry: append to `docs/development/wave-ledger.md`,
   never rewrite earlier entries; update `STATE.md` rows the wave touched.
3. Findings that need the maintainer are escalated by name in `STATE.md` and in
   the plan's escalation table — never silently deferred and never silently decided.
4. Agents never perform credential operations, host edits, or force-pushes.
5. Concurrent waves are normal; fetch and rebase before every push.

### 9.4 Truth surfaces (read these before acting)

| File | Purpose |
|------|---------|
| `docs/R4-UMRS-DESIGN.md` | The target |
| `docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md` | Design vs code, per row |
| `STATE.md` | Release track, escalations, automation state (`Mode: MANUAL`) |
| `docs/development/wave-ledger.md` | Dated wave ledger |
| `docs/development/README.md` | Index of live plans |
| `docs/adr/README.md` | Decisions; rollout blocks inside ADRs are current truth |
| `tools/` validators + `scripts/ci-lint-local.sh` | What is enforced |

## 10. Related Documents

| Document | Purpose |
|----------|---------|
| [docs/R4-UMRS-DESIGN.md](docs/R4-UMRS-DESIGN.md) | Target design |
| [docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md) | Gap rows |
| ROADMAP.md | Order of closing gaps |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Target architecture + implementation map |
| [PLATFORM_CONTRACT.md](PLATFORM_CONTRACT.md) | Operator-facing guarantees of the current implementation |
| [STEADY_STATE_ENGINEERING_POLICY.md](docs/architecture/governance/STEADY_STATE_ENGINEERING_POLICY.md) | Change governance tiers (A/B/C/D) and forbidden categories |
| [INTEGRATION_POLICY.md](docs/architecture/boundaries/INTEGRATION_POLICY.md) | UMRS-first integration boundaries |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup and workflow |
| [TESTING.md](TESTING.md) | Testing guide and standards |
| [docs/adr/](docs/adr/) | Architecture Decision Records (ADR-052 gates, ADR-053 import discipline, ADR-054 workspace + test tiers) |
| [umrs/MERGE_CONSTITUTION.md](umrs/MERGE_CONSTITUTION.md) | Merge-safety invariants for the submodule |

## Changelog

- 2026-10-18: erratum block under the header (stale facts named, rules unchanged; corrected text proposed in ADR-094). No rule edited.
- 2026-09-22: design reset — §1.3 ownership re-derived from the design; §1.5 edge rule scoped to D-01/D-02; §1.6 one loop / one team engine; §2 rows for storage credentials, ZeroMQ fail-closed, decision-gated knowledge mutation, remote-session credentials, MCP trust hosts; §2.1 sandbox rule; §4.3 doc set; §5.3 ADR triggers for design moves; §6 new forbidden patterns; §7 scorecard re-run (A:24, 390 tests); §9 ledger moved to `docs/development/wave-ledger.md`.
- 2026-09-14: §1.2 jobs boundary + api/__init__ rule; §4.1 gate scope; §7 host-edit + dormant-router patterns; §8 scorecard + umrs gates; §10 Loop → Wave Engineering; §11 links (structure-hardening-plan-2026-09-14, ADR-052/053/054).
