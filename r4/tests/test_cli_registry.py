"""Tests for the Plan 2 CLI registry scaffolding."""

from __future__ import annotations

import argparse

import pytest

from r4._cli import CLIRegistry, CommandHandler
from r4._cli.registry import registry as global_registry


class _FakeCommand(CommandHandler):
    name = "fake-test"
    help = "fake"

    def register(self, subparsers):
        return subparsers.add_parser(self.name, help=self.help)

    def handle(self, args):
        return 0


def test_registry_register_and_get() -> None:
    reg = CLIRegistry()
    reg.register(_FakeCommand)
    assert "fake-test" in reg
    assert reg.get("fake-test").name == "fake-test"
    assert len(reg) == 1


def test_registry_rejects_duplicate() -> None:
    reg = CLIRegistry()
    reg.register(_FakeCommand)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_FakeCommand)


def test_registry_rejects_empty_name() -> None:
    class Unnamed(CommandHandler):
        name = ""
        help = "x"

        def register(self, subparsers):
            return subparsers.add_parser("x")

        def handle(self, args):
            return 0

    reg = CLIRegistry()
    with pytest.raises(ValueError, match="non-empty 'name'"):
        reg.register(Unnamed)


def test_registry_iterates_handlers() -> None:
    reg = CLIRegistry()
    reg.register(_FakeCommand)
    names = [h.name for h in reg]
    assert names == ["fake-test"]


def test_command_decorator_uses_global_registry() -> None:
    # The health handler is registered via @command at import time.
    import r4._cli.health  # noqa: F401

    assert "health" in global_registry
    health = global_registry.get("health")
    assert health.name == "health"
    assert health.help


def test_health_handler_registers_subparser() -> None:
    from r4._cli.health import HealthCommand

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    created = HealthCommand().register(subparsers)
    assert isinstance(created, argparse.ArgumentParser)
    # Parser should accept "health" without error.
    ns = parser.parse_args(["health"])
    assert ns.command == "health"
