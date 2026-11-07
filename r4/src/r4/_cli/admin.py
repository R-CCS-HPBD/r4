"""``r4 admin`` command group — operator-only commands.

Groups operator-oriented commands under a single namespace:
  r4 admin analytics   (governance analytics dashboard)
  r4 admin trace       (request audit trail)

User-facing commands are top-level:
  r4 governance        (quotas, ownership, audit, shares)
  r4 map               (connection map with security boundary)
  r4 source            (manage endpoint sources)
"""

from __future__ import annotations

from .groups import CommandGroup
from .registry import command


def _build_admin_children():
    """Lazy-import child handlers to avoid circular imports at module level."""
    from .governance import AdminGovernanceCommand
    from .trace import TraceCommand

    return [
        AdminGovernanceCommand(),
        TraceCommand(),
    ]


@command
class AdminGroup(CommandGroup):
    """``r4 admin`` — operator-only commands."""

    name = "admin"
    help = "Operator commands: analytics, trace"
    group_epilog = (
        "Examples:\n  r4 admin analytics --project my-proj\n  r4 admin trace <request-id>"
    )

    def register(self, subparsers):
        # Build children lazily at registration time
        self.children = _build_admin_children()
        return super().register(subparsers)
