"""Tests for r4_contracts.spaces — the interaction model."""

from __future__ import annotations

from r4_contracts.research import PrimitiveKind
from r4_contracts.spaces import (
    SPACE_DEFINITIONS,
    SPACE_TRANSITIONS,
    Space,
    can_transition,
    get_all_spaces,
    get_space_definition,
)


def test_space_enum_values() -> None:
    assert Space.LITERATURE == "literature"
    assert Space.PLANNING == "planning"
    assert Space.EXPERIMENT == "experiment"
    assert Space.HPC == "hpc"
    assert Space.ANALYSIS == "analysis"
    assert Space.WRITING == "writing"
    assert Space.REVIEW == "review"


def test_all_spaces_defined() -> None:
    """Every Space enum value must have a definition."""
    for space in Space:
        assert space in SPACE_DEFINITIONS, f"Space {space} has no definition"


def test_space_definitions_count() -> None:
    """There are exactly 7 spaces."""
    assert len(SPACE_DEFINITIONS) == 7
    assert len(get_all_spaces()) == 7


def test_literature_space() -> None:
    sd = get_space_definition(Space.LITERATURE)
    assert sd.focus_primitive == PrimitiveKind.PUBLICATION
    assert PrimitiveKind.KNOWLEDGE in sd.context_primitives
    assert PrimitiveKind.DATASET in sd.context_primitives
    assert "/cite" in sd.commands
    assert "/survey" in sd.commands


def test_planning_space() -> None:
    sd = get_space_definition(Space.PLANNING)
    assert sd.focus_primitive == PrimitiveKind.HYPOTHESIS
    assert PrimitiveKind.RESEARCH_GOAL in sd.context_primitives
    assert PrimitiveKind.EXPERIMENT in sd.context_primitives
    assert "/hypothesize" in sd.commands
    assert "/design" in sd.commands


def test_experiment_space() -> None:
    sd = get_space_definition(Space.EXPERIMENT)
    assert sd.focus_primitive == PrimitiveKind.WORKFLOW
    assert PrimitiveKind.DATASET in sd.context_primitives
    assert PrimitiveKind.COMPUTE_RESOURCE in sd.context_primitives
    assert "/run" in sd.commands
    assert "/monitor" in sd.commands


def test_hpc_space() -> None:
    sd = get_space_definition(Space.HPC)
    assert sd.focus_primitive == PrimitiveKind.COMPUTE_RESOURCE
    assert PrimitiveKind.WORKFLOW in sd.context_primitives
    assert "/slurm" in sd.commands
    assert "/submit" in sd.commands
    assert "/queue" in sd.commands
    assert "/gpu" in sd.commands


def test_analysis_space() -> None:
    sd = get_space_definition(Space.ANALYSIS)
    assert sd.focus_primitive == PrimitiveKind.EXPERIMENT
    assert PrimitiveKind.HYPOTHESIS in sd.context_primitives
    assert "/analyze" in sd.commands
    assert "/figure" in sd.commands
    assert "/test" in sd.commands


def test_writing_space() -> None:
    sd = get_space_definition(Space.WRITING)
    assert sd.focus_primitive == PrimitiveKind.PUBLICATION
    assert PrimitiveKind.EXPERIMENT in sd.context_primitives
    assert "/draft" in sd.commands
    assert "/insert" in sd.commands
    assert "/publish" in sd.commands


def test_review_space() -> None:
    sd = get_space_definition(Space.REVIEW)
    assert sd.focus_primitive == PrimitiveKind.PUBLICATION
    assert PrimitiveKind.EXPERIMENT in sd.context_primitives
    assert "/review" in sd.commands
    assert "/reproduce" in sd.commands
    assert "/audit" in sd.commands


def test_space_transitions_lifecycle() -> None:
    """Literature → Planning → Experiment → HPC → Analysis → Writing → Review."""
    assert Space.PLANNING in SPACE_TRANSITIONS[Space.LITERATURE]
    assert Space.EXPERIMENT in SPACE_TRANSITIONS[Space.PLANNING]
    assert Space.HPC in SPACE_TRANSITIONS[Space.EXPERIMENT]
    assert Space.ANALYSIS in SPACE_TRANSITIONS[Space.HPC]
    assert Space.WRITING in SPACE_TRANSITIONS[Space.ANALYSIS]
    assert Space.REVIEW in SPACE_TRANSITIONS[Space.WRITING]


def test_can_transition_valid() -> None:
    assert can_transition(Space.LITERATURE, Space.PLANNING)
    assert can_transition(Space.PLANNING, Space.EXPERIMENT)
    assert can_transition(Space.EXPERIMENT, Space.HPC)
    assert can_transition(Space.HPC, Space.ANALYSIS)
    assert can_transition(Space.ANALYSIS, Space.WRITING)
    assert can_transition(Space.WRITING, Space.REVIEW)


def test_can_transition_same_space() -> None:
    """Staying in the same space is always valid."""
    for space in Space:
        assert can_transition(space, space)


def test_can_transition_invalid() -> None:
    """Literature → HPC is not a valid direct transition."""
    assert not can_transition(Space.LITERATURE, Space.HPC)
    assert not can_transition(Space.HPC, Space.WRITING)


def test_space_definitions_have_keybindings() -> None:
    """Every space should have at least one keybinding."""
    for space, sd in SPACE_DEFINITIONS.items():
        assert len(sd.keybindings) > 0, f"Space {space} has no keybindings"


def test_space_definitions_have_commands() -> None:
    """Every space should have at least one command."""
    for space, sd in SPACE_DEFINITIONS.items():
        assert len(sd.commands) > 0, f"Space {space} has no commands"


def test_space_definitions_have_context_primitives() -> None:
    """Every space should have at least one context primitive."""
    for space, sd in SPACE_DEFINITIONS.items():
        assert len(sd.context_primitives) > 0, f"Space {space} has no context primitives"
