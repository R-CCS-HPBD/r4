"""
Jupyter Widgets (A-502, A-503).

Provides interactive widgets for Jupyter:
- ChatWidget - Interactive chat interface
- RAGWidget - RAG query interface with results display
- OutputFormatter - Markdown/HTML rendering

Usage:
    from r4.widgets import ChatWidget, RAGWidget

    # Chat widget
    chat = ChatWidget()
    chat.display()

    # RAG widget
    rag = RAGWidget(collection="my-docs")
    rag.display()
"""

from __future__ import annotations

import asyncio
import html
from typing import Any, Optional

from IPython.display import HTML, Markdown, display

from .client import Client
from .config import get_config
from .ui_brand import brand_html

# =============================================================================
# Output Formatting (A-503)
# =============================================================================


class OutputFormatter:
    """Format LLM and RAG outputs for Jupyter display."""

    @staticmethod
    def markdown(text: str) -> None:
        """Display text as Markdown."""
        display(Markdown(text))

    @staticmethod
    def html(content: str) -> None:
        """Display raw HTML."""
        display(HTML(content))

    @staticmethod
    def code(code: str, language: str = "python") -> None:
        """Display code with syntax highlighting."""
        style = "background-color: #f4f4f4; padding: 10px; border-radius: 5px;"
        html_content = f"""
        <pre style="{style} overflow-x: auto;">
            <code class="language-{language}">{html.escape(code)}</code>
        </pre>
        """
        display(HTML(html_content))

    @staticmethod
    def chat_message(
        role: str,
        content: str,
        name: Optional[str] = None,
    ) -> None:
        """Display a chat message with styling."""
        if role == "user":
            bg_color = "#e3f2fd"
            icon = "👤"
            align = "right"
        elif role == "assistant":
            bg_color = "#f5f5f5"
            icon = "🤖"
            align = "left"
        else:
            bg_color = "#fff3e0"
            icon = "⚙️"
            align = "left"

        label = name or role.capitalize()

        html_content = f"""
        <div style="margin: 10px 0; text-align: {align};">
            <div style="display: inline-block; max-width: 80%; text-align: left;
                        background-color: {bg_color}; padding: 10px 15px;
                        border-radius: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.1);">
                <div style="font-size: 0.8em; color: #666; margin-bottom: 5px;">
                    {icon} {label}
                </div>
                <div style="white-space: pre-wrap;">{html.escape(content)}</div>
            </div>
        </div>
        """
        display(HTML(html_content))

    @staticmethod
    def rag_result(
        index: int,
        content: str,
        score: float,
        source: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """Display a RAG search result."""
        meta_html = ""
        if metadata:
            meta_items = [f"<li><b>{k}:</b> {v}</li>" for k, v in metadata.items()]
            meta_html = f"<ul style='margin: 5px 0; padding-left: 20px;'>{''.join(meta_items)}</ul>"

        html_content = f"""
        <div style="margin: 10px 0; padding: 15px; border: 1px solid #ddd;
                    border-radius: 8px; background-color: #fafafa;">
            <div style="display: flex; justify-content: space-between; margin-bottom: 10px;">
                <span style="font-weight: bold; color: #1976d2;">
                    📄 Result {index}
                </span>
                <span style="color: #666; font-size: 0.9em;">
                    Score: {score:.4f}
                </span>
            </div>
            <div style="font-size: 0.85em; color: #555; margin-bottom: 8px;">
                Source: {html.escape(source)}
            </div>
            <div style="white-space: pre-wrap; line-height: 1.5;">
                {html.escape(content[:500])}{"..." if len(content) > 500 else ""}
            </div>
            {meta_html}
        </div>
        """
        display(HTML(html_content))

    @staticmethod
    def sources_summary(sources: list[str]) -> None:
        """Display a summary of sources."""
        unique_sources = list(set(sources))
        items = "".join(f"<li>{html.escape(s)}</li>" for s in unique_sources)

        html_content = f"""
        <div style="margin: 10px 0; padding: 10px; background-color: #e8f5e9;
                    border-radius: 5px; border-left: 4px solid #4caf50;">
            <b>📎 Sources ({len(unique_sources)}):</b>
            <ul style="margin: 5px 0 0 0; padding-left: 25px;">{items}</ul>
        </div>
        """
        display(HTML(html_content))

    @staticmethod
    def error(message: str) -> None:
        """Display an error message."""
        html_content = f"""
        <div style="margin: 10px 0; padding: 15px; background-color: #ffebee;
                    border-radius: 5px; border-left: 4px solid #f44336; color: #c62828;">
            <b>❌ Error:</b> {html.escape(message)}
        </div>
        """
        display(HTML(html_content))

    @staticmethod
    def info(message: str) -> None:
        """Display an info message."""
        html_content = f"""
        <div style="margin: 10px 0; padding: 10px; background-color: #e3f2fd;
                    border-radius: 5px; border-left: 4px solid #2196f3; color: #1565c0;">
            <b>ℹ️</b> {html.escape(message)}
        </div>
        """
        display(HTML(html_content))

    @staticmethod
    def success(message: str) -> None:
        """Display a success message."""
        html_content = f"""
        <div style="margin: 10px 0; padding: 10px; background-color: #e8f5e9;
                    border-radius: 5px; border-left: 4px solid #4caf50; color: #2e7d32;">
            <b>✅</b> {html.escape(message)}
        </div>
        """
        display(HTML(html_content))


# =============================================================================
# Chat Widget (A-502)
# =============================================================================


class ChatWidget:
    """
    Interactive chat widget for Jupyter.

    Provides a simple interface for chatting with the LLM.
    """

    def __init__(
        self,
        model: str = "rivault-gpt-oss-static",
        system_prompt: Optional[str] = None,
        client: Optional[Client] = None,
    ):
        """
        Initialize chat widget.

        Args:
            model: LLM model to use
            system_prompt: Optional system prompt
            client: Optional pre-configured client
        """
        self.model = model
        self.system_prompt = system_prompt
        self._client = client
        self._history: list[dict[str, str]] = []
        self._formatter = OutputFormatter()

        if system_prompt:
            self._history.append({"role": "system", "content": system_prompt})

    @property
    def client(self) -> Client:
        """Get or create client instance."""
        if self._client is None:
            config = get_config()
            self._client = Client(
                gateway_url=config.gateway_url,
                api_key=config.api_key,
            )
        return self._client

    def chat(
        self,
        message: str,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        """
        Send a message and get response.

        Args:
            message: User message
            stream: Whether to stream response
            temperature: LLM temperature
            max_tokens: Max tokens in response

        Returns:
            Assistant response text
        """
        # Add user message
        self._history.append({"role": "user", "content": message})
        self._formatter.chat_message("user", message)

        try:
            if stream:
                response = self._stream_response(temperature, max_tokens)
            else:
                response = self._sync_response(temperature, max_tokens)

            # Add assistant response to history
            self._history.append({"role": "assistant", "content": response})
            return response

        except Exception as e:
            self._formatter.error(str(e))
            # Remove failed user message from history
            self._history.pop()
            raise

    def _sync_response(self, temperature: float, max_tokens: int) -> str:
        """Get synchronous response."""
        response = asyncio.get_event_loop().run_until_complete(
            self.client.chat(
                messages=self._history,
                model=self.model,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )
        )

        content = response.choices[0].message.content
        self._formatter.chat_message("assistant", content)
        return content

    def _stream_response(self, temperature: float, max_tokens: int) -> str:
        """Get streaming response with live output."""

        async def stream():
            full_response = []

            # Start assistant message container
            print("🤖 Assistant: ", end="")

            async for chunk in self.client.chat(
                messages=self._history,
                model=self.model,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            ):
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response.append(content)
                    print(content, end="", flush=True)

            print()  # Newline at end
            return "".join(full_response)

        return asyncio.get_event_loop().run_until_complete(stream())

    def clear(self) -> None:
        """Clear chat history."""
        self._history = []
        if self.system_prompt:
            self._history.append({"role": "system", "content": self.system_prompt})
        self._formatter.info("Chat history cleared.")

    def history(self) -> list[dict[str, str]]:
        """Get chat history."""
        return self._history.copy()

    def display(self) -> None:
        """Display chat widget info."""
        html_content = (
            brand_html("chat")
            + """
        <div style="padding: 15px; background-color: #f5f5f5; border-radius: 8px;
                    border: 1px solid #ddd; margin: 10px 0;">
            <h3 style="margin-top: 0; color: #1976d2;">Notebook Helper</h3>
            <p>Use <code>chat.chat("your message")</code> to send messages.</p>
            <p>Other methods:</p>
            <ul>
                <li><code>chat.clear()</code> - Clear history</li>
                <li><code>chat.history()</code> - Get message history</li>
            </ul>
        </div>
        """
        )
        display(HTML(html_content))


# =============================================================================
# RAG Widget (A-502)
# =============================================================================


class RAGWidget:
    """
    Interactive RAG widget for Jupyter.

    Provides interface for querying documents and viewing results.
    """

    def __init__(
        self,
        collection: str = "default",
        top_k: int = 5,
        client: Optional[Client] = None,
    ):
        """
        Initialize RAG widget.

        Args:
            collection: Default collection to query
            top_k: Default number of results
            client: Optional pre-configured client
        """
        self.collection = collection
        self.top_k = top_k
        self._client = client
        self._formatter = OutputFormatter()
        self._last_results: Optional[Any] = None

    @property
    def client(self) -> Client:
        """Get or create client instance."""
        if self._client is None:
            config = get_config()
            self._client = Client(
                gateway_url=config.gateway_url,
                api_key=config.api_key,
            )
        return self._client

    def query(
        self,
        query: str,
        collection: Optional[str] = None,
        top_k: Optional[int] = None,
        show_results: bool = True,
    ) -> Any:
        """
        Query documents.

        Args:
            query: Search query
            collection: Collection to search (default: widget's collection)
            top_k: Number of results (default: widget's top_k)
            show_results: Whether to display results

        Returns:
            Query results
        """
        collection = collection or self.collection
        top_k = top_k or self.top_k

        self._formatter.info(f"Searching '{collection}' for: {query}")

        try:
            result = asyncio.get_event_loop().run_until_complete(
                self.client.rag.query(
                    query=query,
                    collection=collection,
                    top_k=top_k,
                )
            )

            self._last_results = result

            if show_results:
                self._display_results(result)

            return result

        except Exception as e:
            self._formatter.error(str(e))
            raise

    def _display_results(self, result: Any) -> None:
        """Display query results."""
        if not result.results:
            self._formatter.info("No results found.")
            return

        self._formatter.success(f"Found {len(result.results)} results")

        sources = []
        for i, r in enumerate(result.results, 1):
            source = r.metadata.get("source", f"document-{i}")
            sources.append(source)

            self._formatter.rag_result(
                index=i,
                content=r.content,
                score=r.score,
                source=source,
                metadata={k: v for k, v in r.metadata.items() if k != "source"},
            )

        self._formatter.sources_summary(sources)

    def ask(
        self,
        question: str,
        collection: Optional[str] = None,
        top_k: Optional[int] = None,
        model: str = "rivault-gpt-oss-static",
    ) -> str:
        """
        RAG-augmented question answering.

        Queries documents and generates an answer using LLM.

        Args:
            question: Question to answer
            collection: Collection to search
            top_k: Number of documents to retrieve
            model: LLM model to use

        Returns:
            Generated answer
        """
        # Get relevant documents
        result = self.query(
            query=question,
            collection=collection,
            top_k=top_k,
            show_results=False,
        )

        if not result.results:
            self._formatter.info("No relevant documents found.")
            return "I couldn't find any relevant information to answer your question."

        # Build context
        context_parts = []
        sources = []
        for i, r in enumerate(result.results, 1):
            context_parts.append(f"[{i}] {r.content}")
            sources.append(r.metadata.get("source", f"doc-{i}"))

        context = "\n\n".join(context_parts)
        sources_str = ", ".join(set(sources))

        self._formatter.info(f"Using {len(result.results)} documents as context")

        # Generate answer
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a helpful assistant that answers questions based on "
                    "provided context. Always cite your sources using [1], [2], etc."
                ),
            },
            {
                "role": "user",
                "content": f"""Based on the following context:

{context}

---
Sources: {sources_str}

Question: {question}

Please provide a comprehensive answer based on the context above.""",
            },
        ]

        try:
            response = asyncio.get_event_loop().run_until_complete(
                self.client.chat(
                    messages=messages,
                    model=model,
                    temperature=0.3,
                    max_tokens=1024,
                    stream=False,
                )
            )

            answer = response.choices[0].message.content
            self._formatter.chat_message("assistant", answer, name="Answer")
            self._formatter.sources_summary(sources)

            return answer

        except Exception as e:
            self._formatter.error(str(e))
            raise

    def display(self) -> None:
        """Display RAG widget info."""
        html_content = f"""
        <div style="padding: 15px; background-color: #f5f5f5; border-radius: 8px;
                    border: 1px solid #ddd; margin: 10px 0;">
            <h3 style="margin-top: 0; color: #1976d2;">🔍 R4 RAG Widget</h3>
            <p><b>Collection:</b> {self.collection}</p>
            <p><b>Top-K:</b> {self.top_k}</p>
            <p>Methods:</p>
            <ul>
                <li><code>rag.query("search terms")</code> - Search documents</li>
                <li><code>rag.ask("question")</code> - RAG-augmented Q&A</li>
            </ul>
        </div>
        """
        display(HTML(html_content))
