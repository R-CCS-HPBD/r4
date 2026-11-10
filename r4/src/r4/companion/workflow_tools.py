"""Companion workflow tool adapters over the shared WorkflowClient contract."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from r4_contracts.workflow_params import check_workflow_params
from r4_contracts.workflows import WORKFLOW_TOOL_HELP, WorkflowStructuredError

from .workflow_policy import load_workflow_policy, policy_denied_error

if TYPE_CHECKING:
    from r4.client import Client

# Bound the polling loop so an agent turn can never block indefinitely.
DEFAULT_WAIT_TIMEOUT_SEC = 300

# ADR-105 D-3: typed by the workflow's parameter_schema (one tool, no
# per-workflow names: the wave-80 tool array stays byte-stable). Legacy JSON
# strings are still accepted by the executor.
_RUN_PARAMETERS_PARAM: dict[str, Any] = {
    "type": "object",
    "description": (
        "Workflow parameters as a JSON object shaped by the parameter_schema "
        "that workflow_describe returns; checked before submit"
    ),
    "additionalProperties": True,
}
_RUN_INPUT_FILES_PARAM: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Input file keys",
}
# ADR-113 D-3 (2026-11-08): one optional version on every tool that names a
# workflow; workflow_name may also carry it as NAME@VERSION.
VERSION_PARAM: dict[str, Any] = {
    "type": "string",
    "description": (
        "Workflow version (workflow_list `versions`); default: the highest "
        "registered. workflow_name may also be NAME@VERSION"
    ),
}


def _tool(name: str, description: str, parameters: dict[str, Any], execute) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
        "execute": execute,
    }


def _structured_error(code: str, message: str, **details: Any) -> str:
    return json.dumps(
        WorkflowStructuredError(code=code, message=message, details=details).to_dict(),
        indent=2,
    )


def _policy_guard(client: "Client", operation: str) -> str | None:
    project_id = getattr(getattr(client, "config", None), "project_id", None)
    policy = load_workflow_policy(project_id=project_id)
    if policy.allows(operation):
        return None
    return json.dumps(policy_denied_error(operation, policy), indent=2)


def _decode_json_input(value: Any, empty: Any) -> Any:
    """A JSON string is decoded; an object/array (ADR-105 D-3) passes as is."""
    if value is None or value == "":
        return empty
    if isinstance(value, str):
        return json.loads(value)
    return value


def _parse_workflow_run_inputs(
    parameters: Any, input_files: Any
) -> tuple[dict[str, Any], list[Any] | None, str | None]:
    """Parse workflow_run inputs (objects, or legacy JSON strings).

    Returns (params, files, error_str); params is ``{}`` when error_str is set.
    """
    try:
        params = _decode_json_input(parameters, {})
        files = _decode_json_input(input_files, None)
    except json.JSONDecodeError:
        return (
            {},
            None,
            _structured_error("invalid_json", "parameters/input_files must be valid JSON"),
        )
    if not isinstance(params, dict):
        return (
            {},
            None,
            _structured_error("invalid_parameters", "parameters must be a JSON object"),
        )
    if files is not None and not isinstance(files, list):
        return (
            {},
            None,
            _structured_error("invalid_input_files", "input_files must be a JSON array"),
        )
    return params, files, None


def _precheck_params(
    client: "Client",
    workflow_name: str,
    params: dict[str, Any],
    files: list[Any] | None,
    version: str | None = None,
) -> str | None:
    """ADR-105 D-3: check params against the workflow's parameter_schema.

    Returns a structured error naming every mismatch plus the schema, or
    None. A describe failure, or a policy that denies describe, skips the
    check: UMRS validates on trigger and stays authoritative.
    """
    if _policy_guard(client, "describe") is not None:
        return None
    try:
        schema = client.workflows.describe(workflow_name, version=version).get("parameter_schema")
    except Exception:
        return None
    merged = dict(params)
    if files:
        merged["input_files"] = list(files)
    errors = check_workflow_params(merged, schema)
    if not errors:
        return None
    return _structured_error(
        "workflow_parameters_invalid",
        "parameters do not match the workflow's parameter_schema; fix them and retry",
        errors=errors,
        parameter_schema=schema,
    )


def _workflow_status_extras(client: "Client", execution_id: str) -> dict[str, object]:
    """Live step rows / artifacts for the TUI status card. Additive only —
    any detail failure degrades to an empty dict instead of hiding status."""
    try:
        detail = client.workflows.get_execution_detail(execution_id)
    except Exception:
        return {}
    if not isinstance(detail, dict):
        return {}
    extras: dict[str, object] = {}
    steps = detail.get("steps") or (detail.get("results") or {}).get("steps")
    if steps:
        extras["steps"] = steps
    if detail.get("artifacts"):
        extras["artifacts"] = detail["artifacts"]
    return extras


_SITE_PARAM: dict[str, str] = {
    "type": "string",
    "description": "HPC site (see hpc_sites); default: this session's",
}


def _site_target(
    client: "Client", site: str | None, handle: Any = "", *, act: bool = False
) -> tuple[Any, str]:
    """``(SiteTarget, "")``, or ``(None, structured refusal)`` (multi-HPC S3).

    Fails closed: an unknown site is refused by name, never run at home.
    ``act`` (run, replay, cancel) also refuses a non-home site that is not
    eligible (undeclared access mode, no active account); reads do not.
    """
    from .site_routing import SiteRefused, route

    try:
        return route(client, site, handle, act=act), ""
    except SiteRefused as exc:
        return None, _structured_error(exc.code, str(exc))
    except Exception as exc:  # noqa: BLE001 — a routing failure is a refusal
        return None, _structured_error("site_unavailable", str(exc))


def _tagged_json(payload: dict[str, Any], target: Any, ident: Any) -> str:
    """The result JSON with ``site`` and the ``execution_id@site`` handle."""
    from dataclasses import replace

    from .site_routing import tag

    tagged = tag(payload, replace(target, ident=str(ident or "")))
    return json.dumps(tagged, indent=2, default=str)


def _result_json(result: Any, target: Any, **extra: Any) -> str:
    """One run/replay/wait/results payload, tagged with its site."""
    payload = {
        **extra,
        "execution_id": result.execution_id,
        "workflow_name": result.workflow_name or result.workflow_id,
        "status": result.status,
        "artifacts": result.artifacts,
        "outputs": result.outputs,
        "error": result.error,
        **_run_pin(result),
    }
    return _tagged_json(payload, target, result.execution_id)


def _run_pin(run: Any) -> dict[str, str]:
    """The version and code digest a run pinned (ADR-113 D-4); empty
    strings from servers before versions."""
    return {
        "workflow_version": str(getattr(run, "workflow_version", "") or ""),
        "source_sha256": str(getattr(run, "source_sha256", "") or ""),
    }


def _catalog_tool_row(wf: Any) -> dict[str, Any]:
    """One ``workflow_list`` row; ``shared``/``owner`` tell a workflow another
    user shared with the caller from the caller's own (2026-10-23)."""
    return {
        "workflow_name": wf.workflow_id or wf.name,
        # ADR-113 D-3: the default version and every registered one.
        "version": str(getattr(wf, "version", "") or ""),
        "versions": list(getattr(wf, "versions", None) or []),
        "description": wf.description,
        "tags": wf.tags,
        "owner": str(getattr(wf, "owner", "") or ""),
        "shared": getattr(wf, "shared", False) is True,
    }


