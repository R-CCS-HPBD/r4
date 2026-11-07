"""Reading a reply to a plan that awaits confirmation (2026-10-02).

The old check looked for a confirmation word anywhere, and before any
refusal. So "no, don't do it" ran the plan (it contains "do it"),
"yes, but use 4 GPUs" ran it without the change, and "continue planning"
ran it ("continue"). Running a plan is the one reading that must never be
a guess:

- execute: a SHORT reply made only of confirmation words ("yes",
  "go ahead", "run it please", "lgtm");
- cancel: a short refusal ("no", "don't run it", "never mind");
- unclear: hesitation or mixed signals ("wait", "not yet", "yes no"). The
  plan stays pending and the user is asked again;
- revise: anything that says more ("yes, but use 4 GPUs", "no, use the
  other dataset", "add a validation step"). The plan is redone with it,
  and the user is asked again.

Pure and English-only. A decision model can read other phrasings later,
with this as the fallback (ADR-076).
"""

from __future__ import annotations

import re
from typing import Literal

PlanReply = Literal["execute", "cancel", "revise", "unclear"]

#: Unmistakable yes-words (a refusal word next to one makes a reply mixed).
_STRONG_CONFIRM = frozenset(
    {
        "yes",
        "yeah",
        "yep",
        "yup",
        "y",
        "ok",
        "okay",
        "sure",
        "confirm",
        "confirmed",
        "approve",
        "approved",
        "proceed",
        "lgtm",
        "alright",
    }
)
#: Action verbs that confirm on their own but also appear in refusals ("don't run it").
_WEAK_CONFIRM = frozenset({"run", "do", "go", "execute", "continue", "ship", "start"})
_REFUSAL = frozenset(
    {"no", "nope", "nah", "cancel", "stop", "abort", "nevermind", "forget", "scrap", "quit", "drop"}
)
_NEGATION = frozenset({"don't", "dont", "not", "never", "do not"})
_HOLD = frozenset({"wait", "hold", "maybe", "hmm", "unsure", "later", "yet", "perhaps", "pause"})
#: Words a short reply may carry besides its markers without saying more.
_FILLER = frozenset(
    {
        "it",
        "please",
        "thanks",
        "thank",
        "you",
        "now",
        "the",
        "plan",
        "that",
        "this",
        "good",
        "sounds",
        "looks",
        "great",
        "fine",
        "right",
        "correct",
        "perfect",
        "ahead",
        "mind",
        "on",
        "with",
        "a",
        "for",
        "me",
        "go",
        "all",
        "just",
        "let's",
        "lets",
        "us",
        "one",
    }
)
_SHORT_VOCAB = _STRONG_CONFIRM | _WEAK_CONFIRM | _REFUSAL | _NEGATION | _HOLD | _FILLER

#: The notice an unclear reply gets (the plan stays pending).
UNCLEAR_REPLY_NOTICE = (
    "Not sure whether to run the plan. Reply **yes** to run it, **no** to cancel, "
    "or say what to change."
)


def _tokens(message: str) -> list[str]:
    text = (message or "").lower().replace("’", "'").replace("do not", "do_not")
    words = re.findall(r"[a-z_']+", text)
    return ["do not" if w == "do_not" else w.strip("'") or w for w in words]


def _refusal_reading(present: set[str], short: bool) -> PlanReply:
    if not short:
        return "revise"  # "no, use the other dataset"
    return "unclear" if present & _HOLD else "cancel"  # "not yet" keeps the plan


def _confirmation_reading(present: set[str], short: bool) -> PlanReply:
    if not short:
        return "revise"  # "yes, but with 4 GPUs" — the change must be planned first
    return "unclear" if present & _HOLD else "execute"


def classify_plan_reply(message: str) -> PlanReply:
    """execute / cancel / revise / unclear for a reply to a pending plan."""
    present = set(_tokens(message))
    if not present:
        return "unclear"
    short = present <= _SHORT_VOCAB
    refused = bool(present & (_REFUSAL | _NEGATION))
    if refused and present & _STRONG_CONFIRM:
        return "unclear"  # "yes no", "ok don't" — never guess which one counts
    if refused:
        return _refusal_reading(present, short)
    if present & (_STRONG_CONFIRM | _WEAK_CONFIRM):
        return _confirmation_reading(present, short)
    return "unclear" if short and present & _HOLD else "revise"
