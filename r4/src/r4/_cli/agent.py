"""``r4 agent`` command handler (bug.memo D3 Stage 1, 2026-04-25)."""

from __future__ import annotations

import re
import sys
from argparse import ArgumentParser, Namespace

from ._helpers import _die, _parse_lookback
from .base import CommandHandler
from .registry import command

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

_THINKING_DIM = "\033[2;3m"  # dim + italic
_RESET = "\033[0m"


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks, returning only visible content."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _format_thinking(text: str | None) -> str:
    """Format model output: show thinking blocks dimmed, then the answer.

    Thinking content is rendered in dim italic with a 💭 prefix so users
    understand it is the model's internal reasoning, not the answer.
    """
    if not text:
        # Reasoning models (glm-5.3, K3) spend max_tokens on reasoning first: a
        # tight budget can yield content=None. Printing nothing is the honest
        # rendering; callers that loop on empty output (bench graders) retry.
        return ""
    thinking_blocks = _THINK_RE.findall(text)
    visible = _THINK_RE.sub("", text).strip()

    parts: list[str] = []
    for block in thinking_blocks:
        preview = block.strip()
        if preview:
            for line in preview.splitlines():
                parts.append(f"{_THINKING_DIM}💭 {line}{_RESET}")
    if parts:
        parts.append("")  # blank line separator
    if visible:
        parts.append(visible)
    return "\n".join(parts) if parts else ""


def _ensure_agent_chat_defaults(args: Namespace) -> None:
    defaults = {
        "agent_command": "chat",
        "message": None,
        "session": None,
        "project": None,
        "model": None,
        "mode": "interactive",
        "egress_policy": "ask_always",
    }
    for name, value in defaults.items():
        if not hasattr(args, name) or getattr(args, name) is None:
            setattr(args, name, value)


def _prompt_agent_action_approval(request):
    from ..agent import EgressDecision

    print("", file=sys.stderr)
    print("Agent action approval required", file=sys.stderr)
    print(f"  Tool: {request.tool_name}", file=sys.stderr)
    print(f"  Destination: {request.destination_url}", file=sys.stderr)
    print(f"  Payload hash: {request.payload_hash}", file=sys.stderr)
    preview = request.get_preview(240)
    if preview:
        print(f"  Preview: {preview}", file=sys.stderr)
    if request.sensitivity_flags.is_sensitive:
        print("  Sensitive content was detected; review carefully.", file=sys.stderr)

    if not sys.stdin.isatty():
        print("  Denied because stdin is not interactive.", file=sys.stderr)
        return EgressDecision.DENIED

    answer = input("Approve this action? [y/N] ").strip().lower()
    return EgressDecision.APPROVED if answer in {"y", "yes"} else EgressDecision.DENIED


def _install_cli_approval(agent) -> None:
    set_callback = getattr(agent, "set_approval_callback", None)
    if callable(set_callback):
        set_callback(_prompt_agent_action_approval)


def _normalized_objective(text: str) -> str:
    return " ".join(text.lower().split())


def _wants_platform_health(normalized: str) -> bool:
    return "health" in normalized and any(
        word in normalized for word in ("check", "show", "status", "platform", "gateway")
    )


def _wants_model_list(normalized: str) -> bool:
    return "model" in normalized and any(
        word in normalized for word in ("list", "show", "available")
    )


def _wants_endpoint_list(normalized: str) -> bool:
    return "endpoint" in normalized and any(
        word in normalized for word in ("list", "show", "status")
    )


def _wants_endpoint_up(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in ("start ", "launch ", "provision ", "spin up ", "endpoint up")
    )


def _wants_endpoint_down(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in ("stop ", "shutdown ", "terminate ", "endpoint down", "take down")
    )


def _extract_model_from_objective(objective: str) -> str | None:
    words = objective.split()
    skip = {
        "start",
        "launch",
        "provision",
        "stop",
        "shutdown",
        "terminate",
        "spin",
        "up",
        "down",
        "the",
        "a",
        "an",
        "endpoint",
        "model",
        "and",
        "test",
        "it",
        "then",
        "please",
        "take",
    }
    for word in words:
        cleaned = word.strip("\"'").rstrip(".,!?")
        if cleaned and cleaned.lower() not in skip and not cleaned.startswith("--"):
            return cleaned
    return None


