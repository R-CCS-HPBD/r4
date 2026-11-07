"""Model types reranker/ocr/vlm in the CLI, and name lookup past the first page (2026-11-02)."""

from __future__ import annotations

import pytest

from r4.models import MODEL_TYPE_CHOICES, ModelClient, ModelType


def test_cli_type_choices_include_the_service_tier_types():
    assert set(MODEL_TYPE_CHOICES) == {"llm", "embedding", "vision", "reranker", "ocr", "vlm"}
    assert ModelType("reranker") is ModelType.RERANKER


@pytest.mark.parametrize("model_type", ["reranker", "ocr", "vlm"])
def test_register_accepts_the_new_types(model_type):
    from r4.cli import _build_parser

    args = _build_parser().parse_args(
        ["model", "register", "M", "--path", "/models/M", "--type", model_type]
    )
    assert args.model_type == model_type


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name


def _client_with(names: list[str], calls: list[tuple[int, int]]) -> ModelClient:
    client = object.__new__(ModelClient)

    def list_models(scope=None, project_id=None, limit=50, offset=0, **_kw):
        calls.append((limit, offset))
        return [_Named(n) for n in names[offset : offset + limit]]

    client.list_models = list_models  # type: ignore[method-assign]
    return client


def test_name_lookup_finds_a_model_past_the_first_page():
    names = [f"m{i}" for i in range(450)] + ["Qwen3-Reranker-0.6B"]
    calls: list[tuple[int, int]] = []
    found = _client_with(names, calls).get_model_by_name("Qwen3-Reranker-0.6B")
    assert found is not None and found.name == "Qwen3-Reranker-0.6B"
    assert [offset for _, offset in calls] == [0, 200, 400]


def test_name_lookup_stops_on_a_short_page():
    calls: list[tuple[int, int]] = []
    assert _client_with(["a", "b"], calls).get_model_by_name("missing") is None
    assert len(calls) == 1
