"""R4 adapter: WorkflowExecutionPort over ``Client.workflows``."""

from __future__ import annotations

from typing import Any


class R4ClientWorkflowExecutor:
    """Injected composition-layer adapter for real research mode.

    Wraps ``client.workflows.trigger/status/wait_for_completion/get_results/cancel``.
    When ``require_durable=True`` (default), fails fast if the backend does not
    return a ``durable_job_id``.
    """

    def __init__(self, client: Any, *, require_durable: bool = True) -> None:
        self._client = client
        self._require_durable = require_durable
        self._durable_by_execution: dict[str, str] = {}

    def trigger(self, workflow_name: str, params: dict[str, Any]) -> str:
        # Prefer the research-loop project_id in params over Client profile default.
        project_id = params.get("project_id") if isinstance(params, dict) else None
        result = self._client.workflows.trigger(
            workflow_name,
            parameters=params,
            project_id=project_id if isinstance(project_id, str) else None,
        )
        durable = getattr(result, "durable_job_id", None)
        if self._require_durable and not durable:
            raise RuntimeError("durable job backend required for real research mode")
        execution_id = result.execution_id
        if durable:
            self._durable_by_execution[execution_id] = str(durable)
        return execution_id

    def status(self, execution_id: str) -> dict[str, Any]:
        current = self._client.workflows.status(execution_id)
        return self._status_to_dict(current, execution_id)

    def wait(self, execution_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        wait_timeout = int(timeout) if timeout is not None else None
        result = self._client.workflows.wait_for_completion(
            execution_id,
            timeout=wait_timeout,
        )
        return self._result_to_dict(result, execution_id)

    def results(self, execution_id: str) -> dict[str, Any]:
        result = self._client.workflows.get_results(execution_id)
        return self._result_to_dict(result, execution_id)

    def cancel(self, execution_id: str) -> dict[str, Any]:
        current = self._client.workflows.cancel(execution_id)
        return self._status_to_dict(current, execution_id)

    def _status_to_dict(self, current: Any, execution_id: str) -> dict[str, Any]:
        durable = self._durable_by_execution.get(execution_id) or getattr(
            current, "durable_job_id", None
        )
        return {
            "execution_id": getattr(current, "execution_id", execution_id) or execution_id,
            "workflow_name": getattr(current, "workflow_name", "") or "",
            "status": getattr(current, "status", "unknown"),
            "error": getattr(current, "error", None),
            "progress": getattr(current, "progress", None),
            "durable_job_id": durable,
        }

    def _result_to_dict(self, result: Any, execution_id: str) -> dict[str, Any]:
        durable = getattr(result, "durable_job_id", None) or self._durable_by_execution.get(
            execution_id
        )
        if durable:
            self._durable_by_execution[execution_id] = str(durable)
        if self._require_durable and not durable:
            raise RuntimeError("durable job backend required for real research mode")
        return {
            "execution_id": getattr(result, "execution_id", execution_id) or execution_id,
            "workflow_name": getattr(result, "workflow_name", "") or "",
            "status": getattr(result, "status", "unknown"),
            "outputs": getattr(result, "outputs", None) or {},
            "artifacts": getattr(result, "artifacts", None) or [],
            "metrics": getattr(result, "metrics", None) or {},
            "error": getattr(result, "error", None),
            "durable_job_id": durable,
        }
