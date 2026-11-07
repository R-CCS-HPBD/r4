"""Slash command registry for the unified TUI.

Extends the chat REPL's command system with platform-wide operations.
Each slash command maps to the same handler that powers the CLI subcommand,
ensuring consistent behavior across both interfaces.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import shlex
import sys
import threading
import time
from dataclasses import dataclass
from inspect import Parameter, signature
from typing import TYPE_CHECKING, Any, Callable, List

if TYPE_CHECKING:
    from typing import Any

    # UnifiedTUI was the deleted Python TUI app. The type annotation is kept
    # as a string forward-ref for the command handler signatures; at runtime
    # these handlers receive the bridge context object from command_bridge.py.
    UnifiedTUI = Any


@dataclass(frozen=True)
class SlashCommand:
    """A single slash command."""

    name: str
    handler: Callable[["UnifiedTUI", List[str]], None]
    help: str
    category: str = "general"
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlashSubcommand:
    """A discoverable subcommand for completion/help."""

    name: str
    help: str


SUBCOMMANDS: dict[str, tuple[SlashSubcommand, ...]] = {
    "endpoint": (
        SlashSubcommand("list", "list active (ready/degraded) endpoints"),
        SlashSubcommand("up", "provision an endpoint"),
        SlashSubcommand("down", "tear down an endpoint"),
        SlashSubcommand("status", "show endpoint status"),
        SlashSubcommand("help", "show endpoint help"),
    ),
    "mcp": (
        SlashSubcommand("list", "list servers and status"),
        SlashSubcommand("tools", "list all available tools"),
        SlashSubcommand("connect", "connect to servers"),
        SlashSubcommand("status", "connection summary"),
        SlashSubcommand("register", "show registration hint"),
    ),
    "rag": (
        SlashSubcommand("query", "query documents"),
        SlashSubcommand("upload", "upload a document"),
        SlashSubcommand("docs", "list indexed documents"),
        SlashSubcommand("list", "alias for docs"),
    ),
    "job": (
        SlashSubcommand("list", "list jobs"),
        SlashSubcommand("describe", "show job details"),
        SlashSubcommand("cancel", "cancel a job"),
        SlashSubcommand("retry", "retry a failed job"),
    ),
    "workflow": (
        SlashSubcommand("list", "list workflows"),
        SlashSubcommand("trigger", "trigger a workflow (params via --params-json/--param)"),
        SlashSubcommand("upload", "upload a workflow bundle"),
        SlashSubcommand("delete", "delete a registered workflow (owner/admin)"),
        SlashSubcommand("status", "show run status"),
        SlashSubcommand("cancel", "cancel a run"),
    ),
    "research": (
        SlashSubcommand("list", "list research loops"),
        SlashSubcommand("status", "show one loop's iteration DAG"),
        SlashSubcommand("start", "start an autonomous research loop"),
        SlashSubcommand("stop", "stop a running loop"),
        SlashSubcommand("session", "aggregate live research session"),
    ),
    "mode": (
        SlashSubcommand("chat", "normal chat mode"),
        SlashSubcommand("plan", "planning mode"),
        SlashSubcommand("autopilot", "autonomous execution mode"),
    ),
    "models": (
        SlashSubcommand("list", "list provisioned models (companion: interactive picker)"),
        SlashSubcommand("default", "save profile default: /models default [name|#]"),
    ),
    "model": (
        SlashSubcommand("list", "list provisioned models (companion: interactive picker)"),
        SlashSubcommand("default", "save profile default: /models default [name|#]"),
    ),
    "decision-model": (
        SlashSubcommand("on", "use the decision model (default Bespoke-Nimble-9B)"),
        SlashSubcommand("off", "stop using the decision model"),
        SlashSubcommand("status", "show whether a decision model is in use"),
    ),
    "skills": (
        SlashSubcommand("list", "list available skills"),
        SlashSubcommand("on", "activate a skill"),
        SlashSubcommand("off", "deactivate a skill"),
    ),
    "code": (
        SlashSubcommand("status", "detect languages, build systems, and tools"),
        SlashSubcommand("lsp", "inspect or start language-server backends"),
        SlashSubcommand("plan", "show suggested compile/run/test commands"),
        SlashSubcommand("diff", "show working-tree diff (stat + patch)"),
        SlashSubcommand("explain", "open a file with lines in the pane"),
        SlashSubcommand("edit", "agentic edit guided by --details text"),
        SlashSubcommand("review", "agentic review of a file or the diff"),
        SlashSubcommand("compile", "compile or syntax-check code"),
        SlashSubcommand("run", "run a source file or executable"),
        SlashSubcommand("test", "run detected tests"),
        SlashSubcommand("fix", "ask the companion to diagnose and iterate"),
    ),
    "artifact": (
        SlashSubcommand("list", "list artifacts"),
        SlashSubcommand("show", "show a single artifact"),
        SlashSubcommand("lineage", "show lineage graph"),
        SlashSubcommand("derive", "derive a new artifact from parent(s)"),
        SlashSubcommand("status", "update artifact status"),
    ),
    "run": (
        SlashSubcommand("list", "list agent executions"),
        SlashSubcommand("show", "show a single execution"),
        SlashSubcommand("create", "create an execution record"),
        SlashSubcommand("update", "update an execution"),
    ),
}


class SlashRegistry:
    """Registry + dispatcher for all TUI slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}
        self._register_builtins()

    def register(self, cmd: SlashCommand) -> None:
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd

    def dispatch(self, app: "UnifiedTUI", raw: str) -> bool:
        """Parse and run a slash command. Returns True if recognized."""
        if not raw.startswith("/"):
            return False
        try:
            parts = shlex.split(raw[1:])
        except ValueError as exc:
            app.renderer.error(f"could not parse command: {exc}")
            return True
        if not parts:
            return True
        name, args = parts[0], parts[1:]
        cmd = self._commands.get(name)
        if cmd is None:
            app.renderer.error(f"unknown command: /{name} (try /help)")
            return True
        try:
            cmd.handler(app, args)
        except SystemExit:
            raise
        except Exception as exc:
            app.renderer.error(f"/{name} failed: {exc}")
        return True

    def command_list(self) -> list[SlashCommand]:
        """Return unique commands grouped for display."""
        seen: set[str] = set()
        out: list[SlashCommand] = []
        for cmd in self._commands.values():
            if cmd.name in seen:
                continue
            seen.add(cmd.name)
            out.append(cmd)
        return out

    def commands_by_category(self) -> dict[str, list[SlashCommand]]:
        groups: dict[str, list[SlashCommand]] = {}
        for cmd in self.command_list():
            groups.setdefault(cmd.category, []).append(cmd)
        return groups

    def command_completion_items(self) -> list[tuple[str, str]]:
        """Return command/alias names with display metadata for completion."""
        items: list[tuple[str, str]] = []
        seen: set[str] = set()
        for name, command in self._commands.items():
            if name in seen:
                continue
            seen.add(name)
            help_text = command.help if name == command.name else f"alias for /{command.name}"
            items.append((name, help_text))
        return sorted(items)

    # --- built-in commands ---------------------------------------------------

    def _register_builtins(self) -> None:
        # Session
        self.register(
            SlashCommand("help", _cmd_help, "Show available commands", "help", ("h", "?"))
        )
        self.register(SlashCommand("exit", _cmd_exit, "Exit the TUI", "session", ("quit", "q")))
        self.register(
            SlashCommand("clear", _cmd_clear, "Reset conversation (keep config)", "session")
        )
        self.register(SlashCommand("new", _cmd_new, "Start a new conversation", "session"))
        self.register(
            SlashCommand(
                "overview",
                _cmd_overview,
                "Aggregate project/platform status snapshot (workflows, MCP, knowledge, catalog)",
                "project",
            )
        )

        # Mode
        self.register(
            SlashCommand("mode", _cmd_mode, "Show or set mode (chat/plan/autopilot)", "mode")
        )
        self.register(SlashCommand("plan", _cmd_plan, "Switch to plan mode", "mode"))

        # Model & endpoint
        self.register(
            SlashCommand(
                "models",
                _cmd_models,
                "List or choose provisioned models",
                "model",
                aliases=("model",),
            )
        )
        self.register(
            SlashCommand(
                "endpoint",
                _cmd_endpoint,
                "Endpoint operations (list, up, down, status)",
                "endpoint",
                ("endpoints",),
            )
        )
        self.register(SlashCommand("up", _cmd_up, "Alias for /endpoint up", "endpoint"))
        self.register(SlashCommand("down", _cmd_down, "Alias for /endpoint down", "endpoint"))
        self.register(SlashCommand("health", _cmd_health, "Check platform health", "endpoint"))
        self.register(
            SlashCommand(
                "dashboard",
                _cmd_dashboard,
                "Show live backend dashboard",
                "endpoint",
                ("status", "refresh"),
            )
        )

        # Tools & MCP
        self.register(SlashCommand("tools", _cmd_tools, "List available tools", "tools"))
        self.register(SlashCommand("mcp", _cmd_mcp, "MCP server management", "tools"))
        self.register(
            SlashCommand(
                "trust",
                _cmd_trust,
                "Switch to autopilot: tools run without asking (= /mode autopilot)",
                "mode",
                aliases=("yolo",),
            )
        )

        # Data & RAG
        self.register(
            SlashCommand("rag", _cmd_rag, "RAG operations (upload, query, status)", "data")
        )

        # Jobs & workflows
        self.register(SlashCommand("job", _cmd_job, "List/inspect jobs", "workflow"))
        self.register(SlashCommand("workflow", _cmd_workflow, "List/run workflows", "workflow"))
        self.register(
            SlashCommand(
                "research",
                _cmd_research,
                "Control autonomous research loops (list|status|start|stop)",
                "workflow",
            )
        )

        # Context & history
        self.register(
            SlashCommand(
                "context",
                _cmd_context,
                "Show context footprint (turns, messages, tokens)",
                "context",
            )
        )
        self.register(
            SlashCommand(
                "usage",
                _cmd_usage,
                "Show usage totals (LLM calls, tokens, node-hours)",
                "context",
            )
        )
        self.register(
            SlashCommand("compact", _cmd_compact, "Compress conversation history", "context")
        )
        self.register(
            SlashCommand("history", _cmd_history, "Show conversation transcript", "context")
        )
        self.register(SlashCommand("cost", _cmd_cost, "Show session token totals", "context"))

        # Export & session
        self.register(
            SlashCommand("export", _cmd_export, "Preview or save transcript markdown", "export")
        )
        self.register(
            SlashCommand(
                "save",
                _cmd_save,
                "Keep a turn in project knowledge: /save [n] [mine] [--vouch]; "
                "/save path.md writes the transcript",
                "export",
            )
        )
        self.register(
            SlashCommand("share", _cmd_share, "Export session to markdown or gist", "export")
        )
        self.register(
            SlashCommand(
                "sessions",
                _cmd_sessions,
                "List or resume saved sessions",
                "export",
                aliases=("resume",),
            )
        )

        # System & config
        self.register(
            SlashCommand("system", _cmd_system, "Set system prompt for this session", "config")
        )
        self.register(SlashCommand("config", _cmd_config, "Show current configuration", "config"))
        self.register(SlashCommand("retry", _cmd_retry, "Retry the last user message", "session"))
        self.register(
            SlashCommand(
                "progress",
                _cmd_progress,
                "Show harness progress orientation and milestones",
                "harness",
            )
        )
        self.register(
            SlashCommand("probe", _cmd_probe, "Re-scan harness environment probe", "harness")
        )
        self.register(
            SlashCommand(
                "lessons",
                _cmd_lessons,
                "Show steering lessons from past corrections",
                "harness",
            )
        )
        self.register(
            SlashCommand(
                "template",
                _cmd_template,
                "List or apply a harness workflow template",
                "harness",
                aliases=("templates",),
            )
        )

        # Skills & companion
        self.register(
            SlashCommand(
                "skills",
                _cmd_skills,
                "List/select/activate/deactivate skills",
                "tools",
                aliases=("skill",),
            )
        )
        self.register(
            SlashCommand("companion", _cmd_companion, "Companion research agent (alias)", "tools")
        )
        self.register(
            SlashCommand(
                "decision-model",
                _cmd_decision_model,
                "Use a decision model for fast typed judgments: on [model] | off | status",
                "model",
                aliases=("decision",),
            )
        )
        self.register(
            SlashCommand(
                "code", _cmd_code, "Polyglot code workbench (status, compile, run, test)", "tools"
            )
        )

        # A2L provenance
        self.register(
            SlashCommand(
                "artifact", _cmd_artifact, "A2L artifact list/show/lineage/derive/status", "data"
            )
        )
        self.register(
            SlashCommand(
                "run", _cmd_agent_exec, "A2L agent execution list/show/create/update", "data"
            )
        )

        # Conversational surfaces (UX redesign aliases)
        self.register(
            SlashCommand(
                "agent", _cmd_agent_surface, "AI agent (chat, run, create, tools)", "tools"
            )
        )
        self.register(
            SlashCommand("search", _cmd_search_surface, "Agentic multi-hop search", "tools")
        )
        self.register(
            SlashCommand("team", _cmd_team_surface, "Multi-agent HPC collaboration", "tools")
        )

        # Project
        self.register(SlashCommand("project", _cmd_project, "Project management", "project"))

        # Audit & traces (gap analysis M1, M2)
        self.register(
            SlashCommand(
                "audit",
                _cmd_audit,
                "View audit events (list, show, summary)",
                "governance",
            )
        )
        self.register(
            SlashCommand(
                "trace",
                _cmd_trace,
                "Inspect execution traces (list, show, lineage)",
                "governance",
                aliases=("traces",),
            )
        )

        # Checkpoints & recovery (gap analysis M5, M6)
        self.register(
            SlashCommand(
                "checkpoint",
                _cmd_checkpoint,
                "Job checkpoints (list, latest, show, delete)",
                "workflow",
                aliases=("ckpt",),
            )
        )
        self.register(
            SlashCommand(
                "recovery",
                _cmd_recovery,
                "Crash-recovery sessions (list, show)",
                "workflow",
            )
        )

        # Step replay (gap analysis M7)
        self.register(
            SlashCommand(
                "replay",
                _cmd_replay,
                "Step replay viewer (show <replay_id>)",
                "workflow",
            )
        )

        # Outcomes & lineage (gap analysis M8)
        self.register(
            SlashCommand(
                "outcome",
                _cmd_outcome,
                "Workflow outcomes (list, show, summary, by-model)",
                "workflow",
                aliases=("outcomes",),
            )
        )
        self.register(
            SlashCommand(
                "lineage",
                _cmd_lineage,
                "Show workflow lineage graph (lineage <job_id>)",
                "workflow",
            )
        )

        # Adaptive planning (gap analysis M4)
        self.register(
            SlashCommand(
                "adaptive",
                _cmd_adaptive,
                "Adaptive planning suggestions (suggest, heuristics)",
                "workflow",
            )
        )

        # Vector management (gap analysis M3)
        self.register(
            SlashCommand(
                "vectors",
                _cmd_vectors,
                "Vector version management (versions, active, summary, reembed)",
                "data",
                aliases=("vector",),
            )
        )

        # Governance (gap analysis M1)
        self.register(
            SlashCommand(
                "governance",
                _cmd_governance,
                "Resource governance (quotas, ownership, shares)",
                "governance",
                aliases=("gov",),
            )
        )

        # Knowledge search (gap analysis L1)
        self.register(
            SlashCommand(
                "knowledge",
                _cmd_knowledge,
                "Search the knowledge base (knowledge <query>)",
                "data",
                aliases=("kb",),
            )
        )

        # Visibility (gap analysis L4)
        self.register(
            SlashCommand(
                "visibility",
                _cmd_visibility,
                "Model visibility (show, update)",
                "model",
                aliases=("vis",),
            )
        )

        # Memory (gap analysis L5, L6)
        self.register(
            SlashCommand(
                "memory",
                _cmd_memory,
                "Reflective/context memory (list, search, add, delete, reset)",
                "context",
                aliases=("mem",),
            )
        )

        # Secret broker (gap analysis L3)
        self.register(
            SlashCommand(
                "secret",
                _cmd_secret,
                "Secret broker (list, show, lease, revoke, renew, audit, cleanup)",
                "governance",
            )
        )

        # Pipelines (gap analysis L2); CRUD retired 2026-10-18 (H-10.1)
        self.register(
            SlashCommand(
                "pipeline",
                _cmd_pipeline,
                "RAG component catalogs (components); pipeline CRUD retired",
                "data",
                aliases=("pipelines",),
            )
        )


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _cmd_help(app: "UnifiedTUI", args: List[str]) -> None:
    r = app.renderer
    r.system("R4 TUI Commands")
    r.info("")
    r.info("  shift+tab     cycle mode (chat → plan → autopilot)")
    r.info("  @path         attach a local file")
    r.info("  ctrl+c        cancel stream / ctrl+d exit")
    r.info("")
    for category, cmds in app.commands.commands_by_category().items():
        r.system(f"  {category}:")
        for cmd in cmds:
            aliases = f" ({', '.join('/' + a for a in cmd.aliases)})" if cmd.aliases else ""
            r.info(f"    /{cmd.name:<12} {cmd.help}{aliases}")
            subcommands = SUBCOMMANDS.get(cmd.name)
            if subcommands:
                names = ", ".join(subcommand.name for subcommand in subcommands)
                r.info(f"      subcommands: {names}")
        r.info("")


def _cmd_exit(app: "UnifiedTUI", args: List[str]) -> None:
    app.request_exit()


def _cmd_clear(app: "UnifiedTUI", args: List[str]) -> None:
    app.session.clear()
    app.renderer.clear()
    app.renderer.system("session cleared")


def _cmd_new(app: "UnifiedTUI", args: List[str]) -> None:
    app.session.clear()
    app.renderer.clear()
    app.renderer.system("new conversation started")
    app.show_banner()


_OVERVIEW_USAGE = (
    "usage: /overview [project-id] — platform snapshot "
    "(health, endpoints, models, research, skills)"
)


def _overview_arg_error(args: List[str]) -> str | None:
    """Why ``args`` is not ``[project-id]``: help, a flag, or extra words."""
    if not args:
        return None
    if args[0].lower() in {"help", "--help", "-h", "?"}:
        return _OVERVIEW_USAGE
    if len(args) > 1 or args[0].startswith("-"):
        return f"/overview: unexpected {' '.join(args)!r} — {_OVERVIEW_USAGE}"
    return None


def _cmd_overview(app: "UnifiedTUI", args: List[str]) -> None:
    """The platform snapshot as ONE block, scoped to an optional project.

    Each snapshot line used to be its own notice (~60 for one command), and
    any argument — 'help' included — was taken as a project id
    (stabilization 2026-10-21, slash-c#4).
    """
    problem = _overview_arg_error(args)
    if problem:
        app.renderer.info(problem)
        return
    try:
        from ..companion.status_report import build_project_status

        project = args[0] if args else None
        snapshot = build_project_status(app.client, project_id=project, include_local=True)
    except Exception as exc:
        app.renderer.error(f"/overview failed: {exc}")
        return
    scope = f"project {project}" if project else "current project"
    lines = snapshot.splitlines()
    body = "\n".join(line[3:] if line.startswith("## ") else line for line in lines)
    app.renderer.info(f"overview ({scope})\n{body}")


