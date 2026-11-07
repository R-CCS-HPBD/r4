"""Tests for login → source → umrs setup consistency (2026-06-02).

Validates that after ``r4 login``, all configuration files are consistent:
- r4 sources.json has the source with normalized URL and is set as active
- umrs connections/<name>.json has matching gateway_url
- umrs connections/<name>.token has the JWT
- umrs current_connection_profile points to the profile
- Duplicate sources by URL are detected and updated (not duplicated)
- OIDC tokens are synced when API key is absent
"""

from __future__ import annotations

import json
import os
from argparse import Namespace
from unittest.mock import patch

import pytest


@pytest.fixture()
def clean_env(tmp_path):
    """Create a clean XDG config environment with isolated r4 and umrs dirs."""
    xdg_config = tmp_path / "config"
    xdg_config.mkdir()

    env_overrides = {
        "XDG_CONFIG_HOME": str(xdg_config),
        "R4_GATEWAY_URL": "",
        "R4_API_KEY": "",
        "R4_PROFILE": "",
        "R4_SOURCE": "",
        "UMRS_PROFILE": "",
        "UMRS_CONFIG": str(xdg_config / "umrs"),
    }

    with patch.dict(os.environ, env_overrides, clear=False):
        # Clear any cached singletons

        yield {
            "xdg": xdg_config,
            "r4_dir": xdg_config / "r4",
            "umrs_dir": xdg_config / "umrs",
        }


