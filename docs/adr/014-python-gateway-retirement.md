# ADR-014: Python Gateway Retirement

**Status:** Accepted (EXECUTED: Python gateway removed 2026-09-11 under
user-declared pre-real-use waiver; 90-day window waived like the Ink/research
legs below; NGINX+sidecar is the only edge)
**Date:** 2026-07-11
**Relates to:** ADR-003 (three-component gateway), mutation-safety invariant,
development.instructions.md (Production Gateway edge = NGINX + Auth Sidecar)

**Rollout-status (2026-09-07, WP-13 round 1):**

- **Phase 1 — landed.** `gateway/gateway/main.py` emits
  `GATEWAY_DEPRECATED_NOTICE` as a `DeprecationWarning` + structlog
  `gateway.deprecated` warning at lifespan startup, and a
  `DeprecationHeaderMiddleware` stamps every response with
  `x-deprecated: DEPRECATED: Python Gateway — use NGINX + Auth Sidecar edge
  instead (ADR-014)`. Per-route requests log at DEBUG (`gateway.deprecated_route`)
  instead of the ADR's literal warn-per-request — deliberate: per-request WARN is
  log flood; the header is the per-request signal. Pinned by
  `gateway/tests/test_adr014_deprecation_banner.py` (4).
- **Phase 4 — open.** Code removal as scheduled by Phase 3.

**Rollout-status (2026-09-10, Phase 2 → tracker rows):**

