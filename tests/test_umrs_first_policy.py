"""Static guards for the UMRS-first contribution policy."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRIBUTING = ROOT / "CONTRIBUTING.md"
PR_TEMPLATE = ROOT / ".github" / "pull_request_template.md"


def test_contributing_includes_umrs_first_state_checklist() -> None:
    text = CONTRIBUTING.read_text()

    assert "UMRS-First State Ownership Checklist" in text
    assert "Can `r4/` remain a thin SDK/CLI wrapper" in text
    assert "Can `gateway/` remain policy, auth, routing, audit" in text
    assert "workflow registry, endpoint registry, or retry state machine" in text
    assert "why UMRS is not the right owner" in text


def test_pull_request_template_requires_umrs_state_ownership_answer() -> None:
    text = PR_TEMPLATE.read_text()

    assert "UMRS-First State Ownership" in text
    assert "does not add durable platform state" in text
    assert "why UMRS is not the right owner" in text
    assert "documented migration path" in text
