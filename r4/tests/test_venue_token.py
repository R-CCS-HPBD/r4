"""R1-5: venue token seam (secret file) + spawn env sanitization."""

from __future__ import annotations

import pytest

from r4 import venue
from r4.providers.openai_compat import OpenAICompatibleProvider


@pytest.fixture
def venue_env(tmp_path, monkeypatch):
    token_file = tmp_path / "session_token"
    token_file.write_text("sess-token-deadbeef-1\n")
    monkeypatch.setenv("R4_VENUE_SESSION_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-win")
    return str(token_file)


class TestVenueDetection:
    def test_not_in_venue_without_file(self, monkeypatch):
        monkeypatch.delenv("R4_VENUE_SESSION_TOKEN_FILE", raising=False)
        assert not venue.in_venue()
        assert venue.read_venue_token() is None

    def test_missing_file_means_not_venue(self, monkeypatch):
        monkeypatch.setenv("R4_VENUE_SESSION_TOKEN_FILE", "/nonexistent")
        assert not venue.in_venue()

    @pytest.mark.usefixtures("venue_env")
    def test_token_read_strip(self):
        assert venue.in_venue()
        assert venue.read_venue_token() == "sess-token-deadbeef-1"


class TestSpawnEnvSanitization:
    def test_venue_drops_token_file_ref_and_any_session_token_env(self, venue_env):
        env = {
            "R4_VENUE_SESSION_TOKEN_FILE": venue_env,
            "MY_SESSION_TOKEN": "x",
            "PATH": "/usr/bin",
            "HTTPS_PROXY": "http://egress:3128",
        }
        cleaned = venue.sanitize_tool_spawn_env(env)
        assert "R4_VENUE_SESSION_TOKEN_FILE" not in cleaned
        assert "MY_SESSION_TOKEN" not in cleaned
        assert cleaned["PATH"] == "/usr/bin"
        assert cleaned["HTTPS_PROXY"] == "http://egress:3128"  # proxy env is harmless

    def test_venue_drops_provider_api_keys_keeps_gateway_token(self, venue_env):
        env = {
            "OPENAI_API_KEY": "sk-openai",
            "ANTHROPIC_API_KEY": "sk-ant",
            "GEMINI_API_KEY": "g-key",
            "R4_ENDPOINT_API_KEY": "ep-key",
            "R4_GATEWAY_TOKEN": "hatch-needs-this",
            "R4_GATEWAY_URL": "http://gw:8000",
        }
        cleaned = venue.sanitize_tool_spawn_env(env)
        assert cleaned == {
            "R4_GATEWAY_TOKEN": "hatch-needs-this",
            "R4_GATEWAY_URL": "http://gw:8000",
        }

    def test_spawned_tool_env_lacks_provider_keys_keeps_gateway_token(self, venue_env, monkeypatch):
        from r4.companion import tools

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-leak")
        monkeypatch.setenv("GEMINI_API_KEY", "g-leak")
        monkeypatch.setenv("R4_GATEWAY_TOKEN", "hatch-gw-token")
        out = tools._shell_exec("env")
        names = {line.split("=", 1)[0] for line in out.splitlines() if "=" in line}
        assert {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"}.isdisjoint(names)
        assert "R4_VENUE_SESSION_TOKEN_FILE" not in names
        assert "R4_GATEWAY_TOKEN=hatch-gw-token" in out.splitlines()

    def test_local_mode_unchanged(self, monkeypatch):
        monkeypatch.delenv("R4_VENUE_SESSION_TOKEN_FILE", raising=False)
        env = {"OPENAI_API_KEY": "sk-user-local", "PATH": "/bin"}
        assert venue.sanitize_tool_spawn_env(env) == env


class TestProviderFallback:
    @pytest.mark.usefixtures("venue_env")
    def test_provider_uses_venue_token_when_no_explicit_key(self):
        provider = OpenAICompatibleProvider(provider_name="demo", base_url="https://egress:8080/v1")
        assert provider._api_key == "sess-token-deadbeef-1"
        headers = provider._headers()
        assert headers["Authorization"] == "Bearer sess-token-deadbeef-1"

    @pytest.mark.usefixtures("venue_env")
    def test_explicit_key_wins_over_venue_token(self):
        provider = OpenAICompatibleProvider(
            provider_name="demo", base_url="https://egress:8080", api_key="explicit-key"
        )
        assert provider._api_key == "explicit-key"

    def test_local_mode_no_fallback(self, monkeypatch):
        monkeypatch.delenv("R4_VENUE_SESSION_TOKEN_FILE", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "env-key-not-touched")
        provider = OpenAICompatibleProvider(provider_name="demo", api_key=None)
        assert provider._api_key is None
        assert "Authorization" not in provider._headers()
