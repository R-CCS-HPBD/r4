"""Platform tools generated from the UMRS agent manifest (ADR-105 D-2, S5).

``tools/umrs_agent_manifest.py --write`` mirrors the committed contract
(``contracts/umrs-http/v1/agent-tools.json`` + ``agent-openapi.json``) into
this package (``r4_mcp/contracts/``; the image is built without
``contracts/``). This module turns every ``mcp-eligible`` row into one MCP
tool:

- name = the row's ``operation_id`` (today's tool names, D-2: permission
  rules, trust rows and skills key on names);
- description = the agent-facing text (``WORKFLOW_TOOL_HELP`` for the
  workflow contract's own operations, else the OpenAPI summary) plus the
  first paragraph of the OpenAPI description and the row's risk / taint;
- ``inputSchema`` = path parameters + query parameters + the JSON body's
  properties, ``$ref`` resolved inline (self-contained, no lossy
  flattening). The manifest already drops the identity inputs (gateway
  headers, MVP ``caller_*`` / ``is_admin`` query, the client's session
  header); body fields UMRS honours only in MVP mode (``"MVP mode only"``)
  and a legacy alias whose canonical field is present (``params`` beside
  ``parameters``) are not offered either.

``companion-only`` rows (ADR-030; ``workflow_commit``: MCP has no ADR-104
ladder to vouch for a human approval) never become tools. A row whose body
is not JSON is skipped with a logged reason (``rag_upload``: multipart; a
local-file upload is a new file-to-knowledge-store channel and needs its own
decision, it never existed on this server).

Calls go to the gateway (``/gw`` + the UMRS path minus ``/umrs``: the
gateway rewrites ``/gw/<family>/…`` to ``/umrs/<family>/…`` one-to-one for
jobs, workflows and rag) through the server's ``_request`` (Bearer token).
Workflow tools keep the local ``R4_WORKFLOW_POLICY_FILE`` veto
(``r4_mcp.workflow_policy``); a ``workflow_*`` tool with no contract
operation fails closed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote

from r4_contracts.workflows import WORKFLOW_TOOL_HELP

from ..base import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

MANIFEST_NAME = "agent-tools.json"
OPENAPI_NAME = "agent-openapi.json"
MCP_SCOPE = "mcp-eligible"
UMRS_PREFIX = "/umrs"
GATEWAY_PREFIX = "/gw"
_REF_PREFIX = "#/components/schemas/"
_JSON_MEDIA = "application/json"
_HIDDEN_BODY_MARKER = "MVP mode only"
_BODY_ALIASES = {"params": "parameters"}

# Workflow tool → ``r4_contracts.workflows`` operation for the local policy
# guard. ``workflow_plan`` writes nothing (ADR-105 D-4): a describe-level read.
WORKFLOW_POLICY_OPERATIONS = {
    "workflow_list": "list",
    "workflow_describe": "describe",
    "workflow_run": "run",
    "workflow_status": "status",
    "workflow_results": "results",
    "workflow_cancel": "cancel",
    "workflow_plan": "describe",
}


def _path_segment(name: str, value: Any) -> str:
    """One encoded path segment; ``.``/``..`` would re-route the request."""
    text = str(value)
    if text in ("", ".", ".."):
        raise ValueError(f"{name}: {text!r} is not a valid path value")
    return quote(text, safe="")


@dataclass(frozen=True)
class GeneratedOperation:
    """One manifest row, ready to serve as an MCP tool."""

    name: str
    method: str
    path: str  # gateway path template, ``/gw/...``
    description: str
    input_schema: dict[str, Any]
    locations: dict[str, str] = field(default_factory=dict)  # arg → path|query|body
    has_body: bool = False
    # None: not a workflow tool. An unknown operation name fails closed.
    policy_operation: Optional[str] = None

    @property
    def required(self) -> list[str]:
        return list(self.input_schema.get("required", []))

    def split(self, arguments: dict[str, Any]) -> tuple[str, dict[str, Any], Any]:
        """(gateway path, query, JSON body or None) for ``arguments``."""
        path, query, body = self.path, {}, {}
        for name, value in arguments.items():
            where = self.locations[name]
            if where == "path":
                path = path.replace("{" + name + "}", _path_segment(name, value))
            elif value is not None:
                (query if where == "query" else body)[name] = value
        return path, query, body if self.has_body else None


def load_contract() -> tuple[dict[str, Any], dict[str, Any]]:
    """(manifest, OpenAPI subset) from the package copy."""
    root = resources.files("r4_mcp").joinpath("contracts")
    manifest = json.loads(root.joinpath(MANIFEST_NAME).read_text(encoding="utf-8"))
    openapi = json.loads(root.joinpath(OPENAPI_NAME).read_text(encoding="utf-8"))
    return manifest, openapi


# ── schema helpers ────────────────────────────────────────────────────────


def _inline(node: Any, schemas: dict[str, Any], seen: frozenset[str] = frozenset()) -> Any:
    """``node`` with every local ``$ref`` replaced by its component."""
    if isinstance(node, list):
        return [_inline(item, schemas, seen) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
        name = ref[len(_REF_PREFIX) :]
        if name in seen or name not in schemas:
            return {"type": "object"}  # a cycle or a dangling ref: stay valid
        return _inline(schemas[name], schemas, seen | {name})
    return {key: _inline(value, schemas, seen) for key, value in node.items()}


def _clean(schema: dict[str, Any]) -> dict[str, Any]:
    """Collapse ``anyOf: [X, null]`` (null means omit) and drop ``title``."""
    branches = schema.get("anyOf")
    out = {k: v for k, v in schema.items() if k not in ("title", "anyOf")}
    if isinstance(branches, list):
        real = [b for b in branches if b != {"type": "null"}]
        if len(real) == 1:
            return {**real[0], **out}
        out["anyOf"] = real
    return out


def _param_schema(param: dict[str, Any], schemas: dict[str, Any]) -> dict[str, Any]:
    raw = param.get("schema", {})
    schema = _clean(_inline(raw, schemas))
    description = param.get("description") or raw.get("title")
    if description and "description" not in schema:
        schema["description"] = description
    return schema


def _json_body(op: dict[str, Any]) -> tuple[bool, Optional[dict[str, Any]]]:
    """(has a body, its JSON schema or None when the body is not JSON)."""
    content = (op.get("requestBody") or {}).get("content")
    if not content:
        return False, None
    media = content.get(_JSON_MEDIA)
    return True, (media.get("schema", {}) if isinstance(media, dict) else None)


def _hidden_body_field(name: str, schema: dict[str, Any], siblings: dict[str, Any]) -> bool:
    if _HIDDEN_BODY_MARKER in str(schema.get("description", "")):
        return True
    return _BODY_ALIASES.get(name) in siblings


def _body_fields(body: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """(visible properties, their required names) of a resolved JSON body."""
    props = body.get("properties", {})
    visible = {
        name: _clean(schema)
        for name, schema in props.items()
        if not _hidden_body_field(name, schema, props)
    }
    return visible, [n for n in body.get("required", []) if n in visible]


# ── operation building ───────────────────────────────────────────────────


class _SchemaBuilder:
    def __init__(self) -> None:
        self.properties: dict[str, Any] = {}
        self.required: list[str] = []
        self.locations: dict[str, str] = {}

    def add(self, name: str, schema: dict[str, Any], where: str, required: bool) -> None:
        if name in self.locations:
            raise ValueError(f"argument {name!r} is both {self.locations[name]} and {where}")
        self.properties[name] = schema
        self.locations[name] = where
        if required:
            self.required.append(name)

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": self.properties,
            "required": self.required,
            "additionalProperties": False,
        }


def _first_paragraph(text: str) -> str:
    lines = (text or "").strip().split("\n\n")[0].splitlines()
    return " ".join(line.strip() for line in lines if not line.strip().startswith("# auth"))


def _description(row: dict[str, Any], op: dict[str, Any]) -> str:
    contract_op = row["operation_id"].removeprefix("workflow_")
    is_contract = row["operation_id"].startswith("workflow_") and contract_op in WORKFLOW_TOOL_HELP
    lead = WORKFLOW_TOOL_HELP[contract_op] if is_contract else (op.get("summary") or row["tool"])
    parts = [lead]
    detail = _first_paragraph(op.get("description", ""))
    if detail and detail != lead:
        parts.append(detail)
    parts.append(
        f"[UMRS {row['method']} {row['path']} ({row['operation_id']}); "
        f"risk: {row['risk']}; taint: {row['taint']}]"
    )
    return "\n\n".join(parts)


def _policy_operation(row: dict[str, Any]) -> Optional[str]:
    name = row["operation_id"]
    if not (name.startswith("workflow_") or row["tool"].startswith("workflow_")):
        return None
    return WORKFLOW_POLICY_OPERATIONS.get(name, name)  # unmapped: not an operation → denied


def _unsupported(row: dict[str, Any], op: dict[str, Any]) -> Optional[str]:
    if not row["path"].startswith(UMRS_PREFIX + "/"):
        return f"path {row['path']} is not under {UMRS_PREFIX}/"
    has_body, body = _json_body(op)
    if has_body and body is None:
        media = ", ".join(op["requestBody"]["content"])
        return f"request body is {media}, not JSON (no file-path upload adapter)"
    return None


def build_operation(
    row: dict[str, Any], op: dict[str, Any], schemas: dict[str, Any]
) -> GeneratedOperation:
    """The MCP operation for one supported, ``mcp-eligible`` row."""
    builder = _SchemaBuilder()
    for param in op.get("parameters", []):
        if param.get("in") in ("path", "query"):
            schema = _param_schema(param, schemas)
            builder.add(param["name"], schema, param["in"], bool(param.get("required")))
    has_body, body = _json_body(op)
    fields, required = _body_fields(_inline(body or {}, schemas))
    for name, schema in fields.items():
        builder.add(name, schema, "body", name in required)
    return GeneratedOperation(
        name=row["operation_id"],
        method=row["method"],
        path=GATEWAY_PREFIX + row["path"][len(UMRS_PREFIX) :],
        description=_description(row, op),
        input_schema=builder.schema(),
        locations=builder.locations,
        has_body=has_body,
        policy_operation=_policy_operation(row),
    )


def build_operations(
    manifest: dict[str, Any], openapi: dict[str, Any]
) -> tuple[list[GeneratedOperation], dict[str, str]]:
    """(operations for every supported ``mcp-eligible`` row, skipped {id: reason})."""
    schemas = openapi.get("components", {}).get("schemas", {})
    operations: list[GeneratedOperation] = []
    skipped: dict[str, str] = {}
    for row in manifest.get("operations", []):
        if row.get("scope") != MCP_SCOPE:
            continue  # ADR-030: companion-only rows never reach MCP
        op = openapi["paths"][row["path"]][row["method"].lower()]
        reason = _unsupported(row, op)
        if reason:
            skipped[row["operation_id"]] = reason
            logger.info("MCP platform: %s not generated: %s", row["operation_id"], reason)
            continue
        operations.append(build_operation(row, op, schemas))
    return operations, skipped


# ── handlers ─────────────────────────────────────────────────────────────


def argument_problem(op: GeneratedOperation, arguments: dict[str, Any]) -> Optional[str]:
    """Why ``arguments`` cannot be sent, or None."""
    unexpected = sorted(set(arguments) - set(op.locations))
    missing = [name for name in op.required if arguments.get(name) is None]
    problems = []
    if unexpected:
        problems.append(f"unexpected argument(s) {unexpected}")
    if missing:
        problems.append(f"missing required argument(s) {missing}")
    if not problems:
        return None
    return f"Invalid arguments for {op.name}: {'; '.join(problems)}"


def make_handler(op: GeneratedOperation, server: Any) -> Callable[..., Awaitable[ToolResult]]:
    """An async ``handler(**arguments)`` calling the gateway through ``server``.

    ``server`` provides ``_request(method, path, *, params, json_body)``,
    ``_format(result)`` and ``_workflow_policy_guard(operation)`` (the
    ``PlatformServer`` contract); they are looked up per call, so tests may
    patch them on the instance.
    """

    async def handler(**arguments: Any) -> ToolResult:
        if op.policy_operation is not None:
            denied = server._workflow_policy_guard(op.policy_operation)
            if denied is not None:
                return denied
        problem = argument_problem(op, arguments)
        if problem:
            return ToolResult.error(problem)
        try:
            path, query, body = op.split(arguments)
        except ValueError as exc:  # a path value that would re-route
            return ToolResult.error(str(exc))
        result = await server._request(op.method, path, params=query or None, json_body=body)
        return server._format(result)

    handler.__name__ = op.name
    return handler


def generated_tools(
    server: Any, contract: Optional[tuple[dict, dict]] = None
) -> tuple[list[ToolDefinition], dict[str, str]]:
    """(tool definitions bound to ``server``, skipped {operation_id: reason})."""
    operations, skipped = build_operations(*(contract or load_contract()))
    tools = [
        ToolDefinition(
            name=op.name,
            description=op.description,
            handler=make_handler(op, server),
            input_schema=op.input_schema,
        )
        for op in operations
    ]
    return tools, skipped
