"""T1: read-only CLI views use a bounded read timeout (30 s, R4_TIMEOUT wins).

Stabilization cycle 2 (2026-10-21): against a gateway that accepted the
connection and never answered, ``r4 usage``, ``r4 map``, ``r4 governance
audit`` and 17 more read-only views were still silent at 150 s (the config's
300 s request timeout). Mutating calls keep their timeouts.
Live repro: point r4 at a gateway that accepts and never answers.
"""

from __future__ import annotations

import sys

import pytest

from r4._cli.governance import GovernanceCommand
from r4._cli.knowledge import KnowledgeCommand
from r4._cli.read_timeout import READ_ONLY_TIMEOUT_S, command_path, is_read_only_view
from r4.config import Config, get_config, set_config


@pytest.fixture(autouse=True)
def _fresh_config(monkeypatch):
    monkeypatch.delenv("R4_TIMEOUT", raising=False)
    set_config(Config.from_env())
    yield
    set_config(Config.from_env())


def _timeout_seen_by(monkeypatch, handler_cls, argv: list[str]) -> float:
    """Run ``r4 <argv>`` through the real CLI; return the read timeout an
    HTTP client built inside the handler would use."""
    import r4.cli
    from r4.client import Client

    seen: dict[str, float] = {}

    def _handle(self, args):
        client = Client()
        with client.http._client() as http:
            seen["read"] = http.timeout.read
        return 0

    monkeypatch.setattr(handler_cls, "handle", _handle)
    monkeypatch.setattr(sys, "argv", ["r4", *argv])
    with pytest.raises(SystemExit) as done:
        r4.cli.main()
    assert done.value.code == 0
    return seen["read"]


def test_a_read_only_view_waits_30s_not_300s(monkeypatch) -> None:
    assert get_config().timeout == 300
    assert _timeout_seen_by(monkeypatch, GovernanceCommand, ["governance", "audit"]) == 30


def test_r4_timeout_overrides_the_bound(monkeypatch) -> None:
    monkeypatch.setenv("R4_TIMEOUT", "45")
    set_config(Config.from_env())
    assert _timeout_seen_by(monkeypatch, GovernanceCommand, ["governance", "audit"]) == 45


def test_a_mutating_call_keeps_its_timeout(monkeypatch) -> None:
    seen = _timeout_seen_by(monkeypatch, KnowledgeCommand, ["knowledge", "graph-create", "g1"])
    assert seen == 300


def test_command_paths_of_the_measured_views() -> None:
    from r4.cli import _build_parser

    parser = _build_parser()

    def path(*argv: str) -> tuple[str, ...]:
        return command_path(parser, parser.parse_args(list(argv)))

    assert path("governance", "audit") == ("governance", "audit")
    assert path("session") == ("session", "list")  # positional verb, default list
    assert path("--source", "x", "map") == ("map",)
    assert path("data", "assets", "stats") == ("data", "assets", "stats")
    assert is_read_only_view(path("knowledge", "docs", "list"))
    assert not is_read_only_view(path("session", "remove", "s1"))
    assert not is_read_only_view(path("knowledge", "graph-create", "g1"))
    assert READ_ONLY_TIMEOUT_S == 30
