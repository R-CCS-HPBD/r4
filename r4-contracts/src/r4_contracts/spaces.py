"""Spaces — R4's interaction model.

Spaces are modal working contexts that reshape the workspace. The same
dataset might appear in Literature (as a citation), in Experiment (as
input), in Analysis (as processed output), and in Writing (as a figure).
The primitive is the same; the visualization changes with context.

Each Space has:
  - A focus primitive (what the user is working on)
  - A context set (which other primitives are relevant)
  - A command set (which slash commands are available)
  - A visualization (how primitives are rendered)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .research import PrimitiveKind


class Space(str, Enum):
    """The 7 modal working contexts in R4."""

    LITERATURE = "literature"
    PLANNING = "planning"
    EXPERIMENT = "experiment"
    HPC = "hpc"
    ANALYSIS = "analysis"
    WRITING = "writing"
    REVIEW = "review"


@dataclass(frozen=True)
class SpaceDefinition:
    """Static definition of a Space.

    A Space is a lens through which the research workspace is viewed.
    The focus primitive is the primary object of attention; the context
    primitives are secondary objects that provide context. The command
    set is the set of slash commands available in this Space.
    """

    space: Space
    focus_primitive: PrimitiveKind
    context_primitives: frozenset[PrimitiveKind]
    commands: frozenset[str]
    description: str = ""
    # Keybindings (key → command)
    keybindings: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Space definitions
# ---------------------------------------------------------------------------


_LITERATURE = SpaceDefinition(
    space=Space.LITERATURE,
    focus_primitive=PrimitiveKind.PUBLICATION,
    context_primitives=frozenset(
        {
            PrimitiveKind.KNOWLEDGE,
            PrimitiveKind.DATASET,
            PrimitiveKind.AGENT,
        }
    ),
    commands=frozenset({"/cite", "/survey", "/index", "/search"}),
    description="Survey papers, build knowledge base, cite",
    keybindings={
        "s": "/survey",
        "c": "/cite",
        "i": "/index",
        "/": "/search",
    },
)

_PLANNING = SpaceDefinition(
    space=Space.PLANNING,
    focus_primitive=PrimitiveKind.HYPOTHESIS,
    context_primitives=frozenset(
        {
            PrimitiveKind.RESEARCH_GOAL,
            PrimitiveKind.EXPERIMENT,
            PrimitiveKind.AGENT,
            PrimitiveKind.COMPUTE_RESOURCE,
        }
    ),
    commands=frozenset({"/hypothesize", "/design", "/assign", "/allocate"}),
    description="Form hypotheses, design experiments, allocate agents",
    keybindings={
        "h": "/hypothesize",
        "d": "/design",
        "a": "/assign",
        "l": "/allocate",
    },
)

_EXPERIMENT = SpaceDefinition(
    space=Space.EXPERIMENT,
    focus_primitive=PrimitiveKind.WORKFLOW,
    context_primitives=frozenset(
        {
            PrimitiveKind.DATASET,
            PrimitiveKind.COMPUTE_RESOURCE,
            PrimitiveKind.AGENT,
            PrimitiveKind.HYPOTHESIS,
        }
    ),
    commands=frozenset({"/run", "/monitor", "/evidence", "/pause", "/resume"}),
    description="Run workflows, monitor execution, collect evidence",
    keybindings={
        "r": "/run",
        "m": "/monitor",
        "e": "/evidence",
        "p": "/pause",
        "u": "/resume",
    },
)

_HPC = SpaceDefinition(
    space=Space.HPC,
    focus_primitive=PrimitiveKind.COMPUTE_RESOURCE,
    context_primitives=frozenset(
        {
            PrimitiveKind.WORKFLOW,
            PrimitiveKind.SIMULATION,
        }
    ),
    commands=frozenset({"/slurm", "/submit", "/queue", "/gpu", "/storage", "/jobs"}),
    description="Submit Slurm jobs, monitor queues, GPU utilization",
    keybindings={
        "s": "/slurm",
        "u": "/submit",
        "q": "/queue",
        "g": "/gpu",
        "j": "/jobs",
    },
)

_ANALYSIS = SpaceDefinition(
    space=Space.ANALYSIS,
    focus_primitive=PrimitiveKind.EXPERIMENT,
    context_primitives=frozenset(
        {
            PrimitiveKind.HYPOTHESIS,
            PrimitiveKind.DATASET,
            PrimitiveKind.PUBLICATION,
        }
    ),
    commands=frozenset({"/analyze", "/figure", "/test", "/conclude"}),
    description="Process results, generate figures, test hypotheses",
    keybindings={
        "a": "/analyze",
        "f": "/figure",
        "t": "/test",
        "c": "/conclude",
    },
)

_WRITING = SpaceDefinition(
    space=Space.WRITING,
    focus_primitive=PrimitiveKind.PUBLICATION,
    context_primitives=frozenset(
        {
            PrimitiveKind.EXPERIMENT,
            PrimitiveKind.HYPOTHESIS,
            PrimitiveKind.DATASET,
        }
    ),
    commands=frozenset({"/draft", "/insert", "/collaborate", "/publish"}),
    description="Draft publications, insert provenance, collaborate",
    keybindings={
        "d": "/draft",
        "i": "/insert",
        "c": "/collaborate",
        "p": "/publish",
    },
)

_REVIEW = SpaceDefinition(
    space=Space.REVIEW,
    focus_primitive=PrimitiveKind.PUBLICATION,
    context_primitives=frozenset(
        {
            PrimitiveKind.EXPERIMENT,
            PrimitiveKind.HYPOTHESIS,
            PrimitiveKind.WORKFLOW,
            PrimitiveKind.DATASET,
        }
    ),
    commands=frozenset({"/review", "/reproduce", "/audit", "/lineage"}),
    description="Peer review, reproduce experiments, audit lineage",
    keybindings={
        "r": "/review",
        "p": "/reproduce",
        "a": "/audit",
        "l": "/lineage",
    },
)


# ---------------------------------------------------------------------------
# Space registry
# ---------------------------------------------------------------------------


SPACE_DEFINITIONS: dict[Space, SpaceDefinition] = {
    Space.LITERATURE: _LITERATURE,
    Space.PLANNING: _PLANNING,
    Space.EXPERIMENT: _EXPERIMENT,
    Space.HPC: _HPC,
    Space.ANALYSIS: _ANALYSIS,
    Space.WRITING: _WRITING,
    Space.REVIEW: _REVIEW,
}


# ---------------------------------------------------------------------------
# Space transitions — the research lifecycle
# ---------------------------------------------------------------------------


# Valid space transitions (the research lifecycle)
SPACE_TRANSITIONS: dict[Space, list[Space]] = {
    Space.LITERATURE: [Space.PLANNING],
    Space.PLANNING: [Space.EXPERIMENT, Space.HPC],
    Space.EXPERIMENT: [Space.HPC, Space.ANALYSIS],
    Space.HPC: [Space.EXPERIMENT, Space.ANALYSIS],
    Space.ANALYSIS: [Space.WRITING, Space.PLANNING],
    Space.WRITING: [Space.REVIEW],
    Space.REVIEW: [Space.WRITING, Space.LITERATURE],
}


def can_transition(from_space: Space, to_space: Space) -> bool:
    """Check whether a transition between two spaces is valid."""
    if from_space == to_space:
        return True
    return to_space in SPACE_TRANSITIONS.get(from_space, [])


def get_space_definition(space: Space) -> SpaceDefinition:
    """Get the definition for a space."""
    return SPACE_DEFINITIONS[space]


def get_all_spaces() -> list[Space]:
    """Get all spaces in lifecycle order."""
    return [
        Space.LITERATURE,
        Space.PLANNING,
        Space.EXPERIMENT,
        Space.HPC,
        Space.ANALYSIS,
        Space.WRITING,
        Space.REVIEW,
    ]