class TestURLNormalization:
    """Gap 1: gateway_url should be normalized before writing to umrs profile."""

    def test_bare_hostname_gets_https_prefix(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source

        _sync_onboard_source("test", "data0.ai.r-ccs.riken.jp/rccs", "sk-test-key")

        # r4 source should have https://
        sources_file = clean_env["r4_dir"] / "sources.json"
        assert sources_file.exists()
        data = json.loads(sources_file.read_text())
        source = next(s for s in data["sources"] if s["name"] == "test")
        assert source["url"].startswith("https://")

        # umrs profile should also have https://
        umrs_profile = clean_env["umrs_dir"] / "connections" / "test.json"
        assert umrs_profile.exists()
        profile_data = json.loads(umrs_profile.read_text())
        assert profile_data["gateway_url"].startswith("https://")

    def test_localhost_gets_http_prefix(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source

        # "local" is a reserved source name since 2026-10-15 (decision D-8).
        _sync_onboard_source("local-stack", "localhost:8080", "sk-test")

        sources_file = clean_env["r4_dir"] / "sources.json"
        data = json.loads(sources_file.read_text())
        source = next(s for s in data["sources"] if s["name"] == "local-stack")
        assert source["url"].startswith("http://")

    def test_already_normalized_url_unchanged(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source

        _sync_onboard_source("test", "https://data0.ai.r-ccs.riken.jp/rccs", "sk-test")

        sources_file = clean_env["r4_dir"] / "sources.json"
        data = json.loads(sources_file.read_text())
        source = next(s for s in data["sources"] if s["name"] == "test")
        assert source["url"] == "https://data0.ai.r-ccs.riken.jp/rccs"


class TestActiveSourceSet:
    """Gap 2: r4 active source should be set after login."""

    def test_login_sets_r4_active_source(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source
        from r4.source import get_source_store

        _sync_onboard_source("rccs", "https://data0.ai.r-ccs.riken.jp/rccs", "sk-test")

        store = get_source_store()
        assert store.get_active() == "rccs"

    def test_second_login_switches_active_source(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source
        from r4.source import get_source_store

        _sync_onboard_source("rccs", "https://data0.ai.r-ccs.riken.jp/rccs", "sk-test1")
        _sync_onboard_source("ai4s", "https://data0.ai.r-ccs.riken.jp/ai4s", "sk-test2")

        store = get_source_store()
        assert store.get_active() == "ai4s"

    def test_umrs_active_profile_also_set(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source

        _sync_onboard_source("rccs", "https://data0.ai.r-ccs.riken.jp/rccs", "sk-test")

        umrs_current = clean_env["umrs_dir"] / "current_connection_profile"
        assert umrs_current.exists()
        assert umrs_current.read_text().strip() == "rccs"


class TestSourceDeduplication:
    """Gap 4: login should not create duplicate sources for same gateway URL."""

    def test_same_url_different_profile_updates_existing(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source
        from r4.source import get_source_store

        # First login creates source 'rccs'
        _sync_onboard_source("rccs", "https://data0.ai.r-ccs.riken.jp/rccs", "sk-old")

        # Second login with different profile name but same URL
        _sync_onboard_source("rccs-cloud", "https://data0.ai.r-ccs.riken.jp/rccs", "sk-new")

        store = get_source_store()
        sources = store.list()
        # Should have only 1 source (updated 'rccs'), not 2
        platform_sources = [s for s in sources if s.url.endswith("/rccs")]
        assert len(platform_sources) == 1
        assert platform_sources[0].name == "rccs"
        assert platform_sources[0].api_key == "sk-new"

    def test_different_url_creates_new_source(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source
        from r4.source import get_source_store

        _sync_onboard_source("rccs", "https://data0.ai.r-ccs.riken.jp/rccs", "sk-1")
        _sync_onboard_source("ai4s", "https://data0.ai.r-ccs.riken.jp/ai4s", "sk-2")

        store = get_source_store()
        sources = store.list()
        assert len(sources) == 2

    def test_dedup_preserves_zmq_config(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source
        from r4.source import Source, get_source_store

        store = get_source_store()
        # Create source with ZMQ config
        src = Source(
            name="rccs",
            kind="platform",
            url="https://data0.ai.r-ccs.riken.jp/rccs",
            api_key="sk-old",
            zmq_host="data0.ai.r-ccs.riken.jp",
            zmq_control_port=50090,
            zmq_tunnel_mode=True,
        )
        store.add(src)

        # Login with different profile name but same URL
        _sync_onboard_source("rccs-new", "https://data0.ai.r-ccs.riken.jp/rccs", "sk-new")

        updated = store.get("rccs")
        assert updated is not None
        assert updated.zmq_host == "data0.ai.r-ccs.riken.jp"
        assert updated.zmq_control_port == 50090
        assert updated.zmq_tunnel_mode is True
        assert updated.api_key == "sk-new"


class TestUMRSProfileConsistency:
    """Verify umrs profile files are correct after login."""

    def test_token_file_created(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source

        _sync_onboard_source("rccs", "https://data0.ai.r-ccs.riken.jp/rccs", "sk-test-jwt")

        token_path = clean_env["umrs_dir"] / "connections" / "rccs.token"
        assert token_path.exists()
        assert token_path.read_text().strip() == "sk-test-jwt"

    def test_profile_json_has_gateway_url(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source

        _sync_onboard_source("rccs", "https://data0.ai.r-ccs.riken.jp/rccs", "sk-test")

        profile_path = clean_env["umrs_dir"] / "connections" / "rccs.json"
        assert profile_path.exists()
        data = json.loads(profile_path.read_text())
        assert data["gateway_url"] == "https://data0.ai.r-ccs.riken.jp/rccs"
        assert data["name"] == "rccs"

    def test_empty_api_key_removes_token(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source

        # First create with token
        _sync_onboard_source("rccs", "https://data0.ai.r-ccs.riken.jp/rccs", "sk-test")
        token_path = clean_env["umrs_dir"] / "connections" / "rccs.token"
        assert token_path.exists()

        # Re-sync with empty key
        _sync_onboard_source("rccs", "https://data0.ai.r-ccs.riken.jp/rccs", "")
        assert not token_path.exists()


class TestOIDCTokenSync:
    """Gap 6: OIDC access_token should be synced to umrs."""

    def test_oidc_token_used_when_no_api_key(self, clean_env):
        from r4._cli.login import _sync_umrs_client_profile
        from r4.config import ProfileConfigStore

        # Set up profile with gateway_url
        store = ProfileConfigStore(config_dir=clean_env["r4_dir"])
        store.save("test-oidc", gateway_url="https://data0.ai.r-ccs.riken.jp/rccs")

        # Mock auth manager: no API key, but OIDC token exists
        from unittest.mock import MagicMock

        from r4.auth import TokenInfo

        mock_mgr = MagicMock()
        mock_mgr.api_key_manager.get_api_key.return_value = None
        mock_mgr.token_store.load.return_value = TokenInfo(
            access_token="oidc-access-token-123",
            token_type="bearer",
        )

        args = Namespace(profile="test-oidc", gateway_url=None, api_key=None)

        with patch("r4.auth.get_auth_manager", return_value=mock_mgr):
            _sync_umrs_client_profile(args)

        # Verify token was written
        token_path = clean_env["umrs_dir"] / "connections" / "test-oidc.token"
        assert token_path.exists()
        assert token_path.read_text().strip() == "oidc-access-token-123"


class TestFullLoginFlow:
    """Integration test simulating the full login → verify flow."""

    def test_clean_login_creates_all_files(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source
        from r4.source import get_source_store

        gateway_url = "https://data0.ai.r-ccs.riken.jp/rccs"
        api_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0In0.abc"

        _sync_onboard_source("rccs", gateway_url, api_key)

        # 1. sources.json: source exists, is active, URL normalized
        store = get_source_store()
        assert store.get_active() == "rccs"
        src = store.get("rccs")
        assert src is not None
        assert src.url == gateway_url
        assert src.api_key == api_key

        # 2. umrs connections/rccs.json: gateway_url matches
        profile_path = clean_env["umrs_dir"] / "connections" / "rccs.json"
        profile_data = json.loads(profile_path.read_text())
        assert profile_data["gateway_url"] == gateway_url
        assert profile_data["name"] == "rccs"

        # 3. umrs connections/rccs.token: JWT present
        token_path = clean_env["umrs_dir"] / "connections" / "rccs.token"
        assert token_path.read_text().strip() == api_key

        # 4. umrs current_connection_profile: points to rccs
        current = clean_env["umrs_dir"] / "current_connection_profile"
        assert current.read_text().strip() == "rccs"

    def test_sequential_logins_switch_correctly(self, clean_env):
        from r4._cli.onboard import _sync_onboard_source
        from r4.source import get_source_store

        # Login to rccs
        _sync_onboard_source("rccs", "https://data0.ai.r-ccs.riken.jp/rccs", "key-rccs")
        store = get_source_store()
        assert store.get_active() == "rccs"

        # Login to ai4s
        _sync_onboard_source("ai4s", "https://data0.ai.r-ccs.riken.jp/ai4s", "key-ai4s")
        assert store.get_active() == "ai4s"

        # Both sources exist
        assert store.get("rccs") is not None
        assert store.get("ai4s") is not None

        # umrs active profile switched to ai4s
        current = clean_env["umrs_dir"] / "current_connection_profile"
        assert current.read_text().strip() == "ai4s"

        # Both umrs profiles exist
        assert (clean_env["umrs_dir"] / "connections" / "rccs.json").exists()
        assert (clean_env["umrs_dir"] / "connections" / "ai4s.json").exists()


class TestSyncVerification:
    """Verify _sync_umrs_client_profile writes and verifies the active pointer."""

    def test_sync_verifies_active_pointer(self, clean_env):
        from argparse import Namespace

        from r4._cli.login import _sync_umrs_client_profile
        from r4.source import get_umrs_active_profile_name

        args = Namespace(
            profile="rccs-cloud",
            gateway_url="https://data0.ai.r-ccs.riken.jp/rccs",
            api_key="sk-test",
        )
        _sync_umrs_client_profile(args)
        assert get_umrs_active_profile_name() == "rccs-cloud"

    def test_empty_profile_name_is_rejected(self, clean_env):
        from r4.source import set_umrs_active_profile

        assert set_umrs_active_profile("") is False
        assert set_umrs_active_profile(None) is False


class TestR4ProfileEnvHandling:
    """R4_PROFILE empty string should fall back to DEFAULT_PROFILE."""

    def test_empty_r4_profile_env_uses_default(self, clean_env, monkeypatch):
        from r4.config import Config

        monkeypatch.setenv("R4_PROFILE", "")
        config = Config.from_env()
        assert config.profile == "default"
