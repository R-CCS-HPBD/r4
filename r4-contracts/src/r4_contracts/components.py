"""Components — R4's design system component specifications.

This module defines the component contracts for the R4 TUI design system.
Each component specification describes:

  - What the component renders
  - What props/attributes it accepts
  - What events it emits
  - Which research primitive(s) it visualizes
  - Which Space(s) it belongs to

These are pure data specifications — no rendering, no I/O. The actual
implementation (Textual, OpenTUI+SolidJS, or other) reads these specs
and implements the rendering.

All types are stdlib-only ``@dataclass``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .research import PrimitiveKind
from .spaces import Space

# ---------------------------------------------------------------------------
# Component kinds
# ---------------------------------------------------------------------------


class ComponentKind(str, Enum):
    """Kind discriminator for design system components."""

    # Layout components
    WORKSPACE = "workspace"
    NAVIGATION_TREE = "navigation_tree"
    STATUS_BAR = "status_bar"
    HEADER = "header"
    SPLIT_PANE = "split_pane"
    FLOATING_PANEL = "floating_panel"

    # Research primitive visualizations
    RESEARCH_PROJECT_CARD = "research_project_card"
    RESEARCH_GOAL_CARD = "research_goal_card"
    HYPOTHESIS_CARD = "hypothesis_card"
    EXPERIMENT_CARD = "experiment_card"
    WORKFLOW_GRAPH = "workflow_graph"
    WORKFLOW_TIMELINE = "workflow_timeline"
    KNOWLEDGE_PANEL = "knowledge_panel"
    DATASET_BROWSER = "dataset_browser"
    PUBLICATION_VIEWER = "publication_viewer"
    NOTEBOOK_VIEWER = "notebook_viewer"
    COMPUTE_RESOURCE_CARD = "compute_resource_card"
    AGENT_CARD = "agent_card"
    CONVERSATION_VIEW = "conversation_view"

    # Interaction components
    COMMAND_PALETTE = "command_palette"
    SLASH_COMMAND_BAR = "slash_command_bar"
    NOTIFICATION_CENTER = "notification_center"
    INSPECTOR = "inspector"
    DIALOG = "dialog"
    TOAST = "toast"
    FILTER_BAR = "filter_bar"
    SEARCH_BAR = "search_bar"

    # Space-specific components
    LITERATURE_SURVEY_VIEW = "literature_survey_view"
    PLANNING_CANVAS = "planning_canvas"
    EXPERIMENT_RUNNER = "experiment_runner"
    HPC_MONITOR = "hpc_monitor"
    ANALYSIS_DASHBOARD = "analysis_dashboard"
    WRITING_EDITOR = "writing_editor"
    REVIEW_CHECKLIST = "review_checklist"


# ---------------------------------------------------------------------------
# Component specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComponentSpec:
    """Specification for a design system component.

    Attributes:
        kind: The ComponentKind
        name: Human-readable name
        description: What the component does
        primitives: Which research primitives this component visualizes
        spaces: Which Spaces this component belongs to
        props: List of prop names the component accepts
        events: List of event names the component emits
        category: Component category (layout, primitive, interaction, space)
    """

    kind: ComponentKind
    name: str
    description: str
    primitives: frozenset[PrimitiveKind]
    spaces: frozenset[Space]
    props: frozenset[str]
    events: frozenset[str]
    category: str = "layout"


# ---------------------------------------------------------------------------
# Layout components
# ---------------------------------------------------------------------------


WORKSPACE_SPEC = ComponentSpec(
    kind=ComponentKind.WORKSPACE,
    name="Workspace",
    description="Root layout container for the R4 TUI",
    primitives=frozenset(),
    spaces=frozenset(Space),  # All spaces
    props=frozenset({"current_space", "project", "nav_tree", "agents", "notifications"}),
    events=frozenset({"space_changed", "project_loaded", "agent_updated"}),
    category="layout",
)

NAVIGATION_TREE_SPEC = ComponentSpec(
    kind=ComponentKind.NAVIGATION_TREE,
    name="NavigationTree",
    description="Hierarchical project navigation (K9s-inspired)",
    primitives=frozenset(PrimitiveKind),  # All primitives
    spaces=frozenset(Space),  # All spaces
    props=frozenset({"tree", "selected_node", "expanded_nodes", "filter"}),
    events=frozenset({"node_selected", "node_expanded", "node_collapsed"}),
    category="layout",
)

STATUS_BAR_SPEC = ComponentSpec(
    kind=ComponentKind.STATUS_BAR,
    name="StatusBar",
    description="Bottom status bar with Space, Agent Team, GPU, Slurm",
    primitives=frozenset(
        {
            PrimitiveKind.COMPUTE_RESOURCE,
            PrimitiveKind.AGENT,
            PrimitiveKind.WORKFLOW,
        }
    ),
    spaces=frozenset(Space),  # All spaces
    props=frozenset(
        {
            "current_space",
            "agent_team_status",
            "gpu_utilization",
            "slurm_queue",
            "notifications_count",
        }
    ),
    events=frozenset({"status_clicked"}),
    category="layout",
)

HEADER_SPEC = ComponentSpec(
    kind=ComponentKind.HEADER,
    name="Header",
    description="Top header with project, user, connection status",
    primitives=frozenset({PrimitiveKind.RESEARCH_PROJECT}),
    spaces=frozenset(Space),  # All spaces
    props=frozenset(
        {
            "project_name",
            "user_name",
            "gateway_connected",
            "umrs_connected",
            "hpc_connected",
        }
    ),
    events=frozenset({"header_clicked"}),
    category="layout",
)

SPLIT_PANE_SPEC = ComponentSpec(
    kind=ComponentKind.SPLIT_PANE,
    name="SplitPane",
    description="Resizable split pane",
    primitives=frozenset(),
    spaces=frozenset(Space),
    props=frozenset({"left", "right", "split_ratio", "orientation"}),
    events=frozenset({"split_changed"}),
    category="layout",
)

FLOATING_PANEL_SPEC = ComponentSpec(
    kind=ComponentKind.FLOATING_PANEL,
    name="FloatingPanel",
    description="Floating panel overlay",
    primitives=frozenset(),
    spaces=frozenset(Space),
    props=frozenset({"content", "position", "width", "height", "visible"}),
    events=frozenset({"panel_closed", "panel_moved"}),
    category="layout",
)


# ---------------------------------------------------------------------------
# Research primitive visualizations
# ---------------------------------------------------------------------------


RESEARCH_PROJECT_CARD_SPEC = ComponentSpec(
    kind=ComponentKind.RESEARCH_PROJECT_CARD,
    name="ResearchProjectCard",
    description="Project summary with goals, hypotheses, experiments",
    primitives=frozenset({PrimitiveKind.RESEARCH_PROJECT}),
    spaces=frozenset(Space),
    props=frozenset({"project", "show_goals", "show_hypotheses", "show_experiments"}),
    events=frozenset({"project_clicked", "goal_clicked", "hypothesis_clicked"}),
    category="primitive",
)

RESEARCH_GOAL_CARD_SPEC = ComponentSpec(
    kind=ComponentKind.RESEARCH_GOAL_CARD,
    name="ResearchGoalCard",
    description="Goal statement with linked hypotheses",
    primitives=frozenset({PrimitiveKind.RESEARCH_GOAL}),
    spaces=frozenset({Space.PLANNING, Space.REVIEW}),
    props=frozenset({"goal", "hypothesis_ids", "show_progress"}),
    events=frozenset({"goal_clicked", "hypothesis_clicked"}),
    category="primitive",
)

HYPOTHESIS_CARD_SPEC = ComponentSpec(
    kind=ComponentKind.HYPOTHESIS_CARD,
    name="HypothesisCard",
    description="Hypothesis statement with status, confidence, evidence",
    primitives=frozenset({PrimitiveKind.HYPOTHESIS}),
    spaces=frozenset({Space.PLANNING, Space.EXPERIMENT, Space.ANALYSIS, Space.REVIEW}),
    props=frozenset({"hypothesis", "show_evidence", "show_confidence", "show_status"}),
    events=frozenset({"hypothesis_clicked", "evidence_clicked"}),
    category="primitive",
)

EXPERIMENT_CARD_SPEC = ComponentSpec(
    kind=ComponentKind.EXPERIMENT_CARD,
    name="ExperimentCard",
    description="Experiment summary with protocol, status, results",
    primitives=frozenset({PrimitiveKind.EXPERIMENT}),
    spaces=frozenset({Space.PLANNING, Space.EXPERIMENT, Space.ANALYSIS, Space.REVIEW}),
    props=frozenset({"experiment", "show_protocol", "show_results", "show_status"}),
    events=frozenset({"experiment_clicked", "result_clicked"}),
    category="primitive",
)

WORKFLOW_GRAPH_SPEC = ComponentSpec(
    kind=ComponentKind.WORKFLOW_GRAPH,
    name="WorkflowGraph",
    description="DAG visualization with step status, output",
    primitives=frozenset({PrimitiveKind.WORKFLOW}),
    spaces=frozenset({Space.EXPERIMENT, Space.HPC, Space.ANALYSIS}),
    props=frozenset({"workflow", "show_steps", "show_status", "show_outputs", "layout"}),
    events=frozenset({"step_clicked", "step_hover", "workflow_clicked"}),
    category="primitive",
)

WORKFLOW_TIMELINE_SPEC = ComponentSpec(
    kind=ComponentKind.WORKFLOW_TIMELINE,
    name="WorkflowTimeline",
    description="Timeline of workflow steps (start, end, duration)",
    primitives=frozenset({PrimitiveKind.WORKFLOW}),
    spaces=frozenset({Space.EXPERIMENT, Space.HPC, Space.ANALYSIS}),
    props=frozenset({"workflow", "show_durations", "show_status", "zoom_level"}),
    events=frozenset({"step_clicked", "timeline_scrolled"}),
    category="primitive",
)

KNOWLEDGE_PANEL_SPEC = ComponentSpec(
    kind=ComponentKind.KNOWLEDGE_PANEL,
    name="KnowledgePanel",
    description="RAG index status, recent additions, search",
    primitives=frozenset({PrimitiveKind.KNOWLEDGE}),
    spaces=frozenset({Space.LITERATURE, Space.PLANNING, Space.ANALYSIS, Space.WRITING}),
    props=frozenset(
        {
            "knowledge",
            "show_recent",
            "show_status",
            "search_query",
            "show_search",
        }
    ),
    events=frozenset({"knowledge_clicked", "search_submitted", "result_clicked"}),
    category="primitive",
)

DATASET_BROWSER_SPEC = ComponentSpec(
    kind=ComponentKind.DATASET_BROWSER,
    name="DatasetBrowser",
    description="Dataset listing with metadata, preview, upload",
    primitives=frozenset({PrimitiveKind.DATASET}),
    spaces=frozenset({Space.LITERATURE, Space.EXPERIMENT, Space.HPC, Space.ANALYSIS}),
    props=frozenset(
        {
            "datasets",
            "show_metadata",
            "show_preview",
            "show_upload",
            "filter",
        }
    ),
    events=frozenset({"dataset_clicked", "upload_clicked", "preview_clicked"}),
    category="primitive",
)

PUBLICATION_VIEWER_SPEC = ComponentSpec(
    kind=ComponentKind.PUBLICATION_VIEWER,
    name="PublicationViewer",
    description="Publication rendering with provenance, figures",
    primitives=frozenset({PrimitiveKind.PUBLICATION}),
    spaces=frozenset({Space.LITERATURE, Space.WRITING, Space.REVIEW}),
    props=frozenset(
        {
            "publication",
            "show_provenance",
            "show_figures",
            "show_authors",
            "render_mode",
        }
    ),
    events=frozenset({"publication_clicked", "figure_clicked", "provenance_clicked"}),
    category="primitive",
)

NOTEBOOK_VIEWER_SPEC = ComponentSpec(
    kind=ComponentKind.NOTEBOOK_VIEWER,
    name="NotebookViewer",
    description="Notebook rendering with cell execution, outputs",
    primitives=frozenset({PrimitiveKind.NOTEBOOK}),
    spaces=frozenset({Space.EXPERIMENT, Space.HPC, Space.ANALYSIS, Space.WRITING}),
    props=frozenset({"notebook", "show_cells", "show_outputs", "show_execution_count"}),
    events=frozenset({"cell_clicked", "cell_executed", "output_clicked"}),
    category="primitive",
)

COMPUTE_RESOURCE_CARD_SPEC = ComponentSpec(
    kind=ComponentKind.COMPUTE_RESOURCE_CARD,
    name="ComputeResourceCard",
    description="Compute allocation summary (Slurm partition, GPUs, memory)",
    primitives=frozenset({PrimitiveKind.COMPUTE_RESOURCE}),
    spaces=frozenset({Space.EXPERIMENT, Space.HPC, Space.ANALYSIS}),
    props=frozenset(
        {
            "compute_resource",
            "show_partition",
            "show_gpus",
            "show_memory",
            "show_wall_time",
        }
    ),
    events=frozenset({"resource_clicked", "allocate_clicked"}),
    category="primitive",
)

AGENT_CARD_SPEC = ComponentSpec(
    kind=ComponentKind.AGENT_CARD,
    name="AgentCard",
    description="Agent status, current task, reasoning, tools, memory",
    primitives=frozenset({PrimitiveKind.AGENT}),
    spaces=frozenset(Space),
    props=frozenset(
        {
            "agent",
            "show_status",
            "show_task",
            "show_reasoning",
            "show_tools",
            "show_memory",
        }
    ),
    events=frozenset({"agent_clicked", "task_clicked", "reasoning_clicked"}),
    category="primitive",
)

CONVERSATION_VIEW_SPEC = ComponentSpec(
    kind=ComponentKind.CONVERSATION_VIEW,
    name="ConversationView",
    description="Chat-like view for human-agent interaction",
    primitives=frozenset({PrimitiveKind.CONVERSATION}),
    spaces=frozenset(Space),
    props=frozenset({"conversation", "show_messages", "show_timestamps", "show_roles"}),
    events=frozenset({"message_sent", "message_clicked", "conversation_scrolled"}),
    category="primitive",
)


# ---------------------------------------------------------------------------
# Interaction components
# ---------------------------------------------------------------------------


COMMAND_PALETTE_SPEC = ComponentSpec(
    kind=ComponentKind.COMMAND_PALETTE,
    name="CommandPalette",
    description="Fuzzy-searchable command palette (OpenCode-inspired)",
    primitives=frozenset(),
    spaces=frozenset(Space),
    props=frozenset({"commands", "query", "selected_index", "visible"}),
    events=frozenset({"command_selected", "palette_closed", "query_changed"}),
    category="interaction",
)

SLASH_COMMAND_BAR_SPEC = ComponentSpec(
    kind=ComponentKind.SLASH_COMMAND_BAR,
    name="SlashCommandBar",
    description="Slash command input bar with R4 commands",
    primitives=frozenset(),
    spaces=frozenset(Space),
    props=frozenset({"query", "suggestions", "selected_suggestion", "visible"}),
    events=frozenset({"command_submitted", "suggestion_selected", "bar_closed"}),
    category="interaction",
)

NOTIFICATION_CENTER_SPEC = ComponentSpec(
    kind=ComponentKind.NOTIFICATION_CENTER,
    name="NotificationCenter",
    description="Notification center with HPC job, workflow, agent completion",
    primitives=frozenset(),
    spaces=frozenset(Space),
    props=frozenset({"notifications", "unread_count", "filter", "visible"}),
    events=frozenset({"notification_clicked", "notification_dismissed", "center_closed"}),
    category="interaction",
)

INSPECTOR_SPEC = ComponentSpec(
    kind=ComponentKind.INSPECTOR,
    name="Inspector",
    description="Resource inspector panel (K9s-inspired)",
    primitives=frozenset(PrimitiveKind),
    spaces=frozenset(Space),
    props=frozenset({"resource", "resource_kind", "show_provenance", "show_metadata"}),
    events=frozenset({"inspector_closed", "provenance_clicked", "metadata_clicked"}),
    category="interaction",
)

DIALOG_SPEC = ComponentSpec(
    kind=ComponentKind.DIALOG,
    name="Dialog",
    description="Modal dialog (alert, confirm, prompt, select)",
    primitives=frozenset(),
    spaces=frozenset(Space),
    props=frozenset({"title", "message", "kind", "buttons", "visible"}),
    events=frozenset({"dialog_confirmed", "dialog_cancelled", "dialog_closed"}),
    category="interaction",
)

TOAST_SPEC = ComponentSpec(
    kind=ComponentKind.TOAST,
    name="Toast",
    description="Toast notification",
    primitives=frozenset(),
    spaces=frozenset(Space),
    props=frozenset({"message", "kind", "duration", "visible"}),
    events=frozenset({"toast_dismissed"}),
    category="interaction",
)

FILTER_BAR_SPEC = ComponentSpec(
    kind=ComponentKind.FILTER_BAR,
    name="FilterBar",
    description="Filter bar for tables, lists",
    primitives=frozenset(),
    spaces=frozenset(Space),
    props=frozenset({"query", "filters", "active_filters"}),
    events=frozenset({"filter_changed", "filter_cleared", "query_changed"}),
    category="interaction",
)

SEARCH_BAR_SPEC = ComponentSpec(
    kind=ComponentKind.SEARCH_BAR,
    name="SearchBar",
    description="Search bar with fuzzy search",
    primitives=frozenset(),
    spaces=frozenset(Space),
    props=frozenset({"query", "results", "selected_index"}),
    events=frozenset({"search_submitted", "result_selected", "search_cleared"}),
    category="interaction",
)


# ---------------------------------------------------------------------------
# Space-specific components
# ---------------------------------------------------------------------------


LITERATURE_SURVEY_VIEW_SPEC = ComponentSpec(
    kind=ComponentKind.LITERATURE_SURVEY_VIEW,
    name="LiteratureSurveyView",
    description="Paper survey with citation tracking",
    primitives=frozenset({PrimitiveKind.PUBLICATION, PrimitiveKind.KNOWLEDGE}),
    spaces=frozenset({Space.LITERATURE}),
    props=frozenset({"papers", "citations", "show_abstracts", "show_citations"}),
    events=frozenset({"paper_clicked", "cite_clicked", "survey_submitted"}),
    category="space",
)

PLANNING_CANVAS_SPEC = ComponentSpec(
    kind=ComponentKind.PLANNING_CANVAS,
    name="PlanningCanvas",
    description="Hypothesis canvas with experiment design",
    primitives=frozenset(
        {
            PrimitiveKind.HYPOTHESIS,
            PrimitiveKind.EXPERIMENT,
            PrimitiveKind.RESEARCH_GOAL,
        }
    ),
    spaces=frozenset({Space.PLANNING}),
    props=frozenset({"hypotheses", "experiments", "goals", "show_connections"}),
    events=frozenset({"hypothesis_created", "experiment_created", "goal_created"}),
    category="space",
)

EXPERIMENT_RUNNER_SPEC = ComponentSpec(
    kind=ComponentKind.EXPERIMENT_RUNNER,
    name="ExperimentRunner",
    description="Workflow runner with live execution monitoring",
    primitives=frozenset({PrimitiveKind.WORKFLOW, PrimitiveKind.EXPERIMENT}),
    spaces=frozenset({Space.EXPERIMENT}),
    props=frozenset(
        {
            "workflow",
            "experiment",
            "show_steps",
            "show_logs",
            "show_metrics",
        }
    ),
    events=frozenset({"workflow_started", "workflow_paused", "workflow_resumed", "step_clicked"}),
    category="space",
)

HPC_MONITOR_SPEC = ComponentSpec(
    kind=ComponentKind.HPC_MONITOR,
    name="HPCMonitor",
    description="Slurm queue, GPU utilization, job status",
    primitives=frozenset({PrimitiveKind.COMPUTE_RESOURCE, PrimitiveKind.WORKFLOW}),
    spaces=frozenset({Space.HPC}),
    props=frozenset(
        {
            "slurm_queue",
            "gpu_utilization",
            "jobs",
            "show_gpu",
            "show_cpu",
            "show_memory",
        }
    ),
    events=frozenset({"job_clicked", "queue_refreshed", "gpu_clicked"}),
    category="space",
)

ANALYSIS_DASHBOARD_SPEC = ComponentSpec(
    kind=ComponentKind.ANALYSIS_DASHBOARD,
    name="AnalysisDashboard",
    description="Result dashboard with figures, hypothesis testing",
    primitives=frozenset(
        {
            PrimitiveKind.EXPERIMENT,
            PrimitiveKind.HYPOTHESIS,
            PrimitiveKind.DATASET,
        }
    ),
    spaces=frozenset({Space.ANALYSIS}),
    props=frozenset(
        {
            "experiments",
            "hypotheses",
            "datasets",
            "show_figures",
            "show_statistics",
        }
    ),
    events=frozenset({"figure_clicked", "hypothesis_tested", "dataset_clicked"}),
    category="space",
)

WRITING_EDITOR_SPEC = ComponentSpec(
    kind=ComponentKind.WRITING_EDITOR,
    name="WritingEditor",
    description="Publication editor with provenance insertion",
    primitives=frozenset({PrimitiveKind.PUBLICATION, PrimitiveKind.EXPERIMENT}),
    spaces=frozenset({Space.WRITING}),
    props=frozenset(
        {
            "publication",
            "experiments",
            "show_provenance",
            "show_figures",
            "render_mode",
        }
    ),
    events=frozenset({"text_changed", "provenance_inserted", "figure_inserted"}),
    category="space",
)

REVIEW_CHECKLIST_SPEC = ComponentSpec(
    kind=ComponentKind.REVIEW_CHECKLIST,
    name="ReviewChecklist",
    description="Peer review checklist with reproduction verification",
    primitives=frozenset(
        {
            PrimitiveKind.PUBLICATION,
            PrimitiveKind.EXPERIMENT,
            PrimitiveKind.HYPOTHESIS,
        }
    ),
    spaces=frozenset({Space.REVIEW}),
    props=frozenset(
        {
            "publication",
            "experiments",
            "hypotheses",
            "checklist_items",
            "show_provenance",
        }
    ),
    events=frozenset({"item_checked", "reproduce_clicked", "audit_clicked"}),
    category="space",
)


# ---------------------------------------------------------------------------
# Component registry
# ---------------------------------------------------------------------------


COMPONENT_SPECS: dict[ComponentKind, ComponentSpec] = {
    # Layout
    ComponentKind.WORKSPACE: WORKSPACE_SPEC,
    ComponentKind.NAVIGATION_TREE: NAVIGATION_TREE_SPEC,
    ComponentKind.STATUS_BAR: STATUS_BAR_SPEC,
    ComponentKind.HEADER: HEADER_SPEC,
    ComponentKind.SPLIT_PANE: SPLIT_PANE_SPEC,
    ComponentKind.FLOATING_PANEL: FLOATING_PANEL_SPEC,
    # Primitive visualizations
    ComponentKind.RESEARCH_PROJECT_CARD: RESEARCH_PROJECT_CARD_SPEC,
    ComponentKind.RESEARCH_GOAL_CARD: RESEARCH_GOAL_CARD_SPEC,
    ComponentKind.HYPOTHESIS_CARD: HYPOTHESIS_CARD_SPEC,
    ComponentKind.EXPERIMENT_CARD: EXPERIMENT_CARD_SPEC,
    ComponentKind.WORKFLOW_GRAPH: WORKFLOW_GRAPH_SPEC,
    ComponentKind.WORKFLOW_TIMELINE: WORKFLOW_TIMELINE_SPEC,
    ComponentKind.KNOWLEDGE_PANEL: KNOWLEDGE_PANEL_SPEC,
    ComponentKind.DATASET_BROWSER: DATASET_BROWSER_SPEC,
    ComponentKind.PUBLICATION_VIEWER: PUBLICATION_VIEWER_SPEC,
    ComponentKind.NOTEBOOK_VIEWER: NOTEBOOK_VIEWER_SPEC,
    ComponentKind.COMPUTE_RESOURCE_CARD: COMPUTE_RESOURCE_CARD_SPEC,
    ComponentKind.AGENT_CARD: AGENT_CARD_SPEC,
    ComponentKind.CONVERSATION_VIEW: CONVERSATION_VIEW_SPEC,
    # Interaction
    ComponentKind.COMMAND_PALETTE: COMMAND_PALETTE_SPEC,
    ComponentKind.SLASH_COMMAND_BAR: SLASH_COMMAND_BAR_SPEC,
    ComponentKind.NOTIFICATION_CENTER: NOTIFICATION_CENTER_SPEC,
    ComponentKind.INSPECTOR: INSPECTOR_SPEC,
    ComponentKind.DIALOG: DIALOG_SPEC,
    ComponentKind.TOAST: TOAST_SPEC,
    ComponentKind.FILTER_BAR: FILTER_BAR_SPEC,
    ComponentKind.SEARCH_BAR: SEARCH_BAR_SPEC,
    # Space-specific
    ComponentKind.LITERATURE_SURVEY_VIEW: LITERATURE_SURVEY_VIEW_SPEC,
    ComponentKind.PLANNING_CANVAS: PLANNING_CANVAS_SPEC,
    ComponentKind.EXPERIMENT_RUNNER: EXPERIMENT_RUNNER_SPEC,
    ComponentKind.HPC_MONITOR: HPC_MONITOR_SPEC,
    ComponentKind.ANALYSIS_DASHBOARD: ANALYSIS_DASHBOARD_SPEC,
    ComponentKind.WRITING_EDITOR: WRITING_EDITOR_SPEC,
    ComponentKind.REVIEW_CHECKLIST: REVIEW_CHECKLIST_SPEC,
}


def get_component_spec(kind: ComponentKind) -> ComponentSpec:
    """Get the specification for a component kind."""
    return COMPONENT_SPECS[kind]


def get_components_for_space(space: Space) -> list[ComponentSpec]:
    """Get all component specs that belong to a given space."""
    return [spec for spec in COMPONENT_SPECS.values() if space in spec.spaces]


def get_components_for_primitive(
    primitive: PrimitiveKind,
) -> list[ComponentSpec]:
    """Get all component specs that visualize a given primitive."""
    return [spec for spec in COMPONENT_SPECS.values() if primitive in spec.primitives]


def get_components_by_category(category: str) -> list[ComponentSpec]:
    """Get all component specs in a given category."""
    return [spec for spec in COMPONENT_SPECS.values() if spec.category == category]


def get_all_component_specs() -> list[ComponentSpec]:
    """Get all component specs."""
    return list(COMPONENT_SPECS.values())


def get_component_count() -> int:
    """Get the total number of component specs."""
    return len(COMPONENT_SPECS)
