"""
R4 Framework Integrations.

Optional adapters for third-party agent frameworks.
Install with: pip install r4[langchain] or r4[mcp-use]

These are OPTIONAL - the core R4 SDK works standalone.
"""

# Note: Actual imports are lazy to avoid requiring optional dependencies
# Each submodule handles its own ImportError gracefully

__all__ = [
    "langchain",
    "llamaindex",
    "mcp_use",
]
