"""Pre/PostToolUse hook system for the R4 agent.

Inspired by PreToolUse/PostToolUse permission hooks.
Hooks can observe, modify, or block tool calls and their results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class HookAction(Enum):
    """Action returned by a pre-tool hook."""

    ALLOW = "allow"
    MODIFY = "modify"  # allow but with modified args
    BLOCK = "block"


@dataclass
class PreToolResult:
    """Result from a pre-tool hook."""

    action: HookAction = HookAction.ALLOW
    modified_args: Optional[Dict[str, Any]] = None
    reason: str = ""


@dataclass
class PostToolResult:
    """Result from a post-tool hook."""

    modified_output: Optional[Any] = None
    suppress: bool = False  # If True, output is hidden from agent
    reason: str = ""


PreToolHook = Callable[[str, Dict[str, Any]], PreToolResult]
"""Signature: (tool_name, tool_args) → PreToolResult"""

PostToolHook = Callable[[str, Dict[str, Any], Any], PostToolResult]
"""Signature: (tool_name, tool_args, tool_output) → PostToolResult"""


class HookRegistry:
    """Registry for pre- and post-tool-use hooks.

    Hooks are executed in registration order. A ``BLOCK`` from any
    pre-hook stops execution. ``MODIFY`` actions are chained.
    """

    def __init__(self) -> None:
        self._pre_hooks: List[PreToolHook] = []
        self._post_hooks: List[PostToolHook] = []

    def add_pre_hook(self, hook: PreToolHook) -> None:
        self._pre_hooks.append(hook)

    def add_post_hook(self, hook: PostToolHook) -> None:
        self._post_hooks.append(hook)

    def run_pre_hooks(self, tool_name: str, args: Dict[str, Any]) -> PreToolResult:
        """Run all pre-hooks. Returns the aggregate result.

        If any hook returns ``BLOCK``, the aggregate is ``BLOCK``.
        ``MODIFY`` actions are chained (each modifies the args for the next).
        """
        current_args = dict(args)
        for hook in self._pre_hooks:
            try:
                result = hook(tool_name, current_args)
            except Exception as exc:
                logger.warning("Pre-hook %s raised: %s", hook, exc)
                continue

            if result.action == HookAction.BLOCK:
                return PreToolResult(
                    action=HookAction.BLOCK,
                    reason=result.reason or "blocked by pre-hook",
                )
            if result.action == HookAction.MODIFY and result.modified_args:
                current_args = result.modified_args

        if current_args != args:
            return PreToolResult(
                action=HookAction.MODIFY,
                modified_args=current_args,
            )
        return PreToolResult(action=HookAction.ALLOW)

    def run_post_hooks(self, tool_name: str, args: Dict[str, Any], output: Any) -> PostToolResult:
        """Run all post-hooks. Returns the aggregate result."""
        current_output = output
        for hook in self._post_hooks:
            try:
                result = hook(tool_name, args, current_output)
            except Exception as exc:
                logger.warning("Post-hook %s raised: %s", hook, exc)
                continue

            if result.suppress:
                return PostToolResult(suppress=True, reason=result.reason)
            if result.modified_output is not None:
                current_output = result.modified_output

        if current_output is not output:
            return PostToolResult(modified_output=current_output)
        return PostToolResult()


# ---------------------------------------------------------------------------
# Built-in hooks
# ---------------------------------------------------------------------------


def egress_scan_pre_hook(tool_name: str, args: Dict[str, Any]) -> PreToolResult:
    """Pre-hook that blocks tools sending data to untrusted destinations."""
    # This is a placeholder — real implementation would use EgressGuard
    suspicious_keys = {"url", "endpoint", "destination", "target"}
    for key in suspicious_keys:
        val = args.get(key, "")
        if isinstance(val, str) and val.startswith("http"):
            logger.info(
                "Egress scan: tool %s arg '%s' contains URL: %s",
                tool_name,
                key,
                val,
            )
    return PreToolResult(action=HookAction.ALLOW)


def large_output_post_hook(tool_name: str, args: Dict[str, Any], output: Any) -> PostToolResult:
    """Post-hook that truncates overly large tool outputs."""
    if isinstance(output, str) and len(output) > 100_000:
        truncated = output[:50_000] + f"\n\n... [truncated {len(output) - 50_000} chars] ..."
        return PostToolResult(
            modified_output=truncated,
            reason="output exceeded 100k chars",
        )
    return PostToolResult()


# ---------------------------------------------------------------------------
# Default hook registry
# ---------------------------------------------------------------------------

_DEFAULT_HOOKS: HookRegistry | None = None


def _default_hook_registry() -> HookRegistry:
    """Return (and cache) the default hook registry with built-in hooks.

    The default registry includes:
    - ``egress_scan_pre_hook`` — logs URL-containing tool args
    - ``large_output_post_hook`` — truncates outputs >100k chars
    """
    global _DEFAULT_HOOKS
    if _DEFAULT_HOOKS is None:
        _DEFAULT_HOOKS = HookRegistry()
        _DEFAULT_HOOKS.add_pre_hook(egress_scan_pre_hook)
        _DEFAULT_HOOKS.add_post_hook(large_output_post_hook)
    return _DEFAULT_HOOKS
