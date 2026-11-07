"""``decision_evaluate`` — the agent's door to the decision model (ADR-076).

Registered when the client config names a ``decision_model`` (on by default
since 2026-10-03) and the platform serves it. The tool
returns typed probabilistic judgments; it performs no action and grants no
permission — the agent (or the calling policy) decides what to do with them.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from r4.client import Client

_DESCRIPTION = (
    "Ask the platform decision model for fast, typed judgments about a state "
    "(text, JSON object or array). Each question is one of: "
    '`choice` {"type":"choice","instructions":...,"criteria":{"key":"description",...}} '
    "→ the most probable key with all option probabilities; "
    '`score` {"type":"score","instructions":...,"criteria":["lowest level",...,"highest"]} '
    "→ expected zero-based level; "
    '`noul` {"type":"noul","instructions":"a yes/no statement"} → P(true). '
    "Use it for bounded judgments — routing, triage, gates, 'is X the case' checks — "
    "before spending a full model turn. Results are predictions, not proofs, and "
    "never authorize an action by themselves; near 0.5 means genuinely uncertain. "
    "Do not use it for arithmetic, dates or exact comparisons — compute those."
)


def _decision_evaluate(client: "Client"):
    def _run(state: Any, questions: dict[str, Any]) -> str:
        from r4.decisions import DecisionError, decision_model_for

        model = decision_model_for(client)
        if model is None:
            return json.dumps({"type": "decision_error", "code": "no_decision_model"})
        try:
            from r4.service_roles import safe_model_label

            evaluation = model.evaluate(state, questions).to_dict()
            # ADR-106 D-10: the user sees which model judged.
            return json.dumps(
                {"type": "decision", "model": safe_model_label(model.model), **evaluation}
            )
        except DecisionError as exc:
            return json.dumps(
                {"type": "decision_error", "code": "bad_request", "message": str(exc)}
            )
        except Exception as exc:  # noqa: BLE001 - the endpoint may be down or relaying
            return json.dumps(
                {
                    "type": "decision_error",
                    "code": "decision_model_unavailable",
                    "message": str(exc),
                }
            )

    return {
        "name": "decision_evaluate",
        "description": _DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "state": {
                    "description": "The evidence to judge: text, a JSON object or an array.",
                    "anyOf": [{"type": "string"}, {"type": "object"}, {"type": "array"}],
                },
                "questions": {
                    "type": "object",
                    "description": "1–64 questions keyed by your own names (choice/score/noul).",
                    "additionalProperties": {"type": "object"},
                },
            },
            "required": ["state", "questions"],
        },
        "execute": _run,
        "concurrency_safe": True,
        # On by default (/decision-model off removes it): stays visible
        # whatever skill is active, so the agent can use it in any workflow.
        "always_on": True,
    }


def build_decision_tools(client: "Client") -> list[dict[str, Any]]:
    """The decision tool when a decision model is configured and served, else nothing."""
    from r4.decisions.settings import configured_decision_model, decision_model_available

    model = configured_decision_model(client)
    if not model or not decision_model_available(client, model):
        return []
    return [_decision_evaluate(client)]
