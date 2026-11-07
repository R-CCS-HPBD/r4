# ADR-023: Default MCP Hardening Contract (Trust Boundary + Registry Evolution)

**Status:** Accepted (contract layer landed 2026-07-31)
**Date:** 2026-07-31
**Relates to:** ADR-022 (base decision), ADR-015 (egress policy),
docs/development/default-mcp-expansion-plan.md (Phase 2)

## Context

ADR-022 shipped the integration: stdio-adapter bridge, three HPC packages,
capability manifests, deny-at-registration. Hardening details were
documented in the expansion plan but not yet **contracted** — the trust
boundary for invoke arguments, the fail-mode semantics of the adapter, the
per-package observability model, and the registry's evolution path were
spread across code comments, docstrings, and the plan doc. This ADR locks
the currently-true invariants, and reserves the named extension points.

## Hard invariants (contracted, implemented)

| # | Invariant | Where | Enforcement |
|---|---|---|---|
| C1 | R4 ⇄ adapter traffic is **loopback-only** (127.0.0.1) | launcher + bridge | `frontend_default_mcp.py` binds `--host 127.0.0.1`; UMRS SSRF allowlist `127.0.0.1/32` |
| C2 | Invoke arguments pass through to the vendor server **unvalidated** today | `stdio_adapter.py` | Documented trust boundary; server-side policy stays with vendor (`fugaku_policy.py`); UMRS-side enforcement reserved (E3) |
| C3 | Privileged tools are flagged **declaratively** | `hatch.json` `capabilities.approval.dangerous_tools` → UMRS `metadata` | Deny-at-registration in launcher (C2 of ADR-022); agent-client expected to honor |
| C4 | **Fail-closed** on denied tools; **fail-clear** on offline children | `stdio_adapter.py` + `adapter_package.py` | Deny refused pre-call; spawn failure → `StdioMcpError`; static manifest fallback keeps launcher healthy |
| C5 | **Never fails at create time** | `adapter_package.py` | Lazy spawn in `StdioMcpAdapter`; manifest fallback in `ManifestAdapterServer` |
| C6 | Spawn discipline: one long-lived child per adapter, **terminate + respawn**, never nurse | `stdio_adapter.py` | `_kill()` → `_spawn()`; timeout kills child; `_ensure()` respawns on poll-failure |
| C7 | Per-call audit evidence, append-only | `stdout` stderr log + audit JSONL under `~/.local/state/r4-default-mcp/` | `_audit()` writes (ts, server, tool, args_sha256, duration_ms, ok); child stderr retained; structured `stdio_adapter` logger emits spawn/terminate/respawn/invoke events for Loki |
| C8 | R4 never learns the transport | Hatch runtime bridge | stdio client lives in `mcp-servers/`; registry is HTTP-only |
| C9 | Deny lists are deterministic and **host env wins** | launcher `PACKAGE_DEFAULT_ENV` | `setdefault`-only injection |
| C10 | Tool expectations in tests derive from **`hatch.json`**, never hardcoded counts | `mcp-servers/tests/` | assertion against manifest-declared tools |

## Evolution reservations (named, not implemented)

| # | Reserved extension | Motivation | Landing zone |
|---|---|---|---|
| E1 | Per-package `call_timeout` via `hatch.json` | Doc lookups vs job-submission calls have different tolerances | `adapter_harness` builder (TODO(hardening) flag in `stdio_adapter.py`) |
| E2 | Invoke-argument **size cap** pre-round-trip | Blast-radius limit on what a malformed invocation can inject into a vendor server | `stdio_adapter.py #_send` boundary |
| E3 | UMRS-side enforcement of `approval.dangerous_tools` + per-user capability filtering + `mcp.tool_invoked` audit event | C2 today is declarative; UMRS Phase-2 moves to enforcement | `umrs/src/umrs/api/mcp.py` (`call_tool`) |
| E4 | Credential-broker auth model (`umrs-credential-broker`) | Eliminates long-lived credentials from MCP runtimes; acknowledged in `fugaku` `capabilities.auth` | Reuses Slurm `per_user_ssh` plumbing + `0030_credential_provenance.sql` |
| E5 | Runtime quotas (systemd scope) + per-runtime egress rules | Host-level containment moving beyond SSRF allowlist | Ansible `frontend-default-mcp-deps.yaml` + firewalld |
| E6 | Tier-1/3 defaults (time, memory, sequential-thinking, git-rw, fs-sandbox, arxiv, fetch-gated) | Same manifest + adapter pattern extends the registry without R4 changes | `hatch-registry/packages/` + `registry.json` |

## Contracts published in this round

- **README** (`mcp-servers/hatch-registry/README.md`): "JSON-RPC transport
  mechanics" framing; contract behaviors enumerated; dedicated trust-boundary
  block (C1, C2, C3, E1, E2 pointers).
- **Package docstrings** (`stdio_adapter.py`, `adapter_package.py`): ground
  rules codified — spawn discipline, audit/stderr retention, failed-tool
  semantics, offline-child fail-clear behavior.
- **Manifest** (`fugaku` `hatch.json`): `capabilities.auth` acknowledges
  the credential-broker future (C8 of ADR-022, E4 here).
- **Validation** (`test_persistent_mcp_client.py`,
  `test_adapter_fallback.py`): 6 test behaviors each, expectations from
  `hatch.json` (C10), evidence log `/tmp/ci/logs/mcp-adapter-suite-20260731.log`.

## Consequences

- Contract is now explicit on what is **implemented** (C1–C10, validated)
  vs **reserved** (E1–E6, tracked) — no silent drift between plan and code.
- Phase 2+ work (UMRS-side enforcement, credential broker, quotas) is no
  longer "details in the plan" — it is named evolution (E3, E4, E5) with
  landing zones.
- All extension points reuse existing slots (`metadata`, `hatch.json`
  fields, adapter flags) — zero schema or protocol changes needed.
