"""
Unit tests for r4 Config module (A-001).

Tests:
- Environment variable loading
- Configuration validation
- HPC detection
- Effective endpoint resolution
"""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolate_config_home(tmp_path, monkeypatch):
    """Keep config-loading tests isolated from any real user config."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    import r4.config
    import r4.project

    r4.config._config = None
    r4.project._project_manager = None


# =============================================================================
# Config Creation Tests
# =============================================================================


class TestConfigCreation:
    """Tests for Config creation and defaults."""

    def test_default_values(self):
        """Test default configuration values."""
        from r4.config import Config

        config = Config()
        assert config.gateway_url == "http://localhost:8080"
        assert config.api_key is None
        assert config.project_id is None
        assert config.model == "rivault-gpt-oss-static"
        assert config.timeout == 300
        assert config.max_retries == 3
        assert config.verify_ssl is True

    def test_custom_values(self):
        """Test configuration with custom values."""
        from r4.config import Config

        config = Config(
            gateway_url="http://custom:9000",
            api_key="my-key",
            project_id="my-project",
            model="custom-model",
            timeout=600,
        )
        assert config.gateway_url == "http://custom:9000"
        assert config.api_key == "my-key"
        assert config.project_id == "my-project"
        assert config.model == "custom-model"
        assert config.timeout == 600


# =============================================================================
# Environment Loading Tests
# =============================================================================


class TestConfigFromEnv:
    """Tests for loading config from environment variables."""

    def test_from_env_basic(self):
        """Test loading from environment variables."""
        from r4.config import Config

        env_vars = {
            "R4_GATEWAY_URL": "http://gateway:8080",
            "R4_API_KEY": "env-key",
            "R4_PROJECT_ID": "env-project",
            "R4_MODEL": "env-model",
            "R4_TIMEOUT": "120",
        }

        with patch.dict("os.environ", env_vars, clear=False):
            config = Config.from_env()

        assert config.gateway_url == "http://gateway:8080"
        assert config.api_key == "env-key"
        assert config.project_id == "env-project"
        assert config.model == "env-model"
        assert config.timeout == 120

    def test_from_env_defaults(self, monkeypatch):
        """Test defaults when env vars are not set."""
        import os

        from r4.config import Config

        # Isolate the test from the user's persisted profile on disk.
        # Without this, any stored profile (from a real `r4 init`) leaks
        # into the test as `api_key`, etc. See bug.memo M1 (2026-04-25).
        monkeypatch.setattr("r4.config.ProfileConfigStore.load", lambda self, name: {})

        # Clear relevant env vars
        with patch.dict(
            "os.environ",
            {"XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"]},
            clear=True,
        ):
            config = Config.from_env()

        assert config.gateway_url == "http://localhost:8080"
        assert config.api_key is None
        assert config.model == "rivault-gpt-oss-static"
        assert config.timeout == 300

    def test_from_env_slurm_job(self):
        """Test HPC detection via SLURM_JOB_ID."""
        from r4.config import Config

        with patch.dict("os.environ", {"SLURM_JOB_ID": "12345"}, clear=False):
            config = Config.from_env()

        assert config.slurm_job_id == "12345"
        assert config.is_hpc is True

    def test_from_env_custom_gateway(self):
        """Test custom gateway URL from env."""
        from r4.config import Config

        with patch.dict("os.environ", {"R4_GATEWAY_URL": "http://custom:8080"}, clear=False):
            config = Config.from_env()

        assert config.gateway_url == "http://custom:8080"

    def test_from_env_verify_ssl_true(self):
        """Test verify_ssl defaults to true."""
        from r4.config import Config

        with patch.dict("os.environ", {"R4_VERIFY_SSL": "true"}, clear=False):
            config = Config.from_env()

        assert config.verify_ssl is True

    def test_from_env_verify_ssl_false(self):
        """Test verify_ssl can be disabled."""
        from r4.config import Config

        with patch.dict("os.environ", {"R4_VERIFY_SSL": "false"}, clear=False):
            config = Config.from_env()

        assert config.verify_ssl is False

    def test_from_env_loads_stored_profile_settings(self):
        """Stored profile settings should be loaded when env vars are absent."""
        import os

        from r4.config import Config, ProfileConfigStore

        store = ProfileConfigStore()
        store.save(
            "research",
            gateway_url="http://stored:8080",
            project_id="stored-project",
            model="stored-model",
            verify_ssl=False,
            umrs_username="alice",
            deploy_mode="cloud",
            transport_mode="gateway",
        )

        with patch.dict(
            "os.environ",
            {
                "R4_PROFILE": "research",
                "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
            },
            clear=True,
        ):
            config = Config.from_env()

        assert config.profile == "research"
        assert config.gateway_url == "http://stored:8080"
        assert config.project_id == "stored-project"
        assert config.model == "stored-model"
        assert config.verify_ssl is False
        assert config.umrs_username == "alice"
        from r4.config import DeployMode, TransportMode

        assert config.deploy_mode == DeployMode.CLOUD
        assert config.transport_mode == TransportMode.GATEWAY

    def test_from_env_loads_model_routes(self):
        """Stored per-model routes should be loaded from the active profile."""
        import os

        from r4.config import Config, ProfileConfigStore

        ProfileConfigStore().save(
            "research",
            model="gpt-4o",
            model_routes={"gpt-4o": "public", "rivault-gpt": "platform"},
        )

        with patch.dict(
            "os.environ",
            {
                "R4_PROFILE": "research",
                "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
            },
            clear=True,
        ):
            config = Config.from_env()

        assert config.model_routes == {
            "gpt-4o": "public",
            "rivault-gpt": "platform",
        }

    def test_from_env_does_not_reuse_gateway_key_for_bedrock(self):
        """Bedrock SigV4 mode must not treat gateway auth as provider auth."""
        import os

        from r4.config import Config, ProfileConfigStore

        ProfileConfigStore().save(
            "research",
            api_key="gateway-token",
            endpoint_provider="bedrock",
            aws_region="us-east-2",
        )

        with patch.dict(
            "os.environ",
            {
                "R4_PROFILE": "research",
                "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
            },
            clear=True,
        ):
            config = Config.from_env()

        assert config.api_key == "gateway-token"
        assert config.endpoint_api_key is None

    def test_from_env_prefers_environment_over_stored_settings(self):
        """Environment variables should override stored profile values."""
        import os

        from r4.config import Config, ProfileConfigStore

        store = ProfileConfigStore()
        store.save(
            "research",
            gateway_url="http://stored:8080",
            project_id="stored-project",
            model="stored-model",
            verify_ssl=False,
        )

        with patch.dict(
            "os.environ",
            {
                "R4_PROFILE": "research",
                "R4_GATEWAY_URL": "http://env:8080",
                "R4_PROJECT_ID": "env-project",
                "R4_VERIFY_SSL": "true",
                "R4_DEPLOY_MODE": "hpc-local",
                "R4_TRANSPORT_MODE": "direct",
                "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
            },
            clear=True,
        ):
            config = Config.from_env()

        assert config.gateway_url == "http://env:8080"
        assert config.project_id == "env-project"
        assert config.verify_ssl is True
        from r4.config import DeployMode, TransportMode

        assert config.deploy_mode == DeployMode.HPC_LOCAL
        assert config.transport_mode == TransportMode.DIRECT

    def test_from_env_prefers_stored_profile_context_over_current_project_defaults(self):
        """Stored profile context should override active project URL/project defaults."""
        import os

        from r4.config import Config, ProfileConfigStore
        from r4.project import ProjectConfig, get_project_manager

        store = ProfileConfigStore()
        store.save(
            "research",
            gateway_url="http://stored:8080",
            project_id="stored-project",
            model="stored-model",
        )

        pm = get_project_manager()
        pm.create(
            ProjectConfig(
                project_id="proj-beta",
                gateway_url="http://project:8080",
                model="project-model",
            )
        )
        pm.set_current("proj-beta")

        with patch.dict(
            "os.environ",
            {
                "R4_PROFILE": "research",
                "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
            },
            clear=True,
        ):
            config = Config.from_env()

        assert config.gateway_url == "http://stored:8080"
        assert config.project_id == "stored-project"
        assert config.model == "project-model"


# =============================================================================
# Property Tests
# =============================================================================


class TestConfigProperties:
    """Tests for Config property methods."""

    def test_is_hpc_true(self):
        """Test is_hpc when SLURM_JOB_ID is set."""
        from r4.config import Config

        config = Config(slurm_job_id="12345")
        assert config.is_hpc is True

    def test_is_hpc_false(self):
        """Test is_hpc when not in HPC environment."""
        from r4.config import Config

        config = Config(slurm_job_id=None)
        assert config.is_hpc is False

    def test_effective_endpoint_with_gateway(self):
        """Test effective_endpoint appends /gw/llm to gateway_url."""
        from r4.config import Config

        config = Config(
            gateway_url="http://gateway:8080",
        )
        assert config.effective_endpoint == "http://gateway:8080/gw/llm"

    def test_effective_endpoint_custom_port(self):
        """Test effective_endpoint with custom port."""
        from r4.config import Config

        config = Config(
            gateway_url="http://localhost:9000",
        )
        assert config.effective_endpoint == "http://localhost:9000/gw/llm"


# =============================================================================
# Validation Tests
# =============================================================================


class TestConfigValidation:
    """Tests for Config validation."""

    def test_validate_valid_config(self):
        """Test validation passes for valid config."""
        from r4.config import Config

        config = Config(gateway_url="http://localhost:8080", timeout=300)
        errors = config.validate()

        assert errors == []

    def test_validate_no_endpoint(self):
        """Test validation fails when no endpoint is set."""
        from r4.config import Config

        config = Config(gateway_url="")
        errors = config.validate()

        assert len(errors) > 0
        assert "gateway_url" in errors[0] or "endpoint" in errors[0]

    def test_validate_invalid_timeout(self):
        """Test validation fails for invalid timeout."""
        from r4.config import Config

        config = Config(timeout=0)
        errors = config.validate()

        assert len(errors) > 0
        assert "timeout" in errors[0].lower()


# =============================================================================
# Singleton Tests
# =============================================================================


class TestConfigSingleton:
    """Tests for get_config singleton."""

    def test_get_config_returns_config(self):
        """Test get_config returns a Config instance."""
        from r4.config import get_config

        config = get_config()
        assert config is not None

    def test_get_config_cached(self):
        """Test get_config returns cached instance."""
        from r4.config import get_config

        config1 = get_config()
        config2 = get_config()

        # Should return same instance
        assert config1 is config2


class TestUmrsZmqResolution:
    """Round-4 T6: active-source ZMQ values fill unset umrs_server_host/port.

    The webview agent session failed file_upload_platform because the r4 config
    resolved umrs_server_host=127.0.0.1:5555 (built-in defaults) even when the
    active platform source carried the real ZMQ endpoint — the resolver never
    consulted the source. The overlay now fills the two values from the active
    source whenever the defaults are still in place.
    """

    def _source(self, **kw):
        from r4.source import Source

        defaults = dict(
            name="ai4s",
            kind="platform",
            url="https://data0.ai.r-ccs.riken.jp/ai4s",
        )
        defaults.update(kw)
        return Source(**defaults)

    def test_source_zmq_fills_defaults(self, monkeypatch):
        """Active-source zmq_host/port resolve into the r4 config."""
        monkeypatch.setattr("r4.config.ProfileConfigStore.load", lambda self, name: {})
        monkeypatch.delenv("UMRS_SERVER_HOST", raising=False)
        monkeypatch.delenv("UMRS_CONTROL_PORT", raising=False)
        monkeypatch.setattr(
            "r4.source.resolve_active_source",
            lambda: self._source(zmq_host="data0.ai.r-ccs.riken.jp", zmq_control_port=50110),
        )
        from r4.config import Config

        config = Config.from_env()
        assert config.umrs_server_host == "data0.ai.r-ccs.riken.jp"
        assert config.umrs_control_port == 50110

    def test_env_override_beats_source(self, monkeypatch):
        """An explicit env var wins over the source's zmq values."""
        monkeypatch.setattr("r4.config.ProfileConfigStore.load", lambda self, name: {})
        monkeypatch.setattr(
            "r4.source.resolve_active_source",
            lambda: self._source(zmq_host="data0", zmq_control_port=50110),
        )
        monkeypatch.setenv("UMRS_SERVER_HOST", "custom-host")
        monkeypatch.setenv("UMRS_CONTROL_PORT", "6000")
        from r4.config import Config

        config = Config.from_env()
        assert config.umrs_server_host == "custom-host"
        assert config.umrs_control_port == 6000

    def test_stored_override_beats_source(self, monkeypatch):
        """A stored r4 config value wins over the source."""
        monkeypatch.setattr(
            "r4.config.ProfileConfigStore.load",
            lambda self, name: {"umrs_server_host": "stored-host", "umrs_control_port": 6100},
        )
        monkeypatch.setattr(
            "r4.source.resolve_active_source",
            lambda: self._source(zmq_host="data0", zmq_control_port=50110),
        )
        monkeypatch.delenv("UMRS_SERVER_HOST", raising=False)
        monkeypatch.delenv("UMRS_CONTROL_PORT", raising=False)
        from r4.config import Config

        config = Config.from_env()
        assert config.umrs_server_host == "stored-host"
        assert config.umrs_control_port == 6100

    def test_source_without_zmq_fields_keeps_defaults(self, monkeypatch):
        """A source with no zmq registration leaves the defaults alone."""
        monkeypatch.setattr("r4.config.ProfileConfigStore.load", lambda self, name: {})
        monkeypatch.delenv("UMRS_SERVER_HOST", raising=False)
        monkeypatch.delenv("UMRS_CONTROL_PORT", raising=False)
        monkeypatch.setattr("r4.source.resolve_active_source", lambda: self._source())
        from r4.config import Config

        config = Config.from_env()
        assert config.umrs_server_host == "127.0.0.1"
        assert config.umrs_control_port == 5555


