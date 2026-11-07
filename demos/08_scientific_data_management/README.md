# Demo 08: Scientific Data Management

End-to-end lab data flow: upload → `DATA_INGESTION` → catalog grep → raw read → `r4 search --scope lab_only`.

Closes Integration Plan Phase A3, ADR-011 smoke checklist, and RC1 Blank Project **artifact mix** proof.

## What It Shows

- `POST /gw/files/upload` — project-scoped lab files (Blank Project mix)
- Durable `DATA_INGESTION` — Postgres `catalog_objects` row flips `pending` → `ready`
- `GET /gw/catalog/search` — lexical grep over catalog metadata (all modalities)
- `GET /gw/catalog/read` — byte-bounded raw file read with ACL
- `GET /gw/catalog/stats` — object counts by state/modality
- `r4 search --scope lab_only` — agentic catalog + raw read (no web)

## Blank Project Fixture (`fixture/`)

| Path | Count | Modality |
|------|-------|----------|
| `papers/` | 15 minimal PDFs | `document` |
| `notebooks/` | 2 `.ipynb` | `notebook` |
| `code/` | `model.py` | `code` |
| `data/` | `batch-3-yields.csv` | `structured` |
| `README.md` | project overview | `text` |

Minimal PDFs are generated on first run (`catalog_demo.ensure_blank_project_fixture`).
`.git` and other junk dirs are excluded (matches `ResearchProject` ingest filters).

## Quick Start

```bash
# Full demo — Blank Project mix (default)
bash run_demo.sh --auto

# Dry-run: validate fixture only (python3; no gateway or r4 CLI)
R4_DEMO_CATALOG_DRY_RUN=1 bash run_demo.sh --auto

# Legacy mode: CSV + PNG only
R4_DEMO_CATALOG_MODE=legacy bash run_demo.sh --auto

# Strict catalog gate: fail fast on upload/search/read only
R4_DEMO_STRICT=1 bash run_demo.sh --auto

# Unit tests (no gateway)
pytest demos/08_scientific_data_management/
```

## Prerequisites

1. Workstation (or other) tier running — see `demos/tiers/workstation/README.md`
2. `00_onboarding` completed (`r4 auth`, project, endpoint up)
3. `r4` on `PATH` with active profile and `R4_GATEWAY_URL` set

## Expected Output

1. Upload logs show 20 files under `uploads/demo-08-blank-*` (15 papers + notebooks + code + data + README).
2. Catalog search reports all modalities: `document`, `notebook`, `code`, `structured`, `text`.
3. Raw read preview on CSV includes `batch_id,yield_pct,co2_uptake_mmol_g` and batch-3 / 85% yield.
4. `r4 search` answer references batch-3 yield with catalog citations.

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Catalog timeout | `r4 job list`; UMRS `DATA_INGESTION` backend running; increase `R4_DEMO_CATALOG_TIMEOUT_SEC` (default 300) |
| Empty search hits | Project context: `r4 config show` → Project matches upload |
| Missing modalities | Re-run with fresh `R4_DEMO_CATALOG_PREFIX` to avoid stale hits |
| `r4 search` fails | Endpoint up (`r4 endpoint list`); model available |

## Deploy note

Catalog requires `umrs_db`. Before sidecar start:

```bash
python -m umrs.migrations --db-url $UMRS_JOBS_DB_URL  # applies 0054_catalog_objects
python -m umrs.data_catalog.backfill --db-url $UMRS_JOBS_DB_URL --bucket $S3_BUCKET  # once per env with legacy sidecars
```

## Related

- [ADR-011](../../docs/adr/011-data-catalog-and-ingestion.md)
- RC1 integration plan
- Real-mode research workflow: [`workflow/demo08_yield_eval/`](workflow/demo08_yield_eval/)
  (`r4 workflow upload … --name demo08_yield_eval`, then
  `r4 project loop start <project> --workflow demo08_yield_eval` or the Companion
  `research_loop_start(workflow_name="demo08_yield_eval")`)

## Detailed Step-by-Step (verified 2026-08-27)

1. Upload fixture (`fixture/` has 15 PDFs + notebooks + code + CSV)
2. Durable DATA_INGESTION flips rows pending → ready (check via catalog stats)
3. Catalog views
   ```bash
   # /gw/catalog/search|: all modalities seen (document/notebook/code/structured/text)
   # /gw/catalog/read — byte-bounded file bytes with ACL check
   r4 search --scope lab_only "attention"  # agentic catalog+raw read, no web
   ```
4. Output (verbatim from this run): `Steps: 3 | Sources: 10 | Confidence: 10% | Time: 284ms`

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Stats show 0 objects | wrong project namespace selected | `r4 project switch demo-project` (demo switches for you) |
| search lab_only returns 0 sources | fixture ingest not finished | retry after catalog reports 'ready' on each row |