def build_workflow_tools(client: "Client") -> list[dict[str, Any]]:  # noqa: C901
    """Return workflow lifecycle tools for Companion."""
    # Pre-existing factory debt (ruff walks nested closure branches into the
    # outer count; same suppression convention as companion/tools.py factories).
    # Decomposition is tracked separately — do not extend with new closures.
    tools: list[dict[str, Any]] = []

    def _workflow_list() -> str:
        denied = _policy_guard(client, "list")
        if denied:
            return denied
        try:
            workflows = client.workflows.list_workflows()
            if not workflows:
                return json.dumps({"workflows": [], "count": 0})
            payload = [_catalog_tool_row(wf) for wf in workflows]
            return json.dumps({"workflows": payload, "count": len(payload)}, indent=2)
        except Exception as exc:
            return _structured_error("workflow_list_failed", str(exc))

    tools.append(
        _tool(
            "workflow_list",
            WORKFLOW_TOOL_HELP["list"],
            {"type": "object", "properties": {}},
            _workflow_list,
        )
    )

    def _workflow_describe(workflow_name: str, version: str | None = None) -> str:
        denied = _policy_guard(client, "describe")
        if denied:
            return denied
        try:
            data = client.workflows.describe(workflow_name, version=version)
            return json.dumps(data, indent=2, default=str)
        except Exception as exc:
            return _structured_error("workflow_describe_failed", str(exc))

    tools.append(
        _tool(
            "workflow_describe",
            WORKFLOW_TOOL_HELP["describe"],
            {
                "type": "object",
                "properties": {
                    "workflow_name": {"type": "string", "description": "Workflow name"},
                    "version": VERSION_PARAM,
                },
                "required": ["workflow_name"],
            },
            _workflow_describe,
        )
    )

    def _workflow_run(
        workflow_name: str,
        parameters: Any = None,
        input_files: Any = None,
        wait: bool = False,
        site: str | None = None,
        version: str | None = None,
    ) -> str:
        target, refused = _site_target(client, site, act=True)
        if target is None:
            return refused
        denied = _policy_guard(target.client, "run")
        if denied:
            return denied
        params, files, parse_error = _parse_workflow_run_inputs(parameters, input_files)
        if parse_error is not None:
            return parse_error
        invalid = _precheck_params(target.client, workflow_name, params, files, version)
        if invalid is not None:
            return invalid
        try:
            result = target.client.workflows.trigger(
                workflow_name,
                parameters=params,
                input_files=files,
                wait=wait,
                version=version,
            )
            return _result_json(result, target)
        except Exception as exc:
            return _structured_error("workflow_run_failed", str(exc))

    tools.append(
        _tool(
            "workflow_run",
            WORKFLOW_TOOL_HELP["run"],
            {
                "type": "object",
                "properties": {
                    "workflow_name": {"type": "string"},
                    "version": VERSION_PARAM,
                    "parameters": _RUN_PARAMETERS_PARAM,
                    "input_files": _RUN_INPUT_FILES_PARAM,
                    "wait": {"type": "boolean", "default": False},
                    "site": _SITE_PARAM,
                },
                "required": ["workflow_name"],
            },
            _workflow_run,
        )
    )

    # Compatibility alias
    tools.append(
        _tool(
            "workflow_trigger",
            WORKFLOW_TOOL_HELP["run"],
            {
                "type": "object",
                "properties": {
                    "workflow_name": {"type": "string"},
                    "workflow_id": {"type": "string", "description": "Deprecated alias"},
                    "parameters": _RUN_PARAMETERS_PARAM,
                },
                "required": [],
            },
            lambda workflow_name=None, workflow_id=None, parameters=None: _workflow_run(
                workflow_name or workflow_id or "",
                parameters=parameters,
            ),
        )
    )

    def _workflow_save_definition(
        name: str,
        workflow_name: str,
        parameters: str | None = None,
        description: str = "",
        version: str | None = None,
    ) -> str:
        """Persist (workflow, version, params) once for exact-replay later."""
        denied = _policy_guard(client, "run")
        if denied:
            return denied
        try:
            params = json.loads(parameters) if parameters else {}
            if not isinstance(params, dict):
                return _structured_error("invalid_parameters", "parameters must be a JSON object")
            data = client.workflows.save_run_definition(
                name, workflow_name, params=params, description=description, version=version
            )
            return json.dumps(data, indent=2, default=str)
        except json.JSONDecodeError:
            return _structured_error("invalid_json", "parameters must be valid JSON")
        except Exception as exc:
            return _structured_error("workflow_definition_save_failed", str(exc))

    tools.append(
        _tool(
            "workflow_save_definition",
            (
                "Save a named run template (registered workflow + params) so the "
                "exact call can be replayed later WITHOUT an LLM call. Use when "
                "a workflow will recur (nightly runs, weekly analysis, ablation "
                "suites). The definition is stored server-side per user and "
                "pins the workflow version, so a replay runs the same code."
            ),
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Template name"},
                    "workflow_name": {"type": "string"},
                    "version": VERSION_PARAM,
                    "parameters": {
                        "type": "string",
                        "description": "JSON object of parameters",
                    },
                    "description": {"type": "string", "default": ""},
                },
                "required": ["name", "workflow_name"],
            },
            _workflow_save_definition,
        )
    )

    def _workflow_replay(name: str, wait: bool = False, site: str | None = None) -> str:
        target, refused = _site_target(client, site, act=True)
        if target is None:
            return refused
        denied = _policy_guard(target.client, "run")
        if denied:
            return denied
        try:
            result = target.client.workflows.trigger_run_definition(name, wait=wait)
            return _result_json(result, target, definition=name)
        except Exception as exc:
            return _structured_error("workflow_replay_failed", str(exc))

    tools.append(
        _tool(
            "workflow_replay",
            (
                "Replay a saved run definition — exact (workflow, params) with NO "
                "LLM token spent re-deriving parameters. Prefer this whenever the "
                "user repeats a proven invocation. Set wait=true to block for final "
                "status + artifacts."
            ),
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Run definition name"},
                    "wait": {"type": "boolean", "default": False},
                    "site": _SITE_PARAM,
                },
                "required": ["name"],
            },
            _workflow_replay,
        )
    )

    def _workflow_status(execution_id: str, site: str | None = None) -> str:
        target, refused = _site_target(client, site, execution_id)
        if target is None:
            return refused
        denied = _policy_guard(target.client, "status")
        if denied:
            return denied
        try:
            status = target.client.workflows.status(target.ident)
            payload: dict[str, object] = {
                "execution_id": status.execution_id,
                "workflow_name": status.workflow_name or status.workflow_id,
                "status": status.status,
                "progress": status.progress,
                "error": status.error,
                **_run_pin(status),
            }
            # Live step rows for the TUI status card — additive only; failures
            # in the detail fetch never hide the top-level status.
            payload.update(_workflow_status_extras(target.client, target.ident))
            return _tagged_json(payload, target, status.execution_id)
        except Exception as exc:
            return _structured_error("workflow_status_failed", str(exc))

    tools.append(
        _tool(
            "workflow_status",
            WORKFLOW_TOOL_HELP["status"],
            {
                "type": "object",
                "properties": {"execution_id": {"type": "string"}, "site": _SITE_PARAM},
                "required": ["execution_id"],
            },
            _workflow_status,
        )
    )

    def _workflow_wait(
        execution_id: str, timeout: int | None = None, site: str | None = None
    ) -> str:
        target, refused = _site_target(client, site, execution_id)
        if target is None:
            return refused
        denied = _policy_guard(target.client, "wait")
        if denied:
            return denied
        try:
            result = target.client.workflows.wait_for_completion(
                target.ident,
                timeout=timeout or DEFAULT_WAIT_TIMEOUT_SEC,
            )
            return _result_json(result, target)
        except Exception as exc:
            return _structured_error("workflow_wait_failed", str(exc))

    tools.append(
        _tool(
            "workflow_wait",
            WORKFLOW_TOOL_HELP["wait"],
            {
                "type": "object",
                "properties": {
                    "execution_id": {"type": "string"},
                    "timeout": {"type": "integer", "default": DEFAULT_WAIT_TIMEOUT_SEC},
                    "site": _SITE_PARAM,
                },
                "required": ["execution_id"],
            },
            _workflow_wait,
        )
    )

    def _workflow_results(execution_id: str, site: str | None = None) -> str:
        target, refused = _site_target(client, site, execution_id)
        if target is None:
            return refused
        denied = _policy_guard(target.client, "results")
        if denied:
            return denied
        try:
            result = target.client.workflows.get_results(target.ident)
            return _result_json(result, target)
        except Exception as exc:
            return _structured_error("workflow_results_failed", str(exc))

    tools.append(
        _tool(
            "workflow_results",
            WORKFLOW_TOOL_HELP["results"],
            {
                "type": "object",
                "properties": {"execution_id": {"type": "string"}, "site": _SITE_PARAM},
                "required": ["execution_id"],
            },
            _workflow_results,
        )
    )

    def _workflow_cancel(execution_id: str, site: str | None = None) -> str:
        target, refused = _site_target(client, site, execution_id, act=True)
        if target is None:
            return refused
        denied = _policy_guard(target.client, "cancel")
        if denied:
            return denied
        try:
            status = target.client.workflows.cancel(target.ident)
            payload = {
                "execution_id": status.execution_id,
                "status": status.status,
                "error": status.error,
            }
            return _tagged_json(payload, target, status.execution_id)
        except Exception as exc:
            return _structured_error("workflow_cancel_failed", str(exc))

    tools.append(
        _tool(
            "workflow_cancel",
            WORKFLOW_TOOL_HELP["cancel"],
            {
                "type": "object",
                "properties": {"execution_id": {"type": "string"}, "site": _SITE_PARAM},
                "required": ["execution_id"],
            },
            _workflow_cancel,
        )
    )

    def _workflow_upload(workflow_path: str, workflow_name: str | None = None) -> str:
        denied = _policy_guard(client, "upload")
        if denied:
            return denied
        try:
            result = client.workflows.upload(workflow_path, workflow_name=workflow_name)
            return json.dumps(
                {
                    "workflow_name": result.workflow_name or result.workflow_id,
                    # ADR-113 D-1/D-2: the registered version and the advice.
                    "version": str(getattr(result, "version", "") or ""),
                    "source_sha256": str(getattr(result, "source_sha256", "") or ""),
                    "warnings": list(getattr(result, "warnings", None) or []),
                    "status": result.status,
                    "accepted": result.accepted,
                    "source_path": result.source_path,
                    "message": result.message,
                    "error": result.error,
                },
                indent=2,
                default=str,
            )
        except Exception as exc:
            return _structured_error("workflow_upload_failed", str(exc))

    tools.append(
        _tool(
            "workflow_upload",
            WORKFLOW_TOOL_HELP["upload"],
            {
                "type": "object",
                "properties": {
                    "workflow_path": {"type": "string"},
                    "workflow_name": {"type": "string"},
                },
                "required": ["workflow_path"],
            },
            _workflow_upload,
        )
    )

    return tools
