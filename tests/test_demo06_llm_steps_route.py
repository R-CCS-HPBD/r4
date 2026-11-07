"""Demo 06's LLM steps route through the model/endpoint registry (cycle-3 mem
lane, 2026-10-22; ops-watch #520).

Its five LLMStep steps set only ``config.model``. A step that declares
neither ``requires`` nor ``model_type`` falls through the step router to the
env fallback (``OPENAI_BASE_URL`` / ``R4_GATEWAY_URL``), which a server does
not set, so since cycle 3 every step failed honestly with "LLMStep requires
api_base". With ``model_type: "llm"`` (and the model as the step's symbolic
``model``, which the router prefers among the registry's candidates) each step
is resolved from the registry, and the endpoint's served model name fills
``config.model``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / "demos" / "06_complex_workflow" / "workflow.json"
MODEL = "rivault-gpt-oss-static"


def _steps() -> list[Any]:
    from umrs.pipeline_sdk.workflow import WorkflowDefinition

    # run_demo.sh substitutes the placeholder exactly this way (sed).
    text = WORKFLOW.read_text(encoding="utf-8").replace("{{model}}", MODEL)
    steps = WorkflowDefinition.from_dict(json.loads(text)).steps
    for step in steps:
        step.validate()
    return steps


def _router(models: list[dict[str, Any]]) -> Any:
    from umrs.server.step_router import StepRouter

    def find_models(_requires: list[str], model_type: str | None) -> list[dict[str, Any]]:
        return [m for m in models if m["model_type"] == model_type]

    def find_endpoint(name: str, _project: str | None, _owner: str | None) -> dict[str, Any] | None:
        return {
            "endpoint_id": f"ep-{name}",
            "api_base": f"http://{name}.internal:8000/v1",
            "served_model_name": f"served/{name}",
        }

    return StepRouter(find_models=find_models, find_endpoint=find_endpoint)


def test_every_llm_step_declares_the_llm_model_type() -> None:
    steps = _steps()
    assert len(steps) == 5
    assert {s.model_type for s in steps} == {"llm"}
    assert {s.model for s in steps} == {MODEL}


def test_every_llm_step_resolves_from_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from umrs.server.workflow_runner import _apply_routing_decision_to_config

    monkeypatch.delenv("UMRS_STEP_ROUTER_MOCK", raising=False)
    router = _router(
        [
            {"name": "other-llm", "model_type": "llm", "scope": "system"},
            {"name": MODEL, "model_type": "llm", "scope": "system"},
            {"name": "e5", "model_type": "embedding", "scope": "system"},
        ]
    )
    for step in _steps():
        decision = router.resolve(step, project_id="p")
        assert decision.resolved_via == "registry_capability", step.name
        config = dict(step.config)
        _apply_routing_decision_to_config(config, decision)
        # The pinned model is preferred, and its endpoint's served name is sent.
        assert config["api_base"] == f"http://{MODEL}.internal:8000/v1"
        assert config["model"] == f"served/{MODEL}"


def test_no_llm_in_the_registry_is_a_named_routing_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from umrs.server.step_router import StepRoutingError

    monkeypatch.delenv("UMRS_STEP_ROUTER_MOCK", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://env-fallback.invalid/v1")
    router = _router([{"name": "e5", "model_type": "embedding", "scope": "system"}])
    with pytest.raises(StepRoutingError):
        router.resolve(_steps()[0], project_id="p")
