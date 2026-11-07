"""In-memory chat session state for the TUI.

The session is intentionally client-local. Durable persistence (UMRS context
storage, optimistic locking, multi-device sync) is out of scope for the
chat TUI and lives in :mod:`r4.companion` / a future Plan 5 follow-up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

from ..chat_types import ChatMessage


@dataclass
class Attachment:
    """A file attached via ``@path`` mention."""

    path: str
    size_bytes: int
    content: str  # already size-capped and injection-scanned upstream


@dataclass
class Turn:
    """A single user → assistant exchange, for transcript export / /save."""

    user: str
    assistant: str
    attachments: List[Attachment] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ChatSession:
    """Minimal in-memory chat session.

    Keeps both the raw OpenAI-style message list (used for API calls) and a
    higher-level turn log (used for rendering / export).
    """

    model: str
    system_prompt: Optional[str] = None
    messages: List[ChatMessage] = field(default_factory=list)
    turns: List[Turn] = field(default_factory=list)
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _secure: bool = field(default=False, repr=False)

    @property
    def secure(self) -> bool:
        """Whether the session is in secure mode (immutable once set)."""
        return self._secure

    def enable_secure(self) -> None:
        """Activate secure mode. Cannot be deactivated once set."""
        self._secure = True

    def __post_init__(self) -> None:
        if self.system_prompt and not self.messages:
            self.messages.append(ChatMessage.system(self.system_prompt))

    # --- message helpers -----------------------------------------------------

    @staticmethod
    def _compose_user_content(text: str, attachments: Optional[List[Attachment]] = None) -> str:
        """Render the user turn content, inlining attachment payloads safely.

        Keeping attachments inside the user message avoids emitting extra
        mid-conversation ``system`` messages, which some OpenAI-compatible
        backends reject or handle inconsistently.
        """
        if not attachments:
            return text

        parts: List[str] = [text, "", "Attached local file context:"]
        for att in attachments:
            body = att.content if att.content.endswith("\n") else f"{att.content}\n"
            parts.extend(
                [
                    f"File: {att.path} ({att.size_bytes} bytes)",
                    "----- BEGIN FILE -----",
                    body.rstrip("\n"),
                    "----- END FILE -----",
                    "",
                ]
            )
        return "\n".join(parts).rstrip()

    def add_user(self, text: str, attachments: Optional[List[Attachment]] = None) -> None:
        """Append a user turn with any attachment contents embedded inline."""
        self.messages.append(ChatMessage.user(self._compose_user_content(text, attachments)))

    def add_assistant(self, text: str, usage: Optional[dict[str, Any]] = None) -> None:
        """Append an assistant turn and roll token usage into the session."""
        self.messages.append(ChatMessage.assistant(text))
        if usage:
            self.total_prompt_tokens += int(usage.get("prompt_tokens") or 0)
            self.total_completion_tokens += int(usage.get("completion_tokens") or 0)

    def set_system(self, prompt: str) -> None:
        """Replace (or install) the system prompt, keeping user history."""
        self.system_prompt = prompt
        # Drop any pre-existing system messages to avoid duplicates.
        self.messages = [m for m in self.messages if m.role != "system"]
        self.messages.insert(0, ChatMessage.system(prompt))

    def add_system(self, text: str) -> None:
        """Append an additional system message (e.g. skill guidance)."""
        self.messages.append(ChatMessage.system(text))

    def clear(self) -> None:
        """Reset to an empty session (keeps system prompt if set)."""
        self.messages = [ChatMessage.system(self.system_prompt)] if self.system_prompt else []
        self.turns = []
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def drop_last_turn(self) -> Optional[Turn]:
        """Remove the last user/assistant pair — used by ``/retry``.

        Leaves the session-level system prompt in place so that ``/retry`` can
        re-issue the same user message under the same chat rules.
        """
        if not self.turns:
            return None
        turn = self.turns.pop()
        # Pop trailing assistant, then trailing user, skipping anything else.
        for target in ("assistant", "user"):
            while self.messages and self.messages[-1].role != target:
                if self.messages[-1].role in ("assistant", "user"):
                    break
                self.messages.pop()
            if self.messages and self.messages[-1].role == target:
                self.messages.pop()
        return turn

    # --- export --------------------------------------------------------------

    def to_markdown(self) -> str:
        """Render the transcript as a readable markdown document."""
        out: List[str] = [
            "# Chat transcript\n",
            f"- model: `{self.model}`",
            f"- started: {self.started_at.isoformat()}",
            f"- turns: {len(self.turns)}",
            f"- tokens (prompt/completion): "
            f"{self.total_prompt_tokens} / {self.total_completion_tokens}",
            "",
        ]
        if self.system_prompt:
            out.append(f"_System prompt_:\n\n```\n{self.system_prompt}\n```\n")
        for i, turn in enumerate(self.turns, start=1):
            out.append(f"## Turn {i}")
            if turn.attachments:
                attached = ", ".join(a.path for a in turn.attachments)
                out.append(f"_attached: {attached}_")
            out.append(f"\n**User:**\n\n{turn.user}\n")
            out.append(f"**Assistant ({turn.model}):**\n\n{turn.assistant}\n")
        return "\n".join(out)


__all__ = ["Attachment", "ChatSession", "Turn"]
