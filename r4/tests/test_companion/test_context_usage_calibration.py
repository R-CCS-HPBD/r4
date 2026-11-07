"""G10 (2026-10-28): measured-usage calibration of the context estimator.

Provider replies carrying ``usage.prompt_tokens`` re-anchor the
chars-per-token ratio, so JSON-dense agentic payloads (tool calls and
schemas tokenize near 2-2.5 chars/token, not the 3.5 default) stop
under-counting and the compaction threshold fires on real pressure.
"""

from __future__ import annotations

import pytest

from r4.companion.context import CHARS_PER_TOKEN, ContextManager


def _manager(monkeypatch: pytest.MonkeyPatch, **kwargs) -> ContextManager:
    monkeypatch.delenv("R4_COMPANION_CHARS_PER_TOKEN", raising=False)
    return ContextManager(**kwargs)


def _json_heavy_messages(chars: int = 7_000) -> list[dict]:
    blob = '{"a":' + "1," * (chars // 4) + "0}"
    return [
        {"role": "assistant", "content": blob, "tool_calls": [{"name": "f", "args": blob}]},
        {"role": "tool", "content": blob},
    ]


def test_default_ratio_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    assert manager._chars_per_token == CHARS_PER_TOKEN
    assert manager.estimate_tokens("x" * 3_500) == 1_000


def test_measured_samples_move_the_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    messages = _json_heavy_messages()
    counted = manager._counted_chars(messages, None)
    # A JSON-dense wire shape tokenizes near 2.2 chars/token.
    prompt_tokens = int(counted / 2.2)
    before = manager.estimate_tokens('{"k": "v"}' * 500)
    for _ in range(6):
        manager.observe_prompt_usage(messages=messages, tools=None, prompt_tokens=prompt_tokens)
    assert manager._chars_per_token < 2.6
    assert manager.estimate_tokens('{"k": "v"}' * 500) > before


def test_noise_samples_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    manager.observe_prompt_usage(messages=_json_heavy_messages(), tools=None, prompt_tokens=1)
    manager.observe_prompt_usage(messages=_json_heavy_messages(), tools=None, prompt_tokens=10**9)
    manager.observe_prompt_usage(messages=[], tools=None, prompt_tokens=0)
    assert manager._chars_per_token == CHARS_PER_TOKEN


def test_pinned_ratio_ignores_measured_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R4_COMPANION_CHARS_PER_TOKEN", "4.0")
    manager = ContextManager()
    messages = _json_heavy_messages()
    counted = manager._counted_chars(messages, None)
    for _ in range(4):
        manager.observe_prompt_usage(
            messages=messages, tools=None, prompt_tokens=int(counted / 2.2)
        )
    assert manager._chars_per_token == 4.0
    assert manager.estimate_tokens("x" * 4_000) == 1_000


def test_compaction_threshold_fires_on_calibrated_pressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch, max_context_tokens=10_000)
    messages = _json_heavy_messages(chars=10_000)
    counted = manager._counted_chars(messages, None)
    # Default ratio: 10k+ chars read as ~3k tokens — under the 8k threshold.
    assert not manager.needs_compaction(messages)
    # Measured truth: the same payload is ~4.6k tokens... still under; feed
    # the denser 1.6 chars/token sample JSON tool output actually produces.
    dense_tokens = int(counted / 1.6)
    for _ in range(8):
        manager.observe_prompt_usage(messages=messages, tools=None, prompt_tokens=dense_tokens)
    assert manager._chars_per_token <= 1.75
    assert manager.needs_compaction(messages)


def test_tool_schema_mass_counts_toward_the_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(monkeypatch)
    messages = [{"role": "user", "content": "x" * 350}]
    tools = [{"name": "t", "description": "y" * 100, "parameters": {"p": "z" * 3_000}}]
    counted = manager._counted_chars(messages, tools)
    prompt_tokens = counted // 2  # sample ratio exactly 2.0 chars/token
    manager.observe_prompt_usage(messages=messages, tools=tools, prompt_tokens=prompt_tokens)
    expected = 0.6 * CHARS_PER_TOKEN + 0.4 * 2.0
    assert manager._chars_per_token == pytest.approx(expected, rel=1e-3)
