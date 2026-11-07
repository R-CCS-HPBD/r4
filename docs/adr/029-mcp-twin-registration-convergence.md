# ADR-029: MCP twin registration convergence — hatch-* canonical, default-* deprecated alias

**Status:** Accepted
- **Date:** 2026-08-17
- **Related:** Stability wave S1/S8 in wave-companion-any-client-2026-08-16.md, contract test below.

## Context

The platform MCP surface registers the same 3 logical tool servers
(`platform`, `rag`, `knowledge`) twice: once as `default-<package>`
(frontend bootstrap path `frontend_default_mcp.py`, metadata
`source: "default-set"`) and once as `hatch-<package>` (hatch registry
install path). Verified against current code and pinned as premise by the
S8 contract suite:

| Fact | Evidence |
|---|---|
| Both twins are generated from the **same** package definitions in `mcp-servers/hatch-registry/packages/*`. The duplication is a registration-path artifact, not HA — no healthcheck, failover, or precedence semantics exist between twin entries. | [frontend_default_mcp.py](../../mcp-servers/scripts/frontend_default_mcp.py), packages tree |
| Tool payloads of the twins are identical by construction; the S8 twin-identity premise test (`mcp-servers/tests/test_mcp_contract_pinning.py`) fails if they ever diverge. | S8 suite, 2026-08-17 |
| The 6-surface-pairs listing confuses consumers (two names for one capability) and doubles any tool-list surface a client must de-duplicate. | S1 live inventory |
| The hatch registry is the package source of truth; `default-*` is a bootstrap convenience that re-registers those same packages at frontend start. | `hatch-registry/README.md`, `_register()` |

## Decision

1. **`hatch-*` is the canonical MCP server naming.** New consumers and
   documentation address the `hatch-` names; the hatch registry is the
   sole source of truth for package definitions, contract versions, and
   capabilities.

2. **`default-*` entries are a deprecated compatibility alias**, retained
   only until the next MCP re-registration cycle, and retired no later
   than **2026-09-30** (aligned with the S11 flag-expiry cadence).
   Retirement = the bootstrap path skips packages already registered as
   `hatch-*` (identity check), then the `default-*` rows are deleted
   from the UMRS MCP registry in one recorded operation.

3. **Identity is the contract while both exist.** Until removal, the S8
   twin-identity premise is load-bearing: any divergence between a
   `default-*` registration and its package manifest fails CI.
   Post-removal the premise is deleted in the same commit.

4. **No third naming family.** Any future registration path must reuse
   `hatch-*` names (upsert), never mint a new prefix. This rule is
   enforced by review, then by an S12-style premise if a fourth path
   ever appears.

## Alternatives considered

- **HA pairing (formalize both):** rejected — the twins carry literally
  identical tools from one package source; there is nothing to fail over
  between. Pairing semantics would be documentation theater.
- **Immediate collapse:** rejected — consumers addressed by `default-*`
  names (health checks, dashboards, demo flows) break silently. The
  alias window exists so the identity premise protects the transition.
- **default-* canonical instead:** rejected — the hatch registry carries
  the packaging/versioning/capabilities story; `default-*` is a thin
  auto-registration shim over it.

## Consequences

- The MCP surface trend is one tool set per logical server, one name.
- The S8 schema-pinning suite keys off package manifests only — it is
  unaffected by the `default-*` retirement.
- If re-registration is delayed past 2026-09-30, this ADR gets a visible
  amendment with the new date (never silent drift).

**Contract test:** `mcp-servers/tests/test_mcp_contract_pinning.py`
(`test_default_registration_metadata_matches_manifest` — twin identity)
plus `tests/test_stability_governance_s12.py` (ADR linkage).
