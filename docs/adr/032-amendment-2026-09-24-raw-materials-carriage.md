# ADR-032 Amendment (2026-09-24) — Opt-in raw source materials in the bundle

**Status:** Accepted (2026-09-24, agent decision under the maintainer's
  delegation of the same date; implemented the same day). The four open
  questions are decided below, with the reasoning and rejected options.
- **Base document:** [032-knowledge-package-portability-contract.md](032-knowledge-package-portability-contract.md)
  ("Out of scope for v1: raw artifact carriage") with the
  [2026-09-06 amendment](032-amendment-2026-09-06-bundle-contract-v12.md)
  (addenda 1–7)
- **Gap rows:** D-40 (bundle carries materials), D-39 (catalog = materials +
  RAG + graph + skills + MCP + metadata)

## Context

The design's Scientific Catalog bundles *materials* with the knowledge
derived from them. A `r4-kb` v1.2 bundle carries artifact **metadata** only.
The bytes stay in the source installation's object store at
`projects/<project>/knowledge/artifacts/<content_hash>`, and the base ADR
treats them as destination bindings. So an imported project can be queried
but cannot be re-extracted, re-embedded from source or audited against the
original papers and code. This is the last open leg of D-40.

What already exists and constrains the design:

- The export job can read the bytes. The ADR-032 #1 identity map reads them
  by `source_ref` through the ingest bucket; objects over 64 MiB are not read.
- The transport archive import caps what it accepts (100 000 members, 8 GiB
  unpacked) and extracts regular files only (`bundle_archive.safe_extract`).
- Projects carry `data_class` (ADR-062, default **sensitive**). Catalog rows
  carry `licence`, `contributors` and D-46 `redaction_rules`.

## Proposed decision

1. **Opt-in, per export.** `r4 project export --with-raw` (API
   `include_raw: true`). Without it the bundle stays knowledge-only, exactly
   as today; the base ADR's "knowledge-only packages are valid full
   packages" still holds.
2. **Layout: additive, content-addressed.** `raw/<content_hash>` holds one
   file per distinct object (deduplicated). The additive table
   `raw_objects.jsonl` holds `{artifact_id, content_hash, size_bytes,
   mime_type, file}`. The manifest adds `tables.raw_objects` and
   `raw: {included, count, bytes, skipped: {<reason>: n}}`. Readers that do
   not know the table ignore it (the addendum 5 precedent). No version
   bump: v1.2 declared additive tables up front.
3. **Integrity, transitively signed.** The signed manifest pins
   `raw_objects.jsonl` by sha256 (addendum 8 of the 2026-09-06 amendment:
   before it, the signature covered no table contents at all). The table
   pins each object's sha256. Import recomputes
   every file's sha256 and refuses the whole bundle on a mismatch. An
   unlisted file under `raw/` is refused too.
4. **Eligibility at export.** An object travels only when:
   - its artifact row is exported (active, in this project) and carries a
     real content digest (rows with synthetic digests have no object);
   - the stored bytes hash to that digest;
   - it is not excluded by the catalog's D-46 redaction rules (new target
     `raw`, matching by artifact kind / MIME);
   - it fits the size caps (see question 3).

   Anything else is skipped and counted by reason, never silently dropped.
5. **Import binds to the destination.** Each verified object is written to
   the destination project's key
   (`projects/<target>/knowledge/artifacts/<content_hash>`). The restored
   artifact row's `source_ref` is rewritten to that key, the one field that
   is a deployment-local binding. Ids and hashes are untouched. An artifact
   without a carried object stays metadata-only, as today. The destination
   storage credentials never leave UMRS; there is no second credential path
   (CLAUDE.md ownership rule).
6. **Still out of scope:** copying the catalogue plane (data catalogue
   assets, ADR-031), and federation, merge or sync semantics.

## Decisions on the open questions (2026-09-24)

1. **Who may export raw bytes, and sensitive projects:** raw export is
   **owner or admin only**, whatever the data class. Carrying the original
   bytes out is a data release, which is the owner's call, not every
   member's. On top of that, a project whose `data_class` is not
   `non_sensitive` needs `--acknowledge-sensitive`. An unset class counts
   as sensitive (the ADR-062 default). The acknowledgement (who, class,
   when) is audited (`knowledge.export.raw`) and written into the
   manifest's `raw.acknowledgement`. If the registry is unreachable, the
   gate fails closed.
   *Rejected:* (a) refusing sensitive projects outright blocks legitimate
   institutional transfers; (c) admin-only takes the decision away from the
   data owner.
2. **Licence gate:** yes. Raw carriage is refused
   (`knowledge.raw_licence_required`) unless the catalog states a licence.
   The export engine enforces it again (`no_licence`).
3. **Caps:** **64 MiB per object and 1.5 GiB total**. The total is bounded
   by the import route's 2 GiB upload cap, because raw objects such as
   PDFs barely compress. The 4 GiB in the draft would have produced
   bundles that cannot be imported. `UMRS_KB_RAW_OBJECT_MAX_BYTES` and
   `UMRS_KB_RAW_TOTAL_MAX_BYTES` can only tighten the defaults.
4. **Import opt-out:** yes, `r4 knowledge import --no-raw`
   (`accept_raw=false`). The knowledge is restored and the objects are
   counted `raw_declined`, never read.

## Consequences

- An imported catalog can be re-extracted and re-embedded from source,
  which closes D-40's "materials" leg. It also lets the identity map
  (addenda 6–7) be recomputed at the destination.
- Bundles can become large. The caps and the opt-in keep the default
  unchanged.
- The ADR-031 invariant that artifacts are destination bindings still
  holds: the bytes are re-bound, never referenced cross-site.

- Import verifies every object before any write, so one bad or unlisted
  object refuses the whole bundle. A failure of destination storage is a
  named degraded result; the rows stay restored.

## Verification pointers

- `umrs/src/umrs/knowledge/bundle_raw.py` — plan / write / verify, caps, D-46 `raw` rule
- `umrs/src/umrs/api/_raw_export_gate.py` — owner/admin, licence, acknowledgement, fail-closed
- `umrs/src/umrs/knowledge/bundle_export.py` (`_dump_raw`), `bundle_import.py` (`_raw_rows`, `_restore_raw`), `store.py` (`rebind_artifact_source`)
- `umrs/src/umrs/api/knowledge.py` (export route), `api/bundle_restore.py` (`accept_raw`, the destination writer)
- `r4`: `r4 project export --with-raw [--acknowledge-sensitive]`, `r4 knowledge import --no-raw`
- Tests: `umrs/tests/test_bundle_raw.py`, `umrs/tests/test_bundle_import_upload.py` (route gate), `r4/tests/test_knowledge_transport_cli_output.py`
