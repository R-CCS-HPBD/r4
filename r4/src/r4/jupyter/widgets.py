"""
Jupyter Widgets for R4 Platform (A-502).

Provides interactive widgets:
- ChatWidget: Interactive chat interface
- RAGWidget: RAG query and results display

These widgets use ipywidgets for a rich interactive experience.
"""

from __future__ import annotations

from typing import Optional

from r4.ui_brand import brand_html

# Try to import ipywidgets
try:
    import ipywidgets as widgets
    from IPython.display import HTML, Markdown, clear_output, display

    HAS_WIDGETS = True
except ImportError:
    HAS_WIDGETS = False
    widgets = None

# Try to import the R4 client
try:
    from r4.client import R4Client

    HAS_R4 = True
except ImportError:
    HAS_R4 = False
    R4Client = None


def _require_widgets():
    """Raise error if widgets not available."""
    if not HAS_WIDGETS:
        raise ImportError("ipywidgets not installed. Install with: pip install ipywidgets")


def _require_r4():
    """Raise error if r4 not available."""
    if not HAS_R4:
        raise ImportError("r4 package not installed. Install with: pip install r4")


class ChatWidget:
    """
    Interactive chat widget for Jupyter notebooks.

    Provides a chat interface with:
    - Message input
    - Chat history display
    - Streaming responses
    - Model selection
    """

    def __init__(
        self,
        client: Optional[R4Client] = None,
        model: str = "rivault-gpt-oss-static",
        system_prompt: Optional[str] = None,
        gateway_url: str = "http://localhost:8000",
    ):
        """
        Initialize chat widget.

        Args:
            client: Existing R4Client (optional)
            model: Default model to use
            system_prompt: System prompt for conversations
            gateway_url: Gateway URL (if client not provided)
        """
        _require_widgets()
        _require_r4()

        self.model = model
        self.system_prompt = system_prompt
        self.gateway_url = gateway_url

        # Client
        self._client = client or R4Client(gateway_url=gateway_url)

        # Conversation history
        self._messages: list[dict[str, str]] = []
        if system_prompt:
            self._messages.append({"role": "system", "content": system_prompt})

        # Build UI
        self._build_ui()

    def _build_ui(self) -> None:
        """Build the widget UI."""
        self._brand = widgets.HTML(value=brand_html("chat"))

        # Chat history display
        self._history_output = widgets.Output(
            layout=widgets.Layout(
                height="400px",
                overflow_y="auto",
                border="1px solid #ccc",
                padding="10px",
            )
        )

        # Input text area
        self._input = widgets.Textarea(
            placeholder="Ask R4 Chat a question...",
            layout=widgets.Layout(width="100%", height="80px"),
        )

        # Send button
        self._send_button = widgets.Button(
            description="Send",
            button_style="primary",
            layout=widgets.Layout(width="100px"),
        )
        self._send_button.on_click(self._on_send)

        # Clear button
        self._clear_button = widgets.Button(
            description="Clear",
            button_style="warning",
            layout=widgets.Layout(width="100px"),
        )
        self._clear_button.on_click(self._on_clear)

        # Model selector
        self._model_dropdown = widgets.Dropdown(
            options=[
                "rivault-gpt-oss-static",
                "gpt-3.5-turbo",
                "gpt-4",
            ],
            value=self.model,
            description="Model:",
            layout=widgets.Layout(width="250px"),
        )
        self._model_dropdown.observe(self._on_model_change, names="value")

        # Status label
        self._status = widgets.Label(value="Ready")

        # Layout
        controls = widgets.HBox(
            [
                self._send_button,
                self._clear_button,
                self._model_dropdown,
                self._status,
            ]
        )

        self._container = widgets.VBox(
            [
                self._brand,
                self._history_output,
                self._input,
                controls,
            ]
        )

    def _on_send(self, _) -> None:
        """Handle send button click."""
        message = self._input.value.strip()
        if not message:
            return

        # Clear input
        self._input.value = ""

        # Add user message
        self._messages.append({"role": "user", "content": message})
        self._render_message("user", message)

        # Disable input during generation
        self._input.disabled = True
        self._send_button.disabled = True
        self._status.value = "Generating..."

        try:
            # Get response
            response = self._client.chat(
                messages=self._messages,
                model=self._model_dropdown.value,
                stream=False,  # Widget doesn't support streaming well
            )

            assistant_message = response.choices[0].message.content
            self._messages.append({"role": "assistant", "content": assistant_message})
            self._render_message("assistant", assistant_message)

            self._status.value = "Ready"

        except Exception as e:
            self._status.value = f"Error: {e}"

        finally:
            self._input.disabled = False
            self._send_button.disabled = False

    def _on_clear(self, _) -> None:
        """Handle clear button click."""
        self._messages = []
        if self.system_prompt:
            self._messages.append({"role": "system", "content": self.system_prompt})

        with self._history_output:
            clear_output()

        self._status.value = "Conversation cleared"

    def _on_model_change(self, change) -> None:
        """Handle model selection change."""
        self.model = change["new"]
        self._status.value = f"Model: {self.model}"

    def _render_message(self, role: str, content: str) -> None:
        """Render a message in the chat history."""
        with self._history_output:
            if role == "user":
                display(
                    HTML(
                        f'<div style="background-color:#e3f2fd;padding:10px;'
                        f'margin:5px 0;border-radius:5px;">'
                        f"<strong>You:</strong><br>{content}</div>"
                    )
                )
            else:
                display(
                    HTML(
                        f'<div style="background-color:#f5f5f5;padding:10px;'
                        f'margin:5px 0;border-radius:5px;">'
                        f"<strong>Assistant:</strong><br>{content}</div>"
                    )
                )

    def display(self) -> None:
        """Display the widget."""
        display(self._container)

    def _ipython_display_(self) -> None:
        """IPython display hook."""
        self.display()


