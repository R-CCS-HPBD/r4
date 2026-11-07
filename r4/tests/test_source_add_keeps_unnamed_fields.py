"""``r4 source add`` over an existing name keeps what it does not name (connect#4).

Stabilization 2026-10-21. The docstring always said "updated in place", but
the store dropped the old row wholesale: re-adding ``dup`` from the /connect
wizard with the optional key left blank wiped its working key, provider and description. Now an
update keeps every field the add does not give, as ``r4 source set`` does;
``--replace`` still drops the old row and its credentials, and a re-point to
another deployment still keeps nothing of the old URL.
"""

from __future__ import annotations

import io
from argparse import Namespace

import pytest

from r4._cli.source import SourceCommand
from r4.source import Source, SourceStore

_ADD_DEFAULTS = {
    "kind": "public",
    "api_key": None,
    "api_key_env": None,
    "api_key_stdin": False,
    "allow_insecure_http": False,
    "provider": None,
    "aws_region": None,
    "aws_profile": None,
    "description": None,
    "zmq_host": None,
    "zmq_port": None,
    "zmq_tunnel": False,
    "default_model": None,
    "project": None,
    "replace": False,
}

URL = "https://dup.example/v1"


@pytest.fixture
def store(tmp_path, monkeypatch) -> SourceStore:
    root = tmp_path / "xdg"
    (root / "r4").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("R4_API_KEY", "R4_SOURCE", "R4_PROFILE", "UMRS_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    store = SourceStore()
    store.add(
        Source(
            name="dup",
            kind="public",
            url=URL,
            api_key="WORKING-KEY",
            provider="openai",
            description="mine",
        )
    )
    return store


def _add(**overrides) -> int:
    url = overrides.pop("url", URL)
    return SourceCommand()._handle_add(
        Namespace(name="dup", url=url, **{**_ADD_DEFAULTS, **overrides})
    )


def test_a_re_add_without_a_key_keeps_the_stored_key_provider_and_description(store, capsys):
    assert _add() == 0
    src = store.get("dup")
    assert (src.api_key, src.provider, src.description) == ("WORKING-KEY", "openai", "mine")
    out = capsys.readouterr().out
    assert "Updated source 'dup'" in out
    assert "Kept from the existing row: key, provider, description" in out


def test_a_field_the_re_add_names_wins(store, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("NEW-KEY\n"))
    assert _add(api_key_stdin=True, description="renamed") == 0
    src = store.get("dup")
    assert (src.api_key, src.provider, src.description) == ("NEW-KEY", "openai", "renamed")


def test_replace_still_drops_every_old_field(store):
    assert _add(replace=True) == 0
    src = store.get("dup")
    assert (src.api_key, src.provider, src.description) == (None, None, None)


def test_a_re_point_to_another_host_keeps_no_key(store):
    assert _add(url="https://elsewhere.example/v1") == 0
    assert store.get("dup").api_key is None


def test_a_row_of_another_kind_keeps_nothing(store):
    assert _add(kind="platform") == 0
    assert store.get("dup").api_key is None
