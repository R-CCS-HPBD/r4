# ADR-032 Amendment (2026-09-06) — Bundle contract + r4-kb v1.2 manifest

**Status:** Proposed (with implementation in WP-03, same PR)
- **Base document:** [032-knowledge-package-portability-contract.md](032-knowledge-package-portability-contract.md) (Draft — preconditions only)
- **Drives:** WP-03 (this PR), WP-17 (preconditions round), WP-06 (grants re-issuance)

## Decision

1. **The full v1.2 manifest is declared up front** (schema-declaration rule,
   maintainer ratification 2026-09-06). WP-17 populates fields; no second
   version bump is required for that round:

   ```jsonc
   {
     "format": "r4-kb",
     "version": "1.2",
     "project_id": "…",
     "generator": "umrs",
     "generator_version": "<umrs release>",
     "exported_at": "<iso8601>",
     "unsigned": false,
     "skills": [],        // [{ "ref": "<registry id>", "version_pin": "<…>" }] — v1.2 declares, WP-17 may populate
     "mcp_configs": [],   // same by-reference rule
     "ownership": {       // optional block; omitted when not applicable
       "generator_site": "<site id>",
       "owner_id": "<project owner identity>"
     },
     "embedding": {       // trust pin — REQUIRED (see 2)
       "model": "<canonical name or local-id>",
       "model_path": "<prestaged path that produced vectors>",
       "revision": "",
       "dimensions": 768,
       "distance": "cosine",
       "normalization": "unknown",
       "pending": false   // true ⇒ vectors are a placeholder per ADR-031 Invariant 3
     },
     "tables": { "artifacts": {"count": 0, "file": "artifacts.jsonl"}, "…": {} },
     "hash": "<sha256>",
     "signature": "<hmac-sha256>"
   }
   ```