def _print_agent_json(payload: dict) -> None:
    import json

    print(json.dumps(payload, indent=2, default=str))


def _handle_platform_health(client, args) -> bool:
    if not _wants_platform_health(_normalized_objective(args.objective)):
        return False
    readiness = client.readiness()
    if getattr(args, "as_json", False):
        _print_agent_json({"action": "health", "result": readiness})
    else:
        from ._helpers import _print_readiness_report

        _print_readiness_report(readiness)
    return True


def _handle_model_list(client, args) -> bool:
    if not _wants_model_list(_normalized_objective(args.objective)):
        return False
    models = client.list_models()
    if getattr(args, "as_json", False):
        _print_agent_json({"action": "model_list", "models": models})
    else:
        print("Available models:")
        for model in models:
            print(f"  - {model}")
    return True


def _handle_endpoint_list(client, args) -> bool:
    if not _wants_endpoint_list(_normalized_objective(args.objective)):
        return False
    payload = client.list_endpoints(include_stopped=False)
    if getattr(args, "as_json", False):
        _print_agent_json({"action": "endpoint_list", **payload})
    else:
        endpoints = payload.get("endpoints", [])
        if not endpoints:
            print("No active endpoints.")
        for endpoint in endpoints:
            print(
                f"{endpoint.get('instance_id') or endpoint.get('job_id')}: "
                f"{endpoint.get('model') or 'unknown'} "
                f"status={endpoint.get('status') or 'unknown'} "
                f"url={endpoint.get('endpoint_url') or '-'}"
            )
    return True


def _handle_endpoint_up(client, args) -> bool:
    normalized = _normalized_objective(args.objective)
    if not _wants_endpoint_up(normalized):
        return False
    model = _extract_model_from_objective(args.objective)
    if not model:
        return False

    if not getattr(args, "as_json", False):
        print(f"Provisioning endpoint for model: {model}")
        if not sys.stdin.isatty():
            print("  (Non-interactive mode: proceeding automatically)")
        else:
            answer = input(f"Confirm provisioning {model}? [Y/n] ").strip().lower()
            if answer in ("n", "no"):
                print("Cancelled.")
                return True
    result = client.provision_endpoint(model=model, wait=True)
    if getattr(args, "as_json", False):
        _print_agent_json({"action": "endpoint_up", "model": model, "result": result})
    else:
        status = result.get("status", "unknown")
        url = result.get("endpoint_url", "-")
        job_id = result.get("instance_id") or result.get("job_id", "")
        print(f"Endpoint {job_id}: {model} status={status} url={url}")
    return True


def _handle_endpoint_down(client, args) -> bool:
    normalized = _normalized_objective(args.objective)
    if not _wants_endpoint_down(normalized):
        return False

    instance_id = _extract_model_from_objective(args.objective)
    if not instance_id:
        return False
    if not getattr(args, "as_json", False):
        if sys.stdin.isatty():
            answer = input(f"Confirm stopping endpoint {instance_id}? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Cancelled.")
                return True
        else:
            print(
                f"Denied: stopping endpoint {instance_id} requires interactive confirmation.",
                file=sys.stderr,
            )
            return True
    result = client.stop_endpoint(instance_id)
    if getattr(args, "as_json", False):
        _print_agent_json({"action": "endpoint_down", "instance_id": instance_id, "result": result})
    else:
        print(f"Endpoint {instance_id}: stopped")
    return True


def _maybe_handle_builtin_platform_action(client, args) -> bool:
    for handler in (
        _handle_platform_health,
        _handle_model_list,
        _handle_endpoint_list,
        _handle_endpoint_up,
        _handle_endpoint_down,
    ):
        if handler(client, args):
            return True
    return False


