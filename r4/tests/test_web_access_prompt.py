"""ADR-099 D-2: where a session's web access comes from on the CLI.

Order: the ``--web`` flag; on a terminal the question (preselecting
``R4_WEB_ACCESS``, else the session's own choice, else the remembered one);
without one ``R4_WEB_ACCESS``, the session's own choice, the remembered last
choice, else off for a local session and a refusal for a remote start.
Before RS-1 a remote start without ``--web`` silently got the site default
(``public``).
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from r4._cli.web_access_prompt import (
    NO_CHOICE_REMOTE,
    apply_local_web_access,
    ask_web_access,
    choose_web_access,
    notice_for,
    remote_web_access,
)
from r4.companion.web_access import (
    WebAccess,
    WebAccessError,
    make_web_access,
    remember_web_access,
    remembered_for_profile,
    remembered_web_access,
)

_REMEMBERED = make_web_access("allowlist", [".arxiv.org"], source="remembered")


def _answers(*replies: str):
    queue = list(replies)

    def _input(_prompt: str) -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return _input


def _choose(**over: Any) -> WebAccess:
    base: dict[str, Any] = {
        "flag": None,
        "domains": [],
        "remote": False,
        "limits": None,
        "interactive": False,
        "remembered": None,
        "env": {},
    }
    base.update(over)
    return choose_web_access(**base)


# ── the order ───────────────────────────────────────────────────────────────


def test_the_flag_comes_first() -> None:
    access = _choose(
        flag="allowlist",
        domains=["pypi.org,.arxiv.org"],
        env={"R4_WEB_ACCESS": "public"},
        interactive=True,
        remembered=_REMEMBERED,
    )
    assert (access.mode, access.domains, access.source) == (
        "allowlist",
        ("pypi.org", ".arxiv.org"),
        "flag",
    )


def test_on_a_terminal_the_env_preselects_the_question() -> None:
    """RS-1 fix W5: an ambient R4_WEB_ACCESS answered the question for the
    user on a terminal; it now preselects it (Enter takes it)."""
    asked: list[str] = []

    def _enter(prompt: str) -> str:
        asked.append(prompt)
        return ""

    access = _choose(
        env={"R4_WEB_ACCESS": "off"}, interactive=True, remembered=_REMEMBERED, input_fn=_enter
    )
    assert asked and "(Enter = off)" in asked[0]
    assert (access.mode, access.source) == ("none", "prompt")


def test_without_a_terminal_the_env_applies_and_is_named() -> None:
    """RS-1 fix W5: the env choice applied without a word; now one notice."""
    access = _choose(env={"R4_WEB_ACCESS": "off"}, remembered=_REMEMBERED, remote=True)
    assert (access.mode, access.source) == ("none", "env")
    assert notice_for(access) == ("web access: off (from R4_WEB_ACCESS; change it with --web)")


_OWN = make_web_access("none", source="prompt")


def test_a_restart_keeps_the_sessions_own_choice_over_the_remembered_one() -> None:
    """RS-1 fix W2: ``r4 session start`` of a session that chose off sent the
    profile's remembered ``public`` (a silent widening)."""
    access = _choose(current=_OWN, remembered=make_web_access("public", source="remembered"))
    assert (access.mode, access.source) == ("none", "session")
    assert "this session's choice" in str(notice_for(access))
    # an explicit R4_WEB_ACCESS still decides without a terminal
    env = _choose(current=_OWN, env={"R4_WEB_ACCESS": "public"})
    assert env.mode == "public"


def test_on_a_terminal_the_sessions_own_choice_is_preselected() -> None:
    asked: list[str] = []

    def _enter(prompt: str) -> str:
        asked.append(prompt)
        return ""

    remembered = make_web_access("public", source="remembered")
    access = _choose(current=_OWN, remembered=remembered, interactive=True, input_fn=_enter)
    assert "(Enter = off)" in asked[0] and access.mode == "none"


def test_a_terminal_asks_and_preselects_the_remembered_choice() -> None:
    access = _choose(interactive=True, remembered=_REMEMBERED, input_fn=_answers("", ""))
    assert (access.mode, access.domains, access.source) == ("allowlist", (".arxiv.org",), "prompt")


def test_without_a_terminal_the_remembered_choice_applies_with_a_notice() -> None:
    access = _choose(remembered=_REMEMBERED, remote=True)
    assert access is _REMEMBERED
    assert "your last choice" in str(notice_for(access))


def test_a_local_session_without_any_choice_runs_off_and_says_so() -> None:
    access = _choose()
    assert (access.mode, access.source) == ("none", "default_off")
    assert "off (no choice given" in str(notice_for(access))


def test_a_remote_start_without_any_choice_is_refused() -> None:
    with pytest.raises(WebAccessError, match="choose web access: --web off|allowlist|public"):
        _choose(remote=True)
    assert NO_CHOICE_REMOTE == "choose web access: --web off|allowlist|public"


# ── caps and the question ──────────────────────────────────────────────────


_CAPS = {"web": {"allow": ["none", "allowlist"], "domains": [".arxiv.org"]}}


