"""ADR-099 D-2: the web-access choice on every surface that opens a session.

``r4 session start`` and ``r4 team plan --venue server`` send the choice
(``web_policy`` + ``web_allow_domains``) and refuse to start without one;
``r4 --web`` reaches the console's bridge and ``r4 -p``; the bridge takes
the console's answer at ``initialize``, keeps it on the session, exposes it
in the snapshot and through ``session.web_access_*``, previews the caps and
sends the choice with ``session.venue_start``.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest
from bridge_rpc_harness import VENUE_ROW, Harness, make_harness

from r4.companion import session_store as store_module
from r4.companion.session_store import CompanionSessionStore
from r4.companion.web_access import WebAccess, make_web_access


@pytest.fixture(autouse=True)
def _no_ambient_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("R4_WEB_ACCESS", raising=False)


# ── r4 session start ────────────────────────────────────────────────────────


class _Client:
    """Captures the start POST; the preview route is absent (404)."""

    def __init__(self) -> None:
        from types import SimpleNamespace

        self.config = SimpleNamespace(
            profile="default",
            gateway_url="http://gw.test",
            project_id="proj-x",
            model="m",
            auto_retry=False,
            web_access_last="",
            web_access_domains_last=[],
        )
        self.posts: list[tuple[str, Any]] = []

    def post(self, path: str, json: Any = None, **_kw: Any) -> dict[str, Any]:
        self.posts.append((path, json))
        if path.endswith("/venue/start"):
            return {"session_id": "s", "state": "provisioning", "web_policy": json["web_policy"]}
        return {"status": "ok", "applied": True}

    def get(self, path: str, **_kw: Any) -> Any:
        return {}


def _start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **args: Any):
    from r4._cli import sessions as sessions_cli

    monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
    store_module.set_snapshot_enabled(None)
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    client = _Client()
    monkeypatch.setattr(sessions_cli, "_venue_client_and_store", lambda: (client, store))
    session = store.create_session(client, name="remote-me")
    namespace = Namespace(
        session_subcommand="start",
        session_id=session.session_id,
        venue_sources="openai-main",
        venue_default_source="",
        as_json=True,
        **args,
    )
    rc = sessions_cli.SessionsCommand().handle(namespace)
    starts = [body for path, body in client.posts if path.endswith("/venue/start")]
    return rc, starts, store.get_session_record(session.session_id)["metadata"]


def test_session_start_sends_the_allowlist_domains(tmp_path, monkeypatch) -> None:
    rc, starts, metadata = _start(
        tmp_path,
        monkeypatch,
        venue_web_policy="allowlist",
        venue_web_domains=[".arxiv.org,pypi.org", "ARXIV.org."],
    )
    assert rc == 0
    assert starts[0]["web_policy"] == "allowlist"
    assert starts[0]["web_allow_domains"] == [".arxiv.org", "pypi.org", "arxiv.org"]
    # the worker restores the same choice from the mirrored copy (D-9 parity)
    assert metadata["web_access"]["mode"] == "allowlist"
    assert metadata["web_access"]["source"] == "flag"


def test_session_start_off_sends_none(tmp_path, monkeypatch) -> None:
    rc, starts, _ = _start(tmp_path, monkeypatch, venue_web_policy="off")
    assert rc == 0 and starts[0]["web_policy"] == "none" and starts[0]["web_allow_domains"] == []


def test_session_start_without_a_choice_and_without_a_terminal_is_refused(
    tmp_path, monkeypatch, capsys
) -> None:
    rc, starts, _ = _start(tmp_path, monkeypatch, venue_web_policy=None)
    assert rc == 2 and starts == []
    assert "choose web access: --web off|allowlist|public" in capsys.readouterr().err


def test_session_start_uses_the_env_choice(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("R4_WEB_ACCESS", "allowlist:pypi.org")
    rc, starts, _ = _start(tmp_path, monkeypatch, venue_web_policy=None)
    assert rc == 0 and starts[0]["web_allow_domains"] == ["pypi.org"]


# ── r4 team plan --venue server ─────────────────────────────────────────────


def test_team_plan_server_venue_sends_the_choice() -> None:
    from types import SimpleNamespace

    from r4._cli import team_plan

    args = SimpleNamespace(
        venue_sources="openai-main",
        venue_default_source=None,
        venue_web_policy="allowlist",
        venue_web_domains=["pypi.org"],
    )
    kwargs = team_plan._venue_kwargs(args, _Client())
    assert kwargs["web_policy"] == "allowlist" and kwargs["web_allow_domains"] == ["pypi.org"]

    from r4.teams.server_venue import _venue_body

    body = _venue_body(["s"], "", "allowlist", {}, ["pypi.org"])
    assert body["web_allow_domains"] == ["pypi.org"]


def test_team_plan_server_venue_without_a_choice_is_invalid() -> None:
    from types import SimpleNamespace

    from r4._cli import team_plan
    from r4.companion.web_access import WebAccessError

    args = SimpleNamespace(
        venue_sources="s", venue_default_source=None, venue_web_policy=None, venue_web_domains=None
    )
    with pytest.raises(WebAccessError):
        team_plan._venue_kwargs(args, _Client())


# ── r4 --web ────────────────────────────────────────────────────────────────


def test_root_web_flag_reaches_the_console_through_the_launch_env(monkeypatch) -> None:
    """RS-1 fix W5: a launch-only variable (the consumer takes it out), not
    R4_WEB_ACCESS, which every shell the agent opened inherited."""
    import os

    from r4 import cli

    args = cli._build_parser().parse_args(
        ["--web", "allowlist", "--web-domain", ".arxiv.org", "--web-domain", "pypi.org"]
    )
    try:
        cli._apply_web_flag(args)
        assert os.environ["R4_LAUNCH_WEB_ACCESS"] == "allowlist:.arxiv.org,pypi.org"
        assert "R4_WEB_ACCESS" not in os.environ
    finally:
        os.environ.pop("R4_LAUNCH_WEB_ACCESS", None)


def test_root_web_flag_refuses_an_empty_allowlist(monkeypatch, capsys) -> None:
    from r4 import cli

    args = cli._build_parser().parse_args(["--web", "allowlist"])
    with pytest.raises(SystemExit) as stop:
        cli._apply_web_flag(args)
    assert stop.value.code == 2 and "at least one domain" in capsys.readouterr().err


# ── the bridge ──────────────────────────────────────────────────────────────


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return make_harness(tmp_path, monkeypatch)


def test_a_new_session_has_no_choice_in_its_snapshot(harness: Harness) -> None:
    web = harness.server.snapshot()["session"]["web_access"]
    assert web["mode"] == ""


def test_initialize_records_the_consoles_answer(harness: Harness) -> None:
    state = harness.server.initialize(
        {"venue": "local", "web_access": {"mode": "allowlist", "domains": [".arxiv.org"]}}
    )
    web = state["session"]["web_access"]
    assert (web["mode"], web["domains"], web["source"]) == ("allowlist", [".arxiv.org"], "prompt")
    remembered = harness.server.session_web_access_get({})["remembered"]
    assert remembered["mode"] == "allowlist"


def test_the_launch_preset_fills_a_session_without_a_choice(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from r4.companion.ui.web_access_bridge import launch_web_access

    monkeypatch.setenv("R4_LAUNCH_WEB_ACCESS", "off")  # r4 --web off
    harness.server._web_access_preset = launch_web_access(None)
    state = harness.server.initialize({"venue": "local"})
    assert state["session"]["web_access"]["mode"] == "none"
    assert harness.server.session_web_access_get({})["preset"] is True
    assert launch_web_access("allowlist:a.org").source == "flag"
    assert launch_web_access("allowlist") is None  # invalid: logged, the console asks


def test_web_access_set_applies_to_the_session_and_saves(harness: Harness) -> None:
    out = harness.server.session_web_access_set({"mode": "public"})
    assert out["web_access"]["mode"] == "public"
    stored = harness.store.load(harness.sid, harness.client)
    assert WebAccess.from_metadata(stored.metadata).mode == "public"
    with pytest.raises(ValueError, match="web_access.invalid"):
        harness.server.session_web_access_set({"mode": "allowlist", "domains": []})


def test_public_without_a_search_server_says_how_to_add_one(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-102: the answer carries a notice the console shows."""
    from r4.agent import web_search
    from r4.companion.ui.web_access_bridge import SEARCH_SERVER_NOTICE

    monkeypatch.setattr(web_search, "configured", lambda: False)
    out = harness.server.session_web_access_set({"mode": "public"})
    assert out["notice"] == SEARCH_SERVER_NOTICE
    assert "r4 search-server up" in out["notice"]
    assert "notice" not in harness.server.session_web_access_set({"mode": "off"})
    monkeypatch.setattr(web_search, "configured", lambda: True)
    assert "notice" not in harness.server.session_web_access_set({"mode": "public"})


