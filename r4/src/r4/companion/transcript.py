"""Session transcript for headless and benchmark sessions (W6, gap row D-52).

Before 2026-10-05 a headless CompanionSession kept only the prompt and the final
text: tool calls, results, nudges and per-round model usage lived inside the
loop and were lost, so a failed benchmark case could not be replayed. A
TranscriptWriter subscribes to the agent's events and the session's usage hook
and appends one JSON line per event:

    {"ts", "session_id", "kind": "turn_start" | "turn_end" | "model_round" | <event type>,
     "message", "data"}

Enabled by ``R4_TRANSCRIPT_PATH`` (one file) or ``R4_TRANSCRIPT_DIR``
(``<dir>/<session_id>.jsonl``). Text fields are capped and passed through the
token redactor; tool results carry the agent's own 2,000-char event preview.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .tool_summaries import _REDACTED, redact_tokens

_TEXT_CAP = 8_000


# A value stored under a credential-named key is redacted whatever its shape
# (the token patterns miss opaque keys; security sweep 2026-10-21).
_SECRET_KEY = re.compile(r"(api_?key|token|secret|password)$", re.IGNORECASE)


def _clip_field(key: str, value: Any) -> Any:
    if _SECRET_KEY.search(key) and isinstance(value, str) and value:
        return _REDACTED
    return _clip(value)


def _clip(value: Any) -> Any:
    if isinstance(value, str):
        text = redact_tokens(value)
        return (
            text
            if len(text) <= _TEXT_CAP
            else text[:_TEXT_CAP] + f"…[+{len(text) - _TEXT_CAP} chars]"
        )
    if isinstance(value, dict):
        return {str(k): _clip_field(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip(v) for v in value[:50]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clip(str(value))


def transcript_path_for(session_id: str) -> Path | None:
    """Where this session's transcript goes, or ``None`` when not enabled."""
    explicit = os.environ.get("R4_TRANSCRIPT_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    directory = os.environ.get("R4_TRANSCRIPT_DIR", "").strip()
    if directory:
        return Path(directory).expanduser() / f"{session_id}.jsonl"
    return None


class TranscriptWriter:
    """Append-only JSONL transcript; safe to call from tool worker threads."""

    def __init__(self, path: Path, session_id: str) -> None:
        self.path = path
        self.session_id = session_id
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind: str, message: str = "", data: dict[str, Any] | None = None) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "session_id": self.session_id,
            "kind": kind,
            "message": _clip(message or ""),
            "data": _clip(data or {}),
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        try:
            with self._lock, self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass  # a transcript must never break the session

    def on_event(self, event: Any) -> None:
        """Agent event listener (``ResearchCompanion.on_event``)."""
        kind = getattr(getattr(event, "event_type", None), "value", None) or str(
            getattr(event, "event_type", "event")
        )
        self.write(kind, str(getattr(event, "message", "") or ""), getattr(event, "data", None))

    def model_round(self, call_type: str, payload: dict[str, Any]) -> None:
        """Session usage hook: one line per model call."""
        self.write("model_round", call_type, payload)


def attach_transcript(session: Any) -> TranscriptWriter | None:
    """Create and subscribe a writer when a transcript is enabled for ``session``."""
    session_id = str(getattr(session, "session_id", "") or "session")
    path = transcript_path_for(session_id)
    if path is None:
        return None
    try:
        writer = TranscriptWriter(path, session_id)
    except OSError:
        return None
    agent = getattr(session, "agent", None)
    if agent is not None and hasattr(agent, "on_event"):
        agent.on_event(writer.on_event)
    return writer
