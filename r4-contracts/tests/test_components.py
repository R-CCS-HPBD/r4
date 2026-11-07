"""Tests for r4_contracts.components — design system component specs."""

from __future__ import annotations

from r4_contracts.components import (
    AGENT_CARD_SPEC,
    ANALYSIS_DASHBOARD_SPEC,
    COMMAND_PALETTE_SPEC,
    COMPUTE_RESOURCE_CARD_SPEC,
    CONVERSATION_VIEW_SPEC,
    DATASET_BROWSER_SPEC,
    DIALOG_SPEC,
    EXPERIMENT_CARD_SPEC,
    EXPERIMENT_RUNNER_SPEC,
    FILTER_BAR_SPEC,
    FLOATING_PANEL_SPEC,
    HEADER_SPEC,
    HPC_MONITOR_SPEC,
    HYPOTHESIS_CARD_SPEC,
    INSPECTOR_SPEC,
    KNOWLEDGE_PANEL_SPEC,
    LITERATURE_SURVEY_VIEW_SPEC,
    NAVIGATION_TREE_SPEC,
    NOTEBOOK_VIEWER_SPEC,
    NOTIFICATION_CENTER_SPEC,
    PLANNING_CANVAS_SPEC,
    PUBLICATION_VIEWER_SPEC,
    RESEARCH_GOAL_CARD_SPEC,
    RESEARCH_PROJECT_CARD_SPEC,
    REVIEW_CHECKLIST_SPEC,
    SEARCH_BAR_SPEC,
    SLASH_COMMAND_BAR_SPEC,
    SPLIT_PANE_SPEC,
    STATUS_BAR_SPEC,
    TOAST_SPEC,
    WORKFLOW_GRAPH_SPEC,
    WORKFLOW_TIMELINE_SPEC,
    WORKSPACE_SPEC,
    WRITING_EDITOR_SPEC,
    ComponentKind,
    get_all_component_specs,
    get_component_count,
    get_component_spec,
    get_components_by_category,
    get_components_for_primitive,
    get_components_for_space,
)
from r4_contracts.research import PrimitiveKind
from r4_contracts.spaces import Space

# ---------------------------------------------------------------------------
# Component kind enum
# ---------------------------------------------------------------------------


def test_component_kind_count() -> None:
    """There are exactly 34 component kinds."""
    assert len(list(ComponentKind)) == 34


def test_component_kind_values() -> None:
    assert ComponentKind.WORKSPACE == "workspace"
    assert ComponentKind.NAVIGATION_TREE == "navigation_tree"
    assert ComponentKind.AGENT_CARD == "agent_card"
    assert ComponentKind.HPC_MONITOR == "hpc_monitor"


# ---------------------------------------------------------------------------
# Component specs exist for all kinds
# ---------------------------------------------------------------------------


def test_all_kinds_have_specs() -> None:
    """Every ComponentKind must have a spec in COMPONENT_SPECS."""
    from r4_contracts.components import COMPONENT_SPECS

    for kind in ComponentKind:
        assert kind in COMPONENT_SPECS, f"Kind {kind} missing from registry"


def test_component_count() -> None:
    assert get_component_count() == 34


# ---------------------------------------------------------------------------
# Layout component specs
# ---------------------------------------------------------------------------


def test_workspace_spec() -> None:
    assert WORKSPACE_SPEC.name == "Workspace"
    assert WORKSPACE_SPEC.category == "layout"
    assert len(WORKSPACE_SPEC.props) >= 5


def test_navigation_tree_spec() -> None:
    assert NAVIGATION_TREE_SPEC.name == "NavigationTree"
    assert NAVIGATION_TREE_SPEC.category == "layout"
    assert "tree" in NAVIGATION_TREE_SPEC.props
    assert "selected_node" in NAVIGATION_TREE_SPEC.props


def test_status_bar_spec() -> None:
    assert STATUS_BAR_SPEC.name == "StatusBar"
    assert STATUS_BAR_SPEC.category == "layout"
    assert "current_space" in STATUS_BAR_SPEC.props
    assert "gpu_utilization" in STATUS_BAR_SPEC.props
    assert "slurm_queue" in STATUS_BAR_SPEC.props