class TestUmrsZmqTunnelAndPresenceGuards:
    """Round-5 task 1: the T6 overlay must not leak into tunneled profiles
    or stomp explicit stored choices — presence, not value.
    """

    def _source(self, **kw):
        from r4.source import Source

        defaults = dict(
            name="bdrt",
            kind="platform",
            url="https://data0.ai.r-ccs.riken.jp/bdrt",
        )
        defaults.update(kw)
        return Source(**defaults)

    def test_tunnel_mode_skips_overlay_entirely(self, monkeypatch):
        """When umrs_tunnel_mode is on, 127.0.0.1 is the correct endpoint —
        the overlay must not replace it with the source's remote host."""
        monkeypatch.setattr("r4.config.ProfileConfigStore.load", lambda self, name: {})
        monkeypatch.delenv("UMRS_SERVER_HOST", raising=False)
        monkeypatch.delenv("UMRS_CONTROL_PORT", raising=False)
        monkeypatch.setattr(
            "r4.source.resolve_active_source",
            lambda: self._source(zmq_host="data0", zmq_control_port=50110),
        )
        monkeypatch.setenv("UMRS_TUNNEL_MODE", "1")
        from r4.config import Config

        config = Config.from_env()
        assert config.umrs_tunnel_mode is True
        assert config.umrs_server_host == "127.0.0.1"
        assert config.umrs_control_port == 5555

    def test_explicit_stored_localhost_is_a_choice_not_a_default(self, monkeypatch):
        """A stored 127.0.0.1 must not be swapped by the overlay."""
        monkeypatch.setattr(
            "r4.config.ProfileConfigStore.load",
            lambda self, name: {"umrs_server_host": "127.0.0.1"},
        )
        monkeypatch.delenv("UMRS_SERVER_HOST", raising=False)
        monkeypatch.delenv("UMRS_CONTROL_PORT", raising=False)
        monkeypatch.setattr(
            "r4.source.resolve_active_source",
            lambda: self._source(zmq_host="data0", zmq_control_port=50110),
        )
        from r4.config import Config

        config = Config.from_env()
        assert config.umrs_server_host == "127.0.0.1"

    def test_field_level_independence(self, monkeypatch):
        """Filling is per-field: stored host blocks only the host fill; the
        port still falls through to the source."""
        monkeypatch.setattr(
            "r4.config.ProfileConfigStore.load",
            lambda self, name: {"umrs_server_host": "127.0.0.1"},
        )
        monkeypatch.delenv("UMRS_SERVER_HOST", raising=False)
        monkeypatch.delenv("UMRS_CONTROL_PORT", raising=False)
        monkeypatch.setattr(
            "r4.source.resolve_active_source",
            lambda: self._source(zmq_host="data0", zmq_control_port=50110),
        )
        from r4.config import Config

        config = Config.from_env()
        assert config.umrs_server_host == "127.0.0.1"
        assert config.umrs_control_port == 50110


