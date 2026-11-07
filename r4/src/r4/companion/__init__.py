"""R4 Research Companion — a dedicated AI colleague for researchers.

The companion module provides a full-featured terminal agent that acts as
the primary interface between researchers and the R4 platform.
It combines an extensible agent core, a layered skill/memory/context system,
and an invisible harness engineering layer that auto-discovers environment
context, validates operations, tracks progress across sessions, and learns
from researcher corrections.

Usage:
    from r4.companion import ResearchCompanion, CompanionSession

    companion = ResearchCompanion(client)
    session = companion.new_session("Seismic Analysis")
    response = session.send("Analyze my simulation output in data/")

Architecture:
    Agent Core: ResearchCompanion (agentic loop with tools)
    Context: ContextManager (compaction, memory, RAG)
    Session: CompanionSession (thin orchestration layer)
    Session Helpers: session_flow/session_turns/session_usage/session_runtime/session_render
    Persistence: session_store (sqlite state and compatibility bridge)
    Skills: SkillRegistry (built-in + user + project skills)
    Harness: invisible feedforward/feedback layer
    UI: JS (Ink) companion TUI via Node.js bridge
"""

from __future__ import annotations

from .agent import CompanionEvent, CompanionEventType, ResearchCompanion
from .context import ContextManager
from .permissions import PermissionManager, PermissionMode
from .session import CompanionSession
from .session_store import CompanionSessionStore
from .skills import Skill, SkillRegistry

__all__ = [
    "ResearchCompanion",
    "CompanionEvent",
    "CompanionEventType",
    "CompanionSession",
    "CompanionSessionStore",
    "ContextManager",
    "PermissionManager",
    "PermissionMode",
    "Skill",
    "SkillRegistry",
]
