"""Persistent, source-tracked permission rules (G14, 2026-10-29; ADR-104 D-3).

One loader, three files, deny-first, with content-level patterns:

- ``~/.config/r4/permissions.json`` (``user``) — ``allow``/``ask``/``deny``.
- ``~/.config/r4/projects/<id>/permissions.json`` (``user_project``) —
  ``allow``/``ask``/``deny`` for one project, edited by hand and listed or
  pruned with ``/settings rules`` (ADR-104 amendment 1: the approval card no
  longer writes it). The console's older list format
  ``[{"tool": "file_write", "note": …}]`` is read as ``allow`` entries (an
  entry with an argument glob never matched anything and is skipped).
- ``<workspace>/.r4/permissions.json`` (``project``) — only ``ask``/``deny``
  honored; a checked-in file must never *loosen* guardrails (``allow``
  entries are ignored with a warning — repo content must not grant itself
  privileges).

Entry shapes: ``"tool_name"`` (a whole tool; ``fnmatch`` wildcards, so
``"mcp.*"`` names every MCP tool) or ``"tool_name(prefix:text)"`` (the
call's primary string argument must start with the text, e.g.
``shell_exec(prefix:pytest)`` allows ``pytest -q`` but not ``npm publish``).
Every rule carries its source; denials name it.

Also the dangerous-path fence: writes to paths that turn into code
execution or credential access (``.git/``, shell rc files, hook configs,
key material) ask in every mode, whatever a rule allows — the
project-level analog of the existing credential/force-push fences.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

USER_RULES_FILE = "~/.config/r4/permissions.json"
USER_PROJECT_RULES_FILE = "~/.config/r4/projects/{project}/permissions.json"
PROJECT_RULES_FILE = ".r4/permissions.json"

_RULE_RE = re.compile(r"^(?P<tool>[A-Za-z0-9_*.?:-]+?)(?:\(prefix:(?P<prefix>.+)\))?$")
_BEHAVIORS = ("deny", "ask", "allow")  # evaluation order is deny-first

#: The first-string arg a prefix matches against, per tool (best effort).
_PRIMARY_ARGS = {
    "shell_exec": "command",
    "python_exec": "code",
    "file_write": "path",
    "file_edit": "path",
    "apply_patch": "patch_text",
    "file_read": "path",
}

#: Path fragments that always ask (never silently approved by rules/trust).
_DANGEROUS_PATH_PARTS = (
    "/.git/",
    "/.git\\",
    ".git/hooks",
    ".bashrc",
    ".bash_profile",
    ".zshrc",
    ".zprofile",
    "/.ssh/",
    "/.gnupg/",
    ".mcp.json",
    ".r4/hooks.json",
    ".r4/build",
    ".r4/permissions.json",
    # The rule files themselves: an agent never edits its own permissions.
    ".config/r4/permissions.json",
    ".config/r4/projects/",
    "sources.json",
    "credentials.json",
)
_FILE_TOOLS = ("file_write", "file_edit")


@dataclass
class PermissionRule:
    behavior: str
    tool_name: str
    prefix: str
    source: str
    raw: str = ""

    def matches_name(self, tool_name: str) -> bool:
        return fnmatch.fnmatchcase(tool_name, self.tool_name)

    def matches(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        if not self.matches_name(tool_name):
            return False
        if not self.prefix:
            return True
        return _primary_arg(tool_name, arguments).startswith(self.prefix)


def _primary_arg(tool_name: str, arguments: dict[str, Any]) -> str:
    key = _PRIMARY_ARGS.get(tool_name)
    if key is not None:
        value = arguments.get(key)
        return value if isinstance(value, str) else ""
    for value in arguments.values():
        if isinstance(value, str):
            return value
    return ""


@dataclass
class RuleSet:
    rules: list[PermissionRule] = field(default_factory=list)

    def first(
        self, behavior: str, tool_name: str, arguments: dict[str, Any]
    ) -> PermissionRule | None:
        """The first rule of ``behavior`` that matches this call, or None."""
        for rule in self.rules:
            if rule.behavior == behavior and rule.matches(tool_name, arguments):
                return rule
        return None

    def decision(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[str, PermissionRule] | None:
        """The first matching rule in deny → ask → allow order, or None."""
        for behavior in _BEHAVIORS:
            rule = self.first(behavior, tool_name, arguments)
            if rule is not None:
                return behavior, rule
        return None

    def denies_tool(self, tool_name: str) -> bool:
        """True when a deny rule covers the tool whatever its arguments."""
        return any(
            rule.behavior == "deny" and not rule.prefix and rule.matches_name(tool_name)
            for rule in self.rules
        )

    def add(self, rule: PermissionRule) -> None:
        """Take a rule written during the session (no reload needed)."""
        if not any(r.raw == rule.raw and r.behavior == rule.behavior for r in self.rules):
            self.rules.append(rule)

    def remove(self, raw: str, behavior: str, source: str) -> None:
        self.rules = [
            r
            for r in self.rules
            if not (r.raw == raw and r.behavior == behavior and r.source == source)
        ]

    def __bool__(self) -> bool:
        return bool(self.rules)


def _parse_entry(entry: Any, behavior: str, source: str) -> PermissionRule | None:
    if not isinstance(entry, str):
        logger.warning("permission rules (%s): non-string entry %r skipped", source, entry)
        return None
    match = _RULE_RE.match(entry.strip())
    if not match:
        logger.warning("permission rules (%s): malformed entry %r skipped", source, entry)
        return None
    return PermissionRule(
        behavior=behavior,
        tool_name=match.group("tool"),
        prefix=(match.group("prefix") or "").strip(),
        source=source,
        raw=entry,
    )


def _legacy_list(data: list[Any]) -> dict[str, list[str]]:
    """The console's pre-ADR-104 project file: tool entries become ``allow``."""
    tools = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("tool"), str):
            continue
        if item.get("pattern"):
            logger.warning("permission rules: argument glob %r skipped", item.get("pattern"))
            continue
        tools.append(item["tool"])
    return {"allow": tools}


