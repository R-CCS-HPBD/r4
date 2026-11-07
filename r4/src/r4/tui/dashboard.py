"""Shared backend dashboard collection for R4 terminal UIs."""

from __future__ import annotations

import os
from contextlib import contextmanager
from inspect import Parameter, signature
from typing import Any, Iterator


def dashboard_timeout() -> float:
    """Return the short timeout used by interactive dashboard probes."""
    raw = os.environ.get("R4_TUI_DASHBOARD_TIMEOUT_SEC") or os.environ.get(
        "R4_COMPANION_COMMAND_TIMEOUT_SEC"
    )
    try:
        value = float(raw or "5")
    except ValueError:
        value = 5.0
    return max(1.0, value)


@contextmanager
def _temporary_client_timeout(client: Any, timeout: float) -> Iterator[None]:
    config = getattr(client, "config", None)
    if config is None or not hasattr(config, "timeout"):
        yield
        return
    original = getattr(config, "timeout")
    try:
        setattr(config, "timeout", timeout)
        yield
    finally:
        setattr(config, "timeout", original)


def _accepts_timeout(method: Any) -> bool:
    try:
        parameters = signature(method).parameters
    except (TypeError, ValueError):
        return True
    return "timeout" in parameters or any(
        parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters.values()
    )


def _call(method: Any, *args: Any, timeout: float, **kwargs: Any) -> Any:
    if _accepts_timeout(method):
        kwargs.setdefault("timeout", timeout)
    return method(*args, **kwargs)


def _record_value(record: object, *keys: str, default: str = "") -> str:
    for key in keys:
        if isinstance(record, dict):
            value = record.get(key)
        else:
            value = getattr(record, key, None)
        if value not in (None, ""):
            return str(value)
    return default


def _endpoint_models(endpoint: dict[str, Any]) -> str:
    models = endpoint.get("models")
    if isinstance(models, list) and models:
        return ",".join(str(model) for model in models if model)
    return _record_value(endpoint, "model", "served_model_name", default="?")


def _endpoint_snapshot(endpoint: object) -> dict[str, str]:
    item = endpoint if isinstance(endpoint, dict) else {}
    host = _record_value(item, "host", "node")
    port = _record_value(item, "port", "litellm_port")
    endpoint_url = _record_value(item, "endpoint_url", "url")
    location = f"{host}:{port}" if host and port else endpoint_url
    return {
        "id": _record_value(item, "instance_id", "id", "job_id", default="?"),
        "status": _record_value(item, "status", default="unknown"),
        "model": _endpoint_models(item),
        "backend": _record_value(item, "backend", "execution_authority", default="unknown"),
        "location": location or "-",
    }


def _job_snapshot(job: object) -> dict[str, str]:
    return {
        "id": _record_value(job, "job_id", "id", default="?"),
        "status": _record_value(job, "status", default="unknown"),
        "type": _record_value(job, "job_type", "type"),
        "attempts": _record_value(job, "attempts"),
    }


def _workflow_snapshot(workflow: object) -> dict[str, str]:
    return {
        "id": _record_value(workflow, "workflow_id", "id", "name", default="?"),
        "name": _record_value(workflow, "name", "workflow_id", default="?"),
        "status": _record_value(workflow, "status", default="unknown"),
        "description": _record_value(workflow, "description"),
    }


def _document_snapshot(document: object) -> dict[str, str]:
    return {
        "id": _record_value(document, "document_id", "id", default="?"),
        "name": _record_value(document, "filename", "name", "path", default="?"),
        "status": _record_value(document, "status", "state", default="indexed"),
        "chunks": _record_value(document, "chunks", "chunks_created", "chunk_count"),
    }


def _tool_name(tool: object) -> str | None:
    return _record_value(tool, "name", default="") or None


def _items(payload: Any, key: str) -> list[Any]:
    if payload is None:
        return []
    if isinstance(payload, dict):
        value = payload.get(key, [])
        return list(value) if isinstance(value, (list, tuple)) else []
    if isinstance(payload, (list, tuple)):
        return list(payload)
    return [payload]


