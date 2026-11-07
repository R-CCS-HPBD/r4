"""U4: client data layer for the knowledge explorer.

Provider functions accept injected callables so tests run without a
backend; the CLI wires them to ``Client().knowledge``. Each helper is
fault-isolated — one broken section degrades to an error row instead of
aborting the explorer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

SECTIONS = ("artifacts", "findings", "memory", "plans", "graphs")

_READINESS_GLYPH = {
    "ready": "[green]✓[/green]",
    "partial": "[yellow]◐[/yellow]",
    "insufficient": "[red]✗[/red]",
}


def verification_badge(level: Any) -> str:
    """Map a readiness level string to a short badge glyph."""
    return _READINESS_GLYPH.get(str(level or "").lower(), "[dim]…[/dim]")


def _row_error(exc: Exception, section: str) -> dict[str, Any]:
    """An error row replacing a failed section fetch."""
    logger.warning("Explorer fetch failed for %s: %s", section, exc)
    return {
        "id": f"error-{section}",
        "title": f"⚠ {section} unavailable",
        "label": str(exc)[:120],
        "kind": "error",
    }


# ── Section tree ─────────────────────────────────────────────────────────────


def fetch_sections(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the four section rows with count badges from cached payloads."""
    counts = {
        "artifacts": len(data.get("artifacts") or []),
        "findings": len(data.get("findings") or []),
        "memory": len(data.get("memory") or []),
        "plans": len(data.get("plans") or []),
        "graphs": len(data.get("graphs") or []),
    }
    rows = []
    for name in SECTIONS:
        badge = verification_badge(data.get("readiness_level"))
        rows.append(
            {
                "id": name,
                "title": f"{badge} {name}",
                "label": f"{counts[name]} item(s)",
                "count": counts[name],
            }
        )
    return rows


def _call_safe(fn: Callable[[], Any], section: str) -> list[dict[str, Any]]:
    """Run a fetcher, converting failures into an error row list."""
    try:
        return fn() or []
    except Exception as exc:  # noqa: BLE001
        return [_row_error(exc, section)]


def _payload_safe(fn: Callable[[], Any], fallback: Any) -> Any:
    """fetch_project_data's per-section isolation (project_data()'s graphs
    pattern): a broken or retired lane degrades to its empty payload — the
    detail panes carry the ⚠ row via _call_safe."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one section must not abort the explorer
        return fallback


def fetch_project_data(knowledge_client, project_id: str) -> dict[str, Any]:
    """Gather the explorer's full read model for one project (per section)."""
    artifacts = knowledge_client.status(project_id).get("artifacts", [])
    findings = knowledge_client.findings(project_id)
    # memory/planner lanes are retired platform-side (no server route,
    # 2026-10-20): they degrade to empty sections, not an abort.
    memory = _payload_safe(lambda: knowledge_client.memory(project_id).get("memories") or [], [])
    plans = _payload_safe(lambda: knowledge_client.list_plans(project_id), [])
    readiness = knowledge_client.readiness(project_id).get("readiness_level", "")
    return {
        "artifacts": artifacts,
        "findings": findings if isinstance(findings, list) else [],
        "memory": memory,
        "plans": plans if isinstance(plans, list) else [],
        "readiness_level": readiness,
    }


# ── Documents pane ────────────────────────────────────────────────────────────


def fetch_documents(knowledge_client, project_id: str, section: str | None) -> list[dict[str, Any]]:
    """Documents for a section (uniform row shape across source types)."""
    section = section or "artifacts"
    fetchers = {
        "artifacts": lambda: _artifact_rows(knowledge_client, project_id),
        "findings": lambda: _finding_rows(knowledge_client, project_id),
        "memory": lambda: _memory_rows(knowledge_client, project_id),
        "plans": lambda: _plan_rows(knowledge_client, project_id),
    }
    fetcher = fetchers.get(section)
    if fetcher is None:
        return [_row_error(ValueError(f"unknown section {section}"), section)]
    return _call_safe(fetcher, section)


def _artifact_rows(knowledge_client, project_id: str) -> list[dict[str, Any]]:
    status_payload = knowledge_client.status(project_id)
    items = status_payload.get("artifacts", [])
    readiness = status_payload.get("readiness_level", "")
    rows = []
    for item in items:
        artifact_id = item.get("artifact_id", "")
        title = item.get("title") or artifact_id
        rows.append(
            {
                "id": artifact_id,
                "title": f"{verification_badge(readiness)} {title}",
                "label": item.get("kind") or item.get("mime_type") or "artifact",
                "raw": item,
            }
        )
    return rows


def _finding_rows(knowledge_client, project_id: str) -> list[dict[str, Any]]:
    rows = []
    for item in knowledge_client.findings(project_id):
        body = item.get("message") or item.get("detail") or item.get("id", "")
        state = item.get("severity") or item.get("status", "open")
        rows.append(
            {
                "id": item.get("id", ""),
                "title": f"[{state}] {body[:80]}",
                "label": item.get("check") or item.get("spec_name") or "finding",
                "raw": item,
            }
        )
    return rows


def _memory_rows(knowledge_client, project_id: str) -> list[dict[str, Any]]:
    entries = knowledge_client.memory(project_id).get("memories", [])
    return [
        {
            "id": m.get("id", ""),
            "title": m.get("title") or m.get("summary") or "(memory)",
            "label": m.get("type") or "memory",
            "raw": m,
        }
        for m in entries
    ]


def _plan_rows(knowledge_client, project_id: str) -> list[dict[str, Any]]:
    rows = []
    for item in knowledge_client.list_plans(project_id):
        rows.append(
            {
                "id": item.get("plan_id", ""),
                "title": item.get("goal") or "(plan)",
                "label": item.get("status") or "plan",
                "raw": item,
            }
        )
    return rows


# ── Detail pane ──────────────────────────────────────────────────────────────


def fetch_detail(
    knowledge_client,
    project_id: str,
    section: str | None,
    doc_id: str,
) -> dict[str, Any]:
    """Detail payload for one document (evidence chain when applicable)."""
    if section == "artifacts":
        return {
            "title": doc_id,
            "body": knowledge_client.get_lineage(project_id, doc_id),
            "kind": "lineage",
        }
    return {"title": doc_id, "body": {"section": section, "id": doc_id}, "kind": "row"}
