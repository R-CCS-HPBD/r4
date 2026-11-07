"""
Jupyter Integration for R4 Platform (A-501-503).

Provides:
- Magic commands: %%r4, %r4, %rag
- Companion magic: %%companion, %companion, %companion_sessions, etc.
- Interactive chat widget
- Companion chat widget
- Output formatting (Markdown, HTML)
"""

from .magics import R4Magics, load_ipython_extension, unload_ipython_extension
from .widgets import ChatWidget, RAGWidget

__all__ = [
    "R4Magics",
    "ChatWidget",
    "RAGWidget",
    "load_ipython_extension",
    "unload_ipython_extension",
]


# Lazy imports for companion integration
def _load_companion_widget():
    from .companion_widget import CompanionWidget

    return CompanionWidget


def _load_companion_magics():
    from .companion_magic import CompanionMagics

    return CompanionMagics
