# ADR-022: Default MCP Servers — Stdio Adapters, Capability Manifests, and HPC Packages

**Status:** Accepted (Phase 0 implemented; deployment + hardening pending)
**Date:** 2026-07-31
**Relates to:** ADR-013 (knowledge portability / compute separation), ADR-015
(egress policy), ADR-023 (hardening contract — implemented-invariant
enforcement + named evolution), docs/development/default-mcp-expansion-plan.md

## Context

The default Hatch! MCP set on umrs-front (platform-tools, rag-search,
knowledge-tools) covers platform-internal queries only. Users need HPC
operations exposed to agents: RIKYU (`RIKEN-RCCS/Rikyu-Agent`) and Fugaku
(`RIKEN-RCCS/fugaku-mcp`). Both vendors ship **stdio** FastMCP servers,
while the Hatch runtime bridge (`r4/src/r4/integrations/hatch_runtime.py`)
and the UMRS MCP registry support only HTTP on loopback. UMRS has no stdio
transport, and every registered server URL is constrained by the SSRF
allowlist (`MCP_URL_ALLOWLIST=127.0.0.1/32`).

An external review additionally called for: three server categories
(platform / hpc / knowledge), per-server capability manifests, tiered
defaults, approval metadata, and delegated HPC authentication instead of
embedded long-lived credentials.

## Decision

### 1. Stdio → HTTP normalization in the Hatch layer

