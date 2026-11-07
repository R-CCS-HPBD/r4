"""Tests for R4 CLI setup flows."""

import json
import subprocess
import sys
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def isolate_cli_state(tmp_path, monkeypatch):
    """Keep CLI tests isolated from host config and singleton state."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    import r4.auth
    import r4.config
    import r4.project

    r4.auth._auth_manager = None
    r4.config._config = None
    r4.project._project_manager = None


class TestCLIInit:
    """Tests for the unified init command."""

    def test_cmd_init_saves_profile_and_validates_readiness(self, capsys):
        from r4.cli import cmd_init

        args = Namespace(
            profile="research",
            gateway_url="http://gateway.example:8080",
            project="proj-alpha",
            model="rivault-gpt-oss-static",
            api_key="sk-test-init",
            oidc=False,
            timeout=300,
            umrs_username="alice",
            verify_ssl=False,
            skip_health_check=False,
        )

        with patch(
            "r4.client.Client.readiness", return_value={"ready": True, "checks": {"umrs": "ok"}}
        ):
            cmd_init(args)

        self._assert_stored_profile()
        self._assert_config_and_managers()
        self._assert_output_contains(capsys)

    @staticmethod
    def _assert_stored_profile():
        from r4.config import ProfileConfigStore

        stored = ProfileConfigStore().load("research")
        assert stored["gateway_url"] == "http://gateway.example:8080"
        assert stored["project_id"] == "proj-alpha"
        assert stored["verify_ssl"] is False

    @staticmethod
    def _assert_config_and_managers():
        from r4.auth import get_auth_manager
        from r4.config import Config
        from r4.project import get_project_manager

        config = Config.from_env(profile="research")
        assert config.gateway_url == "http://gateway.example:8080"
        assert config.project_id == "proj-alpha"
        assert config.umrs_username == "alice"
        assert get_auth_manager("research").is_authenticated() is True
        assert get_project_manager().get_current() == "proj-alpha"

    @staticmethod
    def _assert_output_contains(capsys):
        output = capsys.readouterr().out
        for expected in ("Active context:", "Profile: research", "Project: proj-alpha"):
            assert expected in output


def test_cmd_init_never_prints_a_gateway_url_password(capsys):
    # `r4 init --gateway-url http://u:PW@host` echoed PW in "Gateway URL:"
    # and the active-context "Gateway:" line (stab cycle 3 review).
    from r4.cli import cmd_init

    args = Namespace(
        profile="research",
        gateway_url="http://u:hunter2-secret@gateway.example:8080",
        project="proj-alpha",
        model="m",
        api_key="sk-test-init",
        oidc=False,
        timeout=300,
        umrs_username="alice",
        verify_ssl=False,
        skip_health_check=True,
    )
    cmd_init(args)
    output = capsys.readouterr().out
    assert "hunter2-secret" not in output
    assert "  Gateway URL: http://***@gateway.example:8080" in output


def test_friendly_status_detail_only_maps_known_states():
    from r4.cli import _friendly_status_detail

    assert _friendly_status_detail("running") is None


def test_display_status_detail_uses_friendly_fallback_for_repeated_status():
    from r4.cli import _display_status_detail

    assert (
        _display_status_detail(
            "starting",
            status_resp={"status": "starting", "detail": "starting"},
            ep={"host": "compute-01"},
        )
        == "starting container / loading model"
    )


def test_display_status_detail_keeps_richer_backend_detail():
    from r4.cli import _display_status_detail

    assert (
        _display_status_detail(
            "starting",
            status_resp={"status": "starting", "detail": "loading model shards"},
            ep={"host": "compute-01"},
        )
        == "loading model shards"
    )

    def test_cmd_init_fails_without_auth_when_non_interactive(self):
        from r4.cli import cmd_init

        args = Namespace(
            profile="research",
            gateway_url="http://gateway.example:8080",
            project="proj-alpha",
            model="rivault-gpt-oss-static",
            api_key=None,
            oidc=False,
            timeout=300,
            umrs_username=None,
            verify_ssl=True,
            skip_health_check=True,
        )

        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stdout.isatty", return_value=False),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cmd_init(args)

        assert exc_info.value.code == 1


class TestCLIProject:
    """Tests for project switching and runtime config refresh."""

    def test_cmd_project_switch_refreshes_runtime_config(self, capsys):
        from r4.cli import cmd_project
        from r4.config import Config, get_config, set_config
        from r4.project import ProjectConfig, get_project_manager

        pm = get_project_manager()
        pm.create(
            ProjectConfig(
                project_id="proj-alpha",
                gateway_url="http://alpha:8080",
                model="model-alpha",
            )
        )
        pm.create(
            ProjectConfig(
                project_id="proj-beta",
                gateway_url="http://beta:8080",
                model="model-beta",
            )
        )
        pm.set_current("proj-alpha")

        set_config(
            Config(
                profile="research",
                gateway_url="http://alpha:8080",
                project_id="proj-alpha",
                model="model-alpha",
            )
        )

        cmd_project(Namespace(project_command="switch", project_id="proj-beta"))

        config = get_config()
        assert config.profile == "research"
        assert config.project_id == "proj-beta"
        assert config.gateway_url == "http://beta:8080"
        assert config.model == "model-beta"

        output = capsys.readouterr().out
        assert "Active context:" in output
        assert "Gateway: http://beta:8080" in output
        assert "Model: model-beta" in output

    def test_project_security_level_property(self):
        """ProjectConfig.security_level reads from metadata."""
        from r4.project import ProjectConfig

        # Default is 'standard'
        p = ProjectConfig(project_id="test")
        assert p.security_level == "standard"

        # Explicit high
        p2 = ProjectConfig(project_id="test", metadata={"security_level": "high"})
        assert p2.security_level == "high"

        # Case-insensitive
        p3 = ProjectConfig(project_id="test", metadata={"security_level": "HIGH"})
        assert p3.security_level == "high"

    def test_project_create_with_security_level(self):
        """r4 project create --security-level high persists to metadata."""
        from r4.cli import cmd_project
        from r4.project import get_project_manager

        cmd_project(
            Namespace(
                project_command="create",
                project_id="sec-test",
                name=None,
                gateway=None,
                model=None,
                security_level="high",
            )
        )

        pm = get_project_manager()
        proj = pm.get("sec-test")
        assert proj is not None
        assert proj.security_level == "high"

    def test_project_current_shows_security_level(self, capsys):
        """r4 project current shows security level when high."""
        from r4.cli import cmd_project
        from r4.project import ProjectConfig, get_project_manager

        pm = get_project_manager()
        pm.create(
            ProjectConfig(
                project_id="sec-show",
                metadata={"security_level": "high"},
            )
        )
        pm.set_current("sec-show")

        cmd_project(Namespace(project_command="current"))

        output = capsys.readouterr().out
        assert "Security level: high" in output

    def test_cmd_health_reports_checked_context(self, capsys):
        from r4.auth import get_auth_manager
        from r4.cli import cmd_health
        from r4.config import Config, set_config

        set_config(
            Config(
                profile="research",
                gateway_url="http://gateway.example:8080",
                project_id="proj-alpha",
                model="rivault-gpt-oss-static",
            )
        )
        get_auth_manager("research").save_api_key("sk-test")

        with patch(
            "r4.client.Client.readiness", return_value={"ready": True, "checks": {"umrs": "ok"}}
        ):
            cmd_health(Namespace())

        output = capsys.readouterr().out
        assert "Checked context:" in output
        assert "Profile: research" in output
        assert "Project: proj-alpha" in output

    def test_cmd_health_json_reports_context(self, capsys):
        from r4.auth import get_auth_manager
        from r4.cli import cmd_health
        from r4.config import Config, set_config

        set_config(
            Config(
                profile="research",
                gateway_url="http://gateway.example:8080",
                project_id="proj-alpha",
                model="rivault-gpt-oss-static",
            )
        )
        get_auth_manager("research").save_api_key("sk-test")

        with patch(
            "r4.client.Client.readiness", return_value={"ready": True, "checks": {"umrs": "ok"}}
        ):
            cmd_health(Namespace(as_json=True, direct_umrs=False))

        data = json.loads(capsys.readouterr().out)
        assert data["ready"] is True
        assert data["checks"] == {"umrs": "ok"}
        assert data["context"] == {
            "profile": "research",
            "project_id": "proj-alpha",
            "gateway_url": "http://gateway.example:8080",
            "model": "rivault-gpt-oss-static",
            "auth_configured": True,
        }

    def test_cmd_health_full_uses_comprehensive_diagnostics(self, capsys):
        from r4.cli import cmd_health

        checks = {
            "config": {"ok": True},
            "auth": {"ok": True},
            "gateway": {"ok": True, "latency_ms": 12},
            "umrs": {"ok": None, "message": "direct UMRS not configured"},
        }

        with patch("r4._cli.health.run_full_diagnostics", return_value=checks):
            cmd_health(Namespace(health_command="full", as_json=True))

        assert json.loads(capsys.readouterr().out) == checks

    def test_cmd_health_account_reports_credential_status(self, capsys):
        from r4.cli import cmd_health

        status = {
            "ok": True,
            "profile": "research",
            "gateway_url": "http://gateway.example:8080",
            "project_id": "proj-alpha",
            "auth_configured": True,
            "accepted": True,
        }

        with patch("r4._cli.health.check_account", return_value=status):
            cmd_health(Namespace(health_command="account", as_json=False))

        output = capsys.readouterr().out
        assert "R4 Account" in output
        assert "credentials accepted by Gateway" in output
        assert "Profile: research" in output


def test_doctor_aliases_health_full(capsys):
    """Doctor now runs full diagnostics + local environment checks.

    No longer a thin alias for ``r4 health full`` — includes R4.md
    presence, env-var conflicts, profile integrity, and toolchain checks.
    """
    from r4._cli.doctor import DoctorCommand

    checks = {"gateway": {"ok": True, "latency_ms": 10}}

    with patch("r4._cli.doctor.run_full_diagnostics", return_value=checks):
        rc = DoctorCommand().handle(Namespace(as_json=False))

    captured = capsys.readouterr()
    assert rc == 0
    assert "R4 Doctor" in captured.out
    assert "local" in captured.out.lower()


def test_ask_agent_shortcut_no_longer_rewrites_action(monkeypatch):
    """r4 ask agent ... no longer rewrites (ask command removed)."""
    from r4 import cli

    monkeypatch.setattr(sys, "argv", ["r4", "ask", "agent", "start", "qwen3-8b"])

    cli._rewrite_agent_shortcut()

    # argv unchanged — ask path removed
    assert sys.argv == ["r4", "ask", "agent", "start", "qwen3-8b"]


def test_ask_agent_shortcut_no_longer_rewrites_how(monkeypatch):
    """r4 ask agent ... no longer rewrites (ask command removed)."""
    from r4 import cli

    monkeypatch.setattr(sys, "argv", ["r4", "ask", "agent", "How", "do", "I", "check", "health?"])

    cli._rewrite_agent_shortcut()

    assert sys.argv == ["r4", "ask", "agent", "How", "do", "I", "check", "health?"]


def test_ask_agent_shortcut_no_longer_rewrites_flags(monkeypatch):
    """r4 ask agent ... no longer rewrites (ask command removed)."""
    from r4 import cli

    monkeypatch.setattr(
        sys,
        "argv",
        ["r4", "ask", "agent", "start", "qwen3-8b", "--model", "qwen3-8b"],
    )

    cli._rewrite_agent_shortcut()

    assert sys.argv == [
        "r4",
        "ask",
        "agent",
        "start",
        "qwen3-8b",
        "--model",
        "qwen3-8b",
    ]


def test_cmd_agent_defaults_to_chat(monkeypatch):
    from r4._cli import agent as agent_cli

    args = Namespace(agent_command=None)
    chat = MagicMock()
    monkeypatch.setitem(agent_cli._AGENT_DISPATCH, "chat", chat)

    agent_cli.cmd_agent(args)

    chat.assert_called_once_with(args)
    assert args.agent_command == "chat"
    assert args.mode == "interactive"


def test_cmd_agent_run_handles_health_objective(capsys):
    from r4._cli.agent import cmd_agent_run

    client = MagicMock()
    client.readiness.return_value = {"ready": True, "checks": {"umrs": "ok"}}

    args = Namespace(
        objective="Check platform health",
        model=None,
        input=None,
        stream=False,
        as_json=True,
        egress_policy="ask_always",
    )

    with patch("r4.client.Client", return_value=client):
        cmd_agent_run(args)

    data = json.loads(capsys.readouterr().out)
    assert data == {"action": "health", "result": {"ready": True, "checks": {"umrs": "ok"}}}
    client.readiness.assert_called_once_with()


def test_cmd_agent_run_handles_endpoint_up(capsys):
    from r4._cli.agent import cmd_agent_run

    client = MagicMock()
    client.provision_endpoint.return_value = {
        "instance_id": "job-999",
        "status": "ready",
        "endpoint_url": "http://ng-dgx-m0-08:8001",
    }

    args = Namespace(
        objective="Start qwen3-8b",
        model=None,
        input=None,
        stream=False,
        as_json=True,
        egress_policy="ask_always",
    )

    with patch("r4.client.Client", return_value=client):
        cmd_agent_run(args)

    data = json.loads(capsys.readouterr().out)
    assert data["action"] == "endpoint_up"
    assert data["model"] == "qwen3-8b"
    client.provision_endpoint.assert_called_once()


def test_cmd_agent_run_handles_endpoint_down(capsys, monkeypatch):
    from r4._cli.agent import cmd_agent_run

    client = MagicMock()
    client.stop_endpoint.return_value = {"instance_id": "174952", "status": "deprovisioned"}

    args = Namespace(
        objective="Stop endpoint 174952",
        model=None,
        input=None,
        stream=False,
        as_json=True,
        egress_policy="ask_always",
    )

    with patch("r4.client.Client", return_value=client):
        cmd_agent_run(args)

    data = json.loads(capsys.readouterr().out)
    assert data["action"] == "endpoint_down"
    assert data["instance_id"] == "174952"


def test_strip_think_tags():
    from r4._cli.agent import _strip_think_tags

    raw = "<think>\nI need to reason about this.\n</think>\n\nHere is the answer."
    assert _strip_think_tags(raw) == "Here is the answer."

    no_tags = "Just a plain response."
    assert _strip_think_tags(no_tags) == "Just a plain response."


def test_format_thinking_shows_reasoning():
    from r4._cli.agent import _format_thinking

    raw = "<think>\nStep 1: check health.\nStep 2: respond.\n</think>\n\nHere is the answer."
    result = _format_thinking(raw)
    # Thinking lines are rendered with dim/italic ANSI and 💭 prefix
    assert "💭" in result
    assert "Step 1: check health." in result
    assert "Step 2: respond." in result
    # Visible part is still present
    assert "Here is the answer." in result

    # No thinking → just the visible text
    plain = "Just a plain response."
    assert _format_thinking(plain) == plain


def test_extract_model_from_objective():
    from r4._cli.agent import _extract_model_from_objective

    assert _extract_model_from_objective("Start qwen3-8b") == "qwen3-8b"
    assert _extract_model_from_objective("Launch the qwen3-8b model") == "qwen3-8b"
    assert _extract_model_from_objective("Stop endpoint 174952") == "174952"


class TestCLIEndpointProvisionTimeout:
    """Regression tests for endpoint provisioning timeout propagation."""

    def test_cmd_up_passes_timeout_to_initial_provision_request(self):
        from r4.cli import cmd_up

        client = MagicMock()
        client.provision_endpoint.return_value = {
            "instance_id": "job-123",
            "status": "provisioning",
        }
        client.get_endpoint_status.return_value = {
            "status": "ready",
            "endpoint_url": "http://ng-dgx-m0-08:8001",
        }

        with (
            patch("r4._cli._helpers._preflight_check"),
            patch("r4.client.Client", return_value=client),
        ):
            cmd_up(Namespace(model="bonsai-8b", timeout=900, as_json=False))

        assert client.provision_endpoint.call_args.kwargs["timeout"] == 900

    def test_cmd_up_reports_initial_provision_progress(self, capsys):
        from r4.cli import cmd_up

        client = MagicMock()
        client.provision_endpoint.return_value = {
            "instance_id": "job-123",
            "status": "provisioning",
        }
        client.get_endpoint_status.return_value = {
            "status": "ready",
            "endpoint_url": "http://ng-dgx-m0-08:8001",
        }

        with (
            patch("r4._cli._helpers._preflight_check"),
            patch("r4.client.Client", return_value=client),
        ):
            cmd_up(Namespace(model="bonsai-8b", timeout=90, as_json=False))

        output = capsys.readouterr().out
        assert "Submitting provisioning request to UMRS..." in output
        assert "status=submitting" in output
        assert "detail=contacting UMRS" in output
        assert "Provision request accepted" in output

    def test_cmd_up_wait_output_reports_progress_status(self, capsys):
        from r4.cli import cmd_up

        client = MagicMock()
        client.provision_endpoint.return_value = {
            "instance_id": "job-123",
            "status": "provisioning",
        }
        client.get_endpoint_status.side_effect = [
            {
                "status": "provisioning",
                "job_status": "allocating node",
            },
            {
                "status": "ready",
                "endpoint_url": "http://ng-dgx-m0-08:8001",
            },
        ]

        with (
            patch("r4._cli._helpers._preflight_check"),
            patch("r4.client.Client", return_value=client),
            patch("time.sleep", return_value=None),
        ):
            cmd_up(Namespace(model="bonsai-8b", timeout=90, as_json=False))

        output = capsys.readouterr().out
        assert "Waiting for endpoint to be ready..." in output
        assert "status=provisioning" in output
        assert "detail=allocating node" in output
        assert "Endpoint ready!" in output

    def test_cmd_up_wait_output_prefers_enriched_detail(self, capsys):
        from r4.cli import cmd_up

        client = MagicMock()
        client.provision_endpoint.return_value = {
            "instance_id": "job-123",
            "status": "provisioning",
        }
        client.get_endpoint_status.side_effect = [
            {
                "status": "starting",
                "detail": "starting container / loading model",
                "active_attempt_status_detail": "polling readiness (submitted to backend)",
            },
            {
                "status": "ready",
                "endpoint_url": "http://ng-dgx-m0-08:8001",
            },
        ]

        with (
            patch("r4._cli._helpers._preflight_check"),
            patch("r4.client.Client", return_value=client),
            patch("time.sleep", return_value=None),
        ):
            cmd_up(Namespace(model="bonsai-8b", timeout=90, as_json=False))

        output = capsys.readouterr().out
        assert "status=starting" in output
        assert "detail=starting container / loading model" in output

    def test_cmd_endpoint_up_passes_timeout_to_initial_provision_request(self):
        from r4.cli import cmd_endpoint

        client = MagicMock()
        client.provision_endpoint.return_value = {
            "instance_id": "job-123",
            "status": "provisioning",
        }

        args = Namespace(
            endpoint_command="up",
            model="bonsai-8b",
            tier="user",
            mount_mcp=None,
            mount_data=None,
            mount_rag=None,
            egress_domains=None,
            wait=False,
            timeout=900,
            as_json=False,
        )

        with (
            patch("r4._cli._helpers._preflight_check"),
            patch("r4.client.Client", return_value=client),
        ):
            cmd_endpoint(args)

        assert client.provision_endpoint.call_args.kwargs["timeout"] == 900

    def test_cmd_endpoint_up_passes_mounts_and_egress(self):
        from r4.cli import cmd_endpoint

        client = MagicMock()
        client.provision_endpoint.return_value = {
            "instance_id": "job-123",
            "status": "provisioning",
        }

        args = Namespace(
            endpoint_command="up",
            model="bonsai-8b",
            tier="project",
            mount_mcp=["/home/alice/tools:/mcp-servers/tools"],
            mount_data="/project/data:/project",
            mount_rag="/project/rag-index:/rag-index",
            egress_domains="api.example.com, data.example.org",
            wait=False,
            timeout=900,
            as_json=False,
            max_model_len=None,
        )

        with (
            patch("r4._cli._helpers._preflight_check"),
            patch("r4.client.Client", return_value=client),
        ):
            cmd_endpoint(args)

        kwargs = client.provision_endpoint.call_args.kwargs
        assert kwargs["tier"] == "project"
        assert kwargs["mounts"] == {
            "mcp_servers": [{"source": "/home/alice/tools", "target": "/mcp-servers/tools"}],
            "project_data": {"source": "/project/data", "target": "/project"},
            "rag_index": {"source": "/project/rag-index", "target": "/rag-index"},
        }
        assert kwargs["egress_domains"] == ["api.example.com", "data.example.org"]

    def test_cmd_endpoint_up_json_returns_nonzero_on_failed_status(self, capsys):
        from r4.cli import cmd_endpoint

        client = MagicMock()
        client.provision_endpoint.return_value = {
            "instance_id": "job-123",
            "status": "failed",
            "error": "Timeout after 594s",
        }

        args = Namespace(
            endpoint_command="up",
            model="e5-base-v2",
            tier="user",
            mount_mcp=None,
            mount_data=None,
            mount_rag=None,
            egress_domains=None,
            wait=True,
            timeout=900,
            as_json=True,
            max_model_len=None,
        )

        with (
            patch("r4._cli._helpers._preflight_check"),
            patch("r4.client.Client", return_value=client),
        ):
            rc = cmd_endpoint(args)

        assert rc == 1
        assert json.loads(capsys.readouterr().out)["status"] == "failed"

    def test_cmd_up_delegates_to_pool_file(self):
        from r4.cli import cmd_up

        args = Namespace(
            model=None,
            pool_file="pool.yaml",
            continue_on_error=False,
            dry_run=False,
            timeout=600,
            as_json=False,
            max_model_len=None,
        )

        with (
            patch("r4._cli._helpers._preflight_check"),
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stdout.isatty", return_value=False),
            patch("r4._cli.up._cmd_up_pool", return_value=0) as pool_cmd,
        ):
            rc = cmd_up(args)

        assert rc == 0
        pool_cmd.assert_called_once()

    def test_cmd_up_interactive_can_choose_multiple(self):
        from r4.cli import cmd_up

        args = Namespace(
            model=None,
            pool_file=None,
            continue_on_error=False,
            dry_run=False,
            timeout=600,
            as_json=False,
            max_model_len=None,
        )

        with (
            patch("r4._cli._helpers._preflight_check"),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("r4._cli.up._prompt_up_mode", return_value="multiple"),
            patch("r4._cli.up._cmd_up_pool", return_value=0) as pool_cmd,
        ):
            rc = cmd_up(args)

        assert rc == 0
        pool_cmd.assert_called_once()

    def test_cmd_down_delegates_to_pool_file(self):
        from r4.cli import cmd_down

        args = Namespace(
            instance_id=None,
            pool_file="pool.yaml",
            all=False,
            continue_on_error=False,
            force=False,
            as_json=False,
        )

        with patch("r4._cli.pool._cmd_pool_down", return_value=0) as pool_cmd:
            rc = cmd_down(args)

        assert rc == 0
        pool_cmd.assert_called_once()


class TestCLIResolveEndpointModels:
    """Tests for ``_resolve_endpoint_models`` (multi-model parsing/validation)."""

    def _ns(self, **kw):
        defaults = {"model": None, "mode": None, "lora": None}
        defaults.update(kw)
        return Namespace(**defaults)

    def test_single_model_default_mode_is_single(self):
        from r4.cli import _resolve_endpoint_models

        models, mode, loras = _resolve_endpoint_models(
            self._ns(model=["foo"]), action="endpoint up"
        )
        assert models == ["foo"]
        assert mode == "single"
        assert loras == []

    def test_back_compat_string_model(self):
        from r4.cli import _resolve_endpoint_models

        models, mode, _ = _resolve_endpoint_models(self._ns(model="bonsai-8b"), action="up")
        assert models == ["bonsai-8b"]
        assert mode == "single"

    def test_repeated_model_flag_with_co_located(self):
        from r4.cli import _resolve_endpoint_models

        models, mode, loras = _resolve_endpoint_models(
            self._ns(model=["a", "b"], mode="co-located"),
            action="endpoint up",
        )
        assert models == ["a", "b"]
        assert mode == "co-located"
        assert loras == []

    def test_comma_separated_model_value(self):
        from r4.cli import _resolve_endpoint_models

        models, mode, _ = _resolve_endpoint_models(
            self._ns(model=["a,b,c"], mode="co-located"),
            action="endpoint up",
        )
        assert models == ["a", "b", "c"]
        assert mode == "co-located"

    def test_lora_implies_multi_lora_mode(self):
        from r4.cli import _resolve_endpoint_models

        models, mode, loras = _resolve_endpoint_models(
            self._ns(model=["base"], lora=["a=/loras/a", "b=/loras/b"]),
            action="endpoint up",
        )
        assert models == ["base"]
        assert mode == "multi-lora"
        assert loras == [
            {"name": "a", "source": "/loras/a"},
            {"name": "b", "source": "/loras/b"},
        ]

    def test_multi_model_without_mode_fails(self):
        from r4.cli import _resolve_endpoint_models

        with pytest.raises(SystemExit):
            _resolve_endpoint_models(self._ns(model=["a", "b"]), action="endpoint up")

    def test_lora_without_equals_fails(self):
        from r4.cli import _resolve_endpoint_models

        with pytest.raises(SystemExit):
            _resolve_endpoint_models(
                self._ns(model=["base"], lora=["bad-spec"]),
                action="endpoint up",
            )

    def test_multi_lora_with_multiple_base_models_fails(self):
        from r4.cli import _resolve_endpoint_models

        with pytest.raises(SystemExit):
            _resolve_endpoint_models(
                self._ns(model=["a", "b"], lora=["x=/p"]),
                action="endpoint up",
            )

    def test_explicit_multi_lora_without_lora_fails(self):
        from r4.cli import _resolve_endpoint_models

        with pytest.raises(SystemExit):
            _resolve_endpoint_models(
                self._ns(model=["base"], mode="multi-lora"),
                action="endpoint up",
            )

    def test_single_mode_with_multiple_models_fails(self):
        from r4.cli import _resolve_endpoint_models

        with pytest.raises(SystemExit):
            _resolve_endpoint_models(
                self._ns(model=["a", "b"], mode="single"),
                action="endpoint up",
            )

    def test_duplicate_lora_name_fails(self):
        from r4.cli import _resolve_endpoint_models

        with pytest.raises(SystemExit):
            _resolve_endpoint_models(
                self._ns(model=["base"], lora=["a=/p1", "a=/p2"]),
                action="endpoint up",
            )


class TestCLIEndpointReferenceHints:
    """Regression tests for endpoint row-number hints."""

    def test_cmd_endpoint_list_handles_null_table_fields(self, capsys):
        from r4.cli import cmd_endpoint

        client = MagicMock()
        client.list_endpoints.return_value = {
            "endpoints": [
                {
                    "instance_id": "174319",
                    "tier": None,
                    "model": None,
                    "served_model_name": "bonsai-8b",
                    "status": None,
                    "endpoint_url": None,
                }
            ]
        }

        args = Namespace(endpoint_command="list", tier=None, all=False, as_json=False)

        with (
            patch("r4._cli._helpers._preflight_check"),
            patch("r4.client.Client", return_value=client),
        ):
            cmd_endpoint(args)

        output = capsys.readouterr().out
        assert "INSTANCE ID" in output
        assert "174319" in output
        assert "n/a" in output
        assert "bonsai-8b" in output
        assert "unknown" in output

    def test_cmd_endpoint_down_suggests_instance_id_for_row_number(self, capsys):
        from r4.cli import cmd_endpoint
        from r4.errors import R4Error

        client = MagicMock()
        client.stop_endpoint.side_effect = R4Error(
            "Failed to stop endpoint: API error: 404",
            details={"status_code": 404},
        )
        client.list_endpoints.return_value = {
            "endpoints": [
                {
                    "instance_id": "174012",
                    "model": "bonsai-8b",
                    "status": "ready",
                }
            ]
        }

        args = Namespace(endpoint_command="down", instance_id="1", force=False)

        with (
            patch("r4._cli._helpers._preflight_check"),
            patch("r4.client.Client", return_value=client),
            pytest.raises(SystemExit) as exc_info,
        ):
            cmd_endpoint(args)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Failed to stop endpoint: API error: 404" in err
        assert "Re-run `r4 endpoint down 174012`." in err

    def test_cmd_endpoint_down_skips_hint_for_non_404_errors(self):
        from r4.cli import cmd_endpoint
        from r4.errors import R4Error

        client = MagicMock()
        client.stop_endpoint.side_effect = R4Error("Failed to stop endpoint: boom")

        args = Namespace(endpoint_command="down", instance_id="1", force=False)

        with patch("r4.client.Client", return_value=client), pytest.raises(SystemExit):
            cmd_endpoint(args)

        client.list_endpoints.assert_not_called()

    def test_cmd_endpoint_down_public_removes_public_endpoint_config(self, capsys):
        from r4.cli import cmd_endpoint

        args = Namespace(endpoint_command="down", instance_id=None, force=False, public=True)

        with patch("r4._cli.down._cmd_down_public", return_value=0) as mock_down_public:
            rc = cmd_endpoint(args)

        assert rc == 0
        mock_down_public.assert_called_once_with(args)
        out = capsys.readouterr().out
        assert out == ""


class TestCLIConfigContext:
    """Tests for local user/project context management."""

    def test_config_context_shows_local_user_and_project(self):
        from r4.config import ProfileConfigStore

        ProfileConfigStore().save(
            "default",
            gateway_url="http://gateway.example:8080",
            model="rivault-gpt-oss-static",
            project_id="proj-alpha",
            umrs_username="alice",
        )

        result = subprocess.run(
            [sys.executable, "-m", "r4.cli", "config", "context"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        assert "Current local context:" in result.stdout
        assert "Profile: default" in result.stdout
        assert "Project: proj-alpha" in result.stdout
        assert "UMRS Username: alice" in result.stdout

    def test_config_context_updates_local_user_and_project(self):
        from r4.config import ProfileConfigStore
        from r4.project import get_project_manager

        ProfileConfigStore().save(
            "default",
            gateway_url="http://gateway.example:8080",
            model="rivault-gpt-oss-static",
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "r4.cli",
                "config",
                "context",
                "--project",
                "proj-beta",
                "--user",
                "bob",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr

        stored = ProfileConfigStore().load("default")
        assert stored["project_id"] == "proj-beta"
        assert stored["umrs_username"] == "bob"
        assert get_project_manager().get_current() == "proj-beta"
        assert "Updated local context for profile 'default':" in result.stdout
        assert "Project: proj-beta" in result.stdout
        assert "UMRS Username: bob" in result.stdout

    def test_config_context_can_clear_local_user_and_project(self):
        from r4.config import ProfileConfigStore
        from r4.project import ProjectConfig, get_project_manager

        ProfileConfigStore().save(
            "default",
            gateway_url="http://gateway.example:8080",
            model="rivault-gpt-oss-static",
            project_id="proj-alpha",
            umrs_username="alice",
        )
        get_project_manager().create(
            ProjectConfig(
                project_id="proj-alpha",
                gateway_url="http://gateway.example:8080",
                model="rivault-gpt-oss-static",
            )
        )
        get_project_manager().set_current("proj-alpha")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "r4.cli",
                "config",
                "context",
                "--clear-project",
                "--clear-user",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr

        stored = ProfileConfigStore().load("default")
        assert "project_id" not in stored
        assert "umrs_username" not in stored
        assert get_project_manager().get_current() is None
        assert "Project: (not set)" in result.stdout
        assert "UMRS Username: (not set)" in result.stdout

    def test_config_context_list_shows_saved_contexts(self):
        from r4.auth import get_auth_manager
        from r4.config import ProfileConfigStore
        from r4.project import ProjectConfig, get_project_manager

        ProfileConfigStore().save(
            "default",
            gateway_url="http://gateway.example:8080",
            model="rivault-gpt-oss-static",
            project_id="proj-alpha",
            umrs_username="alice",
        )
        ProfileConfigStore().save(
            "research",
            gateway_url="http://research.example:8080",
            model="bonsai-8b",
            project_id="proj-beta",
            umrs_username="bob",
        )
        get_project_manager().create(ProjectConfig(project_id="proj-beta", name="Beta"))
        get_auth_manager("research").save_api_key("sk-test")

        result = subprocess.run(
            [sys.executable, "-m", "r4.cli", "config", "context", "list"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        assert "Saved profile contexts:" in result.stdout
        assert "* default" in result.stdout
        assert "research" in result.stdout
        assert "proj-beta (Beta)" in result.stdout
        assert "Auth configured: yes" in result.stdout

    def test_config_context_list_json_format(self):
        from r4.config import ProfileConfigStore

        ProfileConfigStore().save(
            "research",
            gateway_url="http://research.example:8080",
            model="bonsai-8b",
            project_id="proj-beta",
            umrs_username="bob",
        )

        result = subprocess.run(
            [sys.executable, "-m", "r4.cli", "config", "context", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["active_profile"] == "default"
        contexts = {row["profile"]: row for row in data["contexts"]}
        assert contexts["research"]["project_id"] == "proj-beta"
        assert contexts["research"]["umrs_username"] == "bob"


class TestCLIAgent:
    """Tests for agent CLI workflow planning and execution."""

    def test_cmd_agent_create_uses_planning_only_mode_and_saves_json(self, tmp_path):
        from r4.agent import AgentMode, StepType, WorkflowDefinition, WorkflowStep
        from r4.cli import cmd_agent_create

        workflow = WorkflowDefinition(
            name="planned-analysis",
            description="Analyze a dataset",
            project_id="proj-alpha",
            owner_user_id="alice",
            steps=[
                WorkflowStep(
                    id="step_1",
                    name="Search references",
                    description="Search project references",
                    step_type=StepType.RAG_QUERY,
                    config={"query": "dataset references"},
                )
            ],
        )
        output_path = tmp_path / "workflow.json"
        captured: dict[str, object] = {}

        class FakeAgent:
            def __init__(self, client, config):
                captured["mode"] = config.mode
                self.planner = Namespace(plan=lambda objective: workflow)
                self.executor = Namespace(
                    execute_workflow=lambda _: pytest.fail("workflow should not execute")
                )

        args = Namespace(
            description="Analyze the dataset",
            model=None,
            name="custom-analysis",
            output="json",
            save=str(output_path),
            as_json=False,
        )

        with (
            patch("r4.client.Client", return_value=object()),
            patch("r4.agent.SciWorkflowAgent", FakeAgent),
        ):
            cmd_agent_create(args)

        assert captured["mode"] == AgentMode.PLANNING_ONLY
        saved = json.loads(output_path.read_text(encoding="utf-8"))
        assert saved["name"] == "custom-analysis"
        assert saved["project_id"] == "proj-alpha"
        assert saved["steps"][0]["id"] == "step_1"
        assert saved["steps"][0]["step_type"] == StepType.RAG_QUERY.value

    def test_cmd_agent_create_run_executes_planned_workflow(self, capsys):
        from r4.agent import (
            AgentMode,
            StepType,
            WorkflowDefinition,
            WorkflowExecution,
            WorkflowStatus,
            WorkflowStep,
        )
        from r4.cli import cmd_agent_create

        workflow = WorkflowDefinition(
            name="planned-analysis",
            description="Analyze a dataset",
            project_id="proj-alpha",
            owner_user_id="alice",
            steps=[
                WorkflowStep(
                    id="step_1",
                    name="Search references",
                    description="Search project references",
                    step_type=StepType.RAG_QUERY,
                )
            ],
        )
        captured: dict[str, object] = {}

        class FakeAgent:
            def __init__(self, client, config):
                captured["mode"] = config.mode
                self.planner = Namespace(plan=self._plan)
                self.executor = Namespace(execute_workflow=self._execute)

            def _plan(self, objective):
                captured["objective"] = objective
                return workflow

            def _execute(self, planned_workflow):
                captured["workflow"] = planned_workflow
                execution = WorkflowExecution(
                    workflow=planned_workflow, status=WorkflowStatus.COMPLETED
                )
                execution.complete()
                return execution

        args = Namespace(
            description="Analyze the dataset",
            model=None,
            name=None,
            output="run",
            save=None,
            as_json=False,
        )

        with (
            patch("r4.client.Client", return_value=object()),
            patch("r4.agent.SciWorkflowAgent", FakeAgent),
        ):
            cmd_agent_create(args)

        assert captured["mode"] == AgentMode.PLANNING_ONLY
        assert captured["objective"] == "Analyze the dataset"
        assert captured["workflow"] is workflow

        output = capsys.readouterr().out
        assert "Executing workflow: planned-analysis" in output
        assert "Status: completed" in output
        assert f"Workflow ID: {workflow.id}" in output


class TestCLIChat:
    """Tests for chat command input-mode selection."""

    def test_cmd_chat_companion_launches_companion_ui(self):
        from r4.cli import cmd_chat

        args = Namespace(
            message=None,
            model="Bonsai-8B",
            system="stay concise",
            stream=False,
            temperature=0.7,
            max_tokens=None,
            interactive=False,
            companion=True,
            resume="sess-1",
            skill="hpc",
            fs_root=None,
            teamup=False,
        )

        fake_client = object()
        with (
            patch("r4.client.Client", return_value=fake_client),
            patch("r4.companion.ui.launch") as launch,
        ):
            cmd_chat(args)

        launch.assert_called_once_with(
            client=fake_client,
            session_id="sess-1",
            model="Bonsai-8B",
            skill="hpc",
            system="stay concise",
        )

    def test_cmd_chat_defaults_to_companion_on_tty_without_message(self):
        from r4.cli import cmd_chat

        args = Namespace(
            message=None,
            model=None,
            system=None,
            stream=False,
            temperature=0.7,
            max_tokens=None,
            interactive=False,
            fs_root=None,
            teamup=False,
        )

        fake_client = object()
        with (
            patch("r4.client.Client", return_value=fake_client),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("r4.companion.ui.launch") as launch,
        ):
            cmd_chat(args)

        launch.assert_called_once_with(
            client=fake_client,
            session_id=None,
            model=None,
            skill=None,
            system=None,
        )


class TestCLICompanion:
    """Tests for companion command routing."""

    def test_cmd_companion_launches_companion_ui(self):
        from r4.cli import cmd_companion

        args = Namespace(
            companion_command=None,
            model="Bonsai-8B",
            resume="sess-1",
            skill="research",
        )

        fake_client = object()
        with (
            patch("r4.client.Client", return_value=fake_client),
            patch("r4.companion.ui.launch") as launch,
        ):
            cmd_companion(args)

        launch.assert_called_once_with(
            client=fake_client,
            session_id="sess-1",
            model="Bonsai-8B",
            skill="research",
            source_name=None,
        )

    def test_cmd_chat_reads_stdin_when_not_a_tty(self, capsys):
        from r4.cli import cmd_chat

        response = Namespace(content="hello from stdin")
        fake_client = Namespace(chat=MagicMock(return_value=response))
        args = Namespace(
            message=None,
            model="test-model",
            system="be concise",
            stream=False,
            temperature=0.2,
            max_tokens=128,
            interactive=False,
            fs_root=None,
            teamup=False,
        )

        with (
            patch("r4.client.Client", return_value=fake_client),
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stdout.isatty", return_value=False),
            patch("sys.stdin.read", return_value="summarize this"),
        ):
            cmd_chat(args)

        fake_client.chat.assert_called_once_with(
            "summarize this",
            model="test-model",
            system="be concise",
            temperature=0.2,
            max_tokens=128,
            task=None,
            agent=None,
        )
        assert "hello from stdin" in capsys.readouterr().out

    def test_cmd_chat_secure_from_project_security_level(self):
        """Project security_level=high activates secure mode in chat — routed to companion."""
        from r4.cli import cmd_chat
        from r4.project import ProjectConfig, get_project_manager

        pm = get_project_manager()
        pm.create(
            ProjectConfig(
                project_id="sec-proj",
                metadata={"security_level": "high"},
            )
        )
        pm.set_current("sec-proj")

        args = Namespace(
            message=None,
            model=None,
            system=None,
            stream=False,
            temperature=0.7,
            max_tokens=None,
            interactive=False,
            fs_root=None,
            teamup=False,
        )

        fake_client = object()
        with (
            patch("r4.client.Client", return_value=fake_client),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("r4.companion.ui.launch") as launch,
        ):
            cmd_chat(args)

        launch.assert_called_once_with(
            client=fake_client,
            session_id=None,
            model=None,
            skill=None,
            system=None,
        )

    def test_cmd_chat_secure_from_env_var(self, monkeypatch):
        """R4_SECURITY_LEVEL=high activates secure mode in chat — routed to companion."""
        from r4.cli import cmd_chat

        monkeypatch.setenv("R4_SECURITY_LEVEL", "high")

        args = Namespace(
            message=None,
            model=None,
            system=None,
            stream=False,
            temperature=0.7,
            max_tokens=None,
            interactive=False,
            fs_root=None,
            teamup=False,
        )

        fake_client = object()
        with (
            patch("r4.client.Client", return_value=fake_client),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("r4.companion.ui.launch") as launch,
        ):
            cmd_chat(args)

        launch.assert_called_once_with(
            client=fake_client,
            session_id=None,
            model=None,
            skill=None,
            system=None,
        )

    def test_cmd_chat_standard_project_uses_companion(self):
        """Project security_level=standard keeps default companion routing."""
        from r4.cli import cmd_chat
        from r4.project import ProjectConfig, get_project_manager

        pm = get_project_manager()
        pm.create(
            ProjectConfig(
                project_id="std-proj",
                metadata={"security_level": "standard"},
            )
        )
        pm.set_current("std-proj")

        args = Namespace(
            message=None,
            model=None,
            system=None,
            stream=False,
            temperature=0.7,
            max_tokens=None,
            interactive=False,
            fs_root=None,
            teamup=False,
        )

        fake_client = object()
        with (
            patch("r4.client.Client", return_value=fake_client),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch("r4.companion.ui.launch") as launch,
        ):
            cmd_chat(args)

        launch.assert_called_once_with(
            client=fake_client,
            session_id=None,
            model=None,
            skill=None,
            system=None,
        )
