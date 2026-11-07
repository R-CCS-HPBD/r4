"""ADR-101 local tool sandbox: the setting, its resolution order and the profile store."""

from __future__ import annotations

import json
import os

import pytest

from r4.companion.sandbox import (
    SANDBOX_DETAIL_LINES,
    SANDBOX_ENV,
    SANDBOX_LAUNCH_ENV,
    SANDBOX_NOTICE_OFF,
    SANDBOXED_ENV,
    SandboxError,
    SandboxSetting,
    UserSandboxPolicy,
    make_sandbox_setting,
    parse_sandbox_flag,
    profile_sandbox_default,
    remember_sandbox_default,
    resolve_sandbox,
    sandbox_from_env,
    take_launch_sandbox,
    user_sandbox_policy,
)


@pytest.fixture(autouse=True)
def _no_venue_no_sandbox(monkeypatch):
    for name in (SANDBOXED_ENV, SANDBOX_ENV, SANDBOX_LAUNCH_ENV, "R4_VENUE_SESSION_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("raw", "mode"),
    [("on", "on"), ("TRUE", "on"), ("1", "on"), ("yes", "on"), ("off", "off"), ("0", "off")],
)
def test_parse_sandbox_flag_accepts_on_off_aliases(raw, mode):
    assert parse_sandbox_flag(raw) == mode


def test_parse_sandbox_flag_refuses_unknown_value():
    with pytest.raises(SandboxError) as info:
        parse_sandbox_flag("maybe")
    assert info.value.code == "sandbox.refused"
    assert str(info.value).startswith("sandbox.refused: ")


def test_setting_metadata_round_trip_and_labels():
    setting = make_sandbox_setting("on", source="slash", now="2026-10-30T00:00:00+00:00")
    again = SandboxSetting.from_metadata({"sandbox": setting.to_metadata()})
    assert again == setting
    assert again.enabled and again.label() == "on"
    assert SandboxSetting("on", "", "inherited").label() == "on (inherited)"
    assert SandboxSetting("off", "", "venue").label() == "off (remote sandbox)"
    assert SandboxSetting.from_metadata({"sandbox": {"mode": "sideways"}}) is None
    assert SandboxSetting.from_metadata({}) is None


def test_resolve_default_is_off_default_off():
    setting = resolve_sandbox(metadata={}, env={})
    assert (setting.mode, setting.source) == ("off", "default_off")


def test_resolve_order_launch_session_env_profile():
    launch = make_sandbox_setting("off", source="flag")
    session = {"sandbox": {"mode": "on", "chosen_at": "t", "source": "slash"}}
    env = {SANDBOX_ENV: "off"}
    assert resolve_sandbox(metadata=session, env=env, launch=launch).source == "flag"
    assert resolve_sandbox(metadata=session, env=env, profile_default="off").source == "slash"
    assert resolve_sandbox(metadata={}, env=env, profile_default="on").source == "env"
    from_profile = resolve_sandbox(metadata={}, env={}, profile_default="on")
    assert (from_profile.mode, from_profile.source) == ("on", "profile")


def test_resolve_ignores_bad_env_value():
    setting = resolve_sandbox(metadata={}, env={SANDBOX_ENV: "bogus"}, profile_default="on")
    assert setting.source == "profile"
    with pytest.raises(SandboxError):
        sandbox_from_env({SANDBOX_ENV: "bogus"})


def test_resolve_venue_worker_is_off_even_with_session_on(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("t")
    monkeypatch.setenv("R4_VENUE_SESSION_TOKEN_FILE", str(token))
    session = {"sandbox": {"mode": "on", "source": "slash"}}
    launch = make_sandbox_setting("on", source="flag")
    setting = resolve_sandbox(metadata=session, env={}, launch=launch)
    assert (setting.mode, setting.source) == ("off", "venue")


def test_resolve_inherited_when_already_sandboxed():
    session = {"sandbox": {"mode": "off", "source": "slash"}}
    setting = resolve_sandbox(metadata=session, env={SANDBOXED_ENV: "bwrap"})
    assert (setting.mode, setting.source) == ("on", "inherited")


def test_take_launch_sandbox_pops_the_variable():
    env = {SANDBOX_LAUNCH_ENV: "on", "OTHER": "1"}
    setting = take_launch_sandbox(env)
    assert setting is not None and setting.mode == "on" and setting.source == "flag"
    assert SANDBOX_LAUNCH_ENV not in env
    assert take_launch_sandbox(env) is None


def test_take_launch_sandbox_pops_invalid_value_too():
    env = {SANDBOX_LAUNCH_ENV: "garbage"}
    assert take_launch_sandbox(env) is None
    assert env == {}


def test_profile_default_round_trip_in_config_json(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert profile_sandbox_default("p1") == ""
    remember_sandbox_default("on", "p1")
    assert profile_sandbox_default("p1") == "on"
    assert profile_sandbox_default("p2") == ""
    stored = json.loads((tmp_path / "r4" / "config.json").read_text())
    assert stored["profiles"]["p1"]["sandbox_default"] == "on"
    assert oct(os.stat(tmp_path / "r4" / "config.json").st_mode & 0o777) == "0o600"


def test_user_sandbox_policy_reads_config_json(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from r4.config import ProfileConfigStore

    policy = {
        "extra_write_paths": ["/data/x"],
        "extra_deny_read_paths": "~/.secret",
        "env_keep": [],
    }
    ProfileConfigStore().save("default", sandbox_policy=policy)
    assert user_sandbox_policy() == UserSandboxPolicy(("/data/x",), ("~/.secret",), ())


def test_notice_names_both_ways_to_turn_it_on():
    assert "/sandbox on" in SANDBOX_NOTICE_OFF
    assert "r4 --sandbox on" in SANDBOX_NOTICE_OFF
    assert any("Network" in line for line in SANDBOX_DETAIL_LINES)
