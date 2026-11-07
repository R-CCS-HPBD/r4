"""Bridge unified chat slash commands into the companion UIs."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from rich.console import Console
from rich.text import Text

from r4.companion.agent_mode import apply_mode, session_mode
from r4.companion.session_models import SessionMessage
from r4.tui.commands import SUBCOMMANDS, get_slash_registry
from r4.tui.modes import Mode


@dataclass(frozen=True)
class CommandSpec:
    name: str
    help: str
    category: str = "companion"


COMPANION_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("help", "Show commands and shortcuts", "help"),
    CommandSpec("quit", "Save and exit", "session"),
    CommandSpec("export", "Preview markdown or save: /export [path.md]", "export"),
    CommandSpec("usage", "Show billing usage (LLM calls, tokens, node-hours)", "context"),
    CommandSpec("endpoint", "Endpoint operations (list, up, down, status)", "endpoint"),
    # /remember, /memories, /forget, /milestone omitted until implemented (C-03)
    CommandSpec("probe", "Re-scan environment", "harness"),
    CommandSpec("template", "List or apply a harness workflow template", "harness"),
    CommandSpec("templates", "Alias for /template list", "harness"),
    CommandSpec("lessons", "Show steering lessons from past corrections", "harness"),
    CommandSpec("progress", "Show progress milestones", "harness"),
    CommandSpec("trust", "Trust all companion tools this session", "tools"),
)


def _format_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def usage_report_lines(report: dict[str, Any]) -> list[str]:
    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    budget = report.get("budget", {}) if isinstance(report, dict) else {}
    lines = [
        "Usage",
        f"LLM calls: {summary.get('llm_calls', 0)}",
        f"Prompt tokens: {summary.get('prompt_tokens', 0)}",
        f"Completion tokens: {summary.get('completion_tokens', 0)}",
        f"Total tokens: {summary.get('total_tokens', 0)}",
        f"Node-hours: {budget.get('node_hours_estimated', 0.0):.2f}",
    ]
    nearest_expiry_seconds = budget.get("nearest_expiry_seconds")
    if isinstance(nearest_expiry_seconds, int):
        lines.append(f"Nearest endpoint expiry: {_format_duration(nearest_expiry_seconds)}")
    return lines


def render_usage_report(renderer, report: dict[str, Any]) -> None:
    lines = usage_report_lines(report)
    if not lines:
        return
    renderer.system(lines[0])
    for line in lines[1:]:
        renderer.info(line)


def command_specs() -> list[CommandSpec]:
    specs = list(COMPANION_COMMANDS)
    for command in get_slash_registry().command_list():
        if any(spec.name == command.name for spec in specs):
            continue
        specs.append(CommandSpec(command.name, command.help, command.category))
    return specs


def command_names() -> list[str]:
    names: set[str] = set()
    for spec in command_specs():
        names.add(spec.name)
    for command in get_slash_registry().command_list():
        names.update(command.aliases)
    return sorted(names)


def combined_help_text() -> str:
    grouped: dict[str, list[CommandSpec]] = {}
    for spec in command_specs():
        grouped.setdefault(spec.category, []).append(spec)

    lines = [
        "Commands:",
        "  Ctrl+X        Save and exit",
        "  /help         Show this help",
        "",
    ]
    for category, specs in grouped.items():
        lines.append(f"  {category}:")
        for spec in specs:
            lines.append(f"    /{spec.name:<12} {spec.help}")
    return "\n".join(lines)


def render_combined_help(emit: Callable[[str], None]) -> None:
    emit(combined_help_text())


class RichBridgeRenderer:
    def __init__(self, console: Console) -> None:
        self.console = console

    def info(self, text: str) -> None:
        self.console.print(Text(str(text), style="dim"))

    def system(self, text: str) -> None:
        self.console.print(Text(str(text), style="green"))

    def error(self, text: str, *, correlation_id: str | None = None) -> None:
        suffix = f"  (cid={correlation_id})" if correlation_id else ""
        self.console.print(Text(f"error: {text}{suffix}", style="red"))

    def clear(self) -> None:
        self.console.clear()


class TextualBridgeRenderer:
    def __init__(
        self,
        show_system: Callable[[str], None],
        clear_view: Callable[[], None] | None = None,
    ) -> None:
        self._show_system = show_system
        self._clear_view = clear_view

    def info(self, text: str) -> None:
        self._show_system(str(text))

    def system(self, text: str) -> None:
        self._show_system(str(text))

    def error(self, text: str, *, correlation_id: str | None = None) -> None:
        suffix = f"  (cid={correlation_id})" if correlation_id else ""
        self._show_system(f"Error: {text}{suffix}")

    def clear(self) -> None:
        if self._clear_view:
            self._clear_view()
        self._show_system("Session cleared")


class CompanionSessionAdapter:
    def __init__(self, session, on_drop: Callable[[list[str]], None] | None = None) -> None:
        self._session = session
        self.secure = False
        self._on_drop = on_drop

    @property
    def model(self) -> str | None:
        model = getattr(self._session.agent, "model", None)
        return model if isinstance(model, str) and model.strip() else None

    @model.setter
    def model(self, value: str) -> None:
        self._session.agent.model = value

    @property
    def system_prompt(self) -> str | None:
        value = self._session.metadata.get("system_prompt_override")
        return str(value) if value else None

    def set_system(self, prompt: str) -> None:
        self._session.metadata["system_prompt_override"] = prompt

    @property
    def messages(self) -> list[SessionMessage]:
        return self._session.messages

    @messages.setter
    def messages(self, values: list[Any]) -> None:
        self._session.messages = [_coerce_message(value) for value in values]

    @property
    def turns(self) -> list[Any]:
        turns = []
        pending_user: str | None = None
        for message in self._session.messages:
            if message.role == "user":
                pending_user = message.content
            elif message.role == "assistant" and pending_user is not None:
                turns.append(SimpleNamespace(user=pending_user, assistant=message.content))
                pending_user = None
        return turns

    @property
    def total_prompt_tokens(self) -> int:
        return int(self._session.usage_summary.prompt_tokens)

    @property
    def total_completion_tokens(self) -> int:
        return int(self._session.usage_summary.completion_tokens)

    def clear(self) -> None:
        self._session.messages.clear()
        # Re-enable tool calling — ``_tool_calling_disabled`` is otherwise
        # sticky for the whole session and survives /clear and /new.
        agent = getattr(self._session, "agent", None)
        reset = getattr(agent, "reset_tool_state", None)
        if callable(reset):
            reset()

    def to_markdown(self) -> str:
        return self._session.export_markdown()

    def usage_report(self) -> dict[str, Any]:
        report = getattr(self._session, "usage_report", None)
        if callable(report):
            return report()
        return {}

    def drop_last_turn(self):
        """Cut the last complete turn (/retry) and report its turn ids.

        The cut starts at that turn's user message, so its tool rows go
        with it (deleting the last two user/assistant-sized slots left the
        user message behind when the turn used tools). The dropped turn ids
        reach the TUI so it removes the same transcript rows
        (TUI audit r2 #17 / followup).
        """
        turns = self.turns
        if not turns:
            return None
        removed = turns[-1]
        messages = self._session.messages
        start = _last_turn_start(messages)
        dropped = [str(m.metadata["turn_id"]) for m in messages[start:] if _turn_id(m)]
        del messages[start:]
        if self._on_drop is not None:
            self._on_drop(list(dict.fromkeys(dropped)))
        return removed


def _turn_id(message: Any) -> Any:
    return (getattr(message, "metadata", None) or {}).get("turn_id")


def _last_turn_start(messages: list[Any]) -> int | None:
    """Index of the user message that opens the last complete turn
    (the same pairing as ``CompanionSessionAdapter.turns``)."""
    start = pending = None
    for index, message in enumerate(messages):
        if message.role == "user":
            pending = index
        elif message.role == "assistant" and pending is not None:
            start, pending = pending, None
    return start


def _coerce_message(value: Any) -> SessionMessage:
    if isinstance(value, SessionMessage):
        return value
    if isinstance(value, dict):
        return SessionMessage(
            role=str(value.get("role", "system")),
            content=str(value.get("content", "")),
            metadata=value.get("metadata", {}) or {},
        )
    return SessionMessage(
        role=str(getattr(value, "role", "system")),
        content=str(getattr(value, "content", "")),
    )


class CompanionCommandApp:
    def __init__(self, *, session, store, renderer) -> None:
        self._companion_session = session
        self.client = session.client
        self.renderer = renderer
        self.commands = get_slash_registry()
        self.session = CompanionSessionAdapter(session, on_drop=self._note_dropped_turns)
        self.skill_registry = session.skills
        self.context_manager = session.context
        self.session_store = store
        self.local_tools: list[dict[str, Any]] = []
        self.mode = Mode(session_mode(session))
        self.mcp_manager = _make_mcp_manager(self.client)
        self._exit_requested = False

    def request_exit(self) -> None:
        self._exit_requested = True

    def refresh_context_budget(self) -> None:
        """Re-sync the window and budget /context shows (the model may have
        loaded, or the server changed, since the last turn)."""
        from r4.companion.context_budget import sync_session_context_budget

        try:
            sync_session_context_budget(self._companion_session)
        except Exception:  # noqa: BLE001 - /context still shows the last values
            return

    def show_banner(self) -> None:
        self.renderer.system("New companion conversation started")

    def set_mode(self, mode: Mode) -> None:
        """Switch the session's one mode (ADR-104 D-1); the permission mode
        follows it. Leaving plan mode drops a pending plan."""
        had_plan = bool(self._companion_session.metadata.get("plan_mode_state"))
        previous = session_mode(self._companion_session)
        self.mode = Mode(apply_mode(self._companion_session, mode.value))
        if previous == Mode.PLAN.value and mode != Mode.PLAN and had_plan:
            self.renderer.system("pending plan cleared (left plan mode)")
        self.renderer.system(f"mode → {mode.value} ({mode.description})")

    def trust_tools(self) -> None:
        """``/trust``: autopilot for this session (ADR-104 D-1)."""
        self.set_mode(Mode.AUTOPILOT)

    def send_user_message(self, text: str) -> None:
        """Hand ``text`` back to the UI as a chat turn (TUI audit r2, 2026-10-17).

        A renderer that collects follow-ups (the JSONL bridge) gets the prompt
        queued: the TUI sends it as a normal streamed turn, instead of the
        slash command running a whole agent turn under the command's 15 s
        HTTP cap with no streaming. Other renderers keep the inline turn.
        """
        queue = getattr(self.renderer, "follow_up_messages", None)
        if isinstance(queue, list):
            queue.append(text)
            return
        response = self._companion_session.send(text)
        self.renderer.system(response)

    def _note_dropped_turns(self, turn_ids: list[str]) -> None:
        """Hand the turn ids a command cut to a renderer that collects them."""
        dropped = getattr(self.renderer, "dropped_turn_ids", None)
        if isinstance(dropped, list):
            dropped.extend(turn_ids)


def _make_mcp_manager(client):
    try:
        from r4.tui.mcp_manager import MCPConnectionManager

        manager = MCPConnectionManager()
        manager.load_configs()
        # Skip discover_gateway here — it blocks on network I/O and would
        # freeze the Textual UI. Discovery happens lazily on /mcp connect.
        return manager
    except Exception:
        return _NullMCPManager()


class _NullMCPManager:
    def list_connections(self) -> list[Any]:
        return []

    def connect(self, _target: str) -> bool:
        return False

    def connect_all(self) -> dict[str, bool]:
        return {}

    def summary(self) -> str:
        return "unavailable"


def dispatch_shared_command(command: str, *, session, store, renderer) -> bool:
    app = CompanionCommandApp(session=session, store=store, renderer=renderer)
    with _temporary_command_timeout(app.client, command=command):
        return app.commands.dispatch(app, command)


def _command_timeout_seconds(command: str = "") -> float:
    root = command.strip().split(maxsplit=1)[0].lower() if command.strip() else ""
    if root == "/compact":
        raw = os.environ.get("R4_COMPANION_COMPACT_TIMEOUT_SEC")
        if raw:
            try:
                return max(1.0, float(raw))
            except ValueError:
                pass
        return 120.0

    raw = os.environ.get("R4_COMPANION_COMMAND_TIMEOUT_SEC") or os.environ.get(
        "R4_TUI_COMMAND_TIMEOUT_SEC"
    )
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = 15.0
    else:
        value = 15.0
    return max(1.0, value)


@contextmanager
def _temporary_command_timeout(client, *, command: str = ""):
    config = getattr(client, "config", None)
    if config is None:
        yield
        return

    sentinel = object()
    original = getattr(config, "timeout", sentinel)
    timeout = _command_timeout_seconds(command)
    if isinstance(original, (int, float)):
        timeout = min(float(original), timeout)
    setattr(config, "timeout", timeout)
    try:
        yield
    finally:
        if original is sentinel:
            try:
                delattr(config, "timeout")
            except AttributeError:
                pass
        else:
            setattr(config, "timeout", original)


def _completion_yield(name: str, meta: str):
    from prompt_toolkit.completion import Completion

    return Completion(
        name,
        start_position=0,
        display=name,
        display_meta=meta,
    )


def _complete_all_commands():
    for spec in command_specs():
        yield _completion_yield(f"/{spec.name}", spec.help)


def _complete_subcommands(command: str, remainder: str):
    partial = remainder.strip().lower()
    for subcommand in SUBCOMMANDS.get(command, ()):
        if subcommand.name.startswith(partial):
            yield _completion_yield(subcommand.name, subcommand.help)


def _complete_top_commands(command_lower: str):
    for spec in command_specs():
        if spec.name.startswith(command_lower):
            yield _completion_yield(f"/{spec.name}", spec.help)
    # Aliases live on the registry rows (SlashCommand.aliases) — the old
    # `registry.command_aliases(name)` resolver never existed, so alias
    # completion crashed on ANY top-level prefix in the live TUI.
    for command in get_slash_registry().command_list():
        for alias in command.aliases:
            if alias.startswith(command_lower):
                yield _completion_yield(f"/{alias}", command.help)


def _make_command_completer():
    """Build the slash-command completer (module-level: nothing closes over
    make_prompt_session's scope — lifted out for CCN, exemption-age review
    2026-09-13)."""
    from prompt_toolkit.completion import Completer

    class _CommandCompleter(Completer):
        def get_completions(self, document, complete_event):  # type: ignore[override]
            # Only complete when the cursor is at or after a leading '/'
            before_cursor = document.text_before_cursor
            if not before_cursor:
                return

            # Find the start of the slash command word (first '/' or start of buffer)
            slash_idx = before_cursor.rfind("/")
            if slash_idx == -1:
                return

            # Text from the slash to the cursor (inclusive)
            text = before_cursor[slash_idx:]  # e.g. "/h", "/help "
            body = text[1:]  # text after the slash

            # If body is empty (user just typed "/"), complete all commands
            if not body:
                yield from _complete_all_commands()
                return

            body_stripped = body.strip()
            # Check for subcommand (body has a space)
            first_word, space, remainder = body_stripped.partition(" ")

            if space:  # found space separator — complete subcommands
                if first_word.lower() in SUBCOMMANDS:
                    yield from _complete_subcommands(first_word.lower(), remainder)
                return

            # Complete top-level commands by name or aliases
            yield from _complete_top_commands(body_stripped.lower())

    return _CommandCompleter()


def make_prompt_session():
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
    except ImportError:
        return None

    history_path = _history_path()
    return PromptSession(
        history=FileHistory(str(history_path)),
        completer=_make_command_completer(),
    )


def _history_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    path = Path(state) / "r4"
    path.mkdir(parents=True, exist_ok=True)
    return path / "companion_history"