def test_public_without_a_search_server_offers_to_start_one_where_it_can(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-11-01: the console asks to start the local SearXNG only when a
    container engine is installed; without one it shows the notice alone."""
    from r4.agent import searxng_local, web_search

    monkeypatch.setattr(web_search, "configured", lambda: False)
    monkeypatch.setattr(searxng_local, "container_engine", lambda: "podman")
    assert harness.server.session_web_access_set({"mode": "public"})["search_server_offer"]
    monkeypatch.setattr(searxng_local, "container_engine", lambda: None)
    out = harness.server.session_web_access_set({"mode": "public"})
    assert out["notice"] and out["search_server_offer"] is False
    assert "search_server_offer" not in harness.server.session_web_access_set({"mode": "off"})


def test_a_running_remote_session_needs_a_restart(harness: Harness) -> None:
    from r4.companion.ui.bridge_server import _project_runtime, _venue_runtime_projection

    harness.session.metadata["venue"] = "frontend"
    _project_runtime(harness.session, _venue_runtime_projection(VENUE_ROW))
    with pytest.raises(ValueError, match=r"^venue\.restart_required: "):
        harness.server.session_web_access_set({"mode": "off"})


def test_venue_start_sends_the_sessions_choice(harness: Harness) -> None:
    sent: list[dict[str, Any]] = []
    original = harness.client.post

    def post(path: str, json: Any = None, **kw: Any) -> Any:
        if path.endswith("/venue/start"):
            sent.append(json)
            return dict(VENUE_ROW)
        return original(path, json=json, **kw)

    harness.client.post = post  # type: ignore[method-assign]
    harness.session.metadata["web_access"] = make_web_access(
        "allowlist", ["pypi.org"], source="prompt"
    ).to_metadata()
    harness.server.session_venue_start({"source_refs": ["openai-main"]})
    assert sent[0]["web_policy"] == "allowlist" and sent[0]["web_allow_domains"] == ["pypi.org"]


def test_venue_policy_previews_the_caps_for_the_project(harness: Harness) -> None:
    seen: list[Any] = []

    def get(path: str, **kw: Any) -> Any:
        seen.append((path, kw.get("params")))
        return {"choice_required": True, "limits": {"web": {"allow": ["none"]}}, "current": None}

    harness.client.get = get  # type: ignore[method-assign]
    out = harness.server.session_venue_policy({"project": "example-project-b"})
    assert (
        out["limits"] == {"web": {"allow": ["none"]}} and out["project_id"] == "example-project-b"
    )
    assert seen[0][0].endswith(f"/companion/sessions/{harness.sid}/venue/policy")
    assert seen[0][1] == {"project": "example-project-b"}


def test_venue_policy_without_the_route_offers_every_mode(harness: Harness) -> None:
    from r4.errors import APIError

    def get(path: str, **_kw: Any) -> Any:
        raise APIError("not found", status_code=404)

    harness.client.get = get  # type: ignore[method-assign]
    out = harness.server.session_venue_policy({})
    assert out["limits"] is None and "error" not in out


def test_bridge_parses_the_web_access_launch_arg() -> None:
    from r4.companion.ui.bridge_server import _parse_args

    assert _parse_args(["--web-access", "allowlist:a.org"]).web_access == "allowlist:a.org"


def test_rpc_contract_types_the_new_verbs() -> None:
    from r4.companion.ui import rpc_schema

    for verb in ("session.web_access_get", "session.web_access_set", "session.venue_policy"):
        assert verb in rpc_schema.RPC_RESULT_TYPES
    assert json.dumps(rpc_schema.result_schema("session.venue_policy"))
