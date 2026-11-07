"""Client pre-check of workflow parameters against a ``parameter_schema``.

ADR-105 D-3 (2026-11-02): ``workflow_run`` takes its parameters as a JSON
object, and the client checks them against the workflow's
``parameter_schema`` (returned by ``workflow_describe``) before it submits,
so a model sees the schema and every mismatch in one answer instead of a
server round trip per mistake.

UMRS stays authoritative: it validates the full JSON Schema on trigger. This
check covers the top level only (``required``, ``type``, ``enum``,
``additionalProperties: false``) and never rejects what it cannot judge.

Keys UMRS injects into the parameters itself (``input_files``,
``project_id``) are not the caller's extra properties: they are checked only
when the schema declares them, the same rule the server applies.

Stdlib only (INV-10): r4, the MCP server and UMRS may all import it.
"""

from __future__ import annotations

from typing import Any

# Keys the platform merges into the parameters (WorkflowTriggerRequest
# .resolved_params; ``mcp_tools`` from ``r4 workflow run --mcp-tool``).
INJECTED_PARAMETER_KEYS = frozenset({"input_files", "project_id", "mcp_tools"})
# Supplied after this check (the client library adds the active project), so
# never reported missing here.
LATE_SUPPLIED_KEYS = frozenset({"project_id"})

_TYPE_CHECKS: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: (
        (isinstance(v, int) and not isinstance(v, bool))
        or (isinstance(v, float) and v.is_integer())
    ),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}

MAX_ERRORS = 20

# UMRS validates text sent for a declared scalar as that scalar (the CLI's
# ``--param k=v`` sends text); the pre-check accepts what the server accepts.
_TEXT_PARSERS: dict[str, Any] = {
    "integer": int,
    "number": float,
    "boolean": lambda text: {"true": True, "false": False}[text.strip().lower()],
}


def _text_parses_as(value: Any, type_name: str) -> bool:
    parser = _TEXT_PARSERS.get(type_name)
    if parser is None or not isinstance(value, str):
        return False
    try:
        parser(value)
    except (ValueError, KeyError):
        return False
    return True


def _error(name: str, message: str) -> dict[str, str]:
    """One error row; ``path`` is an RFC 6901 JSON Pointer, as UMRS reports."""
    escaped = name.replace("~", "~0").replace("/", "~1")
    return {"path": f"/{escaped}", "message": message}


def _type_error(name: str, value: Any, spec: dict[str, Any]) -> dict[str, str] | None:
    expected = spec.get("type")
    names = expected if isinstance(expected, list) else [expected]
    known = [n for n in names if isinstance(n, str) and n in _TYPE_CHECKS]
    if not known or any(_TYPE_CHECKS[n](value) or _text_parses_as(value, n) for n in known):
        return None
    return _error(name, f"expected {' or '.join(known)}, got {type(value).__name__}")


def _enum_error(name: str, value: Any, spec: dict[str, Any]) -> dict[str, str] | None:
    allowed = spec.get("enum")
    if not isinstance(allowed, list) or value in allowed:
        return None
    return _error(name, f"must be one of {allowed!r}")


def _property_errors(params: dict[str, Any], properties: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for name, value in params.items():
        spec = properties.get(name)
        if not isinstance(spec, dict):
            continue
        for found in (_type_error(name, value, spec), _enum_error(name, value, spec)):
            if found is not None:
                errors.append(found)
    return errors


def _required_errors(params: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, str]]:
    required = schema.get("required")
    if not isinstance(required, list):
        return []
    return [
        _error(name, "required parameter is missing")
        for name in required
        if isinstance(name, str) and name not in params and name not in LATE_SUPPLIED_KEYS
    ]


def _extra_errors(
    params: dict[str, Any], properties: dict[str, Any], schema: dict[str, Any]
) -> list[dict[str, str]]:
    # Like UMRS: no regex is evaluated, so patternProperties disables this check.
    if schema.get("additionalProperties") is not False or "patternProperties" in schema:
        return []
    return [
        _error(name, "unknown parameter (the schema allows no additional properties)")
        for name in params
        if name not in properties and name not in INJECTED_PARAMETER_KEYS
    ]


def check_workflow_params(params: dict[str, Any], schema: Any) -> list[dict[str, str]]:
    """Top-level mismatches of ``params`` against ``schema`` ([] = none found).

    Each error is ``{"path": "/<parameter>", "message": <why>}`` (a JSON
    Pointer, the format UMRS uses for its own errors); at most
    :data:`MAX_ERRORS`. A schema that is not an object schema is not judged.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return []
    raw_properties = schema.get("properties")
    properties = raw_properties if isinstance(raw_properties, dict) else {}
    errors = (
        _required_errors(params, schema)
        + _property_errors(params, properties)
        + _extra_errors(params, properties, schema)
    )
    return errors[:MAX_ERRORS]
