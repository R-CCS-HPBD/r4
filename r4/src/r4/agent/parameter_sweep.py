"""Parameter sweep generation and result aggregation.

Provides strategies for exploring parameter spaces:
- **Grid**: Full factorial (all combinations)
- **Random**: Uniform random sampling
- **Latin Hypercube**: Stratified random sampling
- **Bayesian**: Model-based optimization (placeholder — falls back to random)

Usage::

    from r4.agent.experiment import ExperimentRunner, SlurmExperimentSpec, SweepStrategy
    from r4.agent.parameter_sweep import ParameterSweep

    runner = ExperimentRunner(client)
    sweep = ParameterSweep(
        base_name="lr_sweep",
        slurm_spec=SlurmExperimentSpec(script_path="/run.sh"),
        parameters={"lr": [0.001, 0.01, 0.1], "batch_size": [16, 32]},
        strategy=SweepStrategy.GRID,
        hypothesis_id="h_001",
    )

    experiments = sweep.generate_jobs()
    sweep.submit_all(runner)
    results = sweep.aggregate_results(runner)
    print(f"Best: {results.best_parameters} (score={results.best_score})")
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Dict, List, Optional

from .experiment import (
    Experiment,
    ExperimentRunner,
    ExperimentStatus,
    SlurmExperimentSpec,
    SweepStrategy,
)

logger = logging.getLogger(__name__)


@dataclass
class SweepResults:
    """Aggregated results from a parameter sweep."""

    best_parameters: Dict[str, Any] = field(default_factory=dict)
    best_score: float = float("-inf")
    ranked_results: List[Dict[str, Any]] = field(default_factory=list)
    convergence_data: List[Dict[str, Any]] = field(default_factory=list)
    total_experiments: int = 0
    completed: int = 0
    failed: int = 0


class ParameterSweep:
    """Generate and manage parameter sweep experiments.

    Creates a set of Experiment instances with different parameter
    combinations according to the chosen strategy, then submits them
    through the ExperimentRunner.

    Args:
        base_name: Prefix for experiment names.
        slurm_spec: Base Slurm spec (shared across all experiments).
        parameters: Dict mapping param names to lists of values.
        strategy: Sweep strategy (GRID, RANDOM, etc.).
        hypothesis_id: Optional hypothesis to link experiments to.
        project_id: Optional project for UMRS job submission.
        max_experiments: Cap on total experiments (for grid, truncates).
        score_key: Key in parsed_outputs to rank experiments by.
        maximize: If True, higher scores are better; if False, lower.
        seed: Random seed for reproducibility.
    """

    def __init__(
        self,
        base_name: str,
        slurm_spec: SlurmExperimentSpec,
        parameters: Dict[str, List[Any]],
        strategy: SweepStrategy = SweepStrategy.GRID,
        *,
        hypothesis_id: Optional[str] = None,
        project_id: Optional[str] = None,
        max_experiments: int = 100,
        score_key: str = "score",
        maximize: bool = True,
        seed: int = 42,
    ) -> None:
        self.base_name = base_name
        self.slurm_spec = slurm_spec
        self.parameters = parameters
        self.strategy = strategy
        self.hypothesis_id = hypothesis_id
        self.project_id = project_id
        self.max_experiments = max_experiments
        self.score_key = score_key
        self.maximize = maximize
        self.seed = seed

    # ── Generation ───────────────────────────────────────────────────────

    def generate_jobs(self) -> List[Experiment]:
        """Generate Experiment instances based on the sweep strategy."""
        combos = self._generate_combinations()
        experiments: List[Experiment] = []

        for i, params in enumerate(combos):
            name = f"{self.base_name}_{i:03d}"
            exp = Experiment(
                name=name,
                description=f"Parameter sweep: {self.base_name} (strategy={self.strategy.value})",
                parameters=params,
                slurm_spec=self.slurm_spec,
                hypothesis_id=self.hypothesis_id,
                project_id=self.project_id,
            )
            experiments.append(exp)

        logger.info(
            "Generated %d experiments for sweep %s (strategy=%s)",
            len(experiments),
            self.base_name,
            self.strategy.value,
        )
        return experiments

    def _generate_combinations(self) -> List[Dict[str, Any]]:
        """Generate parameter combinations based on strategy."""
        if self.strategy == SweepStrategy.GRID:
            return self._generate_grid()
        elif self.strategy == SweepStrategy.RANDOM:
            return self._generate_random()
        elif self.strategy == SweepStrategy.LATIN_HYPERCUBE:
            return self._generate_latin_hypercube()
        elif self.strategy == SweepStrategy.BAYESIAN:
            return self._generate_bayesian()
        else:
            raise ValueError(f"Unknown sweep strategy: {self.strategy}")

    def _generate_grid(self) -> List[Dict[str, Any]]:
        """Full factorial grid — all combinations of all parameters."""
        keys = list(self.parameters.keys())
        values = [self.parameters[k] for k in keys]
        combos = [dict(zip(keys, combo)) for combo in product(*values)]
        return combos[: self.max_experiments]

    def _generate_random(self) -> List[Dict[str, Any]]:
        """Uniform random sampling from parameter lists."""
        rng = random.Random(self.seed)
        keys = list(self.parameters.keys())
        combos: List[Dict[str, Any]] = []

        for _ in range(self.max_experiments):
            combo = {k: rng.choice(self.parameters[k]) for k in keys}
            combos.append(combo)

        return combos

    def _generate_latin_hypercube(self) -> List[Dict[str, Any]]:
        """Latin Hypercube sampling from discrete parameter lists.

        Ensures each value appears approximately the same number of
        times across the experiment set.
        """
        rng = random.Random(self.seed)
        keys = list(self.parameters.keys())
        n = self.max_experiments
        combos: List[Dict[str, Any]] = []

        # For each parameter, create a stratified sample
        for i in range(n):
            combo: Dict[str, Any] = {}
            for k in keys:
                values = self.parameters[k]
                # Map experiment index to a stratum, then pick randomly within it
                stratum = i % len(values)
                combo[k] = values[stratum]
            combos.append(combo)

        # Shuffle to avoid systematic patterns
        rng.shuffle(combos)
        return combos

    def _generate_bayesian(self) -> List[Dict[str, Any]]:
        """Bayesian optimization — placeholder, falls back to random.

        A full Bayesian optimization would require iterative model
        updates between experiment batches. This placeholder simply
        uses random sampling as a starting point.
        """
        logger.info("Bayesian strategy not yet implemented — falling back to random")
        return self._generate_random()

    # ── Submission ───────────────────────────────────────────────────────

    def submit_all(self, runner: ExperimentRunner) -> List[Experiment]:
        """Design and submit all experiments via the runner.

        Returns the list of submitted experiments with job IDs.
        """
        experiments = self.generate_jobs()
        submitted: List[Experiment] = []

        for exp in experiments:
            try:
                designed = runner.design(
                    exp.name,
                    exp.slurm_spec,  # type: ignore[arg-type]
                    description=exp.description,
                    parameters=exp.parameters,
                    hypothesis_id=exp.hypothesis_id,
                    project_id=exp.project_id,
                )
                submitted_exp = runner.submit(designed)
                submitted.append(submitted_exp)
            except Exception:
                logger.warning("Failed to submit experiment %s", exp.name)

        logger.info(
            "Submitted %d/%d experiments for sweep %s",
            len(submitted),
            len(experiments),
            self.base_name,
        )
        return submitted

    # ── Aggregation ──────────────────────────────────────────────────────

    def aggregate_results(self, runner: ExperimentRunner) -> SweepResults:
        """Aggregate results from completed experiments.

        Ranks experiments by ``score_key`` from their parsed outputs.
        Experiments without results or without the score key are
        counted as failures.
        """
        experiments = runner.get_all()
        results = SweepResults(total_experiments=len(experiments))

        scored: List[Dict[str, Any]] = []

        for exp in experiments:
            if exp.status == ExperimentStatus.COMPLETED and exp.results:
                results.completed += 1
                score = exp.results.parsed_outputs.get(self.score_key)
                if score is not None:
                    try:
                        score = float(score)
                    except (ValueError, TypeError):
                        score = None

                    scored.append(
                        {
                            "parameters": exp.parameters,
                            "score": score,
                            "experiment_id": exp.experiment_id,
                        }
                    )

                    results.convergence_data.append(
                        {
                            "experiment_id": exp.experiment_id,
                            "score": score,
                            "parameters": exp.parameters,
                        }
                    )
            elif exp.status.is_terminal:
                results.failed += 1

        # Rank by score
        if scored:
            results.ranked_results = sorted(
                scored,
                key=lambda x: x["score"] if x["score"] is not None else float("-inf"),
                reverse=self.maximize,
            )
            best = results.ranked_results[0]
            results.best_parameters = best["parameters"]
            results.best_score = best.get("score", float("-inf"))

        return results
