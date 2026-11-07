"""Pins for _bound_tool_result_history (T5 topic-20 — per-round payload discipline)."""

from __future__ import annotations

from r4.companion.agent import (
    _TOOL_RESULT_HEAD_KEEP,
    _TOOL_RESULT_TAIL_KEEP,
    _bound_tool_result_history,
)


def _msgs(tool_contents: list[tuple[str, str]]) -> list[dict]:
    out = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for tc_id, body in tool_contents:
        out.append({"role": "assistant", "content": None, "tool_calls": [{"id": tc_id}]})
        out.append({"role": "tool", "tool_call_id": tc_id, "content": body})
    return out


def _elision_marker(omitted: int, call_id: str = "c1") -> str:
    return (
        f"\n[… ~{omitted} chars elided to keep the context small — call "
        f'tool_result_fetch(tool_call_id="{call_id}") to read it in full …]\n'
    )


class TestBound:
    def test_big_older_results_get_compacted_with_marker(self):
        msgs = _msgs([("c1", "x" * 10_000), ("c2", "OK")])
        out = _bound_tool_result_history(msgs)
        bodies = {m.get("tool_call_id"): m["content"] for m in out if m.get("role") == "tool"}
        assert bodies["c2"] == "OK"
        c1 = bodies["c1"]
        omitted = 10_000 - _TOOL_RESULT_HEAD_KEEP - _TOOL_RESULT_TAIL_KEEP
        assert len(c1) == _TOOL_RESULT_HEAD_KEEP + _TOOL_RESULT_TAIL_KEEP + len(
            _elision_marker(omitted)
        )
        assert "elided" in c1

    def test_latest_tool_result_stays_untouched(self):
        msgs = _msgs([("c1", "y" * 10_000), ("c2", "z" * 9_000)])
        out = _bound_tool_result_history(msgs)
        bodies = {m.get("tool_call_id"): m["content"] for m in out if m.get("role") == "tool"}
        assert bodies["c2"] == "z" * 9_000
        assert "elided" not in bodies["c2"]

    def test_small_results_stay_intact(self):
        msgs = _msgs([("c1", "abc"), ("c2", "OK")])
        out = _bound_tool_result_history(msgs)
        bodies = {m.get("tool_call_id"): m["content"] for m in out if m.get("role") == "tool"}
        assert bodies == {"c1": "abc", "c2": "OK"}

    def test_single_tool_turn_stays_unchanged(self):
        msgs = _msgs([("c1", "a" * 10_000)])
        out = _bound_tool_result_history(msgs)
        bodies = [m for m in out if m.get("role") == "tool"]
        assert bodies[0]["content"] == "a" * 10_000

    def test_non_tool_rows_stay_untouched(self):
        msgs = _msgs([("c1", "x" * 10_000), ("c2", "OK")])
        out = _bound_tool_result_history(msgs)
        roles = [m["role"] for m in out]
        assert roles == ["system", "user", "assistant", "tool", "assistant", "tool"]


class TestRoundProtectionAndBudget:
    """W4 (2026-10-05, plan row H5)."""

    def test_whole_last_round_stays_full(self):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]})
        msgs.append({"role": "tool", "tool_call_id": "a", "content": "x" * 9_000})
        msgs.append(
            {"role": "assistant", "content": None, "tool_calls": [{"id": "b"}, {"id": "c"}]}
        )
        msgs.append({"role": "tool", "tool_call_id": "b", "content": "y" * 9_000})
        msgs.append({"role": "tool", "tool_call_id": "c", "content": "z" * 9_000})
        bodies = {
            m.get("tool_call_id"): m["content"]
            for m in _bound_tool_result_history(msgs)
            if m.get("role") == "tool"
        }
        assert bodies["b"] == "y" * 9_000 and bodies["c"] == "z" * 9_000
        assert "tool_result_fetch" in bodies["a"]

    def test_nothing_trimmed_under_budget(self):
        msgs = _msgs([("c1", "x" * 10_000), ("c2", "OK")])
        assert _bound_tool_result_history(msgs, budget_chars=50_000) == msgs
        trimmed = _bound_tool_result_history(msgs, budget_chars=5_000)
        assert "elided" in [m for m in trimmed if m.get("tool_call_id") == "c1"][0]["content"]
