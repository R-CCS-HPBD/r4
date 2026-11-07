"""Science — shared contracts layer for autonomous science.

Pattern: r4-contracts/ (stdlib-only, no external dependencies)

This package defines the core scientific objects used by the AI4S
autonomous science loop:

- ScientificReward: multi-class reward function
- Experiment: first-class object with execute() method
- ScienceDomain: pluggable domain interface (Protocol)

See: docs/autonomous-science/PLAN.md
See: docs/autonomous-science/DEVELOPMENT_PLAN.md
"""

from science.domain import Constraints, ResearchContext, ScienceDomain
from science.experiment import Experiment, ExperimentResults, ExperimentStatus
from science.reward import ScientificReward

__all__ = [
    "Constraints",
    "Experiment",
    "ExperimentResults",
    "ExperimentStatus",
    "ResearchContext",
    "ScienceDomain",
    "ScientificReward",
]
