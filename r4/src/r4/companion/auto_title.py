"""Automatic session titles: after a session's first completed turn, ask the
model for a short conversation title.

Console behavior (bridge-wired, 2026-09-29): sessions created by the companion
store carry a timestamped default name (``Session 2026-09-29 22:40``); the
tab strip and sessions pane otherwise show a machine date instead of the
topic. After the first completed turn, the bridge asks the model (one extra
call, response-only, no tools) for a <= 8-word title and renames the session.

Guardrails:
- only timestamped default names are replaced — a user-set title is never
  stomped;
- the ask runs off the turn path (daemon thread at the bridge); failures are
  silent logs, never a broken turn;
- title text is sanitized to one line; markdown/quotes/trailing punctuation
  are stripped; output is capped at ``_MAX_WORDS`` words/`_MAX_CHARS` chars.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

_TIMESTAMPED_DEFAULT = re.compile(r"^Session \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
_MAX_WORDS = 8
_MAX_CHARS = 60
_PROMPT_SNIPPET_CHARS = 800

_SYSTEM_PROMPT = (
    "You write very short conversation titles. Answer with the title text "
    "only: no quotes, no leading dashes, no trailing punctuation, at most "
    "8 words, in the same language as the conversation shown."
)


def is_default_name(name: object) -> bool:
    """True for the store's timestamped default ("Session 2026-09-29 22:40")."""
    return bool(isinstance(name, str) and _TIMESTAMPED_DEFAULT.match(name.strip()))


def sanitize_title(raw: object) -> str | None:
    """Normalize a model reply into one displayable line, else None."""
    if not isinstance(raw, str):
        return None
    line = raw.strip().splitlines()[0] if raw.strip() else ""
    text = line.strip().strip("\"'`“”‘’").strip()
    text = text.lstrip("-•*．、　 ").strip()
    text = re.sub(r"\s+", " ", text).rstrip(".!?:;。！？：；").strip()
    if not text:
        return None
    words = text.split(" ")
    if len(words) > _MAX_WORDS:
        text = " ".join(words[:_MAX_WORDS])
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS].rstrip()
    return text or None


def build_title_messages(first_user: str, first_assistant: str) -> list[dict[str, str]]:
    """Prompt set for the title ask (bounded excerpts, reply-only)."""
    user = (
        f"User request:\n{first_user[:_PROMPT_SNIPPET_CHARS]}\n\n"
        f"Assistant reply (excerpt):\n{first_assistant[:_PROMPT_SNIPPET_CHARS]}\n\n"
        "Write the title for this conversation."
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _first_nonempty(messages: list[Any], role: str) -> str | None:
    for message in messages:
        if getattr(message, "role", "") != role:
            continue
        content = str(getattr(message, "content", "") or "")
        if content.strip():
            return content
    return None


def first_exchange_contents(messages: list[Any]) -> tuple[str, str] | None:
    """First (user, assistant) content pair, or None (title ask skipped)."""
    user_text = _first_nonempty(messages, "user")
    assistant_text = _first_nonempty(messages, "assistant")
    if user_text is None or assistant_text is None:
        return None
    return user_text, assistant_text


def _choice_content(raw: Any) -> str | None:
    """Content string out of the agent's normalized response dict."""
    if not isinstance(raw, dict):
        return None
    choices = raw.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else None


def generate_session_title(
    session: Any,
    request_chat: Callable[[list[dict[str, Any]], None], dict[str, Any]],
) -> str | None:
    """Ask the model for a title; None when guards or the call fail.

    ``request_chat`` is the research companion's chat method bound to this
    session's model (no tools): ``session.agent._chat``. Tests inject doubles.
    """
    name = getattr(session, "name", None)
    if not is_default_name(name):
        return None
    pair = first_exchange_contents(list(getattr(session, "messages", []) or []))
    if pair is None or request_chat is None:
        return None
    try:
        raw = request_chat(build_title_messages(pair[0], pair[1]), None)
    except Exception as exc:  # noqa: BLE001 — a title ask never breaks a turn
        logger.debug("auto title request failed: %s", exc)
        return None
    return sanitize_title(_choice_content(raw))
