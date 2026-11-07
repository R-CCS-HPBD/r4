"""The one on/off parser of the TB 4.0 harness switches (review 2026-10-24).

Three switches turned off only on ``0``, two also on ``false``/``no``/``off``;
an ablation arm written ``=false`` would have run with the fix on.
"""

from __future__ import annotations

import pytest

from r4.companion.env_switch import autonomous, env_flag, env_switch


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        (" Yes ", True),
        ("ON", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("off", False),
        ("", None),
        ("maybe", None),
    ],
)
def test_env_flag(monkeypatch, raw, expected):
    monkeypatch.setenv("R4_TEST_SWITCH", raw)
    assert env_flag("R4_TEST_SWITCH") is expected


@pytest.mark.parametrize("default", [True, False])
def test_unset_or_unknown_gives_the_default(monkeypatch, default):
    monkeypatch.delenv("R4_TEST_SWITCH", raising=False)
    assert env_switch("R4_TEST_SWITCH", default) is default
    monkeypatch.setenv("R4_TEST_SWITCH", "maybe")
    assert env_switch("R4_TEST_SWITCH", default) is default


def test_autonomous(monkeypatch):
    monkeypatch.setenv("R4_AUTONOMOUS", "true")
    assert autonomous()
    monkeypatch.setenv("R4_AUTONOMOUS", "0")
    assert not autonomous()
