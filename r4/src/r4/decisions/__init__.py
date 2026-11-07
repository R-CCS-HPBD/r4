"""Decision model (ADR-076): fast typed judgments beside the chat model.

A Jev-style decision model answers bounded questions about a state —
``choice`` (pick an option), ``score`` (place on a rubric), ``noul`` (P(true))
— with full probability distributions, from candidate-token logprobs
instead of generated text. R4 uses it for gates, routing and triage inside
agent workflows; the platform serves it like any other model (UMRS endpoint,
catalog name, ``/gw/llm``).

    from r4.decisions import DecisionModel
    dm = DecisionModel(client, "Bespoke-Nimble-9B")
    ev = dm.evaluate("I was charged twice", {
        "refund": {"type": "noul", "instructions": "Does the user ask for a refund?"},
    })
    ev.answers["refund"]["noul"]   # 0.97
"""

from .evaluator import DecisionModel, Evaluation, decision_model_for
from .questions import DecisionError, parse_questions

__all__ = [
    "DecisionError",
    "DecisionModel",
    "Evaluation",
    "decision_model_for",
    "parse_questions",
]
