"""Tests for r4_contracts.workspace — the R4 state management layer."""

from __future__ import annotations

from r4_contracts.research import (
    Agent,
    AgentStatus,
    ResearchProject,
)
from r4_contracts.spaces import Space
from r4_contracts.workspace import (
    SLASH_COMMANDS,
    NavNode,
    NavTree,
    Notification,
    NotificationKind,
    Workspace,
    find_slash_command,
    get_all_slash_commands,
    get_slash_commands_for_space,
)

# ---------------------------------------------------------------------------
# Workspace creation and defaults
# ---------------------------------------------------------------------------


def test_workspace_creation() -> None:
    """A new workspace starts in the LITERATURE space."""
    w = Workspace()
    assert w.current_space == Space.LITERATURE
    assert w.space_history == []
    assert w.agents == []
    assert w.notifications == []
    assert w.unread_notification_count == 0
    assert w.command_history == []
    assert w.gateway_connected is False
    assert w.umrs_connected is False
    assert w.hpc_connected is False


def test_workspace_default_space_is_literature() -> None:
    w = Workspace()
    assert w.current_space == Space.LITERATURE


# ---------------------------------------------------------------------------
# Space transitions
# ---------------------------------------------------------------------------


def test_transition_to_valid_space() -> None:
    """Literature → Planning is a valid transition."""
    w = Workspace()
    assert w.transition_to(Space.PLANNING) is True
    assert w.current_space == Space.PLANNING
    assert w.space_history == [Space.LITERATURE]


def test_transition_to_invalid_space() -> None:
    """Literature → HPC is not a valid direct transition."""
    w = Workspace()
    assert w.transition_to(Space.HPC) is False
    assert w.current_space == Space.LITERATURE


def test_transition_back() -> None:
    """Transition back restores the previous space."""
    w = Workspace()
    w.transition_to(Space.PLANNING)
    assert w.transition_back() is True
    assert w.current_space == Space.LITERATURE


def test_transition_back_empty_history() -> None:
    """Transition back with empty history returns False."""
    w = Workspace()
    assert w.transition_back() is False


def test_full_research_lifecycle() -> None:
    """Test the full research lifecycle: Literature → Planning → Experiment → HPC → Analysis → Writing → Review."""
    w = Workspace()
    assert w.transition_to(Space.PLANNING) is True
    assert w.transition_to(Space.EXPERIMENT) is True
    assert w.transition_to(Space.HPC) is True
    assert w.transition_to(Space.ANALYSIS) is True
    assert w.transition_to(Space.WRITING) is True
    assert w.transition_to(Space.REVIEW) is True
    assert w.current_space == Space.REVIEW


# ---------------------------------------------------------------------------
# Agent team management
# ---------------------------------------------------------------------------


def test_add_agent() -> None:
    w = Workspace()
    agent = Agent(
        id="agent-001",
        project_id="proj-001",
        name="Literature Agent",
        role="literature",
        status=AgentStatus.EXECUTING,
    )
    w.add_agent(agent)
    assert len(w.agents) == 1
    assert w.agents[0].id == "agent-001"


def test_get_agent() -> None:
    w = Workspace()
    agent = Agent(id="a1", project_id="p1", name="Agent 1")
    w.add_agent(agent)
    found = w.get_agent("a1")
    assert found is not None
    assert found.name == "Agent 1"
    assert w.get_agent("nonexistent") is None


def test_get_active_agents() -> None:
    w = Workspace()
    w.add_agent(
        Agent(
            id="a1",
            project_id="p1",
            name="Idle Agent",
            status=AgentStatus.IDLE,
        )
    )
    w.add_agent(
        Agent(
            id="a2",
            project_id="p1",
            name="Executing Agent",
            status=AgentStatus.EXECUTING,
        )
    )
    w.add_agent(
        Agent(
            id="a3",
            project_id="p1",
            name="Thinking Agent",
            status=AgentStatus.THINKING,
        )
    )
    active = w.get_active_agents()
    assert len(active) == 2
    assert active[0].id == "a2"
    assert active[1].id == "a3"


