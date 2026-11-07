# SPDX-License-Identifier: Apache-2.0
"""Regression tests for ``r4._cli.agent._format_thinking``.

2026-10-20 spike finding (Harness² kq cell grading): ``r4 chat`` died with
``Error: expected string or bytes-like object`` whenever a reasoning model spent
the whole ``max_tokens`` budget on reasoning and the reply carried
``content=None`` — the formatting helper passed that None straight into a
regex. The one-shot chat path is how the same-model lane's graders call the
model, so the crash made blind judge calls fail closed after three retries.
"""

from r4._cli.agent import _format_thinking


def test_none_content_returns_empty_string():
    # The regression itself: old code raised TypeError here.
    assert _format_thinking(None) == ""


def test_empty_string_returns_empty_string():
    assert _format_thinking("") == ""


def test_plain_text_passthrough():
    assert _format_thinking("hello world") == "hello world"


def test_thinking_block_dimmed_and_answer_visible():
    out = _format_thinking("<think>step one</think>FINAL ANSWER: 42")
    assert "step one" in out
    assert out.endswith("FINAL ANSWER: 42")
