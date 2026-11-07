"""Typed decision questions and answers (Jev / TypeSafe ``systemone`` shape).

Three question types, keyed by caller-chosen names:

- ``choice`` — one of named options; ``criteria`` maps option key → description
  (``None`` = the key itself is shown);
- ``score``  — an ordered rubric, lowest first; the answer is the expected
  zero-based level;
- ``noul``   — a yes/no statement; the answer is P(true). Optional
  ``criteria`` ``{"true": ..., "false": ...}`` define the two sides.

Answers carry the full distribution. ``confidence`` is 1 − normalized
entropy: distribution concentration, not calibrated correctness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Union

#: Candidate codes are single letters A..Z (one token each in the Qwen vocab).
MAX_OPTIONS = 26
MAX_QUESTIONS = 64

State = Union[str, dict, list]


class DecisionError(ValueError):
    """A request the decision model cannot evaluate (raised before any call)."""


@dataclass(frozen=True)
class ChoiceQuestion:
    instructions: Any
    criteria: dict[str, str | None]
    type: str = field(default="choice", init=False)


@dataclass(frozen=True)
class ScoreQuestion:
    instructions: Any
    criteria: list[str]
    type: str = field(default="score", init=False)


@dataclass(frozen=True)
class NoulQuestion:
    instructions: Any
    yes: str = "Yes"
    no: str = "No"
    type: str = field(default="noul", init=False)


Question = Union[ChoiceQuestion, ScoreQuestion, NoulQuestion]


def _nonempty_text(value: Any, what: str) -> None:
    if isinstance(value, str):
        if not value.strip():
            raise DecisionError(f"{what} must be nonempty")
    elif not isinstance(value, (dict, list)) or not value:
        raise DecisionError(f"{what} must be a nonempty string, object or array")


def _build_choice(name: str, instructions: Any, criteria: Any) -> Question:
    if not isinstance(criteria, dict):
        raise DecisionError(f"{name}: choice criteria must map option keys to descriptions")
    return ChoiceQuestion(instructions, dict(criteria))


def _build_score(name: str, instructions: Any, criteria: Any) -> Question:
    if not isinstance(criteria, list):
        raise DecisionError(f"{name}: score criteria must be an ordered list of levels")
    return ScoreQuestion(instructions, list(criteria))


def _build_noul(name: str, instructions: Any, criteria: Any) -> Question:
    criteria = criteria if criteria is not None else {}
    if not isinstance(criteria, dict) or set(criteria) - {"true", "false"}:
        raise DecisionError(f"{name}: noul criteria may only define 'true' and 'false'")
    return NoulQuestion(
        instructions, yes=criteria.get("true", "Yes"), no=criteria.get("false", "No")
    )


_BUILDERS = {"choice": _build_choice, "score": _build_score, "noul": _build_noul}


def _from_json(name: str, raw: Any) -> Question:
    if not isinstance(raw, dict):
        raise DecisionError(f"{name}: a question must be an object")
    extra = set(raw) - {"type", "instructions", "criteria"}
    if extra:
        raise DecisionError(f"{name}: unsupported keys {sorted(extra)}")
    builder = _BUILDERS.get(raw.get("type"))
    if builder is None:
        raise DecisionError(f"{name}: type must be one of choice, score, noul")
    return builder(name, raw.get("instructions"), raw.get("criteria"))


def parse_question(name: str, raw: Any) -> Question:
    """One question from its JSON shape (as an agent or a config sends it)."""
    if isinstance(raw, (ChoiceQuestion, ScoreQuestion, NoulQuestion)):
        question: Question = raw
    else:
        question = _from_json(name, raw)
    _validate(name, question)
    return question


def _validate_choice(name: str, question: ChoiceQuestion) -> None:
    if not 2 <= len(question.criteria) <= MAX_OPTIONS:
        raise DecisionError(f"{name}: choice needs 2–{MAX_OPTIONS} options")
    if any(not isinstance(key, str) or not key.strip() for key in question.criteria):
        raise DecisionError(f"{name}: option keys must be nonempty strings")
    if any(text is not None and not isinstance(text, str) for text in question.criteria.values()):
        raise DecisionError(f"{name}: option descriptions must be strings or null")


def _validate_score(name: str, question: ScoreQuestion) -> None:
    if not 2 <= len(question.criteria) <= MAX_OPTIONS:
        raise DecisionError(f"{name}: score needs 2–{MAX_OPTIONS} levels")
    if any(not isinstance(t, str) or not t.strip() for t in question.criteria):
        raise DecisionError(f"{name}: score levels must be nonempty strings")


def _validate_noul(name: str, question: NoulQuestion) -> None:
    if not isinstance(question.yes, str) or not isinstance(question.no, str):
        raise DecisionError(f"{name}: noul criteria must be strings")


def _validate(name: str, question: Question) -> None:
    if not isinstance(name, str) or not name.strip():
        raise DecisionError("question names must be nonempty strings")
    _nonempty_text(question.instructions, f"{name}: instructions")
    if isinstance(question, ChoiceQuestion):
        _validate_choice(name, question)
    elif isinstance(question, ScoreQuestion):
        _validate_score(name, question)
    else:
        _validate_noul(name, question)


def parse_questions(raw: Any) -> dict[str, Question]:
    if not isinstance(raw, dict) or not raw:
        raise DecisionError("questions must be a nonempty object keyed by question name")
    if len(raw) > MAX_QUESTIONS:
        raise DecisionError(f"at most {MAX_QUESTIONS} questions per evaluation")
    return {name: parse_question(name, q) for name, q in raw.items()}


def option_keys(question: Question) -> list[str]:
    """Answer keys in candidate order (A, B, C, ...)."""
    if isinstance(question, ChoiceQuestion):
        return list(question.criteria)
    if isinstance(question, ScoreQuestion):
        return [str(i) for i in range(len(question.criteria))]
    return ["false", "true"]  # Nimble's training contract: A=false, B=true


def confidence(probabilities: list[float]) -> float:
    entropy = -math.fsum(p * math.log(p) for p in probabilities if p > 0)
    return min(1.0, max(0.0, 1 - entropy / math.log(len(probabilities))))


def answer_for(question: Question, probabilities: list[float]) -> dict[str, Any]:
    """The typed answer for one question from its candidate distribution."""
    keys = option_keys(question)
    distribution = dict(zip(keys, probabilities, strict=True))
    if isinstance(question, NoulQuestion):
        return {"type": "noul", "noul": distribution["true"]}
    if isinstance(question, ScoreQuestion):
        return {
            "type": "score",
            "score": math.fsum(i * p for i, p in enumerate(probabilities)),
            "legend": {str(i): text for i, text in enumerate(question.criteria)},
            "probabilities": distribution,
            "confidence": confidence(probabilities),
        }
    return {
        "type": "choice",
        "choice": max(distribution, key=distribution.__getitem__),
        "probabilities": distribution,
        "confidence": confidence(probabilities),
    }