def test_the_flag_cannot_widen_the_caps() -> None:
    with pytest.raises(WebAccessError, match="not allowed"):
        _choose(flag="public", limits=_CAPS)
    with pytest.raises(WebAccessError, match="outside the allowed list"):
        _choose(flag="allowlist", domains=["pypi.org"], limits=_CAPS)
    ok = _choose(flag="allowlist", domains=["export.arxiv.org"], limits=_CAPS)
    assert ok.domains == ("export.arxiv.org",)


def test_the_question_offers_only_allowed_modes(capsys: pytest.CaptureFixture[str]) -> None:
    access = ask_web_access(
        remote=True, limits=_CAPS, remembered=None, input_fn=_answers("2", ".arxiv.org")
    )
    options = capsys.readouterr().err.splitlines()[1:-1]
    assert [line.split()[1] for line in options] == ["off", "allowlist"]
    assert (access.mode, access.domains) == ("allowlist", (".arxiv.org",))


def test_the_question_prefills_suggested_domains() -> None:
    caps = {"web": {"allow": ["none", "allowlist", "public"], "suggested_domains": ["pypi.org"]}}
    access = ask_web_access(remote=True, limits=caps, remembered=None, input_fn=_answers("2", ""))
    assert (access.mode, access.domains) == ("allowlist", ("pypi.org",))


def test_the_question_retries_bad_answers_then_gives_up() -> None:
    access = ask_web_access(
        remote=False, limits=None, remembered=None, input_fn=_answers("9", "off")
    )
    assert access.mode == "none"
    with pytest.raises(WebAccessError):
        ask_web_access(remote=False, limits=None, remembered=None, input_fn=_answers())


# ── remembered, remote helper, headless ─────────────────────────────────────


@pytest.fixture
def store(tmp_path) -> Iterator[Any]:
    from r4.config import ProfileConfigStore

    yield ProfileConfigStore(config_dir=tmp_path)


def test_remember_and_read_back(store: Any) -> None:
    remember_web_access(
        make_web_access("allowlist", ["a.org"], source="prompt"), profile="p", store=store
    )
    back = remembered_for_profile("p", store=store)
    assert back is not None and (back.mode, back.domains, back.source) == (
        "allowlist",
        ("a.org",),
        "remembered",
    )
    assert remembered_web_access(SimpleNamespace(web_access_last="")) is None


def test_config_carries_the_remembered_fields(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from r4.config import Config, ProfileConfigStore

    ProfileConfigStore(config_dir=tmp_path).save(
        "default", web_access_last="allowlist", web_access_domains_last=["a.org"]
    )
    monkeypatch.setattr("r4.config._resolve_config_dir", lambda: tmp_path)
    config = Config.from_env()
    assert config.web_access_last == "allowlist"
    assert config.web_access_domains_last == "a.org"  # stored as a list, read as csv


def test_remote_web_access_asks_with_the_preview_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, Any]] = []

    class _Client:
        config = SimpleNamespace(profile="default", web_access_last="", web_access_domains_last=[])

        def get(self, url: str, **kw: Any) -> Any:
            seen.append((url, kw.get("params")))
            return {"limits": _CAPS}

    monkeypatch.delenv("R4_WEB_ACCESS", raising=False)
    saved: list[Any] = []
    monkeypatch.setattr(
        "r4._cli.web_access_prompt.remember_web_access",
        lambda access, profile: saved.append((access.mode, profile)),
    )
    access = remote_web_access(
        flag=None,
        domains=[],
        client=_Client(),
        project="example-project-b",
        preview_url="/umrs/companion/sessions/s1/venue/policy",
        interactive=True,
        input_fn=_answers("1"),
    )
    assert access.mode == "none"
    assert seen == [("/umrs/companion/sessions/s1/venue/policy", {"project": "example-project-b"})]
    assert saved == [("none", "default")]


def test_headless_takes_the_launch_flag_out_of_the_environment() -> None:
    """RS-1 fix W5: ``r4 --web`` reaches ``r4 -p`` as R4_LAUNCH_WEB_ACCESS,
    taken out of the environment so the agent's shells do not inherit it."""
    session = SimpleNamespace(metadata={})
    config = SimpleNamespace(web_access_last="", web_access_domains_last=[])
    launch = {"R4_LAUNCH_WEB_ACCESS": "allowlist:pypi.org"}
    access = apply_local_web_access(session, config, env={}, launch_env=launch)
    assert (access.mode, access.domains, access.source) == ("allowlist", ("pypi.org",), "flag")
    assert launch == {}


def test_headless_applies_env_then_keeps_a_resumed_choice() -> None:
    session = SimpleNamespace(metadata={})
    config = SimpleNamespace(web_access_last="", web_access_domains_last=[])
    assert apply_local_web_access(session, config, env={}).source == "default_off"
    session.metadata["web_access"] = make_web_access("public", source="prompt").to_metadata()
    assert apply_local_web_access(session, config, env={}).mode == "public"
    explicit = apply_local_web_access(session, config, env={"R4_WEB_ACCESS": "off"})
    assert explicit.mode == "none" and session.metadata["web_access"]["source"] == "env"
