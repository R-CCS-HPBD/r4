"""RS-1 round-3 review fix N2 for the web-access choice (ADR-099 D-2; M-4).

A local ``default_off`` (an ``r4 -p`` run nobody chose for: web off, with a
notice) is not a choice. Before: a later non-interactive ``r4 session
start`` reused it as "this session's choice" and the remote sandbox started
with web off that nobody chose, and the console's ``session.venue_start``
sent it as the user's ``none``.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from r4._cli.web_access_prompt import NO_CHOICE_REMOTE, choose_web_access, notice_for
from r4.companion import session_store as store_module
from r4.companion.session_store import CompanionSessionStore
from r4.companion.web_access import WebAccess, WebAccessError, make_web_access

DEFAULT_OFF = make_web_access("none", source="default_off")


@pytest.fixture(autouse=True)
def _no_ambient_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("R4_WEB_ACCESS", raising=False)
    monkeypatch.delenv("R4_LAUNCH_WEB_ACCESS", raising=False)


def _choose(*, remote: bool, remembered: WebAccess | None = None) -> WebAccess:
    return choose_web_access(
        flag=None,
        domains=(),
        remote=remote,
        limits=None,
        interactive=False,
        remembered=remembered,
        env={},
        current=DEFAULT_OFF,
    )


def test_a_remote_start_from_default_off_is_refused() -> None:
    with pytest.raises(WebAccessError, match=NO_CHOICE_REMOTE):
        _choose(remote=True)


def test_a_remote_start_from_default_off_takes_the_remembered_choice() -> None:
    remembered = make_web_access("allowlist", ["a.org"], source="remembered")
    access = _choose(remote=True, remembered=remembered)
    assert (access.mode, access.domains, access.source) == ("allowlist", ("a.org",), "remembered")


def test_a_local_restart_from_default_off_says_no_choice_was_given() -> None:
    access = _choose(remote=False)
    assert (access.mode, access.source) == ("none", "default_off")
    assert "no choice given" in (notice_for(access) or "")


def test_the_question_does_not_preselect_default_off() -> None:
    prompts: list[str] = []

    def enter(text: str) -> str:
        prompts.append(text)
        return ""  # Enter: takes the preselection, when there is one

    with pytest.raises(WebAccessError, match="no web access chosen"):
        choose_web_access(
            flag=None,
            domains=(),
            remote=True,
            limits=None,
            interactive=True,
            remembered=None,
            input_fn=enter,
            env={},
            current=DEFAULT_OFF,
        )
    assert prompts and not any("Enter = off" in p for p in prompts)


class _Client:
    def __init__(self) -> None:
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
        return {"session_id": "s", "state": "provisioning", "web_policy": "none"}

    def get(self, path: str, **_kw: Any) -> Any:
        return {}


def test_session_start_refuses_a_default_off_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from r4._cli import sessions as sessions_cli
    from r4._cli import web_access_prompt

    monkeypatch.setenv("R4_SESSION_SNAPSHOT", "off")
    store_module.set_snapshot_enabled(None)
    monkeypatch.setattr(web_access_prompt, "remembered_web_access", lambda *_a, **_k: None)
    monkeypatch.setattr(web_access_prompt, "is_interactive", lambda: False)
    store = CompanionSessionStore(db_path=tmp_path / "sessions.db")
    client = _Client()
    monkeypatch.setattr(sessions_cli, "_venue_client_and_store", lambda: (client, store))
    session = store.create_session(client, name="r4-p-run")
    session.metadata["web_access"] = DEFAULT_OFF.to_metadata()
    store.save(session)
    args = Namespace(
        session_subcommand="start",
        session_id=session.session_id,
        venue_sources="openai-main",
        venue_default_source="",
        venue_web_policy=None,
        venue_web_domains=None,
        as_json=True,
    )
    assert sessions_cli.SessionsCommand().handle(args) != 0
    assert [p for p, _ in client.posts if p.endswith("/venue/start")] == []
    assert NO_CHOICE_REMOTE in capsys.readouterr().err


# ``session.venue_start`` on a ``default_off`` session: refused on the client
# since round 4 (web R4-2), in test_web_access_rs1_round4.py.
