"""Workspace — R4's state management layer.

The Workspace is the bridge between the research primitives (data model)
and the TUI (presentation layer). It manages:

  - Current research project
  - Current space (Literature, Planning, Experiment, HPC, Analysis,
    Writing, Review)
  - Navigation tree state
  - Agent team status
  - Command history
  - Notification queue

The Workspace is a pure data model — no I/O, no side effects. The TUI
reads from it and writes to it via the bridge server.

All types are stdlib-only ``@dataclass``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .research import (
    Agent,
    AgentStatus,
    PrimitiveKind,
    ResearchProject,
)
from .spaces import SPACE_DEFINITIONS, Space, SpaceDefinition, can_transition

# ---------------------------------------------------------------------------
# Navigation tree
# ---------------------------------------------------------------------------


@dataclass
class NavNode:
    """A single node in the navigation tree.

    Each node references a research primitive by id and kind, and has
    children that are also NavNodes.
    """

    label: str
    primitive_id: str = ""
    primitive_kind: PrimitiveKind | None = None
    children: list[NavNode] = field(default_factory=list)
    expanded: bool = False
    icon: str = ""


@dataclass
class NavTree:
    """The navigation tree for the current workspace.

    The tree is organized by primitive kind, with each kind having
    children that are the individual primitives.
    """

    root: NavNode = field(default_factory=lambda: NavNode(label="R4", icon="◆"))

    def build(self, project: ResearchProject) -> None:
        """Build the navigation tree from a research project."""
        self.root.children.clear()

        # Research Goals
        goals_node = NavNode(label="Goals", icon="◎", expanded=True)
        for gid in project.goal_ids:
            goals_node.children.append(
                NavNode(
                    label=gid,
                    primitive_id=gid,
                    primitive_kind=PrimitiveKind.RESEARCH_GOAL,
                )
            )
        self.root.children.append(goals_node)

        # Hypotheses
        hyp_node = NavNode(label="Hypotheses", icon="△", expanded=True)
        for hid in project.hypothesis_ids:
            hyp_node.children.append(
                NavNode(
                    label=hid,
                    primitive_id=hid,
                    primitive_kind=PrimitiveKind.HYPOTHESIS,
                )
            )
        self.root.children.append(hyp_node)

        # Experiments
        exp_node = NavNode(label="Experiments", icon=" exp ", expanded=True)
        for eid in project.experiment_ids:
            exp_node.children.append(
                NavNode(
                    label=eid,
                    primitive_id=eid,
                    primitive_kind=PrimitiveKind.EXPERIMENT,
                )
            )
        self.root.children.append(exp_node)

        # Workflows
        wf_node = NavNode(label="Workflows", icon="▶", expanded=True)
        for wid in project.workflow_ids:
            wf_node.children.append(
                NavNode(
                    label=wid,
                    primitive_id=wid,
                    primitive_kind=PrimitiveKind.WORKFLOW,
                )
            )
        self.root.children.append(wf_node)

        # Datasets
        ds_node = NavNode(label="Datasets", icon="▤", expanded=True)
        for did in project.dataset_ids:
            ds_node.children.append(
                NavNode(
                    label=did,
                    primitive_id=did,
                    primitive_kind=PrimitiveKind.DATASET,
                )
            )
        self.root.children.append(ds_node)

        # Publications
        pub_node = NavNode(label="Papers", icon="✦", expanded=True)
        for pid in project.publication_ids:
            pub_node.children.append(
                NavNode(
                    label=pid,
                    primitive_id=pid,
                    primitive_kind=PrimitiveKind.PUBLICATION,
                )
            )
        self.root.children.append(pub_node)

        # Notebooks
        nb_node = NavNode(label="Notebooks", icon="▣", expanded=True)
        for nid in project.notebook_ids:
            nb_node.children.append(
                NavNode(
                    label=nid,
                    primitive_id=nid,
                    primitive_kind=PrimitiveKind.NOTEBOOK,
                )
            )
        self.root.children.append(nb_node)

        # Simulations
        sim_node = NavNode(label="Simulations", icon="⬡", expanded=True)
        for sid in project.simulation_ids:
            sim_node.children.append(
                NavNode(
                    label=sid,
                    primitive_id=sid,
                    primitive_kind=PrimitiveKind.SIMULATION,
                )
            )
        self.root.children.append(sim_node)

        # Compute Resources
        cr_node = NavNode(label="Compute", icon="⬢", expanded=True)
        for cid in project.compute_resource_ids:
            cr_node.children.append(
                NavNode(
                    label=cid,
                    primitive_id=cid,
                    primitive_kind=PrimitiveKind.COMPUTE_RESOURCE,
                )
            )
        self.root.children.append(cr_node)

        # Agents
        agent_node = NavNode(label="Agents", icon="●", expanded=True)
        for aid in project.agent_ids:
            agent_node.children.append(
                NavNode(
                    label=aid,
                    primitive_id=aid,
                    primitive_kind=PrimitiveKind.AGENT,
                )
            )
        self.root.children.append(agent_node)

        # Conversations
        conv_node = NavNode(label="Conversations", icon="💬", expanded=True)
        for cid in project.conversation_ids:
            conv_node.children.append(
                NavNode(
                    label=cid,
                    primitive_id=cid,
                    primitive_kind=PrimitiveKind.CONVERSATION,
                )
            )
        self.root.children.append(conv_node)


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------


class NotificationKind(str, Enum):
    """Kind of notification."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class Notification:
    """A single notification in the workspace."""

    kind: NotificationKind
    title: str
    message: str = ""
    timestamp: str = ""
    action_label: str = ""
    action_command: str = ""


