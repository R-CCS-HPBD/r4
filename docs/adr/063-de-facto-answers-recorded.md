# ADR-063: De-facto answers recorded — metadata home, KG home, identity mapping, session authority

**Status:** Accepted (recording decisions the tree already made)
**Date:** 2026-09-22 (maintainer sign-off, design reset wave)
**Relates to:** ADR-043 (attempt-scoped STS), ADR-049 (ontology typed contract), ADR-057 (chat-turn authority, Proposed), gap rows D-18 / D-42 / D-55 / D-56 and §9 of [docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md)

## Context

The design's open-questions list asks where the knowledge graph lives, where
metadata lives, how identity maps to Slurm/k8s/S3, and where session state
lives. The implementation already answered all four de facto; the gap
analysis §9 names them "record as decided". This ADR does the recording.

## Decision

| Question | Answer (recorded) | Evidence |
|---|---|---|
| Metadata home (D-18) | **Postgres** for jobs, catalog, knowledge, sessions, trust, grants, projects, audit, A2L lineage; S3 for blobs. Accounts move from JSON files into Postgres beside projects (D-06); chat-turn authority finishes with ADR-057. | Migrations 0009–0100 |
| Knowledge-graph home (D-42) | **Postgres relational tables** (`knowledge_nodes` / `knowledge_edges` + registry 0069 + ontology 0091, transitive closure via CTE, ADR-049 leg C); no dedicated graph DB, no graph extension. | D-42 row |
| Identity mapping (D-55) | **Per target:** Slurm = per-user `slurm_account` (`--account=`) with optional per-user SSH delegation, else the UMRS service connection; S3 = attempt-scoped STS (ADR-043); k8s = service account. Impersonation-by-account, not one blanket service account. | D-55 row |
| Session authority (D-56) | The **Postgres session journal is the authority** (migration 0080); SQLite is a read-model rebuilt from it. Companion chat-turn authority completes under ADR-057; remote sessions checkpoint per turn. | D-56 row |

## Consequences

- The four design open questions close; the design document is amended in
  the same wave.
- Remaining honest tails stay tracked on their own rows: accounts→Postgres
  (D-06), remote-session rail reach for knowledge/rag (D-42 partial),
  ADR-057 rollout (D-56 partial).
