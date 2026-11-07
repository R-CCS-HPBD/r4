"""Mode state machine for the unified TUI.

Three modes, cycled with Shift+Tab — the session's one mode (ADR-104 D-1;
``r4.companion.agent_mode`` derives the permission mode from it):
- Chat: read-only tools run, every other tool asks (default)
- Plan: read-only tools only; a plan first, which you accept into chat or autopilot
- Autopilot: tools run without asking (ADR-104 amendment 2); only credential
  access, knowledge decisions, your ask rules and deletes R4 could not back
  up first still ask
"""

from __future__ import annotations

from enum import Enum


class Mode(Enum):
    """TUI interaction modes."""

    CHAT = "chat"
    PLAN = "plan"
    AUTOPILOT = "autopilot"

    def next(self) -> "Mode":
        """Cycle to the next mode."""
        order = [Mode.CHAT, Mode.PLAN, Mode.AUTOPILOT]
        idx = order.index(self)
        return order[(idx + 1) % len(order)]

    @property
    def label(self) -> str:
        return self.value

    @property
    def description(self) -> str:
        descriptions = {
            Mode.CHAT: "read-only tools run; others ask",
            Mode.PLAN: "plan first — read-only tools only until you accept the plan",
            Mode.AUTOPILOT: (
                "tools run without asking; only credentials, knowledge decisions"
                " and deletes without a backup ask"
            ),
        }
        return descriptions[self]

    @property
    def prompt_prefix(self) -> str:
        prefixes = {
            Mode.CHAT: "you",
            Mode.PLAN: "plan",
            Mode.AUTOPILOT: "auto",
        }
        return prefixes[self]

    @property
    def style(self) -> str:
        styles = {
            Mode.CHAT: "bold green",
            Mode.PLAN: "bold yellow",
            Mode.AUTOPILOT: "bold magenta",
        }
        return styles[self]