2. **Embedding trust pin is required in every v1.2 manifest** (ratified
   bundle-contract decision: "embedding-model identity MUST be in the
   manifest"). It describes the *vector pipeline that produced
   `embeddings.jsonl`*. Sources, in precedence order: `memory_vectors.model`
   non-`'default'` rows; else aggregation over `knowledge_node_embeddings`
   (0075); else `pending: true` honestly (never silent). `provider` and
   fully canonical model-id workflow are out of v1.2 scope (WP-17).
   *(Correction 2026-10-21, cycle-2 N1: the exporter's `memory_vectors`
   reads named columns — `project_id`, `vector`, `document_id`, `metadata` —
   that no migration creates, so `embeddings.jsonl` was always empty and the
   pin always came from `knowledge_node_embeddings`. The `memory_vectors` leg
   is retired: the pin and `embedding_count` come from the node vectors
   (`node_embeddings.jsonl`), `embeddings.jsonl` stays in the layout empty,
   an unreadable vector table is stated as `embeddings_note: "embeddings not
   included: …"` (degraded result), and an import reports legacy
   `embeddings.jsonl` rows as not imported instead of failing.)*

3. **Unsigned bundles are no longer produced by default.** Export with no
   `UMRS_SECRET_KEY` configured fails unless the request explicitly sets
   `allow_unsigned: true`, in which case the manifest carries
   `"unsigned": true` and a loud audit line is logged. Import restores an
   unsigned (or unverifiable) bundle only with explicit
   `allow_unsigned=True`; otherwise `BundleFormatError` is raised before any
   write. The previous behavior (silent unsigned roundtrip) is retired.

4. **Version grammar is one-read-many-one-write-one.** Writers produce
   `1.2` only. Readers accept `1.0`, `1.1`, `1.2`. Reading `< 1.2`
   tolerates absent embedding pin / ownership / skills blocks and returns
   degraded markers (`result.embedding_pin == False`,
   `result.unsigned == True` when signature empty) instead of raising.

5. **`KNOWLEDGE_EXPORT` becomes dispatchable.** The server export route
   submits `JobType.KNOWLEDGE_EXPORT` with spec `{project_id,
   allow_unsigned?}`; the kind is removed from
   `_UNDISPATCHABLE_JOB_TYPES`; `create_backend_factory` gains the branch and
   its status-checker counterpart. This changes endpoint/job wire behavior
   (named in the WP-03 PR, pinned by tests).

6. **Restore remap covers ownership only; grant re-issuance waits for
   WP-06** (dependency rule, ratified 2026-09-06). Bundle rows restore under
   the target project scope; no grant/ACL semantics travel.

7. **Route shadowing removed:** durable verify moves to
   `POST /umrs/knowledge/verify/trigger` and store-backed readiness to
   `GET /umrs/knowledge/readiness/graph`. Existing `POST /verify` (sync QA)
   and `GET /readiness` (controller) semantics are the live client contract
   and are unchanged.

## Consequences

- Every new bundle is cross-site interpretable (pin embedded) and
  attestable (signed by default).
- The r4-client aggregation path converges to the same 8-table v1.2 layout
  in WP-03; its legacy flat-layout reader is retired at read level in favor
  of the shared importer contract (legacy 1.x still readable).
- ADR-032's five normative preconditions remain drafting gates for WP-17.
  Export from live tables (flagged as ADR-032 #2 violation) is acknowledged
  here as v1.2's interim state, remediated by the reconciliation-snapshot
  export in WP-17, and recorded in the WP-17 gap row (G-P2-5).

## Addendum (2026-09-06, WP-17 landing)

- **Reference population.** Manifest `skills` and `mcp_configs` are now
  populated by reference from the `skills` and `mcp_servers` registries
  (project + system scopes; each entry carries `version_pin`). Per the
  ratified contract, grants/ACLs are still excluded (site-local
  authorization) and the `ownership` block remains optional.
- **Declarative markers.** Every v1.2 manifest carries
  `collision_policy: "abort"`, `provenance_canonical: false`, and
  `reconciliation_snapshot: null` — the last two are *documented interim*
  states until the reconciliation-snapshot exporter lands (the remaining
  precondition round), instead of a silent superset.
- **Collision semantics (precondition #4, first leg).** Import resolves
  identity by primary key for knowledge_artifacts and knowledge_nodes:
  same identity + same `content_hash` → idempotent skip (reruns stay
  safe); same identity + different `content_hash` → the whole import
  aborts with `BundleFormatError` (no rows re-written). The *deterministic
  side-by-side diff text* required by the precondition is deferred to the
  same round as the canonical-identity mapping (both are migration-class).
- **Ownership remap.** Restore remap is the retarget mechanism only
  (`target_project_id`); grants/ACL remapping is definitively out of the
  bundle per the ratification — ownership travels as provenance only.

## Addendum 2 (2026-09-06, WP-18 landing)

- **Deterministic collision diff (precondition #4 continuation).** On
  abort, the import error now carries a sorted-JSON diff
  `{"table", "id", "existing": {content_hash, kind, label, size_bytes},
  "incoming_hash"}` (12-char fingerprints) instead of the placeholder
  message. Same-hash idempotent skip is preserved.
- **Degradation of the collision check is operator-visible.**
  `KnowledgeImportResult.collision_checks_degraded` counts tables whose
  identity lookup failed (per run); one WARNING per degraded table per
  run. The import itself continues idempotently, as before.
- **Reconciliation snapshot — attach leg (precondition #5, partial).**
  When a sync DSN is reachable (`UMRS_DB_URL`/`DATABASE_URL`),
  `reconciliation_snapshot` embeds `{captured_at, states, assets_total,
  catalogue_available, digest}` — a deterministic summary of
  `plane_reconciler.scan_project` — instead of `null`. The ad-hoc read
  beneath both paths keeps `provenance_canonical: false`; genuine
  snapshot-consumption (exporter gated on the checklist result instead
  of living tables) and the canonical-identity switch (precondition #1,
  migration-class) remain deferred to the full schema round.
- **Writer boundary.** The only code that stamped a portable-format
  "1.1" bundle was `KnowledgeBundleWriter` (r4 flat layout), and it had
  **no production call sites** — the class was deleted in WP-18 (repo is
  pre-release; no compat shim kept). The `KnowledgeBundleManifest`
  dataclass and `KnowledgeBundleReader` remain for imports/reads. All
  bundle producers (umrs durable export; CLI client projection) emit v1.2.

## Addendum 3 (2026-09-22, bundle v2 / gap rows D-23, D-40, D-41)

- **Reference blocks are consumed on import (D-23 → Implemented).** The
  v1.2 `skills` / `mcp_configs` blocks stopped being write-only:
  `KnowledgeImportEngine(skill_store=, mcp_store=)` registers skill refs
  as disabled stubs (`manifest._bundle_ref`, never stomping existing
  rows) and resolves MCP refs against the registry, reporting
  `mcp_refs_missing` in the restore result (what Hatchling still has to
  fetch). Absent stores defer — counted, never a crash.
- **`catalog` + `ownership` blocks emitted (D-40).** The manifest now
  carries the D-39 `scientific_catalogs` row verbatim under `catalog`
  (best-effort, {} when the project has none) and the declared-but-never-
  emitted `ownership` block (contributors + licence sourced from the
  catalog row). Restore re-registers the catalog row idempotently
  (`catalog_registered`), closing the export → import round-trip of the
  catalog object. Blocks are additive-optional: readers accepting 1.0–1.2
  need no change (client `BUNDLE_VERSION = "1.2"`, V-11).
- **Transforming upgrade 1.x → 1.2 (D-41).** Import back-fills the v1.2
  blocks on read (`result.upgraded_from` carries the source version);
  `write_upgrade=true` additionally rewrites `manifest.json` at v1.2 and
  drops the stale signature (an upgraded bundle is unsigned until
  re-exported). This closes the "imported older catalog is upgraded" leg
  of gap row D-41 for the reader side; the base ADR's preconditions
  (#1 canonical identity, #5 snapshot-consumption) stay deferred on their
  own rows.
- **Plane fence kept.** The import engine talks to a store adapter
  (`register_from_bundle(spec_dict, owner_id=)`) owned by the api layer;
  `umrs.knowledge` never imports `umrs.api` (ADR-053).

## Addendum 4 (2026-09-24, precondition #5 consumption leg / gap rows D-40, D-41)

- **The exporter consumes the checked snapshot (precondition #5).** The
  reconciliation scan now runs *before* any table is dumped
  (`reconcile_snapshot.take_export_snapshot`) and gates the canonical plane:
  document + chunk nodes of assets outside `COMPLETE` / `EMBEDDING_PENDING`
  (i.e. `PARTIAL`), every orphaned chunk node, and every edge touching an
  excluded node stay out of the bundle. `reconciliation_snapshot` records
  `consumed: true`, `export_eligible_states`, `excluded_assets`
  (`{document_id, state, reasons}`), `orphaned_chunks_excluded` and
  `excluded_node_count` beside the attach-leg summary. No reachable sync DSN
  keeps the documented interim: `reconciliation_snapshot: null`, ad-hoc read.
- **Scope of the gate — honest boundary.** The reconciler's planes and the
  exporter's tables overlap only on `knowledge_nodes` (+ edges). The exported
  vector table is `memory_vectors`; the reconciler's vector plane is
  `knowledge_node_embeddings` (0075), and the catalogue it checks is
  `catalog_objects` while the bundle's `catalog.jsonl` comes from
  `knowledge_catalog`. Those tables are not snapshot-gated yet.
- **Finding (named next leg):** `knowledge_node_embeddings` — the vectors the
  durable ingest chain (KNOWLEDGE_EMBED) writes — are read only to derive the
  embedding pin and are **never exported**, so a bundle of a normally
  ingested project arrives with no canonical vectors (the destination
  re-embeds, which precondition #3 permits but the design's "portable RAG /
  vector index" does not intend). Carrying eligible node embeddings (with the
  pin) is the next D-40 leg.
- `provenance_canonical` stays `false` (precondition #2 is its own gate);
  precondition #1 (canonical identity) stays deferred, migration-class.

## Addendum 5 (2026-09-24, canonical node vectors travel / gap row D-40)

- **`node_embeddings.jsonl` (additive table).** Export writes the project's
  `knowledge_node_embeddings` rows — the vectors KNOWLEDGE_EMBED produces —
  as `{node_id, model_name, dimension, embedding, content_hash}`, skipping
  rows of snapshot-excluded nodes (addendum 4). Readers that do not know the
  table ignore it; the manifest `tables` block lists it.
- **Destination-scoped trust (precondition #3, applied to node vectors).**
  Import trusts exactly one pipeline: the destination's own knowledge
  embedder (`UMRS_KNOWLEDGE_EMBED_MODEL`). A row is imported only when its
  model matches by basename (sites stage the same model under different
  paths) and the vector length equals its declared dimension; it lands under
  the destination's model name so the destination's queries find it, and
  existing rows are never overwritten. Everything else — including every row
  when the destination has no embedder configured — is counted
  `node_embeddings_pending` for re-embedding (`KNOWLEDGE_REINDEX`).
- **Honest limit of the pin.** 0075 records `model_name` + `dimension` only;
  revision, normalization, distance metric and tokenizer revision (the full
  precondition #3 pin) are not stored per row, so the match is model +
  dimension. Recording the full pin per vector is a schema follow-up.
- `memory_vectors` carriage is unchanged (not snapshot-gated, no trust check
  yet) — its own follow-up. *(2026-10-21: retired, see the item 2
  correction; it never carried a row.)*

## Addendum 6 (2026-09-24, precondition #1 v1 leg — canonical identity map / gap rows D-40, D-41)

- **Contract `cid-1` (`umrs/knowledge/canonical_identity.py`).**
  `physical_content_digest → canonical_content_identity → document_id`,
  with the normalization chosen from the bytes alone (never the MIME type,
  which differs between deployments): `text-v1` when the bytes are strict
  UTF-8 with no NUL (one leading BOM stripped, NFC, CRLF/CR → LF, trailing
  spaces/tabs per line and trailing newlines removed; identity = sha256 of
  that); `bytes` otherwise (sha256 of the raw bytes); `physical` when the
  bytes were unreadable at export (the stored digest — weaker, never wrong:
  it can miss a text match, it never equates two different documents).
  `document_id = "kdoc-" + identity[:32]`. A new normalization is a new
  contract version, never an edit of `text-v1`.
- **Map at export time, never rewrite in place** (the base ADR's v1 scope).
  The exporter reads each artifact's bytes by `source_ref` (the ingest
  bucket; objects over 64 MiB are not read) and adds a
  `canonical_identity` block — `{contract, document_id, identity,
  normalization}` — to every `artifacts.jsonl` row; the manifest gains
  `identity: {contract, normalizations: {<name>: count}}`. Existing rows
  keep their deployment-local ids.
- **Import carries, never trusts blindly.** A row's block is kept only when
  it names `cid-1`, a known normalization and a 64-hex identity; the
  `document_id` is recomputed from the identity (a forged id is dropped).
  The block lands in the artifact's `metadata.canonical_identity` (JSONB —
  no schema change). Before the restore, import looks the incoming
  document ids up among the destination project's artifacts that carry one,
  and reports each "same logical document under another id" pair as
  `canonical_matches` — reported, never merged (merge/sync is the
  federation ADR's). A failed lookup is a degraded collision check, never
  a failed import.
- **Honest limits.** Destination artifacts ingested natively carry no block
  until something stamps them, so today a match is found against earlier
  imports (and identical raw bytes still dedup per project as before).
  Stamping native rows is the follow-up (a metadata backfill job, reading
  bytes the same way); the identity is not yet an index.

## Addendum 7 (2026-09-24, precondition #1 — native rows carry the identity / gap rows D-40, D-41)

- **Stamped at ingest.** The ingest paths that hold the bytes — inline
  text, local file, remote fetch (`knowledge_adapters`) and the loop's
  finding mint (`finding_ingest`) — write `metadata.canonical_identity`
  (`cid-1`) on the new artifact row. Rows minted without bytes (rag-upload
  mint with a synthetic digest, workflow-output provenance rows) stay
  unstamped; the export keeps computing theirs on the fly.
- **Backfilled for older rows.** `python -m umrs.knowledge.identity_backfill
  [--project-id P] [--apply]` (dry-run by default; DSN + S3 from the server
  env) reads each unstamped active artifact's bytes by `source_ref` and
  adds the block, keyset-paged, never overwriting an existing block. Rows
  whose bytes are unreadable or over the 64 MiB cap stay unstamped —
  freezing the weaker `physical` identity would hide a later text match.
  Ids are untouched (addendum 6: a map, never a rewrite).
- **Export reuses a carried block** (no byte read); only unstamped rows are
  read. Metadata arriving as JSON text (pools without a JSONB codec) is
  decoded on both the export and import side.
- With native rows stamped, an import's `canonical_matches` reaches
  documents ingested on the destination, not only earlier imports. Still
  not an index (a lookup filters the project's JSONB): an expression index
  is a later migration if a project's artifact count calls for it.

## Addendum 8 (2026-09-24, integrity covers table contents — defect fix / gap row D-40)

- **Defect.** The manifest hash and HMAC covered `manifest.json` only, and its
  `tables` block holds names and counts. A signed bundle whose
  `artifacts` / `nodes` / `edges` (or any) rows were edited still verified,
  and the import reported "(verified)".
- **Fix.** Export writes each present table file's `sha256` into its
  `tables` entry before hashing and signing, so the signature pins the
  contents. `verify_bundle_integrity` then requires every pinned file to
  be present under a plain file name (no path) with a matching digest.
- **Compatibility.** Bundles exported before this carry no digests. They
  verify as before (manifest only), and the import result says
  `tables_verified: false`. r4 prints "manifest verified; table contents not pinned".
- This is also the base that addendum 9 / the raw-materials amendment
  builds on: `raw_objects.jsonl` is a pinned table, and it pins every raw
  object's sha256.
