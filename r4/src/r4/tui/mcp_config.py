"""MCP connection configuration loader.

Loads MCP server definitions from multiple sources:
1. Platform Gateway (auto-discovered via /gw/mcp/servers)
2. Project-level .r4/mcp.json or .github/mcp.json
3. User-level ~/.config/r4/mcp-servers.json
4. Environment variable R4_MCP_CONFIG

Config format is compatible with GitHub Copilot CLI's mcp.json:
{
  "mcpServers": {
    "server-name": {
      "type": "http",       // or "stdio"
      "url": "http://...",  // for http type
      "command": "python",  // for stdio type
      "args": ["-m", "server"],
      "env": {"KEY": "val"},
      "headers": {"Authorization": "Bearer ${R4_TOKEN}"},
      "sandbox": false      // or {"web": "none", "domains": [...], "write": [...]}
    }
  }
}

``sandbox`` (ADR-101 D-9) is read only from the user-level file and
``$R4_MCP_CONFIG``; a project file is repo-controlled, so its ``sandbox`` key
is ignored (``false`` there never exempts a server).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Union

logger = logging.getLogger(__name__)

#: Sources whose ``sandbox`` key counts (ADR-101 D-9): never a project file.
SANDBOX_TRUSTED_SOURCES = ("user", "env")
_SANDBOX_KEYS = ("web", "domains", "write")


def _parse_sandbox(raw: Any, source: str) -> Union[bool, Dict[str, Any]]:
    """``false`` (exempt) or ``{web, domains, write}``; ``True`` (default) otherwise."""
    if source not in SANDBOX_TRUSTED_SOURCES:
        return True
    if raw is False:
        return False
    if isinstance(raw, dict):
        return {key: raw[key] for key in _SANDBOX_KEYS if key in raw}
    return True


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    type: str = "http"  # "http" or "stdio"
    url: str = ""
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    description: str = ""
    enabled: bool = True
    source: str = "config"  # config | gateway | hatchling
    #: ADR-101 D-9: ``False`` exempts the server from the MCP sandbox, a dict
    #: carries its ``web``/``domains``/``write``; set only from trusted sources.
    sandbox: Union[bool, Dict[str, Any]] = True

    @property
    def sandbox_exempt(self) -> bool:
        return self.sandbox is False

    @classmethod
    def from_dict(
        cls, name: str, data: Dict[str, Any], source: str = "config"
    ) -> "MCPServerConfig":
        return cls(
            name=name,
            type=data.get("type", "http"),
            url=_expand_env(data.get("url", "")),
            command=data.get("command", ""),
            args=data.get("args", []),
            env={k: _expand_env(v) for k, v in data.get("env", {}).items()},
            headers={k: _expand_env(v) for k, v in data.get("headers", {}).items()},
            description=data.get("description", ""),
            enabled=bool(data.get("enabled", True)),
            source=source,
            sandbox=_parse_sandbox(data.get("sandbox", True), source),
        )


def _expand_env(value: str) -> str:
    """Expand ${VAR} references in config values."""
    if not isinstance(value, str):
        return value
    import re

    def _replacer(match):
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    return re.sub(r"\$\{(\w+)\}", _replacer, value)


# String literals come first so ``//`` inside a quoted value (e.g. a URL) is
# never treated as a comment. The previous ``//.*$`` regex cut ``https://…``
# inside string values, leaving invalid JSON that then failed to parse silently.
_JSONC_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*')


def _strip_jsonc_comments(text: str) -> str:
    """Strip ``//`` line comments without touching quoted strings."""
    parts: list[str] = []
    last = 0
    for match in _JSONC_TOKEN.finditer(text):
        parts.append(text[last : match.start()])
        if match.group(0).startswith('"'):
            parts.append(match.group(0))
        last = match.end()
    parts.append(text[last:])
    return "".join(parts)


def _load_json_file(path: Path) -> Dict[str, Any]:
    """Load a JSON or JSONC file (strips // comments)."""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(_strip_jsonc_comments(text))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("could not load %s: %s", path, exc)
        return {}


