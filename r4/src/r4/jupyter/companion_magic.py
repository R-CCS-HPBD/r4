"""IPython magic for the Research Companion.

Provides:
- ``%companion`` / ``%%companion`` — send a message to the companion
- ``%companion_config``           — configure session settings
- ``%companion_sessions``        — list / resume / export sessions
- ``%companion_remember``        — save a memory fact
- ``%companion_skill``           — activate / list skills

Usage::

    %load_ext r4.jupyter.companion_magic

    # Quick question (line magic)
    %companion What GPU partition should I use for a 7B model?

    # Multi-line prompt (cell magic)
    %%companion
    I have earthquake simulation output in data/output.h5.
    Help me visualize the wavefield at timestep 500.

    # Resume a previous session
    %companion_sessions resume abc12345

    # Remember a fact across sessions
    %companion_remember project My project studies seismic wave propagation
"""

from __future__ import annotations

import logging

from IPython.core.magic import Magics, cell_magic, line_magic, magics_class
from IPython.core.magic_arguments import argument, magic_arguments, parse_argstring
from IPython.display import Markdown, display

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — keep load_ext fast
# ---------------------------------------------------------------------------

_COMPANION_AVAILABLE = True
try:
    from r4.client import Client
except ImportError:
    _COMPANION_AVAILABLE = False


def _require_companion():
    if not _COMPANION_AVAILABLE:
        raise ImportError(
            "r4 is required for the companion magic. Install with: pip install r4[companion]"
        )


