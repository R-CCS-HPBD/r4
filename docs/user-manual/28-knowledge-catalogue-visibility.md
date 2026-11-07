# 28. Knowledge & Catalogue — Planes, Visibility, and Operation Modes

> Deep-dive chapter for the knowledge and catalogue experience as of
> **WP-22 (2026-09-13)**. Reading order for best context:
> [27. Knowledge Management](27-knowledge-management.md),
> [7. RAG](07-rag.md),
> [6. Endpoints](06-endpoints.md).
> Backbone: canonical graph, RAG umbrella, asset grants, ontology as a
> typed contract, **visibility tiering + session-history build**.

A knowledge asset in R4 spans several data planes. This chapter
describes every plane a normal user can see, who can see it, how writes
move through the gates, and which operation mode the r4 client and the
UMRS control plane are in at that moment.

---

## 1. The planes (where anything knowledge-carrying actually lives)

| # | Plane | Physical home | What it holds | Read gate |
|---|-------|-------|------|-----|
| 1 | Project graph + corpus | PostgreSQL `knowledge_nodes` / `knowledge_edges` (umrs_db) | ingested documents, findings, annotation nodes/edges per knowledge graph | project owner + system tier |
| 2 | Catalog | PostgreSQL `catalog_objects` (migration 0054; `visibility` column from 0093, 2026-09-13) | metadata rows over S3 objects (states, modalities, scope ACL) | project owner + system tier |
| 2b | Satellite catalog plane | PostgreSQL `sat_knowledge_objects` (`visual_visibility` from 0092) | satellite-side knowledge objects mirrored for the graph planes | project owner + system tier |
| 3 | Central corpus lane | same `knowledge_nodes` queried by the K3 facade | corpus subgraphs built by durable ingest | family rule (below) |
| 4 | Embeddings lane | pgvector `knowledge_node_embeddings` + semantic index models | per-node vectors for the semantic lane | same as owning plane |
| 5 | Artifact bytes | S3/MinIO `umrs-files` bucket (`staged/`, `work/`) | originals; presigned URL access | project-scoped URLs only |
| 6 | Endpoint corpus | vectors behind each mini-RiVault's `rag_service` | endpoint-scoped index, project/user lifetime | per-endpoint, **read-only while dual-corpus dwell is live** |
| 7 | Registry: graphs | `knowledge_graphs` (migration 0069; `access` from 0116) | graph ids, names, owner/project, kind/status, ontology binding, access list | scope + access list |
| 8 | Registry: ontologies | `knowledge_ontologies` (migration 0091) | versioned vocab contracts with retire-tombstones | any selected graph's binder |
| 9 | Registry: grants | `asset_grants` (migration 0083) | per-asset, per-grantee version-pinned read grants | deny-by-default, tombstoned |
| 10 | Session | `~/.config/r4/companion/sessions.db` (SQLite; override with `R4_COMPANION_DB`) | chat history, messages, `/acquisition` mode, venue | the owning user's machine only |

**Nothing lives on the GPU node.** vLLM + LiteLLM carry model weights
and serve requests; all knowledge rows, graph edges, catalog rows, and
bundles sit in UMRS (PostgreSQL) and S3. Inference consumes read
planes; it owns none of them.

---

## 2. Visibility families (authoritative)

Every catalog-grade row carries a **`visibility` column** — migration 0092 for `knowledge_nodes` and `sat_knowledge_objects`, migration 0093 (2026-09-13) for `catalog_objects`:

| tier | meaning | examples |
|-------------|---------|-----------------|
| `project` | owned by one project | everything ingest/upload produces by default |
| `system` | marked platform-wide | operator-approved platform handbooks, curated notes |
| `ambient` | read-gated everywhere | seeded demo corpora, rows of discontinued projects |

The **family rule** every read lane applies:

```
visible  := (row.project_id == caller_project AND visibility in ('project','system'))
         OR (visibility == 'system')
excluded := visibility == 'ambient'  (always)
```

**Which lanes are gated today (2026-09-13):** the rag lane, the
knowledge-graph lane, the embeddings (semantic) lane, and catalog search —
all through one tier predicate in `umrs.knowledge.access_scope`. **Not yet
gated:** graph traversal (`walk`/closure reads) and bundle export; a
traversal or export that starts from a row you can see may still reach
rows the family rule would hide. Treat that as an open item, not a
guarantee. `DEFAULT 'project'` means no existing user's content changed semantics

**Sharing beyond the tier ladder (2026-10-03):** every shareable
family — knowledge graphs, scientific catalogs, RAG corpora, workflows,
personal buckets, endpoints — carries one row-level access list
(`user:<id> | project:<id> | circle:<project>/<id> | system`; several entries of each
kind; every group membership of a caller counts; `system` is admin-granted).
The tiers above remain the default: a row *without* an access list behaves
exactly as before. The list governs *use* (reads and runs); lifecycle
writes stay with the owner/owning project. Manage it per family with
`r4 knowledge graph-access`, `r4 catalog access`, `r4 rag corpus-access`,
`r4 workflow access`, `r4 bucket access` (§13).
at migration time, and **no ambient row is ever deleted** — it is
stopped at read time, reversible by unmarking unless re-marked.