def test_get_agent_team_status() -> None:
    w = Workspace()
    w.add_agent(
        Agent(
            id="a1",
            project_id="p1",
            name="Idle",
            status=AgentStatus.IDLE,
        )
    )
    w.add_agent(
        Agent(
            id="a2",
            project_id="p1",
            name="Thinking",
            status=AgentStatus.THINKING,
        )
    )
    w.add_agent(
        Agent(
            id="a3",
            project_id="p1",
            name="Executing",
            status=AgentStatus.EXECUTING,
        )
    )
    w.add_agent(
        Agent(
            id="a4",
            project_id="p1",
            name="Waiting",
            status=AgentStatus.WAITING,
        )
    )
    w.add_agent(
        Agent(
            id="a5",
            project_id="p1",
            name="Error",
            status=AgentStatus.ERROR,
        )
    )
    status = w.get_agent_team_status()
    assert status["total"] == 5
    assert status["idle"] == 1
    assert status["thinking"] == 1
    assert status["executing"] == 1
    assert status["waiting"] == 1
    assert status["error"] == 1


# ---------------------------------------------------------------------------
# Notification management
# ---------------------------------------------------------------------------


def test_add_notification() -> None:
    w = Workspace()
    w.add_notification(
        Notification(
            kind=NotificationKind.SUCCESS,
            title="Job completed",
            message="Job #20391 finished successfully",
        )
    )
    assert len(w.notifications) == 1
    assert w.unread_notification_count == 1


def test_mark_notifications_read() -> None:
    w = Workspace()
    w.add_notification(Notification(kind=NotificationKind.INFO, title="Test"))
    w.add_notification(Notification(kind=NotificationKind.WARNING, title="Test 2"))
    w.mark_notifications_read()
    assert w.unread_notification_count == 0
    assert len(w.notifications) == 2


def test_clear_notifications() -> None:
    w = Workspace()
    w.add_notification(Notification(kind=NotificationKind.INFO, title="Test"))
    w.clear_notifications()
    assert len(w.notifications) == 0
    assert w.unread_notification_count == 0


# ---------------------------------------------------------------------------
# Command history
# ---------------------------------------------------------------------------


def test_add_command() -> None:
    w = Workspace()
    w.add_command("/hypothesize")
    w.add_command("/experiment")
    assert w.get_command_history() == ["/hypothesize", "/experiment"]


# ---------------------------------------------------------------------------
# Connection status
# ---------------------------------------------------------------------------


def test_is_fully_connected() -> None:
    w = Workspace()
    assert w.is_fully_connected() is False

    w.gateway_connected = True
    assert w.is_fully_connected() is False

    w.umrs_connected = True
    assert w.is_fully_connected() is False

    w.hpc_connected = True
    assert w.is_fully_connected() is True


def test_get_connection_status() -> None:
    w = Workspace()
    w.gateway_connected = True
    w.umrs_connected = False
    w.hpc_connected = True
    status = w.get_connection_status()
    assert status["gateway"] is True
    assert status["umrs"] is False
    assert status["hpc"] is True


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_to_dict() -> None:
    w = Workspace()
    w.workspace_id = "ws-001"
    w.gateway_connected = True
    w.umrs_connected = True
    w.hpc_connected = True
    w.add_agent(
        Agent(
            id="a1",
            project_id="p1",
            name="Test Agent",
            status=AgentStatus.IDLE,
        )
    )
    d = w.to_dict()
    assert d["workspace_id"] == "ws-001"
    assert d["current_space"] == "literature"
    assert d["gateway_connected"] is True
    assert d["umrs_connected"] is True
    assert d["hpc_connected"] is True
    assert len(d["agents"]) == 1
    assert d["agents"][0]["id"] == "a1"


# ---------------------------------------------------------------------------
# Navigation tree
# ---------------------------------------------------------------------------


def test_nav_node_creation() -> None:
    node = NavNode(label="Hypotheses", icon="△")
    assert node.label == "Hypotheses"
    assert node.children == []
    assert node.expanded is False


