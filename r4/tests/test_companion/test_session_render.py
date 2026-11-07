"""Tests for companion formatting and export helpers."""

from __future__ import annotations

from r4.companion.session_models import SessionMessage
from r4.companion.session_render import export_markdown, format_duration


def test_format_duration_formats_hours_minutes_and_seconds():
    assert format_duration(5) == "5s"
    assert format_duration(65) == "1m05s"
    assert format_duration(3665) == "1h01m"


def test_export_markdown_renders_message_sections():
    markdown = export_markdown(
        name="Research Session",
        session_id="session-1",
        created_at="2026-04-14T12:00:00Z",
        messages=[
            SessionMessage(role="user", content="Hello"),
            SessionMessage(role="assistant", content="Hi"),
        ],
    )

    assert markdown == (
        "# Research Session\n"
        "Session: session-1\n"
        "Created: 2026-04-14T12:00:00Z\n\n"
        "## User\n"
        "Hello\n\n"
        "## Assistant\n"
        "Hi\n"
    )