Non-goals: ambient hiding touches only CENTRAL planes; an endpoint
corpus is its owner's venue (it dies with the endpoint or stays, never
ambient).

### Ambient/system marking (operator)

```bash
# audit what's out there
python umrs/tools/mark_ambient_corpus.py --dsn postgresql://... --audit

# hide a seeded corpus
python umrs/tools/mark_ambient_corpus.py --dsn postgresql://... \
    --mark-ambient seed-demo-corpus

# make a corpus visible to everyone (platform knowledge)
python umrs/tools/mark_ambient_corpus.py --dsn postgresql://... \
    --mark-system platform-handbook

# undo: return a corpus to the project tier (2026-09-13)
python umrs/tools/mark_ambient_corpus.py --dsn postgresql://... \
    --mark-project seed-demo-corpus
```

Nothing marks anything without an **explicit project list**. Marks cover
the graph, satellite and catalog planes (catalog plane since 2026-09-13).

### Imported knowledge counts as a family

Rows materialized by bundle import land in the caller's project with
tier `project` **plus** their import provenance; they are visible per
the family rule AND have the import link provable in registry rows
(companion `knowledge_bundle.py` / CLI `r4 knowledge import`). Sharing
beyond the family rule works through the **access lists** (the
2026-10-03 update above): a row's `access` names `user:/project:/circle:/
system` readers, several entries of each kind, circles resolving through
active membership (circles). Asset **grants** remain the **model**
registry's tombstoned, version-pinned read-share mechanism — surfaces
show `[shared]` chips on grant-derived model rows; grant rows creatable
for graphs/workflows/endpoints are compatibility creases that never
authorized reads (the access lists do).

---

## 3. Connectivity (where the same readers run)

All four surfaces read through the gated lanes named in §2 (rag, graph,
embeddings, catalog search).

| Surface | Access | Typical actions |
|---------|--------|-----------------|
| **CLI (`r4`)** | project + system + owner imports | `r4 knowledge query`/`search`, `graphs`, `upload`, `export`/`import`, `ingest`, `stats` |
| **Chat (companion)** | same planes, plus session | knowledge tools incl. `knowledge_build_from_context`; every call gated by `/acquisition` mode |
| **TUI** | same planes | `/knowledge`, `/acquisition`, slash hints for graph picks |
| **Web console** | same planes | Models view (grant badges), Graph view |
| **API** | same planes | `/umrs/knowledge/*`, `/umrs/knowledge/graphs`, `/umrs/catalog/stats` |

**Sessions / chat history stay local**: SQLite on the user's machine,
not the corpus. Context is never read server-side until a knowledge flow
materializes a part of it (see §4.3).

---

## 4. Write flows (how rows get born)

All writes pass two gates in order:

1. **Acquisition write gate** (`/acquisition open|off|extend <graph>`)
   — session-scoped; refuses when `disabled`, constricts to pinned
   graphs in `extend` mode, lets `open` write anywhere.
2. **Approval / proposal chain** — approvals are durable (companion
   inbox in Postgres); each write that matters is inspected and
   individually approved.

### 4.1 Live feed ("enable, then chat")

1. At session start: `/acquisition open` (or extend of a chosen graph).
2. Chat as usual — ``r4 knowledge_ingest`` runs for named
   files/URLs/search terms; each queues a durable
   `KNOWLEDGE_INGEST` job on UMRS (Slurm/K8s/compose).
3. Jobs extract knowledge and write `knowledge_nodes/edges`,
   embeddings, catalog rows with tier `project` owned by the current
   project. Physics: byte staging in S3 → extraction job → PG rows.

Failure visibility is loud, typed, and named (`job list`,
`/health`, the companion tool result) — never a silent zero.

### 4.2 Loop flow ("iterate research")

`research_loop_start` spawns a durable research loop that appends
verified FINDING nodes with lineage (artifact → hypothesis), gated the
same way; findings are materialized knowledge rows, not session
messages.

### 4.3 History build ("talk now, build later")

New verb: **`knowledge_build_from_context`** (tool on the
companion; not `/acquisition`-cleared ship-like) — for the case where
the user chatted WITHOUT building first:

1. The companion agent packages a **verbatim** excerpt of user-chosen
   turns into a `session-history` JSONL payload
   (`lane: session-history`, `built_by: agent` since 2026-10-24; it
   was `user-initiated`).
2. The payload enters the SAME acquisition-gated central upload
   (`/knowledge/upload`); the write gate applies as always
   (`disabled`/`pinned` mismatches refuse loudly). *2026-10-24 (review of
   knowledge K1 part B):* the upload is marked as the agent's write: the
   model typed the excerpt, so it needs an approved proposal (`proposal_id`)
   and lands proposed. Unmarked, it landed established as the user's, which
   K-1 forbids. A verbatim keep read from the session store is the user's
   `/save` (manual 27).
3. Meaning: nothing about the words is synthesized — user-chosen slices
   become durable project knowledge, and full provenance ties them to
   the session.

Package invariants enforced by pins: empty content / no project /
mismatched pins / > 4 MiB slices / upload errors all stop loudly.

### 4.4 Everywhere consistency

Same gate-chain backs `r4 knowledge upload/import/ingest`, the
companion tool, and the `/umrs/knowledge/upload` route. There is no
private path.

---

## 5. Operation modes (r4 client and UMRS server)

### 5.1 r4 client modes

| Mode | Selector | Impact |
|------|----------|--------|
| API key        | `r4 auth login --api-key` | local profile; per-call headers |
| Gateway (strict) | default after login | Gateway-verified identity headers; UMRS strict by default |
| Transport selection | `R4_TRANSPORT_MODE=gateway \| direct \| auto` (default `auto`; `r4/src/r4/config.py`) | `gateway` = Gateway HTTP only; `direct` = talk to UMRS directly (admin/harness paths); `auto` = prefer gateway, fall back. `r4 health --direct-umrs` is a diagnostic flag, not a mode value |
| Demo | `VITE_R4_WEB_DEMO=1` | mock views visible (never default) |

### 5.2 Acquisition (per session; knowledge work)

| Mode | Value | Behavior |
|------|-------|----------|
| **open** | `/acquisition open` | writes allowed, approvals per call |
| **disabled** | `/acquisition off` | every write refuses loudly |
| **pinned (extend)** | `/acquisition extend <graph(,g2)>` | only the pinned graph(s) extend; pinning by name/id cross-resolves |

### 5.3 UMRS server modes

| Mode | Trigger | Impact on knowledge |
|------|---------|---------------------|
| Strict auth + HMAC | default | gateway-verified callers only; 401 identities else |
| MVP (dev only) | `UMRS_AUTH_MODE=mvp` | unverified legacy headers only in dev |
| Dual-corpus dwell | current | writes route central; endpoint corpora kept read-only |
| Offline inference | default, `HTTP_PROXY=""`, `TRANSFORMERS_OFFLINE=1` | no ambient downloads at inference |
| Egress | job-side EgressGuard profile | out through EgressGuard only when the profile is activated; unrelated to `R4_TRANSPORT_MODE` |
| Knowledge lanes | no flag needed | rag / graph / embeddings / catalog search served per family rule; traversal + export not yet gated |
| Worker venues | Slurm/K8s/Compose via `RIVAULT_ORCHESTRATOR` | jobs still write the same PG + S3; venue only changes compute |

### 5.4 Lane honesty conventions

Every hit on read lanes names its venue (`corpus_venue`, `lane`,
`corpus_*`) plus `wired_backends` / `unavailable_backends` in facade
responses — empty is acceptable, fabricated is not.

---

## 6. Ontology as a typed contract

Graph rows also bind to an **ontology** (versioned; registry from
migration 0091). Reads and writes evolve accordingly:

| Capability | Behavior |
|------------|----------|
| Write validation | compiler rejects payloads outside the vocabulary pre-write |
| Extraction widening | `extraction_fields` map declared fields onto extra relations (additive only) |
| Closure inference | `walk()` returns transitive dependencies with cycle-safe, depth-capped semantics; fails CLOSED on undeclared-transitive relations, on collapsed sat names, and on unknown ones |
| Subtype lattice | `subtype_of` pairs declare a type hierarchy; parent-type queries expand to descendants transitively (never widened silently) |
| Response visibility | query + graph detail payloads include the resolved ontology (`ontology_error` names unknown references — never smudged) |
| Traversal type filtering | custom ontologies filter rows by their `entity_types`; the default ontology stays a no-op |

The read-time closure and write-time validation are additive to the
existing lanes; they change nothing unless a graph binds a custom
vocabulary.

---

## 7. Failure semantics (what every user should expect)

Every refusal is typed and NAMED:

| Code family | Means |
|---------|-------|
| `knowledge_acquisition_blocked` | write gate off/mismatched at this moment |
| `project_missing` | no current project; verb cannot guess it |
| `ontology_error` | graph binds at an unknown/unregistered ontology |
| `wired/unavailable_backends` | backend plane down — reason named per lane |
| `*_blocked / *_failed` | all the rest; never a silent empty/zero |

Metrics carry only cardinality-safe labels (`lane`, `venue`, `kind`,
`graph kind/status`, `model`, `site`).

---

## 8. Cross-references

- [Visibility tiering + session-history build (design note)](../../docs/adr/050-visibility-and-session-history-build.md)
- [Ontology as typed contract (design note)](../../docs/adr/049-ontology-typed-contract.md)
- [Asset grants and scope ladders (design note)](../../docs/adr/041-asset-grants-scope-grammar.md)
- [Unified knowledge spine (design note)](../../docs/adr/027-unified-knowledge-spine.md)
- [Canonical graph / sat migration (design note)](../../docs/adr/026-sat-kg-disposition.md)
- [27. Knowledge Management](27-knowledge-management.md),
  [7. RAG](07-rag.md),
  [4. Chat & Companion](04-chat.md),
  [21. UMRS Operations](21-umrs-operations.md).