def test_header_spec() -> None:
    assert HEADER_SPEC.name == "Header"
    assert HEADER_SPEC.category == "layout"
    assert "project_name" in HEADER_SPEC.props
    assert "gateway_connected" in HEADER_SPEC.props


def test_split_pane_spec() -> None:
    assert SPLIT_PANE_SPEC.name == "SplitPane"
    assert SPLIT_PANE_SPEC.category == "layout"
    assert "split_ratio" in SPLIT_PANE_SPEC.props


def test_floating_panel_spec() -> None:
    assert FLOATING_PANEL_SPEC.name == "FloatingPanel"
    assert FLOATING_PANEL_SPEC.category == "layout"
    assert "visible" in FLOATING_PANEL_SPEC.props


# ---------------------------------------------------------------------------
# Research primitive visualization specs
# ---------------------------------------------------------------------------


def test_research_project_card_spec() -> None:
    assert RESEARCH_PROJECT_CARD_SPEC.name == "ResearchProjectCard"
    assert RESEARCH_PROJECT_CARD_SPEC.category == "primitive"
    assert PrimitiveKind.RESEARCH_PROJECT in RESEARCH_PROJECT_CARD_SPEC.primitives


def test_research_goal_card_spec() -> None:
    assert RESEARCH_GOAL_CARD_SPEC.name == "ResearchGoalCard"
    assert PrimitiveKind.RESEARCH_GOAL in RESEARCH_GOAL_CARD_SPEC.primitives


def test_hypothesis_card_spec() -> None:
    assert HYPOTHESIS_CARD_SPEC.name == "HypothesisCard"
    assert PrimitiveKind.HYPOTHESIS in HYPOTHESIS_CARD_SPEC.primitives
    assert "show_confidence" in HYPOTHESIS_CARD_SPEC.props
    assert "show_evidence" in HYPOTHESIS_CARD_SPEC.props


def test_experiment_card_spec() -> None:
    assert EXPERIMENT_CARD_SPEC.name == "ExperimentCard"
    assert PrimitiveKind.EXPERIMENT in EXPERIMENT_CARD_SPEC.primitives
    assert "show_protocol" in EXPERIMENT_CARD_SPEC.props
    assert "show_results" in EXPERIMENT_CARD_SPEC.props


def test_workflow_graph_spec() -> None:
    assert WORKFLOW_GRAPH_SPEC.name == "WorkflowGraph"
    assert PrimitiveKind.WORKFLOW in WORKFLOW_GRAPH_SPEC.primitives
    assert "show_steps" in WORKFLOW_GRAPH_SPEC.props
    assert "show_status" in WORKFLOW_GRAPH_SPEC.props


def test_workflow_timeline_spec() -> None:
    assert WORKFLOW_TIMELINE_SPEC.name == "WorkflowTimeline"
    assert PrimitiveKind.WORKFLOW in WORKFLOW_TIMELINE_SPEC.primitives
    assert "show_durations" in WORKFLOW_TIMELINE_SPEC.props


def test_knowledge_panel_spec() -> None:
    assert KNOWLEDGE_PANEL_SPEC.name == "KnowledgePanel"
    assert PrimitiveKind.KNOWLEDGE in KNOWLEDGE_PANEL_SPEC.primitives
    assert "search_query" in KNOWLEDGE_PANEL_SPEC.props


def test_dataset_browser_spec() -> None:
    assert DATASET_BROWSER_SPEC.name == "DatasetBrowser"
    assert PrimitiveKind.DATASET in DATASET_BROWSER_SPEC.primitives
    assert "show_upload" in DATASET_BROWSER_SPEC.props


def test_publication_viewer_spec() -> None:
    assert PUBLICATION_VIEWER_SPEC.name == "PublicationViewer"
    assert PrimitiveKind.PUBLICATION in PUBLICATION_VIEWER_SPEC.primitives
    assert "show_provenance" in PUBLICATION_VIEWER_SPEC.props


def test_notebook_viewer_spec() -> None:
    assert NOTEBOOK_VIEWER_SPEC.name == "NotebookViewer"
    assert PrimitiveKind.NOTEBOOK in NOTEBOOK_VIEWER_SPEC.primitives
    assert "show_cells" in NOTEBOOK_VIEWER_SPEC.props


