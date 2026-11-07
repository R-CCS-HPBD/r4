"""Contract tests for agent tool playbook discovery surface."""

from r4_contracts.agent_tool_playbook import (
    AGENT_TOOL_PLAYBOOK,
    AGENT_TOOL_PLAYBOOK_SECTIONS,
    AGENT_TOOL_PLAYBOOK_VERSION,
    CATALOG_AGENT_GUIDANCE,
    EXPERIENCE_AGENT_GUIDANCE,
    KNOWLEDGE_AGENT_GUIDANCE,
    KNOWLEDGE_OPERATIONS,
    KNOWLEDGE_TOOL_HELP,
    PLATFORM_GUIDE_TOPICS,
    RESEARCH_LOOP_AGENT_GUIDANCE,
    RESEARCH_LOOP_OPERATIONS,
    RESEARCH_LOOP_TOOL_HELP,
    RESEARCH_SESSION_AGENT_GUIDANCE,
    RESEARCH_SESSION_OPERATIONS,
    RESEARCH_SESSION_TOOL_HELP,
    SPINE_DISAMBIGUATION,
    UMRS_OPS_AGENT_GUIDANCE,
    WORKFLOW_AGENT_GUIDANCE,
    WORKFLOW_AUTHORING_AGENT_GUIDANCE,
    platform_guide,
)
from r4_contracts.workflows import WORKFLOW_OPERATIONS, WORKFLOW_TOOL_HELP


def test_playbook_version_present():
    assert AGENT_TOOL_PLAYBOOK_VERSION


_SECTION_CONSTANTS = {
    "spines": SPINE_DISAMBIGUATION,
    "umrs": UMRS_OPS_AGENT_GUIDANCE,
    "workflow": WORKFLOW_AGENT_GUIDANCE,
    "authoring": WORKFLOW_AUTHORING_AGENT_GUIDANCE,
    "catalog": CATALOG_AGENT_GUIDANCE,
    "loops": RESEARCH_LOOP_AGENT_GUIDANCE,
    "sessions": RESEARCH_SESSION_AGENT_GUIDANCE,
    "knowledge": KNOWLEDGE_AGENT_GUIDANCE,
    "experiences": EXPERIENCE_AGENT_GUIDANCE,
}


def test_section_map_is_single_source():
    for key, constant in _SECTION_CONSTANTS.items():
        assert AGENT_TOOL_PLAYBOOK_SECTIONS[key] is constant, key
    assert "rc1" not in AGENT_TOOL_PLAYBOOK_SECTIONS  # retired 2026-09-07 (WP-07)


def test_playbook_composed_from_sections():
    # The topic tuple IS the composition order (single source; no hand-typed list).
    expected = "\n\n".join(
        AGENT_TOOL_PLAYBOOK_SECTIONS[k].strip() for k in PLATFORM_GUIDE_TOPICS[1:]
    )
    assert expected == AGENT_TOOL_PLAYBOOK
    assert SPINE_DISAMBIGUATION.strip() in AGENT_TOOL_PLAYBOOK


def test_spine_statement_names_one_spine_and_the_retirement():
    assert "One research spine" in SPINE_DISAMBIGUATION
    assert "retired" in SPINE_DISAMBIGUATION
    assert "workflow_name" in RESEARCH_LOOP_AGENT_GUIDANCE
    assert "approvals" in RESEARCH_LOOP_OPERATIONS and "decide" in RESEARCH_LOOP_OPERATIONS


def test_platform_guide_topics():
    assert platform_guide("all") == AGENT_TOOL_PLAYBOOK
    for key, constant in _SECTION_CONSTANTS.items():
        assert platform_guide(key) == constant.strip(), key
    assert set(PLATFORM_GUIDE_TOPICS) == {"all", *_SECTION_CONSTANTS}


def test_platform_guide_rejects_unknown_topic():
    try:
        platform_guide("nope")
    except ValueError as exc:
        assert "Unknown platform_guide topic" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_workflow_tool_help_covers_operations():
    assert WORKFLOW_TOOL_HELP.keys() == set(WORKFLOW_OPERATIONS)


def test_new_family_tool_help_covers_operations():
    assert RESEARCH_LOOP_TOOL_HELP.keys() == set(RESEARCH_LOOP_OPERATIONS)
    assert RESEARCH_SESSION_TOOL_HELP.keys() == set(RESEARCH_SESSION_OPERATIONS)
    assert KNOWLEDGE_TOOL_HELP.keys() == set(KNOWLEDGE_OPERATIONS)


def test_new_family_guidance_in_full_playbook():
    assert RESEARCH_LOOP_AGENT_GUIDANCE.strip() in AGENT_TOOL_PLAYBOOK
    assert RESEARCH_SESSION_AGENT_GUIDANCE.strip() in AGENT_TOOL_PLAYBOOK
    assert KNOWLEDGE_AGENT_GUIDANCE.strip() in AGENT_TOOL_PLAYBOOK


def test_catalog_notes_companion_scope():
    assert "Companion-scoped" in CATALOG_AGENT_GUIDANCE
