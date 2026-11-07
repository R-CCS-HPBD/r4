"""Workflow policy loader for MCP platform tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from r4_contracts.workflows import WORKFLOW_MUTATION_OPERATIONS, WORKFLOW_OPERATIONS

DEFAULT_POLICY: dict[str, Any] = {
    "version": "1",
    # Default-allow posture (2026-08-30): the local guard is a VETO layer for
    # operators who point R4_WORKFLOW_POLICY_FILE at a restrictive policy —
    # daily companion operation of UMRS workflows must work out of the box.
    # The authoritative gate stays server-side (ADR-027 workflow_author/admin).
    "defaults": {
        "list": True,
        "describe": True,
        "status": True,
        "results": True,
        "wait": True,
        "run": True,
        "cancel": True,
        "upload": True,
    },
    "projects": {},
    "agents": {},
}


def _default_operations() -> dict[str, bool]:
    defaults = DEFAULT_POLICY.get("defaults", {})
    return {op: bool(defaults.get(op, False)) for op in WORKFLOW_OPERATIONS}


def _mutation_fail_closed_operations() -> dict[str, bool]:
    operations = _default_operations()
    for operation in WORKFLOW_MUTATION_OPERATIONS:
        operations[operation] = False
    return operations


def _read_policy_file(path: str) -> dict[str, Any] | None:
    """Read the env-pointed policy file; None signals fail-closed."""
    policy_path = Path(path).expanduser()
    if not policy_path.is_file():
        return None
    try:
        loaded = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _resolve_raw_policy() -> dict[str, Any] | None:
    """Raw policy dict, or None when the env-pointed file fails closed."""
    path = os.environ.get("R4_WORKFLOW_POLICY_FILE", "")
    if not path:
        return dict(DEFAULT_POLICY)
    loaded = _read_policy_file(path)
    if loaded is None:
        return None
    raw = dict(DEFAULT_POLICY)
    raw.update(loaded)
    return raw


def _merge_override_table(ops: dict[str, Any], override: object) -> None:
    """Merge one project/agent override table into ops (dict entries only)."""
    if not isinstance(override, dict):
        return
    for key, value in override.items():
        if key in WORKFLOW_OPERATIONS:
            ops[key] = bool(value)


def _scope_tables(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """projects/agents tables from the raw policy (empty dicts when absent)."""
    projects = raw.get("projects") if isinstance(raw.get("projects"), dict) else {}
    agents = raw.get("agents") if isinstance(raw.get("agents"), dict) else {}
    return projects, agents


def _apply_named_overrides(
    ops: dict[str, Any], raw: dict[str, Any], project_id: str | None, agent_id: str | None
) -> dict[str, bool]:
    """Merge project/agent override tables into the defaults map."""
    projects, agents = _scope_tables(raw)
    _merge_override_table(ops, projects.get(project_id or ""))
    _merge_override_table(ops, agents.get(agent_id or ""))
    return {op: bool(ops.get(op, False)) for op in WORKFLOW_OPERATIONS}


def load_policy(project_id: str | None = None, agent_id: str | None = None) -> dict[str, bool]:
    raw = _resolve_raw_policy()
    if raw is None:
        return _mutation_fail_closed_operations()
    return _apply_named_overrides(dict(raw.get("defaults", {})), raw, project_id, agent_id)
