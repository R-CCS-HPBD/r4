# Blank Project fixture (Demo 08)

Representative RC1 project layout for catalog smoke tests:

| Path | Count | Catalog modality |
|------|-------|------------------|
| `papers/` | 15 minimal PDFs | `document` |
| `notebooks/` | 2 `.ipynb` | `notebook` |
| `code/` | Python sources | `code` |
| `data/` | CSV lab results | `structured` |
| `README.md` | Project overview | `text` |

Minimal PDFs are generated on first run by `catalog_demo.ensure_blank_project_fixture()`
(see `MANIFEST.json`). Other files are committed.
