"""CLI command registry (Plan 2)."""

from __future__ import annotations

from typing import Dict, Iterator, Type

from .base import CommandHandler


class CLIRegistry:
    """Registry of :class:`CommandHandler` subclasses.

    Handlers are registered via the :func:`command` class decorator. The
    top-level CLI entry point in ``r4.cli.main`` iterates this registry to
    wire subparsers and dispatch parsed args.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, CommandHandler] = {}

    def register(self, handler_cls: Type[CommandHandler]) -> Type[CommandHandler]:
        """Register ``handler_cls`` (instantiated lazily)."""
        instance = handler_cls()
        if not instance.name:
            raise ValueError(
                f"CommandHandler {handler_cls.__name__} must declare a non-empty 'name'"
            )
        if instance.name in self._handlers:
            raise ValueError(f"CommandHandler '{instance.name}' already registered")
        self._handlers[instance.name] = instance
        return handler_cls

    def get(self, name: str) -> CommandHandler:
        """Return the handler registered for ``name``."""
        return self._handlers[name]

    def names(self) -> Iterator[str]:
        """Iterate registered command names."""
        return iter(self._handlers.keys())

    def __iter__(self) -> Iterator[CommandHandler]:
        return iter(self._handlers.values())

    def __len__(self) -> int:
        return len(self._handlers)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._handlers


#: Global CLI registry instance.
registry = CLIRegistry()


def command(cls: Type[CommandHandler]) -> Type[CommandHandler]:
    """Class decorator that registers ``cls`` with the global :data:`registry`."""
    return registry.register(cls)
