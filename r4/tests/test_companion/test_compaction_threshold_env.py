"""Pins for env-overridable compaction threshold (T5 topic-20 tail)."""

from __future__ import annotations

from r4.companion.context import ContextManager


def _make(env_value: str | None) -> ContextManager:
    import os

    if env_value is None:
        os.environ.pop("R4_COMPANION_COMPACTION_THRESHOLD", None)
    else:
        os.environ["R4_COMPANION_COMPACTION_THRESHOLD"] = env_value
    try:
        return ContextManager(client=None)
    finally:
        os.environ.pop("R4_COMPANION_COMPACTION_THRESHOLD", None)


class TestCompactionThresholdEnv:
    def test_default_080_stays(self):
        cm = _make(None)
        assert cm.compaction_threshold == 0.80

    def test_env_override_accepted_when_parseable(self):
        cm = _make("0.65")
        assert cm.compaction_threshold == 0.65

    def test_env_malformed_falls_back_to_default(self):
        cm = _make("bogus")
        assert cm.compaction_threshold == 0.80

    def test_env_out_of_band_ignored(self):
        cm = _make("1.5")
        assert cm.compaction_threshold == 0.80