# ---------------------------------------------------------------------------
# Slash command
# ---------------------------------------------------------------------------


@dataclass
class SlashCommand:
    """A slash command definition.

    Slash commands are the primary invocation mechanism from the prompt
    input. Each command has a name, description, and the space(s) it is
    available in.
    """

    name: str  # e.g. "/hypothesize"
    description: str = ""
    category: str = ""
    spaces: list[Space] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Workspace — the state management layer
# ---------------------------------------------------------------------------


@dataclass
class Workspace:
    """The R4 workspace state.

    This is the central state object for the R4 TUI. It holds:

      - The current research project
      - The current space (modal working context)
      - The navigation tree
      - The agent team (list of agents)
      - The notification queue
      - The command history

    The Workspace is a pure data model. The TUI reads from it and
    writes to it via the bridge server.
    """

    # Identity
    workspace_id: str = ""
    project: ResearchProject | None = None

    # Current space
    current_space: Space = Space.LITERATURE
    space_history: list[Space] = field(default_factory=list)

    # Navigation
    nav_tree: NavTree = field(default_factory=NavTree)
    selected_nav_node: NavNode | None = None

    # Agent team
    agents: list[Agent] = field(default_factory=list)

    # Notifications
    notifications: list[Notification] = field(default_factory=list)
    unread_notification_count: int = 0

    # Command history
    command_history: list[str] = field(default_factory=list)

    # Connection status
    gateway_connected: bool = False
    umrs_connected: bool = False
    hpc_connected: bool = False

    # Metadata
    created_at: str = ""
    updated_at: str = ""

    # ------------------------------------------------------------------
    # Space transitions
    # ------------------------------------------------------------------

    def transition_to(self, space: Space) -> bool:
        """Transition to a new space.

        Returns True if the transition is valid, False otherwise.
        """
        if not can_transition(self.current_space, space):
            return False
        self.space_history.append(self.current_space)
        self.current_space = space
        return True

    def transition_back(self) -> bool:
        """Transition back to the previous space.

        Returns True if there was a previous space, False otherwise.
        """
        if not self.space_history:
            return False
        self.current_space = self.space_history.pop()
        return True

    def get_current_space_definition(self) -> SpaceDefinition:
        """Get the definition for the current space."""
        return SPACE_DEFINITIONS[self.current_space]

    # ------------------------------------------------------------------
    # Project management
    # ------------------------------------------------------------------

    def load_project(self, project: ResearchProject) -> None:
        """Load a research project into the workspace."""
        self.project = project
        self.nav_tree.build(project)

    def get_project(self) -> ResearchProject | None:
        """Get the current research project."""
        return self.project

    # ------------------------------------------------------------------
    # Agent team management
    # ------------------------------------------------------------------

    def add_agent(self, agent: Agent) -> None:
        """Add an agent to the research team."""
        self.agents.append(agent)

    def get_agent(self, agent_id: str) -> Agent | None:
        """Get an agent by id."""
        for agent in self.agents:
            if agent.id == agent_id:
                return agent
        return None

    def get_active_agents(self) -> list[Agent]:
        """Get all agents that are currently executing or thinking."""
        return [
            agent
            for agent in self.agents
            if agent.status in (AgentStatus.EXECUTING, AgentStatus.THINKING)
        ]

    def get_agent_team_status(self) -> dict[str, Any]:
        """Get a summary of the agent team status.

        Returns a dict with:
          - total: total number of agents
          - idle: number of idle agents
          - thinking: number of thinking agents
          - executing: number of executing agents
          - waiting: number of waiting agents
          - error: number of error agents
        """
        status_counts = {
            "total": len(self.agents),
            "idle": 0,
            "thinking": 0,
            "executing": 0,
            "waiting": 0,
            "error": 0,
        }
        for agent in self.agents:
            if agent.status == AgentStatus.IDLE:
                status_counts["idle"] += 1
            elif agent.status == AgentStatus.THINKING:
                status_counts["thinking"] += 1
            elif agent.status == AgentStatus.EXECUTING:
                status_counts["executing"] += 1
            elif agent.status == AgentStatus.WAITING:
                status_counts["waiting"] += 1
            elif agent.status == AgentStatus.ERROR:
                status_counts["error"] += 1
        return status_counts

    # ------------------------------------------------------------------
    # Notification management
    # ------------------------------------------------------------------

    def add_notification(self, notification: Notification) -> None:
        """Add a notification to the queue."""
        self.notifications.append(notification)
        self.unread_notification_count += 1

    def mark_notifications_read(self) -> None:
        """Mark all notifications as read."""
        self.unread_notification_count = 0

    def clear_notifications(self) -> None:
        """Clear all notifications."""
        self.notifications.clear()
        self.unread_notification_count = 0

    # ------------------------------------------------------------------
    # Command history
    # ------------------------------------------------------------------

    def add_command(self, command: str) -> None:
        """Add a command to the command history."""
        self.command_history.append(command)

    def get_command_history(self) -> list[str]:
        """Get the command history."""
        return self.command_history

    # ------------------------------------------------------------------
    # Connection status
    # ------------------------------------------------------------------

    def is_fully_connected(self) -> bool:
        """Check if all connections are active."""
        return self.gateway_connected and self.umrs_connected and self.hpc_connected

    def get_connection_status(self) -> dict[str, bool]:
        """Get the connection status for all services."""
        return {
            "gateway": self.gateway_connected,
            "umrs": self.umrs_connected,
            "hpc": self.hpc_connected,
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize the workspace to a dict."""
        return {
            "workspace_id": self.workspace_id,
            "current_space": self.current_space.value,
            "space_history": [s.value for s in self.space_history],
            "agents": [
                {
                    "id": a.id,
                    "name": a.name,
                    "role": a.role,
                    "status": a.status.value,
                    "current_task": a.current_task,
                }
                for a in self.agents
            ],
            "unread_notification_count": self.unread_notification_count,
            "gateway_connected": self.gateway_connected,
            "umrs_connected": self.umrs_connected,
            "hpc_connected": self.hpc_connected,
        }


# ---------------------------------------------------------------------------
# Slash command registry
# ---------------------------------------------------------------------------


# All slash commands available in R4
SLASH_COMMANDS: list[SlashCommand] = [
    # Research commands
    SlashCommand(
        name="/hypothesize",
        description="Form a new hypothesis",
        category="Research",
        spaces=[Space.PLANNING],
        aliases=["/hyp"],
    ),
    SlashCommand(
        name="/experiment",
        description="Design a new experiment",
        category="Research",
        spaces=[Space.PLANNING, Space.EXPERIMENT],
        aliases=["/exp"],
    ),
    SlashCommand(
        name="/simulate",
        description="Run a simulation",
        category="Research",
        spaces=[Space.EXPERIMENT, Space.HPC],
        aliases=["/sim"],
    ),
    SlashCommand(
        name="/analyze",
        description="Analyze experiment results",
        category="Research",
        spaces=[Space.ANALYSIS],
        aliases=["/ana"],
    ),
    SlashCommand(
        name="/conclude",
        description="Conclude the research and write up findings",
        category="Research",
        spaces=[Space.ANALYSIS, Space.WRITING],
        aliases=["/con"],
    ),
    # Workflow commands
    SlashCommand(
        name="/run",
        description="Run a workflow",
        category="Workflow",
        spaces=[Space.EXPERIMENT, Space.HPC],
        aliases=["/r"],
    ),
    SlashCommand(
        name="/monitor",
        description="Monitor a running workflow",
        category="Workflow",
        spaces=[Space.EXPERIMENT, Space.HPC],
        aliases=["/mon"],
    ),
    SlashCommand(
        name="/pause",
        description="Pause a running workflow",
        category="Workflow",
        spaces=[Space.EXPERIMENT, Space.HPC],
    ),
    SlashCommand(
        name="/resume",
        description="Resume a paused workflow",
        category="Workflow",
        spaces=[Space.EXPERIMENT, Space.HPC],
    ),
    # HPC commands
    SlashCommand(
        name="/slurm",
        description="Interact with Slurm scheduler",
        category="HPC",
        spaces=[Space.HPC],
        aliases=["/s"],
    ),
    SlashCommand(
        name="/submit",
        description="Submit a job to Slurm",
        category="HPC",
        spaces=[Space.HPC],
        aliases=["/sub"],
    ),
    SlashCommand(
        name="/queue",
        description="Show Slurm queue",
        category="HPC",
        spaces=[Space.HPC],
        aliases=["/q"],
    ),
    SlashCommand(
        name="/gpu",
        description="Show GPU utilization",
        category="HPC",
        spaces=[Space.HPC],
    ),
    SlashCommand(
        name="/jobs",
        description="Show all jobs",
        category="HPC",
        spaces=[Space.HPC],
    ),
    # Knowledge commands
    SlashCommand(
        name="/cite",
        description="Cite a paper",
        category="Knowledge",
        spaces=[Space.LITERATURE, Space.WRITING],
    ),
    SlashCommand(
        name="/survey",
        description="Survey papers on a topic",
        category="Knowledge",
        spaces=[Space.LITERATURE],
    ),
    SlashCommand(
        name="/index",
        description="Index documents for RAG",
        category="Knowledge",
        spaces=[Space.LITERATURE],
    ),
    SlashCommand(
        name="/search",
        description="Search the knowledge base",
        category="Knowledge",
        spaces=[Space.LITERATURE, Space.ANALYSIS],
    ),
    # Dataset commands
    SlashCommand(
        name="/dataset",
        description="Manage datasets",
        category="Datasets",
        spaces=[Space.LITERATURE, Space.EXPERIMENT, Space.ANALYSIS],
    ),
    SlashCommand(
        name="/upload",
        description="Upload a dataset",
        category="Datasets",
        spaces=[Space.EXPERIMENT],
    ),
    # Publication commands
    SlashCommand(
        name="/draft",
        description="Draft a publication",
        category="Publications",
        spaces=[Space.WRITING],
    ),
    SlashCommand(
        name="/insert",
        description="Insert provenance into a publication",
        category="Publications",
        spaces=[Space.WRITING],
    ),
    SlashCommand(
        name="/publish",
        description="Publish a paper",
        category="Publications",
        spaces=[Space.WRITING, Space.REVIEW],
    ),
    # Reproduce commands
    SlashCommand(
        name="/reproduce",
        description="Reproduce an experiment",
        category="Reproduce",
        spaces=[Space.REVIEW],
    ),
    SlashCommand(
        name="/audit",
        description="Audit the provenance of a result",
        category="Reproduce",
        spaces=[Space.REVIEW],
    ),
    SlashCommand(
        name="/lineage",
        description="Show the lineage of an artifact",
        category="Reproduce",
        spaces=[Space.REVIEW, Space.ANALYSIS],
    ),
    # Navigation commands
    SlashCommand(
        name="/projects",
        description="List all research projects",
        category="Navigate",
    ),
    SlashCommand(
        name="/goals",
        description="List all research goals",
        category="Navigate",
    ),
    SlashCommand(
        name="/hypotheses",
        description="List all hypotheses",
        category="Navigate",
    ),
    SlashCommand(
        name="/experiments",
        description="List all experiments",
        category="Navigate",
    ),
    SlashCommand(
        name="/datasets",
        description="List all datasets",
        category="Navigate",
    ),
    SlashCommand(
        name="/papers",
        description="List all publications",
        category="Navigate",
    ),
    SlashCommand(
        name="/agents",
        description="List all agents",
        category="Navigate",
    ),
    SlashCommand(
        name="/compute",
        description="List all compute resources",
        category="Navigate",
    ),
]


def get_slash_commands_for_space(space: Space) -> list[SlashCommand]:
    """Get all slash commands available in a given space."""
    return [cmd for cmd in SLASH_COMMANDS if not cmd.spaces or space in cmd.spaces]


def get_all_slash_commands() -> list[SlashCommand]:
    """Get all slash commands."""
    return SLASH_COMMANDS


def find_slash_command(name: str) -> SlashCommand | None:
    """Find a slash command by name or alias.

    Args:
        name: The command name (e.g. "/hypothesize") or alias (e.g. "/hyp")

    Returns:
        The SlashCommand if found, None otherwise.
    """
    for cmd in SLASH_COMMANDS:
        if cmd.name == name or name in cmd.aliases:
            return cmd
    return None