def _cmd_mode(app: "UnifiedTUI", args: List[str]) -> None:
    from .modes import Mode

    if not args:
        app.renderer.info(f"current mode: {app.mode.label} — {app.mode.description}")
        app.renderer.info("usage: /mode chat|plan|autopilot  (Shift+Tab cycles)")
        return
    try:
        new_mode = Mode(args[0].lower())
    except ValueError:
        app.renderer.error(f"unknown mode: {args[0]} (chat|plan|autopilot)")
        return
    app.set_mode(new_mode)


def _cmd_plan(app: "UnifiedTUI", args: List[str]) -> None:
    from .modes import Mode

    app.set_mode(Mode.PLAN)


def _cmd_models(app: "UnifiedTUI", args: List[str]) -> None:
    if args and args[0].lower() == "default":
        _cmd_model_default(app, args[1:])
        return
    if args and args[0].lower() in {"list", "ls"}:
        args = args[1:]
    _list_or_select_models(app, args)


def _cmd_model_default(app: "UnifiedTUI", args: List[str]) -> None:
    profile_default = getattr(app.client.config, "model", None)
    if args:
        options = _collect_model_options(app)
        if not options:
            app.renderer.error("no provisioned models found (run /endpoint up <model>)")
            return
        selected = _resolve_model_selection(options, args[0])
        if selected is None:
            app.renderer.error(f"unknown or unprovisioned model: {args[0]}")
            return
        model = selected
    else:
        model = app.session.model
        if not model:
            app.renderer.error("no model selected — use /models <name> or /models default <name>")
            if profile_default:
                app.renderer.info(f"  profile default is still: {profile_default}")
            return
    _persist_profile_default_model(app, model)
    app.session.model = model
    app.renderer.system(f"profile default → {model}")


def _model_status_value(model: object) -> str:
    status = getattr(model, "status", None)
    value = getattr(status, "value", None)
    return str(value or status or "available")


def _model_option_name(model: object) -> str | None:
    name = getattr(model, "name", None) or getattr(model, "model_id", None)
    return str(name) if name else None


def format_model_option_label(
    option: dict[str, str],
    *,
    current: bool = False,
    profile_default: str = "",
) -> str:
    name = option["name"]
    status = option.get("status", "")
    suffix = f" ({status})" if status else ""
    markers = ""
    if current:
        markers += " ← current"
    if (profile_default and name == profile_default) or option.get("is_profile_default"):
        markers += " ← default"
    return f"{name}{suffix}{markers}"


def _append_model_option(
    options: list[dict[str, str]],
    seen: set[str],
    *,
    name: str | None,
    source: str,
    status: str = "available",
) -> None:
    if not name or name in seen:
        return
    seen.add(name)
    options.append({"name": name, "source": source, "status": status})


def persist_profile_default_model(client: Any, model: str) -> None:
    """Save the profile default model to config.json and update the live client."""
    from r4.config import ProfileConfigStore

    config = client.config
    profile = getattr(config, "profile", "default")
    ProfileConfigStore().save(profile, model=model)
    config.model = model


def _persist_profile_default_model(app: "UnifiedTUI", model: str) -> None:
    persist_profile_default_model(app.client, model)


def _registry_model_options() -> list[dict[str, str]]:
    """Registry LLM models; a failed registry call RAISES.

    Swallowing it read as an empty registry: signed out, /endpoint up said
    "No registry models found — try /endpoint up <model>", pointing back at
    itself instead of the auth failure (stabilization 2026-10-21, slash-a#0).
    """
    from r4.models import ModelClient, ModelStatus, ModelType

    models = ModelClient().list_models(
        status=ModelStatus.AVAILABLE,
        model_type=ModelType.LLM,
    )

    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for model in models:
        _append_model_option(
            options,
            seen,
            name=_model_option_name(model),
            source="registry",
            status=_model_status_value(model),
        )
    return options


def _served_model_options(client: Any) -> list[dict[str, str]]:
    try:
        model_names = client.list_models()
    except Exception:
        return []
    if not isinstance(model_names, list):
        return []
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for model_name in model_names:
        _append_model_option(
            options,
            seen,
            name=str(model_name),
            source="served",
        )
    return options


def _endpoint_model_names(endpoint: dict) -> list[str]:
    names: list[str] = []
    models = endpoint.get("models")
    if isinstance(models, list):
        names.extend(str(model) for model in models if model)
    model = endpoint.get("model")
    if model:
        names.append(str(model))
    return names


def _endpoint_model_options(client: Any) -> list[dict[str, str]]:
    from r4.companion.model_resolution import endpoint_status_usable

    try:
        response = _call_client_with_timeout(
            client.list_endpoints,
            include_stopped=False,
            timeout=_interactive_command_timeout(),
        )
        endpoints = response.get("endpoints", [])
    except Exception:
        return []

    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        status = str(endpoint.get("status") or "unknown")
        if not endpoint_status_usable(status):
            continue
        for model_name in _endpoint_model_names(endpoint):
            _append_model_option(
                options,
                seen,
                name=model_name,
                source="endpoint",
                status=status,
            )
    return options


def _collect_endpoint_model_options(client: Any) -> list[dict[str, str]]:
    """Return deduplicated model choices from provisioned endpoints only."""
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    profile_default = getattr(getattr(client, "config", None), "model", "") or ""
    for source_options in (_endpoint_model_options(client),):
        for option in source_options:
            _append_model_option(
                options,
                seen,
                name=option["name"],
                source=option["source"],
                status=option.get("status", "available"),
            )
    for option in options:
        option["is_profile_default"] = option["name"] == profile_default
        option["label"] = format_model_option_label(
            option,
            profile_default=profile_default,
        )
    return options


def collect_model_options(client: Any) -> list[dict[str, str]]:
    """Model choices for pickers: endpoint models plus extra-source models.

    Public sources (Ollama, OpenAI-compatible) and peer platform sources
    appear as qualified ``model@source`` options; the agent resolves them via
    `r4.model_routing.resolve_model_route`.
    """
    from r4.tui.model_options import collect_bridge_model_options

    return collect_bridge_model_options(client)


def _collect_model_options(app: "UnifiedTUI") -> list[dict[str, str]]:
    return collect_model_options(app.client)


def _resolve_model_selection(options: list[dict[str, str]], selector: str) -> str | None:
    if selector.isdigit():
        index = int(selector) - 1
        if 0 <= index < len(options):
            return options[index]["name"]
        return None
    selector_lower = selector.lower()
    for option in options:
        if option["name"] == selector or option["name"].lower() == selector_lower:
            return option["name"]
    return None


def _list_or_select_models(app: "UnifiedTUI", args: List[str]) -> None:
    options = _collect_model_options(app)
    if not options:
        app.renderer.info("(no provisioned models found — use /endpoint up <model>)")
        return

    profile_default = getattr(app.client.config, "model", "") or ""
    app.renderer.info("provisioned models:")
    for index, option in enumerate(options, start=1):
        current = app.session.model
        line = format_model_option_label(
            option,
            current=bool(current and option["name"] == current),
            profile_default=profile_default,
        )
        app.renderer.info(f"  [{index}] {line}")

    app.renderer.info("  /models default — save current model to profile")
    app.renderer.info("  /models default <name|#> — save a specific model")

    if args:
        selected = _resolve_model_selection(options, args[0])
        if selected is None:
            app.renderer.error(f"unknown or unprovisioned model: {args[0]}")
            app.renderer.info("use /models to see provisioned models")
            return
        app.session.model = selected
        app.renderer.system(f"model → {selected}")


def _endpoint_models_label(endpoint: dict) -> str:
    names = _endpoint_model_names(endpoint)
    return ",".join(names) if names else "?"


def _endpoint_instance_id(endpoint: dict) -> str:
    return endpoint.get("instance_id") or endpoint.get("id") or endpoint.get("job_id") or "unknown"


def _endpoint_location(endpoint: dict) -> str:
    host = endpoint.get("host") or endpoint.get("node") or ""
    port = endpoint.get("port") or endpoint.get("litellm_port") or ""
    return f" {host}:{port}" if host and port else ""


def _endpoint_line(endpoint: dict) -> str:
    status = endpoint.get("status", "unknown")
    backend = endpoint.get("backend") or endpoint.get("execution_authority") or "unknown"
    return (
        f"  {_endpoint_instance_id(endpoint)}  {status}  "
        f"model={_endpoint_models_label(endpoint)} backend={backend}{_endpoint_location(endpoint)}"
    )


def _endpoint_list_display_limit() -> int | None:
    raw = os.environ.get("R4_TUI_ENDPOINT_LIST_LIMIT")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _interactive_command_timeout() -> float:
    raw = os.environ.get("R4_TUI_COMMAND_TIMEOUT_SEC") or os.environ.get(
        "R4_COMPANION_COMMAND_TIMEOUT_SEC"
    )
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = 15.0
    else:
        value = 15.0
    return max(1.0, value)


# The v3 TUI gives the whole dispatch_slash_command 60s (slash.ts), not just
# the provision submit: /endpoint up first resolves the model and checks for a
# reusable endpoint (each on the interactive budget). The submit gets what is
# left of a 50s command budget, so the command answers inside that window.
# The interactive floor still wins, so R4_TUI_COMMAND_TIMEOUT_SEC above ~45s
# (or slow earlier calls) can push the command past v3's window.
_PROVISION_SUBMIT_CAP_SEC = 50.0


def _provision_submit_timeout(elapsed: float = 0.0) -> float:
    """Timeout for the provision submit segment (TUI audit #15).

    Honours ``R4_PROVISION_SUBMIT_TIMEOUT_SEC`` (default 300, as the client
    does), capped at 50s minus the ``elapsed`` seconds the command already
    spent, and never below the interactive timeout.
    """
    try:
        submit = float(os.environ.get("R4_PROVISION_SUBMIT_TIMEOUT_SEC") or "300")
    except ValueError:
        submit = 300.0
    remaining = _PROVISION_SUBMIT_CAP_SEC - max(0.0, elapsed)
    return max(_interactive_command_timeout(), min(submit, remaining))


