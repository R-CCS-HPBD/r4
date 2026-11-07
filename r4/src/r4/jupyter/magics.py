"""
IPython Magic Commands for R4 Platform (A-501).

Provides:
- %%r4: Cell magic for LLM chat
- %r4: Line magic for quick queries
- %rag: RAG query magic
- %r4_config: Configure settings

Usage:
    # Load the extension
    %load_ext r4.jupyter

    # Chat with LLM
    %%r4
    Explain the concept of quantum entanglement

    # Quick query
    %r4 What is the speed of light?

    # RAG query
    %rag query="Find papers about neural networks" collection="papers"

    # Configure
    %r4_config model=rivault-gpt-oss-static temperature=0.7
"""

from __future__ import annotations

import os
from typing import Optional

from IPython.core.magic import (
    Magics,
    cell_magic,
    line_magic,
    magics_class,
)
from IPython.core.magic_arguments import argument, magic_arguments, parse_argstring
from IPython.display import HTML, Markdown, display

# Try to import the r4 client
try:
    from r4.client import R4Client

    HAS_R4 = True
except ImportError:
    HAS_R4 = False
    R4Client = None


@magics_class
class R4Magics(Magics):
    """
    IPython magics for R4 Platform.

    Provides convenient access to LLM and RAG capabilities from Jupyter.
    """

    def __init__(self, shell):
        """Initialize magics with IPython shell."""
        super().__init__(shell)

        # Configuration
        self._config = {
            "model": os.getenv("R4_MODEL", "rivault-gpt-oss-static"),
            "temperature": float(os.getenv("R4_TEMPERATURE", "0.7")),
            "max_tokens": int(os.getenv("R4_MAX_TOKENS", "2048")),
            "stream": True,
            "collection": os.getenv("R4_COLLECTION", "default"),
            "top_k": int(os.getenv("R4_TOP_K", "5")),
            "gateway_url": os.getenv("R4_GATEWAY_URL", "http://localhost:8000"),
        }

        # Client (lazy loaded)
        self._client: Optional[R4Client] = None

    def _get_client(self) -> R4Client:
        """Get or create the R4 client."""
        if not HAS_R4:
            raise ImportError("r4 package not installed. Install with: pip install r4")

        if self._client is None:
            self._client = R4Client(
                gateway_url=self._config["gateway_url"],
            )

        return self._client

    def _format_response(self, text: str, format_type: str = "markdown") -> None:
        """Display formatted response."""
        if format_type == "markdown":
            display(Markdown(text))
        elif format_type == "html":
            display(HTML(f"<div class='r4-response'>{text}</div>"))
        else:
            print(text)

    def _stream_response(self, response_iter) -> str:
        """Stream response tokens and return full text."""
        full_text = ""
        print("", end="", flush=True)

        for chunk in response_iter:
            if isinstance(chunk, str):
                print(chunk, end="", flush=True)
                full_text += chunk
            elif hasattr(chunk, "choices") and chunk.choices:
                delta = chunk.choices[0].delta
                if hasattr(delta, "content") and delta.content:
                    print(delta.content, end="", flush=True)
                    full_text += delta.content

        print()  # Newline at end
        return full_text

    @line_magic
    @magic_arguments()
    @argument("query", nargs="*", help="Query text")
    @argument("-m", "--model", help="Model to use")
    @argument("-t", "--temperature", type=float, help="Temperature")
    @argument("--no-stream", action="store_true", help="Disable streaming")
    @argument("-o", "--output", help="Variable to store result")
    def r4(self, line: str) -> Optional[str]:
        """
        Line magic for quick LLM queries.

        Examples:
            %r4 What is quantum computing?
            %r4 -m gpt-4 Explain relativity
            %r4 -o result What is 2+2?
        """
        args = parse_argstring(self.r4, line)
        query = " ".join(args.query)

        if not query:
            print("Usage: %r4 <your question>")
            return None

        try:
            client = self._get_client()

            model = args.model or self._config["model"]
            temperature = args.temperature or self._config["temperature"]
            stream = not args.no_stream and self._config["stream"]

            response = client.chat(
                query,
                model=model,
                temperature=temperature,
                max_tokens=self._config["max_tokens"],
                stream=stream,
            )

            if stream:
                result = self._stream_response(response)
            else:
                result = response.content
                self._format_response(result)

            # Store result if requested
            if args.output:
                self.shell.user_ns[args.output] = result
                return None

            return result

        except Exception as e:
            print(f"Error: {e}")
            return None

    @cell_magic("r4")
    @magic_arguments()
    @argument("-m", "--model", help="Model to use")
    @argument("-t", "--temperature", type=float, help="Temperature")
    @argument("-s", "--system", help="System prompt")
    @argument("--no-stream", action="store_true", help="Disable streaming")
    @argument("-o", "--output", help="Variable to store result")
    @argument("--format", choices=["markdown", "html", "text"], default="markdown")
    def r4_cell(self, line: str, cell: str) -> Optional[str]:
        """
        Cell magic for LLM chat.

        Examples:
            %%r4
            Explain quantum computing in simple terms.

            %%r4 -s "You are a Python expert"
            How do I sort a list?

            %%r4 --format html -o answer
            What is the capital of France?
        """
        args = parse_argstring(self.r4_cell, line)

        if not cell.strip():
            print("Usage: %%r4\\n<your prompt>")
            return None

        try:
            client = self._get_client()

            model = args.model or self._config["model"]
            temperature = args.temperature or self._config["temperature"]
            stream = not args.no_stream and self._config["stream"]

            response = client.chat(
                cell.strip(),
                model=model,
                temperature=temperature,
                max_tokens=self._config["max_tokens"],
                stream=stream,
                system=args.system,
            )

            if stream:
                result = self._stream_response(response)
            else:
                result = response.content

            # Format output
            if not stream:
                self._format_response(result, args.format)

            # Store result if requested
            if args.output:
                self.shell.user_ns[args.output] = result
                return None

            return result

        except Exception as e:
            print(f"Error: {e}")
            return None

    @line_magic
    @magic_arguments()
    @argument("query", nargs="*", help="Search query")
    @argument("-c", "--collection", help="Collection to search")
    @argument("-k", "--top-k", type=int, help="Number of results")
    @argument("--with-llm", action="store_true", help="Include LLM synthesis")
    @argument("-o", "--output", help="Variable to store results")
    def rag(self, line: str) -> Optional[list]:
        """
        Line magic for RAG queries.

        Examples:
            %rag Find papers about neural networks
            %rag -c papers -k 10 quantum computing applications
            %rag --with-llm Summarize the documents about ML
        """
        args = parse_argstring(self.rag, line)
        query = " ".join(args.query)

        if not query:
            print("Usage: %rag <search query>")
            return None

        try:
            client = self._get_client()

            collection = args.collection or self._config["collection"]
            top_k = args.top_k or self._config["top_k"]

            # Perform RAG query
            response = client.rag.query(
                query=query,
                project_id=collection,
                top_k=top_k,
            )
            results = response.sources

            if response.answer:
                print("Answer:\n")
                self._format_response(response.answer)
                print()

            # Display results
            print(f"Found {len(results)} results:\n")
            for i, result in enumerate(results, 1):
                print(f"[{i}] Score: {result.score:.3f}")
                content = result.content[:200]
                print(f"    {content}...")
                if result.metadata:
                    print(f"    Source: {result.metadata.get('source', 'N/A')}")
                print()

            if args.with_llm and response.answer:
                print("\n--- RAG Answer ---\n")
                self._format_response(response.answer)

            # Store results if requested
            if args.output:
                self.shell.user_ns[args.output] = results
                return None

            return results

        except Exception as e:
            print(f"Error: {e}")
            return None

    @line_magic
    @magic_arguments()
    @argument("settings", nargs="*", help="Settings as key=value pairs")
    @argument("--show", action="store_true", help="Show current settings")
    @argument("--reset", action="store_true", help="Reset to defaults")
    def r4_config(self, line: str) -> None:
        """
        Configure R4 magic settings.

        Examples:
            %r4_config --show
            %r4_config model=gpt-4 temperature=0.5
            %r4_config --reset
        """
        args = parse_argstring(self.r4_config, line)

        if args.reset:
            self._config = {
                "model": "rivault-gpt-oss-static",
                "temperature": 0.7,
                "max_tokens": 2048,
                "stream": True,
                "collection": "default",
                "top_k": 5,
                "gateway_url": "http://localhost:8000",
            }
            print("Configuration reset to defaults.")
            self._client = None  # Reset client
            return

        if args.show or not args.settings:
            print("Current configuration:")
            for key, value in self._config.items():
                print(f"  {key}: {value}")
            return

        # Parse and apply settings
        for setting in args.settings:
            if "=" not in setting:
                print(f"Invalid setting format: {setting}")
                continue

            key, value = setting.split("=", 1)
            key = key.strip()
            value = value.strip()

            if key not in self._config:
                print(f"Unknown setting: {key}")
                continue

            # Type conversion
            if key in ("temperature",):
                value = float(value)
            elif key in ("max_tokens", "top_k"):
                value = int(value)
            elif key in ("stream",):
                value = value.lower() in ("true", "1", "yes")

            self._config[key] = value
            print(f"Set {key} = {value}")

        # Reset client if gateway URL changed
        if "gateway_url" in [s.split("=")[0] for s in args.settings]:
            self._client = None


def load_ipython_extension(ipython):
    """Load the r4 extension into IPython."""
    ipython.register_magics(R4Magics)
    print("R4 Platform magics loaded.")
    print("  %%r4 / %r4 - LLM chat")
    print("  %rag - RAG search")
    print("  %r4_config - Configure settings")


def unload_ipython_extension(ipython):
    """Unload the r4 extension from IPython."""
    return None
