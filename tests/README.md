# Repo-root test belt — charter (ADR-054, D-5)

Repo-root `tests/` is the **enforcement and contract belt**: validators,
drift detectors, topology checks, contract rails. It is NOT a home for
product-specific suites — r4 internals belong in `r4/tests/`, umrs
internals in `umrs/tests/` (moved under plan §6.2 step 2.5).

## Tiers

Every test runs in the default belt unless it carries a tier marker:

| Marker        | Meaning                                    | Opt-in |
|---------------|--------------------------------------------|--------|
| `e2e`         | full-stack paths (compose, gateway)        | `R4_RUN_E2E=1` |
| `k8s`         | needs a Kubernetes cluster                 | `R4_RUN_E2E=1` |
| `hpc`         | needs Slurm / HPC resources                | `R4_RUN_E2E=1` |
| `integration` | needs external services (MinIO, PG, MCP)   | `R4_RUN_E2E=1` |

The single rule lives in `tests/conftest.py::pytest_collection_modifyitems`:
an item carrying any tier marker is **skipped** unless the run set
`R4_RUN_E2E=1` (or the module declares its own `R4_E2E_<TIER>` variable —
files with pre-existing module guards keep them; new heavyweight tests must
use the marker instead of boilerplate).

Environment-capability skips (kubectl/slurm/CUDA detection) apply on top and
are independent of tiers: they only convert would-be-failures into honest
skips on hosts that lack the tool — they never include or exclude a tier.

## Entry points

```bash
.venv/bin/python -m pytest tests -q                 # default belt
R4_RUN_E2E=1 .venv/bin/python -m pytest tests -q    # everything
.venv/bin/python -m pytest tests -m e2e -q          # only e2e lanes
```

## Adding tests

- Validator/drift/contract pins → this tree, no marker.
- Long-running or service-dependent paths → add the tier marker
  (`pytestmark = pytest.mark.e2e` at module level for whole files).
- Product-specific behavior (r4 CLI, umrs API, MCP adapters) → the owning
  package's `tests/` directory, not here.
