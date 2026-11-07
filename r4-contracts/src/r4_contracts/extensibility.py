"""Extensibility governance contracts (COR-004).

Shared DTOs and validators for the platform's extension points:

- Custom MCP server manifests
- Plugin job registration
- Extension signing metadata

These contracts enforce consistent governance across r4 (CLI/SDK),
UMRS (runtime enforcement), and the MCP server runner.

All types are stdlib-only ``@dataclass`` — no Pydantic, no third-party deps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------


class SandboxSchemaVersion(str, Enum):
    """Schema versions for custom MCP / extension manifests.

    Each version defines the set of fields, validation rules, and
    sandbox enforcement semantics.  Loaders must reject manifests
    whose schema_version exceeds the runner's known versions.
    """

    V1 = "1"  # Initial: network=egress_guard, filesystem=read_only, mem, runtime


# Current schema version — bumped when manifest fields change
CURRENT_SCHEMA_VERSION = SandboxSchemaVersion.V1

# All versions the current runner can load
SUPPORTED_SCHEMA_VERSIONS = frozenset({SandboxSchemaVersion.V1})


# ---------------------------------------------------------------------------
# Extension tier and status
# ---------------------------------------------------------------------------


class ExtensionTier(str, Enum):
    """Extension ownership tier — mirrors deployment tiers."""

    SYSTEM = "system"
    PROJECT = "project"
    USER = "user"


class ExtensionStatus(str, Enum):
    """Lifecycle status for a registered extension."""

    PENDING = "pending"  # Submitted, not yet approved
    APPROVED = "approved"  # Approved for use
    SUSPENDED = "suspended"  # Temporarily disabled
    REVOKED = "revoked"  # Permanently disabled


# ---------------------------------------------------------------------------
# Sandbox constraints (shared validation target)
# ---------------------------------------------------------------------------


@dataclass
class SandboxConstraints:
    """Declares the sandbox environment for an extension.

    These constraints are enforced at load time by the MCP runner and
    validated at registration time by UMRS.
    """

    network: str = "egress_guard"  # Must be "egress_guard"
    filesystem: str = "read_only"  # Must be "read_only"
    max_memory_mb: int = 512  # Must be > 0
    max_runtime_sec: int = 30  # Must be > 0

    def validate(self) -> list[str]:
        """Return a list of validation error messages (empty = valid)."""
        errors: list[str] = []
        if self.network != "egress_guard":
            errors.append(f"sandbox.network must be 'egress_guard', got '{self.network}'")
        if self.filesystem != "read_only":
            errors.append(f"sandbox.filesystem must be 'read_only', got '{self.filesystem}'")
        if self.max_memory_mb <= 0:
            errors.append(f"sandbox.max_memory_mb must be positive, got {self.max_memory_mb}")
        if self.max_runtime_sec <= 0:
            errors.append(f"sandbox.max_runtime_sec must be positive, got {self.max_runtime_sec}")
        return errors


# ---------------------------------------------------------------------------
# Egress policy declaration
# ---------------------------------------------------------------------------


@dataclass
class EgressDeclaration:
    """Network egress allowed by the extension."""

    domains: list[str] = field(default_factory=list)
    protocols: list[str] = field(default_factory=lambda: ["https"])

    def validate(self) -> list[str]:
        errors: list[str] = []
        allowed = {"http", "https"}
        for p in self.protocols:
            if p not in allowed:
                errors.append(f"Unsupported protocol '{p}' (allowed: {allowed})")
        return errors


# ---------------------------------------------------------------------------
# Extension manifest (the authoritative shape for config.json)
# ---------------------------------------------------------------------------


@dataclass
class ExtensionManifest:
    """Platform-level extension manifest.

    This is the r4-contracts equivalent of the MCP runner's
    ``CustomMCPManifest``.  It defines what UMRS and the Gateway validate
    when an extension is registered, and what ``r4 extension register``
    sends upstream.
    """

    name: str
    version: str
    entry_point: str
    schema_version: str = CURRENT_SCHEMA_VERSION.value
    tools: list[str] = field(default_factory=list)
    tool_capabilities: dict[str, list[str]] = field(default_factory=dict)
    egress: EgressDeclaration = field(default_factory=EgressDeclaration)
    sandbox: SandboxConstraints = field(default_factory=SandboxConstraints)
    description: str = ""
    author: str = ""

    # Signing metadata (populated after review/approval)
    signature: str | None = None
    signed_by: str | None = None
    signed_at: str | None = None

    def validate(self) -> list[str]:
        """Return a list of validation error messages (empty = valid)."""
        errors: list[str] = []
        if not self.name:
            errors.append("name is required")
        if not self.version:
            errors.append("version is required")
        if not self.entry_point:
            errors.append("entry_point is required")
        if not self.tools:
            errors.append("tools list must not be empty")

        # Schema version check
        try:
            sv = SandboxSchemaVersion(self.schema_version)
            if sv not in SUPPORTED_SCHEMA_VERSIONS:
                errors.append(
                    f"schema_version '{self.schema_version}' not supported "
                    f"(supported: {[v.value for v in SUPPORTED_SCHEMA_VERSIONS]})"
                )
        except ValueError:
            errors.append(f"unknown schema_version '{self.schema_version}'")

        # Entry point security checks
        errors.extend(_validate_entry_point(self.entry_point))

        # Sandbox validation
        errors.extend(self.sandbox.validate())

        # Egress validation
        errors.extend(self.egress.validate())

        # Tool capability references
        for tool_name in self.tool_capabilities:
            if tool_name not in self.tools:
                errors.append(f"tool_capabilities references unknown tool '{tool_name}'")

        return errors

    def to_dict(self) -> dict:
        """Serialize to a JSON-transportable dict."""
        d: dict = {
            "name": self.name,
            "version": self.version,
            "schema_version": self.schema_version,
            "entry_point": self.entry_point,
            "tools": list(self.tools),
            "tool_capabilities": {k: list(v) for k, v in self.tool_capabilities.items()},
            "egress": {
                "domains": list(self.egress.domains),
                "protocols": list(self.egress.protocols),
            },
            "sandbox": {
                "network": self.sandbox.network,
                "filesystem": self.sandbox.filesystem,
                "max_memory_mb": self.sandbox.max_memory_mb,
                "max_runtime_sec": self.sandbox.max_runtime_sec,
            },
            "description": self.description,
            "author": self.author,
        }
        if self.signature:
            d["signature"] = self.signature
            d["signed_by"] = self.signed_by
            d["signed_at"] = self.signed_at
        return d

    @classmethod
    def from_dict(cls, data: dict) -> ExtensionManifest:
        """Deserialize from a dict (e.g. parsed config.json)."""
        egress_data = data.get("egress", {})
        sandbox_data = data.get("sandbox", {})
        caps = data.get("capabilities", {})

        return cls(
            name=data.get("name", ""),
            version=data.get("version", ""),
            entry_point=data.get("entry_point", ""),
            schema_version=data.get("schema_version", CURRENT_SCHEMA_VERSION.value),
            tools=caps.get("tools", data.get("tools", [])),
            tool_capabilities=caps.get("tool_capabilities", data.get("tool_capabilities", {})),
            egress=EgressDeclaration(
                domains=egress_data.get("domains", []),
                protocols=egress_data.get("protocols", ["https"]),
            ),
            sandbox=SandboxConstraints(
                network=sandbox_data.get("network", "egress_guard"),
                filesystem=sandbox_data.get("filesystem", "read_only"),
                max_memory_mb=sandbox_data.get("max_memory_mb", 512),
                max_runtime_sec=sandbox_data.get("max_runtime_sec", 30),
            ),
            description=data.get("description", ""),
            author=data.get("author", ""),
            signature=data.get("signature"),
            signed_by=data.get("signed_by"),
            signed_at=data.get("signed_at"),
        )


# ---------------------------------------------------------------------------
# Plugin job contract
# ---------------------------------------------------------------------------


@dataclass
class PluginJobContract:
    """Contract for a plugin that submits jobs via the platform.

    Plugins that create UMRS jobs must declare their resource requirements
    and execution authority constraints upfront.
    """

    plugin_name: str
    job_type: str  # e.g. "custom_inference", "data_transform"
    execution_authorities: list[str] = field(  # allowed backends
        default_factory=lambda: ["compose"]
    )
    max_runtime_sec: int = 3600
    requires_gpu: bool = False
    requires_egress: bool = False
    max_memory_mb: int = 4096
    max_cpus: int = 4

    def validate(self) -> list[str]:
        """Return validation errors."""
        errors: list[str] = []
        if not self.plugin_name:
            errors.append("plugin_name is required")
        if not self.job_type:
            errors.append("job_type is required")
        allowed_authorities = {"slurm", "k8s", "compose"}
        for auth in self.execution_authorities:
            if auth not in allowed_authorities:
                errors.append(
                    f"Unknown execution_authority '{auth}' (allowed: {allowed_authorities})"
                )
        if self.max_runtime_sec <= 0:
            errors.append("max_runtime_sec must be positive")
        if self.max_memory_mb <= 0:
            errors.append("max_memory_mb must be positive")
        if self.max_cpus <= 0:
            errors.append("max_cpus must be positive")
        return errors

    def to_dict(self) -> dict:
        return {
            "plugin_name": self.plugin_name,
            "job_type": self.job_type,
            "execution_authorities": list(self.execution_authorities),
            "max_runtime_sec": self.max_runtime_sec,
            "requires_gpu": self.requires_gpu,
            "requires_egress": self.requires_egress,
            "max_memory_mb": self.max_memory_mb,
            "max_cpus": self.max_cpus,
        }


# ---------------------------------------------------------------------------
# Entry point validation helpers
# ---------------------------------------------------------------------------

_SHELL_EXECUTABLES = {"sh", "bash", "dash", "zsh", "fish", "ksh"}


def _validate_entry_point(entry_point: str) -> list[str]:
    """Validate an extension entry point for security."""
    errors: list[str] = []
    parts = entry_point.split()
    if not parts:
        errors.append("entry_point is empty")
        return errors

    executable = parts[0].split("/")[-1]  # basename

    # Shell invocation is forbidden
    if executable in _SHELL_EXECUTABLES:
        errors.append(f"entry_point must not invoke a shell ('{executable}')")

    # Inline code flags are forbidden
    if "-c" in parts:
        errors.append("entry_point must not use '-c' inline code execution")

    # Path traversal is forbidden
    for part in parts:
        if ".." in part:
            errors.append(f"entry_point contains path traversal: '{part}'")

    return errors


# ---------------------------------------------------------------------------
# Capability grant record
# ---------------------------------------------------------------------------


@dataclass
class CapabilityGrant:
    """Record of an approved capability grant for an extension.

    Tracks who approved what capability for which extension,
    providing an audit trail for extensibility governance.
    """

    extension_name: str
    capability: str  # ToolCapability flag name
    granted_by: str  # user_id of approver
    granted_at: str  # ISO-8601
    scope: str = "user"  # system | project | user
    project_id: str | None = None
    reason: str = ""
    expires_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "extension_name": self.extension_name,
            "capability": self.capability,
            "granted_by": self.granted_by,
            "granted_at": self.granted_at,
            "scope": self.scope,
            "project_id": self.project_id,
            "reason": self.reason,
            "expires_at": self.expires_at,
        }