def _search_project_configs() -> List[Path]:
    """Find MCP config files in the project directory."""
    cwd = Path.cwd()
    candidates = [
        cwd / ".r4" / "mcp.json",
        cwd / ".github" / "mcp.json",
        cwd / ".r4" / "mcp-servers.json",
    ]
    return [p for p in candidates if p.is_file()]


def _user_config_path() -> Path:
    """User-level MCP server configuration."""
    config_dir = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_dir) / "r4" / "mcp-servers.json"


def load_mcp_configs() -> List[MCPServerConfig]:
    """Load MCP server configurations from all sources.

    Returns combined list (later sources override earlier by name).
    """
    servers: Dict[str, MCPServerConfig] = {}

    # 1. User-level config
    user_path = _user_config_path()
    if user_path.is_file():
        data = _load_json_file(user_path)
        for name, sdata in data.get("mcpServers", {}).items():
            servers[name] = MCPServerConfig.from_dict(name, sdata, source="user")
        logger.debug("loaded %d MCP servers from %s", len(data.get("mcpServers", {})), user_path)

    # 2. Project-level configs
    for config_path in _search_project_configs():
        data = _load_json_file(config_path)
        for name, sdata in data.get("mcpServers", {}).items():
            servers[name] = MCPServerConfig.from_dict(name, sdata, source="project")
        logger.debug("loaded MCP config from %s", config_path)

    # 3. Environment variable override
    env_config = os.environ.get("R4_MCP_CONFIG")
    if env_config:
        env_path = Path(env_config)
        if env_path.is_file():
            data = _load_json_file(env_path)
            for name, sdata in data.get("mcpServers", {}).items():
                servers[name] = MCPServerConfig.from_dict(name, sdata, source="env")
            logger.debug("loaded MCP config from R4_MCP_CONFIG=%s", env_config)

    return list(servers.values())


# =============================================================================
# Enabled toggling (TUI floating pane / bridge RPC)
# =============================================================================


# String literals first so braces inside quoted values never count.
_BRACE_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[{}]')


def _entry_block_span(text: str, name: str) -> tuple[int, int] | None:
    """Locate the ``"name": { … }`` block span inside an mcpServers file."""
    match = re.search(r'"' + re.escape(name) + r'"\s*:\s*\{', text)
    if match is None:
        return None
    brace = text.rfind("{", match.start(), match.end())
    depth = 1
    for token in _BRACE_TOKEN.finditer(text, brace + 1):
        depth += (token.group(0) == "{") - (token.group(0) == "}")
        if depth == 0:
            return brace, token.end()
    return None


def _set_enabled_in_text(text: str, name: str, enabled: bool) -> str | None:
    """Comment-preserving rewrite of one entry's ``enabled`` flag."""
    span = _entry_block_span(text, name)
    if span is None:
        return None
    block = text[span[0] : span[1]]
    flag = '"enabled": ' + ("true" if enabled else "false")
    replaced, count = re.subn(r'"enabled"\s*:\s*(true|false)', flag, block, count=1)
    if count == 0:
        indent_match = re.search(r"\n([ \t]+)", block)
        indent = indent_match.group(1) if indent_match else "  "
        replaced = block[:1] + "\n" + indent + flag + "," + block[1:]
    return text[: span[0]] + replaced + text[span[1] :]


def set_mcp_enabled(name: str, enabled: bool, path: Path | None = None) -> bool:
    """Flip one entry's ``enabled`` flag in the user-level config (atomic).

    Returns False when the file or the entry is absent, or when the write
    would produce invalid JSON (nothing is written in that case).
    """
    target = path or _user_config_path()
    if not target.is_file():
        return False
    rewritten = _set_enabled_in_text(target.read_text(encoding="utf-8"), name, enabled)
    if rewritten is None:
        return False
    try:
        json.loads(_strip_jsonc_comments(rewritten))
    except json.JSONDecodeError:
        return False
    tmp = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(rewritten, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return True


__all__ = ["MCPServerConfig", "load_mcp_configs", "set_mcp_enabled"]
