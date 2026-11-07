"""Model calls a tool makes inside an agent turn are metered (ADR-092 D-7).

A plan agent's model rounds are charged through the companion's
``usage_callback`` and refused through its ``pre_llm_call``. Some tools call
the model themselves, on the client they were built with: ``test_fix_loop``
asks for a patch on every failing iteration, ``search_agentic`` plans and
synthesises. Those calls went past the run's meter, the run's ceilings and
the lead session's caps (the slice-1 open item of 2026-10-10).

So the catalog an agent (or a team role, MD-7) is offered is built on a
:class:`MeteredToolClient`: the same client, whose ``chat`` and
``chat_completion`` first ask ``guard`` (which raises to refuse the call)
and then ``charge`` the measured usage. Everything else is the wrapped
client's own attribute, read through. A streamed reply is charged when it is
exhausted, with usage estimated from its text (a stream reports none).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from .brokers import usage_tokens

TOOL_CALL_TYPE = "subagent_tool"
_CHARS_PER_TOKEN = 4


class MeteredToolClient:
    """``client`` with its model calls guarded and charged (see the module doc).

    ``guard(call_type)`` runs before every model call and raises to refuse
    it; ``charge(call_type, payload)`` receives the companion's usage payload
    shape (``prompt_tokens``, ``completion_tokens``, ``total_tokens``,
    ``estimated``, ``model``) after it.
    """

    def __init__(
        self,
        client: Any,
        *,
        charge: Callable[[str, dict[str, Any]], None],
        guard: Callable[[str], None],
    ) -> None:
        self._metered_client = client
        self._metered_charge = charge
        self._metered_guard = guard

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_metered_"):
            raise AttributeError(name)
        return getattr(self._metered_client, name)

    @property
    def metered_client(self) -> Any:
        """The wrapped client."""
        return self._metered_client

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        return self._metered("chat", args, kwargs)

    def chat_completion(self, *args: Any, **kwargs: Any) -> Any:
        return self._metered("chat_completion", args, kwargs)

    def chat_stream(self, *args: Any, **kwargs: Any) -> Any:
        self._metered_guard(TOOL_CALL_TYPE)
        chunks = self._metered_client.chat_stream(*args, **kwargs)
        return self._charged_stream(chunks, args, kwargs)

    def _metered(self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        self._metered_guard(TOOL_CALL_TYPE)
        reply = getattr(self._metered_client, method)(*args, **kwargs)
        if kwargs.get("stream"):
            return self._charged_stream(reply, args, kwargs)
        content = str(getattr(reply, "content", "") or "")
        self._metered_charge(TOOL_CALL_TYPE, _payload(reply, _sent_text(args, kwargs), content))
        return reply

    def _charged_stream(
        self, chunks: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Iterator[Any]:
        text: list[str] = []
        try:
            for chunk in chunks:
                text.append(str(chunk))
                yield chunk
        finally:
            payload = _payload(None, _sent_text(args, kwargs), "".join(text))
            self._metered_charge(TOOL_CALL_TYPE, {**payload, "model": kwargs.get("model") or ""})


def _payload(reply: Any, sent: str, content: str) -> dict[str, Any]:
    """The usage of one call; estimated (four characters a token) when the
    reply reports none, so an unmeasured call is never charged as free."""
    usage = getattr(reply, "usage", None)
    tokens_in = usage_tokens(usage, "prompt_tokens")
    tokens_out = usage_tokens(usage, "completion_tokens")
    estimated = tokens_in + tokens_out == 0
    if estimated:
        tokens_in = len(sent) // _CHARS_PER_TOKEN + 1
        tokens_out = len(content) // _CHARS_PER_TOKEN + 1
    return {
        "prompt_tokens": tokens_in,
        "completion_tokens": tokens_out,
        "total_tokens": tokens_in + tokens_out,
        "estimated": estimated,
        "model": str(getattr(reply, "model", "") or ""),
    }


def _sent_text(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """Everything a call sends, as text, for an estimate."""
    parts: list[Any] = [*args, kwargs.get("message"), kwargs.get("system")]
    parts.append(kwargs.get("messages"))
    return " ".join(str(part) for part in parts if part)


__all__ = ["TOOL_CALL_TYPE", "MeteredToolClient"]