def _read_rules(path: Path) -> dict[str, Any] | None:
    """The rule object a file holds ({} when absent), None when unreadable."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        logger.warning("permission rules: %s unreadable (%s) — skipped", path, exc)
        return None
    if isinstance(data, list):
        return _legacy_list(data)
    if not isinstance(data, dict):
        logger.warning("permission rules: %s must hold an object — skipped", path)
        return None
    return data


def _load_file(path: Path, source: str, *, honor_allow: bool, into: list[PermissionRule]) -> None:
    data = _read_rules(path)
    if not data:
        return
    for behavior in _BEHAVIORS:
        if behavior == "allow" and not honor_allow:
            _warn_allow_ignored(data, path)
            continue
        _collect_entries(data, behavior, source, into)


def _warn_allow_ignored(data: dict[str, Any], path: Path) -> None:
    if data.get("allow"):
        logger.warning(
            "permission rules: %s allow entries ignored (project files may only tighten)",
            path,
        )


def _collect_entries(
    data: dict[str, Any], behavior: str, source: str, into: list[PermissionRule]
) -> None:
    for entry in data.get(behavior) or []:
        rule = _parse_entry(entry, behavior, source)
        if rule is not None:
            into.append(rule)


def _safe_project(project_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", project_id or "") or "default"


def user_project_rules_path(project_id: str) -> Path:
    """The per-project user rule file (the card's "always for this project")."""
    return Path(
        os.path.expanduser(USER_PROJECT_RULES_FILE.format(project=_safe_project(project_id)))
    )


def rule_sources(workspace_root: str | None, project_id: str = "") -> list[tuple[str, Path, bool]]:
    """``(source, path, allow honored)`` for each rule file, in load order."""
    sources = [("user", Path(os.path.expanduser(USER_RULES_FILE)), True)]
    if project_id:
        sources.append(("user_project", user_project_rules_path(project_id), True))
    if workspace_root:
        sources.append(("project", Path(workspace_root) / PROJECT_RULES_FILE, False))
    return sources


def load_permission_rules(workspace_root: str | None, project_id: str = "") -> RuleSet:
    """Merge user rules, the user's rules for ``project_id`` (all behaviors)
    and the workspace's checked-in rules (ask/deny only)."""
    merged: list[PermissionRule] = []
    for source, path, honor_allow in rule_sources(workspace_root, project_id):
        _load_file(path, source, honor_allow=honor_allow, into=merged)
    return RuleSet(merged)


def remove_project_rule(project_id: str, behavior: str, raw: str) -> bool:
    """Drop one entry from the project's user rule file; True when removed."""
    path = user_project_rules_path(project_id)
    data = _read_rules(path)
    entries = list((data or {}).get(behavior) or [])
    if raw not in entries:
        return False
    entries.remove(raw)
    path.write_text(json.dumps({**(data or {}), behavior: entries}, indent=2) + "\n")
    return True


def rule_rows(ruleset: RuleSet | None) -> list[dict[str, Any]]:
    """The rules as ``/settings rules`` lists them (index = position)."""
    rows = []
    for index, rule in enumerate(ruleset.rules if ruleset else []):
        rows.append(
            {
                "index": index,
                "behavior": rule.behavior,
                "rule": rule.raw or rule.tool_name,
                "source": rule.source,
                "removable": rule.source == "user_project",
            }
        )
    return rows


def _patch_paths(arguments: dict[str, Any]) -> list[str]:
    """The files an ``apply_patch`` call names (both diff and Codex forms)."""
    text = arguments.get("patch_text")
    if not isinstance(text, str) or not text:
        return []
    from .apply_patch import parse_patch

    try:
        patches = parse_patch(text)
    except Exception:  # noqa: BLE001 - a malformed patch fails in the tool
        return []
    return [p for fp in patches for p in (fp.old_path, fp.new_path) if p]


def _written_paths(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    if tool_name == "apply_patch":
        return _patch_paths(arguments)
    if tool_name in _FILE_TOOLS:
        path = str(arguments.get("path") or "")
        return [path] if path else []
    return []


def _protected_marker(path: str) -> str | None:
    lowered = "/" + path.replace("\\", "/").lower()
    for marker in _DANGEROUS_PATH_PARTS:
        if marker.replace("\\", "/").lower() in lowered:
            return marker
    return None


def dangerous_path_reason(tool_name: str, arguments: dict[str, Any]) -> str | None:
    """Why a write to this path always asks, or None — paths that become
    code execution or credential access must not pass silently."""
    for path in _written_paths(tool_name, arguments):
        marker = _protected_marker(path)
        if marker is not None:
            return (
                f"{tool_name} targets a protected path ({marker.strip('/')}) — "
                "writes there turn into code execution or credential access"
            )
    return None
