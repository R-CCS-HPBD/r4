"""``r4 usage`` wiring (cleanup C-2(iii), 2026-10-19).

The handler used to hard-code ``/umrs/usage/*`` — behind the gateway those
404 (the edge's only ``/umrs/`` location is ``/umrs/zmq/``), so a registered
command would still fail for every gateway user. These pins fail on the old
code: the paths must ride ``Client._api_prefix`` (``/gw`` behind the edge,
``/umrs`` in direct-UMRS mode), and the command must be registered in all
three CLI tables.
"""

from __future__ import annotations


def test_usage_registered_in_all_three_cli_tables():
    from r4.cli import _DISPATCH_MODULES, _PRE_REGISTER_COMMANDS, _PRE_REGISTER_MODULES

    assert "usage" in _PRE_REGISTER_MODULES
    assert "usage" in _PRE_REGISTER_COMMANDS
    assert "usage" in _DISPATCH_MODULES


def test_usage_handler_resolves_through_the_registry():
    # `usage` is imported for the registration run (@command decorator) before
    # the registry is queried; isort sorts this block alphabetically.
    from r4._cli import registry as cli_registry
    from r4._cli import usage as usage_mod  # noqa: F401

    handler = cli_registry.get("usage")
    assert type(handler).__name__ == "UsageHandler"


class _StubClient:
    def __init__(self, prefix: str) -> None:
        self._api_prefix = prefix
        self.calls: list[str] = []

    def get(self, path: str, params=None):
        self.calls.append(path)
        return {"rows": [], "records": []}


def test_summary_path_is_mode_aware(capsys):
    from r4._cli.usage import _show_summary

    for prefix, expected in (("/gw", "/gw/usage/summary"), ("/umrs", "/umrs/usage/summary")):
        client = _StubClient(prefix)
        _show_summary(client, {}, as_json=True, group_by="model")
        assert client.calls == [expected]
    capsys.readouterr()


def test_records_and_billing_paths_are_mode_aware(capsys):
    from r4._cli.usage import _show_billing, _show_records

    client = _StubClient("/gw")
    _show_records(client, {}, as_json=True, limit=5)
    _show_billing(client, {}, as_json=True)
    assert client.calls == ["/gw/usage/records", "/gw/usage/billing"]
    capsys.readouterr()
