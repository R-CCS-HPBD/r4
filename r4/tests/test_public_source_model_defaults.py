"""An active public source's own model, not the built-in profile default.

Stabilization 2026-10-21:

- chat#6: with one active public source that declares ``--default-model``,
  the TUI's first turn said "No model selected" (``r4 chat`` worked): the
  initial-model resolver only knew mini-RiVault endpoints and the profile
  default (``rivault-gpt-oss-static``), which the source does not serve.
- slash-a#1 / resize-render#8: ``r4 endpoint list --json`` named the public
  row's model ``cfg.model`` — the built-in default when the source declares
  none — with no source name, so the TUI's /models offered a model nobody
  serves, filed under "unknown".
"""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import MagicMock

from r4._cli.endpoint import _print_public_endpoint_list, _public_endpoint_entry
from r4.companion.model_resolution import resolve_initial_model
from r4.source import Source, get_source_store


def _client() -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            model="rivault-gpt-oss-static", endpoint_url=None, endpoint_provider=None
        ),
        list_endpoints=MagicMock(return_value={"endpoints": []}),
    )


def _use_public(name: str, default_model: str | None) -> Source:
    source = Source(
        name=name,
        kind="public",
        url="http://127.0.0.1:9/v1",
        default_model=default_model,
    )
    store = get_source_store()
    store.add(source)
    store.set_active(name)
    return source


def test_initial_model_is_the_active_public_sources_default(monkeypatch) -> None:
    monkeypatch.delenv("R4_MODEL", raising=False)
    monkeypatch.delenv("R4_SOURCE", raising=False)
    monkeypatch.delenv("R4_GATEWAY_URL", raising=False)
    _use_public("fakechat", "fake-a")

    assert resolve_initial_model(_client(), explicit=None, saved=None) == "fake-a@fakechat"


def test_no_source_default_still_leaves_the_model_unset(monkeypatch) -> None:
    monkeypatch.delenv("R4_MODEL", raising=False)
    monkeypatch.delenv("R4_SOURCE", raising=False)
    monkeypatch.delenv("R4_GATEWAY_URL", raising=False)
    _use_public("fakechat", None)

    assert resolve_initial_model(_client(), explicit=None, saved=None) is None


def test_public_endpoint_row_names_the_source_and_never_the_builtin_default(monkeypatch) -> None:
    monkeypatch.delenv("R4_MODEL", raising=False)
    cfg = SimpleNamespace(
        model="rivault-gpt-oss-static",
        endpoint_provider=None,
        endpoint_url="http://127.0.0.1:9/v1",
    )
    bare = Source(name="fakesrc", kind="public", url="http://127.0.0.1:9/v1")
    declared = Source(
        name="fakemd", kind="public", url="http://127.0.0.1:9/v1", default_model="fake-md"
    )

    assert _public_endpoint_entry(cfg, bare)["model"] is None
    assert _public_endpoint_entry(cfg, bare)["_source"] == "fakesrc"
    assert _public_endpoint_entry(cfg, declared)["model"] == "fake-md"
    # No source (a profile-level endpoint): the profile's model, as before.
    assert _public_endpoint_entry(cfg)["model"] == "rivault-gpt-oss-static"


def test_endpoint_list_json_carries_the_active_source(monkeypatch, capsys) -> None:
    monkeypatch.delenv("R4_MODEL", raising=False)
    monkeypatch.delenv("R4_SOURCE", raising=False)
    monkeypatch.delenv("R4_GATEWAY_URL", raising=False)
    _use_public("fakesrc", None)
    cfg = SimpleNamespace(
        model="rivault-gpt-oss-static",
        endpoint_provider=None,
        endpoint_url="http://127.0.0.1:9/v1",
    )

    assert _print_public_endpoint_list(Namespace(as_json=True, source=None), cfg) == 0
    row = json.loads(capsys.readouterr().out)["endpoints"][0]
    assert row["_source"] == "fakesrc"
    assert row["model"] is None