R4 and UMRS remain transport-blind: a vendor stdio server is wrapped by a
shared stdlib JSON-RPC adapter (`mcp-servers/scripts/stdio_adapter.py`) and
served over loopback HTTP by the existing Hatch runtime. Package entries
are manifest-driven via a single shared builder
(`mcp-servers/scripts/adapter_package.py`: manifest load, offline fallback,
deny env parsing) — each vendor package declares only a spawn command, an
optional deny env var, and an offline hint. The adapter spawns the vendor
child lazily, restarts it on crash, enforces a per-call timeout, and writes
a per-call audit JSONL record. Lifecycle and invocation events are emitted
via structured logging (`stdio_adapter` logger): spawn (pid+command),
terminate, respawn, and per-invoke success/failure with duration — so the
observability stack can ingest them alongside the audit JSONL. Packages
never fail to start — when the child is unavailable the package serves the
static `hatch.json` tool list and surfaces a clear spawn error at invoke
(mirrors Rikyu's "must never fail at startup" design rule).

### 2. Three HPC adapter packages as defaults

| Package | Port | Category | Risk | Child |
|---|---|---|---|---|
| `rikyu-hpc` | 8174 | hpc | privileged | `python -m rikyu_mcp.hpc_server` |
| `rikyu-docs` | 8175 | hpc | low | `python -m rikyu_mcp.docs_server` |
| `fugaku` | 8176 | hpc | privileged | `python <FUGAKU_MCP_HOME>/fugaku_mcp.py` |

### 3. Capability manifest rides existing metadata

Each `hatch.json` carries a `capabilities` block —
`category`, `risk`, `transport`, `network.outbound`, `filesystem.readonly`,
`requires`, `approval.dangerous_tools`. The launcher serializes it verbatim
into UMRS `MCPServer.metadata` at registration. **No UMRS schema change**:
`metadata` is freeform JSON (`umrs/mcp/models.py`), and `registered_by` is
left untouched.

### 4. Tool deny at launch, deterministically

- Launcher-side `PACKAGE_TOOL_DENY` filters the tool list POSTed to UMRS
  (`run_command_on_cluster`; Fugaku `run_command`, `check_update`).
- Package-side `PACKAGE_DEFAULT_ENV` sets env defaults (`RIKYU_HOST`,
  `*_TOOL_DENY`) — every value is a non-empty string and host environment
  always wins (`setdefault` only), so deployment `.env` overrides stay
  authoritative.
- `approval.dangerous_tools` is declarative at this phase; server-side
  enforcement and per-user filtering are follow-on UMRS work (gaps
  confirmed: no `mcp.tool_invoked` audit, no caller scoping in `call_tool`).

### 5. Identity model

Pilot on a dedicated, least-privilege **service identity** (RIKYU SSH key,
Fugaku X.509 PEM under `~/.fugaku`), paths via group_vars, secrets never in
the repo. Post-pilot, HPC auth migrates to UMRS-mediated per-user
delegation reusing the existing Slurm `per_user_ssh` plumbing
(`remote_slurm_backend.py`) and the credential provenance ledger
(`0030_credential_provenance.sql`) — no new broker component.

### REST surface boundary (restatement)

This ADR changes nothing in the UMRS REST surface: registration stays
`POST /umrs/mcp/servers` with HTTP+loopback upsert, invocation stays
`POST /umrs/mcp/call` (or `/invoke`). R4 never learns that a default
server is stdio-backed — stdio transport terminates inside the Hatch
adapter; the registry and proxy path are untouched. All new code lives in
`mcp-servers/` packages/scripts plus Ansible deploy tasks; `umrs/`, `r4/`,
and `gateway/` source trees are not modified (import directions preserved:
no new cross-package imports).

### 6. Supply chain

Vendor code installed only from pinned SHAs via the Ansible deps task
(`frontend-default-mcp-deps.yaml`): shared runtime venv with `mcp`,
`hpc-agent-core` (pinned range), Rikyu `git+…@SHA#subdirectory=server`,
and fugaku-mcp cloned at a pinned ref. The `main` placeholders in
group_vars MUST be replaced with SHAs before production rollout.

## Registry evolution (future)

The `capabilities` blocks added by this ADR are groundwork for a richer
registry contract, not the end state. Planned extensions (tracked in
`docs/development/default-mcp-expansion-plan.md`): per-package
`call_timeout` sourced from `hatch.json`; invoke-argument size caps before
the child round-trip; UMRS-side enforcement of
`approval.dangerous_tools` and per-user capability filtering (today the
r4 client is expected to honor the metadata declaratively); and the
UMRS credential-broker authentication model acknowledged in the
`fugaku` package's `capabilities.auth` field.

## Compatibility

- Launch path and behavior are unchanged beyond the added flags; the three
  existing packages (8171–8173) are byte-identical in behavior.
- Feature flags
  (`umrs_front_default_mcp_rikyu_enabled` / `_fugaku_enabled`) gate both the
  dependency provisioning and the `--packages` selection, so a deployment
  can disable the HPC set without touching code.
- Registration remains HTTP+loopback via upsert; downsizing the set is a
  re-run of the launcher with a different package list.

## Upstream references

- `RIKEN-RCCS/Rikyu-Agent` — two stdio servers (`rikyu-hpc`, `rikyu-docs`),
  deps `mcp` + `hpc-agent-core>=0.3,<0.4`, config `~/.hpc-agent/rikyu.json`
  or `RIKYU_HOST`; docs server works offline on its bundled BM25 index.
- `RIKEN-RCCS/fugaku-mcp` — single stdio server, dep `mcp` only, X.509 cert
  via `FUGAKU_CERT`, built-in env-driven policy (`fugaku_policy.py`) used
  as the reference pattern for HPC-side policy.
- Hatch! package contract — `hatch.json` + `server.py` with
  `create_server()` returning `list_tools()` / `invoke()` (see
  `mcp-servers/hatch-registry/README.md`).

## Validation

Chain executed and green (2026-07-31):

1. `pytest mcp-servers/tests/test_persistent_mcp_client.py
   mcp-servers/tests/test_adapter_fallback.py -q` → **12 tests passed**
   (6 client: list/deny filter, invoke content + audit record, denied
   refusal, restart-after-child-death, timeout-kills-child, spawn-failure;
   6 fallback: static-tool serving + spawn-error surfacing per package).
   No live HPC access required; a fake stdio MCP server is used.
2. Offline fallback smoke: all three packages `create_server()` →
   `list_tools()` serve static manifest tools (26/3/14) and `invoke()`
   raises `StdioMcpError` cleanly when the vendor venv is absent.
3. `ruff check` + `ruff format --check` on all new/edited files — clean.
4. Boundary validators (`import_boundary_validator`, `contracts_fence`,
   `stale_name_validator`) — pass.
5. Codacy CLI analysis on every new/edited source file — zero findings.
6. Ansible YAML parse-check — valid.
7. `scripts/ci-lint-local.sh` — passed (untracked-file caveat: explicit
   ruff/pytest/compileall runs above cover the new files).

## Consequences

- Frontend default surface grows from 3 → 6 packages; UMRS registration is
  upsert-based, so re-deploys converge.
- Privileged HPC tools are reachable through `/gw/mcp/invoke` under the
  service identity until per-user delegation lands — mitigated by tool
  deny at registration, audit JSONL per call, and the plan's Phase-2
  hardening items (invocation audit event, per-user filtering, runtime
  quotas, egress rules).
- Follow-on defaults (time, memory, sequential-thinking, git, fs-sandbox,
  arxiv, fetch-gated) use the same manifest + adapter pattern per the
  phased roadmap in `docs/development/default-mcp-expansion-plan.md`.
