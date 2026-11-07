"""Secondary LLM flows for the R4 agent.

Lightweight LLM calls used for internal agent tasks that should not
go through the main (expensive/slow) model:

- Title generation for conversations
- Content classification / triage
- Answer verification / self-critique
- Summary generation

Inspired by onyx's secondary LLM pattern.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SecondaryLLM:
    """Thin wrapper for secondary (cheap, fast) LLM calls.

    Uses whichever provider is cheapest or explicitly configured as the
    secondary model.

    Args:
        provider: A :class:`Provider` instance to use.
        model: Override model name for the secondary provider.
    """

    def __init__(self, provider: Any, model: Optional[str] = None):
        self._provider = provider
        self._model = model

    def _call(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 256,
        temperature: float = 0.3,
    ) -> str:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            resp = self._provider.chat(
                messages,
                model=self._model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            choices = resp.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return ""
        except Exception as exc:
            logger.warning("Secondary LLM call failed: %s", exc)
            return ""

    def generate_title(self, conversation: str) -> str:
        """Generate a short title for a conversation."""
        return (
            self._call(
                "Generate a concise title (max 8 words) for this conversation. "
                "Return ONLY the title, no quotes or punctuation.",
                conversation[:2000],
                max_tokens=30,
            )
            .strip()
            .strip('"')
            .strip("'")
        )

    def classify_intent(self, user_message: str) -> str:
        """Classify user intent into a category.

        Returns one of: question, task, code, debug, explain, other.
        """
        result = (
            self._call(
                "Classify this message into exactly one category: "
                "question, task, code, debug, explain, other. "
                "Return ONLY the category word.",
                user_message[:1000],
                max_tokens=10,
            )
            .strip()
            .lower()
        )
        valid = {"question", "task", "code", "debug", "explain", "other"}
        return result if result in valid else "other"

    def verify_answer(self, question: str, answer: str) -> bool:
        """Quick self-critique: does the answer address the question?"""
        result = (
            self._call(
                "Does this answer adequately address the question? "
                "Reply with exactly 'yes' or 'no'.",
                f"Question: {question}\n\nAnswer: {answer[:2000]}",
                max_tokens=5,
            )
            .strip()
            .lower()
        )
        return result.startswith("yes")

    def summarize(self, text: str, max_words: int = 100) -> str:
        """Generate a short summary."""
        return self._call(
            f"Summarize the following in at most {max_words} words.",
            text[:4000],
            max_tokens=max_words * 2,
        ).strip()
