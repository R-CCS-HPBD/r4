"""Load deployment workflow capability policy for Companion and MCP."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from r4_contracts.workflows import (
    WORKFLOW_CAPABILITY_RISK,
    WORKFLOW_MUTATION_OPERATIONS,
    WORKFLOW_OPERATIONS,
)

DEFAULT_POLICY: dict[str, Any] = {
    "version": "1",
    "defaults": {
        "list": True,
        "describe": True,
        "status": True,
        "results": True,
        "wait": True,
        "run": True,
        "cancel": True,
        # Default-allow: local policy is a veto layer for operators, not a
        # daily gate (server-side ADR-027 role check stays the authority).
        "upload": True,
    },
    "projects": {},
    "agents": {},
}


@dataclass
class WorkflowPolicy:
    """Resolved workflow capability policy."""

    operations: dict[str, bool] = field(default_factory=dict)
    source: str = "defaults"
    raw: dict[str, Any] = field(default_factory=dict)

    def allows(self, operation: str) -> bool:
        if operation not in WORKFLOW_OPERATIONS:
            return False
        return bool(self.operations.get(operation, False))

    def risk(self, operation: str) -> str:
        return WORKFLOW_CAPABILITY_RISK.get(operation, "elevated")


def _merge_policy(
    base: dict[str, bool],
    override: dict[str, Any] | None,
) -> dict[str, bool]:
    merged = dict(base)
    if not isinstance(override, dict):
        return merged
    for key in WORKFLOW_OPERATIONS:
        if key in override:
            merged[key] = bool(override[key])
    return merged


def _default_operations() -> dict[str, bool]:
    return _merge_policy(DEFAULT_POLICY.get("defaults", {}), None)


def _mutation_fail_closed_operations() -> dict[str, bool]:
    operations = _default_operations()
    for operation in WORKFLOW_MUTATION_OPERATIONS:
        operations[operation] = False
    return operations


def _closed_policy(reason: str, path: Path) -> WorkflowPolicy:
    """Fail-closed policy for unreadable env-pointed policy files."""
    return WorkflowPolicy(
        operations=_mutation_fail_closed_operations(),
        source=reason,
        raw={"path": str(path)},
    )


def _read_named_policy_file(path: str) -> tuple[dict[str, Any], str] | WorkflowPolicy:
    """Raw policy dict and source, or a closed WorkflowPolicy on failure."""
    policy_path = Path(path).expanduser()
    if not policy_path.is_file():
        return _closed_policy("missing_policy_file", policy_path)
    try:
        loaded = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _closed_policy("invalid_policy_file", policy_path)
    if not isinstance(loaded, dict):
        return _closed_policy("invalid_policy_file", policy_path)
    raw = dict(DEFAULT_POLICY)
    raw.update(loaded)
    return raw, str(policy_path)


def _named_scopes(
    operations: dict[str, bool],
    raw: dict[str, Any],
    *,
    project_id: str | None,
    agent_id: str | None,
) -> tuple[dict[str, bool], str | None]:
    """Apply project/agent override tables; report the winning scope label."""
    projects = raw.get("projects") if isinstance(raw.get("projects"), dict) else {}
    agents = raw.get("agents") if isinstance(raw.get("agents"), dict) else {}
    source: str | None = None
    if project_id and project_id in projects:
        operations = _merge_policy(operations, projects[project_id])
        source = f"project:{project_id}"
    if agent_id and agent_id in agents:
        operations = _merge_policy(operations, agents[agent_id])
        source = f"agent:{agent_id}"
    return operations, source


def load_workflow_policy(
    *,
    project_id: str | None = None,
    agent_id: str | None = None,
    policy_file: str | None = None,
) -> WorkflowPolicy:
    """Load policy from ``R4_WORKFLOW_POLICY_FILE`` with optional overrides."""
    path = policy_file or os.environ.get("R4_WORKFLOW_POLICY_FILE", "")
    raw = dict(DEFAULT_POLICY)
    source = "defaults"
    if path:
        resolved = _read_named_policy_file(path)
        if isinstance(resolved, WorkflowPolicy):
            return resolved
        raw, source = resolved

    operations = _merge_policy(raw.get("defaults", {}), None)
    operations, scope_source = _named_scopes(
        operations, raw, project_id=project_id, agent_id=agent_id
    )
    return WorkflowPolicy(
        operations=operations,
        source=scope_source or source,
        raw=raw,
    )


def policy_denied_error(operation: str, policy: WorkflowPolicy) -> dict[str, Any]:
    """Deny payload for workflow operations blocked by the local companion policy.

    Distinguishes the local fail-closed guard from any server-side deny so an
    agent (or user) never confuses 'companion never forwarded the call' with
    'the gateway/UMRS rejected it' — the 2026-08-30 misdiagnosis case.
    """
    return {
        "error": "policy_denied",
        "code": "workflow_operation_disabled",
        "message": (
            f"Workflow operation '{operation}' is disabled by the local companion "
            f"policy (evaluated client-side; no request reached the gateway/UMRS "
            "— this is not a server-side deny)."
        ),
        "policy_source": policy.source,
        "surface": "r4_companion.local_policy",
        "remediation": (
            "Set R4_WORKFLOW_POLICY_FILE to a JSON enabling this operation "
            f'(e.g. {{"version": "1", "defaults": {{"{operation}": true}}}}) '
            "in the shell that launches the TUI. If your deployment normally "
            "allows it, the gateway-side ADR-027 role gate (workflow_author "
            "claim or admin) applies after this local guard is satisfied."
        ),
    }
