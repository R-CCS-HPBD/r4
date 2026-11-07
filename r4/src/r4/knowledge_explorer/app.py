"""U4: Textual three-pane knowledge explorer.

Panes: section tree → documents (verification/coverage badges) → detail
(lineage/evidence chain). Keyboard: arrows/Tab to cycle panes, ``e`` to
export the project's .kb bundle, ``r`` refresh, ``q`` quit.

Data access goes through injected provider callables — the CLI wires
them to ``Client().knowledge``; tests inject fakes.

Implementation note: ``ListView.clear()`` is awaitable in Textual —
loaders are async workers so each clear is awaited before appending,
which prevents DuplicateIds across re-renders.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, ListItem, ListView, Static

from .data import SECTIONS, fetch_sections

logger = logging.getLogger(__name__)


def _row_list_item(row: dict[str, Any]) -> ListItem:
    """Convert one row dict into a ListView item with an id payload."""
    return ListItem(
        Static(f"{row.get('title', '')}\n[dim]{row.get('label', '')}[/dim]"),
        id=str(row.get("id") or "row"),
    )


class ExploreApp(App[None]):
    """Three-pane explorer over a project's knowledge surface."""

    CSS = """
    #sections { width: 26; border: solid $primary; }
    #docs { width: 1fr; border: solid $surface; }
    #detail { width: 46; border: solid $primary; height: 100%; overflow-y: auto; }
    ListItem { padding: 0 1; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("e", "export", "Export bundle"),
        Binding("g", "toggle_graph", "Graph +/- (include/exclude)"),
        Binding("d", "toggle_discovery", "Discovery on/off"),
    ]

    def __init__(
        self,
        *,
        project_id: str,
        project_data_provider: Callable[[], dict[str, Any]],
        documents_provider: Callable[[str | None], list[dict[str, Any]]],
        detail_provider: Callable[[str | None, str], dict[str, Any]],
        export_provider: Callable[[], Any] | None = None,
        toggle_graph_provider: Callable[[str], dict[str, Any]] | None = None,
        toggle_discovery_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self._project_id = project_id
        self._project_data_provider = project_data_provider
        self._documents_provider = documents_provider
        self._detail_provider = detail_provider
        self._export_provider = export_provider
        self._toggle_graph_provider = toggle_graph_provider
        self._toggle_discovery_provider = toggle_discovery_provider
        self._active_section: str = SECTIONS[0]
        self._data: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(id="sections")
            with Vertical():
                yield ListView(id="docs")
            yield Static("Select a section…", id="detail", markup=True)
        yield Footer()

    # ------------------------------------------------------------------
    # Loading helpers
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self.title = f"r4 knowledge explore — {self._project_id}"
        self.action_refresh()

    def reload_project(self) -> None:
        """Pull fresh data and rebuild all three panes."""
        self._data = self._project_data_provider()
        self.run_worker(self._refresh_panes(self._active_section), exclusive=True, group="refresh")

    # ------------------------------------------------------------------
    # Multi-graph selection actions (graphs section)
    # ------------------------------------------------------------------

    def action_toggle_graph(self) -> None:
        """Cycle the highlighted graph through include/exclude/neutral."""
        if self._toggle_graph_provider is None or self._active_section != "graphs":
            return
        graph_id = self._highlighted_doc_id()
        if not graph_id or graph_id == "discovery-toggle":
            if graph_id == "discovery-toggle":
                self.action_toggle_discovery()
            return
        self._toggle_graph_provider(graph_id)
        self.run_worker(self._load_documents("graphs"), exclusive=True, group="refresh")

    def action_toggle_discovery(self) -> None:
        """Flip agent auto-discovery on/off."""
        if self._toggle_discovery_provider is None:
            return
        self._toggle_discovery_provider()
        if self._active_section == "graphs":
            self.run_worker(self._load_documents("graphs"), exclusive=True, group="refresh")

    def _highlighted_doc_id(self) -> str | None:
        """Id payload of the highlighted row in the documents pane."""
        docs = self.query_one("#docs", ListView)
        item = docs.highlighted_child
        if item is None:
            return None
        return item.id

    async def _refresh_panes(self, section: str) -> None:
        """Full rebuild of sections + documents (workers entry point)."""
        await self._render_sections()
        await self._load_documents(section)

    async def _render_sections(self) -> None:
        rows = fetch_sections(self._data)
        view = self.query_one("#sections", ListView)
        await view.clear()
        for row in rows:
            view.append(_row_list_item(row))
        if rows:
            view.index = 0

    async def _load_documents(self, section: str | None) -> None:
        self._active_section = section or self._active_section
        try:
            rows = self._documents_provider(self._active_section)
        except Exception as exc:  # noqa: BLE001
            logger.exception("document load failed")
            self.notify(f"Load failed: {exc}", severity="error")
            return
        docs = self.query_one("#docs", ListView)
        await docs.clear()
        for row in rows:
            docs.append(_row_list_item(row))
        if rows:
            docs.index = 0

    def _load_detail(self, doc_id: str) -> None:
        detail = self.query_one("#detail", Static)
        if not doc_id:
            detail.update("[dim]No selection[/dim]")
            return
        try:
            payload = self._detail_provider(self._active_section, doc_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("detail load failed")
            detail.update(f"[red]detail unavailable: {exc}[/red]")
            return
        body = json.dumps(payload.get("body", {}), indent=2, default=str)
        detail.update(f"[b]{payload.get('title', doc_id)}[/b]\n\n{body}")

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item_id = event.item.id if event.item else None
        if event.list_view.id == "sections" and item_id:
            self._active_section = item_id
            self.run_worker(self._load_documents(item_id), exclusive=True, group="docs")
        elif event.list_view.id == "docs" and item_id:
            self._load_detail(item_id)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_refresh(self) -> None:
        try:
            self.reload_project()
        except Exception as exc:  # noqa: BLE001
            logger.exception("refresh failed")
            self.notify(f"Refresh failed: {exc}", severity="error")
            return
        self.notify("Refreshed", timeout=2)

    def action_export(self) -> None:
        if not self._export_provider:
            self.notify("Export unavailable", severity="warning")
            return
        try:
            result = self._export_provider()
            self.notify(f"Exported: {result}", timeout=8)
        except Exception as exc:
            logger.exception("Bundle export failed")
            self.notify(f"Export failed: {exc}", severity="error")