def _local_tool_names(local_tools: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for tool in local_tools or []:
        if isinstance(tool, dict):
            name = tool.get("name")
        else:
            name = getattr(tool, "name", None)
        if name:
            names.append(str(name))
    return names


def _skill_names(skill_registry: Any) -> tuple[list[str], list[str]]:
    if skill_registry is None:
        return [], []
    try:
        skills = [str(skill.name) for skill in skill_registry.list_all()]
        active = [str(skill.name) for skill in skill_registry.list_active()]
        return skills, active
    except Exception:
        return [], []


def collect_backend_snapshot(
    client: Any,
    *,
    model: str | None = None,
    mode: str = "chat",
    approval: str = "ask",
    secure: bool = False,
    local_tools: list[dict[str, Any]] | None = None,
    skill_registry: Any = None,
    mcp_manager: Any = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Collect a best-effort, JSON-safe snapshot of R4 backend surfaces."""
    probe_timeout = timeout or dashboard_timeout()
    config = getattr(client, "config", None)
    skills, active_skills = _skill_names(skill_registry)
    snapshot: dict[str, Any] = {
        "model": model or getattr(config, "model", ""),
        "mode": mode,
        "approval": approval,
        "secure": bool(secure),
        "profile": getattr(config, "profile", "default"),
        "project": getattr(config, "project_id", "") or "default",
        "gateway": getattr(config, "gateway_url", ""),
        "readiness": {"ready": False, "error": "not checked"},
        "endpoints": [],
        "jobs": [],
        "workflows": [],
        "rag_documents": [],
        "mcp_summary": "no MCP",
        "mcp_tools": [],
        "local_tools": _local_tool_names(local_tools),
        "skills": skills,
        "active_skills": active_skills,
        "errors": {},
    }
    errors: dict[str, str] = snapshot["errors"]

    with _temporary_client_timeout(client, probe_timeout):
        try:
            snapshot["readiness"] = client.readiness()
        except Exception as exc:
            snapshot["readiness"] = {"ready": False, "error": str(exc)}
            errors["readiness"] = str(exc)

        readiness = snapshot.get("readiness") or {}
        if readiness.get("error"):
            errors.setdefault("backend", "gateway probe failed; skipped backend surface probes")
            return snapshot

        try:
            response = _call(client.list_endpoints, include_stopped=False, timeout=probe_timeout)
            snapshot["endpoints"] = [
                _endpoint_snapshot(endpoint) for endpoint in _items(response, "endpoints")[:8]
            ]
        except Exception as exc:
            errors["endpoints"] = str(exc)

        try:
            jobs_client = getattr(client, "jobs", None)
            if jobs_client is not None and hasattr(jobs_client, "list"):
                jobs = jobs_client.list(limit=8)
            elif hasattr(client, "list_jobs"):
                response = _call(client.list_jobs, timeout=probe_timeout)
                jobs = _items(response, "jobs")
            else:
                jobs = []
            snapshot["jobs"] = [_job_snapshot(job) for job in _items(jobs, "jobs")[:8]]
        except Exception as exc:
            errors["jobs"] = str(exc)

        try:
            workflow_client = getattr(client, "workflows", None)
            if workflow_client is not None and hasattr(workflow_client, "list_workflows"):
                workflows = workflow_client.list_workflows()
            elif hasattr(client, "list_workflows"):
                response = _call(client.list_workflows, timeout=probe_timeout)
                workflows = _items(response, "workflows")
            else:
                workflows = []
            snapshot["workflows"] = [
                _workflow_snapshot(workflow) for workflow in _items(workflows, "workflows")[:8]
            ]
        except Exception as exc:
            errors["workflows"] = str(exc)

        try:
            rag_client = getattr(client, "rag", None)
            if rag_client is not None and hasattr(rag_client, "list_documents"):
                documents = rag_client.list_documents(project_id=snapshot["project"])
            else:
                documents = []
            snapshot["rag_documents"] = [
                _document_snapshot(document) for document in _items(documents, "documents")[:8]
            ]
        except Exception as exc:
            errors["rag"] = str(exc)

        try:
            if mcp_manager is not None:
                snapshot["mcp_summary"] = mcp_manager.summary()
            tools = client.mcp.list_tools()
            snapshot["mcp_tools"] = [
                name for tool in _items(tools, "tools")[:12] if (name := _tool_name(tool))
            ]
        except Exception as exc:
            errors["mcp"] = str(exc)

    return snapshot


__all__ = ["collect_backend_snapshot", "dashboard_timeout"]
