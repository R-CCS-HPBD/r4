"""Endpoint expiry guard policy for the research companion."""

from __future__ import annotations

from typing import Any, Callable

DEFAULT_ENDPOINT_WARNING_THRESHOLD_SEC = 15 * 60
DEFAULT_ENDPOINT_BLOCK_THRESHOLD_SEC = 5 * 60

TOOL_REQUEST_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hpc_submit": (
        "batch job",
        "launch job",
        "sbatch",
        "slurm",
        "submit",
    ),
    "python_exec": (
        "analyze",
        "analyse",
        "benchmark",
        "compute",
        "corpus",
        "dataset",
        "embed",
        "embedding",
        "export",
        "index",
        "ingest",
        "plot",
        "process",
        "python",
        "script",
    ),
    "shell_exec": (
        "benchmark",
        "command",
        "execute",
        "launch",
        "pipeline",
        "run",
        "scan",
        "start",
        "workflow",
    ),
}

LONG_RUNNING_TOOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hpc_submit": TOOL_REQUEST_KEYWORDS["hpc_submit"],
    "python_exec": (
        "benchmark",
        "corpus",
        "dataset",
        "embed",
        "embedding",
        "export",
        "full",
        "index",
        "ingest",
        "process",
        "scan",
        "train",
        "workflow",
    ),
    "shell_exec": (
        "benchmark",
        "full",
        "launch",
        "pipeline",
        "scan",
        "server",
        "service",
        "workflow",
    ),
}


def match_tools(
    user_message: str,
    tools: list[dict[str, Any]],
    keyword_map: dict[str, tuple[str, ...]],
) -> list[str]:
    lowered = user_message.lower()
    matches: list[str] = []
    for tool in tools:
        name = tool.get("name")
        if not isinstance(name, str):
            continue
        keywords = keyword_map.get(name)
        if not keywords:
            continue
        if any(keyword in lowered for keyword in keywords):
            matches.append(name)
    return matches


def evaluate_endpoint_guard(
    *,
    user_message: str,
    tools: list[dict[str, Any]],
    active_endpoints: list[dict[str, Any]],
    warning_threshold_sec: int,
    block_threshold_sec: int,
    format_duration: Callable[[int], str],
) -> dict[str, str]:
    requested_tools = match_tools(user_message, tools, TOOL_REQUEST_KEYWORDS)
    if not requested_tools:
        return {"state": "clear"}

    matched_tools = match_tools(user_message, tools, LONG_RUNNING_TOOL_KEYWORDS)
    if not matched_tools:
        return {"state": "clear"}

    expiring_endpoints = [
        endpoint
        for endpoint in active_endpoints
        if isinstance(endpoint.get("remaining_seconds"), int)
    ]
    if not expiring_endpoints:
        return {"state": "clear"}

    nearest = min(expiring_endpoints, key=lambda endpoint: endpoint["remaining_seconds"])
    endpoint_id = nearest.get("instance_id", "unknown")
    backend = nearest.get("backend", "unknown")
    remaining_seconds = nearest["remaining_seconds"]
    remaining_text = format_duration(remaining_seconds)
    tool_text = ", ".join(matched_tools)

    if remaining_seconds <= block_threshold_sec:
        return {
            "state": "blocked",
            "message": (
                f"Active endpoint {endpoint_id} ({backend}) expires in {remaining_text}. "
                f"I blocked this request because it likely needs long-running tools ({tool_text}). "
                "Renew or reprovision the endpoint before retrying."
            ),
        }

    if remaining_seconds <= warning_threshold_sec:
        return {
            "state": "warning",
            "prompt": (
                "[Endpoint expiry guard]\n"
                f"- Active endpoint {endpoint_id} ({backend}) expires in {remaining_text}. "
                f"The request likely needs tools such as {tool_text}. "
                "Prefer inspection or short actions unless the endpoint can be renewed first."
            ),
        }

    return {"state": "clear"}
