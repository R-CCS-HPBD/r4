"""Reading a reply to a pending plan (2026-10-02, companion/plan_reply.py).

The old check let "no, don't do it" run the plan ("do it"), "yes, but use
4 GPUs" run it without the change, and "continue planning" run it. Only an
unmistakable yes executes now; a short no cancels; hesitation asks again;
anything that says more revises the plan.
"""

from __future__ import annotations

import pytest

from r4.companion.plan_reply import classify_plan_reply


@pytest.mark.parametrize(
    "reply",
    [
        "yes",
        "Yes!",
        "y",
        "go ahead",
        "run it please",
        "lgtm",
        "ok, run it now",
        "do it",
        "execute the plan",
        "sounds good, go",
        "yes, execute now",
        "let's do it",
        "proceed",
        "Yes. Execute.",
    ],
)
def test_short_confirmations_execute(reply):
    assert classify_plan_reply(reply) == "execute"


@pytest.mark.parametrize(
    "reply",
    [
        "no",
        "no, cancel",
        "nope",
        "don't do it",
        "no, don't run it",
        "never mind",
        "stop",
        "cancel it",
        "Do not run it.",
        "no thanks",
        "don’t run it",
    ],
)
def test_short_refusals_cancel(reply):
    assert classify_plan_reply(reply) == "cancel"


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "   ",
        "wait",
        "not yet",
        "hold on",
        "hmm",
        "yes no",
        "ok don't",
        "maybe later",
        "don't start yet",
    ],
)
def test_hesitation_and_mixed_signals_ask_again(reply):
    assert classify_plan_reply(reply) == "unclear"


@pytest.mark.parametrize(
    "reply",
    [
        "yes, but use 4 GPUs",
        "no, use the other dataset",
        "continue planning",
        "add a validation step",
        "Focus on storage mounts only",
        "do it with the second model",
    ],
)
def test_replies_that_say_more_revise_the_plan(reply):
    assert classify_plan_reply(reply) == "revise"
