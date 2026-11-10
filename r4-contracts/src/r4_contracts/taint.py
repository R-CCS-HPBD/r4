"""Untrusted-content provenance (taint) DTOs — ADR-039.

Contracts layer is stdlib-only and dependency-free (ADR-005). These types
cross the wire between the companion session (where untrusted textual
content lands) and the permission gate (where tool calls are checked).

The contract is minimal on purpose for the first fence round: a *tainted
segment* pins an excerpt of tool output to a taint class; matching is
done by substring against later tool-call arguments (WP-02).
"""

from __future__ import annotations

from dataclasses import dataclass

#: Taint classes every producer agrees on.
TAINT_CLASSES: tuple[str, ...] = (
    "document",
    "rag_chunk",
    "lab_file",
    "mcp_output",
    "web",
    "generic",
    # ADR-114: text another of the user's sessions sent (session_inbox).
    "session_message",
)

#: Companion tool name → taint class for results that count as untrusted
#: external content (results from platform knowledge/RAG/lab/MCP/web tools).
TOOL_TAINT_CLASSES: dict[str, str] = {
    "knowledge_query": "rag_chunk",
    # ADR-093 D-10: every retrieval tool returns document text. Retrieved
    # passages the harness injects register as ``context_pack``.
    "search_docs": "rag_chunk",
    "open_passage": "rag_chunk",  # K1.4: one passage with its context
    "rag_query": "rag_chunk",
    "search_agentic": "rag_chunk",
    "context_pack": "rag_chunk",
    # ADR-092: a plan's results carry what its agents read.
    "team_plan_run": "generic",
    "team_plan_result": "generic",
    "knowledge_show": "rag_chunk",
    "lab_catalog_search": "lab_file",
    "lab_file_read": "lab_file",
    "mcp_invoke": "mcp_output",
    "mcp_list_tools": "mcp_output",
    "search": "web",
    "web_search": "web",
    "web_fetch": "web",
    # ADR-114 D-4: another session's message is another agent's words.
    "session_inbox": "session_message",
}

#: Name prefix → taint class for tools whose names are minted at runtime.
#: First-class MCP tools register as ``mcp.<server>.<tool>`` (companion
#: ``mcp_first_class``), so no fixed map entry can name them; their results
#: are MCP output like ``mcp_invoke``'s (2026-10-30 fix).
TOOL_TAINT_PREFIXES: tuple[tuple[str, str], ...] = (("mcp.", "mcp_output"),)


def taint_class_for_tool(tool_name: str) -> str | None:
    """The taint class of a tool's results by its name alone, or None.

    The fixed map first, then the dynamic-name prefixes. A tool whose
    taint depends on what the call did (a shell command that downloads)
    is classified by the producer, which sees the arguments.
    """
    fixed = TOOL_TAINT_CLASSES.get(tool_name)
    if fixed is not None:
        return fixed
    for prefix, taint_class in TOOL_TAINT_PREFIXES:
        if tool_name.startswith(prefix) and len(tool_name) > len(prefix):
            return taint_class
    return None


#: Minimum excerpt length that counts as a provenance signal. Anything
#: shorter is too generic to attribute (JSON punctuation, short words).
MIN_TAINT_EXCERPT_LEN = 24

#: Hard bound adopted per tool result when registering provenance.
MAX_EXCERPTS_PER_RESULT = 32


@dataclass(frozen=True)
class TaintedSegment:
    """One untrusted-content excerpt with provenance.

    Attributes:
        text_excerpt: Normalized substring evidence (≤512 chars) sourced
            from a tool result.
        taint_class: One of :data:`TAINT_CLASSES`.
        source_tool: The tool name the excerpt came from.
        registered_at: Epoch seconds when the registry took it.
    """

    text_excerpt: str
    taint_class: str
    source_tool: str
    registered_at: float


__all__ = [
    "TaintedSegment",
    "TOOL_TAINT_CLASSES",
    "TOOL_TAINT_PREFIXES",
    "TAINT_CLASSES",
    "taint_class_for_tool",
    "MIN_TAINT_EXCERPT_LEN",
    "MAX_EXCERPTS_PER_RESULT",
]
