"""ADR-105 D-3: the stdlib client pre-check of workflow parameters."""

from __future__ import annotations

from r4_contracts.workflow_params import MAX_ERRORS, check_workflow_params

SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "integer"},
        "mode": {"type": "string", "enum": ["fast", "accurate"]},
        "ratio": {"type": ["number", "null"]},
    },
    "required": ["steps"],
    "additionalProperties": False,
}


def _paths(errors: list[dict[str, str]]) -> list[str]:
    return [e["path"].lstrip("/") for e in errors]


def test_valid_params_pass():
    assert check_workflow_params({"steps": 3, "mode": "fast", "ratio": None}, SCHEMA) == []


def test_missing_required_is_named():
    assert _paths(check_workflow_params({"mode": "fast"}, SCHEMA)) == ["steps"]


def test_type_enum_and_extra_keys_are_all_reported():
    errors = check_workflow_params({"steps": "three", "mode": "slow", "colour": 1}, SCHEMA)
    assert _paths(errors) == ["steps", "mode", "colour"]


def test_bool_is_not_an_integer():
    assert _paths(check_workflow_params({"steps": True}, SCHEMA)) == ["steps"]


def test_injected_keys_are_not_extra_properties():
    assert (
        check_workflow_params({"steps": 1, "input_files": ["a"], "project_id": "p"}, SCHEMA) == []
    )


def test_declared_injected_key_is_type_checked():
    schema = {**SCHEMA, "properties": {**SCHEMA["properties"], "input_files": {"type": "array"}}}
    assert _paths(check_workflow_params({"steps": 1, "input_files": "a"}, schema)) == [
        "input_files"
    ]


def test_schemas_it_cannot_judge_pass():
    assert check_workflow_params({"x": 1}, None) == []
    assert check_workflow_params({"x": 1}, {"type": "array"}) == []
    assert (
        check_workflow_params({"x": 1}, {"type": "object", "properties": {"x": {"type": "exotic"}}})
        == []
    )


def test_errors_are_bounded():
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    params = {f"k{i}": i for i in range(MAX_ERRORS + 5)}
    assert len(check_workflow_params(params, schema)) == MAX_ERRORS


def test_paths_are_json_pointers_like_umrs():
    assert check_workflow_params({"a/b": 1}, {**SCHEMA, "required": []})[0]["path"] == "/a~1b"


def test_what_the_server_accepts_passes():
    """UMRS validates text as the declared scalar and 3.0 as an integer."""
    assert check_workflow_params({"steps": "3"}, SCHEMA) == []
    assert check_workflow_params({"steps": 3.0}, SCHEMA) == []
    flags = {"type": "object", "properties": {"dry": {"type": "boolean"}}}
    assert check_workflow_params({"dry": "false"}, flags) == []


def test_late_supplied_project_id_is_never_missing():
    schema = {**SCHEMA, "required": ["steps", "project_id"]}
    assert check_workflow_params({"steps": 1}, schema) == []


def test_pattern_properties_disable_the_extra_key_check():
    schema = {"type": "object", "patternProperties": {"^x_": {}}, "additionalProperties": False}
    assert check_workflow_params({"x_dim": 1}, schema) == []


def test_mcp_tools_is_injected():
    assert check_workflow_params({"steps": 1, "mcp_tools": ["m.t"]}, SCHEMA) == []
