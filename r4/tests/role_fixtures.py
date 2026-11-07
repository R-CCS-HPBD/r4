"""Test doubles for the role-turn runtime (moved here in ADR-092 slice 9).

``chat_once_factory`` was the native engine's single-chat turn engine; since
slice 9 every native role turn goes through the run's AgentBroker, so no
production path uses it. Peer tests still inject it as a one-call companion.
"""

from __future__ import annotations

from typing import Any


def chat_once_factory(client: Any) -> Any:
    """Legacy single-chat turn engine for engine adapters whose semantics are
    one call per role per iteration (the SPI native engine's contract).

    The factory keeps the ``model`` it is built with: each role's own model
    reaches ``client.chat`` (it was dropped before ADR-092 slice 1, so every
    role silently ran on the client's default model)."""

    class _ChatOnce:
        def __init__(self, model: str | None) -> None:
            self.model = model

        def on_event(self, cb) -> None:
            return None

        def send(
            self,
            context: str,
            *,
            extra_system: str = "",
            tools: list | None = None,
            stream: bool = False,
            **kwargs: Any,
        ) -> Any:
            del tools, stream, kwargs
            response = client.chat(  # type: ignore[attr-defined]
                message=context,
                system=extra_system,
                temperature=0.7,
                model=self.model,
            )
            return getattr(response, "content", response)

    def _factory(*args: Any, model: str | None = None, **kwargs: Any) -> _ChatOnce:
        del args, kwargs
        return _ChatOnce(model)

    return _factory
