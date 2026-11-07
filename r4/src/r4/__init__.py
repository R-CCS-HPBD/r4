"""
R4 Client SDK.

User-facing Python SDK for interacting with the RiVault R4 Platform.

Usage:
    from r4 import Client

    client = Client()  # Auto-discover from environment
    response = client.chat("What is quantum entanglement?")
    print(response)

    # Streaming
    for chunk in client.chat("Explain machine learning", stream=True):
        print(chunk, end="", flush=True)

    # Data transfer (UMRS ZeroMQ)
    result = client.data.upload("ct_scan.tif", project_id="example-project-c")
    results = client.data.upload_directory("ct_scans/", pattern="*.tif")

    # RAG (upload indexes inline; semantic search)
    client.rag.upload("notes.pdf", project_id="my-project")
    response = client.rag.query("What does the simulation show?", project_id="my-project")

    # Workflows (n8n pipelines)
    result = client.workflows.trigger("ct-analysis", input_files=["scan.tif"])

The SDK provides:
- OpenAI-compatible chat API
- Data transfer (client.data) - UMRS ZeroMQ file upload/download
- RAG (client.rag) - Embedding and semantic search
- Workflows (client.workflows) - n8n pipeline triggers
- Environment auto-configuration
- HPC-aware defaults (Slurm job awareness)
"""

# F-040: Lazy imports to reduce CLI cold-start time.
# All public symbols are resolved on first access via __getattr__.
from __future__ import annotations

__version__ = "0.2.0rc4"

# Map every public symbol to its origin module (relative to this package).
_LAZY_IMPORTS: dict[str, str] = {
    # auth
    "APIKeyManager": ".auth",
    "AuthManager": ".auth",
    "TokenInfo": ".auth",
    "TokenStore": ".auth",
    "get_auth_manager": ".auth",
    # client
    "ChatMessage": ".client",
    "ChatResponse": ".client",
    "Client": ".client",
    # config
    "Config": ".config",
    "DeployMode": ".config",
    "TransportMode": ".config",
    "get_config": ".config",
    # data
    "DataClient": ".data",
    "DownloadResult": ".data",
    "FileInfo": ".data",
    "UploadResult": ".data",
    # errors
    "APIError": ".errors",
    "AuthenticationError": ".errors",
    "ConfigurationError": ".errors",
    "R4ConnectionError": ".errors",
    "R4Error": ".errors",
    "R4TimeoutError": ".errors",
    "RateLimitError": ".errors",
    # mcp
    "MCPClient": ".mcp",
    "MCPTool": ".mcp",
    "MCPToolCall": ".mcp",
    "MCPToolResult": ".mcp",
    # oidc (optional, requires httpx)
    "OIDCConfig": ".oidc",
    "OIDCAuthCodeFlow": ".oidc",
    "oidc_login": ".oidc",
    "refresh_token": ".oidc",
    # observability
    "counted": ".observability",
    "get_health_metrics": ".observability",
    "instrument_fastapi": ".observability",
    "metrics": ".observability",
    "setup_logging": ".observability",
    "timed": ".observability",
    "traced": ".observability",
    "tracer": ".observability",
    # project
    "ProjectConfig": ".project",
    "ProjectManager": ".project",
    "get_project_manager": ".project",
    # rag
    "IndexResult": ".rag",
    "RAGClient": ".rag",
    "RAGQuery": ".rag",
    "RAGResponse": ".rag",
    # umrs_transport (optional, requires pyzmq)
    "UMRSTransport": ".umrs_transport",
    "UMRSTransportConfig": ".umrs_transport",
    "UploadStatus": ".umrs_transport",
    # vision
    "VisionClient": ".vision",
    # workflows
    "WorkflowClient": ".workflows",
    "WorkflowDefinition": ".workflows",
    "WorkflowResult": ".workflows",
    "WorkflowStatus": ".workflows",
    "WorkflowUploadResult": ".workflows",
}

