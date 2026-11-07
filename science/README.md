# science — Shared Contracts Layer for Autonomous Science

**Pattern:** `r4-contracts/` (stdlib-only, no external dependencies)

This package defines the core scientific objects used by the AI4S
autonomous science loop:

- **`ScientificReward`** — multi-class reward function with weighted components
- **`Experiment`** — first-class object with `execute()` method
- **`ScienceDomain`** — pluggable domain interface (`Protocol`)
- **`science.domains`** — package for domain plugins (none bundled; implement the `ScienceDomain` Protocol)

## Architecture

```
science/
├── pyproject.toml
├── README.md
├── src/
│   └── science/
│       ├── __init__.py      # Public API
│       ├── reward.py         # ScientificReward
│       ├── experiment.py     # Experiment, ExperimentResults
│       ├── domain.py         # ScienceDomain Protocol, Hypothesis, Evidence
│       └── domains/
│           ├── __init__.py
│           └── (your_domain.py)  # add your own domain plugin here
└── tests/
    ├── test_reward.py
    ├── test_experiment.py
    ├── test_domain.py
    └── test_integration_week1.py
```

## ScientificReward Components

| Component | Definition | Weight |
|-----------|-----------|--------|
| `performance` | `metric_delta` (e.g., `recall_new - recall_old`) | 0.5 |
| `novelty` | L2 distance in parameter space from prior experiments | 0.1 |
| `information_gain` | `belief_entropy_before - belief_entropy_after` | 0.2 |
| `reproducibility` | `1.0 if result_replicated else 0.0` | 0.1 |
| `cost` | `normalized_gpu_hours` | 0.1 (subtracted) |

## Usage

```python
from science import ScientificReward, Experiment
from my_domains import MyDomain  # your ScienceDomain implementation

domain = MyDomain()
ctx = ResearchContext(question_id="q_001")
hyp = domain.formulate_hypothesis("Can threshold tuning improve detection?", ctx)
exp = domain.design_experiment(hyp, Constraints())
results = exp.execute(workflow_runner=run_workflow_record, workflow_json={...})
evidence = domain.evaluate_results(exp, results)
reward = domain.compute_reward(exp, evidence)
```

## Installation

```bash
uv sync --package science
```

## Testing

```bash
uv run --package science pytest
```

## See Also

- [Autonomous Science Plan](../docs/autonomous-science/PLAN.md)
- [Development Plan](../docs/autonomous-science/DEVELOPMENT_PLAN.md)
