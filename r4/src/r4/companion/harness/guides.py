"""Feedforward guides — invisible context assembly for the system prompt.

Implements per-plan layered prompt assembly and research-domain detection.
The researcher never sees these; they simply experience a companion that
is deeply contextual from the first message.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..context import ContextManager
    from ..skills import SkillRegistry

logger = logging.getLogger(__name__)

# Known research domains with vocabulary hints and guidance
DOMAIN_HINTS: dict[str, dict[str, Any]] = {
    "seismology": {
        "keywords": [
            "seismic",
            "earthquake",
            "waveform",
            "specfem",
            "moment tensor",
            "fault",
            "velocity model",
            "tremor",
        ],
        "guidance": (
            "The researcher works in computational seismology. They may use SPECFEM, ObsPy, "
            "or similar tools. Prefer structured data formats (SAC, miniSEED). "
            "Simulations often run on many nodes — be mindful of I/O and parallelism."
        ),
    },
    "materials_science": {
        "keywords": [
            "DFT",
            "VASP",
            "quantum espresso",
            "crystal",
            "lattice",
            "band structure",
            "phonon",
            "MD simulation",
        ],
        "guidance": (
            "The researcher works in computational materials science. "
            "They may use VASP, Quantum ESPRESSO, LAMMPS, or ASE. "
            "Input files are critical — small errors in POSCAR/INCAR cause expensive failures."
        ),
    },
    "bioinformatics": {
        "keywords": [
            "genome",
            "protein",
            "sequence",
            "BLAST",
            "alignment",
            "FASTA",
            "PDB",
            "molecular dynamics",
        ],
        "guidance": (
            "The researcher works in bioinformatics or structural biology. "
            "They may use GROMACS, AMBER, Rosetta, or bioinformatics pipelines. "
            "Be aware of large data files and memory requirements."
        ),
    },
    "climate_science": {
        "keywords": [
            "climate",
            "atmospheric",
            "ocean model",
            "NetCDF",
            "CMIP",
            "reanalysis",
            "ERA5",
            "WRF",
        ],
        "guidance": (
            "The researcher works in climate or earth science. "
            "Data is often in NetCDF/HDF5 format and very large. "
            "Prefer xarray/dask for out-of-core processing."
        ),
    },
    "machine_learning": {
        "keywords": [
            "training",
            "neural network",
            "transformer",
            "dataset",
            "loss",
            "epoch",
            "fine-tune",
            "inference",
            "GPU",
        ],
        "guidance": (
            "The researcher works on machine learning research. "
            "Be mindful of GPU memory, batch sizes, and checkpoint management. "
            "Models should be pre-staged, never downloaded at inference time."
        ),
    },
    "fluid_dynamics": {
        "keywords": [
            "CFD",
            "Navier-Stokes",
            "turbulence",
            "Reynolds",
            "OpenFOAM",
            "mesh",
            "finite element",
            "finite volume",
        ],
        "guidance": (
            "The researcher works in computational fluid dynamics. "
            "They may use OpenFOAM, FEniCS, or custom solvers. "
            "Mesh quality and convergence checks are critical."
        ),
    },
}


class FeedforwardGuides:
    """Assemble context layers for the system prompt.

    Layers (in order of injection):
      1. Base system prompt (handled by ResearchCompanion)
      2. Environment snapshot (handled by EnvironmentProbe)
      3. Active skill prompts (from SkillRegistry -> injected via session)
      4. Session memory (from ContextManager -> injected via session)
      5. Project context (README summary from probe)
      6. Progress state (from ProgressTracker -> injected via harness)
      7. Domain-specific guidance (detected here)
      8. Lessons (from SteeringLoop -> injected via harness)

    This class handles layer 7 (domain detection) and provides utilities
    for layer composition.
    """

    def __init__(
        self,
        *,
        context_manager: "ContextManager | None" = None,
        skill_registry: "SkillRegistry | None" = None,
    ):
        self._context = context_manager
        self._skills = skill_registry
        self._detected_domain: str | None = None

    def detect_domain(self, messages: list) -> str | None:
        """Analyse conversation messages to identify research domain.

        Returns a guidance string for the detected domain, or None.
        """
        if self._detected_domain:
            return DOMAIN_HINTS.get(self._detected_domain, {}).get("guidance")

        # Build text corpus from recent messages
        text_parts: list[str] = []
        for m in messages[-10:]:
            content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
            if content:
                text_parts.append(content.lower())
        text = " ".join(text_parts)

        # Score each domain
        best_domain: str | None = None
        best_score = 0
        for domain, info in DOMAIN_HINTS.items():
            score = sum(1 for kw in info["keywords"] if kw in text)
            if score > best_score:
                best_score = score
                best_domain = domain

        if best_score >= 2:  # Need at least 2 keyword matches
            self._detected_domain = best_domain
            logger.info("Detected research domain: %s (score=%d)", best_domain, best_score)
            return DOMAIN_HINTS[best_domain]["guidance"]

        return None

    def reset_domain(self) -> None:
        """Clear detected domain (e.g., when switching topics)."""
        self._detected_domain = None

    @property
    def detected_domain(self) -> str | None:
        return self._detected_domain
