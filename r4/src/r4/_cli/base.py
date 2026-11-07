"""CommandHandler base class (Plan 2)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from argparse import ArgumentParser, Namespace


class CommandHandler(ABC):
    """Abstract base class for a CLI command group.

    Subclasses declare a top-level command name (``name``) and a short
    one-line ``help`` string. ``register(subparsers)`` wires argparse
    subparsers; ``handle(args)`` is called with the parsed namespace.
    """

    #: Top-level subcommand name, e.g. ``"chat"`` or ``"rag"``.
    name: str = ""

    #: One-line help shown in ``r4 --help``.
    help: str = ""

    @abstractmethod
    def register(self, subparsers) -> ArgumentParser:  # pragma: no cover - abstract
        """Register this command's argparse subparser.

        Returns:
            The :class:`argparse.ArgumentParser` created via
            ``subparsers.add_parser(self.name, ...)``.
        """
        raise NotImplementedError

    @abstractmethod
    def handle(self, args: Namespace) -> int:  # pragma: no cover - abstract
        """Execute the command given parsed ``args``.

        Returns:
            Process exit code (``0`` on success).
        """
        raise NotImplementedError
