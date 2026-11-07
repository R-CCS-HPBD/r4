"""Rich REPL fallback for the research companion.

A minimal, always-available terminal interface used when the JS TUI cannot
run (e.g. missing Node.js, SSH login node, or ``force_repl=True``).
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from rich.markdown import Markdown
from rich.panel import Panel

from r4.ui_theme import make_console, rotating_status

if TYPE_CHECKING:
    from ..agent import CompanionEvent
    from ..session import CompanionSession

logger = logging.getLogger(__name__)


class RichREPL:
    """Simple Rich-based REPL that wraps a :class:`~CompanionSession`."""

    def __init__(self, session: "CompanionSession") -> None:
        self.session = session
        self.console = make_console()

    def _on_event(self, event: "CompanionEvent") -> None:
        """Print live events as they happen (tool calls, blocked, errors)."""
        from r4.companion.agent import CompanionEventType

        if event.event_type == CompanionEventType.TOOL_CALL:
            self.console.print(f"[r4.tool dim]🔧 {event.message}[/]")
        elif event.event_type == CompanionEventType.TOOL_RESULT:
            msg = event.message[:200]
            self.console.print(f"[r4.success dim]✓ {msg}[/]")
        elif event.event_type == CompanionEventType.TOOL_BLOCKED:
            self.console.print(f"[r4.warning dim]⛔ {event.message}[/]")
        elif event.event_type == CompanionEventType.ERROR:
            self.console.print(f"[r4.error dim]✗ {event.message}[/]")
        elif event.event_type == CompanionEventType.TOOLS_DISABLED:
            self.console.print(f"[r4.warning dim]⚠ {event.message}[/]")

    def run(self) -> None:
        """Run the input loop until the user exits."""

        self.session.agent.on_event(self._on_event)

        self.console.print(
            Panel.fit(
                "[r4.title](\u25d5\u1d57\u25d5) R4 Research Companion (REPL)[/]\n"
                "[r4.subtitle]Type a message, or [bold]/quit[/] to exit.[/]",
                title="Welcome",
                border_style="r4.accent",
            )
        )

        while True:
            try:
                user_input = self.console.input("[bold green]>> [/]").strip()
                if not user_input:
                    continue
                if user_input.lower() in ("/quit", "/exit", "/q"):
                    break

                with rotating_status(self.console):
                    response = self.session.send(user_input)

                self.console.print()
                self.console.print(Markdown(response))
                self.console.print()

            except KeyboardInterrupt:
                self.console.print("\n[dim]Interrupted. Type /quit to exit.[/]")
            except Exception as exc:  # pragma: no cover
                logger.exception("REPL turn failed")
                self.console.print(f"[red]Error: {exc}[/]")

        self._save_session()

    def _save_session(self) -> None:
        """Persist the session before exiting."""
        try:
            from ..session_store import CompanionSessionStore

            store = CompanionSessionStore()
            store.save(self.session)
            self.console.print("[dim]Session saved.[/]")
        except Exception as exc:  # pragma: no cover
            logger.debug("Session save failed: %s", exc)


def open_cli_session(
    client: Any,
    *,
    name: str,
    session_id: str | None = None,
    model: str | None = None,
    skill: str | None = None,
    system: str | None = None,
) -> "CompanionSession":
    """The session a terminal entry (REPL, ``r4 -p``) runs, set up as the
    console bridge sets up its own (companion sweep 2026-10-21).

    ``session_id`` RESUMES the stored session: a fresh session under that id
    was saved over the stored conversation at exit. With no ``model`` the
    session takes the one the console would pick (an active public source's
    default model, a ready endpoint); it said "No model selected" while
    ``r4 chat`` answered. An unknown skill is an error; the system prompt
    rides the session metadata.
    """
    session = _load_or_create(client, name, session_id)
    if model or not getattr(session.agent, "model", None):
        from ..model_resolution import resolve_initial_model

        resolved = resolve_initial_model(client, explicit=model, saved=None)
        if resolved:
            session.agent.model = resolved
    if skill and not session.skills.activate(skill):
        raise ValueError(f"unknown skill: {skill}")
    if system:
        session.metadata["system_prompt_override"] = system
    return session


def _load_or_create(client: Any, name: str, session_id: str | None) -> "CompanionSession":
    from ..session import CompanionSession
    from ..session_store import CompanionSessionStore

    if session_id:
        session = CompanionSessionStore().load(str(session_id), client)
        if session is None:
            raise ValueError(f"session not found: {session_id}")
        return session
    return CompanionSession(session_id=str(uuid.uuid4()), name=name, client=client)


def launch_repl(
    client,
    *,
    session_id: str | None = None,
    model: str | None = None,
    skill: str | None = None,
    system: str | None = None,
    web_flag: Any = None,
) -> None:
    """Launch the companion in REPL mode.

    This is the entry-point used by ``ui/__init__.py`` when the JS TUI is
    unavailable or ``force_repl=True``. ``web_flag`` is ``r4 --web``'s
    choice; without it the session's own choice stands, else the user is
    asked (ADR-099 D-2).
    """
    from ..._cli.sandbox_prompt import apply_local_sandbox
    from ..._cli.web_access_prompt import apply_repl_web_access

    logging.basicConfig(level=logging.WARNING)
    session = open_cli_session(
        client, name="REPL Session", session_id=session_id, model=model, skill=skill, system=system
    )
    # ADR-101: settled before the web question, whose local note depends on it.
    apply_local_sandbox(session)
    apply_repl_web_access(session, getattr(client, "config", None), flag=web_flag)
    repl = RichREPL(session)
    repl.run()