class TestZmqSessionProfileResolution:
    """Round-5 T3: _get_jwt_for_session must pick the ACTIVE platform source's
    profile, not the r4 `default` profile (whose test-token got the chain 401).
    """

    def _source(self, **kw):
        from r4.source import Source

        return Source(
            name="rccs-cloud", kind="platform", url="https://data0.ai.r-ccs.riken.jp/rccs", **kw
        )

    def test_active_platform_source_profile_wins(self, monkeypatch):
        seen = {"profile": None}

        class _FakeAuthMgr:
            def __init__(self, profile="default"):
                seen["profile"] = profile

            def get_auth_header(self):
                return {"Authorization": "Bearer a.b.c"}

        monkeypatch.setattr("r4.auth.AuthManager", _FakeAuthMgr)
        monkeypatch.setattr("r4.source.resolve_active_source", lambda: self._source())
        from r4.umrs_transport import UMRSTransport

        jwt = UMRSTransport().__getattribute__("_get_jwt_for_session")()
        assert seen["profile"] == "rccs-cloud"
        assert jwt == "a.b.c"

    def test_non_platform_source_falls_back_default(self, monkeypatch):
        seen = {"profile": None}

        class _FakeAuthMgr:
            def __init__(self, profile="default"):
                seen["profile"] = profile

            def get_auth_header(self):
                return None

        class _NoneSource:
            kind = "public"
            name = "public-oai"

        monkeypatch.setattr("r4.auth.AuthManager", _FakeAuthMgr)
        monkeypatch.setattr("r4.source.resolve_active_source", lambda: _NoneSource())
        jwt = (
            __import__("r4.umrs_transport", fromlist=["UMRSTransport"])
            .UMRSTransport()
            ._get_jwt_for_session()
        )
        assert seen["profile"] == "default"
        assert jwt is None


