"""The shared ask-policy vocabulary (ADR-066; ADR-092 slice 6): the one pin
of which classes an action kind carries. UMRS's server driver and r4's local
loop both read this table; each side has one consumer test of its own."""

from __future__ import annotations

import pytest

from r4_contracts import ask_policy as ap


def test_the_trigger_classes():
    assert ap.TRIGGER_CLASSES == (
        "cost",
        "competing_hypotheses",
        "direction_change",
        "physical_action",
    )


def test_the_action_kind_table():
    assert dict(ap.ACTION_KIND_CLASSES) == {
        "workflow_trigger": {"cost"},
        "agent_iteration": {"cost"},
        "plan_run": {"cost"},
    }
    assert ap.PLAN_RUN == "plan_run"
    assert "plan_gate" not in ap.ACTION_KIND_CLASSES  # a plan's gates always ask


def test_the_table_is_read_only():
    with pytest.raises(TypeError):
        ap.ACTION_KIND_CLASSES["plan_run"] = frozenset()  # type: ignore[index]