def _accepts_timeout(method) -> bool:
    try:
        parameters = signature(method).parameters
    except (TypeError, ValueError):
        return True
    return "timeout" in parameters or any(
        parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _call_client_with_timeout(method, *args, timeout: float | None = None, **kwargs):
    if timeout is not None and _accepts_timeout(method):
        kwargs["timeout"] = timeout
    return method(*args, **kwargs)


def _is_reusable_endpoint(endpoint: dict) -> bool:
    status = str(endpoint.get("status") or "").strip().lower()
    return status not in {"", "failed", "stopped", "terminated", "cancelled", "expired"}


def _active_endpoint_for_model(app: "UnifiedTUI", model: str) -> dict | None:
    try:
        response = _call_client_with_timeout(
            app.client.list_endpoints,
            include_stopped=False,
            timeout=_interactive_command_timeout(),
        )
    except Exception as exc:
        app.renderer.info(f"could not check existing endpoints before provisioning: {exc}")
        return None

    for endpoint in response.get("endpoints", []):
        if not isinstance(endpoint, dict) or not _is_reusable_endpoint(endpoint):
            continue
        if model in _endpoint_model_names(endpoint):
            return endpoint
    return None


def _render_endpoint_reuse(app: "UnifiedTUI", model: str, endpoint: dict) -> None:
    instance_id = (
        endpoint.get("instance_id") or endpoint.get("id") or endpoint.get("job_id") or "unknown"
    )
    app.renderer.system(f"endpoint already active for {model}: {instance_id}")
    app.renderer.info(_endpoint_line(endpoint))
    app.renderer.info(
        "  use /endpoint status <id> to follow progress, "
        "or /endpoint up --new <model> to request another endpoint"
    )


def _running_model_names(client: Any) -> set[str]:
    try:
        response = _call_client_with_timeout(
            client.list_endpoints,
            include_stopped=False,
            timeout=_interactive_command_timeout(),
        )
        endpoints = response.get("endpoints", [])
    except Exception:
        return set()

    running: set[str] = set()
    for endpoint in endpoints:
        if not isinstance(endpoint, dict) or not _is_reusable_endpoint(endpoint):
            continue
        running.update(_endpoint_model_names(endpoint))
    return running


def collect_registry_model_options(client: Any) -> list[dict[str, str]]:
    """Registry LLM models that can be provisioned via /endpoint up."""
    options = _registry_model_options()
    running = _running_model_names(client)
    for option in options:
        label = format_model_option_label(option)
        if option["name"] in running:
            label = f"{label} ← running"
            option["running"] = True
        else:
            option["running"] = False
        option["label"] = label
    return options


def _render_endpoint_list_page(
    app: "UnifiedTUI",
    endpoints: list,
    total: Any,
    include_stopped: bool,
) -> None:
    if not endpoints:
        app.renderer.info("(no endpoints — use /endpoint up <model>)")
        return
    if total is None:
        total = len(endpoints)
    scope = "all" if include_stopped else "active"
    app.renderer.info(f"endpoints ({total} {scope}):")
    limit = _endpoint_list_display_limit()
    visible = endpoints if limit is None else endpoints[:limit]
    for endpoint in visible:
        if isinstance(endpoint, dict):
            app.renderer.info(_endpoint_line(endpoint))
    remaining = len(endpoints) - len(visible)
    if remaining > 0:
        cli_hint = "r4 endpoint list --all" if include_stopped else "r4 endpoint list"
        app.renderer.info(f"  (... and {remaining} more — use `{cli_hint}` for the full table)")


def _cmd_endpoint_list(app: "UnifiedTUI", include_stopped: bool = False) -> None:
    try:
        response = _call_client_with_timeout(
            app.client.list_endpoints,
            include_stopped=include_stopped,
            timeout=_interactive_command_timeout(),
        )
    except Exception as exc:
        app.renderer.error(f"could not list endpoints: {exc}")
        return
    _render_endpoint_list_page(
        app,
        response.get("endpoints", []),
        response.get("total"),
        include_stopped,
    )


def _cmd_endpoint_status(app: "UnifiedTUI", args: List[str]) -> None:
    if not args:
        _cmd_endpoint_list(app, include_stopped=True)
        return
    instance_id = args[0]
    try:
        endpoint = _call_client_with_timeout(
            app.client.get_endpoint_status,
            instance_id,
            timeout=_interactive_command_timeout(),
        )
    except Exception as exc:
        app.renderer.error(f"could not get endpoint status: {exc}")
        return
    app.renderer.info(_endpoint_line(endpoint))
    detail = endpoint.get("detail") if isinstance(endpoint, dict) else None
    if detail:
        app.renderer.info(f"  detail: {detail}")


def _render_endpoint_help(app: "UnifiedTUI") -> None:
    app.renderer.info("Endpoint commands:")
    app.renderer.info("  /endpoint                  — list active endpoints, then this help")
    app.renderer.info("  /endpoint list             — active (ready/degraded) only")
    app.renderer.info("  /endpoint list --all       — all statuses; full history")
    app.renderer.info("  /endpoint up               — provision (companion: opens picker)")
    app.renderer.info("  /endpoint up <model>       — provision a named registry model")
    app.renderer.info("  /endpoint up --new         — force new endpoint (companion: picker)")
    app.renderer.info("  /endpoint down             — stop endpoint (companion: opens picker)")
    app.renderer.info("  /endpoint down <id>        — stop by instance id")
    app.renderer.info("  /endpoint status [id]      — show endpoint status")
    app.renderer.info("  /up and /down              — shorthand for /endpoint up|down")


def _cmd_endpoint(app: "UnifiedTUI", args: List[str]) -> None:
    if not args:
        _cmd_endpoint_list(app)
        _render_endpoint_help(app)
        return

    subcommand = args[0].lower()
    rest = args[1:]
    if subcommand in {"list", "ls"}:
        _cmd_endpoint_list(app, include_stopped="--all" in rest or "all" in rest)
    elif subcommand in {"up", "start", "provision"}:
        _cmd_up(app, rest)
    elif subcommand in {"down", "stop", "delete", "deprovision"}:
        _cmd_down(app, rest)
    elif subcommand in {"status", "show", "get"}:
        _cmd_endpoint_status(app, rest)
    elif subcommand in {"help", "?"}:
        _render_endpoint_help(app)
    else:
        app.renderer.error(f"unknown endpoint sub-command: {subcommand}")
        _render_endpoint_help(app)


def _up_parse_args(args: List[str]) -> tuple[bool, list[str]]:
    request_new = False
    filtered_args: list[str] = []
    for arg in args:
        if arg in {"--new", "--force", "--no-reuse"}:
            request_new = True
        else:
            filtered_args.append(arg)
    return request_new, filtered_args


def _registry_options_or_report(app: "UnifiedTUI") -> list[dict[str, str]] | None:
    """Registry options, or None after rendering why the registry call failed."""
    try:
        return collect_registry_model_options(app.client)
    except Exception as exc:  # noqa: BLE001 - shown to the user, not swallowed.
        app.renderer.error(f"could not list registry models: {exc}")
        return None


def _up_pick_model(app: "UnifiedTUI", filtered_args: list[str]) -> str | None:
    model = filtered_args[0] if filtered_args else None
    if not model:
        options = _registry_options_or_report(app)
        if options is None:
            return None
        if options:
            app.renderer.info("registry models (pick one with /endpoint up <name-or-#>):")
            for index, option in enumerate(options, start=1):
                app.renderer.info(f"  [{index}] {option.get('label') or option['name']}")
            return None
        model = getattr(app.session, "model", None)
        if not model:
            app.renderer.info("usage: /endpoint up <model>")
            app.renderer.info("  no registry models found; check gateway connection with /health")
            return None
    return model


def _up_resolve_selection(app: "UnifiedTUI", model: str) -> str | None:
    options = _registry_options_or_report(app)
    if options is None:
        return None
    resolved = _resolve_model_selection(options, model)
    if resolved is None:
        app.renderer.error(f"invalid selection: {model}")
        return None
    return resolved


def _up_provision(app: "UnifiedTUI", model: str, started: float) -> dict | None:
    app.renderer.info(f"provisioning endpoint for {model}…")
    try:
        return _call_client_with_timeout(
            app.client.provision_endpoint,
            model=model,
            wait=False,
            timeout=_provision_submit_timeout(time.monotonic() - started),
        )
    except Exception as exc:
        app.renderer.error(f"provision failed: {exc}")
        return None


def _cmd_up(app: "UnifiedTUI", args: List[str]) -> None:
    started = time.monotonic()
    request_new, filtered_args = _up_parse_args(args)

    model = _up_pick_model(app, filtered_args)
    if model is None:
        return

    if model.isdigit():
        model = _up_resolve_selection(app, model)
        if model is None:
            return

    if not request_new:
        existing = _active_endpoint_for_model(app, model)
        if existing is not None:
            _render_endpoint_reuse(app, model, existing)
            return

    response = _up_provision(app, model, started)
    if response is None:
        return

    instance_id = response.get("instance_id") or response.get("job_id") or "unknown"
    status = str(response.get("status", "submitted"))
    app.renderer.system(f"endpoint provision requested: {instance_id}")
    app.renderer.info(f"  model={model} status={status}")
    app.renderer.info("  use /endpoint status <id> or /endpoint list to follow progress")


_DOWN_TERMINAL_STATUSES = frozenset({"failed", "terminated", "expired", "deprovisioned"})


def _stoppable_endpoints(endpoints: list[object]) -> list[dict]:
    stoppable: list[dict] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        status = str(endpoint.get("status") or "").strip().lower()
        if status in _DOWN_TERMINAL_STATUSES:
            continue
        stoppable.append(endpoint)
    return stoppable


def _endpoint_instance_ref(endpoint: dict) -> str:
    return str(
        endpoint.get("instance_id") or endpoint.get("id") or endpoint.get("job_id") or ""
    ).strip()


def collect_stoppable_endpoint_options(client: Any) -> list[dict[str, str]]:
    """Endpoints /endpoint down can stop; a failed gateway call RAISES (the
    bridge reports its message and hint), never "No active endpoints to stop"
    (slash-a#0)."""
    response = _call_client_with_timeout(
        client.list_endpoints,
        include_stopped=True,
        timeout=_interactive_command_timeout(),
    )
    endpoints = response.get("endpoints", [])

    options: list[dict[str, str]] = []
    for endpoint in _stoppable_endpoints(endpoints):
        instance_id = _endpoint_instance_ref(endpoint)
        if not instance_id:
            continue
        options.append(
            {
                "id": instance_id,
                "label": _endpoint_line(endpoint).strip(),
                "status": str(endpoint.get("status") or ""),
            }
        )
    return options


def _resolve_down_instance(
    app: "UnifiedTUI",
    stoppable: list[dict],
    selector: str | None,
) -> str | None:
    """Resolve the instance id to stop, or ``None`` when the user must pick."""
    instance_id = selector
    if not instance_id:
        if len(stoppable) == 1:
            return _endpoint_instance_ref(stoppable[0])
        app.renderer.info("active endpoints — pick one to stop:")
        for index, endpoint in enumerate(stoppable, start=1):
            app.renderer.info(f"  [{index}] {_endpoint_line(endpoint)}")
        app.renderer.info("usage: /endpoint down <instance-id|#>")
        return None

    # A numeric job id is an instance ref, not a row number in this new list.
    if instance_id.isdigit() and instance_id not in map(_endpoint_instance_ref, stoppable):
        index = int(instance_id) - 1
        if 0 <= index < len(stoppable):
            return _endpoint_instance_ref(stoppable[index])
        app.renderer.error(f"invalid selection: {selector}")
        return None

    if not instance_id:
        app.renderer.error("could not resolve endpoint id")
        return None
    return instance_id


def _cmd_down(app: "UnifiedTUI", args: List[str]) -> None:
    """Tear down an endpoint without delegating to the CLI (keeps the bridge alive)."""
    force = "--force" in args
    filtered = [arg for arg in args if arg != "--force"]
    selector = filtered[0] if filtered else None

    try:
        response = _call_client_with_timeout(
            app.client.list_endpoints,
            include_stopped=True,
            timeout=_interactive_command_timeout(),
        )
        endpoints = response.get("endpoints", [])
    except Exception as exc:
        app.renderer.error(f"could not list endpoints: {exc}")
        return

    stoppable = _stoppable_endpoints(endpoints)
    if not stoppable:
        app.renderer.info("(no active endpoints to stop)")
        return

    instance_id = _resolve_down_instance(app, stoppable, selector)
    if not instance_id:
        return

    app.renderer.info(f"stopping endpoint {instance_id}…")
    try:
        result = _call_client_with_timeout(
            app.client.stop_endpoint,
            instance_id,
            force=force,
            timeout=_interactive_command_timeout(),
        )
    except Exception as exc:
        app.renderer.error(f"teardown failed: {exc}")
        return

    status = str(result.get("status", "stopped"))
    app.renderer.system(f"endpoint {instance_id} → {status}")


def _cmd_health(app: "UnifiedTUI", args: List[str]) -> None:
    try:
        readiness = app.client.readiness()
        if readiness.get("ready"):
            app.renderer.system("✓ gateway reachable and ready")
        else:
            app.renderer.error("⚠ gateway reachable but not ready")
            for k, v in readiness.get("checks", {}).items():
                if str(v) != "ok":
                    app.renderer.info(f"  {k}: {v}")
    except Exception:
        app.renderer.error("✗ gateway unreachable")


def _cmd_dashboard(app: "UnifiedTUI", args: List[str]) -> None:
    render_dashboard = getattr(app, "render_dashboard", None)
    if callable(render_dashboard):
        render_dashboard()
        return
    app.renderer.info("dashboard requires the full UnifiedTUI app")


def _tool_display_name(tool: object) -> str:
    name = getattr(tool, "name", None)
    if name:
        return str(name)
    if isinstance(tool, dict):
        return str(tool.get("name", tool))
    return str(tool)


def _tool_display_description(tool: object) -> str:
    desc = getattr(tool, "description", None)
    if desc:
        return str(desc)
    if isinstance(tool, dict):
        return str(tool.get("description", ""))
    return ""


def _cmd_tools(app: "UnifiedTUI", args: List[str]) -> None:
    local = list(getattr(app, "local_tools", []))
    if local:
        app.renderer.info("local tools:")
        for tool in local:
            app.renderer.info(f"  {tool['name']}  —  {tool['description']}")
    try:
        tools = app.client.mcp.list_tools()
    except Exception:
        tools = []
    if not tools and not local:
        app.renderer.info("(no tools available — use /mcp to configure)")
        return
    if not tools:
        return
    app.renderer.info("mcp tools:")
    for tool in tools:
        app.renderer.info(f"  {_tool_display_name(tool)}  —  {_tool_display_description(tool)}")


def _render_mcp_help(app: "UnifiedTUI") -> None:
    app.renderer.info("MCP server commands:")
    app.renderer.info("  /mcp list       — list servers and status")
    app.renderer.info("  /mcp tools      — list all available tools")
    app.renderer.info("  /mcp connect    — connect to all servers")
    app.renderer.info("  /mcp status     — connection summary")
    app.renderer.info("  /mcp register   — register a server (use r4 mcp register)")


def _mcp_list_gateway_servers(app: "UnifiedTUI") -> None:
    # Fallback to gateway API
    try:
        servers = app.client.mcp.list_servers()
        if not servers:
            app.renderer.info("(no MCP servers)")
            return
        for s in servers:
            name = s.get("name", "?") if isinstance(s, dict) else str(s)
            app.renderer.info(f"  ● {name}")
    except Exception as exc:
        app.renderer.error(f"could not list servers: {exc}")


def _mcp_list(app: "UnifiedTUI") -> None:
    connections = app.mcp_manager.list_connections()
    if not connections:
        _mcp_list_gateway_servers(app)
        return
    for conn in connections:
        app.renderer.info(f"  {conn.status_line()}  [{conn.config.source}]")


def _mcp_connect(app: "UnifiedTUI", args: List[str]) -> None:
    target = args[1] if len(args) > 1 else None
    if target:
        ok = app.mcp_manager.connect(target)
        app.renderer.info(f"{'connected' if ok else 'failed'}: {target}")
    else:
        results = app.mcp_manager.connect_all()
        for name, ok in results.items():
            status = "●" if ok else "✗"
            app.renderer.info(f"  {status} {name}")


def _cmd_mcp(app: "UnifiedTUI", args: List[str]) -> None:
    if not args:
        _render_mcp_help(app)
        return
    sub = args[0]
    if sub in ("list", "ls"):
        _mcp_list(app)
    elif sub == "tools":
        _cmd_tools(app, [])
    elif sub == "connect":
        _mcp_connect(app, args)
    elif sub == "status":
        app.renderer.info(f"MCP: {app.mcp_manager.summary()}")
    elif sub == "register":
        app.renderer.info("use `r4 mcp register <name> --command ...` for server registration")
    else:
        app.renderer.error(f"unknown mcp sub-command: {sub}")


def _cmd_trust(app: "UnifiedTUI", args: List[str]) -> None:
    """ADR-104 D-1: one mode; /trust is /mode autopilot."""
    from .modes import Mode

    app.set_mode(Mode.AUTOPILOT)


def _render_rag_help(app: "UnifiedTUI") -> None:
    app.renderer.info("RAG commands:")
    app.renderer.info("  /rag query <text>    — query documents")
    app.renderer.info("  /rag upload <file>   — upload a document")
    app.renderer.info("  /rag docs [project]  — list indexed documents")
    app.renderer.info("  /rag docs delete <id> --yes — delete a document")


def _tui_project_id(app: "UnifiedTUI") -> str:
    config = getattr(app.client, "config", None)
    project_id = getattr(config, "project_id", None)
    if project_id:
        return project_id
    try:
        from r4.config import get_config

        return get_config().project_id or "default"
    except Exception:
        return "default"


def _cmd_rag_query(app: "UnifiedTUI", args: List[str]) -> None:
    if len(args) <= 1:
        app.renderer.info("usage: /rag query <text>")
        return
    query = " ".join(args[1:])
    app.renderer.info(f"querying: {query}")
    try:
        # Client has no rag_query; the RAG facade is client.rag (audit #11).
        result = app.client.rag.query(query, project_id=_tui_project_id(app))
    except Exception as exc:
        app.renderer.error(f"RAG query failed: {exc}")
        return
    app.renderer.system(str(getattr(result, "answer", "") or "(no answer)"))
    for index, source in enumerate(getattr(result, "sources", None) or [], 1):
        app.renderer.info(_rag_citation_line(index, source))


def _rag_citation_line(index: int, source: object) -> str:
    """One numbered citation: the source's name (else its id) and score."""
    name = getattr(source, "source_name", "") or getattr(source, "document_id", "") or "?"
    try:
        score = f"{float(getattr(source, 'score', 0.0) or 0.0):.2f}"
    except (TypeError, ValueError):
        score = "?"
    return f"[{index}] {name} (score: {score})"


def _cmd_rag_upload(app: "UnifiedTUI", args: List[str]) -> None:
    """Upload one file into the project's knowledge (K1.3 F1: it printed
    "uploading" and a hint, and uploaded nothing)."""
    if len(args) <= 1:
        app.renderer.info("usage: /rag upload <file>")
        return
    path = os.path.expanduser(" ".join(args[1:]))
    project_id = _tui_project_id(app)
    app.renderer.info(f"uploading: {path} → project {project_id}")
    try:
        result = app.client.rag.upload(path, project_id=project_id)
    except Exception as exc:
        app.renderer.error(f"RAG upload failed: {exc}")
        return
    status = str(getattr(result, "status", "") or "")
    if status.startswith("queued"):
        # The id a queued upload returns is the ingest job's, not the
        # document's; the item lists once that job has stored it (review of
        # K1 part B, 2026-10-24).
        app.renderer.info(
            f"queued for knowledge ingest (job {getattr(result, 'document_id', '')}) — "
            "it lists in /rag docs once the job has stored it; retrieval finds it "
            "once embedding finishes"
        )
        return
    app.renderer.info(f"uploaded: {getattr(result, 'document_id', '')} ({status or 'ok'})")


def _first_doc_value(doc: dict, keys: tuple[str, ...], default: object) -> object:
    for key in keys:
        value = doc.get(key)
        if value not in (None, ""):
            return value
    return default


def _rag_doc_label(doc: dict) -> str:
    doc_id = str(_first_doc_value(doc, ("document_id", "id"), "?"))
    name = _first_doc_value(doc, ("filename", "name", "path"), doc_id)
    status = _first_doc_value(doc, ("status", "state"), "indexed")
    chunks = _first_doc_value(doc, ("chunks", "chunks_created", "chunk_count"), None)
    suffix = f"  chunks={chunks}" if chunks is not None else ""
    return f"  {doc_id}  {status}  {name}{suffix}"


def _cmd_rag_docs_delete(app: "UnifiedTUI", args: List[str]) -> None:
    """``/rag docs delete <id> --yes`` (K1.3 F7: it listed a project named
    "delete"). The delete is irreversible (the artifact and everything
    derived from it), so it needs ``--yes``, as ``r4 knowledge docs delete``
    asks first (review of K1 part B, 2026-10-24)."""
    rest = [a for a in args[2:] if a not in ("--yes", "-y")]
    if len(rest) != 1:
        app.renderer.info("usage: /rag docs delete <document-id> --yes")
        return
    project_id = _tui_project_id(app)
    if len(rest) == len(args) - 2:
        app.renderer.info(
            f"/rag docs delete removes {rest[0]} and everything derived from it in "
            f"project {project_id}; add --yes to delete"
        )
        return
    try:
        result = app.client.rag.delete_document(rest[0], project_id=project_id)
    except Exception as exc:
        app.renderer.error(f"RAG delete failed: {exc}")
        return
    counts = getattr(result, "counts", None) or {}
    detail = ", ".join(f"{k}={v}" for k, v in counts.items() if v)
    app.renderer.info(f"deleted {rest[0]} from project {project_id}  {detail}".rstrip())


def _cmd_rag_docs(app: "UnifiedTUI", args: List[str]) -> None:
    if len(args) > 1 and args[1] == "delete":
        _cmd_rag_docs_delete(app, args)
        return
    if len(args) > 1 and args[1] == "list":
        args = args[1:]
    project_id = args[1] if len(args) > 1 else _tui_project_id(app)
    try:
        docs = app.client.rag.list_documents(project_id=project_id)
    except Exception as exc:
        app.renderer.error(f"RAG docs failed: {exc}")
        return
    if not docs:
        app.renderer.info(f"(no indexed documents for project {project_id})")
        return
    app.renderer.info(f"indexed documents for project {project_id}:")
    for doc in docs[:20]:
        app.renderer.info(_rag_doc_label(doc))


def _cmd_rag(app: "UnifiedTUI", args: List[str]) -> None:
    if not args:
        _render_rag_help(app)
        return
    handlers = {
        "query": _cmd_rag_query,
        "upload": _cmd_rag_upload,
        "docs": _cmd_rag_docs,
        "documents": _cmd_rag_docs,
        "list": _cmd_rag_docs,
    }
    handler = handlers.get(args[0])
    if handler is None:
        app.renderer.info("usage: /rag query|upload|docs")
        return
    handler(app, args)


def _cmd_job(app: "UnifiedTUI", args: List[str]) -> None:
    subcommand = args[0].lower() if args else "list"
    rest = args[1:]

    if subcommand in {"list", "ls"}:
        _cmd_job_list(app)
        return
    if subcommand in {"describe", "status", "show", "get"}:
        _cmd_job_describe(app, rest)
        return
    if subcommand == "cancel":
        _cmd_job_action(app, rest, action="cancel")
        return
    if subcommand == "retry":
        _cmd_job_action(app, rest, action="retry")
        return
    app.renderer.error(f"unknown job sub-command: {subcommand}")
    app.renderer.info("usage: /job list|describe|cancel|retry")


def _job_client(app: "UnifiedTUI"):
    return getattr(app.client, "jobs", None)


def _record_value(record: object, *keys: str, default: str = "?") -> str:
    for key in keys:
        if isinstance(record, dict):
            value = record.get(key)
        else:
            value = getattr(record, key, None)
        if value not in (None, ""):
            return str(value)
    return default


def _job_line(job_record: object) -> str:
    job_id = _record_value(job_record, "id", "job_id")
    job_type = _record_value(job_record, "type", "job_type", default="")
    status = _record_value(job_record, "status")
    attempts = _record_value(job_record, "attempts", default="")
    return f"  {job_id}  {status}  {job_type}  {attempts}".rstrip()


def _cmd_job_list(app: "UnifiedTUI") -> None:
    try:
        client_jobs = _job_client(app)
        if client_jobs is not None and hasattr(client_jobs, "list"):
            jobs = client_jobs.list(limit=20)
        else:
            response = app.client.list_jobs()
            jobs = response.get("jobs", []) if isinstance(response, dict) else response
        if not jobs:
            app.renderer.info("(no jobs)")
            return
        for job_record in list(jobs)[:10]:
            app.renderer.info(_job_line(job_record))
    except Exception as exc:
        app.renderer.error(f"could not list jobs: {exc}")


def _cmd_job_describe(app: "UnifiedTUI", args: List[str]) -> None:
    if not args:
        app.renderer.info("usage: /job describe <job_id>")
        return
    client_jobs = _job_client(app)
    if client_jobs is None or not hasattr(client_jobs, "get"):
        app.renderer.info("job details require the UMRS job client")
        return
    try:
        app.renderer.info(_job_line(client_jobs.get(args[0])))
    except Exception as exc:
        app.renderer.error(f"could not describe job: {exc}")


def _cmd_job_action(app: "UnifiedTUI", args: List[str], *, action: str) -> None:
    if not args:
        app.renderer.info(f"usage: /job {action} <job_id>")
        return
    client_jobs = _job_client(app)
    method = getattr(client_jobs, action, None) if client_jobs is not None else None
    if not callable(method):
        app.renderer.info(f"job {action} requires the UMRS job client")
        return
    try:
        job_record = method(args[0])
        app.renderer.system(f"job {action}: {_record_value(job_record, 'job_id', 'id')}")
        app.renderer.info(_job_line(job_record))
    except Exception as exc:
        app.renderer.error(f"could not {action} job: {exc}")


def _cmd_workflow(app: "UnifiedTUI", args: List[str]) -> None:
    subcommand = args[0].lower() if args else "list"
    rest = args[1:]

    if subcommand in {"list", "ls"}:
        _cmd_workflow_list(app)
        return
    if subcommand in {"status", "show", "get"}:
        _cmd_workflow_status(app, rest)
        return
    if subcommand in {"cancel", "stop"}:
        _cmd_workflow_cancel(app, rest)
        return
    if subcommand in {"trigger", "run"}:
        _cmd_workflow_trigger(app, rest)
        return
    if subcommand == "upload":
        _cmd_workflow_upload(app, rest)
        return
    if subcommand == "delete":
        _cmd_workflow_delete(app, rest)
        return
    app.renderer.error(f"unknown workflow sub-command: {subcommand}")
    app.renderer.info("usage: /workflow list|trigger|upload|delete|status|cancel")


def _workflow_client(app: "UnifiedTUI"):
    return getattr(app.client, "workflows", None)


def _workflow_line(workflow_record: object) -> str:
    name = _record_value(workflow_record, "name", "workflow_id")
    status = _record_value(workflow_record, "status", default="")
    description = _record_value(workflow_record, "description", default="")
    return f"  {name}  {status}  {description}".rstrip()


def _status_line(status_record: object) -> str:
    execution_id = _record_value(status_record, "execution_id", "id")
    status = _record_value(status_record, "status")
    progress = _record_value(status_record, "progress", default="")
    return f"  {execution_id}  {status}  {progress}".rstrip()


def _cmd_workflow_list(app: "UnifiedTUI") -> None:
    try:
        workflow_client = _workflow_client(app)
        if workflow_client is not None and hasattr(workflow_client, "list_workflows"):
            workflows = workflow_client.list_workflows()
        else:
            response = app.client.list_workflows()
            workflows = response.get("workflows", []) if isinstance(response, dict) else response
        if not workflows:
            app.renderer.info("(no workflows)")
            return
        for workflow_record in list(workflows)[:10]:
            app.renderer.info(_workflow_line(workflow_record))
    except Exception as exc:
        app.renderer.error(f"could not list workflows: {exc}")


def _cmd_workflow_status(app: "UnifiedTUI", args: List[str]) -> None:
    if not args:
        app.renderer.info("usage: /workflow status <execution_id>")
        return
    workflow_client = _workflow_client(app)
    method = getattr(workflow_client, "status", None) if workflow_client is not None else None
    if not callable(method):
        app.renderer.info("workflow status requires the workflow client")
        return
    try:
        app.renderer.info(_status_line(method(args[0])))
    except Exception as exc:
        app.renderer.error(f"could not get workflow status: {exc}")


def _cmd_workflow_cancel(app: "UnifiedTUI", args: List[str]) -> None:
    if not args:
        app.renderer.info("usage: /workflow cancel <execution_id>")
        return
    workflow_client = _workflow_client(app)
    method = getattr(workflow_client, "cancel", None) if workflow_client is not None else None
    if not callable(method):
        app.renderer.info("workflow cancel requires the workflow client")
        return
    try:
        result = method(args[0])
    except Exception as exc:
        app.renderer.error(f"could not cancel workflow: {exc}")
        return
    _render_workflow_cancel(app, args[0], result)


def _cmd_workflow_delete(app: "UnifiedTUI", args: List[str]) -> None:
    """Delete a registered workflow (owner/admin, server-enforced).

    The explicitly typed name IS the confirmation: a JSONL renderer cannot
    answer the CLI's interactive [y/N] (2026-10-19 — v3 forwards unknown
    workflow verbs here, so this lands `/workflow delete` in both consoles).
    """
    if not args:
        app.renderer.info("usage: /workflow delete <workflow_name>")
        return
    workflow_client = _workflow_client(app)
    method = getattr(workflow_client, "delete", None) if workflow_client is not None else None
    if not callable(method):
        app.renderer.info("workflow delete requires the workflow client")
        return
    name = args[0]
    try:
        result = method(name)
    except Exception as exc:
        app.renderer.error(f"could not delete workflow: {exc}")
        return
    message = _record_value(result, "message", default="") if isinstance(result, dict) else ""
    app.renderer.system(message or f"workflow deleted: {name}")


_WORKFLOW_ENDED_STATUSES = frozenset({"succeeded", "failed", "completed"})


def _render_workflow_cancel(app: "UnifiedTUI", execution_id: str, result: object) -> None:
    """Report the status UMRS returned; the cancel route answers 200 for an ended run too."""
    status = _record_value(result, "status").lower()
    if status == "cancelled":
        app.renderer.system(f"workflow cancelled: {execution_id}")
    elif status in _WORKFLOW_ENDED_STATUSES:
        app.renderer.info(f"workflow {execution_id} already {status}; nothing to cancel")
    else:
        app.renderer.system(f"workflow cancel requested: {execution_id} (status={status})")


_WORKFLOW_TRIGGER_USAGE = (
    "usage: /workflow trigger <workflow_name> "
    "[--param k=v] [--params-json obj] [--params-file f] "
    "[--input-file f] [--project p] [--wait]"
)


def _cmd_workflow_trigger(app: "UnifiedTUI", args: List[str]) -> None:
    """Trigger a UMRS workflow from the console with full input definition.

    Usage:
      /workflow trigger <name> [--param k=v]* [--params-json '<obj>']
                               [--params-file f] [--input-file f]*
                               [--project p] [--wait]
    """
    if not args:
        app.renderer.info(_WORKFLOW_TRIGGER_USAGE)
        return

    plan = _workflow_trigger_plan(app, args)
    if plan is None:
        return

    workflow_client = _workflow_client(app)
    method = getattr(workflow_client, "trigger", None) if workflow_client is not None else None
    if not callable(method):
        app.renderer.info("workflow trigger requires the workflow client")
        return
    # --wait is bounded: the bridge serves one request at a time, so an
    # unbounded poll would hang every later RPC until the HPC run ends.
    wait_kwargs = {"timeout": _workflow_wait_timeout()} if plan["wait"] else {}
    try:
        result = method(
            workflow_id=plan["workflow_name"],
            parameters=plan["parameters"],
            input_files=plan["input_files"],
            project_id=plan["project_id"],
            wait=plan["wait"],
            **wait_kwargs,
        )
        app.renderer.system(f"workflow triggered: {_record_value(result, 'execution_id', 'id')}")
        app.renderer.info(_status_line(result))
    except Exception as exc:
        _render_workflow_trigger_error(app, exc, waited=plan["wait"])


_WAIT_TIMEOUT_RE = re.compile(r"Timed out waiting for execution (\S+)")

# The same timeout also bounds the trigger POST; twice the cap stays below
# v3's 60 s dispatch_slash_command timeout whatever the operator override.
_WORKFLOW_WAIT_CAP_SEC = 20.0


def _workflow_wait_timeout() -> float:
    return min(_interactive_command_timeout(), _WORKFLOW_WAIT_CAP_SEC)


def _render_workflow_trigger_error(app: "UnifiedTUI", exc: Exception, *, waited: bool) -> None:
    """A bounded --wait that runs out is not a failure: the run carries on."""
    match = _WAIT_TIMEOUT_RE.search(str(exc)) if waited else None
    if match is None:
        app.renderer.error(f"could not trigger workflow: {exc}")
        return
    app.renderer.system(f"workflow triggered: {match.group(1)}")
    app.renderer.info(f"{exc}; it keeps running: follow it with /workflow status {match.group(1)}")


def _workflow_trigger_plan(app: "UnifiedTUI", args: List[str]) -> dict | None:
    """Build the invocation plan for `/workflow trigger`; None on bad input."""
    plan: dict = {
        "workflow_name": args[0],
        "kv_parts": [],
        "params_json_parts": [],
        "params_file": None,
        "input_files": [],
        "project_id": None,
        "wait": False,
    }
    invalid = _workflow_fold_flags(app, args, plan)
    if invalid is not None:
        app.renderer.error(f"unknown argument: {invalid}")
        app.renderer.info(_WORKFLOW_TRIGGER_USAGE)
        return None
    parameters = _workflow_merge_parameters(
        app, plan["params_json_parts"], plan["params_file"], plan["kv_parts"]
    )
    if parameters is None:
        return None
    plan["parameters"] = parameters
    plan["project_id"] = plan["project_id"] or _tui_project_id(app)
    return plan


def _workflow_fold_flags(app: "UnifiedTUI", args: List[str], plan: dict) -> str | None:
    """Fold `/workflow trigger` flags into the plan; return the invalid token."""
    i = 1
    while i < len(args):
        token = args[i]
        fold = _WORKFLOW_FLAG_FOLDERS.get(token)
        if fold is None:
            return token
        try:
            i = fold(args, i, plan)
        except IndexError:
            return token
    return None


def _workflow_fold_kv(args: List[str], i: int, plan: dict) -> int:
    plan["kv_parts"].append(args[i + 1])
    return i + 2


def _workflow_fold_params_json(args: List[str], i: int, plan: dict) -> int:
    fragments, next_i = _workflow_json_fragments(args, i + 1)
    plan["params_json_parts"].append(fragments)
    return next_i


def _workflow_fold_params_file(args: List[str], i: int, plan: dict) -> int:
    plan["params_file"] = args[i + 1]
    return i + 2


def _workflow_fold_input_file(args: List[str], i: int, plan: dict) -> int:
    plan["input_files"].append(args[i + 1])
    return i + 2


def _workflow_fold_project(args: List[str], i: int, plan: dict) -> int:
    plan["project_id"] = args[i + 1]
    return i + 2


def _workflow_fold_wait(args: List[str], i: int, plan: dict) -> int:
    plan["wait"] = True
    return i + 1


_WORKFLOW_FLAG_FOLDERS: dict[str, Callable[[List[str], int, dict], int]] = {
    "--param": _workflow_fold_kv,
    "-p": _workflow_fold_kv,
    "--params-json": _workflow_fold_params_json,
    "--params-file": _workflow_fold_params_file,
    "--input-file": _workflow_fold_input_file,
    "--project": _workflow_fold_project,
    "--wait": _workflow_fold_wait,
}


def _workflow_json_fragments(args: List[str], start: int) -> tuple[str, int]:
    """Reassemble JSON fragments that shlex splits from casual typing."""
    fragments = [args[start]]
    i = start + 1
    while i < len(args) and not args[i].startswith("--"):
        fragments.append(args[i])
        i += 1
    return " ".join(fragments), i


def _workflow_merge_parameters(
    app: "UnifiedTUI",
    params_json_parts: List[str],
    params_file,
    kv_parts: List[str],
) -> dict | None:
    """Merge --params-json/--params-file objects with --param k=v overrides.

    The JSON object is the same ``parameters`` payload the Studio view sends;
    --param KEY=VALUE overrides are applied on top. Returns None (after an
    error line is emitted) when any input is invalid.
    """
    merged: dict = {}
    for raw in params_json_parts:
        obj = _workflow_json_object(app, raw, "--params-json")
        if obj is None:
            return None
        merged.update(obj)
    if params_file:
        obj = _workflow_file_json_object(app, params_file)
        if obj is None:
            return None
        merged.update(obj)
    return _workflow_kv_into(app, merged, kv_parts)


def _workflow_json_object(app: "UnifiedTUI", raw: str, source: str) -> dict | None:
    """Parse a JSON object from text, repairing shlex-stripped quotes."""
    import json as _json

    try:
        obj = _json.loads(raw)
    except _json.JSONDecodeError:
        try:
            obj = _json.loads(_workflow_bare_json_repair(raw))
        except _json.JSONDecodeError as exc:
            app.renderer.error(f"invalid JSON from {source}: {exc}")
            return None
    if not isinstance(obj, dict):
        app.renderer.error(f"{source} must contain a JSON object")
        return None
    return obj


def _workflow_file_json_object(app: "UnifiedTUI", params_file) -> dict | None:
    """Parse a JSON object from a --params-file path."""
    from pathlib import Path as _Path

    try:
        text = _Path(params_file).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        app.renderer.error(f"could not read --params-file {params_file}: {exc}")
        return None
    return _workflow_json_object(app, text, "--params-file")


def _workflow_kv_into(app: "UnifiedTUI", merged: dict, kv_parts: List[str]) -> dict | None:
    """Apply --param KEY=VALUE overrides; None on malformed input."""
    for part in kv_parts:
        if "=" not in part:
            app.renderer.error(f"invalid --param value {part!r} (expected KEY=VALUE)")
            return None
        key, value = part.split("=", 1)
        merged[key.strip()] = value
    return merged


def _workflow_bare_json_repair(text: str) -> str:
    """Re-add quotes that shlex strips from casual JSON typing."""
    import re as _re

    reserved = {"true", "false", "null", "nan", "nanf", "inf", "infinity"}

    text = _re.sub(r"([{,]\s*)([A-Za-z_][\w.+-]*)\s*:", r'\1"\2":', text)

    def _quote_value(m: _re.Match) -> str:
        word = m.group(1)
        return m.group(0) if word.lower() in reserved else f': "{word}"'

    return _re.sub(r":\s*([A-Za-z][\w.+-]*)\s*(?=[,}])", _quote_value, text)


def _cmd_workflow_upload(app: "UnifiedTUI", args: List[str]) -> None:
    if not args:
        app.renderer.info("usage: /workflow upload <path>")
        return
    workflow_client = _workflow_client(app)
    method = getattr(workflow_client, "upload", None) if workflow_client is not None else None
    if not callable(method):
        app.renderer.info("workflow upload requires the workflow client")
        return
    try:
        result = method(workflow_path=args[0], workflow_name=None)
        app.renderer.system(f"workflow uploaded: {_record_value(result, 'workflow_id', 'name')}")
        app.renderer.info(_workflow_line(result))
    except Exception as exc:
        app.renderer.error(f"could not upload workflow: {exc}")


_RESEARCH_USAGE = (
    "usage: /research list [project_id] | status <loop_id> [project_id] | "
    "start <goal text> [--project p] [--max-iterations n] | stop <loop_id> [project_id]"
)


def _research_runtime_for(app: "UnifiedTUI"):
    """Resolve the ResearchRuntime for research-loop slash control."""
    from ..runtime import get_runtime

    return get_runtime(project_id=_tui_project_id(app), client=getattr(app, "client", None))


def _cmd_research(app: "UnifiedTUI", args: List[str]) -> None:
    subcommand = args[0].lower() if args else "list"
    rest = args[1:]
    if subcommand in {"list", "ls"}:
        _cmd_research_list(app, rest)
        return
    if subcommand in {"status", "show", "get"}:
        _cmd_research_status(app, rest)
        return
    if subcommand == "start":
        _cmd_research_start(app, rest)
        return
    if subcommand in {"stop", "cancel", "pause"}:
        _cmd_research_stop(app, rest)
        return
    if subcommand in {"session", "view"}:
        _cmd_research_session(app, rest)
        return
    app.renderer.error(f"unknown research sub-command: {subcommand}")
    app.renderer.info(_RESEARCH_USAGE)


def _cmd_research_list(app: "UnifiedTUI", args: List[str]) -> None:
    runtime = _research_runtime_for(app)
    try:
        loops = runtime.list_loops(args[0] if args else None) or []
    except Exception as exc:
        app.renderer.error(f"research loop list failed: {exc}")
        return
    project_id = args[0] if args else _tui_project_id(app)
    if not loops:
        app.renderer.info(f"(no research loops on project {project_id})")
        app.renderer.info("start one with: /research start <research goal>")
        return
    caps = runtime.capabilities()
    app.renderer.info(f"(runtime: {caps.location})")
    for loop in loops[:10]:
        app.renderer.info(_research_loop_line(loop))


def _research_loop_line(loop: object) -> str:
    loop_id = _record_value(loop, "loop_id", "id")
    status = _record_value(loop, "status")
    iteration = _record_value(loop, "current_iteration", "iteration", default="0")
    max_iter = _record_value(loop, "max_iterations", default="?")
    return f"  {loop_id}  {status}  iter {iteration}/{max_iter}".rstrip()


def _cmd_research_status(app: "UnifiedTUI", args: List[str]) -> None:
    if not args:
        app.renderer.info("usage: /research status <loop_id> [project_id]")
        return
    loop_id = args[0]
    runtime = _research_runtime_for(app)
    try:
        detail = runtime.get_loop(loop_id)
        iterations = runtime.list_iterations(loop_id, limit=5)
    except Exception as exc:
        app.renderer.error(f"research loop status failed: {exc}")
        return
    app.renderer.system(_research_loop_line(detail))
    app.renderer.info(f"  phase: {_record_value(detail, 'phase', default='-')}")
    app.renderer.info(f"  goal : {_record_value(detail, 'research_goal', 'goal', default='-')}")
    if iterations:
        app.renderer.info("  recent iterations:")
        for it in iterations:
            it_phase = _record_value(it, "phase", default="-")
            it_status = _record_value(it, "status", default="-")
            app.renderer.info(f"    {it.iteration_number}. {it_phase} ({it_status})")


def _research_flag_tokens(app: "UnifiedTUI") -> dict[str, Any]:
    """Value-taking flag handlers for `/research start` (complexity-safe)."""

    def _int_flag(value: str):
        try:
            return int(value)
        except ValueError:
            app.renderer.error("integer expected")
            raise

    return {
        "--project": lambda v: v,
        "--max-iterations": _int_flag,
        "--session": lambda v: v,
    }


def _research_start_plan(app: "UnifiedTUI", args: List[str]) -> dict | None:
    """Split `/research start` arguments into goal + flags; plan or error."""
    goal_parts: List[str] = []
    value_flags = _research_flag_tokens(app)
    parsed: dict[str, Any] = {
        "project_id": None,
        "max_iterations": None,
        "session": "",
        "agentic": False,
    }
    i = 0
    while i < len(args):
        handler = value_flags.get(args[i])
        if handler is not None and i + 1 < len(args):
            key = args[i].lstrip("-").replace("-", "_")
            field = "project_id" if key == "project" else key
            try:
                parsed[field] = handler(args[i + 1])
            except ValueError:
                return None
            i += 2
        else:
            goal_parts.append(args[i])
            i += 1
    goal = " ".join(goal_parts).strip()
    if not goal:
        app.renderer.error("research goal text is required")
        app.renderer.info('usage: /research start "Optimize MOF-5 for CO2 capture" [--project p]')
        return None
    return {"goal": goal, **parsed}


def _cmd_research_start(app: "UnifiedTUI", args: List[str]) -> None:
    plan = _research_start_plan(app, args)
    if plan is None:
        return
    from ..runtime import LoopSpec

    runtime = _research_runtime_for(app)
    target_project = plan["project_id"] or _tui_project_id(app)
    try:
        state = runtime.start_loop(
            LoopSpec(
                project_id=target_project,
                research_goal=plan["goal"],
                max_iterations=plan["max_iterations"] or 10,
                session_id=plan.get("session") or "",
                agentic=bool(plan.get("agentic")),
            )
        )
    except Exception as exc:
        app.renderer.error(f"research loop start failed: {exc}")
        return
    caps = runtime.capabilities()
    app.renderer.system(
        f"research loop started: {state.loop_id} "
        f"(project {target_project}, runtime: {caps.location})"
    )
    # Which model the iterations use (maintainer 2026-11-03: "are loop also
    # using the service models of r4?"): this start binds no agent, so the
    # server's planned engine or local placeholders run them.
    from ..runtime.loop_model import loop_model_note

    app.renderer.info(f"model: {loop_model_note(caps.location, agentic=bool(plan.get('agentic')))}")
    app.renderer.info(
        f"watch iterations: /research status {state.loop_id} · stop: /research stop {state.loop_id}"
    )
    app.renderer.info("open the Workflows / Knowledge graphs for the live view while it runs.")


def _cmd_research_stop(app: "UnifiedTUI", args: List[str]) -> None:
    if not args:
        app.renderer.info("usage: /research stop <loop_id> [project_id]")
        return
    loop_id = args[0]
    runtime = _research_runtime_for(app)
    try:
        runtime.stop_loop(loop_id)
        app.renderer.system(f"research loop stopped: {loop_id}")
    except Exception as exc:
        app.renderer.error(f"research loop stop failed: {exc}")


def _cmd_research_session(app: "UnifiedTUI", args: List[str]) -> None:
    """Show the aggregated live research session for the project (read-only)."""
    from ..runtime.session_view import research_session_view

    project_id = args[0] if args else _tui_project_id(app)
    try:
        view = research_session_view(app.client, project_id)
    except Exception as exc:
        app.renderer.error(f"research session view failed: {exc}")
        return
    loops = view["loops"]
    app.renderer.system(
        f"research session {project_id} — runtime: {view['runtime']} "
        f"({view['loops']['active_count']}/{view['loops']['count']} running)"
    )
    for item in loops["items"][:6]:
        app.renderer.info(
            f"  {item['loop_id']}  {item['status']}  iter {item['iterations']}  {item['venue']}"
        )
    sessions = view.get("sessions") or {}
    if sessions.get("available") and sessions.get("count"):
        app.renderer.info(f"  sessions ({sessions['count']}):")
        for sess in sessions["items"][:4]:
            app.renderer.info(
                f"    {sess.get('session_id')}  {sess.get('status')}  {sess.get('title')}"
            )
        app.renderer.info("attach a new loop: /research start <goal> --session latest")
    knowledge = view["knowledge"]
    if knowledge.get("available"):
        app.renderer.info(f"  knowledge: {knowledge['nodes']} nodes · {knowledge['edges']} edges")
    else:
        app.renderer.info(f"  knowledge: ({knowledge.get('reason', 'unavailable')})")


def _context_window_lines(app: "UnifiedTUI") -> list[str]:
    """The window the budget is computed from and the budget itself, as the
    companion session last synced them (the served window, not the name
    table, for a model on an Ollama server: 2026-10-30)."""
    refresh = getattr(app, "refresh_context_budget", None)
    if callable(refresh):
        refresh()
    ctx = getattr(app, "context_manager", None)
    budget = getattr(ctx, "max_context_tokens", None)
    if not isinstance(budget, int) or budget <= 0:
        return []
    lines = []
    window = getattr(ctx, "model_context_window", None)
    if isinstance(window, int) and window > 0:
        basis = getattr(ctx, "context_window_basis", None) or "unknown"
        lines.append(f"  context window:   {window:,} tokens ({basis})")
    threshold = getattr(ctx, "compaction_threshold", None)
    at = f", compacts at {threshold:.0%}" if isinstance(threshold, float) else ""
    lines.append(f"  context budget:   {budget:,} tokens{at}")
    return lines


def _cmd_context(app: "UnifiedTUI", args: List[str]) -> None:
    s = app.session
    total = s.total_prompt_tokens + s.total_completion_tokens
    app.renderer.system("Context Usage")
    app.renderer.info(f"  turns:            {len(s.turns)}")
    app.renderer.info(f"  prompt tokens:    {s.total_prompt_tokens}")
    app.renderer.info(f"  completion tokens: {s.total_completion_tokens}")
    app.renderer.info(f"  total tokens:     {total}")
    app.renderer.info(f"  messages:         {len(s.messages)}")
    for line in _context_window_lines(app):
        app.renderer.info(line)


def _usage_duration_label(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _cmd_usage(app: "UnifiedTUI", args: List[str]) -> None:
    usage_report = getattr(app.session, "usage_report", None)
    if callable(usage_report):
        try:
            report = usage_report()
            summary = report.get("summary", {}) if isinstance(report, dict) else {}
            budget = report.get("budget", {}) if isinstance(report, dict) else {}
            app.renderer.system("Usage")
            app.renderer.info(f"LLM calls: {summary.get('llm_calls', 0)}")
            app.renderer.info(f"Prompt tokens: {summary.get('prompt_tokens', 0)}")
            app.renderer.info(f"Completion tokens: {summary.get('completion_tokens', 0)}")
            app.renderer.info(f"Total tokens: {summary.get('total_tokens', 0)}")
            app.renderer.info(f"Node-hours: {budget.get('node_hours_estimated', 0.0):.2f}")
            nearest_expiry = budget.get("nearest_expiry_seconds")
            if isinstance(nearest_expiry, int):
                expiry_label = _usage_duration_label(nearest_expiry)
                app.renderer.info(f"Nearest endpoint expiry: {expiry_label}")
            return
        except Exception as exc:
            app.renderer.error(f"usage report unavailable: {exc}")
    # Fallback for non-companion sessions that only expose token totals.
    _cmd_context(app, args)


def _compact_via_context_manager(app: "UnifiedTUI", msg_count: int) -> bool:
    """Try companion ContextManager first (smarter compaction)."""
    ctx_mgr = getattr(app, "context_manager", None)
    if ctx_mgr is None:
        return False
    try:
        messages_dicts = [_message_dict(m) for m in app.session.messages]
        compacted = ctx_mgr.compact(messages_dicts)
        if compacted is None or len(compacted) >= msg_count:
            return False
        from r4.chat_types import ChatMessage

        app.session.messages = [ChatMessage(**m) if isinstance(m, dict) else m for m in compacted]
        app.renderer.system(f"compacted: {msg_count} → {len(app.session.messages)} messages")
        return True
    except Exception:
        return False  # Fall through to basic compaction


def _message_dict(message: object) -> dict:
    """API dict for a ChatMessage, or a bridge SessionMessage (which has no to_dict)."""
    to_dict = getattr(message, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {"role": message.role, "content": message.content}


def _compact_basic(app: "UnifiedTUI", msg_count: int) -> None:
    """Basic compaction: keep system + last 4, summarize middle."""
    app.renderer.info(f"compacting {msg_count} messages…")
    kept = []
    if app.session.messages and app.session.messages[0].role == "system":
        kept.append(app.session.messages[0])
    middle = app.session.messages[len(kept) : -4] if msg_count > 4 + len(kept) else []
    tail = app.session.messages[-4:] if msg_count > 4 else app.session.messages[len(kept) :]
    if middle:
        summary_parts = []
        for m in middle:
            summary_parts.append(f"[{m.role}] {str(m.content)[:100]}")
        from r4.chat_types import ChatMessage

        summary = ChatMessage.system(
            "[context compacted] Previous conversation summary:\n" + "\n".join(summary_parts[-10:])
        )
        kept.append(summary)
    kept.extend(tail)
    app.session.messages = kept
    app.renderer.system(f"compacted: {msg_count} → {len(kept)} messages")


def _cmd_compact(app: "UnifiedTUI", args: List[str]) -> None:
    msg_count = len(app.session.messages)
    if msg_count <= 3:
        app.renderer.info("(conversation too short to compact)")
        return

    if _compact_via_context_manager(app, msg_count):
        return
    _compact_basic(app, msg_count)


def _cmd_history(app: "UnifiedTUI", args: List[str]) -> None:
    if _render_turn_history(app):
        return
    if _render_raw_message_history(app, resumed=False):
        return
    app.renderer.info("(no turns yet)")


def _render_turn_history(app: "UnifiedTUI") -> bool:
    turns = list(getattr(app.session, "turns", []))
    if not turns:
        return False
    for i, turn in enumerate(turns, start=1):
        app.renderer.info(f"--- turn {i} ---")
        app.renderer.info(f"you: {turn.user}")
        app.renderer.info(f"asst: {turn.assistant[:200]}{'…' if len(turn.assistant) > 200 else ''}")
    return True


def _render_raw_message_history(app: "UnifiedTUI", *, resumed: bool) -> bool:
    messages = [
        message
        for message in list(getattr(app.session, "messages", []))
        if getattr(message, "content", "") not in (None, "")
    ]
    if not messages:
        return False
    if resumed:
        app.renderer.info("(resumed session has raw messages without completed turns)")
    else:
        app.renderer.info("(no completed turns yet; showing raw message log)")
    for index, message in enumerate(messages, start=1):
        role = str(getattr(message, "role", "message"))
        label = {"user": "you", "assistant": "asst", "system": "sys"}.get(role, role)
        content = str(getattr(message, "content", ""))
        preview = content[:200]
        suffix = "…" if len(content) > 200 else ""
        app.renderer.info(f"--- message {index} ---")
        app.renderer.info(f"{label}: {preview}{suffix}")
    return True


def _cmd_cost(app: "UnifiedTUI", args: List[str]) -> None:
    s = app.session
    app.renderer.info(
        f"turns={len(s.turns)}  "
        f"prompt={s.total_prompt_tokens}  "
        f"completion={s.total_completion_tokens}"
    )


def _cmd_export(app: "UnifiedTUI", args: List[str]) -> None:
    if args:
        _cmd_save(app, [" ".join(args)])
        return
    markdown = app.session.to_markdown()
    lines = markdown.splitlines()
    preview_lines = lines[:12]
    app.renderer.system(f"session export ({len(lines)} lines)")
    if preview_lines:
        app.renderer.info("\n".join(preview_lines))
    else:
        app.renderer.info("(empty transcript)")
    if len(lines) > len(preview_lines):
        app.renderer.info("…")
        app.renderer.info("use /export <path.md> (or /save <path.md>) to write the full file")


def _cmd_save(app: "UnifiedTUI", args: List[str]) -> None:
    """``/save [n] [mine] [--vouch] …`` keeps a turn in project knowledge
    (K1.6, ``r4.companion.turn_save``); ``/save <path.md>`` writes the
    transcript."""
    if app.session.secure:
        app.renderer.error("save is disabled in secure mode")
        return
    from r4.companion.turn_save import parse_save_args

    try:
        request = parse_save_args(args)
    except ValueError as exc:
        app.renderer.error(str(exc))
        return
    if request is not None:
        _save_turn_to_knowledge(app, request)
        return
    from pathlib import Path

    path = Path(args[0]).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(app.session.to_markdown(), encoding="utf-8")
    app.renderer.system(f"saved to {path}")


def _save_turn_to_knowledge(app: "UnifiedTUI", request: Any) -> None:
    """One turn, verbatim, into project knowledge (K1.6)."""
    from r4.companion.turn_save import receipt_line, save_turn

    session = getattr(app, "_companion_session", None)
    if session is None:
        app.renderer.error("/save <turn> needs a companion chat session")
        return
    try:
        result = save_turn(session, app.client, request)
    except Exception as exc:
        app.renderer.error(f"save failed: {exc}")
        return
    app.renderer.system(receipt_line(result))


def _resolve_harness(app: "UnifiedTUI"):
    companion = getattr(app, "_companion_session", None)
    if companion is None:
        return None
    try:
        return companion.harness
    except Exception:
        return None


def _cmd_progress(app: "UnifiedTUI", args: List[str]) -> None:
    harness = _resolve_harness(app)
    if harness is None:
        app.renderer.info("progress tracking is unavailable in this session surface")
        return
    orientation = harness.progress.orientation()
    app.renderer.system("Progress")
    if orientation:
        app.renderer.info(orientation)
    else:
        app.renderer.info("(no persisted progress yet)")


def _cmd_probe(app: "UnifiedTUI", args: List[str]) -> None:
    harness = _resolve_harness(app)
    if harness is None:
        app.renderer.info("probe refresh is unavailable in this session surface")
        return
    harness.refresh_probe()
    app.renderer.system("environment probe refreshed")


def _render_template_list(app: "UnifiedTUI", harness: Any) -> None:
    templates = harness.list_templates()
    active = harness.active_template()
    app.renderer.system("Harness templates")
    if active is not None:
        app.renderer.info(f"Active: {active.name} — {active.description}")
    for index, template in enumerate(templates, 1):
        marker = " *" if active is not None and active.name == template.name else ""
        app.renderer.info(f"  {index}. {template.name:<22} {template.description}{marker}")
    app.renderer.info("usage: /template <name>  |  /template clear")


def _render_template_apply_result(app: "UnifiedTUI", result: dict[str, Any]) -> None:
    app.renderer.system(f"template → {result['template']}")
    app.renderer.info(str(result["description"]))
    activated = result.get("activated_skills") or []
    if activated:
        app.renderer.info("skills activated: " + ", ".join(activated))
    missing = result.get("missing_skills") or []
    if missing:
        app.renderer.info("skills not found: " + ", ".join(missing))
    milestones_added = result.get("milestones_added") or []
    if milestones_added:
        app.renderer.info(f"milestones added: {len(milestones_added)}")


def _cmd_template(app: "UnifiedTUI", args: List[str]) -> None:
    harness = _resolve_harness(app)
    if harness is None:
        app.renderer.info("templates are unavailable in this session surface")
        return

    if not args or args[0].lower() in ("list", "ls"):
        _render_template_list(app, harness)
        return

    if args[0].lower() == "clear":
        harness.clear_active_template()
        app.renderer.system("template selection cleared (auto-detect restored)")
        return

    try:
        result = harness.apply_template(args[0])
    except ValueError:
        app.renderer.error(f"unknown template: {args[0]} (try /template list)")
        return

    _render_template_apply_result(app, result)


def _cmd_lessons(app: "UnifiedTUI", args: List[str]) -> None:
    harness = _resolve_harness(app)
    if harness is None:
        app.renderer.info("lessons are unavailable in this session surface")
        return
    steering = getattr(harness, "steering", None)
    if steering is None:
        app.renderer.info("steering lessons are unavailable in this session surface")
        return
    if args and args[0].lower() == "clear":
        steering.clear()
        app.renderer.system("steering lessons cleared")
        return
    lessons = steering.list_lessons()
    app.renderer.system("Steering lessons")
    if not lessons:
        app.renderer.info("(no lessons recorded yet)")
        return
    for index, lesson in enumerate(lessons, 1):
        rule = getattr(lesson, "rule", str(lesson))
        created = getattr(lesson, "created_at", "")
        suffix = f"  ({created[:10]})" if created else ""
        app.renderer.info(f"  {index}. {rule}{suffix}")


def _cmd_share(app: "UnifiedTUI", args: List[str]) -> None:
    app.renderer.info("share options:")
    app.renderer.info("  /save path.md    — save as markdown")
    app.renderer.info("  (gist export planned for future release)")


def _print_recent_sessions(app: "UnifiedTUI", sessions: list[object]) -> None:
    app.renderer.info("recent sessions:")
    for session in sessions:
        sid = _session_field(session, "id", "session_id")
        title = _session_field(session, "name", "title")
        surface = _session_field(session, "surface")
        model = _session_field(session, "model")
        app.renderer.info(f"  {sid}  {title}  [{surface or 'session'}]  model={model or '?'}")
    app.renderer.info("\nusage: /sessions <session_id>  (/resume is an alias)")


def _resume_session(app: "UnifiedTUI", store: object, session_id: str) -> None:
    try:
        loaded = _load_session_from_store(store, session_id)
    except Exception as exc:
        app.renderer.error(f"could not resume: {exc}")
        return
    if loaded is None:
        app.renderer.error(f"session not found: {session_id}")
        return
    app.session = loaded
    app.renderer.system(f"resumed session {session_id}: {len(app.session.turns)} turn(s) loaded")
    _render_resumed_history(app)


def _cmd_sessions(app: "UnifiedTUI", args: List[str]) -> None:
    store = getattr(app, "session_store", None)
    if store is None:
        app.renderer.info("(session persistence not available)")
        return
    try:
        sessions = store.list_sessions(limit=10)
    except Exception:
        sessions = []
    if not sessions:
        app.renderer.info("(no saved sessions found)")
        return
    if not args:
        _print_recent_sessions(app, sessions)
        return
    _resume_session(app, store, args[0])


def _session_field(session_record: object, *names: str) -> str:
    for name in names:
        if isinstance(session_record, dict):
            value = session_record.get(name)
        else:
            value = getattr(session_record, name, None)
        if value not in (None, ""):
            return str(value)
    return ""


def _load_session_from_store(store: object, session_id: str):
    load = getattr(store, "load", None)
    if callable(load):
        return load(session_id)
    load_session = getattr(store, "load_session", None)
    if callable(load_session):
        return load_session(session_id)
    raise AttributeError("session store has no load method")


def _render_resumed_history(app: "UnifiedTUI") -> None:
    if _render_turn_history(app):
        return
    if _render_raw_message_history(app, resumed=True):
        return
    app.renderer.info("(resumed session has no turns yet)")


def _cmd_system(app: "UnifiedTUI", args: List[str]) -> None:
    if not args:
        current = app.session.system_prompt or "(none)"
        app.renderer.info(f"system prompt: {current}")
        return
    prompt = " ".join(args)
    app.session.set_system(prompt)
    app.renderer.system("system prompt updated")


def _cmd_config(app: "UnifiedTUI", args: List[str]) -> None:
    from r4.config import get_config

    config = get_config()
    app.renderer.info(f"gateway:  {config.gateway_url}")
    app.renderer.info(f"project:  {config.project_id or '(none)'}")
    app.renderer.info(f"model:    {app.session.model}")
    app.renderer.info(f"mode:     {app.mode.label} — {app.mode.description}")


def _cmd_retry(app: "UnifiedTUI", args: List[str]) -> None:
    turn = app.session.drop_last_turn()
    if turn is None:
        app.renderer.error("nothing to retry")
        return
    app.renderer.system(f"retrying: {turn.user[:80]}{'…' if len(turn.user) > 80 else ''}")
    app.send_user_message(turn.user)


def _code_run_lsp(
    app: "UnifiedTUI", lsp_action: str, path: str, options: dict, impls: dict
) -> None:
    language = _string_option(options, "language")
    if lsp_action == "start":
        app.renderer.info(impls["start_language_servers"](path, language=language))
        return
    app.renderer.info(impls["detect_language_servers_text"](path, language=language))


def _code_run_compile(app: "UnifiedTUI", path: str, options: dict, impls: dict) -> None:
    app.renderer.info(
        impls["compile_code"](
            path,
            language=_string_option(options, "language"),
            source_path=_string_option(options, "source_path"),
            target=_string_option(options, "target"),
            output_path=_string_option(options, "output_path"),
            timeout=_int_option(options, "timeout", 120),
        )
    )


def _code_run_run(app: "UnifiedTUI", path: str, options: dict, impls: dict) -> None:
    app.renderer.info(
        impls["run_code"](
            path,
            language=_string_option(options, "language"),
            source_path=_string_option(options, "source_path"),
            executable=_string_option(options, "executable"),
            args=_string_option(options, "args"),
            timeout=_int_option(options, "timeout", 60),
        )
    )


def _code_run_test(app: "UnifiedTUI", path: str, options: dict, impls: dict) -> None:
    app.renderer.info(
        impls["test_code"](
            path,
            language=_string_option(options, "language"),
            target=_string_option(options, "target"),
            timeout=_int_option(options, "timeout", 180),
        )
    )


def _code_head_lines(path: str, *, limit: int = 60) -> list[str]:
    from pathlib import Path

    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"not a file: {path}")
    with p.open(encoding="utf-8", errors="replace") as fh:
        return fh.readlines()[:limit]


def _git_diff_args(path: str, staged: bool, extra: list[str]) -> list[str]:
    """Build: git diff [--staged] <extra> [-- <path>], hardened (ADR-101 ESC-1):
    no fsmonitor, hook, external diff or textconv of the repository runs."""
    from r4.safe_git import diff_flags, git_argv

    args = git_argv("diff", *diff_flags())
    if staged:
        args.append("--staged")
    args += extra
    if path != ".":
        args += ["--", path]
    return args


def _code_run_diff(app: "UnifiedTUI", path: str, options: dict[str, object]) -> None:
    """Copilot-grade diff view: --stat summary then a trimmed patch."""
    import subprocess

    staged = "--staged" in options or "--cached" in options

    def _run_git(extra: list[str]) -> str:
        from r4.safe_git import git_env

        return subprocess.run(
            _git_diff_args(path, staged, extra),
            capture_output=True,
            text=True,
            timeout=20,
            env=git_env(),
        ).stdout

    try:
        stat_output = _run_git(["--stat"])
        if not stat_output.strip():
            app.renderer.info("(no changes — working tree clean)")
            return
        for line in stat_output.splitlines()[:24]:
            app.renderer.info(line)
        for line in _run_git(["--unified=3"]).splitlines()[:80]:
            app.renderer.info(line)
    except FileNotFoundError as exc:
        raise ValueError("git unavailable on PATH") from exc


def _code_run_explain(app: "UnifiedTUI", path: str) -> None:
    app.renderer.info(f"--- {path} (first 60 lines) ---")
    lines = _code_head_lines(path)
    for idx, line in enumerate(lines, 1):
        app.renderer.info(f"{idx:>4} │ {line.rstrip()}")


def _code_run_edit(app: "UnifiedTUI", path: str, options: dict[str, object]) -> None:
    details = _string_option(options, "details") or "apply the intended change carefully"
    prompt = (
        "Apply a focused edit agentically. "
        f"Path: {path}. Request: {details}. "
        "Read the surrounding code first, prefer minimal diffs, then compile or test "
        "the change you made and show the resulting diff in your reply."
    )
    app.renderer.system(f"starting agentic edit on {path}")
    app.send_user_message(prompt)


def _code_run_review(app: "UnifiedTUI", path: str, options: dict[str, object]) -> None:
    details = _string_option(options, "details")
    focus = details or (
        "correctness, regressions, and security issues; cite file:line"
        if path != "."
        else "review the working diff: regressions, security, and types; cite file:line"
    )
    prompt = (
        f"Perform a rigorous code review. Target: {path}. Focus: {focus}. "
        "Return findings ordered by severity, one line each, then a short verdict."
    )
    app.renderer.system(f"starting agentic review on {path}")
    app.send_user_message(prompt)


def _run_code_subcommand(
    app: "UnifiedTUI",
    subcommand: str,
    lsp_action: str,
    path: str,
    options: dict[str, object],
    impls: dict,
) -> None:
    if subcommand in {"status", "context", "detect"}:
        app.renderer.info(impls["detect_code_context_text"](path))
        return
    handlers = _code_handlers(app, path, options, impls)
    handler = handlers.get(subcommand)
    if subcommand == "lsp":
        _code_run_lsp(app, lsp_action, path, options, impls)
    elif handler is not None:
        handler()
    else:
        app.renderer.error(f"unknown code sub-command: {subcommand}")
        app.renderer.info(
            "usage: /code status|lsp|plan|diff|explain|edit|review|compile|run|test|fix [path]"
        )


def _code_handlers(app, path, options, impls):
    """Dispatch table for the /code verbs (single lookup, no elif-chain)."""
    return {
        "plan": lambda: app.renderer.info(impls["code_plan"](path)),
        "compile": lambda: _code_run_compile(app, path, options, impls),
        "build": lambda: _code_run_compile(app, path, options, impls),
        "check": lambda: _code_run_compile(app, path, options, impls),
        "run": lambda: _code_run_run(app, path, options, impls),
        "test": lambda: _code_run_test(app, path, options, impls),
        "tests": lambda: _code_run_test(app, path, options, impls),
        "fix": lambda: _cmd_code_fix(app, path, options),
        "diff": lambda: _code_run_diff(app, path, options),
        "explain": lambda: _code_run_explain(app, path),
        "open": lambda: _code_run_explain(app, path),
        "view": lambda: _code_run_explain(app, path),
        "edit": lambda: _code_run_edit(app, path, options),
        "review": lambda: _code_run_review(app, path, options),
    }


def _cmd_code(app: "UnifiedTUI", args: List[str]) -> None:
    from r4.companion.coding import (
        code_plan,
        compile_code,
        detect_code_context_text,
        detect_language_servers_text,
        run_code,
        start_language_servers,
        test_code,
    )

    impls = {
        "code_plan": code_plan,
        "compile_code": compile_code,
        "detect_code_context_text": detect_code_context_text,
        "detect_language_servers_text": detect_language_servers_text,
        "run_code": run_code,
        "start_language_servers": start_language_servers,
        "test_code": test_code,
    }

    subcommand = args[0].lower() if args else "status"
    rest = args[1:]
    lsp_action = "status"
    if subcommand == "lsp" and rest and rest[0].lower() in {"start", "on"}:
        lsp_action = "start"
        rest = rest[1:]
    options = _parse_code_options(rest)
    path = str(options.get("path") or ".")

    try:
        _run_code_subcommand(app, subcommand, lsp_action, path, options, impls)
    except Exception as exc:
        app.renderer.error(f"code {subcommand} failed: {exc}")


def _cmd_code_fix(app: "UnifiedTUI", path: str, options: dict[str, object]) -> None:
    details = _string_option(options, "details") or "the current coding issue"
    prompt = (
        "Diagnose and fix the local code issue agentically. "
        f"Path: {path}. Details: {details}. "
        "Use detect_code_context first, then plan, edit narrowly, compile or run tests, "
        "and iterate until the failure is explained or fixed."
    )
    app.renderer.system("starting agentic code fix loop")
    app.send_user_message(prompt)


def _parse_code_options(args: List[str]) -> dict[str, object]:
    options: dict[str, object] = {"path": "."}
    positionals: list[str] = []
    passthrough: list[str] = []
    index = 0
    while index < len(args):
        value = args[index]
        if value == "--":
            passthrough = args[index + 1 :]
            break
        option_key = {
            "--language": "language",
            "--lang": "language",
            "--source": "source_path",
            "--source-path": "source_path",
            "--target": "target",
            "--output": "output_path",
            "--output-path": "output_path",
            "--exec": "executable",
            "--executable": "executable",
            "--timeout": "timeout",
            "--details": "details",
        }.get(value)
        if option_key:
            if index + 1 >= len(args):
                raise ValueError(f"missing value for {value}")
            options[option_key] = args[index + 1]
            index += 2
            continue
        positionals.append(value)
        index += 1

    if positionals:
        options["path"] = positionals[0]
    if passthrough:
        options["args"] = shlex.join(passthrough)
    elif len(positionals) > 1:
        options["details"] = " ".join(positionals[1:])
    return options


def _string_option(options: dict[str, object], name: str) -> str | None:
    value = options.get(name)
    if value in (None, ""):
        return None
    return str(value)


def _int_option(options: dict[str, object], name: str, default: int) -> int:
    value = options.get(name)
    if value in (None, ""):
        return default
    return int(value)


def _cmd_project(app: "UnifiedTUI", args: List[str]) -> None:
    """/project — current project context (read-only) + lifecycle pointers.

    Deliberately NOT a verb surface: the project lifecycle (import/init/
    plan/readiness/verify/switch/...) is owned by the Bun/Console /project
    commands via project_bridge.py, and research runs are UMRS loops
    (research_loop_* tools, `r4 project loop start`; the RC1
    project_research_* family retired 2026-09-07). SUBCOMMANDS stays
    subcommand-free here so the shared bridge catalog remains disjoint
    (test_shared_slash_catalog_parity).
    """
    from r4.project import ProjectManager

    pm = ProjectManager()
    current = pm.get_current()
    cfg = pm.get_current_config()
    if current:
        name = (getattr(cfg, "name", "") or "").strip() if cfg else ""
        suffix = f" — {name}" if name else ""
        app.renderer.info(f"project: {current}{suffix}")
    else:
        app.renderer.info("project: (none active)")
    count = len(list(pm.list()))
    if count:
        app.renderer.info(f"registered: {count} project(s)")
    app.renderer.info("lifecycle: `r4 console` /project verbs, `r4 project …` CLI, or ask in chat")


def _skill_names(reg) -> list[str]:
    return [skill.name for skill in reg.list_all()]


def _resolve_skill_name(reg, selector: str) -> str | None:
    skills = reg.list_all()
    if selector.isdigit():
        idx = int(selector) - 1
        if 0 <= idx < len(skills):
            return skills[idx].name
        return None
    selector_lower = selector.lower()
    for skill in skills:
        if skill.name == selector or skill.name.lower() == selector_lower:
            return skill.name
    return None


def _render_skill_list(app: "UnifiedTUI", reg) -> None:
    all_skills = reg.list_all()
    active_names = {skill.name for skill in reg.list_active()}
    for idx, skill in enumerate(all_skills, start=1):
        marker = "●" if skill.name in active_names else "○"
        app.renderer.info(f"  [{idx}] {marker} {skill.name} — {skill.description} [{skill.source}]")
    app.renderer.info(f"\n  {len(active_names)} active / {len(all_skills)} total")
    app.renderer.info("  use /skills <number|name> to toggle, or /skills on|off <number|name>")


def _set_skill_active(app: "UnifiedTUI", reg, selector: str, active: bool) -> None:
    name = _resolve_skill_name(reg, selector)
    if name is None:
        app.renderer.error(f"unknown skill: {selector}")
        return
    if active:
        if reg.activate(name):
            app.renderer.system(f"skill activated: {name}")
        else:
            app.renderer.error(f"unknown skill: {selector}")
        return
    reg.deactivate(name)
    app.renderer.system(f"skill deactivated: {name}")


def _toggle_skill(app: "UnifiedTUI", reg, selector: str) -> None:
    name = _resolve_skill_name(reg, selector)
    if name is None:
        valid = ", ".join(_skill_names(reg))
        app.renderer.error(f"unknown skill: {selector} ({valid})")
        return
    active_names = {skill.name for skill in reg.list_active()}
    _set_skill_active(app, reg, name, active=name not in active_names)


def _handle_skill_activation_command(app: "UnifiedTUI", reg, args: List[str]) -> bool:
    desired = {
        "on": True,
        "activate": True,
        "off": False,
        "deactivate": False,
    }.get(args[0].lower())
    if desired is None:
        return False
    selector = args[1] if len(args) > 1 else ""
    if not selector:
        verb = "on" if desired else "off"
        app.renderer.info(f"usage: /skills {verb} <number|name>")
        return True
    _set_skill_active(app, reg, selector, active=desired)
    return True


def _cmd_skills(app: "UnifiedTUI", args: List[str]) -> None:
    reg = getattr(app, "skill_registry", None)
    if reg is None:
        app.renderer.info("(companion skills not available)")
        return
    if not args or args[0].lower() in ("list", "ls"):
        _render_skill_list(app, reg)
        return
    if _handle_skill_activation_command(app, reg, args):
        return
    _toggle_skill(app, reg, args[0])


_DECISION_MODEL_HELP = (
    "usage: /decision-model [status] | on [MODEL] | off\n"
    "  on      give the agent the decision_evaluate tool (default model: {default})\n"
    "  off     remove it\n"
    "  status  show the current setting\n"
    "The choice is saved in your profile and applies from the next message."
)


def _decision_model_status_text(state, client) -> str:
    from r4.decisions.settings import gateway_serves

    if not state.enabled:
        text = "decision model: off"
    else:
        served = gateway_serves(client, state.model)
        where = {True: "served by the platform", False: "NOT listed by the platform right now"}
        text = f"decision model: on — {state.model} ({where.get(served, 'availability unknown')})"
    if state.override:
        text += f"\n  note: {state.override} takes precedence over the profile setting"
    return text


def _decision_model_target(verb: str, args: List[str], client) -> str:
    """The model `on`/`off` asks for ("" = off)."""
    from r4.decisions.settings import DEFAULT_DECISION_MODEL, current_state

    if verb == "off":
        return ""
    if len(args) > 1 and args[1].strip():
        return args[1].strip()
    return current_state(client).model or DEFAULT_DECISION_MODEL


def _cmd_decision_model(app: "UnifiedTUI", args: List[str]) -> None:
    """``/decision-model on [MODEL] | off | status`` (ADR-076)."""
    from r4.decisions.settings import (
        DEFAULT_DECISION_MODEL,
        current_state,
        refresh_session_tools,
        set_decision_model,
    )

    client = getattr(app, "client", None)
    if getattr(client, "config", None) is None:
        app.renderer.info("(decision model needs a platform connection)")
        return
    verb = args[0].lower() if args else "status"
    if verb in ("status", "show"):
        app.renderer.info(_decision_model_status_text(current_state(client), client))
        return
    if verb not in ("on", "off"):
        app.renderer.info(_DECISION_MODEL_HELP.format(default=DEFAULT_DECISION_MODEL))
        return
    state = set_decision_model(client, _decision_model_target(verb, args, client))
    session = getattr(app, "_companion_session", None)
    if session is not None:
        refresh_session_tools(session, client)
    app.renderer.system(_decision_model_status_text(state, client))


def _cmd_companion(app: "UnifiedTUI", args: List[str]) -> None:
    app.renderer.info(
        "Companion capabilities are integrated into this TUI.\n"
        "  /skills — manage skill bundles\n"
        "  Skills auto-activate based on keywords in your input."
    )


# ---------------------------------------------------------------------------
# A2L — artifact and agent-exec slash commands
# ---------------------------------------------------------------------------


_ARTIFACT_HELP = (
    "usage: /artifact list|show|derive|status|lineage\n"
    "  list   [--type TYPE] [--status STATUS] [--project ID]\n"
    "  show   <artifact-id>\n"
    "  lineage <artifact-id> [--direction up|down|both] [--depth N]\n"
    "  derive --type TYPE --uri URI --parent ID [--parent ID ...]\n"
    "  status <artifact-id> completed|failed [--error MSG]"
)


def _artifact_ns_list(app: "UnifiedTUI", ns: "argparse.Namespace", rest: List[str]) -> bool:
    ns.type = None
    ns.status = None
    ns.project = None
    ns.user = None
    ns.limit = 20
    ns.offset = 0
    ns.as_json = False
    _parse_flags(
        rest,
        ns,
        {"--type": "type", "--status": "status", "--project": "project", "--user": "user"},
    )
    return True


def _artifact_ns_show(app: "UnifiedTUI", ns: "argparse.Namespace", rest: List[str]) -> bool:
    if not rest:
        app.renderer.info("usage: /artifact show <artifact-id>")
        return False
    ns.artifact_id = rest[0]
    ns.as_json = False
    return True


def _artifact_ns_lineage(app: "UnifiedTUI", ns: "argparse.Namespace", rest: List[str]) -> bool:
    if not rest:
        app.renderer.info("usage: /artifact lineage <artifact-id>")
        return False
    ns.artifact_id = rest[0]
    ns.direction = "both"
    ns.depth = 10
    ns.as_json = False
    _parse_flags(rest[1:], ns, {"--direction": "direction", "--depth": "depth"})
    return True


def _artifact_ns_status(app: "UnifiedTUI", ns: "argparse.Namespace", rest: List[str]) -> bool:
    if len(rest) < 2:
        app.renderer.info("usage: /artifact status <id> completed|failed [--error MSG]")
        return False
    ns.artifact_id = rest[0]
    ns.new_status = rest[1]
    ns.error = None
    _parse_flags(rest[2:], ns, {"--error": "error"})
    return True


def _artifact_ns_derive(app: "UnifiedTUI", ns: "argparse.Namespace", rest: List[str]) -> bool:
    ns.type = None
    ns.uri = None
    ns.parents = []
    ns.metadata = "{}"
    ns.project = None
    _parse_flags(
        rest,
        ns,
        {
            "--type": "type",
            "--uri": "uri",
            "--parent": "parents",
            "--metadata": "metadata",
            "--project": "project",
        },
        multi={"--parent": True},
    )
    if not ns.type or not ns.uri or not ns.parents:
        app.renderer.info("usage: /artifact derive --type TYPE --uri URI --parent ID")
        return False
    return True


_ARTIFACT_NS_BUILDERS = {
    "list": _artifact_ns_list,
    "show": _artifact_ns_show,
    "lineage": _artifact_ns_lineage,
    "status": _artifact_ns_status,
    "derive": _artifact_ns_derive,
}


def _build_artifact_namespace(
    app: "UnifiedTUI", subcommand: str, rest: List[str]
) -> "argparse.Namespace | None":
    ns = argparse.Namespace()
    ns.artifact_command = subcommand

    # Map common args for each subcommand
    builder = _ARTIFACT_NS_BUILDERS.get(subcommand)
    if builder is None:
        app.renderer.error(f"unknown artifact subcommand: {subcommand}")
        app.renderer.info("usage: /artifact list|show|lineage|derive|status|help")
        return None
    if not builder(app, ns, rest):
        return None
    return ns


def _cmd_artifact(app: "UnifiedTUI", args: List[str]) -> None:
    """Delegate /artifact subcommands to the CLI handler."""
    subcommand = args[0].lower() if args else "list"

    if subcommand == "help":
        app.renderer.info(_ARTIFACT_HELP)
        return

    try:
        from r4._cli.artifact import cmd_artifact
    except ImportError:
        app.renderer.error("A2L artifact module not available")
        return

    ns = _build_artifact_namespace(app, subcommand, args[1:])
    if ns is None:
        return

    _run_cli_into_renderer(app, cmd_artifact, ns, f"artifact {subcommand}")


class _ThreadStream:
    """Capture one thread's writes; every other thread keeps the real stream.

    redirect_stdout swaps sys.stdout for the whole process, and bridge worker
    threads write their JSON-RPC frames through print() while a command runs.
    """

    def __init__(self, real: Any, buffer: io.StringIO, owner: int) -> None:
        self._real = real
        self._buffer = buffer
        self._owner = owner

    def write(self, text: str) -> int:
        target = self._buffer if threading.get_ident() == self._owner else self._real
        return target.write(text)

    def flush(self) -> None:
        if threading.get_ident() != self._owner:
            self._real.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _run_cli_into_renderer(app: "UnifiedTUI", handler, ns, label: str) -> None:
    """Run a print()-based CLI handler and replay its output through the renderer.

    Under the companion bridge stdout is the JSON-RPC channel: a bare print()
    there is a protocol error and the TUI never shows the line.
    """
    out, err = io.StringIO(), io.StringIO()
    owner = threading.get_ident()
    try:
        with (
            contextlib.redirect_stdout(_ThreadStream(sys.stdout, out, owner)),
            contextlib.redirect_stderr(_ThreadStream(sys.stderr, err, owner)),
        ):
            handler(ns)
    except SystemExit:
        pass
    except Exception as exc:
        err.write(f"{label} failed: {exc}\n")
    for line in out.getvalue().splitlines():
        app.renderer.info(line)
    for line in err.getvalue().splitlines():
        app.renderer.error(line)


def _agent_exec_namespace(
    subcommand: str, rest: list[str], renderer
) -> "argparse.Namespace | None":
    import argparse

    ns = argparse.Namespace(exec_command=subcommand)
    if subcommand == "list":
        ns.agent = None
        ns.status = None
        ns.project = None
        ns.user = None
        ns.limit = 20
        ns.offset = 0
        ns.as_json = False
        _parse_flags(
            rest,
            ns,
            {"--agent": "agent", "--status": "status", "--project": "project", "--user": "user"},
        )
        return ns
    if subcommand == "show":
        if not rest:
            renderer.info("usage: /run show <exec-id>")
            return None
        ns.exec_id = rest[0]
        ns.as_json = False
        return ns
    if subcommand == "create":
        ns.agent = None
        ns.version = ""
        ns.model = ""
        ns.prompt_hash = ""
        ns.params = "{}"
        ns.inputs = []
        ns.project = None
        _parse_flags(
            rest,
            ns,
            {
                "--agent": "agent",
                "--version": "version",
                "--model": "model",
                "--params": "params",
                "--input": "inputs",
                "--project": "project",
            },
            multi={"--input": True},
        )
        if not ns.agent:
            renderer.info("usage: /run create --agent NAME")
            return None
        return ns
    if subcommand == "update":
        if not rest:
            renderer.info("usage: /run update <exec-id> [--status STATUS]")
            return None
        ns.exec_id = rest[0]
        ns.status = None
        ns.outputs = []
        ns.error = None
        ns.trace = None
        _parse_flags(
            rest[1:],
            ns,
            {"--status": "status", "--output": "outputs", "--error": "error", "--trace": "trace"},
            multi={"--output": True},
        )
        return ns
    renderer.error(f"unknown run subcommand: {subcommand}")
    renderer.info("usage: /run list|show|create|update|help")
    return None


def _cmd_agent_exec(app: "UnifiedTUI", args: List[str]) -> None:
    """Delegate /run (A2L agent executions) to the ``r4 agent-exec`` handler."""
    subcommand = args[0].lower() if args else "list"
    if subcommand == "help":
        app.renderer.info(
            "usage: /run list|show|create|update\n"
            "  list   [--agent NAME] [--status STATUS] [--project ID]\n"
            "  show   <exec-id>\n"
            "  create --agent NAME [--version VER]\n"
            "  update <exec-id> --status STATUS"
        )
        return
    try:
        from r4._cli.agent_exec import cmd_agent_exec
    except ImportError:
        app.renderer.error("A2L agent-exec module not available")
        return
    ns = _agent_exec_namespace(subcommand, args[1:], app.renderer)
    if ns is None:
        return
    _run_cli_into_renderer(app, cmd_agent_exec, ns, f"run {subcommand}")


def _parse_flags(
    args: List[str],
    ns: "argparse.Namespace",
    mapping: dict,
    multi: dict | None = None,
) -> None:
    """Minimal positional/flag parser for TUI slash command args."""
    multi = multi or {}
    i = 0
    while i < len(args):
        token = args[i]
        if token in mapping:
            attr = mapping[token]
            if i + 1 < len(args):
                val = args[i + 1]
                if multi.get(token):
                    current = getattr(ns, attr, [])
                    setattr(ns, attr, current + [val])
                else:
                    try:
                        current = getattr(ns, attr)
                        if isinstance(current, int):
                            val = int(val)
                    except (AttributeError, ValueError):
                        pass
                    setattr(ns, attr, val)
                i += 2
                continue
        i += 1


def get_slash_registry() -> SlashRegistry:
    """Factory — each TUI instance gets its own registry."""
    return SlashRegistry()


# ---------------------------------------------------------------------------
# UX redesign: surface aliases (/agent, /search, /team)
# ---------------------------------------------------------------------------


def _cmd_agent_surface(app: "UnifiedTUI", args: List[str]) -> None:
    """Delegate /agent to the agent CLI surface."""
    if not args:
        app.renderer.info(
            "usage: /agent chat|run|create|sessions|tools|scan|audit\n"
            "  or just type your question — the companion is already an agent."
        )
        return
    subcommand = args[0].lower()
    if subcommand in ("chat", "run"):
        message = " ".join(args[1:]) if len(args) > 1 else None
        if message:
            app.renderer.info(f"[agent {subcommand}] {message}")
            app.send_user_message(message)
        else:
            app.renderer.info(f"agent {subcommand} mode — type your message below")
    else:
        app.renderer.info(f"use the CLI for agent {subcommand}: r4 agent {subcommand}")


def _cmd_search_surface(app: "UnifiedTUI", args: List[str]) -> None:
    """Delegate /search to an inline search."""
    query = " ".join(args) if args else None
    if not query:
        app.renderer.info("usage: /search <query>")
        return
    app.renderer.info(f"[search] {query}")
    # A turn, not a re-sent slash line: the agent runs the search tools itself.
    app.send_user_message(
        f"Search for: {query}. Use the search and knowledge tools, follow up across "
        "sources where one hit points to another, and cite each source you use."
    )


def _cmd_team_surface(app: "UnifiedTUI", args: List[str]) -> None:
    """Delegate /team to the teamup CLI surface."""
    if not args:
        app.renderer.info(
            "usage: /team create|list|status|run|pause|resume|cancel|logs|report|hardware\n"
            "  use the CLI for full teamup control: r4 team <subcommand>"
        )
        return
    app.renderer.info(f"use the CLI for team operations: r4 team {' '.join(args)}")


# ---------------------------------------------------------------------------
# Gap-analysis slash commands (audit, traces, checkpoints, etc.)
# ---------------------------------------------------------------------------


def _gw_get(app: "UnifiedTUI", path: str, **kwargs: object) -> Any:
    """Helper to GET a gateway path with a short timeout."""
    import asyncio

    url = f"{app.client.config.gateway_url}{path}"
    return asyncio.ensure_future(app.client.get(url, **kwargs))  # type: ignore[arg-type]


def _render_kv(app: "UnifiedTUI", data: dict, indent: int = 2) -> None:
    """Render a dict as key-value lines."""
    prefix = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            app.renderer.info(f"{prefix}{key}:")
            _render_kv(app, value, indent + 2)
        elif isinstance(value, list):
            app.renderer.info(f"{prefix}{key}: ({len(value)} items)")
        else:
            app.renderer.info(f"{prefix}{key}: {value}")


def _render_table(
    app: "UnifiedTUI",
    rows: list[dict],
    columns: list[str],
    *,
    max_rows: int = 20,
) -> None:
    """Render a list of dicts as a simple table."""
    if not rows:
        app.renderer.info("(no records)")
        return
    header = "  " + "  ".join(f"{c:<16}" for c in columns)
    app.renderer.info(header)
    app.renderer.info("  " + "-" * (len(header) - 2))
    for row in rows[:max_rows]:
        line = "  " + "  ".join(f"{str(row.get(c, '')):<16}" for c in columns)
        app.renderer.info(line)
    if len(rows) > max_rows:
        app.renderer.info(f"  ... ({len(rows) - max_rows} more)")


def _cmd_audit(app: "UnifiedTUI", args: List[str]) -> None:
    """``/audit list|show|summary`` — view audit events."""
    if not args:
        app.renderer.info("usage: /audit list|show <id>|summary")
        return

    sub = args[0].lower()
    client = app.client

    try:
        if sub == "list":
            params: dict[str, Any] = {}
            if len(args) > 1:
                params["limit"] = int(args[1])
            data = client.get(
                f"{getattr(client, '_api_prefix', '/gw')}/ops/audit/trace", params=params
            )
            events = data.get("events", [])
            _render_table(
                app,
                events,
                ["ts", "event", "actor", "project_id", "outcome"],
            )
        elif sub == "show" and len(args) > 1:
            _audit_show(app, client, args[1])
        elif sub == "summary":
            _audit_summary(app, client)
        else:
            app.renderer.info("usage: /audit list|show <id>|summary")
    except Exception as exc:
        app.renderer.error(f"audit query failed: {exc}")


# /umrs/ops/audit/trace filters on request_id/event/actor/time only and
# drops unknown params, so show and summary work on its newest events.
_AUDIT_SCAN_LIMIT = 500


def _audit_show(app: "UnifiedTUI", client: Any, event_id: str) -> None:
    data = client.get(
        f"{getattr(client, '_api_prefix', '/gw')}/ops/audit/trace",
        params={"limit": _AUDIT_SCAN_LIMIT},
    )
    for event in data.get("events", []):
        if event.get("event_id") == event_id:
            _render_kv(app, event)
            return
    app.renderer.info(
        f"audit event not found: {event_id} (searched the newest {_AUDIT_SCAN_LIMIT})"
    )


def _audit_summary(app: "UnifiedTUI", client: Any) -> None:
    from collections import Counter

    data = client.get(
        f"{getattr(client, '_api_prefix', '/gw')}/ops/audit/trace",
        params={"limit": _AUDIT_SCAN_LIMIT},
    )
    events = data.get("events", [])
    app.renderer.info(f"audit summary (newest {len(events)} events):")
    for label, key in (("by event", "event"), ("by outcome", "outcome")):
        app.renderer.info(f"  {label}:")
        for value, count in Counter(str(e.get(key) or "?") for e in events).most_common(10):
            app.renderer.info(f"    {value}: {count}")


def _trace_list_selector(args: List[str]) -> tuple[str | None, str | None]:
    # Parse --job or --execution
    job_id = None
    execution_id = None
    i = 1
    while i < len(args):
        if args[i] == "--job" and i + 1 < len(args):
            job_id = args[i + 1]
            i += 2
        elif args[i] == "--execution" and i + 1 < len(args):
            execution_id = args[i + 1]
            i += 2
        else:
            i += 1
    return job_id, execution_id


def _render_trace_rows(app: "UnifiedTUI", traces: list) -> None:
    if not traces:
        app.renderer.info("(no traces found)")
        return
    app.renderer.info(f"Traces ({len(traces)} steps):")
    for trace in traces:
        status = trace.get("status", "?")
        icon = {"succeeded": "✓", "failed": "✗", "running": "…"}.get(status, "?")
        name = trace.get("step_name", "")
        app.renderer.info(f"  [{icon}] {trace.get('trace_id', '')}  {name}")


def _trace_list(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    job_id, execution_id = _trace_list_selector(args)
    if not job_id and not execution_id:
        app.renderer.error("usage: /trace list --job <id> | --execution <id>")
        return
    if job_id:
        data = client.get(f"{getattr(client, '_api_prefix', '/gw')}/traces/job/{job_id}")
    else:
        data = client.get(
            f"{getattr(client, '_api_prefix', '/gw')}/traces/execution/{execution_id}"
        )
    _render_trace_rows(app, data.get("traces", []))


def _trace_show(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    _render_kv(app, client.get(f"{getattr(client, '_api_prefix', '/gw')}/traces/{args[1]}"))


def _trace_lineage(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    data = client.get(f"{getattr(client, '_api_prefix', '/gw')}/traces/{args[1]}/lineage")
    traces = data.get("traces", [])
    app.renderer.info(f"Lineage ({len(traces)} ancestors):")
    for trace in traces:
        app.renderer.info(f"  {trace.get('trace_id', '')}  {trace.get('step_name', '')}")


def _trace_children(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    data = client.get(f"{getattr(client, '_api_prefix', '/gw')}/traces/{args[1]}/children")
    traces = data.get("traces", [])
    app.renderer.info(f"Children ({len(traces)}):")
    for trace in traces:
        app.renderer.info(f"  {trace.get('trace_id', '')}  {trace.get('step_name', '')}")


_TRACE_HANDLERS: dict[str, tuple[Any, int]] = {
    "list": (_trace_list, 1),
    "show": (_trace_show, 2),
    "lineage": (_trace_lineage, 2),
    "children": (_trace_children, 2),
}


def _cmd_trace(app: "UnifiedTUI", args: List[str]) -> None:
    """``/trace list|show|lineage|children`` — inspect execution traces."""
    if not args:
        app.renderer.info("usage: /trace list --job <id>|--execution <id>")
        app.renderer.info("       /trace show <trace_id>")
        app.renderer.info("       /trace lineage <trace_id>")
        app.renderer.info("       /trace children <trace_id>")
        return

    _dispatch_subcommand(
        app,
        args,
        _TRACE_HANDLERS,
        "usage: /trace list|show|lineage|children",
        "trace",
    )


def _dispatch_subcommand(
    app: "UnifiedTUI",
    args: List[str],
    handlers: dict[str, tuple[Any, int]],
    usage: str,
    error_label: str,
) -> None:
    """Run ``handlers[args[0]](app, args, app.client)`` with arity + error guard.

    Each handler entry is ``(handler, min_args)``; ``min_args`` counts the
    subcommand token itself, so ``2`` means at least one argument after it.
    """
    entry = handlers.get(args[0].lower())
    if entry is None or len(args) < entry[1]:
        app.renderer.info(usage)
        return
    handler, _min_args = entry
    try:
        handler(app, args, app.client)
    except Exception as exc:
        app.renderer.error(f"{error_label} query failed: {exc}")


def _checkpoint_list(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    data = client.get(f"/gw/checkpoints/job/{args[1]}")
    _render_table(
        app,
        data.get("checkpoints", []),
        ["checkpoint_id", "job_id", "step_name", "status", "created_at"],
    )


def _checkpoint_latest(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    _render_kv(app, client.get(f"/gw/checkpoints/job/{args[1]}/latest"))


def _checkpoint_show(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    _render_kv(app, client.get(f"/gw/checkpoints/{args[1]}"))


def _checkpoint_delete(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    client.delete(f"/gw/checkpoints/job/{args[1]}")
    app.renderer.system(f"deleted checkpoints for job: {args[1]}")


_CHECKPOINT_HANDLERS: dict[str, tuple[Any, int]] = {
    "list": (_checkpoint_list, 2),
    "latest": (_checkpoint_latest, 2),
    "show": (_checkpoint_show, 2),
    "delete": (_checkpoint_delete, 2),
}


_RETIRED_DRIFT_2026_08_22 = (
    "back-end routes were never mounted (retired R4, drift remediation 2026-08-22)"
)


_RETIRED_NO_EDGE_2026_10_17 = "its routes have no gateway location (retired 2026-10-17, TUI audit)"


def _retired_surface(name: str, reason: str = _RETIRED_DRIFT_2026_08_22):
    """Mark a slash-command family as dead gateway-side (R4, 2026-08-22).

    These back-end routes were intentionally excluded from the production
    NGINX/UMRS mount set; the legacy Python gateway only proxied to routers
    that were never mounted. Keep the handlers (they may be remounted in a
    later release) but fail loudly so users get an actionable message.
    """

    def _wrap(handler):
        def _inner(app: "UnifiedTUI", args: List[str]) -> None:
            # System channel, not error: nothing failed locally — the
            # back-end family simply isn't mounted on this gateway.
            app.renderer.system(
                f"/{name}{' ' + ' '.join(args) if args else ''}: not available "
                f"on this gateway — {reason}."
            )

        return _inner

    return _wrap


@_retired_surface("checkpoint")
def _cmd_checkpoint(app: "UnifiedTUI", args: List[str]) -> None:
    """``/checkpoint list|latest|show|delete`` — job checkpoint viewer."""
    if not args:
        app.renderer.info("usage: /checkpoint list <job_id>")
        app.renderer.info("       /checkpoint latest <job_id>")
        app.renderer.info("       /checkpoint show <checkpoint_id>")
        app.renderer.info("       /checkpoint delete <job_id>")
        return

    _dispatch_subcommand(
        app,
        args,
        _CHECKPOINT_HANDLERS,
        "usage: /checkpoint list|latest|show|delete",
        "checkpoint",
    )


@_retired_surface("recovery")
def _cmd_recovery(app: "UnifiedTUI", args: List[str]) -> None:
    """``/recovery list|show`` — crash-recovery session browser."""
    if not args:
        app.renderer.info("usage: /recovery list|show <job_id>")
        return

    sub = args[0].lower()
    client = app.client

    try:
        if sub == "list":
            data = client.get("/gw/recovery/sessions")
            sessions = data.get("sessions", [])
            _render_table(
                app,
                sessions,
                ["session_id", "job_id", "status", "recovered_at"],
            )
        elif sub == "show" and len(args) > 1:
            data = client.get(f"/gw/recovery/sessions/{args[1]}")
            _render_kv(app, data)
        else:
            app.renderer.info("usage: /recovery list|show")
    except Exception as exc:
        app.renderer.error(f"recovery query failed: {exc}")


@_retired_surface("replay")
def _cmd_replay(app: "UnifiedTUI", args: List[str]) -> None:
    """``/replay show <replay_id>`` — step replay viewer."""
    if not args or args[0] != "show" or len(args) < 2:
        app.renderer.info("usage: /replay show <replay_id>")
        return

    client = app.client
    try:
        data = client.get(f"/gw/step-replay/{args[1]}")
        _render_kv(app, data)
    except Exception as exc:
        app.renderer.error(f"replay query failed: {exc}")


def _outcome_list(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    data = client.get("/gw/workflow-outcomes/outcomes")
    _render_table(
        app,
        data.get("outcomes", []),
        ["outcome_id", "workflow_name", "status", "created_at"],
    )


def _outcome_show(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    _render_kv(app, client.get(f"/gw/workflow-outcomes/outcomes/{args[1]}"))


def _outcome_summary(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    _render_kv(app, client.get(f"/gw/workflow-outcomes/{args[1]}/outcomes/summary"))


def _outcome_by_model(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    data = client.get(f"/gw/workflow-outcomes/outcomes/by-model/{args[1]}")
    _render_table(
        app,
        data.get("outcomes", []),
        ["outcome_id", "workflow_name", "status", "model_ref"],
    )


_OUTCOME_HANDLERS: dict[str, tuple[Any, int]] = {
    "list": (_outcome_list, 1),
    "show": (_outcome_show, 2),
    "summary": (_outcome_summary, 2),
    "by-model": (_outcome_by_model, 2),
}


@_retired_surface("outcome")
def _cmd_outcome(app: "UnifiedTUI", args: List[str]) -> None:
    """``/outcome list|show|summary|by-model`` — workflow outcomes."""
    if not args:
        app.renderer.info("usage: /outcome list|show <id>|summary <wf>|by-model <model>")
        return

    _dispatch_subcommand(
        app,
        args,
        _OUTCOME_HANDLERS,
        "usage: /outcome list|show|summary|by-model",
        "outcome",
    )


@_retired_surface("lineage")
def _cmd_lineage(app: "UnifiedTUI", args: List[str]) -> None:
    """``/lineage <job_id>`` — show workflow lineage graph."""
    if not args:
        app.renderer.info("usage: /lineage <job_id>")
        return

    client = app.client
    try:
        data = client.get(f"/gw/workflow-fork/lineage/{args[0]}")
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        app.renderer.info(f"Lineage graph: {len(nodes)} nodes, {len(edges)} edges")
        for node in nodes[:10]:
            app.renderer.info(
                f"  {node.get('node_id', '')}  {node.get('label', '')}  "
                f"({node.get('node_type', '?')})"
            )
        if len(nodes) > 10:
            app.renderer.info(f"  ... ({len(nodes) - 10} more nodes)")
    except Exception as exc:
        app.renderer.error(f"lineage query failed: {exc}")


@_retired_surface("adaptive")
def _cmd_adaptive(app: "UnifiedTUI", args: List[str]) -> None:
    """``/adaptive suggest <workflow>|heuristics`` — adaptive planning."""
    if not args:
        app.renderer.info("usage: /adaptive suggest <workflow_name>")
        app.renderer.info("       /adaptive heuristics")
        return

    sub = args[0].lower()
    client = app.client

    try:
        if sub == "suggest" and len(args) > 1:
            data = client.get(f"/gw/adaptive-planning/suggest/{args[1]}")
            suggestions = data.get("suggestions", [])
            app.renderer.info(f"Adaptive suggestions ({len(suggestions)}):")
            for suggestion in suggestions:
                app.renderer.info(
                    f"  [{suggestion.get('confidence', '?')}]  {suggestion.get('description', '')}"
                )
        elif sub == "heuristics":
            data = client.get("/gw/adaptive-planning/heuristics")
            heuristics = data.get("heuristics", [])
            app.renderer.info(f"Adaptive heuristics ({len(heuristics)}):")
            for heuristic in heuristics:
                app.renderer.info(
                    f"  {heuristic.get('heuristic_id', '')}  "
                    f"{heuristic.get('description', '')}  "
                    f"(active={heuristic.get('active', True)})"
                )
        else:
            app.renderer.info("usage: /adaptive suggest <workflow>|heuristics")
    except Exception as exc:
        app.renderer.error(f"adaptive planning query failed: {exc}")


def _vectors_versions(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    params: dict[str, Any] = {}
    if len(args) > 1:
        params["model_name"] = args[1]
    data = client.get("/gw/vectors/versions", params=params)
    _render_table(
        app,
        data.get("versions", []),
        ["version_id", "model_name", "dim", "active", "created_at"],
    )


def _vectors_active(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    _render_kv(app, client.get(f"/gw/vectors/versions/active/{args[1]}"))


def _vectors_summary(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    _render_kv(app, client.get("/gw/vectors/summary"))


def _vectors_reembed(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    data = client.post("/gw/vectors/reembed", json={"model_name": args[1]})
    _render_kv(app, data)


_VECTORS_HANDLERS: dict[str, tuple[Any, int]] = {
    "versions": (_vectors_versions, 1),
    "active": (_vectors_active, 2),
    "summary": (_vectors_summary, 1),
    "reembed": (_vectors_reembed, 2),
}


@_retired_surface("vectors")
def _cmd_vectors(app: "UnifiedTUI", args: List[str]) -> None:
    """``/vectors versions|active|summary|reembed`` — vector management."""
    if not args:
        app.renderer.info("usage: /vectors versions [model_name]")
        app.renderer.info("       /vectors active <model_name>")
        app.renderer.info("       /vectors summary")
        app.renderer.info("       /vectors reembed <model_name>")
        return

    _dispatch_subcommand(
        app,
        args,
        _VECTORS_HANDLERS,
        "usage: /vectors versions|active|summary|reembed",
        "vector",
    )


def _governance_flag_params(args: List[str], flag_map: dict[str, str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    i = 1
    while i < len(args):
        key = flag_map.get(args[i])
        if key is not None and i + 1 < len(args):
            params[key] = args[i + 1]
            i += 2
        else:
            i += 1
    return params


def _governance_quotas(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    data = client.get(
        f"{getattr(client, '_api_prefix', '/gw')}/ops/governance/quotas",
        params={"project_id": args[1]},
    )
    quotas = data.get("quotas", [])
    _render_table(
        app,
        quotas,
        ["resource_type", "current_count", "warn_count", "max_count"],
    )


def _governance_ownership(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    params = _governance_flag_params(args, {"--owner": "owner_id", "--project": "project_id"})
    data = client.get(
        f"{getattr(client, '_api_prefix', '/gw')}/ops/governance/ownership", params=params
    )
    records = data.get("records", [])
    _render_table(
        app,
        records,
        ["resource_type", "resource_id", "owner_id", "project_id"],
    )


_DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _since_timestamp(value: str) -> float:
    """``1h`` / ``30m`` / ``2d`` -> a unix lower bound; a bare number is one already."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd])", value.strip().lower())
    if match is None:
        return float(value)
    return time.time() - float(match.group(1)) * _DURATION_SECONDS[match.group(2)]


def _governance_audit(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    params = _governance_flag_params(args, {"--project": "project_id", "--since": "since"})
    if "since" in params:
        # The route types since as a float timestamp; "1h" was a 422.
        params["since"] = _since_timestamp(params["since"])
    data = client.get(f"{getattr(client, '_api_prefix', '/gw')}/ops/audit/trace", params=params)
    events = data.get("events", [])
    _render_table(
        app,
        events,
        ["ts", "event", "actor", "project_id", "outcome"],
    )


def _governance_shares(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    params: dict[str, Any] = {}
    if len(args) > 1 and args[1] == "--resource-type" and len(args) > 2:
        params["resource_type"] = args[2]
    data = client.get(
        f"{getattr(client, '_api_prefix', '/gw')}/ops/governance/shares", params=params
    )
    shares = data.get("shares", [])
    _render_table(
        app,
        shares,
        ["resource_type", "resource_id", "granted_to", "access_level"],
    )


def _governance_orphans(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    # resource_type is a required query param (a 422 without it); the route
    # is admin-only and answers 403 unless the caller carries the admin token.
    data = client.get(
        f"{getattr(client, '_api_prefix', '/gw')}/ops/governance/orphans",
        params={"resource_type": args[1]},
    )
    orphans = data.get("orphans", [])
    _render_table(
        app,
        orphans,
        ["resource_type", "resource_id", "orphaned_at"],
    )


_GOVERNANCE_HANDLERS: dict[str, tuple[Any, int]] = {
    "quotas": (_governance_quotas, 2),
    "ownership": (_governance_ownership, 1),
    "audit": (_governance_audit, 1),
    "shares": (_governance_shares, 1),
    "orphans": (_governance_orphans, 2),
}


def _cmd_governance(app: "UnifiedTUI", args: List[str]) -> None:
    """``/governance quotas|ownership|audit|shares|orphans`` — governance."""
    if not args:
        app.renderer.info("usage: /governance quotas <project_id>")
        app.renderer.info("       /governance ownership [--owner ID] [--project ID]")
        app.renderer.info("       /governance audit [--project ID] [--since 1h]")
        app.renderer.info("       /governance shares [--resource-type TYPE]")
        app.renderer.info("       /governance orphans <resource_type>   (admin only)")
        return

    _dispatch_subcommand(
        app,
        args,
        _GOVERNANCE_HANDLERS,
        "usage: /governance quotas <project_id>|ownership|audit|shares|orphans <resource_type>",
        "governance",
    )


def _cmd_knowledge(app: "UnifiedTUI", args: List[str]) -> None:
    """``/knowledge <query>`` — search the project's knowledge (K3 facade).

    Same call as ``r4 knowledge search``. It POSTed ``/gw/knowledge/search``,
    a GET-only route, and every search failed with 405 (2026-10-17).
    """
    query = " ".join(args) if args else None
    if not query:
        app.renderer.info("usage: /knowledge <query>")
        return

    try:
        payload = app.client.knowledge.query(query, project_id=_tui_project_id(app), limit=20)
    except Exception as exc:
        app.renderer.error(f"knowledge search failed: {exc}")
        return
    _render_knowledge_results(app, payload)


def _render_knowledge_results(app: "UnifiedTUI", payload: dict) -> None:
    """Print the K3 facade's hits, which arrive as {"status": "ok", "query": {...}}."""
    if isinstance(payload.get("query"), dict):
        payload = payload["query"]
    results = payload.get("results") or []
    if not results:
        app.renderer.info("(no knowledge results)")
        return
    app.renderer.info(f"Knowledge results ({len(results)}):")
    for i, result in enumerate(results[:20], start=1):
        for line in _knowledge_result_lines(i, result):
            app.renderer.info(line)


_KNOWLEDGE_SNIPPET_CHARS = 160
#: Only the top hits carry a snippet line (20 hits at 3 lines each was 60).
_KNOWLEDGE_SNIPPET_HITS = 5


def _knowledge_result_lines(number: int, result: dict[str, Any]) -> list[str]:
    """A hit's title and score, and for the top hits a one-line snippet
    (K1.4). The passage id is not printed: no user command takes it (review
    of K1 part A, 2026-10-24); the agent reads it from the tool result."""
    from r4_contracts.context_pack import sanitize_name, sanitize_text

    score = result.get("score")
    shown = f"{score:.2f}" if isinstance(score, (int, float)) else "?"
    title = sanitize_name(str(result.get("title") or result.get("source") or "?"))
    lines = [f"  [{number}] {title}  (score: {shown})"]
    snippet = " ".join(sanitize_text(str(result.get("content") or "")).split())
    if snippet and number <= _KNOWLEDGE_SNIPPET_HITS:
        clipped = " …" if len(snippet) > _KNOWLEDGE_SNIPPET_CHARS else ""
        lines.append(f"      {snippet[:_KNOWLEDGE_SNIPPET_CHARS]}{clipped}")
    return lines


@_retired_surface("visibility")
def _cmd_visibility(app: "UnifiedTUI", args: List[str]) -> None:
    """``/visibility show <model>|update <model> <scope>`` — model visibility."""
    if not args:
        app.renderer.info("usage: /visibility show <model_name>")
        app.renderer.info("       /visibility update <model_name> <scope>")
        return

    sub = args[0].lower()
    client = app.client

    try:
        if sub == "show" and len(args) > 1:
            data = client.get(
                "/gw/model-visibility/visibility/resolve",
                params={"model_name": args[1]},
            )
            _render_kv(app, data)
        elif sub == "update" and len(args) > 2:
            data = client.patch(
                "/gw/model-visibility/visibility",
                json={"model_name": args[1], "scope": args[2]},
            )
            _render_kv(app, data)
        else:
            app.renderer.info("usage: /visibility show <model>|update <model> <scope>")
    except Exception as exc:
        app.renderer.error(f"visibility query failed: {exc}")


def _memory_list(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    tier = args[1] if len(args) > 1 else "session"
    data = client.get(f"/gw/reflective-memory/{tier}")
    _render_table(
        app,
        data.get("entries", []),
        ["entry_id", "tier", "content_preview", "created_at"],
    )


def _memory_search(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    query = " ".join(args[1:])
    data = client.post(
        "/gw/reflective-memory/search",
        json={"query": query, "limit": 20},
    )
    results = data.get("results", [])
    app.renderer.info(f"Memory search results ({len(results)}):")
    for i, result in enumerate(results[:20], start=1):
        score = result.get("score", "")
        preview = result.get("content_preview", "")
        app.renderer.info(f"  [{i}] (score: {score}) {preview}")


def _memory_add(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    text = " ".join(args[1:])
    data = client.post(
        "/gw/context/memory",
        json={"content": text, "tier": "session"},
    )
    _render_kv(app, data)


def _memory_delete(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    client.delete(f"/gw/reflective-memory/{args[1]}/{args[2]}")
    app.renderer.system(f"deleted memory entry: {args[1]}/{args[2]}")


def _memory_reset(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    client.post(f"/gw/reflective-memory/{args[1]}/reset")
    app.renderer.system(f"reset memory tier: {args[1]}")


_MEMORY_HANDLERS: dict[str, tuple[Any, int]] = {
    "list": (_memory_list, 1),
    "search": (_memory_search, 2),
    "add": (_memory_add, 2),
    "delete": (_memory_delete, 3),
    "reset": (_memory_reset, 2),
}


# The edge has no /gw/reflective-memory or /gw/context location and
# /umrs/context is not mounted; /secret targets /gw/secret-broker while the
# edge serves only /gw/secrets with other shapes (TUI audit 2026-10-17).
@_retired_surface("memory", _RETIRED_NO_EDGE_2026_10_17)
def _cmd_memory(app: "UnifiedTUI", args: List[str]) -> None:
    """``/memory list|search|add|delete|reset`` — reflective/context memory."""
    if not args:
        app.renderer.info("usage: /memory list [tier]")
        app.renderer.info("       /memory search <query>")
        app.renderer.info("       /memory add <text>")
        app.renderer.info("       /memory delete <tier> <entry_id>")
        app.renderer.info("       /memory reset <tier>")
        return

    _dispatch_subcommand(
        app,
        args,
        _MEMORY_HANDLERS,
        "usage: /memory list|search|add|delete|reset",
        "memory",
    )


def _secret_list(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    params: dict[str, Any] = {}
    if len(args) > 1:
        params["project_id"] = args[1]
    data = client.get("/gw/secret-broker/secrets", params=params)
    secrets = data.get("secrets", [])
    _render_table(
        app,
        secrets,
        ["secret_id", "name", "scope", "project_id", "created_at"],
    )


def _secret_show(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    _render_kv(app, client.get(f"/gw/secret-broker/secrets/{args[1]}"))


def _secret_lease(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    data = client.post(
        f"/gw/secret-broker/secrets/{args[1]}/lease",
        json={"duration_seconds": 3600},
    )
    _render_kv(app, data)


def _secret_revoke(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    client.post(f"/gw/secret-broker/leases/{args[1]}/revoke")
    app.renderer.system(f"revoked lease: {args[1]}")


def _secret_renew(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    data = client.post(
        f"/gw/secret-broker/leases/{args[1]}/renew",
        json={"duration_seconds": 3600},
    )
    _render_kv(app, data)


def _secret_audit(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    limit = int(args[1]) if len(args) > 1 else 50
    data = client.get(
        "/gw/secret-broker/audit",
        params={"limit": limit},
    )
    events = data.get("events", [])
    _render_table(
        app,
        events,
        ["ts", "event", "actor", "secret_id", "outcome"],
    )


def _secret_cleanup(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    _render_kv(app, client.post("/gw/secret-broker/cleanup"))


_SECRET_HANDLERS: dict[str, tuple[Any, int]] = {
    "list": (_secret_list, 1),
    "show": (_secret_show, 2),
    "lease": (_secret_lease, 2),
    "revoke": (_secret_revoke, 2),
    "renew": (_secret_renew, 2),
    "audit": (_secret_audit, 1),
    "cleanup": (_secret_cleanup, 1),
}


@_retired_surface("secret", _RETIRED_NO_EDGE_2026_10_17)
def _cmd_secret(app: "UnifiedTUI", args: List[str]) -> None:
    """``/secret list|show|lease|revoke|renew|audit|cleanup`` — secret broker."""
    if not args:
        app.renderer.info("usage: /secret list [project_id]")
        app.renderer.info("       /secret show <secret_id>")
        app.renderer.info("       /secret lease <secret_id>")
        app.renderer.info("       /secret revoke <lease_id>")
        app.renderer.info("       /secret renew <lease_id>")
        app.renderer.info("       /secret audit [limit]")
        app.renderer.info("       /secret cleanup")
        return

    _dispatch_subcommand(
        app,
        args,
        _SECRET_HANDLERS,
        "usage: /secret list|show|lease|revoke|renew|audit|cleanup",
        "secret broker",
    )


def _listing_rows(data: Any, key: str) -> list:
    """UMRS answers these listings with a bare JSON array; older shapes wrapped it."""
    return data if isinstance(data, list) else data.get(key, [])


_PIPELINE_CRUD_RETIRED = (
    "retired (2026-10-18) — pipeline definitions were kept in UMRS process "
    "memory, lost at every restart, and never read by ingest or search. "
    "Ingestion chunks and embeds with the site settings "
    "UMRS_RAG_CHUNK_MAX_TOKENS and UMRS_RAG_EMBEDDING_MODEL; list the "
    "components with /pipeline components [chunkers|embedders|rerankers]."
)


def _pipeline_crud_retired(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    """``/pipeline list|show|create|delete``: name the replacement, call nothing."""
    del client
    app.renderer.system(f"/pipeline {args[0]}: {_PIPELINE_CRUD_RETIRED}")


def _pipeline_rag_profiles(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    # UMRS retired rag-profiles CRUD (owner decision 2026-08-22): every
    # /umrs/rag-profiles route answers 410 Gone.
    app.renderer.system(
        "/pipeline rag-profiles: retired — profiles were never read at ingest or "
        "search time (UMRS answers 410); tune retrieval in the site config."
    )


# Keys of the UMRS registry entries' to_dict() (umrs/src/umrs/rag/registry.py).
_COMPONENT_COLUMNS: dict[str, list[str]] = {
    "chunkers": ["chunker_type", "name", "is_builtin"],
    "embedders": ["embedder_type", "model_name", "dimension", "is_builtin"],
    "rerankers": ["reranker_type", "model_name", "is_builtin"],
}


def _pipeline_components(app: "UnifiedTUI", args: List[str], client: Any) -> None:
    component_type = args[1] if len(args) > 1 else "chunkers"
    components = _listing_rows(
        client.get(f"{getattr(client, '_api_prefix', '/gw')}/components/{component_type}"),
        "components",
    )
    _render_table(
        app,
        components,
        _COMPONENT_COLUMNS.get(component_type, ["name", "model_name", "is_builtin"]),
    )


_PIPELINE_HANDLERS: dict[str, tuple[Any, int]] = {
    "list": (_pipeline_crud_retired, 1),
    "show": (_pipeline_crud_retired, 1),
    "create": (_pipeline_crud_retired, 1),
    "delete": (_pipeline_crud_retired, 1),
    "rag-profiles": (_pipeline_rag_profiles, 1),
    "components": (_pipeline_components, 1),
}


def _cmd_pipeline(app: "UnifiedTUI", args: List[str]) -> None:
    """``/pipeline components``; list/show/create/delete/rag-profiles are retired."""
    if not args:
        app.renderer.info("usage: /pipeline components [chunkers|embedders|rerankers]")
        app.renderer.info("       (list/show/create/delete and rag-profiles are retired)")
        return

    _dispatch_subcommand(
        app,
        args,
        _PIPELINE_HANDLERS,
        "usage: /pipeline components [chunkers|embedders|rerankers]",
        "pipeline",
    )


__all__ = ["SlashCommand", "SlashRegistry", "get_slash_registry"]