def build_agent_parser(subparsers, *, name: str = "agent") -> ArgumentParser:
    """Build the full agent subparser tree.

    Entry point of ``r4 agent``.
    """
    from argparse import RawDescriptionHelpFormatter

    ap = subparsers.add_parser(
        name,
        help="Legacy SciWorkflow Agent (explicit legacy entry point)",
        description=(
            "Legacy SciWorkflow Agent for scientific workflow planning. "
            "R4-owned UIs and `r4 acp` use Research Companion instead. "
            "With no subcommand, this starts agent chat. With a plain sentence "
            "after `agent`, R4 routes how-to questions to chat and action "
            "requests to `run`."
        ),
        epilog=(
            "Examples:\n"
            f"  r4 {name}\n"
            f'  r4 {name} "How do I check platform health?"\n'
            f'  r4 {name} "Start qwen3-8b and test it"\n'
            f'  r4 {name} run "triage the failing workflow"\n'
            f"  r4 {name} chat --mode interactive\n"
            f"  r4 {name} tools --search rag"
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    ap.set_defaults(
        agent_command="chat",
        message=None,
        session=None,
        project=None,
        model=None,
        mode="interactive",
        egress_policy="ask_always",
    )
    agt = ap.add_subparsers(
        dest="agent_command",
        title="subcommands",
        metavar="<agent-command>",
    )

    a_chat = agt.add_parser("chat", help="Ask how to use R4, or chat with the agent")
    a_chat.add_argument("message", nargs="?", help="Initial message (or start interactive)")
    a_chat.add_argument("--session", "-s", help="Resume existing session ID")
    a_chat.add_argument("--project", "-p", help="Project ID for RAG context")
    a_chat.add_argument("--model", "-m", help="LLM model to use")
    a_chat.add_argument(
        "--source",
        metavar="NAME",
        help="Endpoint source to use (see `r4 source list`)",
    )
    a_chat.add_argument(
        "--mode",
        choices=["interactive", "autonomous", "planning"],
        default="interactive",
        help="Agent mode (default: interactive)",
    )
    a_chat.add_argument(
        "--egress-policy",
        choices=["ask_always", "ask_sensitive", "allow_all"],
        default="ask_always",
        help="Egress approval policy (default: ask_always)",
    )

    a_run = agt.add_parser("run", help="Plan and run a workflow from a natural-language objective")
    a_run.add_argument("objective", help="Workflow objective in natural language")
    a_run.add_argument("--project", "-p", help="Project ID")
    a_run.add_argument("--model", "-m", help="LLM model to use")
    a_run.add_argument(
        "--source",
        metavar="NAME",
        help="Endpoint source to use (see `r4 source list`)",
    )
    a_run.add_argument("--input", "-i", action="append", help="Input values (key=value)")
    a_run.add_argument("--stream", action="store_true", help="Stream execution events")
    a_run.add_argument(
        "--egress-policy",
        choices=["ask_always", "ask_sensitive", "allow_all"],
        default="ask_always",
        help="Approval policy for tool/command actions (default: ask_always)",
    )
    a_run.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")

    a_create = agt.add_parser("create", help="Create a workflow from description")
    a_create.add_argument("description", help="Workflow description in natural language")
    a_create.add_argument("--name", "-n", help="Workflow name (auto-generated if not provided)")
    a_create.add_argument("--project", "-p", help="Project ID")
    a_create.add_argument("--model", "-m", help="LLM model for workflow planning")
    a_create.add_argument(
        "--source",
        metavar="NAME",
        help="Endpoint source to use (see `r4 source list`)",
    )
    a_create.add_argument(
        "--output",
        "-o",
        choices=["yaml", "json", "run"],
        default="yaml",
        help="Output format: yaml/json for definition, run to execute immediately",
    )
    a_create.add_argument("--save", "-s", help="Save workflow to file")
    a_create.add_argument(
        "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
    )

    a_sessions = agt.add_parser("sessions", help="List and manage sessions")
    a_sessions.add_argument("--delete", "-d", help="Delete session by ID")
    a_sessions.add_argument(
        "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
    )

    a_tools = agt.add_parser("tools", help="List available tools")
    a_tools.add_argument("--server", help="Filter by MCP server")
    a_tools.add_argument("--search", "-s", help="Search tool names/descriptions")
    a_tools.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")

    a_scan = agt.add_parser("scan", help="Scan content for sensitive data")
    a_scan.add_argument("--file", "-f", help="File to scan")
    a_scan.add_argument("--text", "-t", help="Text to scan")
    a_scan.add_argument("--redact", "-r", action="store_true", help="Output redacted content")
    a_scan.add_argument("--json", "-j", action="store_true", dest="as_json", help="Output as JSON")

    a_audit = agt.add_parser("audit", help="Show this user's egress audit trail")
    a_audit.add_argument(
        "--last",
        default="24h",
        help="Lookback window (e.g. 1h, 24h, 7d). Default: 24h",
    )
    a_audit.add_argument(
        "--user",
        help="Filter by user_id (admins only; defaults to current user)",
    )
    a_audit.add_argument("--project", help="Filter by project_id")
    a_audit.add_argument(
        "--action",
        choices=["allowed", "denied"],
        help="Show only allowed or denied requests",
    )
    a_audit.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max rows to display (default: 100)",
    )
    a_audit.add_argument(
        "--json",
        "-j",
        action="store_true",
        dest="as_json",
        help="Output as JSON",
    )

    return ap


@command
class AgentCommand(CommandHandler):
    """``r4 agent`` — AI workflow agent."""

    name = "agent"
    help = "AI agent for scientific workflows"

    def register(self, subparsers) -> ArgumentParser:
        return build_agent_parser(subparsers, name=self.name)

    def handle(self, args: Namespace) -> int:

        cmd_agent(args)
        return 0


_AGENT_DISPATCH: dict[str, object] = {}  # populated after function definitions


def cmd_agent(args):
    """Handle agent command (A-1101 to A-1210)."""
    if not args.agent_command:
        _ensure_agent_chat_defaults(args)

    _apply_source_override(getattr(args, "source", None))
    _rebind_model_to_source(getattr(args, "model", None), getattr(args, "source", None))

    handler = _AGENT_DISPATCH.get(args.agent_command)
    if handler:
        handler(args)
    else:
        print(f"Unknown agent command: {args.agent_command}", file=sys.stderr)
        sys.exit(1)


def _apply_source_override(source_name: str | None) -> None:
    """Route subsequent LLM calls through a named endpoint source.

    Mirrors the --source wiring in ``r4 chat``: a platform source retargets
    the gateway URL; a public source switches the direct-endpoint config.
    """
    if not source_name:
        return
    from ..source import resolve_active_source
    from ..source_context import use_source

    source = resolve_active_source(source_name)
    if source is None:
        return
    use_source(source)  # its own credential only (onboarding plan S1)


def _rebind_model_to_source(model: str | None, source_name: str | None) -> None:
    """Persist model→route binding for an explicit --source choice.

    An explicit ``--source NAME`` is user intent and must beat any stale
    stored binding (e.g. a ``platform`` default written by an earlier
    unverified ``model use``). Clears the legacy/default binding so the
    route resolver picks the named source.
    """
    if not model or not source_name:
        return
    from ..config import Config, ProfileConfigStore, get_config, set_config
    from ..model_routing import update_model_route_map

    cfg = Config.from_env()
    routes = update_model_route_map(cfg, model, source_name)
    ProfileConfigStore().save(cfg.profile, model_routes=routes)
    from dataclasses import replace as _replace

    set_config(_replace(get_config(), model_routes=routes))


def cmd_agent_chat(args):
    """Interactive chat with the agent."""
    from ..agent import AgentConfig, AgentMode, AgentSession
    from ..client import Client

    # Bare public models (e.g. ollama) need a discovered route binding just
    # like `r4 chat`; otherwise the call falls back to the platform gateway.
    if getattr(args, "model", None):
        from .chat import _maybe_discover_source

        _maybe_discover_source(args.model)

    client = Client()
    config = _agent_chat_config(args, AgentConfig, AgentMode)
    session = _agent_chat_session(client, args, config, AgentSession)
    _install_cli_approval(session.agent)

    if args.project:
        session.set_project(args.project)

    session.on_event(_agent_chat_event)
    if args.message:
        response = _format_thinking(session.chat(args.message))
        print(f"\n{response}")
        return

    _run_agent_chat_repl(session)


def _agent_chat_config(args: Namespace, agent_config, agent_mode):
    mode_map = {
        "interactive": agent_mode.INTERACTIVE,
        "autonomous": agent_mode.AUTONOMOUS,
        "planning": agent_mode.PLANNING_ONLY,
    }
    mode = mode_map.get(args.mode, agent_mode.INTERACTIVE)
    return agent_config(
        mode=mode,
        model=args.model,
        egress_policy=args.egress_policy,
    )


def _agent_chat_session(client, args: Namespace, config, agent_session):
    if args.session:
        try:
            session = agent_session.load(client, args.session)
            print(f"Resumed session: {session.name}")
            return session
        except FileNotFoundError:
            print(f"Session not found: {args.session}", file=sys.stderr)
            sys.exit(1)
    session = agent_session.create(client, config=config)
    print(f"Created session: {session.id}")
    return session


def _agent_chat_event(event) -> None:
    event_icons = {
        "planning_start": "🔄",
        "planning_complete": "✅",
        "step_start": "▶️",
        "step_complete": "✓",
        "step_failed": "✗",
        "tool_call": "🔧",
        "tool_result": "📤",
        "egress_request": "🔒",
        "message": "💬",
        "error": "❌",
    }
    icon = event_icons.get(event.event_type.value, "•")
    print(f"  {icon} {event.message}")


def _run_agent_chat_repl(session) -> None:
    print(
        "Agent chat (ask R4 how-to questions or request work; type 'exit' to quit, 'save' to save)"
    )
    print("=" * 50)

    while True:
        try:
            should_exit = _handle_agent_chat_input(session, input("\nYou: ").strip())
            if should_exit:
                break
        except KeyboardInterrupt:
            session.save()
            print(f"\n\nSession saved: {session.id}")
            break
        except EOFError:
            break


def _handle_agent_chat_input(session, user_input: str) -> bool:
    if not user_input:
        return False
    command = user_input.lower()
    if command == "exit":
        session.save()
        print(f"\nSession saved: {session.id}")
        return True
    if command == "save":
        session.save()
        print(f"Session saved: {session.id}")
        return False
    if command == "tools":
        _print_agent_tools(session.agent.list_available_tools())
        return False

    response = _format_thinking(session.chat(user_input))
    print(f"\nAgent: {response}")
    return False


def _print_agent_tools(tools) -> None:
    for tool in tools[:20]:
        print(f"  - {tool['name']}: {tool['description'][:60]}...")


def cmd_agent_create(args):
    """Create a workflow from natural language description."""
    import json
    import uuid

    import yaml

    from ..agent import AgentConfig, AgentMode, SciWorkflowAgent
    from ..client import Client

    client = Client()

    # Configure agent for planning mode
    config = AgentConfig(
        mode=AgentMode.PLANNING_ONLY,
        model=args.model,
        egress_policy="ask_always",
    )

    agent = SciWorkflowAgent(client, config)
    _install_cli_approval(agent)

    # Plan workflow from description
    print(f"Planning workflow from: {args.description[:60]}...")

    try:
        workflow = agent.planner.plan(args.description)
    except Exception as e:
        _die(str(e), action="agent plan")

    # Set workflow name if provided
    if args.name:
        workflow.name = args.name
    elif not workflow.name:
        workflow.name = f"workflow_{uuid.uuid4().hex[:8]}"

    # Convert to serializable format
    workflow_dict = workflow.to_dict()

    # Output based on format
    if args.output == "run":
        # Execute immediately
        print(f"Executing workflow: {workflow.name}")
        result = agent.executor.execute_workflow(workflow)
        if args.as_json:
            print(json.dumps(result.to_dict(), indent=2, default=str))
        else:
            print(f"Status: {result.status.value}")
            print(f"Workflow ID: {workflow.id}")
    elif args.output == "json":
        output = json.dumps(workflow_dict, indent=2)
        if args.save:
            with open(args.save, "w") as f:
                f.write(output)
            print(f"Workflow saved to: {args.save}")
        else:
            print(output)
    else:  # yaml
        output = yaml.dump(workflow_dict, default_flow_style=False, sort_keys=False)
        if args.save:
            with open(args.save, "w") as f:
                f.write(output)
            print(f"Workflow saved to: {args.save}")
        else:
            print(output)

    if not args.save and not args.as_json and args.output != "run":
        print(f"\nWorkflow ID: {workflow.id}")
        print(f"Steps: {len(workflow.steps)}")
        print("Use 'r4 agent run <objective>' to execute")


def cmd_agent_run(args):
    """Run a workflow from an objective."""
    import json

    from ..agent import AgentConfig, AgentMode, SciWorkflowAgent
    from ..client import Client

    if getattr(args, "model", None):
        from .chat import _maybe_discover_source

        _maybe_discover_source(args.model)

    client = Client()
    if _maybe_handle_builtin_platform_action(client, args):
        return

    config = AgentConfig(
        mode=AgentMode.AUTONOMOUS,
        model=args.model,
        egress_policy=getattr(args, "egress_policy", "ask_always"),
    )

    agent = SciWorkflowAgent(client, config)
    _install_cli_approval(agent)
    _register_agent_run_stream(agent, args, json)

    result = agent.run(args.objective, inputs=_agent_run_inputs(args.input))
    _print_agent_run_result(result, args, json)


def _agent_run_inputs(raw_inputs: list[str] | None) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for item in raw_inputs or []:
        if "=" in item:
            key, value = item.split("=", 1)
            inputs[key] = value
    return inputs


def _register_agent_run_stream(agent, args: Namespace, json_module) -> None:
    if not args.stream:
        return

    def on_event(event):
        if args.as_json:
            print(json_module.dumps(event.to_dict()))
        else:
            print(f"[{event.event_type.value}] {event.message}")

    agent.on_event(on_event)


def _print_agent_run_result(result, args: Namespace, json_module) -> None:
    if args.as_json:
        print(json_module.dumps(_agent_run_payload(result), indent=2, default=str))
        return

    print(f"\nWorkflow: {result.workflow.name}")
    print(f"Status: {result.status.value}")
    print(f"Steps: {len(result.workflow.steps)}")
    _print_agent_step_results(result)


def _agent_run_payload(result) -> dict:
    return {
        "id": result.id,
        "status": result.status.value,
        "workflow": result.workflow.name,
        "steps": len(result.workflow.steps),
        "results": {key: value.to_dict() for key, value in result.step_results.items()},
    }


def _print_agent_step_results(result) -> None:
    for step_id, step_result in result.step_results.items():
        step = next((item for item in result.workflow.steps if item.id == step_id), None)
        desc = step.description if step else step_id
        if step_result.status.value == "completed":
            print(f"  ✓ {desc}")
        else:
            print(f"  ✗ {desc}: {step_result.error}")


def cmd_agent_sessions(args):
    """List and manage agent sessions."""
    import json

    from ..agent import AgentSession

    # Delete session if requested
    if args.delete:
        from ..client import Client

        client = Client()
        try:
            session = AgentSession.load(client, args.delete)
            session.delete()
            print(f"Deleted session: {args.delete}")
        except FileNotFoundError:
            print(f"Session not found: {args.delete}", file=sys.stderr)
            sys.exit(1)
        return

    # List sessions
    sessions = AgentSession.list_sessions()

    if args.as_json:
        print(json.dumps(sessions, indent=2))
    else:
        if not sessions:
            print("No sessions found")
            return

        print(f"{'ID':<36} {'Name':<30} {'Messages':<10} {'Updated':<20}")
        print("-" * 100)

        for s in sessions:
            updated = s["updated_at"][:19] if s.get("updated_at") else "N/A"
            print(f"{s['id']:<36} {s['name'][:28]:<30} {s['message_count']:<10} {updated:<20}")


def cmd_agent_tools(args):
    """List available tools."""
    import json

    from ..agent import SciWorkflowAgent
    from ..client import Client

    client = Client()
    agent = SciWorkflowAgent(client)

    tools = agent.list_available_tools()

    # Apply filters
    if args.server:
        tools = [t for t in tools if args.server in t.get("server", "")]

    if args.search:
        query = args.search.lower()
        tools = [
            tool
            for tool in tools
            if query in tool["name"].lower() or query in tool.get("description", "").lower()
        ]

    if args.as_json:
        print(json.dumps(tools, indent=2))
    else:
        if not tools:
            print("No tools found")
            return

        for t in tools:
            desc = t.get("description", "")[:70]
            if len(t.get("description", "")) > 70:
                desc += "..."
            print(f"  {t['name']}: {desc}")


def cmd_agent_scan(args):
    """Scan content for sensitive data."""
    import json

    from ..agent import ContentScanner

    scanner = ContentScanner()

    # Get content to scan
    if args.file:
        try:
            with open(args.file) as f:
                content = f.read()
        except Exception as e:
            _die(str(e), action="agent: reading file")
    elif args.text:
        content = args.text
    else:
        # Read from stdin
        content = sys.stdin.read()

    if not content:
        print("No content to scan", file=sys.stderr)
        sys.exit(1)

    # Scan
    result = scanner.scan(content)

    if args.redact:
        print(scanner.redact(content))
        return

    if args.as_json:
        print(
            json.dumps(
                {
                    "is_sensitive": result.is_sensitive,
                    "summary": result.summary(),
                    "matches": [
                        {
                            "pattern": m.pattern_name,
                            "category": m.category.name,
                            "redacted": m.redacted(),
                        }
                        for m in result.matches
                    ],
                },
                indent=2,
            )
        )
    else:
        if result.is_sensitive:
            print("⚠️  Sensitive content detected:")
            print(f"   {result.summary()}")
            print("\nMatches:")
            for m in result.matches[:10]:
                print(f"  - [{m.category.name}] {m.pattern_name}: {m.redacted()}")
            if len(result.matches) > 10:
                print(f"  ... and {len(result.matches) - 10} more")
        else:
            print("✓ No sensitive content detected")


def cmd_agent_audit(args):
    """Show egress audit trail for the current user (A-1209)."""
    import json

    from ..client import Client

    try:
        since = _parse_lookback(args.last)
    except ValueError as exc:
        _die(str(exc), action="agent audit: parsing --last")

    params: dict = {
        "since": since.isoformat(),
        "limit": args.limit,
    }
    if args.user:
        params["user_id"] = args.user
    if args.project:
        params["project_id"] = args.project
    if args.action:
        params["action"] = args.action

    client = Client()
    try:
        resp = client.get(f"{getattr(client, '_api_prefix', '/gw')}/egress/audit", params=params)
    except Exception as exc:  # noqa: BLE001
        _die(
            f"Failed to fetch egress audit: {exc}",
            action="agent audit: GET /gw/egress/audit",
            hint="The Gateway must expose /gw/egress/audit (proxy to UMRS).",
        )

    items = resp.get("items", []) if isinstance(resp, dict) else []
    if args.as_json:
        print(json.dumps({"items": items, "count": len(items)}, indent=2, default=str))
        return

    if not items:
        print("No egress requests in the selected window.")
        return

    print(f"{'When':<20} {'Action':<8} {'Tool':<18} {'Domain':<28} {'Status':<7} URL")
    print("-" * 110)
    for r in items:
        when = str(r.get("created_at", ""))[:19]
        action = r.get("action", "")
        tool = (r.get("tool_name") or "-")[:18]
        domain = (r.get("destination_domain") or "")[:28]
        status_code = r.get("response_status")
        url = r.get("destination_url", "")
        print(
            f"{when:<20} {action:<8} {tool:<18} {domain:<28} "
            f"{(str(status_code) if status_code is not None else '-'):<7} {url}"
        )


_AGENT_DISPATCH.update(
    {
        "chat": cmd_agent_chat,
        "create": cmd_agent_create,
        "run": cmd_agent_run,
        "sessions": cmd_agent_sessions,
        "tools": cmd_agent_tools,
        "scan": cmd_agent_scan,
        "audit": cmd_agent_audit,
    }
)