def test_compute_resource_card_spec() -> None:
    assert COMPUTE_RESOURCE_CARD_SPEC.name == "ComputeResourceCard"
    assert PrimitiveKind.COMPUTE_RESOURCE in COMPUTE_RESOURCE_CARD_SPEC.primitives
    assert "show_gpus" in COMPUTE_RESOURCE_CARD_SPEC.props


def test_agent_card_spec() -> None:
    assert AGENT_CARD_SPEC.name == "AgentCard"
    assert PrimitiveKind.AGENT in AGENT_CARD_SPEC.primitives
    assert "show_reasoning" in AGENT_CARD_SPEC.props
    assert "show_memory" in AGENT_CARD_SPEC.props


def test_conversation_view_spec() -> None:
    assert CONVERSATION_VIEW_SPEC.name == "ConversationView"
    assert PrimitiveKind.CONVERSATION in CONVERSATION_VIEW_SPEC.primitives
    assert "show_messages" in CONVERSATION_VIEW_SPEC.props


# ---------------------------------------------------------------------------
# Interaction component specs
# ---------------------------------------------------------------------------


def test_command_palette_spec() -> None:
    assert COMMAND_PALETTE_SPEC.name == "CommandPalette"
    assert COMMAND_PALETTE_SPEC.category == "interaction"
    assert "commands" in COMMAND_PALETTE_SPEC.props
    assert "query" in COMMAND_PALETTE_SPEC.props


def test_slash_command_bar_spec() -> None:
    assert SLASH_COMMAND_BAR_SPEC.name == "SlashCommandBar"
    assert SLASH_COMMAND_BAR_SPEC.category == "interaction"
    assert "suggestions" in SLASH_COMMAND_BAR_SPEC.props


def test_notification_center_spec() -> None:
    assert NOTIFICATION_CENTER_SPEC.name == "NotificationCenter"
    assert NOTIFICATION_CENTER_SPEC.category == "interaction"
    assert "unread_count" in NOTIFICATION_CENTER_SPEC.props


def test_inspector_spec() -> None:
    assert INSPECTOR_SPEC.name == "Inspector"
    assert INSPECTOR_SPEC.category == "interaction"
    assert "show_provenance" in INSPECTOR_SPEC.props


def test_dialog_spec() -> None:
    assert DIALOG_SPEC.name == "Dialog"
    assert DIALOG_SPEC.category == "interaction"
    assert "title" in DIALOG_SPEC.props
    assert "message" in DIALOG_SPEC.props


def test_toast_spec() -> None:
    assert TOAST_SPEC.name == "Toast"
    assert TOAST_SPEC.category == "interaction"
    assert "duration" in TOAST_SPEC.props


def test_filter_bar_spec() -> None:
    assert FILTER_BAR_SPEC.name == "FilterBar"
    assert FILTER_BAR_SPEC.category == "interaction"
    assert "active_filters" in FILTER_BAR_SPEC.props


def test_search_bar_spec() -> None:
    assert SEARCH_BAR_SPEC.name == "SearchBar"
    assert SEARCH_BAR_SPEC.category == "interaction"
    assert "results" in SEARCH_BAR_SPEC.props


# ---------------------------------------------------------------------------
# Space-specific component specs
# ---------------------------------------------------------------------------


def test_literature_survey_view_spec() -> None:
    assert LITERATURE_SURVEY_VIEW_SPEC.name == "LiteratureSurveyView"
    assert LITERATURE_SURVEY_VIEW_SPEC.category == "space"
    assert Space.LITERATURE in LITERATURE_SURVEY_VIEW_SPEC.spaces


def test_planning_canvas_spec() -> None:
    assert PLANNING_CANVAS_SPEC.name == "PlanningCanvas"
    assert PLANNING_CANVAS_SPEC.category == "space"
    assert Space.PLANNING in PLANNING_CANVAS_SPEC.spaces


def test_experiment_runner_spec() -> None:
    assert EXPERIMENT_RUNNER_SPEC.name == "ExperimentRunner"
    assert EXPERIMENT_RUNNER_SPEC.category == "space"
    assert Space.EXPERIMENT in EXPERIMENT_RUNNER_SPEC.spaces


