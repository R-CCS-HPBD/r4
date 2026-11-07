"""Manifest-driven custom MCP runner.

The runner loads user-mounted MCP server directories that contain a
``config.json`` manifest, validates declared capabilities, exposes only those
declared tools through MCP, and launches the configured entry point with
EgressGuard proxy environment variables.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..base import MCPServer, ToolDefinition, ToolParameter, ToolResult, TransportType
from ..security import (
    CapabilityEnforcer,
    SecurityContext,
    ToolCapability,
    ToolMetadata,
)

DEFAULT_PROXY_URL = "http://egress-guard:8888"
DEFAULT_NO_PROXY = "localhost,127.0.0.1,::1,litellm,vllm,ll-postgres,coredns,nginx,egress-guard"
INTERPRETER_EXECUTABLES = {
    "python",
    "python3",
    Path(sys.executable).name,
    "node",
    "bun",
    "deno",
    "ruby",
    "perl",
    "php",
}
SHELL_EXECUTABLES = {"sh", "bash", "dash", "zsh", "fish", "ksh"}
ENTRY_POINT_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".rb", ".pl", ".php"}
SAFE_INHERITED_ENV_KEYS = {
    "CURL_CA_BUNDLE",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
    "USER",
    "VIRTUAL_ENV",
}


class CustomMCPConfigError(ValueError):
    """Raised when a custom MCP manifest is invalid."""


@dataclass
class CustomMCPSandbox:
    """Sandbox settings declared by a mounted MCP server."""

    network: str = "egress_guard"
    filesystem: str = "read_only"
    max_memory_mb: int = 512
    max_runtime_sec: int = 30

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "CustomMCPSandbox":
        sandbox = cls(
            network=str(raw.get("network", "egress_guard")),
            filesystem=str(raw.get("filesystem", "read_only")),
            max_memory_mb=int(raw.get("max_memory_mb", 512)),
            max_runtime_sec=int(raw.get("max_runtime_sec", raw.get("timeout_sec", 30))),
        )
        if sandbox.network != "egress_guard":
            raise CustomMCPConfigError("custom MCP sandbox.network must be egress_guard")
        if sandbox.filesystem != "read_only":
            raise CustomMCPConfigError("custom MCP sandbox.filesystem must be read_only")
        if sandbox.max_memory_mb <= 0:
            raise CustomMCPConfigError("custom MCP sandbox.max_memory_mb must be positive")
        if sandbox.max_runtime_sec <= 0:
            raise CustomMCPConfigError("custom MCP sandbox.max_runtime_sec must be positive")
        return sandbox


@dataclass
class CustomMCPManifest:
    """Validated custom MCP manifest."""

    name: str
    version: str
    entry_point: str
    root: Path
    tools: list[str]
    tool_capabilities: dict[str, ToolCapability]
    schema_version: str = "1"
    tool_descriptions: dict[str, str] = field(default_factory=dict)
    tool_schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    egress_domains: list[str] = field(default_factory=list)
    egress_protocols: list[str] = field(default_factory=lambda: ["https"])
    sandbox: CustomMCPSandbox = field(default_factory=CustomMCPSandbox)

    @classmethod
    def from_file(cls, path: Path) -> "CustomMCPManifest":
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise CustomMCPConfigError(f"Invalid JSON in {path}: {exc}") from exc
        return cls.from_raw(raw, path.parent)

    @classmethod
    def from_raw(cls, raw: dict[str, Any], root: Path) -> "CustomMCPManifest":
        name = _required_string(raw, "name")
        entry_point = _required_string(raw, "entry_point")
        schema_version = str(raw.get("schema_version", "1"))
        if schema_version not in ("1",):
            raise CustomMCPConfigError(
                f"Unsupported schema_version '{schema_version}' (supported: ['1'])"
            )
        root = root.expanduser().resolve()
        _validate_entry_point(entry_point, root)
        capabilities = _required_mapping(raw, "capabilities")
        tools = _required_string_list(capabilities, "tools")
        tool_capabilities = _parse_tool_capabilities(capabilities, tools)
        egress = capabilities.get("egress", {})
        if egress and not isinstance(egress, dict):
            raise CustomMCPConfigError("capabilities.egress must be an object")
        return cls(
            name=name,
            version=str(raw.get("version", "0.1.0")),
            entry_point=entry_point,
            root=root,
            schema_version=schema_version,
            tools=tools,
            tool_capabilities=tool_capabilities,
            tool_descriptions=_string_dict(raw.get("tool_descriptions", {})),
            tool_schemas=_schema_dict(raw.get("tool_schemas", {})),
            egress_domains=_string_list(egress.get("domains", [])),
            egress_protocols=_string_list(egress.get("protocols", ["https"])),
            sandbox=CustomMCPSandbox.from_raw(raw.get("sandbox", {})),
        )


class CustomMCPServer(MCPServer):
    """MCP server that proxies declared custom tools to mounted entry points."""

    name = "custom-mcp"
    version = "0.1.0"

    def __init__(
        self,
        manifests: list[CustomMCPManifest],
        enforcer: Optional[CapabilityEnforcer] = None,
        context: Optional[SecurityContext] = None,
        transport: TransportType = TransportType.STDIO,
    ):
        if not manifests:
            raise CustomMCPConfigError("No custom MCP config.json files found")
        self.manifests = manifests
        self.enforcer = enforcer or CapabilityEnforcer()
        self.security_context = context or SecurityContext(
            user_id=os.getenv("RIVAULT_OWNER_ID", "custom-mcp"),
            session_id=os.getenv("RIVAULT_INSTANCE_ID", "custom-mcp-session"),
            granted_capabilities=_parse_capability_list(
                os.getenv("RIVAULT_MCP_GRANTED_CAPABILITIES", "STANDARD_USER").split(",")
            ),
        )
        self._tool_manifest: dict[str, CustomMCPManifest] = {}
        super().__init__(transport)
        self._register_manifest_tools()

    def _register_manifest_tools(self) -> None:
        for manifest in self.manifests:
            for tool_name in manifest.tools:
                self._register_manifest_tool(manifest, tool_name)

    def _register_manifest_tool(self, manifest: CustomMCPManifest, tool_name: str) -> None:
        if tool_name in self._tool_manifest:
            raise CustomMCPConfigError(f"Duplicate custom MCP tool name: {tool_name}")
        self._tool_manifest[tool_name] = manifest
        self._tools[tool_name] = ToolDefinition(
            name=tool_name,
            description=manifest.tool_descriptions.get(
                tool_name,
                f"Custom MCP tool '{tool_name}' from {manifest.name}",
            ),
            parameters=_tool_parameters(manifest.tool_schemas.get(tool_name, {})),
        )
        self.enforcer.register_tool(
            ToolMetadata(
                name=tool_name,
                server_name=manifest.name,
                required_capabilities=manifest.tool_capabilities[tool_name],
            )
        )

    async def handle_call_tool(self, params: dict) -> dict:
        tool_name = str(params.get("name", ""))
        arguments = params.get("arguments", {})
        manifest = self._tool_manifest.get(tool_name)
        if manifest is None:
            return ToolResult.error(f"Custom MCP tool is not declared: {tool_name}").to_mcp_format()

        access = self.enforcer.check_access(
            self.security_context,
            manifest.name,
            tool_name,
        )
        if not access.allowed:
            return ToolResult.error(f"Capability denied: {access.denied_reason}").to_mcp_format()

        result = await self._invoke_entry_point(manifest, tool_name, arguments)
        return self._scan_tool_result(tool_name, result, params).to_mcp_format()

    async def _invoke_entry_point(
        self,
        manifest: CustomMCPManifest,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        command = shlex.split(manifest.entry_point)
        if not command:
            return ToolResult.error("Custom MCP entry_point is empty")

        payload = json.dumps({"tool": tool_name, "arguments": arguments})
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=manifest.root,
                env=_sandbox_env(manifest),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_sandbox_subprocess_kwargs(manifest),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload.encode()),
                timeout=manifest.sandbox.max_runtime_sec,
            )
        except asyncio.TimeoutError:
            if proc is not None:
                await _terminate_process(proc)
            return ToolResult.error(
                f"Custom MCP tool timed out after {manifest.sandbox.max_runtime_sec}s"
            )
        except OSError as exc:
            return ToolResult.error(f"Failed to launch custom MCP entry point: {exc}")

        if proc.returncode != 0:
            message = stderr.decode(errors="replace").strip() or f"exit code {proc.returncode}"
            return ToolResult.error(message)
        return ToolResult.success(stdout.decode(errors="replace").strip())


def _validate_entry_point(entry_point: str, root: Path) -> None:
    command = _entry_point_command(entry_point)
    _reject_unsafe_entry_point(command)
    _validate_entry_point_paths(command, root)


def _entry_point_command(entry_point: str) -> list[str]:
    command = shlex.split(entry_point)
    if not command:
        raise CustomMCPConfigError("Custom MCP entry_point is empty")
    return command


def _reject_unsafe_entry_point(command: list[str]) -> None:
    executable_name = Path(command[0]).name
    if executable_name in SHELL_EXECUTABLES:
        raise CustomMCPConfigError("custom MCP entry_point may not invoke a shell")
    if "-c" in command:
        raise CustomMCPConfigError("custom MCP entry_point may not execute inline code")


def _validate_entry_point_paths(command: list[str], root: Path) -> None:
    executable_name = Path(command[0]).name
    if _looks_like_path(command[0]) and executable_name not in INTERPRETER_EXECUTABLES:
        _require_path_inside_root(command[0], root, "entry_point executable")

    for token in _entry_point_path_tokens(command):
        _require_path_inside_root(token, root, "entry_point path")


def _entry_point_path_tokens(command: list[str]) -> list[str]:
    tokens = []
    for index, token in enumerate(command[1:], start=1):
        if _skip_entry_point_token(command, index, token):
            continue
        if _looks_like_path(token):
            tokens.append(token)
    return tokens


def _skip_entry_point_token(command: list[str], index: int, token: str) -> bool:
    return token == "-m" or command[index - 1] == "-m" or token.startswith("-")


def _looks_like_path(token: str) -> bool:
    return (
        "/" in token or token.startswith(".") or Path(token).suffix in ENTRY_POINT_SUFFIXES
    ) and "://" not in token


def _require_path_inside_root(value: str, root: Path, label: str) -> None:
    path = Path(value).expanduser()
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CustomMCPConfigError(f"custom MCP {label} must stay within {root}") from exc
    if not resolved.exists():
        raise CustomMCPConfigError(f"custom MCP {label} does not exist: {resolved}")


def load_manifests(config_dir: str) -> list[CustomMCPManifest]:
    """Load all custom MCP manifests from a directory or a direct config path."""
    root = Path(config_dir).expanduser().resolve()
    if root.is_file():
        return [CustomMCPManifest.from_file(root)]
    if not root.is_dir():
        raise CustomMCPConfigError(f"Custom MCP config path does not exist: {root}")
    candidates = []
    direct = root / "config.json"
    if direct.exists():
        candidates.append(direct)
    candidates.extend(sorted(path for path in root.glob("*/config.json") if path.is_file()))
    return [CustomMCPManifest.from_file(path) for path in candidates]


def _sandbox_env(manifest: CustomMCPManifest) -> dict[str, str]:
    proxy_url = os.getenv("RIVAULT_HTTP_PROXY") or os.getenv("HTTP_PROXY") or DEFAULT_PROXY_URL
    https_proxy = os.getenv("RIVAULT_HTTPS_PROXY") or os.getenv("HTTPS_PROXY") or proxy_url
    no_proxy = os.getenv("RIVAULT_NO_PROXY") or os.getenv("NO_PROXY") or DEFAULT_NO_PROXY
    env = _safe_inherited_env()
    env.update(
        {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": https_proxy,
            "http_proxy": proxy_url,
            "https_proxy": https_proxy,
            "NO_PROXY": no_proxy,
            "no_proxy": no_proxy,
            "EGRESS_GUARD_REQUIRED": "1",
            "EGRESS_DOMAIN_ALLOWLIST": ",".join(manifest.egress_domains),
            "EGRESS_PROTOCOL_ALLOWLIST": ",".join(manifest.egress_protocols),
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_OFFLINE": "1",
            "RIVAULT_SANDBOX_FILESYSTEM": manifest.sandbox.filesystem,
            "RIVAULT_SANDBOX_NETWORK": manifest.sandbox.network,
            "RIVAULT_SANDBOX_MAX_MEMORY_MB": str(manifest.sandbox.max_memory_mb),
            "RIVAULT_SANDBOX_MAX_RUNTIME_SEC": str(manifest.sandbox.max_runtime_sec),
        }
    )
    return env


def _safe_inherited_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in SAFE_INHERITED_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _sandbox_subprocess_kwargs(manifest: CustomMCPManifest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    if _resource_limits_supported():
        kwargs["preexec_fn"] = _sandbox_preexec_fn(manifest)
    return kwargs


def _resource_limits_supported() -> bool:
    return os.name == "posix" and sys.platform.startswith("linux")


def _sandbox_preexec_fn(manifest: CustomMCPManifest) -> Callable[[], None]:
    def apply_limits() -> None:
        _apply_resource_limits(manifest)

    return apply_limits


def _apply_resource_limits(manifest: CustomMCPManifest) -> None:
    import resource

    memory_bytes = manifest.sandbox.max_memory_mb * 1024 * 1024
    cpu_seconds = manifest.sandbox.max_runtime_sec + 1
    _set_resource_limit(resource, resource.RLIMIT_AS, memory_bytes)
    _set_resource_limit(resource, resource.RLIMIT_CPU, cpu_seconds)
    if hasattr(resource, "RLIMIT_CORE"):
        _set_resource_limit(resource, resource.RLIMIT_CORE, 0)


def _set_resource_limit(resource_module: Any, limit_name: int, requested: int) -> None:
    soft, hard = resource_module.getrlimit(limit_name)
    hard_limit = _bounded_limit(resource_module, hard, requested)
    soft_limit = min(_bounded_limit(resource_module, soft, requested), hard_limit)
    resource_module.setrlimit(limit_name, (soft_limit, hard_limit))


def _bounded_limit(resource_module: Any, current: int, requested: int) -> int:
    if current == resource_module.RLIM_INFINITY:
        return requested
    return min(current, requested)


async def _terminate_process(proc: Any) -> None:
    if proc.returncode is not None:
        return
    _signal_process(proc, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        _signal_process(proc, signal.SIGKILL)
        await proc.wait()


def _signal_process(proc: Any, signum: signal.Signals) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signum)
            return
    except (ProcessLookupError, PermissionError):
        pass

    if signum == signal.SIGTERM:
        proc.terminate()
    else:
        proc.kill()


def _required_mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise CustomMCPConfigError(f"config.json must declare object '{key}'")
    return value


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CustomMCPConfigError(f"config.json must declare non-empty string '{key}'")
    return value.strip()


def _required_string_list(raw: dict[str, Any], key: str) -> list[str]:
    values = _string_list(raw.get(key, []))
    if not values:
        raise CustomMCPConfigError(f"config.json must declare at least one '{key}' entry")
    return values


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _schema_dict(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, dict)}


def _parse_tool_capabilities(
    capabilities: dict[str, Any],
    tools: list[str],
) -> dict[str, ToolCapability]:
    raw = capabilities.get("tool_capabilities", {})
    if not isinstance(raw, dict):
        raise CustomMCPConfigError("capabilities.tool_capabilities must be an object")
    parsed = {tool: _parse_capability_list(raw.get(tool, ["STANDARD_USER"])) for tool in tools}
    missing = [tool for tool, capability in parsed.items() if capability == ToolCapability.NONE]
    if missing:
        raise CustomMCPConfigError(
            "Declared tools must have non-empty capabilities: " + ", ".join(missing)
        )
    return parsed


def _parse_capability_list(value: Any) -> ToolCapability:
    names = value if isinstance(value, list) else [value]
    capability = ToolCapability.NONE
    for name in names:
        token = str(name).strip().upper()
        if not token:
            continue
        if not hasattr(ToolCapability, token):
            raise CustomMCPConfigError(f"Unknown ToolCapability: {token}")
        capability |= getattr(ToolCapability, token)
    return capability


def _tool_parameters(schema: dict[str, Any]) -> list[ToolParameter]:
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
    if not isinstance(properties, dict):
        return []
    return [_tool_parameter(name, spec, required) for name, spec in properties.items()]


def _tool_parameter(name: str, spec: Any, required: set[str]) -> ToolParameter:
    spec = spec if isinstance(spec, dict) else {}
    return ToolParameter(
        name=str(name),
        type=str(spec.get("type", "string")),
        description=str(spec.get("description", "")),
        required=str(name) in required,
        enum=spec.get("enum"),
        default=spec.get("default"),
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run user-mounted custom MCP servers")
    parser.add_argument(
        "--config-dir", default="/mcp-servers", help="Directory containing config.json files"
    )
    parser.add_argument("--validate-only", action="store_true", help="Validate manifests and exit")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)

    try:
        manifests = load_manifests(args.config_dir)
        server = CustomMCPServer(manifests, transport=TransportType(args.transport))
    except CustomMCPConfigError as exc:
        print(f"custom-mcp config error: {exc}", file=sys.stderr)
        return 2

    if args.validate_only:
        print(f"validated {len(manifests)} custom MCP manifest(s)")
        return 0
    server.run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
