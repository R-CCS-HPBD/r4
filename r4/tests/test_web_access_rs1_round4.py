"""RS-1 round-4 review fixes for the web-access choice (ADR-099 D-2; M-4, M-6).

R4-2 the console bridge's ``session.venue_start`` on a session without the
     user's choice (a local ``default_off`` included) sent ``web_policy=""``
     and left the refusal to the server. A server still on the transition
     setting (``UMRS_SESSION_WEB_CHOICE_REQUIRED=false``) then gave the site
     default (public) to a start nobody chose for, and the mirror had already
     pinned the session to the remote venue. Now refused on the client,
     before the mirror, as ``r4 session start`` and ``team_plan_run`` do.
R4-3 round-3 N2's rule (``default_off`` is no choice) at the two other places
     that reuse a session's choice: the Rich REPL kept a resumed ``r4 -p``
     session's ``default_off`` without asking, and the console launch preset
     (``r4 --web``) did not answer for it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bridge_rpc_harness import Harness, make_harness

from r4._cli.web_access_prompt import apply_repl_web_access
from r4.companion.ui.web_access_bridge import WebAccessBridgeMixin
from r4.companion.web_access import make_web_access, parse_web_access_spec

DEFAULT_OFF = make_web_access("none", source="default_off")


@pytest.fixture(autouse=True)
def _no_ambient_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("R4_WEB_ACCESS", raising=False)
    monkeypatch.delenv("R4_LAUNCH_WEB_ACCESS", raising=False)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return make_harness(tmp_path, monkeypatch)


def _record_posts(harness: Harness) -> list[str]:
    paths: list[str] = []
    original = harness.client.post

    def post(path: str, json: Any = None, **kw: Any) -> Any:
        paths.append(path)
        return original(path, json=json, **kw)

    harness.client.post = post  # type: ignore[method-assign]
    return paths


@pytest.mark.parametrize("web_access", [DEFAULT_OFF.to_metadata(), None])
def test_console_venue_start_without_a_choice_is_refused_before_the_mirror(
    harness: Harness, web_access: dict[str, Any] | None
) -> None:
    if web_access is None:
        harness.session.metadata.pop("web_access", None)
    else:
        harness.session.metadata["web_access"] = web_access
    posts = _record_posts(harness)
    with pytest.raises(ValueError, match=r"^sessionpolicy\.web_choice_required: "):
        harness.server.session_venue_start({"source_refs": ["openai-main"]})
    assert [p for p in posts if p.endswith(("/venue/start", "/snapshot"))] == []
    assert harness.session.metadata.get("venue") == "local"


def test_console_venue_start_with_the_answer_still_starts(harness: Harness) -> None:
    harness.session.metadata["web_access"] = DEFAULT_OFF.to_metadata()
    posts = _record_posts(harness)
    harness.server.session_venue_start({"source_refs": ["openai-main"], "web_policy": "none"})
    assert any(p.endswith("/venue/start") for p in posts)
    assert harness.session.metadata["web_access"]["source"] == "prompt"


def _config() -> SimpleNamespace:
    return SimpleNamespace(profile="default", web_access_last="", web_access_domains_last=[])


def test_the_repl_asks_on_a_resumed_default_off_session() -> None:
    asked: list[str] = []

    def answer(question: str) -> str:
        asked.append(question)
        return "3"

    session = SimpleNamespace(metadata={"web_access": DEFAULT_OFF.to_metadata()})
    access = apply_repl_web_access(
        session, _config(), flag=None, interactive=True, input_fn=answer, env={}
    )
    assert asked
    assert (access.mode, access.source) == ("public", "prompt")
    assert session.metadata["web_access"]["source"] == "prompt"


def test_the_repl_still_keeps_a_users_own_choice() -> None:
    own = make_web_access("allowlist", "a.org", source="prompt").to_metadata()
    session = SimpleNamespace(metadata={"web_access": own})

    def never(_question: str) -> str:
        raise AssertionError("asked")

    access = apply_repl_web_access(session, _config(), flag=None, interactive=True, input_fn=never)
    assert access.domains == ("a.org",)


class _Bridge(WebAccessBridgeMixin):
    def __init__(self, preset: str) -> None:
        self._web_access_preset = parse_web_access_spec(preset, source="flag")


def test_the_launch_preset_answers_for_a_default_off_session() -> None:
    session = SimpleNamespace(metadata={"web_access": DEFAULT_OFF.to_metadata()})
    _Bridge("public")._apply_initial_web_access(session, None)
    assert (session.metadata["web_access"]["mode"], session.metadata["web_access"]["source"]) == (
        "public",
        "flag",
    )


def test_the_launch_preset_never_replaces_a_users_own_choice() -> None:
    own = make_web_access("none", source="prompt").to_metadata()
    session = SimpleNamespace(metadata={"web_access": own})
    _Bridge("public")._apply_initial_web_access(session, None)
    assert session.metadata["web_access"] == own