- **Phase 2 — PARTIAL → done for the live-facing surfaces.** Findings from
  the sweep: e2e compose was already NGINX-only (no Python-gateway option
  anywhere — the ADR's Phase-2 example was already cleaned);
  `docs/architecture/boundaries/GATEWAY_SIDECAR_BOUNDARY.md` was the only
  live-facing doc still presenting the Python Gateway as a *current* layer
  — a dated retirement banner now heads it (removal date + UMRS-owned
  re-reading). Dated audit/admin papers keep line references as-is
  (historical records, not rewritten).
- **Tracker rows (G-P12-3 requirement — each legacy surface gets its own
  row before any retirement claim):**

  | Surface | Marked since | Eligible for removal |
  |---|---|---|
  | **~~Python Gateway~~ (`gateway/gateway/`) — REMOVED 2026-09-11** | Phase-1 runtime warnings 2026-09-07; window date 2026-12-06 | **REMOVED (window waived by user decision: pre-real-use; all four phases exit-verified)** |
  | **~~Ink tui-js~~ (`r4/companion-tui-js/`) — REMOVED 2026-09-11** | v3-parity certified 2026-09-01; deletion soak announced 2026-09-15 | **REMOVED (soak waived by user decision: pre-real-use; parity already code-verified)** |
  | **sat_kg compat surface** (`umrs.knowledge_graph_api`, `sat_kg_*` tables) | ADR-026 read-only compat 2026-08-15 | compat window **≥ 2026-11-13** (90d), owner decides + migration note for table retirement |
  | **~~`r4 research` CLI~~ (`r4/src/r4/_cli/research.py`) — REMOVED 2026-09-11** | deprecation warning since WP-07 fold 2026-09-07 | **REMOVED (same pre-real-use waiver; TUI `/research` loop surface is untouched)** |

  Eligible ≠ removed: every removal waits its own dated commit + gate
  evidence (green CI minus the surface, references grep-cleaned,
  ADR rollout block updated in the same wave).
- **Phase 3 + 4 — untouched this wave** (dates not reached).

**Rollout-status (2026-09-11, Phases 3+4 EXECUTED — user-decided window waiver):**

- The user declared the platform **pre-real-use with no production data**,
  the same waiver family already applied to Ink/research-CLI/WP-14/sat_kg on
  this date. The 2026-12-06 wait is waived and the removal executes now.
- **Removed:** `gateway/gateway/` (50 files), `gateway/Dockerfile`,
  `gateway/compose/gateway.yml`, `gateway/pyproject.toml`,
  `gateway/requirements.txt`; 25 Python-bound gateway test files (only the
  NGINX/sidecar contracts remain: `test_nginx_config.py`,
  `test_nginx_audit_runtime.py`, `test_contracts.py`, plus
  `sidecar/test_sidecar.py`); `tests/test_mcp_apps_e2e.py` (bound to the
  Python-gw `/gw/mcp/apps` surface, which has no NGINX analogue);
  `deploy/ansible/roles/login_node/tasks/gateway.yaml` + `gateway.env.j2`
  (generic profile's dead Dockerfile build);
  `deploy/ansible/roles/login_node/tasks/python-gateway-legacy.yaml`.
- **Rewired:** `hpc.yaml` no longer forks by `rccs_gateway_stack`
  (NGINX+sidecar include is unconditional when gateway deploy is on);
  inventories (`rccs_cloud`, `bdrt`, `octopus`) declare `nginx_sidecar` the
  only stack; generic `group_vars/login_nodes.yaml` drops
  `gateway_stack: python_legacy` (login_node now reports "no managed edge
  gateway" for the generic profile); `gateway.rccs.env.j2` loses the legacy
  env opt-in; `tests/test_gateway_topology.py` asserts absence of the legacy
  files instead of their presence.
- **CI:** `ci.yaml` + `gateway-ci.yaml` repointed — lint scope
  `gateway/gateway` → `gateway/sidecar`, tests run `tests/
  sidecar/test_sidecar.py` with `sidecar/requirements.txt` (no editable
  install left), Bandit scans `sidecar/`, Docker builds switch to
  `gateway/nginx` + `gateway/sidecar` images
  (`ai-platform-gateway-nginx` / `ai-platform-gateway-sidecar`,
  `rivault-gateway-nginx`).
- **Observability:** gateway-era `rivault_*` series had no surviving emitter;
  the dependent alert rules (GatewayHighErrorRate/HighLatency,
  EndpointProvisioningStuck, NoActiveEndpoints, LLMHighLatency,
  LLMFirstTokenSlow) were pruned in both `observability/prometheus/alerts.yaml`
  and `umrs/deploy/umrs-front/observability/prometheus/alerts.yaml`
  (`check_metrics_coverage.py` would hard-fail on silent-dead rules). The
  ratified P11 TTFT SLO is now unmeasured — explicitly owned by G-P11-2 /
  G-P1-4 until the sidecar or UMRS route instruments TTFT.
- **Traceability:** rows citing deleted files rewritten to surviving
  surfaces (P1-C5 → umrs metrics/timing; P5-C2 → `r4_mcp.egress_guard`
  deny-by-default authority; P11-C1 → umrs metrics module; P12-C4 →
  implemented with deployment evidence); G-P12-3 → **Closed**.
- **Gate:** `scripts/ci-lint-local.sh` full scoped run ALL CHECKS PASSED
  after the wave (traceability OK, metrics coverage missing=0, doc sanity
  0 findings).

**Rollout-status (2026-09-11, Ink surface removal — user-decided soak waiver):**

- **REMOVED the full Ink TUI surface** under the maintainer's same
  pre-real-use waiver as WP-14 ("no important data yet"): with no real
  users to migrate, the extra soak days protect nothing; parity was already
  certified per code (r4-tui-v3 bun suite + wave A–D landings).
- **Removed:** `r4/companion-tui-js/` (84 files, the Ink+React TUI bundle and
  its authored sources), `r4/hatch_build.py` (its packaging hook),
  `[tool.hatch.build.hooks.custom]` block in `r4/pyproject.toml`, the
  Makefile `build-companion`/`install-companion` targets and Node.js
  “Companion TUI” scaffolding the docs used to present.
- **Left, re-termed:** `--legacy-tui` CLI flags on `r4 companion` /
  `r4 chat --companion` now select the **Rich REPL fallback** rather than
  the Ink TUI (no Ink surface exists anymore); the launcher module
  (`companion/ui/__init__.py`) has removal-error stubs at the old entry
  names (`_launch_js_ui`, `_companion_js_cmd`) that raise with the removal
  date and migration pointer, and **its tests pin them**
  (`r4/tests/test_companion/test_ui_launch.py`,
  `r4/tests/test_cli_tui.py::TestCutoverDecision` rewritten to v3-only
  semantics; `R4_COMPANION_TUI=ink` is now inert).
- **Gate:** full scoped local gate GREEN on the wave (boundary, complexity,
  lint, ruff/c901/lizard; one pre-existing env-host suite red).
- Phase 3/4 (Python Gateway removal window) untouched.

  **Also removed in the same waiver wave (2026-09-11): the deprecated
  `r4 research` CLI** (711-line `_cli/research.py`). Its only remaining
  role was the deprecated scripted entry; the current loop surface —
  TUI `/research` (commands fuse runtime store + `test_tui_command_sweep`'s
  30+ reliability-charged tests — remains fully live. The argparse
  dispatch tuple dropped the legacy module; no code imports remained.
  (Pre-release: `r4 research` now returns the unknown-subcommand error and
  points at TUI/loop starts for the next-gen spine.)

## Context

The platform's production edge is **NGINX + Auth Sidecar** (ADR-003). The Python
Gateway (formerly `gateway/gateway/`) was the original edge component during
development, was kept as legacy/dev-compatibility until 2026-09-11, and was
then **removed** (rollout blocks above).

### Current State (past tense as of 2026-09-11 — the package is gone)

**Production path (deployed, unchanged):**
```
Client → NGINX → Auth Sidecar → UMRS/LiteLLM/RAG/MCP
```

**Python Gateway status:**
- Service name in Docker Compose: `gateway` → was **removed** from
  `docker-compose.e2e.yaml` before this ADR was written (no `gateway:`
  service definition existed)
- E2E tests route through `gateway-nginx` (NGINX edge) and
  `gateway-sidecar` (Auth Sidecar), never through the Python gateway
- Demo scripts use `GATEWAY_URL` pointing to NGINX/Sidecar
- The Python Gateway code that remained under `gateway/gateway/` **existed
  only** for legacy dev convenience (`uvicorn gateway/main.py`), backward
  compatibility with older client configurations, and a re-architecture that
  was never planned; it was deleted 2026-09-11 under the user-declared
  pre-real-use waiver. `gateway/` now holds `nginx/`, `sidecar/`, `k8s/`,
  `tests/` and `README.md` only.

**Evidence:**
- `docker-compose.e2e.yaml` defines `gateway-nginx` and `gateway-sidecar` services
  but no `gateway:` service — the Python Gateway was removed
- `tests/run_e2e_tests.py` uses `e2e-gateway-nginx` as the endpoint
- Demo scripts reference `http://gateway:8080` which resolves to `gateway-nginx`
  (NGINX), not Python Gateway
- `.github/instructions/development.instructions.md`:
  "Production Gateway edge is NGINX + Auth Sidecar; the Python Gateway is
   legacy/development compatibility only"

### Migration Steps

#### Phase 1 — Deprecation Notice
1. Add startup warning banner to Python Gateway:
   ```python
   import warnings
   warnings.warn(
       "Python Gateway is deprecated. Use NGINX + Auth Sidecar edge instead.",
       DeprecationWarning,
       stacklevel=2,
   )
   ```
2. Add `DEPRECATED: Python Gateway — use NGINX edge instead.` to response
   headers
3. Log deprecation on every request

#### Phase 2 — Documentation
1. Update `docker-compose.e2e.yaml` comments documenting the deprecation
2. Remove Python Gateway from any documentation that lists it as an option
3. Add ADR-014 reference to `docs/adr/README.md`

#### Phase 3 — Removal Criteria
The Python Gateway can be removed entirely when:
1. No client code references `http://gateway:8080` (Python Gateway port)
2. All E2E tests pass without Python Gateway service
3. No local development scripts require Python Gateway
4. Migration window has elapsed (minimum 90 days from deprecation notice)

#### Phase 4 — Code Removal
1. Remove `gateway/gateway/` directory
2. Remove `gateway/` from workspace root
3. Update `pyproject.toml` `[project.scripts]` if Python Gateway was registered

### Rollback Plan

The Python Gateway can be re-deployed from git history if a regression is
discovered that requires it. The code is not deleted — only the deployment
configuration changes.

### Non-Goals

- This ADR does not affect UMRS, R4 SDK/CLI, or any component downstream of
  the NGINX edge
- This ADR does not modify the NGINX + Auth Sidecar architecture
- This ADR does not remove the `gateway/` namespace — only the Python Gateway
  service and its code

## Decision

Retire the Python Gateway in favor of the NGINX + Auth Sidecar production edge.
Follow the phased approach above with deprecation notice first, then removal
after validation and a 90-day migration window.