class RAGWidget:
    """
    Interactive RAG query widget for Jupyter notebooks.

    Provides:
    - Query input
    - Collection selection
    - Results display
    - Optional LLM synthesis
    """

    def __init__(
        self,
        client: Optional[R4Client] = None,
        collection: str = "default",
        top_k: int = 5,
        gateway_url: str = "http://localhost:8000",
    ):
        """
        Initialize RAG widget.

        Args:
            client: Existing R4Client (optional)
            collection: Default collection
            top_k: Number of results to return
            gateway_url: Gateway URL (if client not provided)
        """
        _require_widgets()
        _require_r4()

        self.collection = collection
        self.top_k = top_k
        self.gateway_url = gateway_url

        # Client
        self._client = client or R4Client(gateway_url=gateway_url)

        # Build UI
        self._build_ui()

    def _build_ui(self) -> None:
        """Build the widget UI."""
        self._create_input_widgets()
        self._create_control_widgets()
        self._create_layout()

    def _create_input_widgets(self) -> None:
        """Create query and collection input widgets."""
        self._query_input = widgets.Text(
            placeholder="Enter your search query...",
            layout=widgets.Layout(width="100%"),
        )
        self._collection_input = widgets.Text(
            value=self.collection,
            description="Collection:",
            layout=widgets.Layout(width="300px"),
        )
        self._topk_slider = widgets.IntSlider(
            value=self.top_k,
            min=1,
            max=20,
            step=1,
            description="Results:",
            layout=widgets.Layout(width="300px"),
        )

    def _create_control_widgets(self) -> None:
        """Create buttons and output widgets."""
        self._search_button = widgets.Button(
            description="Search",
            button_style="primary",
            layout=widgets.Layout(width="100px"),
        )
        self._search_button.on_click(self._on_search)
        self._synthesize_checkbox = widgets.Checkbox(
            value=False,
            description="Include LLM synthesis",
        )
        self._results_output = widgets.Output(
            layout=widgets.Layout(
                height="400px",
                overflow_y="auto",
                border="1px solid #ccc",
                padding="10px",
            )
        )
        self._status = widgets.Label(value="Ready")

    def _create_layout(self) -> None:
        """Assemble widgets into container layout."""
        controls = widgets.HBox([self._collection_input, self._topk_slider])
        buttons = widgets.HBox(
            [
                self._search_button,
                self._synthesize_checkbox,
                self._status,
            ]
        )
        self._container = widgets.VBox(
            [
                self._query_input,
                controls,
                buttons,
                self._results_output,
            ]
        )

    def _on_search(self, _) -> None:
        """Handle search button click."""
        query = self._query_input.value.strip()
        if not query:
            self._status.value = "Please enter a query"
            return

        self._search_button.disabled = True
        self._status.value = "Searching..."

        with self._results_output:
            clear_output()
            try:
                results = self._execute_search(query)
                self._display_results(results)
                if self._synthesize_checkbox.value and results:
                    self._synthesize_results(query, results)
                self._status.value = "Done"
            except Exception as e:
                display(HTML(f'<div style="color:red;">Error: {e}</div>'))
                self._status.value = "Error"
            finally:
                self._search_button.disabled = False

    def _execute_search(self, query: str) -> list:
        """Execute RAG query and return results."""
        return self._client.rag.query(
            query=query,
            collection=self._collection_input.value,
            top_k=self._topk_slider.value,
        )

    def _display_results(self, results: list) -> None:
        """Display search results as HTML."""
        display(HTML(f"<h4>Found {len(results)} results</h4>"))
        for i, result in enumerate(results, 1):
            score = result.get("score", 0)
            content = result.get("content", "")
            metadata = result.get("metadata", {})
            truncated = content[:500] + ("..." if len(content) > 500 else "")
            display(
                HTML(
                    f'<div style="border:1px solid #ddd;padding:10px;'
                    f'margin:10px 0;border-radius:5px;">'
                    f"<strong>[{i}] Score: {score:.3f}</strong><br>"
                    f"<p>{truncated}</p>"
                    f"<small>Source: {metadata.get('source', 'N/A')}</small>"
                    f"</div>"
                )
            )

    def _synthesize_results(self, query: str, results: list) -> None:
        """Run LLM synthesis on search results."""
        self._status.value = "Synthesizing..."
        display(HTML("<h4>LLM Synthesis</h4>"))
        context = "\n\n".join(r.get("content", "") for r in results[:5])
        synthesis = self._client.chat(
            messages=[
                {"role": "system", "content": "Synthesize the following documents."},
                {"role": "user", "content": f"Query: {query}\n\nDocuments:\n{context}"},
            ],
            stream=False,
        )
        display(Markdown(synthesis.choices[0].message.content))

    def display(self) -> None:
        """Display the widget."""
        display(self._container)

    def _ipython_display_(self) -> None:
        """IPython display hook."""
        self.display()