class TestFieldTableSync:
    """_FIELD_SPECS must cover every field from_env sets via the table, and
    must not drift away from the Config dataclass (round-6 task 2.2)."""

    def test_table_covers_dataclass_table_fields(self):
        from r4.config import _FIELD_SPECS, Config

        table_attrs = {spec.attr for spec in _FIELD_SPECS}
        special_attrs = {
            "profile",
            "model",
            "verify_ssl",
            "aws_region",
            "endpoint_api_key",
            "use_umrs_transport",
            "umrs_tunnel_mode",
            "deploy_mode",
            "transport_mode",
            "model_routes",
            # Set by _apply_active_source_overlay (onboarding plan S1).
            "bound_source",
            # Set by Config.from_env from _login_home (onboarding plan S1).
            "login_home",
            # ADR-099 D-2: set by _stored_web_access (the profile store only).
            "web_access_last",
            "web_access_domains_last",
        }
        all_fields = {f.name for f in Config.__dataclass_fields__.values()}
        missing = all_fields - table_attrs - special_attrs
        assert missing == set(), (
            "dataclass fields not resolved anywhere in from_env: "
            f"{missing}. Add a _FieldSpec entry or a named helper."
        )

    def test_table_attrs_exist_on_dataclass(self):
        from r4.config import _FIELD_SPECS, Config

        all_fields = set(Config.__dataclass_fields__)
        for spec in _FIELD_SPECS:
            assert spec.attr in all_fields, spec.attr


