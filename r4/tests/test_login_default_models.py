"""`r4 login` stores the site's service-tier default models (ADR-106 D-9).

connect-info advertises ``default_models`` (role -> ``svc-*`` alias, plus an
ordered ``fallback``). Login keeps the map on the platform source and fills
the source's ``default_model`` from the chat role, but only while the user
has not chosen one: the user's own choice always wins. Before this, a new
user's default model was the dead built-in ``rivault-gpt-oss-static``.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

from r4._cli import login as login_cli
from r4.source import Source

GW = "https://univ.example"


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, body: dict) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: _Reply(json.dumps(body).encode())
    )


def _src(default_model=None, default_models=None):
    return SimpleNamespace(default_model=default_model, default_models=default_models)


ADVERTISED = {"chat": "svc-chat", "decision": "svc-decision", "fallback": ["svc-fast"]}


def test_fetch_carries_the_map(monkeypatch) -> None:
    _serve(monkeypatch, {"zmq": {"control_port": 5555}, "default_models": ADVERTISED})
    assert login_cli._fetch_connect_info(GW)["_default_models"] == ADVERTISED


def test_fetch_without_the_field_carries_nothing(monkeypatch) -> None:
    _serve(monkeypatch, {"zmq": {"control_port": 5555}})
    assert "_default_models" not in login_cli._fetch_connect_info(GW)


def test_unset_default_model_takes_the_chat_alias() -> None:
    src = _src()
    changed = login_cli._apply_default_models(src, {"_default_models": ADVERTISED})
    assert (changed, src.default_model, src.default_models) == (True, "svc-chat", ADVERTISED)


def test_the_users_choice_wins() -> None:
    src = _src(default_model="kimi-k3")
    login_cli._apply_default_models(src, {"_default_models": ADVERTISED})
    assert (src.default_model, src.default_models["chat"]) == ("kimi-k3", "svc-chat")


def test_absent_field_changes_nothing() -> None:
    src = _src(default_models={"chat": "svc-chat"})
    assert (login_cli._apply_default_models(src, {}), src.default_models) == (
        False,
        {"chat": "svc-chat"},
    )


def test_malformed_entries_are_dropped() -> None:
    src = _src()
    login_cli._apply_default_models(
        src, {"_default_models": {"chat": 5, "fast": " svc-fast ", "fallback": ["a", 3, " "]}}
    )
    expected = {"fast": "svc-fast", "fallback": ["a"]}
    assert (src.default_model, src.default_models) == (None, expected)


def test_unchanged_map_reports_no_change() -> None:
    src = _src(default_model="svc-chat", default_models=dict(ADVERTISED))
    assert login_cli._apply_default_models(src, {"_default_models": ADVERTISED}) is False


def test_source_round_trips_the_map() -> None:
    src = Source(name="rccs", kind="platform", url=GW, default_models=ADVERTISED)
    assert Source.from_dict(src.to_dict()).default_models == ADVERTISED