# Availability flags — resolved lazily via __getattr__.
# Do NOT set these at module level; __getattr__ only fires when
# the name is not found in the module dict.


def __getattr__(name: str):  # noqa: N807
    if name == "_UMRS_TRANSPORT_AVAILABLE":
        try:
            from . import umrs_transport as _  # noqa: F401

            globals()["_UMRS_TRANSPORT_AVAILABLE"] = True
        except ImportError:
            globals()["_UMRS_TRANSPORT_AVAILABLE"] = False
        return globals()["_UMRS_TRANSPORT_AVAILABLE"]

    if name == "_OIDC_AVAILABLE":
        try:
            from . import oidc as _  # noqa: F401

            globals()["_OIDC_AVAILABLE"] = True
        except ImportError:
            globals()["_OIDC_AVAILABLE"] = False
        return globals()["_OIDC_AVAILABLE"]

    module_path = _LAZY_IMPORTS.get(name)
    if module_path is not None:
        from importlib import import_module

        mod = import_module(module_path, __name__)
        val = getattr(mod, name)
        # Cache on the module dict so subsequent access skips __getattr__
        globals()[name] = val
        return val

    raise AttributeError(f"module 'r4' has no attribute {name!r}")


__all__ = [
    # Client
    "Client",
    "ChatMessage",
    "ChatResponse",
    # Configuration
    "Config",
    "DeployMode",
    "TransportMode",
    "get_config",
    # Data transfer (UMRS ZeroMQ)
    "DataClient",
    "UploadResult",
    "DownloadResult",
    "FileInfo",
    # RAG (embedding and search)
    "RAGClient",
    "RAGQuery",
    "RAGResponse",
    "IndexResult",
    # Vision
    "VisionClient",
    # Workflows (n8n pipelines)
    "WorkflowClient",
    "WorkflowStatus",
    "WorkflowResult",
    "WorkflowDefinition",
    "WorkflowUploadResult",
    # UMRS Transport (optional, requires pyzmq)
    "UMRSTransport",
    "UMRSTransportConfig",
    "UploadStatus",
    # MCP (A-301, A-302, A-303)
    "MCPClient",
    "MCPTool",
    "MCPToolCall",
    "MCPToolResult",
    # Auth (A-101, A-102)
    "AuthManager",
    "TokenInfo",
    "TokenStore",
    "APIKeyManager",
    "get_auth_manager",
    # OIDC (A-103) - optional
    "OIDCConfig",
    "OIDCAuthCodeFlow",
    "oidc_login",
    "refresh_token",
    # Project (A-406)
    "ProjectConfig",
    "ProjectManager",
    "get_project_manager",
    # Observability (O-005)
    "metrics",
    "tracer",
    "setup_logging",
    "timed",
    "counted",
    "traced",
    "instrument_fastapi",
    "get_health_metrics",
    # Errors
    "R4Error",
    "R4ConnectionError",
    "R4TimeoutError",
    "AuthenticationError",
    "ConfigurationError",
    "RateLimitError",
    "APIError",
    # Version
    "__version__",
]


# Jupyter integration - lazy imports to avoid IPython dependency
def load_ipython_extension(ipython):
    """Load R4 Jupyter extension. Use: %load_ext r4"""
    from .jupyter import load_ipython_extension as _load

    _load(ipython)


def get_jupyter_magics():
    """Get Jupyter magic class for manual registration."""
    from .jupyter import R4Magics

    return R4Magics


def get_chat_widget(**kwargs):
    """Get interactive chat widget. Use in Jupyter notebooks."""
    from .widgets import ChatWidget

    return ChatWidget(**kwargs)


def get_rag_widget(**kwargs):
    """Get interactive RAG widget. Use in Jupyter notebooks."""
    from .widgets import RAGWidget

    return RAGWidget(**kwargs)


def get_output_formatter():
    """Get output formatter for Jupyter display."""
    from .widgets import OutputFormatter

    return OutputFormatter()
