# ADR-017: A2L ↔ Knowledge Graph Identity Bridge

**Status:** Accepted

## Context

A2L (Artifact & Agent Layer) is a separate service providing scientific memory
with content-addressed artifacts, lineage graphs, and type system. UMRS owns the
knowledge graph with artifacts, IRs, nodes/edges, embeddings, and verification.

Both systems use different identity schemes:
- **A2L**: content-hash based (`sha256:...`) with deterministic IDs
- **UMRS**: UUID-based (`uuid4`) with `artifact_id` in knowledge_artifacts table

When a workflow runs through UMRS and produces artifacts that A2L also tracks,
there is no canonical mapping between the two identity spaces. This breaks
lineage queries, citation chains, and `.kb` bundle exports that span both systems.

## Decision

Establish a bidirectional identity bridge using a new `a2l_artifact_refs` table
in the UMRS knowledge store schema.

### Design

1. **Table**: `a2l_artifact_refs (artifact_id UUID, a2l_hash TEXT, a2l_id TEXT,
   synced_at TIMESTAMP, source TEXT)`
2. **Content-addressed lookup**: On knowledge ingest, compute sha256 of artifact
   content; if it matches an existing `a2l_hash`, record the `a2l_id`
3. **Gateway HTTP**: Expose `POST /gw/a2l/resolve` and `GET /gw/a2l/artifacts`
   for cross-reference resolution. No Python imports of `a2l` in `umrs`
4. **Best-effort**: If A2L is unavailable, knowledge jobs continue without
   lineage enrichment. Recorded as `a2l: unreachable` in `degraded[]`
5. **KB bundle**: `.kb` v1.x manifest includes an `a2l_refs` section mapping
   UMRS artifact_ids → A2L identifiers

### Lineage Query Surface

```
GET /gw/projects/{id}/knowledge/lineage?artifact_id=UUID
→ Returns: { "artifact": {...}, "parents": [{a2l_id, umrs_id}], "children": [...] }
```

### .kb Export Inclusion

```json
{
  "manifest": {"version": "1.x", "schema": "r4-kb"},
  "a2l_refs": {
    "artifact-uuid-1": {"a2l_id": "a2l-abc123", "a2l_hash": "sha256:..."},
    "artifact-uuid-2": {"a2l_id": "a2l-def456", "a2l_hash": "sha256:..."}
  },
  ...
}
```

## Consequences

### Positive
- Unified lineage across A2L and UMRS knowledge graph
- Citations reference both systems: `[{kind/short}] title (A2L: a2l-abc123)`
- `.kb` exports are portable across systems
- Graceful degradation when A2L is unreachable

### Negative
- New migration adds one table (minimal schema footprint)
- HTTP calls to A2L add latency; must be best-effort with timeout
- Gateway route adds surface; must follow `deployment-operations.instructions.md`
- Requires HTTP access to A2L (no `import a2l` in UMRS — enforced by boundary validator)

### Neutral
- Migration is additive; backward-compatible with existing `.kb` v1.x
- Lineage queries are optional; knowledge graph operations are independent

## References
- [INV-13 Knowledge portability](../instructions/development.instructions.md)
- [.kb bundle export](docs/adr/013-knowledge-portability-and-compute-separation.md)
- [A2L README](../../a2l/README.md)
- TASK-050 → TASK-051 → TASK-052 dependency chain