def test_hpc_monitor_spec() -> None:
    assert HPC_MONITOR_SPEC.name == "HPCMonitor"
    assert HPC_MONITOR_SPEC.category == "space"
    assert Space.HPC in HPC_MONITOR_SPEC.spaces


def test_analysis_dashboard_spec() -> None:
    assert ANALYSIS_DASHBOARD_SPEC.name == "AnalysisDashboard"
    assert ANALYSIS_DASHBOARD_SPEC.category == "space"
    assert Space.ANALYSIS in ANALYSIS_DASHBOARD_SPEC.spaces


def test_writing_editor_spec() -> None:
    assert WRITING_EDITOR_SPEC.name == "WritingEditor"
    assert WRITING_EDITOR_SPEC.category == "space"
    assert Space.WRITING in WRITING_EDITOR_SPEC.spaces


def test_review_checklist_spec() -> None:
    assert REVIEW_CHECKLIST_SPEC.name == "ReviewChecklist"
    assert REVIEW_CHECKLIST_SPEC.category == "space"
    assert Space.REVIEW in REVIEW_CHECKLIST_SPEC.spaces


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


def test_get_component_spec() -> None:
    spec = get_component_spec(ComponentKind.AGENT_CARD)
    assert spec.name == "AgentCard"


def test_get_components_for_space() -> None:
    """Get components available in the HPC space."""
    hpc_components = get_components_for_space(Space.HPC)
    assert len(hpc_components) > 0
    # AgentCard should be available in all spaces
    agent_specs = [s for s in hpc_components if s.kind == ComponentKind.AGENT_CARD]
    assert len(agent_specs) == 1


def test_get_components_for_primitive() -> None:
    """Get components that visualize the AGENT primitive."""
    agent_components = get_components_for_primitive(PrimitiveKind.AGENT)
    assert len(agent_components) > 0
    # AgentCard should be among them
    agent_card = [s for s in agent_components if s.kind == ComponentKind.AGENT_CARD]
    assert len(agent_card) == 1


def test_get_components_by_category() -> None:
    layout_components = get_components_by_category("layout")
    assert len(layout_components) == 6  # 6 layout components

    primitive_components = get_components_by_category("primitive")
    assert len(primitive_components) == 13  # 13 primitive visualizations

    interaction_components = get_components_by_category("interaction")
    assert len(interaction_components) == 8  # 8 interaction components

    space_components = get_components_by_category("space")
    assert len(space_components) == 7  # 7 space-specific components


def test_get_all_component_specs() -> None:
    all_specs = get_all_component_specs()
    assert len(all_specs) == 34


# ---------------------------------------------------------------------------
# Component spec invariants
# ---------------------------------------------------------------------------


def test_all_specs_have_name() -> None:
    """Every component spec must have a non-empty name."""
    for spec in get_all_component_specs():
        assert spec.name, f"Component {spec.kind} has no name"


def test_all_specs_have_description() -> None:
    """Every component spec must have a non-empty description."""
    for spec in get_all_component_specs():
        assert spec.description, f"Component {spec.kind} has no description"


def test_all_specs_have_category() -> None:
    """Every component spec must have a valid category."""
    valid_categories = {"layout", "primitive", "interaction", "space"}
    for spec in get_all_component_specs():
        assert spec.category in valid_categories, (
            f"Component {spec.kind} has invalid category: {spec.category}"
        )


def test_all_specs_have_props() -> None:
    """Every component spec must have at least one prop."""
    for spec in get_all_component_specs():
        assert len(spec.props) > 0, f"Component {spec.kind} has no props"


def test_all_specs_have_events() -> None:
    """Most component specs should have at least one event."""
    specs_without_events = [s for s in get_all_component_specs() if len(s.events) == 0]
    # Allow components with no events (e.g., pure display components)
    # but log a warning
    assert len(specs_without_events) <= 5, (
        f"Too many components ({len(specs_without_events)}) have no events"
    )


def test_all_specs_belong_to_at_least_one_space() -> None:
    """Every component spec must belong to at least one Space."""
    for spec in get_all_component_specs():
        assert len(spec.spaces) > 0, f"Component {spec.kind} belongs to no space"
