"""Tests for companion UI launcher interrupt handling (post-Ink removal).

The Ink JS TUI was deleted 2026-09-11 (ADR-014). These tests pin the
removal error classes that surface at the legacy launch entry points —
a stale call site must fail loudly with the removal date + ADR reference,
never half-launch.
"""

from __future__ import annotations

import pytest

from r4.companion import ui as ui_module


def test_launch_js_ui_raises_removal_notice() -> None:
    with pytest.raises(RuntimeError, match="removed 2026-09-11"):
        ui_module._launch_js_ui(
            session_id=None,
            model=None,
            skill=None,
            system=None,
        )


def test_companion_js_cmd_raises_removal_notice() -> None:
    with pytest.raises(RuntimeError, match="removed 2026-09-11"):
        ui_module._companion_js_cmd("/tmp/x.mjs", node="node")


def test_ink_notice_carries_migration_pointer() -> None:
    with pytest.raises(RuntimeError, match="r4 console"):
        ui_module._launch_js_ui(session_id=None, model=None, skill=None, system=None)
