"""Canonical capability registry — one verb table for the transports (P9).

Until WP-08 the four transports each grew their own verb set (ACP-only
``workflows/* + knowledge``, bridge-only ``project_* + jobs``, neither
agreed). This module is the single place a capability is declared; the
parity contract test (r4/tests/test_capability_parity.py) proves every
claimed transport actually serves the verb, so a future verb lands here
first and the transports follow — never the other way around again.

Naming: each transport keeps its own spelling; the registry records the
exact served name per rail. ``None`` means the rail does not serve the
capability (recorded, deliberate — the contract surfaces it instead of
letting it drift silently). ACP spellings use ``family/verb``; the bridge
keeps its historical mixed spellings (``workflow.list`` vs
``workflow_describe``) — unifying spellings is a separate decision.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    """One management capability and its per-transport spellings."""

    name: str  # canonical "family.verb"
    family: str
    mutating: bool
    acp: str | None = None
    bridge: str | None = None
    # G-P9-1 round 2 (2026-09-18): CLI spelling (`r4 <cmd>` argv tail), or
    # None when the capability intentionally ships no CLI surface (workflow
    # describe/detail/results and the research loop family live on the
    # ACP/bridge rails per their retirement-era design; catalog.search and
    # knowledge.set_acquisition are model-side tools, never CLI verbs).
    cli: str | None = None


# The canonical management surface (G-P9-1/G-P9-4). ACP + bridge spellings
# were pinned in WP-08; the CLI map landed in round 2 (2026-09-18) — every
# declared spelling is probe-verified against `r4 <spelling> --help`.
CAPABILITIES: tuple[Capability, ...] = (
    # ── projects ──
    Capability(
        "projects.list",
        "projects",
        False,
        "projects/list",
        "project_list",
        cli="project list",
    ),
    Capability(
        "projects.switch",
        "projects",
        True,
        "projects/switch",
        "project_switch",
        cli="project switch",
    ),
    Capability(
        "projects.create",
        "projects",
        True,
        "projects/create",
        "project_create",
        cli="project create",
    ),
    Capability(
        "projects.register",
        "projects",
        True,
        # ACP None like delete: a console/CLI verb (2026-11-03).
        None,
        "project_register",
        cli="project register",
    ),
    Capability(
        "projects.delete",
        "projects",
        True,
        # ACP deliberately None: deletion is a console/CLI verb (2026-10-01);
        # the VS Code surface manages projects by switch/create only.
        None,
        "project_delete",
        cli="project delete",
    ),
    # ── workflows ──
    Capability(
        "workflows.list",
        "workflows",
        False,
        "workflows/list",
        "workflow.list",
        cli="workflow list",
    ),
    Capability("workflows.describe", "workflows", False, "workflows/describe", "workflow_describe"),
    Capability(
        "workflows.trigger",
        "workflows",
        True,
        "workflows/trigger",
        "workflow.run",
        cli="workflow trigger",
    ),
    Capability(
        "workflows.status",
        "workflows",
        False,
        "workflows/status",
        "workflow.status",
        cli="workflow status",
    ),
    Capability("workflows.detail", "workflows", False, "workflows/detail", "workflow.detail"),
    Capability("workflows.results", "workflows", False, "workflows/results", "workflow.results"),
    Capability(
        "workflows.cancel",
        "workflows",
        True,
        "workflows/cancel",
        "workflow.cancel",
        cli="workflow cancel",
    ),
    Capability(
        "workflows.approve_gate",
        "workflows",
        True,
        "workflows/approve_gate",
        "workflow.approve_gate",
        cli="workflow approve",
    ),
    # ── jobs (ACP rail added in WP-08 — G-P9-4) ──
    Capability(
        "jobs.list",
        "jobs",
        False,
        "jobs/list",
        "list_jobs",
        cli="job list",
    ),
    Capability(
        "jobs.status",
        "jobs",
        False,
        "jobs/status",
        "get_job",
        cli="job describe",
    ),
    Capability(
        "jobs.cancel",
        "jobs",
        True,
        "jobs/cancel",
        "stop_job",
        cli="job cancel",
    ),
    # ── endpoints (ACP rail added in WP-08 — G-P9-4) ──
    Capability(
        "endpoints.list",
        "endpoints",
        False,
        "endpoints/list",
        "list_up_models",
        cli="endpoint list",
    ),
    # bridge has no stop verb (list_stoppable_endpoints feeds `r4 down` only);
    # recorded, round-2 leg.
    Capability(
        "endpoints.stop",
        "endpoints",
        True,
        "endpoints/stop",
        None,
        cli="endpoint down",
    ),
    # ── research loops / sessions ──
    Capability("research.loops", "research", False, "research/loops", "research_loop.list"),
    Capability("research.loop_status", "research", False, None, "research_loop.status"),
    Capability("research.loop_start", "research", True, "research/loop_start", None),
    Capability("research.loop_stop", "research", True, "research/loop_stop", None),
    Capability(
        "research.session_view", "research", False, "research/session", "research_session.view"
    ),
    # ── knowledge / catalog ──
    Capability(
        "knowledge.graph",
        "knowledge",
        False,
        "knowledge/graph",
        None,
        cli="knowledge graph",
    ),
    Capability(
        "knowledge.graphs",
        "knowledge",
        False,
        "knowledge/graphs",
        None,
        cli="knowledge graphs",
    ),
    Capability(
        "knowledge.query",
        "knowledge",
        False,
        "knowledge/query",
        None,
        cli="knowledge search",
    ),
    Capability("knowledge.detail", "knowledge", False, None, "get_knowledge"),
    Capability(
        "knowledge.graph_content",
        "knowledge",
        False,
        "knowledge/graph_content",
        "knowledge_graph_content",
        cli="knowledge graph show",
    ),
    Capability("catalog.search", "catalog", False, "catalog/search", None),
    # Session-scoped acquisition mode (user-switchable write gate; bridge-only —
    # ACP/Studio surface is a separate decision).
    Capability("knowledge.set_acquisition", "knowledge", True, None, "set_knowledge_acquisition"),
    # ── skills / mcp ──
    Capability(
        "skills.list",
        "skills",
        False,
        "skills/list",
        "list_skills",
        cli="skill list",
    ),
    Capability("skills.set_active", "skills", True, "skills/set_active", "activate_skill"),
    Capability(
        "mcp.servers",
        "mcp",
        False,
        "mcp/servers",
        "list_mcp_servers",
        cli="mcp list",
    ),
    Capability("mcp.tools", "mcp", False, "mcp/tools", "list_mcp_tools"),
    Capability(
        "mcp.server_health",
        "mcp",
        False,
        "mcp/server_health",
        "mcp_server_health",
        cli="mcp health",
    ),
    # ── sessions ──
    Capability(
        "sessions.fork",
        "sessions",
        True,
        None,
        "session.fork",
        cli="session fork",
    ),
    # ── frontend-venue runtime (wave 2026-09-21, ADR-046 A2) ──
    Capability(
        "sessions.venue_start",
        "sessions",
        True,
        None,
        "session.venue_start",
        cli="session start",
    ),
    Capability(
        "sessions.venue_status",
        "sessions",
        False,
        None,
        "session.venue_status",
        cli="session venue",
    ),
    Capability(
        "sessions.venue_stop",
        "sessions",
        True,
        None,
        "session.venue_stop",
        cli="session stop",
    ),
    Capability("sessions.venue_sync", "sessions", False, None, "session.venue_sync"),
    # 2026-10-01: one teamup role agent → persona'd chat session (agent tabs).
    Capability(
        "teamups.spawn_agent_session",
        "teamups",
        True,
        None,
        "teamup.spawn_agent_session",
    ),
    # ── team plans (ADR-092 slice 3): the console's rail over the chat tools'
    # executor. ACP keeps the chat tools only (deliberate None); a CLI run is
    # foreground (Ctrl-C cancels), and runs are listed only to the console.
    Capability(
        "teamplans.templates",
        "teamplans",
        False,
        None,
        "team.plan_templates",
        cli="team plan list",
    ),
    Capability(
        "teamplans.run",
        "teamplans",
        True,
        None,
        "team.plan_run",
        cli="team plan run",
    ),
    Capability(
        "teamplans.result",
        "teamplans",
        False,
        None,
        "team.plan_result",
        cli="team plan result",
    ),
    Capability("teamplans.cancel", "teamplans", True, None, "team.plan_cancel"),
    Capability("teamplans.runs", "teamplans", False, None, "team.runs"),
    # ── sealed sources (ADR-046 R1-6 verbs reachable from the TUI) ──
    Capability(
        "sources.list_remote",
        "sources",
        False,
        None,
        "source.list_remote",
        cli="source list",
    ),
    Capability("sources.push", "sources", True, None, "source.push", cli="source push"),
    Capability("sources.unpush", "sources", True, None, "source.unpush", cli="source unpush"),
    # ── commands ──
    Capability("commands.list", "commands", False, "commands/list", "list_slash_commands"),
)


def capability_names() -> frozenset[str]:
    """All canonical capability names."""
    return frozenset(c.name for c in CAPABILITIES)


def transports_of(name: str) -> frozenset[str]:
    """Rails serving one capability ('acp' / 'bridge' subsets)."""
    for cap in CAPABILITIES:
        if cap.name == name:
            return frozenset(t for t, v in (("acp", cap.acp), ("bridge", cap.bridge)) if v)
    return frozenset()
