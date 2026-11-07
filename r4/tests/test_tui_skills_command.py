from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from r4.companion.skills import SkillRegistry
from r4.tui.commands import _cmd_endpoint, _cmd_models, _cmd_rag, _cmd_skills, _cmd_up


def _make_app() -> SimpleNamespace:
    return SimpleNamespace(renderer=MagicMock(), skill_registry=SkillRegistry())


def _info_lines(app) -> list[str]:
    return [call.args[0] for call in app.renderer.info.call_args_list]


def _has_line(lines: list[str], *needles: str) -> bool:
    """Any line containing all needles."""
    return any(all(n in line for n in needles) for line in lines)


def test_skills_list_includes_numbered_selectors() -> None:
    app = _make_app()
    # Numbered selectors are 1-based over list_all(); order shifts as
    # builtin skills change, so derive the index from the registry.
    names = [skill.name for skill in app.skill_registry.list_all()]
    idx = names.index("research") + 1

    _cmd_skills(app, [])

    info_lines = [call.args[0] for call in app.renderer.info.call_args_list]
    assert any(f"[{idx}]" in line and "research" in line for line in info_lines)
    assert any("/skills <number|name>" in line for line in info_lines)


def test_skills_numeric_selection_toggles_skill() -> None:
    app = _make_app()
    # Numbered selectors are 1-based over list_all(); order shifts as new
    # builtin skills land, so derive the expectation from the registry.
    target = app.skill_registry.list_all()[1].name

    _cmd_skills(app, ["2"])
    assert target in {skill.name for skill in app.skill_registry.list_active()}
    app.renderer.system.assert_called_with(f"skill activated: {target}")

    _cmd_skills(app, ["2"])
    assert target not in {skill.name for skill in app.skill_registry.list_active()}
    app.renderer.system.assert_called_with(f"skill deactivated: {target}")


def test_skills_name_selection_toggles_skill() -> None:
    app = _make_app()

    _cmd_skills(app, ["hpc"])

    assert "hpc" in {skill.name for skill in app.skill_registry.list_active()}
    app.renderer.system.assert_called_with("skill activated: hpc")


def test_skills_explicit_on_off_accept_numbers() -> None:
    app = _make_app()
    target = app.skill_registry.list_all()[1].name

    _cmd_skills(app, ["on", "2"])
    assert target in {skill.name for skill in app.skill_registry.list_active()}

    _cmd_skills(app, ["off", "2"])
    assert target not in {skill.name for skill in app.skill_registry.list_active()}


def test_skills_invalid_selection_reports_valid_names() -> None:
    app = _make_app()

    _cmd_skills(app, ["999"])

    message = app.renderer.error.call_args.args[0]
    assert "unknown skill: 999" in message
    assert "research" in message


def test_rag_docs_lists_indexed_documents() -> None:
    app = _make_app()
    app.client = SimpleNamespace(
        config=SimpleNamespace(project_id="demo"),
        rag=SimpleNamespace(
            list_documents=MagicMock(
                return_value=[
                    {
                        "document_id": "doc-1",
                        "filename": "paper.pdf",
                        "status": "indexed",
                        "chunks_created": 4,
                    }
                ]
            )
        ),
    )

    _cmd_rag(app, ["docs"])

    app.client.rag.list_documents.assert_called_once_with(project_id="demo")
    info_lines = [call.args[0] for call in app.renderer.info.call_args_list]
    assert any("indexed documents for project demo" in line for line in info_lines)
    assert any("doc-1" in line and "paper.pdf" in line for line in info_lines)


def _app_with_endpoints(endpoints):
    app = _make_app()
    app.client = SimpleNamespace(list_endpoints=MagicMock(return_value={"endpoints": endpoints}))
    return app


def test_endpoint_default_lists_and_shows_subcommands() -> None:
    app = _app_with_endpoints(
        [
            {
                "instance_id": "ep-1",
                "status": "ready",
                "model": "qwen",
                "backend": "slurm",
                "host": "node1",
                "port": 8001,
            }
        ]
    )

    _cmd_endpoint(app, [])

    app.client.list_endpoints.assert_called_once()
    call_kwargs = app.client.list_endpoints.call_args
    assert call_kwargs.kwargs.get("include_stopped") is False
    lines = _info_lines(app)
    assert _has_line(lines, "endpoints (1 active):")
    assert _has_line(lines, "ep-1", "qwen")
    assert _has_line(lines, "/endpoint up")


def test_endpoint_status_fetches_specific_endpoint() -> None:
    app = _make_app()
    app.client = SimpleNamespace(
        get_endpoint_status=MagicMock(
            return_value={
                "instance_id": "ep-1",
                "status": "starting",
                "model": "qwen",
                "backend": "slurm",
                "detail": "waiting for service readiness",
            }
        )
    )

    _cmd_endpoint(app, ["status", "ep-1"])

    app.client.get_endpoint_status.assert_called_once()
    assert app.client.get_endpoint_status.call_args.args[0] == "ep-1"
    info_lines = [call.args[0] for call in app.renderer.info.call_args_list]
    assert any("ep-1" in line and "starting" in line for line in info_lines)
    assert any("waiting for service readiness" in line for line in info_lines)


def test_endpoint_down_passes_instance_id() -> None:
    app = _make_app()
    app.client = SimpleNamespace(
        list_endpoints=MagicMock(
            return_value={
                "endpoints": [
                    {
                        "instance_id": "ep-1",
                        "status": "ready",
                        "model": "qwen",
                        "backend": "slurm",
                    }
                ]
            }
        ),
        stop_endpoint=MagicMock(return_value={"instance_id": "ep-1", "status": "deprovisioned"}),
    )

    _cmd_endpoint(app, ["down", "ep-1"])

    app.client.stop_endpoint.assert_called_once()
    call = app.client.stop_endpoint.call_args
    assert call.args[0] == "ep-1"
    assert call.kwargs.get("force") is False


