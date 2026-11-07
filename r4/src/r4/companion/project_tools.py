"""Companion tools for project context — list, create, switch.

Knowledge tools require a current project; the chat agent previously had no
way to create one, so knowledge/research prompts stalled. These wrap the
shared local registry (``r4.project.ProjectManager``) — the same registry the
``projects/*`` ACP methods and the ``r4 project`` CLI drive — and sync the
client config so follow-up knowledge calls resolve the new current project.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from r4.client import Client
    from r4.project import ProjectConfig


def _tool(name: str, description: str, parameters: dict[str, Any], execute) -> dict[str, Any]:
    return {"name": name, "description": description, "parameters": parameters, "execute": execute}


def _structured_error(code: str, message: str, **details: Any) -> str:
    return json.dumps(
        {"type": "project_error", "code": code, "message": message, "details": details},
        indent=2,
    )


def _sync_client_config(client: "Client", project: "ProjectConfig") -> None:
    """Point the live client config at the project (created once, kept stale otherwise)."""
    config = getattr(client, "config", None)
    if config is None:
        return
    try:
        config.project_id = project.project_id
        if project.gateway_url and not getattr(config, "gateway_url", None):
            config.gateway_url = project.gateway_url
        if project.model and not getattr(config, "model", None):
            config.model = project.model
    except Exception:  # noqa: BLE001
        logger.debug("Client config sync skipped for %s", project.project_id, exc_info=True)


def _serialize(project: "ProjectConfig", current: str | None) -> dict[str, Any]:
    return {
        "project_id": project.project_id,
        "name": project.name,
        "gateway_url": project.gateway_url,
        "model": project.model,
        "active": project.project_id == current,
    }


def _pick(value: str | None, fallback: str | None) -> str | None:
    return value if (value and value.strip()) else fallback


def _build_project_ensure(client: "Client") -> dict[str, Any]:
    def _ensure(
        project_id: str,
        name: str | None = None,
        gateway: str | None = None,
        model: str | None = None,
    ) -> str:
        from r4.project import ProjectConfig, get_project_manager

        pid = (project_id or "").strip()
        if not pid:
            return _structured_error(
                "invalid_project_id",
                "project_id is required — derive a short slug from the research subject "
                '(e.g. "mermaids-seismic").',
            )
        pm = get_project_manager()
        existing = pm.get(pid)
        project = ProjectConfig(
            project_id=pid,
            name=_pick(name, existing.name if existing else None),
            gateway_url=_pick(gateway, existing.gateway_url if existing else None),
            model=_pick(model, existing.model if existing else None),
            metadata=dict(existing.metadata) if existing else {},
        )
        try:
            pm.create(project)
            pm.set_current(pid)
        except Exception as exc:  # noqa: BLE001
            return _structured_error("project_ensure_failed", str(exc), project_id=pid)
        _sync_client_config(client, project)
        return json.dumps(
            {
                "type": "project_ensure",
                "created": existing is None,
                "project_id": pid,
                "current": pid,
                "project": _serialize(project, pid),
                "next": (
                    "knowledge_ingest with urls/files/sources to feed the graph; "
                    "knowledge_status for counts; research_loop_start for iterations; "
                    "Studio shows projects and the knowledge graph."
                ),
            },
            indent=2,
            default=str,
        )

    return _tool(
        "project_ensure",
        "Create the project if missing (idempotent) and make it the current "
        "one. ALWAYS call this first when the user starts a knowledge or "
        "research task and no project is current — derive the id from the "
        'subject (lowercase slug, e.g. "mermaids-seismic"). Then proceed with '
        "knowledge_ingest.",
        {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": 'Short unique slug (e.g. "mermaids-seismic")',
                },
                "name": {"type": "string", "description": "Human-readable project name"},
                "gateway": {"type": "string", "description": "Gateway URL for this project"},
                "model": {"type": "string", "description": "Default model for this project"},
            },
            "required": ["project_id"],
        },
        _ensure,
    )


def build_project_tools(client: "Client") -> list[dict[str, Any]]:
    """Return project-context tools for Companion.

    ``project_list`` / ``project_use`` already live in ``tools.py``; this only
    adds the create-if-missing verb the agent needs before knowledge calls.
    """
    return [
        _build_project_ensure(client),
    ]
