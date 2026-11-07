# ADR-030: Companion-only tools pinned Companion-scoped — no MCP promotion

**Status:** Accepted
- **Date:** 2026-08-17
- **Related:** GAP-COMP-1, S3 correlation, and the S4 companion-route premise in
  wave-companion-any-client-2026-08-16.md, ADR-029, contract test below.

## Context

The tool families `lab_catalog_*`, `research_loop_*`, `research_session_*`,
and `project_research_*` are de-facto public contracts used by Companion (note 2026-09-07: the `project_research_*` family retired with WP-07 — one research spine on ADR-036 loops; `research_loop_*` stays Companion-scoped)
clients but are invisible to MCP contract tests. The S3 correlation asked
whether they should be promoted to MCP names (and thus MCP contract
coverage) or formally pinned Companion-scoped.

| Fact | Evidence |
|---|---|
| These tools are **stateful and rail-shaped**: loops need approval gates, session authority (ADR-025), and per-turn budgets — semantics the MCP tool model does not carry. The 3 pinned MCP servers (ADR-029 + S8) are stateless query surfaces by contrast. | `r4/src/r4/companion/*` tool modules; S8 snapshot |
| S3 found **zero boundary violations**: no traffic to these tools outside the agent rails; no consumer asked for them via MCP. | S2/S3 wave log, 2026-08-17 |
| The remote-client gap was never "exposed via MCP" — it was "no gateway-served companion endpoint" (GAP-COMP-1), which the m-series venue (`/gw/companion/*`, m1/m5/m6) now addresses with ownership-pinned, approval-shaped routes. | companion wave, m1–m6 |
| Promoting them to MCP would put long-running, mutation-capable loops under a contract designed for read-mostly stateless tools — a category error that weakens both contracts. | S8 contract semantics |

## Decision

1. **Companion-only tools are formally pinned Companion-scoped.** They
   are never exposed as MCP tool names, never registered in hatch
   packages, and never promised MCP contract stability.

2. **Their contract surface is the companion venue**, pinned by the S4
   premise suites: the exact `/umrs/companion` route shape
   (`test_surface_characterization.py::test_companion_contract_served_exactly`)
   and the NGINX `/gw/companion/*` fold-in premise. Changes to that
   surface follow the same one-contract-test discipline as v1 changes.

3. **MCP absence is itself a tested premise.** A contract test asserts
   no hatch package manifest exposes a companion-only tool prefix —
   promotion by accident fails CI.

4. **Future reconsideration is cheap:** if a durable, stateless subset
   ever emerges (e.g. a read-only `research_loop_status`), it may be
   added to `mcp-servers/hatch-registry/packages/knowledge-tools` under
   the S8 contract rules — new tool, new snapshot entry, version bump.
   What stays forbidden is lifting the rail-shaped families wholesale.

## Alternatives considered

- **Promote to MCP names:** rejected — couples mutation-capable,
  approval-gated loops to a stateless-query contract; MCP contract
  tests would misrepresent what these tools are.
- **Leave unpinned:** rejected — the S1 inventory flagged invisibility
  to contract tests; sans decision the surface drifts unnoticed.

## Consequences

- MCP consumers get only stateless query tools; anything with loops,
  approvals, or sessions lives behind the companion venue with
  ownership pinning.
- Remote clients reach these capabilities through `/gw/companion/*`
  (m-series), completing the GAP-COMP-1 story without an MCP side-door.

**Contract tests:**
`mcp-servers/tests/test_mcp_contract_pinning.py`
(`test_companion_only_tools_absent_from_mcp_packages`),
`umrs/tests/test_surface_characterization.py`
(companion-route premise), and
`tests/test_stability_governance_s12.py` (ADR linkage).
