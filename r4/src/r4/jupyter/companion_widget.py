"""Interactive Jupyter widget for the Research Companion.

Provides ``CompanionWidget`` — a self-contained chat interface rendered
via ipywidgets that maintains a full :class:`CompanionSession` under
the hood, complete with skills, harness, and memory.

Usage::

    from r4.jupyter.companion_widget import CompanionWidget

    w = CompanionWidget()
    w  # display in notebook cell
"""

from __future__ import annotations

import logging

from r4.ui_brand import brand_html

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy widget imports
# ---------------------------------------------------------------------------

_HAS_WIDGETS = False
try:
    import ipywidgets as widgets
    from IPython.display import Markdown, clear_output, display

    _HAS_WIDGETS = True
except ImportError:
    widgets = None  # type: ignore[assignment]

_HAS_R4 = False
try:
    from r4.client import Client

    _HAS_R4 = True
except ImportError:
    Client = None  # type: ignore[assignment,misc]


def _require():
    if not _HAS_WIDGETS:
        raise ImportError("ipywidgets is required. Install with: pip install ipywidgets")
    if not _HAS_R4:
        raise ImportError("r4 is required. Install with: pip install r4[companion]")


class CompanionWidget:
    """Interactive companion chat widget for Jupyter notebooks.

    Parameters
    ----------
    client : Client | None
        Existing r4 client.  Created automatically if *None*.
    model : str | None
        Override inference model.
    skill : str | None
        Pre-activate a research skill.
    session_id : str | None
        Resume an existing session by ID (prefix match supported).
    """

    def __init__(
        self,
        *,
        client: Client | None = None,
        model: str | None = None,
        skill: str | None = None,
        session_id: str | None = None,
    ):
        _require()

        self._client = client or Client()
        self._model = model
        self._initial_skill = skill

        from r4.companion.permissions import PermissionMode
        from r4.companion.session import CompanionSessionStore

        self._store = CompanionSessionStore()

        # Load or create session
        if session_id:
            sessions = self._store.list_sessions(limit=200)
            match = next((s for s in sessions if s["id"].startswith(session_id)), None)
            self._session = self._store.load(match["id"], self._client) if match else None
            if self._session is None:
                raise ValueError(f"Session {session_id} not found")
        else:
            self._session = self._store.create_session(
                self._client,
                name="Jupyter widget",
                model=model,
            )

        # Notebooks can't do interactive tool approval
        self._session.permissions.set_mode(PermissionMode.AUTO_SAFE)

        if self._initial_skill:
            self._session.skills.activate(self._initial_skill)

        self._build_ui()

    # -- UI -----------------------------------------------------------------

    def _build_ui(self):
        self._brand = widgets.HTML(value=brand_html("companion"))

        # Chat history
        self._history = widgets.Output(
            layout=widgets.Layout(
                height="420px",
                overflow_y="auto",
                border="1px solid #ddd",
                padding="8px",
            )
        )

        # Input
        self._input = widgets.Textarea(
            placeholder="Ask R4 Companion about your research…",
            layout=widgets.Layout(width="100%", height="72px"),
        )
        self._input.observe(self._on_key, names="value")

        # Buttons
        self._send_btn = widgets.Button(
            description="Send",
            button_style="primary",
            layout=widgets.Layout(width="80px"),
        )
        self._send_btn.on_click(lambda _: self._on_send())

        self._clear_btn = widgets.Button(
            description="Clear",
            button_style="warning",
            layout=widgets.Layout(width="80px"),
        )
        self._clear_btn.on_click(lambda _: self._on_clear())

        # Skill selector
        skill_names = [s.name for s in self._session.skills.list_all()]
        self._skill_dd = widgets.Dropdown(
            options=["(none)"] + skill_names,
            value="(none)",
            description="Skill:",
            layout=widgets.Layout(width="200px"),
        )
        self._skill_dd.observe(self._on_skill_change, names="value")

        # Status
        self._status = widgets.Label(value=f"Session: {self._session.session_id[:8]}")

        controls = widgets.HBox(
            [
                self._send_btn,
                self._clear_btn,
                self._skill_dd,
                self._status,
            ]
        )

        self._container = widgets.VBox([self._brand, self._history, self._input, controls])

        # Render existing messages if resumed
        for m in self._session.messages:
            self._render(m.role, m.content)

    # -- Event handlers -----------------------------------------------------

    def _on_key(self, change):
        """Submit on Shift+Enter (newline check heuristic)."""
        # ipywidgets doesn't expose key events; we rely on the Send button.
        pass

    def _on_send(self):
        text = self._input.value.strip()
        if not text:
            return

        self._input.value = ""
        self._render("user", text)

        self._input.disabled = True
        self._send_btn.disabled = True
        self._status.value = "Thinking…"

        try:
            response = self._session.send(text)
            self._render("assistant", response)
            self._store.save(self._session)
            self._status.value = f"Session: {self._session.session_id[:8]}"
        except Exception as exc:
            self._render("error", str(exc))
            self._status.value = f"Error: {exc}"
            logger.exception("CompanionWidget error")
        finally:
            self._input.disabled = False
            self._send_btn.disabled = False

    def _on_clear(self):
        """Clear displayed history (session state retained)."""
        with self._history:
            clear_output()
        self._status.value = "Display cleared"

    def _on_skill_change(self, change):
        name = change["new"]
        if name == "(none)":
            return
        self._session.skills.activate(name)
        self._status.value = f"Skill: {name}"

    # -- Rendering ----------------------------------------------------------

    def _render(self, role: str, content: str):
        with self._history:
            if role == "user":
                display(Markdown(f"**You:** {content}"))
            elif role == "assistant":
                display(Markdown(f"---\n{content}"))
            elif role == "error":
                display(Markdown(f"**Error:** {content}"))

    # -- Public API ---------------------------------------------------------

    def send(self, text: str) -> str:
        """Programmatically send a message and return the response.

        Also updates the widget display.
        """
        self._render("user", text)
        response = self._session.send(text)
        self._render("assistant", response)
        self._store.save(self._session)
        return response

    @property
    def session(self):
        """The underlying :class:`CompanionSession`."""
        return self._session

    @property
    def messages(self) -> list[dict[str, str]]:
        """Conversation messages as dicts."""
        return [{"role": m.role, "content": m.content} for m in self._session.messages]

    def export_markdown(self) -> str:
        """Export the session as markdown."""
        return self._session.export_markdown()

    # -- IPython display hooks ----------------------------------------------

    def display(self):
        """Display the widget."""
        display(self._container)

    def _ipython_display_(self, **kwargs):
        self.display()
