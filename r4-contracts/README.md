# r4-contracts

Shared **schemas, DTOs, and lightweight validators** used by both:

- `r4` — the client SDK / CLI
- `umrs` — the durable platform (orchestration, registries, etc.)

This package exists to eliminate direct `r4 → umrs` imports for things that are
pure data shapes or pre-flight validators. It has **zero runtime dependencies
beyond the Python standard library**, so the small `r4` client stays small and
UMRS does not need to ship as a client-side dependency.

## Scope

In scope:

- Recipe definition schema and validation (`r4_contracts.recipes`)
- Cron expression validation and matching (`r4_contracts.scheduler`)
- Skill manifest schema (`r4_contracts.skills`)
- Pre-flight security scanners (`r4_contracts.security`)
- MCP DTO models (`r4_contracts.mcp`)
- API model mixin (`r4_contracts.dto`)

Out of scope (stays in UMRS):

- Durable job tracking and state machines
- Workflow execution / orchestration
- Endpoint provisioning
- RAG indexing / retrieval
- Connector implementations
- Schedule store / cron daemon

## Compatibility

UMRS keeps its own copies of these schemas as the implementation owner. This
package mirrors the lightweight DTO portion as the client-side contract.
Implementations on the server side may evolve faster — clients pin to
`r4-contracts` for stability.