def test_nav_tree_build() -> None:
    """Build a navigation tree from a research project."""
    project = ResearchProject(
        id="proj-001",
        name="Catalyst Investigation",
        goal_ids=["goal-001"],
        hypothesis_ids=["h-001", "h-002"],
        experiment_ids=["exp-001"],
        dataset_ids=["ds-001"],
    )
    tree = NavTree()
    tree.build(project)
    # Root should have children
    assert len(tree.root.children) > 0
    # Should have a Goals node
    goals = [c for c in tree.root.children if c.label == "Goals"]
    assert len(goals) == 1
    assert len(goals[0].children) == 1
    # Should have a Hypotheses node
    hyps = [c for c in tree.root.children if c.label == "Hypotheses"]
    assert len(hyps) == 1
    assert len(hyps[0].children) == 2


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


def test_slash_commands_not_empty() -> None:
    assert len(SLASH_COMMANDS) > 0


def test_get_all_slash_commands() -> None:
    cmds = get_all_slash_commands()
    assert len(cmds) == len(SLASH_COMMANDS)


def test_find_slash_command_by_name() -> None:
    cmd = find_slash_command("/hypothesize")
    assert cmd is not None
    assert cmd.name == "/hypothesize"


def test_find_slash_command_by_alias() -> None:
    cmd = find_slash_command("/hyp")
    assert cmd is not None
    assert cmd.name == "/hypothesize"


def test_find_slash_command_not_found() -> None:
    cmd = find_slash_command("/nonexistent")
    assert cmd is None


def test_get_slash_commands_for_space() -> None:
    """Get slash commands available in the PLANNING space."""
    cmds = get_slash_commands_for_space(Space.PLANNING)
    # Should include /hypothesize and /experiment
    names = [c.name for c in cmds]
    assert "/hypothesize" in names
    assert "/experiment" in names


def test_get_slash_commands_for_hpc_space() -> None:
    cmds = get_slash_commands_for_space(Space.HPC)
    names = [c.name for c in cmds]
    assert "/slurm" in names
    assert "/submit" in names
    assert "/queue" in names
    assert "/gpu" in names


def test_get_slash_commands_for_review_space() -> None:
    cmds = get_slash_commands_for_space(Space.REVIEW)
    names = [c.name for c in cmds]
    assert "/reproduce" in names
    assert "/audit" in names
    assert "/lineage" in names


# ---------------------------------------------------------------------------
# Integration: Workspace + Research Project + Spaces
# ---------------------------------------------------------------------------


def test_workspace_with_research_project() -> None:
    """Test loading a research project into the workspace and navigating spaces."""
    project = ResearchProject(
        id="proj-001",
        name="Catalyst Investigation",
        goal_ids=["goal-001"],
        hypothesis_ids=["h-001"],
        experiment_ids=["exp-001"],
        dataset_ids=["ds-001"],
    )
    w = Workspace()
    w.load_project(project)

    # Verify project is loaded
    assert w.project is not None
    assert w.project.id == "proj-001"

    # Verify navigation tree is built
    assert len(w.nav_tree.root.children) > 0

    # Navigate through the research lifecycle
    assert w.transition_to(Space.PLANNING) is True
    assert w.transition_to(Space.EXPERIMENT) is True
    assert w.transition_to(Space.HPC) is True
    assert w.transition_to(Space.ANALYSIS) is True
    assert w.transition_to(Space.WRITING) is True
    assert w.transition_to(Space.REVIEW) is True

    # Verify space history
    assert len(w.space_history) == 6


def test_workspace_agent_team_with_notifications() -> None:
    """Test agent team status combined with notifications."""
    w = Workspace()
    w.add_agent(
        Agent(
            id="a1",
            project_id="p1",
            name="Literature Agent",
            status=AgentStatus.EXECUTING,
        )
    )
    w.add_agent(
        Agent(
            id="a2",
            project_id="p1",
            name="HPC Agent",
            status=AgentStatus.WAITING,
        )
    )

    # Simulate agent completion notification
    w.add_notification(
        Notification(
            kind=NotificationKind.SUCCESS,
            title="Literature Agent finished",
            message="Surveyed 42 papers",
        )
    )

    assert w.unread_notification_count == 1
    assert len(w.notifications) == 1
    assert w.notifications[0].kind == NotificationKind.SUCCESS