def _provisioned_endpoints_fixture():
    return [
        {
            "instance_id": "14528",
            "status": "ready",
            "host": "localhost",
            "port": 8001,
            "models": ["endpoint-a", "endpoint-b"],
        },
        {
            "instance_id": "14529",
            "status": "failed",
            "model": "failed-model",
        },
    ]


def _app_with_endpoint_models(endpoints, served):
    app = _make_app()
    app.session = SimpleNamespace(model=served)
    app.client = SimpleNamespace(
        config=SimpleNamespace(model="endpoint-b", profile="default"),
        list_models=MagicMock(return_value=[served]),
        list_endpoints=MagicMock(return_value={"endpoints": endpoints}),
    )
    return app


def test_models_lists_only_provisioned_endpoint_models() -> None:
    app = _app_with_endpoint_models(_provisioned_endpoints_fixture(), "served-model")

    _cmd_models(app, [])

    lines = _info_lines(app)
    assert _has_line(lines, "endpoint-a")
    assert _has_line(lines, "(ready)")
    assert not _has_line(lines, "failed-model")
    assert not _has_line(lines, "(endpoint:ready, ready)")


def test_models_number_selects_first_ready_model() -> None:
    app = _app_with_endpoint_models(_provisioned_endpoints_fixture(), "served-model")

    _cmd_models(app, ["1"])

    assert app.session.model == "endpoint-a"


def test_models_lists_degraded_endpoint_models() -> None:
    app = _make_app()
    app.session = SimpleNamespace(model=None)
    app.client = SimpleNamespace(
        config=SimpleNamespace(model="", profile="default"),
        list_models=MagicMock(return_value=[]),
        list_endpoints=MagicMock(
            return_value={
                "endpoints": [
                    {"instance_id": "ep-1", "status": "degraded", "model": "degraded-model"},
                ]
            }
        ),
    )

    _cmd_models(app, [])

    info_lines = [call.args[0] for call in app.renderer.info.call_args_list]
    assert any("degraded-model" in line for line in info_lines)
    assert any("(degraded)" in line for line in info_lines)


def test_model_default_persists_profile_model(tmp_path, monkeypatch) -> None:
    from r4.config import ProfileConfigStore

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    app = _make_app()
    app.session = SimpleNamespace(model="endpoint-a")
    app.client = SimpleNamespace(
        config=SimpleNamespace(model="old-default", profile="default"),
        list_models=MagicMock(return_value=[]),
        list_endpoints=MagicMock(
            return_value={
                "endpoints": [
                    {"instance_id": "ep-1", "status": "ready", "model": "endpoint-a"},
                ]
            }
        ),
    )

    _cmd_models(app, ["default"])

    assert app.client.config.model == "endpoint-a"
    stored = ProfileConfigStore().load("default")
    assert stored["model"] == "endpoint-a"


def test_up_uses_current_session_model_without_cli_prompt() -> None:
    """When a model name is given as argument, provision directly."""
    app = _make_app()
    app.session = SimpleNamespace(model="qwen-test")
    app.client = SimpleNamespace(
        provision_endpoint=MagicMock(
            return_value={
                "instance_id": "ep-1",
                "status": "pending",
            }
        )
    )

    _cmd_up(app, ["qwen-test"])

    app.client.provision_endpoint.assert_called_once()
    call_kwargs = app.client.provision_endpoint.call_args
    assert call_kwargs.kwargs.get("model") == "qwen-test"
    assert call_kwargs.kwargs.get("wait") is False
    system_lines = [call.args[0] for call in app.renderer.system.call_args_list]
    info_lines = [call.args[0] for call in app.renderer.info.call_args_list]
    assert any("endpoint provision requested: ep-1" in line for line in system_lines)
    assert any("model=qwen-test status=pending" in line for line in info_lines)


def test_up_lists_registry_models_when_no_arg() -> None:
    from unittest.mock import patch

    app = _make_app()
    app.session = SimpleNamespace(model=None)
    app.client = SimpleNamespace(
        list_endpoints=MagicMock(return_value={"endpoints": []}),
        provision_endpoint=MagicMock(),
    )

    with patch(
        "r4.tui.commands._registry_model_options",
        return_value=[{"name": "qwen-72b", "source": "registry", "status": "available"}],
    ):
        _cmd_up(app, [])

    app.client.provision_endpoint.assert_not_called()
    info_lines = [call.args[0] for call in app.renderer.info.call_args_list]
    assert any("registry models" in line for line in info_lines)
    assert any("[1] qwen-72b" in line for line in info_lines)


def test_up_reports_usage_without_selected_model() -> None:
    from unittest.mock import patch

    app = _make_app()
    app.session = SimpleNamespace(model=None)
    app.client = SimpleNamespace(
        provision_endpoint=MagicMock(),
        list_models=MagicMock(return_value=[]),
        list_endpoints=MagicMock(return_value={"endpoints": []}),
    )

    with patch("r4.tui.commands._registry_model_options", return_value=[]):
        _cmd_up(app, [])

    app.client.provision_endpoint.assert_not_called()
    info_lines = [call.args[0] for call in app.renderer.info.call_args_list]
    assert any("usage: /endpoint up <model>" in line for line in info_lines)
