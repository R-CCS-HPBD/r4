# Example: Moving Knowledge Between Deployments (`.kb`)

Knowledge bundles are the sanctioned way to move a project's research
layer — documents, embeddings, graph, catalog — between deployments,
projects, or just onto a backup disk. They carry an integrity manifest
(content hash + HMAC signature when the deployment is secured).

This walk shows the full loop: build a project, seed knowledge, export,
validate, and restore onto another location.

## 1. Seed a project

```bash
r4 project create samples --name "Samples"
r4 project switch samples

mkdir -p /tmp/samples-corpus
cat > /tmp/samples-corpus/notes.md <<'MD'
# Turbine inspection notes
Unit T-7 shows vibration above baseline at 3.2 kHz (2026-07-30).
Preliminary hypothesis: blade imbalance. Run modal analysis to confirm.
MD

r4 project import /tmp/samples-corpus
```

## 2. Review the layer

```bash
r4 knowledge query "blade imbalance"
# 1 result(s) for "blade imbalance" [catalog; 8ms]
#   catalog_hit         0.81  notes.md
#       Unit T-7 shows vibration above baseline at 3.2 kHz...

r4 knowledge explore
# sections: artifacts(1) • findings(0) • memory(0) • plans(0)
# detail: lineage — artifact 'samples/notes.md' derived from '(none)'
```

## 3. Export

```bash
r4 knowledge export samples --output /data/backups/samples.kb
# ✓ Knowledge bundle exported: /data/backups/samples.kb
#   Documents: 1
#   Chunks: 3
#   Entities: 2
#   Relations: 1
#   Catalog objects: 1
```

Inspect before trusting:

```bash
r4 knowledge info /data/backups/samples.kb
# Knowledge Bundle:
#   Format: r4-kb v1.1
#   Project ID: samples
#   Embedding Model: intfloat/e5-base-v2
#   Contents: documents 1, chunks 3, entities 2, relations 1, catalog 1

r4 knowledge validate /data/backups/samples.kb
# ✓ Bundle is valid: /data/backups/samples.kb
#   Format: r4-kb v1.1
```

## 4. Restore on the same deployment

```bash
r4 knowledge restore /data/backups/samples.kb
# ✓ Restore complete (verified) — project: samples
#   Artifacts       : 1
#   Documents       : 3
#   Graph nodes     : 2
#   Rows skipped    : 0
```

Run it again — idempotency keeps the second pass cheap:

```bash
r4 knowledge restore /data/backups/samples.kb
# ✓ Restore complete (verified) — project: samples
#   Artifacts       : 0
#   Rowsskipped     : 1   # deduped by content hash
```

## 5. Restore for disaster recovery

The strength appears on a *different* deployment (upload the `.kb` to it,
then restore with its own project name):

```bash
# On the recovery host:
r4 knowledge restore /srv/kb/samples.kb --retarget samples-dr
# ✓ Restore complete — project: samples-dr

r4 project switch samples-dr
r4 knowledge query "vibration"
# 1 result(s) … same content, new project
```

## What got restored

| Table | Contents | Idempotent behavior |
|-------|----------|---------------------|
| Artifacts | registered blob references | content-hash dedup |
| Documents | knowledge IR (write-once) | insert-once |
| Graph nodes/edges | entity state | upsert (latest wins) |
| Embeddings | memory vectors + catalog | ON CONFLICT DO NOTHING |
| A2L links/refs | provenance edges | insert-once |

If a single row is malformed it's skipped and counted (`Rows skipped`),
never aborting the restore.

## Troubleshooting

- **`integrity verification failed`** — the manifest hash or signature
  doesn't match. The bundle is corrupted (or the recovery host lacks the
  `UMRS_SECRET_KEY` used to sign it).
- **`Database unavailable (503)`** �� the UMRS PG pool is absent. Restore
  requires a live UMRS backend.
- **`/srv/kb/...` not found** — the path is read *by the server*. The
  `.kb` must sit on a filesystem UMRS can see (shared mount or remote
  host), not your laptop.
