"""Formatting and export helpers for companion sessions."""

from __future__ import annotations

from .session_models import SessionMessage


def format_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def export_markdown(
    *,
    name: str,
    session_id: str,
    created_at: str,
    messages: list[SessionMessage],
) -> str:
    lines = [f"# {name}", f"Session: {session_id}", f"Created: {created_at}", ""]
    for message in messages:
        role = message.role.capitalize()
        lines.append(f"## {role}")
        lines.append(message.content)
        lines.append("")
    return "\n".join(lines)
