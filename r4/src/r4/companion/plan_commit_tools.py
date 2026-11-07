"""Plan → commit for compute runs (ADR-105 D-4, slice S3).

``workflow_plan`` asks UMRS what a run would use — lane, partition, account,
GPU steps, an upper-bound wall time, quota — and gets back a signed plan;
nothing runs and nothing is stored (SAFE: it works in chat and plan mode).
``job_commit`` hands the signed plan back; UMRS verifies the digest,
re-resolves, and runs it once (the plan id is the idempotency key).

Who approved the commit matters: when no human approved this call (autopilot
on a server allowlist, or an "always" grant), the tool says ``auto`` and
UMRS refuses unless the plan fits the project's auto-commit threshold —
a deterministic server check, not a model's judgement (ADR-076 stays extra
caution only). The ADR-104 rung-10 amendment lives in ``permissions.py``.
"""

from __future__ import annotations

import json
from typing import Any

from .workflow_tools import _parse_workflow_run_inputs, _precheck_params, _structured_error

COMMIT_HELP = (
    "Run a plan that workflow_plan returned: pass the plan object as-is "
    "(it carries `signed` and `digest`). UMRS checks the signature and that "
    "nothing changed, then starts the run once; its end is announced as a "
    "<job-notification>. A stale or expired plan is refused: plan again."
)
PLAN_HELP = (
    "Plan a registered workflow run WITHOUT running it: UMRS validates the "
    "parameters and answers where and how it would run (lane, partition, "
    "account, GPU steps, an upper-bound wall time, quota) plus a signed plan. "
    "Show the user the plan, then call job_commit with it to run."
)


def _plan_payload(plan: Any) -> tuple[str, dict[str, Any], str] | str:
    """(workflow_name, signed, digest) from a plan object, or an error."""
    if isinstance(plan, str):
        try:
            plan = json.loads(plan)
        except ValueError:
            return _structured_error(
                "invalid_plan", "plan must be the object workflow_plan returned"
            )
    if not isinstance(plan, dict):
        return _structured_error("invalid_plan", "plan must be the object workflow_plan returned")
    signed, digest = plan.get("signed"), plan.get("digest")
    if not isinstance(signed, dict) or not isinstance(digest, str) or not digest:
        return _structured_error("invalid_plan", "the plan has no signed/digest: plan again")
    return str(signed.get("workflow_name") or plan.get("workflow_name") or ""), signed, digest


def build_plan_commit_tools(client: Any, permission_manager: Any = None) -> list[dict[str, Any]]:
    """``workflow_plan`` (SAFE) and ``job_commit`` (RISKY, D-4)."""

    def _workflow_plan(workflow_name: str, parameters: Any = None, input_files: Any = None) -> str:
        params, files, error = _parse_workflow_run_inputs(parameters, input_files)
        if error is not None:
            return error
        invalid = _precheck_params(client, workflow_name, params, files)
        if invalid is not None:
            return invalid
        try:
            data = client.workflows.plan(workflow_name, parameters=params, input_files=files)
        except Exception as exc:
            return _structured_error("workflow_plan_failed", str(exc))
        return json.dumps(data, indent=2, default=str)

    def _job_commit(plan: Any = None) -> str:
        parsed = _plan_payload(plan)
        if isinstance(parsed, str):
            return parsed
        workflow_name, signed, digest = parsed
        # A human yes to THIS plan's card (keyed by its digest); anything
        # else — autopilot, an "always" grant, a rule — is an auto commit.
        human = bool(permission_manager and permission_manager.take_commit_approval(digest))
        try:
            data = client.workflows.commit(
                workflow_name, signed=signed, digest=digest, auto=not human
            )
        except Exception as exc:
            return _structured_error("job_commit_failed", str(exc))
        return json.dumps(data, indent=2, default=str)

    return [
        {
            "name": "workflow_plan",
            "description": PLAN_HELP,
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_name": {"type": "string"},
                    "parameters": {
                        "type": "object",
                        "description": "Parameters (workflow_describe parameter_schema)",
                        "additionalProperties": True,
                    },
                    "input_files": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["workflow_name"],
            },
            "execute": _workflow_plan,
        },
        {
            "name": "job_commit",
            "description": COMMIT_HELP,
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "object",
                        "description": "The plan workflow_plan returned (signed + digest)",
                    },
                },
                "required": ["plan"],
            },
            "execute": _job_commit,
        },
    ]
