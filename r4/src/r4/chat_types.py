"""Chat DTOs used by :class:`r4.client.Client`.

Extracted from ``client.py`` so the client module stays under the project's
module-size lint budget. Importing from ``r4.client`` still works – the
module re-exports these names for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .errors import APIError


def validate_chat_completion_response(data: dict[str, Any]) -> None:
    """Raise :class:`APIError` when a completion payload cannot be parsed."""
    if not data:
        raise APIError(
            "Empty response from chat completion (gateway or upstream endpoint may be unavailable)",
        )
    if "_raw" in data and "choices" not in data:
        raw = data.get("_raw", "")
        snippet = raw if isinstance(raw, str) else str(raw)
        snippet = snippet[:300].strip()
        raise APIError(
            "Non-JSON response from chat completion (upstream may be down or returning HTML)",
            response_body=snippet or data,
        )
    choices = data.get("choices")
    if choices is not None and len(choices) == 0:
        raise APIError("Chat completion returned no choices", response_body=data)


@dataclass
class ChatMessage:
    """A message in a chat conversation."""

    role: str  # "user", "assistant", "system"
    content: str
    name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to API format."""
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        return d

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> "ChatMessage":
        return cls(role="assistant", content=content)

    @classmethod
    def system(cls, content: str) -> "ChatMessage":
        return cls(role="system", content=content)


@dataclass
class Usage:
    """Token usage information."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatResponse:
    """Response from a chat completion."""

    content: str
    model: str
    finish_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)
    raw_response: Optional[dict[str, Any]] = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "ChatResponse":
        """Create from API response."""
        validate_chat_completion_response(data)
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        usage_data = data.get("usage", {}) or {}

        return cls(
            content=message.get("content", ""),
            model=data.get("model", ""),
            finish_reason=choice.get("finish_reason", "stop"),
            usage=Usage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            ),
            raw_response=data,
        )


def coerce_chat_message(message: Any) -> ChatMessage:
    """Normalize supported chat message shapes into :class:`ChatMessage`."""
    if isinstance(message, ChatMessage):
        return message

    if isinstance(message, dict):
        role = message.get("role")
        content = message.get("content")
        name = message.get("name")
    else:
        role = getattr(message, "role", None)
        if hasattr(role, "value"):
            role = role.value
        content = getattr(message, "content", None)
        name = getattr(message, "name", None)

    if role is None or content is None:
        raise TypeError("Chat messages must provide role and content")

    return ChatMessage(role=str(role), content=str(content), name=name)


# Back-compat alias
_coerce_chat_message = coerce_chat_message


__all__ = [
    "ChatMessage",
    "ChatResponse",
    "Usage",
    "coerce_chat_message",
]