@magics_class
class CompanionMagics(Magics):
    """IPython magics that wrap a :class:`CompanionSession`."""

    def __init__(self, shell):
        super().__init__(shell)
        self._session = None
        self._store = None
        self._client = None
        self._model: str | None = None

    # -- helpers ------------------------------------------------------------

    def _ensure_session(self):
        """Lazily create a client, store, and session on first use."""
        _require_companion()
        if self._client is None:
            self._client = Client()
        if self._store is None:
            from r4.companion.session import CompanionSessionStore

            self._store = CompanionSessionStore()
        if self._session is None:
            from r4.companion.permissions import PermissionMode

            self._session = self._store.create_session(
                self._client,
                name="Jupyter session",
                model=self._model,
            )
            # Auto-approve safe tools in notebooks; prompt not practical
            self._session.permissions.set_mode(PermissionMode.AUTO_SAFE)

    def _send(self, text: str) -> str:
        """Send a message and return the response."""
        self._ensure_session()
        return self._session.send(text)

    def _save(self):
        """Persist session state."""
        if self._session and self._store:
            self._store.save(self._session)

    # -- %companion / %%companion -------------------------------------------

    @line_magic
    @magic_arguments()
    @argument("query", nargs="*", help="Message text")
    @argument("-m", "--model", help="Override model for this session")
    @argument("-o", "--output", help="Variable name to store raw response text")
    def companion(self, line: str):
        """Line magic — send a single-line message to the companion.

        Examples::

            %companion What is the best partition for GPU jobs on RCCS?
            %companion -o answer Summarize my project README
        """
        args = parse_argstring(self.companion, line)

        if args.model:
            self._model = args.model
            # Reset session so next call picks up the new model
            self._session = None

        query = " ".join(args.query)
        if not query:
            display(Markdown("*Usage:* `%companion <your question>`"))
            return

        response = self._send(query)
        display(Markdown(response))
        self._save()

        if args.output:
            self.shell.user_ns[args.output] = response

    @cell_magic("companion")
    @magic_arguments()
    @argument("-m", "--model", help="Override model for this session")
    @argument("-s", "--skill", help="Activate a skill for this prompt")
    @argument("-o", "--output", help="Variable name to store raw response text")
    def companion_cell(self, line: str, cell: str):
        """Cell magic — send a multi-line prompt to the companion.

        Examples::

            %%companion
            I have an HDF5 file with seismic traces.
            Help me load and plot the first 10 traces using matplotlib.

            %%companion -s analysis -o result
            Analyze the CSV in data/results.csv and find anomalies.
        """
        args = parse_argstring(self.companion_cell, line)

        if args.model:
            self._model = args.model
            self._session = None

        prompt = cell.strip()
        if not prompt:
            display(Markdown("*Usage:* `%%companion\\n<your prompt>`"))
            return

        self._ensure_session()

        if args.skill:
            self._session.skills.activate(args.skill)

        response = self._send(prompt)
        display(Markdown(response))
        self._save()

        if args.output:
            self.shell.user_ns[args.output] = response

    # -- %companion_sessions ------------------------------------------------

    @line_magic
    @magic_arguments()
    @argument("action", nargs="?", default="list", help="list | resume <id> | export <id>")
    @argument("session_id", nargs="?", help="Session ID (for resume/export)")
    def companion_sessions(self, line: str):
        """Manage companion sessions.

        Examples::

            %companion_sessions              # list
            %companion_sessions resume abc1   # resume by prefix
            %companion_sessions export abc1   # show markdown
        """
        args = parse_argstring(self.companion_sessions, line)
        self._ensure_session()

        action = args.action or "list"

        if action == "list":
            sessions = self._store.list_sessions()
            if not sessions:
                display(Markdown("*No saved sessions.*"))
                return
            lines = ["| ID | Name | Updated |", "|---|------|---------|"]
            for s in sessions[:15]:
                lines.append(f"| `{s['id'][:8]}` | {s['name']} | {s['updated_at'][:16]} |")
            display(Markdown("\n".join(lines)))

        elif action == "resume" and args.session_id:
            sid = args.session_id
            # Support prefix matching
            sessions = self._store.list_sessions(limit=200)
            match = next((s for s in sessions if s["id"].startswith(sid)), None)
            if not match:
                display(Markdown(f"*Session `{sid}` not found.*"))
                return
            loaded = self._store.load(match["id"], self._client)
            if loaded:
                self._session = loaded
                display(Markdown(f"Resumed **{loaded.name}** ({len(loaded.messages)} messages)"))
            else:
                display(Markdown(f"*Failed to load session `{sid}`.*"))

        elif action == "export" and args.session_id:
            sid = args.session_id
            sessions = self._store.list_sessions(limit=200)
            match = next((s for s in sessions if s["id"].startswith(sid)), None)
            if not match:
                display(Markdown(f"*Session `{sid}` not found.*"))
                return
            loaded = self._store.load(match["id"], self._client)
            if loaded:
                display(Markdown(loaded.export_markdown()))
            else:
                display(Markdown(f"*Failed to load session `{sid}`.*"))

        else:
            display(Markdown("*Usage:* `%companion_sessions [list|resume <id>|export <id>]`"))

    # -- %companion_remember ------------------------------------------------

    @line_magic
    @magic_arguments()
    @argument("key", help="Memory key")
    @argument("value", nargs="*", help="Memory value")
    def companion_remember(self, line: str):
        """Save a persistent memory fact.

        Examples::

            %companion_remember project Seismic wave propagation study
            %companion_remember partition gpu_a100
        """
        args = parse_argstring(self.companion_remember, line)
        self._ensure_session()

        value = " ".join(args.value) if args.value else args.key
        self._session.context.remember(args.key, value, source="jupyter")
        display(Markdown(f"Remembered **{args.key}**: {value}"))

    # -- %companion_skill ---------------------------------------------------

    @line_magic
    @magic_arguments()
    @argument("name", nargs="?", help="Skill name to activate (omit to list)")
    def companion_skill(self, line: str):
        """Activate a skill or list available skills.

        Examples::

            %companion_skill            # list
            %companion_skill hpc        # activate
        """
        args = parse_argstring(self.companion_skill, line)
        self._ensure_session()

        if args.name:
            self._session.skills.activate(args.name)
            display(Markdown(f"Activated skill **{args.name}**"))
        else:
            skills = self._session.skills.list_all()
            active = self._session.skills._active
            lines = ["| Skill | Description | Active |", "|-------|-------------|--------|"]
            for s in skills:
                mark = "yes" if s.name in active else ""
                lines.append(f"| **{s.name}** | {s.description} | {mark} |")
            display(Markdown("\n".join(lines)))

    # -- %companion_config --------------------------------------------------

    @line_magic
    @magic_arguments()
    @argument("-m", "--model", help="Set model")
    @argument("--trust", action="store_true", help="Trust all tools this session")
    @argument("--info", action="store_true", help="Show current config")
    def companion_config(self, line: str):
        """Configure the companion session.

        Examples::

            %companion_config --info
            %companion_config -m Qwen/Qwen2.5-7B-Instruct
            %companion_config --trust
        """
        args = parse_argstring(self.companion_config, line)
        self._ensure_session()

        if args.model:
            self._model = args.model
            self._session = None  # Reset to pick up new model
            self._ensure_session()
            display(Markdown(f"Model set to **{args.model}**"))

        if args.trust:
            from r4.companion.permissions import PermissionMode

            self._session.permissions.set_mode(PermissionMode.TRUST)
            display(Markdown("All tools **trusted** for this session."))

        if args.info:
            info = {
                "session_id": self._session.session_id[:8],
                "name": self._session.name,
                "messages": len(self._session.messages),
                "active_skills": list(self._session.skills._active),
                "permission_mode": self._session.permissions.mode.value,
                "model": self._model or "(default)",
            }
            lines = [f"- **{k}**: {v}" for k, v in info.items()]
            display(Markdown("\n".join(lines)))


# ---------------------------------------------------------------------------
# Extension entry points
# ---------------------------------------------------------------------------


def load_ipython_extension(ipython):
    """Called by ``%load_ext r4.jupyter.companion_magic``."""
    ipython.register_magics(CompanionMagics)


def unload_ipython_extension(ipython):
    """Called when the extension is unloaded."""
    pass
