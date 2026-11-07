# ADR-032: Knowledge Package — portability contract

**Status:** Draft (preconditions only)
- **Governing rule (from the review round-trip):** ADR-032 *does not begin
  by defining the package format*. It begins by defining **the conditions
  under which a package is allowed to claim portability**. Schema work is
  deliberately deferred until those preconditions hold — anything earlier
  would be serializing three eventually-consistent databases and shipping
  blind.
- **Date:** 2026-08-23
- **Related:** ADR-031 (plane ownership + normative invariants; gates this
  document), `docs/development/drift-remediation-plan-2026-08-22.md`
  (execution queue), `memories/repo/knowledge-catalog-three-tier-boundary-2026-08-21.md`.

## Context

The user asked how knowledge moves between R4 instances (RCCS ↔ laptop ↔
other HPC ↔ archive) — and clarified that "portable" means **portable as a
semantic contract**, not "dump the same Postgres tables elsewhere." ADR-031
separated physical existence, semantic meaning, and retrieval state. This
document defines the movement of the *contract*; the planes stay
deployment-local as ADR-031 demands.

```
                    ADR-031
               ownership + invariants
                       │
                       ▼
              reconciliation checker
                       │
                 trusted state
                       │
                       ▼
                    ADR-032
             portable knowledge package
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
       graph       provenance    optional
       identity                   vectors
          │                         │
          └──────────┬──────────────┘
                     ▼
              destination R4
                     │
             local catalogue  (deployment binding)
             local object store
             local embeddings
```

## Normative preconditions (drafting gate — schema work begins only after all five hold)

1. **Identity stability.** `document_id` is derived from the canonical
   content identity, never from upload metadata:

   ```
   physical_content_digest
        ↓
   canonical_content_identity
        ↓
   document_id
   ```

   The digest-to-identity normalization is part of the identity contract
   (versioned). Equality across deployments requires the canonical
   identity, not byte identity alone — same logical document with
   different serializations must converge on one `document_id`.
   Existing canonical rows keep their current deployment-local IDs;
   the identity switch is migration-class and out of v1 scope (map at
   export time, do not rewrite in place).

2. **Provenance canonicalization gate exported.** A package is
   **portable-complete** only when every exported asset’s provenance is
   rendered in the canonical shape by the ADR-031 reconciliation job.
   Anything else is a diagnostic dump, not a portable scientific
   package.

3. **Destination-scoped embedding trust.** Each import declares exactly
   one trusted pipeline pinned on
   `provider / model / revision / implementation / dimensions /
   normalization / distance metric / tokenizer revision`. Package
   vectors failing the pin become `EMBEDDING_PENDING` (Invariant 3,
   ADR-031); model-name match alone is NEVER sufficient for
   auto-coexistence.

4. **Federation semantics outside v1.** Import identity collision →
   **abort with deterministic diff** (assets compared side-by-side,
   provenance preserved). No silent merge; versions / tombstones /
   conflict semantics are their own ADR.

5. **Reconciliation-checker-snapshot export.** The exporter consumes a
   checked snapshot emitted by the reconciliation job — never reads the
   three planes ad hoc. Export eligibility = snapshot-competent
   (`COMPLETE` or `EMBEDDING_PENDING`), never `PARTIAL` or `ORPHANED`.

## Out of scope for v1

- Merge / sync / replication semantics (federation ADR, future).
- Raw artifact carriage (knowledge-only packages are valid full
  packages); artifacts remain destination bindings.
- Catalogue plane copy: the catalogue remains deployment-local; the
  package carries only binding pointers resolved by checksum where the
  bytes also travel.

## This is a draft skeleton

Sections for Decision / Consequences / Import-export protocol /
Validator / Invariant tests are pending on the preconditions above.
Drafting now would invert the precondition ordering this document
defines.