class TestFieldPrecedenceAndFalsy:
    """Round-6 review §4 — pin the four-layer precedence contract per field,
    and the falsy value behavior (0/False/"" treated deliberately)."""

    @staticmethod
    def _config_from(stored, monkeypatch, *, local_cfg=None, repo_cfg=None, project=None, env=None):
        import r4.config as cfg_mod

        monkeypatch.setattr(cfg_mod.ProfileConfigStore, "load", lambda self, name: dict(stored))
        monkeypatch.setattr(
            cfg_mod,
            "_load_current_project_defaults",
            lambda: dict(project or {}),
        )
        monkeypatch.setattr(cfg_mod, "_load_local_config", lambda: dict(local_cfg or {}))
        monkeypatch.setattr(cfg_mod, "_load_repo_config", lambda: dict(repo_cfg or {}))
        with pytest.MonkeyPatch.context() as mp:
            for k, v in (env or {}).items():
                mp.setenv(k, v)
            return cfg_mod.Config.from_env()

    def test_env_wins_over_all_layers(self, monkeypatch):
        config = self._config_from(
            {"gateway_url": "stored-url"},
            monkeypatch,
            local_cfg={"gateway_url": "local-url"},
            repo_cfg={"gateway_url": "repo-url"},
            project={"gateway_url": "project-url"},
            env={"R4_GATEWAY_URL": "env-url"},
        )
        assert config.gateway_url == "https://env-url"  # normalized

    def test_local_toml_beats_repo_and_stored(self, monkeypatch):
        config = self._config_from(
            {"gateway_url": "stored-url"},
            monkeypatch,
            local_cfg={"gateway_url": "local-url"},
            repo_cfg={"gateway_url": "repo-url"},
            project={"gateway_url": "project-url"},
        )
        assert config.gateway_url == "https://local-url"  # normalized

    def test_repo_beats_stored_and_project(self, monkeypatch):
        config = self._config_from(
            {"gateway_url": "stored-url"},
            monkeypatch,
            repo_cfg={"gateway_url": "repo-url"},
            project={"gateway_url": "project-url"},
        )
        assert config.gateway_url == "https://repo-url"  # normalized

    def test_stored_beats_project(self, monkeypatch):
        config = self._config_from(
            {"project_id": "stored-p"},
            monkeypatch,
            project={"project_id": "project-p"},
        )
        assert config.project_id == "stored-p"

    def test_empty_string_from_env_falls_through(self, monkeypatch):
        """Legacy falsy behavior: empty-string env isn't a value; the
        next non-empty layer wins."""
        config = self._config_from(
            {"gateway_url": "stored-url"},
            monkeypatch,
            env={"R4_GATEWAY_URL": "  "},
        )
        assert config.gateway_url == "https://stored-url"  # normalized

    def test_rs1_lane_a_and_w_fields_both_load(self, monkeypatch):
        """ADR-099: lane A (ask_user_mode) and lane W (the last web-access
        choice) both edited Config; the merge keeps and resolves both."""
        stored = {
            "ask_user_mode": "auto",
            "web_access_last": "allowlist",
            "web_access_domains_last": ["a.org", "b.org"],
        }
        config = self._config_from(stored, monkeypatch)
        assert config.ask_user_mode == "auto"
        assert config.web_access_last == "allowlist"
        assert config.web_access_domains_last == "a.org,b.org"
        env_wins = self._config_from(stored, monkeypatch, env={"R4_ASK_USER_MODE": "ask"})
        assert (env_wins.ask_user_mode, env_wins.web_access_last) == ("ask", "allowlist")
        bad = self._config_from({"ask_user_mode": "sometimes"}, monkeypatch)
        assert (bad.ask_user_mode, bad.web_access_last, bad.web_access_domains_last) == (
            "ask",
            "",
            "",
        )

    def test_toml_zero_wins_for_numeric_fields(self, monkeypatch):
        """0 from a high-priority layer is a DELIBERATE value, not
        'missing' (へpin falsy semantics of _first_non_empty).

        Stored `timeout` was and remains intentionally unread (matches
        the original loader — env > TOML > default only for timeout),
        so the pin goes through the TOML layer."""
        config = self._config_from({}, monkeypatch, local_cfg={"timeout": 0})
        assert config.timeout == 0

    def test_stored_timeout_field_not_read_by_loader(self, monkeypatch):
        """Legacy parity: stored config never consulted for timeout."""
        config = self._config_from({"timeout": 0}, monkeypatch)
        assert config.timeout == 300

    def test_stored_verify_ssl_false_wins(self, monkeypatch):
        """False in stored config disables TLS verification — must not
        collapse into the default True."""
        config = self._config_from({"verify_ssl": False}, monkeypatch)
        assert config.verify_ssl is False

    def test_toml_verify_ssl_false_wins_over_stored_true(self, monkeypatch):
        config = self._config_from(
            {"verify_ssl": True},
            monkeypatch,
            local_cfg={"verify_ssl": False},
        )
        assert config.verify_ssl is False
