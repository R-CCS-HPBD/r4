"""Side questions (``/btw``, Claude-Code parity — 2026-09-24).

A side question is answered from the CURRENT conversation only: no tools, no
new context, and neither the question nor the answer is added to the session
history. It can run while a turn is in flight (the bridge answers it on a
worker thread). The model call rides the normal client, so the data-class
gate (sensitive projects never reach public providers) still applies.
"""

from __future__ import annotations

from typing import Any

SIDE_SYSTEM = (
    "You answer a quick side question about an ongoing conversation between a "
    "scientist and an AI research assistant. Use ONLY the conversation below; "
    "you have no tools and must not propose to run anything. If the answer is "
    "not in the conversation, say so briefly. Be concise."
)

_CONTEXT_MESSAGES = 30
_PER_MESSAGE_CHARS = 2000


def build_side_prompt(
    messages: list[Any],
    question: str,
    *,
    limit: int = _CONTEXT_MESSAGES,
    per_message: int = _PER_MESSAGE_CHARS,
) -> str:
    """The conversation tail (user/assistant turns) plus the side question."""
    lines: list[str] = []
    for message in messages[-limit:]:
        role = str(getattr(message, "role", "") or "")
        if role not in ("user", "assistant"):
            continue
        content = str(getattr(message, "content", "") or "").strip()
        if len(content) > per_message:
            content = content[:per_message] + " …"
        lines.append(f"[{role}] {content}")
    transcript = "\n".join(lines) or "(the conversation is empty)"
    return f"Conversation so far:\n{transcript}\n\nSide question: {question}"


def answer_side_question(session: Any, question: str) -> str:
    """Ask the session's model the side question; the session is not modified."""
    question = question.strip()
    if not question:
        raise ValueError("question is required")
    client = getattr(session, "client", None)
    if client is None:
        raise RuntimeError("no model client on this session")
    model = getattr(getattr(session, "agent", None), "model", None) or None
    prompt = build_side_prompt(list(getattr(session, "messages", []) or []), question)
    response = client.chat(prompt, system=SIDE_SYSTEM, temperature=0.3, model=model)
    return str(getattr(response, "content", "") or "").strip()


__all__ = ["SIDE_SYSTEM", "answer_side_question", "build_side_prompt"]
